# 设计文档：AI 驱动智能混剪 v2（双 Pipeline 架构）

## 1. 背景与动机

### 当前架构的问题

现有系统采用 **A-roll / B-roll 固定分类 + 4 种 mixing_mode** 的架构：

```
用户手动分类素材 → 代码硬编码决定主轴/插入 → VLM 在固定框架内填空
```

这本质上是"人驱动 + AI 辅助"，而不是"AI 驱动"：

1. **用户负担重**：上传时必须手动标记 A-roll / B-roll，需要理解剪辑概念
2. **模式割裂**：4 种 mixing_mode（pure_mix / mix_with_script / broll_voiceover / montage）各自独立分支，大量重复逻辑，维护成本高
3. **VLM 被限制**：VLM 只能在"A-roll 的第几秒插入 B-roll"这个固定框架内做决策，无法自主决定叙事结构
4. **扩展性差**：新增一种混剪风格就要加一个 mode 分支

### 目标

**AI 驱动**：用户只提供素材 + 指令，系统自动选择最优 pipeline 完成剪辑。

```
用户上传素材（不分类）+ 提示词/脚本
    │
    ├── 有语音素材 → 文本驱动 pipeline（ASR → LLM 选段 → 词级切点）
    │
    └── 纯视觉素材 → 视觉驱动 pipeline（抽帧 → VLM 分析 → timeline）
    │
    ▼
统一 timeline → 引擎执行 → 输出视频
```

## 2. 核心设计理念

### 2.1 素材即素材，不分 A/B

去掉 A-roll / B-roll 的概念。所有上传的视频/图片/音频都是"素材"（clip），VLM 自己决定每个素材的角色：

| 旧概念 | 新概念 |
|--------|--------|
| A-roll（主讲视频） | clip — VLM 自动识别为"主讲" |
| B-roll（插入素材） | clip — VLM 自动识别为"产品特写/空镜/过渡" |
| 4 种 mixing_mode | 1 种统一流程，VLM 根据素材内容自适应 |

### 2.2 VLM 做导演，引擎做执行

```
┌─────────────────────────────────────────────────┐
│                   VLM 导演层                      │
│                                                   │
│  输入：                                           │
│    - 所有素材的帧预览 + 元信息                      │
│    - 用户提示词 / 脚本文案                          │
│    - 目标时长、风格等参数                           │
│                                                   │
│  输出：                                           │
│    - 统一 timeline JSON                           │
│    - 每条 entry 指定：哪个素材、哪段、放在哪、为什么  │
│                                                   │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│                   执行引擎层                      │
│                                                   │
│  - 按 timeline 切片、缩放、拼接                    │
│  - 合并音频（原始音频 / TTS）                      │
│  - 烧字幕                                        │
│  - 纯机械执行，不做任何剪辑决策                     │
│                                                   │
└─────────────────────────────────────────────────┘
```

### 2.3 素材预分析 + 持久化（上传即理解）

传统做法是混剪时才分析素材，每次都重复调用 VLM。新架构把素材理解前置到**上传阶段**，分析结果持久化到数据库和向量索引，混剪时直接查询：

```
上传时（一次性）：
  素材文件 → 抽帧 → VLM 分析 → 结构化摘要存 DB
                              → 描述文本 embedding 存向量索引
                  → 音频检测 → 静音/有声区间存 DB
                  → Whisper 转录 → 文本存 DB

混剪时（零 VLM 成本）：
  用户选素材 → 从 DB 查摘要 → 直接进 VLM 阶段 2（剪辑规划）
  或：用户输入提示词 → 向量检索推荐素材 → 进阶段 2
```

**收益：**
- 每个素材只分析一次，N 次混剪复用同一份摘要
- 混剪时只需一次 VLM 调用（阶段 2），token 成本减半
- 素材库支持语义搜索（"找产品特写镜头"）
- 静音检测、转录结果缓存，不重复计算

### 2.4 两阶段 VLM 分析（上传 + 混剪）

将原来的"两阶段都在混剪时执行"改为"阶段 1 在上传时、阶段 2 在混剪时"：

**阶段 1 — 素材理解（Upload-time，异步）**

素材上传后，后台异步触发 VLM 分析，结果写入 `asset_analysis` 表：

```json
{
  "asset_id": "5b45d011-...",
  "description": "女主播正面讲解护肤品，手持产品展示",
  "role": "presenter",
  "visual_quality": "high",
  "audio_quality": "good",
  "has_speech": true,
  "speech_ranges": [[0, 230]],
  "transcript": "大家好，今天给大家推荐一款...",
  "key_moments": [
    {"time": 5.0, "desc": "展示产品包装"},
    {"time": 22.0, "desc": "涂抹演示"}
  ],
  "scene_tags": ["室内", "美妆", "产品展示", "口播"],
  "embedding": [0.023, -0.118, ...]
}
```

**阶段 2 — 剪辑规划（Mix-time）**

混剪时，从 DB 加载素材摘要，结合用户指令，只需一次 VLM 调用生成 timeline：

```json
[
  {"clip_index": 0, "source_start": 0.0, "source_end": 8.0, "start": 0.0, "end": 8.0, "reason": "主播开场介绍"},
  {"clip_index": 1, "source_start": 0.0, "source_end": 2.0, "start": 8.0, "end": 10.0, "reason": "插入产品特写"},
  ...
]
```

对于未完成分析的素材（刚上传、分析失败），阶段 2 时实时抽帧分析作为 fallback。

### 2.5 双 Pipeline 架构：文本驱动 + 视觉驱动

系统根据素材特征自动路由到最优 pipeline，两条 pipeline 输出相同格式的 timeline，共享同一套执行引擎。

#### 路由逻辑

```python
# 在 asset_analysis 表中已有 has_speech 和 role 字段
if analysis.has_speech and analysis.role == "presenter":
    # 文本驱动：精确到词级的语义剪辑
    timeline = text_driven_pipeline(clip, transcript, user_prompt)
else:
    # 视觉驱动：基于画面内容的智能剪辑
    timeline = vision_driven_pipeline(clip, frames, user_prompt)
```

#### 对比

| | 文本驱动 Pipeline | 视觉驱动 Pipeline |
|---|---|---|
| 决策依据 | ASR 转录文本（语义） | 视频帧（视觉） |
| AI 模型 | LLM（qwen3.6-plus，纯文本，便宜） | VLM（gpt-5.4，多模态，贵） |
| 切点精度 | 词级（ASR word timestamps） | 帧级（需 smart-cut 修正） |
| 语义理解 | 读完整文本，理解上下文和话题边界 | 看稀疏帧，推测内容 |
| 适用素材 | 口播、直播、访谈、课程（有人说话） | 产品特写、空镜、纯画面（无语音） |
| Token 成本 | 低（纯文本） | 高（图片 base64） |
| 字幕 | ASR 直出（天然精确） | Whisper 后处理 |

#### 文本驱动 Pipeline 详细流程

```
长视频（有语音）
  │
  ▼
① Whisper ASR（word-level timestamps）
  │  输出：完整转录文本 + 每个词的 start/end 时间
  │  （上传时已完成，结果在 asset_analysis.transcript）
  │
  ▼
② LLM 文本分析（qwen3.6-plus，纯文本调用）
  │  输入：完整转录文本 + 用户指令 + 目标时长
  │  任务：
  │    - 识别话题边界和段落结构
  │    - 找出高价值段落（金句、干货、高潮点）
  │    - 去除低价值内容（口头禅、重复、跑题）
  │    - 按目标时长选择段落组合
  │  输出：选中段落列表，每段标注起止句子文本
  │
  ▼
③ 时间戳映射
  │  LLM 选的句子 → 在 ASR 词级时间戳中定位
  │  → 精确到每个词的 start/end
  │
  ▼
④ 切点优化（呼吸口检测）
  │  起点：段落第一个词的 start - 0.1s
  │  终点：段落最后一个词的 end → 找下一个词的 start → 取中点
  │  去除口头禅片段（"嗯"、"那个"、"就是说"）
  │
  ▼
⑤ 生成统一 timeline
  │  与视觉驱动 pipeline 输出格式完全一致
  │
  ▼
⑥ 执行引擎（共享）
     FFmpeg 切片 + 拼接 + 字幕 + BGM
```

#### LLM 选段 Prompt 设计

```
你是专业视频剪辑师。根据以下视频转录文本，选出最有价值的片段，
组成一条 {target_duration} 秒的短视频。

转录文本（带时间戳）：
[00:00.0 - 00:05.2] 大家好，今天跟大家聊一下...
[00:05.2 - 00:12.8] 第一个点就是关于...
...

用户指令：{director_prompt}

规则：
1. 用户指令优先级最高
2. 选择信息密度高、观点明确的段落
3. 去除口头禅、重复表述、跑题内容
4. 保持叙事逻辑连贯（按原始顺序排列）
5. 每个段落必须是语义完整的（不能切在半句话中间）
6. 总时长尽量接近目标时长（允许 ±10% 误差）

输出 JSON：
[
  {
    "start_text": "第一个点就是关于",
    "end_text": "这就是核心逻辑",
    "reason": "核心观点阐述，信息密度高"
  },
  ...
]
```

#### 时间戳映射算法

```python
def map_text_to_timestamps(
    selected_segments: list[dict],   # LLM 输出的段落列表
    word_timestamps: list[dict],     # Whisper 词级时间戳
) -> list[dict]:
    """将 LLM 选的文本段落映射到精确时间戳。

    对每个段落：
    1. 在 word_timestamps 中找到 start_text 的第一个词
    2. 找到 end_text 的最后一个词
    3. 起点 = 第一个词的 start - 0.1s（留呼吸）
    4. 终点 = 最后一个词的 end + 到下一个词 start 的间隙中点
    """
    timeline = []
    cursor = 0.0

    for seg in selected_segments:
        # 模糊匹配：在词序列中找到最佳匹配位置
        start_idx = fuzzy_find_text(word_timestamps, seg["start_text"])
        end_idx = fuzzy_find_text(word_timestamps, seg["end_text"], search_from=start_idx)

        if start_idx is None or end_idx is None:
            continue

        # 精确切点
        cut_start = max(0, word_timestamps[start_idx]["start"] - 0.1)
        cut_end = word_timestamps[end_idx]["end"]

        # Snap to breath gap
        if end_idx + 1 < len(word_timestamps):
            next_word_start = word_timestamps[end_idx + 1]["start"]
            gap = next_word_start - cut_end
            if gap > 0.05:
                cut_end += gap / 2  # 取静音间隙中点

        duration = cut_end - cut_start
        timeline.append({
            "clip_index": 0,
            "source_start": round(cut_start, 3),
            "source_end": round(cut_end, 3),
            "start": round(cursor, 3),
            "end": round(cursor + duration, 3),
            "reason": seg.get("reason", ""),
        })
        cursor += duration

    return timeline
```

#### 口头禅过滤

```python
# 在 ASR 结果中标记并跳过口头禅
FILLER_WORDS = {"嗯", "啊", "那个", "就是说", "然后", "对吧", "你知道吗", "怎么说呢"}

def remove_fillers(word_timestamps: list[dict]) -> list[dict]:
    """标记口头禅词汇，在切点计算时跳过。"""
    return [w for w in word_timestamps if w["word"] not in FILLER_WORDS]
```

### 2.6 混合模式：文本 + 视觉协同（未来）

最强方案是两条 pipeline 协同工作：

```
有语音的长视频 + B-roll 素材
  │
  ├──→ 文本驱动：ASR → LLM 选段（决定保留哪些内容）
  │
  ├──→ 视觉驱动：VLM 分析 B-roll 素材（决定在哪插入什么）
  │
  └──→ 合并 timeline：
       - LLM 定叙事结构（主轴）
       - VLM 定 B-roll 插入点（丰富视觉）
       → 最终 timeline
```

MVP 阶段不实现混合模式，先按素材类型自动路由。

## 3. 统一 Timeline 格式

### 3.1 Timeline Entry Schema

```json
{
  "clip_index": 0,
  "source_start": 5.0,
  "source_end": 12.0,
  "start": 0.0,
  "end": 7.0,
  "reason": "主播展示产品包装，信息密度高"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `clip_index` | int | 素材索引（0-based），对应输入素材列表 |
| `source_start` | float | 在源素材中的起始时间（秒） |
| `source_end` | float | 在源素材中的结束时间（秒） |
| `start` | float | 在输出 timeline 中的起始时间（秒） |
| `end` | float | 在输出 timeline 中的结束时间（秒） |
| `reason` | string | VLM 的剪辑决策理由 |

### 3.2 与旧格式的对比

| 旧格式 | 新格式 |
|--------|--------|
| `type: "a_roll" / "b_roll"` | `clip_index: N`（不区分角色） |
| `start/end` 是 A-roll 的绝对时间 | `start/end` 是输出 timeline 的时间 |
| 无 source 时间范围 | `source_start/source_end` 精确指定源素材裁剪范围 |
| B-roll 由代码 cycle 分配 | VLM 直接指定用哪个素材 |

这个格式实际上就是现有 `montage` 模式的 timeline 格式，只是现在它成为唯一格式。

## 4. 新架构

### 4.1 系统架构图

```
┌──────────────────────────────────────────────────────────────┐
│                         前端 (mix.html)                       │
│                                                               │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ 素材选择     │  │ 提示词/脚本   │  │ 参数（时长/风格/TTS）│ │
│  │ （语义搜索） │  │ （可选）      │  │                      │ │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬───────────┘ │
│         └────────────────┼─────────────────────┘             │
│                          ▼                                    │
│                   POST /api/mix/create                        │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                    MixingService (编排层)                      │
│                                                               │
│  1. 解析参数，加载素材文件路径                                  │
│  2. 从 asset_analysis 表加载素材摘要（缓存命中）               │
│  3. 可选：生成 TTS 音频                                       │
│  4. 调用 AIDirectorService（只需阶段 2）                      │
│  5. 可选：生成/烧字幕                                         │
│  6. 可选：混入 BGM                                            │
│  7. 更新任务状态                                              │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                  AIDirectorService (导演层)                    │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ 加载素材摘要（从 DB，不调 AI）                            │ │
│  │  - has_speech? role? transcript?                         │ │
│  └────────────────────────┬────────────────────────────────┘ │
│                           │                                   │
│              ┌────────────┴────────────┐                     │
│              ▼                         ▼                      │
│  ┌──────────────────────┐  ┌──────────────────────┐         │
│  │ 文本驱动 Pipeline     │  │ 视觉驱动 Pipeline     │         │
│  │ (has_speech=true)     │  │ (has_speech=false)    │         │
│  │                      │  │                      │         │
│  │ ASR 转录(已缓存)     │  │ 抽帧 → VLM 分析      │         │
│  │  → LLM 选段          │  │  → VLM 生成 timeline  │         │
│  │  → 词级时间戳映射     │  │  → smart-cut 修正     │         │
│  │  → 呼吸口切点优化     │  │                      │         │
│  └──────────┬───────────┘  └──────────┬───────────┘         │
│             └────────────┬────────────┘                      │
│                          ▼                                    │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ 统一 timeline（格式完全一致）                             │ │
│  └────────────────────────┬────────────────────────────────┘ │
│                           ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Fallback：如果 LLM/VLM 失败                               │ │
│  │  - 基于素材时长均匀分配的 blind-cut timeline              │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│                  TimelineExecutor (执行层)                     │
│                                                               │
│  - 按 timeline 用 FFmpeg 切片、缩放、拼接                     │
│  - 合并音频轨道                                               │
│  - 纯机械执行，不做决策                                       │
└──────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────┐
                    │   素材智能层（上传时触发）     │
                    │                              │
                    │  ┌────────────────────────┐  │
                    │  │ AssetAnalysisService    │  │
                    │  │                        │  │
                    │  │ 上传 → 抽帧 → VLM 分析  │  │
                    │  │      → 音频检测         │  │
                    │  │      → Whisper 转录     │  │
                    │  │      → Embedding 生成   │  │
                    │  └───────────┬────────────┘  │
                    │              ▼                │
                    │  ┌────────────────────────┐  │
                    │  │ 持久化存储              │  │
                    │  │                        │  │
                    │  │ asset_analysis 表       │  │
                    │  │  - 结构化摘要           │  │
                    │  │  - 角色/标签            │  │
                    │  │  - 音频元信息           │  │
                    │  │  - 转录文本             │  │
                    │  │                        │  │
                    │  │ 向量索引                │  │
                    │  │  - 素材描述 embedding   │  │
                    │  │  - 支持语义检索         │  │
                    │  └────────────────────────┘  │
                    └─────────────────────────────┘
```

### 4.2 数据流

```
=== 上传时（异步，一次性） ===

素材文件
     │
     ├──→ [抽帧] → 稀疏帧 → [VLM 阶段1] → 结构化摘要 ──→ asset_analysis 表
     │                                          │
     │                                          └──→ [Embedding] → 向量索引
     │
     ├──→ [音频检测] → 静音区间 / 有声区间 ──→ asset_analysis 表
     │
     └──→ [Whisper] → 转录文本 ──→ asset_analysis 表


=== 混剪时 ===

用户选素材 + 提示词 + 参数
     │
     ├──→ [查 DB] → 素材摘要（缓存命中，零 VLM 成本）
     │       │
     │       ▼
     │   [VLM 阶段2] → timeline JSON（唯一的 VLM 调用）
     │       │
     │       ▼
     └──→ [FFmpeg 执行] → 输出视频


=== 素材搜索时 ===

用户输入："找产品特写镜头"
     │
     └──→ [Embedding] → 向量相似度检索 → 匹配素材列表
```

## 5. API 变更

### 5.1 新的 MixCreateRequest

```python
class MixCreateRequest(BaseModel):
    topic: str = ""                          # 主题描述
    asset_ids: list[str]                     # 素材 ID 列表（不分 A/B）
    director_prompt: str = ""                # 用户提示词/剪辑指令
    script_text: str = ""                    # 脚本文案（可选，用于 TTS + 字幕）

    # 输出参数
    aspect_ratio: str = "9:16"               # 画面比例
    max_output_duration: int = 60            # 目标输出时长（秒）
    video_count: int = 1                     # 输出视频数量
    transition: str = "none"                 # 转场效果

    # TTS 参数（可选）
    tts_voice: str = ""                      # TTS 音色
    
    # BGM 参数（可选）
    bgm_enabled: bool = False
    bgm_asset_id: str = ""
    bgm_volume: float = 0.2
```

**去掉的字段：**
- `mixing_mode` — 不再需要，VLM 自动决定
- `a_roll_asset_ids` / `b_roll_asset_ids` — 合并为 `asset_ids`
- `tts_text` — 重命名为 `script_text`，语义更清晰
- `clip_duration` / `concat_mode` — blind-cut 参数，不再需要

### 5.2 VLM 交互接口（视觉驱动 Pipeline）

```python
class VLMService:
    def analyze_single_clip(
        self,
        frames: list[tuple[float, str]],
        clip_metadata: dict,
    ) -> dict | None:
        """上传时调用：分析单个素材。
        
        返回结构化摘要 dict，或 None（失败时）。
        在素材上传后异步调用，结果存入 asset_analysis 表。
        """

    def generate_unified_timeline(
        self,
        clip_summaries: list[dict],
        dense_frames: list[list[tuple[float, str]]],
        clip_metadata: list[dict],
        target_duration: float,
        user_prompt: str = "",
        script_text: str = "",
    ) -> list[dict] | None:
        """混剪时调用：视觉驱动剪辑规划。
        
        接收从 DB 加载的素材摘要（不再实时调 VLM 分析），
        返回统一 timeline，或 None（失败时走 fallback）。
        """
```

### 5.3 LLM 文本选段接口（文本驱动 Pipeline）

```python
class TextDrivenEditingService:
    """文本驱动剪辑服务 — 基于 ASR 转录 + LLM 选段。"""

    def __init__(self):
        self.config = ExternalConfig.get_instance()

    def generate_text_driven_timeline(
        self,
        transcript: str,
        word_timestamps: list[dict],
        target_duration: float,
        user_prompt: str = "",
    ) -> list[dict] | None:
        """从转录文本生成 timeline。

        流程：
        1. 将转录文本 + 用户指令发给 LLM（qwen3.6-plus）
        2. LLM 返回选中的段落列表（start_text / end_text / reason）
        3. 将段落文本映射到 word-level timestamps
        4. 切点 snap 到呼吸口（词间静音间隙中点）
        5. 输出统一 timeline 格式

        Args:
            transcript: 完整 ASR 转录文本（带时间标记）。
            word_timestamps: Whisper 词级时间戳列表。
            target_duration: 目标输出时长（秒）。
            user_prompt: 用户剪辑指令。

        Returns:
            统一 timeline list，或 None（失败时走 fallback）。
        """

    def select_segments_with_llm(
        self,
        transcript: str,
        target_duration: float,
        user_prompt: str = "",
    ) -> list[dict] | None:
        """调用 LLM 选择高价值段落。

        Returns:
            [{"start_text": "...", "end_text": "...", "reason": "..."}, ...]
        """

    def map_text_to_timestamps(
        self,
        selected_segments: list[dict],
        word_timestamps: list[dict],
    ) -> list[dict]:
        """将 LLM 选的文本段落映射到精确时间戳。

        Returns:
            统一 timeline 格式的 list。
        """
```

### 5.4 素材搜索接口

```python
# GET /api/assets/search?q=产品特写&limit=10
class AssetSearchResponse(BaseModel):
    items: list[AssetWithAnalysis]
    total: int

class AssetWithAnalysis(BaseModel):
    id: str
    original_filename: str
    category: str
    # 来自 asset_analysis
    description: str | None
    role: str | None
    scene_tags: list[str] | None
    relevance_score: float | None   # 向量相似度分数
```

## 6. 素材智能层（Asset Intelligence Layer）

这是新架构的核心新增模块，负责素材的预分析、持久化和语义检索。

### 6.1 AssetAnalysisService

```python
class AssetAnalysisService:
    """素材预分析服务 — 上传时异步触发。"""

    def analyze_asset(self, asset_id: str) -> None:
        """完整分析流程（异步执行）：
        
        1. 抽帧 → VLM 分析 → 结构化摘要
        2. 音频检测 → 静音区间 / 有声区间
        3. Whisper 转录 → 文本
        4. 摘要文本 → Embedding → 向量索引
        5. 全部结果写入 asset_analysis 表
        """

    def get_analysis(self, asset_id: str) -> dict | None:
        """从 DB 查询素材分析结果。
        
        混剪时调用，替代实时 VLM 分析。
        """

    def search_by_text(self, query: str, limit: int = 10) -> list[dict]:
        """语义搜索：用户输入自然语言，返回最相关的素材。
        
        流程：query → embedding → 向量相似度检索 → 返回匹配素材
        """

    def reanalyze_asset(self, asset_id: str) -> None:
        """重新分析素材（VLM 模型升级后可触发）。"""
```

### 6.2 asset_analysis 表结构

```sql
CREATE TABLE asset_analysis (
    id          TEXT PRIMARY KEY,
    asset_id    TEXT NOT NULL UNIQUE REFERENCES assets(id) ON DELETE CASCADE,
    
    -- VLM 结构化输出
    description     TEXT,           -- 内容描述
    role            TEXT,           -- presenter / product_closeup / lifestyle / transition / other
    visual_quality  TEXT,           -- high / medium / low
    scene_tags      JSON,           -- ["室内", "美妆", "产品展示"]
    key_moments     JSON,           -- [{"time": 5.0, "desc": "展示产品包装"}]
    
    -- 音频元信息
    audio_quality   TEXT,           -- good / noisy / silent
    has_speech      BOOLEAN DEFAULT FALSE,
    speech_ranges   JSON,           -- [[0, 230], [1650, 1664]] 有声时间段（秒）
    transcript      TEXT,           -- Whisper 转录全文
    
    -- 向量
    embedding       BLOB,           -- 素材描述的 embedding 向量（float32 数组）
    
    -- 元信息
    status          TEXT DEFAULT 'pending',  -- pending / analyzing / completed / failed
    error_message   TEXT,
    vlm_model       TEXT,           -- 分析时使用的 VLM 模型版本
    analyzed_at     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_asset_analysis_asset_id ON asset_analysis(asset_id);
CREATE INDEX idx_asset_analysis_role ON asset_analysis(role);
CREATE INDEX idx_asset_analysis_status ON asset_analysis(status);
```

### 6.3 向量存储方案

考虑到项目当前使用 SQLite，向量存储有两个选择：

**方案 A：sqlite-vec 扩展（推荐，轻量）**

```python
# 直接在 SQLite 里做向量检索，无需额外服务
import sqlite_vec

# 创建向量表
db.execute("""
    CREATE VIRTUAL TABLE asset_embeddings USING vec0(
        asset_id TEXT PRIMARY KEY,
        embedding FLOAT[768]
    )
""")

# 检索
db.execute("""
    SELECT asset_id, distance
    FROM asset_embeddings
    WHERE embedding MATCH ?
    ORDER BY distance
    LIMIT 10
""", [query_embedding])
```

优点：零依赖，跟现有 SQLite 数据库一体化，部署简单
缺点：大规模（>100K 素材）时性能可能不够

**方案 B：ChromaDB（功能更强）**

```python
import chromadb

client = chromadb.PersistentClient(path="storage/chroma")
collection = client.get_or_create_collection("asset_embeddings")

# 写入
collection.add(
    ids=[asset_id],
    embeddings=[embedding],
    metadatas=[{"role": "presenter", "tags": "美妆,口播"}],
    documents=[description],
)

# 检索
results = collection.query(
    query_texts=["产品特写镜头"],
    n_results=10,
)
```

优点：内置 embedding 生成、metadata 过滤、持久化
缺点：多一个依赖

**建议**：先用 sqlite-vec（方案 A），素材库规模大了再迁移到 ChromaDB。

### 6.4 Embedding 生成

素材描述文本 → embedding 向量的生成方式：

**方案 A：调用 VLM 同一 API 的 embedding 端点**

```python
# 复用已有的 VLM API（如果支持 embedding）
response = client.post(f"{vlm_api_url}/embeddings", json={
    "model": "text-embedding-3-small",
    "input": description_text,
})
embedding = response.json()["data"][0]["embedding"]
```

**方案 B：本地轻量模型（零 API 成本）**

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")  # 支持中文
embedding = model.encode(description_text)
```

优点：无 API 调用成本，离线可用
缺点：首次加载模型需要下载 ~400MB

**建议**：优先用方案 A（复用已有 API），API 不支持 embedding 时 fallback 到方案 B。

### 6.5 上传时的异步分析流程

```python
# 在 assets.py upload 接口末尾触发异步分析
@router.post("/upload", response_model=AssetUploadResponse, status_code=201)
async def upload_asset(...):
    # ... 现有上传逻辑 ...
    
    # 触发异步素材分析（不阻塞上传响应）
    thread = threading.Thread(
        target=AssetAnalysisService().analyze_asset,
        args=(asset.id,),
        daemon=True,
    )
    thread.start()
    
    return asset
```

前端素材卡片上显示分析状态：
- 🔄 分析中...（status = analyzing）
- ✅ 已分析（status = completed，显示 role 标签）
- ❌ 分析失败（status = failed，可手动重试）

### 6.6 混剪时的摘要加载

```python
# AIDirectorService 混剪时的流程
def run_pipeline(self, asset_ids, ...):
    analysis_service = AssetAnalysisService()
    
    clip_summaries = []
    for asset_id in asset_ids:
        analysis = analysis_service.get_analysis(asset_id)
        if analysis and analysis["status"] == "completed":
            # 缓存命中：直接用 DB 里的摘要
            clip_summaries.append(analysis)
        else:
            # 缓存未命中：实时抽帧分析（fallback）
            frames = self.vlm_service.extract_frames(asset.file_path)
            summary = self.vlm_service.analyze_single_clip(frames, metadata)
            clip_summaries.append(summary or {"description": "", "role": "other"})
    
    # 只需一次 VLM 调用：阶段 2 剪辑规划
    timeline = self.vlm_service.generate_unified_timeline(
        clip_summaries, dense_frames, metadata, target_duration, ...
    )
```

## 6. VLM Prompt 设计

### 6.1 阶段 1 — 素材理解 Prompt

```
你是专业视频导演。分析以下素材，为每个素材输出内容摘要。

素材列表：
  clip_0: {filename} ({duration}s) — [帧预览 ×N]
  clip_1: {filename} ({duration}s) — [帧预览 ×N]
  ...

{用户指令（如有）}

请为每个素材输出 JSON：
[
  {
    "clip_index": 0,
    "description": "内容描述",
    "role": "presenter / product_closeup / lifestyle / transition / other",
    "visual_quality": "high / medium / low",
    "key_moments": [{"time": 5.0, "desc": "关键画面描述"}]
  }
]
```

### 6.2 阶段 2 — 剪辑规划 Prompt

```
你是专业视频导演。根据素材分析结果和用户指令，规划一条 {target_duration} 秒的视频。

素材摘要：
{阶段 1 输出}

用户指令：{director_prompt}
脚本文案：{script_text}（如有）

规则：
1. 用户指令优先级最高
2. 如有脚本文案，剪辑节奏应配合文案叙事
3. 主讲类素材作为叙事主轴，产品特写/空镜作为视觉丰富
4. 避免在关键动作（手势、产品展示）中间切走
5. 短插入（1-3s）用于节奏变化，长片段（5-15s）用于信息传递
6. 同一素材的不同片段可以多次使用，但避免连续重复
7. timeline 必须无间隙，覆盖完整输出时长

输出 JSON timeline：
[
  {"clip_index": 0, "source_start": 0.0, "source_end": 8.0, 
   "start": 0.0, "end": 8.0, "reason": "..."},
  ...
]
```

## 7. 音频策略

VLM 不直接处理音频，音频由编排层决定：

| 场景 | 音频来源 | 决策者 |
|------|---------|--------|
| 有脚本文案 | TTS 合成音频 | 用户（提供了 script_text） |
| 无脚本，素材有音频 | 提取主要素材的原始音频 | 系统自动（选 VLM 标记为 presenter 的素材） |
| 无脚本，素材无音频 | 无音频 / 仅 BGM | 系统自动 |

音频来源的选择逻辑：

```python
if script_text:
    audio = tts_service.synthesize(script_text, voice)
else:
    # 找到 VLM 标记为 presenter 的素材，提取其音频
    presenter_clips = [c for c in clip_summaries if c["role"] == "presenter"]
    if presenter_clips:
        audio = extract_audio(presenter_clips[0])
    else:
        audio = None  # 仅 BGM 或静音
```

## 8. 数据库变更

### 8.1 新增 asset_analysis 表

详见 [6.2 asset_analysis 表结构](#62-asset_analysis-表结构)。

### 8.2 TaskAsset 表简化

```sql
-- 旧：roll_type 区分 a_roll / b_roll / asset
-- 新：去掉 roll_type，所有素材平等
ALTER TABLE task_assets DROP COLUMN roll_type;
-- 或保留字段但统一为 "clip"
```

### 8.3 Task 表

`mix_params` JSON 字段内容变更：

```json
// 旧
{
  "mixing_mode": "pure_mix",
  "a_roll_asset_ids": [...],
  "b_roll_asset_ids": [...]
}

// 新
{
  "asset_ids": [...],
  "director_prompt": "...",
  "script_text": "..."
}
```

## 9. 前端变更

### 9.1 整体方案：对话式 UI（Chat-based）

**核心思路**：智能混剪模块采用对话式交互，类似 Lobe UI 的布局。用户通过对话窗口上传素材、输入指令、查看结果，取代原来的多步骤表单。

**参考方案：Lobe UI**

Lobe UI（https://github.com/lobehub/lobe-ui）是 LobeChat 的 UI 组件库，提供了成熟的对话式布局。但考虑到当前项目用的是 Jinja2 模板 + Alpine.js + Tailwind（无 React），直接用 Lobe UI 组件不现实。

**适配策略**：借鉴 Lobe UI 的布局设计，用 Tailwind + Alpine.js 手写实现：

```
┌─────────────────────────────────────────────────────┐
│  侧边栏（已有）  │          对话主区域                │
│                  │                                    │
│  首页            │  ┌──────────────────────────────┐  │
│  素材库          │  │  消息流（上滚）                │  │
│  ▶ 智能混剪      │  │                              │  │
│  任务列表        │  │  [系统] 欢迎使用智能混剪...    │  │
│  批量任务        │  │                              │  │
│  审核            │  │  [用户] 上传了 3 个素材        │  │
│                  │  │    📎 产品展示.mp4             │  │
│                  │  │    📎 主播口播.mp4             │  │
│                  │  │    📎 特写.mp4                │  │
│                  │  │                              │  │
│                  │  │  [用户] 剪一条60秒种草视频     │  │
│                  │  │                              │  │
│                  │  │  [系统] 正在分析素材...        │  │
│                  │  │  [系统] 正在生成剪辑方案...    │  │
│                  │  │  [系统] ✅ 生成完成            │  │
│                  │  │    ▶ output-1.mp4 [预览]      │  │
│                  │  │    ▶ output-2.mp4 [预览]      │  │
│                  │  │                              │  │
│                  │  └──────────────────────────────┘  │
│                  │                                    │
│                  │  ┌──────────────────────────────┐  │
│                  │  │  输入区域                      │  │
│                  │  │  [📎 添加素材] [输入指令...]    │  │
│                  │  │                     [发送 ▶]  │  │
│                  │  └──────────────────────────────┘  │
│                  │                                    │
│                  │  ┌──────────────────────────────┐  │
│                  │  │  参数面板（可折叠）             │  │
│                  │  │  时长: 60s  数量: 3  比例: 9:16│  │
│                  │  │  TTS: [音色选择]  BGM: [开关]  │  │
│                  │  └──────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 9.2 路由变更

```python
# 旧路由
/mix              → mix.html（多步骤表单，删除）
/tasks/new        → tasks_new.html（文案生成，保留）

# 新路由
/mix              → mix_chat.html（对话式智能混剪，新建）
```

侧边栏导航中，原来的"任务列表"里的"新建任务"按钮改为跳转到 `/mix`。

### 9.3 对话消息类型

对话流中的消息分为几种类型，用不同的卡片样式渲染：

| 类型 | 发送方 | 内容 |
|------|--------|------|
| `text` | 用户/系统 | 纯文本消息 |
| `asset_upload` | 用户 | 素材上传卡片（缩略图 + 文件名 + 分析状态） |
| `progress` | 系统 | 进度状态（分析中 / 生成中 / 完成） |
| `video_result` | 系统 | 视频结果卡片（预览播放器 + 下载 + 提交审核） |
| `error` | 系统 | 错误提示 |
| `params` | 系统 | 参数确认卡片（时长/数量/比例等） |

### 9.4 交互流程

```
1. 用户进入 /mix 页面
   → 系统显示欢迎消息 + 引导

2. 用户点击 📎 添加素材（或拖拽上传）
   → 调用 POST /api/assets/upload（现有接口，不变）
   → 素材自动进入素材库
   → 对话流显示素材卡片（缩略图 + 分析状态）
   → 后台异步触发素材分析

3. 用户也可以从素材库选择已有素材
   → 弹出素材选择面板（支持语义搜索）
   → 选中的素材显示在对话流中

4. 用户输入剪辑指令："剪一条60秒的产品种草视频"
   → 前端组装请求：asset_ids + director_prompt + 参数
   → 调用 POST /api/mix/create（现有接口）
   → 对话流显示进度消息

5. 前端轮询 GET /api/mix/{task_id}/status（现有接口）
   → 更新进度消息（分析中 → 生成中 → 完成）

6. 生成完成
   → 对话流显示视频结果卡片
   → 用户可预览、下载、提交审核
   → 用户可继续对话："再剪一条，多用产品特写"
```

### 9.5 通信协议：保持 REST 轮询（MVP）

**不变的接口：**
- `POST /api/assets/upload` — 上传素材
- `POST /api/assets/upload/batch` — 批量上传
- `POST /api/mix/create` — 创建混剪任务
- `GET /api/mix/{task_id}/status` — 查询状态（轮询）
- `GET /api/assets` — 素材列表

**轮询策略：**
```javascript
// 任务创建后，每 3 秒轮询一次状态
async function pollTaskStatus(taskId) {
    const poll = setInterval(async () => {
        const status = await apiCall('GET', `/api/mix/${taskId}/status`);
        updateProgressMessage(status);
        if (['video_done', 'failed'].includes(status.status)) {
            clearInterval(poll);
            if (status.status === 'video_done') {
                showVideoResult(status);
            } else {
                showError(status.error_message);
            }
        }
    }, 3000);
}
```

**未来升级路径（非 MVP）：**
当需要实时进度时，可以加 SSE（Server-Sent Events）端点：
```
GET /api/mix/{task_id}/stream → text/event-stream
```
但 MVP 阶段用轮询完全够用。

### 9.6 素材上传 → 自动进素材库

**现有逻辑已满足**：`POST /api/assets/upload` 上传后就会创建 Asset 记录，素材自动在素材库里。对话窗口里上传的素材和素材库页面上传的素材走同一个接口，数据完全一致。

对话窗口里额外做的事：
1. 上传成功后，把 asset_id 加入当前会话的 `selectedAssetIds` 列表
2. 在对话流中显示素材卡片
3. 后台触发异步分析（新增逻辑）

### 9.7 新的步骤流程（对话式）

不再有固定的 Step 1/2/3/4，而是自然对话：

```
[系统] 👋 欢迎使用智能混剪！上传素材或从素材库选择，然后告诉我你想怎么剪。

[用户] 📎 上传了 产品展示.mp4、主播口播.mp4
[系统] ✅ 已收到 2 个素材，正在分析中...
[系统] 📊 分析完成：
       · 主播口播.mp4 — 主讲类，27分钟，前3分50秒有声
       · 产品展示.mp4 — 产品特写，8秒

[用户] 剪3条60秒的种草视频，多插入产品特写
[系统] 📋 确认参数：3条 × 60秒，9:16竖屏，无配音
       [开始生成] [修改参数]

[用户] 点击 [开始生成]
[系统] ⏳ 正在生成剪辑方案...
[系统] ⏳ 正在渲染视频 (1/3)...
[系统] ⏳ 正在渲染视频 (2/3)...
[系统] ⏳ 正在渲染视频 (3/3)...
[系统] ✅ 生成完成！
       ▶ output-1.mp4 [预览] [下载]
       ▶ output-2.mp4 [预览] [下载]
       ▶ output-3.mp4 [预览] [下载]
       [提交审核]

[用户] 第二条不太好，能不能重新剪？多用主播讲解的部分
[系统] ⏳ 正在重新生成第2条...
```

## 10. 向后兼容与迁移

### 10.1 迁移策略

采用**渐进式迁移**，不一次性删除旧代码：

1. **Phase 1**：新增统一流程作为新的 mixing_mode = `"vlm_auto"`，与旧模式并存
2. **Phase 2**：前端默认使用新流程，旧模式保留但标记为 deprecated
3. **Phase 3**：确认新流程稳定后，移除旧模式代码

### 10.2 旧任务兼容

已有的 task 记录（mix_params 里有 mixing_mode）不受影响，查询和回放正常。新任务使用新的 mix_params 格式。

## 11. Fallback 策略

双 Pipeline 的降级方案：

```
=== 文本驱动 Pipeline ===
Whisper 转录失败 → 降级到视觉驱动 pipeline
LLM 选段失败 → 用 ASR 静音检测自动切段
LLM 返回退化结果（只选了 1 段）→ 拒绝，用均匀切段
文本→时间戳映射失败 → 用句子级时间戳（粗糙但可用）

=== 视觉驱动 Pipeline ===
上传时分析失败 → 标记 status=failed，混剪时实时抽帧分析
素材摘要缓存未命中 → 实时调 VLM 分析（单个素材）
VLM timeline 失败 → 生成 blind-cut timeline（均匀分配素材）
VLM 返回退化 timeline（entry 太少）→ 拒绝，走 blind-cut

=== 共享 ===
Embedding 生成失败 → 素材不进向量索引，仅支持传统搜索
TTS 失败 → Edge-TTS fallback
Whisper 失败 → 跳过字幕
Pipeline 路由判断失败 → 默认走视觉驱动（更通用）
```

## 12. 与现有 montage 模式的关系

现有的 `montage` 模式（Mode 4）实际上就是这个新架构的雏形：
- 所有素材平等，不分 A/B
- VLM 自主决定排列顺序
- 统一 timeline 格式（clip_index + source_start/end）

新架构在此基础上增加了：
- **双 Pipeline**：文本驱动（口播类）+ 视觉驱动（纯画面类），自动路由
- 素材预分析 + 持久化（上传即理解）
- 音频智能选择（自动识别 presenter 素材）
- 词级切点优化（呼吸口检测）
- 静音检测（避免选到无声片段）
- 更丰富的 VLM prompt（支持脚本文案引导）

## 13. 待讨论

1. **素材数量上限**：VLM 单次能处理多少素材的帧？需要根据 token 限制测试。当前 max_frames=30 对单个素材够用，但 10 个素材 × 30 帧 = 300 帧可能超限。阶段 2 可以只发主要素材的密集帧 + 其他素材的文本摘要来控制 token。
2. **长视频处理**：如果单个素材超过 10 分钟，阶段 1 的稀疏帧是否足够理解内容？是否需要分段处理？
3. **实时预览**：VLM 生成 timeline 后，是否需要先让用户预览 timeline 再执行？还是直接执行？
4. **多轮对话**：用户看到结果不满意时，是否支持"重新剪，这次多用产品特写"这种迭代？
5. ~~**成本控制**~~：✅ 已解决 — 素材预分析 + 持久化，混剪时只需一次 VLM 调用。
6. **Embedding 模型选择**：用 VLM API 的 embedding 端点还是本地 sentence-transformers？前者简单但有 API 成本，后者免费但需要下载模型。
7. **分析结果过期**：VLM 模型升级后，旧的分析结果是否需要重新生成？是否需要版本标记 + 批量重分析机制？
8. **向量索引规模**：sqlite-vec 在多大规模下需要迁移到 ChromaDB 或 Milvus？需要压测。
9. **隐私与安全**：素材帧发送到外部 VLM API 是否有合规风险？是否需要支持本地 VLM（如 LLaVA）？
