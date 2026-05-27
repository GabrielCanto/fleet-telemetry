"""Fault transition: concurrent fault requests for the same vehicle must yield exactly one
maintenance record and one cancelled mission (atomic + idempotent)."""
import concurrent.futures
import threading

from sqlalchemy import func, select

from app.faults import transition_status
from app.models import MaintenanceRecord, Mission, MissionStatus, Vehicle, VehicleStatus


def test_concurrent_double_fault_yields_one_maintenance_record(session_factory):
    vehicle_id = "v-1"
    n = 8
    barrier = threading.Barrier(n)

    def worker(_):
        barrier.wait()
        with session_factory() as db, db.begin():
            transition_status(db, vehicle_id, VehicleStatus.fault, reason="concurrent test")

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(worker, range(n)))

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
        vehicle = db.get(Vehicle, vehicle_id)

    assert records == 1
    assert active_missions == 0
    assert vehicle.status == VehicleStatus.fault
