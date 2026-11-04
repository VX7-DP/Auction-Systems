import { afterEach, describe, expect, test, vi } from 'vitest'
import {
  ApiError,
  createAuction,
  getAuction,
  getSession,
  placeBid,
  signIn,
  wsUrl,
} from './client.ts'
import { browserTransport, setTransport, type TransportRequest } from './transport.ts'

type Reply = { status: number; body?: unknown } | Error | 'hang'

function fakeTransport(reply: Reply) {
  const calls: { url: string; init?: TransportRequest }[] = []
  setTransport({
    fetch: (url, init) => {
      calls.push({ url, init })
      if (reply === 'hang') {
        return new Promise((_, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          )
        })
      }
      if (reply instanceof Error) return Promise.reject(reply)
      return Promise.resolve({
        ok: reply.status >= 200 && reply.status < 300,
        status: reply.status,
        json: () => Promise.resolve(reply.body),
      })
    },
    openSocket: () => {
      throw new Error('not used')
    },
  })
  return calls
}

afterEach(() => {
  setTransport(browserTransport)
  vi.useRealTimers()
})

describe('REST client', () => {
  test('signIn posts the display name as JSON', async () => {
    const calls = fakeTransport({ status: 200, body: { bidder_id: 'b1', display_name: 'Asha' } })

    expect(await signIn('Asha')).toEqual({ bidder_id: 'b1', display_name: 'Asha' })
    expect(calls[0].url).toBe('/api/session')
    expect(calls[0].init?.method).toBe('POST')
    expect(calls[0].init?.headers?.['Content-Type']).toBe('application/json')
    expect(JSON.parse(calls[0].init?.body ?? '')).toEqual({ display_name: 'Asha' })
  })

  test('getSession returns null when signed out (401)', async () => {
    fakeTransport({ status: 401, body: { detail: 'Sign in first.' } })

    expect(await getSession()).toBeNull()
  })

  test('HTTP errors carry the status and the server detail', async () => {
    fakeTransport({ status: 404, body: { detail: 'Auction not found.' } })

    const error = await getAuction('x').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ kind: 'http', status: 404, message: 'Auction not found.' })
  })

  test('422 validation details become readable text', async () => {
    fakeTransport({
      status: 422,
      body: { detail: [{ loc: ['body', 'title'], msg: 'String should have at least 1 character' }] },
    })

    const error = await createAuction({ title: '', start_price: 0, end_at: 'x' }, 'pw').catch(
      (e: unknown) => e,
    )
    expect(error).toMatchObject({ status: 422, message: 'title: String should have at least 1 character' })
  })

  test('createAuction sends the admin password header', async () => {
    const calls = fakeTransport({ status: 200, body: { id: 'a1' } })

    await createAuction({ title: 'Lamp', start_price: 100, end_at: '2030-01-01T00:00:00Z' }, 'secret')
    expect(calls[0].url).toBe('/api/admin/auctions')
    expect(calls[0].init?.headers?.['X-Admin-Password']).toBe('secret')
  })

  test('placeBid encodes the auction id and returns the result', async () => {
    const calls = fakeTransport({ status: 200, body: { bid_id: 'b', status: 'accepted', seq: 3 } })

    const result = await placeBid('a/1', { bid_id: 'b', amount: 500 })
    expect(result).toEqual({ bid_id: 'b', status: 'accepted', seq: 3 })
    expect(calls[0].url).toBe('/api/auctions/a%2F1/bids')
  })

  test('a failed connection is a network error', async () => {
    fakeTransport(new TypeError('Failed to fetch'))

    await expect(getAuction('a')).rejects.toMatchObject({ kind: 'network' })
  })

  test('a request that never answers times out', async () => {
    vi.useFakeTimers()
    fakeTransport('hang')

    const pending = placeBid('a', { bid_id: 'b', amount: 1 }).catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(await pending).toMatchObject({ kind: 'timeout' })
  })
})

describe('wsUrl', () => {
  test('uses wss on https and ws on http, same host', () => {
    expect(wsUrl('a1', { protocol: 'https:', host: 'auction.example' })).toBe(
      'wss://auction.example/ws/auctions/a1',
    )
    expect(wsUrl('a1', { protocol: 'http:', host: 'localhost:5173' })).toBe(
      'ws://localhost:5173/ws/auctions/a1',
    )
  })
})
