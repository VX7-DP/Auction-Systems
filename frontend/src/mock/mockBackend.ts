// An in-memory stand-in for the backend, speaking the CLAUDE.md section 10 contract:
// the same paths, status codes, JSON and WebSocket messages. It plugs in at the
// transport boundary (api/transport.ts), so the real client code runs against it.
// Used by the tests and by `npm run dev:mock`. Never part of the production build.
import {
  MAX_AMOUNT,
  type AuctionEvent,
  type AuctionSnapshot,
  type AuctionStatus,
  type AuctionSummary,
  type Bidder,
  type BidResult,
  type RecentBid,
} from '../api/contract.ts'
import type { SocketLike, Transport, TransportRequest, TransportResponse } from '../api/transport.ts'
import { newBidId } from '../lib/bidId.ts'
import { nextBid } from '../lib/money.ts'

export interface MockBackendOptions {
  adminPassword?: string
  /** Simulated network delay for requests and socket opens. */
  latencyMs?: number
  /** Keep the guest session in localStorage under this key, like a cookie would. */
  sessionStorageKey?: string
}

export interface MockBid {
  bidId?: string
  bidderId: string
  bidderName: string
  amount: number
  isBot?: boolean
}

interface StoredAuction {
  id: string
  title: string
  startPrice: number
  endAtMs: number
  status: AuctionStatus
  seq: number
  highAmount: number | null
  highBidderId: string | null
  highBidderName: string | null
  highIsBot: boolean | null
  bids: RecentBid[]
  results: Map<string, BidResult>
}

export interface LoggedRequest {
  method: string
  path: string
  body: unknown
}

const RECENT_BIDS = 20
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const AWARE_ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/
const BOT_NAMES = ['Riya', 'Marco', 'Aiko', 'Sam', 'Lena', 'Kofi']

type Json = { status: number; body: unknown }

const json = (status: number, body: unknown): Json => ({ status, body })
const invalid = (field: string, msg: string): Json =>
  json(422, { detail: [{ loc: ['body', field], msg, type: 'value_error' }] })

export function createMockBackend(options: MockBackendOptions = {}) {
  const adminPassword = options.adminPassword ?? 'admin'
  const latencyMs = options.latencyMs ?? 0
  const auctions = new Map<string, StoredAuction>()
  const sockets = new Set<MockSocket>()
  const dropNext = new Set<string>()
  const requests: LoggedRequest[] = []
  const timers: ReturnType<typeof setTimeout>[] = []
  const intervals: ReturnType<typeof setInterval>[] = []
  let session: Bidder | null = loadSession()

  function loadSession(): Bidder | null {
    if (!options.sessionStorageKey) return null
    try {
      const saved = localStorage.getItem(options.sessionStorageKey)
      return saved ? (JSON.parse(saved) as Bidder) : null
    } catch {
      return null
    }
  }

  function saveSession(bidder: Bidder) {
    session = bidder
    if (!options.sessionStorageKey) return
    try {
      localStorage.setItem(options.sessionStorageKey, JSON.stringify(bidder))
    } catch {
      // Storage can be unavailable (private mode); the session then lasts until reload.
    }
  }

  function later(fn: () => void) {
    if (latencyMs > 0) setTimeout(fn, latencyMs)
    else queueMicrotask(fn)
  }

  // --- state and events ----------------------------------------------------------

  function snapshot(id: string): AuctionSnapshot {
    const a = auctions.get(id)
    if (!a) throw new Error(`No auction ${id}`)
    return {
      id: a.id,
      title: a.title,
      start_price: a.startPrice,
      end_at: new Date(a.endAtMs).toISOString(),
      status: a.status,
      seq: a.seq,
      high_amount: a.highAmount,
      high_bidder_id: a.highBidderId,
      high_bidder_name: a.highBidderName,
      high_is_bot: a.highIsBot,
      recent_bids: a.bids.slice(-RECENT_BIDS).reverse(),
    }
  }

  function summary(a: StoredAuction): AuctionSummary {
    return {
      id: a.id,
      title: a.title,
      status: a.status,
      high_amount: a.highAmount,
      end_at: new Date(a.endAtMs).toISOString(),
    }
  }

  function publish(a: StoredAuction, event: AuctionEvent) {
    if (dropNext.delete(a.id)) return
    const data = JSON.stringify(event)
    for (const socket of sockets) {
      if (socket.auctionId === a.id) socket.deliver(data)
    }
  }

  function publishUpdate(a: StoredAuction) {
    publish(a, {
      type: 'auction_update',
      auction_id: a.id,
      seq: a.seq,
      high_amount: a.highAmount,
      high_bidder_id: a.highBidderId,
      high_bidder_name: a.highBidderName,
      is_bot: a.highIsBot,
      end_at: new Date(a.endAtMs).toISOString(),
      status: a.status,
    })
  }

  function createAuction(input: { title: string; startPrice: number; endAt: number | string | Date }) {
    const a: StoredAuction = {
      id: newBidId(),
      title: input.title,
      startPrice: input.startPrice,
      endAtMs: new Date(input.endAt).getTime(),
      status: 'open',
      seq: 0,
      highAmount: null,
      highBidderId: null,
      highBidderName: null,
      highIsBot: null,
      bids: [],
      results: new Map(),
    }
    auctions.set(a.id, a)
    return snapshot(a.id)
  }

  /** The bid decision, mirroring the Lua script in CLAUDE.md section 4. */
  function bid(auctionId: string, input: MockBid): BidResult {
    const bidId = input.bidId ?? newBidId()
    const a = auctions.get(auctionId)
    if (!a) return { bid_id: bidId, status: 'rejected', reason: 'not_found' }
    const previous = a.results.get(bidId)
    if (previous) return previous

    let result: BidResult
    if (a.status === 'closed' || Date.now() >= a.endAtMs) {
      result = { bid_id: bidId, status: 'rejected', reason: 'closed' }
    } else if (a.status === 'paused') {
      result = { bid_id: bidId, status: 'rejected', reason: 'paused' }
    } else if (input.amount <= (a.highAmount ?? a.startPrice)) {
      result = { bid_id: bidId, status: 'rejected', reason: 'too_low' }
    } else {
      a.seq += 1
      a.highAmount = input.amount
      a.highBidderId = input.bidderId
      a.highBidderName = input.bidderName
      a.highIsBot = input.isBot ?? false
      a.bids.push({
        seq: a.seq,
        amount: input.amount,
        bidder_name: input.bidderName,
        is_bot: input.isBot ?? false,
        created_at: new Date().toISOString(),
      })
      result = { bid_id: bidId, status: 'accepted', seq: a.seq }
      publishUpdate(a)
    }
    a.results.set(bidId, result)
    return result
  }

  function setStatus(auctionId: string, status: 'open' | 'paused') {
    const a = auctions.get(auctionId)
    if (!a || a.status === 'closed' || a.status === status) return
    a.status = status
    a.seq += 1
    publishUpdate(a)
  }

  function closeNow(auctionId: string) {
    const a = auctions.get(auctionId)
    if (!a || a.status === 'closed') return
    a.endAtMs = Math.min(a.endAtMs, Date.now())
    a.status = 'closed'
    a.seq += 1
    publish(a, {
      type: 'auction_closed',
      auction_id: a.id,
      seq: a.seq,
      winner_id: a.highBidderId,
      winner_name: a.highBidderName,
      amount: a.highAmount,
    })
  }

  /** The close sweeper: closes every auction whose end time has passed. */
  function closeDue() {
    for (const a of auctions.values()) {
      if (a.status !== 'closed' && Date.now() >= a.endAtMs) closeNow(a.id)
    }
  }

  // --- REST ------------------------------------------------------------------------

  function route(method: string, path: string, body: unknown, headers: Record<string, string>): Json {
    if (path === '/api/health' && method === 'GET') {
      return json(200, { status: 'ok', postgres: 'up', redis: 'up' })
    }
    if (path === '/api/session') {
      if (method === 'GET') return session ? json(200, session) : json(401, { detail: 'Sign in first.' })
      if (method === 'POST') {
        const name = (body as { display_name?: unknown } | null)?.display_name
        const trimmed = typeof name === 'string' ? name.trim() : ''
        if (trimmed.length < 1 || trimmed.length > 40) {
          return invalid('display_name', 'Display name must be 1 to 40 characters')
        }
        saveSession({ bidder_id: session?.bidder_id ?? newBidId(), display_name: trimmed })
        return json(200, session)
      }
    }
    if (path === '/api/auctions' && method === 'GET') {
      return json(200, [...auctions.values()].map(summary))
    }
    if (path === '/api/admin/auctions' && method === 'POST') {
      if (headerValue(headers, 'x-admin-password') !== adminPassword) {
        return json(401, { detail: 'Wrong admin password.' })
      }
      return createFromRequest(body)
    }
    const auctionMatch = /^\/api\/auctions\/([^/]+)(\/bids)?$/.exec(path)
    if (auctionMatch) {
      const id = decodeURIComponent(auctionMatch[1])
      if (!UUID.test(id)) return invalid('auction_id', 'Input should be a valid UUID')
      if (!auctionMatch[2] && method === 'GET') {
        return auctions.has(id) ? json(200, snapshot(id)) : json(404, { detail: 'Auction not found.' })
      }
      if (auctionMatch[2] && method === 'POST') {
        if (!session) return json(401, { detail: 'Sign in first.' })
        const input = (body ?? {}) as { bid_id?: unknown; amount?: unknown }
        if (typeof input.bid_id !== 'string' || !UUID.test(input.bid_id)) {
          return invalid('bid_id', 'Input should be a valid UUID')
        }
        if (!isAmount(input.amount) || input.amount <= 0) {
          return invalid('amount', 'Input should be a positive integer')
        }
        return json(
          200,
          bid(id, {
            bidId: input.bid_id,
            bidderId: session.bidder_id,
            bidderName: session.display_name,
            amount: input.amount,
          }),
        )
      }
    }
    return json(404, { detail: 'Not Found' })
  }

  function createFromRequest(body: unknown): Json {
    const input = (body ?? {}) as { title?: unknown; start_price?: unknown; end_at?: unknown }
    const title = typeof input.title === 'string' ? input.title.trim() : ''
    if (title.length < 1 || title.length > 200) return invalid('title', 'Title must be 1 to 200 characters')
    if (!isAmount(input.start_price)) return invalid('start_price', 'Input should be a whole number of 0 or more')
    if (typeof input.end_at !== 'string' || !AWARE_ISO.test(input.end_at) || Number.isNaN(Date.parse(input.end_at))) {
      return invalid('end_at', 'Input should be a date and time with a time zone')
    }
    return json(200, createAuction({ title, startPrice: input.start_price, endAt: input.end_at }))
  }

  const transport: Transport = {
    fetch(url: string, init: TransportRequest = {}): Promise<TransportResponse> {
      const method = (init.method ?? 'GET').toUpperCase()
      const path = new URL(url, 'http://mock.local').pathname
      let body: unknown = null
      try {
        body = init.body ? JSON.parse(init.body) : null
      } catch {
        body = null
      }
      requests.push({ method, path, body })
      return new Promise((resolve, reject) => {
        if (init.signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'))
        init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        later(() => {
          const result = route(method, path, body, init.headers ?? {})
          const copy = JSON.parse(JSON.stringify(result.body ?? null)) as unknown
          resolve({
            ok: result.status >= 200 && result.status < 300,
            status: result.status,
            json: () => Promise.resolve(copy),
          })
        })
      })
    },
    openSocket(url: string): SocketLike {
      return new MockSocket(url)
    },
  }

  // --- WebSocket -------------------------------------------------------------------

  class MockSocket implements SocketLike {
    onopen: SocketLike['onopen'] = null
    onmessage: SocketLike['onmessage'] = null
    onclose: SocketLike['onclose'] = null
    onerror: SocketLike['onerror'] = null
    readonly auctionId: string
    private state: 'connecting' | 'open' | 'closing' | 'closed' = 'connecting'

    constructor(url: string) {
      const match = /\/ws\/auctions\/([^/?#]+)/.exec(url)
      this.auctionId = match ? decodeURIComponent(match[1]) : ''
      later(() => {
        if (this.state !== 'connecting') return
        if (!auctions.has(this.auctionId)) {
          this.finish(1008)
          return
        }
        this.state = 'open'
        sockets.add(this)
        this.onopen?.({})
      })
    }

    deliver(data: string) {
      queueMicrotask(() => {
        if (this.state === 'open') this.onmessage?.({ data })
      })
    }

    close() {
      if (this.state === 'closing' || this.state === 'closed') return
      this.state = 'closing'
      queueMicrotask(() => this.finish(1000))
    }

    /** The server or network drops the connection. */
    drop() {
      if (this.state !== 'closed') this.finish(1006)
    }

    private finish(code: number) {
      if (this.state === 'closed') return
      this.state = 'closed'
      sockets.delete(this)
      this.onclose?.({ code })
    }
  }

  // --- simulation (dev mode) -------------------------------------------------------

  const botIds = new Map(BOT_NAMES.map((name) => [name, newBidId()]))

  function botBid() {
    const open = [...auctions.values()].filter((a) => a.status === 'open' && a.endAtMs > Date.now())
    if (open.length === 0) return
    const a = open[Math.floor(Math.random() * open.length)]
    const names = BOT_NAMES.filter((name) => name !== a.highBidderName)
    const name = names[Math.floor(Math.random() * names.length)]
    const amount = nextBid(a.highAmount, a.startPrice, 2)
    bid(a.id, { bidderId: botIds.get(name)!, bidderName: name, amount, isBot: true })
  }

  return {
    transport,
    requests,
    createAuction,
    bid,
    snapshot,
    pause: (id: string) => setStatus(id, 'paused'),
    resume: (id: string) => setStatus(id, 'open'),
    closeNow,
    closeDue,
    /** The next event for this auction is lost, like a missed Pub/Sub message. */
    dropNextEvent: (id: string) => void dropNext.add(id),
    /** Drop every open socket (for one auction, or all), like a network blip. */
    disconnectSockets(id?: string) {
      for (const socket of [...sockets]) {
        if (!id || socket.auctionId === id) socket.drop()
      }
    },
    signInAs(displayName: string): Bidder {
      saveSession({ bidder_id: session?.bidder_id ?? newBidId(), display_name: displayName })
      return session!
    },
    /** Simulated bidders plus the close sweeper, for `npm run dev:mock`. */
    startSimulation(botEveryMs: [number, number] = [2500, 6000]) {
      const scheduleBot = () => {
        const [min, max] = botEveryMs
        timers.push(
          setTimeout(() => {
            botBid()
            scheduleBot()
          }, min + Math.random() * (max - min)),
        )
      }
      scheduleBot()
      intervals.push(setInterval(closeDue, 500))
    },
    stop() {
      for (const timer of timers) clearTimeout(timer)
      for (const interval of intervals) clearInterval(interval)
      timers.length = 0
      intervals.length = 0
    },
  }
}

export type MockBackend = ReturnType<typeof createMockBackend>

function headerValue(headers: Record<string, string>, name: string): string | undefined {
  const key = Object.keys(headers).find((k) => k.toLowerCase() === name)
  return key === undefined ? undefined : headers[key]
}

function isAmount(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= MAX_AMOUNT
}
