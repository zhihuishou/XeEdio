# 需求文档：智能视频剪辑模块

## 简介

智能视频剪辑（Smart Video Mixing）是 XeEdio 视频生产平台的核心功能模块。该模块基于 MoneyPrinterTurbo 的 MoviePy 视频合成引擎（`combine_videos` 函数）重新构建，完全替换现有的原始 FFmpeg `filter_complex` 命令行合成方案（`composition_service.py`）。用户通过选择 A-Roll（达人口播视频，含音频轨道）和可选的 B-Roll（产品镜头、空镜素材）素材，配置混剪参数后，由引擎自动完成视频分段、拼接、转场、缩放和编码输出。模块支持批量生成多个不同排列版本的成品视频，并自动提交至现有审核流程。

## 术语表

- **Platform（平台）**：XeEdio 视频生产平台系统的总称
- **Mixing_Engine（混剪引擎）**：基于 MoviePy 的视频混剪合成核心服务，参考 MoneyPrinterTurbo 的 `combine_videos` 函数实现，负责素材分段、拼接、转场、缩放和 FFmpeg 编码输出
- **A_Roll（主画面）**：达人口播或产品展示视频素材，其音频轨道决定成品视频的总时长；对应素材库分类 `talent_speaking`
- **B_Roll（辅助画面）**：产品镜头、空镜头等辅助视频素材，可从素材库手动选择（分类 `product`、`pexels_broll`）或通过 Pexels API 搜索获取
- **Asset_Library（素材库）**：管理视频、图片、音频素材的上传、分类和检索模块（已有功能）
- **Asset（素材）**：上传到素材库中的单个媒体文件，包含 `file_path`、`category`、`media_type`、`duration` 等元数据
- **Clip_Segment（片段）**：视频素材按照配置的 `max_clip_duration` 切分后的单个视频片段
- **Transition_Effect（转场效果）**：两个视频片段之间的过渡动画效果，包括 FadeIn、FadeOut、SlideIn、SlideOut、Shuffle（随机选择）
- **Aspect_Ratio（画面比例）**：输出视频的宽高比，支持 16:9（1920×1080）、9:16（1080×1920）、1:1（1080×1080）
- **Concat_Mode（拼接模式）**：视频片段的排列方式，支持 random（随机打乱）和 sequential（顺序排列）
- **TTS_Engine（语音合成引擎）**：使用 Edge-TTS 将文本转换为语音的服务（已有功能）
- **LLM_Service（大语言模型服务）**：调用 LLM API 生成文案或关键词的服务（已有功能）
- **Pexels_Service（Pexels 搜索服务）**：通过 Pexels API 按关键词搜索免费商用视频素材的服务
- **BGM（背景音乐）**：可选的背景音乐音频，与主音频混合后作为成品视频的音频轨道
- **Mixing_Task（混剪任务）**：一次视频混剪的完整流程记录，从素材选择到视频输出
- **Review_System（审核系统）**：现有的视频审核流程，支持运营对视频执行通过或拒绝操作（已有功能）
- **Web_UI（前端界面）**：基于 HTML + Tailwind CSS + Alpine.js 构建的 Web 操作界面
- **Forbidden_Word_Filter（违禁词过滤器）**：对文本内容执行违禁词检测的服务（已有功能）

## 需求

### 需求 1：A-Roll 素材选择

**用户故事：** 作为用户，我希望能从素材库中选择一个或多个视频作为 A-Roll 主画面素材，以便作为混剪视频的主要内容和音频来源。

#### 验收标准

1. WHEN 用户进入智能视频剪辑页面（`/mix`）时，THE Web_UI SHALL 展示 A-Roll 素材选择区域，从 Asset_Library 中加载 `media_type` 为 `video` 的素材列表供用户浏览和选择
2. THE Web_UI SHALL 支持用户选择一个或多个视频素材作为 A-Roll
3. WHEN 用户选择 A-Roll 素材后，THE Web_UI SHALL 展示已选素材的缩略图、文件名和时长信息
4. THE Web_UI SHALL 支持用户通过拖拽或上下箭头调整已选 A-Roll 素材的排列顺序
5. THE Web_UI SHALL 支持用户点击移除按钮删除已选的单个 A-Roll 素材
6. IF 用户未选择任何 A-Roll 素材即触发混剪，THEN THE Platform SHALL 拒绝该操作并提示"请至少选择一个 A-Roll 素材"

### 需求 2：B-Roll 素材选择（手动）

**用户故事：** 作为用户，我希望能从素材库中手动选择 B-Roll 辅助画面素材，以便丰富混剪视频的视觉内容。

#### 验收标准

1. THE Web_UI SHALL 在智能视频剪辑页面展示 B-Roll 素材选择区域，从 Asset_Library 中加载 `media_type` 为 `video` 的素材列表供用户浏览和选择
2. THE Web_UI SHALL 支持用户选择零个或多个素材作为 B-Roll
3. WHEN 用户选择 B-Roll 素材后，THE Web_UI SHALL 展示已选素材的缩略图、文件名和时长信息
4. THE Web_UI SHALL 支持用户点击移除按钮删除已选的单个 B-Roll 素材

### 需求 3：B-Roll Pexels 搜索（可选）

**用户故事：** 作为用户，我希望能通过关键词搜索 Pexels 免费商用视频作为 B-Roll 素材，以便在素材库资源不足时获取高质量空镜素材。

#### 验收标准

1. WHERE Pexels 搜索功能已启用（系统配置中已设置 Pexels API Key），THE Web_UI SHALL 在 B-Roll 选择区域提供"搜索 Pexels"入口
2. WHEN 用户输入搜索关键词并触发搜索时，THE Pexels_Service SHALL 调用 Pexels API 按关键词搜索视频素材，并返回符合当前配置画面比例的搜索结果列表
3. WHEN Pexels 搜索返回结果后，THE Web_UI SHALL 展示搜索结果的预览缩略图和时长信息，并允许用户选择其中的视频作为 B-Roll
4. WHEN 用户选择 Pexels 搜索结果作为 B-Roll 时，THE Pexels_Service SHALL 将选中的视频下载到本地缓存目录
5. IF Pexels API 调用失败或超时（超过 60 秒），THEN THE Pexels_Service SHALL 返回搜索失败的错误提示
6. IF 系统配置中未设置 Pexels API Key，THEN THE Web_UI SHALL 隐藏 Pexels 搜索入口

### 需求 4：LLM 关键词生成（可选）

**用户故事：** 作为用户，我希望能使用 LLM 自动生成 B-Roll 搜索关键词，以便在不确定搜索词时获得智能推荐。

#### 验收标准

1. WHERE LLM 关键词生成功能已启用（系统配置中已设置 LLM API Key），THE Web_UI SHALL 在 B-Roll 选择区域提供"AI 生成关键词"入口
2. WHEN 用户输入视频主题或描述并触发关键词生成时，THE LLM_Service SHALL 调用已配置的 LLM API 生成一组适合搜索 B-Roll 空镜素材的英文关键词列表
3. WHEN LLM 返回关键词列表后，THE Web_UI SHALL 展示生成的关键词，并允许用户编辑、删除或添加关键词
4. WHEN 用户确认关键词后，THE Web_UI SHALL 支持一键使用这些关键词触发 Pexels 搜索
5. IF LLM API 调用失败或超时（超过 30 秒），THEN THE LLM_Service SHALL 返回生成失败的错误提示

### 需求 5：混剪参数配置

**用户故事：** 作为用户，我希望能配置视频混剪的各项参数，以便根据不同场景需求定制输出视频的效果。

#### 验收标准

1. THE Web_UI SHALL 在智能视频剪辑页面展示混剪参数配置面板，包含以下可配置项：画面比例、转场效果、片段时长、拼接模式、输出视频数量
2. THE Web_UI SHALL 支持用户选择画面比例，可选值为：16:9（横屏）、9:16（竖屏）、1:1（方形），默认值为 9:16
3. THE Web_UI SHALL 支持用户选择转场效果，可选值为：无转场（None）、淡入（FadeIn）、淡出（FadeOut）、滑入（SlideIn）、滑出（SlideOut）、随机（Shuffle），默认值为无转场
4. THE Web_UI SHALL 支持用户设置片段时长（max_clip_duration），取值范围为 2 至 15 秒的整数，默认值为 5 秒
5. THE Web_UI SHALL 支持用户选择拼接模式，可选值为：随机打乱（random）、顺序排列（sequential），默认值为随机打乱
6. THE Web_UI SHALL 支持用户设置输出视频数量，取值范围为 1 至 5 的整数，默认值为 1
7. IF 用户输入的片段时长超出 2-15 秒范围或输出视频数量超出 1-5 范围，THEN THE Web_UI SHALL 提示参数超出范围并阻止提交

### 需求 6：MoviePy 混剪引擎 — 素材分段与拼接

**用户故事：** 作为用户，我希望混剪引擎能将所有视频素材按配置的片段时长切分并拼接，以便生成节奏紧凑的混剪视频。

#### 验收标准

1. WHEN 混剪任务启动时，THE Mixing_Engine SHALL 使用 MoviePy 的 `VideoFileClip` 读取所有输入的视频素材（A-Roll 和 B-Roll），并按照配置的 `max_clip_duration` 将每个视频切分为多个 Clip_Segment
2. WHEN 视频素材的剩余时长不足一个完整片段时，THE Mixing_Engine SHALL 保留该尾部片段（不丢弃）
3. WHEN 拼接模式为 random 时，THE Mixing_Engine SHALL 将所有 Clip_Segment 随机打乱后拼接
4. WHEN 拼接模式为 sequential 时，THE Mixing_Engine SHALL 仅取每个视频素材的第一个片段，按照素材的原始顺序拼接
5. THE Mixing_Engine SHALL 将每个 Clip_Segment 缩放至配置的目标画面比例对应的分辨率（16:9 → 1920×1080，9:16 → 1080×1920，1:1 → 1080×1080）
6. WHEN Clip_Segment 的原始宽高比与目标画面比例不一致时，THE Mixing_Engine SHALL 按比例缩放素材并使用黑色背景填充空白区域（letterbox/pillarbox），保持素材不变形
7. WHEN 配置了转场效果时，THE Mixing_Engine SHALL 在每个 Clip_Segment 上应用对应的转场动画效果
8. WHEN 转场效果为 Shuffle 时，THE Mixing_Engine SHALL 为每个 Clip_Segment 随机选择一种转场效果（FadeIn、FadeOut、SlideIn、SlideOut）

### 需求 7：MoviePy 混剪引擎 — 音频处理与时长匹配

**用户故事：** 作为用户，我希望混剪引擎能以 A-Roll 的音频时长为基准自动调整视频总时长，以便音画同步。

#### 验收标准

1. THE Mixing_Engine SHALL 提取所有 A-Roll 素材的音频轨道，按顺序拼接作为成品视频的主音频
2. THE Mixing_Engine SHALL 以主音频的总时长作为成品视频的目标时长
3. WHEN 所有 Clip_Segment 拼接后的总时长短于音频时长时，THE Mixing_Engine SHALL 循环复用已有的 Clip_Segment 直到视频时长达到或超过音频时长
4. WHEN 所有 Clip_Segment 拼接后的总时长超过音频时长时，THE Mixing_Engine SHALL 裁剪视频使其与音频时长匹配
5. THE Mixing_Engine SHALL 将每个处理完成的 Clip_Segment 写入临时文件，然后使用 FFmpeg concat demuxer 将所有临时文件串联为最终视频（H.264 视频编码 + AAC 音频编码），避免 MoviePy 逐段合并时反复重编码导致画质劣化
6. THE Mixing_Engine SHALL 在编码完成后清理所有临时 Clip_Segment 文件

### 需求 8：TTS 语音合成替换音频（可选）

**用户故事：** 作为用户，我希望在需要替换 A-Roll 原始音频时，能使用 TTS 从文本生成配音，以便为视频添加 AI 旁白。

#### 验收标准

1. WHERE TTS 功能已启用，THE Web_UI SHALL 在混剪参数配置面板中提供"使用 TTS 配音"开关
2. WHEN 用户启用 TTS 配音时，THE Web_UI SHALL 展示文本输入框和语音角色选择下拉框
3. THE Web_UI SHALL 从 TTS_Engine 获取可用语音角色列表（至少包含中文男声和中文女声各一种）供用户选择
4. WHEN 用户输入文本并触发 TTS 合成时，THE TTS_Engine SHALL 使用 Edge-TTS 将文本转换为音频文件并返回音频时长
5. WHEN TTS 合成完成后，THE Mixing_Engine SHALL 使用 TTS 生成的音频替代 A-Roll 原始音频作为成品视频的主音频，并以 TTS 音频时长作为视频目标时长
6. IF TTS 合成失败，THEN THE TTS_Engine SHALL 返回失败原因并允许用户重新触发合成

### 需求 9：BGM 背景音乐混合（可选）

**用户故事：** 作为用户，我希望能为混剪视频添加背景音乐，以便提升视频的观感和氛围。

#### 验收标准

1. WHERE BGM 功能已启用，THE Web_UI SHALL 在混剪参数配置面板中提供"添加背景音乐"开关
2. THE Web_UI SHALL 支持用户选择 BGM 来源：从素材库选择 `media_type` 为 `audio` 的文件，或选择"随机 BGM"由系统从内置曲库中随机选取
3. THE Web_UI SHALL 支持用户设置 BGM 音量比例，取值范围为 0.0 至 1.0，默认值为 0.2
4. WHEN 用户启用 BGM 时，THE Mixing_Engine SHALL 使用 MoviePy 的 `CompositeAudioClip` 将 BGM 音频与主音频混合，BGM 音量按照配置的比例调整
5. THE Mixing_Engine SHALL 将 BGM 音频循环播放至与视频时长匹配，并在结尾处添加 3 秒淡出效果

### 需求 10：混剪任务执行与输出

**用户故事：** 作为用户，我希望触发混剪后系统能异步执行任务并输出成品视频，以便我在等待期间处理其他工作。

#### 验收标准

1. WHEN 用户确认素材选择和参数配置后触发混剪时，THE Platform SHALL 创建一个 Mixing_Task 记录，包含唯一任务 ID、素材列表、参数配置和初始状态 `processing`
2. THE Platform SHALL 异步执行混剪任务，在任务执行期间用户可以离开当前页面
3. WHEN 配置的输出视频数量大于 1 时，THE Mixing_Engine SHALL 为每个版本独立执行混剪流程（random 模式下每个版本的片段排列顺序不同），生成对应数量的成品视频文件
4. WHEN 混剪任务完成后，THE Platform SHALL 将成品视频存储到 `storage/tasks/{task_id}/` 目录，文件命名为 `output-{version_number}.mp4`，并记录视频元数据（分辨率、时长、文件大小）
5. WHEN 混剪任务完成后，THE Platform SHALL 将任务状态更新为 `video_done`
6. IF 混剪过程中 MoviePy 或 FFmpeg 执行失败，THEN THE Platform SHALL 记录详细错误日志并将任务状态更新为 `failed`，同时记录失败原因
7. THE Web_UI SHALL 在混剪任务执行期间展示进度指示器，告知用户当前处理阶段（如"正在处理第 2/3 个版本"）

### 需求 11：混剪结果预览与审核提交

**用户故事：** 作为用户，我希望能预览混剪完成的视频并提交审核，以便运营人员进行终审。

#### 验收标准

1. WHEN 混剪任务状态为 `video_done` 时，THE Web_UI SHALL 展示成品视频的 HTML5 视频播放器，支持在浏览器中在线播放
2. WHEN 配置的输出视频数量大于 1 时，THE Web_UI SHALL 展示所有版本的视频列表，支持逐个切换预览
3. WHEN 用户确认视频质量后触发提交审核时，THE Platform SHALL 将任务状态更新为 `pending_review`
4. WHEN 任务状态为 `pending_review` 时，THE Review_System SHALL 在运营的待审核列表（`/api/reviews/pending`）中展示该任务
5. THE Web_UI SHALL 支持用户在提交审核前下载成品视频到本地

### 需求 12：混剪任务列表与状态管理

**用户故事：** 作为用户，我希望能查看所有混剪任务的状态和历史记录，以便跟踪工作进展。

#### 验收标准

1. THE Web_UI SHALL 在任务列表页面展示所有混剪任务，包含任务 ID、创建时间、当前状态和素材摘要信息
2. THE Platform SHALL 维护 Mixing_Task 的状态流转：`processing` → `video_done` → `pending_review` → `approved` / `rejected`；`processing` → `failed`
3. THE Web_UI SHALL 支持按任务状态筛选混剪任务列表
4. WHEN 用户点击某个混剪任务时，THE Web_UI SHALL 展示该任务的完整详情，包括素材列表、参数配置、成品视频预览
5. IF 混剪任务状态为 `failed`，THEN THE Web_UI SHALL 展示失败原因并提供"重新混剪"入口

### 需求 13：与现有审核系统集成

**用户故事：** 作为运营，我希望混剪生成的视频能进入现有的审核流程，以便统一管理所有待审核内容。

#### 验收标准

1. WHEN 混剪任务提交审核后，THE Review_System SHALL 在待审核列表中展示该任务的视频预览链接、素材信息和混剪参数
2. THE Review_System SHALL 支持运营对混剪视频执行"通过"或"拒绝"操作，复用现有的 `/api/reviews/{task_id}/approve` 和 `/api/reviews/{task_id}/reject` 接口
3. WHEN 运营拒绝混剪视频时，THE Review_System SHALL 要求运营填写拒绝原因
4. WHEN 运营通过混剪视频时，THE Platform SHALL 将任务状态更新为 `approved`
5. WHEN 运营拒绝混剪视频时，THE Platform SHALL 将任务状态更新为 `rejected`，并记录拒绝原因供用户查看

### 需求 14：与现有权限系统集成

**用户故事：** 作为管理员，我希望智能视频剪辑模块遵循现有的角色权限体系，以确保操作安全。

#### 验收标准

1. THE Platform SHALL 允许所有已认证用户（intern、operator、admin）执行以下智能视频剪辑操作：选择素材、配置参数、触发混剪、预览视频、提交审核、查看自己创建的混剪任务
2. THE Platform SHALL 限制仅 operator 和 admin 角色可执行审核操作：查看待审核的混剪任务、执行通过或拒绝操作
3. THE Platform SHALL 允许 admin 角色执行所有智能视频剪辑操作，包括查看所有用户的混剪任务和管理混剪引擎配置
4. WHEN 用户尝试执行超出角色权限的操作时，THE Platform SHALL 返回 HTTP 403 状态码和权限不足的错误信息

### 需求 15：混剪引擎配置管理

**用户故事：** 作为管理员，我希望能配置混剪引擎的默认参数和外部服务密钥，以便灵活调整平台行为。

#### 验收标准

1. THE Platform SHALL 支持管理员通过管理后台（`/admin/config`）配置混剪引擎的默认参数：默认画面比例、默认转场效果、默认片段时长、默认拼接模式
2. THE Platform SHALL 支持管理员通过管理后台配置 Pexels API Key
3. THE Platform SHALL 支持管理员通过管理后台配置 TTS 语音角色列表、语速和音量
4. THE Platform SHALL 支持管理员通过管理后台配置输出视频的编码参数：码率（默认 8M）、输出格式（默认 mp4）
5. WHEN 管理员修改混剪引擎配置后，THE Platform SHALL 立即生效，新创建的混剪任务使用更新后的配置值
6. THE Platform SHALL 将混剪引擎配置持久化存储到 `system_config` 数据库表，确保服务重启后配置不丢失

### 需求 16：前端步骤流程页面

**用户故事：** 作为用户，我希望智能视频剪辑页面提供清晰的步骤引导，以便我能按流程完成视频混剪操作。

#### 验收标准

1. THE Web_UI SHALL 在 `/mix` 路由下提供智能视频剪辑工作页面，采用与首页一致的 Apple 风格极简设计（白色背景、圆角卡片、淡紫渐变色调）
2. THE Web_UI SHALL 将混剪流程分为四个步骤展示：选择 A-Roll → 选择 B-Roll（可选）→ 配置参数 → 生成与预览
3. THE Web_UI SHALL 在页面顶部展示步骤进度指示器，标识当前所在步骤
4. THE Web_UI SHALL 支持用户在步骤之间前进和后退导航
5. THE Web_UI SHALL 在最后一步展示混剪任务的生成进度和成品视频预览播放器
6. THE Web_UI SHALL 使用 Alpine.js 管理页面状态和步骤切换，使用 Tailwind CSS 实现响应式布局
