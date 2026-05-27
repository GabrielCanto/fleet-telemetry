"""POST /telemetry — accept one telemetry event and run the ingest spine."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..faults import UnknownVehicleError
from ..ingest import ingest_event
from ..schemas import TelemetryAccepted, TelemetryAnomalyOut, TelemetryIn

router = APIRouter(tags=["telemetry"])


@router.post("/telemetry", response_model=TelemetryAccepted, status_code=201)
def post_telemetry(event: TelemetryIn, db: Session = Depends(get_db)) -> TelemetryAccepted:
    try:
        with db.begin():
            result = ingest_event(db, event)
    except UnknownVehicleError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown vehicle_id: {exc.vehicle_id}")

    return TelemetryAccepted(
        event_id=result["event_id"],
        is_newest=result["is_newest"],
        fault_transition=result["fault_transition"],
        anomalies=[
            TelemetryAnomalyOut(anomaly_type=t, message=m, severity=s)
            for (t, m, s) in result["anomalies"]
        ],
    )
