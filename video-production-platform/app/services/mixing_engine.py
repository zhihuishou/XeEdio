"""MoviePy mixing engine for smart video composition.

Core video processing logic based on MoneyPrinterTurbo's combine_videos function
and video_effects.py. Uses MoviePy for clip manipulation and FFmpeg concat demuxer
for final assembly.
"""

import gc
import itertools
import logging
import os
import random
import subprocess
from dataclasses import dataclass
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

# Encoding defaults (matching MoneyPrinterTurbo)
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"
VIDEO_CODEC = "libx264"
FPS = 30

# Aspect ratio to resolution mapping
ASPECT_RESOLUTIONS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}


@dataclass
class SubClippedVideoClip:
    """Tracks a segment of a source video file."""

    file_path: str
    start_time: float = 0.0
    end_time: float = 0.0
    width: int = 0
    height: int = 0
    duration: float = 0.0

    def __post_init__(self):
        if self.duration == 0.0 and self.end_time > self.start_time:
            self.duration = self.end_time - self.start_time


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

def _get_ffmpeg_binary() -> str:
    """Return the ffmpeg binary path, respecting IMAGEIO_FFMPEG_EXE env var."""
    return os.environ.get("IMAGEIO_FFMPEG_EXE") or "ffmpeg"


def _escape_ffmpeg_concat_path(file_path: str) -> str:
    """Escape single quotes for FFmpeg concat demuxer list files."""
    return file_path.replace("'", "'\\''")


def _concat_video_clips_with_ffmpeg(
    clip_files: List[str],
    output_file: str,
    threads: int,
    output_dir: str,
    audio_file: str = None,
) -> None:
    """Join processed clip files using FFmpeg concat demuxer and mux speech audio.

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
    ]
    
    if audio_file and os.path.exists(audio_file):
        command.extend(["-i", audio_file, "-map", "0:v:0", "-map", "1:a:0"])
    
    command.extend([
        "-c:v", VIDEO_CODEC,
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-threads", str(threads or 2),
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_file,
    ])

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
    """Safely close a MoviePy clip and release resources."""
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
            for child_clip in clip.clips:
                if child_clip is not clip:
                    _close_clip(child_clip)

        if hasattr(clip, "clips"):
            clip.clips = []
    except Exception as e:
        logger.error("failed to close clip: %s", str(e))

    del clip
    gc.collect()


def _delete_files(files) -> None:
    """Delete one or more files, ignoring errors."""
    if isinstance(files, str):
        files = [files]
    for file in files:
        try:
            os.remove(file)
        except Exception as e:
            logger.debug("failed to delete file %s: %s", file, str(e))


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
# Transition effects (based on MoneyPrinterTurbo video_effects.py)
# ---------------------------------------------------------------------------

def fadein_transition(clip, duration: float = 0.5):
    """Apply a fade-in from black transition."""
    return clip.with_effects([vfx.FadeIn(duration)])


def fadeout_transition(clip, duration: float = 0.5):
    """Apply a fade-out to black transition."""
    return clip.with_effects([vfx.FadeOut(duration)])


def slidein_transition(clip, duration: float = 0.5, side: str = "left"):
    """Apply a slide-in transition from the given side.

    Uses explicit position animation with a black background for reliable
    visual results (MoviePy's built-in SlideIn can be unstable on full-screen
    clips).
    """
    width, height = clip.size

    def position(current_time: float):
        progress = min(max(current_time / max(duration, 0.001), 0), 1)
        if side == "left":
            return (-width + width * progress, 0)
        if side == "right":
            return (width - width * progress, 0)
        if side == "top":
            return (0, -height + height * progress)
        if side == "bottom":
            return (0, height - height * progress)
        return (0, 0)

    background = ColorClip(
        size=(width, height), color=(0, 0, 0)
    ).with_duration(clip.duration)
    moving_clip = clip.with_position(position)
    return CompositeVideoClip(
        [background, moving_clip], size=(width, height)
    ).with_duration(clip.duration)


def slideout_transition(clip, duration: float = 0.5, side: str = "right"):
    """Apply a slide-out transition towards the given side."""
    width, height = clip.size
    transition_start = max(clip.duration - duration, 0)

    def position(current_time: float):
        if current_time <= transition_start:
            return (0, 0)
        progress = min(
            max((current_time - transition_start) / max(duration, 0.001), 0), 1
        )
        if side == "left":
            return (-width * progress, 0)
        if side == "right":
            return (width * progress, 0)
        if side == "top":
            return (0, -height * progress)
        if side == "bottom":
            return (0, height * progress)
        return (0, 0)

    background = ColorClip(
        size=(width, height), color=(0, 0, 0)
    ).with_duration(clip.duration)
    moving_clip = clip.with_position(position)
    return CompositeVideoClip(
        [background, moving_clip], size=(width, height)
    ).with_duration(clip.duration)


def apply_transition(clip, transition_type: str, duration: float = 1.0):
    """Dispatcher that applies the requested transition effect.

    For "shuffle" mode, randomly picks one of the four transition types.

    Args:
        clip: MoviePy video clip.
        transition_type: One of "none", "fade_in", "fade_out",
                         "slide_in", "slide_out", "shuffle".
        duration: Transition duration in seconds.

    Returns:
        Clip with transition applied.
    """
    if transition_type in (None, "none", ""):
        return clip

    shuffle_side = random.choice(["left", "right", "top", "bottom"])

    if transition_type == "fade_in":
        return fadein_transition(clip, duration)
    elif transition_type == "fade_out":
        return fadeout_transition(clip, duration)
    elif transition_type == "slide_in":
        return slidein_transition(clip, duration, shuffle_side)
    elif transition_type == "slide_out":
        return slideout_transition(clip, duration, shuffle_side)
    elif transition_type == "shuffle":
        transition_funcs = [
            lambda c: fadein_transition(c, duration),
            lambda c: fadeout_transition(c, duration),
            lambda c: slidein_transition(c, duration, shuffle_side),
            lambda c: slideout_transition(c, duration, shuffle_side),
        ]
        return random.choice(transition_funcs)(clip)

    logger.warning("unknown transition type '%s', skipping", transition_type)
    return clip


# ---------------------------------------------------------------------------
# Core mixing functions
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
) -> str:
    """Core mixing function — segments, resizes, transitions, and joins clips.

    Closely follows MoneyPrinterTurbo's combine_videos logic:
    1. Load audio to determine target duration.
    2. Split each source video into segments of max_clip_duration.
    3. Arrange segments (shuffle for random, first-segment-only for sequential).
    4. Process each segment: resize to target resolution, apply transition,
       write to temp file.
    5. Loop segments if total duration < audio duration.
    6. Use FFmpeg concat demuxer to join temp files into final video.
    7. Clean up temp files.

    Args:
        combined_video_path: Output file path.
        video_paths: List of source video file paths.
        audio_file: Audio file path (determines target duration).
        video_aspect: Target aspect ratio ("16:9", "9:16", or "1:1").
        video_concat_mode: "random" or "sequential".
        video_transition: Transition effect name.
        max_clip_duration: Maximum duration per clip segment in seconds.
        threads: Number of FFmpeg threads.

    Returns:
        Path to the combined output video.
    """
    # 1. Load audio and get duration
    audio_clip = AudioFileClip(audio_file)
    audio_duration = audio_clip.duration
    _close_clip(audio_clip)
    logger.info("audio duration: %.2f seconds", audio_duration)
    logger.info("maximum clip duration: %d seconds", max_clip_duration)

    output_dir = os.path.dirname(combined_video_path)
    os.makedirs(output_dir, exist_ok=True)

    # 2. Parse aspect ratio to resolution
    video_width, video_height = ASPECT_RESOLUTIONS.get(
        video_aspect, (1080, 1920)
    )

    # 3. Build subclipped segments from all source videos
    subclipped_items: List[SubClippedVideoClip] = []

    for video_path in video_paths:
        clip = VideoFileClip(video_path)
        clip_duration = clip.duration
        clip_w, clip_h = clip.size
        _close_clip(clip)

        start_time = 0.0
        while start_time < clip_duration:
            end_time = min(start_time + max_clip_duration, clip_duration)
            if end_time > start_time:
                subclipped_items.append(
                    SubClippedVideoClip(
                        file_path=video_path,
                        start_time=start_time,
                        end_time=end_time,
                        width=clip_w,
                        height=clip_h,
                    )
                )
            start_time = end_time
            # Sequential mode: only take the first segment per video
            if video_concat_mode == "sequential":
                break

    # 4. Shuffle if random mode
    if video_concat_mode == "random":
        random.shuffle(subclipped_items)

    logger.debug("total subclipped items: %d", len(subclipped_items))

    # 5. Process segments until video_duration >= audio_duration
    processed_clips: List[SubClippedVideoClip] = []
    video_duration = 0.0

    for i, item in enumerate(subclipped_items):
        if video_duration >= audio_duration:
            break

        logger.debug(
            "processing clip %d: %dx%d, current duration: %.2fs, remaining: %.2fs",
            i + 1, item.width, item.height,
            video_duration, audio_duration - video_duration,
        )

        try:
            clip = VideoFileClip(item.file_path).subclipped(
                item.start_time, item.end_time
            )
            clip_duration = clip.duration
            clip_w, clip_h = clip.size

            # Resize to target resolution (maintain aspect ratio, pad with black)
            if clip_w != video_width or clip_h != video_height:
                clip_ratio = clip_w / clip_h
                video_ratio = video_width / video_height

                if clip_ratio == video_ratio:
                    clip = clip.resized(new_size=(video_width, video_height))
                else:
                    if clip_ratio > video_ratio:
                        scale_factor = video_width / clip_w
                    else:
                        scale_factor = video_height / clip_h

                    new_width = int(clip_w * scale_factor)
                    new_height = int(clip_h * scale_factor)

                    background = ColorClip(
                        size=(video_width, video_height), color=(0, 0, 0)
                    ).with_duration(clip_duration)
                    clip_resized = clip.resized(
                        new_size=(new_width, new_height)
                    ).with_position("center")
                    clip = CompositeVideoClip([background, clip_resized])

            # Apply transition effect
            clip = apply_transition(clip, video_transition)

            # Enforce max clip duration after transition
            if clip.duration > max_clip_duration:
                clip = clip.subclipped(0, max_clip_duration)

            # Write to temp file
            clip_file = os.path.join(output_dir, f"temp-clip-{i + 1}.mp4")
            clip.write_videofile(
                clip_file, logger=None, fps=FPS, codec=VIDEO_CODEC
            )

            clip_duration_saved = clip.duration
            _close_clip(clip)

            processed_clips.append(
                SubClippedVideoClip(
                    file_path=clip_file,
                    duration=clip_duration_saved,
                    width=video_width,
                    height=video_height,
                )
            )
            video_duration += clip_duration_saved

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

    clip_files = [c.file_path for c in processed_clips]
    logger.info("concatenating %d clips with ffmpeg and replacing audio", len(clip_files))
    _concat_video_clips_with_ffmpeg(
        clip_files=clip_files,
        output_file=combined_video_path,
        threads=threads,
        output_dir=output_dir,
        audio_file=audio_file,
    )

    # 7. Clean up temp files (deduplicate in case of looped references)
    unique_temp_files = list(set(clip_files))
    _delete_files(unique_temp_files)

    logger.info("video combining completed")
    return combined_video_path


def _resolve_aroll_position(a_roll_segments, a_cursor, seg_dur):
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


def _get_file_end(a_roll_segments, file_path):
    """Get the end time (duration) of a specific A-roll file."""
    for path, start, end in a_roll_segments:
        if path == file_path:
            return end
    return 999999.0


def execute_timeline(
    timeline: list,
    a_roll_paths: List[str],
    b_roll_paths: List[str],
    audio_file: str,
    output_path: str,
    video_aspect: str = "9:16",
    video_transition: str = "none",
    threads: int = 2,
) -> str:
    """Execute a VLM-generated timeline to produce the final video.

    Iterates over timeline entries, extracts A-roll/B-roll segments,
    concatenates via FFmpeg, and merges with audio.
    """
    import itertools

    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)
    target_w, target_h = ASPECT_RESOLUTIONS.get(video_aspect, (1080, 1920))

    # Build A-roll segments for position lookup
    a_roll_segments = []
    for path in a_roll_paths:
        clip = VideoFileClip(path)
        dur = clip.duration
        _close_clip(clip)
        a_roll_segments.append((path, 0.0, dur))

    # B-roll cycle
    b_cycle = itertools.cycle(b_roll_paths) if b_roll_paths else iter([])

    temp_files = []
    for i, entry in enumerate(timeline):
        entry_type = entry.get("type", "a_roll")
        start = float(entry.get("start", 0))
        end = float(entry.get("end", start + 1))
        seg_dur = end - start

        if seg_dur <= 0.05:
            continue

        try:
            if entry_type == "a_roll":
                # Find the right A-roll file and offset for this time range
                a_file, a_offset = _resolve_aroll_position(a_roll_segments, start, seg_dur)
                a_clip = VideoFileClip(a_file)
                actual_end = min(a_offset + seg_dur, a_clip.duration)
                if actual_end <= a_offset:
                    _close_clip(a_clip)
                    logger.warning("timeline entry %d: A-roll offset %.2f beyond file duration %.2f, skipping", i, a_offset, a_clip.duration)
                    continue
                clip = a_clip.subclipped(a_offset, actual_end)
            else:
                b_path = next(b_cycle, None)
                if not b_path:
                    logger.warning("timeline entry %d: no B-roll available, skipping", i)
                    continue
                b_clip = VideoFileClip(b_path)
                use_dur = min(seg_dur, b_clip.duration)
                clip = b_clip.subclipped(0, use_dur)

            clip = clip.without_audio()
            clip = _resize_clip(clip, target_w, target_h)

            if entry_type == "b_roll":
                clip = apply_transition(clip, video_transition)

            temp_path = os.path.join(output_dir, f"tl-seg-{i:04d}.mp4")
            clip.write_videofile(temp_path, logger=None, fps=FPS, codec=VIDEO_CODEC, audio=False)
            temp_files.append(temp_path)
            _close_clip(clip)
        except Exception as e:
            logger.error("timeline segment %d (%s %.2f-%.2f) failed: %s", i, entry_type, start, end, str(e))

    if not temp_files:
        raise RuntimeError("No timeline segments rendered")

    # Concat video segments
    video_only = os.path.join(output_dir, "tl-video-only.mp4")
    concat_list = os.path.join(output_dir, "tl-concat.txt")
    with open(concat_list, "w") as f:
        for tf in temp_files:
            f.write(f"file '{os.path.abspath(tf)}'\n")

    cmd = [_get_ffmpeg_binary(), "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
           "-c:v", VIDEO_CODEC, "-pix_fmt", "yuv420p", "-an", video_only]
    subprocess.run(cmd, capture_output=True, text=True, check=False)

    # Merge with audio
    cmd2 = [_get_ffmpeg_binary(), "-y", "-i", video_only, "-i", audio_file,
            "-c:v", "copy", "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
            "-shortest", "-movflags", "+faststart", output_path]
    subprocess.run(cmd2, capture_output=True, text=True, check=False)

    _delete_files(temp_files + [video_only, concat_list])
    logger.info("timeline execution complete: %s", output_path)
    return output_path


def _generate_subtitles(audio_path: str, ass_path: str, video_w: int, video_h: int) -> None:
    """Generate subtitles from audio using VideoCaptioner (bijian ASR, free cloud).

    Falls back to local faster-whisper if VideoCaptioner fails.
    """
    import re as _re

    logger.info("generating subtitles from audio: %s", audio_path)

    # Output as SRT (VideoCaptioner default), then convert to ASS
    srt_path = ass_path.replace(".ass", ".srt")

    # Strategy 1: VideoCaptioner CLI with bijian (free cloud ASR)
    try:
        cmd = [
            "videocaptioner", "transcribe", audio_path,
            "--asr", "bijian",
            "-o", srt_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if result.returncode == 0 and os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
            logger.info("VideoCaptioner transcription succeeded: %s", srt_path)
            _srt_to_ass(srt_path, ass_path, video_w, video_h)
            return
        else:
            logger.warning("VideoCaptioner failed (rc=%d): %s", result.returncode, (result.stderr or "")[:200])
    except FileNotFoundError:
        logger.warning("videocaptioner CLI not found, falling back to whisper")
    except subprocess.TimeoutExpired:
        logger.warning("VideoCaptioner timed out after 300s")
    except Exception as e:
        logger.warning("VideoCaptioner error: %s", str(e)[:200])

    # Strategy 2: Local faster-whisper fallback
    segments_data = []
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8", download_root=None)
        segments, info = model.transcribe(
            audio_path, language="zh", vad_filter=True, word_timestamps=True
        )
        current_text = ""
        current_start = 0.0
        current_end = 0.0
        MAX_CHARS = 15
        MAX_DURATION = 4.0

        for seg in segments:
            if seg.words:
                for word in seg.words:
                    w_text = word.word.strip()
                    if not w_text:
                        continue
                    if not current_text:
                        current_start = word.start
                    current_text += w_text
                    current_end = word.end
                    should_split = (
                        len(current_text) >= MAX_CHARS
                        or (current_end - current_start) >= MAX_DURATION
                        or w_text[-1] in "。！？!?，,、"
                    )
                    if should_split and current_text.strip():
                        segments_data.append((current_start, current_end, current_text.strip()))
                        current_text = ""
                        current_start = 0.0
            else:
                text = seg.text.strip()
                if text:
                    segments_data.append((seg.start, seg.end, text))

        if current_text.strip():
            segments_data.append((current_start, current_end, current_text.strip()))
    except Exception as e:
        logger.warning("whisper fallback failed: %s", str(e)[:100])

    if not segments_data:
        logger.warning("no subtitles generated")
        return

    # Write ASS file
    _write_ass_file(segments_data, ass_path, video_w, video_h)


def _srt_to_ass(srt_path: str, ass_path: str, video_w: int, video_h: int) -> None:
    """Convert SRT subtitle file to ASS format."""
    import re as _re
    segments = []
    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Parse SRT blocks
        blocks = _re.split(r'\n\n+', content.strip())
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) >= 3:
                # Line 2: timestamps  00:00:01,000 --> 00:00:03,500
                ts_match = _re.match(r'(\d+:\d+:\d+[,\.]\d+)\s*-->\s*(\d+:\d+:\d+[,\.]\d+)', lines[1])
                if ts_match:
                    start = _srt_time_to_seconds(ts_match.group(1))
                    end = _srt_time_to_seconds(ts_match.group(2))
                    text = ' '.join(lines[2:]).strip()
                    if text:
                        segments.append((start, end, text))
    except Exception as e:
        logger.error("SRT parse failed: %s", str(e)[:200])
        return

    if segments:
        _write_ass_file(segments, ass_path, video_w, video_h)


def _srt_time_to_seconds(ts: str) -> float:
    """Convert SRT timestamp (HH:MM:SS,mmm) to seconds."""
    ts = ts.replace(',', '.')
    parts = ts.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _write_ass_file(segments_data: list, ass_path: str, video_w: int, video_h: int) -> None:
    """Write ASS subtitle file from segment data."""
    font_size = max(18, int(video_h * 0.028))
    margin_bottom = max(30, int(video_h * 0.06))

    header = f"""[Script Info]
Title: Auto subtitles
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,20,20,{margin_bottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for s, e, t in segments_data:
        events.append(f"Dialogue: 0,{_seconds_to_ass_time(s)},{_seconds_to_ass_time(e)},Default,,0,0,0,,{t}")

    os.makedirs(os.path.dirname(ass_path) or ".", exist_ok=True)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")
    logger.info("ASS subtitles written: %d segments -> %s", len(events), ass_path)
    for s, e, t in segments_data:
        events.append(f"Dialogue: 0,{_seconds_to_ass_time(s)},{_seconds_to_ass_time(e)},Default,,0,0,0,,{t}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")


def _seconds_to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_subtitles_from_script(script_text: str, audio_duration: float, ass_path: str, video_w: int, video_h: int) -> None:
    """Generate ASS subtitles from script text."""
    import re as _re

    raw = _re.split(r'[。！？!?\n]+', script_text)
    segments = [s.strip() for s in raw if s.strip()]
    if not segments:
        return

    seg_dur = audio_duration / len(segments)
    font_size = max(18, int(video_h * 0.028))
    margin_bottom = max(30, int(video_h * 0.06))

    header = f"""[Script Info]
Title: Script subtitles
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,2,20,20,{margin_bottom},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for i, text in enumerate(segments):
        s = i * seg_dur
        e = (i + 1) * seg_dur
        events.append(f"Dialogue: 0,{_seconds_to_ass_time(s)},{_seconds_to_ass_time(e)},Default,,0,0,0,,{text}")

    os.makedirs(os.path.dirname(ass_path) or ".", exist_ok=True)
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")


def burn_subtitles(video_path: str, ass_path: str, output_path: str) -> str:
    """Burn subtitles into video using FFmpeg subtitles filter.

    Tries SRT with subtitles filter first, falls back to drawtext, then copy.
    """
    srt_path = ass_path.replace(".ass", ".srt")
    _ass_to_srt(ass_path, srt_path)

    subtitle_file = srt_path if os.path.exists(srt_path) and os.path.getsize(srt_path) > 0 else ass_path
    ffmpeg_bin = _get_ffmpeg_binary()

    # Strategy 1: FFmpeg subtitles filter (requires libass — conda-forge ffmpeg has it)
    cmd = [
        ffmpeg_bin, "-y",
        "-i", video_path,
        "-vf", f"subtitles={subtitle_file}",
        "-c:v", "libx264", "-b:v", "8M",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    logger.info("burning subtitles via FFmpeg subtitles filter: %s", subtitle_file)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        logger.info("subtitles burned successfully")
        return output_path

    logger.warning("subtitles filter failed (rc=%d): %s", result.returncode, (result.stderr or "")[-200:])

    # Strategy 2: Copy without subtitles
    logger.warning("subtitle burn failed, copying without subtitles")
    import shutil
    shutil.copy(video_path, output_path)
    return output_path


def _ass_to_srt(ass_path: str, srt_path: str) -> None:
    """Convert ASS to SRT format for VideoCaptioner."""
    events = _parse_ass_events(ass_path)
    if not events:
        return
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(events, 1):
            f.write(f"{i}\n")
            f.write(f"{_seconds_to_srt_time(start)} --> {_seconds_to_srt_time(end)}\n")
            f.write(f"{text}\n\n")


def _seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def _parse_ass_events(ass_path: str) -> list:
    """Parse ASS file and return list of (start_seconds, end_seconds, text)."""
    import re
    events = []
    try:
        with open(ass_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("Dialogue:"):
                    # Format: Dialogue: 0,H:MM:SS.cc,H:MM:SS.cc,Style,,0,0,0,,Text
                    parts = line.split(",", 9)
                    if len(parts) >= 10:
                        start_ts = parts[1].strip()
                        end_ts = parts[2].strip()
                        text = parts[9].strip()
                        if text:
                            events.append((
                                _ass_time_to_seconds(start_ts),
                                _ass_time_to_seconds(end_ts),
                                text,
                            ))
    except Exception as e:
        logger.error("failed to parse ASS file %s: %s", ass_path, str(e))
    return events


def _ass_time_to_seconds(ts: str) -> float:
    """Convert ASS timestamp H:MM:SS.cc to seconds."""
    try:
        parts = ts.split(":")
        h = int(parts[0])
        m = int(parts[1])
        s_parts = parts[2].split(".")
        s = int(s_parts[0])
        cs = int(s_parts[1]) if len(s_parts) > 1 else 0
        return h * 3600 + m * 60 + s + cs / 100.0
    except Exception:
        return 0.0


def extract_audio_from_videos(
    video_paths: List[str], output_path: str
) -> float:
    """Extract and concatenate audio tracks from multiple videos.

    Args:
        video_paths: List of video file paths to extract audio from.
        output_path: Output audio file path (mp3).

    Returns:
        Total duration of the concatenated audio in seconds.
    """
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
            # Concatenate audio clips sequentially by offsetting start times
            offset = 0.0
            offset_clips = []
            for ac in audio_clips:
                offset_clips.append(ac.with_start(offset))
                offset += ac.duration
            final_audio = CompositeAudioClip(offset_clips)

        final_audio.write_audiofile(output_path, logger=None)
        logger.info(
            "extracted audio: %.2fs total, saved to %s",
            total_duration, output_path,
        )
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
    """Mix background music into a video or audio file using FFmpeg.

    Uses FFmpeg's amix filter to blend BGM with the main audio track.
    Works with both .mp4 video files and .mp3 audio files as input.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Use FFmpeg to mix audio tracks directly
    # This avoids MoviePy's write_audiofile codec issues with .mp4 files
    cmd = [
        _get_ffmpeg_binary(), "-y",
        "-i", main_audio_path,
        "-i", bgm_file,
        "-filter_complex",
        f"[1:a]volume={bgm_volume},afade=t=out:st=0:d={fade_out_duration}[bgm];"
        f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3[out]",
        "-map", "0:v?",  # copy video stream if present
        "-map", "[out]",
        "-c:v", "copy",
        "-c:a", AUDIO_CODEC,
        "-b:a", AUDIO_BITRATE,
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("BGM mix failed: %s", (result.stderr or "")[-300:])
        raise RuntimeError(f"BGM mixing failed: {(result.stderr or '')[-200:]}")

    logger.info("BGM mixed: volume=%.2f, output=%s", bgm_volume, output_path)
    return output_path
