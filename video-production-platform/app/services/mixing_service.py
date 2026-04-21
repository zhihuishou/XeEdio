"""Mixing task orchestration service.

Coordinates the full mixing workflow: task creation, background execution,
status tracking, review submission, and retry logic.
"""

import asyncio
import glob
import json
import logging
import os
import random
import threading
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Asset, Task, TaskAsset, SessionLocal, generate_uuid, utcnow
from app.services import mixing_engine
from app.services.config_service import ConfigService
from app.services.task_service import transition_state
from app.utils.errors import NotFoundError, ValidationError

logger = logging.getLogger("app.mixing_service")


class MixingService:
    """Orchestrates mixing task lifecycle."""

    def __init__(self, db: Session):
        self.db = db
        self.config = ConfigService.get_instance()

    # ------------------------------------------------------------------
    # 4.1  create_mix_task
    # ------------------------------------------------------------------

    def create_mix_task(self, request, user_id: str) -> Task:
        """Create a mixing task and kick off background execution.

        Args:
            request: MixCreateRequest with all mixing parameters.
            user_id: ID of the user creating the task.

        Returns:
            The newly created Task record.

        Raises:
            NotFoundError: If any referenced asset does not exist.
        """
        # Validate A-roll assets exist
        for asset_id in request.a_roll_asset_ids:
            asset = self.db.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                raise NotFoundError(
                    message=f"A-Roll 素材不存在: {asset_id}",
                    details={"asset_id": asset_id},
                )

        # Validate B-roll assets if provided
        for asset_id in request.b_roll_asset_ids:
            asset = self.db.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                raise NotFoundError(
                    message=f"B-Roll 素材不存在: {asset_id}",
                    details={"asset_id": asset_id},
                )

        # Build mix_params JSON
        mix_params = json.dumps({
            "aspect_ratio": request.aspect_ratio,
            "transition": request.transition,
            "clip_duration": request.clip_duration,
            "concat_mode": request.concat_mode,
            "video_count": request.video_count,
            "tts_text": request.tts_text,
            "tts_voice": request.tts_voice,
            "bgm_enabled": request.bgm_enabled,
            "bgm_asset_id": request.bgm_asset_id,
            "bgm_volume": request.bgm_volume,
        }, ensure_ascii=False)

        # Create Task record
        task = Task(
            id=generate_uuid(),
            topic=request.topic,
            status="processing",
            mix_params=mix_params,
            created_by=user_id,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self.db.add(task)
        self.db.flush()

        # Save TaskAsset records for A-roll
        for i, asset_id in enumerate(request.a_roll_asset_ids):
            ta = TaskAsset(
                id=generate_uuid(),
                task_id=task.id,
                asset_id=asset_id,
                roll_type="a_roll",
                sequence_order=i,
            )
            self.db.add(ta)

        # Save TaskAsset records for B-roll
        for i, asset_id in enumerate(request.b_roll_asset_ids):
            ta = TaskAsset(
                id=generate_uuid(),
                task_id=task.id,
                asset_id=asset_id,
                roll_type="b_roll",
                sequence_order=i,
            )
            self.db.add(ta)

        self.db.commit()
        self.db.refresh(task)

        # Start background thread for mixing execution
        thread = threading.Thread(
            target=self.execute_mix,
            args=(task.id,),
            daemon=True,
        )
        thread.start()

        return task

    # ------------------------------------------------------------------
    # 4.2  execute_mix
    # ------------------------------------------------------------------

    def execute_mix(self, task_id: str) -> None:
        """Run the full mixing pipeline in a background thread.

        Uses its own database session (SessionLocal) because the request
        session is not safe to share across threads.
        """
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                logger.error("task %s not found in execute_mix", task_id)
                return

            params = json.loads(task.mix_params)

            aspect_ratio = params.get("aspect_ratio", "9:16")
            transition = params.get("transition", "none")
            clip_duration = params.get("clip_duration", 5)
            concat_mode = params.get("concat_mode", "random")
            video_count = params.get("video_count", 1)
            tts_text = params.get("tts_text")
            tts_voice = params.get("tts_voice")
            bgm_enabled = params.get("bgm_enabled", False)
            bgm_asset_id = params.get("bgm_asset_id")
            bgm_volume = params.get("bgm_volume", 0.2)

            # Resolve asset file paths from TaskAsset records
            a_roll_assets = (
                db.query(TaskAsset)
                .filter(TaskAsset.task_id == task_id, TaskAsset.roll_type == "a_roll")
                .order_by(TaskAsset.sequence_order)
                .all()
            )
            b_roll_assets = (
                db.query(TaskAsset)
                .filter(TaskAsset.task_id == task_id, TaskAsset.roll_type == "b_roll")
                .order_by(TaskAsset.sequence_order)
                .all()
            )

            a_roll_paths = []
            for ta in a_roll_assets:
                asset = db.query(Asset).filter(Asset.id == ta.asset_id).first()
                if asset and asset.file_path:
                    a_roll_paths.append(asset.file_path)

            b_roll_paths = []
            for ta in b_roll_assets:
                asset = db.query(Asset).filter(Asset.id == ta.asset_id).first()
                if asset and asset.file_path:
                    b_roll_paths.append(asset.file_path)

            all_video_paths = a_roll_paths + b_roll_paths

            # Prepare output directory
            output_dir = f"storage/tasks/{task_id}"
            os.makedirs(output_dir, exist_ok=True)

            # Determine audio source
            audio_file: Optional[str] = None

            if tts_text:
                # Use TTS to generate audio
                audio_file = os.path.join(output_dir, "tts_audio.mp3")
                self._synthesize_tts(tts_text, tts_voice, audio_file)
            else:
                # Extract audio from A-roll videos
                audio_file = os.path.join(output_dir, "extracted_audio.mp3")
                mixing_engine.extract_audio_from_videos(a_roll_paths, audio_file)

            # Generate each version
            output_paths = []
            for version in range(1, video_count + 1):
                output_path = os.path.join(output_dir, f"output-{version}.mp4")
                logger.info(
                    "generating version %d/%d for task %s",
                    version, video_count, task_id,
                )
                mixing_engine.combine_videos(
                    combined_video_path=output_path,
                    video_paths=all_video_paths,
                    audio_file=audio_file,
                    video_aspect=aspect_ratio,
                    video_concat_mode=concat_mode,
                    video_transition=transition,
                    max_clip_duration=clip_duration,
                )

                # Mix BGM if enabled
                if bgm_enabled:
                    bgm_file = self._resolve_bgm_file(bgm_asset_id, db)
                    if bgm_file:
                        bgm_output = os.path.join(output_dir, f"output-{version}-bgm.mp4")
                        mixing_engine.mix_bgm(
                            main_audio_path=output_path,
                            bgm_file=bgm_file,
                            output_path=bgm_output,
                            bgm_volume=bgm_volume,
                        )
                        # Replace original output with BGM version
                        os.replace(bgm_output, output_path)

                output_paths.append(output_path)

            # Collect metadata from the first output
            video_resolution = None
            video_duration = None
            video_file_size = None
            if output_paths and os.path.exists(output_paths[0]):
                video_file_size = sum(
                    os.path.getsize(p) for p in output_paths if os.path.exists(p)
                )
                video_resolution = self._get_resolution_label(aspect_ratio)
                video_duration = self._probe_duration(output_paths[0])

            # Update task to video_done
            task.status = "video_done"
            task.video_paths = json.dumps(
                [os.path.relpath(p) for p in output_paths], ensure_ascii=False
            )
            task.video_resolution = video_resolution
            task.video_duration = video_duration
            task.video_file_size = video_file_size
            task.updated_at = utcnow()
            db.commit()

            logger.info("task %s completed: %d videos generated", task_id, len(output_paths))

        except Exception as e:
            logger.exception("task %s failed: %s", task_id, str(e))
            try:
                task = db.query(Task).filter(Task.id == task_id).first()
                if task:
                    task.status = "failed"
                    task.error_message = str(e)
                    task.updated_at = utcnow()
                    db.commit()
            except Exception:
                logger.exception("failed to update task %s status to failed", task_id)
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 4.3  get_status
    # ------------------------------------------------------------------

    def get_status(self, task_id: str) -> dict:
        """Return current task status, progress, and metadata.

        Args:
            task_id: The task ID.

        Returns:
            Dict with status, progress, video_paths, and metadata.

        Raises:
            NotFoundError: If the task does not exist.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise NotFoundError(message=f"任务不存在: {task_id}")

        video_paths = None
        if task.video_paths:
            try:
                video_paths = json.loads(task.video_paths)
            except (json.JSONDecodeError, TypeError):
                video_paths = None

        progress = None
        if task.status == "processing":
            progress = "正在处理中…"
        elif task.status == "video_done":
            progress = "处理完成"
        elif task.status == "failed":
            progress = "处理失败"

        return {
            "task_id": task.id,
            "status": task.status,
            "progress": progress,
            "video_paths": video_paths,
            "video_resolution": task.video_resolution,
            "video_duration": task.video_duration,
            "video_file_size": task.video_file_size,
            "error_message": task.error_message,
        }

    # ------------------------------------------------------------------
    # 4.4  submit_review
    # ------------------------------------------------------------------

    def submit_review(self, task_id: str) -> Task:
        """Submit a completed mixing task for review.

        Args:
            task_id: The task ID.

        Returns:
            Updated Task record.

        Raises:
            NotFoundError: If the task does not exist.
            ValidationError: If the task is not in video_done status.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise NotFoundError(message=f"任务不存在: {task_id}")

        if task.status != "video_done":
            raise ValidationError(
                message=f"只有状态为 video_done 的任务才能提交审核，当前状态: {task.status}",
                details={"current_status": task.status},
            )

        transition_state(task, "pending_review")
        self.db.commit()
        self.db.refresh(task)
        return task

    # ------------------------------------------------------------------
    # 4.5  retry
    # ------------------------------------------------------------------

    def retry(self, task_id: str) -> Task:
        """Retry a failed or rejected mixing task.

        Args:
            task_id: The task ID.

        Returns:
            Updated Task record.

        Raises:
            NotFoundError: If the task does not exist.
            ValidationError: If the task is not in failed or rejected status.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise NotFoundError(message=f"任务不存在: {task_id}")

        if task.status not in ("failed", "rejected"):
            raise ValidationError(
                message=f"只有状态为 failed 或 rejected 的任务才能重试，当前状态: {task.status}",
                details={"current_status": task.status},
            )

        transition_state(task, "processing")
        task.error_message = None
        self.db.commit()
        self.db.refresh(task)

        # Start background thread again
        thread = threading.Thread(
            target=self.execute_mix,
            args=(task.id,),
            daemon=True,
        )
        thread.start()

        return task

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _synthesize_tts(self, text: str, voice: Optional[str], output_path: str) -> None:
        """Synthesize TTS audio using edge-tts.

        Args:
            text: Text to synthesize.
            voice: Voice name (defaults to zh-CN-XiaoxiaoNeural).
            output_path: Path to save the audio file.
        """
        import edge_tts

        selected_voice = voice or "zh-CN-XiaoxiaoNeural"
        communicate = edge_tts.Communicate(text, selected_voice)

        # edge-tts is async, run in a new event loop
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(communicate.save(output_path))
        finally:
            loop.close()

        logger.info("TTS synthesized: voice=%s, output=%s", selected_voice, output_path)

    def _resolve_bgm_file(self, bgm_asset_id: Optional[str], db: Session) -> Optional[str]:
        """Resolve BGM file path from asset ID or pick a random built-in song.

        Args:
            bgm_asset_id: Asset ID for a specific BGM, or None for random.
            db: Database session.

        Returns:
            Path to the BGM audio file, or None if unavailable.
        """
        if bgm_asset_id:
            asset = db.query(Asset).filter(Asset.id == bgm_asset_id).first()
            if asset and asset.file_path and os.path.exists(asset.file_path):
                return asset.file_path

        # Fall back to random built-in song from MoneyPrinterTurbo resource
        songs_dir = os.path.join("MoneyPrinterTurbo", "resource", "songs")
        if not os.path.isdir(songs_dir):
            # Try relative to project root
            songs_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "..", "MoneyPrinterTurbo", "resource", "songs",
            )
        songs = glob.glob(os.path.join(songs_dir, "*.mp3"))
        if songs:
            return random.choice(songs)

        logger.warning("no BGM file available")
        return None

    @staticmethod
    def _get_resolution_label(aspect_ratio: str) -> str:
        """Map aspect ratio to a human-readable resolution label."""
        mapping = {
            "16:9": "1920x1080",
            "9:16": "1080x1920",
            "1:1": "1080x1080",
        }
        return mapping.get(aspect_ratio, "1080x1920")

    @staticmethod
    def _probe_duration(video_path: str) -> Optional[float]:
        """Get video duration using ffprobe."""
        import subprocess

        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet", "-print_format", "json",
                    "-show_format", video_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                import json as _json
                data = _json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except Exception:
            pass
        return None
