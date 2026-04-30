# Requirements Document

## Introduction

The Agent Pipeline Routing feature replaces the hardcoded if/else routing logic in `AIDirectorService.run_auto_pipeline()` with an LLM-based intelligent routing step. The current implementation routes pipelines solely based on asset analysis labels (`role=presenter` + `has_speech=True`), completely ignoring the user's natural language intent expressed in `director_prompt`. This causes incorrect pipeline selection when user intent conflicts with asset characteristics — for example, a user requesting "混剪这些产品素材" (montage these product clips) is forced into the hybrid pipeline simply because one asset happens to have a presenter label.

The new Agent Router receives the user's `director_prompt`, asset analysis summaries, and a registry of available pipelines with their capabilities. It uses an LLM call to determine which pipeline best matches the user's intent, assigns assets to pipeline-specific roles, and extracts pipeline-specific parameters. The system falls back to the existing if/else logic when the LLM call fails, ensuring zero-downtime degradation.

## Glossary

- **Agent_Router**: The new LLM-powered routing component within `AIDirectorService` that analyzes user intent and asset characteristics to select the optimal pipeline and assign asset roles.
- **Routing_Decision**: The structured output from the Agent_Router containing the selected pipeline identifier, asset-to-role assignments, and pipeline-specific parameters.
- **Pipeline_Registry**: A declarative data structure describing available pipelines, their capabilities, expected asset roles, and when each pipeline is most appropriate.
- **Director_Prompt**: The user's natural language instruction (`director_prompt` field) describing what they want the system to do with their assets.
- **Asset_Summary**: A condensed representation of an asset's analysis results including role, has_speech, description, duration, and original_filename — provided to the Agent_Router as context.
- **Pipeline_Identifier**: A string enum value identifying a pipeline: `text_driven`, `vision_montage`, `hybrid`, or `multi_asset_montage`.
- **Asset_Role_Assignment**: A mapping from asset IDs to their assigned roles within the selected pipeline (e.g., presenter, broll, montage_clip).
- **Fallback_Router**: The existing if/else routing logic preserved as a fallback mechanism when the Agent_Router LLM call fails.
- **AIDirectorService**: The existing orchestration service in `ai_director_service.py` that coordinates pipeline execution.
- **IntentParsingService**: The existing service that extracts structural parameters (video_count, strip_audio, etc.) from the director_prompt — distinct from pipeline routing.

## Requirements

### Requirement 1: Pipeline Registry Definition

**User Story:** As a developer, I want a declarative registry of available pipelines and their capabilities, so that the LLM router has structured context about what each pipeline can do and when to use it.

#### Acceptance Criteria

1. THE Pipeline_Registry SHALL define each available pipeline with a unique Pipeline_Identifier, a human-readable description in Chinese, a list of expected asset roles, and a list of trigger keywords or scenarios.
2. THE Pipeline_Registry SHALL include the following pipelines: `text_driven` (口播裁剪 — for trimming presenter/speech content), `vision_montage` (视觉混剪 — for montage of visual clips without speech focus), `hybrid` (混合剪辑 — for combining presenter speech with B-roll inserts), and `multi_asset_montage` (多素材混剪 — for equal-weight montage of multiple clips).
3. THE Pipeline_Registry SHALL be defined as a Python data structure within the codebase, not requiring external configuration files.
4. WHEN a new pipeline is added to the system, THE Pipeline_Registry SHALL be the single location that needs updating to make the Agent_Router aware of the new pipeline.

### Requirement 2: Asset Summary Construction

**User Story:** As a developer, I want asset analysis data formatted into concise summaries for the LLM, so that the Agent_Router can make informed routing decisions without exceeding token limits.

#### Acceptance Criteria

1. WHEN asset analysis data is available for routing, THE Agent_Router SHALL construct an Asset_Summary for each asset containing: asset_id, original_filename, role (from analysis), has_speech (boolean), description (truncated to 100 characters), and duration in seconds.
2. THE Agent_Router SHALL include the original_filename in each Asset_Summary so that the LLM can match user references to specific files (e.g., "1.mov放开头").
3. IF asset analysis is not available for a given asset, THEN THE Agent_Router SHALL construct a minimal Asset_Summary with the original_filename and duration only, marking role as "unknown" and has_speech as null.

### Requirement 3: LLM Router Prompt Construction

**User Story:** As a developer, I want a well-structured prompt for the routing LLM call, so that the model reliably selects the correct pipeline based on user intent and asset characteristics.

#### Acceptance Criteria

1. THE Agent_Router SHALL construct a system prompt that includes the Pipeline_Registry descriptions, expected JSON output schema, and routing priority rules.
2. THE Agent_Router SHALL construct a user prompt that includes the Director_Prompt text, the list of Asset_Summaries, and the number of assets.
3. THE Agent_Router prompt SHALL instruct the LLM to prioritize user intent over asset labels — specifically, when the user explicitly requests a montage operation (混剪), the router SHALL prefer `vision_montage` or `multi_asset_montage` even if presenter assets are present.
4. THE Agent_Router prompt SHALL instruct the LLM to map filename references in the Director_Prompt to specific asset IDs using the original_filename field from Asset_Summaries.
5. THE Agent_Router prompt SHALL instruct the LLM to output a JSON object with fields: `pipeline` (Pipeline_Identifier string), `asset_roles` (object mapping asset_id to role string), and `parameters` (object with pipeline-specific key-value pairs).

### Requirement 4: LLM Router Execution

**User Story:** As a developer, I want the routing LLM call to execute reliably with appropriate timeout and error handling, so that pipeline routing does not become a bottleneck or single point of failure.

#### Acceptance Criteria

1. WHEN a Director_Prompt is provided and asset analysis data is available, THE Agent_Router SHALL make an LLM API call to the default LLM provider configured in ExternalConfig (luxee.ai / gpt-5.4).
2. THE Agent_Router SHALL set a request timeout of 15 seconds for the routing LLM call.
3. THE Agent_Router SHALL use a temperature of 0.1 for the routing LLM call to ensure deterministic routing decisions.
4. THE Agent_Router SHALL set max_tokens to 1024 for the routing response.
5. IF the LLM API call succeeds, THE Agent_Router SHALL parse the response as JSON and validate the Routing_Decision structure.
6. IF the LLM API call fails due to timeout, network error, or HTTP error status, THEN THE Agent_Router SHALL log the failure reason and return a null Routing_Decision, triggering fallback routing.
7. IF the LLM response is not valid JSON or does not conform to the expected Routing_Decision schema, THEN THE Agent_Router SHALL log the parsing error and raw response, and return a null Routing_Decision.

### Requirement 5: Routing Decision Validation

**User Story:** As a developer, I want the routing decision validated before execution, so that invalid LLM outputs do not cause pipeline failures.

#### Acceptance Criteria

1. WHEN a Routing_Decision is received from the LLM, THE Agent_Router SHALL validate that the `pipeline` field contains a valid Pipeline_Identifier from the Pipeline_Registry.
2. THE Agent_Router SHALL validate that all asset_ids referenced in `asset_roles` correspond to actual assets provided to the routing call.
3. THE Agent_Router SHALL validate that the assigned roles in `asset_roles` are compatible with the selected pipeline's expected roles as defined in the Pipeline_Registry.
4. IF the `pipeline` field is `text_driven` or `hybrid`, THE Agent_Router SHALL validate that at least one asset is assigned the "presenter" role.
5. IF validation fails on any check, THEN THE Agent_Router SHALL log the specific validation failure and return a null Routing_Decision, triggering fallback routing.

### Requirement 6: Fallback to Existing Routing Logic

**User Story:** As a system operator, I want the system to fall back to the existing if/else routing when the LLM router fails, so that video production continues without interruption.

#### Acceptance Criteria

1. IF the Agent_Router returns a null Routing_Decision (due to LLM failure, parse error, or validation failure), THEN THE AIDirectorService SHALL execute the existing if/else routing logic based on asset analysis labels.
2. IF the Director_Prompt is empty or contains only whitespace, THEN THE AIDirectorService SHALL skip the Agent_Router entirely and use the existing if/else routing logic directly.
3. IF no asset analysis data is available for any asset, THEN THE AIDirectorService SHALL skip the Agent_Router and default to the vision_montage pipeline via the existing fallback path.
4. WHEN fallback routing is activated, THE AIDirectorService SHALL log a warning message indicating the reason for fallback and the pipeline selected by the fallback logic.

### Requirement 7: Intent-Aware Routing Rules

**User Story:** As a video editor, I want the system to respect my natural language instructions when choosing a pipeline, so that saying "混剪" results in a montage even when my assets include presenter footage.

#### Acceptance Criteria

1. WHEN the Director_Prompt contains montage-related terms ("混剪", "剪辑", "拼接", "montage"), THE Agent_Router SHALL prefer `vision_montage` or `multi_asset_montage` over `text_driven` or `hybrid`, regardless of asset speech labels.
2. WHEN the Director_Prompt contains presenter-trimming terms ("裁剪口播", "剪口播", "trim presenter", "cut speech"), THE Agent_Router SHALL prefer `text_driven` pipeline and assign the speech-containing asset as presenter.
3. WHEN the Director_Prompt references specific assets by filename (e.g., "1.mov放开头", "用产品.mp4做封面"), THE Agent_Router SHALL map those filename references to the corresponding asset IDs and reflect the user's placement intent in the `parameters` field.
4. WHEN the Director_Prompt contains audio-source instructions (e.g., "用口播素材的音频配上产品画面"), THE Agent_Router SHALL select `hybrid` pipeline and assign the speech asset as audio source and the product assets as visual B-roll.
5. WHEN the Director_Prompt does not contain clear pipeline preference signals, THE Agent_Router SHALL fall back to asset-characteristic-based routing (equivalent to the current if/else logic).

### Requirement 8: Routing Decision Integration with Pipeline Execution

**User Story:** As a developer, I want the routing decision to be seamlessly consumed by the existing pipeline execution methods, so that the new router can be integrated without rewriting pipeline internals.

#### Acceptance Criteria

1. WHEN the Agent_Router produces a valid Routing_Decision with `pipeline` set to `text_driven`, THE AIDirectorService SHALL call `_run_text_driven()` with the asset assigned the "presenter" role as the primary clip.
2. WHEN the Agent_Router produces a valid Routing_Decision with `pipeline` set to `vision_montage` or `multi_asset_montage`, THE AIDirectorService SHALL call `run_montage_pipeline()` with all assets in their assigned order.
3. WHEN the Agent_Router produces a valid Routing_Decision with `pipeline` set to `hybrid`, THE AIDirectorService SHALL call `_run_hybrid_pipeline()` with the presenter asset and B-roll assets separated according to `asset_roles`.
4. WHEN the Routing_Decision contains a `parameters` field, THE AIDirectorService SHALL pass relevant parameters to the selected pipeline method (e.g., asset ordering hints, audio source preferences).
5. THE Agent_Router integration SHALL not modify the signatures or internal logic of `_run_text_driven()`, `run_montage_pipeline()`, or `_run_hybrid_pipeline()`.

### Requirement 9: Observability and Logging

**User Story:** As a developer, I want comprehensive logging of routing decisions, so that I can debug pipeline selection issues and monitor LLM router accuracy.

#### Acceptance Criteria

1. WHEN the Agent_Router makes a routing decision, THE AIDirectorService SHALL log the full Routing_Decision JSON (pipeline, asset_roles, parameters) at INFO level.
2. WHEN the Agent_Router selects a different pipeline than what the fallback logic would have chosen, THE AIDirectorService SHALL log a comparison message noting the divergence (e.g., "Agent router chose vision_montage; fallback would have chosen hybrid").
3. WHEN the Agent_Router LLM call completes, THE AIDirectorService SHALL log the response latency in milliseconds.
4. THE AIDirectorService SHALL store the routing method used ("agent_router" or "fallback") and the selected pipeline in the task's `mix_params` JSON for post-hoc analysis.

### Requirement 10: Router Response Parsing

**User Story:** As a developer, I want robust JSON parsing of the LLM router response, so that the system handles various LLM output formats gracefully.

#### Acceptance Criteria

1. THE Agent_Router SHALL strip markdown code fences (```json ... ```) from the LLM response before JSON parsing.
2. THE Agent_Router SHALL strip qwen3-style thinking tags (`<think>...</think>`) from the LLM response before JSON parsing.
3. IF the LLM response contains multiple JSON objects, THE Agent_Router SHALL extract and use only the first valid JSON object.
4. THE Agent_Router SHALL handle both string and object values for the `parameters` field gracefully.
5. IF the `asset_roles` field is missing but the `pipeline` field is valid, THE Agent_Router SHALL infer asset roles from asset analysis data using the existing if/else logic for role assignment.
