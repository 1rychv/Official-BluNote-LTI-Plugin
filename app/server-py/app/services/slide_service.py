"""
Slide tracking service for managing current slide state
"""
import json
from typing import Optional
from datetime import datetime

from ..database.connection import get_redis_connection
from ..models.slide_models import SlideInfo, SlideContext


class SlideService:
    """Service for managing slide state in Redis"""

    def __init__(self):
        self.slide_ttl = 7200  # 2 hours

    def _get_slide_key(self, course_id: str) -> str:
        """Get Redis key for current slide"""
        return f"slide:current:{course_id}"

    async def store_current_slide(
        self,
        course_id: str,
        slide_number: int,
        slide_title: str,
        updated_by: str
    ) -> SlideInfo:
        """Store current slide information in Redis"""
        slide_info = SlideInfo(
            slide_number=slide_number,
            slide_title=slide_title,
            course_id=course_id,
            updated_at=datetime.utcnow(),
            updated_by=updated_by
        )

        redis = await get_redis_connection()
        slide_key = self._get_slide_key(course_id)

        # Store slide data as JSON
        slide_data = slide_info.model_dump_json()
        await redis.setex(slide_key, self.slide_ttl, slide_data)

        return slide_info

    async def get_current_slide(self, course_id: str) -> Optional[SlideInfo]:
        """Get current slide information from Redis"""
        redis = await get_redis_connection()
        slide_key = self._get_slide_key(course_id)

        slide_data = await redis.get(slide_key)
        if not slide_data:
            return None

        try:
            slide_dict = json.loads(slide_data)
            return SlideInfo(**slide_dict)
        except (json.JSONDecodeError, ValueError):
            # Clean up corrupted data
            await redis.delete(slide_key)
            return None

    async def get_slide_context(self, course_id: str) -> SlideContext:
        """Get slide context for confusion tracking"""
        current_slide = await self.get_current_slide(course_id)

        if not current_slide:
            return SlideContext()

        return SlideContext(
            slide_number=current_slide.slide_number,
            slide_title=current_slide.slide_title,
            timestamp=current_slide.updated_at
        )

    async def clear_current_slide(self, course_id: str) -> bool:
        """Clear current slide information"""
        redis = await get_redis_connection()
        slide_key = self._get_slide_key(course_id)

        result = await redis.delete(slide_key)
        return result > 0


# Global service instance
slide_service = SlideService()