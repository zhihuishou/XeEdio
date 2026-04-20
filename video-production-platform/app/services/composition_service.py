"""Video composition service using FFmpeg for A-roll/B-roll mixing."""

import json
import logging
import os
import subprocess
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Asset, Task, TaskAsset, generate_uuid, utcnow
from app.services.config_service import ConfigService

logger = logging.getLogger("app.ffmpeg")


class CompositionService:
    """Service for video composition using FFmpeg."""

    def __init__(self, db: Session):
        self.db = db
        self.config = ConfigService.get_instance()

    def _get_default_resolution(self) -> str:
        """Get default video resolution from config."""
        return self.config.get_config("video_resolution", self.db, "1080x1920")

    def _get_default_bitrate(self) -> str:
        """Get default video bitrate from config."""
        return self.config.get_config("video_bitrate", self.db, "8M")

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio file duration using ffprobe.

        Args:
            audio_path: Path to audio file.

        Returns:
            Duration in seconds.
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-print_format", "json",
                    "-show_format", audio_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, ValueError) as e:
            logger.warning("ffprobe failed for %s: %s", audio_path, str(e))

        return 0.0

    def _get_video_duration(self, video_path: str) -> float:
        """Get video file duration using ffprobe."""
        return self._get_audio_duration(video_path)  # Same ffprobe logic works

    def _get_asset_paths(self, asset_ids: list[str]) -> list[str]:
        """Resolve asset IDs to file paths.

        Args:
            asset_ids: List of asset IDs.

        Returns:
            List of file paths for existing assets.
        """
        paths = []
        for asset_id in asset_ids:
            asset = self.db.query(Asset).filter(Asset.id == asset_id).first()
            if asset and asset.file_path and os.path.exists(asset.file_path):
                paths.append(asset.file_path)
        return paths

    def build_ffmpeg_command(
        self,
        a_roll_paths: list[str],
        b_roll_paths: list[str],
        audio_path: str,
        output_path: str,
        resolution: str,
        bitrate: str,
        transition: str = "fade",
        audio_duration: float = 0.0,
    ) -> list[str]:
        """Build FFmpeg command for video composition.

        Strategy:
        - Concatenate all A-roll and B-roll clips
        - Scale to target resolution
        - Apply fade transition between clips
        - Mix TTS audio
        - Trim to match audio duration

        Args:
            a_roll_paths: Paths to A-roll video files.
            b_roll_paths: Paths to B-roll video files.
            audio_path: Path to TTS audio file.
            output_path: Output video file path.
            resolution: Target resolution (e.g., '1080x1920').
            bitrate: Target bitrate (e.g., '8M').
            transition: Transition type (currently supports 'fade').
            audio_duration: Duration of the audio in seconds.

        Returns:
            FFmpeg command as list of strings.
        """
        # Parse resolution
        parts = resolution.split("x")
        width = int(parts[0]) if len(parts) == 2 else 1080
        height = int(parts[1]) if len(parts) == 2 else 1920

        # Combine all video inputs (A-roll first, then B-roll)
        all_video_paths = a_roll_paths + b_roll_paths
        if not all_video_paths:
            raise ValueError("No video inputs provided")

        # Build complex filter for concatenation with transitions
        cmd = ["ffmpeg", "-y"]

        # Add all video inputs
        for path in all_video_paths:
            cmd.extend(["-i", path])

        # Add audio input
        cmd.extend(["-i", audio_path])

        audio_input_idx = len(all_video_paths)
        num_videos = len(all_video_paths)

        # Build filter complex
        filter_parts = []

        # Scale each input to target resolution and set duration
        segment_duration = audio_duration / num_videos if num_videos > 0 and audio_duration > 0 else 5.0

        for i in range(num_videos):
            filter_parts.append(
                f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1,trim=0:{segment_duration},setpts=PTS-STARTPTS[v{i}]"
            )

        # Apply fade transitions between segments
        if num_videos == 1:
            filter_parts.append(f"[v0]copy[outv]")
        else:
            # Concatenate with xfade transitions
            fade_duration = min(0.5, segment_duration / 4)  # Fade duration

            if transition == "fade" and num_videos == 2:
                offset = segment_duration - fade_duration
                filter_parts.append(
                    f"[v0][v1]xfade=transition=fade:duration={fade_duration}:offset={offset}[outv]"
                )
            elif transition == "fade" and num_videos > 2:
                # Chain xfade for multiple inputs
                offset = segment_duration - fade_duration
                filter_parts.append(
                    f"[v0][v1]xfade=transition=fade:duration={fade_duration}:offset={offset}[xf0]"
                )
                for i in range(2, num_videos):
                    prev_label = f"xf{i - 2}"
                    accumulated_offset = offset + (i - 1) * (segment_duration - fade_duration)
                    if i == num_videos - 1:
                        filter_parts.append(
                            f"[{prev_label}][v{i}]xfade=transition=fade:duration={fade_duration}:offset={accumulated_offset}[outv]"
                        )
                    else:
                        filter_parts.append(
                            f"[{prev_label}][v{i}]xfade=transition=fade:duration={fade_duration}:offset={accumulated_offset}[xf{i - 1}]"
                        )
            else:
                # Simple concatenation without transitions
                concat_inputs = "".join(f"[v{i}]" for i in range(num_videos))
                filter_parts.append(f"{concat_inputs}concat=n={num_videos}:v=1:a=0[outv]")

        filter_complex = ";".join(filter_parts)

        cmd.extend(["-filter_complex", filter_complex])

        # Map outputs
        cmd.extend([
            "-map", "[outv]",
            "-map", f"{audio_input_idx}:a",
            "-c:v", "libx264",
            "-b:v", bitrate,
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            "-movflags", "+faststart",
            output_path,
        ])

        return cmd

    def compose(
        self,
        task_id: str,
        a_roll_asset_ids: list[str],
        b_roll_asset_ids: list[str],
        transition: str = "fade",
        resolution: Optional[str] = None,
        bitrate: Optional[str] = None,
    ) -> Task:
        """Execute video composition for a task.

        Args:
            task_id: The task ID.
            a_roll_asset_ids: List of A-roll asset IDs.
            b_roll_asset_ids: List of B-roll asset IDs.
            transition: Transition effect type.
            resolution: Output resolution (uses config default if None).
            bitrate: Output bitrate (uses config default if None).

        Returns:
            Updated Task record.

        Raises:
            ValueError: If task not found or invalid state.
            RuntimeError: If FFmpeg execution fails.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if task.status != "tts_done":
            raise ValueError(
                f"Task status must be 'tts_done' to compose video, current: '{task.status}'"
            )

        # Verify TTS audio exists
        audio_path = task.tts_audio_path
        if not audio_path or not os.path.exists(audio_path):
            raise ValueError("TTS audio file not found. Please synthesize TTS first.")

        # Get audio duration
        audio_duration = task.tts_duration or self._get_audio_duration(audio_path)
        if audio_duration <= 0:
            audio_duration = self._get_audio_duration(audio_path)

        # Resolve asset paths
        a_roll_paths = self._get_asset_paths(a_roll_asset_ids)
        b_roll_paths = self._get_asset_paths(b_roll_asset_ids)

        if not a_roll_paths:
            raise ValueError("No valid A-roll assets found")

        # Use config defaults if not specified
        target_resolution = resolution or self._get_default_resolution()
        target_bitrate = bitrate or self._get_default_bitrate()

        # Prepare output path
        output_dir = f"storage/tasks/{task_id}"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "output.mp4")

        # Build FFmpeg command
        try:
            cmd = self.build_ffmpeg_command(
                a_roll_paths=a_roll_paths,
                b_roll_paths=b_roll_paths,
                audio_path=audio_path,
                output_path=output_path,
                resolution=target_resolution,
                bitrate=target_bitrate,
                transition=transition,
                audio_duration=audio_duration,
            )
        except ValueError as e:
            raise ValueError(str(e))

        # Execute FFmpeg
        logger.info("FFmpeg command for task %s: %s", task_id, " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )
        except subprocess.TimeoutExpired:
            logger.error("FFmpeg timed out for task %s", task_id)
            self._log_ffmpeg_output(task_id, "TIMEOUT", "FFmpeg execution timed out after 300s")
            raise RuntimeError("FFmpeg execution timed out")
        except FileNotFoundError:
            logger.error("FFmpeg not found on system")
            raise RuntimeError("FFmpeg is not installed or not in PATH")

        # Log FFmpeg output
        self._log_ffmpeg_output(task_id, result.stdout, result.stderr)

        if result.returncode != 0:
            error_msg = result.stderr[-500:] if result.stderr else "Unknown FFmpeg error"
            logger.error("FFmpeg failed for task %s: %s", task_id, error_msg)
            raise RuntimeError(f"FFmpeg composition failed: {error_msg}")

        # Verify output file
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("FFmpeg produced no output file")

        # Get video metadata
        video_duration = self._get_video_duration(output_path)
        video_file_size = os.path.getsize(output_path)

        # Update task record
        task.video_path = output_path
        task.video_resolution = target_resolution
        task.video_duration = video_duration
        task.video_file_size = video_file_size
        task.status = "video_done"
        task.updated_at = utcnow()

        # Save asset associations
        for i, asset_id in enumerate(a_roll_asset_ids):
            ta = TaskAsset(
                id=generate_uuid(),
                task_id=task_id,
                asset_id=asset_id,
                roll_type="a_roll",
                sequence_order=i,
            )
            self.db.add(ta)

        for i, asset_id in enumerate(b_roll_asset_ids):
            ta = TaskAsset(
                id=generate_uuid(),
                task_id=task_id,
                asset_id=asset_id,
                roll_type="b_roll",
                sequence_order=i,
            )
            self.db.add(ta)

        self.db.commit()

        # Auto-transition to pending_review
        task.status = "pending_review"
        task.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(task)

        logger.info(
            "Video composition complete for task %s: resolution=%s, duration=%.2fs, size=%d bytes",
            task_id, target_resolution, video_duration, video_file_size,
        )

        return task

    def _log_ffmpeg_output(self, task_id: str, stdout: str, stderr: str) -> None:
        """Log FFmpeg output to the ffmpeg log file.

        Args:
            task_id: Task ID for context.
            stdout: FFmpeg stdout.
            stderr: FFmpeg stderr.
        """
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "ffmpeg.log")

        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Task: {task_id}\n")
                f.write(f"Timestamp: {utcnow().isoformat()}\n")
                if stdout:
                    f.write(f"STDOUT:\n{stdout[-1000:]}\n")
                if stderr:
                    f.write(f"STDERR:\n{stderr[-2000:]}\n")
                f.write(f"{'='*60}\n")
        except OSError:
            logger.warning("Failed to write FFmpeg log for task %s", task_id)

    def get_status(self, task_id: str) -> dict:
        """Get composition status for a task.

        Args:
            task_id: The task ID.

        Returns:
            Dict with task status and video metadata.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise ValueError(f"Task {task_id} not found")

        return {
            "task_id": task.id,
            "status": task.status,
            "video_path": task.video_path,
            "video_resolution": task.video_resolution,
            "video_duration": task.video_duration,
            "video_file_size": task.video_file_size,
        }
