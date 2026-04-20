"""Pydantic schemas for task endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TaskCreate(BaseModel):
    """Request body for creating a task."""
    topic: str


class TaskAssetInfo(BaseModel):
    """Asset info within a task."""
    id: str
    asset_id: str
    roll_type: str
    sequence_order: int


class TaskResponse(BaseModel):
    """Response model for a single task."""
    id: str
    topic: str
    status: str
    copywriting_raw: Optional[str] = None
    copywriting_filtered: Optional[str] = None
    copywriting_final: Optional[str] = None
    tts_voice: Optional[str] = None
    tts_audio_path: Optional[str] = None
    tts_duration: Optional[float] = None
    video_path: Optional[str] = None
    video_resolution: Optional[str] = None
    video_duration: Optional[float] = None
    video_file_size: Optional[int] = None
    review_comment: Optional[str] = None
    created_by: str
    batch_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """Response model for task list."""
    tasks: list[TaskResponse]
    total: int
