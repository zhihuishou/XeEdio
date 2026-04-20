"""Pydantic schemas for video composition service."""

from typing import Optional

from pydantic import BaseModel, Field


class ComposeRequest(BaseModel):
    """Request body for triggering video composition."""

    a_roll_assets: list[str] = Field(..., min_length=1, description="List of A-roll asset IDs")
    b_roll_assets: list[str] = Field(default=[], description="List of B-roll asset IDs")
    transition: str = Field(default="fade", description="Transition effect (e.g., fade)")
    resolution: Optional[str] = Field(None, description="Output resolution (e.g., 1080x1920)")
    bitrate: Optional[str] = Field(None, description="Output bitrate (e.g., 8M)")


class ComposeResponse(BaseModel):
    """Response for video composition."""

    task_id: str
    status: str
    video_path: str
    video_resolution: str
    video_duration: float
    video_file_size: int
    message: str = "视频合成完成"


class CompositionStatusResponse(BaseModel):
    """Response for composition status query."""

    task_id: str
    status: str
    video_path: Optional[str] = None
    video_resolution: Optional[str] = None
    video_duration: Optional[float] = None
    video_file_size: Optional[int] = None
