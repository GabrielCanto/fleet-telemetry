"""Ingest spine: tie-case correctness under concurrency.

The `is_newest` guard uses a STRICT `>` (`event.timestamp > vehicle.last_event_ts`), so
two concurrent events for the same vehicle with identical timestamps yield:

  - both rows persisted (append is unconditional);
  - exactly ONE state update — the first-committed event wins, the tie is treated as
    out-of-order because `T > T` is false;
  - the zone counter increments TWICE (a crossing counts regardless of ordering);
  - exactly ONE fault transition + maintenance record, even if both events carry
    `status=fault` on a non-fault vehicle, because is_newest gates the funnel.
"""
import concurrent.futures
import threading
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.ingest import ingest_event
from app.models import (
    MaintenanceRecord,
    Mission,
    MissionStatus,
    TelemetryEvent,
    Vehicle,
    VehicleStatus,
    Zone,
)
from app.schemas import TelemetryIn


def test_same_timestamp_concurrent_events_persist_both_with_one_state_update(session_factory):
    """Two events, same vehicle, identical timestamp, fired together: both stored,
    only one is_newest, zone counted twice, current state is one of the two batteries."""
    vehicle_id = "v-1"
    t = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    zone = "charging_bay_1"
    barrier = threading.Barrier(2)
    results: list[dict | None] = [None, None]

    def worker(idx: int, battery: float):
        event = TelemetryIn(
            vehicle_id=vehicle_id,
            timestamp=t,
            battery_pct=battery,
            speed_mps=1.0,
            status="moving",
            zone_entered=zone,
        )
        barrier.wait()  # release together to force the lock to actually contend
        with session_factory() as db, db.begin():
            results[idx] = ingest_event(db, event)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        # different battery values so we can confirm the winner is one of them
        list(ex.map(lambda args: worker(*args), [(0, 60.0), (1, 40.0)]))

    # exactly one of the two events was treated as the newest (strict > breaks the tie)
    flags = sorted(r["is_newest"] for r in results if r is not None)
    assert flags == [False, True]

    with session_factory() as db:
        event_count = db.execute(
            select(func.count())
            .select_from(TelemetryEvent)
            .where(TelemetryEvent.vehicle_id == vehicle_id)
        ).scalar_one()
        zone_count = db.execute(
            select(Zone.entry_count).where(Zone.zone_id == zone)
        ).scalar_one()
        vehicle = db.get(Vehicle, vehicle_id)

    assert event_count == 2                # both rows persisted (append-only)
    assert zone_count == 2                 # a crossing counts regardless of ordering
    assert vehicle.last_event_ts == t      # state advanced to the tied timestamp
    assert vehicle.battery_pct in (40.0, 60.0)  # whichever committed first wins; both are valid outcomes


def test_same_timestamp_concurrent_fault_events_trigger_funnel_exactly_once(session_factory):
    """Two concurrent fault events with identical timestamps on a non-fault vehicle: the
    funnel must run exactly once. Belt-and-suspenders — the is_newest tiebreak skips
    the second event's state update entirely, before the edge guard would even need to."""
    vehicle_id = "v-2"
    t = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    barrier = threading.Barrier(2)

    def worker(_):
        event = TelemetryIn(
            vehicle_id=vehicle_id,
            timestamp=t,
            battery_pct=50.0,
            speed_mps=0.0,
            status="fault",
        )
        barrier.wait()
        with session_factory() as db, db.begin():
            ingest_event(db, event)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(worker, range(2)))

    with session_factory() as db:
        records = db.execute(
            select(func.count())
            .select_from(MaintenanceRecord)
            .where(MaintenanceRecord.vehicle_id == vehicle_id)
        ).scalar_one()
        active_missions = db.execute(
            select(func.count())
            .select_from(Mission)
            .where(Mission.vehicle_id == vehicle_id, Mission.status == MissionStatus.active)
        ).scalar_one()
        events = db.execute(
            select(func.count())
            .select_from(TelemetryEvent)
            .where(TelemetryEvent.vehicle_id == vehicle_id)
        ).scalar_one()
        vehicle = db.get(Vehicle, vehicle_id)

    assert records == 1
    assert active_missions == 0
    assert events == 2                         # both telemetry rows are still persisted
    assert vehicle.status == VehicleStatus.fault
