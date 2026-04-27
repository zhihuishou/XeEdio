"""Unit tests for EmbeddingService.

Tests:
- generate_embedding: empty/blank text → None
- generate_embedding: API success → returns vector
- generate_embedding: API fails, local fallback succeeds
- generate_embedding: both strategies fail → None
- _generate_via_api: correct payload and headers
- _generate_via_api: HTTP error → None
- _generate_via_api: request error → None
- _generate_via_api: malformed response → None
- _generate_via_api: no config → None
- _generate_via_local: sentence-transformers not installed → None
- _get_embedding_config: dedicated config takes priority
- _get_embedding_config: falls back to VLM config base URL
"""

import json
import sys
from unittest.mock import MagicMock, patch, PropertyMock

import httpx
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

from app.services.embedding_service import EmbeddingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_EMBEDDING = [0.01, -0.02, 0.03, 0.04, -0.05]


def _make_api_response(embedding: list[float] | None = None, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response for embedding API."""
    body = {"data": [{"embedding": embedding or SAMPLE_EMBEDDING}]}
    resp = httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("POST", "https://api.example.com/v1/embeddings"),
    )
    return resp


def _make_service(
    embedding_api_url: str = "",
    embedding_api_key: str = "",
    embedding_model: str = "",
    vlm_api_url: str = "",
    vlm_api_key: str = "",
) -> EmbeddingService:
    """Create an EmbeddingService with mocked config."""
    with patch("app.services.embedding_service.ExternalConfig") as mock_cls:
        mock_config = MagicMock()

        def _get(key, default=None):
            mapping = {
                "embedding.api_url": embedding_api_url,
                "embedding.api_key": embedding_api_key,
                "embedding.model": embedding_model,
            }
            return mapping.get(key, default)

        mock_config.get.side_effect = _get
        mock_config.get_vlm_config.return_value = {
            "api_url": vlm_api_url,
            "api_key": vlm_api_key,
            "model": "gpt-5.4",
        }
        mock_cls.get_instance.return_value = mock_config
        svc = EmbeddingService()
    return svc


# ---------------------------------------------------------------------------
# Tests: generate_embedding — top-level orchestration
# ---------------------------------------------------------------------------

class TestGenerateEmbedding:
    """Tests for the public generate_embedding method."""

    def test_empty_text_returns_none(self):
        svc = _make_service()
        assert svc.generate_embedding("") is None
        assert svc.generate_embedding("   ") is None

    def test_none_text_returns_none(self):
        svc = _make_service()
        # None is falsy, should be caught
        assert svc.generate_embedding(None) is None

    @patch.object(EmbeddingService, "_generate_via_api")
    @patch.object(EmbeddingService, "_generate_via_local")
    def test_api_success_skips_local(self, mock_local, mock_api):
        """When API succeeds, local fallback is not called."""
        mock_api.return_value = SAMPLE_EMBEDDING
        svc = _make_service()

        result = svc.generate_embedding("test text")

        assert result == SAMPLE_EMBEDDING
        mock_api.assert_called_once_with("test text")
        mock_local.assert_not_called()

    @patch.object(EmbeddingService, "_generate_via_api")
    @patch.object(EmbeddingService, "_generate_via_local")
    def test_api_fails_falls_back_to_local(self, mock_local, mock_api):
        """When API returns None, local fallback is tried."""
        mock_api.return_value = None
        mock_local.return_value = [0.1, 0.2, 0.3]
        svc = _make_service()

        result = svc.generate_embedding("test text")

        assert result == [0.1, 0.2, 0.3]
        mock_api.assert_called_once()
        mock_local.assert_called_once_with("test text")

    @patch.object(EmbeddingService, "_generate_via_api")
    @patch.object(EmbeddingService, "_generate_via_local")
    def test_both_fail_returns_none(self, mock_local, mock_api):
        """When both strategies fail, returns None."""
        mock_api.return_value = None
        mock_local.return_value = None
        svc = _make_service()

        result = svc.generate_embedding("test text")
        assert result is None

    @patch.object(EmbeddingService, "_generate_via_api")
    def test_strips_whitespace_before_calling(self, mock_api):
        """Leading/trailing whitespace is stripped before embedding."""
        mock_api.return_value = SAMPLE_EMBEDDING
        svc = _make_service()

        svc.generate_embedding("  hello world  ")
        mock_api.assert_called_once_with("hello world")


# ---------------------------------------------------------------------------
# Tests: _get_embedding_config
# ---------------------------------------------------------------------------

class TestGetEmbeddingConfig:
    """Tests for config resolution logic."""

    def test_dedicated_config_takes_priority(self):
        svc = _make_service(
            embedding_api_url="https://embed.example.com/v1/embeddings",
            embedding_api_key="embed-key",
            embedding_model="custom-model",
            vlm_api_url="https://vlm.example.com/v1/chat/completions",
            vlm_api_key="vlm-key",
        )
        cfg = svc._get_embedding_config()
        assert cfg["api_url"] == "https://embed.example.com/v1/embeddings"
        assert cfg["api_key"] == "embed-key"
        assert cfg["model"] == "custom-model"

    def test_falls_back_to_vlm_config(self):
        svc = _make_service(
            vlm_api_url="https://api.luxee.ai/v1/chat/completions",
            vlm_api_key="vlm-key-123",
        )
        cfg = svc._get_embedding_config()
        assert cfg["api_url"] == "https://api.luxee.ai/v1/embeddings"
        assert cfg["api_key"] == "vlm-key-123"
        assert cfg["model"] == "text-embedding-3-small"

    def test_vlm_url_without_chat_completions_suffix(self):
        """VLM URL that doesn't end with /chat/completions."""
        svc = _make_service(
            vlm_api_url="https://api.example.com/v1/completions",
            vlm_api_key="key",
        )
        cfg = svc._get_embedding_config()
        assert cfg["api_url"] == "https://api.example.com/v1/embeddings"

    def test_no_config_returns_empty(self):
        svc = _make_service()
        cfg = svc._get_embedding_config()
        assert cfg["api_url"] == ""
        assert cfg["api_key"] == ""

    def test_dedicated_url_without_key_falls_back_to_vlm(self):
        """Dedicated URL set but no key → falls back to VLM."""
        svc = _make_service(
            embedding_api_url="https://embed.example.com/v1/embeddings",
            embedding_api_key="",
            vlm_api_url="https://vlm.example.com/v1/chat/completions",
            vlm_api_key="vlm-key",
        )
        cfg = svc._get_embedding_config()
        # Falls back because api_key is empty
        assert cfg["api_url"] == "https://vlm.example.com/v1/embeddings"
        assert cfg["api_key"] == "vlm-key"

    def test_default_model_when_not_specified(self):
        svc = _make_service(
            embedding_api_url="https://embed.example.com/v1/embeddings",
            embedding_api_key="key",
        )
        cfg = svc._get_embedding_config()
        assert cfg["model"] == "text-embedding-3-small"


# ---------------------------------------------------------------------------
# Tests: _generate_via_api
# ---------------------------------------------------------------------------

class TestGenerateViaApi:
    """Tests for the API-based embedding generation."""

    @patch("app.services.embedding_service.httpx.Client")
    def test_successful_api_call(self, mock_client_cls):
        """Successful API call returns embedding vector."""
        mock_response = _make_api_response(SAMPLE_EMBEDDING)
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        svc = _make_service(
            embedding_api_url="https://api.example.com/v1/embeddings",
            embedding_api_key="test-key",
            embedding_model="text-embedding-3-small",
        )
        result = svc._generate_via_api("hello world")

        assert result == SAMPLE_EMBEDDING
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://api.example.com/v1/embeddings"
        payload = call_args[1]["json"]
        assert payload["model"] == "text-embedding-3-small"
        assert payload["input"] == "hello world"
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer test-key"

    def test_no_config_returns_none(self):
        """No API config → returns None without making request."""
        svc = _make_service()
        result = svc._generate_via_api("hello")
        assert result is None

    @patch("app.services.embedding_service.httpx.Client")
    def test_http_error_returns_none(self, mock_client_cls):
        """HTTP 4xx/5xx → returns None."""
        mock_response = httpx.Response(
            status_code=401,
            text="Unauthorized",
            request=httpx.Request("POST", "https://api.example.com/v1/embeddings"),
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        svc = _make_service(
            embedding_api_url="https://api.example.com/v1/embeddings",
            embedding_api_key="bad-key",
        )
        result = svc._generate_via_api("hello")
        assert result is None

    @patch("app.services.embedding_service.httpx.Client")
    def test_request_error_returns_none(self, mock_client_cls):
        """Network/connection error → returns None."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value = mock_client

        svc = _make_service(
            embedding_api_url="https://api.example.com/v1/embeddings",
            embedding_api_key="key",
        )
        result = svc._generate_via_api("hello")
        assert result is None

    @patch("app.services.embedding_service.httpx.Client")
    def test_malformed_response_returns_none(self, mock_client_cls):
        """Response missing 'data' or 'embedding' → returns None."""
        mock_response = httpx.Response(
            status_code=200,
            json={"result": "unexpected"},
            request=httpx.Request("POST", "https://api.example.com/v1/embeddings"),
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        svc = _make_service(
            embedding_api_url="https://api.example.com/v1/embeddings",
            embedding_api_key="key",
        )
        result = svc._generate_via_api("hello")
        assert result is None

    @patch("app.services.embedding_service.httpx.Client")
    def test_empty_embedding_list_returns_none(self, mock_client_cls):
        """Response with empty embedding list → returns None."""
        mock_response = httpx.Response(
            status_code=200,
            json={"data": [{"embedding": []}]},
            request=httpx.Request("POST", "https://api.example.com/v1/embeddings"),
        )
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        svc = _make_service(
            embedding_api_url="https://api.example.com/v1/embeddings",
            embedding_api_key="key",
        )
        result = svc._generate_via_api("hello")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _generate_via_local
# ---------------------------------------------------------------------------

class TestGenerateViaLocal:
    """Tests for the local sentence-transformers fallback."""

    @patch("app.services.embedding_service.EmbeddingService._generate_via_api")
    def test_import_error_returns_none(self, mock_api):
        """sentence-transformers not installed → returns None."""
        mock_api.return_value = None
        svc = _make_service()

        # Patch the import inside _generate_via_local to raise ImportError
        import builtins
        original_import = builtins.__import__

        def _mock_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("No module named 'sentence_transformers'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_mock_import):
            result = svc._generate_via_local("hello")

        assert result is None

    def test_successful_local_generation(self):
        """Mock sentence-transformers to return a vector."""
        svc = _make_service()

        mock_model_instance = MagicMock()
        mock_model_instance.encode.return_value = [0.1, 0.2, 0.3]

        mock_st_module = MagicMock()
        mock_st_module.SentenceTransformer.return_value = mock_model_instance

        with patch.dict(sys.modules, {"sentence_transformers": mock_st_module}):
            result = svc._generate_via_local("hello world")

        assert result == [0.1, 0.2, 0.3]
        mock_st_module.SentenceTransformer.assert_called_once_with(
            "paraphrase-multilingual-MiniLM-L12-v2"
        )
        mock_model_instance.encode.assert_called_once_with("hello world")

    def test_model_exception_returns_none(self):
        """Model loading/encoding exception → returns None."""
        svc = _make_service()

        mock_st_module = MagicMock()
        mock_st_module.SentenceTransformer.side_effect = RuntimeError("CUDA error")

        with patch.dict(sys.modules, {"sentence_transformers": mock_st_module}):
            result = svc._generate_via_local("hello")

        assert result is None
