from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DemoSettings(BaseSettings):
    """Demo-mode settings, from DEMO_* environment variables.

    The bots also need RUN_BACKGROUND_JOBS (app.config) to be true.
    """

    model_config = SettingsConfigDict(env_prefix="DEMO_", env_file=".env", extra="ignore")

    # Simulated bidders. Turn off, for example, to load-test human traffic alone.
    bots_enabled: bool = True
    # Mean seconds between the bots' passes over the open auctions.
    bot_tick_seconds: float = Field(default=2.0, gt=0)
