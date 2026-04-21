# 任务清单：智能视频剪辑模块

## 任务

- [x] 1. 数据模型与状态机扩展
  - [x] 1.1 在 `app/models/database.py` 的 Task 模型中新增 `mix_params`（Text, nullable）、`video_paths`（Text, nullable）、`error_message`（Text, nullable）字段
  - [x] 1.2 在 `app/services/task_service.py` 的 `VALID_TRANSITIONS` 中新增混剪状态流转路径：`draft` → `processing`、`copy_confirmed` → `processing`、`processing` → `video_done`/`failed`、`failed` → `processing`、`rejected` → `processing`
  - [x] 1.3 在 `app/models/init_db.py` 中确保数据库迁移正确创建新字段
  - [x] 1.4 在 `app/utils/errors.py` 的 `ErrorCode` 枚举中新增 `MIXING_ERROR = "MIXING_ERROR"`

- [x] 2. Pydantic 请求/响应模型
  - [x] 2.1 创建 `app/schemas/mix.py`，定义 `MixCreateRequest`（含 topic、a_roll_asset_ids、b_roll_asset_ids、aspect_ratio、transition、clip_duration、concat_mode、video_count、tts_text、tts_voice、bgm_enabled、bgm_asset_id、bgm_volume 字段及验证规则）、`MixCreateResponse`、`MixStatusResponse`、`SubmitReviewResponse`、`RetryResponse`
  - [x] 2.2 在 `app/schemas/mix.py` 中定义 `PexelsSearchRequest`、`PexelsVideoItem`、`PexelsSearchResponse`、`PexelsDownloadRequest`、`PexelsDownloadResponse`
  - [x] 2.3 在 `app/schemas/mix.py` 中定义 `KeywordGenerateRequest`、`KeywordGenerateResponse`

- [x] 3. MoviePy 混剪引擎核心
  - [x] 3.1 创建 `app/services/mixing_engine.py`，实现 `combine_videos()` 函数：接收视频路径列表、音频文件路径、aspect_ratio、concat_mode、transition、max_clip_duration 参数；使用 MoviePy `VideoFileClip` 读取视频并按 max_clip_duration 切分为片段；根据 concat_mode 排列片段（random=shuffle, sequential=首段）；逐个片段缩放至目标分辨率（保持比例 + 黑色填充）、应用转场效果、写入临时文件；循环片段直到总时长 ≥ 音频时长；使用 FFmpeg concat demuxer 串联临时文件为最终视频；清理临时文件
  - [x] 3.2 在 `mixing_engine.py` 中实现 `extract_audio_from_videos()` 函数：从多个 A-Roll 视频中提取音频轨道，按顺序拼接为单个音频文件，返回总时长
  - [x] 3.3 在 `mixing_engine.py` 中实现 `mix_bgm()` 函数：使用 MoviePy `CompositeAudioClip` 将 BGM 与主音频混合，BGM 按配置音量调整，循环至视频时长，结尾 3 秒淡出
  - [x] 3.4 在 `mixing_engine.py` 中实现转场效果函数（复用 MoneyPrinterTurbo 的 `video_effects.py` 逻辑）：fadein_transition、fadeout_transition、slidein_transition、slideout_transition，以及 shuffle 模式随机选择

- [x] 4. 混剪任务编排服务
  - [x] 4.1 创建 `app/services/mixing_service.py`，实现 `MixingService` 类的 `create_mix_task()` 方法：验证素材存在性、创建 Task 记录（status=processing, mix_params=JSON）、保存 TaskAsset 关联、启动后台线程执行混剪
  - [x] 4.2 实现 `MixingService.execute_mix()` 方法：获取任务参数、如果启用 TTS 则先调用 TTS_Engine 合成音频、解析素材路径、对每个 version 调用 mixing_engine.combine_videos()、如果启用 BGM 则混合背景音乐、保存输出文件到 `storage/tasks/{task_id}/output-{n}.mp4`、更新任务状态为 video_done、异常时更新为 failed 并记录 error_message
  - [x] 4.3 实现 `MixingService.get_status()` 方法：返回任务状态、进度信息、视频路径列表、元数据
  - [x] 4.4 实现 `MixingService.submit_review()` 方法：验证任务状态为 video_done，调用 `transition_state()` 转换为 pending_review
  - [x] 4.5 实现 `MixingService.retry()` 方法：验证任务状态为 failed 或 rejected，重置为 processing 并重新启动混剪

- [x] 5. Pexels 搜索服务
  - [x] 5.1 创建 `app/services/pexels_service.py`，实现 `PexelsService` 类的 `search_videos()` 方法：从 system_config 读取 Pexels API Key、调用 Pexels API 搜索视频、按 aspect_ratio 过滤结果、返回视频列表（url、thumbnail、duration、尺寸）
  - [x] 5.2 实现 `PexelsService.download_video()` 方法：下载视频到本地缓存目录、创建 Asset 记录（category=pexels_broll）、返回 Asset

- [x] 6. API 路由层
  - [x] 6.1 创建 `app/routers/mix.py`，实现 `POST /api/mix/create` 端点：接收 MixCreateRequest、调用 MixingService.create_mix_task()、返回 MixCreateResponse；权限 require_role("intern", "operator", "admin")
  - [x] 6.2 实现 `GET /api/mix/{task_id}/status` 端点：调用 MixingService.get_status()、返回 MixStatusResponse
  - [x] 6.3 实现 `POST /api/mix/{task_id}/submit-review` 端点：调用 MixingService.submit_review()、返回 SubmitReviewResponse
  - [x] 6.4 实现 `POST /api/mix/{task_id}/retry` 端点：调用 MixingService.retry()、返回 RetryResponse
  - [x] 6.5 实现 `POST /api/mix/pexels/search` 端点：调用 PexelsService.search_videos()、返回 PexelsSearchResponse
  - [x] 6.6 实现 `POST /api/mix/pexels/download` 端点：调用 PexelsService.download_video()、返回 PexelsDownloadResponse
  - [x] 6.7 实现 `POST /api/mix/keywords/generate` 端点：调用现有 LLM_Service 生成关键词、返回 KeywordGenerateResponse
  - [x] 6.8 在 `app/main.py` 中注册 mix router，在 `app/routers/pages.py` 中新增 `GET /mix` 页面路由

- [x] 7. 前端页面
  - [x] 7.1 创建 `app/templates/mix.html`，实现四步流程页面骨架：步骤进度指示器、步骤容器、前进/后退导航按钮；使用 Alpine.js `x-data="mixPage()"` 管理状态；采用与首页一致的 Apple 风格设计（白色背景、圆角卡片、淡紫渐变色调、Tailwind CSS）
  - [x] 7.2 实现步骤 1（选择 A-Roll）：调用 `/api/assets` 加载视频素材列表、支持多选、展示缩略图/文件名/时长、支持拖拽排序和移除
  - [x] 7.3 实现步骤 2（选择 B-Roll）：调用 `/api/assets` 加载素材列表、支持多选和移除；条件展示 Pexels 搜索入口和 AI 关键词生成入口
  - [x] 7.4 实现步骤 3（配置参数）：画面比例选择器、转场效果选择器、片段时长滑块/输入框、拼接模式选择器、输出数量选择器；可选 TTS 配音面板（文本输入 + 语音选择）；可选 BGM 面板（来源选择 + 音量滑块）
  - [x] 7.5 实现步骤 4（生成与预览）：触发混剪按钮、进度指示器（轮询 `/api/mix/{task_id}/status`）、成品视频 HTML5 播放器、多版本切换、提交审核按钮、下载按钮

- [x] 8. 权限与配置集成
  - [x] 8.1 在 `app/utils/auth.py` 的 `PERMISSION_MATRIX` 中新增混剪相关权限项：`create_mix`、`view_mix_status`、`submit_mix_review`、`retry_mix`、`search_pexels`、`generate_keywords`
  - [x] 8.2 在管理后台配置页面（`admin_config.html`）中新增混剪引擎默认参数配置区域：默认画面比例、默认转场效果、默认片段时长、默认拼接模式、Pexels API Key、视频编码码率

- [x] 9. 清理与集成
  - [x] 9.1 删除旧的 `app/services/composition_service.py` 和 `app/routers/composition.py` 和 `app/schemas/composition.py`，从 `app/main.py` 中移除 composition router 注册
  - [x] 9.2 更新首页 `home.html` 中"智能视频剪辑"卡片的链接确保指向 `/mix`（已有，验证即可）
