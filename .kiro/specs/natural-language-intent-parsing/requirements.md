# Requirements Document

## Introduction

This feature adds an LLM-powered intent parsing layer to the XeEdio video mixing pipeline. Currently, the frontend `mix_chat.html` uses simple regex to extract duration and count from user input, while other parameters (font, subtitles, TTS, aspect ratio, audio stripping) must be set manually via the UI panel. The `director_prompt` is passed raw to the VLM/LLM without structured extraction.

The Natural Language Intent Parsing feature will intercept the user's natural language input, call an LLM to extract structured mixing parameters as JSON, apply sensible defaults for any unspecified parameters, and merge the extracted parameters with UI panel settings (with LLM-extracted values taking priority). This eliminates the need for manual parameter configuration in most cases and replaces the brittle frontend regex parsing.

## Glossary

- **Intent_Parser**: The backend service module (`IntentParsingService`) responsible for calling the LLM to extract structured mixing parameters from natural language input.
- **Mix_Params**: The JSON object containing all mixing parameters (aspect_ratio, video_count, max_output_duration, strip_audio, subtitle_font, tts_text, etc.) that drives the mixing pipeline.
- **Director_Prompt**: The user's free-form natural language instruction describing how they want the video edited.
- **UI_Panel_Defaults**: The parameter values set by the user via the frontend settings panel (aspect ratio, duration, count, TTS voice, etc.).
- **Parsed_Intent**: The structured JSON object returned by the LLM after parsing the user's natural language input.
- **LLM_Provider**: The external LLM API endpoint configured in `ExternalConfig` (e.g., luxee.ai gpt-5.4 or dashscope qwen3.6-plus).
- **MixingService**: The existing backend service that orchestrates mixing task creation and execution.
- **Mix_Chat_Frontend**: The `mix_chat.html` Alpine.js chat interface where users input natural language instructions and manage mixing parameters.

## Requirements

### Requirement 1: LLM-Based Parameter Extraction

**User Story:** As a video editor, I want the system to automatically extract mixing parameters from my natural language instructions, so that I don't have to manually configure each setting in the UI panel.

#### Acceptance Criteria

1. WHEN a Director_Prompt is submitted, THE Intent_Parser SHALL call the LLM_Provider to extract a Parsed_Intent JSON object containing recognized mixing parameters.
2. THE Parsed_Intent SHALL support extraction of the following fields: `strip_audio` (boolean), `subtitle_font` (string), `video_count` (integer), `max_output_duration` (integer, seconds), `aspect_ratio` (string), `tts_text` (string), `editing_style` (string), and `bgm_enabled` (boolean).
3. WHEN the LLM_Provider returns a response, THE Intent_Parser SHALL parse the response into a valid Parsed_Intent JSON object within 10 seconds.
4. WHEN the Director_Prompt contains Chinese duration expressions (e.g., "3分钟", "40秒", "3-5分钟"), THE Intent_Parser SHALL extract the duration as an integer in seconds (e.g., 180, 40, 300).
5. WHEN the Director_Prompt contains Chinese count expressions (e.g., "3条", "5个"), THE Intent_Parser SHALL extract the video count as an integer.
6. WHEN the Director_Prompt contains audio-related instructions (e.g., "去除背景声", "保留原声"), THE Intent_Parser SHALL extract `strip_audio` as a boolean value.
7. WHEN the Director_Prompt contains font name references (e.g., "字体用抖音美好体"), THE Intent_Parser SHALL extract `subtitle_font` as the font name string.

### Requirement 2: Sensible Default Values

**User Story:** As a video editor, I want the system to apply sensible defaults for any parameters I don't explicitly mention, so that the mixing pipeline works correctly even with minimal instructions.

#### Acceptance Criteria

1. THE Intent_Parser SHALL provide default values for all supported Mix_Params fields when the Director_Prompt does not specify them.
2. THE Intent_Parser SHALL use the following default values: `strip_audio` = false, `video_count` = 1, `max_output_duration` = 60, `aspect_ratio` = "9:16", `bgm_enabled` = false, `subtitle_font` = null (use system default), `tts_text` = null, `editing_style` = null.
3. WHEN a parameter is explicitly mentioned in the Director_Prompt, THE Intent_Parser SHALL use the extracted value instead of the default.

### Requirement 3: Parameter Override Priority

**User Story:** As a video editor, I want my natural language instructions to override the UI panel settings, so that I can quickly change parameters by typing instead of clicking through the panel.

#### Acceptance Criteria

1. WHEN both UI_Panel_Defaults and Parsed_Intent contain a value for the same parameter, THE MixingService SHALL use the Parsed_Intent value.
2. WHEN the Parsed_Intent does not contain a value for a parameter, THE MixingService SHALL fall back to the UI_Panel_Defaults value.
3. THE Mix_Chat_Frontend SHALL send both the UI_Panel_Defaults and the Director_Prompt to the backend, and THE backend SHALL perform the merge.

### Requirement 4: LLM Prompt Construction

**User Story:** As a developer, I want the intent parsing LLM prompt to be well-structured and produce consistent JSON output, so that the parsing is reliable across different user inputs.

#### Acceptance Criteria

1. THE Intent_Parser SHALL send a system prompt to the LLM_Provider that defines the expected JSON output schema with field names, types, and descriptions.
2. THE Intent_Parser SHALL include the user's Director_Prompt as the user message in the LLM API call.
3. THE Intent_Parser SHALL set the LLM temperature to 0.1 or lower to maximize output consistency.
4. THE Intent_Parser SHALL instruct the LLM to output only valid JSON with no additional explanation text.

### Requirement 5: Error Handling and Fallback

**User Story:** As a video editor, I want the system to gracefully handle LLM failures, so that my mixing task still proceeds even if intent parsing fails.

#### Acceptance Criteria

1. IF the LLM_Provider API call fails (timeout, HTTP error, or network error), THEN THE Intent_Parser SHALL return the default Mix_Params values and log the error.
2. IF the LLM_Provider returns a response that cannot be parsed as valid JSON, THEN THE Intent_Parser SHALL attempt to extract a JSON object from the response text, and if that also fails, return default Mix_Params values.
3. IF the LLM_Provider returns JSON with out-of-range values (e.g., `video_count` > 10, `max_output_duration` > 300 or < 15), THEN THE Intent_Parser SHALL clamp the values to the valid range defined in the MixCreateRequest schema.
4. IF the Intent_Parser encounters any error, THEN THE MixingService SHALL proceed with the mixing task using fallback parameters and SHALL NOT block task creation.

### Requirement 6: Frontend Regex Removal

**User Story:** As a developer, I want to remove the brittle regex-based parameter extraction from the frontend, so that all parameter parsing is handled consistently by the backend LLM.

#### Acceptance Criteria

1. THE Mix_Chat_Frontend SHALL remove the existing regex-based duration parsing logic (the `minMatch` and `secMatch` patterns).
2. THE Mix_Chat_Frontend SHALL remove the existing regex-based video count parsing logic (the `countMatch` pattern).
3. THE Mix_Chat_Frontend SHALL send the raw Director_Prompt text and UI_Panel_Defaults to the backend without client-side parameter extraction.
4. WHEN the backend returns the Parsed_Intent, THE Mix_Chat_Frontend SHALL display the extracted parameters to the user as a confirmation message before task creation begins.

### Requirement 7: Integration with Mixing Pipeline

**User Story:** As a developer, I want the parsed intent parameters to flow into the existing mixing pipeline, so that the extracted parameters actually control video generation.

#### Acceptance Criteria

1. WHEN a mix task is created, THE MixingService SHALL call the Intent_Parser to parse the Director_Prompt before storing Mix_Params.
2. THE MixingService SHALL merge the Parsed_Intent with UI_Panel_Defaults and store the merged result as the task's Mix_Params JSON.
3. WHEN `strip_audio` is true in the merged Mix_Params, THE MixingService SHALL pass the strip_audio flag to the mixing engine for audio removal during rendering.
4. WHEN `subtitle_font` is specified in the merged Mix_Params, THE MixingService SHALL pass the font name to the subtitle rendering step instead of using the system-resolved default.

### Requirement 8: Intent Parsing API Endpoint

**User Story:** As a frontend developer, I want a dedicated API endpoint for intent parsing, so that the frontend can preview extracted parameters before submitting the full mix task.

#### Acceptance Criteria

1. THE system SHALL expose a `POST /api/mix/parse-intent` endpoint that accepts a JSON body with a `director_prompt` string field.
2. WHEN a valid request is received, THE endpoint SHALL return the Parsed_Intent JSON object with all extracted and default parameter values.
3. THE endpoint SHALL respond within 10 seconds under normal LLM_Provider latency.
4. IF the Director_Prompt field is empty or missing, THEN THE endpoint SHALL return the default Mix_Params values without calling the LLM_Provider.

### Requirement 9: Parsed Intent JSON Serialization Round-Trip

**User Story:** As a developer, I want the Parsed_Intent JSON to survive serialization and deserialization without data loss, so that parameters are preserved accurately through the pipeline.

#### Acceptance Criteria

1. FOR ALL valid Parsed_Intent objects, serializing to JSON and deserializing back SHALL produce an equivalent object (round-trip property).
2. THE Parsed_Intent JSON schema SHALL use only primitive JSON types (string, number, boolean, null) to ensure cross-language compatibility.
3. WHEN the Parsed_Intent contains Chinese characters (e.g., font names, TTS text), THE serialization SHALL use `ensure_ascii=False` to preserve readability.

### Requirement 10: Structured Multi-Video Splitting via LLM Segment Output

**User Story:** As a video editor, when I request multiple output videos (e.g., "剪2条"), I want the system to reliably split the timeline into separate videos using structured data from the LLM, not brittle regex matching on free-text reason fields.

#### Acceptance Criteria

1. WHEN `video_count` > 1 in the merged Mix_Params, THE TextDrivenEditingService LLM segment selection prompt SHALL instruct the LLM to include a `video_number` integer field (1-based) in each returned segment, indicating which output video the segment belongs to.
2. THE LLM segment selection prompt SHALL specify that each segment's `video_number` must be between 1 and `video_count`, and that each video should have at least one segment.
3. THE `map_text_to_timestamps()` method SHALL preserve the `video_number` field from the LLM output into the timeline entries.
4. THE `_split_timeline_by_video()` function in AIDirectorService SHALL read the `video_number` field directly from timeline entries instead of using regex pattern matching on the `reason` text.
5. WHEN `video_count` is 1 or not specified, THE LLM prompt SHALL NOT include multi-video instructions, and all entries SHALL be treated as a single video.
6. WHEN a timeline entry is missing the `video_number` field (fallback), THE `_split_timeline_by_video()` function SHALL fall back to regex pattern matching on the `reason` field as a secondary strategy.
