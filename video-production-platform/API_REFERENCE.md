# XeEdio Video Production Platform — API Reference

Base URL: `http://localhost:8000`

## 认证

所有 API（除登录）需要 JWT Token：
```
Authorization: Bearer <token>
```

### POST /api/auth/login
```json
// Request
{"username": "admin", "password": "admin123"}

// Response 200
{"access_token": "eyJ...", "username": "admin", "role": "admin"}
```

---

## 素材管理

### POST /api/assets/upload
上传素材文件（Admin）。`multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | File | ✅ | 视频/图片/音频文件 |
| category | string | ✅ | `talent_speaking` / `product` / `pexels_broll` |

### GET /api/assets
素材列表（分页）

| 参数 | 默认 | 说明 |
|------|------|------|
| category | - | 按分类筛选 |
| keyword | - | 按文件名搜索 |
| page | 1 | 页码 |
| page_size | 20 | 每页数量 |

### DELETE /api/assets/{id}
删除素材（Admin）。返回 204。

---

## 智能混剪（核心）

### POST /api/mix/create
创建混剪任务。根据 `mixing_mode` 走不同管线。

```json
{
  "topic": "产品推荐视频",
  "mixing_mode": "pure_mix",
  "a_roll_asset_ids": ["asset-id-1"],
  "b_roll_asset_ids": ["asset-id-2", "asset-id-3"],
  "asset_ids": [],
  "aspect_ratio": "9:16",
  "transition": "fade_in",
  "clip_duration": 5,
  "concat_mode": "random",
  "video_count": 1,
  "max_output_duration": 60,
  "tts_text": null,
  "tts_voice": null,
  "bgm_enabled": false,
  "bgm_asset_id": null,
  "bgm_volume": 0.2,
  "director_prompt": "多插入产品特写，前10秒不要切换画面"
}
```

#### 三种模式参数对照

| 字段 | pure_mix | mix_with_script | broll_voiceover |
|------|----------|----------------|-----------------|
| a_roll_asset_ids | ✅ 必填 | ✅ 必填 | ❌ 空数组 |
| b_roll_asset_ids | 可选 | 可选 | ❌ 空数组 |
| asset_ids | ❌ 空数组 | ❌ 空数组 | ✅ 必填 |
| tts_text | ❌ null | ✅ 必填 | ✅ 必填 |
| tts_voice | ❌ | 可选 | 可选 |
| director_prompt | 可选 | 可选 | ❌ 无效 |
| clip_duration | ❌ VLM决定 | ❌ VLM决定 | ✅ 有效 |
| concat_mode | ❌ VLM决定 | ❌ VLM决定 | ✅ random/sequential |
| max_output_duration | ✅ | ✅ | ❌ TTS时长决定 |

#### 响应
```json
{"task_id": "uuid", "status": "processing", "message": "混剪任务已创建"}
```

### GET /api/mix/{task_id}/status
查询任务状态。轮询直到 `status != "processing"`。

```json
{
  "task_id": "uuid",
  "status": "video_done",
  "progress": "处理完成",
  "video_paths": ["storage/tasks/uuid/output-1.mp4"],
  "video_resolution": "1080x1920",
  "video_duration": 48.5,
  "video_file_size": 25000000,
  "error_message": null,
  "ai_director_used": true
}
```

| status 值 | 说明 |
|-----------|------|
| processing | 处理中 |
| video_done | 完成，可预览 |
| pending_review | 已提交审核 |
| approved | 审核通过 |
| rejected | 审核拒绝 |
| failed | 失败 |

### POST /api/mix/{task_id}/submit-review
提交审核（status 必须为 video_done）

### POST /api/mix/{task_id}/retry
重试失败/被拒绝的任务

---

## AI 辅助

### GET /api/mix/voices
获取可用 AI 口播音色列表

```json
{
  "voices": [
    {"id": "longyan_v2", "name": "妍妍", "gender": "女", "preview_url": "/static/voice_previews/longyan_v2.mp3"},
    {"id": "longyingtian", "name": "甜甜", "gender": "女", "preview_url": "/static/voice_previews/longyingtian.mp3"}
  ]
}
```

### POST /api/mix/keywords/generate
LLM 生成 B-roll 搜索关键词

```json
// Request
{"topic": "保湿面霜推荐"}

// Response
{"keywords": ["skincare routine", "moisturizer application", "beauty product"]}
```

### POST /api/mix/pexels/search
搜索 Pexels 免费视频素材

```json
// Request
{"keywords": ["skincare"], "aspect_ratio": "9:16", "per_page": 10}

// Response
{"videos": [{"url": "...", "thumbnail_url": "...", "duration": 15, "width": 1080, "height": 1920}], "total": 5}
```

### POST /api/mix/pexels/download
下载 Pexels 视频到素材库

```json
// Request
{"video_url": "https://..."}

// Response
{"asset_id": "uuid", "file_path": "storage/assets/uuid/original.mp4"}
```

---

## 配置

### GET /api/config/llm-providers
获取可用 LLM 模型列表（不含 API Key）

### GET /api/config （Admin）
获取系统配置

### PUT /api/config （Admin）
更新系统配置

---

## 用户管理（Admin）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/users | 用户列表 |
| POST | /api/users | 创建用户 |
| PUT | /api/users/{id} | 更新用户 |
| DELETE | /api/users/{id} | 删除用户 |

---

## 审核

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/reviews/pending | 待审核列表 |
| POST | /api/reviews/{task_id}/approve | 通过 |
| POST | /api/reviews/{task_id}/reject | 拒绝（需 reason） |

---

## 底层架构

### 混剪管线

```
pure_mix:
  A-roll音频提取 → Whisper转录 → VLM抽帧分析 → JSON时间线 → MoviePy执行 → 字幕烧录

mix_with_script:
  CosyVoice TTS → VLM抽帧分析 → JSON时间线 → MoviePy执行 → 脚本字幕烧录

broll_voiceover:
  CosyVoice TTS → 素材切片拼接(MoneyPrinterTurbo逻辑) → 脚本字幕烧录
```

### 降级策略

| 服务 | 主方案 | 降级 |
|------|--------|------|
| VLM | api.luxee.ai gpt-5.4 | 均匀分布盲切 |
| AI TTS | 阿里云 CosyVoice | Edge-TTS |
| 字幕识别 | VideoCaptioner bijian | faster-whisper → 无字幕 |
| 字幕烧录 | FFmpeg subtitles | drawtext → 无字幕 |

### config.yaml 配置项

```yaml
llm.providers.{id}.api_url    # LLM API 地址
llm.providers.{id}.api_key    # LLM API Key
vlm.api_url                   # VLM 多模态 API
vlm.api_key                   # VLM API Key
vlm.frame_interval            # 抽帧间隔（秒）
vlm.max_frames                # 最大抽帧数
ai_tts.api_key                # 阿里云百炼 API Key
ai_tts.model                  # CosyVoice 模型
ai_tts.voice                  # 默认音色
pexels.api_key                # Pexels API Key
```
