"""
gemini_anomaly.py
-----------------
Uses Gemini 2.0 Flash to detect:
  1. Dormant account activation anomalies
  2. KYC profile-vs-behaviour mismatches

No ML training whatsoever — Gemini is the inference engine.
"""

import os
import json
import asyncio
import logging
from typing import Any, Dict, List

import google.generativeai as genai  # type: ignore
from dotenv import load_dotenv  # type: ignore

load_dotenv()

logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# ── Shared Gemini client ──────────────────────────────────────────────────────

def _get_model():
    return genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        generation_config={"response_mime_type": "application/json"},
    )


# ── Safe defaults ─────────────────────────────────────────────────────────────

_DORMANT_DEFAULT = {
    "is_dormant_anomaly": False,
    "dormant_confidence": 0,
    "dormant_reason": "Gemini call failed — safe default returned",
}

_PROFILE_DEFAULT = {
    "is_profile_mismatch": False,
    "mismatch_confidence": 0,
    "mismatch_reason": "Gemini call failed — safe default returned",
    "inflow_to_declared_ratio": 0,
}


# ── Function 1 — Dormant Account Anomaly ─────────────────────────────────────

async def detect_dormant_anomaly(
    account_id: str,
    kyc_profile: Dict[str, Any],
    transaction_history: List[Dict[str, Any]],
    current_transaction: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Returns JSON:
      { is_dormant_anomaly, dormant_confidence, dormant_reason }
    """
    days_inactive = kyc_profile.get("days_since_last_transaction", 0)
    avg_historical = _avg_amount(transaction_history)
    current_amount = current_transaction.get("amount", 0)
    channel = current_transaction.get("channel", "UNKNOWN")
    occupation = kyc_profile.get("occupation", "UNKNOWN")
    income_band = kyc_profile.get("income_band", "UNKNOWN")

    prompt = f"""
You are a bank fraud detection system analysing account activity.

Account details:
- Account ID: {account_id}
- KYC Occupation: {occupation}
- KYC Income Band: {income_band}
- Days since last transaction: {days_inactive}
- Historical average transaction amount: ₹{avg_historical:,.2f}
- Current transaction amount: ₹{current_amount:,.2f}
- Current transaction channel: {channel}
- Number of historical transactions on record: {len(transaction_history)}

Your task:
Determine if this is a dormant account activation anomaly — where a long-inactive account
suddenly receives a large or unexpected transaction inconsistent with its KYC profile.

Respond ONLY with valid JSON matching this exact schema (no markdown, no text outside JSON):
{{
  "is_dormant_anomaly": <true|false>,
  "dormant_confidence": <integer 0-100>,
  "dormant_reason": "<one sentence explanation>"
}}
"""

    try:
        model = _get_model()
        response = await asyncio.to_thread(model.generate_content, prompt)
        result = json.loads(response.text)
        # Validate required keys
        assert "is_dormant_anomaly" in result
        assert "dormant_confidence" in result
        assert "dormant_reason" in result
        return result
    except Exception as e:
        logger.warning(f"detect_dormant_anomaly failed for {account_id}: {e}")
        return _DORMANT_DEFAULT.copy()


# ── Function 2 — Profile Mismatch ────────────────────────────────────────────

async def detect_profile_mismatch(
    account_id: str,
    kyc_profile: Dict[str, Any],
    recent_transactions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Returns JSON:
      { is_profile_mismatch, mismatch_confidence, mismatch_reason, inflow_to_declared_ratio }
    """
    occupation = kyc_profile.get("occupation", "UNKNOWN")
    income_band = kyc_profile.get("income_band", "UNKNOWN")
    declared_monthly = kyc_profile.get("declared_monthly_income", 0)
    age = kyc_profile.get("age", 0)

    total_inflow = sum(t.get("amount", 0) for t in recent_transactions)
    foreign_senders = sum(1 for t in recent_transactions if t.get("sender_type") == "FOREIGN")

    # Build compact transaction list for prompt (don't send full objects)
    txn_summary = []
    for t in recent_transactions[:30]:  # type: ignore
        txn_summary.append(
            f"  - ₹{t.get('amount', 0):,.0f} via {t.get('channel', '?')} "
            f"at {t.get('timestamp', '?')} from {t.get('sender_type', 'LOCAL')}"
        )
    txn_text = "\n".join(txn_summary) if txn_summary else "  (no recent transactions)"

    ratio = round(float(total_inflow / declared_monthly), 1) if declared_monthly > 0 else 0.0

    prompt = f"""
You are a bank fraud detection system performing KYC behaviour analysis.

Account details:
- Account ID: {account_id}
- Age: {age}
- Occupation: {occupation}
- Income Band: {income_band}
- Declared Monthly Income: ₹{declared_monthly:,.2f}

Last 30 days activity:
{txn_text}

Summary statistics:
- Total inflow last 30 days: ₹{total_inflow:,.2f}
- Number of foreign senders: {foreign_senders}
- Inflow-to-declared-income ratio: {ratio}x

Your task:
Determine if there is a suspicious mismatch between this account's declared KYC profile
and its actual transactional behaviour. Look for: unusual hours, foreign sources,
income far exceeding declared levels, patterns inconsistent with stated occupation.

Respond ONLY with valid JSON matching this exact schema (no markdown, no text outside JSON):
{{
  "is_profile_mismatch": <true|false>,
  "mismatch_confidence": <integer 0-100>,
  "mismatch_reason": "<one sentence explanation>",
  "inflow_to_declared_ratio": <number>
}}
"""

    try:
        model = _get_model()
        response = await asyncio.to_thread(model.generate_content, prompt)
        result = json.loads(response.text)
        assert "is_profile_mismatch" in result
        assert "mismatch_confidence" in result
        return result
    except Exception as e:
        logger.warning(f"detect_profile_mismatch failed for {account_id}: {e}")
        return _PROFILE_DEFAULT.copy()


# ── Helper ────────────────────────────────────────────────────────────────────

def _avg_amount(transactions: List[Dict[str, Any]]) -> float:
    if not transactions:
        return 0.0
    amounts = [t.get("amount", 0) for t in transactions]
    return sum(amounts) / len(amounts)
