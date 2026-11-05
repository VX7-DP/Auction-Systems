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
    <header className="hdr">
      <div className="wrap hdr-in">
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>
            SummitTech<span className="brand-light"> Auctions</span>
          </span>
        </Link>
        <div className="who-bar">
          {session.status === 'signed_in' && session.bidder && (
            <>
              <span className="who-as">Bidding as</span>
              <strong className="who-name">{session.bidder.display_name}</strong>
              <Link to={signInHref()}>
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
    <footer className="ftr">
      <div className="wrap ftr-in">
        <p>
          Demo: bidders marked <BotBadge /> are simulated, so there's always live action.
        </p>
        <div className="ftr-links">
          <Link to="/admin">Admin</Link>
          <a href="https://summittech.in">Built by Sumit · summittech.in</a>
        </div>
      </div>
    </footer>
  )
}

export function Page({ children }: { children: ReactNode }) {
  return <div className="wrap page">{children}</div>
}
