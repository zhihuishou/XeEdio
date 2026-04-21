# Requirements Document

## Introduction

The AI Director (AI 编导) module replaces the current blind-cut mixing logic in XeEdio's video production platform with VLM-powered intelligent video editing. The existing `mixing_engine.py` uses fixed-interval B-roll insertion (`combine_videos()`) that blindly splits A-roll into equal chunks and inserts B-roll between them, often covering critical moments such as product demonstrations or talent gestures. The AI Director introduces a "decision-then-execution" architecture: extract frames from A-roll, send them to a Vision Language Model (VLM) for semantic analysis, receive a JSON timeline, and execute precise cuts based on that timeline. The module also introduces three distinct mixing modes to support different content creation workflows — pure mix with original audio, mix with AI-generated script and voiceover, and pure B-roll montage with AI voiceover.

## Glossary

- **AI_Director_Service**: The orchestration service that coordinates VLM frame analysis, timeline generation, and pipeline routing across the three mixing modes.
- **VLM_Service**: The service responsible for communicating with the Vision Language Model API (api.luxee.ai, model gpt-5.4) to analyze A-roll frames and generate editing timelines.
- **Timeline_Executor**: The component within `mixing_engine.py` that reads a JSON timeline array and executes precise video cuts using MoviePy, replacing the current fixed-interval insertion logic.
- **Frame_Extractor**: The component that uses FFmpeg to extract low-resolution frames from A-roll videos at configurable intervals and encodes them as base64 strings.
- **Timeline**: A JSON array of objects, each containing `type` (a_roll or b_roll), `start` (seconds), `end` (seconds), and `reason` (string explaining the editorial decision).
- **A-roll**: Primary video footage featuring talent speaking, product demonstrations, or main content with an audio track.
- **B-roll**: Supplementary video footage such as product close-ups, stock footage, or scenic shots used to enrich visual storytelling.
- **Mixing_Mode**: One of three pipeline configurations — `pure_mix` (Mode 1), `mix_with_script` (Mode 2), or `broll_voiceover` (Mode 3).
- **AI_TTS_Service**: The service that generates high-quality voiceover audio from LLM-generated scripts, with fallback to Edge-TTS.
- **Subtitle_Generator**: The component that produces subtitle tracks — via Whisper ASR for Mode 1, or directly from LLM script text for Mode 2 and Mode 3.
- **Blind_Cut_Engine**: The existing fixed-interval B-roll insertion logic in `combine_videos()` that serves as the fallback when the VLM is unavailable.
- **MixCreateRequest**: The Pydantic request schema for creating a mixing task, extended with a `mixing_mode` field.
- **MixingService**: The existing task orchestration service in `mixing_service.py` that manages task lifecycle and background execution.

## Requirements

### Requirement 1: VLM Frame Extraction

**User Story:** As a video editor, I want the system to extract representative frames from A-roll videos, so that the VLM can analyze visual content for intelligent editing decisions.

#### Acceptance Criteria

1. WHEN an A-roll video file path is provided, THE Frame_Extractor SHALL use FFmpeg to extract one frame every N seconds, where N is the `vlm.frame_interval` value from config.yaml (default 2 seconds).
2. THE Frame_Extractor SHALL resize each extracted frame to a maximum width of 512 pixels while preserving the aspect ratio, to reduce token consumption.
3. THE Frame_Extractor SHALL encode each extracted frame as a base64 string in JPEG format.
4. WHILE extracting frames, THE Frame_Extractor SHALL stop after reaching the `vlm.max_frames` limit from config.yaml (default 30 frames).
5. THE Frame_Extractor SHALL return a list of tuples, each containing the frame timestamp in seconds and the base64-encoded image string.
6. IF the A-roll video file is unreadable or has zero duration, THEN THE Frame_Extractor SHALL raise a descriptive error indicating the file path and failure reason.
7. IF FFmpeg is not available on the system, THEN THE Frame_Extractor SHALL raise a descriptive error indicating that FFmpeg is required.

### Requirement 2: VLM Timeline Generation

**User Story:** As a video editor, I want the VLM to analyze A-roll frames and generate an intelligent editing timeline, so that B-roll is inserted at semantically appropriate moments rather than at fixed intervals.

#### Acceptance Criteria

1. WHEN a list of base64-encoded frames, an audio transcript, and a list of B-roll descriptions are provided, THE VLM_Service SHALL send a multimodal request to the VLM API endpoint configured in `vlm.api_url` using the model specified in `vlm.model`.
2. THE VLM_Service SHALL construct the prompt to instruct the VLM to identify safe B-roll insertion points by analyzing talent gestures, product demonstrations, and semantic content of the audio transcript.
3. THE VLM_Service SHALL parse the VLM response as a JSON array of Timeline objects, each containing `type` (string: "a_roll" or "b_roll"), `start` (number: seconds), `end` (number: seconds), and `reason` (string).
4. WHEN the VLM response contains valid JSON, THE VLM_Service SHALL validate that all Timeline entries have non-negative `start` values, that `end` is greater than `start` for each entry, and that entries are sorted by `start` time without overlapping intervals.
5. IF the VLM API returns a non-JSON response or the JSON does not match the expected Timeline schema, THEN THE VLM_Service SHALL raise a descriptive error and return a failure indicator so the caller can fall back to blind-cut mode.
6. IF the VLM API request times out after 60 seconds, THEN THE VLM_Service SHALL retry once, and if the retry also fails, return a failure indicator.
7. IF the VLM API returns an HTTP error status (4xx or 5xx), THEN THE VLM_Service SHALL log the error details and return a failure indicator.
8. THE VLM_Service SHALL include the VLM API key from `vlm.api_key` in the Authorization header as a Bearer token.

### Requirement 3: Timeline Execution

**User Story:** As a video editor, I want the system to execute a JSON timeline with precise video cuts, so that the final output reflects the VLM's intelligent editing decisions.

#### Acceptance Criteria

1. WHEN a valid Timeline JSON array is provided along with A-roll and B-roll file paths, THE Timeline_Executor SHALL produce a video where each segment matches the specified `type`, `start`, and `end` from the Timeline.
2. THE Timeline_Executor SHALL use MoviePy `subclipped(start, end)` to extract the precise time range from A-roll source files for `a_roll` segments.
3. THE Timeline_Executor SHALL select B-roll clips from the available B-roll pool using a round-robin strategy (via `itertools.cycle`) for `b_roll` segments, trimming each to the duration specified in the Timeline entry.
4. THE Timeline_Executor SHALL strip audio from all video segments and apply the separate audio track (A-roll extracted audio or TTS audio) to the final output.
5. THE Timeline_Executor SHALL resize all segments to the target resolution based on the configured aspect ratio, using black padding to preserve the original aspect ratio.
6. THE Timeline_Executor SHALL apply the configured transition effect to B-roll segments.
7. THE Timeline_Executor SHALL concatenate all rendered segments using FFmpeg concat demuxer to produce the final output file.
8. IF a Timeline entry references a time range beyond the A-roll duration, THEN THE Timeline_Executor SHALL clamp the segment to the actual A-roll duration and log a warning.

### Requirement 4: AI Director Service Orchestration

**User Story:** As a video editor, I want a single service to orchestrate the full AI-directed editing pipeline, so that I can trigger intelligent mixing with one API call.

#### Acceptance Criteria

1. WHEN a mixing task with `mixing_mode` set to `pure_mix` or `mix_with_script` is created, THE AI_Director_Service SHALL execute the three-step pipeline: frame extraction, VLM timeline generation, and timeline execution.
2. THE AI_Director_Service SHALL extract audio transcript from A-roll using Whisper ASR (faster-whisper) and pass the transcript text to the VLM_Service as context for timeline generation.
3. THE AI_Director_Service SHALL collect B-roll descriptions (filenames and durations) from the provided B-roll assets and pass them to the VLM_Service.
4. WHEN the VLM_Service returns a valid Timeline, THE AI_Director_Service SHALL pass the Timeline to the Timeline_Executor for rendering.
5. IF the VLM_Service returns a failure indicator, THEN THE AI_Director_Service SHALL fall back to the existing Blind_Cut_Engine (`combine_videos()` with fixed-interval insertion) and log a warning that AI Director mode was unavailable.
6. THE AI_Director_Service SHALL update the task progress status to indicate the current pipeline stage: "extracting_frames", "analyzing_with_vlm", "executing_timeline", or "falling_back_to_blind_cut".

### Requirement 5: Three Mixing Mode Support

**User Story:** As a video editor, I want to choose between three mixing modes, so that I can create different types of video content with the appropriate pipeline.

#### Acceptance Criteria

1. THE MixCreateRequest SHALL include a `mixing_mode` field that accepts one of three values: `pure_mix`, `mix_with_script`, or `broll_voiceover`.
2. WHEN `mixing_mode` is `pure_mix`, THE MixingService SHALL require at least one A-roll asset, use A-roll original audio as the audio track, invoke the AI_Director_Service for timeline generation, and generate subtitles via Whisper ASR.
3. WHEN `mixing_mode` is `mix_with_script`, THE MixingService SHALL require at least one A-roll asset and a non-empty `tts_text` field, generate TTS voiceover audio from the script, invoke the AI_Director_Service for timeline generation using the script as transcript context, and use the script text directly as subtitles.
4. WHEN `mixing_mode` is `mix_with_script` and no B-roll assets are provided, THE MixingService SHALL use the LLM keyword generation endpoint to extract keywords from the script, search Pexels for matching stock footage, and download the results as B-roll assets automatically.
5. WHEN `mixing_mode` is `broll_voiceover`, THE MixingService SHALL require at least one B-roll asset and a non-empty `tts_text` field, generate TTS voiceover audio, skip AI Director analysis (no A-roll to analyze), use the existing Blind_Cut_Engine with random or sequential B-roll arrangement, and use the script text directly as subtitles.
6. IF `mixing_mode` is `pure_mix` and no A-roll assets are provided, THEN THE MixingService SHALL reject the request with a validation error.
7. IF `mixing_mode` is `broll_voiceover` and no B-roll assets are provided, THEN THE MixingService SHALL reject the request with a validation error.
8. IF `mixing_mode` is `broll_voiceover` and `tts_text` is empty, THEN THE MixingService SHALL reject the request with a validation error.

### Requirement 6: AI TTS Integration

**User Story:** As a video editor, I want the system to generate high-quality AI voiceover from scripts, so that Mode 2 and Mode 3 videos have professional narration.

#### Acceptance Criteria

1. WHEN `ai_tts.api_url` and `ai_tts.api_key` are configured in config.yaml, THE AI_TTS_Service SHALL use the external AI TTS API to synthesize voiceover audio from the provided script text.
2. IF `ai_tts.api_url` is empty or the AI TTS API request fails, THEN THE AI_TTS_Service SHALL fall back to Edge-TTS using the voice configured in `tts.voices` from config.yaml, provided `ai_tts.fallback_to_edge_tts` is true.
3. IF `ai_tts.fallback_to_edge_tts` is false and the AI TTS API is unavailable, THEN THE AI_TTS_Service SHALL raise a descriptive error indicating that TTS synthesis failed and no fallback is configured.
4. THE AI_TTS_Service SHALL save the synthesized audio file to `storage/tasks/{task_id}/tts_audio.mp3`.
5. THE AI_TTS_Service SHALL return the audio file path and duration in seconds.

### Requirement 7: Subtitle Generation

**User Story:** As a video editor, I want subtitles generated automatically for all mixing modes, so that the output videos are accessible and engaging.

#### Acceptance Criteria

1. WHEN `mixing_mode` is `pure_mix`, THE Subtitle_Generator SHALL use Whisper ASR (faster-whisper) to transcribe the A-roll audio and generate an ASS subtitle file with word-level timestamps.
2. WHEN `mixing_mode` is `mix_with_script` or `broll_voiceover`, THE Subtitle_Generator SHALL generate an ASS subtitle file from the LLM script text, splitting the text into segments aligned with the TTS audio duration.
3. THE Subtitle_Generator SHALL produce subtitle files in ASS format with configurable font size proportional to the output video resolution.
4. IF Whisper ASR is unavailable in Mode 1, THEN THE Subtitle_Generator SHALL fall back to FFmpeg silence detection to identify speech segments, and log a warning that subtitle text content is unavailable.
5. THE Subtitle_Generator SHALL burn the ASS subtitle file into the final video output using FFmpeg.

### Requirement 8: Graceful Degradation

**User Story:** As a system operator, I want the system to degrade gracefully when external services are unavailable, so that video production continues without interruption.

#### Acceptance Criteria

1. IF the VLM API is unreachable or returns errors for a mixing task, THEN THE AI_Director_Service SHALL automatically fall back to the Blind_Cut_Engine and complete the mixing task using fixed-interval B-roll insertion.
2. IF the AI TTS API is unavailable, THEN THE AI_TTS_Service SHALL automatically fall back to Edge-TTS, provided `ai_tts.fallback_to_edge_tts` is true in config.yaml.
3. IF Whisper ASR model loading fails, THEN THE Subtitle_Generator SHALL fall back to FFmpeg silence detection for speech segment identification.
4. WHEN any fallback is activated, THE system SHALL log a warning message identifying the failed service and the fallback mechanism used.
5. WHEN any fallback is activated, THE system SHALL include the fallback status in the task progress update so the frontend can display it to the user.

### Requirement 9: Frontend Mode Selector

**User Story:** As a video editor, I want to select the mixing mode from the /mix page, so that I can choose the appropriate pipeline before starting a task.

#### Acceptance Criteria

1. THE /mix page Step 3 (params configuration) SHALL display a mode selector with three options: "纯混剪" (pure_mix), "混剪 + AI 脚本" (mix_with_script), and "纯素材 + AI 口播" (broll_voiceover).
2. WHEN `pure_mix` mode is selected, THE /mix page SHALL hide the TTS text input and voice selector fields.
3. WHEN `mix_with_script` or `broll_voiceover` mode is selected, THE /mix page SHALL display the TTS text input area and voice selector fields.
4. WHEN `broll_voiceover` mode is selected, THE /mix page Step 1 (A-roll selection) SHALL be skipped, and the flow SHALL start at Step 2 (B-roll selection).
5. THE /mix page SHALL include the selected `mixing_mode` value in the `MixCreateRequest` payload when submitting the task.

### Requirement 10: AI Director Processing Status

**User Story:** As a video editor, I want to see the AI Director's progress during processing, so that I understand what the system is doing and how long it might take.

#### Acceptance Criteria

1. WHILE a mixing task is in `processing` status with AI Director enabled, THE MixStatusResponse SHALL include a `progress` field indicating the current pipeline stage: "正在抽取关键帧…" (extracting frames), "AI 编导分析中…" (VLM analyzing), "正在执行智能剪辑…" (executing timeline), or "AI 编导不可用，使用传统混剪…" (falling back).
2. THE /mix page Step 4 (generate) SHALL display the progress stage text to the user during processing.
3. WHEN the task completes, THE MixStatusResponse SHALL indicate whether AI Director mode or fallback mode was used for the final output.

### Requirement 11: Timeline JSON Schema Validation

**User Story:** As a developer, I want the Timeline JSON to be strictly validated, so that malformed VLM responses do not cause runtime errors in the Timeline Executor.

#### Acceptance Criteria

1. THE VLM_Service SHALL validate that the parsed Timeline is a non-empty JSON array.
2. THE VLM_Service SHALL validate that each Timeline entry contains exactly the fields `type`, `start`, `end`, and `reason`, all with correct data types (string, number, number, string).
3. THE VLM_Service SHALL validate that `type` is either "a_roll" or "b_roll" for each entry.
4. THE VLM_Service SHALL validate that `start` is greater than or equal to zero and `end` is strictly greater than `start` for each entry.
5. THE VLM_Service SHALL validate that Timeline entries are ordered by `start` time and that no two entries have overlapping time ranges.
6. IF any validation check fails, THEN THE VLM_Service SHALL log the specific validation error and return a failure indicator.

### Requirement 12: Configuration Management

**User Story:** As a system administrator, I want VLM and AI TTS settings managed through config.yaml, so that I can adjust API endpoints and parameters without code changes.

#### Acceptance Criteria

1. THE ExternalConfig service SHALL read VLM configuration from the `vlm` section of config.yaml, including `api_url`, `api_key`, `model`, `frame_interval`, and `max_frames`.
2. THE ExternalConfig service SHALL read AI TTS configuration from the `ai_tts` section of config.yaml, including `api_url`, `api_key`, and `fallback_to_edge_tts`.
3. WHEN config.yaml is modified, THE ExternalConfig service SHALL pick up the new values on the next request without requiring a server restart (existing hot-reload behavior).
4. IF `vlm.api_key` is empty, THEN THE AI_Director_Service SHALL skip VLM analysis and use the Blind_Cut_Engine directly.
