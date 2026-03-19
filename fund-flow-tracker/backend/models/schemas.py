from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime


# ── Auth ────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str

class LoginResponse(BaseModel):
    token: str
    officer_id: str
    email: str
    role: str

class OfficerOut(BaseModel):
    officer_id: str
    email: str
    role: str


# ── Transaction Ingest ───────────────────────────────────────────────────────

class Transaction(BaseModel):
    account_id: str
    amount: float
    timestamp: datetime
    branch_id: str
    channel: str            # ATM / UPI / NEFT / BRANCH / MOBILE_APP / SWIFT
    product_type: str       # SAVINGS / CURRENT / CREDIT / WALLET / FD
    transaction_type: str   # DEPOSIT / TRANSFER
    recipient_account_id: Optional[str] = None


# ── KYC Profile ──────────────────────────────────────────────────────────────

class KYCProfile(BaseModel):
    account_id: str
    occupation: str         # STUDENT / SALARIED / BUSINESS / UNKNOWN
    income_band: str        # LOW / MID / HIGH
    age: int
    city: str
    is_dormant: bool
    last_transaction_date: Optional[str] = None
    declared_monthly_income: float


# ── Engine Results ────────────────────────────────────────────────────────────

class Engine1Result(BaseModel):
    triggered: bool
    account_id_masked: str
    cumulative_suspicious_amount: float
    transaction_count: int
    branches_involved: List[str]
    cities_involved: List[str]
    channels_used: List[str]
    time_span: str
    kyc_profile: Dict[str, Any]
    alert_level: str            # LEVEL_1 / LEVEL_1_B
    heightened_mode: bool
    gemini_dormant_flag: bool
    gemini_profile_flag: bool
    engine1_score: int          # 0–40

class Engine2Result(BaseModel):
    triggered: bool
    central_aggregator_masked: Optional[str] = None
    subgraph_accounts: List[str] = []
    branches_per_account: Dict[str, str] = {}
    channels_per_edge: Dict[str, str] = {}
    product_chain: Optional[str] = None
    product_switch_count: int = 0
    taint_traced_amount: float = 0.0
    time_window: str = ""
    cycle_detected: bool = False
    cycle_velocity: str = "NONE"  # CRITICAL / HIGH / MEDIUM / NONE
    gemini_flags_per_account: Dict[str, Any] = {}
    engine2_score: int = 0       # 0–60
    ml_addition: int = 0         # 0 / 8 / 15 / 20
    detection_type: str = ""


# ── Fused Alert ───────────────────────────────────────────────────────────────

class FusedAlert(BaseModel):
    account_id_masked: str
    flag_type: str
    risk_score: int
    suspicious_amount: float
    freeze_status: str = "NONE"
    frozen_amount: float = 0.0
    ttl_expires_at: Optional[datetime] = None
    triggered_by: str
    gemini_explanation: Optional[str] = None
    product_chain: Optional[str] = None
    engine1_score: int = 0
    engine2_score: int = 0
    ml_addition: int = 0
    subgraph_data: Optional[Dict[str, Any]] = None


# ── SAR ───────────────────────────────────────────────────────────────────────

class SARRequest(BaseModel):
    alert_id: str
    graph_image: str    # base64 encoded PNG


class SARResponse(BaseModel):
    download_url: str
    filename: str


# ── Demo ─────────────────────────────────────────────────────────────────────

class DemoLoadResponse(BaseModel):
    scenario: str
    status: str
    alert_id: Optional[str] = None


# ── Alert Update Actions ──────────────────────────────────────────────────────

class LockResponse(BaseModel):
    success: bool
    locked_by: Optional[str] = None
    message: str

class ActionResponse(BaseModel):
    success: bool
    message: str
    new_freeze_status: Optional[str] = None
    new_risk_score: Optional[int] = None
