import { useState } from 'react'
import { type AdDetail, importPlatsbankenLink, submitAd } from '../api'
import { ErrorBox } from './ErrorBox'

type Mode = 'paste' | 'link'

type Props = {
  /** Called with the ad's id once the backend has it (new or duplicate). */
  onAdded: (adId: number) => void
}

/** Add one ad: paste its text, or give a Platsbanken link. Both answer the
 *  same way (202 new / 200 duplicate / 409 warning), so they share the logic. */
export function AddAdPanel({ onAdded }: Props) {
  const [mode, setMode] = useState<Mode>('paste')
  const [rawText, setRawText] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [link, setLink] = useState('')

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [note, setNote] = useState('')
  // What was sent when a warning came back. "Continue anyway" re-sends THIS,
  // not whatever is in the inputs by then (the Day 8 bug can't happen here).
  const [warned, setWarned] = useState<{ mode: Mode; body: Record<string, unknown> } | null>(null)

  async function send(sendMode: Mode, body: Record<string, unknown>) {
    setBusy(true)
    setError(null)
    setNote('')
    setWarned(null)
    try {
      const ad: AdDetail =
        sendMode === 'paste'
          ? await submitAd(body as Parameters<typeof submitAd>[0])
          : await importPlatsbankenLink(body as Parameters<typeof importPlatsbankenLink>[0])
      setNote(ad.duplicate ? `Already in JobRadar as ad #${ad.id}.` : `Added as ad #${ad.id} — parsing in the background.`)
      if (!ad.duplicate) {
        setRawText('')
        setSourceUrl('')
        setLink('')
      }
      onAdded(ad.id)
    } catch (e) {
      setError(e)
      setWarned({ mode: sendMode, body })
    } finally {
      setBusy(false) // always, even after an error
    }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault()
    if (mode === 'paste') send('paste', { raw_text: rawText, source_url: sourceUrl, confirm: false })
    else send('link', { url: link, confirm: false })
  }

  return (
    <section className="card">
      <div className="tabs">
        <button className={mode === 'paste' ? 'tab active' : 'tab'} onClick={() => setMode('paste')}>
          Paste an ad
        </button>
        <button className={mode === 'link' ? 'tab active' : 'tab'} onClick={() => setMode('link')}>
          Platsbanken link
        </button>
      </div>

      <form onSubmit={submit} className="stack">
        {mode === 'paste' ? (
          <>
            <textarea
              value={rawText}
              onChange={(e) => setRawText(e.target.value)}
              placeholder="Paste a job ad (Swedish or English), e.g. from LinkedIn or a company site…"
              rows={8}
            />
            <input
              type="url"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="Where you found it (optional)"
            />
          </>
        ) : (
          <input
            type="url"
            value={link}
            onChange={(e) => setLink(e.target.value)}
            placeholder="https://arbetsformedlingen.se/platsbanken/annonser/31575359"
          />
        )}
        <div>
          <button className="button" disabled={busy || (mode === 'paste' ? !rawText.trim() : !link.trim())}>
            {busy ? 'Sending…' : mode === 'paste' ? 'Parse ad' : 'Import ad'}
          </button>
        </div>
      </form>

      <ErrorBox
        error={error}
        onConfirm={warned ? () => send(warned.mode, { ...warned.body, confirm: true }) : undefined}
      />
      {note && <p className="muted">{note}</p>}
    </section>
  )
}
