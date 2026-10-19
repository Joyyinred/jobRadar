import { useCallback, useEffect, useState } from 'react'
import { getOccupationGroups, type OccupationGroup } from './api'
import { AdDetailPanel } from './components/AdDetailPanel'
import { AdList } from './components/AdList'
import { AddAdPanel } from './components/AddAdPanel'
import { MarketPanel } from './components/MarketPanel'
import { SearchImportPanel } from './components/SearchImportPanel'
import './App.css'

type Page = 'ads' | 'add' | 'market'

const PAGES: { id: Page; label: string }[] = [
  { id: 'ads', label: 'Ads' },
  { id: 'add', label: 'Add ads' },
  { id: 'market', label: 'Market' },
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

  // useCallback: the same function object on every render, so panels that
  // depend on it (e.g. in a useEffect) don't restart for no reason.
  const refreshList = useCallback(() => setRefreshKey((k) => k + 1), [])

  useEffect(() => {
    getOccupationGroups().then(setGroups).catch(() => setGroups([]))
  }, [refreshKey])

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
        <nav>
          {PAGES.map(({ id, label }) => (
            <button key={id} className={page === id ? 'nav active' : 'nav'} onClick={() => setPage(id)}>
              {label}
            </button>
          ))}
        </nav>
      </header>

      {page === 'ads' && (
        <main className="layout">
          <div className="column">
            <AdList groups={groups} selectedId={selectedId} onSelect={setSelectedId} refreshKey={refreshKey} />
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
              <AdDetailPanel key={selectedId} adId={selectedId} onChanged={refreshList} />
            )}
          </div>
        </main>
      )}

      {page === 'add' && (
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
