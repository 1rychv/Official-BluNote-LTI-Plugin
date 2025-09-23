"""Database connection management for Redis and PostgreSQL."""
import os
import redis.asyncio as redis
from typing import Optional
import logging

try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False
    asyncpg = None

logger = logging.getLogger(__name__)

# Global connection pools
redis_pool: Optional[redis.ConnectionPool] = None
postgres_pool = None


async def init_redis():
    """Initialize Redis connection pool."""
    global redis_pool
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", "20"))

    redis_pool = redis.ConnectionPool.from_url(
        redis_url,
        max_connections=max_connections,
        decode_responses=True
    )
    logger.info(f"Redis pool initialized with {max_connections} max connections")


async def init_postgres():
    """Initialize PostgreSQL connection pool."""
    global postgres_pool

    if not ASYNCPG_AVAILABLE:
        logger.info("asyncpg not available, skipping PostgreSQL initialization")
        return

    postgres_dsn = os.getenv("POSTGRES_DSN")

    if not postgres_dsn:
        logger.info("PostgreSQL DSN not provided, skipping PostgreSQL initialization")
        return

    max_size = int(os.getenv("POSTGRES_MAX_SIZE", "10"))

    try:
        postgres_pool = await asyncpg.create_pool(
            postgres_dsn,
            min_size=1,
            max_size=max_size
        )
        logger.info(f"PostgreSQL pool initialized with {max_size} max connections")
    except Exception as e:
        logger.warning(f"Failed to initialize PostgreSQL: {e}. Continuing with Redis-only mode.")


async def close_connections():
    """Close all database connections."""
    global redis_pool, postgres_pool

    if redis_pool:
        await redis_pool.disconnect()
        redis_pool = None
        logger.info("Redis pool closed")

    if postgres_pool:
        await postgres_pool.close()
        postgres_pool = None
        logger.info("PostgreSQL pool closed")


def get_redis() -> redis.Redis:
    """Get Redis client from pool."""
    if not redis_pool:
        raise RuntimeError("Redis pool not initialized")
    return redis.Redis(connection_pool=redis_pool)


def get_postgres():
    """Get PostgreSQL pool."""
    return postgres_pool


async def get_db_connection():
    """Get a PostgreSQL database connection from the pool."""
    if not postgres_pool:
        raise RuntimeError("PostgreSQL not initialized. Check POSTGRES_DSN environment variable.")
    return await postgres_pool.acquire()


async def get_redis_connection() -> redis.Redis:
    """Get a Redis connection from the pool."""
    if not redis_pool:
        raise RuntimeError("Redis not initialized")
    return redis.Redis(connection_pool=redis_pool)