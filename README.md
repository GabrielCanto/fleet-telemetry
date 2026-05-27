# Fleet Telemetry Monitoring Service

A vertical slice of a fleet-monitoring system for **50 autonomous industrial vehicles** emitting
telemetry at **1 Hz**. It ingests telemetry, persists it, detects anomalies in real time, counts zone
entries, handles fault transitions atomically, and exposes a REST API consumed by a live React + TypeScript
dashboard. A fleet **simulator** drives the whole thing for a demo, and a small **pytest** suite proves the
concurrency-critical behaviour.

- **Backend:** FastAPI · PostgreSQL · SQLAlchemy 2.0 (sync) · psycopg3 · Pydantic v2
- **Frontend:** React · TypeScript · Vite · TanStack Query (2s polling)
- **Infra:** Docker Compose (Postgres + backend + frontend)

> **Design thesis (drives every decision):** at ~50 writes/sec the system is *not throughput-bound*. Every
> "safe under concurrency" requirement is about the **semantic correctness of interleaved transactions**, not
> scale — so the design optimises for *provable correctness with the simplest mechanism* and consciously
> rejects over-engineering. The reasoning lives in **[docs/ADR.md](docs/ADR.md)**.

---

## What it does

| Capability | Where |
|---|---|
| Accept telemetry, persist every event, update current state | `POST /telemetry` → `app/ingest.py` |
| Real-time anomaly detection (4 stateless + 2 stateful rules) | `app/anomalies.py` |
| Atomic per-zone entry counter (no lost updates under bursts) | `POST /telemetry` (zone branch) + `GET /zones/counts` |
| Fault transition: cancel active mission + open maintenance record, **atomically & exactly once** | `app/faults.py` |
| Query recent anomalies by vehicle + time range | `GET /anomalies` |
| Concurrency-safe aggregate fleet state | `GET /fleet/state` |
| Live vehicle list incl. most-recent anomaly | `GET /vehicles` |
| Live dashboard (status, battery, anomalies, zone counts) | `frontend/` |

---

## Run it (Docker — recommended)

```bash
docker compose up --build
```

This starts three services:

| Service | URL | Notes |
|---|---|---|
| Postgres | `localhost:5433` | host 5433 → container 5432 (host 5432 is often taken) |
| Backend (FastAPI) | http://localhost:8000 | OpenAPI docs at http://localhost:8000/docs |
| Dashboard (Vite) | http://localhost:5173 | polls the backend every 2s |

On startup the backend creates the schema and **seeds 20 zones + 50 vehicles** (`v-1` … `v-50`), each with
one active mission. Open the dashboard at **http://localhost:5173**.

### Drive a live demo (simulator)

The simulator discovers the roster/zones from the API and posts realistic telemetry at 1 Hz for all 50
vehicles, including periodic "shift change" bursts where many vehicles enter the same charging bay in the
same instant (the concurrent-same-zone scenario).

```bash
# in a container (no local deps needed):
docker compose run --rm -v "$PWD/simulator:/sim" -e API_BASE=http://backend:8000 backend python /sim/simulate.py

# …or locally (Python 3.9+):
pip install -r simulator/requirements.txt
API_BASE=http://localhost:8000 python simulator/simulate.py
```

Watch the dashboard update live: statuses/batteries change, anomalies surface, and zone counts climb.

---

## Run the tests

```bash
docker compose up -d db backend
docker compose exec backend pytest tests/ -v
```

Tests run against a dedicated **`fleet_test`** database (created automatically), so they never touch dev data.
They include the three concurrency proofs:

- `test_zone_counter_concurrency` — 30 vehicles enter the same zone simultaneously → count is exactly 30.
- `test_fault_transition_concurrency` — 8 concurrent fault requests for one vehicle → exactly **1** maintenance
  record, mission cancelled once.
- `test_fleet_state` — the aggregate always sums to 50, including under concurrent status updates.

Plus anomaly-rule coverage, the out-of-order guard, and the 404/422 validation paths.

---

## Local development (without Docker)

- **Postgres:** `docker compose up -d db` (published on host `5433`).
- **Backend** requires **Python 3.12** (it uses 3.10+ typing). If your system Python is older, use the Docker
  path above or a 3.12 venv:
  ```bash
  cd backend
  python3.12 -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  DATABASE_URL=postgresql+psycopg://fleet:fleet@localhost:5433/fleet uvicorn app.main:app --reload
  ```
- **Frontend** requires **Node 18+** (Vite). If your Node is older: `nvm use 20` (or run it via Docker).
  ```bash
  cd frontend && npm install && npm run dev
  ```

---

## Environment variables

| Variable | Default | Used by |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://fleet:fleet@localhost:5433/fleet` (compose overrides host to `db:5432`) | backend |
| `CORS_ORIGINS` | `http://localhost:5173` (comma-separated) | backend |
| `VITE_API_BASE` | `http://localhost:8000` | frontend |

See `.env.example`.

---

## API reference (with examples)

Base URL `http://localhost:8000`.

```bash
# Ingest a telemetry event (validated; unknown vehicle -> 404, unknown zone -> 422)
curl -X POST localhost:8000/telemetry -H 'content-type: application/json' -d '{
  "vehicle_id": "v-12", "timestamp": "2026-05-27T10:00:00Z",
  "lat": 37.41, "lon": -122.08, "battery_pct": 78, "speed_mps": 1.2,
  "status": "moving", "error_codes": [], "zone_entered": "charging_bay_1"
}'
# -> {"event_id":..., "is_newest":true, "fault_transition":false, "anomalies":[...]}

# Per-zone entry counts
curl localhost:8000/zones/counts

# Aggregate fleet state (always sums to the fleet size)
curl localhost:8000/fleet/state
# -> {"idle":46,"moving":2,"charging":1,"fault":1}

# Recent anomalies, filterable by vehicle and time range
curl "localhost:8000/anomalies?vehicle_id=v-12&from=2026-05-27T00:00:00Z&to=2026-05-28T00:00:00Z&limit=50"

# Live vehicle list (status, battery, most-recent anomaly)
curl localhost:8000/vehicles

# Status update — transitioning to fault cancels the active mission + opens a maintenance record
curl -X POST localhost:8000/vehicles/v-12/status -H 'content-type: application/json' -d '{"status":"fault"}'
# -> {"vehicle_id":"v-12","status":"fault","previous_status":"moving",
#     "mission_cancelled":true,"maintenance_record_created":true}
```

---

## Project layout

```
fleet-telemetry/
├── docker-compose.yml          # postgres (5433) + backend + frontend
├── .env.example
├── docs/
│   ├── ADR.md                  # architecture decisions (start here)
│   └── AI_LOG.md               # AI interaction log
├── backend/
│   ├── Dockerfile              # python:3.12-slim
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py             # FastAPI app, lifespan (schema + seed), CORS
│   │   ├── config.py db.py models.py schemas.py constants.py seed.py
│   │   ├── ingest.py           # the one-transaction ingest spine
│   │   ├── faults.py           # apply_fault_transition / transition_status (shared funnel)
│   │   ├── anomalies.py        # 6 anomaly rules
│   │   └── routers/            # telemetry, zones, vehicles, anomalies, fleet
│   └── tests/                  # concurrency proofs + anomaly/ingest tests
├── simulator/simulate.py       # 50 vehicles @ 1 Hz, self-configuring from the API
└── frontend/                   # Vite + React + TS dashboard (2s polling)
```

---

## Documentation

- **[docs/ADR.md](docs/ADR.md)** — the key decisions, the concurrency strategy, assumptions, what changes at
  scale, and what was deliberately left out.
- **[docs/AI_LOG.md](docs/AI_LOG.md)** — how AI tooling was used to build this, including corrections.
