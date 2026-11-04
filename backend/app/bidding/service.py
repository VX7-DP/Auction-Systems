"""The one bid path, used by the bids endpoint and by the simulated bidders."""

from uuid import UUID

from app.contract import BidResult


async def place_bid(
    auction_id: UUID,
    bid_id: UUID,
    bidder_id: UUID,
    bidder_name: str,
    amount: int,
    is_bot: bool = False,
) -> BidResult:
    """Decide the bid in Redis (Lua CAS), commit it to Postgres, then publish it.

    See CLAUDE.md section 4. Get the Redis client and the Postgres sessionmaker
    from app.resources.get_resources().
    """
    raise NotImplementedError
