"""
Pydantic models for slide tracking functionality
"""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field


class SlideUpdate(BaseModel):
    """Model for slide update requests"""
    slide_number: int = Field(ge=1, description="Slide number (1-based)")
    slide_title: str = Field(max_length=200, description="Brief title or topic of the slide")


class SlideInfo(BaseModel):
    """Model for slide information with metadata"""
    slide_number: int
    slide_title: str
    course_id: str
    updated_at: datetime
    updated_by: str  # user_id of instructor who set the slide


class SlideUpdateResponse(BaseModel):
    """Response model for slide update operations"""
    success: bool
    message: str
    slide: Optional[SlideInfo] = None


class SlideContext(BaseModel):
    """Model for slide context used in confusion tracking"""
    slide_number: Optional[int] = None
    slide_title: Optional[str] = None
    timestamp: Optional[datetime] = None