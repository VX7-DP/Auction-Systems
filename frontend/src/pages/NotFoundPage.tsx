import { Page } from '../components/Layout.tsx'
import { Link } from '../components/Link.tsx'
import { useDocumentTitle } from '../lib/useDocumentTitle.ts'

export function NotFoundPage() {
  useDocumentTitle('Page not found')
  return (
    <Page>
      <div className="empty">
        <h1>Page not found</h1>
        <p className="muted">The link may be broken, or the page may have moved.</p>
        <Link to="/" className="btn btn-primary">
          See all auctions
        </Link>
      </div>
    </Page>
  )
}
