"""Fleet aggregate: per-status counts always sum to the fleet size, including under
concurrent status updates (single GROUP BY = single MVCC snapshot)."""
import concurrent.futures
import threading

from sqlalchemy import func, select

from app.faults import transition_status
from app.models import Vehicle, VehicleStatus


def test_fleet_state_basic_counts(client):
    for vid, status in [("v-1", "moving"), ("v-2", "moving"), ("v-3", "charging"), ("v-4", "fault")]:
        assert client.post(f"/vehicles/{vid}/status", json={"status": status}).status_code == 200

    fleet = client.get("/fleet/state").json()
    assert fleet == {"idle": 46, "moving": 2, "charging": 1, "fault": 1}
    assert sum(fleet.values()) == 50


def test_fleet_state_safe_under_concurrent_updates(session_factory):
    cycle = [VehicleStatus.idle, VehicleStatus.moving, VehicleStatus.charging]
    stop = threading.Event()

    def writer(i: int):
        vehicle_id = f"v-{i + 1}"
        steps = 0
        while not stop.is_set() and steps < 25:
            with session_factory() as db, db.begin():
                transition_status(db, vehicle_id, cycle[steps % len(cycle)])
            steps += 1

    def reader(samples: list):
        for _ in range(80):
            with session_factory() as db:
                rows = db.execute(
                    select(Vehicle.status, func.count()).group_by(Vehicle.status)
                ).all()
            samples.append(sum(n for _, n in rows))

    samples: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        writers = [ex.submit(writer, i) for i in range(10)]
        reader_future = ex.submit(reader, samples)
        concurrent.futures.wait(writers)
        stop.set()
        reader_future.result()

    assert samples
    assert all(total == 50 for total in samples)
