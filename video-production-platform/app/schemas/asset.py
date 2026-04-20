"""Pydantic schemas for asset management."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AssetResponse(BaseModel):
    """Single asset response model."""

    id: str
    filename: str
    original_filename: str
    category: str
    media_type: str
    file_format: str
    file_size: int
    duration: Optional[float] = None
    thumbnail_path: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AssetListResponse(BaseModel):
    """Paginated asset list response."""

    items: list[AssetResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class AssetUploadResponse(BaseModel):
    """Response after successful asset upload."""

    id: str
    filename: str
    original_filename: str
    category: str
    media_type: str
    file_format: str
    file_size: int
    duration: Optional[float] = None
    thumbnail_path: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
