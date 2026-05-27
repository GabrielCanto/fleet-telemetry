"""The ingest spine: one transaction per telemetry event, anchored on a single
`SELECT ... FOR UPDATE` lock of the vehicle row.

Order matters (see the ADR):
  1. lock the vehicle row (or raise UnknownVehicleError -> 404)
  2. capture previous_status BEFORE any mutation (the fault edge depends on it)
  3. out-of-order guard (is_newest)
  4. insert the telemetry event (ALWAYS, even for late events)
  5. detect anomalies: stateless always, stateful only if is_newest -- detection runs
     BEFORE the state update so it reads the genuine previous values
  6. update current state ONLY if is_newest; on the non-fault->fault edge defer to
     apply_fault_transition (which owns setting status='fault')
  7. increment the zone counter atomically (a crossing counts even if late)

The caller wraps this in a transaction (`with db.begin(): ...`) so all of the above
commit atomically.
"""
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from .anomalies import detect_anomalies
from .faults import UnknownVehicleError, apply_fault_transition
from .models import Anomaly, Severity, TelemetryEvent, Vehicle, VehicleStatus
from .schemas import TelemetryIn


def ingest_event(db: Session, event: TelemetryIn) -> dict:
    # 1. lock the vehicle row (fixed, seeded fleet -> unknown id is a 404, not an upsert)
    vehicle = db.get(Vehicle, event.vehicle_id, with_for_update=True)
    if vehicle is None:
        raise UnknownVehicleError(event.vehicle_id)

    # 2. capture previous status before mutating anything
    previous_status = vehicle.status

    # 3. out-of-order guard
    is_newest = vehicle.last_event_ts is None or event.timestamp > vehicle.last_event_ts

    # 4. append the event unconditionally
    ev = TelemetryEvent(
        vehicle_id=event.vehicle_id,
        ts=event.timestamp,
        lat=event.lat,
        lon=event.lon,
        battery_pct=event.battery_pct,
        speed_mps=event.speed_mps,
        status=event.status,
        error_codes=list(event.error_codes),
        zone_entered=event.zone_entered,
    )
    db.add(ev)
    db.flush()  # assign ev.id

    # 5. anomalies (detect before the state update so previous values are intact)
    detected = detect_anomalies(event, vehicle, is_newest)
    anomaly_rows: list[Anomaly] = []
    for atype, message, severity, detail in detected:
        row = Anomaly(
            vehicle_id=event.vehicle_id,
            anomaly_type=atype,
            message=message,
            severity=severity,
            event_ts=event.timestamp,
            event_id=ev.id,
            detail=detail,
        )
        db.add(row)
        anomaly_rows.append(row)

    fault_transition = False

    # 6. current-state update only for the newest event
    if is_newest:
        if anomaly_rows:
            db.flush()  # assign anomaly ids
            # surface the most severe anomaly from this event (critical wins, then latest)
            chosen = max(anomaly_rows, key=lambda a: (a.severity == Severity.critical, a.id))
            vehicle.last_anomaly_id = chosen.id

        vehicle.battery_pct = event.battery_pct
        vehicle.speed_mps = event.speed_mps
        if event.lat is not None:
            vehicle.lat = event.lat
        if event.lon is not None:
            vehicle.lon = event.lon
        if event.zone_entered is not None:
            vehicle.current_zone = event.zone_entered
        vehicle.last_event_ts = event.timestamp
        vehicle.last_event_id = ev.id
        vehicle.last_seen_at = datetime.now(timezone.utc)

        # status handling: only the genuine non-fault -> fault edge triggers the funnel.
        if event.status == VehicleStatus.fault and previous_status != VehicleStatus.fault:
            apply_fault_transition(db, vehicle, triggering_event_id=ev.id)
            fault_transition = True
        else:
            vehicle.status = event.status

    # 7. atomic zone counter -- a crossing is counted regardless of event ordering
    if event.zone_entered is not None:
        db.execute(
            text("UPDATE zones SET entry_count = entry_count + 1 WHERE zone_id = :z"),
            {"z": event.zone_entered},
        )

    return {
        "event_id": ev.id,
        "is_newest": is_newest,
        "fault_transition": fault_transition,
        "anomalies": [(a.anomaly_type, a.message, a.severity) for a in anomaly_rows],
    }
