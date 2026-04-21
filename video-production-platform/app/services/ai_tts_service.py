"""AI TTS service with graceful degradation to Edge-TTS.

Tries an external AI TTS API first (placeholder — not yet implemented).
Falls back to Edge-TTS when the external API is unavailable or fails.
"""

import asyncio
import json
import logging
import os
import subprocess
from typing import Optional

from app.services.external_config import ExternalConfig

logger = logging.getLogger("app.ai_tts_service")


class AITTSService:
    """AI TTS synthesis with graceful degradation to Edge-TTS."""

    def __init__(self):
        self.config = ExternalConfig.get_instance()

    def synthesize(
        self,
        text: str,
        task_id: str,
        voice: Optional[str] = None,
    ) -> tuple[str, float]:
        """Synthesize voiceover audio from text.

        Tries AI TTS API first, falls back to Edge-TTS if configured.

        Args:
            text: Script text to synthesize.
            task_id: Task ID for output path.
            voice: Optional voice name override.

        Returns:
            Tuple of (audio_file_path, duration_seconds).

        Raises:
            RuntimeError: If both AI TTS and fallback fail.
        """
        output_dir = os.path.join("storage", "tasks", task_id)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "tts_audio.mp3")

        ai_tts_config = self.config.get_ai_tts_config()
        api_url = ai_tts_config.get("api_url", "")
        api_key = ai_tts_config.get("api_key", "")
        fallback_to_edge_tts = ai_tts_config.get("fallback_to_edge_tts", True)

        # Step 1: Try external AI TTS API if configured
        external_success = False
        if api_url and api_key:
            try:
                self._call_external_api(text, output_path, api_url, api_key, voice)
                external_success = True
                logger.info("AI TTS API synthesis succeeded for task %s", task_id)
            except Exception as e:
                logger.warning(
                    "AI TTS API failed (%s), %s",
                    str(e)[:200],
                    "falling back to Edge-TTS" if fallback_to_edge_tts else "no fallback configured",
                )
        else:
            logger.info("AI TTS API not configured, using Edge-TTS directly")

        # Step 2: Fall back to Edge-TTS if external failed or not configured
        if not external_success:
            if not fallback_to_edge_tts:
                raise RuntimeError(
                    "AI TTS synthesis failed and fallback_to_edge_tts is disabled"
                )
            try:
                self._synthesize_edge_tts(text, output_path, voice)
                logger.info("Edge-TTS synthesis succeeded for task %s", task_id)
            except Exception as e:
                raise RuntimeError(
                    f"Both AI TTS API and Edge-TTS fallback failed: {str(e)[:200]}"
                ) from e

        # Step 3: Get duration via ffprobe
        duration = self._get_audio_duration(output_path)
        logger.info(
            "TTS audio saved: %s (%.2fs)", output_path, duration
        )

        return output_path, duration

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _call_external_api(
        text: str,
        output_path: str,
        api_url: str,
        api_key: str,
        voice: Optional[str] = None,
    ) -> None:
        """Call external AI TTS API (placeholder).

        This is a placeholder that will be replaced with the actual API
        integration when the user provides the API specification.
        """
        logger.info("AI TTS API not yet implemented — will fall back to Edge-TTS")
        raise NotImplementedError("AI TTS API not yet implemented")

    @staticmethod
    def _synthesize_edge_tts(
        text: str,
        output_path: str,
        voice: Optional[str] = None,
    ) -> None:
        """Synthesize audio using Edge-TTS (free Microsoft TTS).

        Uses the same async pattern as mixing_service.py's _synthesize_tts.
        """
        import edge_tts

        selected_voice = voice or "zh-CN-XiaoxiaoNeural"
        communicate = edge_tts.Communicate(text, selected_voice)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(communicate.save(output_path))
        finally:
            loop.close()

        logger.info("Edge-TTS synthesized: voice=%s, output=%s", selected_voice, output_path)

    @staticmethod
    def _get_audio_duration(audio_path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
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
            audio_path,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except Exception as e:
            logger.warning("ffprobe failed for %s: %s", audio_path, str(e)[:200])
        return 0.0
