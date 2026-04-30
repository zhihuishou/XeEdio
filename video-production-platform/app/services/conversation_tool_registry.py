"""Tool registry for the conversational agent."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.database import Asset, User
from app.schemas.mix import MixCreateRequest
from app.services.asset_analysis_service import AssetAnalysisService
from app.services.mixing_service import MixingService


class ConversationToolRegistry:
    """Registers and executes tools exposed to function-calling LLM."""

    def __init__(
        self,
        db: Session,
        current_user: User,
        *,
        context_state: dict[str, Any] | None = None,
    ):
        self.db = db
        self.current_user = current_user
        self._mixing = MixingService(db)
        self._analysis = AssetAnalysisService()
        self.context_state = context_state or {}

    def get_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_mix",
                    "description": "创建一个新的视频混剪任务",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string"},
                            "asset_ids": {"type": "array", "items": {"type": "string"}},
                            "director_prompt": {"type": "string"},
                            "max_output_duration": {"type": "integer"},
                            "video_count": {"type": "integer"},
                            "aspect_ratio": {"type": "string", "enum": ["16:9", "9:16", "1:1"]},
                        },
                        "required": ["topic", "asset_ids"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_task_status",
                    "description": "查询混剪任务状态和结果",
                    "parameters": {
                        "type": "object",
                        "properties": {"task_id": {"type": "string"}},
                        "required": ["task_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "analyze_assets",
                    "description": "触发素材分析流程",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "asset_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["asset_ids"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_assets",
                    "description": "按描述语义搜索当前用户素材",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["query"],
                    },
                },
            },
        ]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name == "create_mix":
            return self._create_mix(arguments)
        if tool_name == "get_task_status":
            return self._get_task_status(arguments)
        if tool_name == "analyze_assets":
            return self._analyze_assets(arguments)
        if tool_name == "search_assets":
            return self._search_assets(arguments)
        return {"ok": False, "error": f"tool_not_found: {tool_name}"}

    def _create_mix(self, arguments: dict[str, Any]) -> dict[str, Any]:
        context_assets = list(self.context_state.get("asset_ids") or [])
        resolved_asset_ids = list(arguments.get("asset_ids") or context_assets)
        topic = str(arguments.get("topic", "")).strip() or "智能混剪任务"
        director_prompt = str(arguments.get("director_prompt", "")).strip() or None
        payload = MixCreateRequest(
            topic=topic,
            asset_ids=resolved_asset_ids,
            director_prompt=director_prompt,
            max_output_duration=int(arguments.get("max_output_duration") or 60),
            video_count=int(arguments.get("video_count") or 1),
            aspect_ratio=str(arguments.get("aspect_ratio") or "9:16"),
        )
        task = self._mixing.create_mix_task(payload, self.current_user.id)
        return {
            "ok": True,
            "result": {"task_id": task.id, "status": task.status},
            "context_updates": {
                "task_ids": [task.id],
                "asset_ids": payload.asset_ids,
                "last_mix_params": {
                    "topic": topic,
                    "director_prompt": director_prompt,
                    "max_output_duration": payload.max_output_duration,
                    "video_count": payload.video_count,
                    "aspect_ratio": payload.aspect_ratio,
                },
            },
        }

    def _get_task_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        context_task_ids = list(self.context_state.get("task_ids") or [])
        fallback_task_id = context_task_ids[-1] if context_task_ids else ""
        task_id = str(arguments.get("task_id", "")).strip() or fallback_task_id
        status = self._mixing.get_status(task_id)
        return {
            "ok": True,
            "result": status,
            "context_updates": {"last_task_status": status},
        }

    def _analyze_assets(self, arguments: dict[str, Any]) -> dict[str, Any]:
        asset_ids = [str(x) for x in (arguments.get("asset_ids") or []) if str(x).strip()]
        for asset_id in asset_ids:
            self._analysis.analyze_asset(asset_id)
        return {
            "ok": True,
            "result": {"triggered": len(asset_ids), "asset_ids": asset_ids},
            "context_updates": {"asset_ids": asset_ids},
        }

    def _search_assets(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        limit = int(arguments.get("limit") or 10)
        results = self._analysis.search_by_text(query, limit=limit, db=self.db)
        user_asset_ids = {
            row.id
            for row in self.db.query(Asset.id).filter(Asset.uploaded_by == self.current_user.id).all()
        }
        filtered = [r for r in results if r.get("id") in user_asset_ids]
        return {"ok": True, "result": {"items": filtered, "query": query}}

    @staticmethod
    def stringify_tool_result(result: dict[str, Any]) -> str:
        return json.dumps(result, ensure_ascii=False)
