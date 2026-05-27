import { useZoneCounts } from '../hooks/useApi'

export function ZoneCounts() {
  const { data, isLoading, isError } = useZoneCounts()
  const max = data ? Math.max(1, ...data.map((z) => z.entry_count)) : 1

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Zone entries</h2>
        <span className="muted">{data?.length ?? 0} zones</span>
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {isError && <p className="error">Failed to load zones.</p>}

      {data && (
        <ul className="zones">
          {data.map((z) => (
            <li key={z.zone_id} className="zone">
              <span className="zone-name">{z.name}</span>
              <span className="zone-bar">
                <span
                  className="zone-bar-fill"
                  style={{ width: `${(z.entry_count / max) * 100}%` }}
                />
              </span>
              <span className="zone-count mono">{z.entry_count}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
