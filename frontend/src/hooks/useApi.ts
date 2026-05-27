import { useQuery } from '@tanstack/react-query'

import { fetchFleetState, fetchVehicles, fetchZoneCounts } from '../api'

// Single knob for the live cadence. Polling (not websockets) is intentional at this
// scale — see the ADR.
const POLL_MS = 2000

export function useFleetState() {
  return useQuery({ queryKey: ['fleetState'], queryFn: fetchFleetState, refetchInterval: POLL_MS })
}

export function useVehicles() {
  return useQuery({ queryKey: ['vehicles'], queryFn: fetchVehicles, refetchInterval: POLL_MS })
}

export function useZoneCounts() {
  return useQuery({ queryKey: ['zoneCounts'], queryFn: fetchZoneCounts, refetchInterval: POLL_MS })
}
