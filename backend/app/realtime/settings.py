from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RealtimeSettings(BaseSettings):
    """Real-time fan-out settings, from REALTIME_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="REALTIME_", env_file=".env", extra="ignore")

    # How long an event that skips a seq waits for the missing ones, so a pair of bids
    # that published out of order still reaches clients in order (app.realtime.hub).
    # Longer catches more reordering; every genuine gap is delayed by this much.
    reorder_window_ms: int = Field(default=100, ge=0, le=5000)
