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
