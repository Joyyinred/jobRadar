import { useEffect, useState } from 'react'
import { type AdDetail, getAd, retryAd } from '../api'
import { EMPLOYMENT_LABELS, label, SENIORITY_LABELS, SWEDISH_LABELS } from '../labels'
import { ErrorBox } from './ErrorBox'
import { ApplyLinks, FullAd } from './Listing'

type Props = {
  adId: number
  /** Called when the ad changes state (finished parsing, retried), so the list can refresh. */
  onChanged: () => void
}

const POLL_MS = 3000
const isFinal = (ad: AdDetail | null) => ad?.status === 'completed' || ad?.status === 'failed'

/** One ad. While it's still pending/processing, keeps asking until it's done
 *  (Day 6 polling) — and stops by itself when you select another ad. */
export function AdDetailPanel({ adId, onChanged }: Props) {
  const [ad, setAd] = useState<AdDetail | null>(null)
  // When the ad was loaded: "days left" counts from here. Reading the clock
  // during render would make rendering impure (a different result each time).
  const [loadedAt, setLoadedAt] = useState(0)
  const [error, setError] = useState<unknown>(null)
  const [retrying, setRetrying] = useState(false)
  // Bumping this re-runs the loading/polling effect below (used after Retry).
  const [reloadKey, setReloadKey] = useState(0)

  // Load the ad; if it isn't finished, poll. The cleanup runs when adId
  // changes (another ad selected), on Retry, or when the panel closes: it
  // stops the timer and ignores replies still on their way — no stale ad can
  // overwrite a newer one.
  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let wasFinal: boolean | null = null

    async function load() {
      try {
        const latest = await getAd(adId)
        if (stopped) return
        setAd(latest)
        setLoadedAt(Date.now())
        setError(null)
        const final = isFinal(latest)
        if (wasFinal === false && final) onChanged() // it just finished: refresh the list
        wasFinal = final
        if (!final) timer = setTimeout(load, POLL_MS)
      } catch (e) {
        if (!stopped) setError(e)
      }
    }

    load()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
    // onChanged is App's refreshList, kept stable by useCallback — so listing
    // it here doesn't restart the polling on every App render.
  }, [adId, reloadKey, onChanged])

  async function retry() {
    setRetrying(true)
    try {
      await retryAd(adId)
      onChanged()
      setReloadKey((k) => k + 1) // the ad is pending again: start polling it again
    } catch (e) {
      setError(e)
    } finally {
      setRetrying(false)
    }
  }

  if (error) return <section className="card"><ErrorBox error={error} /></section>
  if (!ad) return <section className="card muted">Loading ad #{adId}…</section>

  return (
    <section className="card">
      <p className="muted">Ad #{ad.id}</p>

      {!isFinal(ad) && (
        <p className="status-line">
          <span className="spinner" /> {ad.status === 'pending' ? 'Waiting in the queue…' : 'Parsing…'} (checks every{' '}
          {POLL_MS / 1000}s)
        </p>
      )}

      {ad.status === 'failed' && (
        <div className="alert alert-error">
          {ad.error}{' '}
          <button className="button button-small" onClick={retry} disabled={retrying}>
            {retrying ? 'Retrying…' : 'Retry'}
          </button>
        </div>
      )}

      {ad.result && (
        <>
          <h2>{ad.result.title_en || ad.result.title || 'Untitled role'}</h2>
          {ad.result.title && ad.result.title !== ad.result.title_en && (
            <p className="original-title">Original: {ad.result.title}</p>
          )}
          <p className="company-line">
            {ad.result.company ?? 'Company not stated'}
            {ad.result.city && ` · ${ad.result.city}`}
          </p>
          <div className="tags">
            <span className="tag">{label(SENIORITY_LABELS, ad.result.seniority)}</span>
            <span className={`tag employment-${ad.result.employment_type}`}>
              {label(EMPLOYMENT_LABELS, ad.result.employment_type)}
            </span>
            <span className="tag">{label(SWEDISH_LABELS, ad.result.swedish_requirement)}</span>
            {ad.result.occupation_group && <span className="tag tag-muted">{ad.result.occupation_group}</span>}
          </div>

          {ad.listing && <ApplyLinks listing={ad.listing} now={loadedAt} />}

          <h3>Required</h3>
          <SkillChips skills={ad.result.required_skills} kind="required" />
          <h3>Nice to have</h3>
          <SkillChips skills={ad.result.nice_to_have_skills} kind="nice" />
        </>
      )}

      {/* Not parsed yet (or failed): the links and the text are still worth having. */}
      {!ad.result && ad.listing && <ApplyLinks listing={ad.listing} now={loadedAt} />}
      {ad.listing && <FullAd text={ad.listing.text} />}
    </section>
  )
}

function SkillChips({ skills, kind }: { skills: string[]; kind: 'required' | 'nice' }) {
  if (skills.length === 0) return <p className="muted">None listed.</p>
  return (
    <div className="chips">
      {skills.map((skill) => (
        <span key={skill} className={`chip chip-${kind}`}>
          {skill}
        </span>
      ))}
    </div>
  )
}
