"""Asset Analysis Service — VLM-powered asset understanding at upload time.

Analyzes uploaded assets asynchronously:
1. Extract sparse frames → VLM analysis → structured summary
2. Audio detection → silence/speech ranges
3. Whisper transcription → text
4. Results persisted to asset_analysis table
"""
from __future__ import annotations


import json
import logging
import math
import os
import re
import subprocess
from datetime import datetime, timezone

from app.models.database import Asset, AssetAnalysis, SessionLocal, generate_uuid, utcnow
from app.services.embedding_service import EmbeddingService
from app.services.vlm_service import VLMService

logger = logging.getLogger("app.asset_analysis")


# ------------------------------------------------------------------
# Utility: cosine similarity
# ------------------------------------------------------------------

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Returns 0.0 when either vector has zero magnitude.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class AssetAnalysisService:
    """Async asset analysis — triggered after upload."""

    def __init__(self):
        self.vlm_service = VLMService()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_asset(self, asset_id: str) -> None:
        """Full analysis pipeline (runs in background thread).

        1. VLM frame analysis → description, role, tags, key_moments
        2. Audio detection → speech ranges, audio quality
        3. Whisper transcription → transcript text
        4. Write all results to asset_analysis table
        """
        db = SessionLocal()
        try:
            asset = db.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                logger.error("asset %s not found for analysis", asset_id)
                return

            # Create or get analysis record
            analysis = db.query(AssetAnalysis).filter(
                AssetAnalysis.asset_id == asset_id
            ).first()
            if not analysis:
                analysis = AssetAnalysis(
                    id=generate_uuid(),
                    asset_id=asset_id,
                    status="analyzing",
                    created_at=utcnow(),
                )
                db.add(analysis)
            else:
                analysis.status = "analyzing"
                analysis.error_message = None
            db.commit()

            file_path = asset.file_path
            media_type = asset.media_type

            if media_type != "video":
                # For non-video assets, just mark as completed with basic info
                analysis.description = f"{media_type} file: {asset.original_filename}"
                analysis.role = "other"
                analysis.status = "completed"
                analysis.analyzed_at = utcnow()

                # Generate embedding for non-video assets too
                self._generate_and_store_embedding(analysis)

                db.commit()
                logger.info("asset %s: non-video, marked as completed", asset_id)
                return

            if not os.path.exists(file_path):
                analysis.status = "failed"
                analysis.error_message = f"File not found: {file_path}"
                db.commit()
                return

            # --- Step 1: VLM frame analysis ---
            vlm_result = self._analyze_with_vlm(file_path, asset.original_filename)
            if vlm_result:
                analysis.description = vlm_result.get("description", "")
                analysis.role = vlm_result.get("role", "other")
                analysis.visual_quality = vlm_result.get("visual_quality", "medium")
                analysis.scene_tags = json.dumps(
                    vlm_result.get("scene_tags", []), ensure_ascii=False
                )
                analysis.key_moments = json.dumps(
                    vlm_result.get("key_moments", []), ensure_ascii=False
                )
                vlm_config = self.vlm_service.config.get_vlm_config()
                analysis.vlm_model = vlm_config.get("model", "unknown")
            else:
                analysis.description = f"Video: {asset.original_filename}"
                analysis.role = "other"
                analysis.visual_quality = "medium"

            # --- Step 2: Audio detection ---
            audio_info = self._detect_audio(file_path)
            analysis.audio_quality = audio_info.get("quality", "silent")
            analysis.has_speech = audio_info.get("has_speech", False)
            analysis.speech_ranges = json.dumps(
                audio_info.get("speech_ranges", [])
            )

            # --- Step 3: Whisper transcription ---
            if analysis.has_speech:
                transcript = self._transcribe(file_path)
                analysis.transcript = transcript
            else:
                analysis.transcript = ""

            analysis.status = "completed"
            analysis.analyzed_at = utcnow()

            # --- Step 4: Generate embedding from description + scene_tags ---
            self._generate_and_store_embedding(analysis)

            db.commit()
            logger.info(
                "asset %s analysis completed: role=%s, has_speech=%s",
                asset_id, analysis.role, analysis.has_speech,
            )

        except Exception as e:
            logger.exception("asset %s analysis failed: %s", asset_id, str(e))
            try:
                analysis = db.query(AssetAnalysis).filter(
                    AssetAnalysis.asset_id == asset_id
                ).first()
                if analysis:
                    analysis.status = "failed"
                    analysis.error_message = str(e)[:500]
                    db.commit()
            except Exception:
                pass
        finally:
            db.close()

    def get_analysis(self, asset_id: str, db=None) -> dict | None:
        """Load analysis from DB. Returns dict or None."""
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True
        try:
            analysis = db.query(AssetAnalysis).filter(
                AssetAnalysis.asset_id == asset_id
            ).first()
            if not analysis:
                return None
            return {
                "asset_id": analysis.asset_id,
                "description": analysis.description or "",
                "role": analysis.role or "other",
                "visual_quality": analysis.visual_quality or "medium",
                "scene_tags": json.loads(analysis.scene_tags) if analysis.scene_tags else [],
                "key_moments": json.loads(analysis.key_moments) if analysis.key_moments else [],
                "audio_quality": analysis.audio_quality or "silent",
                "has_speech": analysis.has_speech or False,
                "speech_ranges": json.loads(analysis.speech_ranges) if analysis.speech_ranges else [],
                "transcript": analysis.transcript or "",
                "status": analysis.status,
                "error_message": analysis.error_message,
                "vlm_model": analysis.vlm_model,
                "analyzed_at": analysis.analyzed_at,
            }
        finally:
            if close_db:
                db.close()

    def search_by_text(self, query: str, limit: int = 10, db=None) -> list[dict]:
        """Semantic search: find assets most relevant to *query*.

        Generates an embedding for the query text, loads all asset_analysis
        records that have embeddings, computes cosine similarity, and returns
        the top-N results sorted by relevance.

        Args:
            query: Natural-language search query.
            limit: Maximum number of results to return (default 10).
            db: Optional SQLAlchemy session (creates one if not provided).

        Returns:
            List of dicts with asset info + ``relevance_score``, sorted
            descending by similarity.
        """
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            # Generate query embedding
            embedding_service = EmbeddingService()
            query_embedding = embedding_service.generate_embedding(query)
            if query_embedding is None:
                logger.warning("Failed to generate embedding for search query: %s", query[:80])
                return []

            # Load all analyses that have embeddings
            analyses = (
                db.query(AssetAnalysis)
                .filter(AssetAnalysis.embedding.isnot(None))
                .filter(AssetAnalysis.status == "completed")
                .all()
            )

            if not analyses:
                return []

            # Load associated assets in bulk
            asset_ids = [a.asset_id for a in analyses]
            assets_map = {
                a.id: a
                for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()
            }

            # Score each analysis by cosine similarity
            scored: list[tuple[float, AssetAnalysis]] = []
            for analysis in analyses:
                try:
                    stored_embedding = json.loads(analysis.embedding)
                    if not isinstance(stored_embedding, list) or not stored_embedding:
                        continue
                    score = _cosine_similarity(query_embedding, stored_embedding)
                    scored.append((score, analysis))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue

            # Sort descending by score, take top-N
            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[:limit]

            results = []
            for score, analysis in top:
                asset = assets_map.get(analysis.asset_id)
                if asset is None:
                    continue

                try:
                    tags = json.loads(analysis.scene_tags) if analysis.scene_tags else []
                except (json.JSONDecodeError, TypeError):
                    tags = []

                results.append({
                    "id": asset.id,
                    "original_filename": asset.original_filename,
                    "category": asset.category,
                    "media_type": asset.media_type,
                    "description": analysis.description or "",
                    "role": analysis.role or "other",
                    "scene_tags": tags,
                    "relevance_score": round(score, 4),
                })

            return results
        finally:
            if close_db:
                db.close()

    def reanalyze_asset(self, asset_id: str) -> None:
        """Re-run analysis (e.g. after VLM model upgrade)."""
        self.analyze_asset(asset_id)

    # ------------------------------------------------------------------
    # Private: Embedding generation
    # ------------------------------------------------------------------

    def _generate_and_store_embedding(self, analysis: AssetAnalysis) -> None:
        """Generate an embedding from description + scene_tags and store it.

        Embedding failure is non-fatal — it logs a warning but does not
        affect the overall analysis status.
        """
        try:
            description = analysis.description or ""
            scene_tags_raw = analysis.scene_tags or "[]"
            try:
                tags = json.loads(scene_tags_raw) if isinstance(scene_tags_raw, str) else scene_tags_raw
            except (json.JSONDecodeError, TypeError):
                tags = []

            embed_text = f"{description} {' '.join(tags)}".strip()
            if not embed_text:
                logger.debug("asset %s: empty embed text, skipping embedding", analysis.asset_id)
                return

            embedding_service = EmbeddingService()
            embedding_vector = embedding_service.generate_embedding(embed_text)

            if embedding_vector is not None:
                analysis.embedding = json.dumps(embedding_vector).encode("utf-8")
                logger.info(
                    "asset %s: embedding generated, dim=%d",
                    analysis.asset_id, len(embedding_vector),
                )
            else:
                logger.warning("asset %s: embedding generation returned None", analysis.asset_id)
        except Exception as e:
            logger.warning(
                "asset %s: embedding generation failed (non-fatal): %s",
                analysis.asset_id, str(e)[:200],
            )

    # ------------------------------------------------------------------
    # Private: VLM analysis
    # ------------------------------------------------------------------

    def _analyze_with_vlm(self, video_path: str, filename: str) -> dict | None:
        """Extract frames and ask VLM to analyze the clip."""
        try:
            vlm_config = self.vlm_service.config.get_vlm_config()
            if not vlm_config.get("api_url") or not vlm_config.get("api_key"):
                logger.warning("VLM not configured, skipping analysis")
                return None

            duration = self._probe_duration(video_path)
            if duration <= 0:
                return None

            # Sparse frames: 1 every 10s for short, 1 every 30s for long
            interval = 10.0 if duration < 300 else 30.0
            max_frames = 20

            frames = self.vlm_service.extract_frames(
                video_path,
                frame_interval=interval,
                max_frames=max_frames,
            )
            if not frames:
                return None

            # Build VLM prompt for single-clip analysis
            content = self._build_analysis_prompt(frames, filename, duration)

            api_url = vlm_config["api_url"]
            api_key = vlm_config["api_key"]
            model = vlm_config.get("model", "qwen3-vl-plus")

            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                "max_tokens": 2048,
                "temperature": 0.3,
                "stream": False,
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            }

            raw_text = self.vlm_service._call_vlm_api(api_url, payload, headers)
            if not raw_text:
                return None

            return self._parse_analysis_json(raw_text)

        except Exception as e:
            logger.warning("VLM analysis failed: %s", str(e)[:200])
            return None

    def _build_analysis_prompt(
        self, frames: list[tuple[float, str]], filename: str, duration: float
    ) -> list[dict]:
        """Build multimodal content for single-clip analysis."""
        content: list[dict] = []
        content.append({
            "type": "text",
            "text": f"Analyze this video clip: {filename} (duration: {duration:.1f}s)",
        })
        for timestamp, b64_data in frames:
            content.append({"type": "text", "text": f"[Frame at {timestamp:.1f}s]"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_data}"},
            })
        content.append({
            "type": "text",
            "text": "Based on the frames above, output your analysis as JSON.",
        })
        return content

    @staticmethod
    def _parse_analysis_json(raw_text: str) -> dict | None:
        """Parse VLM analysis response into structured dict."""
        text = raw_text.strip()
        # Try to extract JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                # Validate expected fields
                result = {
                    "description": str(data.get("description", "")),
                    "role": str(data.get("role", "other")),
                    "visual_quality": str(data.get("visual_quality", "medium")),
                    "scene_tags": data.get("scene_tags", []),
                    "key_moments": data.get("key_moments", []),
                }
                # Normalize role
                valid_roles = {"presenter", "product_closeup", "lifestyle", "transition", "other"}
                if result["role"] not in valid_roles:
                    result["role"] = "other"
                return result
            except (json.JSONDecodeError, TypeError):
                pass
        logger.warning("Failed to parse VLM analysis JSON: %s", text[:200])
        return None

    # ------------------------------------------------------------------
    # Private: Audio detection
    # ------------------------------------------------------------------

    def _detect_audio(self, video_path: str) -> dict:
        """Detect audio quality and speech ranges using FFmpeg."""
        ffmpeg = os.environ.get("IMAGEIO_FFMPEG_EXE", "ffmpeg") or "ffmpeg"
        duration = self._probe_duration(video_path)
        if duration <= 0:
            return {"quality": "silent", "has_speech": False, "speech_ranges": []}

        # Check overall volume
        cmd = [
            ffmpeg, "-y", "-i", video_path,
            "-af", "volumedetect", "-f", "null", "-",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            mean_match = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", result.stderr or "")
            mean_vol = float(mean_match.group(1)) if mean_match else -100.0
        except Exception:
            mean_vol = -100.0

        if mean_vol < -70:
            return {"quality": "silent", "has_speech": False, "speech_ranges": []}

        # Detect speech ranges using silencedetect
        cmd = [
            ffmpeg, "-y", "-i", video_path,
            "-af", "silencedetect=noise=-40dB:d=3",
            "-f", "null", "-",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            stderr = result.stderr or ""

            silence_starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*([\d.]+)", stderr)]
            silence_ends = [float(m.group(1)) for m in re.finditer(r"silence_end:\s*([\d.]+)", stderr)]

            # Build speech ranges (gaps between silences)
            speech_ranges = []
            prev_end = 0.0
            for i in range(len(silence_starts)):
                if silence_starts[i] - prev_end > 1.0:
                    speech_ranges.append([round(prev_end, 1), round(silence_starts[i], 1)])
                if i < len(silence_ends):
                    prev_end = silence_ends[i]
            if duration - prev_end > 1.0:
                speech_ranges.append([round(prev_end, 1), round(duration, 1)])

            has_speech = len(speech_ranges) > 0
            quality = "good" if has_speech else "silent"
            if has_speech and mean_vol < -30:
                quality = "noisy"

            return {
                "quality": quality,
                "has_speech": has_speech,
                "speech_ranges": speech_ranges,
            }
        except Exception as e:
            logger.warning("silence detection failed: %s", str(e)[:100])
            return {
                "quality": "good" if mean_vol > -40 else "noisy",
                "has_speech": mean_vol > -50,
                "speech_ranges": [[0, round(duration, 1)]] if mean_vol > -50 else [],
            }

    # ------------------------------------------------------------------
    # Private: Whisper transcription
    # ------------------------------------------------------------------

    def _transcribe(self, video_path: str) -> str:
        """Transcribe audio using faster-whisper."""
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("base", device="cpu", compute_type="int8", download_root=None)
            segments, info = model.transcribe(video_path, language="zh", vad_filter=True)
            return " ".join(seg.text.strip() for seg in segments if seg.text.strip())
        except Exception as e:
            logger.warning("Whisper transcription failed: %s", str(e)[:200])
            return ""

    # ------------------------------------------------------------------
    # Private: Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _probe_duration(video_path: str) -> float:
        """Get video duration using ffprobe."""
        ffprobe = os.environ.get("IMAGEIO_FFMPEG_EXE", "ffmpeg") or "ffmpeg"
        ffprobe = ffprobe.replace("ffmpeg", "ffprobe")
        if not ffprobe or ffprobe == "ffprobe":
            ffprobe = "ffprobe"
        cmd = [
            ffprobe, "-v", "quiet", "-print_format", "json",
            "-show_format", video_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except Exception:
            pass
        return 0.0


# ------------------------------------------------------------------
# VLM System Prompt for asset analysis
# ------------------------------------------------------------------

ANALYSIS_SYSTEM_PROMPT = """You are a professional video analyst. Analyze the provided video frames and output a structured JSON summary.

Output ONLY a JSON object with these fields:
{
  "description": "Brief description of the video content in Chinese",
  "role": "One of: presenter, product_closeup, lifestyle, transition, other",
  "visual_quality": "One of: high, medium, low",
  "scene_tags": ["tag1", "tag2", ...],
  "key_moments": [{"time": 5.0, "desc": "Description of key moment"}, ...]
}

Role definitions:
- presenter: A person speaking to camera, hosting, demonstrating, or presenting
- product_closeup: Close-up shots of products, packaging, or details
- lifestyle: Lifestyle scenes, outdoor shots, ambient footage
- transition: Short transitional clips, motion graphics, or filler
- other: Anything that doesn't fit the above categories

scene_tags should be Chinese keywords describing the content (e.g. "室内", "美妆", "产品展示", "口播").
key_moments should highlight the most important visual moments with their timestamps."""
