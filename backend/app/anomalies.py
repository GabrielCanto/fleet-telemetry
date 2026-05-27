"""Anomaly detection rules — a small, deterministic, auditable taxonomy (6 rules).

Stateless rules depend only on the incoming event. Stateful rules compare the event to
the vehicle's previous reading (read O(1) from the locked `vehicles` row) and are only
meaningful for the newest event, so the caller passes `is_newest`.

Each rule yields a (type, message, severity, detail) tuple.
"""
from typing import Optional

from . import constants
from .models import AnomalyType, Severity, Vehicle, VehicleStatus
from .schemas import TelemetryIn

Detected = tuple[AnomalyType, str, Severity, Optional[dict]]


def detect_anomalies(event: TelemetryIn, vehicle: Vehicle, is_newest: bool) -> list[Detected]:
    out: list[Detected] = []

    # --- stateless (always evaluated) ---
    if event.status == VehicleStatus.fault:
        out.append((AnomalyType.fault_status, "Vehicle reported fault status", Severity.critical, None))

    if event.battery_pct < constants.LOW_BATTERY_PCT:
        sev = Severity.critical if event.battery_pct < constants.CRITICAL_BATTERY_PCT else Severity.warning
        out.append((
            AnomalyType.low_battery,
            f"Battery {event.battery_pct:.0f}% below {constants.LOW_BATTERY_PCT}% threshold",
            sev,
            {"battery_pct": event.battery_pct},
        ))

    if event.error_codes:
        out.append((
            AnomalyType.error_code,
            "Error codes reported: " + ", ".join(event.error_codes),
            Severity.warning,
            {"error_codes": list(event.error_codes)},
        ))

    if event.speed_mps > constants.MAX_SPEED_MPS:
        out.append((
            AnomalyType.overspeed,
            f"Speed {event.speed_mps:.1f} m/s exceeds {constants.MAX_SPEED_MPS} m/s cap",
            Severity.warning,
            {"speed_mps": event.speed_mps},
        ))

    # --- stateful (only for the newest event, and only with a prior reading) ---
    if is_newest and vehicle.last_event_ts is not None:
        prev = vehicle.battery_pct
        delta = event.battery_pct - prev
        if abs(delta) > constants.BATTERY_JUMP_PCT:
            out.append((
                AnomalyType.battery_jump,
                f"Battery jumped {delta:+.0f} pts ({prev:.0f}% -> {event.battery_pct:.0f}%) between consecutive events",
                Severity.warning,
                {"prev_battery_pct": prev, "battery_pct": event.battery_pct},
            ))
        if delta > constants.BATTERY_RISE_PCT and event.status != VehicleStatus.charging:
            out.append((
                AnomalyType.battery_increase_no_charge,
                f"Battery rose {delta:+.0f} pts while status={event.status.value} (not charging)",
                Severity.warning,
                {"prev_battery_pct": prev, "battery_pct": event.battery_pct, "status": event.status.value},
            ))

    return out
