"""Pydantic schemas for conversational agent APIs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatSendRequest(BaseModel):
    conversation_id: str | None = Field(default=None, description="Existing conversation ID")
    message: str = Field(..., min_length=1, max_length=4000, description="User message")
    asset_ids: list[str] = Field(default_factory=list, description="Selected asset IDs for context")


class ChatSendResponse(BaseModel):
    conversation_id: str
    accepted: bool = True
    message: str = "消息已接收，Agent 正在处理中"


class ConversationItem(BaseModel):
    id: str
    title: str
    last_message_preview: str | None = None
    is_processing: bool = False
    updated_at: str | None = None
    created_at: str | None = None


class ConversationListResponse(BaseModel):
    items: list[ConversationItem]
    total: int = 0
    page: int = 1
    page_size: int = 20
    has_more: bool = False


class ConversationMessageItem(BaseModel):
    id: str
    role: str
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    created_at: str | None = None


class ConversationDetailResponse(BaseModel):
    id: str
    title: str
    asset_ids: list[str] = Field(default_factory=list)
    agent_state: dict[str, Any] = Field(default_factory=dict)
    messages: list[ConversationMessageItem]
    created_at: str | None = None
    updated_at: str | None = None


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
