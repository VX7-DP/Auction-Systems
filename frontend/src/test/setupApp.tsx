import { render } from '@testing-library/react'
import App from '../App.tsx'
import { setTransport, type Transport } from '../api/transport.ts'
import { createMockBackend, type MockBackend } from '../mock/mockBackend.ts'

interface Options {
  /** Runs before the first render, e.g. to create auctions or sign in. */
  seed?: (backend: MockBackend) => void
  /** Wraps the mock transport, e.g. to fail one request. */
  wrap?: (transport: Transport) => Transport
}

/** Renders the whole app at `path`, talking to a fresh in-memory backend. */
export function setupApp(path = '/', options: Options = {}) {
  const backend = createMockBackend({ adminPassword: 'admin' })
  options.seed?.(backend)
  setTransport(options.wrap ? options.wrap(backend.transport) : backend.transport)
  window.history.replaceState(null, '', path)
  const view = render(<App />)
  return { backend, ...view }
}
