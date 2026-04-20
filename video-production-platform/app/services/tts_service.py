"""TTS (Text-to-Speech) service using edge-tts for async speech synthesis."""

import asyncio
import json
import logging
import os
import subprocess
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Task, utcnow
from app.services.config_service import ConfigService

logger = logging.getLogger("app.tts")

# Default voices if not configured
DEFAULT_VOICES = [
    {"name": "zh-CN-XiaoxiaoNeural", "display_name": "晓晓 (女声)", "gender": "female", "language": "zh-CN"},
    {"name": "zh-CN-YunxiNeural", "display_name": "云希 (男声)", "gender": "male", "language": "zh-CN"},
    {"name": "zh-CN-YunjianNeural", "display_name": "云健 (男声)", "gender": "male", "language": "zh-CN"},
    {"name": "zh-CN-XiaoyiNeural", "display_name": "晓伊 (女声)", "gender": "female", "language": "zh-CN"},
]


class TTSService:
    """Service for text-to-speech synthesis using edge-tts."""

    def __init__(self, db: Session):
        self.db = db
        self.config = ConfigService.get_instance()

    def _get_voices(self) -> list[dict]:
        """Get available voices from ConfigService or defaults."""
        voices_json = self.config.get_config("tts_voices", self.db)
        if voices_json:
            try:
                return json.loads(voices_json)
            except (json.JSONDecodeError, TypeError):
                pass
        return DEFAULT_VOICES

    def _get_default_voice(self) -> str:
        """Get the default voice name."""
        voices = self._get_voices()
        if voices:
            return voices[0]["name"]
        return "zh-CN-XiaoxiaoNeural"

    def _get_speed(self) -> str:
        """Get TTS speed from config (e.g., '+0%', '-10%', '+20%')."""
        return self.config.get_config("tts_speed", self.db, "+0%")

    def _get_volume(self) -> str:
        """Get TTS volume from config (e.g., '+0%', '+50%')."""
        return self.config.get_config("tts_volume", self.db, "+0%")

    def get_voices(self) -> list[dict]:
        """Return the list of available voice options."""
        return self._get_voices()

    async def _synthesize_with_edge_tts(self, text: str, voice: str, output_path: str) -> None:
        """Run edge-tts synthesis asynchronously.

        Args:
            text: Text to synthesize.
            voice: Voice name.
            output_path: Path to save the audio file.
        """
        import edge_tts

        speed = self._get_speed()
        volume = self._get_volume()

        communicate = edge_tts.Communicate(text, voice, rate=speed, volume=volume)
        await communicate.save(output_path)

    def _get_audio_duration(self, audio_path: str) -> float:
        """Get audio duration using ffprobe.

        Falls back to mutagen if ffprobe is not available.

        Args:
            audio_path: Path to the audio file.

        Returns:
            Duration in seconds.
        """
        # Try ffprobe first
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
                duration = float(data.get("format", {}).get("duration", 0))
                if duration > 0:
                    return duration
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, ValueError):
            pass

        # Fallback: try mutagen
        try:
            from mutagen.mp3 import MP3
            audio = MP3(audio_path)
            return audio.info.length
        except Exception:
            pass

        # Last resort: estimate from file size (rough approximation for mp3 at ~128kbps)
        try:
            file_size = os.path.getsize(audio_path)
            # Approximate: 128kbps = 16KB/s
            return file_size / 16000.0
        except OSError:
            return 0.0

    def synthesize(self, task_id: str, voice: Optional[str] = None) -> Task:
        """Synthesize TTS audio for a task.

        Args:
            task_id: The task ID.
            voice: Optional voice name override.

        Returns:
            Updated Task record.

        Raises:
            ValueError: If task not found or invalid state.
            RuntimeError: If synthesis fails.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if task.status != "copy_confirmed":
            raise ValueError(
                f"Task status must be 'copy_confirmed' to synthesize TTS, current: '{task.status}'"
            )

        # Determine text to synthesize
        text = task.copywriting_final or task.copywriting_filtered or task.copywriting_raw
        if not text:
            raise ValueError("No copywriting text available for synthesis")

        # Determine voice
        selected_voice = voice or self._get_default_voice()

        # Ensure output directory exists
        output_dir = f"storage/tasks/{task_id}"
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "tts_audio.mp3")

        # Run async synthesis
        try:
            asyncio.run(self._synthesize_with_edge_tts(text, selected_voice, output_path))
        except Exception as e:
            logger.error("TTS synthesis failed for task %s: %s", task_id, str(e))
            raise RuntimeError(f"TTS synthesis failed: {str(e)}")

        # Verify file was created
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError("TTS synthesis produced no output file")

        # Get audio duration
        duration = self._get_audio_duration(output_path)

        # Update task record
        task.tts_audio_path = output_path
        task.tts_duration = duration
        task.tts_voice = selected_voice
        task.status = "tts_done"
        task.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(task)

        logger.info(
            "TTS synthesis complete for task %s: voice=%s, duration=%.2fs",
            task_id, selected_voice, duration,
        )

        return task

    async def preview(self, text: str, voice: Optional[str] = None) -> dict:
        """Generate a short preview audio clip.

        Args:
            text: Short text to preview (max 200 chars).
            voice: Optional voice name.

        Returns:
            Dict with audio_path, duration, voice.
        """
        selected_voice = voice or self._get_default_voice()

        # Generate preview in a temp location
        preview_id = str(uuid.uuid4())
        preview_dir = "storage/previews"
        os.makedirs(preview_dir, exist_ok=True)
        output_path = os.path.join(preview_dir, f"{preview_id}.mp3")

        try:
            await self._synthesize_with_edge_tts(text, selected_voice, output_path)
        except Exception as e:
            logger.error("TTS preview failed: %s", str(e))
            raise RuntimeError(f"TTS preview failed: {str(e)}")

        duration = self._get_audio_duration(output_path)

        return {
            "audio_path": output_path,
            "duration": duration,
            "voice": selected_voice,
        }
