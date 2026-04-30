"""Conversational agent APIs with SSE event streaming."""

from __future__ import annotations

import asyncio
import queue
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.models.database import Conversation, SessionLocal, User, get_db
from app.schemas.chat import (
    ChatSendRequest,
    ChatSendResponse,
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationItem,
    ConversationListResponse,
    ConversationMessageItem,
    ConversationUpdateRequest,
)
from app.services.chat_sse_broker import ChatSSEEvent, sse_broker
from app.services.conversation_agent_service import ConversationAgentService
from app.services.conversation_runtime import runtime_state
from app.services.auth_service import decode_access_token
from app.utils.auth import require_role
from app.utils.errors import AuthError, StateTransitionError

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _run_agent_loop(
    conversation_id: str,
    user_message: str,
    user_id: str,
    asset_ids: list[str] | None = None,
) -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            sse_broker.publish(conversation_id, "error", {"message": "用户不存在"})
            return
        service = ConversationAgentService(
            db,
            user,
            event_callback=lambda event, payload: sse_broker.publish(conversation_id, event, payload),
        )
        service.process_user_message(
            conversation_id=conversation_id,
            user_message=user_message,
            asset_ids=asset_ids,
        )
    except Exception as exc:  # noqa: BLE001
        sse_broker.publish(conversation_id, "error", {"message": str(exc)})
    finally:
        runtime_state.release(conversation_id)
        db.close()


@router.post("/send", response_model=ChatSendResponse)
def send_message(
    body: ChatSendRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    service = ConversationAgentService(db, current_user)
    conversation_id = body.conversation_id

    if conversation_id:
        service.get_conversation(conversation_id)
    else:
        conversation = service.create_conversation()
        conversation_id = conversation.id

    if runtime_state.is_active(conversation_id):
        raise StateTransitionError(
            message="该会话当前正在处理中，请等待完成后再发送新消息",
        )

    runtime_state.acquire(conversation_id)
    background_tasks.add_task(
        _run_agent_loop,
        conversation_id,
        body.message,
        current_user.id,
        body.asset_ids,
    )
    return ChatSendResponse(conversation_id=conversation_id)


@router.post("/conversations", response_model=ConversationItem)
def create_conversation(
    body: ConversationCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    service = ConversationAgentService(db, current_user)
    conversation = service.create_conversation()
    if body.title and body.title.strip():
        conversation = service.update_conversation_title(conversation.id, body.title)
    return _conversation_to_item(conversation)


@router.get("/{conversation_id}/stream")
async def stream_events(
    conversation_id: str,
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    current_user = _resolve_stream_user(db, request, token)
    service = ConversationAgentService(db, current_user)
    service.get_conversation(conversation_id)

    q = sse_broker.subscribe(conversation_id)

    async def event_generator():
        try:
            while True:
                try:
                    event: ChatSSEEvent = await asyncio.to_thread(q.get, True, 30)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue

                yield sse_broker.encode_sse(event)
                if event.event in ("complete", "error"):
                    break
        finally:
            sse_broker.unsubscribe(conversation_id, q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _resolve_stream_user(db: Session, request: Request, token: str | None) -> User:
    auth_header = request.headers.get("Authorization", "")
    bearer_token = ""
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header.removeprefix("Bearer ").strip()
    jwt_token = bearer_token or (token or "")
    if not jwt_token:
        raise AuthError(message="Missing token for stream")

    payload = decode_access_token(jwt_token)
    user_id = payload.get("sub") if payload else None
    if not user_id:
        raise AuthError(message="Invalid or expired token")
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise AuthError(message="User not found")
    if user.role not in {"intern", "operator", "admin"}:
        raise AuthError(message="Role not allowed")
    return user


@router.get("/conversations", response_model=ConversationListResponse)
def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    service = ConversationAgentService(db, current_user)
    conversations = service.list_conversations()
    total = len(conversations)
    start = (page - 1) * page_size
    end = start + page_size
    slice_items = conversations[start:end]
    items = [_conversation_to_item(c) for c in slice_items]
    return ConversationListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=end < total,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation_detail(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    service = ConversationAgentService(db, current_user)
    conversation = service.get_conversation(conversation_id)
    return _to_conversation_detail(conversation)


@router.put("/conversations/{conversation_id}", response_model=ConversationItem)
def update_conversation(
    conversation_id: str,
    body: ConversationUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    service = ConversationAgentService(db, current_user)
    conversation = service.get_conversation(conversation_id)
    if body.title is not None:
        conversation = service.update_conversation_title(conversation_id, body.title)
    return _conversation_to_item(conversation)


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("intern", "operator", "admin")),
):
    service = ConversationAgentService(db, current_user)
    service.delete_conversation(conversation_id)
    return {"conversation_id": conversation_id, "deleted": True}


def _to_conversation_detail(conversation: Conversation) -> ConversationDetailResponse:
    import json

    raw_state = conversation.agent_state or "{}"
    raw_assets = conversation.asset_ids or "[]"
    try:
        state: dict[str, Any] = json.loads(raw_state)
    except Exception:  # noqa: BLE001
        state = {}
    try:
        asset_ids: list[str] = json.loads(raw_assets)
    except Exception:  # noqa: BLE001
        asset_ids = []
    messages = [
        ConversationMessageItem(
            id=m.id,
            role=m.role,
            content=m.content or "",
            tool_name=m.tool_name,
            tool_call_id=m.tool_call_id,
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m in sorted(
            conversation.messages,
            key=lambda row: row.created_at.isoformat() if row.created_at else "",
        )
    ]
    return ConversationDetailResponse(
        id=conversation.id,
        title=_conversation_title(conversation, state),
        asset_ids=asset_ids,
        agent_state=state,
        messages=messages,
        created_at=conversation.created_at.isoformat() if conversation.created_at else None,
        updated_at=conversation.updated_at.isoformat() if conversation.updated_at else None,
    )


def _conversation_to_item(conversation: Conversation) -> ConversationItem:
    import json

    state_raw = conversation.agent_state or "{}"
    try:
        state = json.loads(state_raw)
    except Exception:  # noqa: BLE001
        state = {}
    latest = conversation.messages[-1] if conversation.messages else None
    preview = (latest.content or "").strip() if latest else None
    if preview:
        preview = preview[:60] + ("..." if len(preview) > 60 else "")
    return ConversationItem(
        id=conversation.id,
        title=_conversation_title(conversation, state),
        last_message_preview=preview,
        is_processing=runtime_state.is_active(conversation.id),
        created_at=conversation.created_at.isoformat() if conversation.created_at else None,
        updated_at=conversation.updated_at.isoformat() if conversation.updated_at else None,
    )


def _conversation_title(conversation: Conversation, state: dict[str, Any]) -> str:
    custom_title = str((state or {}).get("custom_title") or "").strip()
    if custom_title:
        return custom_title
    first_user = next((m for m in conversation.messages if m.role == "user" and (m.content or "").strip()), None)
    if first_user:
        title = first_user.content.strip()
        return title[:24] + ("..." if len(title) > 24 else "")
    return "未命名会话"
