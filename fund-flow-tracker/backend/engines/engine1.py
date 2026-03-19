"""
engine1.py
----------
Structuring Detection via sliding-window sum + dynamic KYC thresholds.
Optionally activates heightened mode when Gemini flags dormant/profile issues.
Max contribution: 40 points.
"""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from neo4j import AsyncGraphDatabase  # type: ignore
from supabase import create_client, Client  # type: ignore
from dotenv import load_dotenv  # type: ignore

from engines.gemini_anomaly import detect_dormant_anomaly, detect_profile_mismatch  # type: ignore
from services.presidio_service import mask_account_id  # type: ignore

load_dotenv()

logger = logging.getLogger(__name__)

# ── DB Clients ────────────────────────────────────────────────────────────────

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD")
SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPA_URL, SUPA_KEY)

# Dynamic thresholds per occupation
_THRESHOLDS = {
    "STUDENT":  {"normal": 20_000, "heightened": 10_000},
    "SALARIED": {"normal": 40_000, "heightened": 20_000},
    "BUSINESS": {"normal": 50_000, "heightened": 25_000},
    "UNKNOWN":  {"normal": 15_000, "heightened":  7_500},
}

# Branch → city mapping (populated by faker_generator)
_BRANCH_CITY: Dict[str, str] = {}

BRANCH_CITY_MAP = {
    "BRN_MUM_042": "Mumbai",
    "BRN_MUM_011": "Mumbai",
    "BRN_MUM_007": "Mumbai",
    "BRN_DEL_011": "Delhi",
    "BRN_DEL_042": "Delhi",
    "BRN_BLR_007": "Bangalore",
    "BRN_BLR_042": "Bangalore",
    "BRN_HYD_005": "Hyderabad",
    "BRN_HYD_011": "Hyderabad",
    "BRN_CHN_003": "Chennai",
    "BRN_CHN_011": "Chennai",
    "BRN_KOL_009": "Kolkata",
    "BRN_KOL_042": "Kolkata",
}


# ── Main analyse function ─────────────────────────────────────────────────────

async def analyse(transaction: Dict[str, Any]) -> Dict[str, Any]:
    account_id = transaction["account_id"]
    amount = float(transaction["amount"])
    branch_id = transaction.get("branch_id", "UNKNOWN")
    channel = transaction.get("channel", "UNKNOWN")
    txn_time = transaction["timestamp"]
    if isinstance(txn_time, str):
        txn_time = datetime.fromisoformat(txn_time)

    # Step 2 — Pull KYC from Supabase
    kyc = _fetch_kyc(account_id)

    # Step 3 — Run Gemini anomaly checks in parallel
    history = await _fetch_history_for_gemini(account_id)
    dormant_result, profile_result = await asyncio.gather(
        detect_dormant_anomaly(account_id, kyc, history, transaction),
        detect_profile_mismatch(account_id, kyc, history),
    )

    gemini_dormant = dormant_result.get("is_dormant_anomaly", False)
    gemini_profile = profile_result.get("is_profile_mismatch", False)
    heightened_mode = gemini_dormant or gemini_profile

    # Step 4 — Dynamic threshold
    occupation = kyc.get("occupation", "UNKNOWN").upper()
    mode = "heightened" if heightened_mode else "normal"
    threshold = _THRESHOLDS.get(occupation, _THRESHOLDS["UNKNOWN"])[mode]

    # Step 5 — Sliding window (7-day, two-pointer)
    all_deposits = await _fetch_deposits_neo4j(account_id)
    window_result = _sliding_window(all_deposits, txn_time, threshold)

    if not window_result["triggered"]:
        return _no_trigger(account_id)

    # Step 6 — Alert level
    branches_in_window = window_result["branches"]
    alert_level = "LEVEL_1_B" if len(branches_in_window) >= 3 else "LEVEL_1"

    # Step 7 — Risk score contribution
    window_sum = window_result["window_sum"]
    engine1_score = _calculate_score(window_sum, threshold, alert_level)

    # Step 8 — Build result
    cities = list({BRANCH_CITY_MAP.get(b, "Unknown") for b in branches_in_window})

    return {
        "triggered": True,
        "account_id_masked": mask_account_id(account_id),
        "cumulative_suspicious_amount": window_sum,
        "transaction_count": window_result["count"],
        "branches_involved": list(branches_in_window),
        "cities_involved": cities,
        "channels_used": window_result["channels"],
        "time_span": window_result["time_span"],
        "kyc_profile": {
            "occupation": kyc.get("occupation"),
            "income_band": kyc.get("income_band"),
            "account_type": "SAVINGS",
        },
        "alert_level": alert_level,
        "heightened_mode": heightened_mode,
        "gemini_dormant_flag": gemini_dormant,
        "gemini_profile_flag": gemini_profile,
        "engine1_score": engine1_score,
        "flag_type": "STRUCTURING",
        "triggered_by": "ENGINE_1",
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_kyc(account_id: str) -> Dict[str, Any]:
    try:
        resp = supabase.table("kyc_profiles").select("*").eq("account_id", account_id).single().execute()
        return resp.data or {}
    except Exception as e:
        logger.warning(f"KYC fetch failed for {account_id}: {e}")
        return {"occupation": "UNKNOWN", "income_band": "LOW", "declared_monthly_income": 0}


async def _fetch_history_for_gemini(account_id: str) -> List[Dict[str, Any]]:
    """Fetch last 50 transactions for this account from Neo4j for Gemini context."""
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    try:
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Account {account_id: $aid})-[t:TRANSFERRED_TO]->()
                RETURN t.amount AS amount, t.timestamp AS timestamp,
                       t.channel AS channel, t.branch_id AS branch_id
                ORDER BY t.timestamp DESC LIMIT 50
                """,
                aid=account_id,
            )
            records = await result.data()
            return [dict(r) for r in records]
    except Exception as e:
        logger.warning(f"Neo4j history fetch failed: {e}")
        return []
    finally:
        await driver.close()
    return []


async def _fetch_deposits_neo4j(account_id: str) -> List[Dict[str, Any]]:
    """Fetch all incoming deposit transactions for sliding window."""
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    try:
        async with driver.session() as session:
            result = await session.run(
                """
                MATCH ()-[t:TRANSFERRED_TO]->(a:Account {account_id: $aid})
                RETURN t.amount AS amount, t.timestamp AS ts,
                       t.branch_id AS branch_id, t.channel AS channel
                ORDER BY t.timestamp ASC
                """,
                aid=account_id,
            )
            records = await result.data()
            return [dict(r) for r in records]
    except Exception as e:
        logger.warning(f"Neo4j deposit fetch failed: {e}")
        return []
    finally:
        await driver.close()
    return []


def _sliding_window(
    deposits: List[Dict[str, Any]],
    current_time: datetime,
    threshold: float,
) -> Dict[str, Any]:
    """Two-pointer 7-day sliding window."""
    if not deposits:
        return {"triggered": False}

    def _parse_ts(ts):
        if isinstance(ts, datetime):
            return ts
        if hasattr(ts, "to_native"):
            return ts.to_native()
        return datetime.fromisoformat(str(ts))

    deposits_sorted = sorted(deposits, key=lambda d: _parse_ts(d["ts"]))

    left = 0
    current_sum = 0.0
    branches: Dict[str, int] = {}  # branch → count in window
    channels: List[str] = []
    best: Optional[Dict] = None

    for right in range(len(deposits_sorted)):
        d = deposits_sorted[right]
        current_sum += float(d["amount"])
        br = d.get("branch_id", "UNKNOWN")
        branches[br] = branches.get(br, 0) + 1
        channels.append(d.get("channel", "UNKNOWN"))

        r_time = _parse_ts(d["ts"])

        # Shrink window if > 7 days
        while left <= right:
            l_time = _parse_ts(deposits_sorted[left]["ts"])
            if (r_time - l_time).days > 7:
                old = deposits_sorted[left]
                current_sum -= float(old["amount"])
                old_br = old.get("branch_id", "UNKNOWN")
                branches[old_br] -= 1
                if branches[old_br] == 0:
                    branches.pop(old_br, None)
                left += 1  # type: ignore
            else:
                break

        if current_sum >= threshold:
            l_time = _parse_ts(deposits_sorted[left]["ts"])
            best = {
                "triggered": True,
                "window_sum": current_sum,
                "count": right - left + 1,  # type: ignore
                "branches": set(branches.keys()),
                "channels": list(set(channels[left : right + 1])),  # type: ignore
                "time_span": f"{l_time.date()} to {r_time.date()}",
            }

    if best is not None:
        return best
    return {"triggered": False}


def _calculate_score(window_sum: float, threshold: float, alert_level: str) -> int:
    ratio = window_sum / threshold
    if ratio < 1.2:
        score = 15
    elif ratio < 1.5:
        score = 25
    else:
        score = 35
    if alert_level == "LEVEL_1_B":
        score += 5
    return min(score, 40)


def _no_trigger(account_id: str) -> Dict[str, Any]:
    return {
        "triggered": False,
        "account_id_masked": mask_account_id(account_id),
        "cumulative_suspicious_amount": 0,
        "transaction_count": 0,
        "branches_involved": [],
        "cities_involved": [],
        "channels_used": [],
        "time_span": "",
        "kyc_profile": {},
        "alert_level": "NONE",
        "heightened_mode": False,
        "gemini_dormant_flag": False,
        "gemini_profile_flag": False,
        "engine1_score": 0,
        "flag_type": "NONE",
        "triggered_by": "ENGINE_1",
    }
