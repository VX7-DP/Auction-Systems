"""Settings only the bid engine needs, read from BIDDING_* environment variables.

They are read when the app starts (see lifespan.py), not at import time, so tests can
set them per test.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BiddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BIDDING_", env_file=".env", extra="ignore")

    # Postgres statement timeout for each attempt to save an accepted bid.
    insert_timeout_ms: int = Field(default=1000, gt=0)
    # Attempts to save a bid before its auction is paused.
    insert_attempts: int = Field(default=2, ge=1)
    # Postgres statement timeout while reloading an auction. The reload waits for bid
    # writes already in progress, so keep it above insert_timeout_ms.
    reload_timeout_ms: int = Field(default=5000, gt=0)
    # How long a reload may hold its Redis lock before another instance may take over.
    reload_lock_ttl_ms: int = Field(default=10000, gt=0)
    # Seqs reserved in Postgres at a time (auctions.seq_ceiling). One reservation per
    # this many state changes; it is also the jump in seq after Redis loses an auction.
    seq_block: int = Field(default=1000, ge=2)
    # Close sweeper period, and the TTL of its job lock (renewed every period).
    sweep_interval_ms: int = Field(default=1000, gt=0)
    job_lock_ttl_ms: int = Field(default=10000, gt=0)
    # How long Redis remembers which auction accepted a bid_id, so a bid_id reused on
    # another auction is refused at once (section 10 of the design doc, bid:{bid_id}).
    bid_owner_ttl_s: int = Field(default=86400, gt=0)
    # The most one bid may raise the price: the larger of this percentage of the
    # current price (the opening price before any bid) and min_max_raise minor units.
    # A bid above that is rejected as too_high (section 10 of the design doc).
    max_raise_percent: int = Field(default=100, gt=0, le=10_000)
    min_max_raise: int = Field(default=100_000, gt=0)


_current: BiddingSettings | None = None


def load_bidding_settings() -> BiddingSettings:
    """Reads the settings from the environment and makes them current."""
    global _current
    _current = BiddingSettings()
    return _current


def get_bidding_settings() -> BiddingSettings:
    return _current if _current is not None else load_bidding_settings()
