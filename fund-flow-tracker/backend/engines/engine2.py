"""
engine2.py
----------
Smurfing, Round-Tripping, Product-Switching detection via Neo4j graph queries.
Check A → Smurfing (in-degree + branch diversity)
Check B → Round-Trip / Layering (DFS cycle + time velocity)
Check C → Product-Switching (distinct product categories in subgraph)
Max contribution: 60 points + up to 20 Gemini ML addition.
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from neo4j import AsyncGraphDatabase
from supabase import create_client, Client
from dotenv import load_dotenv

from engines.gemini_anomaly import detect_dormant_anomaly, detect_profile_mismatch
from services.presidio_service import mask_account_id

load_dotenv()

logger = logging.getLogger(__name__)

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD")
SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPA_URL, SUPA_KEY)

PRODUCT_CATEGORIES = {
    "SAVINGS": "Deposit", "CURRENT": "Deposit", "FD": "Deposit",
    "CREDIT": "Credit", "OVERDRAFT": "Credit", "LOAN": "Credit",
    "WALLET": "Wallet", "PREPAID": "Wallet",
    "MUTUAL_FUND": "Investment", "DEMAT": "Investment",
}

CITY_MAP = {
    "BRN_MUM_042": "Mumbai", "BRN_MUM_011": "Mumbai",
    "BRN_DEL_011": "Delhi",  "BRN_DEL_042": "Delhi",
    "BRN_BLR_007": "Bangalore", "BRN_BLR_042": "Bangalore",
    "BRN_HYD_005": "Hyderabad", "BRN_HYD_011": "Hyderabad",
    "BRN_CHN_003": "Chennai",  "BRN_CHN_011": "Chennai",
    "BRN_KOL_009": "Kolkata",  "BRN_KOL_042": "Kolkata",
}


# ── Main ──────────────────────────────────────────────────────────────────────

async def analyse(transaction: Dict[str, Any]) -> Dict[str, Any]:
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    try:
        async with driver.session() as session:
            smurfing = await _check_smurfing(session, transaction)
            roundtrip = await _check_roundtrip(session, transaction)

            # Combine subgraph accounts
            all_accounts: Set[str] = set()
            all_accounts.update(smurfing.get("accounts", []))
            all_accounts.update(roundtrip.get("cycle_accounts", []))
            all_accounts.add(transaction["account_id"])

            product_switch = await _check_product_switching(session, list(all_accounts))

            best_score = max(smurfing.get("score", 0), roundtrip.get("score", 0))
            detection_type = smurfing.get("type", "") or roundtrip.get("type", "")

            if best_score == 0:
                return _no_trigger(transaction["account_id"])

            ps_score = 15 if product_switch["triggered"] else 0
            engine2_score = min(best_score + ps_score, 60)

            # Gemini ML fusion
            gemini_flags = await _run_gemini_fusion(list(all_accounts))
            ml_addition = _ml_score(gemini_flags)

            aggregator = smurfing.get("aggregator") or roundtrip.get("cycle_accounts", [None])[0]
            taint = smurfing.get("taint_amount", 0) or roundtrip.get("cycle_amount", 0)

            return {
                "triggered": True,
                "central_aggregator_masked": mask_account_id(aggregator) if aggregator else None,
                "subgraph_accounts": list(all_accounts),
                "branches_per_account": smurfing.get("branches_per_account", {}),
                "channels_per_edge": smurfing.get("channels_per_edge", {}),
                "product_chain": product_switch.get("chain"),
                "product_switch_count": product_switch.get("category_count", 0),
                "taint_traced_amount": taint,
                "time_window": smurfing.get("time_window") or roundtrip.get("time_window", ""),
                "cycle_detected": roundtrip.get("cycle_detected", False),
                "cycle_velocity": roundtrip.get("velocity", "NONE"),
                "gemini_flags_per_account": gemini_flags,
                "engine2_score": engine2_score,
                "ml_addition": ml_addition,
                "detection_type": _flag_type(detection_type, product_switch["triggered"]),
                "flag_type": _flag_type(detection_type, product_switch["triggered"]),
                "triggered_by": "ENGINE_2",
            }
    finally:
        await driver.close()


# ── Check A — Smurfing ────────────────────────────────────────────────────────

async def _check_smurfing(session, transaction: Dict[str, Any]) -> Dict[str, Any]:
    account_id = transaction["account_id"]
    cutoff = datetime.utcnow() - timedelta(hours=24)

    result = await session.run(
        """
        MATCH ()-[t:TRANSFERRED_TO]->(agg:Account {account_id: $aid})
        WHERE t.amount < 50000
          AND t.timestamp > $cutoff
        RETURN t.amount AS amount, t.branch_id AS branch, t.channel AS channel,
               t.timestamp AS ts
        """,
        aid=account_id,
        cutoff=cutoff.isoformat(),
    )
    rows = await result.data()

    if len(rows) < 5:
        return {"score": 0}

    branches = {r["branch"] for r in rows}
    cities = {CITY_MAP.get(b, "Unknown") for b in branches}
    n_cities = len(cities)
    taint = sum(float(r["amount"]) for r in rows)

    if n_cities >= 5:
        score = 60
    elif n_cities >= 3:
        score = 50
    else:
        score = 30

    # Build lookup maps for frontend
    branches_per_account = {account_id: f"{list(branches)[0]}/{list(cities)[0]}"}
    channels_per_edge = {f"edge_{i}": r["channel"] for i, r in enumerate(rows)}

    timestamps = [r["ts"] for r in rows if r["ts"]]
    time_window = ""
    if timestamps:
        time_window = f"{min(timestamps)} to {max(timestamps)}"

    return {
        "score": score,
        "type": "SMURFING",
        "aggregator": account_id,
        "accounts": [account_id],
        "taint_amount": taint,
        "branches_per_account": branches_per_account,
        "channels_per_edge": channels_per_edge,
        "time_window": time_window,
    }


# ── Check B — Round-Trip / DFS Cycle ─────────────────────────────────────────

async def _check_roundtrip(session, transaction: Dict[str, Any]) -> Dict[str, Any]:
    account_id = transaction["account_id"]

    # Load subgraph (2-hop) around account
    result = await session.run(
        """
        MATCH (a:Account {account_id: $aid})-[t1:TRANSFERRED_TO]->(b:Account)
              -[t2:TRANSFERRED_TO]->(c:Account)
        RETURN a.account_id AS a, b.account_id AS b, c.account_id AS c,
               t1.timestamp AS ts1, t2.timestamp AS ts2,
               t1.amount AS amt1, t2.amount AS amt2
        LIMIT 200
        """,
        aid=account_id,
    )
    rows = await result.data()

    # Check if c == a (simple 3-node cycle)
    cycles = [r for r in rows if r["c"] == account_id]
    if not cycles:
        return {"score": 0, "cycle_detected": False}

    best = cycles[0]
    ts1 = _parse_ts(best["ts1"])
    ts2 = _parse_ts(best["ts2"])
    if ts1 and ts2:
        minutes = (ts2 - ts1).total_seconds() / 60
    else:
        minutes = 9999

    if minutes <= 120:
        score = 60
        velocity = "CRITICAL"
    elif minutes <= 1440:
        score = 45
        velocity = "HIGH"
    elif minutes <= 10080:
        score = 25
        velocity = "MEDIUM"
    else:
        return {"score": 0, "cycle_detected": False}

    cycle_amount = float(best["amt1"]) + float(best["amt2"])
    time_window = f"{ts1} to {ts2}" if ts1 and ts2 else ""

    return {
        "score": score,
        "type": "ROUNDTRIP",
        "cycle_detected": True,
        "velocity": velocity,
        "cycle_accounts": [best["a"], best["b"], best["c"]],
        "cycle_amount": cycle_amount,
        "time_window": time_window,
    }


# ── Check C — Product Switching ───────────────────────────────────────────────

async def _check_product_switching(session, account_ids: List[str]) -> Dict[str, Any]:
    if not account_ids:
        return {"triggered": False}

    result = await session.run(
        """
        MATCH (a:Account)
        WHERE a.account_id IN $ids
        RETURN a.account_id AS id, a.product_type AS product
        """,
        ids=account_ids,
    )
    rows = await result.data()

    products = [r["product"] for r in rows if r.get("product")]
    categories = {PRODUCT_CATEGORIES.get(p, "Unknown") for p in products}
    categories.discard("Unknown")

    if len(categories) < 3:
        return {"triggered": False, "category_count": len(categories)}

    chain = " → ".join(products[:6])
    return {
        "triggered": True,
        "category_count": len(categories),
        "chain": chain,
    }


# ── Gemini ML fusion ──────────────────────────────────────────────────────────

async def _run_gemini_fusion(account_ids: List[str]) -> Dict[str, Any]:
    flags = {}
    tasks = []

    async def _check_one(aid: str):
        kyc = _fetch_kyc(aid)
        dormant, profile = await asyncio.gather(
            detect_dormant_anomaly(aid, kyc, [], {}),
            detect_profile_mismatch(aid, kyc, []),
        )
        flags[aid] = {"dormant": dormant, "profile": profile}

    for aid in account_ids[:10]:  # Cap at 10 accounts per subgraph
        tasks.append(_check_one(aid))

    await asyncio.gather(*tasks, return_exceptions=True)
    return flags


def _ml_score(flags: Dict[str, Any]) -> int:
    dormant_high = sum(
        1 for v in flags.values()
        if v.get("dormant", {}).get("dormant_confidence", 0) > 70
    )
    profile_high = sum(
        1 for v in flags.values()
        if v.get("profile", {}).get("mismatch_confidence", 0) > 70
    )

    if dormant_high > 1 and profile_high > 1:
        return 20
    if dormant_high > 1 or profile_high > 1:
        return 15
    if dormant_high == 1 or profile_high == 1:
        return 8
    return 0


def _fetch_kyc(account_id: str) -> Dict[str, Any]:
    try:
        resp = supabase.table("kyc_profiles").select("*").eq("account_id", account_id).single().execute()
        return resp.data or {}
    except Exception:
        return {"occupation": "UNKNOWN", "income_band": "LOW", "declared_monthly_income": 0}


def _flag_type(detection: str, ps: bool) -> str:
    if ps and detection:
        return "PRODUCT_SWITCHING"
    return detection or "SMURFING"


def _parse_ts(ts) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _no_trigger(account_id: str) -> Dict[str, Any]:
    return {
        "triggered": False,
        "central_aggregator_masked": None,
        "subgraph_accounts": [],
        "branches_per_account": {},
        "channels_per_edge": {},
        "product_chain": None,
        "product_switch_count": 0,
        "taint_traced_amount": 0.0,
        "time_window": "",
        "cycle_detected": False,
        "cycle_velocity": "NONE",
        "gemini_flags_per_account": {},
        "engine2_score": 0,
        "ml_addition": 0,
        "detection_type": "",
        "flag_type": "NONE",
        "triggered_by": "ENGINE_2",
    }
