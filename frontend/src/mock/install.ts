import { setTransport } from '../api/transport.ts'
import { createMockBackend, type MockBackend } from './mockBackend.ts'
import { seedDemoAuctions } from './seed.ts'

declare global {
  interface Window {
    /** Dev only: poke the mock from the console, e.g. mockBackend.pause(id). */
    mockBackend?: MockBackend
  }
}

export function installMockBackend(): MockBackend {
  const backend = createMockBackend({ latencyMs: 120, sessionStorageKey: 'mock-backend-session' })
  seedDemoAuctions(backend)
  backend.startSimulation()
  setTransport(backend.transport)
  window.mockBackend = backend
  console.info('Mock backend on. Try mockBackend.pause(id), mockBackend.disconnectSockets().')
  return backend
}
