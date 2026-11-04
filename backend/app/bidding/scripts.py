"""The Lua scripts that make every decision on the hot path, atomically, in Redis.

Each script runs alone inside Redis, so a decision and the state change it causes can
never interleave with another bid. Redis TIME is the one clock for deadlines.

Numbers cross the Lua boundary as decimal strings: Lua numbers are doubles, exact up
to 2**53 (MAX_AMOUNT), but tostring() would print them with only 14 digits.
"""

import hashlib
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import NoScriptError

# Internal keys of the bid engine. The section 10 contract keys are in app.contract.
PAUSED_AUCTIONS_KEY = "bidding:paused"


def bid_memory_key(auction_id: UUID) -> str:
    """Hash of accepted bid_id -> "seq|amount|bidder_id|is_bot|bidder_name"."""
    return f"auction:{auction_id}:bids"


def reload_lock_key(auction_id: UUID) -> str:
    return f"lock:reload:{auction_id}"


class LuaScript:
    """Runs by SHA, and sends the source again after a Redis restart empties the cache."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.sha = hashlib.sha1(source.encode()).hexdigest()

    async def __call__(self, redis: Redis, keys: list[str], args: list[str]) -> list[str] | int:
        try:
            return await redis.evalsha(self.sha, len(keys), *keys, *args)
        except NoScriptError:
            return await redis.eval(self.source, len(keys), *keys, *args)


_NOW_MS = """
local now = redis.call('TIME')
local now_ms = tonumber(now[1]) * 1000 + math.floor(tonumber(now[2]) / 1000)
"""

# KEYS: auction hash, bid memory.
# ARGV: bid_id, bidder_id, bidder_name, amount, is_bot ("1" or "0").
# Returns {"NOT_LOADED"}, {"REJECTED", reason}, {"REPEAT", epoch, remembered record}
# or {"ACCEPTED", epoch, seq, end_at_ms}.
DECIDE = LuaScript(
    """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return {'NOT_LOADED'}
end
local epoch = redis.call('HGET', KEYS[1], 'epoch')
-- A bid_id seen before gets its original decision, whatever the auction does now.
local seen = redis.call('HGET', KEYS[2], ARGV[1])
if seen then
  return {'REPEAT', epoch, seen}
end
local state = redis.call('HMGET', KEYS[1],
  'status', 'end_at_ms', 'start_price', 'high_amount', 'seq')
if state[1] == 'closed' then
  return {'REJECTED', 'closed'}
end
"""
    + _NOW_MS
    + """
if now_ms >= tonumber(state[2]) then
  return {'REJECTED', 'closed'}
end
if state[1] ~= 'open' then
  return {'REJECTED', 'paused'}
end
local floor = state[4]
if not floor then
  floor = state[3]
end
if tonumber(ARGV[4]) <= tonumber(floor) then
  return {'REJECTED', 'too_low'}
end
local seq = string.format('%d', tonumber(state[5]) + 1)
redis.call('HSET', KEYS[1], 'seq', seq, 'high_amount', ARGV[4], 'high_bidder_id', ARGV[2],
  'high_bidder_name', ARGV[3], 'high_is_bot', ARGV[5])
redis.call('HSET', KEYS[2], ARGV[1],
  seq .. '|' .. ARGV[4] .. '|' .. ARGV[2] .. '|' .. ARGV[5] .. '|' .. ARGV[3])
return {'ACCEPTED', epoch, seq, state[2]}
"""
)

# KEYS: auction hash, bid memory, paused set.
# ARGV: auction_id, epoch, status, end_at_ms, start_price, seq, high_amount ("" if none),
#       high_bidder_id, high_bidder_name, high_is_bot, then bid_id and record pairs.
# Replaces the auction's Redis state unless Redis already holds a load at least as new.
# seq never goes backwards, and a paused auction reopens with one more seq.
# Returns {"STALE"} or {"LOADED", seq, reopened ("1" or "0")}.
LOAD = LuaScript(
    """
local current = redis.call('HMGET', KEYS[1], 'epoch', 'seq', 'status')
if current[1] and tonumber(current[1]) >= tonumber(ARGV[2]) then
  return {'STALE'}
end
local seq = tonumber(ARGV[6])
if current[2] and tonumber(current[2]) > seq then
  seq = tonumber(current[2])
end
local reopened = '0'
if current[3] == 'paused' and ARGV[3] == 'open' then
  seq = seq + 1
  reopened = '1'
end
seq = string.format('%d', seq)
redis.call('DEL', KEYS[1], KEYS[2])
redis.call('HSET', KEYS[1], 'status', ARGV[3], 'end_at_ms', ARGV[4], 'start_price', ARGV[5],
  'seq', seq, 'epoch', ARGV[2])
if ARGV[7] ~= '' then
  redis.call('HSET', KEYS[1], 'high_amount', ARGV[7], 'high_bidder_id', ARGV[8],
    'high_bidder_name', ARGV[9], 'high_is_bot', ARGV[10])
end
for i = 11, #ARGV, 2 do
  redis.call('HSET', KEYS[2], ARGV[i], ARGV[i + 1])
end
redis.call('SREM', KEYS[3], ARGV[1])
return {'LOADED', seq, reopened}
"""
)

# KEYS: auction hash, paused set. ARGV: epoch, auction_id.
# Pauses an open auction, unless Redis has been reloaded since the failed bid.
# Returns {"UNCHANGED"} or {"PAUSED", seq}.
PAUSE = LuaScript(
    """
local state = redis.call('HMGET', KEYS[1], 'epoch', 'status', 'seq')
if state[1] ~= ARGV[1] or state[2] ~= 'open' then
  return {'UNCHANGED'}
end
local seq = string.format('%d', tonumber(state[3]) + 1)
redis.call('HSET', KEYS[1], 'status', 'paused', 'seq', seq)
redis.call('SADD', KEYS[2], ARGV[2])
return {'PAUSED', seq}
"""
)

# KEYS: auction hash. Closes the auction if it is open and Redis TIME has reached end_at.
# Returns {"NOT_LOADED"}, {"PAUSED"}, {"NOT_DUE"} or {"CLOSED", seq, epoch}; an auction
# closed earlier returns its closing seq again.
CLOSE = LuaScript(
    """
local state = redis.call('HMGET', KEYS[1], 'status', 'end_at_ms', 'seq', 'epoch')
if not state[1] then
  return {'NOT_LOADED'}
end
if state[1] == 'closed' then
  return {'CLOSED', state[3], state[4]}
end
if state[1] == 'paused' then
  return {'PAUSED'}
end
"""
    + _NOW_MS
    + """
if now_ms < tonumber(state[2]) then
  return {'NOT_DUE'}
end
local seq = string.format('%d', tonumber(state[3]) + 1)
redis.call('HSET', KEYS[1], 'status', 'closed', 'seq', seq)
return {'CLOSED', seq, state[4]}
"""
)

# KEYS: lock. ARGV: token. Releases the lock only if this token holds it.
RELEASE_LOCK = LuaScript(
    """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""
)
