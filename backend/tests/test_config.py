import pytest

from app.config import Settings, async_database_url


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgres://u:p@db:5432/app", "postgresql+asyncpg://u:p@db:5432/app"),
        ("postgresql://u:p@db:5432/app", "postgresql+asyncpg://u:p@db:5432/app"),
        ("postgresql+asyncpg://u:p@db:5432/app", "postgresql+asyncpg://u:p@db:5432/app"),
        ("sqlite+aiosqlite:///x.db", "sqlite+aiosqlite:///x.db"),
    ],
)
def test_async_database_url(given: str, expected: str) -> None:
    assert async_database_url(given) == expected


def test_settings_switch_plain_postgres_url_to_asyncpg() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url="postgres://u:p@db/app",
        redis_url="redis://localhost:6379/0",
        session_secret="a-session-secret-of-enough-length",
        admin_password="admin",
    )

    assert settings.database_url == "postgresql+asyncpg://u:p@db/app"
