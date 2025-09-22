import time
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..lti.middleware import verify_token, require_course_access


router = APIRouter()


@router.get('/course/{course_id}/metrics')
async def get_metrics(course_id: str, claims: Dict[str, Any] = Depends(verify_token)):
    """Get confusion metrics for a course"""
    # Verify user has access to this course
    if claims.get("course_id") != course_id:
        raise HTTPException(403, "Access denied to this course")

    # Import here to avoid circular imports
    from ..main import compute_metrics

    data = {
        "courseId": course_id,
        "threshold": 25,  # TODO: Make configurable
        **compute_metrics(course_id)
    }
    return JSONResponse(content=data)


@router.get('/course/{course_id}/roster')
async def get_roster(course_id: str, claims: Dict[str, Any] = Depends(verify_token)):
    """Get course roster count"""
    # Verify user has access to this course
    if claims.get("course_id") != course_id:
        raise HTTPException(403, "Access denied to this course")

    # Import here to avoid circular imports
    from ..main import get_course

    course = get_course(course_id)
    return {
        "courseId": course_id,
        "roster": int(course.roster or 0)
    }


@router.post('/course/{course_id}/roster')
async def post_roster(course_id: str, payload: Dict[str, Any], claims: Dict[str, Any] = Depends(verify_token)):
    """Update course roster count (instructor only)"""
    # Verify user has access to this course
    if claims.get("course_id") != course_id:
        raise HTTPException(403, "Access denied to this course")

    if not claims.get("is_instructor", False):
        raise HTTPException(403, "Instructor role required")

    roster = payload.get('roster')
    try:
        value = int(roster)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid roster value")

    if value < 1:
        raise HTTPException(400, "Roster must be at least 1")

    # Import here to avoid circular imports
    from ..main import get_course

    course = get_course(course_id)
    course.roster = value

    return {
        "ok": True,
        "courseId": course_id,
        "roster": value
    }


@router.get('/user/{user_id}/tutoring')
async def get_tutoring(
    user_id: str,
    claims: Dict[str, Any] = Depends(verify_token)
):
    """Get tutoring content for a user"""
    # Ensure user can only access their own tutoring content
    if claims["user_id"] != user_id and not claims.get("is_instructor", False):
        raise HTTPException(403, "Access denied")

    # Import here to avoid circular imports
    from ..main import state

    content = None
    for course in state.courses.values():
        if user_id in course.tutoringByUser:
            content = course.tutoringByUser[user_id]
            break

    return {
        "userId": user_id,
        "content": content
    }


@router.post('/confused')
async def report_confused(
    payload: Dict[str, Any],
    claims: Dict[str, Any] = Depends(verify_token)
):
    """Report confusion (students only)"""
    if not claims.get("is_student", False):
        raise HTTPException(403, "Only students can report confusion")

    # Extract data from payload or use claims
    course_id = payload.get("courseId") or claims.get("course_id")
    user_id = payload.get("userId") or claims.get("user_id")

    # Ensure student can only report for themselves
    if user_id != claims["user_id"]:
        raise HTTPException(403, "Cannot report confusion for another user")

    # Import here to avoid circular imports
    from ..main import record_press, maybe_trigger, get_course

    # Record the confusion press
    accepted = record_press(course_id, user_id)

    if accepted:
        # Update presence
        course = get_course(course_id)
        course.presenceByUser[user_id] = time.time() * 1000

        # Check for threshold trigger
        await maybe_trigger(course_id)

    return {"status": "recorded", "accepted": accepted}