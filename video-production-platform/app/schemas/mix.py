"""Pydantic schemas for smart video mixing service."""
from __future__ import annotations


from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 混剪任务 — 创建 / 状态 / 审核 / 重试
# ---------------------------------------------------------------------------

class MixCreateRequest(BaseModel):
    """Request body for creating a mix task.

    Uses ``mixing_mode="auto"`` which automatically routes to the optimal
    pipeline (text-driven or vision-driven) based on asset analysis.
    """

    topic: str = Field(..., min_length=1, max_length=2000, description="视频主题")
    asset_ids: list[str] = Field(default=[], description="素材 ID 列表")
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
    video_count: int = Field(default=1, ge=1, le=10, description="输出视频数量")
    max_output_duration: int = Field(default=60, ge=15, le=300, description="每段输出视频最大时长（秒）")
    tts_text: Optional[str] = Field(default=None, description="TTS 配音文本（可选）")
    tts_voice: Optional[str] = Field(default=None, description="TTS 语音角色（可选）")
    bgm_enabled: bool = Field(default=False, description="是否启用背景音乐")
    bgm_asset_id: Optional[str] = Field(default=None, description="BGM 素材 ID（None 表示随机）")
    bgm_volume: float = Field(default=0.2, ge=0.0, le=1.0, description="BGM 音量比例")
    director_prompt: Optional[str] = Field(default=None, max_length=2000, description="AI 编导自定义指令（可选）")
    mixing_mode: str = Field(
        default="auto",
        pattern=r"^(auto)$",
        description="混剪模式。仅支持 'auto'，系统根据素材分析自动路由到最优 pipeline。",
    )
    subtitle_enabled: bool = Field(default=False, description="是否开启字幕（需先启用 TTS）")


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
    execution_phase: Optional[str] = None
    video_paths: Optional[list[str]] = None
    video_resolution: Optional[str] = None
    video_duration: Optional[float] = None
    video_file_size: Optional[int] = None
    error_message: Optional[str] = None
    ai_director_used: Optional[bool] = None


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


# ---------------------------------------------------------------------------
# 意图解析 — Intent Parsing
# ---------------------------------------------------------------------------

class ParseIntentRequest(BaseModel):
    """Request body for intent parsing preview."""

    director_prompt: str = Field(
        default="",
        max_length=2000,
        description="用户自然语言指令",
    )


class ParseIntentResponse(BaseModel):
    """Response with extracted mixing parameters."""

    strip_audio: bool = False
    video_count: int = 1
    max_output_duration: int = 60
    aspect_ratio: str = "9:16"
    bgm_enabled: bool = False
    subtitle_font: Optional[str] = None
    tts_text: Optional[str] = None
    editing_style: Optional[str] = None
    fade_out: bool = True
    fade_out_duration: float = 0.3


# ---------------------------------------------------------------------------
# 会话与消息持久化
# ---------------------------------------------------------------------------

class MixSessionCreateRequest(BaseModel):
    title: Optional[str] = Field(default="未命名会话", max_length=200)


class MixSessionResponse(BaseModel):
    session_id: str
    title: str
    last_task_id: Optional[str] = None
    last_message_preview: Optional[str] = None
    is_processing: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MixSessionListResponse(BaseModel):
    items: list[MixSessionResponse]
    total: int = 0
    page: int = 1
    page_size: int = 20
    has_more: bool = False


class MixSessionMessageItem(BaseModel):
    id: str
    sequence: int
    sender: str
    type: str
    content: Optional[str] = None
    extra: Optional[dict] = None
    created_at: Optional[str] = None


class MixSessionDetailResponse(BaseModel):
    session_id: str
    title: str
    last_task_id: Optional[str] = None
    messages: list[MixSessionMessageItem]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MixSessionMessageCreateRequest(BaseModel):
    sender: str = Field(..., pattern=r"^(user|system)$")
    type: str = Field(..., max_length=50)
    content: Optional[str] = Field(default="")
    extra: Optional[dict] = None


class MixSessionUpsertRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    last_task_id: Optional[str] = None
