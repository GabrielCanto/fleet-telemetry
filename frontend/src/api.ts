import type { FleetState, Vehicle, ZoneCount } from './types'

const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

export const fetchFleetState = () => getJSON<FleetState>('/fleet/state')
export const fetchVehicles = () => getJSON<Vehicle[]>('/vehicles')
export const fetchZoneCounts = () => getJSON<ZoneCount[]>('/zones/counts')
