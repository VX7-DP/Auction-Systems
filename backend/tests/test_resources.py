import asyncio

from app.config import Settings
from app.resources import open_resources

# More than the Redis pool holds at once (100 connections).
CONCURRENT_COMMANDS = 150


async def test_redis_commands_wait_for_a_free_connection_instead_of_failing(
    settings: Settings,
) -> None:
    """A spike of concurrent commands must queue for a pooled connection, not raise.

    Each BLPOP on an empty list holds its connection for 0.3 s, so the commands
    beyond the pool size can only succeed by waiting for one to come free.
    """
    async with open_resources(settings) as resources:
        commands = [
            resources.redis.blpop([f"test:empty:{i}"], timeout=0.3)
            for i in range(CONCURRENT_COMMANDS)
        ]

        assert await asyncio.gather(*commands) == [None] * CONCURRENT_COMMANDS
