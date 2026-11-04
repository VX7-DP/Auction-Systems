import type { ReactNode } from 'react'
import { currentPath, useLocation } from '../lib/router.ts'
import { useSession } from '../session/SessionContext.ts'
import { BotBadge } from './Badges.tsx'
import { Link } from './Link.tsx'

function signInHref(): string {
  const here = currentPath()
  return here.startsWith('/sign-in') ? '/sign-in' : `/sign-in?next=${encodeURIComponent(here)}`
}

export function Header() {
  const session = useSession()
  useLocation() // re-render on navigation so the sign-in link returns here
  return (
    <header className="site-header">
      <div className="container header-row">
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden="true" />
          Live Auction
        </Link>
        <div className="header-session">
          {session.status === 'signed_in' && session.bidder && (
            <>
              <span className="muted hide-narrow">Bidding as</span>
              <strong className="name">{session.bidder.display_name}</strong>
              <Link to={signInHref()} className="link-subtle">
                Change
              </Link>
            </>
          )}
          {session.status === 'signed_out' && (
            <Link to={signInHref()} className="btn btn-secondary btn-sm">
              Sign in
            </Link>
          )}
        </div>
      </div>
    </header>
  )
}

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="container footer-row">
        <p className="muted">
          Demo: bidders marked <BotBadge /> are simulated, so there's always live action.
        </p>
        <Link to="/admin" className="link-subtle">
          Admin
        </Link>
      </div>
    </footer>
  )
}

export function Page({ children }: { children: ReactNode }) {
  return <div className="container page">{children}</div>
}
