"""Pydantic schemas for copywriting service."""

from typing import Optional

from pydantic import BaseModel, Field


class CopywritingGenerateRequest(BaseModel):
    """Request body for generating copywriting."""

    topic: str = Field(..., min_length=1, description="Video topic for copywriting generation")
    task_id: Optional[str] = Field(None, description="Existing task ID to update, or auto-create if omitted")
    provider_id: Optional[str] = Field(None, description="LLM provider ID from config.yaml (e.g. deepseek, doubao-pro, gpt-4o-mini")
    api_key: Optional[str] = Field(None, description="API key for the selected provider (overrides config.yaml)")


class CopywritingEditRequest(BaseModel):
    """Request body for editing copywriting."""

    copywriting_final: str = Field(..., min_length=1, description="Edited copywriting text")


class ForbiddenWordMatchItem(BaseModel):
    """A forbidden word match."""

    word: str
    position: int
    category: Optional[str] = None
    suggestion: Optional[str] = None


class CopywritingResponse(BaseModel):
    """Response for copywriting operations."""

    task_id: str
    topic: str
    status: str
    copywriting_raw: Optional[str] = None
    copywriting_filtered: Optional[str] = None
    copywriting_final: Optional[str] = None
    filter_status: Optional[str] = None
    matches: list[ForbiddenWordMatchItem] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CopywritingConfirmResponse(BaseModel):
    """Response for copywriting confirmation."""

    task_id: str
    status: str
    copywriting_final: str
    message: str = "文案已确认锁定"
