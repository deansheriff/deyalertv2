from collections.abc import AsyncIterator

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.errors import ApiError

_queue: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    global _queue
    if _queue is None:
        _queue = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _queue


async def get_redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(get_settings().redis_url)
    try:
        yield client
    finally:
        await client.aclose()


async def hit_limit(redis: Redis, key: str, limit: int, window_seconds: int) -> None:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    if count > limit:
        raise ApiError(429, "rate_limited", "Too many requests. Try again later.")
