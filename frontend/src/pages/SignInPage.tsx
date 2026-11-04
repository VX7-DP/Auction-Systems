import { Page } from '../components/Layout.tsx'
import { SignInForm } from '../components/SignInForm.tsx'
import { navigate, safeNext, useLocation } from '../lib/router.ts'
import { useDocumentTitle } from '../lib/useDocumentTitle.ts'
import { useSession } from '../session/SessionContext.ts'

export function SignInPage() {
  useDocumentTitle('Sign in')
  const { search } = useLocation()
  const session = useSession()
  const next = safeNext(new URLSearchParams(search).get('next'))
  const signedIn = session.status === 'signed_in' && session.bidder

  return (
    <Page>
      <section className="card narrow">
        <h1>{signedIn ? 'Change your name' : 'Pick a display name'}</h1>
        <p className="muted">
          {signedIn
            ? `You're bidding as ${session.bidder?.display_name}. Other bidders see your new name on future bids.`
            : 'Your name appears next to your bids. No password or sign-up needed.'}
        </p>
        {session.status !== 'loading' && (
          <SignInForm
            autoFocus
            submitLabel={signedIn ? 'Save name' : 'Start bidding'}
            onSignedIn={() => navigate(next, { replace: true })}
          />
        )}
      </section>
    </Page>
  )
}
