import { Footer, Header } from './components/Layout.tsx'
import { matchRoute, useLocation } from './lib/router.ts'
import { AdminPage } from './pages/AdminPage.tsx'
import { AuctionListPage } from './pages/AuctionListPage.tsx'
import { AuctionPage } from './pages/AuctionPage.tsx'
import { NotFoundPage } from './pages/NotFoundPage.tsx'
import { SignInPage } from './pages/SignInPage.tsx'
import { SessionProvider } from './session/SessionProvider.tsx'

function Routes() {
  const route = matchRoute(useLocation().pathname)
  switch (route.name) {
    case 'list':
      return <AuctionListPage />
    case 'auction':
      // key: a fresh page (and live feed) per auction
      return <AuctionPage key={route.id} auctionId={route.id} />
    case 'sign-in':
      return <SignInPage />
    case 'admin':
      return <AdminPage />
    case 'not-found':
      return <NotFoundPage />
  }
}

function App() {
  return (
    <SessionProvider>
      <div className="app">
        <Header />
        <main>
          <Routes />
        </main>
        <Footer />
      </div>
    </SessionProvider>
  )
}

export default App
