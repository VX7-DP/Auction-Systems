import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { ApiError, createAuction } from '../api/client.ts'
import type { AuctionSnapshot } from '../api/contract.ts'
import { Page } from '../components/Layout.tsx'
import { Link } from '../components/Link.tsx'
import { currencySymbol, formatMoney, parseAmount } from '../lib/money.ts'
import { formatDateTime } from '../lib/time.ts'
import { useDocumentTitle } from '../lib/useDocumentTitle.ts'

const DURATIONS = [
  { label: '2 minutes', minutes: 2 },
  { label: '5 minutes', minutes: 5 },
  { label: '15 minutes', minutes: 15 },
  { label: '1 hour', minutes: 60 },
  { label: '1 day', minutes: 24 * 60 },
] as const

type Errors = Partial<Record<'password' | 'title' | 'price' | 'end' | 'form', string>>

export function AdminPage() {
  useDocumentTitle('Admin')
  const [password, setPassword] = useState('')
  const [title, setTitle] = useState('')
  const [price, setPrice] = useState('')
  const [duration, setDuration] = useState<string>('5')
  const [customEnd, setCustomEnd] = useState('')
  const [errors, setErrors] = useState<Errors>({})
  const [busy, setBusy] = useState(false)
  const [created, setCreated] = useState<AuctionSnapshot | null>(null)
  const id = useId()

  function endAt(): Date | null {
    if (duration === 'custom') {
      // datetime-local has no zone: the browser reads it as local time.
      const parsed = customEnd ? new Date(customEnd) : null
      return parsed && !Number.isNaN(parsed.getTime()) ? parsed : null
    }
    return new Date(Date.now() + Number(duration) * 60_000)
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const found: Errors = {}
    if (!password) found.password = 'Enter the admin password.'
    const trimmed = title.trim()
    if (!trimmed) found.title = 'Enter a title.'
    else if (trimmed.length > 200) found.title = 'Use at most 200 characters.'
    const parsedPrice = parseAmount(price.trim() === '' ? '0' : price, undefined, { allowZero: true })
    if (!parsedPrice.ok) found.price = parsedPrice.error
    const end = endAt()
    if (!end) found.end = 'Pick an end date and time.'
    else if (end.getTime() <= Date.now()) found.end = 'The end time must be in the future.'
    setErrors(found)
    if (Object.keys(found).length > 0 || !parsedPrice.ok || !end) return

    setBusy(true)
    try {
      const auction = await createAuction(
        { title: trimmed, start_price: parsedPrice.value, end_at: end.toISOString() },
        password,
      )
      setCreated(auction)
      setTitle('')
      setPrice('')
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setErrors({ password: 'Wrong admin password.' })
      } else {
        setErrors({ form: caught instanceof Error ? caught.message : 'Something went wrong.' })
      }
    } finally {
      setBusy(false)
    }
  }

  if (created) {
    return (
      <Page>
        <section className="card narrow" role="status">
          <p className="label">Auction created</p>
          <h1>{created.title}</h1>
          <p className="muted">
            Opening price {formatMoney(created.start_price)} · ends {formatDateTime(created.end_at)}
          </p>
          <div className="button-row">
            <Link to={`/auctions/${created.id}`} className="btn btn-primary">
              Open auction
            </Link>
            <button className="btn btn-secondary" onClick={() => setCreated(null)}>
              Create another
            </button>
          </div>
        </section>
      </Page>
    )
  }

  return (
    <Page>
      <section className="card narrow">
        <h1>Create an auction</h1>
        <p className="muted">It goes live as soon as you create it.</p>
        <form className="stack" onSubmit={handleSubmit} noValidate>
          <Field id={`${id}-password`} label="Admin password" error={errors.password}>
            <input
              id={`${id}-password`}
              className="input"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
            />
          </Field>
          <Field id={`${id}-title`} label="Title" error={errors.title}>
            <input
              id={`${id}-title`}
              className="input"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={200}
              placeholder="e.g. Vintage film camera"
            />
          </Field>
          <Field id={`${id}-price`} label="Opening price" error={errors.price}>
            <span className="input-group">
              <span className="input-prefix" aria-hidden="true">
                {currencySymbol()}
              </span>
              <input
                id={`${id}-price`}
                className="input"
                inputMode="decimal"
                value={price}
                onChange={(event) => setPrice(event.target.value)}
                placeholder="0"
              />
            </span>
          </Field>
          <Field id={`${id}-end`} label="Ends" error={errors.end}>
            <select id={`${id}-end`} className="input" value={duration} onChange={(event) => setDuration(event.target.value)}>
              {DURATIONS.map((option) => (
                <option key={option.minutes} value={String(option.minutes)}>
                  In {option.label}
                </option>
              ))}
              <option value="custom">At a date and time…</option>
            </select>
          </Field>
          {duration === 'custom' && (
            <Field id={`${id}-custom`} label="End date and time (your time zone)">
              <input
                id={`${id}-custom`}
                className="input"
                type="datetime-local"
                value={customEnd}
                onChange={(event) => setCustomEnd(event.target.value)}
              />
            </Field>
          )}
          {errors.form && (
            <p className="form-error" role="alert">
              {errors.form}
            </p>
          )}
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? 'Creating…' : 'Create auction'}
          </button>
        </form>
      </section>
    </Page>
  )
}

function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {children}
      {error && (
        <span className="form-error" role="alert">
          {error}
        </span>
      )}
    </div>
  )
}
