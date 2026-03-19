import os
import bcrypt
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPA_URL, SUPA_KEY)

def insert_demo_officers():
    print("Creating demo officers using bcrypt directly...")
    
    officers = [
        {"email": "officer@bank.com", "password": "password123", "role": "officer"},
        {"email": "senior@bank.com", "password": "password123", "role": "senior"}
    ]
    
    for o in officers:
        # Hash using bcrypt directly to bypass passlib bug
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(o["password"].encode('utf-8'), salt).decode('utf-8')
        
        try:
            resp = supabase.table("officers").upsert({
                "email": o["email"],
                "password_hash": hashed,
                "role": o["role"]
            }, on_conflict="email").execute()
            print(f"  ✓ {o['email']} (password: {o['password']}) created/updated")
        except Exception as e:
            print(f"  ❌ Error creating {o['email']}: {e}")

if __name__ == "__main__":
    insert_demo_officers()
