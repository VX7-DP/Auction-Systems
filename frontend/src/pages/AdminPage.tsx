import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { ApiError, createAuction } from '../api/client.ts'
import type { AuctionSnapshot } from '../api/contract.ts'
import { CheckIcon, ChevronIcon, ErrorIcon } from '../components/Icons.tsx'
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
        <section className="narrow created" role="status">
          <p className="label-caps created-label">
            <CheckIcon size={16} />
            Auction created
          </p>
          <h1 className="title-h">{created.title}</h1>
          <p className="created-meta num">
            Opening price {formatMoney(created.start_price)} · ends {formatDateTime(created.end_at)}
          </p>
          <div className="actions">
            <Link to={`/auctions/${created.id}`} className="btn btn-primary btn-lg">
              Open auction
            </Link>
            <button className="btn btn-secondary btn-lg" onClick={() => setCreated(null)}>
              Create another
            </button>
          </div>
        </section>
      </Page>
    )
  }

  return (
    <Page>
      <section className="narrow">
        <h1 className="title-h">Create an auction</h1>
        <p className="lede">It goes live as soon as you create it.</p>
        <form className="form-fields" onSubmit={handleSubmit} noValidate>
          <Field id={`${id}-password`} label="Admin password" error={errors.password}>
            <input
              id={`${id}-password`}
              className="inp"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
            />
          </Field>
          <Field id={`${id}-title`} label="Title" error={errors.title}>
            <input
              id={`${id}-title`}
              className="inp"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={200}
              placeholder="e.g. Vintage film camera"
            />
          </Field>
          <Field id={`${id}-price`} label="Opening price" error={errors.price}>
            <span className="cur">
              <span className="cur-sym" aria-hidden="true">
                {currencySymbol()}
              </span>
              <input
                id={`${id}-price`}
                className="cur-input"
                inputMode="decimal"
                value={price}
                onChange={(event) => setPrice(event.target.value)}
                placeholder="0"
              />
            </span>
          </Field>
          <Field id={`${id}-end`} label="Ends" error={errors.end}>
            <span className="sel">
              <select id={`${id}-end`} className="inp" value={duration} onChange={(event) => setDuration(event.target.value)}>
                {DURATIONS.map((option) => (
                  <option key={option.minutes} value={String(option.minutes)}>
                    In {option.label}
                  </option>
                ))}
                <option value="custom">At a date and time…</option>
              </select>
              <ChevronIcon />
            </span>
          </Field>
          {duration === 'custom' && (
            <Field id={`${id}-custom`} label="End date and time (your time zone)">
              <input
                id={`${id}-custom`}
                className="inp"
                type="datetime-local"
                value={customEnd}
                onChange={(event) => setCustomEnd(event.target.value)}
              />
            </Field>
          )}
          {errors.form && (
            <p className="err" role="alert">
              <ErrorIcon size={16} />
              {errors.form}
            </p>
          )}
          <button className="btn btn-primary btn-lg btn-block" type="submit" disabled={busy}>
            {busy ? 'Creating…' : 'Create auction'}
          </button>
        </form>
      </section>
    </Page>
  )
}

function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: ReactNode }) {
  return (
    <div className="fld">
      <label className="lbl" htmlFor={id}>
        {label}
      </label>
      {children}
      {error && (
        <span className="err" role="alert">
          <ErrorIcon size={16} />
          {error}
        </span>
      )}
    </div>
  )
}
