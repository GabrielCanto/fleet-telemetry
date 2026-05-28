# Architecture Decision Record — Fleet Telemetry Monitoring Service

**Context.** A vertical slice for 50 autonomous vehicles emitting telemetry at 1 Hz (~50 writes/sec). The
brief stresses concurrency safety in four places: telemetry bursts, same-zone entry counting, fault
transitions, and the fleet aggregate.

**Framing thesis.** At ~50 writes/sec this system is **not throughput-bound**. Every "safe under concurrency"
requirement is really about the **semantic correctness of interleaved transactions**. So I optimised for
*provable correctness with the simplest mechanism that is obviously correct*, and deliberately rejected
machinery that only pays off at much larger scale (sharded counters, queues, event-sourcing, Redis/Kafka).

---

## 1. The most important decisions

**a) PostgreSQL + one vehicle-row lock per ingest (the architectural spine).**
`POST /telemetry` runs a single transaction that `SELECT … FOR UPDATE`s the vehicle row and then, under that
one lock, does everything: out-of-order guard → append the event → detect anomalies → update current state →
(on the fault edge) cancel mission + open maintenance → increment the zone counter → commit. Serialising all
of a vehicle's mutations on its own row makes out-of-order handling, stateful anomaly detection, the
current-state projection, and fault consequences **correct together, atomically**, with almost no
machinery. Chose **Postgres over SQLite** because the whole problem is concurrent writers needing real
row-level locking, `SELECT … FOR UPDATE`, partial unique indexes, and MVCC snapshots — SQLite serialises
writers globally and lacks these primitives. **No deadlocks:** every path locks the vehicle row first and the
only other lock (the zone row, or the active-mission row) is always taken *after* it, so there is no
lock-ordering cycle.

**b) Synchronous SQLAlchemy, not async.** Because we are correctness-bound rather than throughput-bound,
async I/O buys nothing here — it would only add session-per-task discipline and async test scaffolding.
FastAPI runs `def` endpoints in a threadpool, so concurrent requests still execute on separate
connections and `FOR UPDATE` blocking is genuinely exercised (the tests prove it). Synchronous code makes the
transaction boundaries and locking obvious, which is the property that matters.

**c) Three concurrency mechanisms, each the simplest correct option.**
- **Zone counter — no lost updates.** `UPDATE zones SET entry_count = entry_count + 1 WHERE zone_id = ?`.
  Under READ COMMITTED the UPDATE takes a row write-lock; a concurrent same-zone UPDATE blocks, then re-reads
  the freshest committed value and adds to *N+1*. The naive read-modify-write (`SELECT` → `+1` → `UPDATE`)
  loses updates because two readers both see *N*. 20 rows ⇒ no hot-row concern at this rate.
- **Fault transition — atomic + idempotent.** A single funnel (`apply_fault_transition`) used by *both* the
  telemetry path and the status endpoint. The caller locks the vehicle `FOR UPDATE`, captures
  `previous_status`, and only enters the funnel on a genuine non-fault→fault edge. Three idempotency layers
  guarantee exactly-once side effects under a concurrent double-fault: (1) the lock serialises attempts, so
  the second re-reads `status='fault'` and no-ops; (2) the mission cancel is a conditional
  `UPDATE … WHERE status='active'`; (3) a **partial unique index** `(vehicle_id) WHERE resolved_at IS NULL`
  on `maintenance_records` + `ON CONFLICT DO NOTHING` is the backstop. Isolation choice: **READ COMMITTED +
  pessimistic `FOR UPDATE`**, not SERIALIZABLE-with-retry (needs a 40001 retry loop) and not optimistic
  versioning (doesn't serialise the side effects).
- **Fleet aggregate — safe for free.** `SELECT status, COUNT(*) … GROUP BY status` is one statement = one MVCC
  snapshot, so it always sums to the fleet size and never tears. A maintained counter table would only
  reintroduce a hot-row write and drift risk; it wins only when the base table is huge *and* the endpoint is hot.

---

## 2. Anomaly definition (the brief says "your definition — justify it")

Simple, deterministic, **auditable** rules evaluated inside the ingest transaction. Stateless rules depend
only on the event; stateful rules compare against the previous reading cached on the locked vehicle row (O(1),
no scan).

| Type | Kind | Rule |
|---|---|---|
| `fault_status` | stateless | `status == fault` (critical) |
| `low_battery` | stateless | `battery_pct < 15` (critical if `< 5`) |
| `error_code` | stateless | `error_codes` non-empty |
| `overspeed` | stateless | `speed_mps > 8` (warehouse cap) |
| `battery_jump` | stateful | `|Δbattery| > 30` between consecutive events → sensor fault |
| `battery_increase_no_charge` | stateful | battery rises `> 2` while not charging → impossible reading |

Thresholds are tunable constants. "Fault" the **state** is defined narrowly as `status == 'fault'`; an error
code alone raises an *anomaly* but does not flip the vehicle's state. Stateless rules run on every event;
stateful rules and state mutations run only for the newest event (the out-of-order guard).

## 3. Polling, not websockets (frontend)

The dashboard polls every 2s via TanStack Query. At 50 vehicles and a 2s human-monitoring cadence, polling is
trivially correct, survives reconnects for free, needs no server-side connection state, and keeps the budget
on the backend. Websockets/SSE were considered and rejected as unnecessary complexity here; they become
worthwhile when push latency matters or fan-out is large (noted below).

---

## 4. Unclear requirements & the assumptions I made

- **Anomaly definition** was left open → the 6 rules above.
- **`lat` / `lon`** are accepted as optional (nullable) even though the spec example shows them populated.
  GPS dropouts are a real failure mode in industrial fleets; rejecting those events would silently lose
  telemetry. The schema is intentionally Postel: it accepts the spec-example shape *and* the dropout case.
- **Unknown `vehicle_id`** on telemetry → **rejected with 404**. The fleet is a fixed, provisioned roster
  (seeded), so this keeps the "`/fleet/state` always sums to 50" invariant exact and testable. *Alternative:*
  upsert-on-ingest, which I rejected to avoid ingesting garbage IDs.
- **Duplicate / retried telemetry** → **out of scope**, because the spec provides no client-supplied event id.
  The guarantee is *no lost updates under concurrency*, not exactly-once against client retries; a duplicate
  packet would append a second event and re-increment a zone. Fix later with a client `event_id` + unique
  constraint or a dedupe window.
- **Out-of-order events** → always persisted (append-only), but state, stateful anomalies, and fault
  transitions are skipped unless the event is the newest seen. **Zone entries are counted regardless of
  ordering** — a crossing happened, so it counts.
- **Timestamps** → all stored as `TIMESTAMPTZ`; incoming timestamps normalised to UTC (naive assumed UTC).
- **Missions/maintenance lifecycle** → each vehicle seeds with one active mission; maintenance records open on
  fault and are never auto-resolved (no resolution workflow in scope).

## 5. What would change "at significant scale"

I'd call it significant at ~10³–10⁴× the data, e.g. thousands of vehicles at higher rates or hundreds of
millions of telemetry rows. Then: **partition/rotate `telemetry_events`** (native partitioning or TimescaleDB)
and treat it as a retention-managed firehose; **decouple ingestion** behind a queue (Kafka/SQS) with workers,
making `POST /telemetry` a thin enqueue; **maintained/sharded counters** for zones once a single row is hot;
**read replicas** for the dashboard queries; **shard by `vehicle_id`** since the lock domain is already
per-vehicle (it shards cleanly); and **websockets/SSE** to push updates instead of polling. None of these are
justified at 50 vehicles, and adding them now would be the over-engineering the thesis warns against.

## 6. Deliberately left out (and why)

Auth/authz; Alembic migrations (used `create_all` + idempotent seed — Alembic is the production path);
`location_jump` anomaly (haversine×Δt — finicky, low marginal value); **stale/offline detection** (it is
triggered by the *absence* of events, which is structurally impossible in an event-driven path — it needs a
background scheduler); batch ingest; pagination beyond a `limit`; rate limiting; metrics/tracing; and a
maintenance-resolution workflow. Each is a deliberate scope cut for the 5–6h budget, not an oversight.
