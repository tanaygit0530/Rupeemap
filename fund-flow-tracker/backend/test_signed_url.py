import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPA_URL, SUPA_KEY)

try:
    # Upload test file
    supabase.storage.from_("sar-reports").upload(path="test-file-mock3", file=b"hello world", file_options={"content-type": "text/plain"})
    # Try fetching signed url structure
    res = supabase.storage.from_("sar-reports").create_signed_url(path="test-file-mock3", expires_in=3600)
    print("Signed URL result layout Type:", type(res))
    print("Signed URL result:", res)
except Exception as e:
    print("Error during signed URL test:", e)
