"""In-memory runtime state for conversation loop coordination."""

from __future__ import annotations

import threading


class ConversationRuntimeState:
    """Tracks active conversation runs to prevent concurrent loops."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[str] = set()

    def acquire(self, conversation_id: str) -> bool:
        with self._lock:
            if conversation_id in self._active:
                return False
            self._active.add(conversation_id)
            return True

    def release(self, conversation_id: str) -> None:
        with self._lock:
            self._active.discard(conversation_id)

    def is_active(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._active


runtime_state = ConversationRuntimeState()
