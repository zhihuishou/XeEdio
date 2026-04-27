"""Unit tests for mixing_mode='auto' in MixingService.

Tests:
- Schema validation: 'auto' is accepted by MixCreateRequest
- create_mix_task validation: 'auto' requires asset_ids
- create_mix_task: 'auto' stores TaskAsset records with roll_type='clip'
- execute_mix: 'auto' branch calls AIDirectorService.run_auto_pipeline()
  with correct parameters (clip_paths, asset_ids, aspect_ratio, etc.)
- execute_mix: 'auto' branch handles TTS when tts_text is provided
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing modules under test.
# mixing_service.py has a deep dependency chain:
#   sqlalchemy, app.models.database, app.services.config_service,
#   app.services.task_service, app.utils.errors, etc.
# We mock them all at sys.modules level before importing.
# ---------------------------------------------------------------------------

for mod_name in [
    "moviepy", "moviepy.video", "moviepy.video.io",
    "moviepy.video.io.VideoFileClip",
    "edge_tts",
    "faster_whisper",
    "httpx",
    "fastapi", "fastapi.responses",
    "starlette", "starlette.middleware", "starlette.middleware.base",
    "starlette.requests", "starlette.responses",
]:
    sys.modules.setdefault(mod_name, MagicMock())

# Mock sqlalchemy
_mock_sa = MagicMock()
sys.modules["sqlalchemy"] = _mock_sa
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
_mock_sa.update = MagicMock()

_mock_orm = MagicMock()
sys.modules["sqlalchemy.orm"] = _mock_orm
_mock_orm.DeclarativeBase = type("DeclarativeBase", (), {})
_mock_orm.Session = MagicMock()
_mock_orm.relationship = MagicMock()
_mock_orm.sessionmaker = MagicMock(return_value=MagicMock())

sys.modules["sqlalchemy.ext"] = MagicMock()
sys.modules["sqlalchemy.ext.declarative"] = MagicMock()

# Mock mixing_engine
_mock_mixing_engine = MagicMock()
sys.modules["app.services.mixing_engine"] = _mock_mixing_engine

# Now import the modules under test
from app.schemas.mix import MixCreateRequest
from app.services.mixing_service import MixingService
from app.utils.errors import ValidationError


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

class TestMixCreateRequestAutoMode:
    """Tests that MixCreateRequest schema accepts 'auto' as mixing_mode."""

    def test_auto_mode_accepted(self):
        """mixing_mode='auto' should be valid."""
        req = MixCreateRequest(
            topic="测试主题",
            asset_ids=["asset-001"],
            mixing_mode="auto",
        )
        assert req.mixing_mode == "auto"

    def test_auto_mode_with_all_params(self):
        """mixing_mode='auto' with all optional params should be valid."""
        req = MixCreateRequest(
            topic="测试主题",
            asset_ids=["asset-001", "asset-002"],
            mixing_mode="auto",
            aspect_ratio="16:9",
            transition="fade_in",
            max_output_duration=120,
            tts_text="配音文本",
            tts_voice="longyan_v2",
            director_prompt="多用特写镜头",
        )
        assert req.mixing_mode == "auto"
        assert req.aspect_ratio == "16:9"
        assert req.director_prompt == "多用特写镜头"

    def test_invalid_mode_rejected(self):
        """An invalid mixing_mode should be rejected."""
        with pytest.raises(Exception):
            MixCreateRequest(
                topic="测试主题",
                asset_ids=["asset-001"],
                mixing_mode="invalid_mode",
            )

    def test_old_modes_rejected(self):
        """Old deprecated modes should no longer be accepted."""
        for mode in ("pure_mix", "mix_with_script", "broll_voiceover", "montage"):
            with pytest.raises(Exception):
                MixCreateRequest(topic="测试", mixing_mode=mode)


# ---------------------------------------------------------------------------
# MixingService.create_mix_task validation tests
# ---------------------------------------------------------------------------

class TestCreateMixTaskAutoValidation:
    """Tests that create_mix_task validates 'auto' mode correctly."""

    def test_auto_mode_requires_asset_ids(self):
        """auto mode with empty asset_ids should raise ValidationError."""
        mock_db = MagicMock()
        service = MixingService(mock_db)

        request = MixCreateRequest(
            topic="测试",
            asset_ids=[],
            mixing_mode="auto",
        )

        with pytest.raises(ValidationError, match="自动模式需要至少一个素材"):
            service.create_mix_task(request, user_id="user-001")

    def test_auto_mode_stores_assets_as_clip_roll_type(self):
        """auto mode should store TaskAsset records with roll_type='clip'."""
        mock_db = MagicMock()

        # Mock asset lookup to return a valid asset
        mock_asset = MagicMock()
        mock_asset.id = "asset-001"
        mock_asset.file_path = "/tmp/clip.mp4"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_asset

        service = MixingService(mock_db)

        request = MixCreateRequest(
            topic="测试",
            asset_ids=["asset-001"],
            mixing_mode="auto",
        )

        # Patch Task and TaskAsset to accept kwargs
        class FakeTask:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        class FakeTaskAsset:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        with patch("app.services.mixing_service.threading"), \
             patch("app.services.mixing_service.Task", FakeTask), \
             patch("app.services.mixing_service.TaskAsset", FakeTaskAsset):
            task = service.create_mix_task(request, user_id="user-001")

        # Verify db.add was called for the Task and TaskAsset
        add_calls = mock_db.add.call_args_list
        assert len(add_calls) >= 2  # Task + at least 1 TaskAsset

        # Check the TaskAsset was created with roll_type="clip"
        task_asset_obj = add_calls[1][0][0]
        assert task_asset_obj.roll_type == "clip"

    def test_auto_mode_mix_params_include_mixing_mode(self):
        """auto mode should store mixing_mode='auto' in mix_params JSON."""
        mock_db = MagicMock()
        mock_asset = MagicMock()
        mock_asset.id = "asset-001"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_asset

        service = MixingService(mock_db)

        request = MixCreateRequest(
            topic="测试",
            asset_ids=["asset-001"],
            mixing_mode="auto",
        )

        class FakeTask:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        class FakeTaskAsset:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        with patch("app.services.mixing_service.threading"), \
             patch("app.services.mixing_service.Task", FakeTask), \
             patch("app.services.mixing_service.TaskAsset", FakeTaskAsset):
            task = service.create_mix_task(request, user_id="user-001")

        # The Task object was added to db — check its mix_params
        task_obj = mock_db.add.call_args_list[0][0][0]
        params = json.loads(task_obj.mix_params)
        assert params["mixing_mode"] == "auto"
        assert "mix_contract" in params
        assert params["mix_contract"]["contract_version"] == 1
        assert params["mix_contract"]["video_count"] == 1


# ---------------------------------------------------------------------------
# MixingService.execute_mix auto branch tests
# ---------------------------------------------------------------------------

class TestExecuteMixAutoBranch:
    """Tests that execute_mix routes 'auto' mode to run_auto_pipeline."""

    def _setup_mock_db(self, mock_session_local, task_params, asset_paths=None):
        """Helper to set up a mock DB session for execute_mix tests."""
        if asset_paths is None:
            asset_paths = ["/tmp/clip1.mp4"]

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        mock_task = MagicMock()
        mock_task.id = "task-001"
        mock_task.mix_params = json.dumps(task_params)
        mock_task.status = "processing"

        # Build mock TaskAsset and Asset records
        mock_task_assets = []
        mock_assets = {}
        for i, path in enumerate(asset_paths):
            ta = MagicMock()
            ta.asset_id = f"asset-{i:03d}"
            ta.roll_type = "clip"
            ta.sequence_order = i
            mock_task_assets.append(ta)

            asset = MagicMock()
            asset.id = f"asset-{i:03d}"
            asset.file_path = path
            mock_assets[asset.id] = asset

        # Build a list of assets in order for sequential first() calls
        asset_list = [mock_assets[f"asset-{i:03d}"] for i in range(len(asset_paths))]

        def query_side_effect(model):
            q = MagicMock()
            name = getattr(model, "__name__", str(model))
            if name == "Task":
                q.filter.return_value.first.return_value = mock_task
            elif name == "TaskAsset":
                q.filter.return_value.order_by.return_value.all.return_value = mock_task_assets
            elif name == "Asset":
                q.filter.return_value.first.side_effect = asset_list
            return q

        mock_db.query.side_effect = query_side_effect
        return mock_db, mock_task

    @patch("app.services.mixing_service.SessionLocal")
    @patch("app.services.mixing_service._mix_semaphore")
    def test_auto_mode_calls_run_auto_pipeline(self, mock_semaphore, mock_session_local):
        """auto mode should call AIDirectorService.run_auto_pipeline with correct params."""
        mock_semaphore.acquire.return_value = True

        task_params = {
            "mixing_mode": "auto",
            "aspect_ratio": "9:16",
            "transition": "none",
            "video_count": 1,
            "max_output_duration": 60,
            "tts_text": None,
            "tts_voice": None,
            "bgm_enabled": False,
            "bgm_asset_id": None,
            "bgm_volume": 0.2,
            "director_prompt": "多用特写",
        }

        mock_db, mock_task = self._setup_mock_db(mock_session_local, task_params)

        mock_director = MagicMock()
        mock_director.run_auto_pipeline.return_value = ("/tmp/output-1.mp4", True)

        with patch("app.services.mixing_service.os.makedirs"), \
             patch("app.services.mixing_service.os.path.exists", return_value=True), \
             patch("app.services.mixing_service.os.path.getsize", return_value=1000), \
             patch("app.services.mixing_service.os.path.relpath", side_effect=lambda p: p), \
             patch("app.services.mixing_service.MixingService._probe_duration", return_value=30.0), \
             patch("app.services.ai_director_service.AIDirectorService", return_value=mock_director):

            service = MixingService(mock_db)
            service.execute_mix("task-001")

        # Verify run_auto_pipeline was called
        mock_director.run_auto_pipeline.assert_called_once()
        call_kwargs = mock_director.run_auto_pipeline.call_args
        assert call_kwargs.kwargs["clip_paths"] == ["/tmp/clip1.mp4"]
        assert call_kwargs.kwargs["asset_ids"] == ["asset-000"]
        assert call_kwargs.kwargs["aspect_ratio"] == "9:16"
        assert call_kwargs.kwargs["transition"] == "none"
        assert call_kwargs.kwargs["max_output_duration"] == 60
        assert call_kwargs.kwargs["director_prompt"] == "多用特写"
        assert call_kwargs.kwargs["audio_file"] is None
        assert call_kwargs.kwargs["video_count"] == 1

    @patch("app.services.mixing_service.SessionLocal")
    @patch("app.services.mixing_service._mix_semaphore")
    def test_auto_mode_with_tts(self, mock_semaphore, mock_session_local):
        """auto mode with tts_text should synthesize TTS and pass audio_file."""
        mock_semaphore.acquire.return_value = True

        task_params = {
            "mixing_mode": "auto",
            "aspect_ratio": "9:16",
            "transition": "none",
            "video_count": 1,
            "max_output_duration": 60,
            "tts_text": "这是配音文本",
            "tts_voice": "longyan_v2",
            "bgm_enabled": False,
            "bgm_asset_id": None,
            "bgm_volume": 0.2,
            "director_prompt": "",
        }

        mock_db, mock_task = self._setup_mock_db(mock_session_local, task_params)

        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = ("/tmp/tts_audio.mp3", 15.0)

        mock_director = MagicMock()
        mock_director.run_auto_pipeline.return_value = ("/tmp/output-1.mp4", True)

        with patch("app.services.mixing_service.os.makedirs"), \
             patch("app.services.mixing_service.os.path.exists", return_value=True), \
             patch("app.services.mixing_service.os.path.getsize", return_value=1000), \
             patch("app.services.mixing_service.os.path.relpath", side_effect=lambda p: p), \
             patch("app.services.mixing_service.MixingService._probe_duration", return_value=30.0), \
             patch("app.services.ai_director_service.AIDirectorService", return_value=mock_director), \
             patch("app.services.ai_tts_service.AITTSService", return_value=mock_tts):

            service = MixingService(mock_db)
            service.execute_mix("task-001")

        # Verify TTS was called
        mock_tts.synthesize.assert_called_once_with("这是配音文本", "task-001", "longyan_v2")

        # Verify run_auto_pipeline received the TTS audio file
        call_kwargs = mock_director.run_auto_pipeline.call_args
        assert call_kwargs.kwargs["audio_file"] == "/tmp/tts_audio.mp3"

    @patch("app.services.mixing_service.SessionLocal")
    @patch("app.services.mixing_service._mix_semaphore")
    def test_auto_mode_multiple_assets(self, mock_semaphore, mock_session_local):
        """auto mode with multiple assets should pass all clip_paths and asset_ids."""
        mock_semaphore.acquire.return_value = True

        task_params = {
            "mixing_mode": "auto",
            "aspect_ratio": "16:9",
            "transition": "fade_in",
            "video_count": 1,
            "max_output_duration": 120,
            "tts_text": None,
            "tts_voice": None,
            "bgm_enabled": False,
            "bgm_asset_id": None,
            "bgm_volume": 0.2,
            "director_prompt": "选精华片段",
        }

        mock_db, mock_task = self._setup_mock_db(
            mock_session_local, task_params,
            asset_paths=["/tmp/clip1.mp4", "/tmp/clip2.mp4"],
        )

        mock_director = MagicMock()
        mock_director.run_auto_pipeline.return_value = ("/tmp/output-1.mp4", True)

        with patch("app.services.mixing_service.os.makedirs"), \
             patch("app.services.mixing_service.os.path.exists", return_value=True), \
             patch("app.services.mixing_service.os.path.getsize", return_value=1000), \
             patch("app.services.mixing_service.os.path.relpath", side_effect=lambda p: p), \
             patch("app.services.mixing_service.MixingService._probe_duration", return_value=30.0), \
             patch("app.services.ai_director_service.AIDirectorService", return_value=mock_director):

            service = MixingService(mock_db)
            service.execute_mix("task-001")

        call_kwargs = mock_director.run_auto_pipeline.call_args
        assert len(call_kwargs.kwargs["clip_paths"]) == 2
        assert len(call_kwargs.kwargs["asset_ids"]) == 2
        assert call_kwargs.kwargs["aspect_ratio"] == "16:9"
        assert call_kwargs.kwargs["transition"] == "fade_in"
        assert call_kwargs.kwargs["max_output_duration"] == 120
        assert call_kwargs.kwargs["director_prompt"] == "选精华片段"

    @patch("app.services.mixing_service.SessionLocal")
    @patch("app.services.mixing_service._mix_semaphore")
    def test_timeline_only_rerun_skips_tts_and_postprocess(self, mock_semaphore, mock_session_local):
        """timeline_only should recompute timeline without TTS/subtitle/BGM post-process."""
        mock_semaphore.acquire.return_value = True

        task_params = {
            "mixing_mode": "auto",
            "aspect_ratio": "9:16",
            "transition": "none",
            "video_count": 1,
            "max_output_duration": 60,
            "tts_text": "这段文案应该被忽略",
            "tts_voice": "longyan_v2",
            "bgm_enabled": True,
            "bgm_asset_id": "bgm-001",
            "bgm_volume": 0.2,
            "director_prompt": "重算时间线",
            "timeline_only": True,
        }

        mock_db, _mock_task = self._setup_mock_db(mock_session_local, task_params)

        mock_director = MagicMock()
        mock_director.run_auto_pipeline.return_value = ("/tmp/output-1.mp4", True)
        _mock_mixing_engine.generate_subtitles_from_script.reset_mock()
        _mock_mixing_engine._generate_subtitles.reset_mock()
        _mock_mixing_engine.mix_bgm.reset_mock()

        with patch("app.services.mixing_service.os.makedirs"), \
             patch("app.services.mixing_service.os.path.exists", return_value=True), \
             patch("app.services.mixing_service.os.path.getsize", return_value=1000), \
             patch("app.services.mixing_service.os.path.relpath", side_effect=lambda p: p), \
             patch("app.services.mixing_service.MixingService._probe_duration", return_value=30.0), \
             patch("app.services.ai_director_service.AIDirectorService", return_value=mock_director), \
             patch("app.services.ai_tts_service.AITTSService") as mock_tts_cls:

            service = MixingService(mock_db)
            service.execute_mix("task-001")

        mock_tts_cls.assert_not_called()
        _mock_mixing_engine.generate_subtitles_from_script.assert_not_called()
        _mock_mixing_engine._generate_subtitles.assert_not_called()
        _mock_mixing_engine.mix_bgm.assert_not_called()
