"""Anomaly detection + ingest semantics via the HTTP API."""
from datetime import datetime, timedelta, timezone


def _event(**overrides) -> dict:
    base = {
        "vehicle_id": "v-1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "battery_pct": 80,
        "speed_mps": 1.0,
        "status": "moving",
    }
    base.update(overrides)
    return base


def _types(response) -> set:
    return {a["anomaly_type"] for a in response.json()["anomalies"]}


def test_stateless_rules_fire(client):
    r = client.post(
        "/telemetry",
        json=_event(vehicle_id="v-1", battery_pct=4, speed_mps=12, status="fault", error_codes=["E1"]),
    )
    assert r.status_code == 201
    assert _types(r) == {"fault_status", "low_battery", "overspeed", "error_code"}


def test_clean_event_has_no_anomalies(client):
    r = client.post("/telemetry", json=_event(vehicle_id="v-7", battery_pct=90, speed_mps=2))
    assert r.status_code == 201
    assert r.json()["anomalies"] == []


def test_battery_jump_is_stateful(client):
    t0 = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    first = client.post("/telemetry", json=_event(vehicle_id="v-8", timestamp=t0.isoformat(), battery_pct=80))
    assert first.json()["anomalies"] == []  # first reading just establishes the baseline
    second = client.post(
        "/telemetry",
        json=_event(vehicle_id="v-8", timestamp=(t0 + timedelta(seconds=1)).isoformat(), battery_pct=20),
    )
    assert "battery_jump" in _types(second)


def test_battery_increase_without_charging_is_stateful(client):
    t0 = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    client.post("/telemetry", json=_event(vehicle_id="v-9", timestamp=t0.isoformat(), battery_pct=50))
    r = client.post(
        "/telemetry",
        json=_event(vehicle_id="v-9", timestamp=(t0 + timedelta(seconds=1)).isoformat(), battery_pct=60, status="moving"),
    )
    assert "battery_increase_no_charge" in _types(r)


def test_out_of_order_event_skips_state_and_stateful(client):
    t0 = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    client.post("/telemetry", json=_event(vehicle_id="v-10", timestamp=t0.isoformat(), battery_pct=70, speed_mps=3))
    late = client.post(
        "/telemetry",
        json=_event(vehicle_id="v-10", timestamp=(t0 - timedelta(minutes=5)).isoformat(), battery_pct=10),
    )
    assert late.status_code == 201
    assert late.json()["is_newest"] is False
    # stateless rule still recorded for the (late) event...
    assert "low_battery" in _types(late)
    # ...but the stateful battery_jump is skipped, and current state is unchanged
    assert "battery_jump" not in _types(late)
    vehicle = next(v for v in client.get("/vehicles").json() if v["vehicle_id"] == "v-10")
    assert vehicle["battery_pct"] == 70
    assert vehicle["status"] == "moving"


def test_late_fault_event_does_not_transition(client):
    t0 = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    client.post("/telemetry", json=_event(vehicle_id="v-11", timestamp=t0.isoformat(), status="moving"))
    late_fault = client.post(
        "/telemetry",
        json=_event(vehicle_id="v-11", timestamp=(t0 - timedelta(minutes=1)).isoformat(), status="fault"),
    )
    assert late_fault.json()["fault_transition"] is False
    vehicle = next(v for v in client.get("/vehicles").json() if v["vehicle_id"] == "v-11")
    assert vehicle["status"] == "moving"


def test_unknown_vehicle_returns_404(client):
    assert client.post("/telemetry", json=_event(vehicle_id="v-999")).status_code == 404


def test_status_update_unknown_vehicle_returns_404(client):
    assert client.post("/vehicles/v-999/status", json={"status": "fault"}).status_code == 404


def test_unknown_zone_returns_422(client):
    assert client.post("/telemetry", json=_event(vehicle_id="v-1", zone_entered="nowhere")).status_code == 422


def test_anomalies_filter_by_vehicle_and_time(client):
    t0 = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    client.post("/telemetry", json=_event(vehicle_id="v-12", timestamp=t0.isoformat(), battery_pct=4))
    client.post("/telemetry", json=_event(vehicle_id="v-13", timestamp=t0.isoformat(), battery_pct=4))

    only_12 = client.get("/anomalies", params={"vehicle_id": "v-12"}).json()
    assert only_12 and all(a["vehicle_id"] == "v-12" for a in only_12)

    after = client.get(
        "/anomalies", params={"vehicle_id": "v-12", "from": (t0 + timedelta(hours=1)).isoformat()}
    ).json()
    assert after == []


# --- threshold boundaries (these guard the tunable constants from off-by-one regressions) ---


def test_low_battery_threshold_is_strictly_below_15_and_severity_flips_at_5(client):
    # 15 sits exactly on the threshold -> no anomaly (the rule is `< 15`, strict)
    r = client.post("/telemetry", json=_event(vehicle_id="v-1", battery_pct=15))
    assert "low_battery" not in _types(r)

    # 14 fires as a warning
    r = client.post("/telemetry", json=_event(vehicle_id="v-2", battery_pct=14))
    sev = {a["anomaly_type"]: a["severity"] for a in r.json()["anomalies"]}
    assert sev.get("low_battery") == "warning"

    # 5 still warning (the critical rule is `< 5`, strict)
    r = client.post("/telemetry", json=_event(vehicle_id="v-3", battery_pct=5))
    sev = {a["anomaly_type"]: a["severity"] for a in r.json()["anomalies"]}
    assert sev.get("low_battery") == "warning"

    # 4 escalates to critical
    r = client.post("/telemetry", json=_event(vehicle_id="v-4", battery_pct=4))
    sev = {a["anomaly_type"]: a["severity"] for a in r.json()["anomalies"]}
    assert sev.get("low_battery") == "critical"


def test_overspeed_threshold_is_strictly_above_8(client):
    # 8.0 m/s is at the warehouse cap -> not yet an anomaly
    r = client.post("/telemetry", json=_event(vehicle_id="v-5", speed_mps=8))
    assert "overspeed" not in _types(r)

    # 8.5 m/s exceeds the cap
    r = client.post("/telemetry", json=_event(vehicle_id="v-6", speed_mps=8.5))
    assert "overspeed" in _types(r)


def test_battery_jump_threshold_is_strictly_above_30(client):
    t0 = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)

    # |delta| == 30 sits on the threshold -> no jump (the rule is `> 30`, strict)
    client.post("/telemetry", json=_event(vehicle_id="v-7", timestamp=t0.isoformat(), battery_pct=80))
    r = client.post(
        "/telemetry",
        json=_event(vehicle_id="v-7", timestamp=(t0 + timedelta(seconds=1)).isoformat(), battery_pct=50),
    )
    assert "battery_jump" not in _types(r)

    # |delta| == 31 trips the rule (fresh vehicle so the prior reading isn't the seed default)
    client.post("/telemetry", json=_event(vehicle_id="v-8", timestamp=t0.isoformat(), battery_pct=80))
    r = client.post(
        "/telemetry",
        json=_event(vehicle_id="v-8", timestamp=(t0 + timedelta(seconds=1)).isoformat(), battery_pct=49),
    )
    assert "battery_jump" in _types(r)


# --- semantics not covered above ---


def test_first_event_does_not_fire_stateful_rules(client):
    # Seeded vehicles start at battery_pct=100. A first reading of 10 means abs(delta)=90
    # which WOULD trip battery_jump if the guard `vehicle.last_event_ts is not None` were
    # missing — but on the first event, stateful rules are suppressed by design.
    r = client.post("/telemetry", json=_event(vehicle_id="v-15", battery_pct=10))
    types = _types(r)
    assert "low_battery" in types               # stateless still fires
    assert "battery_jump" not in types          # stateful suppressed (no prior reading)
    assert "battery_increase_no_charge" not in types


def test_out_of_order_event_still_increments_zone_counter(client):
    t0 = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)

    # establish a newest reading without a zone
    client.post("/telemetry", json=_event(vehicle_id="v-16", timestamp=t0.isoformat()))

    # a late event with a zone — state must not change, but the crossing is still counted
    late = client.post(
        "/telemetry",
        json=_event(
            vehicle_id="v-16",
            timestamp=(t0 - timedelta(minutes=5)).isoformat(),
            zone_entered="charging_bay_1",
        ),
    )
    assert late.status_code == 201
    assert late.json()["is_newest"] is False

    counts = {z["zone_id"]: z["entry_count"] for z in client.get("/zones/counts").json()}
    assert counts["charging_bay_1"] == 1

    vehicle = next(v for v in client.get("/vehicles").json() if v["vehicle_id"] == "v-16")
    assert vehicle["current_zone"] is None  # state untouched by the late event


def test_fault_via_telemetry_cancels_mission_and_opens_maintenance(client, session_factory):
    from sqlalchemy import select

    from app.models import MaintenanceRecord, Mission, MissionStatus

    r = client.post("/telemetry", json=_event(vehicle_id="v-17", status="fault"))
    assert r.status_code == 201
    body = r.json()
    assert body["fault_transition"] is True
    assert "fault_status" in {a["anomaly_type"] for a in body["anomalies"]}

    with session_factory() as db:
        active = db.execute(
            select(Mission).where(
                Mission.vehicle_id == "v-17", Mission.status == MissionStatus.active
            )
        ).scalars().all()
        records = db.execute(
            select(MaintenanceRecord).where(MaintenanceRecord.vehicle_id == "v-17")
        ).scalars().all()
    assert active == []
    assert len(records) == 1
    assert records[0].triggering_event_id == body["event_id"]

    vehicle = next(v for v in client.get("/vehicles").json() if v["vehicle_id"] == "v-17")
    assert vehicle["status"] == "fault"


def test_last_anomaly_id_surfaces_critical_over_warning_from_the_same_event(client):
    # One event raises BOTH a critical (low_battery@4) and a warning (overspeed). The
    # vehicles projection must surface the critical one, not the latest by id.
    r = client.post("/telemetry", json=_event(vehicle_id="v-18", battery_pct=4, speed_mps=12))
    assert r.status_code == 201
    types = _types(r)
    assert {"low_battery", "overspeed"} <= types

    vehicle = next(v for v in client.get("/vehicles").json() if v["vehicle_id"] == "v-18")
    assert vehicle["last_anomaly"]["anomaly_type"] == "low_battery"
    assert vehicle["last_anomaly"]["severity"] == "critical"
