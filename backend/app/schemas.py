"""Pydantic request/response schemas.

Validation happens at the boundary, before any DB work: `status` must be a known enum
value and `zone_entered` must be a known zone (else 422); the incoming `timestamp` is
normalized to timezone-aware UTC.
"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .constants import ZONES
from .models import AnomalyType, MissionStatus, Severity, VehicleStatus  # noqa: F401


def _to_utc(dt: datetime) -> datetime:
    """Naive datetimes are assumed UTC; aware datetimes are converted to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --- Requests ---

class TelemetryIn(BaseModel):
    vehicle_id: str
    timestamp: datetime
    lat: Optional[float] = None
    lon: Optional[float] = None
    battery_pct: float = Field(ge=0, le=100)
    speed_mps: float = Field(ge=0)
    status: VehicleStatus
    error_codes: list[str] = Field(default_factory=list)
    zone_entered: Optional[str] = None

    @field_validator("timestamp")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return _to_utc(v)

    @field_validator("zone_entered")
    @classmethod
    def _known_zone(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ZONES:
            raise ValueError(f"unknown zone_entered: {v!r}")
        return v


class StatusUpdateIn(BaseModel):
    status: VehicleStatus


# --- Responses ---

class TelemetryAnomalyOut(BaseModel):
    anomaly_type: AnomalyType
    message: str
    severity: Severity


class TelemetryAccepted(BaseModel):
    event_id: int
    is_newest: bool
    fault_transition: bool
    anomalies: list[TelemetryAnomalyOut]


class ZoneCountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    zone_id: str
    name: str
    entry_count: int


class AnomalyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    vehicle_id: str
    anomaly_type: AnomalyType
    message: str
    severity: Severity
    detected_at: datetime
    event_ts: datetime
    event_id: Optional[int]


class FleetState(BaseModel):
    idle: int = 0
    moving: int = 0
    charging: int = 0
    fault: int = 0


class VehicleAnomalyOut(BaseModel):
    anomaly_type: AnomalyType
    message: str
    severity: Severity
    event_ts: datetime


class VehicleOut(BaseModel):
    vehicle_id: str
    status: VehicleStatus
    battery_pct: float
    speed_mps: float
    lat: Optional[float]
    lon: Optional[float]
    current_zone: Optional[str]
    last_seen_at: Optional[datetime]
    last_anomaly: Optional[VehicleAnomalyOut] = None


class StatusUpdateOut(BaseModel):
    vehicle_id: str
    status: VehicleStatus
    previous_status: VehicleStatus
    mission_cancelled: bool
    maintenance_record_created: bool
