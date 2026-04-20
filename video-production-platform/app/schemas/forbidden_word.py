"""Pydantic schemas for forbidden word management."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ForbiddenWordCreate(BaseModel):
    """Request body for creating a forbidden word."""

    word: str = Field(..., min_length=1, description="The forbidden word or phrase")
    category: Optional[str] = Field(None, description="Category of the forbidden word")
    suggestion: Optional[str] = Field(None, description="Suggested replacement")


class ForbiddenWordResponse(BaseModel):
    """Response for a single forbidden word."""

    id: str
    word: str
    category: Optional[str] = None
    suggestion: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class ForbiddenWordListResponse(BaseModel):
    """Response for forbidden word list."""

    items: list[ForbiddenWordResponse]
    total: int


class ForbiddenWordImportRequest(BaseModel):
    """Request body for batch importing forbidden words."""

    words: list[ForbiddenWordCreate] = Field(..., min_length=1, description="List of words to import")


class ForbiddenWordImportResponse(BaseModel):
    """Response for batch import."""

    imported: int
    skipped: int


class ForbiddenWordCheckRequest(BaseModel):
    """Request body for checking text against forbidden words."""

    text: str = Field(..., min_length=1, description="Text to check for forbidden words")


class ForbiddenWordMatch(BaseModel):
    """A single forbidden word match in text."""

    word: str
    position: int
    category: Optional[str] = None
    suggestion: Optional[str] = None


class ForbiddenWordCheckResponse(BaseModel):
    """Response for forbidden word check."""

    status: str = Field(..., description="'passed' or 'contains_forbidden'")
    matches: list[ForbiddenWordMatch] = []
    text: str
