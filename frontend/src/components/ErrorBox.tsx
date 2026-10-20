import { ApiError } from '../api'

const TITLES: Record<string, string> = {
  validation: 'Please fix',
  not_found: 'Not found',
  block: 'Not allowed',
  warning: 'Warning',
  insufficient_data: 'Not enough data',
  upstream: 'Platsbanken problem',
  error: 'Something went wrong',
}

type Props = {
  error: unknown
  /** If given, a warning gets a "Continue anyway" button that calls it. */
  onConfirm?: () => void
}

/** Shows any error from the API. One component for all of them, because the
 *  backend always sends the same shape (Day 8): `type` decides the look. */
export function ErrorBox({ error, onConfirm }: Props) {
  if (!error) return null
  const apiError = error instanceof ApiError ? error : null
  const type = apiError?.type ?? 'error'
  // 401: not logged in. Say what to do, not DRF's "credentials were not provided".
  const message =
    apiError?.status === 401 ? 'Log in to do this.' : (apiError?.message ?? String(error))

  return (
    <div className={`alert alert-${type}`} role="alert">
      <strong>{TITLES[type] ?? TITLES.error}:</strong> {message}
      {type === 'warning' && onConfirm && (
        <button className="button button-small" onClick={onConfirm}>
          Continue anyway
        </button>
      )}
    </div>
  )
}
