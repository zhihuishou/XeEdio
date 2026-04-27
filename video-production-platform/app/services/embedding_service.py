"""Embedding generation service for semantic search.

Generates embedding vectors from asset description text.
Primary: calls VLM/LLM API embedding endpoint (OpenAI-compatible format).
Fallback: local sentence-transformers model (if available).

Configuration is read from ExternalConfig:
  - embedding.api_url   (dedicated embedding endpoint)
  - embedding.api_key
  - embedding.model
If not set, falls back to VLM config's base URL + /embeddings path.
"""
from __future__ import annotations


import logging
from typing import Optional

import httpx

from app.services.external_config import ExternalConfig

logger = logging.getLogger("app.embedding_service")

# Default embedding model for OpenAI-compatible APIs
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

# HTTP timeout for embedding API calls (seconds)
EMBEDDING_API_TIMEOUT = 30.0


class EmbeddingService:
    """Generate embedding vectors from text for semantic search."""

    def __init__(self) -> None:
        self.config = ExternalConfig.get_instance()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_embedding(self, text: str) -> list[float] | None:
        """Generate an embedding vector from *text*.

        Tries the remote API first; falls back to a local
        sentence-transformers model if the API is unavailable or fails.

        Args:
            text: The input text to embed.

        Returns:
            A list of floats representing the embedding vector,
            or ``None`` if both strategies fail.
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for embedding generation")
            return None

        text = text.strip()

        # Try API first
        embedding = self._generate_via_api(text)
        if embedding is not None:
            return embedding

        # Fallback to local model
        embedding = self._generate_via_local(text)
        if embedding is not None:
            return embedding

        logger.warning("All embedding generation methods failed for text: %s", text[:80])
        return None

    # ------------------------------------------------------------------
    # Private: API-based embedding
    # ------------------------------------------------------------------

    def _get_embedding_config(self) -> dict:
        """Resolve embedding API configuration.

        Priority:
        1. Dedicated ``embedding.*`` config keys.
        2. VLM config base URL with ``/embeddings`` path appended.
        """
        api_url = self.config.get("embedding.api_url", "")
        api_key = self.config.get("embedding.api_key", "")
        model = self.config.get("embedding.model", "")

        if api_url and api_key:
            return {"api_url": api_url, "api_key": api_key, "model": model or DEFAULT_EMBEDDING_MODEL}

        # Fallback: derive from VLM config
        vlm_config = self.config.get_vlm_config()
        vlm_url = vlm_config.get("api_url", "")
        vlm_key = vlm_config.get("api_key", "")

        if vlm_url and vlm_key:
            # Strip trailing path like /chat/completions to get base URL
            base_url = vlm_url
            for suffix in ("/chat/completions", "/completions"):
                if base_url.endswith(suffix):
                    base_url = base_url[: -len(suffix)]
                    break
            # Ensure no trailing slash before appending
            base_url = base_url.rstrip("/")
            embedding_url = f"{base_url}/embeddings"
            return {
                "api_url": embedding_url,
                "api_key": vlm_key,
                "model": model or DEFAULT_EMBEDDING_MODEL,
            }

        return {"api_url": "", "api_key": "", "model": ""}

    def _generate_via_api(self, text: str) -> list[float] | None:
        """Call an OpenAI-compatible embedding endpoint.

        POST {api_url}
        {
            "model": "<model>",
            "input": "<text>"
        }

        Expected response shape::

            {
                "data": [{"embedding": [0.01, -0.02, ...], ...}],
                ...
            }

        Returns:
            Embedding vector as ``list[float]``, or ``None`` on failure.
        """
        cfg = self._get_embedding_config()
        api_url = cfg["api_url"]
        api_key = cfg["api_key"]
        model = cfg["model"]

        if not api_url or not api_key:
            logger.debug("Embedding API not configured, skipping API generation")
            return None

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": model,
            "input": text,
        }

        try:
            with httpx.Client(timeout=EMBEDDING_API_TIMEOUT) as client:
                response = client.post(api_url, json=payload, headers=headers)
                response.raise_for_status()

            data = response.json()
            embedding = data.get("data", [{}])[0].get("embedding")

            if not embedding or not isinstance(embedding, list):
                logger.warning(
                    "Embedding API returned unexpected structure: %s",
                    str(data)[:200],
                )
                return None

            # Ensure all elements are numeric
            result = [float(v) for v in embedding]
            logger.info(
                "Embedding generated via API (%s): dim=%d, text=%s",
                model, len(result), text[:60],
            )
            return result

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Embedding API HTTP error %d: %s",
                exc.response.status_code,
                exc.response.text[:200],
            )
            return None
        except httpx.RequestError as exc:
            logger.warning("Embedding API request error: %s", str(exc)[:200])
            return None
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Failed to parse embedding API response: %s", str(exc)[:200])
            return None

    # ------------------------------------------------------------------
    # Private: Local sentence-transformers fallback
    # ------------------------------------------------------------------

    def _generate_via_local(self, text: str) -> list[float] | None:
        """Generate embedding using a local sentence-transformers model.

        Uses ``paraphrase-multilingual-MiniLM-L12-v2`` which supports
        Chinese text.  The model is loaded lazily on first call.

        Returns:
            Embedding vector as ``list[float]``, or ``None`` if
            sentence-transformers is not installed or loading fails.
        """
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
            vector = model.encode(text)
            result = [float(v) for v in vector]
            logger.info(
                "Embedding generated via local model: dim=%d, text=%s",
                len(result), text[:60],
            )
            return result
        except ImportError:
            logger.debug("sentence-transformers not installed, local fallback unavailable")
            return None
        except Exception as exc:
            logger.warning("Local embedding generation failed: %s", str(exc)[:200])
            return None
