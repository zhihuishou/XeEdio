"""AI Director service — orchestrates VLM analysis and timeline-based editing.

Coordinates the full AI-directed editing pipeline:
1. Extract frames from A-roll
2. Send to VLM for timeline generation
3. Execute timeline (or fallback to blind-cut)
"""

import json
import logging
import os
import subprocess
from typing import Callable, Optional

from app.services.mixing_engine import combine_videos, execute_timeline
from app.services.vlm_service import VLMService

logger = logging.getLogger("app.ai_director_service")


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    ffprobe_bin = os.environ.get("IMAGEIO_FFMPEG_EXE", "ffmpeg").replace(
        "ffmpeg", "ffprobe"
    )
    if ffprobe_bin == "ffprobe" or not ffprobe_bin:
        ffprobe_bin = "ffprobe"

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


class AIDirectorService:
    """Orchestrates VLM analysis and timeline-based editing."""

    def __init__(self, task_id: str, output_dir: str):
        self.task_id = task_id
        self.output_dir = output_dir
        self.vlm_service = VLMService()

    def run_pipeline(
        self,
        a_roll_paths: list[str],
        b_roll_paths: list[str],
        transcript: str,
        aspect_ratio: str = "9:16",
        transition: str = "none",
        audio_file: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, bool]:
        """Run the full AI Director pipeline.

        Steps:
        1. Extract frames from A-roll
        2. Send to VLM for timeline generation
        3. Execute timeline (or fallback to blind-cut)

        Args:
            a_roll_paths: A-roll video file paths.
            b_roll_paths: B-roll video file paths.
            transcript: Audio transcript or script text.
            aspect_ratio: Target aspect ratio.
            transition: Transition effect for B-roll.
            audio_file: Audio track to apply to final output.
            progress_callback: Callable(stage: str) for status updates.

        Returns:
            Tuple of (output_video_path, ai_director_used).
        """
        output_path = os.path.join(self.output_dir, "output-1.mp4")

        # Step 1: Extract frames from first A-roll
        if progress_callback:
            progress_callback("extracting_frames")

        vlm_config = self.vlm_service.config.get_vlm_config()
        try:
            frames = self.vlm_service.extract_frames(
                a_roll_paths[0],
                frame_interval=vlm_config.get("frame_interval", 2),
                max_frames=vlm_config.get("max_frames", 30),
            )
        except Exception as e:
            logger.warning("Frame extraction failed: %s", str(e)[:200])
            frames = []

        # Step 2: Generate timeline via VLM
        if progress_callback:
            progress_callback("analyzing_with_vlm")

        timeline = None
        if frames:
            # Get A-roll total duration
            a_roll_duration = sum(_get_video_duration(p) for p in a_roll_paths)

            # Build B-roll descriptions
            b_roll_descs = [
                {
                    "filename": os.path.basename(p),
                    "duration": _get_video_duration(p),
                }
                for p in b_roll_paths
            ]

            try:
                timeline = self.vlm_service.generate_timeline(
                    frames, transcript, b_roll_descs, a_roll_duration
                )
            except Exception as e:
                logger.warning("VLM timeline generation failed: %s", str(e)[:200])
                timeline = None

        # Step 3: Execute
        if timeline:
            if progress_callback:
                progress_callback("executing_timeline")

            execute_timeline(
                timeline,
                a_roll_paths,
                b_roll_paths,
                audio_file,
                output_path,
                aspect_ratio,
                transition,
            )
            return output_path, True  # (path, ai_director_used)
        else:
            if progress_callback:
                progress_callback("falling_back_to_blind_cut")

            logger.warning(
                "VLM timeline unavailable for task %s, falling back to blind-cut",
                self.task_id,
            )
            combine_videos(
                output_path,
                [],
                audio_file,
                aspect_ratio,
                "random",
                transition,
                a_roll_paths=a_roll_paths,
                b_roll_paths=b_roll_paths,
            )
            return output_path, False

    def _transcribe_audio(self, audio_path: str) -> str:
        """Transcribe audio using Whisper ASR.

        Falls back to empty string if Whisper is unavailable.

        Args:
            audio_path: Path to audio file.

        Returns:
            Transcript text, or empty string on failure.
        """
        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                "base", device="cpu", compute_type="int8", download_root=None
            )
            segments, info = model.transcribe(
                audio_path, language="zh", vad_filter=True
            )
            return " ".join(
                seg.text.strip() for seg in segments if seg.text.strip()
            )
        except Exception as e:
            logger.warning("Whisper transcription failed: %s", str(e)[:200])
            return ""
