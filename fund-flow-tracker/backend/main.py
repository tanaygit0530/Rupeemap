"""
main.py
-------
FastAPI application — all routes wired up.
Parallel engine execution, Supabase Realtime push via insert, full SAR pipeline.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks  # type: ignore
from fastapi.middleware.cors import CORSMiddleware  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
from supabase import create_client, Client  # type: ignore
from dotenv import load_dotenv  # type: ignore

import engines.engine1 as engine1  # type: ignore
import engines.engine2 as engine2  # type: ignore
from services import freeze_service, auth_service, sar_service  # type: ignore
from services.presidio_service import mask_text, unmask_text, mask_account_id  # type: ignore
from models.schemas import (  # type: ignore
    LoginRequest, LoginResponse, Transaction, SARRequest, SARResponse,
    DemoLoadResponse, LockResponse, ActionResponse,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPA_URL, SUPA_KEY)
UTC = timezone.utc

ALERT_TRIGGER_THRESHOLD = 30  # Minimum fused score to create an alert

app = FastAPI(
    title="RupeeMap API",
    description="AML detection system powered by Gemini 2.0 Flash",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Startup — TTL background worker ──────────────────────────────────────────

@app.on_event("startup")
async def startup():
    asyncio.create_task(freeze_service.ttl_worker())
    logger.info("TTL worker started")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    status = {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}

    # Neo4j
    try:
        from neo4j import AsyncGraphDatabase
        driver = AsyncGraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD")),
        )
        async with driver.session() as s:
            await s.run("RETURN 1")
        await driver.close()
        status["neo4j"] = "connected"
    except Exception as e:
        status["neo4j"] = f"error: {e}"

    # Supabase
    try:
        supabase.table("officers").select("id").limit(1).execute()
        status["supabase"] = "connected"
    except Exception as e:
        status["supabase"] = f"error: {e}"

    # Gemini
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-2.0-flash")
        model.generate_content("ping")
        status["gemini"] = "connected"
    except Exception as e:
        status["gemini"] = f"error: {e}"

    return status


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    return await auth_service.login(req.email, req.password)


@app.post("/auth/logout")
async def logout(officer: dict = Depends(auth_service.require_any_officer)):
    return {"success": True}


# ── Alerts ────────────────────────────────────────────────────────────────────

@app.get("/alerts")
async def list_alerts(
    flag_type: Optional[str] = None,
    min_score: Optional[int] = None,
    freeze_status: Optional[str] = None,
    officer: dict = Depends(auth_service.require_any_officer),
):
    query = supabase.table("alerts").select("*").order("risk_score", desc=True)
    if flag_type:
        query = query.eq("flag_type", flag_type)
    if min_score is not None:
        query = query.gte("risk_score", min_score)
    if freeze_status:
        query = query.eq("freeze_status", freeze_status)
    resp = query.execute()
    return resp.data or []


@app.get("/alerts/{alert_id}")
async def get_alert(alert_id: str, officer: dict = Depends(auth_service.require_any_officer)):
    resp = supabase.table("alerts").select("*").eq("id", alert_id).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Alert not found")
    return resp.data


@app.patch("/alerts/{alert_id}/lock")
async def lock_alert(alert_id: str, officer: dict = Depends(auth_service.require_any_officer)):
    return await auth_service.acquire_lock(alert_id, officer)


@app.patch("/alerts/{alert_id}/heartbeat")
async def heartbeat(alert_id: str, officer: dict = Depends(auth_service.require_any_officer)):
    await auth_service.heartbeat_lock(alert_id, officer)
    return {"success": True}


@app.post("/alerts/{alert_id}/escalate")
async def escalate_alert(alert_id: str, officer: dict = Depends(auth_service.require_senior_officer)):
    now = datetime.now(UTC).isoformat()

    resp = supabase.table("alerts").select("frozen_amount, triggered_by").eq("id", alert_id).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Alert not found")

    frozen = float(resp.data.get("frozen_amount", 0))
    source = resp.data.get("triggered_by", "ENGINE_1")

    supabase.table("alerts").update({
        "freeze_status": "FULL",
        "risk_score": 100,
    }).eq("id", alert_id).execute()

    supabase.table("audit_log").insert({
        "alert_id": alert_id,
        "action_type": "FULL_FREEZE",
        "officer_id": officer["officer_id"],
        "officer_role": officer["role"],
        "amount_frozen": frozen,
        "risk_score_at_time": 100,
        "engine_source": source,
        "timestamp": now,
        "notes": f"Manually escalated to Full Freeze by senior officer {officer['email']}",
    }).execute()

    await auth_service.release_lock(alert_id, officer)
    return {"success": True, "new_freeze_status": "FULL", "new_risk_score": 100}


@app.post("/alerts/{alert_id}/confirm")
async def confirm_freeze(alert_id: str, officer: dict = Depends(auth_service.require_any_officer)):
    from datetime import timedelta
    now = datetime.now(UTC)
    new_ttl = (now + timedelta(hours=72)).isoformat()

    resp = supabase.table("alerts").select("frozen_amount, risk_score, triggered_by").eq("id", alert_id).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Alert not found")

    supabase.table("alerts").update({
        "ttl_expires_at": new_ttl,
    }).eq("id", alert_id).execute()

    supabase.table("audit_log").insert({
        "alert_id": alert_id,
        "action_type": "CONFIRM",
        "officer_id": officer["officer_id"],
        "officer_role": officer["role"],
        "amount_frozen": float(resp.data.get("frozen_amount", 0)),
        "risk_score_at_time": int(resp.data.get("risk_score", 0)),
        "engine_source": resp.data.get("triggered_by", "ENGINE_1"),
        "timestamp": now.isoformat(),
        "notes": f"Partial freeze confirmed by {officer['email']} — TTL reset to 72h",
    }).execute()

    await auth_service.release_lock(alert_id, officer)
    return {"success": True, "message": "Freeze confirmed, TTL extended 72h"}


@app.post("/alerts/{alert_id}/release")
async def release_alert(alert_id: str, officer: dict = Depends(auth_service.require_any_officer)):
    now = datetime.now(UTC).isoformat()

    resp = supabase.table("alerts").select("*").eq("id", alert_id).single().execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert = resp.data
    frozen = float(alert.get("frozen_amount", 0))
    account_masked = alert.get("account_id_masked")

    # Restore balance
    try:
        acc = supabase.table("accounts").select("available_balance, lien_amount") \
            .eq("account_id", account_masked).maybe_single().execute()
        if acc.data:
            bal = float(acc.data.get("available_balance", 0))
            lien = float(acc.data.get("lien_amount", 0))
            supabase.table("accounts").update({
                "available_balance": bal + frozen,
                "lien_amount": max(0.0, lien - frozen),
                "account_status": "ACTIVE",
            }).eq("account_id", account_masked).execute()
    except Exception as e:
        logger.warning(f"Balance restore on release failed: {e}")

    supabase.table("alerts").update({"freeze_status": "RELEASED"}).eq("id", alert_id).execute()

    supabase.table("audit_log").insert({
        "alert_id": alert_id,
        "action_type": "RELEASE",
        "officer_id": officer["officer_id"],
        "officer_role": officer["role"],
        "amount_frozen": 0,
        "risk_score_at_time": int(alert.get("risk_score", 0)),
        "engine_source": alert.get("triggered_by", "ENGINE_1"),
        "timestamp": now,
        "notes": f"Released as false positive by {officer['email']}",
    }).execute()

    # Log to false_positives
    supabase.table("false_positives").insert({
        "alert_id": alert_id,
        "account_id_masked": account_masked,
        "flag_type": alert.get("flag_type"),
        "released_by_officer": officer["officer_id"],
        "released_at": now,
    }).execute()

    await auth_service.release_lock(alert_id, officer)
    return {"success": True, "message": "Lien released, balance restored"}


# ── Transaction Ingest ────────────────────────────────────────────────────────

@app.post("/transactions/ingest")
async def ingest_transaction(txn: Transaction, background_tasks: BackgroundTasks):
    txn_dict = txn.dict()

    # Run Engine 1 and Engine 2 in parallel
    engine1_result, engine2_result = await asyncio.gather(
        engine1.analyse(txn_dict),
        engine2.analyse(txn_dict),
    )

    # Score fusion
    fused = _fuse_scores(engine1_result, engine2_result, txn_dict)

    if fused["risk_score"] < ALERT_TRIGGER_THRESHOLD:
        return {"triggered": False, "risk_score": fused["risk_score"]}

    # Generate Gemini explanation
    gemini_explanation = await _generate_gemini_explanation(fused)
    fused["gemini_explanation"] = gemini_explanation

    # Apply freeze and create alert (inserts into Supabase → triggers Realtime)
    freeze_result = await freeze_service.apply_partial_freeze(fused)
    fused["alert_id"] = freeze_result.get("alert_id")

    return {
        "triggered": True,
        "alert_id": fused["alert_id"],
        "risk_score": fused["risk_score"],
        "flag_type": fused["flag_type"],
        "freeze_status": "PARTIAL",
        "frozen_amount": freeze_result["frozen_amount"],
    }


def _fuse_scores(e1: Dict, e2: Dict, txn: Dict) -> Dict:
    e1_score = e1.get("engine1_score", 0)
    e2_score = e2.get("engine2_score", 0)
    ml_add = e2.get("ml_addition", 0)
    total = min(e1_score + e2_score + ml_add, 99)

    # Determine primary flag type (Engine 2 wins if triggered)
    if e2.get("triggered"):
        flag_type = e2.get("flag_type", "SMURFING")
        triggered_by = "ENGINE_2"
        suspicious_amount = e2.get("taint_traced_amount", 0) or e1.get("cumulative_suspicious_amount", 0)
        account_masked = e2.get("central_aggregator_masked") or e1.get("account_id_masked", mask_account_id(txn["account_id"]))
    elif e1.get("triggered"):
        flag_type = "STRUCTURING"
        triggered_by = "ENGINE_1"
        suspicious_amount = e1.get("cumulative_suspicious_amount", 0)
        account_masked = e1.get("account_id_masked", mask_account_id(txn["account_id"]))
    else:
        flag_type = "PROFILE_MISMATCH"
        triggered_by = "GEMINI_ANOMALY"
        suspicious_amount = float(txn.get("amount", 0))
        account_masked = mask_account_id(txn["account_id"])

    return {
        "account_id_masked": account_masked,
        "flag_type": flag_type,
        "risk_score": total,
        "suspicious_amount": suspicious_amount,
        "triggered_by": triggered_by,
        "engine1_score": e1_score,
        "engine2_score": e2_score,
        "ml_addition": ml_add,
        "product_chain": e2.get("product_chain"),
        "subgraph_data": {
            "accounts": e2.get("subgraph_accounts", []),
            "branches": e2.get("branches_per_account", {}),
            "channels": e2.get("channels_per_edge", {}),
            "cycle_detected": e2.get("cycle_detected", False),
            "cycle_velocity": e2.get("cycle_velocity", "NONE"),
            "alert_level": e1.get("alert_level", "NONE"),
            "branches_involved": e1.get("branches_involved", []),
            "cities_involved": e1.get("cities_involved", []),
        },
    }


async def _generate_gemini_explanation(fused: Dict) -> str:
    import google.generativeai as genai
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        prompt = f"""
You are a bank compliance assistant. Explain this fraud alert in plain English for a bank officer.
Keep it under 120 words. Be clear and specific.

Alert Type: {fused['flag_type']}
Risk Score: {fused['risk_score']}/100
Suspicious Amount: ₹{fused['suspicious_amount']:,.2f}
Engine 1 Score: {fused['engine1_score']}/40
Engine 2 Score: {fused['engine2_score']}/60
Gemini AI Addition: +{fused['ml_addition']} points
Product Chain: {fused.get('product_chain', 'N/A')}
"""
        response = await asyncio.wait_for(
            asyncio.to_thread(model.generate_content, prompt),
            timeout=5.0,
        )
        return response.text.strip()
    except Exception:
        return f"Automated detection: {fused['flag_type']} pattern detected with risk score {fused['risk_score']}/100."


# ── SAR Generation ────────────────────────────────────────────────────────────

@app.post("/sar/generate")
async def generate_sar(req: SARRequest, officer: dict = Depends(auth_service.require_any_officer)):
    # Fetch alert
    alert_resp = supabase.table("alerts").select("*").eq("id", req.alert_id).single().execute()
    if not alert_resp.data:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert = alert_resp.data

    # Fetch audit log
    audit_resp = supabase.table("audit_log").select("*").eq("alert_id", req.alert_id).order("timestamp").execute()
    audit_log = audit_resp.data or []

    # Build masked transaction text (simplified — real impl would fetch from Neo4j)
    raw_txn_text = f"""
Account: {alert.get('account_id_masked')}
Flag Type: {alert.get('flag_type')}
Amount: ₹{alert.get('suspicious_amount', 0):,.2f}
Risk Score: {alert.get('risk_score')}
"""
    masked_txn_text, vault = mask_text(raw_txn_text)

    # Generate Gemini narrative (masked)
    narrative_masked = await sar_service.generate_narrative(alert, masked_txn_text)

    # De-mask narrative with real values
    narrative = unmask_text(narrative_masked, vault)

    # Build and upload PDF
    transactions = []  # Would normally fetch from Neo4j subgraph
    download_url, filename = await sar_service.build_pdf_and_upload(
        alert=alert,
        alert_id=req.alert_id,
        graph_image_b64=req.graph_image,
        narrative=narrative,
        transactions=transactions,
        audit_log=audit_log,
    )

    return {"download_url": download_url, "filename": filename}


# ── Demo Scenario Loader ──────────────────────────────────────────────────────

DEMO_SCENARIO_MAP = {
    "DEMO_STRUCTURING": {
        "account_id": "STR_ACC_001",
        "flag_type": "STRUCTURING",
        "risk_score": 72,
        "suspicious_amount": 61_000,
        "triggered_by": "ENGINE_1",
        "gemini_explanation": "Cross-branch structuring detected. A student-profile account (STR_ACC_001) deposited ₹61,000 across 4 cities in 7 days — all amounts just below the ₹20,000 KYC threshold. Branch diversity (Mumbai, Delhi, Bangalore, Chennai) indicates deliberate splitting to evade detection.",
        "product_chain": None,
    },
    "DEMO_SMURFING": {
        "account_id": "AGG_ACC_X",
        "flag_type": "SMURFING",
        "risk_score": 99,
        "suspicious_amount": 2_94_000,
        "triggered_by": "ENGINE_2",
        "gemini_explanation": "Coordinated smurfing network detected. 6 mule accounts transferred ₹49,000 each (just below ₹50,000 threshold) to aggregator AGG_ACC_X within 4 hours across 6 cities. 3 of the 6 mule accounts were dormant for 18+ months — indicating recruited money mules.",
        "product_chain": None,
    },
    "DEMO_ROUNDTRIP": {
        "account_id": "ROUND_A",
        "flag_type": "ROUNDTRIP",
        "risk_score": 95,
        "suspicious_amount": 3_00_000,
        "triggered_by": "ENGINE_2",
        "gemini_explanation": "Circular round-trip layering ring detected. ₹3,00,000 moved through a 3-node cycle (ROUND_A → ROUND_B → ROUND_C → ROUND_A) in just 47 minutes via NEFT. This velocity is characteristic of automated layering scripts. Classified as CRITICAL.",
        "product_chain": "SAVINGS → SAVINGS → SAVINGS → ROUNDTRIP",
    },
    "DEMO_DORMANT": {
        "account_id": "DORM_ACC_001",
        "flag_type": "DORMANT",
        "risk_score": 88,
        "suspicious_amount": 8_50_000,
        "triggered_by": "GEMINI_ANOMALY",
        "gemini_explanation": "Dormant account activation anomaly. Account DORM_ACC_001 (student, low income, age 22) has been inactive for 26 months. A single SWIFT international wire of ₹8,50,000 was received — 85x the account's historical average. Gemini confidence: 91%. Likely account takeover or money mule activation.",
        "product_chain": None,
    },
    "DEMO_PROFILE_MISMATCH": {
        "account_id": "PROF_ACC_001",
        "flag_type": "PROFILE_MISMATCH",
        "risk_score": 83,
        "suspicious_amount": 4_80_000,
        "triggered_by": "GEMINI_ANOMALY",
        "gemini_explanation": "KYC profile mismatch. Student account (age 21, declared income ₹10,000/month) received 18 foreign transfers totalling ₹4,80,000 (48x declared monthly income) — all between 1am–4am on weekdays. Gemini mismatch confidence: 87%. Pattern consistent with international money laundering using student accounts.",
        "product_chain": None,
    },
}


@app.post("/demo/load/{scenario_name}", response_model=DemoLoadResponse)
async def load_demo(scenario_name: str, officer: dict = Depends(auth_service.require_any_officer)):
    if scenario_name not in DEMO_SCENARIO_MAP:
        raise HTTPException(status_code=400, detail=f"Unknown scenario: {scenario_name}")

    scenario = DEMO_SCENARIO_MAP[scenario_name]
    now = datetime.now(UTC)
    from datetime import timedelta

    # Clear any existing demo alerts
    supabase.table("alerts").delete().eq("triggered_by", "ENGINE_1").execute()

    # Create fresh alert
    alert_row = {
        "account_id_masked": mask_account_id(scenario["account_id"]),
        "flag_type": scenario["flag_type"],
        "risk_score": scenario["risk_score"],
        "suspicious_amount": scenario["suspicious_amount"],
        "freeze_status": "PARTIAL",
        "frozen_amount": scenario["suspicious_amount"],
        "ttl_expires_at": (now + timedelta(hours=72)).isoformat(),
        "triggered_by": "ENGINE_1",
        "gemini_explanation": scenario["gemini_explanation"],
        "product_chain": scenario.get("product_chain"),
        "created_at": now.isoformat(),
    }

    resp = supabase.table("alerts").insert(alert_row).execute()
    alert_id = resp.data[0]["id"] if resp.data else None

    if alert_id:
        supabase.table("audit_log").insert({
            "alert_id": alert_id,
            "action_type": "PARTIAL_FREEZE",
            "officer_id": None,
            "officer_role": None,
            "amount_frozen": scenario["suspicious_amount"],
            "risk_score_at_time": scenario["risk_score"],
            "engine_source": "ENGINE_1",
            "timestamp": now.isoformat(),
            "notes": f"Demo scenario loaded: {scenario_name}",
        }).execute()

    return DemoLoadResponse(scenario=scenario_name, status="loaded", alert_id=alert_id)


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn  # type: ignore
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
