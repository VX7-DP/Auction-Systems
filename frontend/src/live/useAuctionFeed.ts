import { useEffect, useMemo, useRef, useState } from 'react'
import { getAuction, wsUrl } from '../api/client.ts'
import { getTransport } from '../api/transport.ts'
import { createAuctionFeed, type AuctionFeed, type FeedState } from './auctionFeed.ts'

const INITIAL: FeedState = { phase: 'loading', view: null, connection: 'connecting', outbid: null, loadError: null }

export interface FeedActions {
  resync(): void
  expectSeq(seq: number): void
  markMine(seq: number): void
}

/** Live state of one auction. The feed is created inside the effect, so StrictMode's
 * mount, unmount, mount cycle simply makes and stops a throwaway feed. */
export function useAuctionFeed(auctionId: string, bidderId: string | null): [FeedState, FeedActions] {
  const [state, setState] = useState<FeedState>(INITIAL)
  const feedRef = useRef<AuctionFeed | null>(null)
  const bidderRef = useRef(bidderId)

  useEffect(() => {
    bidderRef.current = bidderId
  }, [bidderId])

  useEffect(() => {
    const feed = createAuctionFeed({
      auctionId,
      socketUrl: wsUrl(auctionId),
      getAuction,
      openSocket: (url) => getTransport().openSocket(url),
      getBidderId: () => bidderRef.current,
      onChange: setState,
    })
    feedRef.current = feed
    feed.start()
    const wake = () => feed.wake()
    const onVisibility = () => {
      if (document.visibilityState === 'visible') feed.wake()
    }
    window.addEventListener('online', wake)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      feed.stop()
      window.removeEventListener('online', wake)
      document.removeEventListener('visibilitychange', onVisibility)
      if (feedRef.current === feed) feedRef.current = null
    }
  }, [auctionId])

  const actions = useMemo<FeedActions>(
    () => ({
      resync: () => feedRef.current?.resync(),
      expectSeq: (seq) => feedRef.current?.expectSeq(seq),
      markMine: (seq) => feedRef.current?.markMine(seq),
    }),
    [],
  )
  return [state, actions]
}
