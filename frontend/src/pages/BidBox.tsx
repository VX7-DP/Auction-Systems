import { useId, useState, type FormEvent } from 'react'
import { ClockIcon, ErrorIcon, NoticeIcon, PauseIcon } from '../components/Icons.tsx'
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
    return <section className="srow bid-loading" aria-busy="true" />
  }

  if (session.status === 'signed_out' || !me) {
    return (
      <section className="srow" aria-labelledby="join-heading">
        <h2 id="join-heading" className="slabel">
          Join the bidding
        </h2>
        <div className="sval bid">
          <p className="bid-intro">Pick a display name. No password or sign-up needed.</p>
          <SignInForm submitLabel="Start bidding" />
          {bidding.message && <p className="hint">{bidding.message.text}</p>}
        </div>
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
    if (view.highAmount === null && parsed.value <= view.startPrice) {
      setInputError(`Bid more than the opening price of ${formatMoney(view.startPrice)}.`)
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
    <section className="srow" aria-labelledby="bid-heading">
      <h2 id="bid-heading" className="slabel">
        Place a bid
      </h2>
      <div className="sval bid">
        {paused && (
          <div className="note note-warn">
            <PauseIcon />
            <p>Bidding is paused for a moment. It will resume shortly.</p>
          </div>
        )}
        {timeUp && !paused && (
          <div className="note note-info">
            <ClockIcon />
            <p>Time is up. Waiting for the auction to close…</p>
          </div>
        )}

        <button
          className="btn btn-primary btn-lg btn-block"
          disabled={disabled}
          onClick={() => void bidding.submit(suggested)}
        >
          {bidding.inFlight ? 'Placing bid…' : `Bid ${formatMoney(suggested)}`}
        </button>

        <form onSubmit={handleCustom} noValidate>
          <div className="fld">
            <label className="lbl" htmlFor={inputId}>
              Or enter your own amount
            </label>
            <span className="cur-btn">
              <span className="cur">
                <span className="cur-sym" aria-hidden="true">
                  {currencySymbol()}
                </span>
                <input
                  id={inputId}
                  className="cur-input"
                  inputMode="decimal"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  placeholder={String(suggested / 10 ** minorDigits())}
                  aria-invalid={inputError ? true : undefined}
                  aria-describedby="bid-hint"
                />
              </span>
              <button className="btn btn-secondary" type="submit" disabled={disabled}>
                Bid
              </button>
            </span>
          </div>
          <p id="bid-hint" className="hint num">
            {view.highAmount === null
              ? `Must be more than the opening price, ${formatMoney(view.startPrice)}`
              : `Must be more than ${formatMoney(view.highAmount)}`}
          </p>
          {inputError && (
            <p className="err" role="alert">
              <ErrorIcon size={16} />
              {inputError}
            </p>
          )}
        </form>

        <div className="bid-status" aria-live="polite" role="status">
          {message && (
            <div className={`note note-${message.tone}`}>
              <NoticeIcon tone={message.tone} />
              <p>{message.text}</p>
            </div>
          )}
        </div>
        {bidding.retryAmount !== null && !bidding.inFlight && (
          <button className="btn btn-secondary btn-block" onClick={() => void bidding.retry()}>
            Try again: {formatMoney(bidding.retryAmount)}
          </button>
        )}
      </div>
    </section>
  )
}
