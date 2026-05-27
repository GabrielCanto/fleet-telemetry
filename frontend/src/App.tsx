import { useFleetState } from './hooks/useApi'
import { FleetSummary } from './components/FleetSummary'
import { VehicleList } from './components/VehicleList'
import { ZoneCounts } from './components/ZoneCounts'

export default function App() {
  // Reuse the (deduplicated) fleet query just to surface the live cadence in the header.
  const { dataUpdatedAt, isFetching, isError } = useFleetState()
  const updated = dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString() : '—'

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>Fleet Telemetry</h1>
          <p className="muted">50 autonomous vehicles · live monitoring</p>
        </div>
        <div className="live">
          <span className={`dot ${isError ? 'dot-err' : isFetching ? 'dot-on' : ''}`} />
          {isError ? 'backend unreachable' : `polling every 2s · updated ${updated}`}
        </div>
      </header>

      <FleetSummary />

      <div className="columns">
        <VehicleList />
        <ZoneCounts />
      </div>

      <footer className="foot muted">
        REST polling via TanStack Query (2s). Backend: FastAPI + PostgreSQL.
      </footer>
    </div>
  )
}
