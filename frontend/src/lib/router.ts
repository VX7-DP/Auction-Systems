// A small History API router: the app has four pages, so a routing library isn't needed.
import { useSyncExternalStore } from 'react'

export type Route =
  | { name: 'list' }
  | { name: 'auction'; id: string }
  | { name: 'sign-in' }
  | { name: 'admin' }
  | { name: 'not-found' }

export function matchRoute(pathname: string): Route {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  if (path === '/') return { name: 'list' }
  if (path === '/sign-in') return { name: 'sign-in' }
  if (path === '/admin') return { name: 'admin' }
  const auction = /^\/auctions\/([^/]+)$/.exec(path)
  if (auction) {
    try {
      return { name: 'auction', id: decodeURIComponent(auction[1]) }
    } catch {
      return { name: 'not-found' } // a broken %-escape would otherwise blank the app
    }
  }
  return { name: 'not-found' }
}

/** Only same-site paths are allowed as a redirect target, never another origin. */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return '/'
  return next
}

const LOCATION_EVENT = 'app:locationchange'

function subscribe(onChange: () => void): () => void {
  window.addEventListener('popstate', onChange)
  window.addEventListener(LOCATION_EVENT, onChange)
  return () => {
    window.removeEventListener('popstate', onChange)
    window.removeEventListener(LOCATION_EVENT, onChange)
  }
}

function currentLocation(): string {
  return window.location.pathname + window.location.search
}

/** The current path plus query string; re-renders on navigation. */
export function useLocation(): { pathname: string; search: string } {
  const href = useSyncExternalStore(subscribe, currentLocation)
  const url = new URL(href, 'http://localhost')
  return { pathname: url.pathname, search: url.search }
}

export function navigate(to: string, options: { replace?: boolean } = {}): void {
  if (to === currentLocation()) return
  if (options.replace) window.history.replaceState(null, '', to)
  else window.history.pushState(null, '', to)
  window.dispatchEvent(new Event(LOCATION_EVENT))
  if (!navigator.userAgent.includes('jsdom')) window.scrollTo(0, 0)
}

/** The current path and query, for "sign in and come back here" links. */
export function currentPath(): string {
  return currentLocation()
}
