"""App-wide settings, read from environment variables (and an optional .env file).

Shared file owned by session 0. Settings that only one module needs belong in that
module, in its own ``pydantic_settings.BaseSettings`` subclass, so later sessions
never have to edit this file.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # e.g. postgresql+asyncpg://auction:auction@localhost:5432/auction
    database_url: str
    # e.g. redis://localhost:6379/0
    redis_url: str
    # Signs the guest session cookie. Use a long random value in production.
    session_secret: SecretStr = Field(min_length=16)
    # Sent by the admin page in the X-Admin-Password header.
    admin_password: SecretStr = Field(min_length=1)
    # Marks the session cookie Secure. Only turn this off for plain-HTTP local runs.
    cookie_secure: bool = True
    # Background jobs (close sweeper, simulated bidders) start only when this is
    # true. Tests turn it off so jobs never fire inside unrelated tests.
    run_background_jobs: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
