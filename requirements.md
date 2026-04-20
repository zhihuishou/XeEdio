# 需求文档：AI 短视频全链路管线

## 简介

本系统是一个 AI 短视频全链路自动化管线（AI Video Pipeline），用于从一个主题（Topic）出发，自动完成脚本生成、素材采集、视频合成和平台发布的完整流程。系统遵循 Harness 原则（包裹器/接口模式），将工作流划分为 4 个强隔离阶段，每个阶段通过标准化的 Harness 接口进行通信，主控引擎仅负责阶段间的数据传递与编排。

## 术语表

- **Pipeline_Engine**：主控编排引擎，负责按顺序调度各阶段 Harness，读取上一阶段输出并传递给下一阶段
- **Harness**：包裹器/接口层，封装某一阶段的全部外部调用，提供标准化输入输出、重试、超时和降级能力
- **Script_Harness**：阶段 1 的 Harness，负责调用 LLM 生成脚本
- **Material_Harness**：阶段 2 的 Harness，负责并行生成音频素材和视觉素材
- **Synthesis_Harness**：阶段 3 的 Harness，负责调用 FFmpeg 合成最终视频
- **Distribution_Harness**：阶段 4 的 Harness，负责将视频发布到社交平台
- **LLM_Wrapper**：Script_Harness 内部的 LLM 调用封装，负责与 DeepSeek API 通信
- **TTS_Wrapper**：Material_Harness 内部的语音合成封装，负责将口播稿转为音频文件
- **Media_Wrapper**：Material_Harness 内部的素材采集封装，负责从 Pexels 图库获取空镜素材
- **Render_Wrapper**：Synthesis_Harness 内部的渲染封装，负责调用 FFmpeg 子进程合成视频
- **Publish_Wrapper**：Distribution_Harness 内部的发布封装，负责自动化发布到目标平台
- **Script_Output**：脚本生成阶段的标准化输出，包含分镜词列表和口播稿列表的 JSON 结构
- **Material_Output**：素材生成阶段的标准化输出，包含音频文件路径列表和空镜文件路径列表
- **Synthesis_Output**：合成阶段的标准化输出，包含最终 MP4 成片的文件路径
- **Distribution_Output**：发布阶段的标准化输出，包含发布状态和发布 URL

## 需求

### 需求 1：Harness 基础架构

**用户故事：** 作为开发者，我希望所有阶段都通过统一的 Harness 接口进行封装，以便主控引擎无需关心各阶段的内部实现细节。

#### 验收标准

1. THE Harness SHALL 定义统一的 `execute` 接口，接收标准化输入参数并返回标准化输出结果
2. WHEN Harness 接收到输入参数时，THE Harness SHALL 对输入参数进行类型和格式校验，校验失败时返回包含错误原因的标准化错误响应
3. WHEN Harness 内部调用外部服务失败时，THE Harness SHALL 按照配置的重试策略（最大重试次数、重试间隔）自动重试
4. WHEN Harness 内部调用外部服务超过配置的超时时间时，THE Harness SHALL 中断该调用并返回超时错误
5. WHEN Harness 重试次数耗尽仍然失败时，THE Harness SHALL 执行降级策略并返回降级结果或明确的失败状态
6. THE Harness SHALL 将每次执行的输入、输出、耗时和状态记录到结构化日志中

### 需求 2：主控编排引擎

**用户故事：** 作为开发者，我希望有一个主控引擎按顺序编排 4 个阶段的 Harness，以便实现从主题到成片发布的全自动流程。

#### 验收标准

1. WHEN Pipeline_Engine 接收到一个主题（Topic）时，THE Pipeline_Engine SHALL 依次调用 Script_Harness、Material_Harness、Synthesis_Harness、Distribution_Harness
2. THE Pipeline_Engine SHALL 将上一个 Harness 的标准化输出作为下一个 Harness 的输入传递
3. WHEN 任一阶段的 Harness 返回失败状态时，THE Pipeline_Engine SHALL 停止后续阶段的执行并返回包含失败阶段名称和错误信息的管线执行结果
4. THE Pipeline_Engine SHALL 在管线执行完成后返回包含各阶段执行状态、耗时和最终结果的汇总报告
5. WHEN Pipeline_Engine 启动执行时，THE Pipeline_Engine SHALL 为本次执行生成唯一的运行 ID（run_id）用于追踪和日志关联

### 需求 3：脚本生成阶段（Script Harness）

**用户故事：** 作为内容创作者，我希望输入一个主题后系统能自动生成包含分镜词和口播稿的标准化脚本，以便后续阶段使用。

#### 验收标准

1. WHEN Script_Harness 接收到一个主题字符串时，THE LLM_Wrapper SHALL 使用预设的 System Prompt 和该主题调用 DeepSeek API 生成脚本内容
2. WHEN LLM_Wrapper 收到 DeepSeek API 的原始响应时，THE Script_Harness SHALL 将响应解析为包含分镜词列表（scenes）和口播稿列表（narrations）的标准化 JSON 结构
3. THE Script_Harness SHALL 校验解析后的 Script_Output 中分镜词列表和口播稿列表的长度一致且均不为空
4. IF Script_Harness 解析 LLM 响应失败，THEN THE Script_Harness SHALL 重新调用 LLM_Wrapper 并在 Prompt 中附加格式纠正指令
5. WHEN Script_Harness 成功生成 Script_Output 时，THE Script_Harness SHALL 返回包含 scenes 列表和 narrations 列表的标准化 JSON 对象
6. THE Script_Output SHALL 遵循以下 JSON 结构：每个 scene 包含 scene_id（整数）和 description（字符串），每个 narration 包含 scene_id（整数）和 text（字符串）

### 需求 4：素材生成阶段（Material Harness）

**用户故事：** 作为内容创作者，我希望系统能根据脚本自动生成配音音频和空镜素材，以便用于视频合成。

#### 验收标准

1. WHEN Material_Harness 接收到 Script_Output 时，THE Material_Harness SHALL 并行启动 TTS_Wrapper 和 Media_Wrapper 两个子任务
2. WHEN TTS_Wrapper 接收到口播稿列表时，THE TTS_Wrapper SHALL 为每条口播稿调用 Edge-TTS 语音合成服务生成对应的音频文件
3. WHEN TTS_Wrapper 完成音频生成时，THE TTS_Wrapper SHALL 返回按 scene_id 排序的音频文件路径列表
4. WHEN Media_Wrapper 接收到分镜词列表时，THE Media_Wrapper SHALL 为每个分镜词调用 Pexels API 搜索并下载匹配的无版权空镜素材
5. WHEN Media_Wrapper 完成素材下载时，THE Media_Wrapper SHALL 返回按 scene_id 排序的空镜文件路径列表
6. WHEN TTS_Wrapper 和 Media_Wrapper 均完成时，THE Material_Harness SHALL 将两者的输出合并为包含音频路径列表和空镜路径列表的 Material_Output
7. IF TTS_Wrapper 或 Media_Wrapper 中任一子任务失败，THEN THE Material_Harness SHALL 在 Material_Output 中标记失败的子任务并返回部分结果与错误信息

### 需求 5：合成量产阶段（Synthesis Harness）

**用户故事：** 作为内容创作者，我希望系统能将音频和空镜素材自动合成为完整的 MP4 视频，以便直接用于发布。

#### 验收标准

1. WHEN Synthesis_Harness 接收到 Material_Output 时，THE Render_Wrapper SHALL 根据音频路径列表和空镜路径列表构建 FFmpeg 渲染时间轴
2. THE Render_Wrapper SHALL 以异步子进程方式调用 FFmpeg 执行视频合成任务
3. WHEN FFmpeg 子进程执行完成且退出码为 0 时，THE Synthesis_Harness SHALL 返回包含最终 MP4 文件路径的 Synthesis_Output
4. IF FFmpeg 子进程退出码不为 0，THEN THE Synthesis_Harness SHALL 捕获 FFmpeg 的 stderr 输出并返回包含错误详情的失败状态
5. THE Render_Wrapper SHALL 在调用 FFmpeg 前校验所有输入文件路径的存在性，缺失文件时返回明确的校验错误
6. WHILE FFmpeg 子进程执行中，THE Synthesis_Harness SHALL 监控子进程的运行时间，超过配置的超时阈值时终止子进程

### 需求 6：投放发布阶段（Distribution Harness）

**用户故事：** 作为内容创作者，我希望系统能将合成的视频自动发布到社交平台，以便减少手动操作。

#### 验收标准

1. WHEN Distribution_Harness 接收到 Synthesis_Output 和视频标题时，THE Publish_Wrapper SHALL 调用目标平台的发布接口上传视频
2. WHEN Publish_Wrapper 发布成功时，THE Distribution_Harness SHALL 返回包含发布状态（成功）和发布 URL 的 Distribution_Output
3. IF Publish_Wrapper 发布失败，THEN THE Distribution_Harness SHALL 返回包含发布状态（失败）和错误原因的 Distribution_Output
4. THE Publish_Wrapper SHALL 在发布前校验视频文件路径的存在性和文件大小是否在平台限制范围内
5. THE Distribution_Harness SHALL 支持配置目标发布平台（如小红书），通过配置切换不同平台的发布逻辑

### 需求 7：数据模型标准化与序列化

**用户故事：** 作为开发者，我希望各阶段之间传递的数据都有明确的结构定义和序列化能力，以便保证数据一致性和可调试性。

#### 验收标准

1. THE Pipeline_Engine SHALL 使用强类型数据模型定义 Script_Output、Material_Output、Synthesis_Output 和 Distribution_Output
2. THE Pipeline_Engine SHALL 提供将各阶段输出序列化为 JSON 字符串的能力
3. THE Pipeline_Engine SHALL 提供将 JSON 字符串反序列化为对应数据模型的能力
4. 对于所有有效的阶段输出对象，序列化后再反序列化 SHALL 产生与原始对象等价的结果（往返一致性）
5. WHEN 反序列化接收到不符合数据模型结构的 JSON 时，THE Pipeline_Engine SHALL 返回包含具体字段错误信息的校验失败结果

### 需求 8：配置管理

**用户故事：** 作为开发者，我希望所有外部服务的连接参数和运行策略都通过统一的配置管理，以便灵活调整而无需修改代码。

#### 验收标准

1. THE Pipeline_Engine SHALL 从配置文件或环境变量中加载所有外部服务的连接参数（API 密钥、端点地址、超时时间）
2. THE Pipeline_Engine SHALL 从配置中加载每个 Harness 的重试策略参数（最大重试次数、重试间隔、超时阈值）
3. WHEN 配置文件缺失必要参数时，THE Pipeline_Engine SHALL 在启动时报告缺失的配置项并拒绝启动
4. THE Pipeline_Engine SHALL 支持通过环境变量覆盖配置文件中的同名参数
