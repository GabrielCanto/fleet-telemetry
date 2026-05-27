export type VehicleStatus = 'idle' | 'moving' | 'charging' | 'fault'
export type Severity = 'warning' | 'critical'

export interface FleetState {
  idle: number
  moving: number
  charging: number
  fault: number
}

export interface ZoneCount {
  zone_id: string
  name: string
  entry_count: number
}

export interface VehicleAnomaly {
  anomaly_type: string
  message: string
  severity: Severity
  event_ts: string
}

export interface Vehicle {
  vehicle_id: string
  status: VehicleStatus
  battery_pct: number
  speed_mps: number
  lat: number | null
  lon: number | null
  current_zone: string | null
  last_seen_at: string | null
  last_anomaly: VehicleAnomaly | null
}
