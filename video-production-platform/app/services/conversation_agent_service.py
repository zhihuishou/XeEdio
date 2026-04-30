"""Core conversational agent service with function-calling loop."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import httpx
from sqlalchemy.orm import Session

from app.models.database import (
    Conversation,
    ConversationMessage,
    User,
    generate_uuid,
    utcnow,
)
from app.services.conversation_tool_registry import ConversationToolRegistry
from app.services.external_config import ExternalConfig
from app.utils.errors import NotFoundError

EventCallback = Callable[[str, dict[str, Any]], None]
logger = logging.getLogger("app.conversation_agent")


class ConversationAgentService:
    """Provides conversation persistence and agent loop execution."""

    MAX_LOOP_STEPS = 10
    RETRY_BACKOFF_SECONDS = (1, 3)

    def __init__(
        self,
        db: Session,
        current_user: User,
        *,
        event_callback: EventCallback | None = None,
    ):
        self.db = db
        self.current_user = current_user
        self.event_callback = event_callback or (lambda _event, _payload: None)
        self.config = ExternalConfig.get_instance()
        self.tools = ConversationToolRegistry(db, current_user)

    # ------------------------------------------------------------------
    # Conversation CRUD
    # ------------------------------------------------------------------
    def create_conversation(self) -> Conversation:
        now = utcnow()
        conversation = Conversation(
            id=generate_uuid(),
            user_id=self.current_user.id,
            asset_ids=json.dumps([], ensure_ascii=False),
            agent_state=json.dumps(self._default_agent_state(), ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def get_conversation(self, conversation_id: str) -> Conversation:
        conversation = (
            self.db.query(Conversation)
            .filter(
                Conversation.id == conversation_id,
                Conversation.user_id == self.current_user.id,
            )
            .first()
        )
        if not conversation:
            raise NotFoundError(message="会话不存在")
        return conversation

    def list_conversations(self) -> list[Conversation]:
        return (
            self.db.query(Conversation)
            .filter(Conversation.user_id == self.current_user.id)
            .order_by(Conversation.updated_at.desc())
            .all()
        )

    def delete_conversation(self, conversation_id: str) -> None:
        conversation = self.get_conversation(conversation_id)
        self.db.delete(conversation)
        self.db.commit()

    def update_conversation_title(self, conversation_id: str, title: str) -> Conversation:
        conversation = self.get_conversation(conversation_id)
        self._apply_context_updates(conversation, {"custom_title": title.strip()})
        return self.get_conversation(conversation_id)

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------
    def append_message(
        self,
        conversation: Conversation,
        *,
        role: str,
        content: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> ConversationMessage:
        message = ConversationMessage(
            id=generate_uuid(),
            conversation_id=conversation.id,
            role=role,
            content=content or "",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            created_at=utcnow(),
        )
        conversation.updated_at = utcnow()
        self.db.add(message)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(message)
        return message

    def get_messages(self, conversation: Conversation) -> list[ConversationMessage]:
        return (
            self.db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.created_at.asc())
            .all()
        )

    # ------------------------------------------------------------------
    # Agent loop
    # ------------------------------------------------------------------
    def process_user_message(
        self,
        *,
        conversation_id: str | None,
        user_message: str,
        asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        conversation = self.create_conversation() if not conversation_id else self.get_conversation(conversation_id)
        if asset_ids:
            self._apply_context_updates(conversation, {"asset_ids": asset_ids})
        self.append_message(conversation, role="user", content=user_message)

        for step in range(1, self.MAX_LOOP_STEPS + 1):
            self.event_callback("thinking", {"step": step, "status": "agent_loop"})
            llm_payload = self._build_llm_messages(conversation)
            llm_message = self._call_llm_with_retry(llm_payload, self.tools.get_openai_tools())
            tool_calls = self._extract_tool_calls(llm_message)

            if tool_calls:
                for call in tool_calls:
                    tool_name = call["name"]
                    tool_call_id = call["id"]
                    arguments = call["arguments"]
                    parse_error = call.get("parse_error")
                    if parse_error:
                        parse_error_payload = {
                            "ok": False,
                            "error": f"tool_argument_parse_error: {parse_error}",
                            "raw_arguments": call.get("raw_arguments", ""),
                        }
                        self.append_message(
                            conversation,
                            role="tool",
                            content=ConversationToolRegistry.stringify_tool_result(parse_error_payload),
                            tool_name=tool_name,
                            tool_call_id=tool_call_id,
                        )
                        self.event_callback(
                            "tool_result",
                            {
                                "tool_name": tool_name,
                                "tool_call_id": tool_call_id,
                                "result": parse_error_payload,
                            },
                        )
                        continue

                    self.event_callback(
                        "tool_call",
                        {"tool_name": tool_name, "arguments": arguments, "tool_call_id": tool_call_id},
                    )
                    self.tools.context_state = self._load_agent_state(conversation)
                    tool_result = self._execute_tool(tool_name, arguments)
                    self.append_message(
                        conversation,
                        role="tool",
                        content=ConversationToolRegistry.stringify_tool_result(tool_result),
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                    )
                    self._apply_context_updates(conversation, tool_result.get("context_updates") or {})
                    self.event_callback(
                        "tool_result",
                        {"tool_name": tool_name, "tool_call_id": tool_call_id, "result": tool_result},
                    )
                continue

            final_text = str(llm_message.get("content") or "").strip()
            if not final_text:
                final_text = "我已经完成处理，但当前没有可展示的文本结果。"
            self.append_message(conversation, role="assistant", content=final_text)

            event_type = "ask_user" if self._looks_like_follow_up(final_text) else "message"
            self.event_callback(event_type, {"content": final_text})
            self.event_callback("complete", {"conversation_id": conversation.id})
            return {
                "conversation_id": conversation.id,
                "reply": final_text,
                "event_type": event_type,
            }

        limit_message = "本轮处理已达到 10 步上限，请补充更具体信息后继续。"
        self.append_message(conversation, role="assistant", content=limit_message)
        self.event_callback("message", {"content": limit_message})
        self.event_callback("complete", {"conversation_id": conversation.id})
        return {"conversation_id": conversation.id, "reply": limit_message, "event_type": "message"}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------
    def _build_llm_messages(self, conversation: Conversation) -> list[dict[str, Any]]:
        state = self._load_agent_state(conversation)
        provider_id = self.config.get_default_provider()
        use_native_tool_role = self._supports_native_tool_role(provider_id)
        system_prompt = (
            "你是 XeEdio 的视频制作助手。你可以使用工具来创建混剪任务、查询任务状态、"
            "触发素材分析和搜索素材。若关键信息不足，请先追问用户。\n\n"
            f"当前上下文记忆:\n"
            f"- 已关联素材: {state.get('asset_ids') or []}\n"
            f"- 已创建任务: {state.get('task_ids') or []}\n"
            f"- 最近一次混剪参数: {state.get('last_mix_params') or {}}\n"
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for msg in self.get_messages(conversation):
            if msg.role == "tool":
                if use_native_tool_role:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": msg.tool_call_id or "",
                            "name": msg.tool_name or "",
                            "content": msg.content or "",
                        }
                    )
                else:
                    # Compatibility mode for providers that reject native tool-role
                    # continuation in /chat/completions.
                    tool_name = msg.tool_name or "tool"
                    tool_call_id = msg.tool_call_id or "-"
                    tool_content = msg.content or "{}"
                    messages.append(
                        {
                            "role": "assistant",
                            "content": (
                                f"[TOOL_RESULT]\n"
                                f"tool_name={tool_name}\n"
                                f"tool_call_id={tool_call_id}\n"
                                f"result={tool_content}"
                            ),
                        }
                    )
            else:
                messages.append({"role": msg.role, "content": msg.content})
        return messages

    @staticmethod
    def _supports_native_tool_role(provider_id: str) -> bool:
        # luxee.ai gpt-* on /chat/completions currently rejects OpenAI-native
        # function_call_output continuation with role=tool.
        return provider_id not in {"gpt-4o-mini"}

    def _execute_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.tools.execute(tool_name, arguments)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _call_llm_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        provider_id = self.config.get_default_provider()
        provider = self.config.get_llm_provider(provider_id) or {}
        api_url = provider.get("api_url", "")
        api_key = provider.get("api_key", "")
        model = provider.get("model", "")
        debug_meta = {
            "provider_id": provider_id,
            "api_url": api_url,
            "model": model,
            "message_count": len(messages),
            "tool_count": len(tools),
        }
        logger.info("conversation llm config: %s", debug_meta)
        if not api_url or not api_key:
            raise RuntimeError("LLM provider is not configured")

        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.2,
            "max_tokens": 1500,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        attempts = 1 + len(self.RETRY_BACKOFF_SECONDS)
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(api_url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                message = (data.get("choices") or [{}])[0].get("message") or {}
                return message
            except Exception as exc:  # noqa: BLE001
                status_code = None
                response_excerpt = ""
                if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                    status_code = exc.response.status_code
                    response_excerpt = (exc.response.text or "")[:300]
                logger.warning(
                    "conversation llm call failed: provider=%s model=%s status=%s attempt=%s/%s error=%s response=%s",
                    provider_id,
                    model,
                    status_code,
                    attempt,
                    attempts,
                    str(exc),
                    response_excerpt,
                )
                if attempt >= attempts:
                    raise RuntimeError(f"LLM 调用失败: {exc}") from exc
                self.event_callback(
                    "thinking",
                    {"status": "llm_retry", "attempt": attempt + 1, "reason": str(exc)},
                )
                time.sleep(self.RETRY_BACKOFF_SECONDS[attempt - 1])
        raise RuntimeError("LLM 调用失败")

    def _extract_tool_calls(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        tool_calls = []
        raw_calls = message.get("tool_calls") or []
        for call in raw_calls:
            function = call.get("function") or {}
            name = function.get("name")
            call_id = call.get("id") or generate_uuid()
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                if not isinstance(arguments, dict):
                    arguments = {}
                    parse_error = "tool arguments are not a JSON object"
                else:
                    parse_error = None
            except Exception:  # noqa: BLE001
                arguments = {"_raw": raw_args}
                parse_error = "invalid JSON arguments"
            if name:
                tool_calls.append(
                    {
                        "id": call_id,
                        "name": name,
                        "arguments": arguments,
                        "parse_error": parse_error,
                        "raw_arguments": raw_args,
                    }
                )

        legacy_call = message.get("function_call")
        if legacy_call and not tool_calls:
            name = legacy_call.get("name")
            raw_args = legacy_call.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                parse_error = None
            except Exception:  # noqa: BLE001
                arguments = {"_raw": raw_args}
                parse_error = "invalid JSON arguments"
            if name:
                tool_calls.append(
                    {
                        "id": generate_uuid(),
                        "name": name,
                        "arguments": arguments,
                        "parse_error": parse_error,
                        "raw_arguments": raw_args,
                    }
                )
        return tool_calls

    def _apply_context_updates(self, conversation: Conversation, updates: dict[str, Any]) -> None:
        state = self._load_agent_state(conversation)
        if "asset_ids" in updates:
            current = state.get("asset_ids") or []
            state["asset_ids"] = self._dedupe(current + list(updates.get("asset_ids") or []))
        if "task_ids" in updates:
            current = state.get("task_ids") or []
            state["task_ids"] = self._dedupe(current + list(updates.get("task_ids") or []))
        if "last_mix_params" in updates and isinstance(updates.get("last_mix_params"), dict):
            state["last_mix_params"] = updates.get("last_mix_params")
        if "last_task_status" in updates and isinstance(updates.get("last_task_status"), dict):
            state["last_task_status"] = updates.get("last_task_status")
        if "custom_title" in updates:
            state["custom_title"] = str(updates.get("custom_title") or "").strip()

        conversation.asset_ids = json.dumps(state.get("asset_ids") or [], ensure_ascii=False)
        conversation.agent_state = json.dumps(state, ensure_ascii=False)
        conversation.updated_at = utcnow()
        self.db.add(conversation)
        self.db.commit()

    @staticmethod
    def _looks_like_follow_up(text: str) -> bool:
        return ("请提供" in text) or ("还需要" in text) or text.endswith("?") or text.endswith("？")

    @staticmethod
    def _dedupe(items: list[Any]) -> list[Any]:
        out = []
        seen = set()
        for item in items:
            key = str(item)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    @staticmethod
    def _default_agent_state() -> dict[str, Any]:
        return {
            "asset_ids": [],
            "task_ids": [],
            "last_mix_params": {},
            "last_task_status": {},
            "custom_title": "",
        }

    def _load_agent_state(self, conversation: Conversation) -> dict[str, Any]:
        raw = conversation.agent_state or ""
        if not raw:
            return self._default_agent_state()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {
                    "asset_ids": list(parsed.get("asset_ids") or []),
                    "task_ids": list(parsed.get("task_ids") or []),
                    "last_mix_params": dict(parsed.get("last_mix_params") or {}),
                    "last_task_status": dict(parsed.get("last_task_status") or {}),
                    "custom_title": str(parsed.get("custom_title") or "").strip(),
                }
        except Exception:  # noqa: BLE001
            pass
        return self._default_agent_state()
