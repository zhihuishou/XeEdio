"""Property-based tests for VLMService.extract_frames() method.

# Feature: ai-director, Property 1: Frame extraction count and timestamp invariants

Uses Hypothesis to verify that for any (duration, frame_interval, max_frames):
  - count == min(count_of_timestamps_below_duration, max_frames)
  - timestamps are ascending multiples of interval
  - all timestamps < duration
  - list is sorted by timestamp in ascending order

Generates real (tiny) test videos via FFmpeg so extract_frames runs its
full subprocess pipeline.

**Validates: Requirements 1.1, 1.4, 1.5**
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

def _make_vlm_service() -> VLMService:
    """Create a VLMService with mocked config (only ExternalConfig is mocked)."""
    with patch("app.services.vlm_service.ExternalConfig") as mock_config_cls:
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {
            "api_url": "",
            "api_key": "",
        }
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()
    return svc


def _make_test_video(duration: float, tmp_dir: str) -> str:
    """Generate a tiny black video of the given duration using FFmpeg.

    Returns the path to the generated .mp4 file.
    """
    video_path = os.path.join(tmp_dir, f"test_{duration:.3f}s.mp4")
    cmd = [
        "ffmpeg",
        "-f", "lavfi",
        "-i", f"color=c=black:s=64x64:d={duration:.6f}",
        "-r", "25",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-y",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"FFmpeg failed: {result.stderr[:300]}"
    return video_path


def _expected_frame_count(duration: float, frame_interval: float, max_frames: int) -> int:
    """Compute the expected number of frames from extract_frames.

    FFmpeg's fps=1/I filter produces frames at timestamps 0, I, 2I, ...
    The code then iterates these frames and keeps each one where:
      - idx < max_frames
      - idx * frame_interval < duration

    So the count is: min(number of k>=0 where k*I < D, max_frames).
    This equals min(floor(D/I) + 1, M) when D is not an exact multiple
    of I, and min(floor(D/I), M) when it is — equivalently, the number
    of non-negative integer indices whose timestamp falls strictly below
    the video duration, capped at max_frames.
    """
    # Count timestamps k*I that are strictly less than duration
    n = 0
    while n * frame_interval < duration:
        n += 1
    # n is now the first index where n*I >= D, so valid count is n
    # But we also need FFmpeg to have produced at least n frames.
    # FFmpeg's fps filter produces the same set, so n is correct.
    return min(n, max_frames)


# ---------------------------------------------------------------------------
# Property-based test
# ---------------------------------------------------------------------------

# Feature: ai-director, Property 1: Frame extraction count and timestamp invariants

@given(
    duration=st.floats(min_value=1.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    frame_interval=st.floats(min_value=0.5, max_value=5.0, allow_nan=False, allow_infinity=False),
    max_frames=st.integers(min_value=1, max_value=30),
)
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_frame_extraction_count_and_timestamp_invariants(
    duration, frame_interval, max_frames
):
    """Property 1: Frame extraction count and timestamp invariants.

    **Validates: Requirements 1.1, 1.4, 1.5**

    For any A-roll video with duration D seconds, frame interval I seconds,
    and max frames limit M, extract_frames SHALL return frames where:
      - count equals the number of valid timestamp slots (k*I < D) capped by M
      - each timestamp is a non-negative multiple of I: timestamp[k] == k * I
      - all timestamps are strictly less than D
      - the list is sorted by timestamp in ascending order
    """
    # Ensure we get at least 1 frame (0 * I = 0 < D is always true for D > 0,
    # so this is guaranteed, but we add assume for clarity)
    assume(duration > 0 and frame_interval > 0)

    expected_count = _expected_frame_count(duration, frame_interval, max_frames)
    assume(expected_count >= 1)

    vlm_service = _make_vlm_service()
    tmp_dir = tempfile.mkdtemp(prefix="pbt_frames_")

    try:
        # Generate a real test video
        video_path = _make_test_video(duration, tmp_dir)

        # Run the actual extract_frames method (uses real FFmpeg)
        frames = vlm_service.extract_frames(
            video_path=video_path,
            frame_interval=frame_interval,
            max_frames=max_frames,
        )

        # --- Property: frame count matches expected ---
        assert len(frames) == expected_count, (
            f"Expected {expected_count} frames "
            f"(duration={duration}, interval={frame_interval}, "
            f"max_frames={max_frames}), got {len(frames)}"
        )

        # --- Property: timestamps are ascending multiples of interval ---
        for idx, (timestamp, _) in enumerate(frames):
            expected_ts = idx * frame_interval
            assert abs(timestamp - expected_ts) < 1e-9, (
                f"Frame {idx}: expected timestamp {expected_ts}, got {timestamp}"
            )

        # --- Property: all timestamps < duration ---
        for idx, (timestamp, _) in enumerate(frames):
            assert timestamp < duration, (
                f"Frame {idx}: timestamp {timestamp} >= duration {duration}"
            )

        # --- Property: list is sorted by timestamp in ascending order ---
        timestamps = [ts for ts, _ in frames]
        assert timestamps == sorted(timestamps), (
            f"Timestamps not sorted: {timestamps}"
        )

        # --- Each frame has non-empty base64 data ---
        for idx, (_, b64_str) in enumerate(frames):
            assert isinstance(b64_str, str) and len(b64_str) > 0, (
                f"Frame {idx}: empty or non-string base64 data"
            )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
