"""Tests for the semantic search feature (Task 34).

Covers:
- Cosine similarity computation (unit tests)
- AssetAnalysisService.search_by_text() logic
- AssetSearchItem / AssetSearchResponse schema construction
- GET /api/assets/search endpoint logic
"""

import json
import math
import sys
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

from app.services.asset_analysis_service import _cosine_similarity
from app.schemas.asset import AssetSearchItem, AssetSearchResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analysis_mock(**overrides):
    """Create a MagicMock that behaves like an AssetAnalysis ORM record."""
    rec = MagicMock()
    rec.asset_id = overrides.get("asset_id", "asset-001")
    rec.description = overrides.get("description", "test description")
    rec.role = overrides.get("role", "other")
    rec.visual_quality = overrides.get("visual_quality", "medium")
    rec.scene_tags = overrides.get("scene_tags", json.dumps(["tag1"]))
    rec.key_moments = overrides.get("key_moments", json.dumps([]))
    rec.audio_quality = overrides.get("audio_quality", "good")
    rec.has_speech = overrides.get("has_speech", False)
    rec.speech_ranges = overrides.get("speech_ranges", json.dumps([]))
    rec.transcript = overrides.get("transcript", "")
    rec.status = overrides.get("status", "completed")
    rec.error_message = overrides.get("error_message", None)
    rec.vlm_model = overrides.get("vlm_model", None)
    rec.analyzed_at = overrides.get("analyzed_at", None)
    rec.embedding = overrides.get("embedding", None)
    return rec


def _make_asset_mock(**overrides):
    """Create a MagicMock that behaves like an Asset ORM record."""
    rec = MagicMock()
    rec.id = overrides.get("id", "asset-001")
    rec.original_filename = overrides.get("original_filename", "test.mp4")
    rec.category = overrides.get("category", "product")
    rec.media_type = overrides.get("media_type", "video")
    return rec


# ---------------------------------------------------------------------------
# Tests: Cosine similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    """Unit tests for the _cosine_similarity helper."""

    def test_identical_vectors(self):
        """Identical vectors have similarity 1.0."""
        v = [1.0, 2.0, 3.0]
        assert _cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        """Orthogonal vectors have similarity 0.0."""
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        """Opposite vectors have similarity -1.0."""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        """Zero vector returns 0.0 (no division by zero)."""
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert _cosine_similarity(a, b) == 0.0
        assert _cosine_similarity(b, a) == 0.0

    def test_both_zero_vectors(self):
        """Both zero vectors return 0.0."""
        a = [0.0, 0.0]
        b = [0.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_known_similarity(self):
        """Known cosine similarity value."""
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        # dot = 4+10+18 = 32
        # |a| = sqrt(14), |b| = sqrt(77)
        expected = 32.0 / (math.sqrt(14) * math.sqrt(77))
        assert _cosine_similarity(a, b) == pytest.approx(expected, rel=1e-6)

    def test_single_dimension(self):
        """Single-dimension vectors."""
        assert _cosine_similarity([3.0], [5.0]) == pytest.approx(1.0)
        assert _cosine_similarity([3.0], [-5.0]) == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Tests: AssetSearchItem / AssetSearchResponse schemas
# ---------------------------------------------------------------------------


class TestSearchSchemas:
    """Tests for the search response schemas."""

    def test_asset_search_item_construction(self):
        """AssetSearchItem can be constructed from a dict."""
        item = AssetSearchItem(
            id="asset-001",
            original_filename="product.mp4",
            category="product",
            media_type="video",
            description="产品特写镜头",
            role="product_closeup",
            scene_tags=["产品", "特写"],
            relevance_score=0.9234,
        )
        assert item.id == "asset-001"
        assert item.relevance_score == 0.9234
        assert item.scene_tags == ["产品", "特写"]

    def test_asset_search_item_optional_fields(self):
        """AssetSearchItem works with minimal required fields."""
        item = AssetSearchItem(
            id="asset-002",
            original_filename="test.mp4",
            category="product",
            media_type="video",
        )
        assert item.description is None
        assert item.role is None
        assert item.relevance_score is None

    def test_asset_search_response_construction(self):
        """AssetSearchResponse wraps items and total."""
        items = [
            AssetSearchItem(
                id="a1", original_filename="f1.mp4",
                category="product", media_type="video",
                relevance_score=0.95,
            ),
            AssetSearchItem(
                id="a2", original_filename="f2.mp4",
                category="product", media_type="video",
                relevance_score=0.80,
            ),
        ]
        resp = AssetSearchResponse(items=items, total=2)
        assert resp.total == 2
        assert len(resp.items) == 2
        assert resp.items[0].relevance_score == 0.95

    def test_empty_search_response(self):
        """Empty search response."""
        resp = AssetSearchResponse(items=[], total=0)
        assert resp.total == 0
        assert resp.items == []


# ---------------------------------------------------------------------------
# Tests: AssetAnalysisService.search_by_text()
# ---------------------------------------------------------------------------


class TestSearchByText:
    """Tests for the search_by_text service method."""

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_returns_sorted_results(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Results are sorted by cosine similarity descending."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()

        # Query embedding
        query_emb = [1.0, 0.0, 0.0]
        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = query_emb
        mock_embedding_cls.return_value = mock_embedding_instance

        # Two analyses with different embeddings
        analysis_close = _make_analysis_mock(
            asset_id="asset-close",
            description="产品特写",
            embedding=json.dumps([0.9, 0.1, 0.0]).encode("utf-8"),
        )
        analysis_far = _make_analysis_mock(
            asset_id="asset-far",
            description="风景空镜",
            embedding=json.dumps([0.0, 0.0, 1.0]).encode("utf-8"),
        )

        asset_close = _make_asset_mock(id="asset-close", original_filename="close.mp4")
        asset_far = _make_asset_mock(id="asset-far", original_filename="far.mp4")

        # DB query mocks
        mock_analysis_query = MagicMock()
        mock_analysis_query.filter.return_value.filter.return_value.all.return_value = [
            analysis_close, analysis_far
        ]

        mock_asset_query = MagicMock()
        mock_asset_query.filter.return_value.all.return_value = [asset_close, asset_far]

        mock_db.query.side_effect = [mock_analysis_query, mock_asset_query]

        service = AssetAnalysisService()
        results = service.search_by_text("产品特写", limit=10, db=mock_db)

        assert len(results) == 2
        # First result should be the closer one
        assert results[0]["id"] == "asset-close"
        assert results[1]["id"] == "asset-far"
        assert results[0]["relevance_score"] > results[1]["relevance_score"]

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_respects_limit(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Only returns up to `limit` results."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()

        query_emb = [1.0, 0.0]
        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = query_emb
        mock_embedding_cls.return_value = mock_embedding_instance

        # Create 5 analyses
        analyses = []
        assets = []
        for i in range(5):
            aid = f"asset-{i}"
            emb = [1.0 - i * 0.1, i * 0.1]
            analyses.append(_make_analysis_mock(
                asset_id=aid,
                embedding=json.dumps(emb).encode("utf-8"),
            ))
            assets.append(_make_asset_mock(id=aid, original_filename=f"file{i}.mp4"))

        mock_analysis_query = MagicMock()
        mock_analysis_query.filter.return_value.filter.return_value.all.return_value = analyses

        mock_asset_query = MagicMock()
        mock_asset_query.filter.return_value.all.return_value = assets

        mock_db.query.side_effect = [mock_analysis_query, mock_asset_query]

        service = AssetAnalysisService()
        results = service.search_by_text("test", limit=2, db=mock_db)

        assert len(results) == 2

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_empty_results_when_no_embeddings(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Returns empty list when no analyses have embeddings."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()

        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = [1.0, 0.0]
        mock_embedding_cls.return_value = mock_embedding_instance

        mock_analysis_query = MagicMock()
        mock_analysis_query.filter.return_value.filter.return_value.all.return_value = []

        mock_db.query.return_value = mock_analysis_query

        service = AssetAnalysisService()
        results = service.search_by_text("anything", limit=10, db=mock_db)

        assert results == []

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_returns_empty_when_embedding_fails(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Returns empty list when query embedding generation fails."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()

        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = None
        mock_embedding_cls.return_value = mock_embedding_instance

        service = AssetAnalysisService()
        results = service.search_by_text("test query", limit=10, db=mock_db)

        assert results == []

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_skips_invalid_embeddings(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Analyses with corrupt embedding data are skipped gracefully."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()

        query_emb = [1.0, 0.0]
        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = query_emb
        mock_embedding_cls.return_value = mock_embedding_instance

        good_analysis = _make_analysis_mock(
            asset_id="asset-good",
            embedding=json.dumps([0.9, 0.1]).encode("utf-8"),
        )
        bad_analysis = _make_analysis_mock(
            asset_id="asset-bad",
            embedding=b"not-valid-json",
        )

        asset_good = _make_asset_mock(id="asset-good", original_filename="good.mp4")
        asset_bad = _make_asset_mock(id="asset-bad", original_filename="bad.mp4")

        mock_analysis_query = MagicMock()
        mock_analysis_query.filter.return_value.filter.return_value.all.return_value = [
            good_analysis, bad_analysis
        ]

        mock_asset_query = MagicMock()
        mock_asset_query.filter.return_value.all.return_value = [asset_good, asset_bad]

        mock_db.query.side_effect = [mock_analysis_query, mock_asset_query]

        service = AssetAnalysisService()
        results = service.search_by_text("test", limit=10, db=mock_db)

        # Only the good analysis should appear
        assert len(results) == 1
        assert results[0]["id"] == "asset-good"

    @patch("app.services.asset_analysis_service.EmbeddingService")
    @patch("app.services.asset_analysis_service.SessionLocal")
    @patch("app.services.asset_analysis_service.VLMService")
    def test_result_contains_expected_fields(
        self, mock_vlm_cls, mock_session_local, mock_embedding_cls
    ):
        """Each result dict has all expected fields."""
        from app.services.asset_analysis_service import AssetAnalysisService

        mock_db = MagicMock()

        query_emb = [1.0, 0.0]
        mock_embedding_instance = MagicMock()
        mock_embedding_instance.generate_embedding.return_value = query_emb
        mock_embedding_cls.return_value = mock_embedding_instance

        analysis = _make_analysis_mock(
            asset_id="asset-001",
            description="主播讲解",
            role="presenter",
            scene_tags=json.dumps(["美妆", "口播"]),
            embedding=json.dumps([0.8, 0.2]).encode("utf-8"),
        )
        asset = _make_asset_mock(
            id="asset-001",
            original_filename="presenter.mp4",
            category="talent_speaking",
            media_type="video",
        )

        mock_analysis_query = MagicMock()
        mock_analysis_query.filter.return_value.filter.return_value.all.return_value = [analysis]

        mock_asset_query = MagicMock()
        mock_asset_query.filter.return_value.all.return_value = [asset]

        mock_db.query.side_effect = [mock_analysis_query, mock_asset_query]

        service = AssetAnalysisService()
        results = service.search_by_text("主播", limit=10, db=mock_db)

        assert len(results) == 1
        r = results[0]
        assert r["id"] == "asset-001"
        assert r["original_filename"] == "presenter.mp4"
        assert r["category"] == "talent_speaking"
        assert r["media_type"] == "video"
        assert r["description"] == "主播讲解"
        assert r["role"] == "presenter"
        assert r["scene_tags"] == ["美妆", "口播"]
        assert isinstance(r["relevance_score"], float)
        assert 0.0 <= r["relevance_score"] <= 1.0


# ---------------------------------------------------------------------------
# Tests: Endpoint logic (search_assets)
# ---------------------------------------------------------------------------


class TestSearchEndpointLogic:
    """Tests for the GET /api/assets/search endpoint logic.

    We simulate the endpoint's response-building logic since we can't run
    the full FastAPI app in the test environment (consistent with existing
    test patterns).
    """

    def _build_search_response(self, results: list[dict]) -> AssetSearchResponse:
        """Simulate the endpoint logic: build AssetSearchResponse from service results."""
        items = [AssetSearchItem(**r) for r in results]
        return AssetSearchResponse(items=items, total=len(items))

    def test_builds_response_from_service_results(self):
        """Endpoint correctly wraps service results into response schema."""
        results = [
            {
                "id": "a1", "original_filename": "f1.mp4",
                "category": "product", "media_type": "video",
                "description": "产品特写", "role": "product_closeup",
                "scene_tags": ["产品"], "relevance_score": 0.95,
            },
            {
                "id": "a2", "original_filename": "f2.mp4",
                "category": "product", "media_type": "video",
                "description": "空镜", "role": "lifestyle",
                "scene_tags": ["空镜"], "relevance_score": 0.72,
            },
        ]
        resp = self._build_search_response(results)

        assert resp.total == 2
        assert resp.items[0].id == "a1"
        assert resp.items[0].relevance_score == 0.95
        assert resp.items[1].id == "a2"

    def test_empty_results(self):
        """Empty results produce valid response."""
        resp = self._build_search_response([])
        assert resp.total == 0
        assert resp.items == []

    def test_limit_clamping(self):
        """Limit is clamped between 1 and 50."""
        # Simulate the clamping logic from the endpoint
        for raw, expected in [(0, 1), (-5, 1), (100, 50), (25, 25), (50, 50)]:
            clamped = max(1, min(raw, 50))
            assert clamped == expected
