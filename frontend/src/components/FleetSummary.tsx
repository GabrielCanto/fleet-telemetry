import { useFleetState } from '../hooks/useApi'
import type { FleetState } from '../types'

const CELLS: ReadonlyArray<readonly [keyof FleetState, string]> = [
  ['moving', 'Moving'],
  ['idle', 'Idle'],
  ['charging', 'Charging'],
  ['fault', 'Fault'],
]

export function FleetSummary() {
  const { data, isError } = useFleetState()
  const total = data ? data.idle + data.moving + data.charging + data.fault : 0

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Fleet state</h2>
        <span className="muted">{total} vehicles</span>
      </div>
      <div className="stat-grid">
        {CELLS.map(([key, label]) => (
          <div key={key} className={`stat stat-${key}`}>
            <div className="stat-value">{data ? data[key] : '–'}</div>
            <div className="stat-label">{label}</div>
          </div>
        ))}
      </div>
      {isError && <p className="error">Failed to load fleet state.</p>}
    </section>
  )
}
