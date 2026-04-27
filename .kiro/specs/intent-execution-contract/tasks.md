# Implementation Plan: Intent–Execution Contract

## Overview

Implement Mix_Contract snapshot, pass `video_count` + `transition` + `asset_ids` through Montage_Path, multi-output montage (Strategy A), L3 output validation, extend ParsedIntent for editing semantics, and add asset preflight warnings. Phased for incremental delivery.

## Phase 1 — Contract plumbing & montage parity (P0)

- [ ] **1.1** Define `mix_contract` structure and `contract_version` in `mixing_service.py` when building `mix_params` after intent merge; include legacy fallback reader.
- [ ] **1.2** `AIDirectorService.run_auto_pipeline`: pass `video_count` and `asset_ids` into `run_montage_pipeline`.
- [ ] **1.3** `run_montage_pipeline`: add `video_count` parameter; log `mix_contract` summary at start; ensure `asset_ids` used for cached summaries path when lengths match.
- [ ] **1.4** Implement Montage multi-output **Strategy A** (loop `1..video_count`, distinct `output-k.mp4`, contract-aware prompt suffix per iteration).
- [ ] **1.5** `MixingService.execute_mix`: L3 validation—count non-empty `output-{k}.mp4` vs `video_count`; on mismatch set task `failed` + `error_message`.
- [ ] **1.6** Thread `transition` from `mix_contract` / `mix_params` into `run_montage_pipeline` / `execute_montage_timeline` (verify current call chain passes non-`none` values).
- [ ] **1.7** Unit tests: montage with `video_count=2` mocked VLM returns two timelines → two outputs; `video_count=2` but one failure → task failed.
- [ ] **1.8** Manual regression: single-output default (`video_count=1`) unchanged.

## Phase 2 — ParsedIntent editing semantics (P1)

- [ ] **2.1** Extend `ParsedIntent` dataclass: `transition`, `anchor_asset_ids`, `exclude_asset_ids`, `pacing` (+ defaults and clamp rules).
- [ ] **2.2** Update `IntentParsingService` system prompt and `_validate_and_clamp` / `from_dict`.
- [ ] **2.3** Merge new fields into `mix_contract` in `MixingService`.
- [ ] **2.4** Build `EDITING_CONTRACT` JSON blob for VLM calls in `VLMService` / `AIDirectorService` (montage + unified timeline paths).
- [ ] **2.5** Implement anchor enforcement MVP (prepend ordered segments OR inject index table into prompt—pick one per design §4.2).
- [ ] **2.6** Filter excluded assets from timeline before execution.
- [ ] **2.7** Map `pacing` enum to VLM hints or frame-density parameters (document mapping table).
- [ ] **2.8** Extend `ParseIntentResponse` schema and `mix_chat.html` preview if needed.

## Phase 3 — Asset preflight & resilience (P1–P2)

- [ ] **3.1** Implement `preflight_assets(asset_ids) -> list[status]` in service layer (reuse `AssetAnalysisService`).
- [ ] **3.2** On `create_mix_task`, attach `preflight` / `warnings` to response or store on task JSON per requirements policy (warn + proceed default).
- [ ] **3.3** `mix_chat.html`: display warnings when assets incomplete (non-blocking MVP).
- [ ] **3.4** (Optional) Sync lightweight `analyze_single_clip` for `not_found` clips before montage—behind feature flag or max N clips.
- [ ] **3.5** Document reanalyze flow in `API_REFERENCE.md` (cross-link to spec).

## Phase 4 — Docs & cleanup

- [ ] **4.1** Update `natural-language-intent-parsing/design.md` cross-reference OR add single-line pointer from that spec to `mix_contract`.
- [ ] **4.2** Mark `vlm-driven-mixing/tasks.md` follow-up or close related ad-hoc debt.
- [ ] **4.3** E2E test: create mix with `video_count=2`, two assets, assert two outputs or explicit failure.

## Dependencies

- `IntentParsingService`, `AIDirectorService`, `VLMService`, `mixing_service`, `schemas/mix.py`, `mixing_engine.execute_montage_timeline` signature unchanged unless transition already threaded.

## Non-goals (this spec iteration)

- Hermes OAuth / expert mode UI.
- Single VLM call multi-`video_index` timeline (Phase 2 optimization—separate task after Strategy A stable).
