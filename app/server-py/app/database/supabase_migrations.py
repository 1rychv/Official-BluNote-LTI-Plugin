"""Supabase schema migrations for BluNote LTI."""
import logging
from typing import Optional
from supabase import Client

logger = logging.getLogger(__name__)


SCHEMA_MIGRATIONS = [
    {
        "name": "create_confusion_events_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS confusion_events (
            id BIGSERIAL PRIMARY KEY,
            course_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'confused',
            timestamp BIGINT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            window_end TIMESTAMPTZ NOT NULL,
            slide_context JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_confusion_events_course_window
        ON confusion_events (course_id, window_end DESC);

        CREATE INDEX IF NOT EXISTS idx_confusion_events_user_time
        ON confusion_events (user_id, occurred_at DESC);
        """
    },
    {
        "name": "create_confusion_cache_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS confusion_cache (
            course_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            timestamp BIGINT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (course_id, user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_confusion_cache_expires
        ON confusion_cache (expires_at DESC);
        """
    },
    {
        "name": "create_presence_tracking_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS presence_tracking (
            course_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            timestamp BIGINT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (course_id, user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_presence_tracking_expires
        ON presence_tracking (expires_at DESC);
        """
    },
    {
        "name": "create_course_rosters_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS course_rosters (
            course_id TEXT PRIMARY KEY,
            roster_size INTEGER NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
    },
    {
        "name": "create_trigger_cooldowns_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS trigger_cooldowns (
            course_id TEXT PRIMARY KEY,
            last_trigger_time BIGINT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """
    },
    {
        "name": "create_trigger_events_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS trigger_events (
            id BIGSERIAL PRIMARY KEY,
            course_id TEXT NOT NULL,
            confused_users TEXT[] NOT NULL,
            confusion_pct INTEGER NOT NULL,
            threshold INTEGER NOT NULL,
            confused_count INTEGER NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_trigger_events_course_time
        ON trigger_events (course_id, occurred_at DESC);
        """
    },
    {
        "name": "create_tutoring_logs_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS tutoring_logs (
            id BIGSERIAL PRIMARY KEY,
            course_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            content JSONB NOT NULL,
            delivered_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_tutoring_logs_course_user
        ON tutoring_logs (course_id, user_id, delivered_at DESC);

        CREATE INDEX IF NOT EXISTS idx_tutoring_logs_expires
        ON tutoring_logs (expires_at);
        """
    },
    {
        "name": "create_tutoring_cache_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS tutoring_cache (
            user_id TEXT PRIMARY KEY,
            content JSONB NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_tutoring_cache_expires
        ON tutoring_cache (expires_at);
        """
    },
    {
        "name": "create_slide_tracking_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS slide_tracking (
            id BIGSERIAL PRIMARY KEY,
            course_id TEXT NOT NULL,
            slide_number INTEGER NOT NULL,
            slide_title TEXT,
            updated_by TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            created_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_slide_tracking_course_time
        ON slide_tracking (course_id, updated_at DESC);
        """
    },
    {
        "name": "create_lti_launches_table",
        "sql": """
        CREATE TABLE IF NOT EXISTS lti_launches (
            id BIGSERIAL PRIMARY KEY,
            course_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_email TEXT,
            user_name TEXT,
            role TEXT NOT NULL,
            platform_issuer TEXT,
            deployment_id TEXT,
            launch_data JSONB,
            launched_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_lti_launches_course_user
        ON lti_launches (course_id, user_id, launched_at DESC);
        """
    },
    {
        "name": "create_cleanup_functions",
        "sql": """
        -- Function to clean up expired cache entries
        CREATE OR REPLACE FUNCTION cleanup_expired_entries()
        RETURNS void AS $$
        BEGIN
            DELETE FROM confusion_cache WHERE expires_at < NOW();
            DELETE FROM presence_tracking WHERE expires_at < NOW();
            DELETE FROM tutoring_cache WHERE expires_at < NOW();
            DELETE FROM tutoring_logs WHERE expires_at < NOW();
        END;
        $$ LANGUAGE plpgsql;

        -- Function to get active confusion count for a course
        CREATE OR REPLACE FUNCTION get_active_confusion_count(p_course_id TEXT)
        RETURNS INTEGER AS $$
        BEGIN
            RETURN (
                SELECT COUNT(DISTINCT user_id)
                FROM confusion_cache
                WHERE course_id = p_course_id
                AND expires_at > NOW()
            );
        END;
        $$ LANGUAGE plpgsql;

        -- Function to get active presence count for a course
        CREATE OR REPLACE FUNCTION get_active_presence_count(p_course_id TEXT)
        RETURNS INTEGER AS $$
        BEGIN
            RETURN (
                SELECT COUNT(DISTINCT user_id)
                FROM presence_tracking
                WHERE course_id = p_course_id
                AND expires_at > NOW()
            );
        END;
        $$ LANGUAGE plpgsql;
        """
    }
]


async def run_supabase_migrations(supabase: Client) -> bool:
    """Run all Supabase schema migrations."""
    logger.info("Starting Supabase schema migrations...")

    try:
        # Create migrations tracking table if it doesn't exist
        tracking_sql = """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            executed_at TIMESTAMPTZ DEFAULT NOW()
        );
        """

        supabase.rpc("exec_sql", {"query": tracking_sql}).execute()

        # Get already executed migrations
        result = supabase.table("schema_migrations").select("name").execute()
        executed_migrations = set(item["name"] for item in result.data)

        # Run pending migrations
        for migration in SCHEMA_MIGRATIONS:
            migration_name = migration["name"]

            if migration_name in executed_migrations:
                logger.info(f"Skipping already executed migration: {migration_name}")
                continue

            logger.info(f"Executing migration: {migration_name}")

            try:
                # Execute the migration SQL
                supabase.rpc("exec_sql", {"query": migration["sql"]}).execute()

                # Record successful execution
                supabase.table("schema_migrations").insert({
                    "name": migration_name
                }).execute()

                logger.info(f"Successfully executed migration: {migration_name}")

            except Exception as e:
                logger.error(f"Failed to execute migration {migration_name}: {e}")
                return False

        logger.info("All Supabase migrations completed successfully")
        return True

    except Exception as e:
        logger.error(f"Migration process failed: {e}")
        return False


async def create_rpc_functions(supabase: Client) -> bool:
    """Create necessary RPC functions for advanced queries."""
    logger.info("Creating Supabase RPC functions...")

    rpc_functions = [
        {
            "name": "exec_sql",
            "sql": """
            CREATE OR REPLACE FUNCTION exec_sql(query TEXT)
            RETURNS TEXT AS $$
            BEGIN
                EXECUTE query;
                RETURN 'SUCCESS';
            EXCEPTION WHEN OTHERS THEN
                RETURN 'ERROR: ' || SQLERRM;
            END;
            $$ LANGUAGE plpgsql SECURITY DEFINER;
            """
        },
        {
            "name": "get_course_metrics",
            "sql": """
            CREATE OR REPLACE FUNCTION get_course_metrics(p_course_id TEXT)
            RETURNS TABLE(
                active_confusion_count BIGINT,
                active_presence_count BIGINT,
                recent_triggers_count BIGINT,
                recent_tutoring_count BIGINT
            ) AS $$
            BEGIN
                RETURN QUERY
                SELECT
                    (SELECT COUNT(DISTINCT user_id) FROM confusion_cache
                     WHERE course_id = p_course_id AND expires_at > NOW()),
                    (SELECT COUNT(DISTINCT user_id) FROM presence_tracking
                     WHERE course_id = p_course_id AND expires_at > NOW()),
                    (SELECT COUNT(*) FROM trigger_events
                     WHERE course_id = p_course_id AND occurred_at > NOW() - INTERVAL '2 hours'),
                    (SELECT COUNT(*) FROM tutoring_logs
                     WHERE course_id = p_course_id AND delivered_at > NOW() - INTERVAL '2 hours');
            END;
            $$ LANGUAGE plpgsql SECURITY DEFINER;
            """
        }
    ]

    try:
        for func in rpc_functions:
            logger.info(f"Creating RPC function: {func['name']}")
            supabase.rpc("exec_sql", {"query": func["sql"]}).execute()

        logger.info("All RPC functions created successfully")
        return True

    except Exception as e:
        logger.error(f"Failed to create RPC functions: {e}")
        return False