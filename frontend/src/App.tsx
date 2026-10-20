import { useCallback, useEffect, useState } from 'react'
import { getApplications, getMe, getOccupationGroups, logOut, type OccupationGroup, type User } from './api'
import { AdDetailPanel } from './components/AdDetailPanel'
import { AdList } from './components/AdList'
import { AddAdPanel } from './components/AddAdPanel'
import { ApplicationsPanel } from './components/ApplicationsPanel'
import { LoginPanel } from './components/LoginPanel'
import { MarketPanel } from './components/MarketPanel'
import { SearchImportPanel } from './components/SearchImportPanel'
import './App.css'

type Page = 'ads' | 'add' | 'market' | 'applications' | 'login'

const PAGES: { id: Page; label: string; loggedIn?: boolean }[] = [
  { id: 'ads', label: 'Ads' },
  { id: 'add', label: 'Add ads' },
  { id: 'market', label: 'Market' },
  { id: 'applications', label: 'My applications', loggedIn: true }, // personal: only when logged in
]

/** The whole app. Holds only what several pages share: which page is open,
 *  which ad is selected, a counter to refresh the list, and the occupation groups. */
export default function App() {
  // Browsing ads is what you do most, so it's the first page; adding ads is
  // occasional and gets its own page instead of pushing the list down.
  const [page, setPage] = useState<Page>('ads')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)
  const [groups, setGroups] = useState<OccupationGroup[]>([])
  // undefined = still asking the server; null = nobody logged in.
  const [user, setUser] = useState<User | null | undefined>(undefined)
  // Where to go back to after logging in.
  const [afterLogin, setAfterLogin] = useState<Page>('ads')
  // Your status per ad id, for the tags in the ad list. One small request for
  // all of them, rather than asking per ad.
  const [tracked, setTracked] = useState<Record<number, string>>({})

  // useCallback: the same function object on every render, so panels that
  // depend on it (e.g. in a useEffect) don't restart for no reason.
  const refreshList = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    getOccupationGroups().then(setGroups).catch(() => setGroups([]))
  }, [refreshKey])

  useEffect(() => {
    if (!user) return // logged out: `trackedNow` below is empty, nothing to fetch
    getApplications()
      .then(({ results }) => setTracked(Object.fromEntries(results.map((app) => [app.ad.id, app.status]))))
      .catch(() => setTracked({}))
  }, [user, refreshKey])
  const trackedNow = user ? tracked : {}

  // Who's logged in — asked once. The session cookie is HttpOnly, so the page
  // can't look for itself; this also gets the csrftoken cookie for later POSTs.
  useEffect(() => {
    getMe().then(setUser).catch(() => setUser(null))
  }, [])

  function goToLogin(from: Page) {
    setAfterLogin(from)
    setPage('login')
  }

  async function handleLogOut() {
    try {
      await logOut()
    } finally {
      setUser(null) // logged out on screen even if the server didn't answer
      if (page === 'applications') setPage('ads')
    }
  }

  /** After adding an ad: go to the Ads page with it selected, so its parse
   *  progress shows right away. */
  function showAd(adId: number) {
    setSelectedId(adId)
    refreshList()
    setPage('ads')
  }

  return (
    <div className="app">
      <header className="header">
        <h1>
          Job<span>Radar</span>
        </h1>
        <nav className="row">
          {PAGES.filter((p) => !p.loggedIn || user).map(({ id, label }) => (
            <button key={id} className={page === id ? 'nav active' : 'nav'} onClick={() => setPage(id)}>
              {label}
            </button>
          ))}
          {user && (
            <span className="user">
              {user.username} ·{' '}
              <button className="link-button" onClick={handleLogOut}>
                Log out
              </button>
            </span>
          )}
          {user === null && (
            <button className={page === 'login' ? 'nav active' : 'nav'} onClick={() => goToLogin(page)}>
              Log in
            </button>
          )}
        </nav>
      </header>

      {page === 'ads' && (
        <main className="layout">
          <div className="column">
            <AdList
              groups={groups}
              selectedId={selectedId}
              onSelect={setSelectedId}
              refreshKey={refreshKey}
              tracked={trackedNow}
            />
          </div>
          <div className="column sticky">
            {selectedId === null ? (
              <section className="card muted">
                Select an ad to see what it asks for — or{' '}
                <button className="link-button" onClick={() => setPage('add')}>
                  add ads
                </button>
                .
              </section>
            ) : (
              // key: a different ad gets a fresh panel (fresh state), instead
              // of briefly showing the previous ad while the new one loads.
              <AdDetailPanel key={selectedId} adId={selectedId} onChanged={refreshList} loggedIn={!!user} />
            )}
          </div>
        </main>
      )}

      {page === 'add' && !user && (
        <main className="layout single">
          <section className="card muted">
            Adding and importing ads uses the LLM, so it needs an account —{' '}
            <button className="link-button" onClick={() => goToLogin('add')}>
              log in
            </button>
            .
          </section>
        </main>
      )}

      {page === 'applications' && user && (
        <main className="layout single">
          <ApplicationsPanel onOpenAd={showAd} refreshKey={refreshKey} onChanged={refreshList} />
        </main>
      )}

      {page === 'login' && (
        <main className="layout single">
          <LoginPanel
            onLoggedIn={(u) => {
              setUser(u)
              setPage(afterLogin === 'login' ? 'ads' : afterLogin)
            }}
          />
        </main>
      )}

      {page === 'add' && user && (
        <main className="layout">
          <div className="column">
            <AddAdPanel onAdded={showAd} />
          </div>
          <div className="column">
            <SearchImportPanel onProgress={refreshList} onShowAds={() => setPage('ads')} />
          </div>
        </main>
      )}

      {page === 'market' && (
        <main className="layout single">
          <MarketPanel groups={groups} />
        </main>
      )}
    </div>
  )
}
