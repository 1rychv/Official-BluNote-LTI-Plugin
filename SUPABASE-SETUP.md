# Supabase Integration Setup for BluNote LTI

This guide walks you through setting up Supabase as the backend for BluNote LTI, replacing the need for self-hosted Redis and PostgreSQL infrastructure.

## Benefits of Supabase Integration

- **Unified Backend**: PostgreSQL + Redis functionality in one platform
- **Auto-scaling**: Handles traffic spikes automatically
- **Built-in Auth**: LTI 1.3 integration ready
- **Real-time**: WebSocket subscriptions for live updates
- **Managed Infrastructure**: No DevOps overhead
- **Integrated Wrappers**: Redis, S3, and Queue services available

## Step 1: Create Supabase Project

1. Go to [supabase.com](https://supabase.com) and create an account
2. Create a new project:
   - Project name: `BluNote LTI`
   - Database password: Generate a strong password
   - Region: Choose closest to your users

3. Wait for project provisioning (2-3 minutes)

## Step 2: Configure Environment Variables

Copy your Supabase credentials from the project dashboard:

```bash
# In your .env file
USE_SUPABASE=true
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
```

## Step 3: Enable Required Integrations

In your Supabase dashboard, go to **Integrations** and enable:

### 1. Redis Wrapper ✅ (ENABLED)
- Click on "Redis Wrapper"
- Enable it for your project
- **Purpose**: Ultra-fast real-time confusion tracking
- **BluNote Usage**: Stores active confusion presses, presence tracking
- **Performance**: Sub-millisecond response times for real-time metrics

### 2. Supabase Storage ✅ (RECOMMENDED)
- Go to **Storage** in your Supabase dashboard
- Create bucket named: `blunote-files` (private)
- **Purpose**: File storage for BluNote content
- **BluNote Usage**:
  - Tutoring content backup/analytics
  - Slide attachments and materials
  - LTI configuration files
  - Analytics exports (CSV)

### 3. Queues (Optional - Future Enhancement)
- Enable for background processing of AI tutoring generation
- Helps handle trigger events asynchronously
- **Note**: Can be added later when implementing AI tutoring

## Step 4: Install Dependencies

The Supabase Python client is already added to `requirements.txt`:

```bash
cd "BluNote LTI/app/server-py"
pip install -r requirements.txt
```

## Step 5: Run Database Migrations

Start your server to automatically run Supabase migrations:

```bash
cd "BluNote LTI/app/server-py"
USE_SUPABASE=true uvicorn app.main:app --host 0.0.0.0 --port 4000 --reload
```

The application will:
1. Connect to Supabase
2. Create all necessary tables and indexes
3. Set up RPC functions for advanced queries
4. Initialize the state management system

## Step 6: Verify Setup

Check the health endpoint to confirm Supabase connectivity:

```bash
curl http://localhost:4000/health
```

Expected response:
```json
{
  "ok": true,
  "time": "2025-09-23T14:30:00",
  "backend": "supabase",
  "supabase": "ok"
}
```

### Test Redis + Storage Integration

Run the comprehensive test script:

```bash
python test_supabase.py
```

Expected output:
```
🧪 Testing Supabase Integration for BluNote LTI
==================================================
✅ Environment variables configured
📦 Initializing Supabase client...
✅ Supabase client initialized
🔌 Testing connectivity...
✅ Connected to Supabase successfully
📋 Running database migrations...
✅ Database migrations completed
✅ RPC functions created
🏗️  Testing state management...
   Testing Redis wrapper for confusion tracking...
✅ State management working:
   - Confused users: 1
   - Roster size: 2
   - Confusion %: 50%
📁 Testing Supabase Storage...
✅ Supabase Storage working:
   - Bucket exists: blunote-files
   - Tutoring files stored: 1
🧹 Testing cleanup...
✅ Cleanup completed

🎉 All tests passed! Supabase integration is ready.
```

## Step 7: Production Configuration

### Row Level Security (RLS)
Enable RLS for data protection:

```sql
-- In Supabase SQL Editor
ALTER TABLE confusion_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tutoring_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE lti_launches ENABLE ROW LEVEL SECURITY;

-- Add policies based on your LTI context requirements
```

### Performance Optimization
- Enable connection pooling in your Supabase project settings
- Set up read replicas for high-traffic courses
- Configure automatic backups

### Monitoring
- Set up Supabase monitoring alerts
- Configure log retention policies
- Enable slow query monitoring

## Database Schema Overview

The Supabase integration creates these tables:

### Real-time Tracking
- `confusion_cache` - Active confusion presses (Redis-like)
- `presence_tracking` - Student presence for auto-roster
- `trigger_cooldowns` - Prevents trigger spam

### Persistent Data
- `confusion_events` - Historical confusion data
- `trigger_events` - Threshold trigger history
- `tutoring_logs` - AI tutoring delivery logs
- `slide_tracking` - Instructor slide progression

### LTI Integration
- `lti_launches` - Launch data and user context
- `course_rosters` - Manual roster overrides

### Caching
- `tutoring_cache` - User-specific tutoring content

## Migration from Redis+PostgreSQL

If migrating from existing Redis+PostgreSQL setup:

1. Export existing data:
```bash
# Export Redis data (if needed)
redis-cli --scan --pattern "presses:*" | head -100

# Export PostgreSQL data
pg_dump your_database > backup.sql
```

2. Set `USE_SUPABASE=true` in `.env`
3. Restart the application
4. Data will automatically start flowing to Supabase
5. Old Redis/PostgreSQL can be decommissioned after verification

## Troubleshooting

### Connection Issues
- Verify `SUPABASE_URL` and `SUPABASE_ANON_KEY` are correct
- Check Supabase project is not paused
- Ensure your IP is not blocked by Supabase

### Migration Failures
- Check Supabase SQL Editor for error details
- Verify service role key has sufficient permissions
- Check logs: `docker logs your-container-name`

### Performance Issues
- Monitor Supabase dashboard for query performance
- Consider enabling the Redis wrapper for frequently accessed data
- Upgrade Supabase plan if hitting rate limits

## Cost Optimization

- **Free Tier**: Good for development and small courses
- **Pro Tier**: Recommended for production with multiple courses
- **Team Tier**: For institutions with high concurrent usage

Monitor your usage in the Supabase dashboard and upgrade as needed.

## Security Best Practices

1. **Environment Variables**: Never commit credentials to git
2. **RLS Policies**: Implement course-based data isolation
3. **API Keys**: Rotate keys regularly
4. **Network**: Consider VPC if handling sensitive data
5. **Audit Logs**: Enable for compliance requirements

## Next Steps

With Supabase configured, you can now:
- Scale to handle thousands of concurrent students
- Add real-time collaboration features using Supabase Realtime
- Implement advanced analytics with built-in SQL capabilities
- Deploy globally using Supabase Edge Functions
- Add AI features using integrated vector databases

For advanced features like NRPS roster sync and AGS grade passback, the LTI integration remains the same - only the data storage backend changes to Supabase.