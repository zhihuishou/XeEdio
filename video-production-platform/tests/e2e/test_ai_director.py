import os
import subprocess
import base64
import httpx
import json

# API Configuration
API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
API_KEY = "sk-92ae3f703c814454a5537ab42c860f3d"
MODEL = "qwen3-vl-plus"

def extract_frames(video_path: str, output_dir: str, fps: float = 0.5):
    """提取视频帧 (默认 0.5fps，即每两秒提取一帧以免上下文过载)。"""
    os.makedirs(output_dir, exist_ok=True)
    out_pattern = os.path.join(output_dir, "frame_%04d.jpg")
    print(f"Extracting frames from {video_path}...")
    
    import imageio_ffmpeg
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    
    cmd = [
        ffmpeg_exe, "-y", "-i", video_path, 
        "-vf", f"fps={fps},scale=512:-1", # 缩放减小体积
        "-q:v", "2", out_pattern
    ]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print("FFMPEG ERROR:")
        print(result.stderr)
    
    frames = []
    for f in sorted(os.listdir(output_dir)):
        if f.endswith(".jpg"):
            with open(os.path.join(output_dir, f), "rb") as image_file:
                encoded = base64.b64encode(image_file.read()).decode("utf-8")
                frames.append(encoded)
    print(f"Extracted {len(frames)} frames.")
    return frames

def ask_ai_director(frames, text):
    """向大模型提问，获取 JSON 分镜脚本。"""
    system_prompt = (
        "你是一名为短视频自动剪辑系统服务的『AI 智能编导』。\n"
        "我将提供主视频（A-Roll）每 2 秒提取的一帧连续画面，以及背景朗读的声音文案。\n"
        "请根据画面内容和文案的语境，决策何时插入 B-Roll 空镜。\n"
        "规则：\n"
        "1. 如果画面中人物正在展示特定商品、做重点手势，绝对不能覆盖 B-Roll。\n"
        "2. 如果画面处于单调的口播状态，且文案提到具体的概念、事物或情感转移时，可以插入 3~5 秒的 B-Roll。\n"
        "3. 只输出原生的 JSON 数组，无需其他废话。格式如下：\n"
        "[\n"
        "  {\"type\": \"a_roll\", \"start\": 0, \"end\": 4, \"reason\": \"开场展示\"},\n"
        "  {\"type\": \"b_roll\", \"start\": 4, \"end\": 7, \"reason\": \"文案空洞期插入关联空镜\"}\n"
        "]\n"
    )
    
    content_list = [{"type": "text", "text": f"背景配音文案：{text}\n以下是 A-Roll 视频按时间轴顺序的帧图："}]
    
    for idx, f in enumerate(frames):
        content_list.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{f}",
                "detail": "low"
            }
        })
        
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_list}
        ],
        "temperature": 0.2,
        "max_tokens": 1000
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    print("Sending request to VLM API...")
    resp = httpx.post(API_URL, json=payload, headers=headers, timeout=60.0)
    
    if resp.status_code != 200:
        print(f"API Error: {resp.status_code}")
        print(resp.text)
        return None
        
    result = resp.json()["choices"][0]["message"]["content"]
    print("VLM Director API Response:")
    print(result)
    return result

if __name__ == "__main__":
    import glob
    assets = glob.glob(r"E:\X\XeEdio\video-production-platform\storage\assets\*\original.mp4")
    test_video = None
    for a in assets:
        if os.path.getsize(a) > 1000000: # > 1MB means real video
            test_video = a
            break
            
    if not test_video:
        print("No valid video found!")
        exit(1)
            
    frames_dir = os.path.join(os.path.dirname(test_video), "frames")
    
    # 模拟 TTS 配音文本
    dummy_text = "这是一段非常无聊的开场口播。接下来我们要探讨一下大自然的迷人风景，比如参天大树和阳光。然后我们回到演播室看这个新出来的产品。"
    
    encoded_frames = extract_frames(test_video, frames_dir, fps=0.5)
    
    if encoded_frames:
        if len(encoded_frames) > 20: 
            encoded_frames = encoded_frames[:20] # 防止上下文爆炸
        ask_ai_director(encoded_frames, dummy_text)
