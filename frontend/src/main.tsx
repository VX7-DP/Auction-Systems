import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

async function boot() {
  // `npm run dev:mock` runs the whole app against an in-memory backend. Vite replaces
  // MODE at build time, so the production build drops this branch and the mock code.
  if (import.meta.env.MODE === 'mock') {
    const { installMockBackend } = await import('./mock/install.ts')
    installMockBackend()
  }
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

void boot()
