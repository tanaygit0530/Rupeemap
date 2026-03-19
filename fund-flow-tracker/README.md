# Fund Flow Tracker — AML Intelligence Platform

Real-time Anti-Money Laundering detection system powered by **Gemini 2.0 Flash** (no ML training required).

## Quick Start

### 1. Fill in environment variables
```bash
# backend/.env
NEO4J_URI=neo4j+s://xxxxx.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=your_anon_key
GEMINI_API_KEY=your_gemini_key
JWT_SECRET=any_long_random_string
JWT_ALGORITHM=HS256

# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://xxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_anon_key
```

### 2. Backend setup
```bash
cd backend
pip install -r requirements.txt
python data/faker_generator.py   # run once — loads 10k txns + 5 demo scenarios
uvicorn main:app --reload
```

### 3. Frontend setup
```bash
cd frontend
npm install
npm run dev
```

### 4. Insert a test officer in Supabase
```sql
INSERT INTO officers (id, email, password_hash, role)
VALUES (
  gen_random_uuid(),
  'senior@bank.gov.in',
  '$2b$12$example_bcrypt_hash',  -- use passlib to generate
  'senior'
);
```

## Detection Engines

| Engine | Detects | Max Score |
|--------|---------|-----------|
| Engine 1 | Cross-branch structuring (sliding window) | 40 pts |
| Engine 2 | Smurfing, Round-trips, Product-switching | 60 pts |
| Gemini 2.0 Flash | Dormant reactivation, Profile mismatch | +20 pts |

## Demo Scenarios

| Scenario | Detection | Risk Score |
|----------|-----------|-----------|
| DEMO_STRUCTURING | Engine 1 — ₹61k across 4 cities/7 days | 72 |
| DEMO_SMURFING | Engine 2 — 6 mules, 6 cities, 4 hours | 99 |
| DEMO_ROUNDTRIP | Engine 2 — ₹3L cycle in 47 minutes | 95 |
| DEMO_DORMANT | Gemini — 26-month dormant + ₹8.5L SWIFT | 88 |
| DEMO_PROFILE_MISMATCH | Gemini — student 48x income, 18 foreign sources | 83 |

## API Endpoints

```
POST /auth/login                      Public
GET  /alerts                          JWT Required
GET  /alerts/{id}                     JWT Required
PATCH /alerts/{id}/lock               JWT Required
POST /alerts/{id}/escalate            Senior Only
POST /alerts/{id}/confirm             JWT Required
POST /alerts/{id}/release             JWT Required
POST /transactions/ingest             JWT Required
POST /sar/generate                    JWT Required
POST /demo/load/{scenario}            JWT Required
GET  /health                          Public
```

## Architecture

```
Transaction
    │
    ├─── Engine 1 (asyncio.gather) ─── Structuring detection
    │                                   via 7-day sliding window
    │
    └─── Engine 2 (asyncio.gather) ─── Smurfing (Neo4j in-degree)
                                        Round-trip (Neo4j DFS cycle)
                                        Product-switching categories
                                            │
                                            └── Gemini 2.0 Flash
                                                Dormant anomaly
                                                Profile mismatch
                                                (inside each engine)
    │
Score Fusion Layer (max 99)
    │
Freeze Service (lien on Supabase accounts)
    │
TTL Worker (every 30min — auto escalate/release)
    │
Supabase Realtime → Frontend toast notification
```
