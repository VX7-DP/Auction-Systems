// The interface contract from CLAUDE.md section 10, mirrored from backend/app/contract.py.
// Ids are UUID strings, amounts are integers in minor units (paise or cents), and times
// are ISO 8601 UTC strings. Change section 10 first, then the backend, then this file.

// 2**53 - 1: the largest integer that stays exact in JavaScript and in Redis Lua.
export const MAX_AMOUNT = Number.MAX_SAFE_INTEGER

export type AuctionStatus = 'open' | 'paused' | 'closed'
export type BidStatus = 'accepted' | 'rejected' | 'error'
export type RejectReason = 'too_low' | 'closed' | 'paused' | 'not_found'

/** POST and GET /api/session. */
export interface Bidder {
  bidder_id: string
  display_name: string
}

/** One item of GET /api/auctions. */
export interface AuctionSummary {
  id: string
  title: string
  status: AuctionStatus
  high_amount: number | null
  end_at: string
}

export interface RecentBid {
  seq: number
  amount: number
  bidder_name: string
  is_bot: boolean
  created_at: string
}

/** GET /api/auctions/{id}. The high_* fields are null until the first accepted bid. */
export interface AuctionSnapshot {
  id: string
  title: string
  start_price: number
  end_at: string
  status: AuctionStatus
  seq: number
  high_amount: number | null
  high_bidder_id: string | null
  high_bidder_name: string | null
  high_is_bot: boolean | null
  recent_bids: RecentBid[]
}

/** Body of POST /api/auctions/{id}/bids. */
export interface BidCreate {
  bid_id: string
  amount: number
}

/** Response of POST /api/auctions/{id}/bids. reason and seq are left out when unset. */
export interface BidResult {
  bid_id: string
  status: BidStatus
  reason?: RejectReason
  seq?: number
}

/** Body of POST /api/admin/auctions, sent with an X-Admin-Password header. */
export interface AuctionCreate {
  title: string
  start_price: number
  end_at: string
}

/** WebSocket /ws/auctions/{id}: sent on every state change. */
export interface AuctionUpdate {
  type: 'auction_update'
  auction_id: string
  seq: number
  high_amount: number | null
  high_bidder_id: string | null
  high_bidder_name: string | null
  is_bot: boolean | null
  end_at: string
  status: AuctionStatus
}

/** WebSocket /ws/auctions/{id}: sent once when the auction closes. */
export interface AuctionClosed {
  type: 'auction_closed'
  auction_id: string
  seq: number
  winner_id: string | null
  winner_name: string | null
  amount: number | null
}

export type AuctionEvent = AuctionUpdate | AuctionClosed
