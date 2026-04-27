"""Unit tests for AIDirectorService.run_auto_pipeline() routing logic.

Tests the auto-routing decision:
- has_speech=True + role="presenter" → text-driven pipeline
- has_speech=False or role!="presenter" → vision-driven (montage) pipeline
- Missing analysis data → vision-driven fallback
- Analysis load failure → vision-driven fallback
"""

import os
import sys
from unittest.mock import MagicMock, patch, call
from types import ModuleType

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing the module under test.
# AIDirectorService imports mixing_engine (moviepy), asset_analysis_service
# (sqlalchemy), etc. We only need the routing logic, not the real deps.
# ---------------------------------------------------------------------------

# Create mock modules for the entire dependency chain
for mod_name in [
    "moviepy", "moviepy.video", "moviepy.video.io",
    "moviepy.video.io.VideoFileClip",
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.orm.session",
    "sqlalchemy.ext", "sqlalchemy.ext.declarative",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

# Mock sqlalchemy components needed by database.py
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

# Mock mixing_engine
_mock_mixing_engine = MagicMock()
sys.modules["app.services.mixing_engine"] = _mock_mixing_engine

from app.services.ai_director_service import AIDirectorService, _prompt_requests_broll


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def director(tmp_path):
    """Create an AIDirectorService with a temp output directory."""
    return AIDirectorService(task_id="test-task-001", output_dir=str(tmp_path))


@pytest.fixture
def sample_clip_paths(tmp_path):
    """Create dummy clip files so path checks don't fail."""
    paths = []
    for i in range(2):
        p = tmp_path / f"clip_{i}.mp4"
        p.write_bytes(b"\x00" * 100)
        paths.append(str(p))
    return paths


COMPLETED_PRESENTER_ANALYSIS = {
    "asset_id": "asset-001",
    "description": "主播正面讲解护肤品",
    "role": "presenter",
    "visual_quality": "high",
    "audio_quality": "good",
    "has_speech": True,
    "speech_ranges": [[0, 230]],
    "transcript": "大家好，今天给大家推荐一款非常好用的护肤品",
    "status": "completed",
}

COMPLETED_PRODUCT_ANALYSIS = {
    "asset_id": "asset-002",
    "description": "产品特写镜头",
    "role": "product_closeup",
    "visual_quality": "high",
    "audio_quality": "silent",
    "has_speech": False,
    "speech_ranges": [],
    "transcript": "",
    "status": "completed",
}

PENDING_ANALYSIS = {
    "asset_id": "asset-003",
    "description": "",
    "role": "other",
    "has_speech": False,
    "transcript": "",
    "status": "pending",
}

COMPLETED_SPEECH_NO_PRESENTER = {
    "asset_id": "asset-004",
    "description": "生活场景有背景对话",
    "role": "lifestyle",
    "visual_quality": "medium",
    "audio_quality": "good",
    "has_speech": True,
    "speech_ranges": [[0, 60]],
    "transcript": "一些背景对话内容",
    "status": "completed",
}


# ---------------------------------------------------------------------------
# Tests: Routing decision
# ---------------------------------------------------------------------------

class TestAutoRoutingDecision:
    """Tests that run_auto_pipeline routes to the correct pipeline."""

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_presenter_with_speech_routes_to_text_driven(
        self, mock_analysis_cls, mock_text_driven, mock_montage, director, sample_clip_paths
    ):
        """has_speech=True + role=presenter → text-driven pipeline."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = COMPLETED_PRESENTER_ANALYSIS
        mock_analysis_cls.return_value = mock_service

        mock_text_driven.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-001"],
            max_output_duration=60,
        )

        mock_text_driven.assert_called_once()
        mock_montage.assert_not_called()
        assert result == ("/output/output-1.mp4", True)

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_product_closeup_routes_to_vision(
        self, mock_analysis_cls, mock_text_driven, mock_montage, director, sample_clip_paths
    ):
        """has_speech=False + role=product_closeup → vision-driven pipeline."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = COMPLETED_PRODUCT_ANALYSIS
        mock_analysis_cls.return_value = mock_service

        mock_montage.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-002"],
            max_output_duration=60,
        )

        mock_montage.assert_called_once()
        mock_text_driven.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_speech_but_not_presenter_routes_to_vision(
        self, mock_analysis_cls, mock_text_driven, mock_montage, director, sample_clip_paths
    ):
        """has_speech=True but role!=presenter → vision-driven pipeline."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = COMPLETED_SPEECH_NO_PRESENTER
        mock_analysis_cls.return_value = mock_service

        mock_montage.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-004"],
            max_output_duration=60,
        )

        mock_montage.assert_called_once()
        mock_text_driven.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_pending_analysis_routes_to_vision(
        self, mock_analysis_cls, mock_text_driven, mock_montage, director, sample_clip_paths
    ):
        """Analysis status=pending → vision-driven fallback."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = PENDING_ANALYSIS
        mock_analysis_cls.return_value = mock_service

        mock_montage.return_value = ("/output/output-1.mp4", False)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-003"],
            max_output_duration=60,
        )

        mock_montage.assert_called_once()
        mock_text_driven.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_no_analysis_found_routes_to_vision(
        self, mock_analysis_cls, mock_text_driven, mock_montage, director, sample_clip_paths
    ):
        """get_analysis returns None → vision-driven fallback."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = None
        mock_analysis_cls.return_value = mock_service

        mock_montage.return_value = ("/output/output-1.mp4", False)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-999"],
            max_output_duration=60,
        )

        mock_montage.assert_called_once()
        mock_text_driven.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_analysis_load_exception_routes_to_vision(
        self, mock_analysis_cls, mock_text_driven, mock_montage, director, sample_clip_paths
    ):
        """Exception during analysis load → vision-driven fallback."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = Exception("DB connection error")
        mock_analysis_cls.return_value = mock_service

        mock_montage.return_value = ("/output/output-1.mp4", False)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-001"],
            max_output_duration=60,
        )

        mock_montage.assert_called_once()
        mock_text_driven.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    def test_no_asset_ids_routes_to_vision(
        self, mock_text_driven, mock_montage, director, sample_clip_paths
    ):
        """No asset_ids provided → vision-driven fallback (no DB lookup)."""
        mock_montage.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=None,
            max_output_duration=60,
        )

        mock_montage.assert_called_once()
        mock_text_driven.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch.object(AIDirectorService, "_run_hybrid_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_multiple_assets_first_presenter_wins(
        self, mock_analysis_cls, mock_hybrid, mock_text_driven, mock_montage, director, sample_clip_paths
    ):
        """Multiple assets: presenter + non-presenter triggers hybrid pipeline."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [
            COMPLETED_PRODUCT_ANALYSIS,  # asset-002: no speech
            COMPLETED_PRESENTER_ANALYSIS,  # asset-001: presenter with speech
        ]
        mock_analysis_cls.return_value = mock_service

        mock_hybrid.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-002", "asset-001"],
            max_output_duration=60,
        )

        mock_hybrid.assert_called_once()
        mock_text_driven.assert_not_called()
        mock_montage.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch.object(AIDirectorService, "_run_text_driven")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_multiple_assets_none_presenter_routes_to_vision(
        self, mock_analysis_cls, mock_text_driven, mock_montage, director, sample_clip_paths
    ):
        """Multiple assets, none is presenter → vision-driven."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [
            COMPLETED_PRODUCT_ANALYSIS,
            COMPLETED_SPEECH_NO_PRESENTER,
        ]
        mock_analysis_cls.return_value = mock_service

        mock_montage.return_value = ("/output/output-1.mp4", True)

        result = director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-002", "asset-004"],
            max_output_duration=60,
        )

        mock_montage.assert_called_once()
        mock_text_driven.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: Text-driven fallback scenarios
# ---------------------------------------------------------------------------

class TestTextDrivenFallback:
    """Tests that _run_text_driven falls back to vision-driven on failure."""

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch("app.services.ai_director_service.TextDrivenEditingService")
    @patch("app.services.ai_director_service._get_word_timestamps")
    def test_empty_transcript_falls_back(
        self, mock_get_words, mock_text_cls, mock_montage, director, sample_clip_paths
    ):
        """Empty transcript in analysis → fallback to vision-driven."""
        mock_montage.return_value = ("/output/output-1.mp4", False)

        analysis = dict(COMPLETED_PRESENTER_ANALYSIS)
        analysis["transcript"] = ""

        director._run_text_driven(
            clip_paths=sample_clip_paths,
            analysis=analysis,
            max_output_duration=60,
        )

        mock_montage.assert_called_once()
        mock_get_words.assert_not_called()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch("app.services.ai_director_service.TextDrivenEditingService")
    @patch("app.services.ai_director_service._get_word_timestamps")
    def test_no_word_timestamps_falls_back(
        self, mock_get_words, mock_text_cls, mock_montage, director, sample_clip_paths
    ):
        """Word timestamp extraction returns empty → fallback to vision-driven."""
        mock_get_words.return_value = []
        mock_montage.return_value = ("/output/output-1.mp4", False)

        director._run_text_driven(
            clip_paths=sample_clip_paths,
            analysis=COMPLETED_PRESENTER_ANALYSIS,
            max_output_duration=60,
        )

        mock_montage.assert_called_once()

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch("app.services.ai_director_service.TextDrivenEditingService")
    @patch("app.services.ai_director_service._get_word_timestamps")
    def test_text_service_returns_none_falls_back(
        self, mock_get_words, mock_text_cls, mock_montage, director, sample_clip_paths
    ):
        """TextDrivenEditingService returns None → fallback to vision-driven."""
        mock_get_words.return_value = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
        ]
        mock_service = MagicMock()
        mock_service.generate_text_driven_timeline.return_value = None
        mock_text_cls.return_value = mock_service

        mock_montage.return_value = ("/output/output-1.mp4", False)

        director._run_text_driven(
            clip_paths=sample_clip_paths,
            analysis=COMPLETED_PRESENTER_ANALYSIS,
            max_output_duration=60,
        )

        mock_montage.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Parameters passed correctly
# ---------------------------------------------------------------------------

class TestParameterPassing:
    """Tests that run_auto_pipeline passes parameters correctly to sub-pipelines."""

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_vision_pipeline_receives_all_params(
        self, mock_analysis_cls, mock_montage, director, sample_clip_paths
    ):
        """Vision-driven pipeline should receive all relevant parameters."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = COMPLETED_PRODUCT_ANALYSIS
        mock_analysis_cls.return_value = mock_service

        mock_montage.return_value = ("/output/output-1.mp4", True)

        director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-002"],
            aspect_ratio="16:9",
            transition="fade",
            audio_file="/tmp/audio.mp3",
            max_output_duration=120,
            director_prompt="多用特写",
        )

        mock_montage.assert_called_once_with(
            clip_paths=sample_clip_paths,
            aspect_ratio="16:9",
            transition="fade",
            audio_file="/tmp/audio.mp3",
            max_output_duration=120,
            progress_callback=None,
            director_prompt="多用特写",
            asset_ids=["asset-002"],
            clip_original_filenames=None,
            video_count=1,
        )

    @patch.object(AIDirectorService, "_run_text_driven")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_text_pipeline_receives_all_params(
        self, mock_analysis_cls, mock_text_driven, director, sample_clip_paths
    ):
        """Text-driven pipeline should receive all relevant parameters."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = COMPLETED_PRESENTER_ANALYSIS
        mock_analysis_cls.return_value = mock_service

        mock_text_driven.return_value = ("/output/output-1.mp4", True)

        director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-001"],
            aspect_ratio="16:9",
            transition="fade",
            audio_file="/tmp/audio.mp3",
            max_output_duration=120,
            director_prompt="选精华",
        )

        mock_text_driven.assert_called_once_with(
            clip_paths=sample_clip_paths,
            analysis=COMPLETED_PRESENTER_ANALYSIS,
            asset_ids=["asset-001"],
            clip_original_filenames=None,
            aspect_ratio="16:9",
            transition="fade",
            audio_file="/tmp/audio.mp3",
            max_output_duration=120,
            progress_callback=None,
            director_prompt="选精华",
            video_count=1,
        )

    @patch.object(AIDirectorService, "_run_hybrid_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_hybrid_pipeline_receives_video_count(
        self, mock_analysis_cls, mock_hybrid, director, sample_clip_paths
    ):
        """Hybrid pipeline should receive contract video_count."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [
            COMPLETED_PRESENTER_ANALYSIS,
            COMPLETED_PRODUCT_ANALYSIS,
        ]
        mock_analysis_cls.return_value = mock_service
        mock_hybrid.return_value = ("/output/output-1.mp4", True)

        director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-001", "asset-002"],
            video_count=3,
            director_prompt="剪成3条，保留口播主轴",
        )
        call_kwargs = mock_hybrid.call_args.kwargs
        assert call_kwargs["video_count"] == 3


class TestHybridPromptBrollControl:
    def test_prompt_requests_broll_only_when_explicit(self):
        assert _prompt_requests_broll("请穿插一些B-roll特写镜头")
        assert _prompt_requests_broll("中间插入素材做过渡")
        assert not _prompt_requests_broll("剪成2-3条，关键句放首帧衔接")

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_missing_analysis_triggers_premix_analysis(
        self, mock_analysis_cls, mock_montage, director, sample_clip_paths
    ):
        """Missing analysis should trigger analyze_asset before routing."""
        mock_service = MagicMock()
        mock_service.get_analysis.side_effect = [None, COMPLETED_PRODUCT_ANALYSIS]
        mock_analysis_cls.return_value = mock_service
        mock_montage.return_value = ("/output/output-1.mp4", True)

        director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-002"],
            max_output_duration=60,
        )

        mock_service.analyze_asset.assert_called_once_with("asset-002")
        assert mock_service.get_analysis.call_count == 2

    @patch.object(AIDirectorService, "run_montage_pipeline")
    @patch("app.services.ai_director_service.AssetAnalysisService")
    def test_original_filenames_forwarded_to_vision_pipeline(
        self, mock_analysis_cls, mock_montage, director, sample_clip_paths
    ):
        """Provided original filenames should be forwarded to montage pipeline."""
        mock_service = MagicMock()
        mock_service.get_analysis.return_value = COMPLETED_PRODUCT_ANALYSIS
        mock_analysis_cls.return_value = mock_service
        mock_montage.return_value = ("/output/output-1.mp4", True)

        director.run_auto_pipeline(
            clip_paths=sample_clip_paths,
            asset_ids=["asset-002"],
            clip_original_filenames=["1.mov", "2.mov"],
            max_output_duration=60,
        )

        call_kwargs = mock_montage.call_args.kwargs
        assert call_kwargs["clip_original_filenames"] == ["1.mov", "2.mov"]
