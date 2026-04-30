"""In-memory SSE broker for conversation event streaming."""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class ChatSSEEvent:
    event: str
    data: dict[str, Any]


class ChatSSEBroker:
    """Thread-safe pub/sub broker keyed by conversation_id."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[queue.Queue]] = {}

    def subscribe(self, conversation_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.setdefault(conversation_id, []).append(q)
        return q

    def unsubscribe(self, conversation_id: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(conversation_id) or []
            if q in subs:
                subs.remove(q)
            if not subs and conversation_id in self._subscribers:
                self._subscribers.pop(conversation_id, None)

    def publish(self, conversation_id: str, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers.get(conversation_id) or [])
        payload = ChatSSEEvent(event=event, data=data)
        for q in subscribers:
            q.put(payload)

    @staticmethod
    def encode_sse(event: ChatSSEEvent) -> str:
        data = json.dumps(event.data, ensure_ascii=False)
        return f"event: {event.event}\ndata: {data}\n\n"


sse_broker = ChatSSEBroker()
