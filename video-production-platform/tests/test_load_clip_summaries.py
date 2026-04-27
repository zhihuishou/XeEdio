"""Unit tests for AIDirectorService._load_clip_summaries() and
the refactored run_montage_pipeline() with asset_ids / DB cache support.

Tests:
- _load_clip_summaries: cache hit, cache miss, mixed, exception handling
- run_montage_pipeline: uses unified timeline when asset_ids provided,
  falls back to legacy montage timeline when no asset_ids or cache miss
"""

import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

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

_mock_mixing_engine = MagicMock()
sys.modules["app.services.mixing_engine"] = _mock_mixing_engine

from app.services.ai_director_service import AIDirectorService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def director(tmp_path):
    """Create an AIDirectorService with a temp output directory."""
    return AIDirectorService(task_id="test-task-025", output_dir=str(tmp_path))


@pytest.fixture
def sample_clip_paths(tmp_path):
    """Create dummy clip files."""
    paths = []
    for i in range(3):
        p = tmp_path / f"clip_{i}.mp4"
        p.write_bytes(b"\x00" * 100)
        paths.append(str(p))
    return paths


# ---------------------------------------------------------------------------
# Sample analysis data
# ---------------------------------------------------------------------------

COMPLETED_ANALYSIS_A = {
    "asset_id": "asset-a",
    "description": "主播正面讲解护肤品",
    "role": "presenter",
    "visual_quality": "high",
    "scene_tags": ["美妆", "口播"],
    "key_moments": [{"time": 5.0, "desc": "展示产品"}],
    "has_speech": True,
    "transcript": "大家好",
    "status": "completed",
}

COMPLETED_ANALYSIS_B = {
    "asset_id": "asset-b",
    "description": "产品特写镜头",
    "role": "product_closeup",
    "visual_quality": "high",
    "scene_tags": ["产品"],
    "key_moments": [],
    "has_speech": False,
    "transcript": "",
    "status": "completed",
}

COMPLETED_ANALYSIS_C = {
    "asset_id": "asset-c",
    "description": "户外空镜",
    "role": "lifestyle",
    "visual_quality": "medium",
    "scene_tags": ["户外"],
    "key_moments": [],
    "has_speech": False,
    "transcript": "",
    "status": "completed",
}

PENDING_ANALYSIS = {
    "asset_id": "asset-pending",
    "description": "",
    "role": "other",
    "has_speech": False,
    "transcript": "",
    "status": "pending",
}


# ---------------------------------------------------------------------------
# Tests: _load_clip_summaries
# ---------------------------------------------------------------------------

class TestLoadClipSummaries:
    """Tests for the _load_clip_summaries method."""

    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_all_cache_hits(self, mock_analysis_cls, director, sample_clip_paths):
        """All assets have completed analysis → all cache hits, no VLM calls."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [
            COMPLETED_ANALYSIS_A,
            COMPLETED_ANALYSIS_B,
            COMPLETED_ANALYSIS_C,
        ]
        mock_analysis_cls.return_value = mock_service

        # Replace vlm_service with a mock to verify no real-time calls
        director.vlm_service = MagicMock()

        asset_ids = ["asset-a", "asset-b", "asset-c"]
        summaries = director._load_clip_summaries(asset_ids, sample_clip_paths)

        assert len(summaries) == 3
        assert summaries[0]["role"] == "presenter"
        assert summaries[1]["role"] == "product_closeup"
        assert summaries[2]["role"] == "lifestyle"
        # No real-time VLM analysis should have been called
        director.vlm_service.extract_frames.assert_not_called()
        director.vlm_service.analyze_single_clip.assert_not_called()

    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_all_cache_misses(self, mock_analysis_cls, mock_duration, director, sample_clip_paths):
        """No completed analysis → all cache misses, real-time VLM for each."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [None, PENDING_ANALYSIS, None]
        mock_analysis_cls.return_value = mock_service

        # Mock VLM real-time analysis
        director.vlm_service.extract_frames = MagicMock(return_value=[(0.0, "base64data")])
        director.vlm_service.analyze_single_clip = MagicMock(return_value={
            "description": "fallback analysis",
            "role": "other",
        })

        asset_ids = ["asset-x", "asset-pending", "asset-y"]
        summaries = director._load_clip_summaries(asset_ids, sample_clip_paths)

        assert len(summaries) == 3
        assert all(s["role"] == "other" for s in summaries)
        assert director.vlm_service.extract_frames.call_count == 3
        assert director.vlm_service.analyze_single_clip.call_count == 3

    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_mixed_cache_hits_and_misses(self, mock_analysis_cls, mock_duration, director, sample_clip_paths):
        """Mix of cache hits and misses."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [
            COMPLETED_ANALYSIS_A,  # hit
            None,                   # miss
            COMPLETED_ANALYSIS_C,  # hit
        ]
        mock_analysis_cls.return_value = mock_service

        director.vlm_service.extract_frames = MagicMock(return_value=[(0.0, "base64data")])
        director.vlm_service.analyze_single_clip = MagicMock(return_value={
            "description": "realtime",
            "role": "other",
        })

        asset_ids = ["asset-a", "asset-x", "asset-c"]
        summaries = director._load_clip_summaries(asset_ids, sample_clip_paths)

        assert len(summaries) == 3
        assert summaries[0]["role"] == "presenter"  # cache hit
        assert summaries[1]["role"] == "other"       # cache miss → realtime
        assert summaries[2]["role"] == "lifestyle"   # cache hit
        # Only 1 real-time analysis (for the miss)
        assert director.vlm_service.extract_frames.call_count == 1
        assert director.vlm_service.analyze_single_clip.call_count == 1

    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_exception_during_analysis_load(self, mock_analysis_cls, mock_duration, director, sample_clip_paths):
        """Exception during get_analysis → falls back to real-time VLM."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [
            Exception("DB connection error"),
            COMPLETED_ANALYSIS_B,
            COMPLETED_ANALYSIS_C,
        ]
        mock_analysis_cls.return_value = mock_service

        director.vlm_service.extract_frames = MagicMock(return_value=[(0.0, "base64data")])
        director.vlm_service.analyze_single_clip = MagicMock(return_value={
            "description": "fallback",
            "role": "other",
        })

        asset_ids = ["asset-err", "asset-b", "asset-c"]
        summaries = director._load_clip_summaries(asset_ids, sample_clip_paths)

        assert len(summaries) == 3
        assert summaries[0]["role"] == "other"           # exception → fallback
        assert summaries[1]["role"] == "product_closeup"  # cache hit
        assert summaries[2]["role"] == "lifestyle"        # cache hit

    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_realtime_vlm_failure_returns_empty_summary(self, mock_analysis_cls, mock_duration, director, sample_clip_paths):
        """When real-time VLM also fails, returns empty summary dict."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = None
        mock_analysis_cls.return_value = mock_service

        director.vlm_service.extract_frames = MagicMock(return_value=[])

        asset_ids = ["asset-x", "asset-y", "asset-z"]
        summaries = director._load_clip_summaries(asset_ids, sample_clip_paths)

        assert len(summaries) == 3
        for s in summaries:
            assert s["description"] == ""
            assert s["role"] == "other"


# ---------------------------------------------------------------------------
# Tests: run_montage_pipeline with asset_ids
# ---------------------------------------------------------------------------

class TestMontageWithAssetIds:
    """Tests that run_montage_pipeline uses unified timeline when asset_ids provided."""

    @patch("app.services.ai_director_service.snap_timeline_to_breath_gaps", side_effect=lambda t, *a, **kw: t)
    @patch("app.services.ai_director_service.execute_montage_timeline")
    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    @patch.object(AIDirectorService, "_load_clip_summaries")
    def test_uses_unified_timeline_when_summaries_available(
        self, mock_load, mock_duration, mock_execute, mock_snap, director, sample_clip_paths
    ):
        """When asset_ids provided and summaries loaded, uses generate_unified_timeline."""
        mock_load.return_value = [COMPLETED_ANALYSIS_A, COMPLETED_ANALYSIS_B, COMPLETED_ANALYSIS_C]

        # Mock frame extraction
        director.vlm_service.extract_frames = MagicMock(return_value=[(0.0, "b64")])
        director.vlm_service.config.get_vlm_config = MagicMock(return_value={
            "api_url": "http://test", "model": "test", "max_frames": 30, "frame_interval": 2,
        })

        # Mock unified timeline generation
        unified_timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0, "start": 0.0, "end": 5.0, "reason": "opening"},
            {"clip_index": 1, "source_start": 0.0, "source_end": 3.0, "start": 5.0, "end": 8.0, "reason": "product"},
            {"clip_index": 2, "source_start": 0.0, "source_end": 4.0, "start": 8.0, "end": 12.0, "reason": "lifestyle"},
        ]
        director.vlm_service.generate_unified_timeline = MagicMock(return_value=unified_timeline)

        result = director.run_montage_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-a", "asset-b", "asset-c"],
            max_output_duration=60,
        )

        # Should have called _load_clip_summaries
        mock_load.assert_called_once_with(
            ["asset-a", "asset-b", "asset-c"],
            sample_clip_paths,
            None,
        )
        # Should have called generate_unified_timeline (not generate_montage_timeline)
        director.vlm_service.generate_unified_timeline.assert_called_once()
        director.vlm_service.generate_montage_timeline = MagicMock()
        # Should have executed the timeline
        mock_execute.assert_called_once()
        assert result[1] is True  # ai_director_used

    @patch("app.services.ai_director_service.snap_timeline_to_breath_gaps", side_effect=lambda t, *a, **kw: t)
    @patch("app.services.ai_director_service.execute_montage_timeline")
    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    @patch.object(AIDirectorService, "_load_clip_summaries")
    def test_unified_timeline_receives_original_filenames(
        self, mock_load, mock_duration, mock_execute, mock_snap, director, sample_clip_paths
    ):
        """clip_original_filenames should be used in clip_metadata sent to VLM."""
        mock_load.return_value = [COMPLETED_ANALYSIS_A, COMPLETED_ANALYSIS_B, COMPLETED_ANALYSIS_C]
        director.vlm_service.extract_frames = MagicMock(return_value=[(0.0, "b64")])
        director.vlm_service.config.get_vlm_config = MagicMock(return_value={
            "api_url": "http://test", "model": "test", "max_frames": 30, "frame_interval": 2,
        })
        director.vlm_service.generate_unified_timeline = MagicMock(return_value=[
            {"clip_index": 0, "source_start": 0.0, "source_end": 2.0, "start": 0.0, "end": 2.0, "reason": "a"},
            {"clip_index": 1, "source_start": 0.0, "source_end": 2.0, "start": 2.0, "end": 4.0, "reason": "b"},
            {"clip_index": 2, "source_start": 0.0, "source_end": 2.0, "start": 4.0, "end": 6.0, "reason": "c"},
        ])

        director.run_montage_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-a", "asset-b", "asset-c"],
            clip_original_filenames=["1.mov", "2.mov", "3.mov"],
            max_output_duration=60,
        )

        call_kwargs = director.vlm_service.generate_unified_timeline.call_args.kwargs
        meta = call_kwargs["clip_metadata"]
        assert [m["filename"] for m in meta] == ["1.mov", "2.mov", "3.mov"]

    @patch("app.services.ai_director_service.snap_timeline_to_breath_gaps", side_effect=lambda t, *a, **kw: t)
    @patch("app.services.ai_director_service.execute_montage_timeline")
    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    def test_no_asset_ids_uses_legacy_montage(
        self, mock_duration, mock_execute, mock_snap, director, sample_clip_paths
    ):
        """When no asset_ids, uses legacy generate_montage_timeline."""
        director.vlm_service.extract_frames = MagicMock(return_value=[(0.0, "b64")])
        director.vlm_service.config.get_vlm_config = MagicMock(return_value={
            "api_url": "http://test", "model": "test", "max_frames": 30, "frame_interval": 2,
        })

        legacy_timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0, "start": 0.0, "end": 5.0, "reason": "a"},
            {"clip_index": 1, "source_start": 0.0, "source_end": 3.0, "start": 5.0, "end": 8.0, "reason": "b"},
            {"clip_index": 2, "source_start": 0.0, "source_end": 4.0, "start": 8.0, "end": 12.0, "reason": "c"},
        ]
        director.vlm_service.generate_montage_timeline = MagicMock(return_value=legacy_timeline)

        result = director.run_montage_pipeline(
            clip_paths=sample_clip_paths,
            max_output_duration=60,
        )

        director.vlm_service.generate_montage_timeline.assert_called_once()
        mock_execute.assert_called_once()
        assert result[1] is True

    @patch("app.services.ai_director_service.snap_timeline_to_breath_gaps", side_effect=lambda t, *a, **kw: t)
    @patch("app.services.ai_director_service.execute_montage_timeline")
    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    @patch.object(AIDirectorService, "_load_clip_summaries")
    def test_unified_timeline_failure_falls_back_to_legacy(
        self, mock_load, mock_duration, mock_execute, mock_snap, director, sample_clip_paths
    ):
        """When generate_unified_timeline returns None, falls back to legacy montage."""
        mock_load.return_value = [COMPLETED_ANALYSIS_A, COMPLETED_ANALYSIS_B, COMPLETED_ANALYSIS_C]

        director.vlm_service.extract_frames = MagicMock(return_value=[(0.0, "b64")])
        director.vlm_service.config.get_vlm_config = MagicMock(return_value={
            "api_url": "http://test", "model": "test", "max_frames": 30, "frame_interval": 2,
        })

        # Unified timeline fails
        director.vlm_service.generate_unified_timeline = MagicMock(return_value=None)

        # Legacy montage succeeds
        legacy_timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0, "start": 0.0, "end": 5.0, "reason": "a"},
            {"clip_index": 1, "source_start": 0.0, "source_end": 3.0, "start": 5.0, "end": 8.0, "reason": "b"},
            {"clip_index": 2, "source_start": 0.0, "source_end": 4.0, "start": 8.0, "end": 12.0, "reason": "c"},
        ]
        director.vlm_service.generate_montage_timeline = MagicMock(return_value=legacy_timeline)

        result = director.run_montage_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-a", "asset-b", "asset-c"],
            max_output_duration=60,
        )

        # Both should have been called
        director.vlm_service.generate_unified_timeline.assert_called_once()
        director.vlm_service.generate_montage_timeline.assert_called_once()
        mock_execute.assert_called_once()
        assert result[1] is True

    @patch("app.services.ai_director_service.combine_videos")
    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    @patch.object(AIDirectorService, "_load_clip_summaries")
    def test_both_timelines_fail_falls_back_to_blind_cut(
        self, mock_load, mock_duration, mock_combine, director, sample_clip_paths
    ):
        """When both unified and legacy timelines fail, falls back to blind-cut."""
        mock_load.return_value = [COMPLETED_ANALYSIS_A, COMPLETED_ANALYSIS_B, COMPLETED_ANALYSIS_C]

        director.vlm_service.extract_frames = MagicMock(return_value=[(0.0, "b64")])
        director.vlm_service.config.get_vlm_config = MagicMock(return_value={
            "api_url": "http://test", "model": "test", "max_frames": 30, "frame_interval": 2,
        })

        director.vlm_service.generate_unified_timeline = MagicMock(return_value=None)
        director.vlm_service.generate_montage_timeline = MagicMock(return_value=None)

        result = director.run_montage_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-a", "asset-b", "asset-c"],
            max_output_duration=60,
        )

        mock_combine.assert_called_once()
        assert result[1] is False  # ai_director_used = False (blind-cut)

    @patch("app.services.ai_director_service._get_video_duration", return_value=30.0)
    def test_mismatched_asset_ids_length_skips_cache(
        self, mock_duration, director, sample_clip_paths
    ):
        """When asset_ids length doesn't match clip_paths, skips cache loading."""
        director.vlm_service.extract_frames = MagicMock(return_value=[])
        director.vlm_service.config.get_vlm_config = MagicMock(return_value={
            "api_url": "", "model": "test", "max_frames": 30, "frame_interval": 2,
        })

        # Provide mismatched lengths
        with patch("app.services.ai_director_service.combine_videos"):
            result = director.run_montage_pipeline(
                clip_paths=sample_clip_paths,  # 3 paths
                asset_ids=["asset-a"],          # only 1 id
                max_output_duration=60,
            )

        # Should not have tried to load summaries (length mismatch)
        assert result[1] is False  # blind-cut fallback
