import { useState, type FormEvent } from 'react'
import { useSession } from '../session/SessionContext.ts'

const MAX_NAME = 40

export function SignInForm({
  submitLabel = 'Continue',
  onSignedIn,
  autoFocus = false,
}: {
  submitLabel?: string
  onSignedIn?: () => void
  autoFocus?: boolean
}) {
  const session = useSession()
  const [name, setName] = useState(session.bidder?.display_name ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const trimmed = name.trim()
    if (trimmed.length === 0) {
      setError('Enter a display name.')
      return
    }
    if (trimmed.length > MAX_NAME) {
      setError(`Use at most ${MAX_NAME} characters.`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      await session.signIn(trimmed)
      onSignedIn?.()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="stack-sm" onSubmit={handleSubmit} noValidate>
      <label className="field">
        <span className="field-label">Display name</span>
        <input
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={MAX_NAME}
          autoComplete="nickname"
          autoFocus={autoFocus}
          placeholder="e.g. Asha"
          aria-invalid={error ? true : undefined}
        />
      </label>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? 'Signing in…' : submitLabel}
      </button>
    </form>
  )
}
