# Implementation Plan: AI 短视频全链路自动化管线

## Overview

基于 Harness 模式，使用 Go 语言实现 AI 短视频全链路自动化管线。按照从底层数据模型到顶层编排引擎的顺序，逐步构建 4 个阶段的 Harness 及其内部 Wrapper，最终通过 Pipeline_Engine 串联整个流程。所有外部依赖通过接口注入，便于测试时 mock。

## Tasks

- [x] 1. 初始化项目结构与数据模型
  - [x] 1.1 初始化 Go 模块与项目目录结构
    - 创建 `ai-video-pipeline/` 项目根目录
    - 运行 `go mod init` 初始化模块
    - 创建 `cmd/pipeline/`、`internal/config/`、`internal/engine/`、`internal/harness/`、`internal/wrapper/`、`internal/model/` 目录
    - 添加 `pgregory.net/rapid` 和 `github.com/google/uuid` 和 `gopkg.in/yaml.v3` 依赖
    - _Requirements: 1.1, 7.1_

  - [x] 1.2 定义数据模型（model.go）
    - 在 `internal/model/model.go` 中定义 `Scene`、`Narration`、`ScriptOutput`、`MaterialOutput`、`SynthesisOutput`、`DistributionOutput`、`PipelineInput` 结构体，所有字段包含 `json` tag
    - 实现 `ScriptOutput` 的校验函数 `Validate()`：校验 scenes 和 narrations 长度一致且均大于 0，每个 scene_id 为正整数、description 非空，每个 narration 的 scene_id 为正整数、text 非空
    - 实现各数据模型的 `Marshal` 和 `Unmarshal` 辅助函数，Unmarshal 失败时返回包含具体字段名称和错误原因的 error
    - 定义 `StageResult` 和 `PipelineReport` 结构体
    - _Requirements: 3.6, 7.1, 7.2, 7.3, 7.4, 7.5, 2.4_

  - [ ]* 1.3 编写数据模型属性测试
    - **Property 14: 数据模型序列化往返一致性** — 对任意有效的 ScriptOutput、MaterialOutput、SynthesisOutput、DistributionOutput 实例，Marshal 后 Unmarshal 应与原始实例深度相等
    - **Validates: Requirements 7.4**

  - [ ]* 1.4 编写 ScriptOutput 校验属性测试
    - **Property 8: ScriptOutput 校验** — 对任意 ScriptOutput 实例，校验函数应在 scenes/narrations 长度一致且均大于 0 且字段合法时通过，否则返回具体原因
    - **Validates: Requirements 3.3, 3.6**

  - [ ]* 1.5 编写反序列化校验属性测试
    - **Property 15: 反序列化校验错误** — 对任意不符合数据模型结构的 JSON 字节流，Unmarshal 应返回非 nil 错误，且错误信息包含具体字段名称和错误原因
    - **Validates: Requirements 7.5**

- [x] 2. 实现配置管理
  - [x] 2.1 实现配置加载（config.go）
    - 在 `internal/config/config.go` 中定义 `Config`、`DeepSeekConfig`、`EdgeTTSConfig`、`PexelsConfig`、`FFmpegConfig`、`PublishConfig`、`HarnessesConfig`、`HarnessRetryConfig` 结构体
    - 实现 `Load(path string) (*Config, error)` 函数：从 YAML 文件加载配置
    - 实现环境变量覆盖逻辑：环境变量优先级高于配置文件
    - 实现必要参数缺失校验：缺失时返回包含所有缺失参数名称列表的 error
    - 创建默认 `config.yaml` 配置文件模板
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [ ]* 2.2 编写配置加载往返属性测试
    - **Property 16: 配置加载往返一致性** — 对任意有效 Config 实例，序列化为 YAML 后通过 Load 加载，得到的 Config 应与原始实例深度相等
    - **Validates: Requirements 8.1, 8.2**

  - [ ]* 2.3 编写配置缺失参数属性测试
    - **Property 17: 配置缺失参数校验** — 对任意缺失至少一个必要参数的 YAML 配置，Load 应返回非 nil 错误，且错误信息包含所有缺失参数名称
    - **Validates: Requirements 8.3**

  - [ ]* 2.4 编写环境变量覆盖属性测试
    - **Property 18: 环境变量覆盖配置** — 对任意配置参数名称和两个不同值（YAML 中一个、环境变量中一个），Load 后该参数值应等于环境变量中的值
    - **Validates: Requirements 8.4**

- [x] 3. 实现 Harness 基础架构
  - [x] 3.1 定义 Harness 接口与 BaseHarness（harness.go）
    - 在 `internal/harness/harness.go` 中定义 `Harness` 接口：`Name() string` 和 `Execute(ctx context.Context, input []byte) (output []byte, err error)`
    - 实现 `BaseHarness` 结构体：包含 name、RetryConfig、logger 字段
    - 实现 `ExecuteWithRetry` 方法：封装重试逻辑（初始调用 + N 次重试）、context 超时机制、降级函数调用
    - 实现 JSON 输入校验辅助函数 `ValidateJSONInput`
    - 实现结构化日志记录：每次执行记录输入摘要、输出摘要、耗时（毫秒）和状态
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [ ]* 3.2 编写无效 JSON 输入属性测试
    - **Property 1: 无效 JSON 输入被拒绝** — 对任意非法 JSON 字节流，调用 Execute 时应返回非 nil 错误，且错误信息包含输入校验失败原因
    - **Validates: Requirements 1.2**

  - [ ]* 3.3 编写重试与降级属性测试
    - **Property 2: 重试与降级行为一致性** — 对任意 Harness 配置（最大重试次数 N ≥ 1）和总是失败的外部服务调用，Harness 应恰好调用 N+1 次，且最终执行降级或返回失败状态
    - **Validates: Requirements 1.3, 1.5**

  - [ ]* 3.4 编写超时中断属性测试
    - **Property 3: 超时中断** — 对任意超时阈值 T > 0 和响应时间超过 T 的外部服务调用，Harness 应在 T + 合理误差内返回超时错误
    - **Validates: Requirements 1.4**

- [x] 4. Checkpoint — 确保基础架构测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 5. 实现脚本生成阶段
  - [x] 5.1 实现 LLM_Wrapper（llm.go）
    - 在 `internal/wrapper/llm.go` 中实现 `LLMWrapper` 结构体
    - 定义 `LLMClient` 接口：`Generate(ctx context.Context, systemPrompt, userPrompt string) (string, error)`，便于 mock
    - 实现 `DeepSeekLLMClient`：通过 HTTP POST 调用 DeepSeek API，发送 system prompt 和 user prompt，返回原始文本响应
    - _Requirements: 3.1_

  - [x] 5.2 实现 Script_Harness（script.go）
    - 在 `internal/harness/script.go` 中实现 `ScriptHarness` 结构体，组合 `BaseHarness` 和 `LLMClient` 接口
    - 实现 `Execute` 方法：解析输入 topic → 调用 LLM → 解析 LLM 响应为 ScriptOutput → 校验 scenes/narrations → 返回 JSON
    - 实现解析失败时的格式纠正重试逻辑：在 Prompt 中附加格式纠正指令重新调用 LLM
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 5.3 编写 Script_Harness 单元测试
    - 使用 mock LLMClient 测试正常解析路径
    - 测试 LLM 响应解析失败时的格式纠正重试逻辑（mock 第一次返回非法格式，验证第二次调用包含格式纠正指令）
    - _Requirements: 3.1, 3.2, 3.4_

- [x] 6. 实现素材生成阶段
  - [x] 6.1 实现 TTS_Wrapper（tts.go）
    - 在 `internal/wrapper/tts.go` 中定义 `TTSClient` 接口：`Synthesize(ctx context.Context, sceneID int, text string) (string, error)`
    - 实现 `EdgeTTSClient`：通过 exec.Command 调用 edge-tts CLI 生成 MP3 文件，返回文件绝对路径
    - _Requirements: 4.2, 4.3_

  - [x] 6.2 实现 Media_Wrapper（media.go）
    - 在 `internal/wrapper/media.go` 中定义 `MediaClient` 接口：`Search(ctx context.Context, sceneID int, description string) (string, error)`
    - 实现 `PexelsMediaClient`：通过 HTTP GET 调用 Pexels API 搜索视频，下载到本地，返回文件绝对路径
    - _Requirements: 4.4, 4.5_

  - [x] 6.3 实现 Material_Harness（material.go）
    - 在 `internal/harness/material.go` 中实现 `MaterialHarness` 结构体，组合 `BaseHarness`、`TTSClient` 和 `MediaClient` 接口
    - 实现 `Execute` 方法：解析 Script_Output → 使用 goroutine 并行调用 TTS 和 Media → 按 scene_id 升序排列结果 → 合并为 Material_Output → 返回 JSON
    - 实现部分失败处理：任一子任务失败时标记失败子任务名称，保留成功子任务的部分结果
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [ ]* 6.4 编写 Wrapper 输出排序属性测试
    - **Property 9: Wrapper 输出按 scene_id 排序** — 对任意随机顺序的 narrations 或 scenes 列表，TTS_Wrapper 和 Media_Wrapper 返回的文件路径列表应按 scene_id 升序排列
    - **Validates: Requirements 4.3, 4.5**

  - [ ]* 6.5 编写 Material_Output 合并属性测试
    - **Property 10: Material_Output 合并正确性** — 对任意 TTS 返回的音频路径列表 A 和 Media 返回的视频路径列表 V，合并后 audio_paths == A 且 video_paths == V
    - **Validates: Requirements 4.6**

  - [ ]* 6.6 编写部分失败处理属性测试
    - **Property 11: 部分失败处理** — 当 TTS 或 Media 中恰好一个返回错误时，Material_Harness 应返回包含失败子任务名称的错误信息，同时保留成功子任务的部分结果
    - **Validates: Requirements 4.7**

- [x] 7. 实现合成阶段
  - [x] 7.1 实现 Render_Wrapper（render.go）
    - 在 `internal/wrapper/render.go` 中定义 `Renderer` 接口：`Render(ctx context.Context, audioPaths, videoPaths []string) (string, error)`
    - 实现 `FFmpegRenderer`：构建 FFmpeg 命令行参数、校验所有输入文件存在性、通过 exec.Command 执行 FFmpeg 子进程、捕获 stderr
    - 实现 context 超时监控：超时时终止子进程
    - _Requirements: 5.1, 5.2, 5.4, 5.5, 5.6_

  - [x] 7.2 实现 Synthesis_Harness（synthesis.go）
    - 在 `internal/harness/synthesis.go` 中实现 `SynthesisHarness` 结构体，组合 `BaseHarness` 和 `Renderer` 接口
    - 实现 `Execute` 方法：解析 Material_Output → 调用 Renderer → 返回 Synthesis_Output JSON
    - _Requirements: 5.1, 5.2, 5.3_

  - [ ]* 7.3 编写 FFmpeg 命令构建属性测试
    - **Property 12: FFmpeg 命令构建** — 对任意非空的 audio_paths 和 video_paths 列表，构建的 FFmpeg 命令行参数应包含所有输入文件路径，且输出文件以 .mp4 结尾
    - **Validates: Requirements 5.1**

  - [ ]* 7.4 编写文件存在性校验属性测试
    - **Property 13: 文件存在性校验** — 对任意文件路径列表（部分存在部分不存在），校验函数应返回所有不存在的文件路径集合
    - **Validates: Requirements 5.5, 6.4**

  - [ ]* 7.5 编写 Synthesis_Harness 单元测试
    - 使用 mock Renderer 测试 FFmpeg 成功路径（退出码 0，返回 MP4 路径）
    - 测试 FFmpeg 失败路径（非零退出码，错误包含 stderr）
    - _Requirements: 5.3, 5.4_

- [x] 8. Checkpoint — 确保各阶段 Harness 测试通过
  - 确保所有测试通过，如有问题请向用户确认。

- [x] 9. 实现投放发布阶段
  - [x] 9.1 实现 Publish_Wrapper（publish.go）
    - 在 `internal/wrapper/publish.go` 中定义 `Publisher` 接口：`Publish(ctx context.Context, videoPath string, title string) (publishURL string, err error)`
    - 实现一个示例平台的 `Publisher`（如 `XiaohongshuPublisher`）：发布前校验视频文件存在性和文件大小限制、调用平台 API 上传视频
    - _Requirements: 6.1, 6.4_

  - [x] 9.2 实现 Distribution_Harness（distribution.go）
    - 在 `internal/harness/distribution.go` 中实现 `DistributionHarness` 结构体，组合 `BaseHarness` 和 `Publisher` 接口
    - 实现 `Execute` 方法：解析 Synthesis_Output → 调用 Publisher → 返回 Distribution_Output JSON（成功时 status="success" + publish_url，失败时 status="failed" + error_message）
    - 实现通过配置文件指定目标平台名称选择对应 Publisher 实现的工厂逻辑
    - _Requirements: 6.1, 6.2, 6.3, 6.5_

  - [ ]* 9.3 编写 Distribution_Harness 单元测试
    - 使用 mock Publisher 测试发布成功路径（验证 status="success" 和 publish_url）
    - 测试发布失败路径（验证 status="failed" 和 error_message）
    - 测试平台选择逻辑（配置不同平台名称，验证选择正确的 Publisher 实现）
    - _Requirements: 6.2, 6.3, 6.5_

- [x] 10. 实现主控编排引擎
  - [x] 10.1 实现 Pipeline_Engine（engine.go）
    - 在 `internal/engine/engine.go` 中实现 `PipelineEngine` 结构体，包含 `stages []Harness` 和 logger
    - 实现 `Run(ctx context.Context, topic string) PipelineReport` 方法：
      - 生成 UUID v4 格式的 run_id
      - 依次调用 4 个 Harness，将上一阶段输出作为下一阶段输入
      - 任一阶段失败时停止后续阶段，将未执行阶段标记为 "skipped"
      - 记录各阶段耗时（毫秒）和状态
      - 返回完整的 PipelineReport
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 10.2 编写管线顺序执行属性测试
    - **Property 4: 管线顺序执行与数据传递** — 对任意主题字符串和 4 个 mock Harness，Pipeline_Engine 应按 Script → Material → Synthesis → Distribution 顺序调用，且每个阶段收到的输入等于上一阶段的输出
    - **Validates: Requirements 2.1, 2.2**

  - [ ]* 10.3 编写管线错误传播属性测试
    - **Property 5: 管线错误传播与停止** — 对任意失败阶段位置 i ∈ {1,2,3,4}，当第 i 个阶段返回错误时，Pipeline_Engine 应不调用后续阶段，且 PipelineReport 包含失败阶段名称和错误信息
    - **Validates: Requirements 2.3**

  - [ ]* 10.4 编写 PipelineReport 完整性属性测试
    - **Property 6: PipelineReport 完整性** — 对任意管线执行（成功或失败），PipelineReport 应包含所有已执行阶段的 StageResult，未执行阶段 status 为 "skipped"
    - **Validates: Requirements 2.4**

  - [ ]* 10.5 编写 run_id 唯一性属性测试
    - **Property 7: run_id 唯一性与格式** — 对任意两次独立执行，run_id 应互不相同，且符合 UUID v4 格式
    - **Validates: Requirements 2.5**

- [x] 11. 实现入口与集成
  - [x] 11.1 实现 main.go 入口
    - 在 `cmd/pipeline/main.go` 中实现程序入口
    - 加载配置文件（支持命令行参数指定路径）
    - 配置缺失必要参数时输出缺失项名称列表并以非零退出码终止
    - 根据配置初始化各 Wrapper 和 Harness 实例
    - 组装 PipelineEngine，从命令行参数读取 topic，调用 Run 并输出 PipelineReport
    - _Requirements: 2.1, 8.1, 8.3_

  - [x] 11.2 创建默认配置文件 config.yaml
    - 创建包含所有配置项的 YAML 模板文件，含注释说明各参数用途
    - 包含 DeepSeek、Edge-TTS、Pexels、FFmpeg、Publish、Harnesses 各节的默认值
    - _Requirements: 8.1, 8.2_

- [x] 12. Final Checkpoint — 确保所有测试通过
  - 确保所有测试通过，如有问题请向用户确认。

## Notes

- 标记 `*` 的任务为可选任务，可跳过以加速 MVP 开发
- 每个任务引用了具体的需求编号，确保可追溯性
- 所有外部依赖通过接口注入，测试时使用 mock 实现
- 属性测试使用 `pgregory.net/rapid` 库，每个属性测试至少运行 100 次迭代
- 属性测试注释格式：`// Feature: ai-video-pipeline, Property {N}: {property_text}`
- Checkpoint 任务用于增量验证，确保每个阶段的实现正确后再继续
