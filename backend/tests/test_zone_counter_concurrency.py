"""Zone counter: every concurrent entry into the same zone must be counted (no lost
updates). This is the 'shift change' scenario — many vehicles converge on one charging
bay in the same instant."""
import concurrent.futures
import threading
from datetime import datetime, timezone

from sqlalchemy import select

from app.constants import VEHICLE_IDS
from app.ingest import ingest_event
from app.models import Zone
from app.schemas import TelemetryIn


def test_same_zone_burst_counts_every_entry(session_factory):
    n = 30
    zone = "charging_bay_1"
    barrier = threading.Barrier(n)

    def worker(i: int):
        event = TelemetryIn(
            vehicle_id=VEHICLE_IDS[i],
            timestamp=datetime.now(timezone.utc),
            battery_pct=50,
            speed_mps=1.0,
            status="moving",
            zone_entered=zone,
        )
        barrier.wait()  # release all workers together to maximize contention
        with session_factory() as db, db.begin():
            ingest_event(db, event)

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(worker, range(n)))

    with session_factory() as db:
        count = db.execute(select(Zone.entry_count).where(Zone.zone_id == zone)).scalar_one()

    assert count == n
