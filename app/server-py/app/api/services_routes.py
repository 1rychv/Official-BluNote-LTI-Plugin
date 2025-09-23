"""
API routes for LTI Advantage services (NRPS and AGS)
"""
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from ..lti.services import nrps_service, ags_service
from ..security.guards import get_current_user
from ..lti.models import SessionClaims


router = APIRouter(prefix="/api/services", tags=["services"])


class SyncRosterRequest(BaseModel):
    course_id: str


class SyncRosterResponse(BaseModel):
    success: bool
    total_members: Optional[int] = None
    error: Optional[str] = None
    timestamp: str


class SubmitScoreRequest(BaseModel):
    user_id: str
    score: float
    score_maximum: float = 100.0
    comment: Optional[str] = None


class SubmitScoreResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    timestamp: str


@router.post("/nrps/sync", response_model=SyncRosterResponse)
async def sync_roster(
    request: SyncRosterRequest,
    current_user: SessionClaims = Depends(get_current_user)
):
    """
    Manually trigger roster synchronization for a course.
    Requires instructor role.
    """
    # Check if user is instructor
    if not current_user.is_instructor:
        raise HTTPException(403, "Only instructors can sync roster")

    # Check if course matches user's course
    if request.course_id != current_user.course_id:
        raise HTTPException(403, "Cannot sync roster for different course")

    # Check if NRPS URL is available
    if not current_user.nrps_url:
        raise HTTPException(400, "NRPS not available for this course")

    # Perform sync
    result = await nrps_service.sync_course_members(
        course_id=request.course_id,
        nrps_url=current_user.nrps_url,
        platform_issuer=current_user.platform_issuer,
        client_id=None
    )

    return SyncRosterResponse(
        success=result['success'],
        total_members=result.get('total_members'),
        error=result.get('error'),
        timestamp=result['timestamp']
    )


@router.get("/nrps/status/{course_id}")
async def get_roster_status(
    course_id: str,
    current_user: SessionClaims = Depends(get_current_user)
):
    """
    Get the current roster synchronization status for a course
    """
    # Check if course matches user's course
    if course_id != current_user.course_id:
        raise HTTPException(403, "Cannot access roster for different course")

    # Get roster info from database
    from ..database.connection import get_db_connection

    conn = await get_db_connection()
    try:
        # Get member count
        result = await conn.fetchrow("""
            SELECT COUNT(*) as member_count,
                   MAX(updated_at) as last_sync
            FROM course_members
            WHERE course_id = $1
        """, course_id)

        member_count = result['member_count'] if result else 0
        last_sync = result['last_sync'].isoformat() if result and result['last_sync'] else None

        # Get active students from Redis (presence)
        from ..database.connection import get_redis_connection
        redis = await get_redis_connection()
        active_students = await redis.zcard(f"presence:{course_id}")

        return {
            "course_id": course_id,
            "synced_members": member_count,
            "active_students": active_students,
            "last_sync": last_sync,
            "nrps_available": bool(current_user.nrps_url)
        }

    finally:
        await conn.close()


@router.post("/ags/score", response_model=SubmitScoreResponse)
async def submit_score(
    request: SubmitScoreRequest,
    current_user: SessionClaims = Depends(get_current_user)
):
    """
    Submit a score for a student.
    Requires instructor role and AGS configuration.
    """
    # Check if user is instructor
    if not current_user.is_instructor:
        raise HTTPException(403, "Only instructors can submit scores")

    # Check if AGS URL is available
    if not current_user.ags_url:
        raise HTTPException(400, "AGS not available for this course")

    try:
        # First ensure line item exists
        line_item = await ags_service.ensure_line_item(
            course_id=current_user.course_id,
            lineitems_url=current_user.ags_url,
            platform_issuer=current_user.platform_issuer,
            label="BluNote Participation",
            score_maximum=request.score_maximum
        )

        # Submit the score
        result = await ags_service.submit_score(
            user_id=request.user_id,
            line_item_url=line_item['id'],
            platform_issuer=current_user.platform_issuer,
            score=request.score,
            score_maximum=request.score_maximum,
            comment=request.comment
        )

        return SubmitScoreResponse(
            success=result['success'],
            error=result.get('error'),
            timestamp=result['timestamp']
        )

    except Exception as e:
        return SubmitScoreResponse(
            success=False,
            error=str(e),
            timestamp=datetime.utcnow().isoformat()
        )


@router.post("/ags/participation-scores")
async def submit_participation_scores(
    current_user: SessionClaims = Depends(get_current_user)
):
    """
    Calculate and submit participation scores for all students based on confusion events.
    Requires instructor role.
    """
    if not current_user.is_instructor:
        raise HTTPException(403, "Only instructors can submit scores")

    if not current_user.ags_url:
        raise HTTPException(400, "AGS not available for this course")

    from ..database.connection import get_db_connection
    from datetime import datetime

    conn = await get_db_connection()
    try:
        # Calculate participation scores based on confusion events
        result = await conn.fetch("""
            SELECT
                user_id,
                COUNT(*) as event_count,
                COUNT(DISTINCT DATE(occurred_at)) as active_days
            FROM events
            WHERE
                course_id = $1
                AND type = 'confused'
                AND occurred_at > NOW() - INTERVAL '30 days'
            GROUP BY user_id
        """, current_user.course_id)

        # Ensure line item exists
        line_item = await ags_service.ensure_line_item(
            course_id=current_user.course_id,
            lineitems_url=current_user.ags_url,
            platform_issuer=current_user.platform_issuer,
            label="BluNote Participation",
            score_maximum=100.0
        )

        # Submit scores for each student
        submitted = 0
        failed = 0

        for row in result:
            # Calculate score (simple algorithm: min(100, event_count * 10 + active_days * 5))
            score = min(100, row['event_count'] * 10 + row['active_days'] * 5)

            try:
                result = await ags_service.submit_score(
                    user_id=row['user_id'],
                    line_item_url=line_item['id'],
                    platform_issuer=current_user.platform_issuer,
                    score=score,
                    score_maximum=100.0,
                    comment=f"Participation based on {row['event_count']} interactions over {row['active_days']} days"
                )

                if result['success']:
                    submitted += 1
                else:
                    failed += 1

            except Exception as e:
                print(f"Failed to submit score for {row['user_id']}: {e}")
                failed += 1

        return {
            "success": True,
            "submitted": submitted,
            "failed": failed,
            "timestamp": datetime.utcnow().isoformat()
        }

    finally:
        await conn.close()