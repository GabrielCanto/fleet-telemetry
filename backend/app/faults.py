"""Fault-transition logic — the single funnel for entering the `fault` state.

Both entry points (the telemetry ingest path and the explicit status endpoint) lock the
vehicle row FOR UPDATE, capture `previous_status`, and only call `apply_fault_transition`
on the genuine non-fault -> fault edge. The function OWNS setting status='fault' so the
caller must NOT set it beforehand (doing so would defeat the edge guard).

Idempotency layers (so a concurrent double-fault yields exactly one maintenance record):
  1. caller edge guard: the FOR UPDATE lock serializes attempts; the second one re-reads
     status='fault' and skips the funnel entirely;
  2. conditional mission UPDATE (... WHERE status='active');
  3. partial unique index uq_one_open_maintenance_per_vehicle + ON CONFLICT DO NOTHING.
"""
from datetime import datetime, timezone

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import MaintenanceRecord, Mission, MissionStatus, Vehicle, VehicleStatus


class UnknownVehicleError(Exception):
    """Raised when telemetry / a status update references an unprovisioned vehicle_id."""

    def __init__(self, vehicle_id: str):
        super().__init__(vehicle_id)
        self.vehicle_id = vehicle_id


def apply_fault_transition(
    db: Session,
    vehicle: Vehicle,
    triggering_event_id: int | None = None,
    reason: str = "Vehicle entered fault state",
) -> tuple[bool, bool]:
    """Set status=fault, cancel the active mission, open a maintenance record.

    Preconditions: the caller holds a FOR UPDATE lock on `vehicle` and has verified the
    vehicle was NOT already in `fault`. Returns (mission_cancelled, maintenance_created).
    """
    vehicle.status = VehicleStatus.fault
    now = datetime.now(timezone.utc)

    cancel = db.execute(
        update(Mission)
        .where(Mission.vehicle_id == vehicle.vehicle_id, Mission.status == MissionStatus.active)
        .values(status=MissionStatus.cancelled, cancelled_at=now)
    )
    mission_cancelled = cancel.rowcount > 0

    # RETURNING + first() is the reliable way to detect whether ON CONFLICT DO NOTHING
    # actually inserted (rowcount is unreliable for ON CONFLICT under psycopg3).
    created = db.execute(
        pg_insert(MaintenanceRecord)
        .values(vehicle_id=vehicle.vehicle_id, reason=reason, triggering_event_id=triggering_event_id)
        .on_conflict_do_nothing(
            index_elements=["vehicle_id"], index_where=text("resolved_at IS NULL")
        )
        .returning(MaintenanceRecord.id)
    )
    maintenance_created = created.first() is not None

    return mission_cancelled, maintenance_created


def transition_status(
    db: Session,
    vehicle_id: str,
    new_status: VehicleStatus,
    triggering_event_id: int | None = None,
    reason: str = "Manual status update to fault",
) -> tuple[VehicleStatus, bool, bool]:
    """Lock the vehicle, capture previous_status, apply the fault edge or set status.

    The caller manages the surrounding transaction. Returns
    (previous_status, mission_cancelled, maintenance_created). Raises UnknownVehicleError.
    """
    vehicle = db.get(Vehicle, vehicle_id, with_for_update=True)
    if vehicle is None:
        raise UnknownVehicleError(vehicle_id)

    previous_status = vehicle.status
    mission_cancelled = maintenance_created = False

    if new_status == VehicleStatus.fault and previous_status != VehicleStatus.fault:
        mission_cancelled, maintenance_created = apply_fault_transition(
            db, vehicle, triggering_event_id=triggering_event_id, reason=reason
        )
    else:
        vehicle.status = new_status

    return previous_status, mission_cancelled, maintenance_created
