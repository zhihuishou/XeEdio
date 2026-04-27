"""Unit tests for VLMService.generate_unified_timeline() method.

Tests the unified timeline generation (Stage 2 — mix-time):
- Successful timeline generation with valid VLM response
- VLM API not configured → returns None
- Empty clip_summaries / clip_metadata → returns None
- VLM API call failure → returns None
- JSON parse failure → returns None
- Validation: missing fields, invalid clip_index, source time out of range
- Validation: output timeline overlaps
- Content builder: summaries, frames, user_prompt, script_text
"""

import json
import sys
from unittest.mock import MagicMock, patch

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

from app.services.vlm_service import VLMService


# ---------------------------------------------------------------------------
# Fixtures & sample data
# ---------------------------------------------------------------------------

SAMPLE_CLIP_SUMMARIES = [
    {
        "description": "女主播正面讲解护肤品，手持产品展示",
        "role": "presenter",
        "visual_quality": "high",
        "key_moments": [
            {"time": 2.0, "desc": "展示产品包装"},
            {"time": 5.0, "desc": "涂抹演示"},
        ],
        "scene_tags": ["室内", "美妆", "产品展示"],
    },
    {
        "description": "产品特写镜头，白色背景",
        "role": "product_closeup",
        "visual_quality": "high",
        "key_moments": [{"time": 1.0, "desc": "产品正面"}],
        "scene_tags": ["产品", "特写"],
    },
]

SAMPLE_CLIP_METADATA = [
    {"filename": "主播口播.mp4", "duration": 30.0},
    {"filename": "产品特写.mp4", "duration": 8.0},
]

SAMPLE_DENSE_FRAMES = [
    [(0.0, "b64_clip0_frame0"), (2.0, "b64_clip0_frame1")],
    [(0.0, "b64_clip1_frame0")],
]

VALID_TIMELINE_RESPONSE = json.dumps([
    {
        "clip_index": 0, "source_start": 0.0, "source_end": 8.0,
        "start": 0.0, "end": 8.0, "reason": "主播开场介绍",
    },
    {
        "clip_index": 1, "source_start": 0.0, "source_end": 2.0,
        "start": 8.0, "end": 10.0, "reason": "插入产品特写",
    },
    {
        "clip_index": 0, "source_start": 8.0, "source_end": 18.0,
        "start": 10.0, "end": 20.0, "reason": "主播继续讲解",
    },
])


@pytest.fixture
def vlm_service():
    """Create a VLMService with mocked config."""
    with patch("app.services.vlm_service.ExternalConfig") as mock_config_cls:
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {
            "api_url": "https://api.example.com/v1/chat/completions",
            "api_key": "test-key-123",
            "model": "gpt-5.4",
        }
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()
    return svc


@pytest.fixture
def vlm_service_no_config():
    """Create a VLMService with no VLM API configured."""
    with patch("app.services.vlm_service.ExternalConfig") as mock_config_cls:
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {"api_url": "", "api_key": ""}
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()
    return svc


# ---------------------------------------------------------------------------
# Tests: Successful generation
# ---------------------------------------------------------------------------

class TestGenerateUnifiedTimelineSuccess:
    """Tests for successful unified timeline generation."""

    @patch.object(VLMService, "_call_vlm_api")
    def test_returns_valid_timeline_on_success(self, mock_call, vlm_service):
        """Valid VLM response → returns list of timeline entries."""
        mock_call.return_value = VALID_TIMELINE_RESPONSE

        result = vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )

        assert result is not None
        assert len(result) == 3
        assert result[0]["clip_index"] == 0
        assert result[0]["source_start"] == 0.0
        assert result[0]["source_end"] == 8.0
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 8.0
        assert result[0]["reason"] == "主播开场介绍"

    @patch.object(VLMService, "_call_vlm_api")
    def test_calls_vlm_api_with_correct_payload(self, mock_call, vlm_service):
        """Verify VLM API is called with proper model, system prompt, and content."""
        mock_call.return_value = VALID_TIMELINE_RESPONSE

        vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
            user_prompt="多用产品特写",
        )

        mock_call.assert_called_once()
        args = mock_call.call_args
        api_url = args[0][0]
        payload = args[0][1]
        headers = args[0][2]

        assert api_url == "https://api.example.com/v1/chat/completions"
        assert payload["model"] == "gpt-5.4"
        assert payload["temperature"] == 0.3
        assert payload["max_tokens"] == 4096
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert "Bearer test-key-123" in headers["Authorization"]

    @patch.object(VLMService, "_call_vlm_api")
    def test_user_content_includes_summaries(self, mock_call, vlm_service):
        """User content should include clip summaries from DB."""
        mock_call.return_value = VALID_TIMELINE_RESPONSE

        vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )

        payload = mock_call.call_args[0][1]
        user_content = payload["messages"][1]["content"]
        text_parts = [p["text"] for p in user_content if p["type"] == "text"]
        all_text = "\n".join(text_parts)

        assert "主播口播.mp4" in all_text
        assert "产品特写.mp4" in all_text
        assert "presenter" in all_text
        assert "product_closeup" in all_text
        assert "女主播正面讲解护肤品" in all_text

    @patch.object(VLMService, "_call_vlm_api")
    def test_user_content_includes_dense_frames(self, mock_call, vlm_service):
        """User content should include dense frame images."""
        mock_call.return_value = VALID_TIMELINE_RESPONSE

        vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )

        payload = mock_call.call_args[0][1]
        user_content = payload["messages"][1]["content"]
        image_parts = [p for p in user_content if p["type"] == "image_url"]

        # 2 frames for clip 0 + 1 frame for clip 1 = 3 total
        assert len(image_parts) == 3

    @patch.object(VLMService, "_call_vlm_api")
    def test_user_prompt_included_in_content(self, mock_call, vlm_service):
        """User prompt should appear in the content."""
        mock_call.return_value = VALID_TIMELINE_RESPONSE

        vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
            user_prompt="多用产品特写镜头",
        )

        payload = mock_call.call_args[0][1]
        user_content = payload["messages"][1]["content"]
        text_parts = [p["text"] for p in user_content if p["type"] == "text"]
        all_text = "\n".join(text_parts)

        assert "多用产品特写镜头" in all_text

    @patch.object(VLMService, "_call_vlm_api")
    def test_script_text_included_in_content(self, mock_call, vlm_service):
        """Script text should appear in the content when provided."""
        mock_call.return_value = VALID_TIMELINE_RESPONSE

        vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
            script_text="今天给大家推荐一款护肤品",
        )

        payload = mock_call.call_args[0][1]
        user_content = payload["messages"][1]["content"]
        text_parts = [p["text"] for p in user_content if p["type"] == "text"]
        all_text = "\n".join(text_parts)

        assert "今天给大家推荐一款护肤品" in all_text
        assert "脚本文案" in all_text

    @patch.object(VLMService, "_call_vlm_api")
    def test_empty_dense_frames_still_works(self, mock_call, vlm_service):
        """Empty dense_frames list → still generates timeline (text-only prompt)."""
        mock_call.return_value = VALID_TIMELINE_RESPONSE

        result = vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, [],
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )

        assert result is not None
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Tests: Failure cases
# ---------------------------------------------------------------------------

class TestGenerateUnifiedTimelineFailures:
    """Tests for failure scenarios returning None."""

    def test_no_api_config_returns_none(self, vlm_service_no_config):
        """VLM API not configured → returns None."""
        result = vlm_service_no_config.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )
        assert result is None

    @patch.object(VLMService, "_call_vlm_api")
    def test_empty_clip_summaries_returns_none(self, mock_call, vlm_service):
        """Empty clip_summaries → returns None without calling API."""
        result = vlm_service.generate_unified_timeline(
            [], SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )
        assert result is None
        mock_call.assert_not_called()

    @patch.object(VLMService, "_call_vlm_api")
    def test_empty_clip_metadata_returns_none(self, mock_call, vlm_service):
        """Empty clip_metadata → returns None without calling API."""
        result = vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            [], target_duration=20.0,
        )
        assert result is None
        mock_call.assert_not_called()

    @patch.object(VLMService, "_call_vlm_api")
    def test_vlm_api_returns_none(self, mock_call, vlm_service):
        """VLM API call returns None → returns None."""
        mock_call.return_value = None

        result = vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )
        assert result is None

    @patch.object(VLMService, "_call_vlm_api")
    def test_vlm_returns_invalid_json(self, mock_call, vlm_service):
        """VLM returns non-JSON text → returns None."""
        mock_call.return_value = "This is not JSON"

        result = vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )
        assert result is None

    @patch.object(VLMService, "_call_vlm_api")
    def test_vlm_returns_invalid_timeline(self, mock_call, vlm_service):
        """VLM returns timeline with invalid clip_index → returns None."""
        bad_timeline = json.dumps([
            {
                "clip_index": 99, "source_start": 0.0, "source_end": 5.0,
                "start": 0.0, "end": 5.0, "reason": "invalid clip",
            },
        ])
        mock_call.return_value = bad_timeline

        result = vlm_service.generate_unified_timeline(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Tests: _validate_unified_timeline
# ---------------------------------------------------------------------------

class TestValidateUnifiedTimeline:
    """Tests for the unified timeline validator."""

    @patch("app.services.vlm_service.ExternalConfig")
    def _make_service(self, mock_config_cls):
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {"api_url": "", "api_key": ""}
        mock_config_cls.get_instance.return_value = mock_config
        return VLMService()

    def test_valid_timeline_passes(self):
        svc = self._make_service()
        timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 8.0,
             "start": 0.0, "end": 8.0, "reason": "intro"},
            {"clip_index": 1, "source_start": 0.0, "source_end": 2.0,
             "start": 8.0, "end": 10.0, "reason": "cutaway"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is True

    def test_empty_timeline_fails(self):
        svc = self._make_service()
        assert svc._validate_unified_timeline([], SAMPLE_CLIP_METADATA) is False

    def test_not_a_list_fails(self):
        svc = self._make_service()
        assert svc._validate_unified_timeline("not a list", SAMPLE_CLIP_METADATA) is False

    def test_missing_field_fails(self):
        svc = self._make_service()
        # Missing 'reason' field
        timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 8.0,
             "start": 0.0, "end": 8.0},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_invalid_clip_index_negative_fails(self):
        svc = self._make_service()
        timeline = [
            {"clip_index": -1, "source_start": 0.0, "source_end": 5.0,
             "start": 0.0, "end": 5.0, "reason": "bad"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_invalid_clip_index_out_of_range_fails(self):
        svc = self._make_service()
        timeline = [
            {"clip_index": 5, "source_start": 0.0, "source_end": 5.0,
             "start": 0.0, "end": 5.0, "reason": "bad"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_source_end_exceeds_clip_duration_fails(self):
        svc = self._make_service()
        # clip 1 has duration 8.0, source_end=20.0 exceeds it
        timeline = [
            {"clip_index": 1, "source_start": 0.0, "source_end": 20.0,
             "start": 0.0, "end": 20.0, "reason": "too long"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_source_start_negative_fails(self):
        svc = self._make_service()
        timeline = [
            {"clip_index": 0, "source_start": -1.0, "source_end": 5.0,
             "start": 0.0, "end": 5.0, "reason": "bad"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_source_end_before_source_start_fails(self):
        svc = self._make_service()
        timeline = [
            {"clip_index": 0, "source_start": 5.0, "source_end": 3.0,
             "start": 0.0, "end": 5.0, "reason": "bad"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_output_overlap_fails(self):
        svc = self._make_service()
        timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 8.0,
             "start": 0.0, "end": 10.0, "reason": "first"},
            {"clip_index": 1, "source_start": 0.0, "source_end": 2.0,
             "start": 5.0, "end": 7.0, "reason": "overlaps"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_output_end_before_start_fails(self):
        svc = self._make_service()
        timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0,
             "start": 10.0, "end": 5.0, "reason": "bad"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_non_string_reason_fails(self):
        svc = self._make_service()
        timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0,
             "start": 0.0, "end": 5.0, "reason": 123},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_entry_not_a_dict_fails(self):
        svc = self._make_service()
        timeline = ["not a dict"]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is False

    def test_source_end_within_tolerance_passes(self):
        """source_end slightly over clip duration (within 1s tolerance) → passes."""
        svc = self._make_service()
        # clip 1 duration=8.0, source_end=8.5 is within 1s tolerance
        timeline = [
            {"clip_index": 1, "source_start": 0.0, "source_end": 8.5,
             "start": 0.0, "end": 8.5, "reason": "ok"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is True

    def test_adjacent_entries_no_gap_passes(self):
        """Adjacent entries with matching start/end → passes."""
        svc = self._make_service()
        timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0,
             "start": 0.0, "end": 5.0, "reason": "first"},
            {"clip_index": 0, "source_start": 5.0, "source_end": 10.0,
             "start": 5.0, "end": 10.0, "reason": "second"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is True

    def test_same_clip_reused_passes(self):
        """Same clip used multiple times (non-consecutive segments) → passes."""
        svc = self._make_service()
        timeline = [
            {"clip_index": 0, "source_start": 0.0, "source_end": 5.0,
             "start": 0.0, "end": 5.0, "reason": "first use"},
            {"clip_index": 1, "source_start": 0.0, "source_end": 3.0,
             "start": 5.0, "end": 8.0, "reason": "cutaway"},
            {"clip_index": 0, "source_start": 10.0, "source_end": 15.0,
             "start": 8.0, "end": 13.0, "reason": "back to clip 0"},
        ]
        assert svc._validate_unified_timeline(timeline, SAMPLE_CLIP_METADATA) is True


# ---------------------------------------------------------------------------
# Tests: _build_unified_timeline_content
# ---------------------------------------------------------------------------

class TestBuildUnifiedTimelineContent:
    """Tests for the content builder helper."""

    @patch("app.services.vlm_service.ExternalConfig")
    def test_content_structure(self, mock_config_cls):
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {"api_url": "", "api_key": ""}
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()

        content = svc._build_unified_timeline_content(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )

        assert isinstance(content, list)
        assert all(isinstance(c, dict) for c in content)

        # Check text parts include summaries
        text_parts = [p["text"] for p in content if p["type"] == "text"]
        all_text = "\n".join(text_parts)
        assert "20" in all_text  # target duration
        assert "主播口播.mp4" in all_text
        assert "presenter" in all_text

        # Check image parts for dense frames
        image_parts = [p for p in content if p["type"] == "image_url"]
        assert len(image_parts) == 3  # 2 + 1

    @patch("app.services.vlm_service.ExternalConfig")
    def test_no_user_prompt_no_script(self, mock_config_cls):
        """Without user_prompt and script_text, those sections are omitted."""
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {"api_url": "", "api_key": ""}
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()

        content = svc._build_unified_timeline_content(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )

        text_parts = [p["text"] for p in content if p["type"] == "text"]
        # No standalone "用户指令：..." or "脚本文案：..." sections
        user_prompt_parts = [t for t in text_parts if t.startswith("用户指令：")]
        script_parts = [t for t in text_parts if t.startswith("脚本文案：")]
        assert len(user_prompt_parts) == 0
        assert len(script_parts) == 0

    @patch("app.services.vlm_service.ExternalConfig")
    def test_key_moments_included_in_summaries(self, mock_config_cls):
        """Key moments from clip summaries appear in the content."""
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {"api_url": "", "api_key": ""}
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()

        content = svc._build_unified_timeline_content(
            SAMPLE_CLIP_SUMMARIES, SAMPLE_DENSE_FRAMES,
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )

        text_parts = [p["text"] for p in content if p["type"] == "text"]
        all_text = "\n".join(text_parts)
        assert "展示产品包装" in all_text
        assert "涂抹演示" in all_text

    @patch("app.services.vlm_service.ExternalConfig")
    def test_empty_frames_no_image_parts(self, mock_config_cls):
        """Empty dense_frames → no image parts in content."""
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {"api_url": "", "api_key": ""}
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()

        content = svc._build_unified_timeline_content(
            SAMPLE_CLIP_SUMMARIES, [],
            SAMPLE_CLIP_METADATA, target_duration=20.0,
        )

        image_parts = [p for p in content if p["type"] == "image_url"]
        assert len(image_parts) == 0
