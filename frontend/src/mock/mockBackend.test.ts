import { beforeEach, describe, expect, test } from 'vitest'
import type { AuctionEvent, AuctionSnapshot, BidResult } from '../api/contract.ts'
import { createMockBackend, type MockBackend } from './mockBackend.ts'

const MINUTE = 60_000
let backend: MockBackend

async function call(method: string, url: string, body?: unknown, headers: Record<string, string> = {}) {
  const response = await backend.transport.fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return { status: response.status, body: (await response.json()) as any }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function listen(auctionId: string) {
  const events: AuctionEvent[] = []
  const socket = backend.transport.openSocket(`ws://test/ws/auctions/${auctionId}`)
  const closes: (number | undefined)[] = []
  socket.onmessage = (event) => events.push(JSON.parse(String(event.data)))
  socket.onclose = (event) => closes.push(event.code)
  return { events, closes, socket }
}

function newAuction(minutes = 5, startPrice = 100000) {
  return backend.createAuction({ title: 'Lamp', startPrice, endAt: Date.now() + minutes * MINUTE })
}

const bidder = { bidderId: '00000000-0000-4000-8000-000000000001', bidderName: 'Ben' }

beforeEach(() => {
  backend = createMockBackend({ adminPassword: 'pw' })
})

describe('session', () => {
  test('signs in with a trimmed name and reads it back', async () => {
    expect((await call('GET', '/api/session')).status).toBe(401)
    const signedIn = await call('POST', '/api/session', { display_name: '  Asha ' })
    expect(signedIn.status).toBe(200)
    expect(signedIn.body.display_name).toBe('Asha')
    expect((await call('GET', '/api/session')).body).toEqual(signedIn.body)
  })

  test('signing in again keeps the bidder id', async () => {
    const first = (await call('POST', '/api/session', { display_name: 'Asha' })).body
    const second = (await call('POST', '/api/session', { display_name: 'Asha K' })).body
    expect(second).toEqual({ bidder_id: first.bidder_id, display_name: 'Asha K' })
  })

  test.each(['', '   ', 'x'.repeat(41)])('rejects display name %j with 422', async (name) => {
    expect((await call('POST', '/api/session', { display_name: name })).status).toBe(422)
  })
})

describe('auctions', () => {
  test('lists summaries and serves snapshots with null high fields before any bid', async () => {
    const auction = newAuction()
    const list = await call('GET', '/api/auctions')
    expect(list.body).toEqual([
      { id: auction.id, title: 'Lamp', status: 'open', high_amount: null, end_at: auction.end_at },
    ])
    const snapshot = (await call('GET', `/api/auctions/${auction.id}`)).body as AuctionSnapshot
    expect(snapshot).toMatchObject({
      seq: 0,
      start_price: 100000,
      high_amount: null,
      high_bidder_id: null,
      high_bidder_name: null,
      high_is_bot: null,
      recent_bids: [],
    })
    expect(snapshot.end_at).toMatch(/Z$/)
  })

  test('unknown auction is 404, a malformed id is 422', async () => {
    expect((await call('GET', '/api/auctions/00000000-0000-4000-8000-00000000abcd')).status).toBe(404)
    expect((await call('GET', '/api/auctions/not-a-uuid')).status).toBe(422)
  })
})

describe('bids', () => {
  test('needs a session', async () => {
    const auction = newAuction()
    const response = await call('POST', `/api/auctions/${auction.id}/bids`, {
      bid_id: crypto.randomUUID(),
      amount: 200000,
    })
    expect(response.status).toBe(401)
  })

  test.each([0, -1, 1.5, '100', true])('rejects amount %j with 422', async (amount) => {
    await call('POST', '/api/session', { display_name: 'Asha' })
    const auction = newAuction()
    const response = await call('POST', `/api/auctions/${auction.id}/bids`, {
      bid_id: crypto.randomUUID(),
      amount,
    })
    expect(response.status).toBe(422)
  })

  test('accepts a higher bid, gives it the next seq and leaves out reason', async () => {
    const asha = (await call('POST', '/api/session', { display_name: 'Asha' })).body
    const auction = newAuction()
    const bidId = crypto.randomUUID()
    const response = await call('POST', `/api/auctions/${auction.id}/bids`, { bid_id: bidId, amount: 100100 })
    expect(response.body).toEqual({ bid_id: bidId, status: 'accepted', seq: 1 })

    const snapshot = backend.snapshot(auction.id)
    expect(snapshot).toMatchObject({
      seq: 1,
      high_amount: 100100,
      high_bidder_id: asha.bidder_id,
      high_bidder_name: 'Asha',
      high_is_bot: false,
    })
    expect(snapshot.recent_bids[0]).toMatchObject({ seq: 1, amount: 100100, bidder_name: 'Asha', is_bot: false })
  })

  test('a bid must beat the start price and then the high bid', async () => {
    const auction = newAuction()
    const reject = (amount: number): BidResult => backend.bid(auction.id, { ...bidder, amount })
    expect(reject(100000)).toMatchObject({ status: 'rejected', reason: 'too_low' })
    expect(backend.bid(auction.id, { ...bidder, amount: 120000 }).status).toBe('accepted')
    // One raise from 1,200.00 reaches at most 2,400.00, like the server's too_high rule.
    expect(reject(240001)).toMatchObject({ status: 'rejected', reason: 'too_high' })
    expect(backend.bid(auction.id, { ...bidder, amount: 240000 }).status).toBe('accepted')
    expect(reject(120000)).toMatchObject({ status: 'rejected', reason: 'too_low' })
    expect(reject(110000)).toMatchObject({ status: 'rejected', reason: 'too_low' })
  })

  test('a repeated bid_id returns the original result without a new seq', async () => {
    const auction = newAuction()
    const bidId = crypto.randomUUID()
    const first = backend.bid(auction.id, { ...bidder, bidId, amount: 150000 })
    const again = backend.bid(auction.id, { ...bidder, bidId, amount: 150000 })
    expect(again).toEqual(first)
    expect(backend.snapshot(auction.id).seq).toBe(1)
  })

  test('rejects bids on unknown, paused and finished auctions', async () => {
    expect(backend.bid('00000000-0000-4000-8000-00000000abcd', { ...bidder, amount: 1 })).toMatchObject({
      status: 'rejected',
      reason: 'not_found',
    })
    const paused = newAuction()
    backend.pause(paused.id)
    expect(backend.bid(paused.id, { ...bidder, amount: 999999 })).toMatchObject({ reason: 'paused' })
    const ended = newAuction(-1)
    expect(backend.bid(ended.id, { ...bidder, amount: 999999 })).toMatchObject({ reason: 'closed' })
  })
})

describe('live events', () => {
  test('publishes every state change in seq order with section 10 shapes', async () => {
    const auction = newAuction(5)
    const { events } = listen(auction.id)
    await flush()

    backend.bid(auction.id, { ...bidder, amount: 110000, isBot: true })
    backend.pause(auction.id)
    backend.resume(auction.id)
    backend.closeNow(auction.id)
    await flush()

    expect(events.map((event) => [event.type, event.seq])).toEqual([
      ['auction_update', 1],
      ['auction_update', 2],
      ['auction_update', 3],
      ['auction_closed', 4],
    ])
    expect(events[0]).toEqual({
      type: 'auction_update',
      auction_id: auction.id,
      seq: 1,
      high_amount: 110000,
      high_bidder_id: bidder.bidderId,
      high_bidder_name: 'Ben',
      is_bot: true,
      end_at: auction.end_at,
      status: 'open',
    })
    expect(events[1]).toMatchObject({ status: 'paused' })
    expect(events[3]).toEqual({
      type: 'auction_closed',
      auction_id: auction.id,
      seq: 4,
      winner_id: bidder.bidderId,
      winner_name: 'Ben',
      amount: 110000,
    })
  })

  test('closeDue closes auctions whose end time has passed, once', async () => {
    const ended = newAuction(-1)
    const running = newAuction(5)
    const { events } = listen(ended.id)
    await flush()

    backend.closeDue()
    backend.closeDue()
    await flush()

    expect(events).toEqual([
      { type: 'auction_closed', auction_id: ended.id, seq: 1, winner_id: null, winner_name: null, amount: null },
    ])
    expect(backend.snapshot(running.id).status).toBe('open')
  })

  test('dropNextEvent loses one message, like a missed Pub/Sub delivery', async () => {
    const auction = newAuction()
    const { events } = listen(auction.id)
    await flush()

    backend.dropNextEvent(auction.id)
    backend.bid(auction.id, { ...bidder, amount: 110000 })
    backend.bid(auction.id, { ...bidder, amount: 120000 })
    await flush()

    expect(events.map((event) => event.seq)).toEqual([2])
  })

  test('disconnectSockets drops connections; later events miss them', async () => {
    const auction = newAuction()
    const { events, closes } = listen(auction.id)
    await flush()

    backend.disconnectSockets(auction.id)
    backend.bid(auction.id, { ...bidder, amount: 110000 })
    await flush()

    expect(closes).toEqual([1006])
    expect(events).toEqual([])
  })

  test('a socket for an unknown auction is closed', async () => {
    const { closes } = listen('00000000-0000-4000-8000-00000000abcd')
    await flush()
    expect(closes).toEqual([1008])
  })
})

describe('admin', () => {
  const body = () => ({ title: ' Lamp ', start_price: 5000, end_at: new Date(Date.now() + MINUTE).toISOString() })

  test('needs the admin password', async () => {
    expect((await call('POST', '/api/admin/auctions', body())).status).toBe(401)
    expect((await call('POST', '/api/admin/auctions', body(), { 'X-Admin-Password': 'nope' })).status).toBe(401)
  })

  test('creates an auction and returns its snapshot', async () => {
    const response = await call('POST', '/api/admin/auctions', body(), { 'X-Admin-Password': 'pw' })
    expect(response.status).toBe(200)
    expect(response.body).toMatchObject({ title: 'Lamp', start_price: 5000, status: 'open', seq: 0 })
    expect((await call('GET', '/api/auctions')).body).toHaveLength(1)
  })

  test.each([
    { title: '' },
    { start_price: -1 },
    { start_price: 1.5 },
    { end_at: '2030-01-01T00:00:00' }, // no time zone
    { end_at: 'soon' },
  ])('rejects %j with 422', async (change) => {
    const response = await call('POST', '/api/admin/auctions', { ...body(), ...change }, { 'X-Admin-Password': 'pw' })
    expect(response.status).toBe(422)
  })
})
