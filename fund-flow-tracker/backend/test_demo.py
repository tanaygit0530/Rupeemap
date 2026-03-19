import asyncio
import os
from main import load_demo_scenario

# Set mock env for setup if needed
os.environ["SUPABASE_URL"] = os.getenv("SUPABASE_URL", "https://xxxx.supabase.co")
os.environ["SUPABASE_KEY"] = os.getenv("SUPABASE_KEY", "xxxx")

async def test():
    print("Testing backend /demo/load/DEMO_STRUCTURING...")
    try:
        resp = await load_demo_scenario(
            scenario_name="DEMO_STRUCTURING",
            officer={"officer_id": "mock", "email": "test@bank.com", "role": "senior"}
        )
        print("Success:", resp)
    except Exception as e:
        print("\n❌ CRASH TRACEBACK:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
