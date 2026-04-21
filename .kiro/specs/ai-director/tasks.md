# Tasks

## Task 1: Configuration Management (ExternalConfig + config.yaml)

- [x] 1.1 Add `get_vlm_config()` method to `ExternalConfig` in `app/services/external_config.py` that returns a dict with `api_url`, `api_key`, `model`, `frame_interval`, and `max_frames` from the `vlm` section of config.yaml, with sensible defaults (empty string for api_url/api_key, "gpt-5.4" for model, 2 for frame_interval, 30 for max_frames).
- [x] 1.2 Add `get_ai_tts_config()` method to `ExternalConfig` in `app/services/external_config.py` that returns a dict with `api_url`, `api_key`, and `fallback_to_edge_tts` from the `ai_tts` section of config.yaml, with defaults (empty strings, True for fallback).
- [x] 1.3 Verify that the existing `vlm` and `ai_tts` sections in `config.yaml` match the expected schema (already present from prior setup).

## Task 2: Schema Changes (MixCreateRequest)

- [x] 2.1 Add `mixing_mode` field to `MixCreateRequest` in `app/schemas/mix.py` with type `str`, default `"pure_mix"`, and regex pattern `^(pure_mix|mix_with_script|broll_voiceover)$`.
- [x] 2.2 Add optional `ai_director_used` field (bool, default None) to `MixStatusResponse` in `app/schemas/mix.py` to indicate whether AI Director or fallback mode was used.

## Task 3: VLM Service — Frame Extraction

- [x] 3.1 Create `app/services/vlm_service.py` with `VLMService` class and `extract_frames(video_path, frame_interval, max_frames, max_width)` method that uses FFmpeg subprocess to extract frames at the configured interval, resize to max_width preserving aspect ratio, encode as base64 JPEG, and return a list of `(timestamp, base64_string)` tuples. Raise `FileNotFoundError` for missing video files, `RuntimeError` for missing FFmpeg or zero-duration videos. Cap at `max_frames`.
- [ ] 3.2 Write property-based test (Hypothesis) for frame extraction count and timestamp invariants: for any (duration, frame_interval, max_frames), verify count = min(floor(duration/interval), max_frames), timestamps are ascending multiples of interval, and all timestamps < duration. Use a small generated test video via FFmpeg.
  - [ ] 🧪 PBT: Property 1 — Frame extraction count and timestamp invariants

## Task 4: VLM Service — Timeline Generation and Validation

- [x] 4.1 Add `generate_timeline(frames, transcript, b_roll_descriptions, a_roll_duration)` method to `VLMService` that constructs a multimodal prompt with base64 frames and text context, sends it to the VLM API (configured via `get_vlm_config()`), parses the JSON response, validates it, and returns the timeline list or `None` on failure. Include Bearer token auth, 60s timeout, one retry on timeout.
- [x] 4.2 Add `validate_timeline(timeline, a_roll_duration)` method to `VLMService` that checks: non-empty array, each entry has `type` (str, "a_roll"/"b_roll"), `start` (number >= 0), `end` (number > start), `reason` (str); entries sorted by `start`; no overlapping intervals. Return `True`/`False` and log specific validation errors.
- [ ] 4.3 Write property-based test (Hypothesis) for timeline validation: generate random lists of timeline entry dicts (both valid and invalid), verify `validate_timeline` returns True iff all constraints are satisfied.
  - [ ] 🧪 PBT: Property 4 — Timeline validation accepts valid and rejects invalid
- [ ] 4.4 Write property-based test (Hypothesis) for timeline JSON parsing round-trip: generate valid timeline arrays, serialize to JSON string, parse back through the VLM service parser, verify equivalence.
  - [ ] 🧪 PBT: Property 3 — Timeline JSON parsing round-trip

## Task 5: Timeline Executor (mixing_engine.py)

- [x] 5.1 Add `execute_timeline(timeline, a_roll_paths, b_roll_paths, audio_file, output_path, video_aspect, video_transition, threads)` function to `mixing_engine.py` that iterates over timeline entries, extracts A-roll segments via `subclipped(start, end)`, selects B-roll via `itertools.cycle` round-robin, strips audio from all segments, resizes to target resolution with black padding, applies transitions to B-roll segments, concatenates via FFmpeg concat demuxer, and applies the audio track. Clamp timeline entries that exceed A-roll duration with a logged warning.
- [ ] 5.2 Write property-based test (Hypothesis) for B-roll round-robin cycling: for any pool size N >= 1 and K b_roll entries, verify entry i uses clip at index i % N.
  - [ ] 🧪 PBT: Property 5 — B-roll round-robin cycling

## Task 6: AI TTS Service

- [x] 6.1 Create `app/services/ai_tts_service.py` with `AITTSService` class and `synthesize(text, task_id, voice)` method that: reads AI TTS config via `get_ai_tts_config()`, tries the external AI TTS API if `api_url` and `api_key` are configured, falls back to Edge-TTS if the API fails and `fallback_to_edge_tts` is true, raises `RuntimeError` if both fail. Saves audio to `storage/tasks/{task_id}/tts_audio.mp3`. Returns `(audio_path, duration_seconds)`. Gets duration via ffprobe.
- [ ] 6.2 Write unit tests for AI TTS fallback chain: mock AI TTS API success, mock AI TTS failure with Edge-TTS fallback, mock both failing with fallback disabled.

## Task 7: AI Director Service — Orchestration

- [x] 7.1 Create `app/services/ai_director_service.py` with `AIDirectorService` class and `run_pipeline(a_roll_paths, b_roll_paths, transcript, aspect_ratio, transition, audio_file, progress_callback)` method that: (1) calls `progress_callback("extracting_frames")` and extracts frames via `VLMService.extract_frames()`, (2) calls `progress_callback("analyzing_with_vlm")` and generates timeline via `VLMService.generate_timeline()`, (3) if timeline is valid, calls `progress_callback("executing_timeline")` and runs `execute_timeline()`, (4) if timeline is None, calls `progress_callback("falling_back_to_blind_cut")` and runs `combine_videos()` as fallback. Returns the output video path.
- [x] 7.2 Add `_transcribe_audio(audio_path)` private method to `AIDirectorService` that uses faster-whisper to transcribe audio, returning the transcript text. Falls back to empty string if Whisper is unavailable (catches ImportError and model load errors, logs warning).
- [ ] 7.3 Write unit tests for AI Director orchestration: mock VLM success path (verify all 3 steps called), mock VLM failure path (verify fallback to combine_videos), verify progress_callback is called with correct stage strings.

## Task 8: Subtitle Generation Enhancements

- [x] 8.1 Add `generate_subtitles_from_script(script_text, audio_duration, ass_path, video_w, video_h)` function to `mixing_engine.py` that splits script text into segments aligned with the audio duration and writes an ASS subtitle file. Each segment should be roughly equal duration, splitting on sentence boundaries (。！？!? or newlines).
- [x] 8.2 Add `burn_subtitles(video_path, ass_path, output_path)` function to `mixing_engine.py` that uses FFmpeg to burn the ASS subtitle file into the video using the `ass` filter.
- [ ] 8.3 Write property-based test (Hypothesis) for script-to-subtitle coverage: for any non-empty script text and positive duration, verify subtitle segments cover the full duration and contain all words from the script.
  - [ ] 🧪 PBT: Property 6 — Script-to-subtitle coverage

## Task 9: MixingService Mode Routing

- [x] 9.1 Add mode-specific validation to `MixingService.create_mix_task()`: reject `pure_mix` with empty `a_roll_asset_ids`, reject `broll_voiceover` with empty `b_roll_asset_ids`, reject `broll_voiceover` with empty/None `tts_text`. Raise `ValidationError` with descriptive messages.
- [x] 9.2 Refactor `MixingService.execute_mix()` to route based on `mixing_mode`: (a) `pure_mix` — extract A-roll audio, call `AIDirectorService.run_pipeline()`, generate Whisper subtitles via `_generate_subtitles()`, burn subtitles; (b) `mix_with_script` — synthesize TTS via `AITTSService`, call `AIDirectorService.run_pipeline()` with script as transcript, generate script-based subtitles, burn subtitles; (c) `broll_voiceover` — synthesize TTS via `AITTSService`, call `combine_videos()` with B-roll only, generate script-based subtitles, burn subtitles. Store `mixing_mode` in `mix_params` JSON.
- [x] 9.3 Update task progress updates in `execute_mix()` to use the new stage-specific progress strings ("正在抽取关键帧…", "AI 编导分析中…", etc.) and set `ai_director_used` on task completion.
- [ ] 9.4 Write property-based test (Hypothesis) for mixing mode input validation: generate random MixCreateRequest-like dicts with invalid mode/asset combinations, verify rejection.
  - [ ] 🧪 PBT: Property 7 — Mixing mode input validation
- [ ] 9.5 Write unit tests for mode routing: verify `pure_mix` calls AIDirectorService, `broll_voiceover` calls combine_videos directly, `mix_with_script` calls both AITTSService and AIDirectorService.

## Task 10: Frontend — Mode Selector and Conditional UI

- [x] 10.1 Add a mode selector UI component in Step 3 (params configuration) of `mix.html` with three radio-button options: "纯混剪" (pure_mix), "混剪 + AI 脚本" (mix_with_script), "纯素材 + AI 口播" (broll_voiceover). Default to `pure_mix`. Store selection in Alpine.js `mixingMode` data property.
- [x] 10.2 Add conditional visibility: hide TTS text input and voice selector when `pure_mix` is selected; show them when `mix_with_script` or `broll_voiceover` is selected.
- [x] 10.3 Add conditional step flow: when `broll_voiceover` is selected, skip Step 1 (A-roll selection) and start at Step 2 (B-roll selection). Adjust step numbering and progress indicator accordingly.
- [x] 10.4 Include `mixing_mode` in the `MixCreateRequest` payload sent by the `submitMix()` function.
- [x] 10.5 Update the Step 4 (generate/status) section to display the AI Director progress stage text from the `progress` field in `MixStatusResponse`, and show whether AI Director or fallback mode was used upon completion.

## Task 11: Integration Testing

- [ ] 11.1 Write integration test for full `pure_mix` pipeline with mocked VLM returning a valid timeline: verify output video is produced, `ai_director_used` is true.
- [ ] 11.2 Write integration test for graceful degradation: mock VLM failure, verify blind-cut fallback produces output video, `ai_director_used` is false, warning logged.
- [ ] 11.3 Write integration test for `broll_voiceover` pipeline: verify TTS is synthesized, B-roll is arranged, subtitles are generated from script text.
