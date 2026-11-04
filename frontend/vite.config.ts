/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dev server proxies the API and WebSockets to the backend, so the browser sees
// one origin and the session cookie just works. Docker compose sets the target to
// http://backend:8000.
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': apiTarget,
      '/ws': { target: apiTarget, ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
  },
})
