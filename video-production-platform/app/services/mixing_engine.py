"""MoviePy mixing engine for smart video composition.

A-roll (talent speaking) stays intact as the timeline backbone — both video and audio.
B-roll (product shots, stock footage) gets cut into segments and inserted at intervals,
replacing A-roll's visuals while keeping A-roll's audio track continuous.
"""

import gc
import logging
import os
import random
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    VideoFileClip,
    afx,
    vfx,
)

logger = logging.getLogger("app.mixing_engine")

AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"
VIDEO_CODEC = "libx264"
FPS = 30

ASPECT_RESOLUTIONS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_ffmpeg_binary() -> str:
    return os.environ.get("IMAGEIO_FFMPEG_EXE") or "ffmpeg"


def _escape_ffmpeg_concat_path(file_path: str) -> str:
    """Escape single quotes for FFmpeg concat demuxer list files."""
    return file_path.replace("'", "'\\''")


def _concat_video_clips_with_ffmpeg(
    clip_files: List[str],
    output_file: str,
    threads: int,
    output_dir: str,
) -> None:
    """Join processed clip files using FFmpeg concat demuxer.

    This avoids MoviePy's concatenate_videoclips which re-encodes everything,
    causing quality degradation and colour shifts.
    """
    concat_list_file = os.path.join(output_dir, "ffmpeg-concat-list.txt")
    with open(concat_list_file, "w", encoding="utf-8") as fp:
        for clip_file in clip_files:
            absolute_path = os.path.abspath(clip_file)
            fp.write(f"file '{_escape_ffmpeg_concat_path(absolute_path)}'\n")

    command = [
        _get_ffmpeg_binary(),
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_file,
        "-c:v", VIDEO_CODEC,
        "-threads", str(threads or 2),
        "-pix_fmt", "yuv420p",
        output_file,
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            error_message = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(error_message or "ffmpeg concat failed")
    finally:
        _delete_files(concat_list_file)


# ---------------------------------------------------------------------------
# Resource cleanup helpers
# ---------------------------------------------------------------------------

def _close_clip(clip) -> None:
    if clip is None:
        return
    try:
        if hasattr(clip, "reader") and clip.reader is not None:
            clip.reader.close()
        if hasattr(clip, "audio") and clip.audio is not None:
            if hasattr(clip.audio, "reader") and clip.audio.reader is not None:
                clip.audio.reader.close()
            del clip.audio
        if hasattr(clip, "mask") and clip.mask is not None:
            if hasattr(clip.mask, "reader") and clip.mask.reader is not None:
                clip.mask.reader.close()
            del clip.mask
        if hasattr(clip, "clips") and clip.clips:
            for child in clip.clips:
                if child is not clip:
                    _close_clip(child)
            clip.clips = []
    except Exception as e:
        logger.error("failed to close clip: %s", str(e))
    del clip
    gc.collect()


def _delete_files(files) -> None:
    if isinstance(files, str):
        files = [files]
    for f in files:
        try:
            os.remove(f)
        except Exception:
            pass


def _resize_clip(clip, target_w: int, target_h: int):
    """Resize clip to target resolution, maintaining aspect ratio with black padding."""
    clip_w, clip_h = clip.size
    if clip_w == target_w and clip_h == target_h:
        return clip

    clip_ratio = clip_w / clip_h
    target_ratio = target_w / target_h

    if abs(clip_ratio - target_ratio) < 0.01:
        return clip.resized(new_size=(target_w, target_h))

    if clip_ratio > target_ratio:
        scale = target_w / clip_w
    else:
        scale = target_h / clip_h

    new_w = int(clip_w * scale)
    new_h = int(clip_h * scale)

    bg = ColorClip(size=(target_w, target_h), color=(0, 0, 0)).with_duration(clip.duration)
    resized = clip.resized(new_size=(new_w, new_h)).with_position("center")
    return CompositeVideoClip([bg, resized])


# ---------------------------------------------------------------------------
# Transition effects
# ---------------------------------------------------------------------------

def fadein_transition(clip, duration: float = 0.5):
    return clip.with_effects([vfx.FadeIn(duration)])

def fadeout_transition(clip, duration: float = 0.5):
    return clip.with_effects([vfx.FadeOut(duration)])

def slidein_transition(clip, duration: float = 0.5, side: str = "left"):
    w, h = clip.size
    def pos(t):
        p = min(max(t / max(duration, 0.001), 0), 1)
        if side == "left": return (-w + w * p, 0)
        if side == "right": return (w - w * p, 0)
        if side == "top": return (0, -h + h * p)
        return (0, h - h * p)
    bg = ColorClip(size=(w, h), color=(0, 0, 0)).with_duration(clip.duration)
    return CompositeVideoClip([bg, clip.with_position(pos)], size=(w, h)).with_duration(clip.duration)

def slideout_transition(clip, duration: float = 0.5, side: str = "right"):
    w, h = clip.size
    start = max(clip.duration - duration, 0)
    def pos(t):
        if t <= start: return (0, 0)
        p = min(max((t - start) / max(duration, 0.001), 0), 1)
        if side == "left": return (-w * p, 0)
        if side == "right": return (w * p, 0)
        if side == "top": return (0, -h * p)
        return (0, h * p)
    bg = ColorClip(size=(w, h), color=(0, 0, 0)).with_duration(clip.duration)
    return CompositeVideoClip([bg, clip.with_position(pos)], size=(w, h)).with_duration(clip.duration)

def apply_transition(clip, transition_type: str, duration: float = 0.5):
    if transition_type in (None, "none", ""):
        return clip
    side = random.choice(["left", "right", "top", "bottom"])
    if transition_type == "fade_in": return fadein_transition(clip, duration)
    if transition_type == "fade_out": return fadeout_transition(clip, duration)
    if transition_type == "slide_in": return slidein_transition(clip, duration, side)
    if transition_type == "slide_out": return slideout_transition(clip, duration, side)
    if transition_type == "shuffle":
        fn = random.choice([fadein_transition, fadeout_transition,
                            lambda c, d: slidein_transition(c, d, side),
                            lambda c, d: slideout_transition(c, d, side)])
        return fn(clip, duration)
    return clip


# ---------------------------------------------------------------------------
# Core: A-roll + B-roll interleaved mixing
# ---------------------------------------------------------------------------

def combine_videos(
    combined_video_path: str,
    video_paths: List[str],
    audio_file: str,
    video_aspect: str = "9:16",
    video_concat_mode: str = "random",
    video_transition: str = "none",
    max_clip_duration: int = 5,
    threads: int = 2,
    a_roll_paths: Optional[List[str]] = None,
    b_roll_paths: Optional[List[str]] = None,
) -> str:
    """Core mixing function.

    Logic:
    - A-roll stays INTACT as the timeline backbone (video + audio)
    - B-roll segments are inserted at regular intervals, replacing A-roll's
      visuals while A-roll's audio continues underneath
    - If no B-roll, output is just A-roll resized to target aspect ratio

    Timeline example (clip_duration=5s, A-roll=30s, B-roll available):
      [A 0-5s][B1 5-10s][A 10-15s][B2 15-20s][A 20-25s][B3 25-30s]

    Args:
        combined_video_path: Output file path.
        video_paths: Legacy param (ignored if a_roll_paths provided).
        audio_file: Audio file (A-roll extracted audio).
        video_aspect: Target aspect ratio.
        video_concat_mode: "random" or "sequential" for B-roll arrangement.
        video_transition: Transition effect for B-roll insertions.
        max_clip_duration: Duration of each segment in seconds.
        threads: FFmpeg threads.
        a_roll_paths: Explicit A-roll video paths.
        b_roll_paths: Explicit B-roll video paths.
    """
    # Resolve paths
    if a_roll_paths is None:
        a_roll_paths = video_paths
    if b_roll_paths is None:
        b_roll_paths = []

    if not a_roll_paths:
        raise ValueError("No A-roll video paths provided")

    output_dir = os.path.dirname(combined_video_path)
    os.makedirs(output_dir, exist_ok=True)

    target_w, target_h = ASPECT_RESOLUTIONS.get(video_aspect, (1080, 1920))

    # --- Step 1: Build A-roll timeline ---
    # Concatenate all A-roll videos into one continuous clip
    logger.info("building A-roll timeline from %d videos", len(a_roll_paths))
    a_roll_total_duration = 0.0
    a_roll_segments = []  # (file_path, start, end) tuples covering the full A-roll

    for path in a_roll_paths:
        clip = VideoFileClip(path)
        dur = clip.duration
        _close_clip(clip)
        a_roll_segments.append((path, 0.0, dur))
        a_roll_total_duration += dur

    logger.info("A-roll total duration: %.2fs", a_roll_total_duration)

    # --- Step 2: Prepare B-roll segments ---
    # Each B-roll source is used exactly ONCE (no repetition).
    # Each B-roll clip is trimmed to a short duration (2-3s) for natural insertion.
    BROLL_INSERT_DURATION = 2.0  # seconds — short enough to not lose too much A-roll context
    b_roll_clips_info = []  # list of (path, start, end)
    if b_roll_paths:
        for path in b_roll_paths:
            clip = VideoFileClip(path)
            dur = clip.duration
            _close_clip(clip)
            use_dur = min(dur, BROLL_INSERT_DURATION)
            if use_dur > 0.1:
                b_roll_clips_info.append((path, 0.0, use_dur))

        if video_concat_mode == "random":
            random.shuffle(b_roll_clips_info)

    logger.info("B-roll segments available: %d (each %.1fs, used once)", len(b_roll_clips_info), BROLL_INSERT_DURATION)

    # --- Step 3: Build interleaved timeline ---
    # Strategy: Evenly distribute B-roll insertions across the A-roll timeline.
    # If we have N B-roll clips, we split A-roll into (N+1) chunks and insert
    # one B-roll between each pair of A-roll chunks.
    #
    # Example with 2 B-rolls and 30s A-roll:
    #   [A 0-10s] [B1 ~3s] [A 10-20s] [B2 ~3s] [A 20-30s]
    #
    # This avoids the rigid "every other 5s" pattern and feels more natural.

    timeline = []  # list of (source_type, file_path, src_start, src_end, timeline_start)
    n_brolls = len(b_roll_clips_info)

    if n_brolls == 0:
        # No B-roll: just use A-roll straight through
        cursor = 0.0
        for path, start, end in a_roll_segments:
            timeline.append(("aroll", path, start, end, cursor))
            cursor += (end - start)
    else:
        # Split A-roll into (n_brolls + 1) equal chunks
        n_chunks = n_brolls + 1
        chunk_dur = a_roll_total_duration / n_chunks

        cursor = 0.0
        a_cursor = 0.0
        b_idx = 0

        for chunk_i in range(n_chunks):
            # A-roll chunk
            a_chunk_dur = chunk_dur
            # Last chunk gets any remaining time
            if chunk_i == n_chunks - 1:
                a_chunk_dur = a_roll_total_duration - a_cursor

            if a_chunk_dur > 0.1:
                a_file, a_src_start = _resolve_aroll_position(a_roll_segments, a_cursor, a_chunk_dur)
                # Clamp to source file boundary
                a_src_end = min(a_src_start + a_chunk_dur, _get_file_end(a_roll_segments, a_file))
                actual_a_dur = a_src_end - a_src_start
                timeline.append(("aroll", a_file, a_src_start, a_src_end, cursor))
                cursor += actual_a_dur
                a_cursor += actual_a_dur

            # Insert B-roll after this A-roll chunk (except after the last chunk)
            if b_idx < n_brolls:
                b_path, b_start, b_end = b_roll_clips_info[b_idx]
                b_dur = b_end - b_start
                timeline.append(("broll", b_path, b_start, b_end, cursor))
                cursor += b_dur
                # Advance A-roll cursor by B-roll duration (audio continues)
                a_cursor += b_dur
                b_idx += 1

    logger.info("timeline built: %d segments, total %.2fs", len(timeline), cursor)

    # --- Step 4: Render each segment to temp file ---
    temp_files = []
    for i, (src_type, path, src_start, src_end, tl_start) in enumerate(timeline):
        try:
            clip = VideoFileClip(path).subclipped(src_start, src_end)
            clip = clip.without_audio()  # strip audio, we'll add A-roll audio separately
            clip = _resize_clip(clip, target_w, target_h)

            # Apply transition to B-roll insertions
            if src_type == "broll":
                clip = apply_transition(clip, video_transition)

            temp_path = os.path.join(output_dir, f"temp-seg-{i:04d}.mp4")
            clip.write_videofile(temp_path, logger=None, fps=FPS, codec=VIDEO_CODEC, audio=False)
            temp_files.append(temp_path)

            _close_clip(clip)
        except Exception as e:
            logger.error("failed to process clip: %s", str(e))

    # Loop processed clips if video is still shorter than audio
    if video_duration < audio_duration:
        logger.warning(
            "video duration (%.2fs) < audio duration (%.2fs), looping clips",
            video_duration, audio_duration,
        )
        base_clips = processed_clips.copy()
        for clip_item in itertools.cycle(base_clips):
            if video_duration >= audio_duration:
                break
            processed_clips.append(clip_item)
            video_duration += clip_item.duration
        logger.info(
            "after looping: video=%.2fs, audio=%.2fs, looped %d clips",
            video_duration, audio_duration,
            len(processed_clips) - len(base_clips),
        )

    # 6. Merge with FFmpeg concat demuxer
    logger.info("starting clip merging process")
    if not processed_clips:
        logger.warning("no clips available for merging")
        return combined_video_path

    if len(processed_clips) == 1:
        logger.info("using single clip directly")
        import shutil
        shutil.copy(processed_clips[0].file_path, combined_video_path)
        _delete_files([processed_clips[0].file_path])
        logger.info("video combining completed")
        return combined_video_path

    clip_files = [c.file_path for c in processed_clips]
    logger.info("concatenating %d clips with ffmpeg", len(clip_files))
    _concat_video_clips_with_ffmpeg(
        clip_files=clip_files,
        output_file=combined_video_path,
        threads=threads,
        output_dir=output_dir,
    )

    # 7. Clean up temp files (deduplicate in case of looped references)
    unique_temp_files = list(set(clip_files))
    _delete_files(unique_temp_files)

    logger.info("mixing complete: %s", combined_video_path)
    return combined_video_path


def _generate_subtitles(audio_path: str, ass_path: str, video_w: int, video_h: int) -> None:
    """Generate ASS subtitle file from audio.

    Strategy: Use faster-whisper if available and model is cached locally.
    Falls back to simple duration-based subtitle splitting from audio duration
    (placeholder text) if Whisper is unavailable.
    """
    logger.info("generating subtitles from audio: %s", audio_path)

    segments_data = []  # list of (start_seconds, end_seconds, text)

    # Try faster-whisper first
    try:
        from faster_whisper import WhisperModel
        # Only use if model is already cached (don't download)
        model = WhisperModel("base", device="cpu", compute_type="int8",
                             download_root=None)
        segments, info = model.transcribe(audio_path, language="zh", vad_filter=True)
        for seg in segments:
            text = seg.text.strip()
            if text:
                segments_data.append((seg.start, seg.end, text))
        logger.info("whisper transcription: %d segments", len(segments_data))
    except Exception as e:
        logger.warning("whisper unavailable (%s), trying ffmpeg speech detection", str(e)[:100])

        # Fallback: use ffmpeg silencedetect to find speech segments
        try:
            segments_data = _detect_speech_segments(audio_path)
            logger.info("speech detection: %d segments", len(segments_data))
        except Exception as e2:
            logger.warning("speech detection failed: %s", str(e2)[:100])

    if not segments_data:
        logger.warning("no subtitle segments generated")
        return

    # Build ASS file
    font_size = max(18, int(video_h * 0.028))
    margin_bottom = max(30, int(video_h * 0.06))

    ass_header = f"""[Script Info]
Title: Auto-generated subtitles
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,20,20,{margin_bottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for start, end, text in segments_data:
        start_ts = _seconds_to_ass_time(start)
        end_ts = _seconds_to_ass_time(end)
        events.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(events))
        f.write("\n")

    logger.info("subtitles written: %d events, saved to %s", len(events), ass_path)


def _detect_speech_segments(audio_path: str) -> list:
    """Use ffmpeg silencedetect to find non-silent segments, return placeholder text."""
    import json as _json

    # Get audio duration
    probe_cmd = [
        _get_ffmpeg_binary().replace("ffmpeg", "ffprobe"),
        "-v", "quiet", "-print_format", "json", "-show_format", audio_path,
    ]
    probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
    total_dur = 0.0
    if probe_result.returncode == 0:
        data = _json.loads(probe_result.stdout)
        total_dur = float(data.get("format", {}).get("duration", 0))

    if total_dur <= 0:
        return []

    # Use silencedetect to find silence boundaries
    cmd = [
        _get_ffmpeg_binary(),
        "-i", audio_path,
        "-af", "silencedetect=noise=-30dB:d=0.5",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    stderr = result.stderr or ""

    # Parse silence_start and silence_end from stderr
    import re
    silence_starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*([\d.]+)", stderr)]
    silence_ends = [float(m.group(1)) for m in re.finditer(r"silence_end:\s*([\d.]+)", stderr)]

    # Build speech segments (gaps between silences)
    segments = []
    prev_end = 0.0

    for i in range(len(silence_starts)):
        speech_start = prev_end
        speech_end = silence_starts[i]
        if speech_end - speech_start > 0.3:
            segments.append((speech_start, speech_end, ""))  # empty text placeholder
        if i < len(silence_ends):
            prev_end = silence_ends[i]

    # Last segment after final silence
    if prev_end < total_dur - 0.3:
        segments.append((prev_end, total_dur, ""))

    # If no silence detected, just return empty (no subtitles)
    return segments


def _seconds_to_ass_time(seconds: float) -> str:
    """Convert seconds to ASS timestamp format: H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _resolve_aroll_position(
    a_roll_segments: List[tuple],
    a_cursor: float,
    seg_dur: float,
) -> tuple:
    """Find which A-roll source file and offset corresponds to a_cursor position."""
    accumulated = 0.0
    for path, start, end in a_roll_segments:
        file_dur = end - start
        if accumulated + file_dur > a_cursor:
            offset_in_file = a_cursor - accumulated
            return (path, offset_in_file)
        accumulated += file_dur
    # Fallback: use last file
    path, start, end = a_roll_segments[-1]
    return (path, max(0, end - seg_dur))


def _get_file_end(a_roll_segments: List[tuple], file_path: str) -> float:
    """Get the end time (duration) of a specific A-roll file."""
    for path, start, end in a_roll_segments:
        if path == file_path:
            return end
    return 999999.0


# ---------------------------------------------------------------------------
# Audio extraction & BGM mixing (unchanged)
# ---------------------------------------------------------------------------

def extract_audio_from_videos(video_paths: List[str], output_path: str) -> float:
    """Extract and concatenate audio tracks from A-roll videos."""
    if not video_paths:
        raise ValueError("No video paths provided")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    audio_clips = []
    total_duration = 0.0

    try:
        for video_path in video_paths:
            video_clip = VideoFileClip(video_path)
            if video_clip.audio is not None:
                audio_clips.append(video_clip.audio)
                total_duration += video_clip.audio.duration
            else:
                logger.warning("no audio track in %s, skipping", video_path)
                _close_clip(video_clip)

        if not audio_clips:
            raise ValueError("No audio tracks found in provided videos")

        if len(audio_clips) == 1:
            final_audio = audio_clips[0]
        else:
            offset = 0.0
            offset_clips = []
            for ac in audio_clips:
                offset_clips.append(ac.with_start(offset))
                offset += ac.duration
            final_audio = CompositeAudioClip(offset_clips)

        final_audio.write_audiofile(output_path, logger=None)
        logger.info("extracted audio: %.2fs, saved to %s", total_duration, output_path)
    finally:
        for ac in audio_clips:
            try:
                _close_clip(ac)
            except Exception:
                pass
        gc.collect()

    return total_duration


def mix_bgm(
    main_audio_path: str,
    bgm_file: str,
    output_path: str,
    bgm_volume: float = 0.2,
    fade_out_duration: float = 3.0,
) -> str:
    """Mix background music with the main audio track."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    main_clip = AudioFileClip(main_audio_path)
    bgm_clip = AudioFileClip(bgm_file)

    try:
        bgm_processed = bgm_clip.with_effects([
            afx.MultiplyVolume(bgm_volume),
            afx.AudioLoop(duration=main_clip.duration),
            afx.AudioFadeOut(fade_out_duration),
        ])
        mixed = CompositeAudioClip([main_clip, bgm_processed])
        mixed.write_audiofile(output_path, logger=None)
        logger.info("BGM mixed: %.2fs, volume=%.2f", main_clip.duration, bgm_volume)
    finally:
        _close_clip(bgm_clip)
        _close_clip(main_clip)
        gc.collect()

    return output_path
