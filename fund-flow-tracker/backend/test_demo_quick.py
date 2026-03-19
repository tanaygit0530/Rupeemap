import os
import asyncio
from supabase import create_client
from datetime import datetime, timedelta, timezone

load_dotenv = lambda: None # mock

SUPA_URL = "https://wljgtiadglltbdxxvpya.supabase.co"
SUPA_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Indsamd0aWFkZ2xsdGJkeHh2cHlhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM5MTA0MTIsImV4cCI6MjA4OTQ4NjQxMn0.qkWCQvpeaxB6hfebuMIPkNVvHUmsq5WUt-PdNhz4iXQ"

supabase = create_client(SUPA_URL, SUPA_KEY)

async def test_insert():
    print("Testing real backend scenario insert logic...")
    try:
        now = datetime.now(timezone.utc)
        # Match alerts insert perfectly
        alert_row = {
            "account_id_masked": "ACC_MOCK_TEST",
            "flag_type": "STRUCTURING",
            "risk_score": 72,
            "suspicious_amount": 1000,
            "freeze_status": "PARTIAL",
            "frozen_amount": 1000,
            "ttl_expires_at": (now + timedelta(hours=72)).isoformat(),
            "triggered_by": "ENGINE_DEMO",
            "gemini_explanation": "Test explanation.",
            "product_chain": None,
            "created_at": now.isoformat(),
        }
        resp = supabase.table("alerts").insert(alert_row).execute()
        alert_id = resp.data[0]["id"] if resp.data else None
        print(f"✓ Alert inserted: {alert_id}")
        
        # Match audit insert perfectly
        if alert_id:
            supabase.table("audit_log").insert({
                "alert_id": alert_id,
                "action_type": "PARTIAL_FREEZE",
                "officer_id": None,
                "officer_role": None,
                "amount_frozen": 1000,
                "risk_score_at_time": 72,
                "engine_source": "ENGINE_DEMO",
                "timestamp": now.isoformat(),
                "notes": "Test notes",
            }).execute()
            print("✓ Audit log inserted")
            
    except Exception as e:
        print("\n❌ CRASH TRACEBACK:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_insert())
