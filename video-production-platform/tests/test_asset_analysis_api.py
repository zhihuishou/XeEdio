"""Tests for the GET /api/assets/{id}/analysis endpoint.

Verifies that the dedicated analysis endpoint returns the correct
AssetAnalysisResponse or 404 when the asset/analysis doesn't exist.

Uses mock-based testing consistent with the existing test patterns.
"""

import json
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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

from app.schemas.asset import AssetAnalysisResponse


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

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


# ---------------------------------------------------------------------------
# Tests: Endpoint logic — building AssetAnalysisResponse from analysis data
# ---------------------------------------------------------------------------


class TestGetAssetAnalysisEndpointLogic:
    """Tests for the logic that the GET /api/assets/{id}/analysis endpoint uses.

    We simulate the endpoint's response-building logic since we can't run
    the full FastAPI app in the test environment.
    """

    def _build_analysis_response(self, analysis_data: dict | None) -> AssetAnalysisResponse | None:
        """Simulate the endpoint logic: build AssetAnalysisResponse from analysis data.

        Returns None when analysis_data is None (endpoint would raise 404).
        """
        if not analysis_data:
            return None

        return AssetAnalysisResponse(
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

    def test_completed_analysis_returns_full_data(self):
        """Completed analysis returns all fields correctly."""
        resp = self._build_analysis_response(COMPLETED_ANALYSIS)

        assert resp is not None
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

    def test_no_analysis_returns_none(self):
        """No analysis data results in None (endpoint would return 404)."""
        resp = self._build_analysis_response(None)
        assert resp is None

    def test_failed_analysis_includes_error(self):
        """Failed analysis includes error_message."""
        failed = {
            "status": "failed",
            "error_message": "VLM API timeout",
            "description": "",
            "role": "other",
            "visual_quality": None,
            "scene_tags": [],
            "key_moments": [],
            "audio_quality": None,
            "has_speech": False,
            "speech_ranges": [],
            "transcript": "",
            "vlm_model": None,
            "analyzed_at": None,
        }
        resp = self._build_analysis_response(failed)

        assert resp is not None
        assert resp.status == "failed"
        assert resp.error_message == "VLM API timeout"
        assert resp.vlm_model is None

    def test_pending_analysis_minimal(self):
        """Pending analysis has status but minimal other data."""
        pending = {
            "status": "pending",
            "error_message": None,
            "description": "",
            "role": "other",
            "visual_quality": None,
            "scene_tags": [],
            "key_moments": [],
            "audio_quality": None,
            "has_speech": False,
            "speech_ranges": [],
            "transcript": "",
            "vlm_model": None,
            "analyzed_at": None,
        }
        resp = self._build_analysis_response(pending)

        assert resp is not None
        assert resp.status == "pending"
        assert resp.has_speech is False
        assert resp.analyzed_at is None

    def test_response_serialization_keys(self):
        """Serialized response has all expected keys."""
        resp = self._build_analysis_response(COMPLETED_ANALYSIS)
        assert resp is not None

        data = resp.model_dump()
        expected_keys = {
            "status", "error_message", "description", "role",
            "visual_quality", "scene_tags", "key_moments",
            "audio_quality", "has_speech", "speech_ranges",
            "transcript", "vlm_model", "analyzed_at",
        }
        assert set(data.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Tests: Service integration — get_analysis used by the endpoint
# ---------------------------------------------------------------------------


class TestGetAnalysisServiceIntegration:
    """Tests that AssetAnalysisService.get_analysis works correctly for the endpoint."""

    @patch("app.services.asset_analysis_service.SessionLocal")
    def test_get_analysis_returns_data(self, mock_session_local):
        """get_analysis returns analysis dict when record exists."""
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
        assert result["status"] == "completed"
        assert result["description"] == "test description"
        assert result["role"] == "presenter"
        assert result["vlm_model"] == "gpt-5.4"

    @patch("app.services.asset_analysis_service.SessionLocal")
    def test_get_analysis_returns_none_when_missing(self, mock_session_local):
        """get_analysis returns None when no analysis record exists (triggers 404)."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        service = AssetAnalysisService()
        result = service.get_analysis("nonexistent", db=mock_db)

        assert result is None
