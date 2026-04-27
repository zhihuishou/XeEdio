"""VLM (Vision Language Model) service for AI Director.

Handles frame extraction from A-roll videos and VLM API communication
for intelligent timeline generation.
"""
from __future__ import annotations


import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

import httpx

from app.services.external_config import ExternalConfig

logger = logging.getLogger("app.vlm_service")

# ---------------------------------------------------------------------------
# Default frame extraction settings for long videos
# ---------------------------------------------------------------------------
# Max width for VLM frames — 320px is enough for scene understanding,
# cuts base64 payload by ~60% vs 512px.
DEFAULT_FRAME_WIDTH = 320
# JPEG quality for VLM frames (FFmpeg -q:v scale: 2=best, 31=worst).
# 8 gives ~15-25KB per frame at 320px width.
DEFAULT_JPEG_QUALITY = 8
# Max frames per single VLM API call (chunk size)
VLM_CHUNK_SIZE = 15

# System prompt instructing the VLM how to act as an AI video director
VLM_SYSTEM_PROMPT = """You are an expert AI video director. You analyze video frames and audio transcripts to create intelligent editing timelines.

Given:
- A sequence of frames from the main video (A-roll) with timestamps
- The audio transcript
- Available B-roll footage descriptions

Your task: Determine the optimal moments to cut away from A-roll to B-roll.

Rules:
1. NEVER cut away during product demonstrations, hand gestures, or key visual moments
2. Prefer cutting to B-roll during conceptual/abstract narration, transitions between topics, or pauses
3. B-roll insertions should be SHORT (1-2 seconds)
4. The timeline must cover the entire video duration with no gaps
5. Start and end with A-roll

Return ONLY a JSON array, no other text:
[{"type": "a_roll", "start": 0, "end": 4, "reason": "..."}, ...]"""

# System prompt for montage mode — all clips are equal, no A-roll/B-roll distinction
VLM_MONTAGE_SYSTEM_PROMPT = """You are an expert AI video director specializing in montage editing. You analyze multiple video clips and arrange them into a visually compelling montage.

Given:
- A list of video clips with frame previews, filenames, and durations
- An optional theme/topic description

Your task: Create an editing timeline that arranges these clips into a cohesive, visually engaging montage.

Rules:
1. ALL clips are equal — there is no "main" footage. Treat every clip as a potential segment.
2. Analyze visual content, color, motion, and composition to determine the best arrangement order.
3. Each clip can be used partially (trimmed) or fully. Use the best portions of each clip.
4. Aim for visual variety — avoid placing visually similar clips next to each other.
5. Create rhythm through varying segment durations (mix short 1-2s cuts with longer 3-5s holds).
6. The timeline must have no gaps.
7. Use "clip_index" to reference which source clip to use (0-based index).

Return ONLY a JSON array, no other text:
[{"type": "clip", "clip_index": 0, "start": 0.0, "end": 3.5, "source_start": 0.0, "source_end": 3.5, "reason": "..."}, ...]

Where:
- "type" is always "clip"
- "clip_index" is the 0-based index of the source clip
- "start"/"end" are the positions in the OUTPUT timeline
- "source_start"/"source_end" are the trim points within the SOURCE clip
- "reason" explains the editorial decision"""

# System prompt for highlight selection — pick the best segments from a long video
VLM_HIGHLIGHT_SYSTEM_PROMPT = """You are an expert AI video director. Your task is to select the most valuable segments from a long video.

Given:
- Sampled frames from a long video with timestamps
- The total video duration
- The number of highlight segments to select
- The target duration for each segment
- **Director instructions from the user (HIGHEST PRIORITY — follow these first)**

Your task: Identify the best time ranges to extract as highlight clips.

Rules:
1. **USER INSTRUCTIONS TAKE PRIORITY.** If the user specifies what content to select (e.g. "only product demos", "focus on the speaker's key points", "select outdoor scenes"), you MUST follow those instructions above all other rules.
2. Select segments that are visually interesting, content-rich, or emotionally engaging.
3. Avoid selecting segments with dead air, static shots with no action, or blurry/transitional frames.
4. Spread selections across the full video — don't cluster all highlights in one section.
5. Each segment should be self-contained (start and end at natural cut points).
6. Segments must NOT overlap.

Return ONLY a JSON array, no other text:
[{"start": 120.0, "end": 180.0, "reason": "..."}, ...]

Where:
- "start"/"end" are seconds in the source video
- "reason" explains why this segment was selected"""


# System prompt for single-clip analysis (Stage 1 — upload-time)
VLM_SINGLE_CLIP_SYSTEM_PROMPT = """你是专业视频导演。分析提供的视频帧，输出结构化 JSON 摘要。

仅输出一个 JSON 对象，包含以下字段：
{
  "description": "用中文简要描述视频内容",
  "role": "以下之一：presenter, product_closeup, lifestyle, transition, other",
  "visual_quality": "以下之一：high, medium, low",
  "scene_tags": ["标签1", "标签2", ...],
  "key_moments": [{"time": 5.0, "desc": "关键画面描述"}, ...]
}

角色定义：
- presenter：人物面对镜头讲解、主持、演示或展示
- product_closeup：产品、包装或细节的特写镜头
- lifestyle：生活场景、户外镜头、氛围画面
- transition：短过渡片段、动态图形或填充画面
- other：不属于以上类别的内容

scene_tags 应为描述内容的中文关键词（如"室内"、"美妆"、"产品展示"、"口播"）。
key_moments 应标注最重要的视觉时刻及其时间戳。"""


# System prompt for Stage 2 — unified timeline generation (mix-time)
VLM_UNIFIED_TIMELINE_SYSTEM_PROMPT = """你是专业视频导演。根据素材分析结果和用户指令，规划一条视频时间线。

规则：
1. 用户指令优先级最高
2. 如有脚本文案，剪辑节奏应配合文案叙事
3. 主讲类素材作为叙事主轴，产品特写/空镜作为视觉丰富
4. 避免在关键动作中间切走
5. 短插入（1-3s）用于节奏变化，长片段（5-15s）用于信息传递
6. 同一素材的不同片段可以多次使用，但避免连续重复
7. timeline 必须无间隙，覆盖完整输出时长

输出 JSON timeline：
[{"clip_index": 0, "source_start": 0.0, "source_end": 8.0, "start": 0.0, "end": 8.0, "reason": "..."}, ...]"""


class VLMService:
    """VLM frame analysis and timeline generation service."""

    def __init__(self):
        self.config = ExternalConfig.get_instance()

    # ------------------------------------------------------------------
    # Frame extraction
    # ------------------------------------------------------------------

    def extract_frames(
        self,
        video_path: str,
        frame_interval: float = 2.0,
        max_frames: int = 30,
        max_width: int = DEFAULT_FRAME_WIDTH,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
    ) -> list[tuple[float, str]]:
        """Extract frames from video using FFmpeg.

        Extracts one frame every *frame_interval* seconds, resizes to
        *max_width* (preserving aspect ratio), compresses with *jpeg_quality*,
        and returns base64 strings paired with their timestamps.
        """
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        ffmpeg_bin = self._get_ffmpeg_binary()
        if not shutil.which(ffmpeg_bin):
            raise RuntimeError("FFmpeg is required but was not found")

        duration = self._get_video_duration(video_path)
        if duration <= 0:
            raise RuntimeError(f"Video has zero duration: {video_path}")

        temp_dir = tempfile.mkdtemp(prefix="vlm_frames_")
        try:
            cmd = [
                ffmpeg_bin,
                "-i", video_path,
                "-vf", f"fps=1/{frame_interval},scale={max_width}:-1",
                "-q:v", str(jpeg_quality),
                "-y",
                os.path.join(temp_dir, "frame_%04d.jpg"),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                error_msg = (result.stderr or result.stdout or "").strip()
                raise RuntimeError(
                    f"FFmpeg frame extraction failed (rc={result.returncode}): "
                    f"{error_msg[:500]}"
                )

            # 5. Read extracted frames, base64 encode, pair with timestamps
            frames: list[tuple[float, str]] = []
            frame_files = sorted(
                f for f in os.listdir(temp_dir) if f.startswith("frame_") and f.endswith(".jpg")
            )

            for idx, filename in enumerate(frame_files):
                if idx >= max_frames:
                    break
                timestamp = idx * frame_interval
                if timestamp >= duration:
                    break
                filepath = os.path.join(temp_dir, filename)
                with open(filepath, "rb") as fh:
                    b64_str = base64.b64encode(fh.read()).decode("ascii")
                frames.append((timestamp, b64_str))

            logger.info(
                "extracted %d frames from %s (duration=%.1fs, interval=%.1fs)",
                len(frames), video_path, duration, frame_interval,
            )
            return frames

        finally:
            # Clean up temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Single-clip analysis (Stage 1 — upload-time)
    # ------------------------------------------------------------------

    def analyze_single_clip(
        self,
        frames: list[tuple[float, str]],
        clip_metadata: dict,
    ) -> dict | None:
        """Upload-time analysis: analyze a single clip.

        Returns structured summary dict, or None on failure.
        Called asynchronously after asset upload, results stored in
        asset_analysis table.

        Args:
            frames: List of (timestamp, base64_image) tuples from extract_frames.
            clip_metadata: Dict with at least 'filename' and 'duration' keys.

        Returns:
            Dict with keys: description, role, visual_quality, key_moments,
            scene_tags — or None on VLM call / parse failure.
        """
        vlm_config = self.config.get_vlm_config()
        api_url = vlm_config.get("api_url", "")
        api_key = vlm_config.get("api_key", "")
        model = vlm_config.get("model", "qwen3-vl-plus")

        if not api_url or not api_key:
            logger.warning("VLM API not configured, skipping single-clip analysis")
            return None

        if not frames:
            logger.warning("No frames provided for single-clip analysis")
            return None

        filename = clip_metadata.get("filename", "unknown")
        duration = clip_metadata.get("duration", 0)

        # Build multimodal content
        content = self._build_single_clip_content(frames, filename, duration)

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": VLM_SINGLE_CLIP_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            "max_tokens": 2048,
            "temperature": 0.3,
            "stream": False,
            "enable_thinking": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        raw_text = self._call_vlm_api(api_url, payload, headers)
        if not raw_text:
            logger.warning("VLM returned no content for single-clip analysis of %s", filename)
            return None

        result = self._parse_single_clip_json(raw_text)
        if result is None:
            logger.warning("Failed to parse single-clip analysis for %s", filename)
            return None

        logger.info(
            "single-clip analysis completed: %s → role=%s, quality=%s, tags=%s",
            filename, result["role"], result["visual_quality"], result["scene_tags"],
        )
        return result

    def _build_single_clip_content(
        self,
        frames: list[tuple[float, str]],
        filename: str,
        duration: float,
    ) -> list[dict]:
        """Build multimodal content for single-clip analysis prompt."""
        content: list[dict] = []

        content.append({
            "type": "text",
            "text": (
                f"你是专业视频导演。分析以下素材，输出内容摘要。\n\n"
                f"素材：{filename} ({duration:.1f}s) — [帧预览 ×{len(frames)}]"
            ),
        })

        for timestamp, b64_data in frames:
            content.append({"type": "text", "text": f"[Frame at {timestamp:.1f}s]"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
            })

        content.append({
            "type": "text",
            "text": (
                '请输出 JSON：\n'
                '{\n'
                '  "description": "内容描述",\n'
                '  "role": "presenter / product_closeup / lifestyle / transition / other",\n'
                '  "visual_quality": "high / medium / low",\n'
                '  "key_moments": [{"time": 5.0, "desc": "关键画面描述"}],\n'
                '  "scene_tags": ["标签1", "标签2"]\n'
                '}'
            ),
        })

        return content

    @staticmethod
    def _parse_single_clip_json(raw_text: str) -> dict | None:
        """Parse VLM single-clip analysis response into structured dict.

        Extracts a JSON object from the response, validates and normalizes
        the expected fields.
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

        # Find JSON object boundaries
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            logger.warning("No JSON object found in single-clip response: %s", text[:200])
            return None

        try:
            data = json.loads(text[start:end + 1])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to parse single-clip JSON: %s — %s", str(exc), text[:200])
            return None

        # Build normalized result
        result: dict = {
            "description": str(data.get("description", "")),
            "role": str(data.get("role", "other")),
            "visual_quality": str(data.get("visual_quality", "medium")),
            "key_moments": data.get("key_moments", []),
            "scene_tags": data.get("scene_tags", []),
        }

        # Normalize role to valid set
        valid_roles = {"presenter", "product_closeup", "lifestyle", "transition", "other"}
        if result["role"] not in valid_roles:
            result["role"] = "other"

        # Normalize visual_quality to valid set
        valid_qualities = {"high", "medium", "low"}
        if result["visual_quality"] not in valid_qualities:
            result["visual_quality"] = "medium"

        # Ensure key_moments is a list of dicts
        if not isinstance(result["key_moments"], list):
            result["key_moments"] = []
        result["key_moments"] = [
            km for km in result["key_moments"]
            if isinstance(km, dict) and "time" in km and "desc" in km
        ]

        # Ensure scene_tags is a list of strings
        if not isinstance(result["scene_tags"], list):
            result["scene_tags"] = []
        result["scene_tags"] = [str(t) for t in result["scene_tags"] if t]

        return result

    # ------------------------------------------------------------------
    # Unified timeline generation (Stage 2 — mix-time)
    # ------------------------------------------------------------------

    def generate_unified_timeline(
        self,
        clip_summaries: list[dict],
        dense_frames: list[list[tuple[float, str]]],
        clip_metadata: list[dict],
        target_duration: float,
        user_prompt: str = "",
        script_text: str = "",
    ) -> list[dict] | None:
        """Mix-time: vision-driven editing plan.

        Receives clip summaries loaded from DB (no real-time VLM analysis),
        returns unified timeline, or None on failure (fallback).

        Args:
            clip_summaries: List of dicts from asset_analysis DB, each with
                keys: description, role, key_moments, scene_tags, etc.
            dense_frames: List of frame lists, one per clip.
                Each is [(timestamp, base64_image), ...].
            clip_metadata: List of dicts with at least 'filename' and 'duration'.
            target_duration: Desired output duration in seconds.
            user_prompt: User director instructions.
            script_text: Script text for TTS-guided editing (optional).

        Returns:
            List of unified timeline entry dicts, or None on failure.
        """
        vlm_config = self.config.get_vlm_config()
        api_url = vlm_config.get("api_url", "")
        api_key = vlm_config.get("api_key", "")
        model = vlm_config.get("model", "qwen3-vl-plus")

        if not api_url or not api_key:
            logger.warning("VLM API not configured, skipping unified timeline generation")
            return None

        if not clip_summaries or not clip_metadata:
            logger.warning("No clip summaries or metadata provided for unified timeline")
            return None

        user_content = self._build_unified_timeline_content(
            clip_summaries, dense_frames, clip_metadata, target_duration,
            user_prompt, script_text,
        )

        system_prompt = VLM_UNIFIED_TIMELINE_SYSTEM_PROMPT

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 4096,
            "temperature": 0.3,
            "stream": False,
            "enable_thinking": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        raw_text = self._call_vlm_api(api_url, payload, headers)
        if raw_text is None:
            logger.warning("VLM returned no content for unified timeline")
            return None

        timeline = self._parse_timeline_json(raw_text)
        if timeline is None:
            logger.warning("Failed to parse unified timeline JSON")
            return None

        if not self._validate_unified_timeline(timeline, clip_metadata):
            logger.warning("VLM returned invalid unified timeline, falling back")
            return None

        logger.info("VLM generated valid unified timeline with %d entries", len(timeline))
        return timeline

    def _build_unified_timeline_content(
        self,
        clip_summaries: list[dict],
        dense_frames: list[list[tuple[float, str]]],
        clip_metadata: list[dict],
        target_duration: float,
        user_prompt: str = "",
        script_text: str = "",
    ) -> list[dict]:
        """Build multimodal content for Stage 2 unified timeline prompt."""
        content: list[dict] = []

        # Intro with target duration
        content.append({
            "type": "text",
            "text": (
                f"你是专业视频导演。根据素材分析结果和用户指令，"
                f"规划一条 {target_duration:.0f} 秒的视频。"
            ),
        })

        # Clip summaries from DB
        summaries_text = "素材摘要：\n"
        for idx, (summary, meta) in enumerate(zip(clip_summaries, clip_metadata)):
            filename = meta.get("filename", f"clip_{idx}")
            duration = meta.get("duration", 0)
            description = summary.get("description", "无描述")
            role = summary.get("role", "other")
            key_moments = summary.get("key_moments", [])

            moments_str = ""
            if key_moments:
                moments_parts = [
                    f"{km.get('time', 0):.1f}s: {km.get('desc', '')}"
                    for km in key_moments
                    if isinstance(km, dict)
                ]
                if moments_parts:
                    moments_str = f"  关键时刻: {'; '.join(moments_parts)}"

            summaries_text += (
                f"  clip_{idx}: {filename} ({duration:.1f}s) — "
                f"角色: {role}, 描述: {description}"
            )
            if moments_str:
                summaries_text += f"\n{moments_str}"
            summaries_text += "\n"

        content.append({"type": "text", "text": summaries_text})

        # Dense frames for visual reference (if provided)
        for idx, frames in enumerate(dense_frames):
            if not frames:
                continue
            filename = clip_metadata[idx].get("filename", f"clip_{idx}") if idx < len(clip_metadata) else f"clip_{idx}"
            content.append({
                "type": "text",
                "text": f"\n--- clip_{idx}: {filename} 帧预览 ---",
            })
            for timestamp, b64_data in frames:
                content.append({
                    "type": "text",
                    "text": f"[clip_{idx} @ {timestamp:.1f}s]",
                })
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
                })

        # User prompt / director instructions
        if user_prompt and user_prompt.strip():
            content.append({
                "type": "text",
                "text": f"用户指令：{user_prompt.strip()}",
            })

        # Script text (if provided, for TTS-guided editing)
        if script_text and script_text.strip():
            content.append({
                "type": "text",
                "text": f"脚本文案：{script_text.strip()}",
            })

        # Final instruction
        content.append({
            "type": "text",
            "text": (
                f"请规划一条 {target_duration:.0f} 秒的视频 timeline，"
                f"覆盖完整输出时长，无间隙。仅输出 JSON 数组。"
            ),
        })

        return content

    def _validate_unified_timeline(
        self,
        timeline: list[dict],
        clip_metadata: list[dict],
    ) -> bool:
        """Validate unified timeline structure.

        Checks:
        - Non-empty list
        - Each entry has clip_index, source_start, source_end, start, end, reason
        - clip_index is valid (0 <= clip_index < num_clips)
        - source times are valid (source_start >= 0, source_end > source_start)
        - source times within clip duration (with tolerance)
        - Output times are valid (start >= 0, end > start)
        - No overlaps in output timeline
        """
        if not isinstance(timeline, list) or len(timeline) == 0:
            logger.error("Unified timeline validation failed: empty or not a list")
            return False

        num_clips = len(clip_metadata)
        prev_end = -1.0

        for i, entry in enumerate(timeline):
            if not isinstance(entry, dict):
                logger.error("Unified timeline entry %d is not a dict", i)
                return False

            # Check required fields
            for field in ("clip_index", "source_start", "source_end", "start", "end", "reason"):
                if field not in entry:
                    logger.error("Unified timeline entry %d missing field '%s'", i, field)
                    return False

            # Validate clip_index
            clip_index = entry["clip_index"]
            if not isinstance(clip_index, int) or clip_index < 0 or clip_index >= num_clips:
                logger.error(
                    "Unified timeline entry %d invalid clip_index: %r (max %d)",
                    i, clip_index, num_clips - 1,
                )
                return False

            # Validate source times
            source_start = entry["source_start"]
            source_end = entry["source_end"]
            if not isinstance(source_start, (int, float)) or not isinstance(source_end, (int, float)):
                logger.error("Unified timeline entry %d non-numeric source_start/source_end", i)
                return False
            if source_start < 0 or source_end <= source_start:
                logger.error(
                    "Unified timeline entry %d invalid source range %s-%s",
                    i, source_start, source_end,
                )
                return False

            # Check source times within clip duration (with 1s tolerance)
            clip_duration = clip_metadata[clip_index].get("duration", float("inf"))
            if source_end > clip_duration + 1.0:
                logger.error(
                    "Unified timeline entry %d source_end=%s exceeds clip %d duration=%s",
                    i, source_end, clip_index, clip_duration,
                )
                return False

            # Validate output times
            start = entry["start"]
            end = entry["end"]
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                logger.error("Unified timeline entry %d non-numeric start/end", i)
                return False
            if start < 0 or end <= start:
                logger.error(
                    "Unified timeline entry %d invalid output range start=%s end=%s",
                    i, start, end,
                )
                return False

            # Check no overlaps (allow 0.05s tolerance)
            if prev_end > start + 0.05:
                logger.error(
                    "Unified timeline overlap: entry %d start=%s < prev end=%s",
                    i, start, prev_end,
                )
                return False

            # Validate reason is a string
            reason = entry["reason"]
            if not isinstance(reason, str):
                logger.error("Unified timeline entry %d 'reason' is not a string: %r", i, reason)
                return False

            prev_end = end

        return True

    # ------------------------------------------------------------------
    # Timeline generation (legacy A-roll/B-roll)
    # ------------------------------------------------------------------

    def generate_timeline(
        self,
        frames: list[tuple[float, str]],
        transcript: str,
        b_roll_descriptions: list[dict],
        a_roll_duration: float,
        user_prompt: str = "",
    ) -> list[dict] | None:
        """Send frames to VLM API and get editing timeline.

        Constructs a multimodal prompt with base64 frames and text context,
        sends it to the VLM API, parses the JSON response, validates it,
        and returns the timeline list or None on failure.

        Args:
            frames: List of (timestamp, base64_image) from extract_frames.
            transcript: Audio transcript text for context.
            b_roll_descriptions: List of {"filename": str, "duration": float}.
            a_roll_duration: Total A-roll duration in seconds.

        Returns:
            List of timeline entry dicts, or None on failure.
        """
        vlm_config = self.config.get_vlm_config()
        api_url = vlm_config.get("api_url", "")
        api_key = vlm_config.get("api_key", "")
        model = vlm_config.get("model", "qwen3-vl-plus")

        if not api_url or not api_key:
            logger.warning("VLM API not configured (missing api_url or api_key), skipping")
            return None

        # Build multimodal message content
        user_content = self._build_multimodal_content(
            frames, transcript, b_roll_descriptions, a_roll_duration
        )

        # Build system prompt with optional user directives
        system_prompt = VLM_SYSTEM_PROMPT
        if user_prompt and user_prompt.strip():
            system_prompt += f"\n\nAdditional director instructions from the user:\n{user_prompt.strip()}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 4096,
            "temperature": 0.3,
            "stream": False,
            "enable_thinking": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        # Send request with one retry on timeout
        raw_text = self._call_vlm_api(api_url, payload, headers)
        if raw_text is None:
            return None

        # Parse JSON from response
        timeline = self._parse_timeline_json(raw_text)
        if timeline is None:
            return None

        # Validate timeline
        if not self.validate_timeline(timeline, a_roll_duration):
            logger.warning("VLM returned invalid timeline, falling back")
            return None

        logger.info("VLM generated valid timeline with %d entries", len(timeline))
        return timeline

    def generate_montage_timeline(
        self,
        clip_frames: list[list[tuple[float, str]]],
        clip_descriptions: list[dict],
        target_duration: float,
        user_prompt: str = "",
    ) -> list[dict] | None:
        """Generate a montage timeline from multiple equal clips.

        Unlike generate_timeline which assumes A-roll/B-roll hierarchy,
        this treats all clips equally and asks the VLM to arrange them
        into a visually compelling montage.

        Args:
            clip_frames: List of frame lists, one per clip.
                         Each is [(timestamp, base64_image), ...].
            clip_descriptions: List of {"filename": str, "duration": float, "index": int}.
            target_duration: Desired output duration in seconds.
            user_prompt: Optional user directives for the VLM.

        Returns:
            List of montage timeline entry dicts, or None on failure.
        """
        vlm_config = self.config.get_vlm_config()
        api_url = vlm_config.get("api_url", "")
        api_key = vlm_config.get("api_key", "")
        model = vlm_config.get("model", "qwen3-vl-plus")

        if not api_url or not api_key:
            logger.warning("VLM API not configured, skipping montage timeline")
            return None

        user_content = self._build_montage_content(
            clip_frames, clip_descriptions, target_duration
        )

        system_prompt = VLM_MONTAGE_SYSTEM_PROMPT
        if user_prompt and user_prompt.strip():
            system_prompt += f"\n\nAdditional director instructions from the user:\n{user_prompt.strip()}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 4096,
            "temperature": 0.3,
            "stream": False,
            "enable_thinking": False,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        raw_text = self._call_vlm_api(api_url, payload, headers)
        if raw_text is None:
            return None

        timeline = self._parse_timeline_json(raw_text)
        if timeline is None:
            return None

        if not self._validate_montage_timeline(timeline, clip_descriptions):
            logger.warning("VLM returned invalid montage timeline, falling back")
            return None

        logger.info("VLM generated valid montage timeline with %d entries", len(timeline))
        return timeline

    def _validate_montage_timeline(
        self,
        timeline: list[dict],
        clip_descriptions: list[dict],
    ) -> bool:
        """Validate montage timeline structure."""
        if not isinstance(timeline, list) or len(timeline) == 0:
            logger.error("Montage timeline validation failed: empty or not a list")
            return False

        num_clips = len(clip_descriptions)
        prev_end = -1.0

        for i, entry in enumerate(timeline):
            if not isinstance(entry, dict):
                logger.error("Montage timeline entry %d is not a dict", i)
                return False

            for field in ("type", "clip_index", "start", "end", "source_start", "source_end", "reason"):
                if field not in entry:
                    logger.error("Montage timeline entry %d missing field '%s'", i, field)
                    return False

            clip_index = entry["clip_index"]
            if not isinstance(clip_index, int) or clip_index < 0 or clip_index >= num_clips:
                logger.error("Montage timeline entry %d invalid clip_index: %r (max %d)", i, clip_index, num_clips - 1)
                return False

            start = entry["start"]
            end = entry["end"]
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                logger.error("Montage timeline entry %d non-numeric start/end", i)
                return False
            if start < 0 or end <= start:
                logger.error("Montage timeline entry %d invalid start=%s end=%s", i, start, end)
                return False

            source_start = entry["source_start"]
            source_end = entry["source_end"]
            if not isinstance(source_start, (int, float)) or not isinstance(source_end, (int, float)):
                logger.error("Montage timeline entry %d non-numeric source_start/source_end", i)
                return False
            if source_start < 0 or source_end <= source_start:
                logger.error("Montage timeline entry %d invalid source range %s-%s", i, source_start, source_end)
                return False

            if prev_end > start + 0.05:
                logger.error("Montage timeline overlap: entry %d start=%s < prev end=%s", i, start, prev_end)
                return False

            prev_end = end

        return True

    def _build_montage_content(
        self,
        clip_frames: list[list[tuple[float, str]]],
        clip_descriptions: list[dict],
        target_duration: float,
    ) -> list[dict]:
        """Build multimodal content for montage timeline generation."""
        content: list[dict] = []

        content.append({
            "type": "text",
            "text": (
                f"You have {len(clip_descriptions)} video clips to arrange into a montage. "
                f"Target output duration: ~{target_duration:.0f}s. "
                f"Analyze the frames from each clip and create an engaging arrangement."
            ),
        })

        for idx, (frames, desc) in enumerate(zip(clip_frames, clip_descriptions)):
            filename = desc.get("filename", f"clip_{idx}")
            duration = desc.get("duration", 0)
            content.append({
                "type": "text",
                "text": f"\n--- Clip {idx}: {filename} (duration: {duration:.1f}s) ---",
            })
            for timestamp, b64_data in frames:
                content.append({
                    "type": "text",
                    "text": f"[Clip {idx} @ {timestamp:.1f}s]",
                })
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
                })

        content.append({
            "type": "text",
            "text": (
                f"Generate a JSON timeline array for a ~{target_duration:.0f}s montage. "
                f"Use the best portions of each clip. Return ONLY the JSON array."
            ),
        })

        return content

    def select_highlights(
        self,
        frames: list[tuple[float, str]],
        total_duration: float,
        num_segments: int,
        segment_duration: float,
        user_prompt: str = "",
    ) -> list[dict] | None:
        """Ask VLM to select the best highlight segments from a long video.

        For long videos (many frames), splits frames into chunks and sends
        concurrent VLM requests, then merges results. This avoids payload
        size limits and VLM attention degradation.

        Args:
            frames: Sparse frames [(timestamp, base64_image), ...].
            total_duration: Total video duration in seconds.
            num_segments: How many highlight segments to select.
            segment_duration: Target duration for each segment in seconds.
            user_prompt: Director instructions (highest priority).

        Returns:
            List of {"start": float, "end": float, "reason": str}, or None.
        """
        vlm_config = self.config.get_vlm_config()
        api_url = vlm_config.get("api_url", "")
        api_key = vlm_config.get("api_key", "")
        model = vlm_config.get("model", "qwen3-vl-plus")

        if not api_url or not api_key:
            logger.warning("VLM API not configured, skipping highlight selection")
            return None

        # --- Chunked concurrent strategy ---
        # Split frames into chunks of VLM_CHUNK_SIZE, send each chunk
        # as a separate VLM request asking for local highlights, then merge.
        chunks = []
        for i in range(0, len(frames), VLM_CHUNK_SIZE):
            chunks.append(frames[i:i + VLM_CHUNK_SIZE])

        if len(chunks) <= 1:
            # Small enough for a single request
            return self._select_highlights_single(
                frames, total_duration, num_segments, segment_duration,
                api_url, api_key, model, user_prompt,
            )

        # Multiple chunks: concurrent requests
        logger.info(
            "highlight selection: %d frames split into %d chunks of ≤%d",
            len(frames), len(chunks), VLM_CHUNK_SIZE,
        )

        # Ask each chunk for proportional number of candidates (over-select, then rank)
        candidates_per_chunk = max(2, (num_segments * 2) // len(chunks) + 1)

        import asyncio
        import concurrent.futures

        def _call_chunk(chunk_idx: int, chunk_frames: list) -> list[dict]:
            """Send one chunk to VLM and return candidate highlights."""
            chunk_start_ts = chunk_frames[0][0] if chunk_frames else 0
            chunk_end_ts = chunk_frames[-1][0] if chunk_frames else total_duration

            content: list[dict] = []
            content.append({
                "type": "text",
                "text": (
                    f"This is a section of a {total_duration:.0f}s video, "
                    f"covering timestamps {chunk_start_ts:.0f}s to {chunk_end_ts:.0f}s. "
                    f"Select up to {candidates_per_chunk} highlight segments of "
                    f"~{segment_duration:.0f}s each from THIS section."
                ),
            })
            for timestamp, b64_data in chunk_frames:
                content.append({"type": "text", "text": f"[Frame at {timestamp:.0f}s]"})
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
                })
            content.append({
                "type": "text",
                "text": (
                    f"Select up to {candidates_per_chunk} non-overlapping segments "
                    f"within {chunk_start_ts:.0f}-{chunk_end_ts:.0f}s. "
                    f"Return ONLY a JSON array."
                ),
            })

            system_prompt = VLM_HIGHLIGHT_SYSTEM_PROMPT
            if user_prompt and user_prompt.strip():
                system_prompt += (
                    f"\n\n=== DIRECTOR INSTRUCTIONS (HIGHEST PRIORITY) ===\n"
                    f"{user_prompt.strip()}\n"
                    f"=== END DIRECTOR INSTRUCTIONS ==="
                )

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                "max_tokens": 2048,
                "temperature": 0.3,
                "stream": False,
            "enable_thinking": False,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }

            raw = self._call_vlm_api(api_url, payload, headers)
            if raw is None:
                return []
            parsed = self._parse_timeline_json(raw)
            if not isinstance(parsed, list):
                return []
            return [h for h in parsed if isinstance(h, dict) and "start" in h and "end" in h]

        # Run chunks concurrently using ThreadPoolExecutor
        all_candidates = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(chunks), 4)) as pool:
            futures = {
                pool.submit(_call_chunk, idx, chunk): idx
                for idx, chunk in enumerate(chunks)
            }
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                try:
                    results = future.result()
                    logger.info("chunk %d returned %d candidates", idx, len(results))
                    all_candidates.extend(results)
                except Exception as e:
                    logger.warning("chunk %d failed: %s", idx, str(e)[:200])

        if not all_candidates:
            logger.warning("all chunks returned no candidates")
            return None

        # Sort by start, deduplicate overlaps, pick top N
        all_candidates.sort(key=lambda h: h.get("start", 0))

        # Remove overlapping candidates
        deduped = [all_candidates[0]]
        for h in all_candidates[1:]:
            if h.get("start", 0) >= deduped[-1].get("end", 0) - 1.0:
                deduped.append(h)

        # Take top num_segments (already sorted by time)
        if len(deduped) > num_segments:
            # Evenly sample from deduped to maintain spread
            step = len(deduped) / num_segments
            selected = [deduped[int(i * step)] for i in range(num_segments)]
        else:
            selected = deduped

        if not self._validate_highlights(selected, total_duration):
            return None

        selected.sort(key=lambda h: h["start"])
        logger.info("highlight selection: merged %d candidates → %d final", len(all_candidates), len(selected))
        return selected

    def _select_highlights_single(
        self,
        frames: list[tuple[float, str]],
        total_duration: float,
        num_segments: int,
        segment_duration: float,
        api_url: str,
        api_key: str,
        model: str,
        user_prompt: str = "",
    ) -> list[dict] | None:
        """Single-request highlight selection (for small frame counts)."""
        content: list[dict] = []
        content.append({
            "type": "text",
            "text": (
                f"This is a {total_duration:.0f}-second video. "
                f"Select the {num_segments} best segments of ~{segment_duration:.0f}s each."
            ),
        })
        for timestamp, b64_data in frames:
            content.append({"type": "text", "text": f"[Frame at {timestamp:.0f}s]"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
            })
        content.append({
            "type": "text",
            "text": f"Select exactly {num_segments} non-overlapping segments. Return ONLY a JSON array.",
        })

        system_prompt = VLM_HIGHLIGHT_SYSTEM_PROMPT
        if user_prompt and user_prompt.strip():
            system_prompt += (
                f"\n\n=== DIRECTOR INSTRUCTIONS (HIGHEST PRIORITY) ===\n"
                f"{user_prompt.strip()}\n"
                f"=== END DIRECTOR INSTRUCTIONS ==="
            )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "max_tokens": 2048,
            "temperature": 0.3,
            "stream": False,
            "enable_thinking": False,
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

        raw = self._call_vlm_api(api_url, payload, headers)
        if raw is None:
            return None
        highlights = self._parse_timeline_json(raw)
        if highlights is None:
            return None
        if not self._validate_highlights(highlights, total_duration):
            return None
        highlights.sort(key=lambda h: h["start"])
        return highlights

    def _validate_highlights(self, highlights: list[dict], total_duration: float) -> bool:
        """Validate highlight selection response."""
        if not isinstance(highlights, list) or len(highlights) == 0:
            logger.error("Highlight validation failed: empty or not a list")
            return False

        prev_end = -1.0
        for i, h in enumerate(highlights):
            if not isinstance(h, dict):
                logger.error("Highlight entry %d is not a dict", i)
                return False
            for field in ("start", "end", "reason"):
                if field not in h:
                    logger.error("Highlight entry %d missing field '%s'", i, field)
                    return False
            start = h["start"]
            end = h["end"]
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                logger.error("Highlight entry %d non-numeric start/end", i)
                return False
            if start < 0 or end <= start:
                logger.error("Highlight entry %d invalid range %s-%s", i, start, end)
                return False
            if end > total_duration + 1.0:  # small tolerance
                logger.warning("Highlight entry %d end=%s exceeds duration=%s, clamping", i, end, total_duration)
                h["end"] = total_duration

        # Sort and check overlaps
        sorted_h = sorted(highlights, key=lambda x: x["start"])
        for i in range(1, len(sorted_h)):
            if sorted_h[i]["start"] < sorted_h[i - 1]["end"] - 0.5:
                logger.error(
                    "Highlight overlap: entry %d start=%s < prev end=%s",
                    i, sorted_h[i]["start"], sorted_h[i - 1]["end"],
                )
                return False

        return True

    def validate_timeline(
        self,
        timeline: list[dict],
        a_roll_duration: float,
    ) -> bool:
        """Validate timeline JSON structure and constraints.

        Checks:
        - Non-empty array
        - Each entry has type, start, end, reason with correct types
        - type is "a_roll" or "b_roll"
        - start >= 0, end > start
        - Entries sorted by start, no overlaps

        Returns:
            True if valid, False otherwise (logs specific errors).
        """
        if not isinstance(timeline, list) or len(timeline) == 0:
            logger.error("Timeline validation failed: empty or not a list")
            return False

        prev_end = -1.0

        for i, entry in enumerate(timeline):
            # Check required fields exist
            if not isinstance(entry, dict):
                logger.error("Timeline entry %d is not a dict", i)
                return False

            for field in ("type", "start", "end", "reason"):
                if field not in entry:
                    logger.error("Timeline entry %d missing field '%s'", i, field)
                    return False

            # Check type
            entry_type = entry["type"]
            if not isinstance(entry_type, str) or entry_type not in ("a_roll", "b_roll"):
                logger.error(
                    "Timeline entry %d has invalid type: %r (expected 'a_roll' or 'b_roll')",
                    i, entry_type,
                )
                return False

            # Check start/end are numbers
            start = entry["start"]
            end = entry["end"]
            if not isinstance(start, (int, float)):
                logger.error("Timeline entry %d 'start' is not a number: %r", i, start)
                return False
            if not isinstance(end, (int, float)):
                logger.error("Timeline entry %d 'end' is not a number: %r", i, end)
                return False

            # Check start >= 0
            if start < 0:
                logger.error("Timeline entry %d has negative start: %s", i, start)
                return False

            # Check end > start
            if end <= start:
                logger.error(
                    "Timeline entry %d has end (%s) <= start (%s)", i, end, start
                )
                return False

            # Check reason is a string
            reason = entry["reason"]
            if not isinstance(reason, str):
                logger.error("Timeline entry %d 'reason' is not a string: %r", i, reason)
                return False

            # Check sorted by start (start >= previous entry's start)
            if i > 0 and start < timeline[i - 1]["start"]:
                logger.error(
                    "Timeline entries not sorted by start: entry %d start=%s < entry %d start=%s",
                    i, start, i - 1, timeline[i - 1]["start"],
                )
                return False

            # Check no overlaps (current start >= previous end)
            if prev_end > start:
                logger.error(
                    "Timeline overlap: entry %d start=%s < previous end=%s",
                    i, start, prev_end,
                )
                return False

            prev_end = end

        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_ffmpeg_binary() -> str:
        """Get FFmpeg binary path from environment or default."""
        return os.environ.get("IMAGEIO_FFMPEG_EXE") or "ffmpeg"

    def _get_video_duration(self, video_path: str) -> float:
        """Get video duration in seconds using ffprobe."""
        ffprobe_bin = self._get_ffmpeg_binary().replace("ffmpeg", "ffprobe")
        cmd = [
            ffprobe_bin,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except Exception as e:
            logger.warning("ffprobe failed for %s: %s", video_path, str(e)[:200])
        return 0.0

    def _build_multimodal_content(
        self,
        frames: list[tuple[float, str]],
        transcript: str,
        b_roll_descriptions: list[dict],
        a_roll_duration: float,
    ) -> list[dict]:
        """Build the multimodal user message content array.

        Returns a list of content parts (text + image_url) for the
        OpenAI-compatible multimodal API format.
        """
        content: list[dict] = []

        # Intro text with frame context
        content.append({
            "type": "text",
            "text": (
                f"Here are frames extracted from the A-roll video "
                f"(total duration: {a_roll_duration:.1f}s). "
                f"Each frame is labeled with its timestamp."
            ),
        })

        # Add each frame as an image_url content part
        for timestamp, b64_data in frames:
            content.append({
                "type": "text",
                "text": f"[Frame at {timestamp:.1f}s]",
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
            })

        # Transcript
        transcript_text = transcript.strip() if transcript else "(No transcript available)"
        content.append({
            "type": "text",
            "text": f"Transcript:\n{transcript_text}",
        })

        # B-roll descriptions
        if b_roll_descriptions:
            b_roll_lines = []
            for idx, desc in enumerate(b_roll_descriptions, 1):
                filename = desc.get("filename", f"b_roll_{idx}")
                duration = desc.get("duration", 0)
                b_roll_lines.append(f"  {idx}. {filename} ({duration:.1f}s)")
            b_roll_text = "\n".join(b_roll_lines)
        else:
            b_roll_text = "(No B-roll descriptions provided)"

        content.append({
            "type": "text",
            "text": f"Available B-roll footage:\n{b_roll_text}",
        })

        # Final instruction
        content.append({
            "type": "text",
            "text": (
                f"Generate a JSON timeline array covering the full {a_roll_duration:.1f}s "
                f"duration. Return ONLY the JSON array, no other text."
            ),
        })

        return content

    def _call_vlm_api(
        self,
        api_url: str,
        payload: dict,
        headers: dict,
    ) -> str | None:
        """Call VLM API with one retry on timeout.

        Returns the response text content, or None on failure.
        """
        max_attempts = 2  # initial + 1 retry

        for attempt in range(max_attempts):
            try:
                logger.info(
                    "VLM API call attempt %d/%d to %s",
                    attempt + 1, max_attempts, api_url,
                )
                with httpx.Client(timeout=180.0) as client:
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
                    logger.warning("VLM API returned empty content")
                    return None
                else:
                    logger.error(
                        "VLM API HTTP %d: %s",
                        response.status_code,
                        response.text[:300],
                    )
                    # Don't retry on 4xx client errors
                    if 400 <= response.status_code < 500:
                        return None

            except httpx.TimeoutException:
                logger.warning(
                    "VLM API timeout (attempt %d/%d)", attempt + 1, max_attempts
                )
            except Exception as e:
                logger.error("VLM API error: %s", str(e)[:300])
                return None

        logger.error("VLM API failed after %d attempts", max_attempts)
        return None

    @staticmethod
    def _parse_timeline_json(raw_text: str) -> list[dict] | None:
        """Parse timeline JSON from VLM response text.

        Handles cases where the VLM wraps JSON in markdown code fences
        or includes thinking tags (qwen3 series).
        """
        import re as _re

        text = raw_text.strip()

        # Strip qwen3 thinking tags: <think>...</think>
        text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json or ```) and last line (```)
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse VLM response as JSON: %s", str(e))
            logger.debug("Raw VLM response: %s", raw_text[:500])
            return None

        if not isinstance(parsed, list):
            logger.error("VLM response is not a JSON array: %s", type(parsed).__name__)
            return None

        return parsed
