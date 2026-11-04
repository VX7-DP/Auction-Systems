"""App-wide settings, read from environment variables (and an optional .env file).

Shared file owned by session 0. Settings that only one module needs belong in that
module, in its own ``pydantic_settings.BaseSettings`` subclass, so later sessions
never have to edit this file.
"""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DRIVERLESS_POSTGRES_SCHEMES = ("postgres://", "postgresql://")


def async_database_url(url: str) -> str:
    """Point a plain Postgres URL at the asyncpg driver.

    Hosts often hand out postgres:// or postgresql:// URLs; the app needs
    postgresql+asyncpg://. URLs that already name a driver are left alone.
    """
    for scheme in _DRIVERLESS_POSTGRES_SCHEMES:
        if url.startswith(scheme):
            return "postgresql+asyncpg://" + url.removeprefix(scheme)
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # e.g. postgresql+asyncpg://auction:auction@localhost:5432/auction
    # (postgres:// and postgresql:// are accepted and switched to asyncpg).
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

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg(cls, value: str) -> str:
        return async_database_url(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
