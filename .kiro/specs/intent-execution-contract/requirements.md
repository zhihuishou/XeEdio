# Requirements: Intent–Execution Contract & Mix Integrity

## Introduction

XeEdio already parses natural language into `ParsedIntent` and merges values into `mix_params`, and runs AI Director pipelines (text-driven, vision/montage, hybrid). Production tasks have shown a **gap between structured intent and downstream execution**: e.g. `video_count` parsed as 3 while the vision path emits a single `output-1.mp4`; `transition` stays `none` while the user asks for smooth cuts in prose; many assets show `analysis not_found`, weakening routing and cached summaries without surfacing that to the user.

This specification defines an **intent–execution contract**: structured parameters SHALL be consumed or explicitly rejected on every pipeline branch, editing-relevant semantics SHALL be representable and passed to planners (LLM/VLM) in a reproducible way, and asset analysis readiness SHALL be visible and recoverable before or during mix execution.

## Glossary

- **Mix_Contract**: The authoritative subset of `mix_params` (or embedded JSON) that the mixing pipeline MUST honor or fail loudly, including at minimum `video_count`, `max_output_duration`, `aspect_ratio`, `transition`, and optional editing semantics fields.
- **Execution_Report**: Post-run metadata stored with the task (or log): outputs produced, per-output duration, warnings, and validation results.
- **L1_Parse**: `IntentParsingService` (and future expert agents) producing structured fields.
- **L2_Gate**: Pre-execution checks inside `MixingService` / `AIDirectorService` before expensive VLM/FFmpeg work.
- **L3_Validate**: Post-execution checks comparing disk outputs and timeline properties to `Mix_Contract`.
- **Preflight**: An assessment of selected assets’ `asset_analysis` status before or at task creation.
- **Montage_Path**: Vision-driven pipeline (`run_montage_pipeline` and related VLM calls).
- **Text_Path**: Text-driven pipeline using transcript and word timestamps.

## Requirements

### Requirement 1: Mix Contract Snapshot

**User Story:** As an operator, I want the task to record exactly which structured parameters were agreed for execution, so that debugging and audits match user intent.

#### Acceptance Criteria

1. WHEN `MixingService` creates or updates a mix task, THE system SHALL persist a **Mix_Contract** snapshot inside `mix_params` (or a dedicated column) including `contract_version` and all fields the executor is expected to honor.
2. THE Mix_Contract SHALL include at minimum: `video_count`, `max_output_duration`, `aspect_ratio`, `transition`, `strip_audio` (or equivalent audio policy), and `director_prompt` reference or hash where useful for support.
3. THE Mix_Contract SHALL be the single source read by **both** Montage_Path and Text_Path entry points (no branch-specific silent defaults that override contract without logging).

### Requirement 2: Video Count on Every Branch

**User Story:** As an editor, when I say “3条视频”, I want three deliverables or a clear failure—not one video and success.

#### Acceptance Criteria

1. WHEN `Mix_Contract.video_count` is N and N > 1, THE Montage_Path SHALL either produce **N valid output files** (`output-1.mp4` … `output-N.mp4`) or SHALL mark the task **failed** with an explicit `error_message` explaining the shortfall.
2. THE `run_auto_pipeline` method SHALL pass `video_count` and **parallel `asset_ids`** into `run_montage_pipeline` (same order as `clip_paths`).
3. WHEN N > 1 and the implementation uses sequential VLM montage generations, EACH generation SHALL receive contract context including index k of N and per-output target duration derived from `max_output_duration`.
4. IF any branch cannot implement N > 1 within MVP scope, THE system SHALL reject task creation at L2_Gate with a user-visible reason rather than silently producing one output.

### Requirement 3: Transition and Engine Alignment

**User Story:** As an editor, when I choose or describe a transition, I want FFmpeg/mixing to use that transition, not `none` by default.

#### Acceptance Criteria

1. WHEN `Mix_Contract.transition` is not `none`, THE Montage_Path and timeline executors SHALL pass that value into the same code paths used by manual mix creation.
2. WHEN intent parsing extracts a transition from natural language, THE merged `mix_params` SHALL set `Mix_Contract.transition` accordingly (within allowed enum values).
3. IF the user’s phrase is ambiguous, THE system MAY default to `none` but SHALL NOT override an explicit UI panel transition without merge-rule documentation.

### Requirement 4: L3 Output Validation

**User Story:** As a product owner, I want failed contract fulfillment to never present as a silent success.

#### Acceptance Criteria

1. AFTER montage or text-driven execution completes, THE system SHALL count valid output video files and compare to `Mix_Contract.video_count`.
2. IF the count is insufficient, THE task SHALL transition to **failed** (or `partial_success` only if explicitly supported and documented) with `error_message` listing expected vs actual.
3. THE Execution_Report SHALL be persisted or appended to `ai_director.log` / task metadata for support.

### Requirement 5: Editing Semantics in Parsed Intent

**User Story:** As an editor, I want order, anchors, exclusions, and pacing hints to be machine-checkable, not only buried in free text.

#### Acceptance Criteria

1. THE `ParsedIntent` schema (and LLM extractor) SHALL support optional fields: `transition`, `anchor_asset_ids` (ordered list), `exclude_asset_ids`, and `pacing` (enum such as `fast` | `normal` | `slow`).
2. WHEN `anchor_asset_ids` is non-empty, THE planner or post-processor SHALL ensure those clips appear at the start of the timeline in order **or** SHALL fail with a clear error if clips are missing from the task asset set.
3. WHEN `exclude_asset_ids` is non-empty, THE timeline SHALL contain no segments referencing those assets.
4. THE VLM/LLM call SHALL receive a structured **EDITING_CONTRACT** block (JSON) in addition to `director_prompt`, built from Mix_Contract, so prompts remain auditable.

### Requirement 6: Asset Analysis Preflight

**User Story:** As an editor, I want to know before mixing if half my library has no analysis, and I want a path to fix or proceed consciously.

#### Acceptance Criteria

1. WHEN a mix task is created with a list of asset IDs, THE API SHALL return (or the create flow SHALL record) a **preflight summary**: per-asset `analysis_status` (`completed`, `pending`, `failed`, `not_found`).
2. IF one or more assets are not `completed`, THE client SHALL receive `warnings` with human-readable impact (e.g. “auto routing may prefer vision path; cached summaries unavailable for N clips”).
3. THE system SHALL expose a documented policy: **block**, **warn + proceed**, or **enqueue reanalysis** (configurable or query param), defaulting to **warn + proceed** for MVP unless product decides otherwise.
4. THE `POST /api/assets/{id}/reanalyze` flow SHALL remain available; preflight MAY suggest calling it for listed IDs.

### Requirement 7: Observability

**User Story:** As a developer, I want logs to show contract, branch, and validation outcomes.

#### Acceptance Criteria

1. `ai_director.log` SHALL log Mix_Contract summary (values, not secrets) at pipeline start.
2. THE log SHALL record L3 validation result (pass/fail, counts).

### Requirement 8: Backward Compatibility

**User Story:** As an operator, existing tasks without Mix_Contract SHALL still run.

#### Acceptance Criteria

1. IF `mix_params` lacks `Mix_Contract`, THE system SHALL synthesize contract fields from legacy keys (`video_count`, `transition`, etc.) with `contract_version` = 0 or “legacy”.
2. THE default `video_count` SHALL remain 1 when unspecified.

## Out of Scope (Phase 1)

- Hermes / external agent OAuth integration (separate spec).
- Full beat-sync to music analysis.
- Automatic legal/compliance scanning of content.

## References

- Task example: `storage/tasks/ac4c378c-b307-4470-8459-a181d5081bec` — demonstrated `video_count` not honored on vision path, `Transition: none`, and multiple `analysis not_found`.
- Related specs: `natural-language-intent-parsing`, `vlm-driven-mixing`, `ai-director`.
