# 实现计划：视频生产平台

## 概述

基于 MoneyPrinterTurbo 架构扩展的视频生产平台实现计划。按从底层到顶层的顺序组织：先基础设施（项目结构、数据模型、配置、认证），再业务服务层，最后前端 UI。所有代码基于 Python（FastAPI）+ HTML/Tailwind CSS + Alpine.js。

## 任务

- [x] 1. 项目结构与基础设施搭建
  - [x] 1.1 创建项目目录结构和依赖配置
    - 创建 `app/` 目录结构：`models/`、`services/`、`routers/`、`schemas/`、`utils/`、`templates/`、`static/`
    - 创建 `requirements.txt`，包含 fastapi、uvicorn、sqlalchemy、pydantic、python-jose、passlib、bcrypt、chromadb、httpx、python-multipart、edge-tts、hypothesis、pytest
    - 创建 `app/main.py` FastAPI 应用入口
    - 创建 `storage/assets/` 和 `storage/tasks/` 目录占位
    - _需求: 12.1_

  - [x] 1.2 实现 SQLite 数据库模型和初始化
    - 使用 SQLAlchemy 定义所有数据模型：User、Asset、Task、TaskAsset、BatchTask、ForbiddenWord、ReviewLog、SystemConfig
    - 实现数据库初始化脚本，创建表结构
    - 实现数据库会话管理（get_db 依赖注入）
    - 预置 SystemConfig 默认值（llm_api_url、llm_model、tts_voices、video_resolution 等）
    - _需求: 1.3, 8.1, 9.3, 13.6_

  - [ ]* 1.3 编写数据模型属性测试
    - **Property 1: 素材上传往返一致性** — 验证 Asset 模型创建后查询返回一致的元数据
    - **验证: 需求 1.1, 1.2, 1.3**

  - [x] 1.4 实现统一错误处理和响应格式
    - 定义错误码枚举和统一错误响应模型（ErrorResponse）
    - 实现 FastAPI 异常处理器，统一返回 `{"error": {"code", "message", "details"}}` 格式
    - 实现自定义异常类：AuthError、PermissionError、NotFoundError、ValidationError、StateTransitionError
    - _需求: 11.5_

  - [x] 1.5 实现日志系统
    - 配置 Python logging，输出到 `logs/error.log`、`logs/llm.log`、`logs/ffmpeg.log`、`logs/review.log`
    - 实现请求 ID 中间件，为每个请求生成唯一 ID 并注入日志上下文
    - _需求: 3.5, 7.6_

- [x] 2. 认证与权限系统
  - [x] 2.1 实现 JWT 认证服务（UserService）
    - 实现用户密码 bcrypt 加密和验证
    - 实现 JWT Token 生成（包含 user_id、role、过期时间）和验证
    - 实现 `/api/auth/login` 登录接口
    - 实现认证中间件（从请求头提取并验证 JWT Token）
    - _需求: 11.6_

  - [x] 2.2 实现角色权限控制中间件
    - 实现权限装饰器/依赖注入，根据角色和操作检查权限矩阵
    - Intern：浏览素材、生成文案、编辑文案、语音合成、视频合成、查看自己的任务
    - Operator：查看待审核列表、预览视频、通过/拒绝操作
    - Admin：所有操作
    - 超出权限返回 403 PERMISSION_DENIED
    - _需求: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [ ]* 2.3 编写认证与权限属性测试
    - **Property 30: 登录认证正确性** — 生成随机用户名密码组合，验证匹配时返回有效 JWT，不匹配时返回认证错误
    - **验证: 需求 11.6**

  - [ ]* 2.4 编写角色权限属性测试
    - **Property 29: 角色权限访问控制** — 生成随机角色和操作组合，验证权限矩阵执行正确
    - **验证: 需求 11.2, 11.3, 11.4, 11.5**

  - [x] 2.5 实现用户管理接口
    - 实现 `/api/users` CRUD 接口（GET 列表、POST 创建、PUT 更新、DELETE 删除）
    - 仅 Admin 角色可访问
    - 创建默认 Admin 用户的初始化逻辑
    - _需求: 11.1, 11.4_

- [x] 3. 检查点 - 基础设施验证
  - 确保所有测试通过，ask the user if questions arise。

- [x] 4. 系统配置服务（ConfigService）
  - [x] 4.1 实现配置服务和 API 接口
    - 实现 ConfigService：从 SystemConfig 表读写配置，内存缓存 + 写穿透策略确保即时生效
    - 实现 `GET /api/config` 和 `PUT /api/config` 接口，仅 Admin 可访问
    - 预置默认配置项：llm_api_url、llm_api_key、llm_model、tts_voices、tts_speed、tts_volume、video_resolution、video_bitrate、video_format、upload_max_size、upload_allowed_formats、batch_max_concurrency
    - _需求: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [ ]* 4.2 编写配置服务属性测试
    - **Property 31: 系统配置往返一致性与持久化** — 生成随机配置值，验证更新后查询返回一致
    - **验证: 需求 13.1, 13.2, 13.3, 13.4, 13.6**

  - [ ]* 4.3 编写配置变更即时生效属性测试
    - **Property 32: 配置变更即时生效** — 变更配置后验证下一次操作使用新值
    - **验证: 需求 13.5**

- [x] 5. 素材服务（AssetService）
  - [x] 5.1 实现素材上传接口
    - 实现 `POST /api/assets/upload` 接口，仅 Admin 可访问
    - 实现文件格式白名单校验（视频：mp4/mov/avi，图片：jpg/png/webp，音频：mp3/wav/aac）
    - 实现文件大小校验（从 ConfigService 读取上限）
    - 实现文件存储逻辑：保存到 `storage/assets/{asset_id}/original.{ext}`
    - 实现缩略图生成：视频取首帧、图片缩放，保存为 `thumbnail.jpg`
    - 记录素材元数据到 Asset 表（文件名、分类、文件大小、时长等）
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 5.2 编写素材上传属性测试
    - **Property 2: 非法上传拒绝** — 生成随机非法文件格式和超大文件，验证上传被拒绝且数据库无新增记录
    - **验证: 需求 1.4, 1.5**

  - [x] 5.3 实现素材检索与管理接口
    - 实现 `GET /api/assets` 接口：支持分类筛选、关键词搜索、分页（默认每页 20 条）
    - 实现 `GET /api/assets/{id}` 获取素材详情
    - 实现 `DELETE /api/assets/{id}` 删除素材（同时删除文件和数据库记录），仅 Admin 可访问
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 5.4 编写素材检索属性测试
    - **Property 3: 素材搜索过滤正确性** — 验证分类筛选和关键词搜索返回结果的正确性
    - **验证: 需求 2.1, 2.2**

  - [ ]* 5.5 编写素材分页属性测试
    - **Property 6: 素材分页约束** — 验证分页返回记录数不超过每页限制，所有页合集等于总记录集
    - **验证: 需求 2.5**

  - [ ]* 5.6 编写素材删除属性测试
    - **Property 5: 素材删除完整性** — 验证删除后文件和数据库记录同时不存在
    - **验证: 需求 2.4**

  - [ ]* 5.7 编写素材列表字段完整性属性测试
    - **Property 4: 素材列表字段完整性** — 验证返回记录包含所有必要字段且非空
    - **验证: 需求 2.3**

- [x] 6. 检查点 - 基础服务验证
  - 确保所有测试通过，ask the user if questions arise。

- [x] 7. 违禁词过滤服务（ForbiddenWordService）
  - [x] 7.1 实现 ChromaDB RAG 知识库集成
    - 初始化 ChromaDB 客户端和集合（forbidden_words、review_rejections）
    - 实现违禁词向量存储和语义检索
    - 实现精确匹配（关键词列表）+ 语义匹配（向量检索）双通道过滤
    - _需求: 4.1_

  - [x] 7.2 实现违禁词 CRUD 接口
    - 实现 `GET /api/forbidden-words` 获取违禁词列表，仅 Admin
    - 实现 `POST /api/forbidden-words` 添加违禁词，仅 Admin
    - 实现 `DELETE /api/forbidden-words/{id}` 删除违禁词，仅 Admin
    - 实现 `POST /api/forbidden-words/import` 批量导入违禁词，仅 Admin
    - 添加/删除/导入时同步更新 ChromaDB 向量库
    - _需求: 4.3_

  - [ ]* 7.3 编写违禁词 CRUD 属性测试
    - **Property 10: 违禁词 CRUD 往返一致性** — 验证添加后可检索、删除后不可检索、批量导入全部可检索
    - **验证: 需求 4.3**

  - [x] 7.4 实现违禁词检测接口
    - 实现 `POST /api/forbidden-words/check` 接口
    - 返回结果包含：匹配到的违禁词列表、在文案中的位置、替换建议、过滤状态（通过/包含违禁词）
    - _需求: 4.1, 4.2, 4.4_

  - [ ]* 7.5 编写违禁词检测属性测试
    - **Property 9: 违禁词检测完整性** — 生成包含随机违禁词的文案，验证所有违禁词被检测到并返回位置和建议
    - **验证: 需求 4.1, 4.2, 4.4**

- [x] 8. 文案服务（CopywritingService）
  - [x] 8.1 实现 LLM 文案生成接口
    - 实现 `POST /api/copywriting/generate` 接口
    - 从 ConfigService 读取 LLM API 配置（api_url、api_key、model）
    - 构建 prompt 模板，嵌入用户输入的主题
    - 调用 LLM API（兼容 OpenAI API 格式），实现超时控制（30秒）和自动重试（最多2次，间隔5秒）
    - 生成文案后自动调用 ForbiddenWordService 进行违禁词检测
    - 保存原始文案（copywriting_raw）和过滤后文案（copywriting_filtered）到 Task 记录
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 4.5_

  - [ ]* 8.2 编写 LLM Prompt 模板属性测试
    - **Property 7: LLM Prompt 模板包含** — 验证发送给 LLM 的 prompt 包含系统预设模板且主题正确嵌入
    - **验证: 需求 3.1, 3.2**

  - [ ]* 8.3 编写 LLM 调用失败处理属性测试
    - **Property 8: LLM 调用失败处理** — 模拟 API 失败/超时，验证返回错误提示并记录日志
    - **验证: 需求 3.5**

  - [ ]* 8.4 编写违禁词过滤保留对比记录属性测试
    - **Property 11: 违禁词过滤保留对比记录** — 验证系统同时保留原始文案和过滤后文案
    - **验证: 需求 4.5**

  - [x] 8.5 实现文案编辑与确认接口
    - 实现 `PUT /api/copywriting/{task_id}` 编辑文案接口，编辑后自动重新执行违禁词检测
    - 实现 `POST /api/copywriting/{task_id}/confirm` 确认锁定文案接口，锁定后拒绝编辑请求（返回 409 COPY_LOCKED）
    - 实现 `GET /api/copywriting/{task_id}` 获取文案详情接口
    - _需求: 5.1, 5.2, 5.3_

  - [ ]* 8.6 编写文案编辑触发重新过滤属性测试
    - **Property 12: 文案编辑触发重新过滤** — 验证修改文案后自动重新执行违禁词检测
    - **验证: 需求 5.2**

  - [ ]* 8.7 编写文案确认锁定属性测试
    - **Property 13: 文案确认锁定** — 验证确认后编辑请求被拒绝，文案内容不变
    - **验证: 需求 5.3**

- [x] 9. 语音合成服务（TTSService）
  - [x] 9.1 实现 TTS 语音合成接口
    - 实现 `POST /api/tts/{task_id}/synthesize` 接口，仅当文案状态为 copy_confirmed 时允许触发
    - 集成 Edge-TTS 库，从 ConfigService 读取语音参数（语速、音量）
    - 合成音频保存到 `storage/tasks/{task_id}/tts_audio.mp3`
    - 更新 Task 记录：tts_audio_path、tts_duration，状态转为 tts_done
    - _需求: 6.1, 6.2, 6.4_

  - [x] 9.2 实现语音角色列表和预览接口
    - 实现 `GET /api/tts/voices` 返回可用语音角色列表（至少包含男声和女声各一种中文语音）
    - 实现 `POST /api/tts/preview` 预览试听接口，生成短文本的语音片段
    - _需求: 6.3, 6.6_

  - [ ]* 9.3 编写 TTS 合成属性测试
    - **Property 14: TTS 合成产出与元数据** — 验证合成后产生非空音频文件，任务记录包含正确路径和时长
    - **验证: 需求 6.1, 6.4**

  - [ ]* 9.4 编写 TTS 失败处理属性测试
    - **Property 15: TTS 失败处理** — 模拟合成失败，验证返回失败原因且任务状态允许重试
    - **验证: 需求 6.5**

- [x] 10. 视频合成服务（CompositionService）
  - [x] 10.1 实现视频合成核心逻辑
    - 实现 FFmpeg 命令构建器：根据 A roll/B roll 素材列表、TTS 音频、过渡效果、分辨率/码率参数生成 FFmpeg 命令
    - 实现异步 FFmpeg 执行器：subprocess 调用 FFmpeg，捕获 stdout/stderr 输出
    - 实现合成流程：读取 TTS 音频时长 → 裁剪/拼接 A Roll → 裁剪/拼接 B Roll → 添加过渡效果 → 混合音频 → 编码输出
    - 输出视频保存到 `storage/tasks/{task_id}/output.mp4`
    - _需求: 7.1, 7.2, 7.3, 7.4_

  - [x] 10.2 实现视频合成 API 接口
    - 实现 `POST /api/composition/{task_id}/compose` 接口
    - 接收 A roll/B roll 素材 ID 列表、过渡效果、分辨率/码率参数
    - 从 ConfigService 读取默认视频输出参数
    - 合成完成后更新 Task 记录：video_path、video_resolution、video_duration、video_file_size，状态转为 video_done
    - 实现 `GET /api/composition/{task_id}/status` 查询合成进度
    - _需求: 7.1, 7.5, 7.7_

  - [ ]* 10.3 编写视频合成属性测试
    - **Property 16: 视频合成产出与元数据** — 验证合成完成后产生视频文件，任务记录包含分辨率、时长和文件大小
    - **验证: 需求 7.1, 7.5**

  - [ ]* 10.4 编写 A/B Roll 素材分离属性测试
    - **Property 17: A/B Roll 素材分离** — 验证合成请求中 A roll 和 B roll 素材分别指定并正确使用
    - **验证: 需求 7.2**

  - [ ]* 10.5 编写视频时长匹配属性测试
    - **Property 18: 视频时长匹配 TTS 音频时长** — 验证合成视频时长与 TTS 音频时长误差不超过 1 秒
    - **验证: 需求 7.3**

  - [ ]* 10.6 编写视频输出参数可配置属性测试
    - **Property 19: 视频输出参数可配置** — 验证合成视频匹配配置的分辨率和码率
    - **验证: 需求 7.7**

  - [ ]* 10.7 编写 FFmpeg 合成失败处理属性测试
    - **Property 20: FFmpeg 合成失败处理** — 模拟 FFmpeg 失败，验证记录错误日志并返回失败提示
    - **验证: 需求 7.6**

- [x] 11. 检查点 - 核心业务服务验证
  - 确保所有测试通过，ask the user if questions arise。

- [x] 12. 任务管理服务（TaskService）
  - [x] 12.1 实现任务状态机和生命周期管理
    - 实现任务状态机：draft → copy_confirmed → tts_done → video_done → pending_review → approved/rejected → published
    - 实现状态转换校验逻辑，拒绝非法状态转换（返回 409 INVALID_STATE_TRANSITION）
    - 视频合成完成后自动转为 pending_review
    - _需求: 8.1, 8.2_

  - [ ]* 12.2 编写任务状态机属性测试
    - **Property 21: 任务状态机不变量** — 生成随机状态转换序列，验证合法转换成功、非法转换被拒绝
    - **验证: 需求 8.1, 8.2, 10.1, 10.5, 10.6**

  - [x] 12.3 实现任务 CRUD 接口
    - 实现 `POST /api/tasks` 创建任务接口（创建 draft 状态的 Task）
    - 实现 `GET /api/tasks` 任务列表接口：支持状态筛选，Intern 仅看自己的任务，Operator 看待审核任务，Admin 看全部
    - 实现 `GET /api/tasks/{id}` 任务详情接口：返回文案内容、素材选择、视频预览路径
    - _需求: 8.1, 8.3, 8.4, 8.5_

  - [ ]* 12.4 编写任务列表状态筛选属性测试
    - **Property 22: 任务列表状态筛选** — 验证筛选返回的所有任务具有指定状态，且包含必要字段
    - **验证: 需求 8.3, 8.4**

- [x] 13. 批量任务服务（BatchService）
  - [x] 13.1 实现批量任务创建和执行
    - 实现 `POST /api/batches` 创建批量任务接口：接收主题列表和每主题版本数，创建 N × V 个独立 Task 并关联到 BatchTask
    - 实现 `POST /api/batches/upload-csv` CSV 主题列表上传接口
    - 实现 asyncio.Semaphore 并发控制（从 ConfigService 读取 batch_max_concurrency）
    - 实现批量执行引擎：按并发限制调度子任务，单个失败不影响其余
    - _需求: 9.1, 9.2, 9.3, 9.4, 9.6_

  - [x] 13.2 实现批量任务查询接口
    - 实现 `GET /api/batches` 批量任务列表接口
    - 实现 `GET /api/batches/{id}` 批量任务详情接口：返回子任务状态汇总和进度百分比（completed_tasks / total_tasks × 100）
    - _需求: 9.5_

  - [ ]* 13.3 编写批量任务创建属性测试
    - **Property 23: 批量任务创建正确性** — 生成随机主题数和版本数，验证创建 N×V 个 Task 且 total_tasks 正确
    - **验证: 需求 9.1, 9.2, 9.3**

  - [ ]* 13.4 编写批量任务并发限制属性测试
    - **Property 24: 批量任务并发限制** — 验证同时运行的子任务数不超过配置的最大并发数
    - **验证: 需求 9.4**

  - [ ]* 13.5 编写批量任务进度计算属性测试
    - **Property 25: 批量任务进度计算** — 验证进度百分比计算正确，各状态任务数之和等于 total_tasks
    - **验证: 需求 9.5**

  - [ ]* 13.6 编写批量任务故障隔离属性测试
    - **Property 26: 批量任务故障隔离** — 模拟子任务失败，验证其余子任务继续执行
    - **验证: 需求 9.6**

- [x] 14. 审核服务（ReviewService）
  - [x] 14.1 实现审核接口
    - 实现 `GET /api/reviews/pending` 获取待审核任务列表，仅 Operator/Admin
    - 实现 `POST /api/reviews/{task_id}/approve` 通过视频，更新 Task 状态为 approved
    - 实现 `POST /api/reviews/{task_id}/reject` 拒绝视频：校验拒绝原因非空（否则返回 400 REJECTION_REASON_REQUIRED），更新 Task 状态为 rejected
    - 记录 ReviewLog（reviewer_id、action、reason、topic、copywriting_snapshot）
    - _需求: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 14.2 实现审核拒绝写入 RAG 知识库
    - 拒绝时将拒绝原因 + 关联主题 + 文案快照写入 ChromaDB review_rejections 集合
    - 文案生成时从 review_rejections 集合检索相关拒绝历史，注入 LLM prompt 作为负面示例
    - _需求: 10.7_

  - [ ]* 14.3 编写审核拒绝必须填写原因属性测试
    - **Property 27: 审核拒绝必须填写原因** — 验证未提供拒绝原因时操作被拒绝
    - **验证: 需求 10.4**

  - [ ]* 14.4 编写审核拒绝写入 RAG 属性测试
    - **Property 28: 审核拒绝写入 RAG 知识库** — 验证拒绝后原因、主题和文案快照可从知识库检索到
    - **验证: 需求 10.7**

- [x] 15. 检查点 - 全部后端服务验证
  - 确保所有测试通过，ask the user if questions arise。

- [x] 16. 前端页面 - 布局与导航
  - [x] 16.1 创建前端基础结构
    - 创建 HTML 基础模板（base.html）：引入 Tailwind CSS CDN、Alpine.js CDN
    - 实现响应式导航栏：素材库、文案生成、视频合成、任务列表、审核（按角色显示）、系统配置（仅 Admin）
    - 实现登录页面和 JWT Token 管理（localStorage 存储，请求拦截器自动附加）
    - _需求: 12.1, 12.4, 11.6_

  - [x] 16.2 实现素材库页面
    - 素材列表页：缩略图网格展示、分类筛选标签、关键词搜索框、分页控件
    - 素材上传弹窗（Admin）：文件选择、分类标签选择、上传进度条
    - 素材删除确认弹窗（Admin）
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 1.1, 1.2_

- [x] 17. 前端页面 - 视频生成流程
  - [x] 17.1 实现文案生成与编辑页面
    - 步骤引导 UI（Step 1: 输入主题 → Step 2: 编辑文案 → Step 3: 选择素材 → Step 4: 合成视频）
    - 主题输入表单，触发文案生成，显示加载进度指示器
    - 文案编辑器：高亮显示违禁词标记、实时编辑、确认锁定按钮
    - _需求: 3.1, 5.1, 5.4, 12.2, 12.3_

  - [x] 17.2 实现语音合成与素材选择页面
    - 语音角色选择下拉框 + 预览试听按钮
    - TTS 合成触发按钮 + 进度指示器
    - A Roll / B Roll 素材选择面板：从素材库按分类筛选并选择
    - _需求: 6.1, 6.3, 6.6, 7.2_

  - [x] 17.3 实现视频合成与预览页面
    - 合成参数配置（过渡效果、分辨率、码率）
    - 合成触发按钮 + 进度指示器
    - 合成完成后视频预览播放器
    - _需求: 7.1, 7.4, 7.5, 7.7, 12.3_

- [x] 18. 前端页面 - 任务管理与审核
  - [x] 18.1 实现任务列表与详情页面
    - 任务列表：状态筛选标签、主题/状态/创建时间/更新时间列表展示
    - 任务详情页：文案内容、素材选择、生成视频预览、状态流转时间线
    - _需求: 8.3, 8.4, 8.5_

  - [x] 18.2 实现批量任务页面
    - 批量提交表单：多主题输入（文本框多行输入 + CSV 上传）、每主题版本数配置
    - 批量任务列表：进度百分比、子任务状态汇总
    - 批量任务详情：子任务列表、对比预览面板
    - _需求: 9.1, 9.5, 9.7_

  - [x] 18.3 实现审核页面
    - 待审核列表（Operator/Admin）：视频预览、文案内容、素材信息
    - 通过/拒绝操作按钮，拒绝时弹出原因填写弹窗
    - _需求: 10.2, 10.3, 10.4_

- [x] 19. 前端页面 - 管理功能
  - [x] 19.1 实现用户管理页面（Admin）
    - 用户列表：用户名、角色、创建时间
    - 创建/编辑/删除用户弹窗
    - _需求: 11.1, 11.4_

  - [x] 19.2 实现系统配置页面（Admin）
    - LLM 配置表单：API 地址、API Key、模型名称
    - TTS 配置表单：语音角色列表、语速、音量
    - 视频输出配置表单：分辨率、码率、格式
    - 素材上传配置表单：最大文件大小、允许格式
    - 保存后即时生效提示
    - _需求: 13.1, 13.2, 13.3, 13.4, 13.5_

  - [x] 19.3 实现违禁词管理页面（Admin）
    - 违禁词列表：关键词、分类、建议替换
    - 添加/删除违禁词、批量导入（CSV 上传）
    - _需求: 4.3_

- [x] 20. 前端交互反馈与响应式适配
  - [x] 20.1 实现全局交互反馈
    - 统一 Toast 通知组件：成功/错误/警告提示
    - 所有耗时操作的加载状态指示器（spinner / progress bar）
    - 表单提交的即时反馈（按钮 loading 状态、禁用重复提交）
    - _需求: 12.3, 12.5_

  - [x] 20.2 响应式布局适配
    - 确保所有页面在 1024px 及以上宽度正常显示
    - 导航栏在小屏幕下折叠为汉堡菜单
    - _需求: 12.4_

- [x] 21. 集成联调与最终检查点
  - [x] 21.1 串联完整视频生成流程
    - 连接所有后端服务：创建任务 → 生成文案 → 违禁词过滤 → 编辑确认 → TTS 合成 → 选择素材 → 视频合成 → 自动提交审核
    - 确保前端页面正确调用后端 API，状态流转完整
    - _需求: 8.1, 8.2_

  - [x] 21.2 串联批量生成与审核流程
    - 连接批量任务创建 → 并发执行 → 审核通过/拒绝 → RAG 知识库写入
    - 验证故障隔离和进度计算
    - _需求: 9.1, 9.4, 9.6, 10.7_

- [x] 22. 最终检查点 - 全部功能验证
  - 确保所有测试通过，ask the user if questions arise。

## 备注

- 标记 `*` 的子任务为可选测试任务，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保需求可追溯
- 检查点任务用于阶段性验证，确保增量开发的正确性
- 属性测试使用 Hypothesis 库，验证设计文档中定义的 32 个正确性属性
- 单元测试和属性测试互补：单元测试捕获具体 bug，属性测试验证通用正确性
