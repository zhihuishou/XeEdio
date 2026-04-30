"""Agent Router — LLM-powered pipeline routing based on user intent + asset characteristics.

Replaces the hardcoded if/else routing in ``AIDirectorService.run_auto_pipeline()``
with an intelligent routing step that considers the user's ``director_prompt``,
asset analysis summaries, and a declarative pipeline registry.

On any failure the router returns ``None``, triggering the existing fallback
routing logic — zero-risk deployment.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Optional

import httpx

from app.services.external_config import ExternalConfig

logger = logging.getLogger("app.agent_router")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PipelineInfo:
    """Describes a single pipeline in the registry."""

    identifier: str          # e.g. "text_driven"
    name_zh: str             # e.g. "口播裁剪"
    description_zh: str      # Chinese description for LLM prompt
    expected_roles: list[str]   # e.g. ["presenter"] or ["montage_clip"]
    trigger_keywords: list[str] # e.g. ["裁剪口播", "trim presenter"]


@dataclass
class AssetSummary:
    """Condensed asset info for the LLM prompt."""

    asset_id: str
    original_filename: str
    role: str                   # From analysis, or "unknown"
    has_speech: bool | None     # None if analysis unavailable
    description: str            # Truncated to 100 chars
    duration: float             # Seconds


@dataclass
class RoutingDecision:
    """Structured output from the Agent Router."""

    pipeline: str               # Pipeline identifier
    asset_roles: dict[str, str] # {asset_id: role_string}
    parameters: dict            # Pipeline-specific parameters
    raw_response: str           # Original LLM response for logging


# ---------------------------------------------------------------------------
# Pipeline Registry
# ---------------------------------------------------------------------------

PIPELINE_REGISTRY: list[PipelineInfo] = [
    PipelineInfo(
        identifier="text_driven",
        name_zh="口播裁剪",
        description_zh=(
            "适用于口播/演讲类素材的智能裁剪。基于ASR转录文本和词级时间戳，"
            "由LLM选择精华片段。需要至少一个有语音的主播素材。"
        ),
        expected_roles=["presenter"],
        trigger_keywords=["裁剪口播", "剪口播", "trim presenter", "cut speech", "精华片段", "口播"],
    ),
    PipelineInfo(
        identifier="vision_montage",
        name_zh="视觉混剪",
        description_zh=(
            "适用于产品展示、风景、生活方式等视觉素材的混剪。基于VLM视觉分析"
            "生成蒙太奇时间线。不依赖语音内容。"
        ),
        expected_roles=["montage_clip"],
        trigger_keywords=["混剪", "剪辑", "拼接", "montage", "产品混剪", "视觉"],
    ),
    PipelineInfo(
        identifier="hybrid",
        name_zh="混合剪辑",
        description_zh=(
            "结合口播主轴和B-roll插入。以主播语音为叙事主线，在自然停顿处插入"
            "产品/场景画面。需要一个主播素材和至少一个非主播素材。"
        ),
        expected_roles=["presenter", "broll"],
        trigger_keywords=["口播配画面", "穿插", "B-roll", "混合", "用口播的音频配上"],
    ),
    PipelineInfo(
        identifier="multi_asset_montage",
        name_zh="多素材混剪",
        description_zh=(
            "多个素材等权重混剪，适用于素材量较多的场景。所有素材平等参与，"
            "由VLM统一编排时间线。"
        ),
        expected_roles=["montage_clip"],
        trigger_keywords=["多素材", "全部混在一起", "所有素材"],
    ),
]

# Quick lookup set for validation
_VALID_PIPELINE_IDS: set[str] = {p.identifier for p in PIPELINE_REGISTRY}


# ---------------------------------------------------------------------------
# AgentRouter
# ---------------------------------------------------------------------------

class AgentRouter:
    """LLM-powered pipeline routing based on user intent + asset characteristics."""

    def __init__(self) -> None:
        self.config = ExternalConfig.get_instance()

    # ------------------------------------------------------------------
    # Public: build asset summaries
    # ------------------------------------------------------------------

    def build_asset_summaries(
        self,
        asset_ids: list[str],
        clip_paths: list[str],
        clip_original_filenames: list[str] | None,
        analysis_map: dict[str, dict | None],
    ) -> list[AssetSummary]:
        """Construct ``AssetSummary`` list from analysis data and file metadata.

        Args:
            asset_ids: Ordered list of asset identifiers.
            clip_paths: Parallel list of clip file paths.
            clip_original_filenames: Optional parallel list of original filenames.
            analysis_map: Mapping from asset_id → analysis dict (or ``None``).

        Returns:
            List of ``AssetSummary`` objects, one per asset.
        """
        summaries: list[AssetSummary] = []

        for i, asset_id in enumerate(asset_ids):
            # Determine original filename
            if clip_original_filenames and i < len(clip_original_filenames):
                original_filename = clip_original_filenames[i]
            elif i < len(clip_paths):
                original_filename = os.path.basename(clip_paths[i])
            else:
                original_filename = f"clip_{i}"

            analysis = analysis_map.get(asset_id)

            if analysis and analysis.get("status") == "completed":
                role = analysis.get("role", "unknown") or "unknown"
                has_speech = analysis.get("has_speech", False)
                if not isinstance(has_speech, bool):
                    has_speech = bool(has_speech)
                description = str(analysis.get("description", "") or "")
                if len(description) > 100:
                    description = description[:100]
                duration = float(analysis.get("duration", 0) or 0)
            else:
                # Missing or incomplete analysis
                role = "unknown"
                has_speech = None
                description = ""
                duration = float(analysis.get("duration", 0) or 0) if analysis else 0.0

            summaries.append(AssetSummary(
                asset_id=asset_id,
                original_filename=original_filename,
                role=role,
                has_speech=has_speech,
                description=description,
                duration=duration,
            ))

        return summaries

    # ------------------------------------------------------------------
    # Public: route
    # ------------------------------------------------------------------

    def route(
        self,
        director_prompt: str,
        asset_summaries: list[AssetSummary],
        analysis_map: dict[str, dict | None],
    ) -> RoutingDecision | None:
        """Attempt LLM-based routing. Returns ``None`` on any failure.

        Args:
            director_prompt: User's natural language instruction.
            asset_summaries: Condensed asset info for the LLM prompt.
            analysis_map: Full analysis data for role inference fallback.

        Returns:
            ``RoutingDecision`` on success, ``None`` on any failure.
        """
        try:
            start_time = time.time()

            # Build prompts
            system_prompt = self._build_system_prompt()
            user_prompt = self._build_user_prompt(director_prompt, asset_summaries)

            # Get LLM config and call
            llm_config = self._get_llm_config()
            raw_response = self._call_llm(system_prompt, user_prompt, llm_config)

            latency_ms = (time.time() - start_time) * 1000
            logger.info("agent router LLM call completed in %.0fms", latency_ms)

            if raw_response is None:
                logger.warning("agent router LLM call returned None")
                return None

            # Parse response
            parsed = self._parse_response(raw_response)
            if parsed is None:
                logger.warning(
                    "agent router response parsing failed, raw: %s",
                    raw_response[:200],
                )
                return None

            # Validate and build decision
            asset_ids = [s.asset_id for s in asset_summaries]
            decision = self._validate_decision(
                parsed, asset_summaries, analysis_map=analysis_map, asset_ids=asset_ids,
            )
            if decision is None:
                logger.warning("agent router validation failed for parsed response")
                return None

            # Attach raw response
            decision.raw_response = raw_response

            logger.info(
                "agent router decision: pipeline=%s, asset_roles=%s, latency=%.0fms",
                decision.pipeline,
                decision.asset_roles,
                latency_ms,
            )
            return decision

        except Exception as e:
            logger.warning(
                "agent router unexpected error (%s: %s)",
                type(e).__name__,
                str(e)[:200],
            )
            return None

    # ------------------------------------------------------------------
    # Private: LLM configuration
    # ------------------------------------------------------------------

    def _get_llm_config(self) -> dict:
        """Get LLM configuration following the IntentParsingService pattern.

        Resolution order:
        1. ``text_llm`` config section (if available)
        2. Default LLM provider
        3. VLM config as last resort
        """
        # 1. Dedicated text_llm section
        text_llm_url = self.config.get("text_llm.api_url", "")
        text_llm_key = self.config.get("text_llm.api_key", "")
        text_llm_model = self.config.get("text_llm.model", "")

        if text_llm_url and text_llm_key:
            return {
                "api_url": text_llm_url,
                "api_key": text_llm_key,
                "model": text_llm_model or "qwen-plus",
            }

        # 2. Default LLM provider
        default_provider = self.config.get_default_provider()
        provider = self.config.get_llm_provider(default_provider)
        if provider and provider.get("api_url") and provider.get("api_key"):
            return {
                "api_url": provider["api_url"],
                "api_key": provider["api_key"],
                "model": provider.get("model", ""),
            }

        # 3. VLM config as last resort
        vlm_config = self.config.get_vlm_config()
        return {
            "api_url": vlm_config.get("api_url", ""),
            "api_key": vlm_config.get("api_key", ""),
            "model": vlm_config.get("model", ""),
        }

    # ------------------------------------------------------------------
    # Private: Prompt construction
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Build the system prompt with pipeline registry and routing rules."""
        pipeline_descriptions = []
        for p in PIPELINE_REGISTRY:
            pipeline_descriptions.append(
                f"- **{p.identifier}** ({p.name_zh}): {p.description_zh}\n"
                f"  期望角色: {', '.join(p.expected_roles)}\n"
                f"  触发关键词: {', '.join(p.trigger_keywords)}"
            )

        pipelines_text = "\n".join(pipeline_descriptions)

        return (
            "你是一个视频剪辑流水线路由器。根据用户的自然语言指令和素材分析结果，"
            "选择最合适的剪辑流水线，并为每个素材分配角色。\n\n"
            "## 可用流水线\n\n"
            f"{pipelines_text}\n\n"
            "## 输出格式\n\n"
            "请输出一个JSON对象，包含以下字段：\n"
            '- "pipeline": 流水线标识符（text_driven / vision_montage / hybrid / multi_asset_montage）\n'
            '- "asset_roles": 对象，将每个asset_id映射到角色字符串（presenter / broll / montage_clip）\n'
            '- "parameters": 对象，包含流水线特定参数（如排序提示、音频源等）\n\n'
            "示例输出：\n"
            "```json\n"
            '{\n'
            '  "pipeline": "vision_montage",\n'
            '  "asset_roles": {"asset-001": "montage_clip", "asset-002": "montage_clip"},\n'
            '  "parameters": {"ordering": ["asset-002", "asset-001"]}\n'
            '}\n'
            "```\n\n"
            "## 路由优先规则\n\n"
            "1. **用户意图优先于素材标签**：如果用户明确要求混剪（混剪/剪辑/拼接/montage），"
            "即使素材中有主播类素材，也应选择 vision_montage 或 multi_asset_montage。\n"
            "2. 如果用户提到裁剪口播/剪口播/精华片段等，选择 text_driven。\n"
            "3. 如果用户提到口播配画面/穿插/B-roll/用口播的音频配上，选择 hybrid。\n"
            "4. 如果用户提到多素材/全部混在一起/所有素材，选择 multi_asset_montage。\n"
            "5. 如果用户意图不明确，根据素材特征选择：有主播+非主播→hybrid，"
            "仅主播→text_driven，无主播→vision_montage。\n\n"
            "## 文件名映射\n\n"
            "用户可能通过文件名引用素材（如\"1.mov放开头\"）。请根据素材列表中的"
            "original_filename字段将文件名引用映射到对应的asset_id。\n\n"
            "只输出JSON，不要输出其他内容。"
        )

    def _build_user_prompt(
        self,
        director_prompt: str,
        summaries: list[AssetSummary],
    ) -> str:
        """Build the user prompt with director instructions and asset summaries."""
        asset_lines = []
        for s in summaries:
            speech_str = "未知" if s.has_speech is None else ("是" if s.has_speech else "否")
            asset_lines.append(
                f"- asset_id: {s.asset_id}, 文件名: {s.original_filename}, "
                f"角色: {s.role}, 有语音: {speech_str}, "
                f"描述: {s.description or '无'}, 时长: {s.duration:.1f}秒"
            )

        assets_text = "\n".join(asset_lines)

        return (
            f"## 用户指令\n\n{director_prompt}\n\n"
            f"## 素材列表（共{len(summaries)}个素材）\n\n{assets_text}"
        )

    # ------------------------------------------------------------------
    # Private: LLM API call
    # ------------------------------------------------------------------

    def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        llm_config: dict,
    ) -> str | None:
        """Call LLM API and return raw response content.

        Args:
            system_prompt: System prompt text.
            user_prompt: User prompt text.
            llm_config: Dict with ``api_url``, ``api_key``, ``model``.

        Returns:
            Raw response content string, or ``None`` on failure.
        """
        api_url = llm_config.get("api_url", "")
        api_key = llm_config.get("api_key", "")
        model = llm_config.get("model", "")

        if not api_url or not api_key:
            logger.warning("agent router LLM config missing api_url or api_key")
            return None

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
            "stream": False,
        }

        logger.debug(
            "agent router LLM request: model=%s, prompt_length=%d",
            model,
            len(user_prompt),
        )

        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(api_url, json=payload, headers=headers)

            if response.status_code == 200:
                data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if content:
                    return content
                logger.warning("agent router LLM returned empty content")
                return None

            logger.warning(
                "agent router LLM call failed (HTTP %d: %s)",
                response.status_code,
                response.text[:200],
            )
            return None

        except httpx.TimeoutException:
            logger.warning("agent router LLM call timed out (>15s)")
            return None
        except Exception as e:
            logger.warning(
                "agent router LLM call failed (%s: %s)",
                type(e).__name__,
                str(e)[:200],
            )
            return None

    # ------------------------------------------------------------------
    # Private: Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw_text: str) -> dict | None:
        """Extract JSON object from LLM response text.

        Strips qwen3 thinking tags (``<think>...</think>``), markdown code
        fences, then tries direct ``json.loads`` first, then searches for
        the first ``{...}`` block.

        Args:
            raw_text: Raw LLM response content.

        Returns:
            Parsed dict, or ``None`` if extraction fails.
        """
        if not raw_text or not raw_text.strip():
            return None

        text = raw_text.strip()

        # Strip qwen3 thinking tags
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()

        # Try direct parse
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        # Fall back: extract first { ... } block using brace matching
        start = text.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(text[start:i + 1])
                            if isinstance(parsed, dict):
                                return parsed
                        except (json.JSONDecodeError, ValueError):
                            pass
                        break

        return None

    # ------------------------------------------------------------------
    # Private: Validation
    # ------------------------------------------------------------------

    def _validate_decision(
        self,
        data: dict,
        asset_summaries: list[AssetSummary],
        *,
        analysis_map: dict[str, dict | None] | None = None,
        asset_ids: list[str] | None = None,
    ) -> RoutingDecision | None:
        """Validate parsed LLM response and build a ``RoutingDecision``.

        Args:
            data: Parsed JSON dict from the LLM.
            asset_summaries: Asset summaries for cross-referencing.
            analysis_map: Full analysis data for role inference fallback.
            asset_ids: List of valid asset IDs.

        Returns:
            Validated ``RoutingDecision``, or ``None`` on any validation error.
        """
        # Validate pipeline field
        pipeline = data.get("pipeline")
        if not pipeline or pipeline not in _VALID_PIPELINE_IDS:
            logger.warning("agent router validation: invalid pipeline '%s'", pipeline)
            return None

        valid_asset_ids = {s.asset_id for s in asset_summaries}

        # Handle asset_roles — may be missing
        asset_roles = data.get("asset_roles")
        if asset_roles is None or not isinstance(asset_roles, dict) or len(asset_roles) == 0:
            # Infer roles from analysis data
            if analysis_map and asset_ids:
                asset_roles = self._infer_asset_roles(pipeline, analysis_map, asset_ids)
                logger.info("agent router: inferred asset_roles from analysis data")
            else:
                logger.warning("agent router validation: missing asset_roles and cannot infer")
                return None

        # Validate all asset_ids in asset_roles correspond to provided assets
        for aid in asset_roles:
            if aid not in valid_asset_ids:
                logger.warning(
                    "agent router validation: unknown asset_id '%s' in asset_roles", aid
                )
                return None

        # Get expected roles for the selected pipeline
        pipeline_info = next(
            (p for p in PIPELINE_REGISTRY if p.identifier == pipeline), None
        )
        if pipeline_info is None:
            return None

        # Validate assigned roles are compatible with pipeline's expected roles
        expected = set(pipeline_info.expected_roles)
        for aid, role in asset_roles.items():
            if role not in expected:
                logger.warning(
                    "agent router validation: role '%s' for asset '%s' not in expected roles %s for pipeline '%s'",
                    role, aid, expected, pipeline,
                )
                return None

        # For text_driven or hybrid, validate at least one presenter
        if pipeline in ("text_driven", "hybrid"):
            has_presenter = any(r == "presenter" for r in asset_roles.values())
            if not has_presenter:
                logger.warning(
                    "agent router validation: pipeline '%s' requires at least one presenter role",
                    pipeline,
                )
                return None

        # Handle parameters field — may be string or dict
        parameters = data.get("parameters", {})
        if isinstance(parameters, str):
            try:
                parameters = json.loads(parameters)
                if not isinstance(parameters, dict):
                    parameters = {}
            except (json.JSONDecodeError, ValueError):
                parameters = {}
        elif not isinstance(parameters, dict):
            parameters = {}

        return RoutingDecision(
            pipeline=pipeline,
            asset_roles=asset_roles,
            parameters=parameters,
            raw_response="",
        )

    # ------------------------------------------------------------------
    # Private: Role inference
    # ------------------------------------------------------------------

    def _infer_asset_roles(
        self,
        pipeline: str,
        analysis_map: dict[str, dict | None],
        asset_ids: list[str],
    ) -> dict[str, str]:
        """Infer asset roles from analysis data based on pipeline type.

        For text_driven/hybrid: assign ``presenter`` to speech-containing
        assets, ``broll`` to others.
        For vision_montage/multi_asset_montage: assign all as ``montage_clip``.

        Args:
            pipeline: Selected pipeline identifier.
            analysis_map: Mapping from asset_id → analysis dict.
            asset_ids: List of asset IDs to assign roles to.

        Returns:
            Dict mapping asset_id → role string.
        """
        roles: dict[str, str] = {}

        if pipeline in ("text_driven", "hybrid"):
            for aid in asset_ids:
                analysis = analysis_map.get(aid)
                if (
                    analysis
                    and analysis.get("status") == "completed"
                    and analysis.get("has_speech")
                    and analysis.get("role") == "presenter"
                ):
                    roles[aid] = "presenter"
                else:
                    roles[aid] = "broll" if pipeline == "hybrid" else "presenter"
        else:
            # vision_montage or multi_asset_montage
            for aid in asset_ids:
                roles[aid] = "montage_clip"

        return roles
