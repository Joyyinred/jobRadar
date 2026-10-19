import { useEffect, useState } from 'react'
import { getMarketSkills, type MarketSkills, type OccupationGroup } from '../api'
import { percent } from '../labels'
import { ErrorBox } from './ErrorBox'

type Props = { groups: OccupationGroup[] }

/** What do the collected ads ask for? Filters re-run the query right away. */
export function MarketPanel({ groups }: Props) {
  const [seniority, setSeniority] = useState('junior,intern')
  const [group, setGroup] = useState('')
  const [level, setLevel] = useState('')
  const [data, setData] = useState<MarketSkills | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let ignore = false // same out-of-order protection as the ad list
    getMarketSkills({
      seniority: seniority.split(',').filter(Boolean),
      occupation_group: group,
      level,
      top: 20,
    })
      .then((result) => {
        if (!ignore) {
          setData(result)
          setError(null) // cleared when the new answer arrives, not before
        }
      })
      .catch((e) => {
        if (!ignore) {
          setData(null)
          setError(e) // e.g. 422 "only 18 ads match" — shown, not hidden
        }
      })
    return () => {
      ignore = true
    }
  }, [seniority, group, level])

  const top = data?.skills[0]?.share ?? 1

  return (
    <section className="card">
      <h2>What do the ads ask for?</h2>
      <div className="row filters">
        <select value={seniority} onChange={(e) => setSeniority(e.target.value)}>
          <option value="junior,intern">Junior + intern</option>
          <option value="">All levels</option>
          <option value="mid,senior">Mid + senior</option>
        </select>
        <select value={group} onChange={(e) => setGroup(e.target.value)}>
          <option value="">All job roles</option>
          {groups.map((g) => (
            <option key={g.code} value={g.code}>
              {g.label} ({g.ads})
            </option>
          ))}
        </select>
        <select value={level} onChange={(e) => setLevel(e.target.value)}>
          <option value="">Required or nice-to-have</option>
          <option value="required">Required only</option>
          <option value="nice_to_have">Nice-to-have only</option>
        </select>
      </div>

      <ErrorBox error={error} />

      {data && (
        <>
          <div className="stats">
            <Stat value={String(data.sample_size)} label="ads" />
            <Stat value={percent(data.swedish_requirement.required)} label="require Swedish" />
            <Stat value={percent(data.swedish_requirement.preferred)} label="Swedish a plus" />
          </div>

          <table className="skill-table">
            <thead>
              <tr>
                <th>Skill</th>
                <th className="bar-cell">Share of ads</th>
                <th className="num">Required</th>
                <th className="num">Nice to have</th>
              </tr>
            </thead>
            <tbody>
              {data.skills.map((skill) => (
                <tr key={skill.name}>
                  <td>{skill.name}</td>
                  <td className="bar-cell">
                    {/* Two segments: the required part and the nice-to-have part. */}
                    <div className="bar" style={{ width: `${(skill.share / top) * 100}%` }}>
                      <div
                        className="bar-required"
                        style={{ width: `${(skill.required / Math.max(skill.ads, 1)) * 100}%` }}
                      />
                    </div>
                    <span className="bar-label">
                      {percent(skill.share)} ({skill.ads})
                    </span>
                  </td>
                  <td className="num">{skill.required}</td>
                  <td className="num">{skill.nice_to_have}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">{data.note}</p>
        </>
      )}
    </section>
  )
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  )
}
