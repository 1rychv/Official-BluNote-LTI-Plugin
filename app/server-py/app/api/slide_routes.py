"""
REST API routes for slide management
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional

from ..models.slide_models import SlideUpdate, SlideUpdateResponse, SlideInfo
from ..services.slide_service import slide_service
from ..security.guards import get_current_user
from ..lti.models import SessionClaims


router = APIRouter(prefix="/api/slides", tags=["slides"])


@router.post("/{course_id}/current", response_model=SlideUpdateResponse)
async def update_current_slide(
    course_id: str,
    slide_update: SlideUpdate,
    current_user: SessionClaims = Depends(get_current_user)
):
    """
    Update the current slide for a course.
    Requires instructor role.
    """
    # Check if user is instructor
    if not current_user.is_instructor:
        raise HTTPException(403, "Only instructors can update slides")

    # Check if course matches user's course
    if course_id != current_user.course_id:
        raise HTTPException(403, "Cannot update slides for different course")

    try:
        slide_info = await slide_service.store_current_slide(
            course_id=course_id,
            slide_number=slide_update.slide_number,
            slide_title=slide_update.slide_title,
            updated_by=current_user.user_id
        )

        return SlideUpdateResponse(
            success=True,
            message=f"Slide {slide_update.slide_number} updated successfully",
            slide=slide_info
        )

    except Exception as e:
        raise HTTPException(500, f"Failed to update slide: {str(e)}")


@router.get("/{course_id}/current", response_model=Optional[SlideInfo])
async def get_current_slide(
    course_id: str,
    current_user: SessionClaims = Depends(get_current_user)
):
    """
    Get the current slide for a course.
    Available to both instructors and students.
    """
    # Check if course matches user's course
    if course_id != current_user.course_id:
        raise HTTPException(403, "Cannot access slides for different course")

    try:
        slide_info = await slide_service.get_current_slide(course_id)
        return slide_info

    except Exception as e:
        raise HTTPException(500, f"Failed to get current slide: {str(e)}")


@router.delete("/{course_id}/current")
async def clear_current_slide(
    course_id: str,
    current_user: SessionClaims = Depends(get_current_user)
):
    """
    Clear the current slide for a course.
    Requires instructor role.
    """
    # Check if user is instructor
    if not current_user.is_instructor:
        raise HTTPException(403, "Only instructors can clear slides")

    # Check if course matches user's course
    if course_id != current_user.course_id:
        raise HTTPException(403, "Cannot clear slides for different course")

    try:
        cleared = await slide_service.clear_current_slide(course_id)

        if cleared:
            return {"success": True, "message": "Current slide cleared"}
        else:
            return {"success": False, "message": "No current slide to clear"}

    except Exception as e:
        raise HTTPException(500, f"Failed to clear slide: {str(e)}")