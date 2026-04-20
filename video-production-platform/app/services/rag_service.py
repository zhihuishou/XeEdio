"""RAG knowledge base service using ChromaDB for forbidden words and review rejections."""

import logging
import os
from typing import Optional

logger = logging.getLogger("app.rag")

CHROMADB_PATH = "storage/chromadb"

# Collections
FORBIDDEN_WORDS_COLLECTION = "forbidden_words"
REVIEW_REJECTIONS_COLLECTION = "review_rejections"


class RAGService:
    """RAG service backed by ChromaDB for semantic search.

    Provides:
    - Forbidden word storage and retrieval (exact + semantic matching)
    - Review rejection history storage and retrieval

    Falls back to exact-match-only mode if ChromaDB initialization fails.
    """

    _instance: Optional["RAGService"] = None

    def __init__(self):
        self._client = None
        self._forbidden_words_collection = None
        self._review_rejections_collection = None
        self._initialized = False
        self._init_chromadb()

    @classmethod
    def get_instance(cls) -> "RAGService":
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    def _init_chromadb(self) -> None:
        """Initialize ChromaDB client and collections."""
        try:
            import chromadb
            from chromadb.config import Settings

            os.makedirs(CHROMADB_PATH, exist_ok=True)
            self._client = chromadb.PersistentClient(path=CHROMADB_PATH)
            self._forbidden_words_collection = self._client.get_or_create_collection(
                name=FORBIDDEN_WORDS_COLLECTION,
                metadata={"description": "Forbidden words for content filtering"},
            )
            self._review_rejections_collection = self._client.get_or_create_collection(
                name=REVIEW_REJECTIONS_COLLECTION,
                metadata={"description": "Review rejection reasons for RAG learning"},
            )
            self._initialized = True
            logger.info("ChromaDB initialized successfully at %s", CHROMADB_PATH)
        except Exception as e:
            logger.warning(
                "ChromaDB initialization failed, falling back to exact-match mode: %s", str(e)
            )
            self._initialized = False

    @property
    def is_initialized(self) -> bool:
        """Whether ChromaDB is available."""
        return self._initialized

    def add_forbidden_word(self, word: str, category: Optional[str] = None, suggestion: Optional[str] = None) -> None:
        """Add a forbidden word to the vector store.

        Args:
            word: The forbidden word/phrase.
            category: Category of the word.
            suggestion: Suggested replacement.
        """
        if not self._initialized:
            return

        metadata = {}
        if category:
            metadata["category"] = category
        if suggestion:
            metadata["suggestion"] = suggestion
        metadata["word"] = word

        try:
            self._forbidden_words_collection.upsert(
                ids=[word],
                documents=[word],
                metadatas=[metadata],
            )
        except Exception as e:
            logger.error("Failed to add forbidden word to ChromaDB: %s", str(e))

    def remove_forbidden_word(self, word: str) -> None:
        """Remove a forbidden word from the vector store.

        Args:
            word: The forbidden word to remove.
        """
        if not self._initialized:
            return

        try:
            self._forbidden_words_collection.delete(ids=[word])
        except Exception as e:
            logger.error("Failed to remove forbidden word from ChromaDB: %s", str(e))

    def check_text(self, text: str, forbidden_words_list: Optional[list[dict]] = None) -> list[dict]:
        """Check text for forbidden words using exact match + semantic match.

        Args:
            text: The text to check.
            forbidden_words_list: Optional list of forbidden word dicts for exact matching.
                Each dict should have keys: word, category, suggestion.

        Returns:
            List of matches, each containing:
                - word: matched forbidden word
                - position: start index in text
                - category: word category
                - suggestion: replacement suggestion
        """
        matches = []
        seen_words = set()

        # 1. Exact matching from provided list
        if forbidden_words_list:
            for fw in forbidden_words_list:
                word = fw.get("word", "")
                if not word:
                    continue
                start = 0
                while True:
                    pos = text.find(word, start)
                    if pos == -1:
                        break
                    match_key = f"{word}:{pos}"
                    if match_key not in seen_words:
                        seen_words.add(match_key)
                        matches.append({
                            "word": word,
                            "position": pos,
                            "category": fw.get("category", ""),
                            "suggestion": fw.get("suggestion", ""),
                        })
                    start = pos + 1

        # 2. Semantic matching via ChromaDB
        if self._initialized and text.strip():
            try:
                results = self._forbidden_words_collection.query(
                    query_texts=[text],
                    n_results=10,
                )
                if results and results.get("documents") and results["documents"][0]:
                    for i, doc in enumerate(results["documents"][0]):
                        # Check if the semantically similar word actually appears in text
                        metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
                        word = metadata.get("word", doc)
                        if word in text:
                            # Find all positions
                            start = 0
                            while True:
                                pos = text.find(word, start)
                                if pos == -1:
                                    break
                                match_key = f"{word}:{pos}"
                                if match_key not in seen_words:
                                    seen_words.add(match_key)
                                    matches.append({
                                        "word": word,
                                        "position": pos,
                                        "category": metadata.get("category", ""),
                                        "suggestion": metadata.get("suggestion", ""),
                                    })
                                start = pos + 1
            except Exception as e:
                logger.error("ChromaDB semantic search failed: %s", str(e))

        # Sort by position
        matches.sort(key=lambda m: m["position"])
        return matches

    def add_rejection(self, topic: str, reason: str, copywriting: str) -> None:
        """Add a review rejection record to the knowledge base.

        Args:
            topic: The video topic.
            reason: Rejection reason.
            copywriting: The copywriting that was rejected.
        """
        if not self._initialized:
            return

        import uuid
        doc_id = str(uuid.uuid4())
        document = f"Topic: {topic}\nReason: {reason}\nCopywriting: {copywriting}"
        metadata = {
            "topic": topic,
            "reason": reason,
        }

        try:
            self._review_rejections_collection.add(
                ids=[doc_id],
                documents=[document],
                metadatas=[metadata],
            )
        except Exception as e:
            logger.error("Failed to add rejection to ChromaDB: %s", str(e))

    def get_relevant_rejections(self, topic: str, n_results: int = 5) -> list[dict]:
        """Retrieve relevant rejection history for a topic.

        Args:
            topic: The topic to search for.
            n_results: Maximum number of results.

        Returns:
            List of relevant rejections with topic, reason, and copywriting.
        """
        if not self._initialized or not topic.strip():
            return []

        try:
            results = self._review_rejections_collection.query(
                query_texts=[topic],
                n_results=n_results,
            )
            rejections = []
            if results and results.get("documents") and results["documents"][0]:
                for i, doc in enumerate(results["documents"][0]):
                    metadata = results["metadatas"][0][i] if results.get("metadatas") else {}
                    rejections.append({
                        "topic": metadata.get("topic", ""),
                        "reason": metadata.get("reason", ""),
                        "document": doc,
                    })
            return rejections
        except Exception as e:
            logger.error("ChromaDB rejection retrieval failed: %s", str(e))
            return []
