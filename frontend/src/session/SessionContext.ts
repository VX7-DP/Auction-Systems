import { createContext, useContext } from 'react'
import type { Bidder } from '../api/contract.ts'

export interface SessionValue {
  status: 'loading' | 'signed_out' | 'signed_in'
  bidder: Bidder | null
  /** POST /api/session; resolves with the bidder or throws an ApiError. */
  signIn(displayName: string): Promise<Bidder>
  /** Re-read the session, e.g. after the server answered 401. */
  refresh(): Promise<void>
}

export const SessionContext = createContext<SessionValue | null>(null)

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession needs a SessionProvider')
  return value
}
