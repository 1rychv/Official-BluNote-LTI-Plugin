#!/usr/bin/env python3
"""Test script for Supabase integration."""

import os
import asyncio
import sys
from pathlib import Path

# Add the app directory to the Python path
sys.path.insert(0, str(Path(__file__).parent / "app"))

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def test_supabase_integration():
    """Test basic Supabase functionality."""
    print("🧪 Testing Supabase Integration for BluNote LTI")
    print("=" * 50)

    # Check environment variables
    use_supabase = os.getenv("USE_SUPABASE", "false").lower() == "true"
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_ANON_KEY")

    if not use_supabase:
        print("❌ USE_SUPABASE is not set to 'true'")
        print("   Set USE_SUPABASE=true in your .env file")
        return False

    if not supabase_url:
        print("❌ SUPABASE_URL not found in environment")
        print("   Add SUPABASE_URL=https://your-project.supabase.co to .env")
        return False

    if not supabase_key:
        print("❌ SUPABASE_ANON_KEY not found in environment")
        print("   Add SUPABASE_ANON_KEY=your-key to .env")
        return False

    print("✅ Environment variables configured")

    # Test Supabase connection
    try:
        from app.database.supabase_connection import init_supabase, get_supabase
        print("📦 Initializing Supabase client...")

        await init_supabase()
        supabase_client = get_supabase()

        print("✅ Supabase client initialized")
    except Exception as e:
        print(f"❌ Failed to initialize Supabase: {e}")
        return False

    # Test basic connectivity
    try:
        print("🔌 Testing connectivity...")
        result = supabase_client.table("schema_migrations").select("count", count="exact").limit(1).execute()
        print(f"✅ Connected to Supabase successfully")
    except Exception as e:
        print(f"❌ Connectivity test failed: {e}")
        return False

    # Test migrations
    try:
        print("📋 Running database migrations...")
        from app.database.supabase_migrations import run_supabase_migrations, create_rpc_functions

        migration_success = await run_supabase_migrations(supabase_client)
        if migration_success:
            print("✅ Database migrations completed")
        else:
            print("❌ Database migrations failed")
            return False

        rpc_success = await create_rpc_functions(supabase_client)
        if rpc_success:
            print("✅ RPC functions created")
        else:
            print("❌ RPC function creation failed")
            return False

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False

    # Test state management
    try:
        print("🏗️  Testing state management...")
        from app.database.supabase_connection import SupabaseCourseState

        course_state = SupabaseCourseState("test-course-123")

        # Test presence tracking
        await course_state.update_presence("test-user-1")
        await course_state.update_presence("test-user-2")

        # Test confusion tracking (with Redis wrapper)
        print("   Testing Redis wrapper for confusion tracking...")
        await course_state.record_confused_event("test-user-1")
        confused_users = await course_state.get_unique_users_in_window()

        # Test roster
        roster_size = await course_state.get_roster_size()

        # Test metrics
        metrics = await course_state.compute_metrics()

        print(f"✅ State management working:")
        print(f"   - Confused users: {len(confused_users)}")
        print(f"   - Roster size: {roster_size}")
        print(f"   - Confusion %: {metrics['pct']}%")

    except Exception as e:
        print(f"❌ State management test failed: {e}")
        return False

    # Test Supabase Storage
    try:
        print("📁 Testing Supabase Storage...")
        from app.database.supabase_connection import get_storage_helper

        storage_helper = get_storage_helper()

        # Ensure bucket exists
        await storage_helper.ensure_bucket_exists()

        # Test tutoring content storage
        test_content = {
            "title": "Test Tutoring Content",
            "text": "This is a test tutoring explanation for BluNote.",
            "practice": [{"q": "Test question?", "a": "Test answer"}]
        }

        # This will store in both database and Supabase Storage
        await course_state.persist_tutoring_content("test-user-1", test_content)

        # Test file listing
        tutoring_files = await storage_helper.list_tutoring_files("test-course-123", "test-user-1")

        print(f"✅ Supabase Storage working:")
        print(f"   - Bucket exists: blunote-files")
        print(f"   - Tutoring files stored: {len(tutoring_files)}")

    except Exception as e:
        print(f"❌ Supabase Storage test failed: {e}")
        return False

    # Test cleanup
    try:
        print("🧹 Testing cleanup...")
        # Clean up test database data
        supabase_client.table("confusion_cache").delete().eq("course_id", "test-course-123").execute()
        supabase_client.table("presence_tracking").delete().eq("course_id", "test-course-123").execute()
        supabase_client.table("confusion_events").delete().eq("course_id", "test-course-123").execute()
        supabase_client.table("tutoring_logs").delete().eq("course_id", "test-course-123").execute()
        supabase_client.table("tutoring_cache").delete().eq("user_id", "test-user-1").execute()

        # Clean up test storage files
        try:
            storage_helper = get_storage_helper()
            tutoring_files = await storage_helper.list_tutoring_files("test-course-123")
            for file_info in tutoring_files:
                if "name" in file_info:
                    file_path = f"tutoring/test-course-123/{file_info['name']}"
                    supabase_client.storage.from_("blunote-files").remove([file_path])
        except Exception as storage_cleanup_error:
            print(f"   Storage cleanup warning: {storage_cleanup_error}")

        print("✅ Cleanup completed")
    except Exception as e:
        print(f"⚠️  Cleanup warning: {e}")

    print("\n🎉 All tests passed! Supabase integration is ready.")
    print("\nNext steps:")
    print("1. Start your FastAPI server: uvicorn app.main:app --reload")
    print("2. Test with frontend at http://localhost:5173")
    print("3. Monitor your Supabase dashboard for real-time data")

    return True


if __name__ == "__main__":
    # Set up test environment
    os.environ.setdefault("USE_SUPABASE", "true")

    success = asyncio.run(test_supabase_integration())
    exit_code = 0 if success else 1
    sys.exit(exit_code)