import { useState } from 'react'
import { BotBadge, StatusBadge, YouBadge } from '../components/Badges.tsx'
import { ConnectionPill } from '../components/ConnectionPill.tsx'
import { Countdown } from '../components/Countdown.tsx'
import { Page } from '../components/Layout.tsx'
import { Link } from '../components/Link.tsx'
import type { AuctionView } from '../live/auctionFeed.ts'
import { useAuctionFeed } from '../live/useAuctionFeed.ts'
import { usePlaceBid } from '../live/usePlaceBid.ts'
import { formatMoney, nextBid } from '../lib/money.ts'
import { formatClock, formatDateTime } from '../lib/time.ts'
import { useDocumentTitle } from '../lib/useDocumentTitle.ts'
import { useNow } from '../lib/useNow.ts'
import { useSession } from '../session/SessionContext.ts'
import { BidBox } from './BidBox.tsx'

export function AuctionPage({ auctionId }: { auctionId: string }) {
  const session = useSession()
  const me = session.bidder?.bidder_id ?? null
  const [feed, actions] = useAuctionFeed(auctionId, me)
  const bidding = usePlaceBid(auctionId, {
    onAccepted: (seq) => {
      actions.markMine(seq)
      actions.expectSeq(seq)
    },
    onStale: actions.resync,
    onSignedOut: () => void session.refresh(),
  })
  const [dismissedOutbid, setDismissedOutbid] = useState<number | null>(null)
  const now = useNow()
  const view = feed.view
  useDocumentTitle(view?.title ?? null)

  if (feed.phase === 'not_found') {
    return (
      <Page>
        <div className="empty">
          <h1>Auction not found</h1>
          <p className="muted">It may have been removed, or the link is wrong.</p>
          <Link to="/" className="btn btn-primary">
            See all auctions
          </Link>
        </div>
      </Page>
    )
  }

  if (!view) {
    return (
      <Page>
        <BackLink />
        {feed.loadError ? (
          <div className="notice notice-error" role="alert">
            We couldn't load this auction ({feed.loadError}). Retrying…
          </div>
        ) : (
          <p className="muted">Loading auction…</p>
        )}
      </Page>
    )
  }

  const closed = view.status === 'closed'
  const iLead = me !== null && view.highBidderId === me
  const outbid = feed.outbid
  const showOutbid = outbid !== null && outbid.id !== dismissedOutbid && !iLead && !closed

  return (
    <Page>
      <BackLink />
      <div className="auction-head">
        <h1>{view.title}</h1>
        <div className="auction-head-meta">
          <StatusBadge status={view.status} endAt={view.endAt} now={now} />
          <ConnectionPill connection={feed.connection} />
        </div>
      </div>

      {showOutbid && (
        <div className="notice notice-warning outbid" role="alert">
          <div>
            <strong>You've been outbid.</strong>{' '}
            {outbid.byName ?? 'Someone'} {outbid.byIsBot && <BotBadge />} bid{' '}
            {outbid.amount !== null && formatMoney(outbid.amount)}.
          </div>
          <div className="notice-actions">
            {view.status === 'open' && (
              <button
                className="btn btn-primary btn-sm"
                disabled={bidding.inFlight}
                onClick={() => void bidding.submit(nextBid(view.highAmount, view.startPrice))}
              >
                Bid {formatMoney(nextBid(view.highAmount, view.startPrice))}
              </button>
            )}
            <button className="btn btn-ghost btn-sm" onClick={() => setDismissedOutbid(outbid.id)}>
              Dismiss
            </button>
          </div>
        </div>
      )}

      {view.status === 'paused' && (
        <div className="notice notice-info" role="status">
          Bidding is paused for a moment. It will resume shortly; no bids are lost.
        </div>
      )}

      {closed && <ClosedResult view={view} me={me} />}

      <div className="auction-layout">
        <div className="stack">
          <section className="card price-card" aria-label="Current bid">
            <div className="price-row">
              <div>
                <p className="label">{closed ? 'Winning bid' : view.highAmount === null ? 'Opening price' : 'Current bid'}</p>
                <p key={view.highAmount ?? 'none'} className={view.bids[0]?.fresh ? 'price flash' : 'price'}>
                  {formatMoney(view.highAmount ?? view.startPrice)}
                </p>
                <p className="bidder-line">
                  {view.highAmount === null ? (
                    <span className="muted">No bids yet. Be the first.</span>
                  ) : iLead ? (
                    <span className="lead-you">{closed ? 'You won' : "You're the highest bidder"}</span>
                  ) : (
                    <>
                      <span className="muted">by</span> <strong>{view.highBidderName}</strong>{' '}
                      {view.highIsBot && <BotBadge />}
                    </>
                  )}
                </p>
              </div>
              <div className="time-block">
                <p className="label">{closed ? 'Status' : 'Ends in'}</p>
                <p className="time-left">
                  <Countdown endAt={view.endAt} status={view.status} now={now} />
                </p>
                <p className="muted small">
                  {closed ? 'Ended' : 'Ends'} {formatDateTime(view.endAt)}
                </p>
              </div>
            </div>
          </section>
          {!closed && <BidBox view={view} bidding={bidding} now={now} />}
        </div>
        <BidHistory view={view} />
      </div>
    </Page>
  )
}

function BackLink() {
  return (
    <Link to="/" className="back-link">
      ← All auctions
    </Link>
  )
}

function ClosedResult({ view, me }: { view: AuctionView; me: string | null }) {
  const winner = view.winner
  if (!winner || winner.id === null) {
    return (
      <div className="notice notice-info" role="status">
        This auction has ended with no bids.
      </div>
    )
  }
  if (winner.id === me) {
    return (
      <div className="notice notice-success" role="status">
        <div>
          <strong>You won this auction</strong>
          {winner.amount !== null && <> for {formatMoney(winner.amount)}</>}.
        </div>
      </div>
    )
  }
  return (
    <div className="notice notice-info" role="status">
      <div>
        Auction ended. Won by <strong>{winner.name}</strong>
        {view.highIsBot && <> <BotBadge /></>}
        {winner.amount !== null && <> for {formatMoney(winner.amount)}</>}.
      </div>
    </div>
  )
}

function BidHistory({ view }: { view: AuctionView }) {
  return (
    <section className="card history" aria-labelledby="history-heading">
      <h2 id="history-heading" className="section-title">
        Bid history
      </h2>
      {view.bids.length === 0 ? (
        <p className="muted">No bids yet.</p>
      ) : (
        <ol className="history-list">
          {view.bids.map((bid) => (
            <li key={bid.seq} className={bid.fresh ? 'history-row history-row-new' : 'history-row'}>
              <span className="history-amount">{formatMoney(bid.amount)}</span>
              <span className="history-who">
                {bid.bidderName} {bid.isBot && <BotBadge />} {bid.mine && <YouBadge />}
              </span>
              <time className="history-time muted" dateTime={bid.createdAt}>
                {formatClock(bid.createdAt)}
              </time>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
