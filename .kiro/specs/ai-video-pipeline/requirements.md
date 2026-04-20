# Requirements Document

## Introduction

本系统是一个 AI 短视频全链路自动化管线（AI Video Pipeline），使用 Go 语言实现。系统从一个主题（Topic）出发，自动完成脚本生成、素材采集、视频合成和平台发布的完整流程。系统遵循 Harness 模式（包裹器/接口模式），将工作流划分为 4 个强隔离阶段，每个阶段通过标准化的 Harness 接口进行通信，主控引擎（Pipeline_Engine）仅负责阶段间的数据传递与编排。

## Glossary

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
- **Script_Output**：脚本生成阶段的标准化输出，包含分镜词列表（scenes）和口播稿列表（narrations）的 JSON 结构
- **Material_Output**：素材生成阶段的标准化输出，包含音频文件路径列表和空镜文件路径列表
- **Synthesis_Output**：合成阶段的标准化输出，包含最终 MP4 成片的文件路径
- **Distribution_Output**：发布阶段的标准化输出，包含发布状态和发布 URL
- **run_id**：管线单次执行的唯一标识符，用于追踪和日志关联

## Requirements

### Requirement 1: Harness 基础架构

**User Story:** 作为开发者，我希望所有阶段都通过统一的 Harness 接口进行封装，以便主控引擎无需关心各阶段的内部实现细节。

#### Acceptance Criteria

1. THE Harness SHALL 定义统一的 `Execute(ctx context.Context, input []byte) (output []byte, err error)` 接口，接收标准化输入参数并返回标准化输出结果
2. WHEN Harness 接收到输入参数时，THE Harness SHALL 对输入参数进行 JSON 格式校验，校验失败时返回包含错误原因的标准化错误响应
3. WHEN Harness 内部调用外部服务失败时，THE Harness SHALL 按照配置的重试策略（最大重试次数、重试间隔）自动重试
4. WHEN Harness 内部调用外部服务超过配置的超时时间时，THE Harness SHALL 通过 context 取消机制中断该调用并返回超时错误
5. WHEN Harness 重试次数耗尽仍然失败时，THE Harness SHALL 执行降级策略并返回降级结果或包含最终错误信息的失败状态
6. THE Harness SHALL 将每次执行的输入摘要、输出摘要、耗时（毫秒）和状态记录到结构化日志中

### Requirement 2: 主控编排引擎

**User Story:** 作为开发者，我希望有一个主控引擎按顺序编排 4 个阶段的 Harness，以便实现从主题到成片发布的全自动流程。

#### Acceptance Criteria

1. WHEN Pipeline_Engine 接收到一个主题（Topic）字符串时，THE Pipeline_Engine SHALL 依次调用 Script_Harness、Material_Harness、Synthesis_Harness、Distribution_Harness
2. THE Pipeline_Engine SHALL 将上一个 Harness 的标准化输出字节流作为下一个 Harness 的输入传递
3. WHEN 任一阶段的 Harness 返回非 nil 错误时，THE Pipeline_Engine SHALL 停止后续阶段的执行并返回包含失败阶段名称和错误信息的管线执行结果
4. THE Pipeline_Engine SHALL 在管线执行完成后返回包含各阶段执行状态、耗时（毫秒）和最终结果的 PipelineReport 结构体
5. WHEN Pipeline_Engine 启动执行时，THE Pipeline_Engine SHALL 为本次执行生成唯一的 run_id（UUID v4 格式）用于追踪和日志关联

### Requirement 3: 脚本生成阶段（Script_Harness）

**User Story:** 作为内容创作者，我希望输入一个主题后系统能自动生成包含分镜词和口播稿的标准化脚本，以便后续阶段使用。

#### Acceptance Criteria

1. WHEN Script_Harness 接收到一个主题字符串时，THE LLM_Wrapper SHALL 使用预设的 System Prompt 和该主题调用 DeepSeek API 生成脚本内容
2. WHEN LLM_Wrapper 收到 DeepSeek API 的原始响应时，THE Script_Harness SHALL 将响应解析为包含 scenes 列表和 narrations 列表的 Script_Output JSON 结构
3. THE Script_Harness SHALL 校验解析后的 Script_Output 中 scenes 列表和 narrations 列表的长度一致且均大于 0
4. IF Script_Harness 解析 LLM 响应失败，THEN THE Script_Harness SHALL 重新调用 LLM_Wrapper 并在 Prompt 中附加格式纠正指令
5. WHEN Script_Harness 成功生成 Script_Output 时，THE Script_Harness SHALL 返回包含 scenes 列表和 narrations 列表的 JSON 字节流
6. THE Script_Output SHALL 遵循以下 JSON 结构：每个 scene 包含 scene_id（正整数）和 description（非空字符串），每个 narration 包含 scene_id（正整数）和 text（非空字符串）

### Requirement 4: 素材生成阶段（Material_Harness）

**User Story:** 作为内容创作者，我希望系统能根据脚本自动生成配音音频和空镜素材，以便用于视频合成。

#### Acceptance Criteria

1. WHEN Material_Harness 接收到 Script_Output 时，THE Material_Harness SHALL 使用 goroutine 并行启动 TTS_Wrapper 和 Media_Wrapper 两个子任务
2. WHEN TTS_Wrapper 接收到 narrations 列表时，THE TTS_Wrapper SHALL 为每条 narration 调用 Edge-TTS 语音合成服务生成对应的 MP3 音频文件
3. WHEN TTS_Wrapper 完成音频生成时，THE TTS_Wrapper SHALL 返回按 scene_id 升序排列的音频文件绝对路径列表
4. WHEN Media_Wrapper 接收到 scenes 列表时，THE Media_Wrapper SHALL 为每个 scene 的 description 调用 Pexels API 搜索并下载匹配的无版权空镜视频素材
5. WHEN Media_Wrapper 完成素材下载时，THE Media_Wrapper SHALL 返回按 scene_id 升序排列的空镜文件绝对路径列表
6. WHEN TTS_Wrapper 和 Media_Wrapper 均完成时，THE Material_Harness SHALL 将两者的输出合并为包含 audio_paths 列表和 video_paths 列表的 Material_Output
7. IF TTS_Wrapper 或 Media_Wrapper 中任一子任务返回错误，THEN THE Material_Harness SHALL 在 Material_Output 中标记失败的子任务名称并返回部分结果与错误信息

### Requirement 5: 合成量产阶段（Synthesis_Harness）

**User Story:** 作为内容创作者，我希望系统能将音频和空镜素材自动合成为完整的 MP4 视频，以便直接用于发布。

#### Acceptance Criteria

1. WHEN Synthesis_Harness 接收到 Material_Output 时，THE Render_Wrapper SHALL 根据 audio_paths 列表和 video_paths 列表构建 FFmpeg 渲染命令行参数
2. THE Render_Wrapper SHALL 以 exec.Command 方式调用 FFmpeg 子进程执行视频合成任务
3. WHEN FFmpeg 子进程执行完成且退出码为 0 时，THE Synthesis_Harness SHALL 返回包含最终 MP4 文件绝对路径的 Synthesis_Output
4. IF FFmpeg 子进程退出码不为 0，THEN THE Synthesis_Harness SHALL 捕获 FFmpeg 的 stderr 输出并返回包含错误详情的失败状态
5. THE Render_Wrapper SHALL 在调用 FFmpeg 前校验 audio_paths 和 video_paths 中所有文件路径的存在性，缺失文件时返回包含缺失文件路径的校验错误
6. WHILE FFmpeg 子进程执行中，THE Synthesis_Harness SHALL 通过 context 超时机制监控子进程的运行时间，超过配置的超时阈值时终止子进程并返回超时错误

### Requirement 6: 投放发布阶段（Distribution_Harness）

**User Story:** 作为内容创作者，我希望系统能将合成的视频自动发布到社交平台，以便减少手动操作。

#### Acceptance Criteria

1. WHEN Distribution_Harness 接收到 Synthesis_Output 和视频标题字符串时，THE Publish_Wrapper SHALL 调用目标平台的发布接口上传视频文件
2. WHEN Publish_Wrapper 发布成功时，THE Distribution_Harness SHALL 返回包含 status 为 "success" 和 publish_url 字符串的 Distribution_Output
3. IF Publish_Wrapper 发布失败，THEN THE Distribution_Harness SHALL 返回包含 status 为 "failed" 和 error_message 字符串的 Distribution_Output
4. THE Publish_Wrapper SHALL 在发布前校验视频文件路径的存在性和文件大小（字节）是否在平台配置的最大文件大小限制范围内
5. THE Distribution_Harness SHALL 支持通过配置文件指定目标发布平台名称（如 "xiaohongshu"），通过平台名称选择对应的 Publish_Wrapper 实现

### Requirement 7: 数据模型标准化与序列化

**User Story:** 作为开发者，我希望各阶段之间传递的数据都有明确的 Go struct 定义和 JSON 序列化能力，以便保证数据一致性和可调试性。

#### Acceptance Criteria

1. THE Pipeline_Engine SHALL 使用 Go struct 定义 Script_Output、Material_Output、Synthesis_Output 和 Distribution_Output 四种数据模型，每个字段包含 `json` tag
2. THE Pipeline_Engine SHALL 提供将各阶段输出 struct 序列化为 JSON 字节流的 Marshal 函数
3. THE Pipeline_Engine SHALL 提供将 JSON 字节流反序列化为对应数据模型 struct 的 Unmarshal 函数
4. 对于所有有效的阶段输出 struct 实例，Marshal 后再 Unmarshal SHALL 产生与原始 struct 深度相等的结果（往返一致性）
5. WHEN Unmarshal 接收到不符合数据模型结构的 JSON 字节流时，THE Pipeline_Engine SHALL 返回包含具体字段名称和错误原因的校验失败 error

### Requirement 8: 配置管理

**User Story:** 作为开发者，我希望所有外部服务的连接参数和运行策略都通过统一的配置管理，以便灵活调整而无需修改代码。

#### Acceptance Criteria

1. THE Pipeline_Engine SHALL 从 YAML 配置文件或环境变量中加载所有外部服务的连接参数（API 密钥、端点地址、超时时间秒数）
2. THE Pipeline_Engine SHALL 从配置中加载每个 Harness 的重试策略参数（最大重试次数为正整数、重试间隔为正整数毫秒、超时阈值为正整数秒）
3. WHEN 配置文件缺失必要参数时，THE Pipeline_Engine SHALL 在启动时输出缺失的配置项名称列表并以非零退出码终止
4. THE Pipeline_Engine SHALL 支持通过环境变量覆盖配置文件中的同名参数，环境变量优先级高于配置文件
