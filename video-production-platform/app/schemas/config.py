"""Pydantic schemas for system configuration."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ConfigItem(BaseModel):
    """Single configuration item response."""

    key: str
    value: Optional[str] = None
    description: Optional[str] = None
    updated_at: Optional[str] = None


class ConfigUpdateRequest(BaseModel):
    """Request body for updating configuration items.

    Accepts a dict of key-value pairs to update.
    """

    configs: dict[str, str] = Field(
        ...,
        description="Key-value pairs to update",
        examples=[{"llm_api_url": "https://api.deepseek.com", "llm_model": "deepseek-chat"}],
    )


class ConfigResponse(BaseModel):
    """Response containing all configuration items."""

    configs: dict[str, ConfigItem]


class LLMProviderItem(BaseModel):
    """LLM provider info (without API key)."""

    id: str
    name: str
    model: str
    key_hint: str


class LLMProvidersResponse(BaseModel):
    """Response containing available LLM providers."""

    providers: list[LLMProviderItem]
