import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { getSession, signIn as postSession } from '../api/client.ts'
import type { Bidder } from '../api/contract.ts'
import { SessionContext, type SessionValue } from './SessionContext.ts'

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionValue['status']>('loading')
  const [bidder, setBidder] = useState<Bidder | null>(null)

  const refresh = useCallback(
    () =>
      getSession().then(
        (current) => {
          setBidder(current)
          setStatus(current ? 'signed_in' : 'signed_out')
        },
        () => {
          // The server is unreachable: treat the guest as signed out; signing in retries.
          setBidder(null)
          setStatus('signed_out')
        },
      ),
    [],
  )

  const signIn = useCallback(async (displayName: string) => {
    const signedIn = await postSession(displayName)
    setBidder(signedIn)
    setStatus('signed_in')
    return signedIn
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const value = useMemo(() => ({ status, bidder, signIn, refresh }), [status, bidder, signIn, refresh])
  return <SessionContext value={value}>{children}</SessionContext>
}
