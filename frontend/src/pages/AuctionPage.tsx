import { useState } from 'react'
import { BotBadge, StatusBadge, YouBadge } from '../components/Badges.tsx'
import { ConnectionPill } from '../components/ConnectionPill.tsx'
import { Countdown } from '../components/Countdown.tsx'
import { CheckIcon, ErrorIcon, InfoIcon, LeadIcon, PauseIcon } from '../components/Icons.tsx'
import { Page } from '../components/Layout.tsx'
import { Link } from '../components/Link.tsx'
import { AuctionSkeleton } from '../components/Skeletons.tsx'
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
        <div className="narrow nf">
          <h1 className="page-h">Auction not found</h1>
          <p className="lede">It may have been removed, or the link is wrong.</p>
          <Link to="/" className="btn btn-primary btn-lg">
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
          <div className="notices">
            <div className="note note-error" role="alert">
              <ErrorIcon />
              <p>We couldn't load this auction ({feed.loadError}). Retrying…</p>
            </div>
          </div>
        ) : (
          <>
            <AuctionSkeleton />
            <p className="hint">Loading auction…</p>
          </>
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
      <h1 className="title-h">{view.title}</h1>
      <div className="meta">
        <StatusBadge status={view.status} endAt={view.endAt} now={now} />
        <ConnectionPill connection={feed.connection} />
      </div>

      <div className="notices">
        {showOutbid && (
          <div className="note note-outbid" role="alert">
            <p>
              <strong>You've been outbid.</strong>{' '}
              <strong>{outbid.byName ?? 'Someone'}</strong> {outbid.byIsBot && <BotBadge />} bid{' '}
              {outbid.amount !== null && <span className="num">{formatMoney(outbid.amount)}</span>}.
            </p>
            <div className="note-actions">
              {view.status === 'open' && (
                <button
                  className="btn btn-primary"
                  disabled={bidding.inFlight}
                  onClick={() => void bidding.submit(nextBid(view.highAmount, view.startPrice))}
                >
                  Bid {formatMoney(nextBid(view.highAmount, view.startPrice))}
                </button>
              )}
              <button className="btn btn-ghost" onClick={() => setDismissedOutbid(outbid.id)}>
                Dismiss
              </button>
            </div>
          </div>
        )}

        {view.status === 'paused' && (
          <div className="note note-warn" role="status">
            <PauseIcon />
            <p>Bidding is paused for a moment. It will resume shortly; no bids are lost.</p>
          </div>
        )}

        {closed && <ClosedResult view={view} me={me} />}
      </div>

      <div className="cols">
        <div className="col-main stmt">
          <section className="price-card" aria-label="Current bid">
            <div className="srow">
              <p className="slabel">{closed ? 'Winning bid' : view.highAmount === null ? 'Opening price' : 'Current bid'}</p>
              <div className="sval">
                <p key={view.highAmount ?? 'none'} className={view.bids[0]?.fresh ? 'hero amount flash' : 'hero amount'}>
                  {formatMoney(view.highAmount ?? view.startPrice)}
                </p>
                <p className="byline">
                  {view.highAmount === null ? (
                    <span className="byline-none">No bids yet. Be the first.</span>
                  ) : iLead ? (
                    <span className="youline">
                      <LeadIcon />
                      <span className="lead-you">{closed ? 'You won' : "You're the highest bidder"}</span>
                    </span>
                  ) : (
                    <>
                      <span>by</span> <strong>{view.highBidderName}</strong>{' '}
                      {view.highIsBot && <BotBadge />}
                    </>
                  )}
                </p>
              </div>
            </div>
            <div className="srow">
              <p className="slabel">{closed ? 'Status' : 'Ends in'}</p>
              <div className="sval">
                <p className="hero">
                  <Countdown endAt={view.endAt} status={view.status} now={now} />
                </p>
                <p className="sub">
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
    <Link to="/" className="back">
      ← All auctions
    </Link>
  )
}

function ClosedResult({ view, me }: { view: AuctionView; me: string | null }) {
  const winner = view.winner
  if (!winner || winner.id === null) {
    return (
      <div className="note note-info" role="status">
        <InfoIcon />
        <p>This auction has ended with no bids.</p>
      </div>
    )
  }
  if (winner.id === me) {
    return (
      <div className="note note-success" role="status">
        <CheckIcon />
        <p>
          <strong>You won this auction</strong>
          {winner.amount !== null && <> for <span className="num">{formatMoney(winner.amount)}</span></>}.
        </p>
      </div>
    )
  }
  return (
    <div className="note note-info" role="status">
      <InfoIcon />
      <p>
        Auction ended. Won by <strong>{winner.name}</strong>
        {view.highIsBot && <> <BotBadge /></>}
        {winner.amount !== null && <> for <span className="num">{formatMoney(winner.amount)}</span></>}.
      </p>
    </div>
  )
}

function BidHistory({ view }: { view: AuctionView }) {
  return (
    <section className="col-side stmt history" aria-labelledby="history-heading">
      <h2 id="history-heading" className="label-caps hist-h">
        Bid history
      </h2>
      {view.bids.length === 0 ? (
        <p className="hist-empty">No bids yet.</p>
      ) : (
        <ol className="hist">
          {view.bids.map((bid) => (
            <li key={bid.seq} className={bid.fresh ? 'hrow is-new' : 'hrow'}>
              <time className="ht" dateTime={bid.createdAt}>
                {formatClock(bid.createdAt)}
              </time>
              <span className="hwho">
                <span className="hnm">{bid.bidderName}</span> {bid.isBot && <BotBadge />} {bid.mine && <YouBadge />}
              </span>
              <span className="hlead" aria-hidden="true" />
              <span className="hamt">{formatMoney(bid.amount)}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
