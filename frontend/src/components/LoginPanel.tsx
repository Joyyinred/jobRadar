import { useState } from 'react'
import { logIn, type User } from '../api'
import { ErrorBox } from './ErrorBox'

type Props = {
  onLoggedIn: (user: User) => void
}

/** Username + password. The answer sets an HttpOnly session cookie, which the
 *  browser then sends with every request by itself — nothing to store here. */
export function LoginPanel({ onLoggedIn }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const user = await logIn(username, password)
      if (user) onLoggedIn(user)
    } catch (e) {
      setError(e)
      setPassword('') // a wrong password shouldn't sit in the field
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card login-card">
      <h2>Log in</h2>
      <p className="muted">Browsing ads and market stats is open to everyone. Adding ads and importing need an account.</p>
      <form onSubmit={submit} className="stack">
        {/* autoComplete lets password managers fill these in */}
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Username"
          autoComplete="username"
          required
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          autoComplete="current-password"
          required
        />
        <button className="button" disabled={busy}>
          {busy ? 'Logging in…' : 'Log in'}
        </button>
      </form>
      <ErrorBox error={error} />
    </section>
  )
}
