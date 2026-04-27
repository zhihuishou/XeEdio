"""Intent parsing service.

Uses an LLM to extract structured mixing parameters from natural language
user instructions (the *director_prompt*).  The service never raises —
on any failure it returns ``ParsedIntent.defaults()`` so that the mixing
pipeline can always proceed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Optional

import httpx

from app.services.external_config import ExternalConfig

logger = logging.getLogger("app.intent_parsing_service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTENT_DEFAULTS: dict = {
    "strip_audio": False,
    "video_count": 1,
    "max_output_duration": 60,
    "aspect_ratio": "9:16",
    "bgm_enabled": False,
    "subtitle_font": None,
    "tts_text": None,
    "editing_style": None,
    "fade_out": True,
    "fade_out_duration": 0.3,
}

PARAM_CONSTRAINTS: dict = {
    "video_count": {"min": 1, "max": 10},
    "max_output_duration": {"min": 15, "max": 300},
    "fade_out_duration": {"min": 0.1, "max": 3.0},
}

VALID_ASPECT_RATIOS: set[str] = {"16:9", "9:16", "1:1"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ParsedIntent:
    """Structured result of intent parsing."""

    strip_audio: bool = False
    video_count: int = 1
    max_output_duration: int = 60
    aspect_ratio: str = "9:16"
    bgm_enabled: bool = False
    subtitle_font: Optional[str] = None
    tts_text: Optional[str] = None
    editing_style: Optional[str] = None
    fade_out: bool = True
    fade_out_duration: float = 0.3

    def to_dict(self) -> dict:
        """Serialize to dict (includes ``None`` values)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ParsedIntent":
        """Deserialize from *data* with type coercion and validation.

        Unknown keys are silently ignored.  Missing keys fall back to the
        dataclass defaults.
        """
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    @classmethod
    def defaults(cls) -> "ParsedIntent":
        """Return a ``ParsedIntent`` with all default values."""
        return cls()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class IntentParsingService:
    """LLM-based natural language intent parsing for mixing parameters."""

    SYSTEM_PROMPT = (
        "You are a video editing parameter extractor. "
        "Given a user's natural language instruction in Chinese or English, "
        "extract structured mixing parameters as a JSON object.\n\n"
        "Output ONLY a valid JSON object with these fields (omit fields not mentioned):\n"
        "- strip_audio (boolean): whether to remove original audio\n"
        "- subtitle_font (string): font name for subtitles\n"
        "- video_count (integer): number of output videos\n"
        "- max_output_duration (integer): max duration per video in seconds\n"
        "- aspect_ratio (string): one of '16:9', '9:16', '1:1'\n"
        "- tts_text (string): text for TTS voiceover\n"
        "- editing_style (string): editing style description\n"
        "- bgm_enabled (boolean): whether to enable background music\n"
        "- fade_out (boolean): whether to add fade-out at the end of each video. '不要淡出'/'不加淡出' → false\n"
        "- fade_out_duration (number): fade-out duration in seconds, e.g. '淡出1秒' → 1.0\n\n"
        "Rules:\n"
        "- Convert Chinese duration expressions to seconds: "
        "'3分钟'→180, '40秒'→40, '1分30秒'→90, '3-5分钟'→300 (use upper bound)\n"
        "- Convert Chinese count expressions: '3条'→3, '5个'→5\n"
        "- '竖版'/'竖屏' → '9:16', '横版'/'横屏' → '16:9', '方形' → '1:1'\n"
        "- '去除原声'/'去掉背景声'/'静音' → strip_audio: true\n"
        "- '保留原声' → strip_audio: false\n"
        "- Output ONLY valid JSON, no explanation or markdown."
    )

    def __init__(self) -> None:
        self.config = ExternalConfig.get_instance()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_intent(self, director_prompt: str) -> ParsedIntent:
        """Parse natural language prompt into structured parameters.

        Args:
            director_prompt: User's free-form instruction text.

        Returns:
            ``ParsedIntent`` with extracted and default-filled parameters.
            On any failure, returns ``ParsedIntent.defaults()``.
        """
        parsed, _meta = self.parse_intent_with_meta(director_prompt)
        return parsed

    def parse_intent_with_meta(self, director_prompt: str) -> tuple[ParsedIntent, bool]:
        """Parse NL intent and report whether the LLM produced usable JSON.

        Returns:
            A pair ``(parsed, llm_ok)``.  ``llm_ok`` is ``True`` only when the
            LLM returned valid JSON that passed validation.  On failure or empty
            prompt, ``llm_ok`` is ``False`` — callers should prefer explicit UI /
            request fields over ``parsed`` for structural knobs (see
            :meth:`merge_with_ui_defaults`).
        """
        try:
            if not director_prompt or not director_prompt.strip():
                logger.debug("empty director_prompt, returning defaults (llm_ok=False)")
                return ParsedIntent.defaults(), False

            llm_config = self._get_llm_config()
            raw_response = self._call_llm(director_prompt, llm_config)
            if raw_response is None:
                logger.warning("intent parsing LLM call returned None, using defaults (llm_ok=False)")
                return ParsedIntent.defaults(), False

            extracted = self._extract_json(raw_response)
            if extracted is None:
                logger.warning(
                    "intent parsing JSON extraction failed, raw response: %s",
                    raw_response[:200],
                )
                return ParsedIntent.defaults(), False

            result = self._validate_and_clamp(extracted)
            logger.info(
                "intent parsed successfully: video_count=%d, duration=%ds, strip_audio=%s",
                result.video_count,
                result.max_output_duration,
                result.strip_audio,
            )
            return result, True

        except Exception as e:
            logger.warning(
                "intent parsing failed (%s: %s), using defaults (llm_ok=False)",
                type(e).__name__,
                str(e)[:200],
            )
            return ParsedIntent.defaults(), False

    # ------------------------------------------------------------------
    # Private: LLM configuration
    # ------------------------------------------------------------------

    def _get_llm_config(self) -> dict:
        """Get LLM configuration following the existing pattern.

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
    # Private: LLM API call
    # ------------------------------------------------------------------

    def _call_llm(self, director_prompt: str, llm_config: dict) -> str | None:
        """Call LLM API and return raw response content.

        Args:
            director_prompt: User prompt text.
            llm_config: Dict with ``api_url``, ``api_key``, ``model``.

        Returns:
            Raw response content string, or ``None`` on failure.
        """
        api_url = llm_config.get("api_url", "")
        api_key = llm_config.get("api_key", "")
        model = llm_config.get("model", "")

        if not api_url or not api_key:
            logger.warning("intent parsing LLM config missing api_url or api_key")
            return None

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": director_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
            "stream": False,
        }

        logger.debug(
            "intent parsing LLM request: model=%s, prompt_length=%d",
            model,
            len(director_prompt),
        )

        try:
            with httpx.Client(timeout=10.0) as client:
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
                logger.warning("intent parsing LLM returned empty content")
                return None

            logger.warning(
                "intent parsing LLM call failed (HTTP %d: %s), using defaults",
                response.status_code,
                response.text[:200],
            )
            return None

        except httpx.TimeoutException:
            logger.warning("intent parsing LLM call failed (TimeoutException), using defaults")
            return None
        except Exception as e:
            logger.warning(
                "intent parsing LLM call failed (%s: %s), using defaults",
                type(e).__name__,
                str(e)[:200],
            )
            return None

    # ------------------------------------------------------------------
    # Private: JSON extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_json(raw_text: str) -> dict | None:
        """Extract JSON object from LLM response text.

        Strips qwen3 thinking tags (``<think>...</think>``), then tries
        direct ``json.loads`` first, then searches for a ``{...}`` pattern.

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

        # Fall back: search for outermost { ... }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    # ------------------------------------------------------------------
    # Private: Validation & clamping
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_and_clamp(data: dict) -> ParsedIntent:
        """Validate extracted values and clamp to valid ranges.

        - Clamps ``video_count`` to [1, 10]
        - Clamps ``max_output_duration`` to [15, 300]
        - Validates ``aspect_ratio`` against allowed values
        - Coerces types (str → int, str → bool)
        - Fills missing fields with defaults from ``INTENT_DEFAULTS``

        Args:
            data: Raw extracted dict from LLM.

        Returns:
            Validated ``ParsedIntent`` instance.
        """
        result: dict = dict(INTENT_DEFAULTS)  # start from defaults

        # --- boolean fields ---
        for bool_field in ("strip_audio", "bgm_enabled", "fade_out"):
            if bool_field in data:
                val = data[bool_field]
                if isinstance(val, bool):
                    result[bool_field] = val
                elif isinstance(val, str):
                    result[bool_field] = val.lower() in ("true", "1", "yes")
                elif isinstance(val, (int, float)):
                    result[bool_field] = bool(val)

        # --- integer/float fields with clamping ---
        for field_name, constraints in PARAM_CONSTRAINTS.items():
            if field_name in data:
                val = data[field_name]
                try:
                    # Use float for fade_out_duration, int for others
                    if isinstance(constraints["min"], float):
                        num_val = float(val)
                    else:
                        num_val = int(val)
                except (ValueError, TypeError):
                    continue
                num_val = max(constraints["min"], min(constraints["max"], num_val))
                result[field_name] = num_val

        # --- aspect_ratio ---
        if "aspect_ratio" in data:
            ar = str(data["aspect_ratio"]).strip()
            if ar in VALID_ASPECT_RATIOS:
                result["aspect_ratio"] = ar
            # else: keep default "9:16"

        # --- string fields ---
        for str_field in ("subtitle_font", "tts_text", "editing_style"):
            if str_field in data and data[str_field] is not None:
                val = str(data[str_field]).strip()
                if val:
                    result[str_field] = val

        return ParsedIntent(**result)

    # ------------------------------------------------------------------
    # Public: Merge with UI defaults
    # ------------------------------------------------------------------

    @staticmethod
    def merge_with_ui_defaults(
        parsed: ParsedIntent,
        ui_defaults: dict,
        *,
        llm_parse_succeeded: bool = True,
    ) -> dict:
        """Merge parsed intent with UI panel defaults.

        When ``llm_parse_succeeded`` is ``True``: priority is
        ``ParsedIntent`` (non-None) > UI defaults > system defaults.

        When ``llm_parse_succeeded`` is ``False`` (LLM timeout / invalid JSON /
        empty prompt): **only** UI defaults and system defaults apply — the
        ``parsed`` object is ignored so that e.g. ``video_count`` from the
        request is not overwritten by :meth:`ParsedIntent.defaults`.

        Args:
            parsed: LLM-extracted parameters (may be defaults on failure).
            ui_defaults: Frontend / request panel settings.
            llm_parse_succeeded: Whether structured fields from ``parsed`` should win.

        Returns:
            Merged parameter dict ready for ``mix_params`` storage.
        """
        # Start from system defaults
        merged: dict = dict(INTENT_DEFAULTS)

        # Layer UI defaults on top (only non-None values)
        for key in merged:
            if key in ui_defaults and ui_defaults[key] is not None:
                merged[key] = ui_defaults[key]

        # Layer parsed intent only when the LLM path actually succeeded
        if llm_parse_succeeded:
            parsed_dict = parsed.to_dict()
            for key in merged:
                if key in parsed_dict and parsed_dict[key] is not None:
                    merged[key] = parsed_dict[key]

        return merged
