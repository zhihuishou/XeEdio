"""Pydantic schemas for user management."""
from __future__ import annotations


from datetime import datetime

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    """Request body for creating a user."""

    username: str = Field(..., min_length=1, max_length=50, description="Username")
    password: str = Field(..., min_length=6, max_length=128, description="Password")
    role: str = Field(default="intern", description="User role: intern, operator, or admin")


class UserUpdate(BaseModel):
    """Request body for updating a user."""

    username: str | None = Field(None, min_length=1, max_length=50)
    password: str | None = Field(None, min_length=6, max_length=128)
    role: str | None = Field(None, description="User role: intern, operator, or admin")


class UserResponse(BaseModel):
    """User response model (excludes password_hash)."""

    id: str
    username: str
    role: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
