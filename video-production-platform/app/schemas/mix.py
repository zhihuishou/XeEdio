"""Pydantic schemas for smart video mixing service."""

from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 混剪任务 — 创建 / 状态 / 审核 / 重试
# ---------------------------------------------------------------------------

class MixCreateRequest(BaseModel):
    """Request body for creating a mix task."""

    topic: str = Field(..., min_length=1, max_length=200, description="视频主题")
    a_roll_asset_ids: list[str] = Field(..., min_length=1, description="A-Roll 素材 ID 列表（至少一个）")
    b_roll_asset_ids: list[str] = Field(default=[], description="B-Roll 素材 ID 列表（可选）")
    aspect_ratio: str = Field(
        default="9:16",
        pattern=r"^(16:9|9:16|1:1)$",
        description="画面比例：16:9 | 9:16 | 1:1",
    )
    transition: str = Field(
        default="none",
        pattern=r"^(none|fade_in|fade_out|slide_in|slide_out|shuffle)$",
        description="转场效果",
    )
    clip_duration: int = Field(default=5, ge=2, le=15, description="片段时长（秒）")
    concat_mode: str = Field(
        default="random",
        pattern=r"^(random|sequential)$",
        description="拼接模式：random | sequential",
    )
    video_count: int = Field(default=1, ge=1, le=5, description="输出视频数量")
    tts_text: Optional[str] = Field(default=None, description="TTS 配音文本（可选）")
    tts_voice: Optional[str] = Field(default=None, description="TTS 语音角色（可选）")
    bgm_enabled: bool = Field(default=False, description="是否启用背景音乐")
    bgm_asset_id: Optional[str] = Field(default=None, description="BGM 素材 ID（None 表示随机）")
    bgm_volume: float = Field(default=0.2, ge=0.0, le=1.0, description="BGM 音量比例")


class MixCreateResponse(BaseModel):
    """Response after creating a mix task."""

    task_id: str
    status: str
    message: str = "混剪任务已创建"


class MixStatusResponse(BaseModel):
    """Response for querying mix task status."""

    task_id: str
    status: str
    progress: Optional[str] = None
    video_paths: Optional[list[str]] = None
    video_resolution: Optional[str] = None
    video_duration: Optional[float] = None
    video_file_size: Optional[int] = None
    error_message: Optional[str] = None


class SubmitReviewResponse(BaseModel):
    """Response after submitting a mix task for review."""

    task_id: str
    status: str
    message: str = "已提交审核"


class RetryResponse(BaseModel):
    """Response after retrying a failed / rejected mix task."""

    task_id: str
    status: str
    message: str = "已重新开始混剪"


# ---------------------------------------------------------------------------
# Pexels 搜索与下载
# ---------------------------------------------------------------------------

class PexelsSearchRequest(BaseModel):
    """Request body for searching Pexels videos."""

    keywords: list[str] = Field(..., min_length=1, description="搜索关键词列表")
    aspect_ratio: str = Field(
        default="9:16",
        pattern=r"^(16:9|9:16|1:1)$",
        description="画面比例过滤",
    )
    per_page: int = Field(default=10, ge=1, le=20, description="每页结果数量")


class PexelsVideoItem(BaseModel):
    """A single Pexels video item in search results."""

    url: str
    thumbnail_url: str
    duration: int
    width: int
    height: int


class PexelsSearchResponse(BaseModel):
    """Response for Pexels video search."""

    videos: list[PexelsVideoItem]
    total: int


class PexelsDownloadRequest(BaseModel):
    """Request body for downloading a Pexels video."""

    video_url: str


class PexelsDownloadResponse(BaseModel):
    """Response after downloading a Pexels video."""

    asset_id: str
    file_path: str


# ---------------------------------------------------------------------------
# LLM 关键词生成
# ---------------------------------------------------------------------------

class KeywordGenerateRequest(BaseModel):
    """Request body for generating search keywords via LLM."""

    topic: str = Field(..., min_length=1, max_length=500, description="视频主题或描述")


class KeywordGenerateResponse(BaseModel):
    """Response with generated keywords."""

    keywords: list[str]
