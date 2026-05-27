import { useVehicles } from '../hooks/useApi'
import type { Vehicle } from '../types'

function Battery({ pct }: { pct: number }) {
  const level = pct < 20 ? 'low' : pct < 50 ? 'mid' : 'high'
  const width = Math.max(0, Math.min(100, pct))
  return (
    <div className="battery">
      <span className="battery-track">
        <span className={`battery-fill battery-${level}`} style={{ width: `${width}%` }} />
      </span>
      <span className="battery-pct mono">{pct.toFixed(0)}%</span>
    </div>
  )
}

export function VehicleList() {
  const { data, isLoading, isError } = useVehicles()

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Vehicles</h2>
        <span className="muted">{data?.length ?? 0}</span>
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {isError && <p className="error">Failed to load vehicles.</p>}

      {data && (
        <div className="table-wrap">
          <table className="vehicles">
            <thead>
              <tr>
                <th>ID</th>
                <th>Status</th>
                <th>Battery</th>
                <th>Speed</th>
                <th>Zone</th>
                <th>Latest anomaly</th>
              </tr>
            </thead>
            <tbody>
              {data.map((v: Vehicle) => (
                <tr key={v.vehicle_id}>
                  <td className="mono">{v.vehicle_id}</td>
                  <td>
                    <span className={`badge badge-${v.status}`}>{v.status}</span>
                  </td>
                  <td>
                    <Battery pct={v.battery_pct} />
                  </td>
                  <td className="mono">{v.speed_mps.toFixed(1)}</td>
                  <td className="muted">{v.current_zone ?? '—'}</td>
                  <td>
                    {v.last_anomaly ? (
                      <span
                        className={`anomaly sev-${v.last_anomaly.severity}`}
                        title={v.last_anomaly.message}
                      >
                        {v.last_anomaly.anomaly_type}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
