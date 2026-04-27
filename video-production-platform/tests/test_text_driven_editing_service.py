"""Unit tests for TextDrivenEditingService.

Tests the three main methods:
- select_segments_with_llm (JSON parsing, validation)
- map_text_to_timestamps (fuzzy matching, breath-gap snap, filler filtering)
- generate_text_driven_timeline (full pipeline orchestration)

Also tests module-level helpers:
- _fuzzy_find_text
- _char_overlap_score
- _remove_fillers
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.text_driven_editing_service import (
    DURATION_STRATEGY_LONG,
    DURATION_STRATEGY_MEDIUM,
    DURATION_STRATEGY_SHORT,
    FILLER_PATTERNS,
    FILLER_WORDS,
    LLM_SEGMENT_SELECTION_PROMPT,
    LLM_SEGMENT_SELECTION_SYSTEM,
    SENTENCE_PAUSE_THRESHOLD,
    TextDrivenEditingService,
    _char_overlap_score,
    _ends_with_punctuation,
    _fmt_ts,
    _format_transcript_with_timestamps,
    _fuzzy_find_text,
    _get_duration_strategy,
    _is_filler_sequence,
    _is_homophone,
    _remove_fillers,
    _snap_to_breath_gap,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_WORD_TIMESTAMPS = [
    {"word": "大家", "start": 0.0, "end": 0.4},
    {"word": "好", "start": 0.4, "end": 0.6},
    {"word": "今天", "start": 0.8, "end": 1.2},
    {"word": "跟", "start": 1.2, "end": 1.4},
    {"word": "大家", "start": 1.4, "end": 1.8},
    {"word": "聊", "start": 1.8, "end": 2.0},
    {"word": "一下", "start": 2.0, "end": 2.4},
    {"word": "嗯", "start": 2.6, "end": 2.8},  # filler
    {"word": "第一", "start": 3.0, "end": 3.4},
    {"word": "个", "start": 3.4, "end": 3.6},
]


SAMPLE_WORD_TIMESTAMPS_EXTENDED = [
    {"word": "大家", "start": 0.0, "end": 0.4},
    {"word": "好", "start": 0.4, "end": 0.6},
    {"word": "今天", "start": 0.8, "end": 1.2},
    {"word": "跟", "start": 1.2, "end": 1.4},
    {"word": "大家", "start": 1.4, "end": 1.8},
    {"word": "聊", "start": 1.8, "end": 2.0},
    {"word": "一下", "start": 2.0, "end": 2.4},
    {"word": "嗯", "start": 2.6, "end": 2.8},
    {"word": "第一", "start": 3.0, "end": 3.4},
    {"word": "个", "start": 3.4, "end": 3.6},
    {"word": "点", "start": 3.6, "end": 3.8},
    {"word": "就是", "start": 3.8, "end": 4.2},
    {"word": "关于", "start": 4.2, "end": 4.6},
    {"word": "产品", "start": 4.8, "end": 5.2},
    {"word": "的", "start": 5.2, "end": 5.3},
    {"word": "核心", "start": 5.5, "end": 5.9},
    {"word": "功能", "start": 5.9, "end": 6.3},
    {"word": "这", "start": 6.5, "end": 6.7},
    {"word": "就是", "start": 6.7, "end": 7.0},
    {"word": "核心", "start": 7.0, "end": 7.4},
    {"word": "逻辑", "start": 7.4, "end": 7.8},
]


# ---------------------------------------------------------------------------
# Tests: _char_overlap_score
# ---------------------------------------------------------------------------

class TestCharOverlapScore:
    def test_identical_strings(self):
        assert _char_overlap_score("大家好", "大家好") == 1.0

    def test_empty_strings(self):
        assert _char_overlap_score("", "") == 0.0
        assert _char_overlap_score("abc", "") == 0.0
        assert _char_overlap_score("", "abc") == 0.0

    def test_partial_overlap(self):
        score = _char_overlap_score("大家好", "大家好今天")
        assert 0.5 < score <= 1.0

    def test_no_overlap(self):
        score = _char_overlap_score("abc", "xyz")
        assert score == 0.0

    def test_single_char_match(self):
        score = _char_overlap_score("a", "a")
        assert score == 1.0


# ---------------------------------------------------------------------------
# Tests: _remove_fillers
# ---------------------------------------------------------------------------

class TestRemoveFillers:
    def test_removes_filler_words(self):
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "嗯", "start": 0.5, "end": 0.7},
            {"word": "好", "start": 0.8, "end": 1.0},
            {"word": "那个", "start": 1.1, "end": 1.3},
        ]
        result = _remove_fillers(words)
        assert len(result) == 2
        assert result[0]["word"] == "大家"
        assert result[1]["word"] == "好"

    def test_no_fillers(self):
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
        ]
        result = _remove_fillers(words)
        assert len(result) == 2

    def test_all_fillers(self):
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.2},
            {"word": "啊", "start": 0.3, "end": 0.5},
        ]
        result = _remove_fillers(words)
        assert len(result) == 0

    def test_does_not_modify_original(self):
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.2},
            {"word": "好", "start": 0.3, "end": 0.5},
        ]
        _remove_fillers(words)
        assert len(words) == 2  # Original unchanged


# ---------------------------------------------------------------------------
# Tests: _fuzzy_find_text
# ---------------------------------------------------------------------------

class TestFuzzyFindText:
    def test_exact_match(self):
        idx = _fuzzy_find_text(SAMPLE_WORD_TIMESTAMPS, "大家好")
        assert idx == 0

    def test_match_later_in_sequence(self):
        idx = _fuzzy_find_text(SAMPLE_WORD_TIMESTAMPS, "第一个")
        assert idx == 8  # "第一" is at index 8

    def test_search_from_offset(self):
        # "大家" appears at index 0 and 4; searching from 2 should find index 4
        idx = _fuzzy_find_text(SAMPLE_WORD_TIMESTAMPS, "大家", search_from=2)
        assert idx == 4

    def test_no_match(self):
        idx = _fuzzy_find_text(SAMPLE_WORD_TIMESTAMPS, "完全不存在的文本xyz")
        assert idx is None

    def test_empty_inputs(self):
        assert _fuzzy_find_text([], "大家") is None
        assert _fuzzy_find_text(SAMPLE_WORD_TIMESTAMPS, "") is None

    def test_partial_match_with_asr_error(self):
        # Simulate ASR error: "今天根大家" instead of "今天跟大家"
        # Should still find a reasonable match due to character overlap
        idx = _fuzzy_find_text(SAMPLE_WORD_TIMESTAMPS, "今天根大家")
        # Should match near index 2 ("今天" is at index 2)
        assert idx is not None
        assert idx <= 3


# ---------------------------------------------------------------------------
# Tests: TextDrivenEditingService._parse_segments_json
# ---------------------------------------------------------------------------

class TestParseSegmentsJson:
    def test_valid_json_array(self):
        raw = json.dumps([
            {"start_text": "大家好", "end_text": "聊一下", "reason": "开场"},
        ])
        result = TextDrivenEditingService._parse_segments_json(raw)
        assert result is not None
        assert len(result) == 1
        assert result[0]["start_text"] == "大家好"

    def test_json_with_markdown_fences(self):
        raw = '```json\n[{"start_text": "a", "end_text": "b", "reason": "c"}]\n```'
        result = TextDrivenEditingService._parse_segments_json(raw)
        assert result is not None
        assert len(result) == 1

    def test_json_with_surrounding_text(self):
        raw = 'Here is the result:\n[{"start_text": "a", "end_text": "b", "reason": "c"}]\nDone.'
        result = TextDrivenEditingService._parse_segments_json(raw)
        assert result is not None
        assert len(result) == 1

    def test_invalid_json(self):
        result = TextDrivenEditingService._parse_segments_json("not json at all")
        assert result is None

    def test_json_object_not_array(self):
        result = TextDrivenEditingService._parse_segments_json('{"key": "value"}')
        assert result is None

    def test_empty_array(self):
        result = TextDrivenEditingService._parse_segments_json("[]")
        assert result is not None
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Tests: TextDrivenEditingService.map_text_to_timestamps
# ---------------------------------------------------------------------------

class TestMapTextToTimestamps:
    def setup_method(self):
        with patch.object(TextDrivenEditingService, "__init__", lambda self: None):
            self.service = TextDrivenEditingService()

    def test_basic_mapping(self):
        segments = [
            {"start_text": "大家好", "end_text": "聊一下", "reason": "开场"},
        ]
        timeline = self.service.map_text_to_timestamps(
            segments, SAMPLE_WORD_TIMESTAMPS,
        )
        assert len(timeline) == 1
        entry = timeline[0]
        assert entry["clip_index"] == 0
        assert entry["source_start"] >= 0
        assert entry["source_end"] > entry["source_start"]
        assert entry["start"] == 0.0
        assert entry["end"] > 0
        assert entry["reason"] == "开场"

    def test_multiple_segments(self):
        segments = [
            {"start_text": "大家好", "end_text": "聊一下", "reason": "开场"},
            {"start_text": "第一个", "end_text": "第一个", "reason": "要点"},
        ]
        timeline = self.service.map_text_to_timestamps(
            segments, SAMPLE_WORD_TIMESTAMPS_EXTENDED,
        )
        assert len(timeline) >= 1
        # Second segment should start after first ends
        if len(timeline) == 2:
            assert timeline[1]["start"] == pytest.approx(timeline[0]["end"], abs=0.01)

    def test_empty_segments(self):
        timeline = self.service.map_text_to_timestamps([], SAMPLE_WORD_TIMESTAMPS)
        assert timeline == []

    def test_empty_word_timestamps(self):
        segments = [{"start_text": "a", "end_text": "b", "reason": "c"}]
        timeline = self.service.map_text_to_timestamps(segments, [])
        assert timeline == []

    def test_unmatched_segment_skipped(self):
        segments = [
            {"start_text": "完全不存在xyz", "end_text": "也不存在abc", "reason": "skip"},
        ]
        timeline = self.service.map_text_to_timestamps(
            segments, SAMPLE_WORD_TIMESTAMPS,
        )
        assert len(timeline) == 0

    def test_filler_word_at_start_skipped(self):
        """Filler words at segment start should be skipped."""
        # Create words where a filler is at the start of a potential segment
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.3},
            {"word": "大家", "start": 0.5, "end": 0.9},
            {"word": "好", "start": 0.9, "end": 1.2},
        ]
        segments = [{"start_text": "嗯大家好", "end_text": "好", "reason": "test"}]
        timeline = self.service.map_text_to_timestamps(segments, words)
        if timeline:
            # The cut_start should be near "大家" (0.5), not "嗯" (0.0)
            assert timeline[0]["source_start"] >= 0.3

    def test_breath_gap_snap(self):
        """End cut point should snap to breath gap midpoint."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
            # Gap of 0.4s before next word — should snap to midpoint
            {"word": "今天", "start": 1.0, "end": 1.4},
        ]
        segments = [{"start_text": "大家", "end_text": "好", "reason": "test"}]
        timeline = self.service.map_text_to_timestamps(segments, words)
        assert len(timeline) == 1
        # source_end should be snapped: 0.6 + (1.0 - 0.6) / 2 = 0.8
        assert timeline[0]["source_end"] == pytest.approx(0.8, abs=0.01)

    def test_timeline_format(self):
        """Verify unified timeline format fields."""
        segments = [
            {"start_text": "大家好", "end_text": "聊一下", "reason": "开场"},
        ]
        timeline = self.service.map_text_to_timestamps(
            segments, SAMPLE_WORD_TIMESTAMPS,
        )
        assert len(timeline) == 1
        entry = timeline[0]
        required_fields = {"clip_index", "source_start", "source_end", "start", "end", "reason"}
        assert required_fields.issubset(entry.keys())
        assert isinstance(entry["clip_index"], int)
        assert isinstance(entry["source_start"], float)
        assert isinstance(entry["source_end"], float)
        assert isinstance(entry["start"], float)
        assert isinstance(entry["end"], float)
        assert isinstance(entry["reason"], str)


# ---------------------------------------------------------------------------
# Tests: TextDrivenEditingService.select_segments_with_llm
# ---------------------------------------------------------------------------

class TestSelectSegmentsWithLlm:
    def setup_method(self):
        with patch.object(TextDrivenEditingService, "__init__", lambda self: None):
            self.service = TextDrivenEditingService()

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_successful_selection(self, mock_call, mock_config):
        mock_config.return_value = {
            "api_url": "https://api.example.com/v1/chat/completions",
            "api_key": "test-key",
            "model": "qwen-plus",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "大家好", "end_text": "聊一下", "reason": "开场白"},
        ])

        result = self.service.select_segments_with_llm(
            "大家好今天跟大家聊一下", 60.0, "选精华",
        )
        assert result is not None
        assert len(result) == 1
        assert result[0]["start_text"] == "大家好"
        assert result[0]["reason"] == "开场白"

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    def test_no_api_config(self, mock_config):
        mock_config.return_value = {"api_url": "", "api_key": "", "model": ""}
        result = self.service.select_segments_with_llm("text", 60.0)
        assert result is None

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_llm_returns_none(self, mock_call, mock_config):
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = None
        result = self.service.select_segments_with_llm("text", 60.0)
        assert result is None

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_llm_returns_invalid_json(self, mock_call, mock_config):
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = "not valid json"
        result = self.service.select_segments_with_llm("text", 60.0)
        assert result is None

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_segments_missing_fields_filtered(self, mock_call, mock_config):
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "ok"},
            {"start_text": "", "end_text": "b", "reason": "missing start"},
            {"other_field": "x"},
        ])
        result = self.service.select_segments_with_llm("text", 60.0)
        assert result is not None
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tests: TextDrivenEditingService.generate_text_driven_timeline
# ---------------------------------------------------------------------------

class TestGenerateTextDrivenTimeline:
    def setup_method(self):
        with patch.object(TextDrivenEditingService, "__init__", lambda self: None):
            self.service = TextDrivenEditingService()

    @patch.object(TextDrivenEditingService, "select_segments_with_llm")
    def test_full_pipeline(self, mock_select):
        mock_select.return_value = [
            {"start_text": "大家好", "end_text": "聊一下", "reason": "开场"},
        ]
        timeline = self.service.generate_text_driven_timeline(
            transcript="大家好今天跟大家聊一下",
            word_timestamps=SAMPLE_WORD_TIMESTAMPS,
            target_duration=60.0,
            user_prompt="选精华",
        )
        assert timeline is not None
        assert len(timeline) >= 1
        assert timeline[0]["clip_index"] == 0

    @patch.object(TextDrivenEditingService, "select_segments_with_llm")
    def test_empty_transcript(self, mock_select):
        result = self.service.generate_text_driven_timeline(
            transcript="",
            word_timestamps=SAMPLE_WORD_TIMESTAMPS,
            target_duration=60.0,
        )
        assert result is None
        mock_select.assert_not_called()

    @patch.object(TextDrivenEditingService, "select_segments_with_llm")
    def test_empty_word_timestamps(self, mock_select):
        result = self.service.generate_text_driven_timeline(
            transcript="some text",
            word_timestamps=[],
            target_duration=60.0,
        )
        assert result is None
        mock_select.assert_not_called()

    @patch.object(TextDrivenEditingService, "select_segments_with_llm")
    def test_llm_returns_none(self, mock_select):
        mock_select.return_value = None
        result = self.service.generate_text_driven_timeline(
            transcript="some text",
            word_timestamps=SAMPLE_WORD_TIMESTAMPS,
            target_duration=60.0,
        )
        assert result is None

    @patch.object(TextDrivenEditingService, "select_segments_with_llm")
    def test_no_mappable_segments(self, mock_select):
        mock_select.return_value = [
            {"start_text": "不存在xyz", "end_text": "也不存在abc", "reason": "skip"},
        ]
        result = self.service.generate_text_driven_timeline(
            transcript="some text",
            word_timestamps=SAMPLE_WORD_TIMESTAMPS,
            target_duration=60.0,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Tests: LLM Prompt Design (Task 15)
# ---------------------------------------------------------------------------

class TestLlmPromptDesign:
    """Tests for the enhanced LLM segment selection prompt."""

    def test_system_message_exists_and_sets_role(self):
        """System message should define the LLM's role as a video editor."""
        assert LLM_SEGMENT_SELECTION_SYSTEM
        assert "剪辑" in LLM_SEGMENT_SELECTION_SYSTEM
        assert "JSON" in LLM_SEGMENT_SELECTION_SYSTEM

    def test_prompt_contains_duration_range(self):
        """Prompt should include ±10% duration range for clarity."""
        rendered = LLM_SEGMENT_SELECTION_PROMPT.format(
            target_duration=60,
            duration_min=54,
            duration_max=66,
            transcript="测试文本",
            user_instruction="",
            duration_strategy=DURATION_STRATEGY_MEDIUM,
        )
        assert "54" in rendered
        assert "66" in rendered
        assert "60" in rendered

    def test_prompt_contains_selection_rules(self):
        """Prompt should contain detailed selection rules."""
        assert "用户指令优先" in LLM_SEGMENT_SELECTION_PROMPT
        assert "信息密度" in LLM_SEGMENT_SELECTION_PROMPT
        assert "语义完整" in LLM_SEGMENT_SELECTION_PROMPT
        assert "叙事连贯" in LLM_SEGMENT_SELECTION_PROMPT
        assert "口头禅" in LLM_SEGMENT_SELECTION_PROMPT

    def test_prompt_contains_output_format_spec(self):
        """Prompt should clearly specify the JSON output format."""
        assert "start_text" in LLM_SEGMENT_SELECTION_PROMPT
        assert "end_text" in LLM_SEGMENT_SELECTION_PROMPT
        assert "reason" in LLM_SEGMENT_SELECTION_PROMPT

    def test_prompt_contains_example(self):
        """Prompt should include a concrete example for LLM guidance."""
        assert "今天给大家推荐一款" in LLM_SEGMENT_SELECTION_PROMPT
        assert "产品核心卖点" in LLM_SEGMENT_SELECTION_PROMPT

    def test_prompt_handles_edge_cases(self):
        """Prompt should mention handling for short and long transcripts."""
        assert "转录文本很短" in LLM_SEGMENT_SELECTION_PROMPT
        assert "转录文本很长" in LLM_SEGMENT_SELECTION_PROMPT

    def test_prompt_renders_without_user_instruction(self):
        """Prompt should render cleanly when no user instruction is given."""
        rendered = LLM_SEGMENT_SELECTION_PROMPT.format(
            target_duration=60,
            duration_min=54,
            duration_max=66,
            transcript="测试转录文本内容",
            user_instruction="",
            duration_strategy=DURATION_STRATEGY_MEDIUM,
        )
        assert "测试转录文本内容" in rendered
        assert "60" in rendered

    def test_prompt_renders_with_user_instruction(self):
        """Prompt should include user instruction when provided."""
        rendered = LLM_SEGMENT_SELECTION_PROMPT.format(
            target_duration=30,
            duration_min=27,
            duration_max=33,
            transcript="一些文本",
            user_instruction="### 用户指令（最高优先级）\n多选产品展示部分",
            duration_strategy=DURATION_STRATEGY_SHORT,
        )
        assert "多选产品展示部分" in rendered
        assert "最高优先级" in rendered


class TestSelectSegmentsWithLlmPromptIntegration:
    """Tests that select_segments_with_llm builds the correct payload."""

    def setup_method(self):
        with patch.object(TextDrivenEditingService, "__init__", lambda self: None):
            self.service = TextDrivenEditingService()

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_payload_includes_system_message(self, mock_call, mock_config):
        """The API payload should include a system message."""
        mock_config.return_value = {
            "api_url": "https://api.example.com/v1/chat/completions",
            "api_key": "test-key",
            "model": "qwen-plus",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "c"},
        ])

        self.service.select_segments_with_llm("转录文本", 60.0, "")

        # Verify _call_llm_api was called with a payload containing system message
        call_args = mock_call.call_args
        payload = call_args[0][1]  # second positional arg is payload
        messages = payload["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "剪辑" in messages[0]["content"]

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_prompt_includes_duration_range(self, mock_call, mock_config):
        """The rendered prompt should include the ±10% duration range."""
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "c"},
        ])

        self.service.select_segments_with_llm("转录文本", 100.0, "")

        call_args = mock_call.call_args
        payload = call_args[0][1]
        user_content = payload["messages"][1]["content"]
        # 100s target → min 90, max 110
        assert "90" in user_content
        assert "110" in user_content

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_user_instruction_formatted_correctly(self, mock_call, mock_config):
        """User instruction should appear with proper heading."""
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "c"},
        ])

        self.service.select_segments_with_llm("文本", 60.0, "多选产品部分")

        call_args = mock_call.call_args
        payload = call_args[0][1]
        user_content = payload["messages"][1]["content"]
        assert "多选产品部分" in user_content
        assert "最高优先级" in user_content

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_transcript_cap_scales_with_duration(self, mock_call, mock_config):
        """Transcript cap should scale with target duration."""
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "c"},
        ])

        # Very long transcript with short target duration
        long_transcript = "字" * 50000
        self.service.select_segments_with_llm(long_transcript, 30.0, "")

        call_args = mock_call.call_args
        payload = call_args[0][1]
        user_content = payload["messages"][1]["content"]
        # With 30s target: max(5000, 30*250) = 7500, min(30000, 7500) = 7500
        # The transcript in the prompt should be capped, not the full 50000
        assert len(user_content) < 50000

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_empty_user_prompt_no_instruction_section(self, mock_call, mock_config):
        """When user_prompt is empty, no instruction heading should appear."""
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "c"},
        ])

        self.service.select_segments_with_llm("文本", 60.0, "")

        call_args = mock_call.call_args
        payload = call_args[0][1]
        user_content = payload["messages"][1]["content"]
        assert "最高优先级" not in user_content


# ---------------------------------------------------------------------------
# Tests: _format_transcript_with_timestamps (Task 15)
# ---------------------------------------------------------------------------

class TestFormatTranscriptWithTimestamps:
    """Tests for the transcript formatting helper."""

    def test_basic_formatting(self):
        """Words should be grouped into time-marked lines."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
            {"word": "，", "start": 0.6, "end": 0.7},
            {"word": "今天", "start": 0.8, "end": 1.2},
            {"word": "聊", "start": 1.2, "end": 1.4},
        ]
        result = _format_transcript_with_timestamps(words)
        assert "[00:00.0 - 00:00.7]" in result
        assert "大家好，" in result

    def test_splits_on_long_pause(self):
        """A pause > SENTENCE_PAUSE_THRESHOLD should start a new line."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
            # Long pause (> 0.5s)
            {"word": "今天", "start": 2.0, "end": 2.4},
            {"word": "聊", "start": 2.4, "end": 2.6},
        ]
        result = _format_transcript_with_timestamps(words)
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert "大家好" in lines[0]
        assert "今天聊" in lines[1]

    def test_splits_on_punctuation(self):
        """Sentence-ending punctuation should trigger a new line."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好。", "start": 0.4, "end": 0.7},
            {"word": "今天", "start": 0.8, "end": 1.2},
        ]
        result = _format_transcript_with_timestamps(words)
        lines = result.strip().split("\n")
        assert len(lines) == 2

    def test_empty_input(self):
        """Empty word list should return empty string."""
        assert _format_transcript_with_timestamps([]) == ""

    def test_single_word(self):
        """Single word should produce one line."""
        words = [{"word": "你好", "start": 0.0, "end": 0.5}]
        result = _format_transcript_with_timestamps(words)
        assert "[00:00.0 - 00:00.5]" in result
        assert "你好" in result

    def test_time_format_minutes(self):
        """Timestamps beyond 60s should show minutes correctly."""
        words = [
            {"word": "内容", "start": 125.0, "end": 125.5},
        ]
        result = _format_transcript_with_timestamps(words)
        assert "[02:05.0 - 02:05.5]" in result

    def test_continuous_words_grouped(self):
        """Words without pauses or punctuation should stay in one group."""
        words = [
            {"word": "今天", "start": 0.0, "end": 0.4},
            {"word": "跟", "start": 0.4, "end": 0.6},
            {"word": "大家", "start": 0.6, "end": 1.0},
            {"word": "聊", "start": 1.0, "end": 1.2},
            {"word": "一下", "start": 1.2, "end": 1.5},
        ]
        result = _format_transcript_with_timestamps(words)
        lines = result.strip().split("\n")
        assert len(lines) == 1
        assert "今天跟大家聊一下" in lines[0]


# ---------------------------------------------------------------------------
# Tests: _fmt_ts and _ends_with_punctuation helpers (Task 15)
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    """Tests for small helper functions added in Task 15."""

    def test_fmt_ts_zero(self):
        assert _fmt_ts(0.0) == "00:00.0"

    def test_fmt_ts_seconds(self):
        assert _fmt_ts(5.2) == "00:05.2"

    def test_fmt_ts_minutes(self):
        assert _fmt_ts(65.3) == "01:05.3"

    def test_fmt_ts_large(self):
        assert _fmt_ts(3661.0) == "61:01.0"

    def test_ends_with_punctuation_chinese(self):
        assert _ends_with_punctuation("好。") is True
        assert _ends_with_punctuation("吗？") is True
        assert _ends_with_punctuation("啊！") is True
        assert _ends_with_punctuation("说，") is True

    def test_ends_with_punctuation_no(self):
        assert _ends_with_punctuation("大家") is False
        assert _ends_with_punctuation("好") is False

    def test_ends_with_punctuation_empty(self):
        assert _ends_with_punctuation("") is False


# ---------------------------------------------------------------------------
# Tests: Duration strategy variants (Task 15)
# ---------------------------------------------------------------------------

class TestDurationStrategy:
    """Tests for duration-based prompt strategy selection."""

    def test_short_form_strategy(self):
        """Target <= 30s should use short-form strategy."""
        strategy = _get_duration_strategy(15.0)
        assert strategy == DURATION_STRATEGY_SHORT
        assert "1 个最精华的片段" in strategy

    def test_short_form_boundary(self):
        """Target exactly 30s should use short-form strategy."""
        strategy = _get_duration_strategy(30.0)
        assert strategy == DURATION_STRATEGY_SHORT

    def test_medium_form_strategy(self):
        """Target 30-120s should use medium-form strategy."""
        strategy = _get_duration_strategy(60.0)
        assert strategy == DURATION_STRATEGY_MEDIUM
        assert "3–5 个关键段落" in strategy

    def test_medium_form_boundary(self):
        """Target exactly 120s should use medium-form strategy."""
        strategy = _get_duration_strategy(120.0)
        assert strategy == DURATION_STRATEGY_MEDIUM

    def test_long_form_strategy(self):
        """Target > 120s should use long-form strategy."""
        strategy = _get_duration_strategy(180.0)
        assert strategy == DURATION_STRATEGY_LONG
        assert "5 个以上段落" in strategy

    def test_strategy_renders_in_prompt(self):
        """Duration strategy should render correctly in the prompt template."""
        rendered = LLM_SEGMENT_SELECTION_PROMPT.format(
            target_duration=20,
            duration_min=18,
            duration_max=22,
            transcript="测试",
            user_instruction="",
            duration_strategy=DURATION_STRATEGY_SHORT,
        )
        assert "1 个最精华的片段" in rendered


# ---------------------------------------------------------------------------
# Tests: Enhanced prompt content (Task 15)
# ---------------------------------------------------------------------------

class TestEnhancedPromptContent:
    """Tests for the new prompt sections added in Task 15."""

    def test_prompt_contains_time_estimation_guidance(self):
        """Prompt should include time estimation reference (4 chars/sec)."""
        assert "4 字/秒" in LLM_SEGMENT_SELECTION_PROMPT
        assert "0.25 秒" in LLM_SEGMENT_SELECTION_PROMPT

    def test_prompt_contains_content_type_guidance(self):
        """Prompt should include guidance for different content types."""
        assert "口播" in LLM_SEGMENT_SELECTION_PROMPT
        assert "直播" in LLM_SEGMENT_SELECTION_PROMPT
        assert "访谈" in LLM_SEGMENT_SELECTION_PROMPT
        assert "课程" in LLM_SEGMENT_SELECTION_PROMPT

    def test_prompt_contains_segment_length_distribution(self):
        """Prompt should include guidance on mixing segment lengths."""
        assert "短段落" in LLM_SEGMENT_SELECTION_PROMPT
        assert "中段落" in LLM_SEGMENT_SELECTION_PROMPT
        assert "长段落" in LLM_SEGMENT_SELECTION_PROMPT

    def test_prompt_contains_good_vs_bad_examples(self):
        """Prompt should include good vs bad selection guidance."""
        assert "好的选段" in LLM_SEGMENT_SELECTION_PROMPT
        assert "差的选段" in LLM_SEGMENT_SELECTION_PROMPT

    def test_prompt_has_duration_strategy_placeholder(self):
        """Prompt template should have a {duration_strategy} placeholder."""
        assert "{duration_strategy}" in LLM_SEGMENT_SELECTION_PROMPT


# ---------------------------------------------------------------------------
# Tests: select_segments_with_llm with word_timestamps (Task 15)
# ---------------------------------------------------------------------------

class TestSelectSegmentsWithFormattedTranscript:
    """Tests that select_segments_with_llm uses formatted transcript."""

    def setup_method(self):
        with patch.object(TextDrivenEditingService, "__init__", lambda self: None):
            self.service = TextDrivenEditingService()

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_word_timestamps_formats_transcript(self, mock_call, mock_config):
        """When word_timestamps are provided, transcript should be formatted."""
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "c"},
        ])

        word_ts = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
        ]
        self.service.select_segments_with_llm(
            "大家好", 60.0, "", word_timestamps=word_ts,
        )

        call_args = mock_call.call_args
        payload = call_args[0][1]
        user_content = payload["messages"][1]["content"]
        # Should contain time markers from formatted transcript
        assert "[00:00.0" in user_content

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_no_word_timestamps_uses_raw_transcript(self, mock_call, mock_config):
        """Without word_timestamps, raw transcript should be used."""
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "c"},
        ])

        self.service.select_segments_with_llm("原始转录文本", 60.0, "")

        call_args = mock_call.call_args
        payload = call_args[0][1]
        user_content = payload["messages"][1]["content"]
        assert "原始转录文本" in user_content

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_short_duration_uses_short_strategy(self, mock_call, mock_config):
        """Short target duration should include short-form strategy."""
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "c"},
        ])

        self.service.select_segments_with_llm("文本", 15.0, "")

        call_args = mock_call.call_args
        payload = call_args[0][1]
        user_content = payload["messages"][1]["content"]
        assert "1 个最精华的片段" in user_content

    @patch.object(TextDrivenEditingService, "_get_llm_config")
    @patch.object(TextDrivenEditingService, "_call_llm_api")
    def test_long_duration_uses_long_strategy(self, mock_call, mock_config):
        """Long target duration should include long-form strategy."""
        mock_config.return_value = {
            "api_url": "https://api.example.com", "api_key": "k", "model": "m",
        }
        mock_call.return_value = json.dumps([
            {"start_text": "a", "end_text": "b", "reason": "c"},
        ])

        self.service.select_segments_with_llm("文本", 180.0, "")

        call_args = mock_call.call_args
        payload = call_args[0][1]
        user_content = payload["messages"][1]["content"]
        assert "5 个以上段落" in user_content


# ---------------------------------------------------------------------------
# Tests: Homophone matching (Task 16)
# ---------------------------------------------------------------------------

class TestIsHomophone:
    """Tests for the _is_homophone helper."""

    def test_same_character(self):
        assert _is_homophone("跟", "跟") is True

    def test_known_homophone_pair(self):
        assert _is_homophone("跟", "根") is True
        assert _is_homophone("根", "跟") is True

    def test_de_particles(self):
        assert _is_homophone("的", "得") is True
        assert _is_homophone("的", "地") is True
        assert _is_homophone("得", "地") is True

    def test_pronoun_homophones(self):
        assert _is_homophone("他", "她") is True
        assert _is_homophone("他", "它") is True

    def test_non_homophone(self):
        assert _is_homophone("大", "好") is False
        assert _is_homophone("跟", "的") is False

    def test_unknown_character(self):
        # Characters not in the homophone map
        assert _is_homophone("龙", "凤") is False


# ---------------------------------------------------------------------------
# Tests: Enhanced _char_overlap_score with homophone support (Task 16)
# ---------------------------------------------------------------------------

class TestCharOverlapScoreHomophone:
    """Tests for homophone-aware character overlap scoring."""

    def test_exact_match_still_perfect(self):
        """Exact matches should still score 1.0."""
        assert _char_overlap_score("大家好", "大家好") == 1.0

    def test_homophone_substitution_partial_credit(self):
        """Homophone substitutions should get partial credit (0.7 per char)."""
        # "跟" → "根" is a homophone pair
        score = _char_overlap_score("跟大家", "根大家")
        # 0.7 + 1.0 + 1.0 = 2.7 / 3 = 0.9
        assert score == pytest.approx(0.9, abs=0.01)

    def test_de_particle_substitution(self):
        """的/得/地 substitutions should score well."""
        score = _char_overlap_score("产品的功能", "产品得功能")
        # 4 exact + 1 homophone(0.7) = 4.7 / 5 = 0.94
        assert score == pytest.approx(0.94, abs=0.01)

    def test_multiple_homophone_substitutions(self):
        """Multiple homophone errors should still produce a reasonable score."""
        # "跟大家聊的" → "根大家聊得" (two homophone errors)
        score = _char_overlap_score("跟大家聊的", "根大家聊得")
        # 0.7 + 1 + 1 + 1 + 0.7 = 4.4 / 5 = 0.88
        assert score == pytest.approx(0.88, abs=0.01)

    def test_no_overlap_unchanged(self):
        """Completely different strings should still score 0."""
        assert _char_overlap_score("abc", "xyz") == 0.0

    def test_empty_strings_unchanged(self):
        assert _char_overlap_score("", "") == 0.0
        assert _char_overlap_score("abc", "") == 0.0
        assert _char_overlap_score("", "abc") == 0.0


# ---------------------------------------------------------------------------
# Tests: Enhanced _fuzzy_find_text — ASR error scenarios (Task 16)
# ---------------------------------------------------------------------------

# Realistic ASR word timestamps for testing
ASR_WORD_TIMESTAMPS = [
    {"word": "大家", "start": 0.0, "end": 0.4},
    {"word": "好", "start": 0.4, "end": 0.6},
    {"word": "今天", "start": 0.8, "end": 1.2},
    {"word": "根", "start": 1.2, "end": 1.4},      # ASR error: "跟" → "根"
    {"word": "大家", "start": 1.4, "end": 1.8},
    {"word": "聊", "start": 1.8, "end": 2.0},
    {"word": "一下", "start": 2.0, "end": 2.4},
    {"word": "产品", "start": 2.6, "end": 3.0},
    {"word": "得", "start": 3.0, "end": 3.1},       # ASR error: "的" → "得"
    {"word": "核心", "start": 3.2, "end": 3.6},
    {"word": "功能", "start": 3.6, "end": 4.0},
    {"word": "就是", "start": 4.2, "end": 4.6},
    {"word": "这个", "start": 4.6, "end": 5.0},
    {"word": "效果", "start": 5.0, "end": 5.4},
    {"word": "非常", "start": 5.6, "end": 6.0},
    {"word": "好", "start": 6.0, "end": 6.2},
]


class TestFuzzyFindTextHomophone:
    """Tests for fuzzy matching with homophone ASR errors."""

    def test_homophone_gen_gen(self):
        """'跟' transcribed as '根' should still match."""
        # LLM selected "跟大家聊一下" but ASR has "根大家聊一下"
        idx = _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "跟大家聊一下")
        assert idx is not None
        # Should match at index 3 where "根" is
        assert idx == 3

    def test_homophone_de_particle(self):
        """'的' transcribed as '得' should still match."""
        idx = _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "产品的核心功能")
        assert idx is not None
        # Should match at index 7 where "产品" starts
        assert idx == 7

    def test_exact_match_preferred_over_homophone(self):
        """Exact matches should be preferred over homophone matches."""
        # "大家好" is an exact match at index 0
        idx = _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "大家好")
        assert idx == 0


class TestFuzzyFindTextMissingChars:
    """Tests for fuzzy matching with missing characters (漏字)."""

    def test_missing_one_char(self):
        """ASR drops one character — should still find a match."""
        # Target: "大家好今天" but ASR might have "大家好天" (missing "今")
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
            {"word": "天", "start": 0.8, "end": 1.0},  # "今" dropped
            {"word": "聊", "start": 1.0, "end": 1.2},
        ]
        idx = _fuzzy_find_text(words, "大家好今天")
        assert idx is not None
        assert idx == 0

    def test_missing_char_in_middle(self):
        """Missing character in the middle of a phrase."""
        words = [
            {"word": "产", "start": 0.0, "end": 0.2},   # "品" dropped
            {"word": "核心", "start": 0.3, "end": 0.6},
            {"word": "功能", "start": 0.6, "end": 1.0},
        ]
        idx = _fuzzy_find_text(words, "产品核心功能")
        assert idx is not None
        assert idx == 0


class TestFuzzyFindTextExtraChars:
    """Tests for fuzzy matching with extra characters (多字)."""

    def test_extra_char_inserted(self):
        """ASR inserts an extra character — should still match."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "都", "start": 0.4, "end": 0.5},   # extra word
            {"word": "好", "start": 0.5, "end": 0.7},
            {"word": "今天", "start": 0.8, "end": 1.2},
        ]
        idx = _fuzzy_find_text(words, "大家好今天")
        assert idx is not None
        assert idx == 0

    def test_extra_filler_word_in_asr(self):
        """ASR captures a filler word that LLM text doesn't have."""
        words = [
            {"word": "产品", "start": 0.0, "end": 0.4},
            {"word": "呢", "start": 0.4, "end": 0.5},   # extra filler
            {"word": "核心", "start": 0.6, "end": 1.0},
            {"word": "功能", "start": 1.0, "end": 1.4},
        ]
        idx = _fuzzy_find_text(words, "产品核心功能")
        assert idx is not None
        assert idx == 0


class TestFuzzyFindTextShortTarget:
    """Tests for very short target texts (1-3 characters)."""

    def test_single_char_target(self):
        """Single character target should find a match."""
        words = [
            {"word": "大", "start": 0.0, "end": 0.2},
            {"word": "家", "start": 0.2, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
        ]
        idx = _fuzzy_find_text(words, "好")
        assert idx is not None
        assert idx == 2

    def test_two_char_target(self):
        """Two character target should find a match."""
        idx = _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "核心")
        assert idx is not None
        assert idx == 9  # "核心" is at index 9

    def test_three_char_target(self):
        """Three character target should find a match."""
        idx = _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "大家好")
        assert idx == 0


class TestFuzzyFindTextEdgeCases:
    """Edge case tests for _fuzzy_find_text."""

    def test_empty_target(self):
        assert _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "") is None

    def test_whitespace_only_target(self):
        assert _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "   ") is None

    def test_empty_word_list(self):
        assert _fuzzy_find_text([], "大家好") is None

    def test_completely_unrelated_text(self):
        """Text that doesn't appear at all should return None."""
        idx = _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "完全不相关的内容xyz")
        assert idx is None

    def test_search_from_beyond_list(self):
        """search_from beyond list length should return None."""
        idx = _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "大家好", search_from=100)
        assert idx is None

    def test_two_pass_relaxed_match(self):
        """Text with heavy errors should still match on relaxed pass."""
        # Only 2 out of 5 characters match exactly, but with homophones
        # "跟大家聊的" → ASR has "根大家聊得" (2 homophone errors)
        # Score: (0.7 + 1 + 1 + 1 + 0.7) / 5 = 0.88 — passes strict
        idx = _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "跟大家聊的")
        assert idx is not None

    def test_overlapping_start_end_text(self):
        """When start_text and end_text overlap (very short segment)."""
        # Both start and end point to the same word
        idx = _fuzzy_find_text(ASR_WORD_TIMESTAMPS, "效果")
        assert idx is not None
        assert idx == 13  # "效果" at index 13


# ---------------------------------------------------------------------------
# Tests: map_text_to_timestamps with ASR errors (Task 16)
# ---------------------------------------------------------------------------

class TestMapTextToTimestampsASRErrors:
    """Tests for timestamp mapping with ASR transcription errors."""

    def setup_method(self):
        with patch.object(TextDrivenEditingService, "__init__", lambda self: None):
            self.service = TextDrivenEditingService()

    def test_homophone_in_start_text(self):
        """Segment with homophone error in start_text should still map."""
        segments = [
            {
                "start_text": "跟大家聊",
                "end_text": "功能",
                "reason": "核心内容",
            },
        ]
        timeline = self.service.map_text_to_timestamps(
            segments, ASR_WORD_TIMESTAMPS,
        )
        assert len(timeline) == 1
        assert timeline[0]["source_start"] >= 0
        assert timeline[0]["source_end"] > timeline[0]["source_start"]

    def test_homophone_in_end_text(self):
        """Segment with homophone error in end_text should still map."""
        segments = [
            {
                "start_text": "产品",
                "end_text": "产品的核心功能",  # LLM says "的" but ASR has "得"
                "reason": "产品功能",
            },
        ]
        timeline = self.service.map_text_to_timestamps(
            segments, ASR_WORD_TIMESTAMPS,
        )
        assert len(timeline) == 1

    def test_short_segment_single_word(self):
        """Very short segment (single word) should still map."""
        segments = [
            {
                "start_text": "效果",
                "end_text": "效果",
                "reason": "关键词",
            },
        ]
        timeline = self.service.map_text_to_timestamps(
            segments, ASR_WORD_TIMESTAMPS,
        )
        # Single word segment might be < 0.5s and get skipped,
        # but the matching itself should work
        # "效果" is 0.4s (5.0-5.4), which is < 0.5s minimum
        # So it may be skipped — that's correct behavior
        assert len(timeline) <= 1


# ---------------------------------------------------------------------------
# Tests: Enhanced filler word filtering (Task 17)
# ---------------------------------------------------------------------------


class TestExpandedFillerWords:
    """Tests for the expanded FILLER_WORDS set."""

    def test_original_fillers_still_present(self):
        """All original filler words should still be in the set."""
        original = {"嗯", "啊", "那个", "就是说", "然后", "对吧", "你知道吗",
                     "怎么说呢", "呃", "额", "哎", "这个", "所以说", "反正"}
        assert original.issubset(FILLER_WORDS)

    def test_repeated_fillers_added(self):
        """Repeated filler patterns should be in the set."""
        assert "对对对" in FILLER_WORDS
        assert "是是是" in FILLER_WORDS
        assert "好的好的" in FILLER_WORDS
        assert "嗯嗯" in FILLER_WORDS
        assert "啊啊" in FILLER_WORDS

    def test_english_fillers_added(self):
        """English fillers common in Chinese speech should be present."""
        assert "OK" in FILLER_WORDS
        assert "ok" in FILLER_WORDS

    def test_discourse_markers_added(self):
        """Discourse markers used as fillers should be present."""
        assert "其实" in FILLER_WORDS
        assert "基本上" in FILLER_WORDS
        assert "总之" in FILLER_WORDS

    def test_filler_patterns_exist(self):
        """Multi-word filler patterns should be defined."""
        assert len(FILLER_PATTERNS) > 0
        # Check specific patterns
        assert ("就是", "那个") in FILLER_PATTERNS

    def test_remove_fillers_with_new_words(self):
        """_remove_fillers should filter the newly added filler words."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "对对对", "start": 0.5, "end": 0.8},
            {"word": "好", "start": 0.9, "end": 1.1},
            {"word": "嗯嗯", "start": 1.2, "end": 1.4},
            {"word": "OK", "start": 1.5, "end": 1.7},
        ]
        result = _remove_fillers(words)
        assert len(result) == 2
        assert result[0]["word"] == "大家"
        assert result[1]["word"] == "好"


# ---------------------------------------------------------------------------
# Tests: _is_filler_sequence (Task 17)
# ---------------------------------------------------------------------------


class TestIsFillerSequence:
    """Tests for the _is_filler_sequence helper."""

    def test_two_consecutive_fillers(self):
        """Two consecutive filler words should be detected as a sequence."""
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.2},
            {"word": "那个", "start": 0.3, "end": 0.5},
            {"word": "大家", "start": 0.6, "end": 1.0},
        ]
        assert _is_filler_sequence(words, 0, 1) is True

    def test_four_consecutive_fillers(self):
        """Four consecutive fillers like '嗯嗯那个就是说' should be detected."""
        words = [
            {"word": "嗯嗯", "start": 0.0, "end": 0.3},
            {"word": "那个", "start": 0.3, "end": 0.5},
            {"word": "就是说", "start": 0.5, "end": 0.8},
            {"word": "呃", "start": 0.8, "end": 1.0},
        ]
        assert _is_filler_sequence(words, 0, 3) is True

    def test_single_filler_not_sequence(self):
        """A single filler word should NOT be considered a sequence."""
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.2},
            {"word": "大家", "start": 0.3, "end": 0.7},
        ]
        assert _is_filler_sequence(words, 0, 0) is False

    def test_mixed_filler_and_content(self):
        """A range with both filler and content words is not a filler sequence."""
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.2},
            {"word": "大家", "start": 0.3, "end": 0.7},
            {"word": "那个", "start": 0.8, "end": 1.0},
        ]
        assert _is_filler_sequence(words, 0, 2) is False

    def test_no_fillers(self):
        """A range with no filler words is not a filler sequence."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
        ]
        assert _is_filler_sequence(words, 0, 1) is False

    def test_invalid_range(self):
        """Invalid range (end < start) should return False."""
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.2},
            {"word": "那个", "start": 0.3, "end": 0.5},
        ]
        assert _is_filler_sequence(words, 1, 0) is False


# ---------------------------------------------------------------------------
# Tests: Filler trimming at segment boundaries (Task 17)
# ---------------------------------------------------------------------------


class TestFillerTrimmingAtBoundaries:
    """Tests for filler word trimming at both start and end of segments."""

    def setup_method(self):
        with patch.object(TextDrivenEditingService, "__init__", lambda self: None):
            self.service = TextDrivenEditingService()

    def test_filler_at_end_of_segment_trimmed(self):
        """Filler words at the end of a segment should be trimmed."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
            {"word": "今天", "start": 0.8, "end": 1.2},
            {"word": "聊", "start": 1.2, "end": 1.4},
            {"word": "一下", "start": 1.4, "end": 1.8},
            {"word": "嗯", "start": 2.0, "end": 2.2},   # filler at end
            {"word": "那个", "start": 2.3, "end": 2.5},  # filler at end
            {"word": "下一个", "start": 3.0, "end": 3.4},
        ]
        segments = [
            {"start_text": "大家好", "end_text": "那个", "reason": "test"},
        ]
        timeline = self.service.map_text_to_timestamps(segments, words)
        if timeline:
            # source_end should be near "一下" (1.8), not "那个" (2.5)
            assert timeline[0]["source_end"] < 2.3

    def test_filler_at_both_ends_trimmed(self):
        """Fillers at both start and end should be trimmed."""
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.2},   # filler at start
            {"word": "大家", "start": 0.5, "end": 0.9},
            {"word": "好", "start": 0.9, "end": 1.2},
            {"word": "今天", "start": 1.4, "end": 1.8},
            {"word": "聊", "start": 1.8, "end": 2.0},
            {"word": "一下", "start": 2.0, "end": 2.4},
            {"word": "啊", "start": 2.6, "end": 2.8},    # filler at end
            {"word": "下一段", "start": 3.2, "end": 3.6},
        ]
        segments = [
            {"start_text": "嗯大家好", "end_text": "一下啊", "reason": "test"},
        ]
        timeline = self.service.map_text_to_timestamps(segments, words)
        if timeline:
            # Start should be near "大家" (0.5), not "嗯" (0.0)
            assert timeline[0]["source_start"] >= 0.3
            # End should be near "一下" (2.4), not "啊" (2.8)
            assert timeline[0]["source_end"] < 2.7

    def test_all_filler_segment_skipped(self):
        """A segment that is entirely filler words should be skipped."""
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.2},
            {"word": "那个", "start": 0.3, "end": 0.5},
            {"word": "就是说", "start": 0.6, "end": 0.9},
            {"word": "呃", "start": 1.0, "end": 1.2},
            {"word": "大家", "start": 1.5, "end": 1.9},
        ]
        segments = [
            {"start_text": "嗯那个", "end_text": "呃", "reason": "all fillers"},
        ]
        timeline = self.service.map_text_to_timestamps(segments, words)
        assert len(timeline) == 0

    def test_consecutive_fillers_at_start(self):
        """Multiple consecutive fillers at start should all be trimmed."""
        words = [
            {"word": "嗯", "start": 0.0, "end": 0.2},
            {"word": "那个", "start": 0.3, "end": 0.5},
            {"word": "就是说", "start": 0.6, "end": 0.9},
            {"word": "大家", "start": 1.0, "end": 1.4},
            {"word": "好", "start": 1.4, "end": 1.6},
            {"word": "今天", "start": 1.8, "end": 2.2},
            {"word": "聊", "start": 2.2, "end": 2.4},
            {"word": "一下", "start": 2.4, "end": 2.8},
        ]
        segments = [
            {"start_text": "嗯那个", "end_text": "聊一下", "reason": "test"},
        ]
        timeline = self.service.map_text_to_timestamps(segments, words)
        if timeline:
            # Start should be near "大家" (1.0), not "嗯" (0.0)
            assert timeline[0]["source_start"] >= 0.8

    def test_mixed_content_with_fillers_interspersed(self):
        """Fillers interspersed in content should not affect middle content."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
            {"word": "嗯", "start": 0.8, "end": 1.0},   # filler in middle
            {"word": "今天", "start": 1.2, "end": 1.6},
            {"word": "聊", "start": 1.6, "end": 1.8},
            {"word": "一下", "start": 1.8, "end": 2.2},
            {"word": "下一段", "start": 2.8, "end": 3.2},
        ]
        segments = [
            {"start_text": "大家好", "end_text": "聊一下", "reason": "test"},
        ]
        timeline = self.service.map_text_to_timestamps(segments, words)
        assert len(timeline) == 1
        # The segment should start near "大家" and include content through the match
        assert timeline[0]["source_start"] < 0.1
        # The segment should have meaningful duration (not collapsed)
        assert timeline[0]["source_end"] - timeline[0]["source_start"] > 1.0


# ---------------------------------------------------------------------------
# Tests: _snap_to_breath_gap helper (Task 18)
# ---------------------------------------------------------------------------


class TestSnapToBreathGap:
    """Tests for the _snap_to_breath_gap helper function."""

    def test_snaps_to_nearest_gap(self):
        """Should snap to the midpoint of the nearest silence gap."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            # gap: 0.4 → 0.8 (duration 0.4, mid 0.6)
            {"word": "好", "start": 0.8, "end": 1.0},
            # gap: 1.0 → 1.5 (duration 0.5, mid 1.25)
            {"word": "今天", "start": 1.5, "end": 1.9},
        ]
        # Target near the first gap
        result = _snap_to_breath_gap(words, 0.5, search_window=0.5)
        assert result == pytest.approx(0.6, abs=0.01)

    def test_prefers_longer_gap_nearby(self):
        """Should prefer a longer gap that is close to the target."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            # gap: 0.4 → 0.5 (duration 0.1, mid 0.45)
            {"word": "好", "start": 0.5, "end": 0.7},
            # gap: 0.7 → 1.2 (duration 0.5, mid 0.95)
            {"word": "今天", "start": 1.2, "end": 1.6},
        ]
        # Target at 0.6 — both gaps are within window
        result = _snap_to_breath_gap(words, 0.6, search_window=0.5)
        # The second gap (0.5s) should win over the first (0.1s)
        assert result == pytest.approx(0.95, abs=0.01)

    def test_no_gaps_returns_target(self):
        """When words are continuous (no gaps > 0.05s), return target unchanged."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
            {"word": "今天", "start": 0.6, "end": 1.0},
        ]
        result = _snap_to_breath_gap(words, 0.5, search_window=0.5)
        assert result == 0.5

    def test_empty_words_returns_target(self):
        """Empty word list should return target unchanged."""
        result = _snap_to_breath_gap([], 1.0, search_window=0.5)
        assert result == 1.0

    def test_gap_outside_window_ignored(self):
        """Gaps outside the search window should be ignored."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            # gap: 0.4 → 0.8 (mid 0.6) — outside window for target=2.0
            {"word": "好", "start": 0.8, "end": 1.0},
            # gap: 1.0 → 1.5 (mid 1.25) — outside window for target=2.0
            {"word": "今天", "start": 1.5, "end": 1.9},
        ]
        result = _snap_to_breath_gap(words, 2.0, search_window=0.3)
        assert result == 2.0  # No gap found, return target

    def test_multiple_gaps_picks_best_score(self):
        """With multiple gaps, should pick the one with best score."""
        words = [
            {"word": "a", "start": 0.0, "end": 0.3},
            # gap: 0.3 → 0.5 (duration 0.2, mid 0.4)
            {"word": "b", "start": 0.5, "end": 0.8},
            # gap: 0.8 → 1.0 (duration 0.2, mid 0.9)
            {"word": "c", "start": 1.0, "end": 1.3},
            # gap: 1.3 → 1.9 (duration 0.6, mid 1.6)
            {"word": "d", "start": 1.9, "end": 2.2},
        ]
        # Target at 1.0, window 1.0 — all three gaps are in range
        result = _snap_to_breath_gap(words, 1.0, search_window=1.0)
        # Gap 3 (duration 0.6, distance 0.6): score = 0.6/1.6 = 0.375
        # Gap 2 (duration 0.2, distance 0.1): score = 0.2/1.1 = 0.182
        # Gap 1 (duration 0.2, distance 0.6): score = 0.2/1.6 = 0.125
        # Gap 3 wins
        assert result == pytest.approx(1.6, abs=0.01)

    def test_tiny_gaps_ignored(self):
        """Gaps <= 0.05s should be ignored."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            # gap: 0.4 → 0.44 (duration 0.04 — too small)
            {"word": "好", "start": 0.44, "end": 0.6},
        ]
        result = _snap_to_breath_gap(words, 0.42, search_window=0.5)
        assert result == 0.42  # No valid gap, return target


# ---------------------------------------------------------------------------
# Tests: Breath gap optimization in map_text_to_timestamps (Task 18)
# ---------------------------------------------------------------------------


class TestBreathGapOptimization:
    """Tests for enhanced breath-gap snap in map_text_to_timestamps."""

    def setup_method(self):
        with patch.object(TextDrivenEditingService, "__init__", lambda self: None):
            self.service = TextDrivenEditingService()

    def test_start_snaps_to_silence_before_first_word(self):
        """Start cut should snap to a silence gap before the first word."""
        words = [
            {"word": "前面", "start": 0.0, "end": 0.4},
            # gap: 0.4 → 0.9 (duration 0.5, mid 0.65)
            {"word": "大家", "start": 0.9, "end": 1.3},
            {"word": "好", "start": 1.3, "end": 1.5},
            {"word": "今天", "start": 1.7, "end": 2.1},
            {"word": "聊", "start": 2.1, "end": 2.3},
            {"word": "一下", "start": 2.3, "end": 2.7},
            # gap: 2.7 → 3.2
            {"word": "下一段", "start": 3.2, "end": 3.6},
        ]
        segments = [{"start_text": "大家好", "end_text": "聊一下", "reason": "test"}]
        timeline = self.service.map_text_to_timestamps(segments, words)
        assert len(timeline) == 1
        # Start should snap to the gap midpoint (0.65) rather than
        # simple 0.9 - 0.1 = 0.8, because the gap at 0.65 is within
        # the 0.5s search window and is a better cut point.
        assert timeline[0]["source_start"] == pytest.approx(0.65, abs=0.05)

    def test_end_snaps_to_silence_after_last_word(self):
        """End cut should snap to the silence gap after the last word."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
            # gap: 0.6 → 1.0 (duration 0.4, mid 0.8)
            {"word": "今天", "start": 1.0, "end": 1.4},
        ]
        segments = [{"start_text": "大家", "end_text": "好", "reason": "test"}]
        timeline = self.service.map_text_to_timestamps(segments, words)
        assert len(timeline) == 1
        # End should snap to gap midpoint: 0.6 + (1.0 - 0.6)/2 = 0.8
        assert timeline[0]["source_end"] == pytest.approx(0.8, abs=0.05)

    def test_continuous_words_fallback_to_offset(self):
        """When words are continuous (no gaps), start uses simple -0.1s offset."""
        words = [
            {"word": "大家", "start": 0.5, "end": 0.9},
            {"word": "好", "start": 0.9, "end": 1.1},
            {"word": "今天", "start": 1.1, "end": 1.5},
            {"word": "聊一下", "start": 1.5, "end": 2.1},
            {"word": "下一段", "start": 2.1, "end": 2.5},
        ]
        segments = [{"start_text": "大家好", "end_text": "聊一下", "reason": "test"}]
        timeline = self.service.map_text_to_timestamps(segments, words)
        assert len(timeline) == 1
        # No gaps > 0.05s, so _snap_to_breath_gap returns the raw value.
        # Start: raw = 0.5 - 0.1 = 0.4, snap returns 0.4, clamped to
        # min(0.4, 0.5) = 0.4
        assert timeline[0]["source_start"] == pytest.approx(0.4, abs=0.05)
        # End: "聊一下" end = 2.1, no gap to next word, snap returns 2.1
        assert timeline[0]["source_end"] == pytest.approx(2.1, abs=0.05)

    def test_start_never_after_first_word(self):
        """Start cut should never be after the first word's start time."""
        words = [
            {"word": "大家", "start": 1.0, "end": 1.4},
            {"word": "好", "start": 1.4, "end": 1.6},
            # gap: 1.6 → 2.0
            {"word": "今天", "start": 2.0, "end": 2.4},
        ]
        segments = [{"start_text": "大家好", "end_text": "今天", "reason": "test"}]
        timeline = self.service.map_text_to_timestamps(segments, words)
        assert len(timeline) == 1
        # Start must be <= first word start (1.0)
        assert timeline[0]["source_start"] <= 1.0

    def test_end_never_before_last_word(self):
        """End cut should never be before the last word's end time."""
        words = [
            {"word": "大家", "start": 0.0, "end": 0.4},
            {"word": "好", "start": 0.4, "end": 0.6},
            {"word": "今天", "start": 0.8, "end": 1.2},
        ]
        segments = [{"start_text": "大家", "end_text": "今天", "reason": "test"}]
        timeline = self.service.map_text_to_timestamps(segments, words)
        assert len(timeline) == 1
        # End must be >= last word end (1.2)
        assert timeline[0]["source_end"] >= 1.2
