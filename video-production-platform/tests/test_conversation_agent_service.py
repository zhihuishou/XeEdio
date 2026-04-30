"""Tests for conversational agent core (T1-T3)."""

from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base, ConversationMessage, User, generate_uuid, utcnow
from app.services.conversation_agent_service import ConversationAgentService
from app.services.conversation_tool_registry import ConversationToolRegistry


def _build_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    user = User(
        id=generate_uuid(),
        username="tester",
        password_hash="hash",
        role="admin",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return db, user


def test_tool_registry_has_required_tools():
    db, user = _build_session()
    registry = ConversationToolRegistry(db, user)
    names = [tool["function"]["name"] for tool in registry.get_openai_tools()]
    assert names == ["create_mix", "get_task_status", "analyze_assets", "search_assets"]
    assert registry.execute("missing_tool", {})["ok"] is False


def test_agent_loop_tool_then_reply_persists_messages():
    db, user = _build_session()
    events: list[tuple[str, dict]] = []
    service = ConversationAgentService(db, user, event_callback=lambda e, p: events.append((e, p)))

    responses = [
        {
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "get_task_status",
                        "arguments": json.dumps({"task_id": "task-123"}),
                    },
                }
            ]
        },
        {"content": "任务状态是 processing，正在处理中。"},
    ]

    service._call_llm_with_retry = lambda *_args, **_kwargs: responses.pop(0)  # type: ignore[method-assign]
    service.tools.execute = lambda _name, _args: {  # type: ignore[method-assign]
        "ok": True,
        "result": {"task_id": "task-123", "status": "processing"},
        "context_updates": {"task_ids": ["task-123"]},
    }

    result = service.process_user_message(conversation_id=None, user_message="帮我看下任务状态")
    assert result["reply"].startswith("任务状态是")

    rows = db.query(ConversationMessage).order_by(ConversationMessage.created_at.asc()).all()
    assert [r.role for r in rows] == ["user", "tool", "assistant"]
    assert rows[1].tool_name == "get_task_status"
    assert rows[1].tool_call_id == "call-1"
    assert any(evt == "tool_call" for evt, _ in events)
    assert any(evt == "tool_result" for evt, _ in events)
    assert events[-1][0] == "complete"


def test_agent_loop_stops_at_max_steps():
    db, user = _build_session()
    service = ConversationAgentService(db, user)

    tool_message = {
        "tool_calls": [
            {
                "id": "loop-call",
                "type": "function",
                "function": {"name": "search_assets", "arguments": json.dumps({"query": "口播"})},
            }
        ]
    }
    service._call_llm_with_retry = lambda *_args, **_kwargs: tool_message  # type: ignore[method-assign]
    service.tools.execute = lambda _name, _args: {"ok": True, "result": {"items": []}}  # type: ignore[method-assign]

    result = service.process_user_message(conversation_id=None, user_message="继续")
    assert "10 步上限" in result["reply"]


def test_tool_registry_uses_context_fallback_for_task_status():
    db, user = _build_session()
    registry = ConversationToolRegistry(db, user, context_state={"task_ids": ["ctx-task-1"]})
    registry._mixing.get_status = lambda task_id: {"task_id": task_id, "status": "processing"}  # type: ignore[method-assign]

    result = registry.execute("get_task_status", {})
    assert result["ok"] is True
    assert result["result"]["task_id"] == "ctx-task-1"
    assert result["context_updates"]["last_task_status"]["status"] == "processing"


def test_agent_loop_handles_tool_argument_parse_error():
    db, user = _build_session()
    service = ConversationAgentService(db, user)

    responses = [
        {
            "tool_calls": [
                {
                    "id": "bad-call",
                    "type": "function",
                    "function": {
                        "name": "search_assets",
                        "arguments": '{"query": "口播"',  # malformed JSON
                    },
                }
            ]
        },
        {"content": "请补充更明确的搜索条件。"},
    ]
    service._call_llm_with_retry = lambda *_args, **_kwargs: responses.pop(0)  # type: ignore[method-assign]

    result = service.process_user_message(conversation_id=None, user_message="找素材")
    assert "请补充" in result["reply"]

    rows = db.query(ConversationMessage).order_by(ConversationMessage.created_at.asc()).all()
    assert [r.role for r in rows] == ["user", "tool", "assistant"]
    assert "tool_argument_parse_error" in rows[1].content


def test_process_user_message_accepts_asset_ids_into_context():
    db, user = _build_session()
    service = ConversationAgentService(db, user)
    service._call_llm_with_retry = lambda *_args, **_kwargs: {"content": "好的"}  # type: ignore[method-assign]

    result = service.process_user_message(
        conversation_id=None,
        user_message="用这些素材剪一条",
        asset_ids=["a-1", "a-2"],
    )
    conv = service.get_conversation(result["conversation_id"])
    state = service._load_agent_state(conv)
    assert state["asset_ids"] == ["a-1", "a-2"]
