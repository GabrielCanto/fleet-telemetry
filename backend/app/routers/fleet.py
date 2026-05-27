"""GET /fleet/state — aggregate per-status counts across the fleet.

Computed on read with a single GROUP BY statement, so it reflects one MVCC snapshot and
always sums to the fleet size, even under concurrent updates. No maintained counter table.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Vehicle
from ..schemas import FleetState

router = APIRouter(tags=["fleet"])


@router.get("/fleet/state", response_model=FleetState)
def fleet_state(db: Session = Depends(get_db)) -> FleetState:
    rows = db.execute(select(Vehicle.status, func.count()).group_by(Vehicle.status)).all()
    counts = {status.value: n for status, n in rows}
    return FleetState(
        idle=counts.get("idle", 0),
        moving=counts.get("moving", 0),
        charging=counts.get("charging", 0),
        fault=counts.get("fault", 0),
    )
