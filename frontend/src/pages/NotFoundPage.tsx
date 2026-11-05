import { Page } from '../components/Layout.tsx'
import { Link } from '../components/Link.tsx'
import { useDocumentTitle } from '../lib/useDocumentTitle.ts'

export function NotFoundPage() {
  useDocumentTitle('Page not found')
  return (
    <Page>
      <div className="narrow nf">
        <h1 className="page-h">Page not found</h1>
        <p className="lede">The link may be broken, or the page may have moved.</p>
        <Link to="/" className="btn btn-primary btn-lg">
          See all auctions
        </Link>
      </div>
    </Page>
  )
}
