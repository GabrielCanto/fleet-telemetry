"""Vehicle endpoints:
  GET  /vehicles                     -> live list (status, battery, most-recent anomaly)
  POST /vehicles/{vehicle_id}/status -> atomic status update (fault edge cancels mission
                                         + opens a maintenance record)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..faults import UnknownVehicleError, transition_status
from ..models import Anomaly, Vehicle
from ..schemas import StatusUpdateIn, StatusUpdateOut, VehicleAnomalyOut, VehicleOut

router = APIRouter(tags=["vehicles"])


def _vid_key(vehicle_id: str):
    """Sort v-1 .. v-50 numerically rather than lexicographically."""
    try:
        return (0, int(vehicle_id.split("-")[1]))
    except (IndexError, ValueError):
        return (1, vehicle_id)


@router.get("/vehicles", response_model=list[VehicleOut])
def list_vehicles(db: Session = Depends(get_db)):
    vehicles = sorted(
        db.execute(select(Vehicle)).scalars().all(), key=lambda v: _vid_key(v.vehicle_id)
    )

    # one extra query to fetch the most-recent anomaly per vehicle (no N+1)
    anomaly_ids = [v.last_anomaly_id for v in vehicles if v.last_anomaly_id is not None]
    anomalies: dict[int, Anomaly] = {}
    if anomaly_ids:
        for a in db.execute(select(Anomaly).where(Anomaly.id.in_(anomaly_ids))).scalars():
            anomalies[a.id] = a

    out: list[VehicleOut] = []
    for v in vehicles:
        a = anomalies.get(v.last_anomaly_id) if v.last_anomaly_id is not None else None
        out.append(
            VehicleOut(
                vehicle_id=v.vehicle_id,
                status=v.status,
                battery_pct=v.battery_pct,
                speed_mps=v.speed_mps,
                lat=v.lat,
                lon=v.lon,
                current_zone=v.current_zone,
                last_seen_at=v.last_seen_at,
                last_anomaly=(
                    VehicleAnomalyOut(
                        anomaly_type=a.anomaly_type,
                        message=a.message,
                        severity=a.severity,
                        event_ts=a.event_ts,
                    )
                    if a
                    else None
                ),
            )
        )
    return out


@router.post("/vehicles/{vehicle_id}/status", response_model=StatusUpdateOut)
def update_status(vehicle_id: str, body: StatusUpdateIn, db: Session = Depends(get_db)) -> StatusUpdateOut:
    try:
        with db.begin():
            previous_status, mission_cancelled, maintenance_created = transition_status(
                db, vehicle_id, body.status
            )
    except UnknownVehicleError:
        raise HTTPException(status_code=404, detail=f"Unknown vehicle_id: {vehicle_id}")

    return StatusUpdateOut(
        vehicle_id=vehicle_id,
        status=body.status,
        previous_status=previous_status,
        mission_cancelled=mission_cancelled,
        maintenance_record_created=maintenance_created,
    )
