#!/usr/bin/env python3
"""Clone voice from reference video via ChatTTS and dub target video.

Usage:
  /opt/miniconda3/bin/python3.13 scripts/chattss_clone_dub.py \
    --task-dir "/Users/xxx/XeEdio/XeEdio/video-production-platform/storage/tasks/9dc1d5d8-1198-4cc9-8fca-965d0e83af1d"
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import numpy as np
import torch
import torchaudio

import ChatTTS


SEGMENTS = [
    (0.8, 3.5, "给你的手机换个“顶配”皮肤！"),
    (3.5, 7.0, "素皮背板搭配金属边框，上手就是旗舰质感。"),
    (7.0, 11.0, "镜头金属保护环，防刮耐磨还能防磕碰。"),
    (11.0, 15.5, "轻轻一拨秒变支架，横屏看剧超稳。"),
    (15.5, 19.5, "底部全包+精密防尘网，细节直接拉满。"),
    (19.5, 24.0, "这亲肤手感，真的让人“该死的沉溺”。"),
    (24.0, 28.5, "精准开孔不挡屏，戴上它瞬间与众不同。"),
    (28.5, 33.0, "要颜值有颜值，要保护有保护。"),
    (33.0, 36.5, "这质感谁懂啊？赶紧安排！"),
]


def run_ffmpeg_extract(src_video: Path, ref_wav: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src_video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "24000",
        "-t",
        "30",
        str(ref_wav),
    ]
    subprocess.run(cmd, check=True)


def load_reference_audio(path: Path, target_sr: int = 24000) -> np.ndarray:
    wav, sr = torchaudio.load(str(path))
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    if sr != target_sr:
        wav = torchaudio.functional.resample(wav, sr, target_sr)
    return wav.squeeze(0).numpy()


def fit_segment_to_duration(seg: np.ndarray, target_samples: int) -> np.ndarray:
    if target_samples <= 1:
        return seg[:1] if seg.size else np.zeros(1, dtype=np.float32)
    if len(seg) == 0:
        return np.zeros(target_samples, dtype=np.float32)
    if len(seg) == target_samples:
        return seg.astype(np.float32)
    if len(seg) == 1:
        return np.full(target_samples, seg[0], dtype=np.float32)
    # Always time-warp to requested duration. Padding short clips with zeros makes
    # narration sound like silence for most of the segment.
    x_old = np.linspace(0.0, 1.0, num=len(seg), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=target_samples, endpoint=False)
    return np.interp(x_new, x_old, seg).astype(np.float32)


def build_tts_track(
    ref_wav: Path,
    out_wav: Path,
    *,
    model_source: str = "local",
    model_path: Path | None = None,
    allow_fallback_speaker: bool = True,
) -> None:
    sr = 24000
    chat = ChatTTS.Chat()
    load_kwargs = {"source": model_source, "compile": False}
    if model_path is not None:
        load_kwargs["custom_path"] = str(model_path)
    ok = chat.load(**load_kwargs)
    if not ok:
        raise RuntimeError("ChatTTS model load failed. Check model download/network.")

    ref_np = load_reference_audio(ref_wav, target_sr=sr)
    spk_smp = chat.sample_audio_speaker(ref_np)
    fallback_spk_emb = chat.sample_random_speaker()
    params_refine = ChatTTS.Chat.RefineTextParams(prompt="[oral_2][break_3]")

    chunks: list[np.ndarray] = []
    cursor = 0.0
    for idx, (start, end, text) in enumerate(SEGMENTS, start=1):
        if start > cursor:
            chunks.append(np.zeros(int((start - cursor) * sr), dtype=np.float32))
            cursor = start

        wavs = chat.infer(
            [text],
            params_refine_text=params_refine,
            params_infer_code=ChatTTS.Chat.InferCodeParams(
                spk_smp=spk_smp,
                temperature=0.3,
                top_P=0.8,
                top_K=20,
            ),
        )
        seg = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        target_samples = max(1, int(round((end - start) * sr)))
        # In some environments the cloned-speaker path (spk_smp) can collapse to
        # a very short utterance, which sounds like silence after alignment.
        if allow_fallback_speaker and len(seg) < int(target_samples * 0.35):
            wavs = chat.infer(
                [text],
                params_refine_text=params_refine,
                params_infer_code=ChatTTS.Chat.InferCodeParams(
                    spk_emb=fallback_spk_emb,
                    temperature=0.3,
                    top_P=0.8,
                    top_K=20,
                ),
            )
            seg = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
            print(f"[warn] segment_{idx}: speaker-clone output too short, used fallback speaker")
        seg = fit_segment_to_duration(seg, target_samples)
        chunks.append(seg)
        cursor = end
        print(f"[ok] segment_{idx}: {start:.1f}-{end:.1f}s")

    audio = np.concatenate(chunks) if chunks else np.zeros(1, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak < 1e-6:
        raise RuntimeError("Generated narration is near-silent. Please retry synthesis.")
    # ChatTTS can occasionally output very low-amplitude waveforms. Normalize to
    # a stable, clearly audible peak so the exported track is not perceived silent.
    target_peak = 0.85
    audio = audio / peak * target_peak
    torchaudio.save(str(out_wav), torch.from_numpy(audio).unsqueeze(0), sr)
    print(f"[ok] wrote narration: {out_wav}")


def run_ffmpeg_mux(target_video: Path, tts_wav: Path, out_video: Path) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(target_video),
        "-i",
        str(tts_wav),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(out_video),
    ]
    subprocess.run(cmd, check=True)
    print(f"[ok] wrote dubbed video: {out_video}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-dir", required=True)
    parser.add_argument(
        "--model-source",
        default="local",
        choices=["local", "huggingface", "custom"],
        help="ChatTTS model source mode.",
    )
    parser.add_argument(
        "--model-path",
        default="",
        help="Custom model directory when using --model-source custom.",
    )
    parser.add_argument(
        "--clone-only",
        action="store_true",
        help="Force cloned speaker output from reference audio only (disable fallback speaker).",
    )
    args = parser.parse_args()

    task_dir = Path(args.task_dir).resolve()
    src_video = task_dir / "成片.mp4"
    target_video = task_dir / "output-1.mp4"
    ref_wav = task_dir / "chattss_ref.wav"
    tts_wav = (
        task_dir / "chattss_narration_clone.wav"
        if args.clone_only
        else task_dir / "chattss_narration.wav"
    )
    dubbed = (
        task_dir / "output-1_chattss_clone_dubbed.mp4"
        if args.clone_only
        else task_dir / "output-1_chattss_dubbed.mp4"
    )

    if not src_video.exists():
        raise FileNotFoundError(f"reference video not found: {src_video}")
    if not target_video.exists():
        raise FileNotFoundError(f"target video not found: {target_video}")

    model_path = Path(args.model_path).resolve() if args.model_path else None

    run_ffmpeg_extract(src_video, ref_wav)
    build_tts_track(
        ref_wav,
        tts_wav,
        model_source=args.model_source,
        model_path=model_path,
        allow_fallback_speaker=not args.clone_only,
    )
    run_ffmpeg_mux(target_video, tts_wav, dubbed)


if __name__ == "__main__":
    main()

