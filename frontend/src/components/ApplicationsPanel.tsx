import { useEffect, useState } from 'react'
import { type Application, type ApplicationStatus, type ApplicationSummary, getApplications, trackAd } from '../api'
import { label, STATUS_LABELS } from '../labels'
import { ErrorBox } from './ErrorBox'

type Props = {
  /** Open this ad on the Ads page. */
  onOpenAd: (adId: number) => void
  /** Bumped when tracking changed elsewhere (the ad panel), to reload. */
  refreshKey: number
  onChanged: () => void
}

// Still going vs. finished: the finished ones matter less day to day.
const ACTIVE: ApplicationStatus[] = ['offer', 'interview', 'applied', 'saved']
const FINISHED: ApplicationStatus[] = ['rejected', 'withdrawn']

const formatDay = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })

/** Every ad you're tracking, grouped by status, with how companies respond. */
export function ApplicationsPanel({ onOpenAd, refreshKey, onChanged }: Props) {
  const [data, setData] = useState<{ results: Application[]; summary: ApplicationSummary } | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let ignore = false
    getApplications()
      .then((loaded) => !ignore && setData(loaded))
      .catch((e) => !ignore && setError(e))
    return () => {
      ignore = true
    }
  }, [refreshKey])

  async function changeStatus(adId: number, status: ApplicationStatus) {
    try {
      await trackAd(adId, { status })
      onChanged() // bumps refreshKey: this list reloads, the ad list's tags too
    } catch (e) {
      setError(e)
    }
  }

  if (!data) return <section className="card">{error ? <ErrorBox error={error} /> : <p className="muted">Loading…</p>}</section>

  const { results, summary } = data
  if (results.length === 0) {
    return (
      <section className="card muted">
        You're not tracking any ads yet. Open an ad and set "My application" to Saved or Applied.
      </section>
    )
  }

  const group = (statuses: ApplicationStatus[]) =>
    statuses.flatMap((status) => results.filter((app) => app.status === status))

  return (
    <section className="card">
      <h2>My applications</h2>

      <div className="stats">
        {ACTIVE.map((status) => (
          <div className="stat" key={status}>
            <span className="stat-value">{summary.counts[status]}</span>
            <span className="stat-label">{label(STATUS_LABELS, status)}</span>
          </div>
        ))}
      </div>
      <p className="muted">
        {summary.applied === 0
          ? 'No applications sent yet.'
          : `Heard back from ${summary.heard_back} of ${summary.applied} applications` +
            (summary.days_to_answer === null
              ? '.'
              : summary.days_to_answer < 1
                ? ' — usually within a day.'
                : ` — usually within ${summary.days_to_answer} days.`)}
      </p>

      <ErrorBox error={error} />

      <ApplicationTable apps={group(ACTIVE)} onOpenAd={onOpenAd} onStatus={changeStatus} />
      {group(FINISHED).length > 0 && (
        <details className="finished">
          <summary>Finished ({group(FINISHED).length})</summary>
          <ApplicationTable apps={group(FINISHED)} onOpenAd={onOpenAd} onStatus={changeStatus} />
        </details>
      )}
    </section>
  )
}

function ApplicationTable({
  apps,
  onOpenAd,
  onStatus,
}: {
  apps: Application[]
  onOpenAd: (adId: number) => void
  onStatus: (adId: number, status: ApplicationStatus) => void
}) {
  return (
    <table className="skill-table applications">
      <thead>
        <tr>
          <th>Job</th>
          <th>Status</th>
          <th>Applied</th>
        </tr>
      </thead>
      <tbody>
        {apps.map((app) => (
          <tr key={app.ad.id}>
            <td>
              <button className="link-button" onClick={() => onOpenAd(app.ad.id)}>
                {app.ad.title ?? `Ad #${app.ad.id}`}
              </button>
              <div className="muted small">
                {app.ad.company ?? '—'}
                {app.ad.closed && app.status === 'saved' && ' · closed: too late to apply'}
              </div>
            </td>
            <td>
              <select value={app.status} onChange={(e) => onStatus(app.ad.id, e.target.value as ApplicationStatus)}>
                {Object.entries(STATUS_LABELS).map(([code, text]) => (
                  <option key={code} value={code}>
                    {text}
                  </option>
                ))}
              </select>
            </td>
            <td className="muted">{app.applied_at ? formatDay(app.applied_at) : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
