# AI Interaction Log

> **Note for the reviewer (and for me to vet):** this project was built with **Claude Code** (Anthropic) as a
> pair. This log records the meaningful prompts, summarises what the AI produced, and — importantly — where I
> had to **correct or redirect** it. The reflection at the end is my own judgement, not auto-generated text.
> Using AI was explicitly encouraged by the brief; this log is how I show the way I *directed* it.

---

## 1. Planning

**Prompt (summary).** Gave Claude the take-home brief plus a refined spec: build a fleet-telemetry service —
FastAPI + PostgreSQL backend, React/TS dashboard, ADR, AI log; keep it pragmatic, no Kafka/Redis/Celery; use a
single vehicle-row lock for concurrency; atomic zone counter; atomic fault transition.

**Output (summary).** Claude reconnoitred the environment (Python 3.9.6 local, Node v10, Docker available) and
proposed a plan anchored on a **central thesis**: at ~50 writes/sec the system is correctness-bound, not
throughput-bound, so prefer the simplest provably-correct mechanism. It surfaced three genuine forks and asked
me to decide:

- **Anomaly scope** → I chose 4 stateless + 2 stateful rules.
- **Sync vs async SQLAlchemy** → I chose **sync** (async buys nothing at this scale).
- **Simulator** → include it for a live demo.

**Correction / redirection (the important one).** Claude's first plan had a **state-machine ordering bug**: the
ingest spine set `status='fault'` during the generic state update *before* calling the fault-transition
function, which would defeat that function's "already fault → no-op" guard and silently skip the mission-cancel
and maintenance-record side effects. I rejected the plan and required:

1. capture `previous_status` **before** any mutation; never set `status='fault'` before the funnel runs;
2. run fault side effects only for the **newest** telemetry and only on the real non-fault→fault edge;
3. **reject unknown vehicle IDs** (404) rather than silently upserting, to keep the "fleet sums to 50" invariant;
4. single-source the vehicle IDs across seed/simulator/tests;
5. document **duplicate-telemetry idempotency as out of scope** (the spec has no client event id);
6. surface `mission_cancelled` / `maintenance_record_created` in the status response;
7. validate `zone_entered` against the zone list **before** any DB work;
8. normalise timestamps to UTC.

Claude folded all eight into a revised plan, which I approved.

## 2. Implementation

**Prompt (summary).** Build it per the approved plan: models with two partial unique indexes, the ingest spine,
the shared fault funnel, the 6 anomaly rules, the read endpoints, Docker Compose with Postgres on 5433, a
simulator, and pytest concurrency proofs.

**Output (summary).** Claude scaffolded the backend (`constants/config/db/models/schemas/anomalies/faults/
ingest/seed` + five routers + `main`), the Dockerfiles and Compose, the React/TS dashboard (TanStack Query,
2s polling, three components), the self-configuring simulator, and five test files. The app booted cleanly,
seeded 20 zones + 50 vehicles, and the read endpoints returned the expected shapes on the first run.

**Correction / redirection (found via testing).** Manual `curl` testing exposed a real bug: the first fault
transition returned `maintenance_record_created: false` even though the DB **did** contain the new maintenance
record. Cause: `INSERT … ON CONFLICT DO NOTHING` combined with SQLAlchemy's implicit `RETURNING` makes
`result.rowcount` unreliable under psycopg3. Fix: use explicit `.returning(id)` and check
`result.first() is not None`. After the fix the flags were correct (`true` on the first transition, `false` on
the idempotent repeat) and the maintenance count stayed at exactly 1.

## 3. Verification

- `pytest` (in-container, against a dedicated `fleet_test` DB) — **13 passed**, including the three concurrency
  proofs (same-zone burst of 30 → count 30; 8 concurrent faults → exactly 1 maintenance record; fleet aggregate
  always sums to 50 under concurrent updates).
- Ran the simulator for ~8s → ~400 events, ~67 zone entries, ~48 anomalies; fleet state diversified.
- Loaded the dashboard in a browser: it rendered all 50 vehicles, status/battery/anomaly columns, and live zone
  bars; the fleet counts and "updated" timestamp changed between samples (polling works); **no console/CORS
  errors**.

---

## Reflection (my own assessment — please read this as my judgement)

- **Where AI was strong:** scaffolding breadth and speed (a coherent multi-file backend + frontend + tests +
  Docker in one pass), and articulating the concurrency reasoning crisply (the row-lock spine, why
  READ COMMITTED + `FOR UPDATE` suffices, why a single `GROUP BY` is snapshot-safe). The docs draft was a good
  starting point.
- **Where it needed me:** the subtle **ordering bug** in the fault transition. It was easy to miss in prose and
  would have shipped a silent correctness defect; catching it required reasoning about the guard's
  preconditions, not just reading the happy path. That is the kind of thing I have to own.
- **Driver-specific gotcha:** the `ON CONFLICT … RETURNING`/`rowcount` behaviour under psycopg3 only showed up
  when I actually exercised the endpoint and cross-checked the DB. I don't fully trust framework return values
  until I've seen them against a real database.
- **What I double-checked manually:** the concurrency claims (via the tests, not just the prose), the fault
  idempotency (DB row counts after concurrent and repeated faults), and the end-to-end path (simulator →
  Postgres → API → dashboard, including CORS).
- **Takeaway:** AI was a strong accelerator for breadth and boilerplate, but correctness on the parts that
  *matter here* — transaction ordering, isolation, exactly-once side effects — still came from explicit review
  and tests. I treated its output as a fast first draft to interrogate, not as a finished answer.
