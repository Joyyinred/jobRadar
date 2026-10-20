import { useEffect, useState } from 'react'
import { type Application, type ApplicationStatus, getAdApplication, trackAd, untrackAd } from '../api'
import { label, STATUS_LABELS } from '../labels'
import { ErrorBox } from './ErrorBox'

type Props = {
  adId: number
  /** Called after a change, so the ad list's status tags can refresh. */
  onChanged: () => void
}

const formatDay = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })

/** Your application for this ad: status, notes and history. Only shown when
 *  logged in (the API answers 401 otherwise). */
export function TrackBox({ adId, onChanged }: Props) {
  // undefined = loading, null = not tracking
  const [app, setApp] = useState<Application | null | undefined>(undefined)
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let ignore = false
    getAdApplication(adId)
      .then((loaded) => {
        if (ignore) return
        setApp(loaded)
        setNotes(loaded?.notes ?? '')
      })
      .catch((e) => !ignore && setError(e))
    return () => {
      ignore = true
    }
  }, [adId])

  async function save(change: () => Promise<Application | null>) {
    setBusy(true)
    setError(null)
    try {
      const updated = await change()
      setApp(updated)
      setNotes(updated?.notes ?? '')
      onChanged()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  function changeStatus(value: string) {
    if (value === '') {
      // "Not tracking": deletes the application and its history — ask first.
      if (app && !window.confirm('Stop tracking this ad? Its status history and notes are deleted.')) return
      save(async () => {
        await untrackAd(adId)
        return null
      })
    } else {
      save(() => trackAd(adId, { status: value as ApplicationStatus }))
    }
  }

  if (app === undefined) return error ? <ErrorBox error={error} /> : null

  const notesChanged = notes !== (app?.notes ?? '')

  return (
    <div className="track-box">
      <div className="row">
        <strong>My application</strong>
        <select value={app?.status ?? ''} onChange={(e) => changeStatus(e.target.value)} disabled={busy}>
          <option value="">Not tracking</option>
          {Object.entries(STATUS_LABELS).map(([code, text]) => (
            <option key={code} value={code}>
              {text}
            </option>
          ))}
        </select>
      </div>

      {app && (
        <>
          <p className="history muted">
            {app.events.map((e) => `${label(STATUS_LABELS, e.status)} ${formatDay(e.at)}`).join(' → ')}
          </p>
          <textarea
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Notes: who you talked to, what they asked for…"
            maxLength={5000}
          />
          {notesChanged && (
            <button className="button button-small" disabled={busy} onClick={() => save(() => trackAd(adId, { notes }))}>
              Save notes
            </button>
          )}
        </>
      )}
      <ErrorBox error={error} />
    </div>
  )
}
