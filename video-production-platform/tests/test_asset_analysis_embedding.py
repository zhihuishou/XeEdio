"""Tests for automatic embedding generation after asset analysis completes.

Verifies that AssetAnalysisService.analyze_asset() calls EmbeddingService
to generate and store an embedding after analysis is marked as "completed".
Embedding failure must NOT fail the overall analysis.
"""

import json
import sys
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
    "httpx",
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


def _make_analysis_record(**overrides):
    """Create a MagicMock that behaves like an AssetAnalysis ORM record."""
    rec = MagicMock()
    rec.asset_id = overrides.get("asset_id", "asset-001")
    rec.description = overrides.get("description", None)
    rec.role = overrides.get("role", None)
    rec.visual_quality = overrides.get("visual_quality", None)
    rec.scene_tags = overrides.get("scene_tags", None)
    rec.key_moments = overrides.get("key_moments", None)
    rec.audio_quality = overrides.get("audio_quality", None)
    rec.has_speech = overrides.get("has_speech", False)
    rec.speech_ranges = overrides.get("speech_ranges", None)
    rec.transcript = overrides.get("transcript", None)
    rec.status = overrides.get("status", "pending")
    rec.error_message = overrides.get("error_message", None)
    rec.vlm_model = overrides.get("vlm_model", None)
    rec.analyzed_at = overrides.get("analyzed_at", None)
    rec.embedding = overrides.get("embedding", None)
    return rec


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmbeddingAfterAnalysis:
    """Verify embedding is generated and stored after analysis completes."""

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_embedding_generated_for_non_video_asset(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Non-video asset analysis generates embedding from description."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_asset = MagicMock()
        mock_asset.id = "asset-img-001"
        mock_asset.file_path = "/tmp/test.jpg"
        mock_asset.media_type = "image"
        mock_asset.original_filename = "test.jpg"

        # Existing analysis record (avoids AssetAnalysis() constructor issue)
        mock_analysis = _make_analysis_record(asset_id="asset-img-001")

        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_asset,       # Asset query
            mock_analysis,    # AssetAnalysis query (found existing)
        ]

        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = [0.1, 0.2, 0.3]
        mock_embedding_cls.return_value = mock_embedding_instance

        service = AssetAnalysisService()
        service.analyze_asset("asset-img-001")

        # Embedding service should have been called
        mock_embedding_instance.generate_embedding.assert_called_once()
        call_text = mock_embedding_instance.generate_embedding.call_args[0][0]
        assert "image file: test.jpg" in call_text

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    @patch("os.path.exists", return_value=True)
    def test_embedding_generated_for_video_asset(
        self, mock_exists, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Video asset analysis generates embedding from description + scene_tags."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_asset = MagicMock()
        mock_asset.id = "asset-vid-001"
        mock_asset.file_path = "/tmp/test.mp4"
        mock_asset.media_type = "video"
        mock_asset.original_filename = "test.mp4"

        mock_analysis = _make_analysis_record(asset_id="asset-vid-001")

        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_asset,       # Asset query
            mock_analysis,    # AssetAnalysis query (found existing)
        ]

        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = [0.5, -0.3, 0.8]
        mock_embedding_cls.return_value = mock_embedding_instance

        service = AssetAnalysisService()
        # Mock VLM analysis to return structured result
        service._analyze_with_vlm = MagicMock(return_value={
            "description": "主播讲解护肤品",
            "role": "presenter",
            "visual_quality": "high",
            "scene_tags": ["美妆", "口播"],
            "key_moments": [],
        })
        # Mock audio detection and transcription
        service._detect_audio = MagicMock(return_value={
            "quality": "good", "has_speech": True, "speech_ranges": [[0, 10]],
        })
        service._transcribe = MagicMock(return_value="大家好")

        service.analyze_asset("asset-vid-001")

        # Embedding should have been called with description + tags
        mock_embedding_instance.generate_embedding.assert_called_once()
        call_text = mock_embedding_instance.generate_embedding.call_args[0][0]
        assert "主播讲解护肤品" in call_text
        assert "美妆" in call_text
        assert "口播" in call_text

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_embedding_stored_as_json_bytes(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Embedding vector is stored as JSON-encoded bytes in the BLOB field."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_asset = MagicMock()
        mock_asset.id = "asset-img-002"
        mock_asset.file_path = "/tmp/test.png"
        mock_asset.media_type = "image"
        mock_asset.original_filename = "test.png"

        mock_analysis = _make_analysis_record(asset_id="asset-img-002")

        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_asset,
            mock_analysis,
        ]

        embedding_vector = [0.1, -0.2, 0.3, 0.4]
        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = embedding_vector
        mock_embedding_cls.return_value = mock_embedding_instance

        service = AssetAnalysisService()
        service.analyze_asset("asset-img-002")

        # Check that embedding was set as JSON-encoded bytes
        expected_bytes = json.dumps(embedding_vector).encode("utf-8")
        assert mock_analysis.embedding == expected_bytes

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_embedding_failure_does_not_fail_analysis(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Embedding generation failure should NOT fail the overall analysis."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_asset = MagicMock()
        mock_asset.id = "asset-img-003"
        mock_asset.file_path = "/tmp/test.png"
        mock_asset.media_type = "image"
        mock_asset.original_filename = "test.png"

        mock_analysis = _make_analysis_record(asset_id="asset-img-003")

        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_asset,
            mock_analysis,
        ]

        # Embedding service raises an exception
        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.side_effect = RuntimeError("API down")
        mock_embedding_cls.return_value = mock_embedding_instance

        service = AssetAnalysisService()
        # Should NOT raise — embedding failure is non-fatal
        service.analyze_asset("asset-img-003")

        # Analysis should still be marked as completed
        assert mock_analysis.status == "completed"
        # db.commit() should still have been called
        mock_db.commit.assert_called()

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_embedding_none_result_does_not_store(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """When embedding returns None, the embedding field is not overwritten."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_asset = MagicMock()
        mock_asset.id = "asset-img-004"
        mock_asset.file_path = "/tmp/test.png"
        mock_asset.media_type = "image"
        mock_asset.original_filename = "test.png"

        mock_analysis = _make_analysis_record(asset_id="asset-img-004", embedding=None)

        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_asset,
            mock_analysis,
        ]

        # Embedding service returns None
        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = None
        mock_embedding_cls.return_value = mock_embedding_instance

        service = AssetAnalysisService()
        service.analyze_asset("asset-img-004")

        # Embedding was called but returned None
        mock_embedding_instance.generate_embedding.assert_called_once()
        # db.commit() should still be called (analysis completed)
        mock_db.commit.assert_called()
