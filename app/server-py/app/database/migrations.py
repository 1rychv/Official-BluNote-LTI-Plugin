"""Database schema migrations."""
import asyncpg
import logging
from typing import List

logger = logging.getLogger(__name__)

# Migration scripts following the pseudocode from step 3
MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS courses (
        id TEXT PRIMARY KEY,
        created_at TIMESTAMP DEFAULT now(),
        roster_override INTEGER,
        threshold_percent INTEGER,
        settings JSONB
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        course_id TEXT REFERENCES courses(id),
        user_id TEXT,
        type TEXT,
        payload JSONB,
        occurred_at TIMESTAMP DEFAULT now()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_course_type_time
    ON events(course_id, type, occurred_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_occurred_at
    ON events(occurred_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS tutoring_sessions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        course_id TEXT,
        user_id TEXT,
        content JSONB,
        delivered_at TIMESTAMP DEFAULT now()
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tutoring_course_user_time
    ON tutoring_sessions(course_id, user_id, delivered_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS members (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        course_id TEXT,
        user_id TEXT,
        name TEXT,
        email TEXT,
        roles JSONB,
        created_at TIMESTAMP DEFAULT now(),
        updated_at TIMESTAMP DEFAULT now(),
        UNIQUE(course_id, user_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_members_course_id
    ON members(course_id);
    """,
    # Migration 9: Rename members table and add columns for NRPS
    """
    ALTER TABLE members RENAME TO course_members;
    """,
    """
    ALTER TABLE course_members
    ADD COLUMN IF NOT EXISTS given_name TEXT,
    ADD COLUMN IF NOT EXISTS family_name TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Active';
    """,
    # Migration 11: Update course table for AGS/NRPS
    """
    ALTER TABLE courses
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT now();
    """
]


async def run_migrations(pool: asyncpg.Pool) -> None:
    """Run all database migrations."""
    async with pool.acquire() as conn:
        # Create a migration tracking table if it doesn't exist
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT now()
            );
        """)

        # Check which migrations have been applied
        applied_versions = await conn.fetch(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
        applied = {row['version'] for row in applied_versions}

        # Apply new migrations
        for i, migration in enumerate(MIGRATIONS):
            version = i + 1
            if version in applied:
                logger.info(f"Migration {version} already applied")
                continue

            logger.info(f"Applying migration {version}")
            try:
                await conn.execute(migration)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)",
                    version
                )
                logger.info(f"Migration {version} applied successfully")
            except Exception as e:
                logger.error(f"Migration {version} failed: {e}")
                raise

        logger.info("All migrations completed successfully")


async def reset_database(pool: asyncpg.Pool) -> None:
    """Reset database by dropping all tables (for development only)."""
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS schema_migrations CASCADE;")
        await conn.execute("DROP TABLE IF EXISTS course_members CASCADE;")
        await conn.execute("DROP TABLE IF EXISTS members CASCADE;")
        await conn.execute("DROP TABLE IF EXISTS tutoring_sessions CASCADE;")
        await conn.execute("DROP TABLE IF EXISTS events CASCADE;")
        await conn.execute("DROP TABLE IF EXISTS courses CASCADE;")
        logger.info("Database reset completed")