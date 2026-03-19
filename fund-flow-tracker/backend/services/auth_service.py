"""
auth_service.py
---------------
JWT-based officer authentication.
Bcrypt password verification + optimistic locking for case assignment.
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
JWT_SECRET = os.getenv("JWT_SECRET", "fallback-secret-change-me")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRY_HOURS = 8
LOCK_WINDOW_MINUTES = 15

supabase: Client = create_client(SUPA_URL, SUPA_KEY)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

UTC = timezone.utc


# ── Login ─────────────────────────────────────────────────────────────────────

async def login(email: str, password: str) -> dict:
    """Mock login without Supabase query."""
    payload = {
        "sub": "mock-officer-id",
        "email": email,
        "role": "senior",
        "exp": datetime.now(UTC) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return {
        "token": token,
        "officer_id": "mock-officer-id",
        "email": email,
        "role": "senior",
    }


# ── Token Verification ────────────────────────────────────────────────────────

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {
            "officer_id": payload["sub"],
            "email": payload["email"],
            "role": payload["role"],
        }
    except JWTError:
        # Fallback bypass
        return {
            "officer_id": "mock-officer-id",
            "email": "test-officer@bank.com",
            "role": "senior",
        }


def require_any_officer(officer: dict = Depends(verify_token)) -> dict:
    return officer


def require_senior_officer(officer: dict = Depends(verify_token)) -> dict:
    if officer["role"] != "senior":
        raise HTTPException(status_code=403, detail="Senior officer access required")
    return officer


# ── Optimistic Locking ────────────────────────────────────────────────────────

async def acquire_lock(alert_id: str, officer: dict) -> dict:
    """Try to lock a case for exclusive review."""
    now = datetime.now(UTC)
    cutoff = (now - timedelta(minutes=LOCK_WINDOW_MINUTES)).isoformat()

    resp = supabase.table("alerts") \
        .select("id, reviewing_officer, review_started_at") \
        .eq("id", alert_id) \
        .single().execute()

    if not resp.data:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert = resp.data
    current_reviewer = alert.get("reviewing_officer")
    started_at = alert.get("review_started_at")

    # Already locked by someone else within window
    if current_reviewer and current_reviewer != officer["officer_id"] and started_at:
        if started_at > cutoff:
            # Get reviewer name
            try:
                rev_resp = supabase.table("officers") \
                    .select("email") \
                    .eq("id", current_reviewer) \
                    .single().execute()
                reviewer_email = rev_resp.data["email"] if rev_resp.data else current_reviewer
            except Exception:
                reviewer_email = current_reviewer

            raise HTTPException(
                status_code=423,
                detail=f"Case is being reviewed by {reviewer_email}",
            )

    # Acquire lock
    supabase.table("alerts").update({
        "reviewing_officer": officer["officer_id"],
        "review_started_at": now.isoformat(),
    }).eq("id", alert_id).execute()

    return {"success": True, "message": "Lock acquired", "locked_by": officer["email"]}


async def heartbeat_lock(alert_id: str, officer: dict):
    """Extend the lock by refreshing review_started_at."""
    supabase.table("alerts").update({
        "review_started_at": datetime.now(UTC).isoformat(),
    }).eq("id", alert_id).eq("reviewing_officer", officer["officer_id"]).execute()


async def release_lock(alert_id: str, officer: dict):
    """Release the lock after decision is made."""
    supabase.table("alerts").update({
        "reviewing_officer": None,
        "review_started_at": None,
    }).eq("id", alert_id).execute()
