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

from app.services.mixing_engine import combine_videos, execute_timeline, execute_montage_timeline
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
        director_prompt: str = "",
    ) -> tuple[str, bool]:
        """Run the full AI Director pipeline."""
        output_path = os.path.join(self.output_dir, "output-1.mp4")
        log = self._task_log

        log("=" * 60)
        log(f"AI Director Pipeline Start — task={self.task_id}")
        log(f"A-roll: {len(a_roll_paths)} files: {a_roll_paths}")
        if director_prompt:
            log(f"Director prompt: {director_prompt}")
        log(f"B-roll: {len(b_roll_paths)} files: {b_roll_paths}")
        log(f"Transcript length: {len(transcript)} chars")
        log(f"Aspect: {aspect_ratio}, Transition: {transition}")

        # Step 1: Extract frames
        if progress_callback:
            progress_callback("extracting_frames")

        vlm_config = self.vlm_service.config.get_vlm_config()
        try:
            frames = self.vlm_service.extract_frames(
                a_roll_paths[0],
                frame_interval=vlm_config.get("frame_interval", 2),
                max_frames=vlm_config.get("max_frames", 30),
            )
            log(f"Step 1: Extracted {len(frames)} frames from A-roll")
        except Exception as e:
            log(f"Step 1: Frame extraction FAILED: {str(e)[:300]}")
            frames = []

        # Step 2: Generate timeline via VLM
        if progress_callback:
            progress_callback("analyzing_with_vlm")

        timeline = None
        if frames:
            a_roll_duration = sum(_get_video_duration(p) for p in a_roll_paths)
            b_roll_descs = [
                {"filename": os.path.basename(p), "duration": _get_video_duration(p)}
                for p in b_roll_paths
            ]
            log(f"Step 2: Sending {len(frames)} frames + {len(b_roll_descs)} B-roll descs to VLM")
            log(f"  A-roll duration: {a_roll_duration:.1f}s")
            log(f"  B-roll descs: {b_roll_descs}")

            # If transcript is empty, provide a fallback hint so VLM still inserts B-roll
            effective_transcript = transcript
            if not effective_transcript or not effective_transcript.strip():
                effective_transcript = (
                    "(Transcript unavailable. Please analyze the visual content of the frames only. "
                    "Insert B-roll clips at natural visual pauses, scene transitions, or moments "
                    "where the speaker is not actively demonstrating a product. "
                    "Aim for 2-4 B-roll insertions evenly distributed across the video.)"
                )
                log("  ⚠️ Transcript empty, using visual-only fallback prompt")

            try:
                timeline = self.vlm_service.generate_timeline(
                    frames, effective_transcript, b_roll_descs, a_roll_duration,
                    user_prompt=director_prompt,
                )
                if timeline:
                    log(f"Step 2: VLM returned {len(timeline)} timeline entries:")
                    for i, entry in enumerate(timeline):
                        log(f"  [{i}] {entry.get('type')} {entry.get('start')}-{entry.get('end')}s: {entry.get('reason','')[:80]}")
                    # Check if any B-roll entries exist
                    b_count = sum(1 for e in timeline if e.get("type") == "b_roll")
                    log(f"  → A-roll entries: {len(timeline) - b_count}, B-roll entries: {b_count}")
                    if b_count == 0:
                        log("  ⚠️ WARNING: VLM returned NO B-roll entries! All A-roll.")
                else:
                    log("Step 2: VLM returned None (failed or invalid)")
            except Exception as e:
                log(f"Step 2: VLM FAILED: {str(e)[:300]}")
                timeline = None
        else:
            log("Step 2: SKIPPED (no frames extracted)")

        # Step 3: Execute
        if timeline:
            if progress_callback:
                progress_callback("executing_timeline")

            log(f"Step 3: Executing timeline with {len(timeline)} entries")
            execute_timeline(
                timeline,
                a_roll_paths,
                b_roll_paths,
                audio_file,
                output_path,
                aspect_ratio,
                transition,
            )
            log(f"Step 3: Timeline execution COMPLETE → {output_path}")
            log("=" * 60)
            return output_path, True
        else:
            if progress_callback:
                progress_callback("falling_back_to_blind_cut")

            log("Step 3: FALLBACK to blind-cut (no valid timeline)")
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
            log("Step 3: Blind-cut fallback COMPLETE")
            log("=" * 60)
            return output_path, False

    def run_montage_pipeline(
        self,
        clip_paths: list[str],
        aspect_ratio: str = "9:16",
        transition: str = "none",
        audio_file: Optional[str] = None,
        max_output_duration: int = 60,
        progress_callback: Optional[Callable[[str], None]] = None,
        director_prompt: str = "",
    ) -> tuple[str, bool]:
        """Run the montage pipeline — all clips are equal, no A-roll/B-roll.

        Args:
            clip_paths: List of all video clip file paths.
            aspect_ratio: Target aspect ratio.
            transition: Transition effect.
            audio_file: Optional audio file (TTS or extracted). None for no voiceover.
            max_output_duration: Target output duration in seconds.
            progress_callback: Optional callback for progress updates.
            director_prompt: Optional user directives for VLM.

        Returns:
            Tuple of (output_path, ai_director_used).
        """
        output_path = os.path.join(self.output_dir, "output-1.mp4")
        log = self._task_log

        log("=" * 60)
        log(f"AI Director MONTAGE Pipeline Start — task={self.task_id}")
        log(f"Clips: {len(clip_paths)} files: {clip_paths}")
        log(f"Target duration: {max_output_duration}s")
        log(f"Audio file: {audio_file or '(none)'}")
        if director_prompt:
            log(f"Director prompt: {director_prompt}")
        log(f"Aspect: {aspect_ratio}, Transition: {transition}")

        # Step 1: Extract frames from each clip
        if progress_callback:
            progress_callback("extracting_frames")

        vlm_config = self.vlm_service.config.get_vlm_config()
        clip_frames = []
        clip_descriptions = []
        total_clip_duration = 0.0

        for idx, path in enumerate(clip_paths):
            try:
                dur = _get_video_duration(path)
                total_clip_duration += dur
                # Extract fewer frames per clip to stay within token limits
                max_frames_per_clip = max(3, vlm_config.get("max_frames", 30) // len(clip_paths))
                frames = self.vlm_service.extract_frames(
                    path,
                    frame_interval=max(dur / max_frames_per_clip, vlm_config.get("frame_interval", 2)),
                    max_frames=max_frames_per_clip,
                )
                clip_frames.append(frames)
                clip_descriptions.append({
                    "filename": os.path.basename(path),
                    "duration": dur,
                    "index": idx,
                })
                log(f"Step 1: Clip {idx} — {len(frames)} frames, {dur:.1f}s")
            except Exception as e:
                log(f"Step 1: Clip {idx} frame extraction FAILED: {str(e)[:200]}")
                clip_frames.append([])
                clip_descriptions.append({
                    "filename": os.path.basename(path),
                    "duration": _get_video_duration(path),
                    "index": idx,
                })

        log(f"Step 1: Extracted frames from {len(clip_paths)} clips, total source duration: {total_clip_duration:.1f}s")

        # Step 2: Generate montage timeline via VLM
        if progress_callback:
            progress_callback("analyzing_with_vlm")

        target_duration = min(max_output_duration, total_clip_duration)
        timeline = None

        has_frames = any(len(f) > 0 for f in clip_frames)
        if has_frames:
            log(f"Step 2: Sending frames from {len(clip_paths)} clips to VLM for montage arrangement")
            try:
                timeline = self.vlm_service.generate_montage_timeline(
                    clip_frames, clip_descriptions, target_duration,
                    user_prompt=director_prompt,
                )
                if timeline:
                    log(f"Step 2: VLM returned {len(timeline)} montage entries:")
                    for i, entry in enumerate(timeline):
                        log(f"  [{i}] clip_{entry.get('clip_index')} "
                            f"src {entry.get('source_start')}-{entry.get('source_end')}s → "
                            f"out {entry.get('start')}-{entry.get('end')}s: "
                            f"{entry.get('reason','')[:60]}")
                else:
                    log("Step 2: VLM returned None (failed or invalid)")
            except Exception as e:
                log(f"Step 2: VLM FAILED: {str(e)[:300]}")
                timeline = None
        else:
            log("Step 2: SKIPPED (no frames extracted)")

        # Step 3: Execute
        if timeline:
            if progress_callback:
                progress_callback("executing_timeline")

            log(f"Step 3: Executing montage timeline with {len(timeline)} entries")
            execute_montage_timeline(
                timeline,
                clip_paths,
                audio_file,
                output_path,
                aspect_ratio,
                transition,
            )
            log(f"Step 3: Montage execution COMPLETE → {output_path}")
            log("=" * 60)
            return output_path, True
        else:
            if progress_callback:
                progress_callback("falling_back_to_blind_cut")

            log("Step 3: FALLBACK to blind-cut (no valid montage timeline)")
            combine_videos(
                combined_video_path=output_path,
                video_paths=clip_paths,
                audio_file=audio_file,
                video_aspect=aspect_ratio,
                video_concat_mode="random",
                video_transition=transition,
            )
            log("Step 3: Blind-cut fallback COMPLETE")
            log("=" * 60)
            return output_path, False

    def _task_log(self, message: str) -> None:
        """Write a log line to the task-specific log file."""
        from datetime import datetime, timezone
        log_path = os.path.join(self.output_dir, "ai_director.log")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception:
            pass
        logger.info("[task %s] %s", self.task_id, message)

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
