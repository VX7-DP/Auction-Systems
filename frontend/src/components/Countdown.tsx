import type { AuctionStatus } from '../api/contract.ts'
import { formatRemaining } from '../lib/time.ts'

const URGENT_MS = 60_000

/** Time left, from the server's end_at. The server's close event, not this clock, ends the auction. */
export function Countdown({ endAt, status, now }: { endAt: string; status: AuctionStatus; now: number }) {
  if (status === 'closed') return <span className="countdown countdown-ended">Ended</span>
  const remaining = Date.parse(endAt) - now
  if (remaining <= 0) return <span className="countdown countdown-closing">Closing…</span>
  return (
    <span className={remaining < URGENT_MS ? 'countdown countdown-urgent' : 'countdown'}>
      {formatRemaining(remaining)}
    </span>
  )
}
