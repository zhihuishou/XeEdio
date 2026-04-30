"""AI Director service — orchestrates VLM analysis and timeline-based editing.

Coordinates the full AI-directed editing pipeline using dual pipelines:
- Text-driven pipeline: for speech/presenter content (ASR + LLM selection)
- Vision-driven pipeline: for visual content (VLM frame analysis)
- Hybrid pipeline: combines both for mixed content

The ``run_auto_pipeline()`` method automatically routes to the optimal
pipeline based on asset analysis results.
"""
from __future__ import annotations


import json
import logging
import os
import subprocess
from typing import Callable, Optional

from app.services.mixing_engine import combine_videos, execute_montage_timeline
from app.services.vlm_service import VLMService
from app.services.asset_analysis_service import AssetAnalysisService
from app.services.text_driven_editing_service import TextDrivenEditingService
from app.services.agent_router import AgentRouter

logger = logging.getLogger("app.ai_director_service")

import re as _re

# Fallback patterns to detect video number markers in LLM reason text
# when the structured video_number field is missing.
_VIDEO_NUM_PATTERNS = [
    _re.compile(r"第\s*(\d+)\s*条"),
    _re.compile(r"视频\s*(\d+)"),
    _re.compile(r"[Vv]ideo\s*(\d+)"),
]


def _split_timeline_by_video(timeline: list[dict]) -> list[list[dict]]:
    """Split a flat timeline into per-video groups.

    Primary: uses the structured ``video_number`` field set by the LLM.
    Fallback: scans the ``reason`` text for patterns like "第1条", "视频1".

    If no video numbers are found at all, returns everything as one group.
    """
    groups: dict[int, list[dict]] = {}
    untagged: list[dict] = []

    for entry in timeline:
        vid_num = entry.get("video_number")

        # Fallback: try regex on reason text
        if vid_num is None:
            reason = entry.get("reason", "")
            for pattern in _VIDEO_NUM_PATTERNS:
                m = pattern.search(reason)
                if m:
                    vid_num = int(m.group(1))
                    break

        if vid_num is not None:
            groups.setdefault(int(vid_num), []).append(entry)
        else:
            untagged.append(entry)

    if not groups:
        return [timeline]

    # Append untagged entries to the last group
    if untagged:
        last_key = max(groups.keys())
        groups[last_key].extend(untagged)

    return [groups[k] for k in sorted(groups.keys())]


def _prompt_requests_broll(director_prompt: str) -> bool:
    """Return True only when prompt explicitly asks for B-roll insertion."""
    if not director_prompt or not director_prompt.strip():
        return False
    text = director_prompt.lower()
    explicit_terms = (
        "b-roll",
        "broll",
        "插入素材",
        "穿插",
        "空镜",
        "插入特写",
        "加特写",
        "切到素材",
        "过渡镜头",
    )
    return any(term in text for term in explicit_terms)


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    ffprobe_bin = os.environ.get("IMAGEIO_FFMPEG_EXE", "ffmpeg").replace(
        "ffmpeg", "ffprobe"
    )
    if ffprobe_bin == "ffprobe" or not ffprobe_bin:
        ffprobe_bin = "ffprobe"

    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        video_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception as e:
        logger.warning("ffprobe failed for %s: %s", video_path, str(e)[:200])
    return 0.0


def _check_audio_level(video_path: str, start: float, duration: float) -> float:
    """Check the mean audio volume (dB) of a segment using FFmpeg volumedetect.

    Returns mean_volume in dB. Silence is typically < -70dB.
    Returns -100.0 on failure.
    """
    ffmpeg_bin = os.environ.get("IMAGEIO_FFMPEG_EXE", "ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg_bin, "-y",
        "-ss", f"{start:.3f}",
        "-i", video_path,
        "-t", f"{duration:.3f}",
        "-af", "volumedetect",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        import re
        match = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", result.stderr or "")
        if match:
            return float(match.group(1))
    except Exception as e:
        logger.warning("audio level check failed: %s", str(e)[:100])
    return -100.0


def _find_non_overlapping_segment(
    used_ranges: list[tuple[float, float]],
    range_start: float,
    range_end: float,
    duration: float,
) -> tuple[float, float] | None:
    """Find a segment of `duration` seconds within [range_start, range_end]
    that doesn't overlap with any used_ranges.
    """
    # Sort used ranges
    sorted_ranges = sorted(used_ranges, key=lambda r: r[0])

    # Check gaps between used ranges
    cursor = range_start
    for used_start, used_end in sorted_ranges:
        gap = used_start - cursor
        if gap >= duration:
            return (cursor, cursor + duration)
        cursor = max(cursor, used_end)

    # Check gap after last used range
    if range_end - cursor >= duration:
        return (cursor, cursor + duration)

    return None


def _get_word_timestamps(video_path: str) -> list[dict]:
    """Extract word-level timestamps from video audio using faster-whisper.

    Returns list of {"word": str, "start": float, "end": float}.
    Returns empty list on failure.
    """
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8", download_root=None)
        segments, info = model.transcribe(
            video_path, language="zh", vad_filter=True, word_timestamps=True
        )
        words = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    if w.word.strip():
                        words.append({
                            "word": w.word.strip(),
                            "start": w.start,
                            "end": w.end,
                        })
        logger.info("word-level timestamps: %d words from %s", len(words), video_path)
        return words
    except Exception as e:
        logger.warning("word-level timestamp extraction failed: %s", str(e)[:200])
        return []


def _find_breath_gap(words: list[dict], target_time: float, search_window: float = 2.0) -> float:
    """Find the nearest breath gap (silence between words) around target_time.

    Searches within [target_time - search_window, target_time + search_window]
    for the midpoint of the largest silence gap between consecutive words.

    Returns the snapped time, or target_time if no suitable gap found.
    """
    if not words:
        return target_time

    # Find word gaps within the search window
    candidates = []
    for i in range(len(words) - 1):
        gap_start = words[i]["end"]
        gap_end = words[i + 1]["start"]
        gap_mid = (gap_start + gap_end) / 2
        gap_duration = gap_end - gap_start

        # Only consider gaps within the search window
        if abs(gap_mid - target_time) <= search_window and gap_duration > 0.05:
            candidates.append({
                "mid": gap_mid,
                "duration": gap_duration,
                "distance": abs(gap_mid - target_time),
            })

    if not candidates:
        return target_time

    # Prefer: longer gaps that are closer to target_time
    # Score = gap_duration / (1 + distance) — balances proximity and gap size
    best = max(candidates, key=lambda c: c["duration"] / (1 + c["distance"]))
    return best["mid"]


def snap_timeline_to_breath_gaps(
    timeline: list[dict],
    clip_paths: list[str],
    log_fn=None,
) -> list[dict]:
    """Snap montage timeline cut points to breath gaps using word-level timestamps.

    For each clip referenced in the timeline, extracts word-level timestamps
    and adjusts source_start/source_end to fall in silence gaps between words
    rather than cutting mid-word.

    Args:
        timeline: VLM-generated montage timeline entries.
        clip_paths: Source clip file paths.
        log_fn: Optional logging function.

    Returns:
        Adjusted timeline (same structure, snapped times).
    """
    if not timeline:
        return timeline

    log = log_fn or (lambda msg: None)

    # Extract word timestamps per clip (cache to avoid re-processing)
    clip_words: dict[int, list[dict]] = {}
    clip_indices = set(int(e.get("clip_index", 0)) for e in timeline)

    for idx in clip_indices:
        if 0 <= idx < len(clip_paths):
            log(f"  Smart-cut: extracting word timestamps for clip_{idx}")
            clip_words[idx] = _get_word_timestamps(clip_paths[idx])
            log(f"  Smart-cut: got {len(clip_words[idx])} words for clip_{idx}")

    if not any(clip_words.values()):
        log("  Smart-cut: no word timestamps available, skipping snap")
        return timeline

    # Snap each entry's boundaries
    snapped = []
    for entry in timeline:
        e = dict(entry)  # copy
        clip_idx = int(e.get("clip_index", 0))
        words = clip_words.get(clip_idx, [])

        if words:
            old_start = e["source_start"]
            old_end = e["source_end"]

            new_start = _find_breath_gap(words, old_start, search_window=1.5)
            new_end = _find_breath_gap(words, old_end, search_window=1.5)

            # Ensure minimum duration after snapping
            if new_end - new_start < 0.5:
                new_start = old_start
                new_end = old_end

            # Update output timeline times proportionally
            duration_change = (new_end - new_start) - (old_end - old_start)
            e["source_start"] = round(new_start, 3)
            e["source_end"] = round(new_end, 3)

            if abs(old_start - new_start) > 0.05 or abs(old_end - new_end) > 0.05:
                log(f"  Smart-cut [{len(snapped)}]: src {old_start:.1f}-{old_end:.1f} → {new_start:.3f}-{new_end:.3f}")

        snapped.append(e)

    # Recalculate output timeline positions (no gaps)
    cursor = 0.0
    for e in snapped:
        dur = e["source_end"] - e["source_start"]
        e["start"] = round(cursor, 3)
        e["end"] = round(cursor + dur, 3)
        cursor += dur

    total_dur = snapped[-1]["end"] if snapped else 0
    log(f"  Smart-cut: timeline snapped, total duration: {total_dur:.1f}s")
    return snapped


def _merge_timelines(
    main_timeline: list[dict],
    broll_candidates: list[dict],
    num_broll_clips: int,
    min_gap_for_insert: float = 0.3,
) -> list[dict]:
    """Merge text-driven main axis timeline with B-roll insertion candidates.

    Identifies natural break points between text segments (gaps in the main
    timeline) and inserts B-roll clips at those points. B-roll clips are
    cycled through round-robin style.

    The main timeline entries use clip_index=0 (presenter clip). B-roll
    entries use clip_index = 1 + broll_candidate["broll_index"] so they
    reference the correct position in the combined clip list
    [presenter, broll_0, broll_1, ...].

    Args:
        main_timeline: Text-driven timeline entries (clip_index=0).
            Each entry has source_start, source_end, start, end, reason.
        broll_candidates: B-roll insertion candidates from vision analysis.
            Each has broll_index, source_start, source_end, duration.
        num_broll_clips: Total number of B-roll clips (for clip_index offset).
        min_gap_for_insert: Minimum gap between main segments (seconds)
            to consider inserting B-roll. Gaps smaller than this are
            left as-is to maintain tight pacing.

    Returns:
        Merged timeline with both main and B-roll entries, output times
        adjusted to account for insertions. Each entry has a "type" field
        ("main" or "broll") for logging/debugging.
    """
    if not main_timeline:
        return []

    if not broll_candidates:
        # No B-roll to insert — return main timeline with type annotations
        result = []
        for entry in main_timeline:
            e = dict(entry)
            e["type"] = "main"
            result.append(e)
        return result

    # Identify insertion points: gaps between consecutive main segments
    # A gap exists when segment[i+1].source_start > segment[i].source_end
    # (in source time) — this indicates a natural break in the narrative.
    insertion_points: list[int] = []  # indices: insert AFTER main_timeline[i]
    for i in range(len(main_timeline) - 1):
        gap = main_timeline[i + 1]["source_start"] - main_timeline[i]["source_end"]
        if gap >= min_gap_for_insert:
            insertion_points.append(i)

    # If no natural gaps found, insert between the segments with the
    # largest output-time gaps (at least try to insert something)
    if not insertion_points and len(main_timeline) > 1:
        gaps_with_idx = []
        for i in range(len(main_timeline) - 1):
            gap = main_timeline[i + 1]["source_start"] - main_timeline[i]["source_end"]
            gaps_with_idx.append((gap, i))
        gaps_with_idx.sort(reverse=True)
        # Take the top gap(s) — at most as many as we have B-roll candidates
        for _, idx in gaps_with_idx[:len(broll_candidates)]:
            insertion_points.append(idx)
        insertion_points.sort()

    # Limit insertions to the number of B-roll candidates available
    if len(insertion_points) > len(broll_candidates):
        # Keep the insertion points with the largest gaps
        gap_sizes = []
        for ip in insertion_points:
            gap = main_timeline[ip + 1]["source_start"] - main_timeline[ip]["source_end"]
            gap_sizes.append((gap, ip))
        gap_sizes.sort(reverse=True)
        insertion_points = sorted(
            [ip for _, ip in gap_sizes[:len(broll_candidates)]]
        )

    # Build the merged timeline
    merged: list[dict] = []
    broll_idx = 0  # round-robin index into broll_candidates
    insertion_set = set(insertion_points)
    cursor = 0.0

    for i, entry in enumerate(main_timeline):
        # Add main entry with adjusted output times
        duration = entry["source_end"] - entry["source_start"]
        merged.append({
            "clip_index": entry.get("clip_index", 0),
            "source_start": entry["source_start"],
            "source_end": entry["source_end"],
            "start": round(cursor, 3),
            "end": round(cursor + duration, 3),
            "reason": entry.get("reason", ""),
            "type": "main",
        })
        cursor += duration

        # Insert B-roll after this segment if it's an insertion point
        if i in insertion_set and broll_idx < len(broll_candidates):
            broll = broll_candidates[broll_idx]
            broll_dur = broll["duration"]
            # clip_index offset: presenter is 0, B-roll clips start at 1
            broll_clip_index = 1 + broll["broll_index"]

            merged.append({
                "clip_index": broll_clip_index,
                "source_start": broll["source_start"],
                "source_end": broll["source_end"],
                "start": round(cursor, 3),
                "end": round(cursor + broll_dur, 3),
                "reason": f"B-roll insertion: {broll.get('filename', '')}",
                "type": "broll",
            })
            cursor += broll_dur
            broll_idx += 1

    return merged


class AIDirectorService:
    """Orchestrates VLM analysis and timeline-based editing."""

    def __init__(self, task_id: str, output_dir: str):
        self.task_id = task_id
        self.output_dir = output_dir
        self.vlm_service = VLMService()

    def _load_clip_summaries(
        self,
        asset_ids: list[str],
        clip_paths: list[str],
        clip_original_filenames: list[str] | None = None,
    ) -> list[dict]:
        """Load clip summaries from DB cache, falling back to real-time VLM analysis.

        For each asset, tries to load the analysis from the asset_analysis DB
        table via AssetAnalysisService.get_analysis(). If the analysis is
        cached (status=completed), uses the DB summary directly. Otherwise,
        falls back to real-time VLM frame extraction + analyze_single_clip().

        Args:
            asset_ids: List of asset IDs (parallel to clip_paths).
            clip_paths: List of video file paths (parallel to asset_ids).

        Returns:
            List of clip summary dicts, one per asset.
        """
        log = self._task_log
        analysis_service = AssetAnalysisService()
        clip_summaries = []
        cache_hits = 0
        cache_misses = 0

        for idx, (asset_id, clip_path) in enumerate(zip(asset_ids, clip_paths)):
            original_filename = None
            if clip_original_filenames and idx < len(clip_original_filenames):
                original_filename = clip_original_filenames[idx]
            try:
                analysis = analysis_service.get_analysis(asset_id)
                if analysis and analysis.get("status") == "completed":
                    # Cache hit: use DB summary directly
                    cache_hits += 1
                    log(f"  Clip {idx} (asset={asset_id}): cache HIT — role={analysis.get('role')}, desc={str(analysis.get('description', ''))[:60]}")
                    clip_summaries.append(analysis)
                else:
                    # Cache miss: real-time frame analysis (fallback)
                    cache_misses += 1
                    status = analysis.get("status") if analysis else "not_found"
                    log(f"  Clip {idx} (asset={asset_id}): cache MISS (status={status}) — running real-time VLM analysis")
                    summary = self._realtime_analyze_clip(clip_path, idx, original_filename)
                    clip_summaries.append(summary)
            except Exception as e:
                cache_misses += 1
                log(f"  Clip {idx} (asset={asset_id}): cache load FAILED ({str(e)[:100]}) — running real-time VLM analysis")
                summary = self._realtime_analyze_clip(clip_path, idx, original_filename)
                clip_summaries.append(summary)

        log(f"  Summary loading complete: {cache_hits} cache hits, {cache_misses} cache misses")
        return clip_summaries

    def _realtime_analyze_clip(
        self,
        clip_path: str,
        idx: int,
        original_filename: str | None = None,
    ) -> dict:
        """Fallback: extract frames and analyze a single clip in real-time.

        Args:
            clip_path: Path to the video file.
            idx: Clip index for logging.

        Returns:
            Clip summary dict with at least 'description' and 'role' keys.
        """
        log = self._task_log
        try:
            vlm_config = self.vlm_service.config.get_vlm_config()
            dur = _get_video_duration(clip_path)
            interval = 10.0 if dur < 300 else 30.0
            max_frames = 10

            frames = self.vlm_service.extract_frames(
                clip_path,
                frame_interval=interval,
                max_frames=max_frames,
            )
            if not frames:
                log(f"  Clip {idx}: no frames extracted, using empty summary")
                return {"description": "", "role": "other"}

            metadata = {
                "filename": original_filename or os.path.basename(clip_path),
                "duration": dur,
            }
            summary = self.vlm_service.analyze_single_clip(frames, metadata)
            if summary:
                log(f"  Clip {idx}: real-time analysis OK — role={summary.get('role')}")
                return summary
            else:
                log(f"  Clip {idx}: real-time VLM analysis returned None, using empty summary")
                return {"description": "", "role": "other"}
        except Exception as e:
            log(f"  Clip {idx}: real-time analysis FAILED: {str(e)[:200]}")
            return {"description": "", "role": "other"}

    def run_montage_pipeline(
        self,
        clip_paths: list[str],
        aspect_ratio: str = "9:16",
        transition: str = "none",
        audio_file: Optional[str] = None,
        max_output_duration: int = 60,
        progress_callback: Optional[Callable[[str], None]] = None,
        director_prompt: str = "",
        asset_ids: list[str] | None = None,
        clip_original_filenames: list[str] | None = None,
        video_count: int = 1,
    ) -> tuple[str, bool]:
        """Run the montage pipeline — all clips are equal, no A-roll/B-roll.

        When asset_ids are provided, loads cached clip summaries from the DB
        and uses generate_unified_timeline() (Stage 2 only, no per-clip VLM).
        Falls back to the legacy per-clip frame extraction + montage timeline
        when no asset_ids or no summaries are available.

        Args:
            clip_paths: List of all video clip file paths.
            aspect_ratio: Target aspect ratio.
            transition: Transition effect.
            audio_file: Optional audio file (TTS or extracted). None for no voiceover.
            max_output_duration: Target output duration in seconds.
            progress_callback: Optional callback for progress updates.
            director_prompt: Optional user directives for VLM.
            asset_ids: Optional list of asset IDs (parallel to clip_paths)
                for loading cached summaries from DB.
            clip_original_filenames: Optional original filenames (parallel to
                clip_paths). When provided, these names are passed to VLM.
            video_count: Number of outputs to generate.

        Returns:
            Tuple of (output_path, ai_director_used).
        """
        output_path = os.path.join(self.output_dir, "output-1.mp4")
        log = self._task_log

        log("=" * 60)
        log(f"AI Director MONTAGE Pipeline Start — task={self.task_id}")
        log(f"Clips: {len(clip_paths)} files: {clip_paths}")
        log(f"Target duration: {max_output_duration}s")
        log(f"Audio file: {audio_file or '(none)'}")
        log(f"Asset IDs: {asset_ids or '(none)'}")
        log(f"Contract video_count: {video_count}")
        if director_prompt:
            log(f"Director prompt: {director_prompt}")
        log(f"Aspect: {aspect_ratio}, Transition: {transition}")

        # Step 1: Load clip summaries from DB (if asset_ids provided) + extract frames
        if progress_callback:
            progress_callback("extracting_frames")

        vlm_config = self.vlm_service.config.get_vlm_config()
        clip_frames = []
        clip_descriptions = []
        total_clip_duration = 0.0

        # Try loading cached summaries when asset_ids are available
        clip_summaries = None
        if asset_ids and len(asset_ids) == len(clip_paths):
            log("Step 0: Loading clip summaries from DB cache")
            clip_summaries = self._load_clip_summaries(
                asset_ids,
                clip_paths,
                clip_original_filenames,
            )

        for idx, path in enumerate(clip_paths):
            display_filename = os.path.basename(path)
            if clip_original_filenames and idx < len(clip_original_filenames):
                display_filename = clip_original_filenames[idx] or display_filename
            try:
                dur = _get_video_duration(path)
                total_clip_duration += dur
                # Extract fewer frames per clip to stay within token limits
                max_frames_per_clip = max(3, vlm_config.get("max_frames", 30) // len(clip_paths))
                frames = self.vlm_service.extract_frames(
                    path,
                    frame_interval=max(dur / max_frames_per_clip, vlm_config.get("frame_interval", 2)),
                    max_frames=max_frames_per_clip,
                )
                clip_frames.append(frames)
                clip_descriptions.append({
                    "filename": display_filename,
                    "duration": dur,
                    "index": idx,
                })
                log(f"Step 1: Clip {idx} — {len(frames)} frames, {dur:.1f}s")
            except Exception as e:
                log(f"Step 1: Clip {idx} frame extraction FAILED: {str(e)[:200]}")
                clip_frames.append([])
                clip_descriptions.append({
                    "filename": display_filename,
                    "duration": _get_video_duration(path),
                    "index": idx,
                })

        log(f"Step 1: Extracted frames from {len(clip_paths)} clips, total source duration: {total_clip_duration:.1f}s")

        # Step 2: Generate timeline via VLM
        if progress_callback:
            progress_callback("analyzing_with_vlm")

        target_duration = min(max_output_duration, total_clip_duration)
        timeline = None

        # --- Path A: Use cached summaries + generate_unified_timeline (Stage 2 only) ---
        if clip_summaries:
            log("Step 2: Using cached clip summaries → generate_unified_timeline (Stage 2 only)")
            import time as _time
            _vlm_start = _time.time()
            try:
                timeline = self.vlm_service.generate_unified_timeline(
                    clip_summaries=clip_summaries,
                    dense_frames=clip_frames,
                    clip_metadata=clip_descriptions,
                    target_duration=target_duration,
                    user_prompt=director_prompt,
                )
                _vlm_elapsed = _time.time() - _vlm_start
                if timeline:
                    log(f"Step 2: Unified timeline returned {len(timeline)} entries (took {_vlm_elapsed:.1f}s):")
                    for i, entry in enumerate(timeline):
                        log(f"  [{i}] clip_{entry.get('clip_index')} "
                            f"src {entry.get('source_start')}-{entry.get('source_end')}s → "
                            f"out {entry.get('start')}-{entry.get('end')}s: "
                            f"{entry.get('reason','')[:60]}")
                    if len(timeline) < 3:
                        log(f"  ⚠️ Unified timeline returned only {len(timeline)} entries (too few) — rejecting, will try legacy path")
                        timeline = None
                else:
                    log(f"Step 2: Unified timeline returned None (took {_vlm_elapsed:.1f}s), will try legacy path")
            except Exception as e:
                _vlm_elapsed = _time.time() - _vlm_start
                log(f"Step 2: Unified timeline FAILED (took {_vlm_elapsed:.1f}s): {str(e)[:300]}, will try legacy path")
                timeline = None

        # --- Path B: Legacy per-clip frame extraction + montage timeline ---
        if timeline is None:
            has_frames = any(len(f) > 0 for f in clip_frames)
            if has_frames:
                vlm_api_url = vlm_config.get("api_url", "(not configured)")
                vlm_model = vlm_config.get("model", "(not configured)")
                log(f"Step 2: Sending frames from {len(clip_paths)} clips to VLM for montage arrangement")
                log(f"  VLM API: {vlm_api_url}")
                log(f"  VLM Model: {vlm_model}")
                log(f"  Target duration: {target_duration:.1f}s")
                import time as _time
                _vlm_start = _time.time()
                try:
                    timeline = self.vlm_service.generate_montage_timeline(
                        clip_frames, clip_descriptions, target_duration,
                        user_prompt=director_prompt,
                    )
                    _vlm_elapsed = _time.time() - _vlm_start
                    if timeline:
                        log(f"Step 2: VLM returned {len(timeline)} montage entries (took {_vlm_elapsed:.1f}s):")
                        for i, entry in enumerate(timeline):
                            log(f"  [{i}] clip_{entry.get('clip_index')} "
                                f"src {entry.get('source_start')}-{entry.get('source_end')}s → "
                                f"out {entry.get('start')}-{entry.get('end')}s: "
                                f"{entry.get('reason','')[:60]}")

                        # Reject degenerate timelines: single entry = no real editing
                        if len(timeline) < 3:
                            log(f"  ⚠️ VLM returned only {len(timeline)} entries (too few for meaningful montage) — rejecting")
                            timeline = None
                    else:
                        log(f"Step 2: VLM returned None (failed or invalid, took {_vlm_elapsed:.1f}s)")
                except Exception as e:
                    _vlm_elapsed = _time.time() - _vlm_start
                    log(f"Step 2: VLM FAILED (took {_vlm_elapsed:.1f}s): {str(e)[:300]}")
                    timeline = None
            else:
                log("Step 2: SKIPPED (no frames extracted)")

        # Step 3: Execute (single or multi-output)
        requested_outputs = max(1, int(video_count or 1))
        output_paths = [os.path.join(self.output_dir, f"output-{i}.mp4") for i in range(1, requested_outputs + 1)]
        any_ai_used = False

        if timeline:
            if progress_callback:
                progress_callback("executing_timeline")

            # Step 2.5: Snap cut points to breath gaps (word-level precision)
            log("Step 2.5: Snapping cut points to breath gaps (word-level)")
            timeline = snap_timeline_to_breath_gaps(timeline, clip_paths, log_fn=log)

            log(f"Step 3: Executing montage timeline with {len(timeline)} entries")
            execute_montage_timeline(
                timeline,
                clip_paths,
                audio_file,
                output_paths[0],
                aspect_ratio,
                transition,
            )
            log(f"Step 3: Montage execution COMPLETE → {output_paths[0]}")
            any_ai_used = True
        else:
            if progress_callback:
                progress_callback("falling_back_to_blind_cut")

            log("Step 3: FALLBACK to blind-cut (no valid montage timeline)")
            combine_videos(
                combined_video_path=output_paths[0],
                video_paths=clip_paths,
                audio_file=audio_file,
                video_aspect=aspect_ratio,
                video_concat_mode="random",
                video_transition=transition,
            )
            log("Step 3: Blind-cut fallback COMPLETE")

        if requested_outputs > 1:
            log(f"Step 4: Generating additional montage outputs ({requested_outputs - 1} more)")
            for idx in range(1, requested_outputs):
                if progress_callback:
                    progress_callback(f"executing_timeline_{idx + 1}")
                out_path = output_paths[idx]
                prompt_suffix = (
                    f"{director_prompt}\n\n"
                    f"[Contract] This is output {idx + 1}/{requested_outputs}. "
                    "Keep overall intent but vary shot order/rhythm versus prior outputs."
                ).strip()

                iteration_timeline = self.vlm_service.generate_montage_timeline(
                    clip_frames,
                    clip_descriptions,
                    target_duration,
                    user_prompt=prompt_suffix,
                )
                if iteration_timeline:
                    iteration_timeline = snap_timeline_to_breath_gaps(
                        iteration_timeline, clip_paths, log_fn=log
                    )
                    execute_montage_timeline(
                        iteration_timeline,
                        clip_paths,
                        audio_file,
                        out_path,
                        aspect_ratio,
                        transition,
                    )
                    any_ai_used = True
                    log(
                        f"Step 4: Additional montage output {idx + 1}/{requested_outputs} COMPLETE → {out_path}"
                    )
                else:
                    combine_videos(
                        combined_video_path=out_path,
                        video_paths=clip_paths,
                        audio_file=audio_file,
                        video_aspect=aspect_ratio,
                        video_concat_mode="random",
                        video_transition=transition,
                    )
                    log(
                        f"Step 4: Additional montage output {idx + 1}/{requested_outputs} FALLBACK COMPLETE → {out_path}"
                    )

        log("=" * 60)
        return output_paths[0], any_ai_used

    def _ensure_asset_analysis_ready(self, asset_ids: list[str]) -> dict[str, dict | None]:
        """Ensure each asset has completed analysis before pipeline routing."""
        log = self._task_log
        analysis_service = AssetAnalysisService()
        analysis_map: dict[str, dict | None] = {}

        for asset_id in asset_ids:
            try:
                analysis = analysis_service.get_analysis(asset_id)
                status = analysis.get("status") if analysis else "not_found"
                if status != "completed":
                    log(
                        f"  Asset {asset_id}: analysis status={status}, "
                        "triggering pre-mix analysis"
                    )
                    try:
                        analysis_service.analyze_asset(asset_id)
                    except Exception as analyze_err:
                        log(
                            f"  Asset {asset_id}: pre-mix analysis trigger failed: "
                            f"{str(analyze_err)[:200]}"
                        )
                    analysis = analysis_service.get_analysis(asset_id)
                    status = analysis.get("status") if analysis else "not_found"
                    log(f"  Asset {asset_id}: post-trigger analysis status={status}")
                analysis_map[asset_id] = analysis
            except Exception as e:
                log(f"  Asset {asset_id}: analysis preflight failed: {str(e)[:200]}")
                analysis_map[asset_id] = None

        return analysis_map

    def run_auto_pipeline(
        self,
        clip_paths: list[str],
        asset_ids: list[str] | None = None,
        clip_original_filenames: list[str] | None = None,
        aspect_ratio: str = "9:16",
        transition: str = "none",
        audio_file: Optional[str] = None,
        max_output_duration: int = 60,
        progress_callback: Optional[Callable[[str], None]] = None,
        director_prompt: str = "",
        video_count: int = 1,
    ) -> tuple[str, bool]:
        """Auto-routing pipeline: choose text-driven or vision-driven based on asset analysis.

        Loads asset_analysis data for each asset and routes:
        - has_speech=True AND role="presenter" → text-driven pipeline
        - Otherwise → vision-driven (montage) pipeline

        Falls back to vision-driven if analysis data is missing.

        Args:
            clip_paths: List of video clip file paths.
            asset_ids: Optional list of asset IDs (parallel to clip_paths)
                for loading analysis from DB. If None, falls back to
                vision-driven pipeline.
            clip_original_filenames: Optional original filenames (parallel to
                clip_paths). These names are surfaced to VLM prompts.
            aspect_ratio: Target aspect ratio.
            transition: Transition effect.
            audio_file: Optional audio file (TTS). None for no voiceover.
            max_output_duration: Target output duration in seconds.
            progress_callback: Optional callback for progress updates.
            director_prompt: Optional user directives.

        Returns:
            Tuple of (output_path, ai_director_used).
        """
        log = self._task_log

        log("=" * 60)
        log(f"AI Director AUTO Pipeline Start — task={self.task_id}")
        log(f"Clips: {len(clip_paths)} files")
        log(f"Asset IDs: {asset_ids}")
        log(f"Target duration: {max_output_duration}s")
        if director_prompt:
            log(f"Director prompt: {director_prompt}")

        # --- Step 1: Load asset analysis for routing decision ---
        pipeline_choice = "vision"  # default
        analysis_data = None
        presenter_index = None
        presenter_analysis = None
        non_presenter_indices: list[int] = []
        analysis_map: dict[str, dict | None] = {}

        if asset_ids:
            analysis_map = self._ensure_asset_analysis_ready(asset_ids)
            for i, asset_id in enumerate(asset_ids):
                try:
                    analysis = analysis_map.get(asset_id)
                    if analysis and analysis.get("status") == "completed":
                        has_speech = analysis.get("has_speech", False)
                        role = analysis.get("role", "other")
                        log(f"  Asset {asset_id}: has_speech={has_speech}, role={role}")

                        if has_speech and role == "presenter":
                            if presenter_index is None:
                                presenter_index = i
                                presenter_analysis = analysis
                                log(f"  → Identified as PRESENTER (index={i})")
                            else:
                                log(f"  → Additional presenter found (index={i}), treating as non-presenter")
                                non_presenter_indices.append(i)
                        else:
                            non_presenter_indices.append(i)
                    else:
                        status = analysis.get("status") if analysis else "not_found"
                        log(f"  Asset {asset_id}: analysis status={status}, skipping")
                        non_presenter_indices.append(i)
                except Exception as e:
                    log(f"  Asset {asset_id}: analysis load failed: {str(e)[:200]}")
                    non_presenter_indices.append(i)

        # Determine fallback pipeline choice based on asset composition
        if presenter_index is not None and non_presenter_indices:
            pipeline_choice = "hybrid"
            analysis_data = presenter_analysis
            log(f"  → Fallback routing: HYBRID pipeline (presenter at index {presenter_index} + {len(non_presenter_indices)} non-presenter clips)")
        elif presenter_index is not None:
            pipeline_choice = "text"
            analysis_data = presenter_analysis
            log(f"  → Fallback routing: TEXT-DRIVEN pipeline (single presenter, no B-roll)")
        else:
            log("  → Fallback routing: VISION-DRIVEN pipeline (no presenter with speech found)")

        # --- NEW: Attempt Agent Router ---
        import time as _time
        routing_decision = None
        routing_method = "fallback"
        if director_prompt and director_prompt.strip() and asset_ids and analysis_map:
            try:
                _router_start = _time.time()
                router = AgentRouter()
                asset_summaries = router.build_asset_summaries(
                    asset_ids, clip_paths, clip_original_filenames, analysis_map
                )
                routing_decision = router.route(director_prompt, asset_summaries, analysis_map)
                _router_elapsed_ms = (_time.time() - _router_start) * 1000
                if routing_decision:
                    routing_method = "agent_router"
                    log(f"  Agent Router succeeded in {_router_elapsed_ms:.0f}ms")
                    log(f"  Agent Router decision: pipeline={routing_decision.pipeline}, "
                        f"asset_roles={routing_decision.asset_roles}, "
                        f"parameters={routing_decision.parameters}")
                    # Log comparison with fallback
                    _fallback_pipeline_map = {"text": "text_driven", "hybrid": "hybrid", "vision": "vision_montage"}
                    _fallback_id = _fallback_pipeline_map.get(pipeline_choice, pipeline_choice)
                    if routing_decision.pipeline != _fallback_id:
                        log(f"  ⚡ Agent router chose {routing_decision.pipeline}; "
                            f"fallback would have chosen {_fallback_id}")
                else:
                    log(f"  Agent Router returned None after {_router_elapsed_ms:.0f}ms, "
                        f"falling back to existing routing")
            except Exception as e:
                log(f"  Agent Router failed: {str(e)[:200]}, falling back to existing routing")
                routing_decision = None
        else:
            if not director_prompt or not director_prompt.strip():
                log("  Agent Router skipped: director_prompt is empty or whitespace")
            elif not asset_ids:
                log("  Agent Router skipped: no asset_ids provided")
            elif not analysis_map:
                log("  Agent Router skipped: no analysis data available")

        # Write routing decision to file for observability
        try:
            from dataclasses import asdict
            routing_info = {
                "routing_method": routing_method,
                "routing_decision": asdict(routing_decision) if routing_decision else None,
            }
            routing_info_path = os.path.join(self.output_dir, "routing_decision.json")
            with open(routing_info_path, "w", encoding="utf-8") as f:
                json.dump(routing_info, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"  Failed to write routing_decision.json: {str(e)[:100]}")

        # --- Step 2: Execute chosen pipeline ---

        # Dispatch based on Agent Router decision (if available)
        if routing_decision:
            if routing_decision.pipeline == "text_driven":
                # Find the presenter asset from asset_roles
                presenter_asset_id = None
                for aid, role in routing_decision.asset_roles.items():
                    if role == "presenter":
                        presenter_asset_id = aid
                        break
                if presenter_asset_id and presenter_asset_id in (analysis_map or {}):
                    _presenter_analysis = analysis_map[presenter_asset_id]
                    return self._run_text_driven(
                        clip_paths=clip_paths,
                        analysis=_presenter_analysis,
                        asset_ids=asset_ids,
                        clip_original_filenames=clip_original_filenames,
                        aspect_ratio=aspect_ratio,
                        transition=transition,
                        audio_file=audio_file,
                        max_output_duration=max_output_duration,
                        progress_callback=progress_callback,
                        director_prompt=director_prompt,
                        video_count=video_count,
                    )
                else:
                    log("  ⚠️ Agent Router chose text_driven but no presenter found in asset_roles, falling back")

            elif routing_decision.pipeline in ("vision_montage", "multi_asset_montage"):
                return self.run_montage_pipeline(
                    clip_paths=clip_paths,
                    aspect_ratio=aspect_ratio,
                    transition=transition,
                    audio_file=audio_file,
                    max_output_duration=max_output_duration,
                    progress_callback=progress_callback,
                    director_prompt=director_prompt,
                    asset_ids=asset_ids,
                    clip_original_filenames=clip_original_filenames,
                    video_count=video_count,
                )

            elif routing_decision.pipeline == "hybrid":
                # Separate assets into presenter and broll based on asset_roles
                _presenter_aid = None
                _broll_aids = []
                for aid, role in routing_decision.asset_roles.items():
                    if role == "presenter" and _presenter_aid is None:
                        _presenter_aid = aid
                    else:
                        _broll_aids.append(aid)

                if _presenter_aid and asset_ids:
                    try:
                        _p_idx = asset_ids.index(_presenter_aid)
                    except ValueError:
                        _p_idx = None

                    if _p_idx is not None:
                        _presenter_path = clip_paths[_p_idx]
                        _presenter_analysis = analysis_map.get(_presenter_aid)
                        _broll_paths = [clip_paths[asset_ids.index(aid)]
                                        for aid in _broll_aids if aid in asset_ids]
                        _broll_asset_ids = [aid for aid in _broll_aids if aid in asset_ids]

                        if _presenter_analysis:
                            return self._run_hybrid_pipeline(
                                presenter_path=_presenter_path,
                                presenter_analysis=_presenter_analysis,
                                broll_paths=_broll_paths,
                                broll_asset_ids=_broll_asset_ids,
                                aspect_ratio=aspect_ratio,
                                transition=transition,
                                audio_file=audio_file,
                                max_output_duration=max_output_duration,
                                progress_callback=progress_callback,
                                director_prompt=director_prompt,
                                video_count=video_count,
                            )
                        else:
                            log("  ⚠️ Agent Router chose hybrid but presenter analysis unavailable, falling back")
                    else:
                        log("  ⚠️ Agent Router chose hybrid but presenter asset_id not in asset_ids, falling back")
                else:
                    log("  ⚠️ Agent Router chose hybrid but no presenter in asset_roles, falling back")

            log(f"  ⚠️ Agent Router dispatch failed for pipeline={routing_decision.pipeline}, using fallback routing")

        # Fallback: existing if/else routing (unchanged)
        if routing_method == "fallback":
            log(f"  Using fallback routing: pipeline_choice={pipeline_choice}")

        if pipeline_choice == "hybrid" and analysis_data and presenter_index is not None:
            presenter_path = clip_paths[presenter_index]
            broll_paths = [clip_paths[i] for i in non_presenter_indices]
            broll_ids = [asset_ids[i] for i in non_presenter_indices] if asset_ids else None
            return self._run_hybrid_pipeline(
                presenter_path=presenter_path,
                presenter_analysis=analysis_data,
                broll_paths=broll_paths,
                broll_asset_ids=broll_ids,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
                video_count=video_count,
            )
        elif pipeline_choice == "text" and analysis_data:
            return self._run_text_driven(
                clip_paths=clip_paths,
                analysis=analysis_data,
                asset_ids=asset_ids,
                clip_original_filenames=clip_original_filenames,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
                video_count=video_count,
            )
        else:
            return self.run_montage_pipeline(
                clip_paths=clip_paths,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
                asset_ids=asset_ids,
                clip_original_filenames=clip_original_filenames,
                video_count=video_count,
            )

    def _run_text_driven(
        self,
        clip_paths: list[str],
        analysis: dict,
        asset_ids: list[str] | None = None,
        clip_original_filenames: list[str] | None = None,
        aspect_ratio: str = "9:16",
        transition: str = "none",
        audio_file: Optional[str] = None,
        max_output_duration: int = 60,
        progress_callback: Optional[Callable[[str], None]] = None,
        director_prompt: str = "",
        video_count: int = 1,
    ) -> tuple[str, bool]:
        """Execute the text-driven pipeline using ASR transcript + LLM selection.

        When the LLM returns a timeline with entries tagged for multiple
        output videos (e.g. "第1条", "第2条"), this method splits them
        into separate output files (output-1.mp4, output-2.mp4, ...).

        Args:
            clip_paths: List of video clip file paths.
            analysis: Asset analysis dict with transcript.
            aspect_ratio: Target aspect ratio.
            transition: Transition effect.
            audio_file: Optional TTS audio file.
            max_output_duration: Target output duration in seconds.
            progress_callback: Optional progress callback.
            director_prompt: User directives.
            video_count: Number of output videos (from mix_params).

        Returns:
            Tuple of (first_output_path, ai_director_used).
        """
        output_path = os.path.join(self.output_dir, "output-1.mp4")
        log = self._task_log

        log("--- Text-Driven Pipeline ---")

        transcript = analysis.get("transcript", "")
        if not transcript:
            log("  ⚠️ No transcript in analysis, falling back to vision-driven pipeline")
            return self.run_montage_pipeline(
                clip_paths=clip_paths,
                asset_ids=asset_ids,
                clip_original_filenames=clip_original_filenames,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
            )

        # Get word-level timestamps — try extracting from the first clip
        if progress_callback:
            progress_callback("extracting_word_timestamps")

        log("  Step 1: Extracting word-level timestamps")
        word_timestamps = _get_word_timestamps(clip_paths[0])
        if not word_timestamps:
            log("  ⚠️ Word timestamp extraction failed, falling back to vision-driven pipeline")
            return self.run_montage_pipeline(
                clip_paths=clip_paths,
                asset_ids=asset_ids,
                clip_original_filenames=clip_original_filenames,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
            )

        log(f"  Step 1: Got {len(word_timestamps)} word timestamps")

        # Generate text-driven timeline
        if progress_callback:
            progress_callback("generating_text_driven_timeline")

        log("  Step 2: Calling TextDrivenEditingService")
        text_service = TextDrivenEditingService()
        timeline = text_service.generate_text_driven_timeline(
            transcript=transcript,
            word_timestamps=word_timestamps,
            target_duration=float(max_output_duration),
            user_prompt=director_prompt,
            video_count=video_count,
        )

        if not timeline:
            log("  ⚠️ Text-driven timeline generation failed, falling back to vision-driven pipeline")
            return self.run_montage_pipeline(
                clip_paths=clip_paths,
                asset_ids=asset_ids,
                clip_original_filenames=clip_original_filenames,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
            )

        log(f"  Step 2: Text-driven timeline generated with {len(timeline)} entries:")
        for i, entry in enumerate(timeline):
            log(f"    [{i}] src {entry.get('source_start')}-{entry.get('source_end')}s → "
                f"out {entry.get('start')}-{entry.get('end')}s: "
                f"{entry.get('reason', '')[:80]}")

        # --- Step 2.5: Split timeline into per-video groups ---
        video_groups = _split_timeline_by_video(timeline)
        log(f"  Step 2.5: Split into {len(video_groups)} video(s)")

        # Execute the timeline(s)
        if progress_callback:
            progress_callback("executing_timeline")

        log("  Step 3: Executing text-driven timeline")
        try:
            first_output = None
            for vid_idx, group in enumerate(video_groups):
                vid_num = vid_idx + 1
                vid_output = os.path.join(self.output_dir, f"output-{vid_num}.mp4")

                # Recalculate output timestamps for this group (start from 0)
                cursor = 0.0
                adjusted = []
                for entry in group:
                    dur = float(entry["source_end"]) - float(entry["source_start"])
                    adjusted.append({
                        **entry,
                        "start": round(cursor, 3),
                        "end": round(cursor + dur, 3),
                    })
                    cursor += dur

                log(f"  Step 3: Rendering video {vid_num}/{len(video_groups)} "
                    f"({len(adjusted)} entries, {cursor:.1f}s) → {vid_output}")

                execute_montage_timeline(
                    adjusted,
                    clip_paths,
                    audio_file,
                    vid_output,
                    aspect_ratio,
                    transition,
                )
                if first_output is None:
                    first_output = vid_output

            log(f"  Step 3: Text-driven execution COMPLETE → {len(video_groups)} video(s)")
            log("=" * 60)
            return first_output or output_path, True
        except Exception as e:
            log(f"  Step 3: Text-driven execution FAILED: {str(e)[:300]}")
            log("  Falling back to vision-driven pipeline")
            return self.run_montage_pipeline(
                clip_paths=clip_paths,
                asset_ids=asset_ids,
                clip_original_filenames=clip_original_filenames,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
            )

    # ------------------------------------------------------------------
    # Hybrid pipeline: text-driven main axis + vision-driven B-roll
    # ------------------------------------------------------------------

    def _run_hybrid_pipeline(
        self,
        presenter_path: str,
        presenter_analysis: dict,
        broll_paths: list[str],
        broll_asset_ids: list[str] | None = None,
        aspect_ratio: str = "9:16",
        transition: str = "none",
        audio_file: Optional[str] = None,
        max_output_duration: int = 60,
        progress_callback: Optional[Callable[[str], None]] = None,
        director_prompt: str = "",
        video_count: int = 1,
    ) -> tuple[str, bool]:
        """Hybrid pipeline: text-driven narrative + vision-driven B-roll insertion.

        Combines both pipelines:
        1. Text-driven pipeline on the presenter asset → main narrative timeline
        2. Vision-driven analysis on B-roll assets → insertion candidates
        3. Merge: text entries form the main axis, B-roll inserted at natural breaks

        Falls back to text-driven-only or vision-driven-only on partial failure.

        Args:
            presenter_path: File path of the presenter (speech) clip.
            presenter_analysis: Asset analysis dict for the presenter clip.
            broll_paths: File paths of B-roll (non-presenter) clips.
            broll_asset_ids: Optional asset IDs for B-roll clips (for DB cache).
            aspect_ratio: Target aspect ratio.
            transition: Transition effect.
            audio_file: Optional TTS audio file.
            max_output_duration: Target output duration in seconds.
            progress_callback: Optional progress callback.
            director_prompt: User directives.
            video_count: Number of output videos to render.

        Returns:
            Tuple of (output_path, ai_director_used).
        """
        output_path = os.path.join(self.output_dir, "output-1.mp4")
        log = self._task_log

        log("=" * 60)
        log(f"AI Director HYBRID Pipeline Start — task={self.task_id}")
        log(f"Presenter: {presenter_path}")
        log(f"B-roll: {len(broll_paths)} files: {broll_paths}")
        log(f"Target duration: {max_output_duration}s")
        log(f"Contract video_count: {video_count}")
        if director_prompt:
            log(f"Director prompt: {director_prompt}")
        use_broll_insertions = _prompt_requests_broll(director_prompt)
        log(
            f"B-roll insertion mode: {'enabled (prompt-explicit)' if use_broll_insertions else 'disabled (prompt-not-explicit)'}"
        )

        # --- Step 1: Text-driven pipeline on presenter → main axis timeline ---
        if progress_callback:
            progress_callback("generating_text_driven_timeline")

        transcript = presenter_analysis.get("transcript", "")
        if not transcript:
            log("  ⚠️ No transcript for presenter, falling back to vision-driven pipeline")
            all_paths = [presenter_path] + broll_paths
            return self.run_montage_pipeline(
                clip_paths=all_paths,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
                video_count=video_count,
            )

        log("  Step 1: Extracting word-level timestamps from presenter")
        word_timestamps = _get_word_timestamps(presenter_path)
        if not word_timestamps:
            log("  ⚠️ Word timestamp extraction failed, falling back to vision-driven pipeline")
            all_paths = [presenter_path] + broll_paths
            return self.run_montage_pipeline(
                clip_paths=all_paths,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
                video_count=video_count,
            )

        log(f"  Step 1: Got {len(word_timestamps)} word timestamps")

        text_service = TextDrivenEditingService()
        main_timeline = text_service.generate_text_driven_timeline(
            transcript=transcript,
            word_timestamps=word_timestamps,
            target_duration=float(max_output_duration),
            user_prompt=director_prompt,
            video_count=video_count,
        )

        if not main_timeline:
            log("  ⚠️ Text-driven timeline failed, falling back to vision-driven pipeline")
            all_paths = [presenter_path] + broll_paths
            return self.run_montage_pipeline(
                clip_paths=all_paths,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
                video_count=video_count,
            )

        log(f"  Step 1: Text-driven main axis has {len(main_timeline)} entries")
        for i, entry in enumerate(main_timeline):
            log(f"    [{i}] src {entry.get('source_start')}-{entry.get('source_end')}s → "
                f"out {entry.get('start')}-{entry.get('end')}s: "
                f"{entry.get('reason', '')[:60]}")
        video_groups = _split_timeline_by_video(main_timeline)
        log(f"  Step 1.5: Split into {len(video_groups)} video group(s)")

        # --- Step 2: Vision-driven analysis on B-roll → insertion candidates ---
        broll_candidates = []
        if use_broll_insertions and broll_paths:
            if progress_callback:
                progress_callback("analyzing_broll")
            log("  Step 2: Analyzing B-roll clips for insertion candidates")
            for idx, broll_path in enumerate(broll_paths):
                try:
                    dur = _get_video_duration(broll_path)
                    if dur <= 0:
                        log(f"    B-roll {idx}: zero duration, skipping")
                        continue
                    clip_dur = min(dur, 3.0)
                    broll_candidates.append({
                        "broll_index": idx,
                        "path": broll_path,
                        "source_start": 0.0,
                        "source_end": round(clip_dur, 3),
                        "duration": round(clip_dur, 3),
                        "filename": os.path.basename(broll_path),
                    })
                    log(f"    B-roll {idx}: {os.path.basename(broll_path)} → {clip_dur:.1f}s candidate")
                except Exception as e:
                    log(f"    B-roll {idx}: analysis failed: {str(e)[:100]}")
            log(f"  Step 2: {len(broll_candidates)} B-roll candidates ready")
            if not broll_candidates:
                log("  ⚠️ No valid B-roll candidates, using text-driven timeline only")
        else:
            log("  Step 2: Skipped B-roll analysis (prompt did not explicitly request insertion)")

        # --- Step 3: Merge timelines ---
        if progress_callback:
            progress_callback("merging_timelines")

        log("  Step 3: Building per-video hybrid timelines")
        output_paths: list[str] = []
        all_clip_paths = [presenter_path] + broll_paths
        first_output = None

        # --- Step 4: Execute merged timeline(s) ---
        if progress_callback:
            progress_callback("executing_timeline")
        try:
            for vid_idx, group in enumerate(video_groups):
                vid_num = vid_idx + 1
                vid_output = os.path.join(self.output_dir, f"output-{vid_num}.mp4")

                cursor = 0.0
                adjusted_group = []
                for entry in group:
                    dur = float(entry["source_end"]) - float(entry["source_start"])
                    adjusted_group.append({
                        **entry,
                        "start": round(cursor, 3),
                        "end": round(cursor + dur, 3),
                    })
                    cursor += dur

                if use_broll_insertions and broll_candidates:
                    merged_timeline = _merge_timelines(
                        adjusted_group, broll_candidates, len(broll_paths)
                    )
                    broll_count = sum(1 for e in merged_timeline if e.get("type") == "broll")
                    log(
                        f"  Step 4: Rendering hybrid video {vid_num}/{len(video_groups)} "
                        f"(main={len(adjusted_group)}, broll={broll_count}) → {vid_output}"
                    )
                    execute_montage_timeline(
                        merged_timeline,
                        all_clip_paths,
                        audio_file,
                        vid_output,
                        aspect_ratio,
                        transition,
                    )
                else:
                    log(
                        f"  Step 4: Rendering text-driven-only video {vid_num}/{len(video_groups)} "
                        f"(entries={len(adjusted_group)}) → {vid_output}"
                    )
                    execute_montage_timeline(
                        adjusted_group,
                        [presenter_path],
                        audio_file,
                        vid_output,
                        aspect_ratio,
                        transition,
                    )
                output_paths.append(vid_output)
                if first_output is None:
                    first_output = vid_output
            log(f"  Step 4: Hybrid execution COMPLETE → {len(output_paths)} video(s)")
            log("=" * 60)
            return first_output or output_path, True
        except Exception as e:
            log(f"  Step 4: Hybrid execution FAILED: {str(e)[:300]}")
            log("  Falling back to vision-driven pipeline")
            return self.run_montage_pipeline(
                clip_paths=all_clip_paths,
                aspect_ratio=aspect_ratio,
                transition=transition,
                audio_file=audio_file,
                max_output_duration=max_output_duration,
                progress_callback=progress_callback,
                director_prompt=director_prompt,
                video_count=video_count,
            )

    def _task_log(self, message: str) -> None:
        """Write a log line to the task-specific log file."""
        from datetime import datetime, timezone
        log_path = os.path.join(self.output_dir, "ai_director.log")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception:
            pass
        logger.info("[task %s] %s", self.task_id, message)
