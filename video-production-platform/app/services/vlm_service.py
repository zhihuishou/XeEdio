"""VLM (Vision Language Model) service for AI Director.

Handles frame extraction from A-roll videos and VLM API communication
for intelligent timeline generation.
"""

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
        max_width: int = 512,
    ) -> list[tuple[float, str]]:
        """Extract frames from video using FFmpeg.

        Extracts one frame every *frame_interval* seconds, resizes to
        *max_width* (preserving aspect ratio), encodes as JPEG, and
        returns base64 strings paired with their timestamps.

        Args:
            video_path: Path to A-roll video file.
            frame_interval: Seconds between frame extractions.
            max_frames: Maximum number of frames to extract.
            max_width: Maximum width for resized frames.

        Returns:
            List of (timestamp_seconds, base64_jpeg_string) tuples.

        Raises:
            FileNotFoundError: If video file doesn't exist.
            RuntimeError: If FFmpeg is unavailable or extraction fails.
        """
        # 1. Validate video file exists
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        # 2. Check FFmpeg availability
        ffmpeg_bin = self._get_ffmpeg_binary()
        if not shutil.which(ffmpeg_bin):
            raise RuntimeError(
                "FFmpeg is required for frame extraction but was not found on the system"
            )

        # 3. Get video duration via ffprobe
        duration = self._get_video_duration(video_path)
        if duration <= 0:
            raise RuntimeError(
                f"Video has zero or negative duration ({duration}s): {video_path}"
            )

        # 4. Extract frames to a temp directory
        temp_dir = tempfile.mkdtemp(prefix="vlm_frames_")
        try:
            cmd = [
                ffmpeg_bin,
                "-i", video_path,
                "-vf", f"fps=1/{frame_interval},scale={max_width}:-1",
                "-q:v", "5",
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
    # Timeline generation
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
        model = vlm_config.get("model", "gpt-5.4")

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
        model = vlm_config.get("model", "gpt-5.4")

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

        Handles cases where the VLM wraps JSON in markdown code fences.
        """
        text = raw_text.strip()

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
