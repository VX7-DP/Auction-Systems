import { useId, useState, type FormEvent } from 'react'
import { SignInForm } from '../components/SignInForm.tsx'
import type { AuctionView } from '../live/auctionFeed.ts'
import type { PlaceBid } from '../live/usePlaceBid.ts'
import { currencySymbol, formatMoney, minorDigits, nextBid, parseAmount } from '../lib/money.ts'
import { useSession } from '../session/SessionContext.ts'

export function BidBox({ view, bidding, now }: { view: AuctionView; bidding: PlaceBid; now: number }) {
  const session = useSession()
  const [input, setInput] = useState('')
  const [inputError, setInputError] = useState<string | null>(null)
  const inputId = useId()
  const me = session.bidder?.bidder_id ?? null
  const suggested = nextBid(view.highAmount, view.startPrice)
  const paused = view.status === 'paused'
  const timeUp = Date.parse(view.endAt) <= now
  const disabled = bidding.inFlight || paused

  if (session.status === 'loading') {
    return <section className="card bid-box" aria-busy="true" />
  }

  if (session.status === 'signed_out' || !me) {
    return (
      <section className="card bid-box" aria-labelledby="join-heading">
        <h2 id="join-heading" className="section-title">
          Join the bidding
        </h2>
        <p className="muted">Pick a display name. No password or sign-up needed.</p>
        <SignInForm submitLabel="Start bidding" />
        {bidding.message && <p className="muted">{bidding.message.text}</p>}
      </section>
    )
  }

  async function handleCustom(event: FormEvent) {
    event.preventDefault()
    const parsed = parseAmount(input)
    if (!parsed.ok) {
      setInputError(parsed.error)
      return
    }
    if (view.highAmount !== null && parsed.value <= view.highAmount) {
      setInputError(`Bid more than ${formatMoney(view.highAmount)}.`)
      return
    }
    setInputError(null)
    await bidding.submit(parsed.value)
    setInput('')
  }

  // An accepted message goes stale once someone else takes the lead.
  const message =
    bidding.message?.acceptedSeq !== undefined &&
    view.seq >= bidding.message.acceptedSeq &&
    view.highBidderId !== me
      ? null
      : bidding.message

  return (
    <section className="card bid-box" aria-labelledby="bid-heading">
      <h2 id="bid-heading" className="section-title">
        Place a bid
      </h2>
      {paused && <p className="muted">Bidding is paused for a moment. It will resume shortly.</p>}
      {timeUp && !paused && <p className="muted">Time is up. Waiting for the auction to close…</p>}

      <button
        className="btn btn-primary btn-lg btn-block"
        disabled={disabled}
        onClick={() => void bidding.submit(suggested)}
      >
        {bidding.inFlight ? 'Placing bid…' : `Bid ${formatMoney(suggested)}`}
      </button>

      <form className="custom-bid" onSubmit={handleCustom} noValidate>
        <div className="field">
          <label className="field-label" htmlFor={inputId}>
            Or enter your own amount
          </label>
          <span className="input-group">
            <span className="input-prefix" aria-hidden="true">
              {currencySymbol()}
            </span>
            <input
              id={inputId}
              className="input"
              inputMode="decimal"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={String(suggested / 10 ** minorDigits())}
              aria-invalid={inputError ? true : undefined}
              aria-describedby="bid-hint"
            />
            <button className="btn btn-secondary" type="submit" disabled={disabled}>
              Bid
            </button>
          </span>
        </div>
        <p id="bid-hint" className="hint">
          {view.highAmount === null
            ? `Opening price ${formatMoney(view.startPrice)}`
            : `Must be more than ${formatMoney(view.highAmount)}`}
        </p>
        {inputError && (
          <p className="form-error" role="alert">
            {inputError}
          </p>
        )}
      </form>

      <div aria-live="polite" role="status">
        {message && <p className={`bid-message bid-message-${message.tone}`}>{message.text}</p>}
      </div>
      {bidding.retryAmount !== null && !bidding.inFlight && (
        <button className="btn btn-secondary btn-block" onClick={() => void bidding.retry()}>
          Try again: {formatMoney(bidding.retryAmount)}
        </button>
      )}
    </section>
  )
}
