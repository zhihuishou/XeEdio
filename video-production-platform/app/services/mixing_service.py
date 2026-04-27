"""Mixing task orchestration service.

Coordinates the full mixing workflow: task creation, background execution,
status tracking, review submission, and retry logic.

Uses ``mixing_mode="auto"`` which automatically routes to the optimal
pipeline (text-driven or vision-driven) based on asset analysis.
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
from app.services.intent_parsing_service import IntentParsingService, ParsedIntent
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
        if not request.asset_ids:
            raise ValidationError(message="自动模式需要至少一个素材")

        # Validate all referenced assets exist
        for asset_id in set(request.asset_ids):
            asset = self.db.query(Asset).filter(Asset.id == asset_id).first()
            if not asset:
                raise NotFoundError(message=f"素材不存在: {asset_id}")

        # Parse intent from director_prompt (track LLM success for merge rules)
        llm_parse_succeeded = False
        parsed_intent = ParsedIntent.defaults()
        if request.director_prompt and request.director_prompt.strip():
            try:
                parser = IntentParsingService()
                parsed_intent, llm_parse_succeeded = parser.parse_intent_with_meta(
                    request.director_prompt
                )
            except Exception as e:
                logger.warning("intent parsing failed, using UI defaults: %s", str(e))
                parsed_intent = ParsedIntent.defaults()
                llm_parse_succeeded = False

        # Build UI defaults from request fields
        ui_defaults = {
            "aspect_ratio": request.aspect_ratio,
            "video_count": request.video_count,
            "max_output_duration": request.max_output_duration,
            "tts_text": request.tts_text,
            "bgm_enabled": request.bgm_enabled,
        }

        # Merge: when LLM succeeded, parsed > UI > defaults; otherwise UI > defaults
        merged = IntentParsingService.merge_with_ui_defaults(
            parsed_intent,
            ui_defaults,
            llm_parse_succeeded=llm_parse_succeeded,
        )

        mix_contract = self._build_mix_contract(merged, request.transition)

        # Build mix_params JSON with merged values
        mix_params = json.dumps({
            "mixing_mode": "auto",
            "aspect_ratio": mix_contract["aspect_ratio"],
            "transition": mix_contract["transition"],
            "video_count": mix_contract["video_count"],
            "max_output_duration": mix_contract["max_output_duration"],
            "tts_text": merged.get("tts_text"),
            "tts_voice": request.tts_voice,
            "bgm_enabled": merged.get("bgm_enabled", False),
            "bgm_asset_id": request.bgm_asset_id,
            "bgm_volume": request.bgm_volume,
            "director_prompt": request.director_prompt,
            "strip_audio": merged.get("strip_audio", False),
            "subtitle_font": merged.get("subtitle_font"),
            "editing_style": merged.get("editing_style"),
            "fade_out": merged.get("fade_out", True),
            "fade_out_duration": merged.get("fade_out_duration", 0.3),
            "mix_contract": mix_contract,
            "intent_parse_ok": llm_parse_succeeded,
            "execution_phase": "queued",
            "timeline_only": False,
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

        # Save TaskAsset records — all assets are equal, store as "clip"
        for i, asset_id in enumerate(request.asset_ids):
            ta = TaskAsset(id=generate_uuid(), task_id=task.id, asset_id=asset_id, roll_type="clip", sequence_order=i)
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
        """Run the auto mixing pipeline in a background thread.

        Uses AI-directed pipeline routing based on asset analysis:
        the system automatically routes to text-driven or vision-driven
        pipeline depending on asset characteristics.
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

            if self._mixing_cancelled(db, task_id):
                logger.info("task %s already cancelled before execute_mix body", task_id)
                return

            params = json.loads(task.mix_params)
            self._persist_execution_phase(db, task, params, "starting")

            if self._mixing_cancelled(db, task_id):
                self._persist_execution_phase(db, task, params, "cancelled")
                return

            mix_contract = self._resolve_mix_contract(params)

            aspect_ratio = mix_contract["aspect_ratio"]
            transition = mix_contract["transition"]
            video_count = mix_contract["video_count"]
            tts_text = params.get("tts_text")
            tts_voice = params.get("tts_voice")
            bgm_enabled = params.get("bgm_enabled", False)
            bgm_asset_id = params.get("bgm_asset_id")
            bgm_volume = params.get("bgm_volume", 0.2)
            director_prompt = params.get("director_prompt", "")
            max_output_duration = mix_contract["max_output_duration"]
            strip_audio = params.get("strip_audio", False)
            subtitle_font = params.get("subtitle_font")
            fade_out = params.get("fade_out", True)
            fade_out_duration = params.get("fade_out_duration", 0.3)
            timeline_only = bool(params.get("timeline_only", False))

            # Resolve asset file paths — all assets are equal
            all_asset_paths = []
            # Accept both "clip" (new) and "asset" (legacy) for backward compatibility
            asset_records = (
                db.query(TaskAsset)
                .filter(TaskAsset.task_id == task_id, TaskAsset.roll_type.in_(["clip", "asset"]))
                .order_by(TaskAsset.sequence_order)
                .all()
            )
            for ta in asset_records:
                asset = db.query(Asset).filter(Asset.id == ta.asset_id).first()
                if asset and asset.file_path:
                    all_asset_paths.append(asset.file_path)

            output_dir = f"storage/tasks/{task_id}"
            os.makedirs(output_dir, exist_ok=True)

            aspect_resolutions = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
            video_w, video_h = aspect_resolutions.get(aspect_ratio, (1080, 1920))

            ai_director_used = False
            output_paths = []

            # --- Auto mode: AI-directed pipeline routing ---
            # The text-driven pipeline may generate multiple output files
            # (output-1.mp4, output-2.mp4, ...) in a single call when the
            # user requests "剪成N条".  We call the pipeline once and then
            # discover all generated outputs.
            from app.services.ai_director_service import AIDirectorService

            audio_file = None
            duration = None

            # TTS is optional; timeline-only rerun intentionally skips TTS/subtitle/BGM passes
            if (not timeline_only) and tts_text and tts_text.strip():
                self._persist_execution_phase(db, task, params, "tts")
                if self._mixing_cancelled(db, task_id):
                    self._persist_execution_phase(db, task, params, "cancelled")
                    return
                from app.services.ai_tts_service import AITTSService
                ai_tts = AITTSService()
                audio_file, duration = ai_tts.synthesize(tts_text, task_id, tts_voice)
                logger.info("auto mode: TTS audio generated (%.1fs)", duration)
                if self._mixing_cancelled(db, task_id):
                    self._persist_execution_phase(db, task, params, "cancelled")
                    return

            # Collect asset_ids for DB analysis lookup
            asset_id_list = []
            original_filename_list = []
            asset_records_for_auto = (
                db.query(TaskAsset)
                .filter(TaskAsset.task_id == task_id, TaskAsset.roll_type.in_(["clip", "asset"]))
                .order_by(TaskAsset.sequence_order)
                .all()
            )
            for ta in asset_records_for_auto:
                asset_id_list.append(ta.asset_id)
                asset = db.query(Asset).filter(Asset.id == ta.asset_id).first()
                if asset and asset.original_filename:
                    original_filename_list.append(asset.original_filename)
                else:
                    fallback_name = os.path.basename(asset.file_path) if asset and asset.file_path else ta.asset_id
                    original_filename_list.append(fallback_name)

            def _auto_progress(status: str) -> None:
                try:
                    task_ref = db.query(Task).filter(Task.id == task_id).first()
                    if task_ref:
                        task_ref.updated_at = utcnow()
                        db.commit()
                except Exception:
                    pass

            self._persist_execution_phase(db, task, params, "ai_directing")
            if self._mixing_cancelled(db, task_id):
                self._persist_execution_phase(db, task, params, "cancelled")
                return

            ai_director = AIDirectorService(task_id, output_dir)
            raw_output, ai_used = ai_director.run_auto_pipeline(
                clip_paths=all_asset_paths,
                asset_ids=asset_id_list,
                clip_original_filenames=original_filename_list,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=_auto_progress,
                director_prompt=director_prompt,
                video_count=video_count,
            )
            if ai_used:
                ai_director_used = True

            if self._mixing_cancelled(db, task_id):
                self._persist_execution_phase(db, task, params, "cancelled")
                return

            self._persist_execution_phase(
                db,
                task,
                params,
                "timeline_only_export" if timeline_only else "encoding",
            )

            # Discover all output files generated by the pipeline
            # The text-driven pipeline may have created output-1.mp4, output-2.mp4, etc.
            raw_outputs = []
            for vid_num in range(1, 20):  # reasonable upper bound
                candidate = os.path.join(output_dir, f"output-{vid_num}.mp4")
                if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                    raw_outputs.append((vid_num, candidate))
                else:
                    break
            if not raw_outputs:
                # Fallback: use whatever the pipeline returned
                raw_outputs = [(1, raw_output)]

            logger.info("auto pipeline produced %d output(s) for task %s", len(raw_outputs), task_id)

            # Timeline-only rerun: keep only timeline recomposition result and skip all post-processing.
            if timeline_only:
                for version, raw_path in raw_outputs:
                    if self._mixing_cancelled(db, task_id):
                        self._persist_execution_phase(db, task, params, "cancelled")
                        return
                    version_output = os.path.join(output_dir, f"output-{version}.mp4")
                    if raw_path != version_output:
                        import shutil as _shutil
                        _shutil.copy(raw_path, version_output)
                    output_paths.append(version_output)
            # Full pipeline: subtitles + BGM + strip_audio
            else:
                # Post-process each output: subtitles + BGM + strip_audio
                for version, raw_path in raw_outputs:
                    if self._mixing_cancelled(db, task_id):
                        self._persist_execution_phase(db, task, params, "cancelled")
                        return
                    version_output = os.path.join(output_dir, f"output-{version}.mp4")

                    # Subtitles
                    if tts_text and tts_text.strip() and duration:
                        # TTS script → script-based subtitles
                        ass_path = os.path.join(output_dir, f"subtitles-{version}.ass")
                        try:
                            mixing_engine.generate_subtitles_from_script(
                                tts_text, duration, ass_path, video_w, video_h,
                                font_name=subtitle_font,
                            )
                            final_path = os.path.join(output_dir, f"final-{version}.mp4")
                            mixing_engine.burn_subtitles(raw_path, ass_path, final_path)
                            os.replace(final_path, version_output)
                        except Exception as e:
                            logger.warning("subtitle failed: %s", str(e)[:200])
                            import shutil as _shutil
                            if raw_path != version_output:
                                _shutil.copy(raw_path, version_output)
                    else:
                        # No TTS → Whisper ASR subtitles on the output
                        ass_path = os.path.join(output_dir, f"subtitles-{version}.ass")
                        try:
                            mixing_engine._generate_subtitles(
                                raw_path, ass_path, video_w, video_h,
                                font_name=subtitle_font,
                            )
                            if os.path.exists(ass_path) and os.path.getsize(ass_path) > 0:
                                final_path = os.path.join(output_dir, f"final-{version}.mp4")
                                mixing_engine.burn_subtitles(raw_path, ass_path, final_path)
                                os.replace(final_path, version_output)
                            else:
                                import shutil as _shutil
                                if raw_path != version_output:
                                    _shutil.copy(raw_path, version_output)
                        except Exception as e:
                            logger.warning("whisper subtitle failed: %s", str(e)[:200])
                            import shutil as _shutil
                            if raw_path != version_output:
                                _shutil.copy(raw_path, version_output)

                    # BGM mixing
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

                    # Fade out — apply video + audio fade to the end of each video
                    if fade_out:
                        try:
                            mixing_engine._apply_tail_fadeout(
                                mixing_engine._get_ffmpeg_binary(),
                                version_output,
                                fade_duration=fade_out_duration,
                            )
                        except Exception as e:
                            logger.warning("fade_out failed: %s", str(e)[:200])

                    # Strip audio — remove all audio tracks from the final output
                    if strip_audio:
                        try:
                            import subprocess
                            stripped_path = os.path.join(output_dir, f"output-{version}-stripped.mp4")
                            ffmpeg_bin = mixing_engine._get_ffmpeg_binary()
                            cmd = [
                                ffmpeg_bin, "-y",
                                "-i", version_output,
                                "-an",
                                "-c:v", "copy",
                                "-movflags", "+faststart",
                                stripped_path,
                            ]
                            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
                            if result.returncode == 0 and os.path.exists(stripped_path) and os.path.getsize(stripped_path) > 0:
                                os.replace(stripped_path, version_output)
                                logger.info("strip_audio: removed audio from output-%d", version)
                            else:
                                logger.warning("strip_audio failed (rc=%d): %s", result.returncode, (result.stderr or "")[:200])
                        except Exception as e:
                            logger.warning("strip_audio failed: %s", str(e)[:200])

                    output_paths.append(version_output)

            expected_outputs = max(1, int(video_count))
            actual_outputs = len(output_paths)
            if actual_outputs < expected_outputs:
                raise RuntimeError(
                    f"mix contract violated: expected {expected_outputs} outputs, got {actual_outputs}"
                )

            # Store ai_director_used in mix_params
            params["ai_director_used"] = ai_director_used
            params["mix_contract"] = mix_contract
            params["execution_phase"] = "done"
            params["timeline_only"] = timeline_only
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
                if task and task.status == "cancelled":
                    logger.info("task %s was cancelled; not marking failed", task_id)
                    return
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

        execution_phase = None
        ai_director_used = None
        if task.mix_params:
            try:
                mp = json.loads(task.mix_params)
                if isinstance(mp, dict):
                    execution_phase = mp.get("execution_phase")
                    ai_director_used = mp.get("ai_director_used")
            except (json.JSONDecodeError, TypeError):
                pass

        phase_labels = {
            "queued": "排队中",
            "starting": "启动中",
            "tts": "生成配音",
            "ai_directing": "AI 导演编排",
            "encoding": "后期与导出",
            "timeline_only_export": "仅时间线导出",
            "done": "收尾",
            "cancelled": "已取消",
        }
        progress = None
        if task.status == "processing":
            phase_note = phase_labels.get(execution_phase or "", "")
            progress = (
                f"正在处理中…（{phase_note}）" if phase_note else "正在处理中…"
            )
        elif task.status == "video_done":
            progress = "处理完成"
        elif task.status == "failed":
            progress = "处理失败"
        elif task.status == "cancelled":
            progress = "已取消"

        return {
            "task_id": task.id,
            "status": task.status,
            "progress": progress,
            "execution_phase": execution_phase,
            "video_paths": video_paths,
            "video_resolution": task.video_resolution,
            "video_duration": task.video_duration,
            "video_file_size": task.video_file_size,
            "error_message": task.error_message,
            "ai_director_used": ai_director_used,
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
    # recompose — same inputs, run pipeline again (e.g. from video_done)
    # ------------------------------------------------------------------

    def recompose(self, task_id: str) -> Task:
        return self._recompose_with_mode(task_id, timeline_only=False)

    def recompose_timeline_only(self, task_id: str) -> Task:
        """Re-run only timeline recomposition and skip post-processing."""
        return self._recompose_with_mode(task_id, timeline_only=True)

    def _recompose_with_mode(self, task_id: str, timeline_only: bool) -> Task:
        """Re-run mixing for an existing task (completed or recoverable states).

        Unlike :meth:`retry`, this allows ``video_done`` so users can regenerate
        with the same assets and stored mix params without going through
        ``failed`` / ``rejected`` first.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise NotFoundError(message=f"任务不存在: {task_id}")

        result = self.db.execute(
            update(Task)
            .where(Task.id == task_id, Task.status.in_(["video_done", "failed", "rejected"]))
            .values(
                status="processing",
                error_message=None,
                video_paths=None,
                video_resolution=None,
                video_duration=None,
                video_file_size=None,
                updated_at=utcnow(),
            )
        )
        if result.rowcount == 0:
            raise StateTransitionError(
                message=(
                    "仅已完成、失败或驳回的任务可重新混剪，当前状态: "
                    f"{task.status}"
                ),
                details={"current_status": task.status},
            )

        self.db.commit()
        self.db.refresh(task)

        # Mark queued in mix_params for status UI
        try:
            mp = json.loads(task.mix_params) if task.mix_params else {}
            if isinstance(mp, dict):
                mp["execution_phase"] = "queued"
                mp["timeline_only"] = timeline_only
                task.mix_params = json.dumps(mp, ensure_ascii=False)
                self.db.commit()
                self.db.refresh(task)
        except (json.JSONDecodeError, TypeError):
            pass

        thread = threading.Thread(
            target=self.execute_mix,
            args=(task.id,),
            daemon=True,
        )
        thread.start()

        return task

    # ------------------------------------------------------------------
    # cancel
    # ------------------------------------------------------------------

    def cancel(self, task_id: str) -> Task:
        """Cancel a processing mixing task.

        Marks the task as 'cancelled'. The background thread checks this
        status and stops at the next checkpoint.

        Args:
            task_id: The task ID.

        Returns:
            Updated Task record.

        Raises:
            NotFoundError: If the task does not exist.
            StateTransitionError: If the task is not in a cancellable state.
        """
        task = self.db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise NotFoundError(message=f"任务不存在: {task_id}")

        # Can cancel tasks that are processing or video_done (before review)
        result = self.db.execute(
            update(Task)
            .where(Task.id == task_id, Task.status.in_(["processing", "video_done"]))
            .values(status="cancelled", error_message="用户取消", updated_at=utcnow())
        )
        if result.rowcount == 0:
            raise StateTransitionError(
                message=f"当前状态 {task.status} 不可取消",
                details={"current_status": task.status},
            )

        self.db.commit()
        self.db.refresh(task)
        logger.info("task %s cancelled by user", task_id)
        return task

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mixing_cancelled(db: Session, task_id: str) -> bool:
        row = db.query(Task).filter(Task.id == task_id).first()
        return row is not None and row.status == "cancelled"

    @staticmethod
    def _persist_execution_phase(db: Session, task: Task, params: dict, phase: str) -> None:
        params["execution_phase"] = phase
        task.mix_params = json.dumps(params, ensure_ascii=False)
        task.updated_at = utcnow()
        db.commit()

    @staticmethod
    def _build_mix_contract(merged: dict, request_transition: str) -> dict:
        """Build an execution contract from merged intent values."""
        try:
            video_count = int(merged.get("video_count", 1) or 1)
        except (TypeError, ValueError):
            video_count = 1
        try:
            max_output_duration = int(merged.get("max_output_duration", 60) or 60)
        except (TypeError, ValueError):
            max_output_duration = 60

        return {
            "contract_version": 1,
            "video_count": max(1, video_count),
            "max_output_duration": max(1, max_output_duration),
            "aspect_ratio": merged.get("aspect_ratio", "9:16"),
            "transition": merged.get("transition", request_transition or "none"),
            "strip_audio": bool(merged.get("strip_audio", False)),
        }

    @staticmethod
    def _resolve_mix_contract(params: dict) -> dict:
        """Resolve contract from mix_params, supporting legacy tasks."""
        contract = params.get("mix_contract")
        if isinstance(contract, dict):
            resolved = {
                "contract_version": contract.get("contract_version", 1),
                "video_count": contract.get("video_count", params.get("video_count", 1)),
                "max_output_duration": contract.get(
                    "max_output_duration", params.get("max_output_duration", 60)
                ),
                "aspect_ratio": contract.get("aspect_ratio", params.get("aspect_ratio", "9:16")),
                "transition": contract.get("transition", params.get("transition", "none")),
                "strip_audio": contract.get("strip_audio", params.get("strip_audio", False)),
            }
        else:
            resolved = {
                "contract_version": 0,
                "video_count": params.get("video_count", 1),
                "max_output_duration": params.get("max_output_duration", 60),
                "aspect_ratio": params.get("aspect_ratio", "9:16"),
                "transition": params.get("transition", "none"),
                "strip_audio": params.get("strip_audio", False),
            }

        try:
            resolved["video_count"] = max(1, int(resolved["video_count"] or 1))
        except (TypeError, ValueError):
            resolved["video_count"] = 1
        try:
            resolved["max_output_duration"] = max(1, int(resolved["max_output_duration"] or 60))
        except (TypeError, ValueError):
            resolved["max_output_duration"] = 60
        resolved["aspect_ratio"] = resolved.get("aspect_ratio") or "9:16"
        resolved["transition"] = resolved.get("transition") or "none"
        resolved["strip_audio"] = bool(resolved.get("strip_audio", False))
        return resolved

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
