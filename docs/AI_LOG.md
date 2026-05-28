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

## 4. Concurrency review and additional test coverage

**Prompt (summary).** Three threads after merging: (a) a written concurrency-risk review of the ingest spine,
fault funnel, and read endpoints; (b) the exact behaviour when two telemetry events share an `event.timestamp`;
(c) "analyse the existing tests, then write the missing scenarios — and add a seed concurrency test."

**Output (summary).** Claude produced a four-finding review (threadpool capacity exceeds DB connection pool;
seed mission-insert TOCTOU; READ COMMITTED relied on implicitly; zone-update silent no-op on a missing zone),
then traced the same-timestamp tie to one line: the **strict `>`** in `is_newest = event.timestamp >
vehicle.last_event_ts` — so a tie is not newest, the second event is treated as out-of-order, both rows are
persisted, but the zone counter still increments twice. Eleven new tests were added across two extended files
and two new ones (`test_ingest_concurrency.py`, `test_seed_concurrency.py`): anomaly threshold boundaries
(`<15`, `<5`, `>8`, `>30`), first-event suppression of stateful rules, out-of-order zone counting, fault via
`POST /telemetry`, severity tiebreak in `last_anomaly_id`, sequential re-fault idempotency, two same-timestamp
concurrency tests, and the concurrent-`seed()` test. `pytest` went from **13 → 24 passes in ~1.4 s**.

**Correction / redirection.** The seed test was the correction. I asked Claude to write a test that reproduced
its claimed seed bug; on running, the test **passed** — falsifying its earlier claim that concurrent `seed()`
would crash on the missions check-then-`db.add`. The reason it doesn't crash is upstream: zones and vehicles
are inserted with `ON CONFLICT DO NOTHING`, and under Postgres speculative-insertion semantics the second
seeder waits on the first seeder's uncommitted zone row. By the time it reaches the missions `SELECT`, the
first seeder has committed 50 missions and the check-then-add is a no-op. The inconsistency in `seed.py`
(missions don't use ON CONFLICT like the siblings) is a code-quality nit, not a latent correctness bug — and
the new test now pins the invariant, so any future reorder of seed steps would surface as a failure.

## 5. Spec-adherence review and polish pass

**Prompt (summary).** Pasted the full original take-home brief and asked Claude to *analyse* whether every
requirement was met, and to call out anything still missing.

**Output (summary).** Claude walked the 16 spec items against the code, mapping each to the implementing
file/line plus the test that proves it (e.g. `routers/telemetry.py:13` + `test_zone_counter_concurrency`,
`routers/fleet.py:19` + `test_fleet_state_safe_under_concurrent_updates`). All four deliverables (ADR, AI
log, README, public repo) were confirmed present. It then surfaced seven small-but-defensible nits:
permissive `lat`/`lon` vs. the spec example, no literal "50 at once" burst test, no throughput benchmark,
no zero-simulator demo path, `fault_status` raised every fault tick (not just on the edge), `docker
compose down -v` wiping zone counts between runs, and a missing 404 test for the status endpoint. It
proposed three tiny polish items and asked which to apply.

**Action taken.** I asked Claude to apply all three. It branched off `origin/main` as
`feature/spec-polish` (per the CLAUDE.md workflow), then: (1) added an ADR bullet documenting `lat/lon`
optionality as a deliberate "Postel" choice — GPS dropouts are real in industrial fleets, so rejecting
those events would silently lose telemetry; I chose the doc-note over the schema-tightening because the
tightening would have cascaded into ~10 test-helper updates for no real-world benefit; (2) added a
simulator-less demo curl loop to the README so a reviewer running only `docker compose up` can drive a
fault into the dashboard by hand; (3) added `test_status_update_unknown_vehicle_returns_404` next to its
telemetry twin. Full suite: **25 passed** (one more than before). Commit `40e87e4` pushed.

**Correction / redirection.** After the polish commit Claude reported "done" without updating this AI log
— an easy oversight because the log isn't exercised by tests or hooks. I asked it directly: "did you
update the AI log?" Caught and logged here so the deliverable stays accurate. Lesson for next time: any
change that goes on the branch should trigger a check that the AI log still reflects the work.

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
