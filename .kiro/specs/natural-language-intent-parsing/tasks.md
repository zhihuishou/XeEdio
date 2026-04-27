# Implementation Plan: Natural Language Intent Parsing

## Overview

Add an LLM-powered `IntentParsingService` to extract structured mixing parameters from natural language input, a `POST /api/mix/parse-intent` preview endpoint, integration into `MixingService.create_mix_task()` with merge logic, and frontend cleanup to remove brittle regex parsing. The implementation follows the existing `TextDrivenEditingService` patterns for LLM config resolution and API calls.

## Tasks

- [x] 1. Create ParsedIntent data model and IntentParsingService core
  - [x] 1.1 Create `app/services/intent_parsing_service.py` with `ParsedIntent` dataclass, `INTENT_DEFAULTS`, `PARAM_CONSTRAINTS`, `VALID_ASPECT_RATIOS` constants, and `IntentParsingService` class skeleton
    - Define `ParsedIntent` dataclass with fields: `strip_audio`, `video_count`, `max_output_duration`, `aspect_ratio`, `bgm_enabled`, `subtitle_font`, `tts_text`, `editing_style`
    - Implement `ParsedIntent.to_dict()`, `ParsedIntent.from_dict()`, and `ParsedIntent.defaults()` methods
    - _Requirements: 1.2, 2.1, 2.2, 9.2_

  - [x] 1.2 Implement `IntentParsingService._get_llm_config()` following the `TextDrivenEditingService._get_llm_config()` pattern
    - Resolution order: `text_llm` config → default LLM provider → VLM config fallback
    - Use `ExternalConfig.get_instance()` for config access
    - _Requirements: 4.1_

  - [x] 1.3 Implement `IntentParsingService._call_llm()` with httpx, 10-second timeout, temperature=0.1, and error handling
    - Build OpenAI-compatible chat completion payload with system prompt + user prompt
    - Return raw content string on success, `None` on any failure
    - Log warnings on timeout, HTTP errors, and network errors (truncate responses to 200 chars, never log API keys)
    - _Requirements: 1.1, 1.3, 4.2, 4.3, 4.4, 5.1_

  - [x] 1.4 Implement `IntentParsingService._extract_json()` static method
    - Try direct `json.loads()` first
    - Fall back to searching for `{...}` pattern in the response text
    - Return parsed dict or `None`
    - _Requirements: 1.3, 5.2_

  - [x] 1.5 Implement `IntentParsingService._validate_and_clamp()` static method
    - Clamp `video_count` to [1, 10], `max_output_duration` to [15, 300]
    - Validate `aspect_ratio` against `{"16:9", "9:16", "1:1"}`, default to `"9:16"`
    - Coerce types: string numbers to int, string booleans to bool
    - Fill missing fields with defaults from `INTENT_DEFAULTS`
    - _Requirements: 1.4, 1.5, 1.6, 1.7, 2.1, 2.2, 2.3, 5.3_

  - [x] 1.6 Implement `IntentParsingService.merge_with_ui_defaults()` static method
    - Priority chain: ParsedIntent non-None values > UI defaults > system defaults
    - Return merged dict ready for `mix_params` storage
    - _Requirements: 3.1, 3.2_

  - [x] 1.7 Implement `IntentParsingService.parse_intent()` orchestrator method
    - Skip LLM call if `director_prompt` is empty/whitespace
    - Call `_get_llm_config()` → `_call_llm()` → `_extract_json()` → `_validate_and_clamp()`
    - Catch all exceptions internally, return `ParsedIntent.defaults()` on any failure
    - Log info on success with extracted key values, warning on failure
    - _Requirements: 1.1, 1.3, 5.1, 5.2, 5.4_

- [x] 2. Checkpoint — Verify IntentParsingService core
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Add parse-intent API endpoint and schemas
  - [x] 3.1 Add `ParseIntentRequest` and `ParseIntentResponse` Pydantic schemas to `app/schemas/mix.py`
    - `ParseIntentRequest`: `director_prompt` string field, default `""`, max_length 500
    - `ParseIntentResponse`: all ParsedIntent fields with defaults filled in
    - _Requirements: 8.1, 8.4_

  - [x] 3.2 Add `POST /api/mix/parse-intent` endpoint to `app/routers/mix.py`
    - Import `IntentParsingService` and `ParsedIntent`
    - Return defaults without LLM call if `director_prompt` is empty
    - Otherwise call `service.parse_intent()` and return the result
    - Require authentication via `require_role("intern", "operator", "admin")`
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [ ]* 3.3 Write unit tests for the parse-intent endpoint in `tests/test_intent_parsing.py`
    - Test valid request with mocked LLM returns correct parsed values
    - Test empty `director_prompt` returns defaults without LLM call
    - Test missing `director_prompt` field returns defaults
    - _Requirements: 8.1, 8.2, 8.4_

- [x] 4. Integrate IntentParsingService into MixingService
  - [x] 4.1 Modify `MixingService.create_mix_task()` in `app/services/mixing_service.py` to call IntentParsingService
    - Import `IntentParsingService` and `ParsedIntent`
    - Parse `director_prompt` before building `mix_params` JSON
    - Build `ui_defaults` dict from request fields
    - Merge parsed intent with UI defaults using `merge_with_ui_defaults()`
    - Store merged values (including `strip_audio`, `subtitle_font`, `editing_style`) in `mix_params` JSON
    - Wrap in try/except — on any failure, proceed with original request values
    - _Requirements: 7.1, 7.2, 5.4_

  - [x] 4.2 Modify `MixingService.execute_mix()` to read `strip_audio` and `subtitle_font` from `mix_params` and pass them to the mixing engine
    - Read `strip_audio` from params dict, pass to pipeline if true
    - Read `subtitle_font` from params dict, pass to subtitle rendering step if specified
    - _Requirements: 7.3, 7.4_

  - [ ]* 4.3 Write unit tests for MixingService integration in `tests/test_intent_parsing.py`
    - Test `create_mix_task` calls IntentParsingService and stores merged params
    - Test `create_mix_task` proceeds with defaults when IntentParsingService raises
    - _Requirements: 7.1, 7.2, 5.4_

- [x] 5. Structured multi-video splitting via LLM segment output
  - [x] 5.1 Modify `TextDrivenEditingService.select_segments_with_llm()` to accept `video_count` parameter
    - When `video_count` > 1, append multi-video instruction to the LLM prompt requiring each segment to include a `video_number` integer field (1 to `video_count`)
    - When `video_count` is 1 or not specified, do not include multi-video instructions
    - _Requirements: 10.1, 10.2, 10.5_

  - [x] 5.2 Modify `TextDrivenEditingService.map_text_to_timestamps()` to preserve `video_number` field
    - Pass through `video_number` from LLM segment output into the timeline entry dict
    - If `video_number` is missing from a segment, set it to `None` in the timeline entry
    - _Requirements: 10.3_

  - [x] 5.3 Modify `TextDrivenEditingService.generate_text_driven_timeline()` to accept and pass `video_count` to `select_segments_with_llm()`
    - _Requirements: 10.1_

  - [x] 5.4 Rewrite `_split_timeline_by_video()` in `AIDirectorService` to read `video_number` field from timeline entries
    - Primary strategy: read `video_number` field directly from each entry
    - Fallback strategy: regex pattern matching on `reason` text (for entries missing `video_number`)
    - If no video_number fields and no regex matches found, treat all entries as a single video
    - _Requirements: 10.4, 10.6_

  - [x] 5.5 Modify `AIDirectorService._run_text_driven()` to pass `video_count` from `mix_params` through to `TextDrivenEditingService.generate_text_driven_timeline()`
    - Read `video_count` from the task's mix_params (set by IntentParsingService or UI defaults)
    - Pass it as a parameter to `generate_text_driven_timeline()`
    - _Requirements: 10.1, 10.5_

  - [ ]* 5.6 Write unit tests for multi-video splitting in `tests/test_intent_parsing.py`
    - Test `_split_timeline_by_video` with entries containing `video_number` field → correct grouping
    - Test `_split_timeline_by_video` with entries missing `video_number` but having reason tags → regex fallback works
    - Test `_split_timeline_by_video` with no video markers → single group returned
    - Test `select_segments_with_llm` prompt includes multi-video instruction when `video_count` > 1
    - Test `select_segments_with_llm` prompt does NOT include multi-video instruction when `video_count` = 1
    - _Requirements: 10.1, 10.4, 10.5, 10.6_

- [x] 6. Checkpoint — Verify backend integration
  - Ensure all tests pass, ask the user if questions arise.

- [~] 7. Write unit tests for IntentParsingService
  - [ ] 7.1 Write unit tests for `ParsedIntent` model in `tests/test_intent_parsing.py`
    - Test `defaults()` returns exact default values from Requirement 2.2
    - Test `to_dict()` returns all fields including None values
    - Test `from_dict()` with valid data, partial data, and empty dict
    - _Requirements: 2.1, 2.2, 9.1_

  - [ ] 7.2 Write unit tests for `_extract_json()` in `tests/test_intent_parsing.py`
    - Test clean JSON string → parsed dict
    - Test JSON wrapped in markdown fences (```json ... ```) → parsed dict
    - Test JSON with surrounding explanation text → parsed dict
    - Test completely invalid string → None
    - Test empty string → None
    - _Requirements: 1.3, 5.2_

  - [ ] 7.3 Write unit tests for `_validate_and_clamp()` in `tests/test_intent_parsing.py`
    - Test in-range values pass through unchanged
    - Test `video_count` > 10 clamped to 10, < 1 clamped to 1
    - Test `max_output_duration` > 300 clamped to 300, < 15 clamped to 15
    - Test invalid `aspect_ratio` falls back to `"9:16"`
    - Test type coercion: `"3"` → 3, `"true"` → True
    - _Requirements: 5.3, 1.4, 1.5_

  - [ ] 7.4 Write unit tests for `merge_with_ui_defaults()` in `tests/test_intent_parsing.py`
    - Test ParsedIntent values override UI defaults for non-None fields
    - Test UI defaults used when ParsedIntent has None values
    - Test both empty → system defaults
    - Test both fully populated → ParsedIntent wins
    - _Requirements: 3.1, 3.2_

  - [ ] 7.5 Write unit tests for `parse_intent()` with mocked LLM in `tests/test_intent_parsing.py`
    - Test empty prompt returns defaults without LLM call
    - Test successful LLM response returns correct ParsedIntent
    - Test LLM timeout returns defaults
    - Test LLM HTTP 500 returns defaults
    - Test LLM returns non-JSON text → attempt extraction → fallback to defaults
    - _Requirements: 1.1, 1.3, 5.1, 5.2_

- [ ] 8. Write property-based tests for IntentParsingService
  - [ ]* 8.1 Write property test for JSON extraction robustness in `tests/test_intent_parsing_properties.py`
    - **Property 1: JSON extraction robustness**
    - Generate random strings with/without embedded JSON objects using Hypothesis
    - Verify `_extract_json` returns a dict when valid JSON `{...}` is embedded, and `None` when no valid JSON exists
    - Use `@given(st.text())` and `@given(st.dictionaries(st.text(), st.one_of(st.text(), st.integers(), st.booleans(), st.none())))` strategies
    - **Validates: Requirements 1.3, 5.2**

  - [ ]* 8.2 Write property test for value clamping invariant in `tests/test_intent_parsing_properties.py`
    - **Property 2: Value clamping invariant**
    - Generate random integers for `video_count` and `max_output_duration`, random strings for `aspect_ratio`
    - Verify `_validate_and_clamp` always returns `video_count` in [1, 10], `max_output_duration` in [15, 300], `aspect_ratio` in `{"16:9", "9:16", "1:1"}`
    - Use `@given(st.integers(), st.integers(), st.text())` strategies
    - **Validates: Requirements 5.3**

  - [ ]* 8.3 Write property test for merge priority in `tests/test_intent_parsing_properties.py`
    - **Property 3: Merge priority — ParsedIntent overrides UI defaults**
    - Generate random `ParsedIntent` objects and `ui_defaults` dicts
    - Verify: for every field where ParsedIntent has a non-None value, the merged result uses the ParsedIntent value; for None fields, the merged result uses ui_defaults or system default
    - **Validates: Requirements 3.1, 3.2**

  - [ ]* 8.4 Write property test for serialization round-trip in `tests/test_intent_parsing_properties.py`
    - **Property 4: ParsedIntent serialization round-trip**
    - Generate random `ParsedIntent` objects including Chinese characters in `subtitle_font` and `tts_text`
    - Verify `json.dumps(intent.to_dict(), ensure_ascii=False)` → `json.loads()` → `ParsedIntent.from_dict()` produces equivalent object
    - **Validates: Requirements 9.1, 9.3**

- [~] 9. Checkpoint — Verify all backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Frontend changes — Remove regex and integrate parse-intent
  - [x] 10.1 Remove regex-based parsing from `app/templates/mix_chat.html`
    - Remove `minMatch`, `secMatch`, and `countMatch` regex patterns
    - Remove any client-side parameter extraction logic that sets duration/count from regex
    - _Requirements: 6.1, 6.2_

  - [x] 10.2 Add `parseIntent()` function to `mix_chat.html` that calls `POST /api/mix/parse-intent`
    - Send raw `director_prompt` to the backend
    - Display extracted parameters as a confirmation message in the chat before task creation
    - Implement `showParsedParams()` helper to format the extracted params for display
    - _Requirements: 6.3, 6.4, 8.1_

  - [x] 10.3 Update the mix task submission flow in `mix_chat.html` to send raw `director_prompt` and UI panel defaults to the backend
    - Remove any client-side parameter merging
    - Let the backend handle all parameter extraction and merging
    - _Requirements: 3.3, 6.3_

- [~] 11. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The implementation follows existing patterns: `TextDrivenEditingService._get_llm_config()` for LLM config, `_parse_segments_json()` for JSON extraction, and `mix.py` router for endpoint structure
- The default LLM provider is gpt-5.4 via luxee.ai as configured in `ExternalConfig`
- All tests go in `XeEdio/video-production-platform/tests/`
