import type { AuctionStatus } from '../api/contract.ts'

export function BotBadge() {
  return (
    <span className="badge badge-bot" title="A simulated bidder">
      Bot
    </span>
  )
}

export function YouBadge() {
  return <span className="badge badge-you">You</span>
}

/** Live, Closing (time is up, waiting for the server to close it), Paused or Ended. */
export function StatusBadge({ status, endAt, now }: { status: AuctionStatus; endAt: string; now: number }) {
  if (status === 'closed') return <span className="badge badge-ended">Ended</span>
  if (status === 'paused') return <span className="badge badge-paused">Paused</span>
  if (Date.parse(endAt) <= now) return <span className="badge badge-paused">Closing</span>
  return (
    <span className="badge badge-live">
      <span className="dot" aria-hidden="true" />
      Live
    </span>
  )
}
