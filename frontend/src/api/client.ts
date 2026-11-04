// REST calls from CLAUDE.md section 10. Every path is same-origin under /api.
import { BID_TIMEOUT_MS, REQUEST_TIMEOUT_MS } from '../config.ts'
import type {
  AuctionCreate,
  AuctionSnapshot,
  AuctionSummary,
  BidCreate,
  Bidder,
  BidResult,
} from './contract.ts'
import { getTransport } from './transport.ts'

export type ApiErrorKind = 'http' | 'network' | 'timeout'

/**
 * A failed request. kind 'network' and 'timeout' mean the outcome is unknown: the
 * server may or may not have acted on it.
 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null

  constructor(kind: ApiErrorKind, status: number | null, message: string) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  headers?: Record<string, string>
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? REQUEST_TIMEOUT_MS)
  try {
    let response
    try {
      response = await getTransport().fetch(path, {
        method: options.method ?? 'GET',
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: controller.signal,
      })
    } catch {
      if (controller.signal.aborted) {
        throw new ApiError('timeout', null, 'The server took too long to answer.')
      }
      throw new ApiError('network', null, "Couldn't reach the server.")
    }
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      throw new ApiError('http', response.status, detailMessage(body, response.status))
    }
    return body as T
  } finally {
    clearTimeout(timer)
  }
}

/** FastAPI errors are {"detail": "text"} or, for validation, {"detail": [{loc, msg}]}. */
function detailMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item: { loc?: unknown[]; msg?: string }) => {
        const field = item.loc?.filter((part) => part !== 'body').join('.')
        return field ? `${field}: ${item.msg}` : (item.msg ?? 'Invalid value')
      })
      .join('; ')
  }
  return `The server answered with an error (${status}).`
}

const path = (id: string) => encodeURIComponent(id)

/** The signed-in guest, or null when signed out. */
export async function getSession(): Promise<Bidder | null> {
  try {
    return await request<Bidder>('/api/session')
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null
    throw error
  }
}

export function signIn(displayName: string): Promise<Bidder> {
  return request<Bidder>('/api/session', { method: 'POST', body: { display_name: displayName } })
}

export function listAuctions(): Promise<AuctionSummary[]> {
  return request<AuctionSummary[]>('/api/auctions')
}

export function getAuction(id: string): Promise<AuctionSnapshot> {
  return request<AuctionSnapshot>(`/api/auctions/${path(id)}`)
}

export function placeBid(auctionId: string, bid: BidCreate): Promise<BidResult> {
  return request<BidResult>(`/api/auctions/${path(auctionId)}/bids`, {
    method: 'POST',
    body: bid,
    timeoutMs: BID_TIMEOUT_MS,
  })
}

export function createAuction(body: AuctionCreate, adminPassword: string): Promise<AuctionSnapshot> {
  return request<AuctionSnapshot>('/api/admin/auctions', {
    method: 'POST',
    body,
    headers: { 'X-Admin-Password': adminPassword },
  })
}

/** The live updates socket, on the same host as the page. */
export function wsUrl(
  auctionId: string,
  location: Pick<Location, 'protocol' | 'host'> = window.location,
): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${location.host}/ws/auctions/${path(auctionId)}`
}
