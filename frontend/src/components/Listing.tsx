import type { Listing } from '../api'

const DAY_MS = 24 * 60 * 60 * 1000
const SOON_DAYS = 7

const formatDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })

/** How to apply, and by when. */
export function ApplyLinks({ listing, now }: { listing: Listing; now: number }) {
  const { ad_url, apply_url, apply_email } = listing
  if (!ad_url && !apply_url && !apply_email && !listing.apply_by && !listing.removed_at) return null

  return (
    <div className="listing-links">
      <div className="row">
        {/* target=_blank opens a new tab; rel=noopener stops that page from
            reaching back into ours through window.opener. The links themselves
            are checked to be http(s) on the backend, where the data comes in. */}
        {apply_url && (
          <a className="button button-link" href={apply_url} target="_blank" rel="noopener noreferrer">
            Apply ↗
          </a>
        )}
        {!apply_url && apply_email && (
          <a className="button button-link" href={`mailto:${apply_email}`}>
            Apply by email: {apply_email}
          </a>
        )}
        {ad_url && (
          <a className="button button-quiet button-link" href={ad_url} target="_blank" rel="noopener noreferrer">
            View original ad ↗
          </a>
        )}
      </div>
      <Deadline listing={listing} now={now} />
    </div>
  )
}

function Deadline({ listing, now }: { listing: Listing; now: number }) {
  if (listing.removed_at) {
    // removed_at is when WE noticed (backfill_jobtech got a 404), not when it came down.
    return <p className="deadline deadline-closed">No longer on Platsbanken (noticed {formatDate(listing.removed_at)})</p>
  }
  if (!listing.apply_by) return null

  // Deadlines are 23:59:59 on the last day, so whole days left = floor:
  // on the 19th in the afternoon, a deadline on the 23rd is 4.4 days away —
  // "4 days left" — and on the last day itself it's 0: "last day".
  const daysLeft = Math.floor((new Date(listing.apply_by).getTime() - now) / DAY_MS)
  if (daysLeft < 0) {
    return <p className="deadline deadline-closed">Deadline passed ({formatDate(listing.apply_by)})</p>
  }
  const left = daysLeft === 0 ? 'last day' : daysLeft === 1 ? '1 day left' : `${daysLeft} days left`
  return (
    <p className={daysLeft <= SOON_DAYS ? 'deadline deadline-soon' : 'deadline muted'}>
      Apply by {formatDate(listing.apply_by)} · {left}
    </p>
  )
}

/** The whole ad as it was posted — to check what the LLM extracted, and for
 *  everything it doesn't (duties, benefits, contacts). Folded by default:
 *  ads run to thousands of characters. <details> needs no state at all. */
export function FullAd({ text }: { text: string }) {
  return (
    <details className="posting">
      <summary>Show the full ad</summary>
      {/* Plain text in a React text node: React escapes it, so whatever the ad
          contains is shown, never run. (No dangerouslySetInnerHTML.) */}
      <div className="posting-text">{text}</div>
    </details>
  )
}
