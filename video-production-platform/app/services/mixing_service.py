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

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.database import Asset, Task, TaskAsset, SessionLocal, generate_uuid, utcnow
from app.services import mixing_engine
from app.services.config_service import ConfigService
from app.services.task_service import transition_state, VALID_TRANSITIONS
from app.utils.errors import NotFoundError, StateTransitionError, ValidationError

logger = logging.getLogger("app.mixing_service")

# Concurrency control: limit the number of simultaneous mixing tasks
_MAX_CONCURRENT_MIX = 3
_mix_semaphore = threading.Semaphore(_MAX_CONCURRENT_MIX)


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
        mixing_mode = request.mixing_mode

        # Mode-specific validation
        if mixing_mode in ("pure_mix", "mix_with_script"):
            if not request.a_roll_asset_ids:
                raise ValidationError(message="模式1/2需要至少一个 A-Roll 素材")
        if mixing_mode == "broll_voiceover":
            if not request.asset_ids:
                raise ValidationError(message="纯素材模式需要至少一个素材")
        if mixing_mode == "montage":
            if not request.asset_ids:
                raise ValidationError(message="素材混剪模式需要至少一个素材")

        # Validate all referenced assets exist
        all_asset_ids = set(request.a_roll_asset_ids + request.b_roll_asset_ids + request.asset_ids)
        for asset_id in all_asset_ids:
            asset = self.db.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                raise NotFoundError(message=f"素材不存在: {asset_id}")

        # Build mix_params JSON
        mix_params = json.dumps({
            "mixing_mode": mixing_mode,
            "aspect_ratio": request.aspect_ratio,
            "transition": request.transition,
            "clip_duration": request.clip_duration,
            "concat_mode": request.concat_mode,
            "video_count": request.video_count,
            "max_output_duration": request.max_output_duration,
            "tts_text": request.tts_text,
            "tts_voice": request.tts_voice,
            "bgm_enabled": request.bgm_enabled,
            "bgm_asset_id": request.bgm_asset_id,
            "bgm_volume": request.bgm_volume,
            "director_prompt": request.director_prompt,
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

        # Save TaskAsset records based on mode
        if mixing_mode in ("broll_voiceover", "montage"):
            # Mode 3/4: all assets are equal, store as "asset" roll_type
            for i, asset_id in enumerate(request.asset_ids):
                ta = TaskAsset(id=generate_uuid(), task_id=task.id, asset_id=asset_id, roll_type="asset", sequence_order=i)
                self.db.add(ta)
        else:
            # Mode 1/2: separate A-roll and B-roll
            for i, asset_id in enumerate(request.a_roll_asset_ids):
                ta = TaskAsset(id=generate_uuid(), task_id=task.id, asset_id=asset_id, roll_type="a_roll", sequence_order=i)
                self.db.add(ta)
            for i, asset_id in enumerate(request.b_roll_asset_ids):
                ta = TaskAsset(id=generate_uuid(), task_id=task.id, asset_id=asset_id, roll_type="b_roll", sequence_order=i)
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

        Routes to the correct pipeline based on mixing_mode:
        - pure_mix: A-roll audio + AI Director + Whisper subtitles
        - mix_with_script: TTS audio + AI Director + script subtitles
        - broll_voiceover: TTS audio + blind-cut B-roll + script subtitles
        """
        acquired = _mix_semaphore.acquire(timeout=300)  # Wait up to 5 min
        if not acquired:
            db = SessionLocal()
            try:
                task = db.query(Task).filter(Task.id == task_id).first()
                if task:
                    task.status = "failed"
                    task.error_message = "服务器繁忙，并发混剪任务过多，请稍后重试"
                    task.updated_at = utcnow()
                    db.commit()
            except Exception:
                logger.exception("failed to mark task %s as failed after semaphore timeout", task_id)
            finally:
                db.close()
            return

        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if not task:
                logger.error("task %s not found in execute_mix", task_id)
                return

            params = json.loads(task.mix_params)

            mixing_mode = params.get("mixing_mode", "pure_mix")
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
            director_prompt = params.get("director_prompt", "")

            # Resolve asset file paths based on mode
            a_roll_paths = []
            b_roll_paths = []
            all_asset_paths = []  # For mode 3

            if mixing_mode in ("broll_voiceover", "montage"):
                # Mode 3/4: all assets are equal
                asset_records = (
                    db.query(TaskAsset)
                    .filter(TaskAsset.task_id == task_id, TaskAsset.roll_type == "asset")
                    .order_by(TaskAsset.sequence_order)
                    .all()
                )
                for ta in asset_records:
                    asset = db.query(Asset).filter(Asset.id == ta.asset_id).first()
                    if asset and asset.file_path:
                        all_asset_paths.append(asset.file_path)
            else:
                # Mode 1/2: separate A-roll and B-roll
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
                for ta in a_roll_assets:
                    asset = db.query(Asset).filter(Asset.id == ta.asset_id).first()
                    if asset and asset.file_path:
                        a_roll_paths.append(asset.file_path)
                for ta in b_roll_assets:
                    asset = db.query(Asset).filter(Asset.id == ta.asset_id).first()
                    if asset and asset.file_path:
                        b_roll_paths.append(asset.file_path)

            output_dir = f"storage/tasks/{task_id}"
            os.makedirs(output_dir, exist_ok=True)

            aspect_resolutions = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
            video_w, video_h = aspect_resolutions.get(aspect_ratio, (1080, 1920))

            ai_director_used = False

            # --- Split long A-roll into segments ---
            # If A-roll total duration > max_output_duration, split into multiple segments.
            # Each segment becomes one output video.
            max_output_duration = params.get("max_output_duration", 60)

            # Calculate total A-roll duration
            total_a_duration = 0.0
            for path in a_roll_paths:
                dur = self._probe_duration(path) or 0
                total_a_duration += dur

            # Determine how many segments to produce
            if total_a_duration > max_output_duration and mixing_mode in ("pure_mix", "mix_with_script"):
                import math
                num_segments = min(math.ceil(total_a_duration / max_output_duration), video_count * 5)
                segment_duration = total_a_duration / num_segments
                logger.info(
                    "splitting %.1fs A-roll into %d segments of ~%.1fs each (max_output=%ds)",
                    total_a_duration, num_segments, segment_duration, max_output_duration,
                )
            else:
                num_segments = video_count
                segment_duration = total_a_duration

            # Pre-split A-roll into temp files if needed
            a_roll_segment_paths = []
            if num_segments > 1 and len(a_roll_paths) == 1:
                # Split single A-roll file into segments using FFmpeg
                src = a_roll_paths[0]
                for seg_i in range(num_segments):
                    seg_start = seg_i * segment_duration
                    seg_end = min((seg_i + 1) * segment_duration, total_a_duration)
                    seg_path = os.path.join(output_dir, f"aroll-seg-{seg_i}.mp4")
                    cmd = [
                        "ffmpeg", "-y", "-i", src,
                        "-ss", str(seg_start), "-to", str(seg_end),
                        "-c", "copy", "-avoid_negative_ts", "1",
                        seg_path,
                    ]
                    import subprocess as _sp
                    _sp.run(cmd, capture_output=True, check=False)
                    if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                        a_roll_segment_paths.append([seg_path])
                    else:
                        logger.warning("failed to split segment %d", seg_i)
            else:
                # Use original A-roll paths for each version
                for _ in range(num_segments):
                    a_roll_segment_paths.append(a_roll_paths)

            # Generate each segment/version
            output_paths = []
            actual_count = min(len(a_roll_segment_paths), num_segments)
            for version in range(1, actual_count + 1):
                seg_a_paths = a_roll_segment_paths[version - 1]
                version_output = os.path.join(output_dir, f"output-{version}.mp4")
                logger.info("generating segment %d/%d for task %s (mode=%s)", version, actual_count, task_id, mixing_mode)

                if mixing_mode == "pure_mix":
                    # --- Pure Mix: A-roll audio + AI Director ---
                    audio_file = os.path.join(output_dir, f"extracted_audio-{version}.mp3")
                    mixing_engine.extract_audio_from_videos(seg_a_paths, audio_file)

                    from app.services.ai_director_service import AIDirectorService
                    ai_director = AIDirectorService(task_id, output_dir)
                    transcript = ai_director._transcribe_audio(audio_file)
                    raw_output, ai_used = ai_director.run_pipeline(
                        seg_a_paths, b_roll_paths, transcript,
                        aspect_ratio=aspect_ratio, transition=transition, audio_file=audio_file,
                        director_prompt=director_prompt,
                    )
                    if ai_used:
                        ai_director_used = True

                    # Subtitles (Whisper)
                    ass_path = os.path.join(output_dir, f"subtitles-{version}.ass")
                    try:
                        mixing_engine._generate_subtitles(audio_file, ass_path, video_w, video_h)
                        if os.path.exists(ass_path) and os.path.getsize(ass_path) > 0:
                            final_path = os.path.join(output_dir, f"final-{version}.mp4")
                            mixing_engine.burn_subtitles(raw_output, ass_path, final_path)
                            os.replace(final_path, version_output)
                        else:
                            import shutil as _shutil
                            _shutil.copy(raw_output, version_output)
                    except Exception as e:
                        logger.warning("subtitle failed: %s", str(e)[:200])
                        import shutil as _shutil
                        if raw_output != version_output:
                            _shutil.copy(raw_output, version_output)

                elif mixing_mode == "mix_with_script":
                    # --- Mix + AI Script: TTS optional + AI Director ---
                    from app.services.ai_director_service import AIDirectorService

                    audio_file = None
                    duration = None
                    transcript = ""

                    # TTS is optional — synthesize only if text provided
                    if tts_text and tts_text.strip():
                        from app.services.ai_tts_service import AITTSService
                        ai_tts = AITTSService()
                        audio_file, duration = ai_tts.synthesize(tts_text, task_id, tts_voice)
                        transcript = tts_text
                        logger.info("mix_with_script: TTS audio generated (%.1fs)", duration)
                    else:
                        # No TTS — extract original A-roll audio and transcribe
                        audio_file = os.path.join(output_dir, f"extracted_audio-{version}.mp3")
                        mixing_engine.extract_audio_from_videos(seg_a_paths, audio_file)
                        logger.info("mix_with_script: no TTS text, using A-roll audio")

                    ai_director = AIDirectorService(task_id, output_dir)
                    raw_output, ai_used = ai_director.run_pipeline(
                        seg_a_paths, b_roll_paths, transcript,
                        aspect_ratio=aspect_ratio, transition=transition, audio_file=audio_file,
                        director_prompt=director_prompt,
                    )
                    if ai_used:
                        ai_director_used = True

                    # Script-based subtitles only if TTS text was provided
                    if tts_text and tts_text.strip() and duration:
                        ass_path = os.path.join(output_dir, f"subtitles-{version}.ass")
                        try:
                            mixing_engine.generate_subtitles_from_script(tts_text, duration, ass_path, video_w, video_h)
                            final_path = os.path.join(output_dir, f"final-{version}.mp4")
                            mixing_engine.burn_subtitles(raw_output, ass_path, final_path)
                            os.replace(final_path, version_output)
                        except Exception as e:
                            logger.warning("subtitle failed: %s", str(e)[:200])
                            import shutil as _shutil
                            if raw_output != version_output:
                                _shutil.copy(raw_output, version_output)
                    else:
                        import shutil as _shutil
                        if raw_output != version_output:
                            _shutil.copy(raw_output, version_output)

                elif mixing_mode == "broll_voiceover":
                    # --- Mode 3: Pure assets, TTS optional ---
                    audio_file = None
                    duration = None

                    if tts_text and tts_text.strip():
                        from app.services.ai_tts_service import AITTSService
                        ai_tts = AITTSService()
                        audio_file, duration = ai_tts.synthesize(tts_text, task_id, tts_voice)
                        logger.info("broll_voiceover: TTS audio generated (%.1fs)", duration)

                    mixing_engine.combine_videos(
                        combined_video_path=version_output,
                        video_paths=all_asset_paths,
                        audio_file=audio_file,
                        video_aspect=aspect_ratio,
                        video_concat_mode=concat_mode,
                        video_transition=transition,
                        max_clip_duration=clip_duration,
                    )

                    # Script-based subtitles only if TTS text was provided
                    if tts_text and tts_text.strip() and duration:
                        ass_path = os.path.join(output_dir, f"subtitles-{version}.ass")
                        try:
                            mixing_engine.generate_subtitles_from_script(tts_text, duration, ass_path, video_w, video_h)
                            final_path = os.path.join(output_dir, f"final-{version}.mp4")
                            mixing_engine.burn_subtitles(version_output, ass_path, final_path)
                            os.replace(final_path, version_output)
                        except Exception as e:
                            logger.warning("subtitle failed: %s", str(e)[:200])

                elif mixing_mode == "montage":
                    # --- Mode 4: Montage — AI-directed clip arrangement, TTS optional ---
                    from app.services.ai_director_service import AIDirectorService

                    audio_file = None
                    duration = None

                    # TTS is optional in montage mode
                    if tts_text and tts_text.strip():
                        from app.services.ai_tts_service import AITTSService
                        ai_tts = AITTSService()
                        audio_file, duration = ai_tts.synthesize(tts_text, task_id, tts_voice)
                        logger.info("montage mode: TTS audio generated (%.1fs)", duration)

                    ai_director = AIDirectorService(task_id, output_dir)
                    raw_output, ai_used = ai_director.run_montage_pipeline(
                        all_asset_paths,
                        aspect_ratio=aspect_ratio,
                        transition=transition,
                        audio_file=audio_file,
                        max_output_duration=max_output_duration,
                        director_prompt=director_prompt,
                    )
                    if ai_used:
                        ai_director_used = True

                    # Subtitles only if TTS text was provided
                    if tts_text and tts_text.strip() and duration:
                        ass_path = os.path.join(output_dir, f"subtitles-{version}.ass")
                        try:
                            mixing_engine.generate_subtitles_from_script(tts_text, duration, ass_path, video_w, video_h)
                            final_path = os.path.join(output_dir, f"final-{version}.mp4")
                            mixing_engine.burn_subtitles(raw_output, ass_path, final_path)
                            os.replace(final_path, version_output)
                        except Exception as e:
                            logger.warning("subtitle failed: %s", str(e)[:200])
                            import shutil as _shutil
                            if raw_output != version_output:
                                _shutil.copy(raw_output, version_output)
                    else:
                        import shutil as _shutil
                        if raw_output != version_output:
                            _shutil.copy(raw_output, version_output)

                else:
                    # Fallback: old blind-cut logic
                    audio_file = os.path.join(output_dir, f"extracted_audio-{version}.mp3")
                    mixing_engine.extract_audio_from_videos(seg_a_paths, audio_file)
                    mixing_engine.combine_videos(
                        combined_video_path=version_output,
                        video_paths=seg_a_paths + b_roll_paths,
                        audio_file=audio_file,
                        video_aspect=aspect_ratio,
                        video_concat_mode=concat_mode,
                        video_transition=transition,
                        max_clip_duration=clip_duration,
                    )

                # BGM mixing (all modes)
                if bgm_enabled:
                    bgm_file = self._resolve_bgm_file(bgm_asset_id, db)
                    if bgm_file:
                        bgm_output = os.path.join(output_dir, f"output-{version}-bgm.mp4")
                        mixing_engine.mix_bgm(
                            main_audio_path=version_output,
                            bgm_file=bgm_file,
                            output_path=bgm_output,
                            bgm_volume=bgm_volume,
                        )
                        os.replace(bgm_output, version_output)

                output_paths.append(version_output)

            # Store ai_director_used in mix_params
            params["ai_director_used"] = ai_director_used
            task.mix_params = json.dumps(params, ensure_ascii=False)

            # Collect metadata
            video_resolution = None
            video_duration = None
            video_file_size = None
            if output_paths and os.path.exists(output_paths[0]):
                video_file_size = sum(os.path.getsize(p) for p in output_paths if os.path.exists(p))
                video_resolution = self._get_resolution_label(aspect_ratio)
                video_duration = self._probe_duration(output_paths[0])

            task.status = "video_done"
            task.video_paths = json.dumps([os.path.relpath(p) for p in output_paths], ensure_ascii=False)
            task.video_resolution = video_resolution
            task.video_duration = video_duration
            task.video_file_size = video_file_size
            task.updated_at = utcnow()
            db.commit()

            logger.info("task %s completed: %d videos, ai_director=%s", task_id, len(output_paths), ai_director_used)

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
            _mix_semaphore.release()

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

        Uses an atomic UPDATE with WHERE clause to prevent race conditions.

        Args:
            task_id: The task ID.

        Returns:
            Updated Task record.

        Raises:
            NotFoundError: If the task does not exist.
            StateTransitionError: If the task is not in video_done status.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise NotFoundError(message=f"任务不存在: {task_id}")

        # Atomic state transition: only update if current status matches expected
        result = self.db.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == "video_done")
            .values(status="pending_review", updated_at=utcnow())
        )
        if result.rowcount == 0:
            raise StateTransitionError(
                message=f"只有状态为 video_done 的任务才能提交审核，当前状态: {task.status}",
                details={"current_status": task.status},
            )

        self.db.commit()
        self.db.refresh(task)
        return task

    # ------------------------------------------------------------------
    # 4.5  retry
    # ------------------------------------------------------------------

    def retry(self, task_id: str) -> Task:
        """Retry a failed or rejected mixing task.

        Uses an atomic UPDATE with WHERE clause to prevent race conditions.

        Args:
            task_id: The task ID.

        Returns:
            Updated Task record.

        Raises:
            NotFoundError: If the task does not exist.
            StateTransitionError: If the task is not in failed or rejected status.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise NotFoundError(message=f"任务不存在: {task_id}")

        # Atomic state transition: only update if current status is failed or rejected
        result = self.db.execute(
            update(Task)
            .where(Task.id == task_id, Task.status.in_(["failed", "rejected"]))
            .values(status="processing", error_message=None, updated_at=utcnow())
        )
        if result.rowcount == 0:
            raise StateTransitionError(
                message=f"只有状态为 failed 或 rejected 的任务才能重试，当前状态: {task.status}",
                details={"current_status": task.status},
            )

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
