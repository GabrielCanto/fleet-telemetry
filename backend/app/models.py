"""SQLAlchemy 2.0 ORM models.

Tables: vehicles (current-state projection + last-event cache), telemetry_events
(append-only), zones, anomalies (append-only), missions, maintenance_records.

Two partial unique indexes encode hard invariants used by the fault transition:
  - at most one ACTIVE mission per vehicle
  - at most one OPEN (unresolved) maintenance record per vehicle  (exactly-once backstop)
"""
import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class VehicleStatus(str, enum.Enum):
    idle = "idle"
    moving = "moving"
    charging = "charging"
    fault = "fault"


class MissionStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    cancelled = "cancelled"


class AnomalyType(str, enum.Enum):
    fault_status = "fault_status"
    low_battery = "low_battery"
    error_code = "error_code"
    overspeed = "overspeed"
    battery_jump = "battery_jump"
    battery_increase_no_charge = "battery_increase_no_charge"


class Severity(str, enum.Enum):
    warning = "warning"
    critical = "critical"


# Shared Enum type instances so each Postgres ENUM type is created exactly once.
vehicle_status_enum = Enum(VehicleStatus, name="vehicle_status")
mission_status_enum = Enum(MissionStatus, name="mission_status")
anomaly_type_enum = Enum(AnomalyType, name="anomaly_type")
severity_enum = Enum(Severity, name="severity")


class Vehicle(Base):
    __tablename__ = "vehicles"

    vehicle_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[VehicleStatus] = mapped_column(
        vehicle_status_enum, nullable=False, default=VehicleStatus.idle
    )
    battery_pct: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    speed_mps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_zone: Mapped[str | None] = mapped_column(String, nullable=True)
    # last-event cache -> makes stateful anomaly detection O(1) under the row lock
    last_event_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # denormalized soft pointer to the most-recent anomaly (drives F2). Not a FK to avoid
    # a circular vehicles<->anomalies dependency at create_all time.
    last_anomaly_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String, ForeignKey("vehicles.vehicle_id"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_pct: Mapped[float] = mapped_column(Float, nullable=False)
    speed_mps: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[VehicleStatus] = mapped_column(vehicle_status_enum, nullable=False)
    error_codes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    zone_entered: Mapped[str | None] = mapped_column(String, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_telemetry_vehicle_ts", "vehicle_id", "ts"),)


class Zone(Base):
    __tablename__ = "zones"

    zone_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    entry_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


class Anomaly(Base):
    __tablename__ = "anomalies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String, ForeignKey("vehicles.vehicle_id"), nullable=False)
    anomaly_type: Mapped[AnomalyType] = mapped_column(anomaly_type_enum, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(severity_enum, nullable=False, default=Severity.warning)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("telemetry_events.id"), nullable=True
    )
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # serves GET /anomalies?vehicle_id=&from=&to= exactly (backward scan for DESC)
    __table_args__ = (Index("ix_anomalies_vehicle_event_ts", "vehicle_id", "event_ts"),)


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String, ForeignKey("vehicles.vehicle_id"), nullable=False)
    status: Mapped[MissionStatus] = mapped_column(
        mission_status_enum, nullable=False, default=MissionStatus.active
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_one_active_mission_per_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class MaintenanceRecord(Base):
    __tablename__ = "maintenance_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String, ForeignKey("vehicles.vehicle_id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    triggering_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("telemetry_events.id"), nullable=True
    )

    # exactly-once backstop: at most one open maintenance record per vehicle
    __table_args__ = (
        Index(
            "uq_one_open_maintenance_per_vehicle",
            "vehicle_id",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )
