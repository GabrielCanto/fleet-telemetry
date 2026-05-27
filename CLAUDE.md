# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A fleet-telemetry monitoring service (take-home vertical slice): 50 vehicles emit telemetry at ~1 Hz; a
FastAPI + PostgreSQL backend ingests/persists/analyzes it and a React dashboard polls it. The full rationale
is in `docs/ADR.md` — **read it before making design changes**.

**Guiding thesis (it explains most of the design):** at ~50 writes/sec this system is *not throughput-bound* —
every "concurrency" requirement is about the **semantic correctness of interleaved transactions**, not scale.
Optimize for the simplest provably-correct mechanism. Do **not** add sharded counters, queues, caches, async
I/O, or in-memory aggregates "for performance" — that is exactly the over-engineering the design rejects.

## Commands

Everything runs in Docker (the system Python is 3.9 — too old for the 3.12 backend; system Node may be too old
for Vite).

```bash
docker compose up --build          # db:5433, backend:8000 (Swagger at /docs), dashboard:5173
docker compose logs -f backend     # backend runs uvicorn --reload; backend/ is volume-mounted
docker compose down -v             # reset: wipes the DB volume; backend re-creates schema + re-seeds on start
```

Tests run **in the container**, against a dedicated `fleet_test` database — they need real Postgres row locks
(not SQLite):

```bash
docker compose up -d db backend
docker compose exec backend pytest tests/ -v
docker compose exec backend pytest tests/test_anomalies.py::test_stateless_rules_fire -v   # single test
```

Frontend typecheck/build (the Vite dev server does **not** type-check):

```bash
docker compose run --rm frontend npm run build      # tsc --noEmit && vite build
```

Simulator (drives a live demo; self-configures the roster/zones from the API):

```bash
docker compose run --rm -v "$PWD/simulator:/sim" -e API_BASE=http://backend:8000 backend python /sim/simulate.py
```

After editing `backend/requirements.txt` or `frontend/package.json`, rebuild that image
(`docker compose build <service>`).

## Git workflow

- **Branch off fresh `main`; never commit or push to `main` directly.** Start every change from the latest
  remote main:
  ```bash
  git fetch origin && git switch -c <branch> origin/main
  ```
- **Branch naming.** Use the Jira ticket ID, prefixed by type: `feature/<TICKET-ID>` or `fix/<TICKET-ID>`
  (an optional short slug is fine, e.g. `feature/ABC-123-zone-counter`). If there is no ticket, use a concise
  kebab-case name describing the work, e.g. `feature/zone-counter-endpoint`, `fix/fault-ordering`.
- **Commit messages.** Objective, imperative subject (e.g. `Add atomic zone counter`); when a ticket exists,
  prefix it (`ABC-123: add atomic zone counter`). **Never** add a `Co-authored-by: Claude` trailer or any
  "Generated with Claude"-style line.
- **Always push the feature/fix branch to the remote** and set upstream: `git push -u origin <branch>`. Merge
  into `main` only through a reviewed PR.
- **Never force-push** without explicit permission in chat. If a history rewrite is truly needed (e.g. after a
  rebase), use `git push --force-with-lease` — and only after that approval.

## Architecture

**The ingest spine — `backend/app/ingest.py::ingest_event`** is the heart of the system and the code most
likely to break subtly if edited carelessly. `POST /telemetry` runs it in one transaction (`with db.begin()`),
and correctness hangs on **a single `SELECT … FOR UPDATE` lock of the vehicle row** plus a strict step order:

1. lock the vehicle row (unknown id → `UnknownVehicleError` → 404; the fleet is fixed/seeded, never upserted)
2. capture `previous_status` **before any mutation**
3. `is_newest` out-of-order guard (`event.timestamp > vehicle.last_event_ts`)
4. append the telemetry event (always — late events are persisted too)
5. detect anomalies **before** the state update (so stateful rules see the genuine previous values); stateless
   always, stateful only if `is_newest`
6. update current state **only if `is_newest`**; on the genuine non-fault→fault edge, defer to the fault funnel
   — do **not** set `status='fault'` here
7. increment the zone counter **regardless of `is_newest`** (a crossing is a crossing)

The ordering encodes correctness (step 2 before any write; step 5 before step 6). If you change it, re-read the
inline comments and the ADR.

**The fault funnel — `backend/app/faults.py`.** Entering `fault` must atomically cancel the active mission and
open exactly one maintenance record. `transition_status()` is the **single entry point** used by both the
telemetry path and `POST /vehicles/{id}/status`; it locks the vehicle, captures `previous_status`, and only
calls `apply_fault_transition()` on a real non-fault→fault edge. `apply_fault_transition()` **owns** setting
`status='fault'`. Exactly-once survives concurrent double-faults via three layers: the row lock serializes
attempts; the mission cancel is conditional (`WHERE status='active'`); and a partial unique index backs
`ON CONFLICT DO NOTHING`. **Gotcha:** detect an ON-CONFLICT insert with `.returning(id)` +
`result.first() is not None`, never `rowcount` (unreliable for ON CONFLICT under psycopg3).

**Three concurrency mechanisms — keep them as-is:**
- Zone counter: `UPDATE zones SET entry_count = entry_count + 1 WHERE zone_id = ?` (atomic in-DB increment;
  never read-modify-write in Python).
- Fleet aggregate (`GET /fleet/state`): a single `GROUP BY` = one MVCC snapshot, always sums to the fleet size.
  No maintained counter table.
- Fault idempotency: the two **partial unique indexes** in `models.py`
  (`uq_one_active_mission_per_vehicle WHERE status='active'`,
  `uq_one_open_maintenance_per_vehicle WHERE resolved_at IS NULL`) are load-bearing invariants.

**Sync, not async, on purpose.** Endpoints are sync `def`; FastAPI runs them in a threadpool, so each
concurrent request gets its own connection and `FOR UPDATE` genuinely blocks. Keep transaction boundaries
explicit (`with db.begin()`), one Session per request (`get_db`), and use
`db.get(Model, id, with_for_update=True)` for the locking read.

**Models (`models.py`).** Postgres ENUMs are declared once as shared `Enum(...)` instances and reused across
columns (so each type is created once). `vehicles.last_anomaly_id` is a **soft pointer (no FK)** to avoid a
circular vehicles↔anomalies dependency at `create_all` time. Schema comes from `Base.metadata.create_all` +
idempotent `seed.py` in the FastAPI lifespan (no Alembic — that's the noted production path).

**Single source of truth.** `constants.py` holds `ZONES` (20) and `VEHICLE_IDS` (`v-1`…`v-50`); `seed.py` and
the tests import them, and the simulator discovers the roster/zones from the API. Don't hardcode these elsewhere.

## Invariants to preserve

- `/fleet/state` always sums to the fleet size (50). Unknown `vehicle_id` on telemetry → **404**, never an upsert.
- Out-of-order events are stored but must not mutate current state, fire stateful anomalies, or trigger faults.
- `zone_entered` is validated against `ZONES` in the Pydantic schema (422) **before** any DB work; all
  timestamps are normalized to UTC.
- Tests run against real Postgres (`fleet_test`), never SQLite — the row-locking behavior is the thing under test.

## Test layout

`backend/tests/conftest.py` creates/uses the `fleet_test` DB and resets to a clean seeded state before each
test. Concurrency tests use `ThreadPoolExecutor` + a `Barrier` with one Session per thread (large pool) to
force genuine contention. The `client` fixture overrides `get_db` and intentionally skips the app lifespan.
