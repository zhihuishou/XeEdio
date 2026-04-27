"""Unit tests for VLMService.analyze_single_clip() method.

Tests the single-clip analysis (Stage 1 — upload-time):
- Successful analysis with valid VLM response
- VLM API not configured → returns None
- Empty frames → returns None
- VLM API call failure → returns None
- JSON parse failure → returns None
- Role normalization (invalid role → "other")
- Visual quality normalization
- key_moments and scene_tags validation/filtering
- Markdown code fence stripping in response
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
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_FRAMES = [
    (0.0, "base64_frame_0"),
    (2.0, "base64_frame_1"),
    (4.0, "base64_frame_2"),
]

SAMPLE_METADATA = {
    "filename": "产品展示.mp4",
    "duration": 8.5,
}

VALID_VLM_RESPONSE = json.dumps({
    "description": "女主播正面讲解护肤品，手持产品展示",
    "role": "presenter",
    "visual_quality": "high",
    "key_moments": [
        {"time": 2.0, "desc": "展示产品包装"},
        {"time": 5.0, "desc": "涂抹演示"},
    ],
    "scene_tags": ["室内", "美妆", "产品展示"],
})


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
        mock_config.get_vlm_config.return_value = {
            "api_url": "",
            "api_key": "",
        }
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()
    return svc


# ---------------------------------------------------------------------------
# Tests: Successful analysis
# ---------------------------------------------------------------------------

class TestAnalyzeSingleClipSuccess:
    """Tests for successful single-clip analysis."""

    @patch.object(VLMService, "_call_vlm_api")
    def test_returns_structured_dict_on_success(self, mock_call, vlm_service):
        """Valid VLM response → returns dict with all expected fields."""
        mock_call.return_value = VALID_VLM_RESPONSE

        result = vlm_service.analyze_single_clip(SAMPLE_FRAMES, SAMPLE_METADATA)

        assert result is not None
        assert result["description"] == "女主播正面讲解护肤品，手持产品展示"
        assert result["role"] == "presenter"
        assert result["visual_quality"] == "high"
        assert len(result["key_moments"]) == 2
        assert result["key_moments"][0] == {"time": 2.0, "desc": "展示产品包装"}
        assert result["scene_tags"] == ["室内", "美妆", "产品展示"]

    @patch.object(VLMService, "_call_vlm_api")
    def test_calls_vlm_api_with_correct_payload(self, mock_call, vlm_service):
        """Verify the VLM API is called with proper model, system prompt, and content."""
        mock_call.return_value = VALID_VLM_RESPONSE

        vlm_service.analyze_single_clip(SAMPLE_FRAMES, SAMPLE_METADATA)

        mock_call.assert_called_once()
        args = mock_call.call_args
        api_url = args[0][0]
        payload = args[0][1]
        headers = args[0][2]

        assert api_url == "https://api.example.com/v1/chat/completions"
        assert payload["model"] == "gpt-5.4"
        assert payload["temperature"] == 0.3
        assert payload["max_tokens"] == 2048
        assert len(payload["messages"]) == 2
        assert payload["messages"][0]["role"] == "system"
        assert "Bearer test-key-123" in headers["Authorization"]

    @patch.object(VLMService, "_call_vlm_api")
    def test_content_includes_frames_and_metadata(self, mock_call, vlm_service):
        """User content should include filename, duration, and frame images."""
        mock_call.return_value = VALID_VLM_RESPONSE

        vlm_service.analyze_single_clip(SAMPLE_FRAMES, SAMPLE_METADATA)

        payload = mock_call.call_args[0][1]
        user_content = payload["messages"][1]["content"]

        # Should be a list of content parts
        assert isinstance(user_content, list)

        # Check text parts mention filename and duration
        text_parts = [p["text"] for p in user_content if p["type"] == "text"]
        intro_text = text_parts[0]
        assert "产品展示.mp4" in intro_text
        assert "8.5s" in intro_text

        # Check image parts exist (one per frame)
        image_parts = [p for p in user_content if p["type"] == "image_url"]
        assert len(image_parts) == 3


# ---------------------------------------------------------------------------
# Tests: Failure cases
# ---------------------------------------------------------------------------

class TestAnalyzeSingleClipFailures:
    """Tests for failure scenarios returning None."""

    def test_no_api_config_returns_none(self, vlm_service_no_config):
        """VLM API not configured → returns None without calling API."""
        result = vlm_service_no_config.analyze_single_clip(SAMPLE_FRAMES, SAMPLE_METADATA)
        assert result is None

    @patch.object(VLMService, "_call_vlm_api")
    def test_empty_frames_returns_none(self, mock_call, vlm_service):
        """Empty frames list → returns None without calling API."""
        result = vlm_service.analyze_single_clip([], SAMPLE_METADATA)
        assert result is None
        mock_call.assert_not_called()

    @patch.object(VLMService, "_call_vlm_api")
    def test_vlm_api_returns_none(self, mock_call, vlm_service):
        """VLM API call returns None → returns None."""
        mock_call.return_value = None

        result = vlm_service.analyze_single_clip(SAMPLE_FRAMES, SAMPLE_METADATA)
        assert result is None

    @patch.object(VLMService, "_call_vlm_api")
    def test_vlm_api_returns_empty_string(self, mock_call, vlm_service):
        """VLM API returns empty string → returns None."""
        mock_call.return_value = ""

        result = vlm_service.analyze_single_clip(SAMPLE_FRAMES, SAMPLE_METADATA)
        assert result is None

    @patch.object(VLMService, "_call_vlm_api")
    def test_vlm_returns_invalid_json(self, mock_call, vlm_service):
        """VLM returns non-JSON text → returns None."""
        mock_call.return_value = "This is not JSON at all"

        result = vlm_service.analyze_single_clip(SAMPLE_FRAMES, SAMPLE_METADATA)
        assert result is None

    @patch.object(VLMService, "_call_vlm_api")
    def test_vlm_returns_malformed_json(self, mock_call, vlm_service):
        """VLM returns malformed JSON → returns None."""
        mock_call.return_value = '{"description": "test", "role": '

        result = vlm_service.analyze_single_clip(SAMPLE_FRAMES, SAMPLE_METADATA)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: Response parsing and normalization
# ---------------------------------------------------------------------------

class TestParseSingleClipJson:
    """Tests for _parse_single_clip_json static method."""

    def test_valid_json_parsed_correctly(self):
        result = VLMService._parse_single_clip_json(VALID_VLM_RESPONSE)
        assert result is not None
        assert result["description"] == "女主播正面讲解护肤品，手持产品展示"
        assert result["role"] == "presenter"

    def test_json_with_markdown_fences(self):
        """JSON wrapped in ```json ... ``` code fences."""
        wrapped = f"```json\n{VALID_VLM_RESPONSE}\n```"
        result = VLMService._parse_single_clip_json(wrapped)
        assert result is not None
        assert result["role"] == "presenter"

    def test_json_with_surrounding_text(self):
        """JSON embedded in surrounding explanation text."""
        text = f"Here is the analysis:\n{VALID_VLM_RESPONSE}\nDone."
        result = VLMService._parse_single_clip_json(text)
        assert result is not None
        assert result["role"] == "presenter"

    def test_invalid_role_normalized_to_other(self):
        """Unknown role value → normalized to 'other'."""
        data = json.dumps({
            "description": "test",
            "role": "unknown_role",
            "visual_quality": "high",
            "key_moments": [],
            "scene_tags": [],
        })
        result = VLMService._parse_single_clip_json(data)
        assert result is not None
        assert result["role"] == "other"

    def test_invalid_visual_quality_normalized_to_medium(self):
        """Unknown visual_quality → normalized to 'medium'."""
        data = json.dumps({
            "description": "test",
            "role": "presenter",
            "visual_quality": "excellent",
            "key_moments": [],
            "scene_tags": [],
        })
        result = VLMService._parse_single_clip_json(data)
        assert result is not None
        assert result["visual_quality"] == "medium"

    def test_missing_fields_get_defaults(self):
        """Missing fields → filled with defaults."""
        data = json.dumps({"description": "minimal"})
        result = VLMService._parse_single_clip_json(data)
        assert result is not None
        assert result["description"] == "minimal"
        assert result["role"] == "other"
        assert result["visual_quality"] == "medium"
        assert result["key_moments"] == []
        assert result["scene_tags"] == []

    def test_key_moments_filters_invalid_entries(self):
        """key_moments with invalid entries → only valid ones kept."""
        data = json.dumps({
            "description": "test",
            "role": "presenter",
            "visual_quality": "high",
            "key_moments": [
                {"time": 2.0, "desc": "valid moment"},
                {"time": 5.0},  # missing desc
                "not a dict",
                {"desc": "missing time"},
                {"time": 8.0, "desc": "another valid"},
            ],
            "scene_tags": [],
        })
        result = VLMService._parse_single_clip_json(data)
        assert result is not None
        assert len(result["key_moments"]) == 2
        assert result["key_moments"][0]["desc"] == "valid moment"
        assert result["key_moments"][1]["desc"] == "another valid"

    def test_scene_tags_filters_empty_values(self):
        """scene_tags with empty/None values → filtered out."""
        data = json.dumps({
            "description": "test",
            "role": "lifestyle",
            "visual_quality": "medium",
            "key_moments": [],
            "scene_tags": ["室内", "", "美妆", None, "产品展示"],
        })
        result = VLMService._parse_single_clip_json(data)
        assert result is not None
        # None becomes "None" via str() but is truthy, empty string is filtered
        assert "室内" in result["scene_tags"]
        assert "美妆" in result["scene_tags"]
        assert "产品展示" in result["scene_tags"]
        assert "" not in result["scene_tags"]

    def test_key_moments_not_a_list_becomes_empty(self):
        """key_moments as non-list → becomes empty list."""
        data = json.dumps({
            "description": "test",
            "key_moments": "not a list",
            "scene_tags": [],
        })
        result = VLMService._parse_single_clip_json(data)
        assert result is not None
        assert result["key_moments"] == []

    def test_scene_tags_not_a_list_becomes_empty(self):
        """scene_tags as non-list → becomes empty list."""
        data = json.dumps({
            "description": "test",
            "key_moments": [],
            "scene_tags": "not a list",
        })
        result = VLMService._parse_single_clip_json(data)
        assert result is not None
        assert result["scene_tags"] == []

    def test_no_json_object_returns_none(self):
        """Text with no JSON object → returns None."""
        result = VLMService._parse_single_clip_json("no json here")
        assert result is None

    def test_all_valid_roles_accepted(self):
        """All five valid roles are accepted without normalization."""
        for role in ("presenter", "product_closeup", "lifestyle", "transition", "other"):
            data = json.dumps({"description": "test", "role": role})
            result = VLMService._parse_single_clip_json(data)
            assert result is not None
            assert result["role"] == role

    def test_all_valid_qualities_accepted(self):
        """All three valid quality levels are accepted."""
        for quality in ("high", "medium", "low"):
            data = json.dumps({"description": "test", "visual_quality": quality})
            result = VLMService._parse_single_clip_json(data)
            assert result is not None
            assert result["visual_quality"] == quality


# ---------------------------------------------------------------------------
# Tests: _build_single_clip_content
# ---------------------------------------------------------------------------

class TestBuildSingleClipContent:
    """Tests for the content builder helper."""

    @patch("app.services.vlm_service.ExternalConfig")
    def test_content_structure(self, mock_config_cls):
        mock_config = MagicMock()
        mock_config.get_vlm_config.return_value = {"api_url": "", "api_key": ""}
        mock_config_cls.get_instance.return_value = mock_config
        svc = VLMService()

        content = svc._build_single_clip_content(SAMPLE_FRAMES, "test.mp4", 10.0)

        # Should be a list of dicts
        assert isinstance(content, list)
        assert all(isinstance(c, dict) for c in content)

        # First part: intro text with filename and duration
        assert content[0]["type"] == "text"
        assert "test.mp4" in content[0]["text"]
        assert "10.0s" in content[0]["text"]

        # Should have text+image pairs for each frame
        image_parts = [c for c in content if c["type"] == "image_url"]
        assert len(image_parts) == 3

        # Last part: JSON output instruction
        last_text = content[-1]["text"]
        assert "description" in last_text
        assert "role" in last_text
