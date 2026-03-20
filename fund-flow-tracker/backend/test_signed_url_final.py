import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPA_URL = os.getenv("SUPABASE_URL")
SUPA_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPA_URL, SUPA_KEY)

try:
    # Upload dummy file to testing bucket
    supabase.storage.from_("sar-reports").upload(path="test-signed-url-dummy", file=b"hello sign", file_options={"content-type": "text/plain"})
    # Fetch signed URL
    res = supabase.storage.from_("sar-reports").create_signed_url(path="test-signed-url-dummy", expires_in=3600)
    print("Signed URL result Type:", type(res))
    print("Signed URL result:", res)
except Exception as e:
    print("Bucket created but failed or still testing:", e)
