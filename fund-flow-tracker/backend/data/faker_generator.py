"""
faker_generator.py
------------------
Generates 10,000 background transactions + 5 hardcoded fraud demo scenarios.
Run once: python backend/data/faker_generator.py
"""

import os
import sys
import random
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Add parent dir to path so we can import from backend root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from faker import Faker
from neo4j import GraphDatabase
from supabase import create_client

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

fake = Faker("en_IN")

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD")
SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPA_URL, SUPA_KEY)
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

UTC = timezone.utc

# ── Configuration ─────────────────────────────────────────────────────────────

BRANCHES = [
    ("BRN_MUM_042", "Andheri West", "Mumbai"),
    ("BRN_MUM_011", "Bandra", "Mumbai"),
    ("BRN_MUM_007", "Dadar", "Mumbai"),
    ("BRN_DEL_011", "Connaught Place", "Delhi"),
    ("BRN_DEL_042", "Lajpat Nagar", "Delhi"),
    ("BRN_DEL_007", "Dwarka", "Delhi"),
    ("BRN_BLR_007", "Koramangala", "Bangalore"),
    ("BRN_BLR_042", "Indiranagar", "Bangalore"),
    ("BRN_BLR_011", "HSR Layout", "Bangalore"),
    ("BRN_HYD_005", "Banjara Hills", "Hyderabad"),
    ("BRN_HYD_011", "Jubilee Hills", "Hyderabad"),
    ("BRN_CHN_003", "T Nagar", "Chennai"),
    ("BRN_CHN_011", "Anna Nagar", "Chennai"),
    ("BRN_KOL_009", "Park Street", "Kolkata"),
    ("BRN_KOL_042", "Salt Lake", "Kolkata"),
    ("BRN_PUN_003", "MG Road", "Pune"),
    ("BRN_PUN_011", "Kothrud", "Pune"),
    ("BRN_AHM_005", "CG Road", "Ahmedabad"),
    ("BRN_AHM_011", "Maninagar", "Ahmedabad"),
    ("BRN_JIP_003", "Vaishali Nagar", "Jaipur"),
    ("BRN_LUK_003", "Hazratganj", "Lucknow"),
    ("BRN_KOC_003", "Marine Drive", "Kochi"),
    ("BRN_BHO_003", "MP Nagar", "Bhopal"),
    ("BRN_CHD_003", "Sector 17", "Chandigarh"),
    ("BRN_NGP_003", "Sitabuldi", "Nagpur"),
]

CHANNELS = ["ATM", "UPI", "NEFT", "BRANCH", "MOBILE_APP"]
PRODUCTS = ["SAVINGS", "CURRENT", "CREDIT", "WALLET", "FD"]

OCCUPATIONS = ["STUDENT", "SALARIED", "BUSINESS", "UNKNOWN"]
INCOME_BANDS = ["LOW", "MID", "HIGH"]

# Amount ranges per occupation per income band
AMOUNT_RANGES = {
    ("STUDENT", "LOW"): (500, 15_000),
    ("STUDENT", "MID"): (1_000, 25_000),
    ("STUDENT", "HIGH"): (2_000, 35_000),
    ("SALARIED", "LOW"): (1_000, 30_000),
    ("SALARIED", "MID"): (2_000, 45_000),
    ("SALARIED", "HIGH"): (5_000, 80_000),
    ("BUSINESS", "LOW"): (5_000, 50_000),
    ("BUSINESS", "MID"): (10_000, 1_00_000),
    ("BUSINESS", "HIGH"): (20_000, 5_00_000),
    ("UNKNOWN", "LOW"): (500, 20_000),
    ("UNKNOWN", "MID"): (1_000, 40_000),
    ("UNKNOWN", "HIGH"): (2_000, 60_000),
}


# ── Neo4j helpers ─────────────────────────────────────────────────────────────

def _neo4j_create_account(tx, account_id: str, product_type: str, occupation: str,
                           income_band: str, city: str, is_dormant: bool = False):
    tx.run(
        """
        MERGE (a:Account {account_id: $aid})
        SET a.product_type = $product,
            a.kyc_occupation = $occ,
            a.income_band = $band,
            a.city = $city,
            a.is_dormant = $dormant
        """,
        aid=account_id, product=product_type, occ=occupation,
        band=income_band, city=city, dormant=is_dormant,
    )


def _neo4j_create_transfer(tx, from_id: str, to_id: str, txn_id: str, amount: float,
                            timestamp: str, channel: str, branch_id: str, product: str):
    tx.run(
        """
        MATCH (a:Account {account_id: $from_id})
        MATCH (b:Account {account_id: $to_id})
        CREATE (a)-[:TRANSFERRED_TO {
            txn_id: $txn_id, amount: $amount, timestamp: $ts,
            channel: $channel, branch_id: $branch_id, product: $product
        }]->(b)
        """,
        from_id=from_id, to_id=to_id, txn_id=txn_id, amount=amount,
        ts=timestamp, channel=channel, branch_id=branch_id, product=product,
    )


def _supa_insert_kyc(account_id: str, occupation: str, income_band: str,
                     age: int, city: str, is_dormant: bool, declared_monthly: float,
                     last_txn_date: Optional[str] = None):
    try:
        supabase.table("kyc_profiles").upsert({
            "account_id": account_id,
            "occupation": occupation,
            "income_band": income_band,
            "age": age,
            "city": city,
            "is_dormant": is_dormant,
            "last_transaction_date": last_txn_date,
            "declared_monthly_income": declared_monthly,
        }).execute()
    except Exception as e:
        print(f"  KYC insert warning: {e}")


def _supa_insert_account(account_id: str, balance: float, product_type: str):
    try:
        supabase.table("accounts").upsert({
            "account_id": account_id,
            "available_balance": balance,
            "lien_amount": 0,
            "account_status": "ACTIVE",
            "product_type": product_type,
        }).execute()
    except Exception as e:
        print(f"  Account insert warning: {e}")


# ── Background Transactions (10,000) ──────────────────────────────────────────

def generate_background_transactions():
    print("\n[1/6] Generating 10,000 background transactions...")
    count = 0

    # Create 200 accounts
    accounts = []
    for i in range(200):
        acc_id = f"ACC_{i:05d}"
        occ = random.choice(OCCUPATIONS)
        band = random.choice(INCOME_BANDS)
        product = random.choice(PRODUCTS)
        city = random.choice(BRANCHES)[2]
        age = random.randint(18, 65)
        declared = random.uniform(8_000, 2_00_000)
        accounts.append({
            "account_id": acc_id,
            "occupation": occ,
            "income_band": band,
            "product_type": product,
            "city": city,
            "age": age,
            "declared_monthly": declared,
        })

    # Create nodes in Neo4j
    with driver.session() as session:
        for acc in accounts:
            session.execute_write(
                _neo4j_create_account,
                acc["account_id"], acc["product_type"], acc["occupation"],
                acc["income_band"], acc["city"],
            )
        print("  ✓ Account nodes created")

    # Insert KYC and accounts
    for acc in accounts:
        _supa_insert_kyc(
            acc["account_id"], acc["occupation"], acc["income_band"],
            acc["age"], acc["city"], False, acc["declared_monthly"],
        )
        _supa_insert_account(acc["account_id"], random.uniform(10_000, 5_00_000), acc["product_type"])

    print("  ✓ KYC and accounts inserted")

    # Generate 10,000 transfers
    base_time = datetime.now(UTC) - timedelta(days=90)
    with driver.session() as session:
        batch = []
        for i in range(10_000):
            from_acc = random.choice(accounts)
            to_acc = random.choice(accounts)
            if from_acc["account_id"] == to_acc["account_id"]:
                continue

            occ = from_acc["occupation"]
            band = from_acc["income_band"]
            amt_range = AMOUNT_RANGES.get((occ, band), (500, 45_000))
            amount = round(random.uniform(*amt_range), 2)
            branch_tuple = random.choice(BRANCHES)
            channel = random.choice(CHANNELS)
            product = random.choice(PRODUCTS)
            ts = (base_time + timedelta(minutes=random.randint(0, 90 * 24 * 60))).isoformat()
            txn_id = f"TXN_{i:06d}"

            session.execute_write(
                _neo4j_create_transfer,
                from_acc["account_id"], to_acc["account_id"],
                txn_id, amount, ts, channel, branch_tuple[0], product,
            )
            count += 1
            if count % 1000 == 0:
                print(f"  ... {count} transactions written")

    print(f"  ✓ {count} background transactions created\n")


# ── Demo Scenarios ────────────────────────────────────────────────────────────

def create_demo_accounts(session):
    demo_accounts = [
        # Structuring
        ("STR_ACC_001", "SAVINGS", "STUDENT", "LOW", "Mumbai"),
        # Smurfing mules
        ("MULE_001", "SAVINGS", "UNKNOWN", "LOW", "Mumbai"),
        ("MULE_002", "SAVINGS", "UNKNOWN", "LOW", "Delhi"),
        ("MULE_003", "SAVINGS", "UNKNOWN", "LOW", "Bangalore"),
        ("MULE_004", "SAVINGS", "UNKNOWN", "LOW", "Hyderabad"),
        ("MULE_005", "SAVINGS", "UNKNOWN", "LOW", "Chennai"),
        ("MULE_006", "SAVINGS", "UNKNOWN", "LOW", "Kolkata"),
        ("AGG_ACC_X", "SAVINGS", "UNKNOWN", "HIGH", "Mumbai"),
        # Roundtrip
        ("ROUND_A", "SAVINGS", "BUSINESS", "HIGH", "Mumbai"),
        ("ROUND_B", "SAVINGS", "BUSINESS", "HIGH", "Delhi"),
        ("ROUND_C", "SAVINGS", "BUSINESS", "HIGH", "Bangalore"),
        # Dormant
        ("DORM_ACC_001", "SAVINGS", "STUDENT", "LOW", "Mumbai"),
        # Profile mismatch
        ("PROF_ACC_001", "SAVINGS", "STUDENT", "LOW", "Delhi"),
        # Generic endpoint accounts
        ("RECV_001", "SAVINGS", "SALARIED", "MID", "Mumbai"),
    ]
    for acc_id, product, occ, band, city in demo_accounts:
        session.execute_write(_neo4j_create_account, acc_id, product, occ, band, city)


def scenario_structuring(session):
    """DEMO_STRUCTURING — Engine 1 — cross-branch structuring."""
    base = datetime.now(UTC) - timedelta(days=10)
    txns = [
        ("STR_ACC_001", "RECV_001", 14_000, 0, "BRN_MUM_042", "ATM", "SAVINGS"),
        ("STR_ACC_001", "RECV_001", 18_000, 3, "BRN_DEL_011", "BRANCH", "SAVINGS"),
        ("STR_ACC_001", "RECV_001", 12_000, 6, "BRN_BLR_007", "UPI", "SAVINGS"),
        ("STR_ACC_001", "RECV_001", 17_000, 7, "BRN_CHN_003", "MOBILE_APP", "SAVINGS"),
    ]
    for i, (frm, to, amt, day, brn, ch, prod) in enumerate(txns):
        ts = (base + timedelta(days=day)).isoformat()
        session.execute_write(_neo4j_create_transfer, frm, to, f"STR_TXN_{i}", amt, ts, ch, brn, prod)

    _supa_insert_kyc("STR_ACC_001", "STUDENT", "LOW", 20, "Mumbai", False, 10_000)
    _supa_insert_account("STR_ACC_001", 80_000, "SAVINGS")
    print("  ✓ DEMO_STRUCTURING loaded")


def scenario_smurfing(session):
    """DEMO_SMURFING — Engine 2 — 6 mules → 1 aggregator, 4 hours."""
    base = datetime.now(UTC) - timedelta(hours=5)
    mules = [
        ("MULE_001", "BRN_MUM_042", 0),
        ("MULE_002", "BRN_DEL_011", 30),
        ("MULE_003", "BRN_BLR_007", 60),
        ("MULE_004", "BRN_HYD_005", 90),
        ("MULE_005", "BRN_CHN_003", 120),
        ("MULE_006", "BRN_KOL_009", 200),
    ]
    for i, (mule, brn, mins) in enumerate(mules):
        ts = (base + timedelta(minutes=mins)).isoformat()
        session.execute_write(_neo4j_create_transfer, mule, "AGG_ACC_X", f"SMURF_TXN_{i}", 49_000, ts, "BRANCH", brn, "SAVINGS")

        is_dormant = mule in ("MULE_001", "MULE_003", "MULE_005")
        last_txn = (datetime.now(UTC) - timedelta(days=560)).date().isoformat() if is_dormant else None
        _supa_insert_kyc(str(mule), "UNKNOWN", "LOW", 30, "Unknown", is_dormant, 5_000, last_txn)
        _supa_insert_account(str(mule), 60_000, "SAVINGS")

    _supa_insert_kyc("AGG_ACC_X", "UNKNOWN", "HIGH", 35, "Mumbai", False, 50_000)
    _supa_insert_account("AGG_ACC_X", 3_00_000, "SAVINGS")
    print("  ✓ DEMO_SMURFING loaded")


def scenario_roundtrip(session):
    """DEMO_ROUNDTRIP — Engine 2 DFS — 47-minute cycle."""
    base = datetime.now(UTC) - timedelta(hours=2)
    ts0 = base.isoformat()
    ts1 = (base + timedelta(minutes=18)).isoformat()
    ts2 = (base + timedelta(minutes=47)).isoformat()
    session.execute_write(_neo4j_create_transfer, "ROUND_A", "ROUND_B", "RND_TXN_1", 3_00_000, ts0, "NEFT", "BRN_MUM_042", "SAVINGS")
    session.execute_write(_neo4j_create_transfer, "ROUND_B", "ROUND_C", "RND_TXN_2", 2_85_000, ts1, "NEFT", "BRN_DEL_011", "SAVINGS")
    session.execute_write(_neo4j_create_transfer, "ROUND_C", "ROUND_A", "RND_TXN_3", 2_70_000, ts2, "NEFT", "BRN_BLR_007", "SAVINGS")

    for acc_id in ["ROUND_A", "ROUND_B", "ROUND_C"]:
        _supa_insert_kyc(acc_id, "BUSINESS", "HIGH", 40, "Mumbai", False, 1_00_000)
        _supa_insert_account(acc_id, 5_00_000, "SAVINGS")
    print("  ✓ DEMO_ROUNDTRIP loaded")


def scenario_dormant(session):
    """DEMO_DORMANT — Gemini Anomaly — 26-month dormant, ₹8.5L SWIFT."""
    ts = datetime.now(UTC).isoformat()
    session.execute_write(_neo4j_create_transfer, "RECV_001", "DORM_ACC_001", "DORM_TXN_1", 8_50_000, ts, "SWIFT", "BRN_MUM_042", "SAVINGS")

    last_txn = (datetime.now(UTC) - timedelta(days=780)).date().isoformat()
    _supa_insert_kyc("DORM_ACC_001", "STUDENT", "LOW", 22, "Mumbai", True, 10_000, last_txn)
    _supa_insert_account("DORM_ACC_001", 8_60_000, "SAVINGS")
    print("  ✓ DEMO_DORMANT loaded")


def scenario_profile_mismatch(session):
    """DEMO_PROFILE_MISMATCH — Gemini Anomaly — student receiving 48x income from 18 foreign sources."""
    base = datetime.now(UTC) - timedelta(days=30)
    for i in range(18):
        day_offset = random.randint(0, 30)
        hour = random.randint(1, 4)
        ts = (base + timedelta(days=day_offset, hours=hour)).isoformat()
        foreign_acc = f"FOREIGN_{i:03d}"
        session.execute_write(_neo4j_create_account, foreign_acc, "SAVINGS", "UNKNOWN", "HIGH", "International", False)
        session.execute_write(_neo4j_create_transfer, foreign_acc, "PROF_ACC_001", f"PROF_TXN_{i}", 26_666, ts, "SWIFT", "BRN_DEL_011", "SAVINGS")

    _supa_insert_kyc("PROF_ACC_001", "STUDENT", "LOW", 21, "Delhi", False, 10_000)
    _supa_insert_account("PROF_ACC_001", 4_90_000, "SAVINGS")
    print("  ✓ DEMO_PROFILE_MISMATCH loaded")


# ── Main entry point ──────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Fund Flow Tracker — Synthetic Data Generator")
    print("=" * 60)

    # Clear existing demo data
    print("\n[0/6] Clearing existing demo nodes...")
    demo_ids = ["STR_ACC_001", "MULE_001", "MULE_002", "MULE_003", "MULE_004",
                "MULE_005", "MULE_006", "AGG_ACC_X", "ROUND_A", "ROUND_B", "ROUND_C",
                "DORM_ACC_001", "PROF_ACC_001", "RECV_001"]
    with driver.session() as session:
        session.run(
            "MATCH (a:Account) WHERE a.account_id IN $ids DETACH DELETE a",
            ids=demo_ids,
        )
    print("  ✓ Cleared")

    generate_background_transactions()

    print("\n[2/6] Creating demo scenario accounts...")
    with driver.session() as session:
        create_demo_accounts(session)
    print("  ✓ Done")

    print("\n[3/6] Loading DEMO_STRUCTURING...")
    with driver.session() as session:
        scenario_structuring(session)

    print("\n[4/6] Loading DEMO_SMURFING...")
    with driver.session() as session:
        scenario_smurfing(session)

    print("\n[5/6] Loading DEMO_ROUNDTRIP...")
    with driver.session() as session:
        scenario_roundtrip(session)

    print("\n[6/6] Loading DEMO_DORMANT and DEMO_PROFILE_MISMATCH...")
    with driver.session() as session:
        scenario_dormant(session)
        scenario_profile_mismatch(session)

    driver.close()

    print("\n" + "=" * 60)
    print("✅ Data generation complete!")
    print("  - 10,000 background transactions in Neo4j")
    print("  - 5 hardcoded demo scenarios loaded")
    print("  - KYC profiles and accounts in Supabase")
    print("=" * 60)


if __name__ == "__main__":
    main()
