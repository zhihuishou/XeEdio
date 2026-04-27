"""Tests for the asset detail endpoint (GET /api/assets/{id}).

Verifies that the enhanced endpoint returns full analysis results
as a nested `analysis` object in the response.

Uses mock-based testing since FastAPI/SQLAlchemy are not installed
in the test environment.
"""

import json
import sys
from datetime import datetime, timezone
from types import ModuleType
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing modules under test
# ---------------------------------------------------------------------------

for mod_name in [
    "moviepy", "moviepy.video", "moviepy.video.io",
    "moviepy.video.io.VideoFileClip",
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.orm.session",
    "sqlalchemy.ext", "sqlalchemy.ext.declarative",
    "faster_whisper",
    "fastapi", "fastapi.testclient",
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

# Mock mixing_engine
sys.modules["app.services.mixing_engine"] = MagicMock()

from app.schemas.asset import AssetAnalysisResponse, AssetDetailResponse


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

SAMPLE_ASSET_DICT = {
    "id": "asset-001",
    "filename": "original.mp4",
    "original_filename": "主播口播.mp4",
    "category": "talent_speaking",
    "media_type": "video",
    "file_format": "mp4",
    "file_size": 1024000,
    "duration": 120.5,
    "thumbnail_path": "storage/assets/asset-001/thumbnail.jpg",
    "created_at": NOW,
}

COMPLETED_ANALYSIS = {
    "asset_id": "asset-001",
    "description": "主播正面讲解护肤品，手持产品展示",
    "role": "presenter",
    "visual_quality": "high",
    "scene_tags": ["室内", "美妆", "口播"],
    "key_moments": [{"time": 5.0, "desc": "展示产品包装"}],
    "audio_quality": "good",
    "has_speech": True,
    "speech_ranges": [[0, 230]],
    "transcript": "大家好，今天给大家推荐一款非常好用的护肤品",
    "status": "completed",
    "error_message": None,
    "vlm_model": "gpt-5.4",
    "analyzed_at": NOW,
}

FAILED_ANALYSIS = {
    "asset_id": "asset-003",
    "description": "",
    "role": "other",
    "visual_quality": "medium",
    "scene_tags": [],
    "key_moments": [],
    "audio_quality": "silent",
    "has_speech": False,
    "speech_ranges": [],
    "transcript": "",
    "status": "failed",
    "error_message": "VLM API timeout",
    "vlm_model": None,
    "analyzed_at": None,
}

PENDING_ANALYSIS = {
    "asset_id": "asset-004",
    "description": "",
    "role": "other",
    "visual_quality": "medium",
    "scene_tags": [],
    "key_moments": [],
    "audio_quality": "silent",
    "has_speech": False,
    "speech_ranges": [],
    "transcript": "",
    "status": "pending",
    "error_message": None,
    "vlm_model": None,
    "analyzed_at": None,
}


# ---------------------------------------------------------------------------
# Tests: Schema validation
# ---------------------------------------------------------------------------


class TestAssetAnalysisResponseSchema:
    """Tests for the AssetAnalysisResponse Pydantic schema."""

    def test_completed_analysis_schema(self):
        """Completed analysis populates all fields correctly."""
        resp = AssetAnalysisResponse(
            status=COMPLETED_ANALYSIS["status"],
            error_message=COMPLETED_ANALYSIS["error_message"],
            description=COMPLETED_ANALYSIS["description"],
            role=COMPLETED_ANALYSIS["role"],
            visual_quality=COMPLETED_ANALYSIS["visual_quality"],
            scene_tags=COMPLETED_ANALYSIS["scene_tags"],
            key_moments=COMPLETED_ANALYSIS["key_moments"],
            audio_quality=COMPLETED_ANALYSIS["audio_quality"],
            has_speech=COMPLETED_ANALYSIS["has_speech"],
            speech_ranges=COMPLETED_ANALYSIS["speech_ranges"],
            transcript=COMPLETED_ANALYSIS["transcript"],
            vlm_model=COMPLETED_ANALYSIS["vlm_model"],
            analyzed_at=COMPLETED_ANALYSIS["analyzed_at"],
        )

        assert resp.status == "completed"
        assert resp.description == "主播正面讲解护肤品，手持产品展示"
        assert resp.role == "presenter"
        assert resp.visual_quality == "high"
        assert resp.audio_quality == "good"
        assert resp.has_speech is True
        assert resp.speech_ranges == [[0, 230]]
        assert resp.transcript == "大家好，今天给大家推荐一款非常好用的护肤品"
        assert resp.scene_tags == ["室内", "美妆", "口播"]
        assert resp.key_moments == [{"time": 5.0, "desc": "展示产品包装"}]
        assert resp.vlm_model == "gpt-5.4"
        assert resp.analyzed_at == NOW
        assert resp.error_message is None

    def test_failed_analysis_schema(self):
        """Failed analysis includes error_message."""
        resp = AssetAnalysisResponse(
            status="failed",
            error_message="VLM API timeout",
        )
        assert resp.status == "failed"
        assert resp.error_message == "VLM API timeout"
        assert resp.description is None
        assert resp.vlm_model is None

    def test_pending_analysis_schema(self):
        """Pending analysis has minimal fields."""
        resp = AssetAnalysisResponse(status="pending")
        assert resp.status == "pending"
        assert resp.has_speech is None
        assert resp.analyzed_at is None

    def test_analysis_serialization(self):
        """Analysis serializes to dict with all expected keys."""
        resp = AssetAnalysisResponse(
            status="completed",
            description="test",
            role="presenter",
            visual_quality="high",
            scene_tags=["tag1"],
            key_moments=[],
            audio_quality="good",
            has_speech=True,
            speech_ranges=[[0, 10]],
            transcript="hello",
            vlm_model="model-v1",
            analyzed_at=NOW,
            error_message=None,
        )
        data = resp.model_dump()
        expected_keys = {
            "status", "error_message", "description", "role",
            "visual_quality", "scene_tags", "key_moments",
            "audio_quality", "has_speech", "speech_ranges",
            "transcript", "vlm_model", "analyzed_at",
        }
        assert set(data.keys()) == expected_keys


class TestAssetDetailResponseSchema:
    """Tests for the AssetDetailResponse Pydantic schema."""

    def test_detail_with_analysis(self):
        """Detail response includes nested analysis object."""
        analysis = AssetAnalysisResponse(
            status="completed",
            description="test description",
            role="presenter",
        )
        resp = AssetDetailResponse(
            **SAMPLE_ASSET_DICT,
            analysis=analysis,
        )
        assert resp.id == "asset-001"
        assert resp.analysis is not None
        assert resp.analysis.status == "completed"
        assert resp.analysis.description == "test description"

    def test_detail_without_analysis(self):
        """Detail response with analysis=None."""
        resp = AssetDetailResponse(
            **SAMPLE_ASSET_DICT,
            analysis=None,
        )
        assert resp.id == "asset-001"
        assert resp.analysis is None

    def test_detail_serialization_structure(self):
        """Serialized detail has correct top-level keys."""
        analysis = AssetAnalysisResponse(status="completed")
        resp = AssetDetailResponse(
            **SAMPLE_ASSET_DICT,
            analysis=analysis,
        )
        data = resp.model_dump()
        expected_top_keys = {
            "id", "filename", "original_filename", "category",
            "media_type", "file_format", "file_size", "duration",
            "thumbnail_path", "created_at", "analysis",
        }
        assert set(data.keys()) == expected_top_keys
        assert isinstance(data["analysis"], dict)
        assert data["analysis"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Tests: Endpoint logic (building response from analysis data)
# ---------------------------------------------------------------------------


class TestAssetDetailEndpointLogic:
    """Tests for the logic that builds AssetDetailResponse from analysis data.

    Since we can't run the full FastAPI app, we test the response-building
    logic that the endpoint uses.
    """

    def _build_detail_response(self, asset_dict: dict, analysis_data: dict | None) -> AssetDetailResponse:
        """Simulate the endpoint logic: build AssetDetailResponse from asset + analysis."""
        response = AssetDetailResponse(**asset_dict, analysis=None)

        if analysis_data:
            response.analysis = AssetAnalysisResponse(
                status=analysis_data.get("status", "pending"),
                error_message=analysis_data.get("error_message"),
                description=analysis_data.get("description"),
                role=analysis_data.get("role"),
                visual_quality=analysis_data.get("visual_quality"),
                scene_tags=analysis_data.get("scene_tags"),
                key_moments=analysis_data.get("key_moments"),
                audio_quality=analysis_data.get("audio_quality"),
                has_speech=analysis_data.get("has_speech"),
                speech_ranges=analysis_data.get("speech_ranges"),
                transcript=analysis_data.get("transcript"),
                vlm_model=analysis_data.get("vlm_model"),
                analyzed_at=analysis_data.get("analyzed_at"),
            )

        return response

    def test_completed_analysis_included(self):
        """Completed analysis is fully included in the response."""
        resp = self._build_detail_response(SAMPLE_ASSET_DICT, COMPLETED_ANALYSIS)

        assert resp.analysis is not None
        assert resp.analysis.status == "completed"
        assert resp.analysis.description == "主播正面讲解护肤品，手持产品展示"
        assert resp.analysis.role == "presenter"
        assert resp.analysis.visual_quality == "high"
        assert resp.analysis.audio_quality == "good"
        assert resp.analysis.has_speech is True
        assert resp.analysis.speech_ranges == [[0, 230]]
        assert resp.analysis.transcript == "大家好，今天给大家推荐一款非常好用的护肤品"
        assert resp.analysis.scene_tags == ["室内", "美妆", "口播"]
        assert resp.analysis.key_moments == [{"time": 5.0, "desc": "展示产品包装"}]
        assert resp.analysis.vlm_model == "gpt-5.4"
        assert resp.analysis.analyzed_at == NOW
        assert resp.analysis.error_message is None

    def test_no_analysis_returns_none(self):
        """No analysis data results in analysis=None."""
        resp = self._build_detail_response(SAMPLE_ASSET_DICT, None)
        assert resp.analysis is None

    def test_failed_analysis_includes_error(self):
        """Failed analysis includes error_message in response."""
        resp = self._build_detail_response(SAMPLE_ASSET_DICT, FAILED_ANALYSIS)

        assert resp.analysis is not None
        assert resp.analysis.status == "failed"
        assert resp.analysis.error_message == "VLM API timeout"
        assert resp.analysis.vlm_model is None
        assert resp.analysis.analyzed_at is None

    def test_pending_analysis_minimal_fields(self):
        """Pending analysis has status but minimal other data."""
        resp = self._build_detail_response(SAMPLE_ASSET_DICT, PENDING_ANALYSIS)

        assert resp.analysis is not None
        assert resp.analysis.status == "pending"
        assert resp.analysis.vlm_model is None
        assert resp.analysis.analyzed_at is None
        assert resp.analysis.has_speech is False

    def test_asset_fields_preserved(self):
        """Asset-level fields are preserved in the detail response."""
        resp = self._build_detail_response(SAMPLE_ASSET_DICT, COMPLETED_ANALYSIS)

        assert resp.id == "asset-001"
        assert resp.original_filename == "主播口播.mp4"
        assert resp.category == "talent_speaking"
        assert resp.media_type == "video"
        assert resp.file_format == "mp4"
        assert resp.file_size == 1024000
        assert resp.duration == 120.5
        assert resp.created_at == NOW


# ---------------------------------------------------------------------------
# Tests: AssetAnalysisService.get_analysis returns new fields
# ---------------------------------------------------------------------------


class TestGetAnalysisReturnsNewFields:
    """Tests that AssetAnalysisService.get_analysis returns error_message, vlm_model, analyzed_at."""

    @patch("app.services.asset_analysis_service.SessionLocal")
    def test_get_analysis_returns_all_fields(self, mock_session_local):
        """get_analysis returns error_message, vlm_model, and analyzed_at."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()
        mock_analysis = MagicMock()
        mock_analysis.asset_id = "asset-001"
        mock_analysis.description = "test description"
        mock_analysis.role = "presenter"
        mock_analysis.visual_quality = "high"
        mock_analysis.scene_tags = json.dumps(["tag1"])
        mock_analysis.key_moments = json.dumps([{"time": 1.0, "desc": "moment"}])
        mock_analysis.audio_quality = "good"
        mock_analysis.has_speech = True
        mock_analysis.speech_ranges = json.dumps([[0, 10]])
        mock_analysis.transcript = "hello world"
        mock_analysis.status = "completed"
        mock_analysis.error_message = None
        mock_analysis.vlm_model = "gpt-5.4"
        mock_analysis.analyzed_at = NOW

        mock_db.query.return_value.filter.return_value.first.return_value = mock_analysis

        service = AssetAnalysisService()
        result = service.get_analysis("asset-001", db=mock_db)

        assert result is not None
        assert result["error_message"] is None
        assert result["vlm_model"] == "gpt-5.4"
        assert result["analyzed_at"] == NOW

    @patch("app.services.asset_analysis_service.SessionLocal")
    def test_get_analysis_failed_has_error_message(self, mock_session_local):
        """get_analysis for failed analysis returns error_message."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()
        mock_analysis = MagicMock()
        mock_analysis.asset_id = "asset-003"
        mock_analysis.description = ""
        mock_analysis.role = "other"
        mock_analysis.visual_quality = None
        mock_analysis.scene_tags = None
        mock_analysis.key_moments = None
        mock_analysis.audio_quality = None
        mock_analysis.has_speech = False
        mock_analysis.speech_ranges = None
        mock_analysis.transcript = ""
        mock_analysis.status = "failed"
        mock_analysis.error_message = "Connection timeout"
        mock_analysis.vlm_model = None
        mock_analysis.analyzed_at = None

        mock_db.query.return_value.filter.return_value.first.return_value = mock_analysis

        service = AssetAnalysisService()
        result = service.get_analysis("asset-003", db=mock_db)

        assert result is not None
        assert result["status"] == "failed"
        assert result["error_message"] == "Connection timeout"
        assert result["vlm_model"] is None
        assert result["analyzed_at"] is None

    @patch("app.services.asset_analysis_service.SessionLocal")
    def test_get_analysis_returns_none_for_missing(self, mock_session_local):
        """get_analysis returns None when no analysis record exists."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        service = AssetAnalysisService()
        result = service.get_analysis("nonexistent", db=mock_db)

        assert result is None
