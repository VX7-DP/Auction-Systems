// The network boundary. The app talks to the backend only through this module, so the
// mock backend (npm run dev:mock, and the tests) can stand in for the real one.

/** The part of the fetch Response the app uses. */
export interface TransportResponse {
  ok: boolean
  status: number
  json(): Promise<unknown>
}

export interface TransportRequest {
  method?: string
  headers?: Record<string, string>
  body?: string
  signal?: AbortSignal
}

/** The part of the browser WebSocket the app uses. */
export interface SocketLike {
  onopen: ((event: unknown) => void) | null
  onmessage: ((event: { data: unknown }) => void) | null
  onclose: ((event: { code?: number }) => void) | null
  onerror: ((event: unknown) => void) | null
  close(code?: number, reason?: string): void
}

export interface Transport {
  fetch(url: string, init?: TransportRequest): Promise<TransportResponse>
  openSocket(url: string): SocketLike
}

export const browserTransport: Transport = {
  fetch: (url, init) => fetch(url, { credentials: 'same-origin', ...init }),
  openSocket(url) {
    const ws = new WebSocket(url)
    const socket: SocketLike = {
      onopen: null,
      onmessage: null,
      onclose: null,
      onerror: null,
      close: (code, reason) => ws.close(code, reason),
    }
    ws.onopen = (event) => socket.onopen?.(event)
    ws.onmessage = (event) => socket.onmessage?.(event)
    ws.onclose = (event) => socket.onclose?.(event)
    ws.onerror = (event) => socket.onerror?.(event)
    return socket
  },
}

let current: Transport = browserTransport

export function getTransport(): Transport {
  return current
}

export function setTransport(transport: Transport): void {
  current = transport
}
