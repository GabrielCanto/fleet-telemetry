"""Fleet simulator: drives the seeded fleet at ~1 Hz against the telemetry API.

Self-configuring: it discovers the vehicle roster and zones from the API, so the IDs
always match the backend seed (single source of truth). It injects occasional anomalies
and faults, and periodically stages a "shift change" burst where many vehicles enter the
same charging bay in the same tick (the concurrent-same-zone scenario from the spec).

Runs on Python 3.9+ (kept free of 3.10+ syntax) so it works with the system Python too.
    pip install -r simulator/requirements.txt
    API_BASE=http://localhost:8000 python simulator/simulate.py
"""
from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timezone

import httpx

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")
HZ = float(os.environ.get("SIM_HZ", "1.0"))
CHARGING_ZONES = ["charging_bay_1", "charging_bay_2", "charging_bay_3"]


class VehicleSim:
    __slots__ = ("vid", "status", "battery", "lat", "lon", "speed")

    def __init__(self, vid: str):
        self.vid = vid
        self.status = "idle"
        self.battery = random.uniform(40, 100)
        self.lat = 37.41 + random.uniform(-0.01, 0.01)
        self.lon = -122.08 + random.uniform(-0.01, 0.01)
        self.speed = 0.0


def build_event(v: VehicleSim, zones, tick: int) -> dict:
    # status random walk
    roll = random.random()
    if roll < 0.05:
        v.status = "charging"
    elif roll < 0.55:
        v.status = "moving"
    elif roll < 0.62:
        v.status = "idle"

    # battery dynamics
    if v.status == "charging":
        v.battery = min(100.0, v.battery + random.uniform(1.5, 3.0))
    elif v.status == "moving":
        v.battery = max(0.0, v.battery - random.uniform(0.1, 0.5))
    else:
        v.battery = max(0.0, v.battery - random.uniform(0.0, 0.05))

    v.speed = random.uniform(0.5, 3.0) if v.status == "moving" else 0.0
    v.lat += random.uniform(-0.0005, 0.0005)
    v.lon += random.uniform(-0.0005, 0.0005)

    error_codes: list = []
    zone_entered = None

    # occasional zone crossing
    if random.random() < 0.1:
        zone_entered = random.choice(zones)

    # shift-change burst: converge many vehicles on one charging bay in the same tick
    if tick % 30 == 0 and random.random() < 0.5:
        zone_entered = CHARGING_ZONES[(tick // 30) % len(CHARGING_ZONES)]
        v.status = "charging"

    # rare injected anomalies
    r = random.random()
    if r < 0.01:
        v.battery = random.uniform(0, 14)  # low battery
    elif r < 0.02:
        v.status = "moving"
        v.speed = random.uniform(8.1, 12.0)  # overspeed
    elif r < 0.025:
        error_codes = [random.choice(["E_MOTOR", "E_SENSOR", "E_NAV"])]

    return {
        "vehicle_id": v.vid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "lat": round(v.lat, 5),
        "lon": round(v.lon, 5),
        "battery_pct": round(v.battery, 1),
        "speed_mps": round(v.speed, 2),
        "status": v.status,
        "error_codes": error_codes,
        "zone_entered": zone_entered,
    }


async def discover(client: httpx.AsyncClient):
    for _ in range(30):
        try:
            vehicles = [x["vehicle_id"] for x in (await client.get("/vehicles")).json()]
            zones = [z["zone_id"] for z in (await client.get("/zones/counts")).json()]
            if vehicles and zones:
                return vehicles, zones
        except Exception as exc:  # backend not up yet
            print(f"waiting for backend at {API_BASE} ... ({exc})")
        await asyncio.sleep(1)
    raise SystemExit(f"backend not reachable at {API_BASE}")


async def main():
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        vehicles, zones = await discover(client)
        state = {vid: VehicleSim(vid) for vid in vehicles}
        print(f"simulating {len(vehicles)} vehicles at {HZ} Hz against {API_BASE} (Ctrl-C to stop)")

        loop = asyncio.get_event_loop()
        period = 1.0 / HZ
        tick = 0
        while True:
            start = loop.time()

            async def send(payload):
                try:
                    await client.post("/telemetry", json=payload)
                except Exception:
                    pass

            tasks = [send(build_event(state[v], zones, tick)) for v in vehicles]

            # rarely, fault a random vehicle through the dedicated status endpoint
            if random.random() < 0.05:
                victim = random.choice(vehicles)

                async def do_fault(vid=victim):
                    try:
                        await client.post(f"/vehicles/{vid}/status", json={"status": "fault"})
                    except Exception:
                        pass

                tasks.append(do_fault())

            await asyncio.gather(*tasks)
            tick += 1
            await asyncio.sleep(max(0.0, period - (loop.time() - start)))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped")
