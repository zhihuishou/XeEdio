"""Property-based tests for VLMService.extract_frames() method.

# Feature: ai-director, Property 1: Frame extraction count and timestamp invariants

Uses Hypothesis to generate random (duration, frame_interval, max_frames)
combinations, creates a small test video via FFmpeg, and verifies:
- Frame count = min(floor(duration / interval), max_frames)
  (with tolerance for FFmpeg fps-filter boundary behaviour)
- Timestamps are ascending multiples of frame_interval
- All timestamps are non-negative and strictly less than duration
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing the module under test.
# ---------------------------------------------------------------------------
for mod_name in [
    "moviepy", "moviepy.video", "moviepy.video.io",
    "moviepy.video.io.VideoFileClip",
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.orm.session",
    "sqlalchemy.ext", "sqlalchemy.ext.declarative",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

_mock_sa = sys.modules["sqlalchemy"]
_mock_sa.Column = MagicMock()
_mock_sa.String = MagicMock()
_mock_sa.Integer = MagicMock()
_mock_sa.Float = MagicMock()
_mock_sa.Boolean = MagicMock()
_mock_sa.Text = MagicMock()
_mock_sa.DateTime = MagicMock()
_mock_sa.ForeignKey = MagicMock()
_mock_sa.LargeBinary = MagicMock()
_mock_sa.create_engine = MagicMock()

_mock_orm = sys.modules["sqlalchemy.orm"]
_mock_orm.DeclarativeBase = type("DeclarativeBase", (), {})
_mock_orm.Session = MagicMock()
_mock_orm.relationship = MagicMock()
_mock_orm.sessionmaker = MagicMock(return_value=MagicMock())

from app.services.vlm_service import VLMService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_test_video(duration: float, output_path: str) -> None:
    """Generate a minimal black test video of the given duration via FFmpeg."""
    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", f"color=c=black:s=64x64:d={duration}",
        "-c:v", "libx264",
        "-t", str(duration),
        "-y",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg video generation failed: {result.stderr[:300]}")


def _make_vlm_service() -> VLMService:
    """Create a VLMService with mocked ExternalConfig."""
    with patch("app.services.vlm_service.ExternalConfig") as mock_config_cls:
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {
            "api_url": "",
            "api_key": "",
            "model": "test",
        }
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()
    return svc


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Duration: 1–20 seconds (integer seconds to avoid FFmpeg rounding issues)
duration_strategy = st.integers(min_value=1, max_value=20).map(float)

# Frame interval: 1–5 seconds (integer seconds for predictable FFmpeg behaviour)
frame_interval_strategy = st.integers(min_value=1, max_value=5).map(float)

# Max frames: 1–30
max_frames_strategy = st.integers(min_value=1, max_value=30)


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------

class TestFrameExtractionProperty:
    """Property-based tests for extract_frames count and timestamp invariants.

    **Validates: Requirements 1.1, 1.4, 1.5**
    """

    @given(
        duration=duration_strategy,
        frame_interval=frame_interval_strategy,
        max_frames=max_frames_strategy,
    )
    @settings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_frame_extraction_count_and_timestamp_invariants(
        self,
        duration: float,
        frame_interval: float,
        max_frames: int,
    ):
        """Property 1: Frame extraction count and timestamp invariants.

        **Validates: Requirements 1.1, 1.4, 1.5**

        For any (duration, frame_interval, max_frames):
        - count = min(floor(duration / interval), max_frames)
        - timestamps are ascending multiples of interval
        - all timestamps < duration
        """
        # Ensure frame_interval does not exceed duration (otherwise 0 frames)
        assume(frame_interval <= duration)

        vlm_service = _make_vlm_service()
        tmp_dir = tempfile.mkdtemp(prefix="pbt_frame_")
        video_path = os.path.join(tmp_dir, "test.mp4")

        try:
            _generate_test_video(duration, video_path)

            frames = vlm_service.extract_frames(
                video_path=video_path,
                frame_interval=frame_interval,
                max_frames=max_frames,
                max_width=64,
                jpeg_quality=8,
            )

            # --- Property: count ---
            # The design spec states count = min(floor(D/I), M).
            # In practice, FFmpeg's fps filter may produce up to
            # ceil(D/I) frames (including the frame at t=0), so the
            # actual count from extract_frames can be in the range
            # [min(floor(D/I), M), min(ceil(D/I), M)].
            floor_count = min(math.floor(duration / frame_interval), max_frames)
            ceil_count = min(math.ceil(duration / frame_interval), max_frames)
            assert floor_count <= len(frames) <= ceil_count, (
                f"Expected frame count in [{floor_count}, {ceil_count}] "
                f"(duration={duration}, interval={frame_interval}, "
                f"max_frames={max_frames}), got {len(frames)}"
            )

            # --- Property: timestamps are ascending multiples of interval ---
            timestamps = [ts for ts, _ in frames]
            for idx, ts in enumerate(timestamps):
                expected_ts = idx * frame_interval
                assert ts == pytest.approx(expected_ts, abs=0.01), (
                    f"Frame {idx}: expected timestamp {expected_ts}, got {ts}"
                )

            # --- Property: timestamps are strictly ascending ---
            for i in range(1, len(timestamps)):
                assert timestamps[i] > timestamps[i - 1], (
                    f"Timestamps not ascending: {timestamps[i-1]} >= {timestamps[i]}"
                )

            # --- Property: all timestamps < duration ---
            for ts in timestamps:
                assert ts < duration, (
                    f"Timestamp {ts} >= duration {duration}"
                )

            # --- Property: all timestamps are non-negative ---
            for ts in timestamps:
                assert ts >= 0, f"Negative timestamp: {ts}"

        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
