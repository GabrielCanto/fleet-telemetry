"""Concurrent startup: two backend processes calling `seed()` at once must both succeed
and converge on the canonical state (20 zones, 50 vehicles, 50 active missions).

The current single-uvicorn deployment never triggers this, but it pins the invariant for
the moment anyone adds `--workers 2` or a second replica (an `at significant scale`
option discussed in ADR §5).

Why it works despite missions using check-then-`db.add` (no ON CONFLICT): zones and
vehicles ARE inserted with `ON CONFLICT DO NOTHING`, and Postgres' speculative-insertion
semantics make the second seeder WAIT on the first seeder's uncommitted zone/vehicle
inserts. By the time the second seeder reaches `SELECT have_active`, the first has
committed all 50 missions and the check-then-add is a no-op. Effective serialization at
the first conflicting unique-index entry.
"""
import concurrent.futures
import threading

from sqlalchemy import func, select, text

from app.constants import VEHICLE_IDS, ZONES
from app.models import Mission, MissionStatus, Vehicle, Zone
from app.seed import seed


def test_concurrent_seed_is_idempotent(engine):
    # Undo the autouse-seeded state so both seeders genuinely race on a fresh DB.
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE anomalies, telemetry_events, maintenance_records, missions, "
                "vehicles, zones RESTART IDENTITY CASCADE"
            )
        )

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(_):
        barrier.wait()
        try:
            seed(engine)
        except BaseException as exc:  # noqa: BLE001 -- we want to surface ANY failure
            errors.append(exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(worker, range(2)))

    assert errors == [], f"concurrent seed raised: {errors!r}"

    with engine.begin() as conn:
        zone_count = conn.execute(select(func.count()).select_from(Zone)).scalar_one()
        vehicle_count = conn.execute(select(func.count()).select_from(Vehicle)).scalar_one()
        active_missions = conn.execute(
            select(func.count())
            .select_from(Mission)
            .where(Mission.status == MissionStatus.active)
        ).scalar_one()

    assert zone_count == len(ZONES)
    assert vehicle_count == len(VEHICLE_IDS)
    assert active_missions == len(VEHICLE_IDS)
