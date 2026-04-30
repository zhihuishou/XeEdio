# 需求文档：对话式 Agent（Conversational Agent）

## 简介

当前 XeEdio 视频制作平台采用"一次性指令"模式：用户输入一句话，系统生成视频，流程结束。用户无法追问、修改或迭代。本功能将平台升级为多轮对话模式，引入对话式 Agent，使用户能够通过自然语言与系统持续交互——发出指令、查看结果、追问修改、迭代优化——实现类似 Genspark / VibeEdit 的智能剪辑助手体验。

MVP 范围：多轮对话 + SSE 实时推送 + 工具调用 + Agent 追问能力。不包含时间线可视化编辑、Style Skills、Beat-synced cutting 或增量时间线修改（modify_timeline）。

## 术语表

- **Conversation_Agent**：对话式智能体，负责接收用户消息、调用 LLM 进行意图分析、执行工具、返回结果的核心服务组件
- **Conversation**：一次完整的多轮对话会话，包含用户与 Agent 之间的所有消息交换记录
- **Conversation_Message**：对话中的单条消息，包含角色（user / assistant / tool）、内容和元数据
- **Agent_Loop**：Agent 的决策循环，LLM 根据对话历史决定下一步动作（调用工具 / 追问用户 / 直接回复），工具执行结果回注对话历史后 LLM 再次决策，直到产出最终回复或达到循环上限
- **Tool_Registry**：工具注册表，定义 Agent 可调用的工具集合及其 OpenAI Function Calling 格式的描述
- **SSE_Stream**：基于 Server-Sent Events 协议的实时事件推送通道，用于向前端推送 Agent 的思考过程、工具调用、结果和最终回复
- **SSE_Event**：SSE 通道中的单个事件，包含事件类型（thinking / tool_call / tool_result / ask_user / message / complete / error）和数据载荷
- **Function_Calling**：OpenAI 的函数调用格式，LLM 通过结构化 JSON 输出指定要调用的工具名称和参数
- **MixingService**：现有的混剪任务编排服务，负责创建和执行视频混剪任务
- **Asset**：素材文件（视频/图片/音频），存储在平台素材库中
- **Asset_Analysis**：素材的 VLM 分析结果，包含内容描述、角色分类、场景标签等结构化信息

## 需求

### 需求 1：对话会话管理

**用户故事：** 作为视频制作人员，我希望能够创建和管理多轮对话会话，以便在一个连续的上下文中与 Agent 交互完成视频制作任务。

#### 验收标准

1. WHEN 用户发起新对话请求时，THE Conversation_Agent SHALL 创建一个新的 Conversation 并返回唯一的会话标识符
2. WHEN 用户在已有 Conversation 中发送消息时，THE Conversation_Agent SHALL 将该消息追加到对应会话的消息历史中
3. THE Conversation_Agent SHALL 为每个 Conversation 维护完整的消息历史，包含所有 user、assistant 和 tool 角色的 Conversation_Message
4. WHEN 用户请求查看历史对话列表时，THE Conversation_Agent SHALL 返回该用户的所有 Conversation，按最近更新时间降序排列
5. WHEN 用户选择一个历史 Conversation 时，THE Conversation_Agent SHALL 加载并返回该会话的完整消息历史
6. WHEN 用户删除一个 Conversation 时，THE Conversation_Agent SHALL 移除该会话及其所有关联的 Conversation_Message

### 需求 2：Agent 决策循环

**用户故事：** 作为视频制作人员，我希望 Agent 能够自主分析我的意图并决定下一步动作（调用工具、追问我、或直接回复），以便我通过自然语言高效完成视频制作。

#### 验收标准

1. WHEN 用户发送消息时，THE Conversation_Agent SHALL 将完整的对话历史（包含系统提示词、所有 Conversation_Message）发送给 LLM，由 LLM 通过 Function_Calling 格式决定下一步动作
2. WHEN LLM 返回 Function_Calling 指令时，THE Conversation_Agent SHALL 从 Tool_Registry 中查找对应工具并执行，将执行结果作为 tool 角色的 Conversation_Message 追加到对话历史，然后再次调用 LLM 进行决策
3. WHEN LLM 返回纯文本回复（无 Function_Calling）时，THE Conversation_Agent SHALL 将该回复作为 assistant 角色的 Conversation_Message 追加到对话历史，并作为最终回复返回给用户
4. THE Conversation_Agent SHALL 将单次用户消息触发的 Agent_Loop 迭代次数限制为最多 10 次
5. IF Agent_Loop 达到 10 次迭代上限，THEN THE Conversation_Agent SHALL 终止循环并向用户返回一条说明性消息，告知已达到处理步骤上限
6. IF 工具执行过程中发生异常，THEN THE Conversation_Agent SHALL 将错误信息作为 tool 角色的 Conversation_Message 追加到对话历史，并由 LLM 决定如何向用户解释该错误
7. WHEN LLM 判断需要用户提供更多信息时，THE Conversation_Agent SHALL 生成追问消息并返回给用户，等待用户回复后继续 Agent_Loop

### 需求 3：工具注册与执行

**用户故事：** 作为视频制作人员，我希望 Agent 能够调用混剪、素材搜索、任务查询等工具，以便通过对话完成实际的视频制作操作。

#### 验收标准

1. THE Tool_Registry SHALL 注册以下工具，每个工具包含名称、描述和 OpenAI Function_Calling 格式的参数定义：create_mix（创建混剪任务）、get_task_status（查询任务状态）、analyze_assets（触发素材分析）、search_assets（搜索素材）
2. WHEN Conversation_Agent 调用 create_mix 工具时，THE Tool_Registry SHALL 委托 MixingService 创建混剪任务，并返回任务 ID 和初始状态
3. WHEN Conversation_Agent 调用 get_task_status 工具时，THE Tool_Registry SHALL 查询指定任务的当前状态、进度和结果（包括生成的视频路径）
4. WHEN Conversation_Agent 调用 analyze_assets 工具时，THE Tool_Registry SHALL 触发指定素材的 VLM 分析流程，并返回分析状态
5. WHEN Conversation_Agent 调用 search_assets 工具时，THE Tool_Registry SHALL 按文件名或描述在用户的素材库中搜索匹配的 Asset，并返回匹配结果列表
6. IF Conversation_Agent 调用了 Tool_Registry 中不存在的工具名称，THEN THE Tool_Registry SHALL 返回工具未找到的错误信息

### 需求 4：SSE 实时事件推送

**用户故事：** 作为视频制作人员，我希望在对话过程中实时看到 Agent 的思考过程、工具调用和结果，以便了解 Agent 正在做什么并获得即时反馈。

#### 验收标准

1. WHEN 用户建立 SSE 连接时，THE SSE_Stream SHALL 通过 GET /api/chat/{conversation_id}/stream 端点建立持久的 Server-Sent Events 连接
2. WHEN Agent_Loop 开始处理用户消息时，THE SSE_Stream SHALL 推送 thinking 类型的 SSE_Event，包含 Agent 当前的处理状态描述
3. WHEN Conversation_Agent 决定调用工具时，THE SSE_Stream SHALL 推送 tool_call 类型的 SSE_Event，包含工具名称和调用参数
4. WHEN 工具执行完成时，THE SSE_Stream SHALL 推送 tool_result 类型的 SSE_Event，包含工具执行结果
5. WHEN Conversation_Agent 生成追问消息时，THE SSE_Stream SHALL 推送 ask_user 类型的 SSE_Event，包含追问内容
6. WHEN Conversation_Agent 生成最终回复时，THE SSE_Stream SHALL 推送 message 类型的 SSE_Event，包含回复内容
7. WHEN Agent_Loop 处理完成时，THE SSE_Stream SHALL 推送 complete 类型的 SSE_Event，标志本轮处理结束
8. IF Agent_Loop 处理过程中发生不可恢复的错误，THEN THE SSE_Stream SHALL 推送 error 类型的 SSE_Event，包含错误描述信息
9. THE SSE_Stream SHALL 在推送 complete 或 error 类型的 SSE_Event 后关闭连接

### 需求 5：对话上下文记忆

**用户故事：** 作为视频制作人员，我希望 Agent 能记住之前的对话内容（用了哪些素材、生成了什么视频、设置了什么参数），以便我可以基于之前的结果进行追问和修改。

#### 验收标准

1. THE Conversation_Agent SHALL 在每个 Conversation 中维护结构化的上下文状态，包含当前关联的 Asset 列表和已生成的任务 ID 列表
2. WHEN 用户通过对话选择或上传素材时，THE Conversation_Agent SHALL 将这些 Asset 的标识符记录到当前 Conversation 的上下文状态中
3. WHEN create_mix 工具成功创建任务时，THE Conversation_Agent SHALL 将新任务的 ID 记录到当前 Conversation 的上下文状态中
4. WHEN 用户发出修改指令（如"时长改成40秒重新剪"）时，THE Conversation_Agent SHALL 基于上下文状态中记录的素材和参数信息，使用修改后的参数调用 create_mix 工具重新生成视频
5. WHEN 用户引用之前的对话内容（如"第2条开头换一下"）时，THE Conversation_Agent SHALL 结合对话历史和上下文状态理解用户的引用对象，并执行相应操作
6. THE Conversation_Agent SHALL 将上下文状态持久化存储，以便用户在重新打开历史 Conversation 时恢复完整的上下文

### 需求 6：消息发送 API

**用户故事：** 作为前端应用，我需要一个 API 端点来向 Agent 发送用户消息并触发 Agent 处理流程。

#### 验收标准

1. WHEN 前端通过 POST /api/chat/send 发送消息时，THE Conversation_Agent SHALL 接收包含 conversation_id 和消息内容的请求体
2. WHEN 请求中未包含 conversation_id 时，THE Conversation_Agent SHALL 自动创建新的 Conversation 并在响应中返回新的 conversation_id
3. WHEN 消息发送成功时，THE Conversation_Agent SHALL 返回确认响应并异步启动 Agent_Loop 处理流程
4. IF 指定的 conversation_id 不存在或不属于当前用户，THEN THE Conversation_Agent SHALL 返回 404 错误响应
5. IF 该 Conversation 当前已有正在执行的 Agent_Loop，THEN THE Conversation_Agent SHALL 返回 409 冲突错误响应，提示用户等待当前处理完成

### 需求 7：前端对话界面改造

**用户故事：** 作为视频制作人员，我希望聊天界面支持多轮对话模式，能实时展示 Agent 的思考过程和工具调用，以便获得流畅的交互体验。

#### 验收标准

1. THE 前端界面 SHALL 基于 conversation_id 管理对话状态，使用 EventSource API 建立 SSE 连接接收实时事件
2. WHEN 收到 thinking 类型的 SSE_Event 时，THE 前端界面 SHALL 在对话区域显示 Agent 思考状态指示器
3. WHEN 收到 tool_call 类型的 SSE_Event 时，THE 前端界面 SHALL 在对话区域显示工具调用信息，包含工具名称和参数摘要
4. WHEN 收到 tool_result 类型的 SSE_Event 时，THE 前端界面 SHALL 在对话区域显示工具执行结果
5. WHEN 收到 ask_user 类型的 SSE_Event 时，THE 前端界面 SHALL 显示 Agent 的追问内容并激活输入框供用户回复
6. WHEN 收到 message 类型的 SSE_Event 时，THE 前端界面 SHALL 在对话区域显示 Agent 的最终回复
7. WHEN 收到 error 类型的 SSE_Event 时，THE 前端界面 SHALL 在对话区域显示错误提示信息
8. WHILE Agent_Loop 正在处理中，THE 前端界面 SHALL 禁用消息输入框的发送功能，防止用户在处理期间发送新消息
9. WHEN 收到 complete 类型的 SSE_Event 时，THE 前端界面 SHALL 重新启用消息输入框的发送功能

### 需求 8：LLM 集成与 Function Calling 配置

**用户故事：** 作为系统，我需要通过 OpenAI Function Calling 格式与 LLM 通信，以便 Agent 能够结构化地决定调用哪些工具。

#### 验收标准

1. THE Conversation_Agent SHALL 使用平台配置的默认 LLM 提供商（luxee.ai）进行对话推理
2. THE Conversation_Agent SHALL 在每次 LLM 调用中包含 Tool_Registry 中所有工具的 Function_Calling 格式定义
3. THE Conversation_Agent SHALL 构建系统提示词，明确 Agent 的角色（视频制作助手）、可用工具的使用场景、以及何时应追问用户
4. WHEN LLM 返回的 Function_Calling 参数不符合工具定义的参数格式时，THE Conversation_Agent SHALL 将解析错误信息追加到对话历史并重新调用 LLM
5. IF LLM API 调用超时或返回错误，THEN THE Conversation_Agent SHALL 最多重试 2 次，每次间隔递增（1秒、3秒），若仍失败则通过 SSE_Stream 推送 error 类型的 SSE_Event

### 需求 9：数据持久化

**用户故事：** 作为系统，我需要将对话数据和 Agent 状态持久化存储，以便支持会话恢复和历史查询。

#### 验收标准

1. THE 数据库 SHALL 在 conversations 表中存储以下字段：id（主键）、user_id（用户外键）、asset_ids（JSON 格式的关联素材 ID 列表）、agent_state（JSON 格式的 Agent 上下文状态）、created_at（创建时间）、updated_at（更新时间）
2. THE 数据库 SHALL 在 conversation_messages 表中存储以下字段：id（主键）、conversation_id（会话外键）、role（消息角色：user / assistant / tool）、content（消息内容）、tool_name（工具名称，仅 tool 角色消息）、tool_call_id（工具调用标识符，用于关联 Function_Calling 请求与响应）、created_at（创建时间）
3. WHEN Agent_Loop 中的每一步产生新的 Conversation_Message 时，THE Conversation_Agent SHALL 立即将该消息持久化到 conversation_messages 表
4. WHEN Conversation 的上下文状态发生变化时，THE Conversation_Agent SHALL 更新 conversations 表中对应记录的 agent_state 字段
5. THE 数据库 SHALL 对 conversation_messages 表的 conversation_id 字段建立索引，以支持按会话高效查询消息历史
