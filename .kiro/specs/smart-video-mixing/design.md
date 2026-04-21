# 设计文档：智能视频剪辑模块

## 概述

本设计文档描述智能视频剪辑（Smart Video Mixing）模块的技术实现方案。该模块基于 MoneyPrinterTurbo 的 `combine_videos` 函数，使用 MoviePy 库重新构建视频混剪引擎，完全替换现有的 `composition_service.py`（基于原始 FFmpeg `filter_complex` 命令）。

## 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    Frontend (/mix)                       │
│         HTML + Tailwind CSS + Alpine.js                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ A-Roll   │ │ B-Roll   │ │ Params   │ │ Preview &  │ │
│  │ Select   │→│ Select   │→│ Config   │→│ Generate   │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
└───────────────────────┬─────────────────────────────────┘
                        │ REST API
┌───────────────────────▼─────────────────────────────────┐
│                  FastAPI Backend                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │ mix router   │  │ pages router │  │ assets router │ │
│  │ /api/mix/*   │  │ /mix         │  │ /api/assets/* │ │
│  └──────┬───────┘  └──────────────┘  └───────────────┘ │
│         │                                               │
│  ┌──────▼───────────────────────────────────────────┐   │
│  │            mixing_service.py                      │   │
│  │  ┌─────────────┐  ┌──────────────┐               │   │
│  │  │ create_task  │  │ execute_mix  │ (async)       │   │
│  │  └─────────────┘  └──────┬───────┘               │   │
│  └──────────────────────────┼───────────────────────┘   │
│         │                   │                           │
│  ┌──────▼───────┐  ┌───────▼────────┐  ┌───────────┐  │
│  │ task_service  │  │ mixing_engine  │  │ tts_svc   │  │
│  │ (existing)    │  │ (MoviePy core) │  │ (existing)│  │
│  └──────────────┘  └───────┬────────┘  └───────────┘  │
│                            │                           │
│                    ┌───────▼────────┐                   │
│                    │ FFmpeg concat  │                   │
│                    │ (final encode) │                   │
│                    └────────────────┘                   │
└─────────────────────────────────────────────────────────┘
```

## 数据模型变更

### 现有 Task 模型扩展

现有的 `Task` 模型已包含 `video_path`、`video_resolution`、`video_duration`、`video_file_size` 等字段。为支持混剪功能，需要新增以下字段：

```python
# 在 Task 模型中新增字段
class Task(Base):
    # ... 现有字段 ...
    
    # 混剪参数（JSON 存储）
    mix_params = Column(Text, nullable=True)  # JSON: aspect_ratio, transition, clip_duration, concat_mode, video_count, bgm_volume, bgm_source
    
    # 多版本视频路径（JSON 数组）
    video_paths = Column(Text, nullable=True)  # JSON: ["storage/tasks/{id}/output-1.mp4", ...]
    
    # 失败原因
    error_message = Column(Text, nullable=True)
```

### 状态机扩展

现有状态机需要新增混剪专用的状态流转路径：

```python
VALID_TRANSITIONS = {
    # 现有路径
    "draft": ["copy_confirmed", "processing"],  # 新增: draft -> processing (直接混剪，跳过文案)
    "copy_confirmed": ["tts_done", "processing"],  # 新增: 文案确认后直接混剪
    "tts_done": ["video_done", "processing"],  # 保持兼容
    
    # 混剪路径
    "processing": ["video_done", "failed"],
    "video_done": ["pending_review"],
    "pending_review": ["approved", "rejected"],
    "approved": ["published"],
    "rejected": ["draft", "processing"],  # 新增: rejected -> processing (重新混剪)
    "failed": ["processing"],  # 新增: failed -> processing (重试)
}
```

## API 设计

### 新增路由：`/api/mix`

#### POST /api/mix/create — 创建混剪任务

```python
# Request
class MixCreateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    a_roll_asset_ids: list[str] = Field(..., min_length=1)
    b_roll_asset_ids: list[str] = Field(default=[])
    aspect_ratio: str = Field(default="9:16", pattern="^(16:9|9:16|1:1)$")
    transition: str = Field(default="none", pattern="^(none|fade_in|fade_out|slide_in|slide_out|shuffle)$")
    clip_duration: int = Field(default=5, ge=2, le=15)
    concat_mode: str = Field(default="random", pattern="^(random|sequential)$")
    video_count: int = Field(default=1, ge=1, le=5)
    tts_text: Optional[str] = None
    tts_voice: Optional[str] = None
    bgm_enabled: bool = False
    bgm_asset_id: Optional[str] = None  # None = random BGM
    bgm_volume: float = Field(default=0.2, ge=0.0, le=1.0)

# Response
class MixCreateResponse(BaseModel):
    task_id: str
    status: str  # "processing"
    message: str = "混剪任务已创建"
```

权限：`require_role("intern", "operator", "admin")`

#### GET /api/mix/{task_id}/status — 查询混剪任务状态

```python
class MixStatusResponse(BaseModel):
    task_id: str
    status: str
    progress: Optional[str] = None  # e.g., "正在处理第 2/3 个版本"
    video_paths: Optional[list[str]] = None
    video_resolution: Optional[str] = None
    video_duration: Optional[float] = None
    video_file_size: Optional[int] = None
    error_message: Optional[str] = None
```

权限：`require_role("intern", "operator", "admin")`

#### POST /api/mix/{task_id}/submit-review — 提交审核

```python
class SubmitReviewResponse(BaseModel):
    task_id: str
    status: str  # "pending_review"
    message: str = "已提交审核"
```

权限：`require_role("intern", "operator", "admin")`

#### POST /api/mix/{task_id}/retry — 重新混剪（失败或被拒绝后）

```python
class RetryResponse(BaseModel):
    task_id: str
    status: str  # "processing"
    message: str = "已重新开始混剪"
```

权限：`require_role("intern", "operator", "admin")`

### Pexels 搜索 API

#### POST /api/mix/pexels/search — 搜索 Pexels 视频

```python
class PexelsSearchRequest(BaseModel):
    keywords: list[str] = Field(..., min_length=1)
    aspect_ratio: str = Field(default="9:16", pattern="^(16:9|9:16|1:1)$")
    per_page: int = Field(default=10, ge=1, le=20)

class PexelsVideoItem(BaseModel):
    url: str
    thumbnail_url: str
    duration: int
    width: int
    height: int

class PexelsSearchResponse(BaseModel):
    videos: list[PexelsVideoItem]
    total: int
```

#### POST /api/mix/pexels/download — 下载 Pexels 视频到本地

```python
class PexelsDownloadRequest(BaseModel):
    video_url: str

class PexelsDownloadResponse(BaseModel):
    asset_id: str  # 下载后创建的 Asset 记录 ID
    file_path: str
```

### LLM 关键词生成 API

#### POST /api/mix/keywords/generate — LLM 生成搜索关键词

```python
class KeywordGenerateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)

class KeywordGenerateResponse(BaseModel):
    keywords: list[str]
```

### 页面路由

#### GET /mix — 智能视频剪辑工作页面

在 `pages.py` 中新增路由，返回 `mix.html` 模板。

## 核心服务设计

### mixing_service.py — 混剪任务编排服务

负责任务创建、异步执行编排、状态管理。

```python
class MixingService:
    def __init__(self, db: Session):
        self.db = db
    
    def create_mix_task(self, request: MixCreateRequest, user_id: str) -> Task:
        """创建混剪任务并启动异步执行"""
        # 1. 验证素材存在性
        # 2. 创建 Task 记录 (status=processing, mix_params=JSON)
        # 3. 保存 TaskAsset 关联
        # 4. 启动后台线程执行混剪
        # 5. 返回 Task
    
    def execute_mix(self, task_id: str) -> None:
        """异步执行混剪（在后台线程中运行）"""
        # 1. 获取任务和参数
        # 2. 如果启用 TTS，先合成音频
        # 3. 解析素材路径
        # 4. 对每个 version (1..video_count):
        #    a. 调用 mixing_engine.combine_videos()
        #    b. 如果启用 BGM，混合背景音乐
        #    c. 保存输出文件
        # 5. 更新任务状态为 video_done
        # 6. 异常时更新为 failed
    
    def get_status(self, task_id: str) -> dict:
        """查询任务状态"""
    
    def submit_review(self, task_id: str) -> Task:
        """提交审核"""
    
    def retry(self, task_id: str) -> Task:
        """重新混剪"""
```

### mixing_engine.py — MoviePy 混剪引擎

核心视频处理逻辑，参考 MoneyPrinterTurbo 的 `combine_videos` 函数。

```python
def combine_videos(
    combined_video_path: str,
    video_paths: list[str],
    audio_file: str,
    video_aspect: str = "9:16",       # "16:9" | "9:16" | "1:1"
    video_concat_mode: str = "random", # "random" | "sequential"
    video_transition: str = "none",    # "none"|"fade_in"|"fade_out"|"slide_in"|"slide_out"|"shuffle"
    max_clip_duration: int = 5,
    threads: int = 2,
) -> str:
    """
    核心混剪函数，流程：
    1. 读取音频文件获取目标时长
    2. 遍历所有视频，按 max_clip_duration 切分为 SubClippedVideoClip
    3. 根据 concat_mode 排列片段（random=shuffle, sequential=首段）
    4. 逐个处理片段：
       a. 使用 MoviePy VideoFileClip.subclipped() 截取
       b. 缩放至目标分辨率（保持比例 + 黑色填充）
       c. 应用转场效果
       d. 写入临时文件
    5. 循环片段直到总时长 >= 音频时长
    6. 使用 FFmpeg concat demuxer 串联所有临时文件
    7. 清理临时文件
    """

def extract_audio_from_videos(video_paths: list[str], output_path: str) -> float:
    """从 A-Roll 视频中提取并拼接音频轨道，返回总时长"""

def mix_bgm(
    main_audio_path: str,
    bgm_file: str,
    output_path: str,
    bgm_volume: float = 0.2,
    fade_out_duration: float = 3.0,
) -> str:
    """将 BGM 与主音频混合"""
```

### pexels_service.py — Pexels 搜索服务

```python
class PexelsService:
    def search_videos(self, keywords: list[str], aspect_ratio: str, per_page: int) -> list[dict]:
        """搜索 Pexels 视频，参考 MoneyPrinterTurbo 的 search_videos_pexels"""
    
    def download_video(self, video_url: str, db: Session, user_id: str) -> Asset:
        """下载视频并创建 Asset 记录"""
```

## 前端设计

### mix.html — 智能视频剪辑工作页面

使用 Alpine.js 管理四步流程状态：

```javascript
function mixPage() {
    return {
        step: 1,           // 1=A-Roll, 2=B-Roll, 3=Params, 4=Generate
        aRollAssets: [],    // 已选 A-Roll
        bRollAssets: [],    // 已选 B-Roll
        params: {
            aspect_ratio: '9:16',
            transition: 'none',
            clip_duration: 5,
            concat_mode: 'random',
            video_count: 1,
            tts_enabled: false,
            tts_text: '',
            tts_voice: '',
            bgm_enabled: false,
            bgm_asset_id: null,
            bgm_volume: 0.2,
        },
        taskId: null,
        taskStatus: null,
        videoPaths: [],
        // ... methods
    }
}
```

设计风格与首页一致：白色背景、圆角卡片、淡紫渐变色调、Tailwind CSS 响应式布局。

## 文件结构

```
app/
├── routers/
│   ├── mix.py              # 新增：混剪 API 路由
│   └── pages.py            # 修改：新增 /mix 页面路由
├── schemas/
│   └── mix.py              # 新增：混剪请求/响应模型
├── services/
│   ├── mixing_service.py   # 新增：混剪任务编排服务
│   ├── mixing_engine.py    # 新增：MoviePy 混剪引擎核心
│   ├── pexels_service.py   # 新增：Pexels 搜索与下载
│   └── composition_service.py  # 删除：旧的 FFmpeg 合成服务
├── templates/
│   └── mix.html            # 新增：混剪工作页面
└── models/
    └── database.py         # 修改：Task 模型新增字段
```

## 正确性属性

### Property 1: 混剪请求参数验证

**关联需求：** 需求 1 (AC 6), 需求 5 (AC 2-7)

对于任意混剪请求参数组合：
- `a_roll_asset_ids` 为空时，请求被拒绝
- `aspect_ratio` 仅接受 "16:9"、"9:16"、"1:1"
- `clip_duration` 仅接受 2-15 的整数
- `video_count` 仅接受 1-5 的整数
- `bgm_volume` 仅接受 0.0-1.0 的浮点数
- `transition` 仅接受 "none"、"fade_in"、"fade_out"、"slide_in"、"slide_out"、"shuffle"
- `concat_mode` 仅接受 "random"、"sequential"

### Property 2: 视频分段数量不变性

**关联需求：** 需求 6 (AC 1-2)

对于任意视频时长 `d > 0` 和片段时长 `max_clip_duration` (2 ≤ m ≤ 15)：
- 分段数量 = `ceil(d / max_clip_duration)`
- 所有分段时长之和 = 原始视频时长
- 每个分段时长 ≤ `max_clip_duration`
- 最后一个分段时长 = `d % max_clip_duration`（若余数 > 0）或 `max_clip_duration`

### Property 3: 拼接模式下片段集合完整性

**关联需求：** 需求 6 (AC 3-4)

对于 random 模式：
- 打乱后的片段集合与打乱前的片段集合包含相同元素（集合不变性）
- 片段总数不变

对于 sequential 模式：
- 输出片段数量 = 输入视频数量
- 每个片段来自对应视频的第一个分段

### Property 4: 缩放后分辨率匹配目标

**关联需求：** 需求 6 (AC 5-6)

对于任意输入视频尺寸 (w, h) 和目标画面比例：
- 输出视频的宽度和高度精确等于目标分辨率
- 16:9 → 1920×1080, 9:16 → 1080×1920, 1:1 → 1080×1080

### Property 5: 视频时长与音频时长匹配

**关联需求：** 需求 7 (AC 2-4)

对于任意音频时长 `audio_duration` 和片段集合：
- 当片段总时长 < 音频时长时，片段被循环复用直到总时长 ≥ 音频时长
- 最终输出视频时长 ≈ 音频时长（误差 ≤ 1 个片段时长）

### Property 6: 多版本输出数量一致性

**关联需求：** 需求 10 (AC 3-4)

对于任意 `video_count` (1 ≤ n ≤ 5)：
- 成功完成后，输出文件数量 = `video_count`
- 每个输出文件路径格式为 `storage/tasks/{task_id}/output-{i}.mp4`
- 每个输出文件大小 > 0

### Property 7: 状态机流转合法性

**关联需求：** 需求 12 (AC 2)

对于任意状态转换序列：
- 仅允许 `VALID_TRANSITIONS` 中定义的转换
- `processing` 只能转换到 `video_done` 或 `failed`
- `video_done` 只能转换到 `pending_review`
- `failed` 只能转换到 `processing`（重试）
- 非法转换抛出 `StateTransitionError`

### Property 8: 权限控制一致性

**关联需求：** 需求 14 (AC 1-4)

对于任意用户角色和操作组合：
- intern、operator、admin 均可执行混剪操作（创建、查看、提交审核）
- 仅 operator 和 admin 可执行审核操作
- 仅 admin 可管理配置
- 未授权操作返回 HTTP 403
