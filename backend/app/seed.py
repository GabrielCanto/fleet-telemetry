"""Idempotent seed: 20 zones, 50 vehicles, one active mission per vehicle.

Idempotent (ON CONFLICT DO NOTHING + an existence check for missions) so it is safe to
run on every startup. The active missions exist so the fault path has something to cancel.
"""
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .constants import VEHICLE_IDS, ZONES
from .models import Mission, MissionStatus, Vehicle, VehicleStatus, Zone


def seed(engine: Engine) -> None:
    with Session(engine) as db, db.begin():
        for zone_id in ZONES:
            db.execute(
                pg_insert(Zone)
                .values(zone_id=zone_id, name=zone_id.replace("_", " ").title())
                .on_conflict_do_nothing(index_elements=["zone_id"])
            )

        for vehicle_id in VEHICLE_IDS:
            db.execute(
                pg_insert(Vehicle)
                .values(vehicle_id=vehicle_id, status=VehicleStatus.idle, battery_pct=100.0, speed_mps=0.0)
                .on_conflict_do_nothing(index_elements=["vehicle_id"])
            )

        # one active mission per vehicle (only create the ones that are missing)
        have_active = set(
            db.execute(
                select(Mission.vehicle_id).where(Mission.status == MissionStatus.active)
            ).scalars()
        )
        for vehicle_id in VEHICLE_IDS:
            if vehicle_id not in have_active:
                db.add(Mission(vehicle_id=vehicle_id, status=MissionStatus.active))
