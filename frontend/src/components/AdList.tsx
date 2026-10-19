import { useEffect, useState } from 'react'
import { type AdSummary, listAds, type OccupationGroup } from '../api'
import { EMPLOYMENT_LABELS, label, SENIORITY_LABELS } from '../labels'
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
  const [ads, setAds] = useState<AdSummary[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  // Re-fetch whenever a filter (or refreshKey) changes.
  useEffect(() => {
    // `ignore` handles replies arriving out of order: when the filters change
    // again before this request answers, React runs the cleanup below first,
    // so this (now outdated) reply is dropped instead of overwriting the
    // newer one. The old page needed a hand-made request counter for this.
    let ignore = false
    listAds({ seniority: split(seniority), employment_type: employment, occupation_group: group })
      .then((data) => {
        if (!ignore) {
          setAds(data)
          setError(null)
        }
      })
      .catch((e) => !ignore && setError(e))
    return () => {
      ignore = true
    }
  }, [seniority, employment, group, refreshKey])

  return (
    <section className="card">
      <div className="row space-between">
        <h2>Ads</h2>
        <span className="muted">{ads ? `${ads.length} ads` : 'Loading…'}</span>
      </div>

      <div className="row filters">
        <select value={seniority} onChange={(e) => setSeniority(e.target.value)}>
          <option value="">All levels</option>
          <option value="junior,intern">Junior + intern</option>
          <option value="junior">Junior</option>
          <option value="mid,senior">Mid + senior</option>
        </select>
        <select value={employment} onChange={(e) => setEmployment(e.target.value)}>
          <option value="">All employment types</option>
          {['thesis', 'internship', 'permanent', 'fixed_term'].map((code) => (
            <option key={code} value={code}>
              {label(EMPLOYMENT_LABELS, code)}
            </option>
          ))}
        </select>
        <select value={group} onChange={(e) => setGroup(e.target.value)}>
          <option value="">All job roles</option>
          {groups.map((g) => (
            <option key={g.code} value={g.code}>
              {g.label} ({g.ads})
            </option>
          ))}
        </select>
      </div>

      <ErrorBox error={error} />

      <ul className="job-list">
        {ads?.map((ad) => (
          <li key={ad.id}>
            <button className={ad.id === selectedId ? 'job-row selected' : 'job-row'} onClick={() => onSelect(ad.id)}>
              <span className="job-title">{ad.title ?? `Ad #${ad.id}`}</span>
              <span className="job-company">{ad.company ?? '—'}</span>
              <span className="tags">
                {ad.status !== 'completed' && <span className={`tag status-${ad.status}`}>{ad.status}</span>}
                {ad.seniority && ad.seniority !== 'unspecified' && (
                  <span className="tag">{label(SENIORITY_LABELS, ad.seniority)}</span>
                )}
                {ad.employment_type && ad.employment_type !== 'unspecified' && (
                  <span className={`tag employment-${ad.employment_type}`}>
                    {label(EMPLOYMENT_LABELS, ad.employment_type)}
                  </span>
                )}
              </span>
            </button>
          </li>
        ))}
        {ads?.length === 0 && <li className="muted">No ads match these filters.</li>}
      </ul>
    </section>
  )
}
