import { useEffect, useRef, useState } from 'react'
import { type AdPage, listAds, type OccupationGroup } from '../api'
import { DEGREE_LABELS, EMPLOYMENT_LABELS, label, ORIGIN_LABELS, SENIORITY_LABELS } from '../labels'
import { ErrorBox } from './ErrorBox'

type Props = {
  groups: OccupationGroup[]
  selectedId: number | null
  onSelect: (adId: number) => void
  /** Bumped by the parent when ads were added, to re-fetch the list. */
  refreshKey: number
}

/** "junior,intern" in a <select> value -> ["junior", "intern"] for the API. */
const split = (value: string) => value.split(',').filter(Boolean)

export function AdList({ groups, selectedId, onSelect, refreshKey }: Props) {
  const [seniority, setSeniority] = useState('')
  const [employment, setEmployment] = useState('')
  const [group, setGroup] = useState('')
  const [origin, setOrigin] = useState('')
  const [degree, setDegree] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<AdPage | null>(null)
  const [error, setError] = useState<unknown>(null)
  const listRef = useRef<HTMLUListElement>(null)

  // Re-fetch whenever a filter, the page (or refreshKey) changes.
  useEffect(() => {
    // `ignore` handles replies arriving out of order: when the filters change
    // again before this request answers, React runs the cleanup below first,
    // so this (now outdated) reply is dropped instead of overwriting the
    // newer one. The old page needed a hand-made request counter for this.
    let ignore = false
    listAds({ seniority: split(seniority), employment_type: employment, occupation_group: group, origin, degree_level: degree, page })
      .then((latest) => {
        if (!ignore) {
          setData(latest)
          setError(null)
        }
      })
      .catch((e) => !ignore && setError(e))
    return () => {
      ignore = true
    }
  }, [seniority, employment, group, origin, degree, page, refreshKey])

  /** A new filter starts again at page 1 — page 8 of the old filter may not
   *  exist under the new one. Done here, in the event, not in an effect:
   *  one change, one render, one request. */
  const filter = (set: (value: string) => void) => (event: React.ChangeEvent<HTMLSelectElement>) => {
    set(event.target.value)
    setPage(1)
  }

  function goTo(next: number) {
    setPage(next)
    listRef.current?.scrollTo({ top: 0 }) // a new page starts at its top
  }

  const ads = data?.results

  return (
    <section className="card">
      <div className="row space-between">
        <h2>Ads</h2>
        <span className="muted">{data ? `${data.count} ads` : 'Loading…'}</span>
      </div>

      <div className="row filters">
        <select value={origin} onChange={filter(setOrigin)}>
          <option value="">Added by me + imported</option>
          <option value="user">{ORIGIN_LABELS.user}</option>
          <option value="import">{ORIGIN_LABELS.import}</option>
        </select>
        <select value={seniority} onChange={filter(setSeniority)}>
          <option value="">All levels</option>
          <option value="junior,intern">Junior + intern</option>
          <option value="junior">Junior</option>
          <option value="mid,senior">Mid + senior</option>
        </select>
        <select value={employment} onChange={filter(setEmployment)}>
          <option value="">All employment types</option>
          {['thesis', 'internship', 'permanent', 'fixed_term'].map((code) => (
            <option key={code} value={code}>
              {label(EMPLOYMENT_LABELS, code)}
            </option>
          ))}
        </select>
        {/* "Master's level" also lists ads open to either level (the API does that). */}
        <select value={degree} onChange={filter(setDegree)}>
          <option value="">All degree levels</option>
          <option value="master">Master's level</option>
          <option value="bachelor">Bachelor's level</option>
        </select>
        <select value={group} onChange={filter(setGroup)}>
          <option value="">All job roles</option>
          {groups.map((g) => (
            <option key={g.code} value={g.code}>
              {g.label} ({g.ads})
            </option>
          ))}
        </select>
      </div>

      <ErrorBox error={error} />

      <ul className="job-list" ref={listRef}>
        {ads?.map((ad) => (
          <li key={ad.id}>
            <button className={ad.id === selectedId ? 'job-row selected' : 'job-row'} onClick={() => onSelect(ad.id)}>
              {/* title (Swedish) on hover: it's what you'd search for on Platsbanken. */}
              <span className="job-title" title={ad.title ?? undefined}>
                {ad.title_en ?? ad.title ?? `Ad #${ad.id}`}
              </span>
              <span className="job-company">{ad.company ?? '—'}</span>
              <span className="tags">
                {/* Only yours get a tag: most ads are imported, so tagging
                    those too would just be noise on every row. */}
                {ad.origin === 'user' && <span className="tag tag-mine">{ORIGIN_LABELS.user}</span>}
                {ad.closed && <span className="tag tag-closed">Closed</span>}
                {ad.status !== 'completed' && <span className={`tag status-${ad.status}`}>{ad.status}</span>}
                {ad.seniority && ad.seniority !== 'unspecified' && (
                  <span className="tag">{label(SENIORITY_LABELS, ad.seniority)}</span>
                )}
                {ad.employment_type && ad.employment_type !== 'unspecified' && (
                  <span className={`tag employment-${ad.employment_type}`}>
                    {label(EMPLOYMENT_LABELS, ad.employment_type)}
                  </span>
                )}
                {ad.degree_level && ad.degree_level !== 'unspecified' && (
                  <span className="tag tag-degree">{label(DEGREE_LABELS, ad.degree_level)}</span>
                )}
              </span>
            </button>
          </li>
        ))}
        {ads?.length === 0 && <li className="muted">No ads match these filters.</li>}
      </ul>

      {data && data.pages > 1 && (
        <div className="pager">
          <button className="button button-quiet" disabled={page <= 1} onClick={() => goTo(page - 1)}>
            ← Prev
          </button>
          <span className="muted">
            Page {data.page} of {data.pages}
          </span>
          <button className="button button-quiet" disabled={page >= data.pages} onClick={() => goTo(page + 1)}>
            Next →
          </button>
        </div>
      )}
    </section>
  )
}
