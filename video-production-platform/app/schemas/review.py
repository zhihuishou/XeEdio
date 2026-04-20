"""Pydantic schemas for review endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ReviewRejectRequest(BaseModel):
    """Request body for rejecting a video."""
    reason: str


class ReviewLogResponse(BaseModel):
    """Response model for a review log entry."""
    id: str
    task_id: str
    reviewer_id: str
    action: str
    reason: Optional[str] = None
    topic: Optional[str] = None
    copywriting_snapshot: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewTaskResponse(BaseModel):
    """Response model for a pending review task."""
    id: str
    topic: str
    status: str
    copywriting_final: Optional[str] = None
    video_path: Optional[str] = None
    video_resolution: Optional[str] = None
    video_duration: Optional[float] = None
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PendingReviewsResponse(BaseModel):
    """Response model for pending reviews list."""
    tasks: list[ReviewTaskResponse]
    total: int
