import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { ApiError } from '../api/client.ts'
import type { AuctionClosed, AuctionSnapshot, AuctionUpdate } from '../api/contract.ts'
import type { SocketLike } from '../api/transport.ts'
import { FALLBACK_POLL_MS, EXPECT_SEQ_TIMEOUT_MS } from '../config.ts'
import { createAuctionFeed, type AuctionFeed, type FeedState } from './auctionFeed.ts'

const ID = 'a1'
const ME = 'me'
const END = '2030-01-01T00:00:00.000Z'

class FakeSocket implements SocketLike {
  onopen: SocketLike['onopen'] = null
  onmessage: SocketLike['onmessage'] = null
  onclose: SocketLike['onclose'] = null
  onerror: SocketLike['onerror'] = null
  closed = false
  open() {
    this.onopen?.({})
  }
  send(data: unknown) {
    this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) })
  }
  drop() {
    this.onclose?.({ code: 1006 })
  }
  close() {
    this.closed = true
  }
}

function snap(seq: number, overrides: Partial<AuctionSnapshot> = {}): AuctionSnapshot {
  return {
    id: ID,
    title: 'Lamp',
    start_price: 1000,
    end_at: END,
    status: 'open',
    seq,
    high_amount: null,
    high_bidder_id: null,
    high_bidder_name: null,
    high_is_bot: null,
    recent_bids: [],
    ...overrides,
  }
}

function update(seq: number, amount: number, bidder = 'other', overrides: Partial<AuctionUpdate> = {}): AuctionUpdate {
  return {
    type: 'auction_update',
    auction_id: ID,
    seq,
    high_amount: amount,
    high_bidder_id: bidder,
    high_bidder_name: bidder === ME ? 'Me' : 'Riya',
    is_bot: bidder !== ME,
    end_at: END,
    status: 'open',
    ...overrides,
  }
}

let sockets: FakeSocket[]
let fetches: { resolve: (s: AuctionSnapshot) => void; reject: (e: unknown) => void }[]
let state: FeedState
let feed: AuctionFeed
let changes: number

function makeFeed() {
  feed = createAuctionFeed({
    auctionId: ID,
    socketUrl: 'ws://test/ws/auctions/a1',
    getAuction: () =>
      new Promise<AuctionSnapshot>((resolve, reject) => {
        fetches.push({ resolve, reject })
      }),
    openSocket: () => {
      const socket = new FakeSocket()
      sockets.push(socket)
      return socket
    },
    getBidderId: () => ME,
    isVisible: () => true,
    onChange: (next) => {
      state = next
      changes += 1
    },
  })
  feed.start()
  return feed
}

const lastSocket = () => sockets[sockets.length - 1]
/** Resolves the oldest pending snapshot request. */
async function answer(snapshot: AuctionSnapshot) {
  const pending = fetches.shift()
  if (!pending) throw new Error('no snapshot request pending')
  pending.resolve(snapshot)
  await vi.advanceTimersByTimeAsync(0)
}

/** Start, load a snapshot at seq, and open the socket (which resyncs at the same seq). */
async function live(seq = 1, overrides: Partial<AuctionSnapshot> = {}) {
  makeFeed()
  await answer(snap(seq, overrides))
  lastSocket().open()
  await answer(snap(seq, overrides))
}

beforeEach(() => {
  vi.useFakeTimers()
  sockets = []
  fetches = []
  changes = 0
})

afterEach(() => {
  feed.stop()
  vi.useRealTimers()
})

describe('loading and ordering', () => {
  test('opens the socket and fetches a snapshot on start, then fetches again once the socket opens', async () => {
    makeFeed()
    expect(sockets).toHaveLength(1)
    expect(fetches).toHaveLength(1)
    expect(state.phase).toBe('loading')

    await answer(snap(3, { high_amount: 2000, high_bidder_id: 'x', high_bidder_name: 'Ann', high_is_bot: false }))
    expect(state.phase).toBe('ready')
    expect(state.view?.highAmount).toBe(2000)
    expect(state.connection).toBe('connecting')

    lastSocket().open()
    expect(state.connection).toBe('live')
    expect(fetches).toHaveLength(1) // the post-subscription resync
  })

  test('applies events in seq order and ignores duplicates and stale ones', async () => {
    await live(1)
    lastSocket().send(update(2, 1500))
    lastSocket().send(update(2, 1500))
    lastSocket().send(update(1, 1200))
    lastSocket().send(update(3, 1700))

    expect(state.view?.seq).toBe(3)
    expect(state.view?.highAmount).toBe(1700)
    expect(state.view?.bids.map((b) => b.seq)).toEqual([3, 2])
    expect(fetches).toHaveLength(0)
  })

  test('buffers events that arrive before the snapshot and replays only newer ones', async () => {
    makeFeed()
    lastSocket().open()
    lastSocket().send(update(5, 1500))
    lastSocket().send(update(6, 1600))

    await answer(snap(5, { high_amount: 1500, high_bidder_id: 'other', high_bidder_name: 'Riya' }))
    // The open() asked for another snapshot after the first; serve it at the same seq.
    await answer(snap(5, { high_amount: 1500, high_bidder_id: 'other', high_bidder_name: 'Riya' }))

    expect(state.view?.seq).toBe(6)
    expect(state.view?.highAmount).toBe(1600)
  })

  test('ignores malformed messages, unknown types and other auctions', async () => {
    await live(1)
    lastSocket().send('not json')
    lastSocket().send({ type: 'ping' })
    lastSocket().send({ ...update(2, 1500), auction_id: 'other-auction' })
    lastSocket().send({ ...update(2, 1500), seq: 'two' })

    expect(state.view?.seq).toBe(1)
    expect(fetches).toHaveLength(0)
  })
})

describe('seq gaps', () => {
  test('a gap refetches the snapshot, buffering events until it lands', async () => {
    await live(1)
    lastSocket().send(update(3, 1700)) // seq 2 was missed
    expect(fetches).toHaveLength(1)
    lastSocket().send(update(4, 1800)) // arrives while the snapshot is in flight

    await answer(
      snap(3, {
        high_amount: 1700,
        high_bidder_id: 'other',
        high_bidder_name: 'Riya',
        recent_bids: [{ seq: 3, amount: 1700, bidder_name: 'Riya', is_bot: true, created_at: END }],
      }),
    )

    expect(state.view?.seq).toBe(4)
    expect(state.view?.highAmount).toBe(1800)
    expect(fetches).toHaveLength(0)
  })

  test('a snapshot older than the view never moves it backwards', async () => {
    await live(1)
    lastSocket().send(update(2, 1500))
    feed.resync()
    await answer(snap(1))

    expect(state.view?.seq).toBe(2)
    expect(state.view?.highAmount).toBe(1500)
  })

  test('a gap that is still open after the snapshot refetches again, with backoff', async () => {
    await live(1)
    lastSocket().send(update(5, 1900))
    await answer(snap(2, { high_amount: 1500 })) // the server's snapshot lags behind

    expect(state.view?.seq).toBe(2)
    expect(fetches).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(fetches).toHaveLength(1)
    await answer(snap(5, { high_amount: 1900 }))
    expect(state.view?.seq).toBe(5)
  })

  test('only bids that arrive after the first load are marked fresh', async () => {
    await live(1, {
      recent_bids: [{ seq: 1, amount: 1100, bidder_name: 'Ann', is_bot: false, created_at: END }],
      high_amount: 1100,
    })
    lastSocket().send(update(2, 1500))
    expect(state.view?.bids.map((b) => [b.seq, b.fresh])).toEqual([
      [2, true],
      [1, false],
    ])
  })

  test('history merges snapshot rows by seq', async () => {
    await live(1, {
      recent_bids: [{ seq: 1, amount: 1100, bidder_name: 'Ann', is_bot: false, created_at: END }],
      high_amount: 1100,
    })
    lastSocket().send(update(3, 1700))
    await answer(
      snap(3, {
        high_amount: 1700,
        recent_bids: [
          { seq: 3, amount: 1700, bidder_name: 'Riya', is_bot: true, created_at: END },
          { seq: 2, amount: 1500, bidder_name: 'Bo', is_bot: false, created_at: END },
        ],
      }),
    )
    expect(state.view?.bids.map((b) => [b.seq, b.amount])).toEqual([
      [3, 1700],
      [2, 1500],
      [1, 1100],
    ])
  })
})

describe('reconnecting', () => {
  test('reconnects with backoff and resyncs once the new socket opens', async () => {
    await live(1)
    lastSocket().drop()
    expect(state.connection).toBe('reconnecting')
    expect(sockets).toHaveLength(1)

    await vi.advanceTimersByTimeAsync(700)
    expect(sockets).toHaveLength(2)
    lastSocket().drop()
    await vi.advanceTimersByTimeAsync(700)
    expect(sockets).toHaveLength(2) // the second delay is longer
    await vi.advanceTimersByTimeAsync(700)
    expect(sockets).toHaveLength(3)

    lastSocket().open()
    expect(state.connection).toBe('live')
    expect(fetches).toHaveLength(1)
    await answer(snap(4, { high_amount: 2500, high_bidder_id: 'other', high_bidder_name: 'Riya' }))
    expect(state.view?.seq).toBe(4)
  })

  test('polls the snapshot while disconnected', async () => {
    await live(1)
    lastSocket().drop()
    // Keep the socket from coming back, so only the poll can refresh the page.
    for (let i = 0; i < 20 && fetches.length === 0; i++) {
      await vi.advanceTimersByTimeAsync(FALLBACK_POLL_MS / 4)
    }
    expect(fetches.length).toBeGreaterThan(0)
  })

  test('a 4404 close does not reconnect, even before the snapshot answers', async () => {
    makeFeed()
    lastSocket().onclose?.({ code: 4404 })
    await vi.advanceTimersByTimeAsync(2_000)
    expect(sockets).toHaveLength(1)
  })

  test('wake() reconnects at once instead of waiting out the backoff', async () => {
    await live(1)
    lastSocket().drop()
    feed.wake()
    expect(sockets).toHaveLength(2)
  })
})

describe('outbid notice', () => {
  test('fires when the high bidder changes from me to someone else', async () => {
    await live(1)
    lastSocket().send(update(2, 1500, ME))
    expect(state.outbid).toBeNull()
    lastSocket().send(update(3, 1700, 'other'))
    expect(state.outbid).toMatchObject({ byName: 'Riya', byIsBot: true, amount: 1700 })
  })

  test('does not fire between other bidders, for my own raises, or on first load', async () => {
    await live(1, { high_amount: 1500, high_bidder_id: 'other', high_bidder_name: 'Riya' })
    lastSocket().send(update(2, 1600, 'third'))
    lastSocket().send(update(3, 1700, ME))
    lastSocket().send(update(4, 1800, ME))
    expect(state.outbid).toBeNull()
  })

  test('also fires when a resync snapshot shows I lost the lead', async () => {
    await live(1)
    lastSocket().send(update(2, 1500, ME))
    lastSocket().send(update(4, 1900, 'other')) // gap: seq 3 missed
    await answer(snap(4, { high_amount: 1900, high_bidder_id: 'other', high_bidder_name: 'Riya', high_is_bot: true }))
    expect(state.outbid).toMatchObject({ amount: 1900, byName: 'Riya' })
  })
})

describe('closing and errors', () => {
  test('auction_closed records the winner and stops the socket for good', async () => {
    await live(1)
    lastSocket().send(update(2, 1500))
    const closed: AuctionClosed = {
      type: 'auction_closed',
      auction_id: ID,
      seq: 3,
      winner_id: 'other',
      winner_name: 'Riya',
      amount: 1500,
    }
    lastSocket().send(closed)

    expect(state.view?.status).toBe('closed')
    expect(state.view?.winner).toEqual({ id: 'other', name: 'Riya', amount: 1500 })
    expect(state.connection).toBe('closed')
    expect(lastSocket().closed).toBe(true)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(sockets).toHaveLength(1)
  })

  test('a closed snapshot needs no socket', async () => {
    makeFeed()
    await answer(snap(7, { status: 'closed' }))
    expect(state.connection).toBe('closed')
    expect(lastSocket().closed).toBe(true)
  })

  test('404 from the snapshot means not found', async () => {
    makeFeed()
    fetches.shift()!.reject(new ApiError('http', 404, 'Auction not found.'))
    await vi.advanceTimersByTimeAsync(0)
    expect(state.phase).toBe('not_found')
    expect(lastSocket().closed).toBe(true)
  })

  test('other load errors are shown and retried', async () => {
    makeFeed()
    fetches.shift()!.reject(new ApiError('network', null, "Couldn't reach the server."))
    await vi.advanceTimersByTimeAsync(0)
    expect(state.phase).toBe('loading')
    expect(state.loadError).toBe("Couldn't reach the server.")
    await vi.advanceTimersByTimeAsync(2_000)
    await answer(snap(1))
    expect(state.phase).toBe('ready')
    expect(state.loadError).toBeNull()
  })
})

describe('my bids', () => {
  test('expectSeq resyncs if my accepted bid has not shown up in time', async () => {
    await live(1)
    feed.expectSeq(2)
    await vi.advanceTimersByTimeAsync(EXPECT_SEQ_TIMEOUT_MS + 10)
    expect(fetches).toHaveLength(1)
  })

  test('expectSeq does nothing when the event arrived', async () => {
    await live(1)
    feed.expectSeq(2)
    lastSocket().send(update(2, 1500, ME))
    await vi.advanceTimersByTimeAsync(EXPECT_SEQ_TIMEOUT_MS + 10)
    expect(fetches).toHaveLength(0)
  })

  test('markMine flags my rows in the history', async () => {
    await live(1)
    feed.markMine(2)
    lastSocket().send(update(2, 1500, ME))
    lastSocket().send(update(3, 1600, 'other'))
    expect(state.view?.bids.map((b) => [b.seq, b.mine])).toEqual([
      [3, false],
      [2, true],
    ])
  })
})

test('stop() silences the feed', async () => {
  await live(1)
  feed.stop()
  const before = changes
  lastSocket().send(update(2, 1500))
  lastSocket().drop()
  await vi.advanceTimersByTimeAsync(60_000)
  expect(changes).toBe(before)
  expect(sockets).toHaveLength(1)
  expect(lastSocket().closed).toBe(true)
})

describe('history stays true to the server', () => {
  const ben = { seq: 1, amount: 1200, bidder_name: 'Ben', is_bot: true, created_at: END }

  test('a pause or reopen is not a new bid', async () => {
    // The snapshot shows Asha's 1500 still being saved; history has only Ben's 1200.
    await live(2, { high_amount: 1500, high_bidder_id: 'asha', high_bidder_name: 'Asha', recent_bids: [ben] })
    // Her save failed: the pause shows Ben's saved bid, then the auction reopens.
    lastSocket().send(update(3, 1200, 'ben', { status: 'paused', high_bidder_name: 'Ben' }))
    expect(state.view?.status).toBe('paused')
    lastSocket().send(update(4, 1200, 'ben', { status: 'open', high_bidder_name: 'Ben' }))

    expect(state.view?.seq).toBe(4)
    expect(state.view?.bids.map((b) => b.seq)).toEqual([1])
  })

  test('a bid still being saved when the snapshot was read still reaches the history', async () => {
    await live(2, { high_amount: 1500, high_bidder_id: 'other', high_bidder_name: 'Riya', recent_bids: [ben] })
    // Its event arrives once it is saved, at a seq the view already shows.
    lastSocket().send(update(2, 1500))
    expect(fetches).toHaveLength(1)
    await answer(
      snap(2, {
        high_amount: 1500,
        recent_bids: [{ seq: 2, amount: 1500, bidder_name: 'Riya', is_bot: true, created_at: END }, ben],
      }),
    )
    expect(state.view?.bids.map((b) => b.seq)).toEqual([2, 1])
  })

  test('auction_closed with no winner clears the high bid', async () => {
    await live(1, { high_amount: 1500, high_bidder_id: 'asha', high_bidder_name: 'Asha' })
    lastSocket().send({ type: 'auction_closed', auction_id: ID, seq: 2, winner_id: null, winner_name: null, amount: null })
    expect(state.view?.highAmount).toBeNull()
    expect(state.view?.winner).toEqual({ id: null, name: null, amount: null })
  })

  test('auction_closed keeps a winning bot labelled when the page showed someone else', async () => {
    // The page saw Asha's 1500 being saved; the close fenced it off and Ben (a bot) won.
    await live(2, { high_amount: 1500, high_bidder_id: 'asha', high_bidder_name: 'Asha', high_is_bot: false, recent_bids: [ben] })
    lastSocket().send({ type: 'auction_closed', auction_id: ID, seq: 3, winner_id: 'ben', winner_name: 'Ben', amount: 1200 })
    expect(state.view?.highAmount).toBe(1200)
    expect(state.view?.highIsBot).toBe(true)
  })
})
