// Front-end settings. VITE_* values are read at build time.

/** Display currency for every amount. The contract carries minor units only. */
export const CURRENCY: string = import.meta.env.VITE_CURRENCY || 'INR'

/** How often the auction list refreshes while the tab is visible. */
export const LIST_POLL_MS = 5_000
/** WebSocket reconnect backoff: first delay and cap. */
export const RECONNECT_BASE_MS = 500
export const RECONNECT_MAX_MS = 10_000
/** While the WebSocket is down, refetch the snapshot this often. */
export const FALLBACK_POLL_MS = 5_000
/** After our bid is accepted, resync if its event hasn't arrived within this time. */
export const EXPECT_SEQ_TIMEOUT_MS = 2_000
/** Give up waiting for a bid response after this long (the outcome is then unknown). */
export const BID_TIMEOUT_MS = 10_000
export const REQUEST_TIMEOUT_MS = 15_000
/**
 * The most one bid may raise the price: the larger of this percentage of the current
 * price (the opening price before any bid) and MIN_MAX_RAISE minor units. These mirror
 * the server's BIDDING_MAX_RAISE_PERCENT and BIDDING_MIN_MAX_RAISE; the server decides.
 */
export const MAX_RAISE_PERCENT = 100
export const MIN_MAX_RAISE = 100_000
/** Bid history rows kept on the auction page. */
export const HISTORY_LIMIT = 50
