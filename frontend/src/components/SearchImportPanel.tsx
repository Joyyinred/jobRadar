import { useEffect, useState } from 'react'
import { getImport, type ImportJob, startSearchImport } from '../api'
import { ErrorBox } from './ErrorBox'

type Props = {
  /** Called whenever new ads may have arrived, so the list can refresh. */
  onProgress: () => void
  /** Go to the Ads page (offered once the import is done). */
  onShowAds: () => void
}

const POLL_MS = 2000

/** Import many ads from a Platsbanken search. The import runs in the
 *  background (an ImportJob); this panel polls its progress. */
export function SearchImportPanel({ onProgress, onShowAds }: Props) {
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(20)
  const [dataItOnly, setDataItOnly] = useState(true)
  const [job, setJob] = useState<ImportJob | null>(null)
  const [error, setError] = useState<unknown>(null)

  const running = job !== null && (job.status === 'pending' || job.status === 'running')
  const jobId = job?.id

  // While a job is running, ask for its progress every 2 seconds. The cleanup
  // function stops the timer when the job finishes or the panel goes away —
  // React does the "stop polling" bookkeeping the old page did by hand.
  useEffect(() => {
    if (jobId === undefined || !running) return
    const timer = setInterval(async () => {
      try {
        const latest = await getImport(jobId)
        setJob(latest)
        if (latest.created > 0) onProgress()
      } catch {
        // a blip: try again next round
      }
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [jobId, running, onProgress])

  async function start(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      setJob(await startSearchImport({ query, limit, data_it_only: dataItOnly }))
    } catch (e) {
      setError(e)
    }
  }

  return (
    <section className="card">
      <h2>Import from a Platsbanken search</h2>
      <form onSubmit={start} className="row">
        <input
          className="grow"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={dataItOnly ? 'Search words (empty = newest Data/IT ads)' : 'Search words'}
        />
        <input
          className="narrow"
          type="number"
          min={1}
          max={100}
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
          title="How many ads (1–100)"
        />
        <label className="checkbox">
          <input type="checkbox" checked={dataItOnly} onChange={(e) => setDataItOnly(e.target.checked)} />
          Data/IT only
        </label>
        <button className="button" disabled={running}>
          {running ? 'Importing…' : 'Import'}
        </button>
      </form>

      <ErrorBox error={error} />

      {job && (
        <div className="progress-block">
          <div className="progress">
            <div className="progress-bar" style={{ width: `${Math.min(100, (job.fetched / job.params.limit) * 100)}%` }} />
          </div>
          <p className="muted">
            {job.status === 'failed' ? `Import #${job.id} failed: ${job.error} ` : `Import #${job.id} ${job.status}. `}
            Fetched {job.fetched}: <strong>{job.created} new</strong> (parsing in the background), {job.duplicates}{' '}
            already here, {job.skipped} skipped.
          </p>
          {!running && (
            <button className="button" onClick={onShowAds}>
              See the ads
            </button>
          )}
        </div>
      )}
    </section>
  )
}
