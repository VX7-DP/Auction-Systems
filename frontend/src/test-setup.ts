import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { browserTransport, setTransport } from './api/transport.ts'

afterEach(() => {
  cleanup()
  setTransport(browserTransport)
  window.history.replaceState(null, '', '/')
})
