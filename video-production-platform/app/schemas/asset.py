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

    # Analysis fields (from asset_analysis table)
    analysis_status: Optional[str] = None       # pending / analyzing / completed / failed
    analysis_role: Optional[str] = None         # presenter / product_closeup / lifestyle / ...
    analysis_description: Optional[str] = None
    analysis_has_speech: Optional[bool] = None

    model_config = {"from_attributes": True}


class AssetAnalysisResponse(BaseModel):
    """Full analysis result for an asset (from asset_analysis table)."""

    status: str                                     # pending / analyzing / completed / failed
    error_message: Optional[str] = None

    # VLM structured output
    description: Optional[str] = None
    role: Optional[str] = None                      # presenter / product_closeup / lifestyle / transition / other
    visual_quality: Optional[str] = None            # high / medium / low
    scene_tags: Optional[list] = None               # ["室内", "美妆", "产品展示"]
    key_moments: Optional[list] = None              # [{"time": 5.0, "desc": "..."}]

    # Audio metadata
    audio_quality: Optional[str] = None             # good / noisy / silent
    has_speech: Optional[bool] = None
    speech_ranges: Optional[list] = None            # [[0, 230], [1650, 1664]]
    transcript: Optional[str] = None

    # Meta
    vlm_model: Optional[str] = None
    analyzed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AssetDetailResponse(BaseModel):
    """Asset detail response with full analysis data."""

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

    # Full analysis (None if no analysis record exists)
    analysis: Optional[AssetAnalysisResponse] = None

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


class BatchUploadItemResult(BaseModel):
    """Result for a single file in a batch upload."""

    original_filename: str
    success: bool
    asset: Optional[AssetUploadResponse] = None
    error: Optional[str] = None


class BatchUploadResponse(BaseModel):
    """Response after batch asset upload."""

    total: int
    succeeded: int
    failed: int
    results: list[BatchUploadItemResult]


class ReanalyzeResponse(BaseModel):
    """Response after triggering asset re-analysis."""

    asset_id: str
    status: str
    message: str


class AssetSearchItem(BaseModel):
    """Single item in semantic search results."""

    id: str
    original_filename: str
    category: str
    media_type: str
    # From asset_analysis
    description: Optional[str] = None
    role: Optional[str] = None
    scene_tags: Optional[list] = None
    relevance_score: Optional[float] = None

    model_config = {"from_attributes": True}


class AssetSearchResponse(BaseModel):
    """Semantic search response."""

    items: list[AssetSearchItem]
    total: int
