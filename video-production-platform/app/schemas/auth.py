"""Pydantic schemas for authentication."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Login request body."""

    username: str = Field(..., min_length=1, description="Username")
    password: str = Field(..., min_length=1, description="Password")


class LoginResponse(BaseModel):
    """Login response with JWT token."""

    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    role: str
