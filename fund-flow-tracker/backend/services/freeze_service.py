"""
freeze_service.py
-----------------
Applies partial freeze to flagged accounts.
Manages TTL background tasks and auto-escalation/release.
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPA_URL, SUPA_KEY)

UTC = timezone.utc


# ── Apply partial freeze ──────────────────────────────────────────────────────

async def apply_partial_freeze(fused_alert: Dict[str, Any]) -> Dict[str, Any]:
    account_id_masked = fused_alert["account_id_masked"]
    suspicious_amount = float(fused_alert.get("suspicious_amount", 0))
    risk_score = int(fused_alert.get("risk_score", 0))
    engine_source = fused_alert.get("triggered_by", "ENGINE_1")

    # Determine TTL and auto-action
    now = datetime.now(UTC)
    if risk_score > 80:
        ttl = now + timedelta(hours=72)
        auto_action = "ESCALATE"
    elif risk_score >= 50:
        ttl = now + timedelta(hours=72)
        auto_action = "RELEASE"
    else:
        ttl = now + timedelta(hours=24)
        auto_action = "RELEASE"

    # Update accounts table (lien)
    try:
        # Get current balance
        acc_resp = supabase.table("accounts") \
            .select("available_balance, lien_amount") \
            .eq("account_id", account_id_masked) \
            .maybe_single().execute()

        if acc_resp.data:
            current_balance = float(acc_resp.data.get("available_balance", 0))
            current_lien = float(acc_resp.data.get("lien_amount", 0))
            freeze_amount = min(suspicious_amount, current_balance)

            supabase.table("accounts").update({
                "available_balance": current_balance - freeze_amount,
                "lien_amount": current_lien + freeze_amount,
                "account_status": "FROZEN",
            }).eq("account_id", account_id_masked).execute()
        else:
            freeze_amount = suspicious_amount
    except Exception as e:
        logger.warning(f"Account balance update failed: {e}")
        freeze_amount = suspicious_amount

    # Build alert row
    gemini_explanation = fused_alert.get("gemini_explanation", "")
    alert_row = {
        "account_id_masked": account_id_masked,
        "flag_type": fused_alert.get("flag_type", "STRUCTURING"),
        "risk_score": risk_score,
        "suspicious_amount": suspicious_amount,
        "freeze_status": "PARTIAL",
        "frozen_amount": freeze_amount,
        "ttl_expires_at": ttl.isoformat(),
        "triggered_by": engine_source,
        "gemini_explanation": gemini_explanation,
        "product_chain": fused_alert.get("product_chain"),
        "created_at": now.isoformat(),
    }

    alert_resp = supabase.table("alerts").insert(alert_row).execute()
    alert_id = alert_resp.data[0]["id"] if alert_resp.data else None

    # Write audit log entry
    if alert_id:
        supabase.table("audit_log").insert({
            "alert_id": alert_id,
            "action_type": "PARTIAL_FREEZE",
            "officer_id": None,
            "officer_role": None,
            "amount_frozen": freeze_amount,
            "risk_score_at_time": risk_score,
            "engine_source": engine_source,
            "timestamp": now.isoformat(),
            "notes": f"Auto-freeze triggered by {engine_source} — Risk Score {risk_score}",
        }).execute()

    return {
        "alert_id": alert_id,
        "freeze_status": "PARTIAL",
        "frozen_amount": freeze_amount,
        "ttl_expires_at": ttl.isoformat(),
        "auto_action": auto_action,
    }


# ── TTL Background Worker ─────────────────────────────────────────────────────

async def ttl_worker():
    """Runs every 30 minutes, processes expired TTLs."""
    while True:
        try:
            await _process_expired_ttls()
        except Exception as e:
            logger.error(f"TTL worker error: {e}")
        await asyncio.sleep(30 * 60)  # 30 minutes


async def _process_expired_ttls():
    now = datetime.now(UTC).isoformat()

    # Fetch all PARTIAL alerts with expired TTL
    resp = supabase.table("alerts") \
        .select("id, account_id_masked, risk_score, frozen_amount, triggered_by") \
        .eq("freeze_status", "PARTIAL") \
        .lt("ttl_expires_at", now) \
        .execute()

    if not resp.data:
        return

    for alert in resp.data:
        alert_id = alert["id"]
        risk_score = int(alert.get("risk_score", 0))
        frozen_amount = float(alert.get("frozen_amount", 0))
        account_id_masked = alert.get("account_id_masked")

        if risk_score > 80:
            # ESCALATE → Full freeze
            supabase.table("alerts").update({
                "freeze_status": "FULL",
            }).eq("id", alert_id).execute()

            supabase.table("accounts").update({
                "account_status": "FROZEN",
            }).eq("account_id", account_id_masked).execute()

            supabase.table("audit_log").insert({
                "alert_id": alert_id,
                "action_type": "AUTO_ESCALATE",
                "officer_id": None,
                "amount_frozen": frozen_amount,
                "risk_score_at_time": risk_score,
                "engine_source": alert.get("triggered_by", "SYSTEM"),
                "timestamp": datetime.now(UTC).isoformat(),
                "notes": "SYSTEM_AUTO_ESCALATE — TTL expired, high risk score",
            }).execute()

            logger.info(f"Auto-escalated alert {alert_id}")
        else:
            # RELEASE → restore balance
            try:
                acc_resp = supabase.table("accounts") \
                    .select("available_balance, lien_amount") \
                    .eq("account_id", account_id_masked) \
                    .maybe_single().execute()

                if acc_resp.data:
                    bal = float(acc_resp.data.get("available_balance", 0))
                    lien = float(acc_resp.data.get("lien_amount", 0))
                    supabase.table("accounts").update({
                        "available_balance": bal + frozen_amount,
                        "lien_amount": max(0, lien - frozen_amount),
                        "account_status": "ACTIVE",
                    }).eq("account_id", account_id_masked).execute()
            except Exception as e:
                logger.warning(f"Balance restore failed: {e}")

            supabase.table("alerts").update({
                "freeze_status": "RELEASED",
            }).eq("id", alert_id).execute()

            supabase.table("audit_log").insert({
                "alert_id": alert_id,
                "action_type": "AUTO_RELEASE",
                "officer_id": None,
                "amount_frozen": 0,
                "risk_score_at_time": risk_score,
                "engine_source": "SYSTEM",
                "timestamp": datetime.now(UTC).isoformat(),
                "notes": "SYSTEM_AUTO_RELEASE — TTL expired, low risk score",
            }).execute()

            logger.info(f"Auto-released alert {alert_id}")
