"""Text-driven editing service — ASR transcript + LLM segment selection.

For speech-heavy video clips (口播/直播类素材), this service:
1. Sends the full ASR transcript to an LLM (qwen3-vl-plus) to select
   high-value segments based on user instructions and target duration.
2. Maps the LLM-selected text segments back to precise word-level
   timestamps using fuzzy matching.
3. Applies filler-word filtering and breath-gap snap for clean cut points.
4. Outputs a unified timeline format identical to the vision-driven pipeline.
"""
from __future__ import annotations


import json
import logging
import time
from typing import Optional

import httpx

from app.services.external_config import ExternalConfig

logger = logging.getLogger("app.text_driven_editing")

# ---------------------------------------------------------------------------
# Filler words to filter from ASR output before cut-point calculation
# ---------------------------------------------------------------------------
FILLER_WORDS: set[str] = {
    # Basic fillers (基本口头禅)
    "嗯", "啊", "那个", "就是说", "然后", "对吧", "你知道吗", "怎么说呢",
    "呃", "额", "哎", "这个", "所以说", "反正",
    # Repeated fillers (重复型口头禅)
    "对对对", "是是是", "好的好的", "嗯嗯", "啊啊",
    # English fillers common in Chinese speech
    "OK", "ok",
    # Discourse markers often used as fillers (语气词/话语标记)
    "其实", "基本上", "总之",
}

# Multi-word filler patterns: sequences of words that together form a filler.
# Each pattern is a tuple of consecutive words to match.
FILLER_PATTERNS: list[tuple[str, ...]] = [
    ("就是", "那个"),
    ("怎么", "说", "呢"),
    ("就是", "说"),
]

# ---------------------------------------------------------------------------
# Constants for time estimation
# ---------------------------------------------------------------------------
# Average speech rate: ~4 Chinese characters per second at normal pace.
# Each character ≈ 0.25s. Used for estimating segment duration from text.
CHARS_PER_SECOND: float = 4.0
SECONDS_PER_CHAR: float = 0.25

# Pause threshold for grouping words into sentences (seconds).
# Gaps longer than this between consecutive words indicate a natural break.
SENTENCE_PAUSE_THRESHOLD: float = 0.5

# Chinese punctuation marks that indicate sentence boundaries
SENTENCE_PUNCTUATION: set[str] = {
    "。", "！", "？", "，", "；", "、", "…", "——",
    ".", "!", "?", ",", ";",
}

# ---------------------------------------------------------------------------
# LLM prompts for segment selection
# ---------------------------------------------------------------------------

# System message — sets the LLM's role and expertise context
LLM_SEGMENT_SELECTION_SYSTEM = (
    "你是一位资深视频剪辑师，擅长从长视频口播/直播转录文本中提取高价值片段，"
    "组成节奏紧凑、叙事连贯的短视频。你精通中文语境下的话题边界识别、"
    "信息密度判断和语义完整性把控。你只输出 JSON，不输出任何解释文字。"
)

# User message template — the main prompt with transcript, instructions, rules
LLM_SEGMENT_SELECTION_PROMPT = """## 任务
从以下视频转录文本中，选出最有价值的片段，组成约 **{target_duration} 秒**的短视频内容。

## 输入

### 转录文本（带时间标记）
{transcript}

### 目标时长
{target_duration} 秒（允许 ±10% 误差，即 {duration_min}–{duration_max} 秒）

{user_instruction}

## 时长估算参考

中文语速约 **4 字/秒**（每个汉字 ≈ 0.25 秒）。请根据时间标记和文字量估算每个段落的时长，确保选中段落的总时长接近目标。

## 选段规则（按优先级排列）

1. **用户指令优先**：如果用户给出了具体指令，必须严格遵守，其他规则均为次要。
2. **信息密度优先**：选择观点明确、干货密集、有金句或高潮点的段落。
3. **去除低价值内容**：跳过口头禅（嗯、啊、那个、就是说）、重复表述、跑题闲聊、无意义寒暄。
4. **叙事连贯**：选中的段落必须按原始顺序排列，整体形成逻辑通顺的叙事线。
5. **语义完整**：每个段落必须是完整的语义单元——不能切在半句话中间，起止点应在自然的句子或话题边界。
6. **时长控制**：所有选中段落的总时长应在目标时长的 ±10% 范围内。宁可略短也不要为凑时长选低质量内容。
7. **段落粒度**：每个段落建议 5–30 秒。避免选过短（<3 秒）的碎片，也避免选过长（>60 秒）的未经裁剪段落。

## 段落长度分布建议

为了让短视频节奏感好，建议混合使用不同长度的段落：
- **短段落（5–10 秒）**：金句、结论、转折点——制造节奏感和记忆点
- **中段落（10–20 秒）**：核心论点阐述、产品卖点讲解——传递主要信息
- **长段落（20–30 秒）**：完整故事、深度分析——建立信任和说服力

理想的组合是 1-2 个中长段落搭配 1-2 个短段落，形成"信息-节奏-信息"的韵律。

## 不同内容类型的选段策略

根据转录文本的内容特征，灵活调整选段策略：

- **口播/种草类**：优先选产品核心卖点、使用体验、效果对比。开头要有吸引力（"hook"），结尾要有行动号召。
- **直播回放类**：跳过互动环节（"谢谢 XX 的关注"）、等待环节、重复讲解。聚焦产品演示和核心讲解。
- **访谈/对话类**：选择有深度的问答交锋、独到观点、金句。保持问答的完整性（不要只选答案不选问题）。
- **课程/教学类**：按知识点结构选段。保留关键概念的定义和解释，跳过举例中的冗余部分。

{duration_strategy}

## 边界情况处理

- **转录文本很短**（总时长 ≤ 目标时长）：如果全部内容质量尚可，可以选择全部内容作为一个段落。只在内容明显低质量时才裁剪。
- **转录文本很长**（总时长 >> 目标时长）：严格筛选，只保留最精华的部分。优先选择话题的核心论点和结论，跳过铺垫和过渡。
- **无明显高价值内容**：仍然选择相对最好的段落，确保输出不为空。至少返回 1 个段落。

## 输出格式

输出一个 JSON 数组，每个元素代表一个选中的段落。**只输出 JSON，不要输出任何其他文字。**

```json
[
  {{
    "video_number": 1,
    "start_text": "段落起始处的原文（5-10个字，用于定位）",
    "end_text": "段落结束处的原文（5-10个字，用于定位）",
    "reason": "选择该段落的理由（简要说明）"
  }}
]
```

### 字段说明
- `video_number`：该段落属于第几条视频（从 1 开始）。如果用户要求剪成多条视频，用此字段区分。如果用户没有指定多条，所有段落的 video_number 都填 1。
- `start_text`：段落开头的原文片段，5-10 个字，必须是转录文本中实际出现的文字，用于精确定位起始位置。
- `end_text`：段落结尾的原文片段，5-10 个字，必须是转录文本中实际出现的文字，用于精确定位结束位置。
- `reason`：简要说明为什么选择这个段落（如"核心观点阐述"、"产品卖点总结"、"用户痛点分析"）。

### 好的选段 vs 差的选段

**✅ 好的选段**：
- 起止在自然句子边界，语义完整
- 段落之间逻辑连贯，跳过的内容不影响理解
- 混合了不同长度的段落，节奏感好

**❌ 差的选段**：
- 切在半句话中间（如 start_text="的核心成分就是"）
- 选了大段口头禅和重复内容来凑时长
- 所有段落都差不多长，节奏单调
- 段落之间逻辑断裂，观众无法理解上下文

### 示例

假设转录文本包含一段护肤品推荐口播，目标时长 60 秒：

```json
[
  {{
    "video_number": 1,
    "start_text": "今天给大家推荐一款",
    "end_text": "这就是它的核心成分",
    "reason": "产品核心卖点介绍，信息密度高"
  }},
  {{
    "video_number": 1,
    "start_text": "我自己用了大概两周",
    "end_text": "效果真的非常明显",
    "reason": "真实使用体验，说服力强"
  }},
  {{
    "video_number": 1,
    "start_text": "所以如果你也有这个困扰",
    "end_text": "真的可以试一下",
    "reason": "总结推荐，形成完整叙事闭环"
  }}
]
```"""

# ---------------------------------------------------------------------------
# Duration-based prompt strategy variants
# ---------------------------------------------------------------------------

# Short-form (<= 30s): Focus on the single best moment
DURATION_STRATEGY_SHORT = """## 短视频策略（目标 ≤ 30 秒）

这是一条极短的视频，只需要 **1 个最精华的片段**：
- 选择全文中信息密度最高、最有冲击力的单一段落
- 优先选金句、核心结论、最佳演示瞬间
- 不需要完整叙事，只需要一个"记忆点"
- 如果找不到单个足够好的段落，最多选 2 个短段落"""

# Medium-form (30-120s): Select 3-5 key segments
DURATION_STRATEGY_MEDIUM = """## 中等时长策略（目标 30–120 秒）

选择 **3–5 个关键段落**，组成有起承转合的叙事：
- 开头段：吸引注意力的 hook（5-10 秒）
- 中间段：核心内容、论点、演示（各 10-20 秒）
- 结尾段：总结或行动号召（5-10 秒）
- 段落之间的跳转应自然，跳过的内容不影响观众理解"""

# Long-form (> 120s): Allow more segments, maintain narrative arc
DURATION_STRATEGY_LONG = """## 长视频策略（目标 > 120 秒）

可以选择 **5 个以上段落**，构建完整的叙事弧线：
- 保留更多上下文和过渡，让叙事更流畅
- 可以包含铺垫和背景介绍（但仍跳过明显的废话）
- 注意维持"总-分-总"或"问题-分析-结论"的叙事结构
- 每隔 30-40 秒应有一个小高潮或转折，避免平铺直叙"""


def _get_duration_strategy(target_duration: float) -> str:
    """Return the appropriate prompt strategy section based on target duration."""
    if target_duration <= 30:
        return DURATION_STRATEGY_SHORT
    elif target_duration <= 120:
        return DURATION_STRATEGY_MEDIUM
    else:
        return DURATION_STRATEGY_LONG


class TextDrivenEditingService:
    """Text-driven editing service — ASR transcript + LLM segment selection."""

    def __init__(self):
        self.config = ExternalConfig.get_instance()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_text_driven_timeline(
        self,
        transcript: str,
        word_timestamps: list[dict],
        target_duration: float,
        user_prompt: str = "",
        video_count: int = 1,
    ) -> list[dict] | None:
        """Full text-driven pipeline: LLM select → timestamp map → timeline.

        Args:
            transcript: Full ASR transcript text (with time markers).
            word_timestamps: Whisper word-level timestamps list.
                Each entry: {"word": str, "start": float, "end": float}.
            target_duration: Target output duration in seconds.
            user_prompt: User editing instructions.
            video_count: Number of output videos. When > 1, the LLM is
                instructed to tag each segment with a video_number field.

        Returns:
            Unified timeline list, or None on failure (caller should fallback).
        """
        logger.info(
            "text-driven pipeline: transcript=%d chars, words=%d, target=%.0fs, video_count=%d",
            len(transcript), len(word_timestamps), target_duration, video_count,
        )

        if not transcript or not word_timestamps:
            logger.warning("text-driven pipeline: empty transcript or word_timestamps")
            return None

        # Step 1: LLM segment selection
        selected = self.select_segments_with_llm(
            transcript, target_duration, user_prompt,
            word_timestamps=word_timestamps,
            video_count=video_count,
        )
        if not selected:
            logger.warning("text-driven pipeline: LLM returned no segments")
            return None

        logger.info("text-driven pipeline: LLM selected %d segments", len(selected))

        # Step 2: Map text → timestamps
        timeline = self.map_text_to_timestamps(selected, word_timestamps)
        if not timeline:
            logger.warning("text-driven pipeline: timestamp mapping produced empty timeline")
            return None

        logger.info(
            "text-driven pipeline: generated %d timeline entries, total %.1fs",
            len(timeline),
            timeline[-1]["end"] if timeline else 0,
        )
        return timeline

    def select_segments_with_llm(
        self,
        transcript: str,
        target_duration: float,
        user_prompt: str = "",
        word_timestamps: list[dict] | None = None,
        video_count: int = 1,
    ) -> list[dict] | None:
        """Call LLM to select high-value segments from ASR transcript.

        Args:
            transcript: Full ASR transcript (ideally with time markers).
            target_duration: Target output duration in seconds.
            user_prompt: User editing instructions (highest priority).
            word_timestamps: Optional Whisper word-level timestamps. When
                provided, the transcript is reformatted with time markers
                for better LLM segment selection.
            video_count: Number of output videos. When > 1, the prompt
                instructs the LLM to assign a ``video_number`` field to
                each segment.

        Returns:
            List of {"start_text": str, "end_text": str, "reason": str},
            or None on failure.
        """
        llm_config = self._get_llm_config()
        api_url = llm_config.get("api_url", "")
        api_key = llm_config.get("api_key", "")
        model = llm_config.get("model", "")

        if not api_url or not api_key:
            logger.warning("LLM API not configured for text-driven editing")
            return None

        # Build prompt
        user_instruction = ""
        if user_prompt and user_prompt.strip():
            user_instruction = f"### 用户指令（最高优先级）\n{user_prompt.strip()}"

        target_int = int(target_duration)
        duration_min = int(target_duration * 0.9)
        duration_max = int(target_duration * 1.1)

        # Format transcript with timestamps if word_timestamps available
        if word_timestamps:
            formatted_transcript = _format_transcript_with_timestamps(
                word_timestamps,
            )
        else:
            formatted_transcript = transcript

        # Cap transcript length for token limits; use higher cap for
        # longer target durations (more content needed for selection).
        max_transcript_chars = min(30000, max(5000, int(target_duration * 250)))
        truncated_transcript = formatted_transcript[:max_transcript_chars]

        # Select duration-appropriate strategy
        duration_strategy = _get_duration_strategy(target_duration)

        prompt_text = LLM_SEGMENT_SELECTION_PROMPT.format(
            target_duration=target_int,
            duration_min=duration_min,
            duration_max=duration_max,
            transcript=truncated_transcript,
            user_instruction=user_instruction,
            duration_strategy=duration_strategy,
        )

        # When video_count > 1, prepend multi-video allocation instruction
        # before the transcript section so the LLM tags each segment.
        if video_count > 1:
            multi_video_instruction = (
                f"\n### 多视频分配指令\n"
                f"你需要将选出的段落分配到 {video_count} 条独立视频中。\n"
                f"每个段落的 JSON 对象中必须包含一个 \"video_number\" 字段（整数，1 到 {video_count}），表示该段落属于第几条视频。\n"
                f"每条视频至少包含 1 个段落，每条视频的总时长应接近目标时长。\n"
                f"\n"
                f"输出格式示例：\n"
                f"{{\"start_text\": \"...\", \"end_text\": \"...\", \"reason\": \"...\", \"video_number\": 1}}\n"
            )
            # Insert the multi-video instruction before the transcript
            transcript_marker = "### 转录文本（带时间标记）"
            if transcript_marker in prompt_text:
                prompt_text = prompt_text.replace(
                    transcript_marker,
                    multi_video_instruction + "\n" + transcript_marker,
                )
            else:
                # Fallback: prepend to the entire prompt
                prompt_text = multi_video_instruction + "\n" + prompt_text

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": LLM_SEGMENT_SELECTION_SYSTEM},
                {"role": "user", "content": prompt_text},
            ],
            "max_tokens": 4096,
            "temperature": 0.3,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        logger.info(
            "LLM segment selection: api=%s, model=%s, target=%ds",
            api_url, model, int(target_duration),
        )

        start_time = time.time()
        raw_text = self._call_llm_api(api_url, payload, headers)
        elapsed = time.time() - start_time

        if raw_text is None:
            logger.error("LLM segment selection failed (%.1fs)", elapsed)
            return None

        logger.info("LLM segment selection response received (%.1fs)", elapsed)

        # Parse JSON response
        segments = self._parse_segments_json(raw_text)
        if segments is None:
            return None

        # Validate segments
        validated = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            start_text = seg.get("start_text", "")
            end_text = seg.get("end_text", "")
            if start_text and end_text:
                entry = {
                    "start_text": str(start_text),
                    "end_text": str(end_text),
                    "reason": str(seg.get("reason", "")),
                }
                # Pass through video_number if present (for multi-video splitting)
                vn = seg.get("video_number")
                if vn is not None:
                    try:
                        entry["video_number"] = int(vn)
                    except (ValueError, TypeError):
                        pass
                validated.append(entry)

        if not validated:
            logger.warning("LLM returned segments but none were valid")
            return None

        return validated

    def map_text_to_timestamps(
        self,
        selected_segments: list[dict],
        word_timestamps: list[dict],
    ) -> list[dict]:
        """Map LLM-selected text segments to precise word-level timestamps.

        For each segment:
        1. Find start_text position in word sequence (fuzzy match)
        2. Find end_text position (fuzzy match, searching after start)
        3. Trim filler words from BOTH start and end of segment
        4. Skip segments that are entirely filler words
        5. Apply breath-gap snap for clean cut points

        Args:
            selected_segments: LLM output — list of
                {"start_text": str, "end_text": str, "reason": str}.
            word_timestamps: Whisper word-level timestamps —
                [{"word": str, "start": float, "end": float}, ...].

        Returns:
            Unified timeline format:
            [{"clip_index": 0, "source_start": float, "source_end": float,
              "start": float, "end": float, "reason": str}, ...]
        """
        if not selected_segments or not word_timestamps:
            return []

        # Filter filler words for cleaner matching and cut points
        clean_words = _remove_fillers(word_timestamps)
        if not clean_words:
            clean_words = word_timestamps  # Fallback to unfiltered

        timeline: list[dict] = []
        cursor = 0.0

        for seg in selected_segments:
            start_text = seg.get("start_text", "")
            end_text = seg.get("end_text", "")

            if not start_text or not end_text:
                continue

            # Fuzzy find start position in word sequence
            start_idx = _fuzzy_find_text(word_timestamps, start_text)
            # Search for end_text after start position
            search_from = start_idx if start_idx is not None else 0
            end_idx = _fuzzy_find_text(
                word_timestamps, end_text, search_from=search_from,
            )

            if start_idx is None or end_idx is None:
                logger.warning(
                    "text→timestamp mapping failed: start=%r end=%r",
                    start_text[:20], end_text[:20],
                )
                continue

            # Ensure end is after start
            if end_idx < start_idx:
                end_idx = start_idx

            # --- Trim filler words from the START of the segment ---
            while (
                start_idx < end_idx
                and word_timestamps[start_idx]["word"] in FILLER_WORDS
            ):
                start_idx += 1

            # --- Trim filler words from the END of the segment ---
            while (
                end_idx > start_idx
                and word_timestamps[end_idx]["word"] in FILLER_WORDS
            ):
                end_idx -= 1

            # --- Skip segments that are entirely filler words ---
            if (
                start_idx == end_idx
                and word_timestamps[start_idx]["word"] in FILLER_WORDS
            ):
                logger.debug(
                    "skipping all-filler segment: start=%r end=%r",
                    start_text[:20], end_text[:20],
                )
                continue

            # Calculate cut points with breath-gap snap
            # --- Start cut: snap to the best silence gap near the first word ---
            raw_start = max(0, word_timestamps[start_idx]["start"] - 0.1)
            cut_start = _snap_to_breath_gap(
                word_timestamps, raw_start, search_window=0.5,
            )
            # Never cut *after* the first word starts
            cut_start = min(cut_start, word_timestamps[start_idx]["start"])
            cut_start = max(0, cut_start)

            # --- End cut: snap to the best silence gap near the last word ---
            raw_end = word_timestamps[end_idx]["end"]
            # Use the gap to the next word to determine the search window,
            # capped at 1.0s.  This finds the best gap in the immediate
            # vicinity without bleeding into the next sentence.
            if end_idx + 1 < len(word_timestamps):
                next_start = word_timestamps[end_idx + 1]["start"]
                end_window = min(1.0, (next_start - raw_end))
            else:
                end_window = 1.0
            cut_end = _snap_to_breath_gap(
                word_timestamps, raw_end, search_window=max(end_window, 0.1),
            )
            # Never cut *before* the last word ends
            cut_end = max(cut_end, raw_end)

            duration = cut_end - cut_start
            if duration < 0.5:
                # Skip segments shorter than 0.5s
                continue

            timeline.append({
                "clip_index": 0,
                "video_number": seg.get("video_number", 1),
                "source_start": round(float(cut_start), 3),
                "source_end": round(float(cut_end), 3),
                "start": round(float(cursor), 3),
                "end": round(float(cursor + duration), 3),
                "reason": seg.get("reason", ""),
            })
            cursor += duration

        return timeline

    # ------------------------------------------------------------------
    # Private: LLM configuration
    # ------------------------------------------------------------------

    def _get_llm_config(self) -> dict:
        """Get LLM configuration for text-driven editing.

        Uses the 'text_llm' config section if available, otherwise falls
        back to the default LLM provider from the config.
        """
        # Check for dedicated text_llm config section
        text_llm_url = self.config.get("text_llm.api_url", "")
        text_llm_key = self.config.get("text_llm.api_key", "")
        text_llm_model = self.config.get("text_llm.model", "")

        if text_llm_url and text_llm_key:
            return {
                "api_url": text_llm_url,
                "api_key": text_llm_key,
                "model": text_llm_model or "qwen-plus",
            }

        # Fallback: use default LLM provider
        default_provider = self.config.get_default_provider()
        provider = self.config.get_llm_provider(default_provider)
        if provider and provider.get("api_url") and provider.get("api_key"):
            return {
                "api_url": provider["api_url"],
                "api_key": provider["api_key"],
                "model": provider.get("model", ""),
            }

        # Last resort: use VLM config (it's an LLM-compatible endpoint)
        vlm_config = self.config.get_vlm_config()
        return {
            "api_url": vlm_config.get("api_url", ""),
            "api_key": vlm_config.get("api_key", ""),
            "model": vlm_config.get("model", ""),
        }

    # ------------------------------------------------------------------
    # Private: LLM API call
    # ------------------------------------------------------------------

    def _call_llm_api(
        self,
        api_url: str,
        payload: dict,
        headers: dict,
    ) -> str | None:
        """Call LLM API with one retry on timeout.

        Returns the response text content, or None on failure.
        """
        max_attempts = 2

        for attempt in range(max_attempts):
            try:
                logger.info(
                    "LLM API call attempt %d/%d to %s",
                    attempt + 1, max_attempts, api_url,
                )
                with httpx.Client(timeout=60.0) as client:
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
                    logger.warning("LLM API returned empty content")
                    return None
                else:
                    logger.error(
                        "LLM API HTTP %d: %s",
                        response.status_code,
                        response.text[:300],
                    )
                    # Don't retry on 4xx client errors
                    if 400 <= response.status_code < 500:
                        return None

            except httpx.TimeoutException:
                logger.warning(
                    "LLM API timeout (attempt %d/%d)", attempt + 1, max_attempts,
                )
            except Exception as e:
                logger.error("LLM API error: %s", str(e)[:300])
                return None

        logger.error("LLM API failed after %d attempts", max_attempts)
        return None

    # ------------------------------------------------------------------
    # Private: JSON parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_segments_json(raw_text: str) -> list[dict] | None:
        """Parse LLM segment selection response into list of dicts.

        Handles markdown code fences, qwen3 thinking tags, and extracts JSON array.
        """
        import re as _re

        text = raw_text.strip()

        # Strip qwen3 thinking tags: <think>...</think>
        text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()

        # Try to find JSON array in the text
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            text = text[start:end + 1]

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM segment JSON: %s", str(e))
            logger.debug("Raw LLM response: %s", raw_text[:500])
            return None

        if not isinstance(parsed, list):
            logger.error("LLM response is not a JSON array: %s", type(parsed).__name__)
            return None

        return parsed


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _format_transcript_with_timestamps(
    word_timestamps: list[dict],
) -> str:
    """Format raw word timestamps into time-marked transcript lines.

    Groups consecutive words into natural sentences/phrases by splitting
    on punctuation or long pauses (> SENTENCE_PAUSE_THRESHOLD seconds).

    Output format:
        [00:00.0 - 00:05.2] 大家好，今天跟大家聊一下...
        [00:05.2 - 00:12.8] 第一个点就是关于...

    Args:
        word_timestamps: Whisper word-level timestamps list.
            Each entry: {"word": str, "start": float, "end": float}.

    Returns:
        Formatted transcript string with time markers per sentence.
    """
    if not word_timestamps:
        return ""

    lines: list[str] = []
    current_words: list[str] = []
    group_start: float = word_timestamps[0].get("start", 0.0)
    group_end: float = word_timestamps[0].get("end", 0.0)

    for i, wt in enumerate(word_timestamps):
        word = wt.get("word", "")
        word_start = wt.get("start", 0.0)
        word_end = wt.get("end", 0.0)

        # Check if we should start a new group:
        # 1. Long pause between this word and the previous one
        # 2. Previous word ended with sentence-ending punctuation
        should_break = False
        if current_words:
            gap = word_start - group_end
            if gap > SENTENCE_PAUSE_THRESHOLD:
                should_break = True
            elif current_words and _ends_with_punctuation(current_words[-1]):
                should_break = True

        if should_break and current_words:
            # Flush current group
            text = "".join(current_words)
            lines.append(_format_time_line(group_start, group_end, text))
            current_words = []
            group_start = word_start

        current_words.append(word)
        group_end = word_end

    # Flush remaining words
    if current_words:
        text = "".join(current_words)
        lines.append(_format_time_line(group_start, group_end, text))

    return "\n".join(lines)


def _format_time_line(start: float, end: float, text: str) -> str:
    """Format a single time-marked line: [MM:SS.d - MM:SS.d] text."""
    return f"[{_fmt_ts(start)} - {_fmt_ts(end)}] {text}"


def _fmt_ts(seconds: float) -> str:
    """Format seconds as MM:SS.d (e.g. 01:23.4)."""
    minutes = int(seconds) // 60
    secs = seconds - minutes * 60
    return f"{minutes:02d}:{secs:04.1f}"


def _ends_with_punctuation(word: str) -> bool:
    """Check if a word ends with sentence-boundary punctuation."""
    if not word:
        return False
    return word[-1] in SENTENCE_PUNCTUATION


def _snap_to_breath_gap(
    word_timestamps: list[dict],
    target_time: float,
    search_window: float = 0.5,
) -> float:
    """Find the best silence gap near *target_time* and return its midpoint.

    Replicates the scoring logic of ``_find_breath_gap`` in
    ``ai_director_service.py`` so the text-driven pipeline can snap cut
    points to natural breath gaps without importing from the director
    module.

    The algorithm searches all inter-word gaps whose midpoint falls
    within ``[target_time - search_window, target_time + search_window]``
    and picks the one that maximises
    ``gap_duration / (1 + distance_to_target)`` — i.e. it prefers
    longer silences that are close to the requested time.

    Args:
        word_timestamps: Full word-level timestamp list
            (``[{"word": str, "start": float, "end": float}, ...]``).
        target_time: The ideal cut time we want to snap.
        search_window: Half-width of the search range in seconds.

    Returns:
        The snapped time (midpoint of the best gap), or *target_time*
        unchanged when no suitable gap is found.
    """
    if not word_timestamps:
        return target_time

    candidates: list[dict] = []
    for i in range(len(word_timestamps) - 1):
        gap_start = word_timestamps[i]["end"]
        gap_end = word_timestamps[i + 1]["start"]
        gap_duration = gap_end - gap_start
        if gap_duration <= 0.05:
            continue  # ignore tiny / zero gaps
        gap_mid = (gap_start + gap_end) / 2
        distance = abs(gap_mid - target_time)
        if distance <= search_window:
            candidates.append({
                "mid": gap_mid,
                "duration": gap_duration,
                "distance": distance,
            })

    if not candidates:
        return target_time

    # Same scoring as _find_breath_gap: gap_duration / (1 + distance)
    best = max(candidates, key=lambda c: c["duration"] / (1 + c["distance"]))
    return best["mid"]


def _remove_fillers(word_timestamps: list[dict]) -> list[dict]:
    """Filter out filler words from word timestamp list.

    Returns a new list with filler words removed. The original list
    is not modified.
    """
    return [w for w in word_timestamps if w.get("word", "") not in FILLER_WORDS]


def _is_filler_sequence(
    word_timestamps: list[dict],
    start_idx: int,
    end_idx: int,
) -> bool:
    """Check if a range of words forms a consecutive filler sequence.

    Returns True if **all** words in [start_idx, end_idx] (inclusive)
    are filler words, AND there are at least 2 words in the range.
    A single filler word is not considered a "sequence".

    Args:
        word_timestamps: Full word-level timestamp list.
        start_idx: Start index (inclusive).
        end_idx: End index (inclusive).

    Returns:
        True if the range is a consecutive filler sequence (2+ words).
    """
    if end_idx < start_idx:
        return False
    count = end_idx - start_idx + 1
    if count < 2:
        return False
    return all(
        word_timestamps[i].get("word", "") in FILLER_WORDS
        for i in range(start_idx, end_idx + 1)
    )


# ---------------------------------------------------------------------------
# Common Chinese homophone mapping for ASR error tolerance
# ---------------------------------------------------------------------------
# Maps characters to groups of homophones. When comparing characters,
# two characters in the same homophone group count as a partial match.
# This handles common Whisper ASR substitution errors.

_HOMOPHONE_GROUPS: list[set[str]] = [
    # gēn / gēn — very common ASR confusion
    {"跟", "根", "更"},
    # de — structural particles
    {"的", "得", "地"},
    # tā — pronouns
    {"他", "她", "它"},
    # yī — number one variants
    {"一", "壹"},
    # zài — location/again
    {"在", "再"},
    # zhī / zhǐ
    {"只", "支", "之", "知"},
    # shì — to be / matter
    {"是", "事", "式", "试", "市", "视"},
    # yǒu — have
    {"有", "又", "右", "友"},
    # bù — not
    {"不", "布", "步"},
    # dào — arrive / way
    {"到", "道", "倒"},
    # hé — and / river
    {"和", "合", "河"},
    # jiù — then
    {"就", "旧", "九"},
    # yào — want / medicine
    {"要", "药", "耀"},
    # kě — can
    {"可", "渴", "克"},
    # néng — can
    {"能", "嫩"},
    # huì — will / meeting
    {"会", "汇", "回", "灰"},
    # ràng — let
    {"让", "嚷"},
    # gè — measure word
    {"个", "各"},
    # nà / nèi — that
    {"那", "哪", "拿"},
    # zhè — this
    {"这", "着"},
    # dōu — all
    {"都", "督"},
    # hěn — very
    {"很", "恨", "狠"},
    # duì — correct / pair
    {"对", "队", "兑"},
    # ma — question particle
    {"吗", "嘛", "妈", "马"},
    # ba — suggestion particle
    {"吧", "把", "爸", "八"},
    # le / liǎo — aspect particle
    {"了", "啦"},
    # ne — question particle
    {"呢", "哪"},
    # a — exclamation
    {"啊", "阿"},
    # xiǎng — think / want
    {"想", "响", "像", "象", "向"},
    # zuò — do / sit
    {"做", "作", "坐", "座"},
    # shuō — speak
    {"说", "硕"},
    # kàn — look
    {"看", "砍"},
    # chī — eat
    {"吃", "持", "迟"},
    # hǎo — good
    {"好", "号"},
    # dà — big
    {"大", "达"},
    # xiǎo — small
    {"小", "笑", "校"},
    # gōng — work / public
    {"工", "公", "功", "攻"},
    # chǎn — product
    {"产", "铲"},
    # pǐn — item
    {"品", "拼"},
]

# Build a lookup: character → set of homophones (including itself)
_HOMOPHONE_MAP: dict[str, set[str]] = {}
for _group in _HOMOPHONE_GROUPS:
    for _char in _group:
        _HOMOPHONE_MAP[_char] = _group


def _is_homophone(char_a: str, char_b: str) -> bool:
    """Check if two characters are homophones (in the same group)."""
    if char_a == char_b:
        return True
    group = _HOMOPHONE_MAP.get(char_a)
    return group is not None and char_b in group


# ---------------------------------------------------------------------------
# Fuzzy matching: _fuzzy_find_text and _char_overlap_score
# ---------------------------------------------------------------------------

# Score weight for homophone matches (partial credit)
_HOMOPHONE_WEIGHT: float = 0.7


def _fuzzy_find_text(
    word_timestamps: list[dict],
    target_text: str,
    search_from: int = 0,
) -> int | None:
    """Find the best matching position for target_text in word sequence.

    Uses a sliding-window approach with a two-pass strategy:
    - Pass 1 (strict): threshold 0.7 — finds high-confidence matches
    - Pass 2 (relaxed): threshold 0.4 — handles heavy ASR errors

    Handles ASR transcription errors including:
    - Homophone substitutions (同音字): e.g. "跟" → "根"
    - Missing characters (漏字): ASR may drop characters
    - Extra characters (多字): ASR may insert extra characters
    - Word boundary errors: ASR may split/merge words differently

    Args:
        word_timestamps: Full word-level timestamp list.
        target_text: The text string to locate.
        search_from: Start searching from this index.

    Returns:
        Index into word_timestamps of the best match, or None if no
        reasonable match found.
    """
    if not word_timestamps or not target_text:
        return None

    target_text = target_text.strip()
    if not target_text:
        return None

    target_len = len(target_text)

    # Determine window size based on target text length.
    # Allow extra room for ASR insertions (extra characters).
    # For very short targets (1-3 chars), use a small window but at least 2.
    if target_len <= 3:
        window_size = max(2, target_len + 2)
    else:
        # Allow 50% extra for ASR errors (insertions/splits)
        window_size = max(3, min(int(target_len * 1.5), 30))

    # Pass 1: strict threshold (0.7)
    result = _fuzzy_find_text_pass(
        word_timestamps, target_text, search_from, window_size,
        threshold=0.7,
    )
    if result is not None:
        return result

    # Pass 2: relaxed threshold (0.4) — handles heavy ASR errors
    return _fuzzy_find_text_pass(
        word_timestamps, target_text, search_from, window_size,
        threshold=0.4,
    )


def _fuzzy_find_text_pass(
    word_timestamps: list[dict],
    target_text: str,
    search_from: int,
    window_size: int,
    threshold: float,
) -> int | None:
    """Single pass of fuzzy text matching with a given threshold.

    Args:
        word_timestamps: Full word-level timestamp list.
        target_text: The text string to locate.
        search_from: Start searching from this index.
        window_size: Maximum number of words to concatenate per window.
        threshold: Minimum similarity score to accept a match.

    Returns:
        Index of best match, or None if best score < threshold.
    """
    target_len = len(target_text)
    best_idx: int | None = None
    best_score: float = 0.0

    n = len(word_timestamps)

    for i in range(search_from, n):
        candidate = ""
        for j in range(i, min(i + window_size, n)):
            candidate += word_timestamps[j].get("word", "")

            score = _char_overlap_score(target_text, candidate)

            if score > best_score:
                best_score = score
                best_idx = i

            # Early exit on near-perfect match
            if score > 0.9:
                return best_idx

            # Stop extending if candidate is already much longer than target
            if len(candidate) > target_len * 2:
                break

    if best_score < threshold:
        logger.debug(
            "fuzzy_find_text (threshold=%.1f): no match for %r (best_score=%.2f)",
            threshold, target_text[:30], best_score,
        )
        return None

    return best_idx


def _char_overlap_score(text_a: str, text_b: str) -> float:
    """Compute character-level overlap ratio between two strings.

    Returns a value in [0, 1] representing how similar the two strings
    are based on shared characters. Uses homophone-aware matching to
    handle common ASR transcription errors (同音字替换).

    Matching rules:
    - Exact character match: full credit (1.0)
    - Homophone match: partial credit (0.7)
    - No match: 0

    Uses a dynamic-programming LCS variant that gives partial credit
    for homophone matches, making it robust against ASR errors like
    missing characters, extra characters, and homophone substitutions.

    Args:
        text_a: First string (typically the target/query text).
        text_b: Second string (typically the candidate from ASR).

    Returns:
        Similarity score in [0.0, 1.0].
    """
    if not text_a or not text_b:
        return 0.0

    len_a = len(text_a)
    len_b = len(text_b)

    # DP table for LCS with homophone partial credit.
    # dp[i][j] = best score using text_a[:i] and text_b[:j]
    # Use two rows to save memory.
    prev = [0.0] * (len_b + 1)
    curr = [0.0] * (len_b + 1)

    for i in range(1, len_a + 1):
        char_a = text_a[i - 1]
        for j in range(1, len_b + 1):
            char_b = text_b[j - 1]
            if char_a == char_b:
                # Exact match
                curr[j] = prev[j - 1] + 1.0
            elif _is_homophone(char_a, char_b):
                # Homophone match — partial credit
                curr[j] = prev[j - 1] + _HOMOPHONE_WEIGHT
            else:
                # Skip one character from either string
                curr[j] = max(prev[j], curr[j - 1])
        prev, curr = curr, prev
        curr = [0.0] * (len_b + 1)

    # Best score is in prev[len_b] (after the swap)
    best_score = prev[len_b]

    # Normalize by the longer string length
    max_len = max(len_a, len_b)
    return best_score / max_len if max_len > 0 else 0.0
