"""GET /zones/counts — per-zone entry counts."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Zone
from ..schemas import ZoneCountOut

router = APIRouter(tags=["zones"])


@router.get("/zones/counts", response_model=list[ZoneCountOut])
def zone_counts(db: Session = Depends(get_db)):
    return db.execute(select(Zone).order_by(Zone.zone_id)).scalars().all()
