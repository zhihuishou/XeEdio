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

    logger.info("video combining completed")
    return combined_video_path


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
    """Mix background music with the main audio track.

    The BGM is looped to match the main audio duration, volume-adjusted,
    and faded out at the end.

    Args:
        main_audio_path: Path to the main audio file.
        bgm_file: Path to the BGM audio file.
        output_path: Output path for the mixed audio.
        bgm_volume: Volume multiplier for BGM (0.0 to 1.0).
        fade_out_duration: Fade-out duration at the end in seconds.

    Returns:
        Path to the mixed audio file.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    main_clip = AudioFileClip(main_audio_path)
    bgm_clip = AudioFileClip(bgm_file)

    try:
        # Loop BGM to match main audio duration, adjust volume, fade out
        bgm_processed = bgm_clip.with_effects([
            afx.MultiplyVolume(bgm_volume),
            afx.AudioLoop(duration=main_clip.duration),
            afx.AudioFadeOut(fade_out_duration),
        ])

        # Mix main audio and processed BGM
        mixed = CompositeAudioClip([main_clip, bgm_processed])
        mixed.write_audiofile(output_path, logger=None)

        logger.info(
            "BGM mixed: main=%.2fs, bgm_volume=%.2f, output=%s",
            main_clip.duration, bgm_volume, output_path,
        )
    finally:
        _close_clip(bgm_clip)
        _close_clip(main_clip)
        gc.collect()

    return output_path
