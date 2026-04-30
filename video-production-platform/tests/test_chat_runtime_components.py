"""Tests for chat runtime coordination helpers."""

from app.services.chat_sse_broker import ChatSSEEvent, ChatSSEBroker
from app.services.conversation_runtime import ConversationRuntimeState


def test_runtime_state_prevents_duplicate_acquire():
    state = ConversationRuntimeState()
    assert state.acquire("c1") is True
    assert state.is_active("c1") is True
    assert state.acquire("c1") is False
    state.release("c1")
    assert state.is_active("c1") is False


def test_sse_broker_publish_subscribe_and_unsubscribe():
    broker = ChatSSEBroker()
    q1 = broker.subscribe("c1")
    q2 = broker.subscribe("c1")

    broker.publish("c1", "thinking", {"step": 1})

    e1: ChatSSEEvent = q1.get(timeout=1)
    e2: ChatSSEEvent = q2.get(timeout=1)
    assert e1.event == "thinking"
    assert e2.data["step"] == 1
    assert "event: thinking" in broker.encode_sse(e1)

    broker.unsubscribe("c1", q1)
    broker.publish("c1", "complete", {})
    e2_second: ChatSSEEvent = q2.get(timeout=1)
    assert e2_second.event == "complete"
