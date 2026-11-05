import { useCallback, useEffect, useState } from 'react'
import { listAuctions } from '../api/client.ts'
import type { AuctionSummary } from '../api/contract.ts'
import { StatusBadge } from '../components/Badges.tsx'
import { Countdown } from '../components/Countdown.tsx'
import { ErrorIcon } from '../components/Icons.tsx'
import { Page } from '../components/Layout.tsx'
import { Link } from '../components/Link.tsx'
import { CardSkeletons } from '../components/Skeletons.tsx'
import { LIST_POLL_MS } from '../config.ts'
import { formatMoney } from '../lib/money.ts'
import { useDocumentTitle } from '../lib/useDocumentTitle.ts'
import { useNow } from '../lib/useNow.ts'

/** Running auctions first, soonest to end on top; then ended ones, latest first. */
function sortAuctions(auctions: AuctionSummary[]): AuctionSummary[] {
  return [...auctions].sort((a, b) => {
    const aEnded = a.status === 'closed'
    const bEnded = b.status === 'closed'
    if (aEnded !== bEnded) return aEnded ? 1 : -1
    const byEnd = Date.parse(a.end_at) - Date.parse(b.end_at)
    return aEnded ? -byEnd : byEnd
  })
}

export function AuctionListPage() {
  useDocumentTitle(null)
  const [auctions, setAuctions] = useState<AuctionSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const now = useNow()

  const load = useCallback(
    () =>
      listAuctions().then(
        (loaded) => {
          setAuctions(loaded)
          setError(null)
        },
        (caught: unknown) => setError(caught instanceof Error ? caught.message : 'Something went wrong.'),
      ),
    [],
  )

  useEffect(() => {
    void load()
    // The contract has no list-level push, so refresh while the tab is visible.
    const timer = setInterval(() => {
      if (document.visibilityState !== 'hidden') void load()
    }, LIST_POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  return (
    <Page>
      <section className="intro">
        <h1 className="page-h">Live auctions</h1>
        <p className="lede">
          Bids appear for everyone the moment they land. Pick an auction, choose a name and bid.
        </p>
      </section>

      {error && (
        <div className="note note-error note-action" role="alert">
          <ErrorIcon />
          <span>We couldn't load the auctions. {error}</span>
          <button className="btn btn-secondary btn-sm" onClick={() => void load()}>
            Try again
          </button>
        </div>
      )}

      {auctions === null && !error && (
        <>
          <p className="hint loading-hint">Loading auctions…</p>
          <CardSkeletons />
        </>
      )}

      {auctions?.length === 0 && (
        <div className="empty">
          <h2>No auctions right now</h2>
          <p>New auctions show up here as soon as they start.</p>
        </div>
      )}

      {auctions && auctions.length > 0 && (
        <ul className="grid">
          {sortAuctions(auctions).map((auction) => (
            <li key={auction.id}>
              <Link to={`/auctions/${auction.id}`} className="card">
                <div className="card-top">
                  <h2 className="ct">{auction.title}</h2>
                  <StatusBadge status={auction.status} endAt={auction.end_at} now={now} />
                </div>
                <dl className="stats">
                  <div>
                    <dt>{auction.status === 'closed' ? 'Final bid' : 'Current bid'}</dt>
                    <dd className="stat amount">
                      {auction.high_amount === null ? <span className="stat-none">No bids yet</span> : formatMoney(auction.high_amount)}
                    </dd>
                  </div>
                  <div>
                    <dt>{auction.status === 'closed' ? 'Status' : 'Ends in'}</dt>
                    <dd className="stat">
                      <Countdown endAt={auction.end_at} status={auction.status} now={now} />
                    </dd>
                  </div>
                </dl>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Page>
  )
}
