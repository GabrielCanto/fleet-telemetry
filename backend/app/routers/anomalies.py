"""GET /anomalies — recent anomalies filtered by vehicle and time range."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Anomaly
from ..schemas import AnomalyOut

router = APIRouter(tags=["anomalies"])


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("/anomalies", response_model=list[AnomalyOut])
def list_anomalies(
    vehicle_id: Optional[str] = None,
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    stmt = select(Anomaly)
    if vehicle_id:
        stmt = stmt.where(Anomaly.vehicle_id == vehicle_id)
    if from_ is not None:
        stmt = stmt.where(Anomaly.event_ts >= _utc(from_))
    if to is not None:
        stmt = stmt.where(Anomaly.event_ts <= _utc(to))
    stmt = stmt.order_by(Anomaly.event_ts.desc(), Anomaly.id.desc()).limit(limit)
    return db.execute(stmt).scalars().all()
