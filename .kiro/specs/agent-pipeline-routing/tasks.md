# Implementation Plan: Agent Pipeline Routing

## Overview

Replace the hardcoded if/else routing logic in `AIDirectorService.run_auto_pipeline()` with an LLM-powered Agent Router. The implementation creates a new `agent_router.py` module with data models, pipeline registry, LLM prompt construction, response parsing, and validation. Then integrates the router into `ai_director_service.py` with fallback to existing logic on any failure. All changes are in Python, following patterns established by `IntentParsingService`.

## Tasks

- [x] 1. Create data models and pipeline registry in `app/services/agent_router.py`
  - [x] 1.1 Create `app/services/agent_router.py` with `PipelineInfo`, `AssetSummary`, and `RoutingDecision` dataclasses
    - Define `PipelineInfo` with fields: `identifier`, `name_zh`, `description_zh`, `expected_roles`, `trigger_keywords`
    - Define `AssetSummary` with fields: `asset_id`, `original_filename`, `role`, `has_speech`, `description`, `duration`
    - Define `RoutingDecision` with fields: `pipeline`, `asset_roles`, `parameters`, `raw_response`
    - Add type hints and docstrings following the project's style
    - _Requirements: 1.1, 1.2_

  - [x] 1.2 Define `PIPELINE_REGISTRY` as a module-level constant list of `PipelineInfo`
    - Include all four pipelines: `text_driven`, `vision_montage`, `hybrid`, `multi_asset_montage`
    - Use Chinese descriptions and trigger keywords as specified in the design
    - Ensure each entry has non-empty identifier, name_zh, description_zh, expected_roles, and trigger_keywords
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 1.3 Write property test for pipeline registry structure invariant
    - **Property 1: Pipeline Registry structure invariant**
    - Verify each entry has non-empty identifier, name_zh, description_zh, expected_roles, trigger_keywords
    - Verify all identifiers are unique across the registry
    - **Validates: Requirements 1.1**

- [x] 2. Implement asset summary construction
  - [x] 2.1 Implement `AgentRouter.build_asset_summaries()` method
    - Accept `asset_ids`, `clip_paths`, `clip_original_filenames`, and `analysis_map` parameters
    - For each asset with completed analysis: extract role, has_speech, description (truncated to 100 chars), duration
    - Include `original_filename` from `clip_original_filenames` or fall back to basename of clip path
    - For assets with missing/incomplete analysis: set role="unknown", has_speech=None, preserve filename and duration
    - _Requirements: 2.1, 2.2, 2.3_

  - [ ]* 2.2 Write property test for asset summary construction
    - **Property 2: Asset summary construction preserves required fields and truncates description**
    - Generate random analysis dicts with varying-length descriptions using Hypothesis `st.text()`
    - Verify asset_id matches input, original_filename is present, role is non-empty, has_speech is boolean, description ≤ 100 chars, duration ≥ 0
    - **Validates: Requirements 2.1, 2.2**

  - [ ]* 2.3 Write property test for missing analysis handling
    - **Property 3: Missing analysis produces unknown/null summary**
    - Generate random asset metadata with None analysis
    - Verify role="unknown", has_speech=None, original_filename and duration preserved
    - **Validates: Requirements 2.3**

- [x] 3. Implement LLM prompt construction and API call
  - [x] 3.1 Implement `AgentRouter._build_system_prompt()` method
    - Include all pipeline descriptions from `PIPELINE_REGISTRY` with identifiers and Chinese descriptions
    - Include expected JSON output schema (pipeline, asset_roles, parameters)
    - Include routing priority rules: user intent over asset labels, montage keyword handling
    - Include instruction to map filename references to asset IDs
    - _Requirements: 3.1, 3.3, 3.4, 3.5_

  - [x] 3.2 Implement `AgentRouter._build_user_prompt()` method
    - Include the `director_prompt` text
    - Include formatted list of `AssetSummary` objects with all fields
    - Include total asset count
    - _Requirements: 3.2_

  - [x] 3.3 Implement `AgentRouter._get_llm_config()` and `AgentRouter._call_llm()` methods
    - Follow the same LLM config resolution pattern as `IntentParsingService._get_llm_config()`: text_llm → default provider → VLM fallback
    - Use `httpx.Client` with timeout=15s, temperature=0.1, max_tokens=1024
    - Return raw response content string on success, None on any failure
    - Catch and log `httpx.TimeoutException`, network errors, HTTP errors
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.6_

  - [ ]* 3.4 Write property tests for prompt construction
    - **Property 4: System prompt contains all registry pipeline descriptions**
    - Verify `_build_system_prompt()` output contains each pipeline's identifier and description_zh
    - **Validates: Requirements 3.1**
    - **Property 5: User prompt contains director_prompt and all asset summaries**
    - Verify `_build_user_prompt()` output contains director_prompt text, each summary's asset_id, and total asset count
    - **Validates: Requirements 3.2**

- [x] 4. Checkpoint — Verify data models, registry, summaries, and prompts
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement response parsing and validation
  - [x] 5.1 Implement `AgentRouter._parse_response()` method
    - Strip markdown code fences (```json ... ```) before parsing
    - Strip qwen3-style thinking tags (`<think>...</think>`) before parsing
    - Try direct `json.loads` first, then fall back to extracting first `{...}` block
    - Handle multiple JSON objects by extracting only the first valid one
    - Return parsed dict or None on failure
    - Follow the same pattern as `IntentParsingService._extract_json()`
    - _Requirements: 4.5, 4.7, 10.1, 10.2, 10.3_

  - [x] 5.2 Implement `AgentRouter._validate_decision()` method
    - Validate `pipeline` field is a valid identifier from `PIPELINE_REGISTRY`
    - Validate all asset_ids in `asset_roles` correspond to provided asset summaries
    - Validate assigned roles are compatible with the selected pipeline's expected roles
    - For `text_driven` or `hybrid` pipelines, validate at least one asset has "presenter" role
    - Handle `parameters` field as both string and object (parse string to dict if needed)
    - If `asset_roles` is missing but `pipeline` is valid, infer roles from analysis data via `_infer_asset_roles()`
    - Log specific validation failures and return None on any validation error
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 10.4, 10.5_

  - [x] 5.3 Implement `AgentRouter._infer_asset_roles()` method
    - Infer asset roles from analysis data based on pipeline type and asset characteristics
    - For text_driven/hybrid: assign presenter to speech-containing assets, broll to others
    - For vision_montage/multi_asset_montage: assign all as montage_clip
    - _Requirements: 10.5_

  - [ ]* 5.4 Write property tests for response parsing
    - **Property 6: Response parsing round-trip through LLM output wrappers**
    - Generate valid JSON wrapped in markdown fences and/or thinking tags, verify extraction matches original
    - **Validates: Requirements 4.5, 10.1, 10.2**
    - **Property 7: Invalid or non-JSON response returns None**
    - Generate random non-JSON strings, verify _parse_response() returns None
    - **Validates: Requirements 4.7**
    - **Property 8: First JSON object extraction from multi-object response**
    - Generate two valid JSON objects with separator text, verify first is extracted
    - **Validates: Requirements 10.3**

  - [ ]* 5.5 Write property tests for validation logic
    - **Property 9: Validation rejects invalid routing decisions**
    - Generate dicts with invalid pipeline, unknown asset_ids, or missing presenter for text_driven/hybrid
    - Verify _validate_decision() returns None in each case
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
    - **Property 12: Parameters field handles both string and object values**
    - Generate valid routing JSON with parameters as string or dict, verify RoutingDecision.parameters is always a dict
    - **Validates: Requirements 10.4**
    - **Property 13: Missing asset_roles triggers role inference from analysis**
    - Generate valid pipeline JSON without asset_roles, verify roles are inferred from analysis data
    - **Validates: Requirements 10.5**

- [x] 6. Implement the main `AgentRouter.route()` method
  - [x] 6.1 Implement `AgentRouter.route()` orchestration method
    - Build system and user prompts
    - Call LLM via `_call_llm()`
    - Parse response via `_parse_response()`
    - Validate via `_validate_decision()`
    - Measure and return latency for logging
    - Return `RoutingDecision` on success, `None` on any failure
    - Wrap entire method in try/except to ensure no exceptions propagate
    - _Requirements: 4.1, 4.5, 4.6, 4.7, 5.1, 5.2, 5.3, 5.4, 5.5_

- [x] 7. Checkpoint — Verify AgentRouter module is complete and all unit/property tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Integrate Agent Router into `AIDirectorService.run_auto_pipeline()`
  - [x] 8.1 Add Agent Router call before existing if/else block in `run_auto_pipeline()`
    - Import `AgentRouter` in `ai_director_service.py`
    - After `_ensure_asset_analysis_ready()`, check if `director_prompt` is non-empty and `analysis_map` has data
    - If conditions met: instantiate `AgentRouter`, call `build_asset_summaries()`, then `route()`
    - If `director_prompt` is empty/whitespace or no analysis data: skip Agent Router entirely
    - _Requirements: 6.2, 6.3_

  - [x] 8.2 Implement pipeline dispatch based on `RoutingDecision`
    - If valid `RoutingDecision` returned: dispatch to the correct pipeline method based on `pipeline` field
    - `text_driven` → `_run_text_driven()` with presenter asset from `asset_roles`
    - `vision_montage` or `multi_asset_montage` → `run_montage_pipeline()` with all assets
    - `hybrid` → `_run_hybrid_pipeline()` with presenter and broll separated per `asset_roles`
    - Pass `parameters` from RoutingDecision to pipeline methods where applicable
    - If `RoutingDecision` is None: fall through to existing if/else routing unchanged
    - Do NOT modify signatures or internals of `_run_text_driven()`, `run_montage_pipeline()`, or `_run_hybrid_pipeline()`
    - _Requirements: 6.1, 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 8.3 Add observability logging and mix_params storage
    - Log full RoutingDecision JSON at INFO level when agent router succeeds
    - Log comparison message when agent router selects a different pipeline than fallback would have
    - Log LLM response latency in milliseconds
    - Log warning with reason when fallback routing is activated
    - Store `routing_method` ("agent_router" or "fallback") and `routing_decision` in task's `mix_params`
    - _Requirements: 6.4, 9.1, 9.2, 9.3, 9.4_

  - [ ]* 8.4 Write property test for whitespace-only director_prompt skipping
    - **Property 10: Whitespace-only director_prompt skips the Agent Router**
    - Generate whitespace-only strings, verify AgentRouter.route() is not called
    - **Validates: Requirements 6.2**

  - [ ]* 8.5 Write property test for pipeline dispatch correctness
    - **Property 11: Correct pipeline method dispatch based on RoutingDecision**
    - Generate valid RoutingDecisions with mocked pipeline methods, verify correct method is called
    - **Validates: Requirements 8.1, 8.2, 8.3**

- [x] 9. Update existing tests and add integration tests
  - [x] 9.1 Update `tests/test_auto_pipeline_routing.py` for new routing path
    - Ensure existing tests still pass with the Agent Router integration (mock AgentRouter to return None so fallback logic runs)
    - Add tests verifying Agent Router is called when director_prompt is present and analysis is available
    - Add tests verifying Agent Router is skipped when director_prompt is empty
    - _Requirements: 6.1, 6.2, 6.3_

  - [ ]* 9.2 Write integration tests with mocked LLM responses
    - Test montage intent override: director_prompt="混剪这些素材" with presenter asset → montage pipeline
    - Test presenter trim intent: director_prompt="裁剪口播" → text_driven pipeline
    - Test filename reference mapping: director_prompt="1.mov放开头" → correct asset mapping
    - Test audio source intent: director_prompt="用口播素材的音频配上产品画面" → hybrid pipeline
    - Test ambiguous prompt: no clear intent → falls back to asset-based routing
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 10. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document using Hypothesis
- Unit tests validate specific examples and edge cases
- The Agent Router follows the same LLM config and JSON extraction patterns as `IntentParsingService`
- All error paths return None, triggering the existing fallback routing — zero-risk deployment
