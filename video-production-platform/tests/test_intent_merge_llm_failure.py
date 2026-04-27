"""merge_with_ui_defaults must not let ParsedIntent.defaults() override UI on LLM failure."""

from app.services.intent_parsing_service import IntentParsingService, ParsedIntent


def test_merge_when_llm_failed_keeps_ui_video_count_and_duration():
    parsed = ParsedIntent.defaults()  # video_count=1, max_output_duration=60
    ui = {"video_count": 2, "max_output_duration": 18, "aspect_ratio": "16:9"}
    merged = IntentParsingService.merge_with_ui_defaults(
        parsed, ui, llm_parse_succeeded=False
    )
    assert merged["video_count"] == 2
    assert merged["max_output_duration"] == 18
    assert merged["aspect_ratio"] == "16:9"


def test_merge_when_llm_succeeded_parsed_wins_over_ui():
    parsed = ParsedIntent(
        strip_audio=False,
        video_count=3,
        max_output_duration=90,
        aspect_ratio="1:1",
        bgm_enabled=False,
        subtitle_font=None,
        tts_text=None,
        editing_style=None,
        fade_out=True,
        fade_out_duration=0.3,
    )
    ui = {"video_count": 1, "max_output_duration": 60, "aspect_ratio": "9:16"}
    merged = IntentParsingService.merge_with_ui_defaults(
        parsed, ui, llm_parse_succeeded=True
    )
    assert merged["video_count"] == 3
    assert merged["max_output_duration"] == 90
    assert merged["aspect_ratio"] == "1:1"
