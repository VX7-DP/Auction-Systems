import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, placeBid } from '../api/client.ts'
import { newBidId } from '../lib/bidId.ts'
import { formatMoney } from '../lib/money.ts'

export interface BidMessage {
  tone: 'success' | 'error' | 'info'
  text: string
  /** For accepted bids: the seq the server gave it. */
  acceptedSeq?: number
}

export interface PlaceBid {
  inFlight: boolean
  message: BidMessage | null
  /** Set when the last attempt's outcome is unknown; retrying reuses its bid_id. */
  retryAmount: number | null
  submit(amount: number): Promise<void>
  retry(): Promise<void>
  clearMessage(): void
}

interface Callbacks {
  onAccepted(seq: number): void
  onStale(): void
  onSignedOut(): void
}

/**
 * Sends bids with an idempotency key (bid_id). A new key is made for every new
 * attempt; the same key is reused only to retry an attempt whose outcome is unknown
 * (network failure, timeout, 5xx), so a retry can never place the bid twice.
 */
export function usePlaceBid(auctionId: string, callbacks: Callbacks): PlaceBid {
  const [inFlight, setInFlight] = useState(false)
  const [message, setMessage] = useState<BidMessage | null>(null)
  const [unknown, setUnknown] = useState<{ bidId: string; amount: number } | null>(null)
  const callbacksRef = useRef(callbacks)
  useEffect(() => {
    callbacksRef.current = callbacks
  })
  const busy = useRef(false)

  const send = useCallback(
    async (bidId: string, amount: number) => {
      if (busy.current) return
      busy.current = true
      setInFlight(true)
      setMessage(null)
      const { onAccepted, onStale, onSignedOut } = callbacksRef.current
      try {
        const result = await placeBid(auctionId, { bid_id: bidId, amount })
        setUnknown(null)
        if (result.status === 'accepted') {
          if (result.seq !== undefined) onAccepted(result.seq)
          setMessage({ tone: 'success', text: `Your bid of ${formatMoney(amount)} is in.`, acceptedSeq: result.seq })
        } else if (result.status === 'rejected') {
          setMessage(rejectionMessage(result.reason))
          if (result.reason === 'too_low' || result.reason === 'closed') onStale()
        } else {
          setMessage({ tone: 'error', text: "Your bid couldn't be saved, so it wasn't placed. Please try again." })
        }
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          setUnknown(null)
          setMessage({ tone: 'info', text: 'Your session ended. Pick a name to keep bidding.' })
          onSignedOut()
        } else if (error instanceof ApiError && error.kind === 'http' && error.status !== null && error.status < 500) {
          setUnknown(null)
          setMessage({ tone: 'error', text: error.message })
        } else {
          setUnknown({ bidId, amount })
          setMessage({
            tone: 'error',
            text: "We couldn't confirm your bid. Try again: if it already went through, it won't be placed twice.",
          })
        }
      } finally {
        busy.current = false
        setInFlight(false)
      }
    },
    [auctionId],
  )

  const submit = useCallback(
    (amount: number) => {
      const reuse = unknown && unknown.amount === amount ? unknown.bidId : newBidId()
      return send(reuse, amount)
    },
    [send, unknown],
  )

  const retry = useCallback(async () => {
    if (unknown) await send(unknown.bidId, unknown.amount)
  }, [send, unknown])

  return {
    inFlight,
    message,
    retryAmount: unknown?.amount ?? null,
    submit,
    retry,
    clearMessage: () => setMessage(null),
  }
}

function rejectionMessage(reason: string | undefined): BidMessage {
  switch (reason) {
    case 'too_low':
      return { tone: 'info', text: 'Someone bid higher first. Try a higher amount.' }
    case 'closed':
      return { tone: 'info', text: 'This auction has ended.' }
    case 'paused':
      return { tone: 'info', text: 'Bidding is paused for a moment. Please try again shortly.' }
    case 'not_found':
      return { tone: 'error', text: 'This auction no longer exists.' }
    default:
      return { tone: 'error', text: 'Your bid was not accepted.' }
  }
}
