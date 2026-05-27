"""Hardcoded fleet/zone constants and tunable anomaly thresholds.

Single source of truth — reused by the seed, the simulator (via the API), and the tests.
"""

# The 20 warehouse zones, defined at startup (from the spec).
ZONES: list[str] = [
    "inbound_dock_a",
    "inbound_dock_b",
    "receiving_staging",
    "aisle_a",
    "aisle_b",
    "aisle_c",
    "high_bay_1",
    "high_bay_2",
    "bulk_storage",
    "pick_zone_1",
    "pick_zone_2",
    "pack_station",
    "sort_belt",
    "outbound_dock_a",
    "outbound_dock_b",
    "shipping_staging",
    "charging_bay_1",
    "charging_bay_2",
    "charging_bay_3",
    "maintenance_bay",
]

# Fixed, provisioned fleet of 50 vehicles. Single source of truth for seed/tests.
VEHICLE_IDS: list[str] = [f"v-{i}" for i in range(1, 51)]

# --- Anomaly thresholds (tunable) ---
LOW_BATTERY_PCT = 15        # below this -> low_battery anomaly
CRITICAL_BATTERY_PCT = 5    # below this -> severity=critical
MAX_SPEED_MPS = 8           # above this -> overspeed (warehouse cap)
BATTERY_JUMP_PCT = 30       # |delta| above this between consecutive events -> sensor fault
BATTERY_RISE_PCT = 2        # rise above this while not charging -> impossible reading
