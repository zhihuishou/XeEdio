import os
import glob
import json
from moviepy import VideoFileClip, AudioFileClip, ColorClip, CompositeVideoClip
from moviepy import concatenate_videoclips

def resize_and_pad(clip, target_width, target_height):
    """等比例缩放并居中到目标分辨率，四周黑边"""
    clip_w, clip_h = clip.size
    clip_ratio = clip_w / clip_h
    video_ratio = target_width / target_height

    if clip_ratio == video_ratio:
        return clip.resized(new_size=(target_width, target_height))
    
    if clip_ratio > video_ratio:
        scale_factor = target_width / clip_w
    else:
        scale_factor = target_height / clip_h

    new_width = int(clip_w * scale_factor)
    new_height = int(clip_h * scale_factor)
    
    # 强制将尺寸调整为偶数，避免 FFmpeg x264 编码报错
    new_width = new_width - (new_width % 2)
    new_height = new_height - (new_height % 2)

    background = ColorClip(size=(target_width, target_height), color=(0, 0, 0)).with_duration(clip.duration)
    clip_resized = clip.resized(new_size=(new_width, new_height)).with_position("center")
    
    return CompositeVideoClip([background, clip_resized])

def run_smart_mix_usecase():
    print("🎬 开始跑通 AI 智能编导师端到端合成用例...")
    
    a_roll_path = r"E:\X\XeEdio\video-production-platform\storage\assets\388e125f2bdb6cdd772d12629bce46c7.mp4"
    if not os.path.exists(a_roll_path):
        print(f"Error: A-roll not found {a_roll_path}")
        return

    b_roll_paths = [
        r"E:\X\XeEdio\video-production-platform\storage\assets\bd9ceef0b5f41d06cd757d472dfa4e89.mp4",
        r"E:\X\XeEdio\video-production-platform\storage\assets\e2bbf3fb7a38f7930c0cc9af2bedb9e7.mp4"
    ]
    
    print(f"🎥 A-roll (主要口播): {a_roll_path[-50:]}")
    print(f"🎥 B-roll (多视角): {[b[-50:] for b in b_roll_paths]}")
    
    # === 接入大模型获取分镜 JSON ===
    from test_ai_director import extract_frames, ask_ai_director
    
    frames_dir = os.path.join(os.path.dirname(a_roll_path), "frames")
    encoded_frames = extract_frames(a_roll_path, frames_dir, fps=0.5)
    
    if encoded_frames and len(encoded_frames) > 20: 
        encoded_frames = encoded_frames[:20]

    # 达人口播大概的预设文案
    dummy_text = "嗨大家好！今天我要给大家推荐一款我手上一直在用的超级好物！它可以完美融入大自然，让我们生活更加清新。快来看看这精彩的部分吧！"
    print("🧠 正在请求大模型生成剪辑剧本...")
    vlm_resp = ask_ai_director(encoded_frames, dummy_text)
    
    if not vlm_resp:
        print("大模型请求失败，退出！")
        return
        
    import re
    try:
        # 去除 markdown block
        json_str = vlm_resp
        if "```json" in vlm_resp:
            json_str = vlm_resp.split("```json")[1].split("```")[0].strip()
        vlm_timeline = json.loads(json_str)
    except json.JSONDecodeError:
        print("大模型返回的不是合法 JSON，无法拼流！")
        return
    
    target_fps = 30
    target_width, target_height = 1080, 1920
    
    final_clips = []
    
    import itertools
    b_roll_cycle = itertools.cycle(b_roll_paths)
    
    clip_a = VideoFileClip(a_roll_path)
    
    # 构建时间线切片
    for node in vlm_timeline:
        t_type = node["type"]
        start_t = float(node["start"])
        end_t = float(node["end"])
        duration = end_t - start_t
        print(f"🎞️ 切片加工: {t_type} \t | 时长: {duration}s \t | 原因: {node.get('reason', '')}")
        
        # 为了防止 A/B-roll 长度不够，做安全截断或循环
        if t_type == "a_roll":
            safe_start = min(start_t, clip_a.duration - 0.1)
            safe_end = min(end_t, clip_a.duration)
            if safe_end <= safe_start:
                safe_end = safe_start + 1.0 # 强制至少 1s 兜底
            segment = clip_a.subclipped(safe_start, safe_end)
        else:
            # 取下一个 B-roll 素材
            curr_b_path = next(b_roll_cycle)
            clip_b = VideoFileClip(curr_b_path)
            safe_start = 0
            safe_end = min(duration, clip_b.duration)
            segment = clip_b.subclipped(safe_start, safe_end)
        
        segment_padded = resize_and_pad(segment, target_width, target_height)
        final_clips.append(segment_padded)

    print("🧩 正在拼接合成智能分镜序列...")
    merged_video = concatenate_videoclips(final_clips)
    
    # 为合并后的视频加上一份生成的假录音 (或者保持现有的片段原声)
    # 为保持简单，我们直接在这个 POC 里面生成视频
    output_path = r"E:\X\XeEdio\video-production-platform\storage\tasks\smart_director_output_final.mp4"
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    print(f"💾 开始导出最终视频: {output_path}")
    merged_video.write_videofile(
        output_path, 
        fps=target_fps,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        logger="bar"
    )
    
    clip_a.reader.close()
    if clip_a.audio: clip_a.audio.reader.close()
    clip_b.reader.close()
    if clip_b.audio: clip_b.audio.reader.close()
    
    print("✅ 智能编导直出视频生成完毕！")

if __name__ == "__main__":
    run_smart_mix_usecase()
