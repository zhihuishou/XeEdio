"""Unit tests for hybrid pipeline: _merge_timelines() and _run_hybrid_pipeline() routing.

Tests cover:
- _merge_timelines: merging text-driven main axis with B-roll insertions
- run_auto_pipeline: hybrid routing when presenter + non-presenter assets exist
- _run_hybrid_pipeline: fallback scenarios
"""

import os
import sys
from unittest.mock import MagicMock, patch

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

from app.services.ai_director_service import AIDirectorService, _merge_timelines


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def director(tmp_path):
    """Create an AIDirectorService with a temp output directory."""
    return AIDirectorService(task_id="test-hybrid-001", output_dir=str(tmp_path))


@pytest.fixture
def sample_clip_paths(tmp_path):
    """Create dummy clip files."""
    paths = []
    for i in range(3):
        p = tmp_path / f"clip_{i}.mp4"
        p.write_bytes(b"\x00" * 100)
        paths.append(str(p))
    return paths


PRESENTER_ANALYSIS = {
    "asset_id": "asset-presenter",
    "description": "主播正面讲解护肤品",
    "role": "presenter",
    "visual_quality": "high",
    "has_speech": True,
    "transcript": "大家好，今天给大家推荐一款非常好用的护肤品",
    "status": "completed",
}

PRODUCT_ANALYSIS = {
    "asset_id": "asset-product",
    "description": "产品特写镜头",
    "role": "product_closeup",
    "visual_quality": "high",
    "has_speech": False,
    "transcript": "",
    "status": "completed",
}

LIFESTYLE_ANALYSIS = {
    "asset_id": "asset-lifestyle",
    "description": "户外生活场景",
    "role": "lifestyle",
    "visual_quality": "medium",
    "has_speech": False,
    "transcript": "",
    "status": "completed",
}


# ---------------------------------------------------------------------------
# Tests: _merge_timelines
# ---------------------------------------------------------------------------

class TestMergeTimelines:
    """Tests for the _merge_timelines helper function."""

    def test_empty_main_timeline_returns_empty(self):
        """Empty main timeline → empty result."""
        result = _merge_timelines([], [{"broll_index": 0, "source_start": 0, "source_end": 2, "duration": 2}], 1)
        assert result == []

    def test_no_broll_candidates_returns_main_with_type(self):
        """No B-roll candidates → main timeline entries annotated with type='main'."""
        main = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0, "start": 0.0, "end": 5.0, "reason": "intro"},
            {"clip_index": 0, "source_start": 8.0, "source_end": 15.0, "start": 5.0, "end": 12.0, "reason": "main"},
        ]
        result = _merge_timelines(main, [], 0)
        assert len(result) == 2
        assert all(e["type"] == "main" for e in result)
        assert result[0]["reason"] == "intro"
        assert result[1]["reason"] == "main"

    def test_single_broll_inserted_between_segments(self):
        """One B-roll candidate inserted at the gap between two main segments."""
        main = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 10.0, "start": 0.0, "end": 10.0, "reason": "seg1"},
            {"clip_index": 0, "source_start": 15.0, "source_end": 25.0, "start": 10.0, "end": 20.0, "reason": "seg2"},
        ]
        broll = [
            {"broll_index": 0, "source_start": 0.0, "source_end": 2.0, "duration": 2.0, "filename": "product.mp4"},
        ]
        result = _merge_timelines(main, broll, 1)

        assert len(result) == 3
        # First: main seg1
        assert result[0]["type"] == "main"
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 10.0
        # Second: B-roll insertion
        assert result[1]["type"] == "broll"
        assert result[1]["clip_index"] == 1  # 0 + 1 (presenter is 0)
        assert result[1]["start"] == 10.0
        assert result[1]["end"] == 12.0
        assert result[1]["source_start"] == 0.0
        assert result[1]["source_end"] == 2.0
        # Third: main seg2 (shifted by 2s)
        assert result[2]["type"] == "main"
        assert result[2]["start"] == 12.0
        assert result[2]["end"] == 22.0

    def test_multiple_broll_inserted_at_multiple_gaps(self):
        """Multiple B-roll candidates inserted at different gaps."""
        main = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 8.0, "start": 0.0, "end": 8.0, "reason": "seg1"},
            {"clip_index": 0, "source_start": 12.0, "source_end": 20.0, "start": 8.0, "end": 16.0, "reason": "seg2"},
            {"clip_index": 0, "source_start": 25.0, "source_end": 35.0, "start": 16.0, "end": 26.0, "reason": "seg3"},
        ]
        broll = [
            {"broll_index": 0, "source_start": 0.0, "source_end": 1.5, "duration": 1.5, "filename": "product.mp4"},
            {"broll_index": 1, "source_start": 0.0, "source_end": 2.0, "duration": 2.0, "filename": "lifestyle.mp4"},
        ]
        result = _merge_timelines(main, broll, 2)

        assert len(result) == 5  # 3 main + 2 broll
        types = [e["type"] for e in result]
        assert types == ["main", "broll", "main", "broll", "main"]

        # Verify B-roll clip indices are offset correctly
        assert result[1]["clip_index"] == 1  # broll_index 0 → clip_index 1
        assert result[3]["clip_index"] == 2  # broll_index 1 → clip_index 2

    def test_output_times_are_contiguous(self):
        """Merged timeline has no gaps in output times."""
        main = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0, "start": 0.0, "end": 5.0, "reason": "a"},
            {"clip_index": 0, "source_start": 10.0, "source_end": 18.0, "start": 5.0, "end": 13.0, "reason": "b"},
        ]
        broll = [
            {"broll_index": 0, "source_start": 0.0, "source_end": 3.0, "duration": 3.0, "filename": "broll.mp4"},
        ]
        result = _merge_timelines(main, broll, 1)

        # Check contiguity: each entry's start == previous entry's end
        for i in range(1, len(result)):
            assert abs(result[i]["start"] - result[i - 1]["end"]) < 0.01, (
                f"Gap between entry {i-1} and {i}: "
                f"{result[i-1]['end']} → {result[i]['start']}"
            )

    def test_broll_limited_to_candidates_count(self):
        """Number of B-roll insertions doesn't exceed available candidates."""
        main = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0, "start": 0.0, "end": 5.0, "reason": "a"},
            {"clip_index": 0, "source_start": 10.0, "source_end": 15.0, "start": 5.0, "end": 10.0, "reason": "b"},
            {"clip_index": 0, "source_start": 20.0, "source_end": 25.0, "start": 10.0, "end": 15.0, "reason": "c"},
            {"clip_index": 0, "source_start": 30.0, "source_end": 35.0, "start": 15.0, "end": 20.0, "reason": "d"},
        ]
        # Only 1 B-roll candidate but 3 possible insertion points
        broll = [
            {"broll_index": 0, "source_start": 0.0, "source_end": 2.0, "duration": 2.0, "filename": "only.mp4"},
        ]
        result = _merge_timelines(main, broll, 1)

        broll_count = sum(1 for e in result if e["type"] == "broll")
        assert broll_count == 1

    def test_no_gap_between_segments_still_inserts(self):
        """When segments are contiguous in source time, still tries to insert B-roll."""
        main = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 10.0, "start": 0.0, "end": 10.0, "reason": "a"},
            {"clip_index": 0, "source_start": 10.0, "source_end": 20.0, "start": 10.0, "end": 20.0, "reason": "b"},
        ]
        broll = [
            {"broll_index": 0, "source_start": 0.0, "source_end": 1.0, "duration": 1.0, "filename": "broll.mp4"},
        ]
        result = _merge_timelines(main, broll, 1)

        # Should still insert B-roll (fallback to largest gap logic)
        broll_count = sum(1 for e in result if e["type"] == "broll")
        assert broll_count == 1

    def test_single_main_segment_no_insertion(self):
        """Single main segment → no insertion point, B-roll not inserted."""
        main = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 30.0, "start": 0.0, "end": 30.0, "reason": "only"},
        ]
        broll = [
            {"broll_index": 0, "source_start": 0.0, "source_end": 2.0, "duration": 2.0, "filename": "broll.mp4"},
        ]
        result = _merge_timelines(main, broll, 1)

        # Only 1 segment, no gap to insert into
        assert len(result) == 1
        assert result[0]["type"] == "main"


# ---------------------------------------------------------------------------
# Tests: Hybrid routing in run_auto_pipeline
# ---------------------------------------------------------------------------

class TestHybridRouting:
    """Tests that run_auto_pipeline routes to hybrid when presenter + non-presenter."""

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch.object(AIDirectorService, "_run_hybrid_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_presenter_plus_product_routes_to_hybrid(
        self, mock_analysis_cls, mock_hybrid, mock_text, mock_montage,
        director, sample_clip_paths,
    ):
        """Presenter + product_closeup → hybrid pipeline."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [PRESENTER_ANALYSIS, PRODUCT_ANALYSIS]
        mock_analysis_cls.return_value = mock_service

        mock_hybrid.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths[:2],
            asset_ids=["asset-presenter", "asset-product"],
            max_output_duration=60,
        )

        mock_hybrid.assert_called_once()
        mock_text.assert_not_called()
        mock_montage.assert_not_called()
        assert result == ("/output/output-1.mp4", True)

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch.object(AIDirectorService, "_run_hybrid_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_presenter_plus_multiple_broll_routes_to_hybrid(
        self, mock_analysis_cls, mock_hybrid, mock_text, mock_montage,
        director, sample_clip_paths,
    ):
        """Presenter + 2 non-presenter clips → hybrid pipeline."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [
            PRESENTER_ANALYSIS, PRODUCT_ANALYSIS, LIFESTYLE_ANALYSIS,
        ]
        mock_analysis_cls.return_value = mock_service

        mock_hybrid.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-presenter", "asset-product", "asset-lifestyle"],
            max_output_duration=60,
        )

        mock_hybrid.assert_called_once()
        # Verify presenter_path is the first clip, broll_paths are the rest
        call_kwargs = mock_hybrid.call_args[1]
        assert call_kwargs["presenter_path"] == sample_clip_paths[0]
        assert call_kwargs["broll_paths"] == sample_clip_paths[1:]

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch.object(AIDirectorService, "_run_hybrid_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_single_presenter_routes_to_text_driven(
        self, mock_analysis_cls, mock_hybrid, mock_text, mock_montage,
        director, sample_clip_paths,
    ):
        """Single presenter asset (no B-roll) → text-driven, not hybrid."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = PRESENTER_ANALYSIS
        mock_analysis_cls.return_value = mock_service

        mock_text.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths[:1],
            asset_ids=["asset-presenter"],
            max_output_duration=60,
        )

        mock_text.assert_called_once()
        mock_hybrid.assert_not_called()
        mock_montage.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch.object(AIDirectorService, "_run_hybrid_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_no_presenter_routes_to_vision(
        self, mock_analysis_cls, mock_hybrid, mock_text, mock_montage,
        director, sample_clip_paths,
    ):
        """All non-presenter assets → vision-driven, not hybrid."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [PRODUCT_ANALYSIS, LIFESTYLE_ANALYSIS]
        mock_analysis_cls.return_value = mock_service

        mock_montage.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths[:2],
            asset_ids=["asset-product", "asset-lifestyle"],
            max_output_duration=60,
        )

        mock_montage.assert_called_once()
        mock_hybrid.assert_not_called()
        mock_text.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch.object(AIDirectorService, "_run_hybrid_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_hybrid_passes_correct_params(
        self, mock_analysis_cls, mock_hybrid, mock_text, mock_montage,
        director, sample_clip_paths,
    ):
        """Hybrid pipeline receives correct parameters."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [PRESENTER_ANALYSIS, PRODUCT_ANALYSIS]
        mock_analysis_cls.return_value = mock_service

        mock_hybrid.return_value = ("/output/output-1.mp4", True)

        director.run_auto_pipeline(
            clip_paths=sample_clip_paths[:2],
            asset_ids=["asset-presenter", "asset-product"],
            aspect_ratio="16:9",
            transition="fade",
            audio_file="/tmp/audio.mp3",
            max_output_duration=120,
            director_prompt="多用特写",
        )

        mock_hybrid.assert_called_once_with(
            presenter_path=sample_clip_paths[0],
            presenter_analysis=PRESENTER_ANALYSIS,
            broll_paths=[sample_clip_paths[1]],
            broll_asset_ids=["asset-product"],
            aspect_ratio="16:9",
            transition="fade",
            audio_file="/tmp/audio.mp3",
            max_output_duration=120,
            progress_callback=None,
            director_prompt="多用特写",
        )
