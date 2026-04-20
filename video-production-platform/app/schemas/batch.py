"""Pydantic schemas for batch task endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class BatchCreate(BaseModel):
    """Request body for creating a batch task."""
    topics: list[str] = Field(..., min_length=1)
    versions_per_topic: int = Field(default=1, ge=1)


class BatchTaskResponse(BaseModel):
    """Response model for a batch task."""
    id: str
    created_by: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BatchDetailResponse(BaseModel):
    """Response model for batch task detail with progress."""
    id: str
    created_by: str
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    status: str
    progress_percent: float
    created_at: datetime
    updated_at: datetime
    tasks: list[dict] = []

    model_config = {"from_attributes": True}


class BatchListResponse(BaseModel):
    """Response model for batch task list."""
    batches: list[BatchTaskResponse]
    total: int
