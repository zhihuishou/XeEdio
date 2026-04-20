"""Copywriting service - LLM-based video copywriting generation with forbidden word filtering."""

import logging
import time
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models.database import ForbiddenWord, Task, generate_uuid, utcnow
from app.services.config_service import ConfigService
from app.services.rag_service import RAGService

logger = logging.getLogger("app.llm")

# Default prompt template for short video copywriting
SYSTEM_PROMPT_TEMPLATE = """你是一个专业的短视频文案创作者。请根据用户提供的主题，生成一段适合短视频的文案。

要求：
1. 文案结构清晰，适合口播或配音
2. 语言简洁有力，吸引观众注意力
3. 控制在 200-500 字之间
4. 包含开头吸引语、主体内容、结尾引导语
5. 适合竖屏短视频（抖音/快手风格）

{rejection_context}"""

REJECTION_CONTEXT_TEMPLATE = """注意：以下是之前被审核拒绝的类似主题案例，请避免类似问题：
{rejections}
"""


class CopywritingService:
    """Service for LLM-based copywriting generation."""

    def __init__(self, db: Session):
        self.db = db
        self.config = ConfigService.get_instance()
        self.rag = RAGService.get_instance()

    def _get_llm_config(self, override_config: dict = None) -> dict:
        """Get LLM API configuration.

        Args:
            override_config: Optional dict with api_url, api_key, model to override system defaults.

        Returns:
            Dict with api_url, api_key, model.
        """
        if override_config:
            return override_config
        return {
            "api_url": self.config.get_config("llm_api_url", self.db, "https://api.deepseek.com/v1/chat/completions"),
            "api_key": self.config.get_config("llm_api_key", self.db, ""),
            "model": self.config.get_config("llm_model", self.db, "deepseek-chat"),
        }

    def _build_prompt(self, topic: str) -> list[dict]:
        """Build the LLM prompt with system template and topic.

        Injects relevant rejection history as negative examples if available.
        """
        # Get relevant rejections from RAG
        rejection_context = ""
        rejections = self.rag.get_relevant_rejections(topic)
        if rejections:
            rejection_lines = []
            for r in rejections[:3]:  # Limit to 3 examples
                rejection_lines.append(f"- 主题: {r['topic']}, 拒绝原因: {r['reason']}")
            rejection_context = REJECTION_CONTEXT_TEMPLATE.format(
                rejections="\n".join(rejection_lines)
            )

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(rejection_context=rejection_context)

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请为以下主题生成短视频文案：{topic}"},
        ]

    def _call_llm_api(self, messages: list[dict], override_config: dict = None) -> str:
        """Call LLM API with retry logic.

        Args:
            messages: List of message dicts with role and content.
            override_config: Optional dict with api_url, api_key, model to override system defaults.

        Timeout: 30 seconds per request.
        Retries: up to 2 times with 5-second intervals.

        Returns:
            Generated text from LLM.

        Raises:
            Exception with appropriate error info on failure.
        """
        config = self._get_llm_config(override_config)
        api_url = config["api_url"]
        api_key = config["api_key"]
        model = config["model"]

        headers = {
            "Content-Type": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }

        max_retries = 2
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                logger.info(
                    "LLM API call attempt %d/%d to %s with model %s",
                    attempt + 1, max_retries + 1, api_url, model,
                )
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(api_url, json=payload, headers=headers)

                if response.status_code == 200:
                    data = response.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    logger.info("LLM API call successful, response length: %d", len(content))
                    return content
                else:
                    last_error = f"LLM API returned status {response.status_code}: {response.text[:200]}"
                    logger.warning("LLM API error (attempt %d): %s", attempt + 1, last_error)

            except httpx.TimeoutException as e:
                last_error = f"LLM API timeout: {str(e)}"
                logger.warning("LLM API timeout (attempt %d): %s", attempt + 1, last_error)
            except Exception as e:
                last_error = f"LLM API error: {str(e)}"
                logger.error("LLM API unexpected error (attempt %d): %s", attempt + 1, last_error)

            # Wait before retry (except on last attempt)
            if attempt < max_retries:
                time.sleep(5)

        logger.error("LLM API call failed after all retries: %s", last_error)
        raise Exception(last_error)

    def generate_copywriting(self, topic: str, task_id: Optional[str] = None, user_id: str = "", llm_config: dict = None) -> Task:
        """Generate copywriting for a topic.

        Args:
            topic: Video topic.
            task_id: Optional existing task ID to update.
            user_id: ID of the user requesting generation.
            llm_config: Optional dict with api_url, api_key, model to override system defaults.

        Returns:
            The Task record with generated copywriting.
        """
        # Get or create task
        if task_id:
            task = self.db.query(Task).filter(Task.id == task_id).first()
            if not task:
                raise ValueError(f"Task {task_id} not found")
        else:
            task = Task(
                id=generate_uuid(),
                topic=topic,
                status="draft",
                created_by=user_id,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            self.db.add(task)
            self.db.flush()

        # Build prompt and call LLM
        messages = self._build_prompt(topic)
        raw_text = self._call_llm_api(messages, llm_config)

        # Run forbidden word check
        all_words = self.db.query(ForbiddenWord).all()
        words_list = [
            {"word": w.word, "category": w.category or "", "suggestion": w.suggestion or ""}
            for w in all_words
        ]
        matches = self.rag.check_text(raw_text, words_list)

        # Apply filtering - replace forbidden words with suggestions
        filtered_text = raw_text
        if matches:
            # Sort by position descending to replace from end to start
            for match in sorted(matches, key=lambda m: m["position"], reverse=True):
                word = match["word"]
                suggestion = match.get("suggestion", "***")
                replacement = suggestion if suggestion else "***"
                pos = match["position"]
                filtered_text = filtered_text[:pos] + replacement + filtered_text[pos + len(word):]

        # Save to task
        task.copywriting_raw = raw_text
        task.copywriting_filtered = filtered_text
        task.topic = topic
        task.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(task)

        return task

    def check_and_filter_text(self, text: str) -> dict:
        """Check text for forbidden words and return filter results.

        Args:
            text: Text to check.

        Returns:
            Dict with status, matches, and filtered_text.
        """
        all_words = self.db.query(ForbiddenWord).all()
        words_list = [
            {"word": w.word, "category": w.category or "", "suggestion": w.suggestion or ""}
            for w in all_words
        ]
        matches = self.rag.check_text(text, words_list)

        filtered_text = text
        if matches:
            for match in sorted(matches, key=lambda m: m["position"], reverse=True):
                word = match["word"]
                suggestion = match.get("suggestion", "***")
                replacement = suggestion if suggestion else "***"
                pos = match["position"]
                filtered_text = filtered_text[:pos] + replacement + filtered_text[pos + len(word):]

        return {
            "status": "contains_forbidden" if matches else "passed",
            "matches": matches,
            "filtered_text": filtered_text,
        }
