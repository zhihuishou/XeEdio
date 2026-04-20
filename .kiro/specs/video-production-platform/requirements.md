# 需求文档：视频生产平台

## 简介

基于 MoneyPrinterTurbo（Python，Apache 2.0）套壳扩展的视频生产平台。平台支持素材库管理、LLM 文案生成（含违禁词 RAG 过滤）、A roll/B roll 混剪合成、配音合成，以及运营终审发布流程。目标是让实习生也能通过简单的 Web UI 完成从主题输入到视频产出的全流程操作，先跑通商业闭环，后续接入 Credit 计费系统。

## 术语表

- **Platform（平台）**：本视频生产平台系统的总称
- **Asset_Library（素材库）**：管理达人口播素材、产品素材、Pexels 空镜素材的存储与检索模块
- **Asset（素材）**：上传到素材库中的单个媒体文件（视频片段、图片、音频）
- **A_Roll（主画面）**：视频中的主要画面内容，包括达人口播、产品展示等核心镜头
- **B_Roll（辅助画面）**：视频中的空镜头、过渡画面、辅助说明画面（如 Pexels 空镜素材）
- **Copywriting_Engine（文案引擎）**：调用 LLM 生成视频文案的模块，支持 DeepSeek 或其他兼容 OpenAI API 的模型
- **Forbidden_Word_Filter（违禁词过滤器）**：基于 RAG 知识库对生成文案进行敏感内容过滤的模块
- **Composition_Engine（合成引擎）**：基于 FFmpeg 执行视频混剪、配音合成的模块
- **TTS_Engine（语音合成引擎）**：将文案文本转换为语音的模块，默认使用 Edge-TTS
- **Review_System（审核系统）**：运营人员终审生成视频并决定是否发布的模块。未来随着 RAG 知识库积累（如大量视频无法发布时 AI 自动总结问题写入知识库），系统可逐步实现自我迭代，减少人工审核负担
- **Intern（实习生）**：日常操作用户，负责输入主题、选择素材、触发视频生成
- **Operator（运营）**：负责终审生成的视频，决定是否发布
- **Admin（管理员）**：负责管理素材库、配置系统参数、管理用户权限
- **Task（任务）**：一次视频生成的完整流程记录，从文案生成到视频合成的全过程
- **Batch_Task（批量任务）**：一组关联的 Task 集合，支持同主题多版本生成或多主题批量生成
- **Web_UI（前端界面）**：基于 HTML + Tailwind CSS 构建的 Web 操作界面

## 需求

### 需求 1：素材上传

**用户故事：** 作为管理员，我希望能上传各类素材到素材库，以便后续视频合成时使用。

#### 验收标准

1. THE Asset_Library SHALL 支持上传视频、图片、音频三种类型的素材文件
2. WHEN 管理员上传素材时，THE Asset_Library SHALL 要求管理员为素材指定分类标签（达人口播、产品素材、Pexels 空镜）
3. WHEN 素材文件上传完成后，THE Asset_Library SHALL 存储素材文件到本地文件系统或对象存储，并记录素材的元数据（文件名、分类、上传时间、文件大小、时长）
4. IF 上传的文件格式不在支持列表中（视频：mp4/mov/avi；图片：jpg/png/webp；音频：mp3/wav/aac），THEN THE Asset_Library SHALL 拒绝上传并返回格式不支持的错误提示
5. IF 上传的文件大小超过系统配置的上限，THEN THE Asset_Library SHALL 拒绝上传并返回文件过大的错误提示

### 需求 2：素材检索与管理

**用户故事：** 作为实习生，我希望能按分类和关键词检索素材，以便快速找到合成视频所需的素材。

#### 验收标准

1. THE Asset_Library SHALL 支持按分类标签（达人口播、产品素材、Pexels 空镜）筛选素材列表
2. THE Asset_Library SHALL 支持按文件名关键词搜索素材
3. WHEN 实习生查看素材列表时，THE Asset_Library SHALL 展示素材的缩略图、文件名、分类、上传时间和时长信息
4. WHEN 管理员删除一个素材时，THE Asset_Library SHALL 同时删除素材文件和对应的元数据记录
5. THE Asset_Library SHALL 支持素材的分页浏览，每页默认展示 20 条记录

### 需求 3：LLM 文案生成

**用户故事：** 作为实习生，我希望输入一个视频主题后由 LLM 自动生成视频文案，以便快速产出内容。

#### 验收标准

1. WHEN 实习生输入视频主题并触发生成时，THE Copywriting_Engine SHALL 调用 LLM API（DeepSeek 或其他兼容 OpenAI API 的模型）生成视频文案
2. THE Copywriting_Engine SHALL 在 LLM 请求中包含系统预设的 prompt 模板，引导 LLM 生成适合短视频的文案结构
3. WHEN LLM 返回生成的文案后，THE Copywriting_Engine SHALL 将文案传递给 Forbidden_Word_Filter 进行敏感内容检测
4. THE Copywriting_Engine SHALL 支持管理员配置 LLM 的 API 地址、API Key 和模型名称
5. IF LLM API 调用失败或超时（超过 30 秒），THEN THE Copywriting_Engine SHALL 返回生成失败的错误提示并记录错误日志

### 需求 4：违禁词 RAG 过滤

**用户故事：** 作为管理员，我希望系统能自动过滤文案中的违禁词和敏感内容，以避免发布违规视频。

#### 验收标准

1. WHEN 文案生成完成后，THE Forbidden_Word_Filter SHALL 基于 RAG 知识库检测文案中的违禁词和敏感内容
2. WHEN 检测到违禁词时，THE Forbidden_Word_Filter SHALL 标记违禁词的位置并返回修改建议
3. THE Forbidden_Word_Filter SHALL 支持管理员维护违禁词知识库（添加、删除、批量导入违禁词条目）
4. WHEN 文案通过违禁词过滤后，THE Forbidden_Word_Filter SHALL 返回过滤结果状态（通过/包含违禁词）
5. THE Forbidden_Word_Filter SHALL 在过滤完成后保留原始文案和过滤后文案的对比记录

### 需求 5：文案编辑与确认

**用户故事：** 作为实习生，我希望能在文案生成后手动编辑和调整文案内容，以便在合成视频前确保文案质量。

#### 验收标准

1. WHEN 文案生成并完成违禁词过滤后，THE Web_UI SHALL 展示文案内容并允许实习生手动编辑
2. WHEN 实习生修改文案后，THE Forbidden_Word_Filter SHALL 对修改后的文案重新执行违禁词检测
3. WHEN 实习生确认文案最终版本时，THE Platform SHALL 将文案状态标记为"已确认"并锁定文案内容
4. THE Web_UI SHALL 在文案编辑界面高亮显示被 Forbidden_Word_Filter 标记的违禁词

### 需求 6：语音合成（TTS）

**用户故事：** 作为实习生，我希望系统能将确认的文案自动转换为语音，以便用于视频配音。

#### 验收标准

1. WHEN 文案状态为"已确认"且实习生触发语音合成时，THE TTS_Engine SHALL 将文案文本转换为语音音频文件
2. THE TTS_Engine SHALL 默认使用 Edge-TTS 进行语音合成
3. THE TTS_Engine SHALL 支持实习生选择语音角色（至少包含男声和女声各一种中文语音）
4. WHEN 语音合成完成后，THE TTS_Engine SHALL 将生成的音频文件存储到任务目录并记录音频时长
5. IF 语音合成失败，THEN THE TTS_Engine SHALL 返回失败原因并允许实习生重新触发合成
6. THE Web_UI SHALL 支持实习生在合成前预览试听所选语音角色的效果

### 需求 7：视频混剪合成

**用户故事：** 作为实习生，我希望系统能将素材、文案和配音自动合成为完整视频，支持 A roll 和 B roll 混剪模式。

#### 验收标准

1. WHEN 实习生选择素材并触发视频合成时，THE Composition_Engine SHALL 基于 FFmpeg 将 A_Roll 素材、B_Roll 素材和 TTS 音频合成为一个完整视频
2. THE Composition_Engine SHALL 支持实习生分别指定 A_Roll（达人口播/产品素材）和 B_Roll（空镜素材）的素材来源
3. THE Composition_Engine SHALL 根据 TTS 音频时长自动调整视频总时长
4. THE Composition_Engine SHALL 支持在 B_Roll 片段之间添加过渡效果（至少支持淡入淡出）
5. WHEN 视频合成完成后，THE Composition_Engine SHALL 将输出视频存储到任务目录并记录视频元数据（分辨率、时长、文件大小）
6. IF 合成过程中 FFmpeg 执行失败，THEN THE Composition_Engine SHALL 记录 FFmpeg 错误日志并返回合成失败的提示
7. THE Composition_Engine SHALL 支持配置输出视频的分辨率（默认 1080x1920 竖屏）和码率

### 需求 8：任务管理

**用户故事：** 作为实习生，我希望能查看所有视频生成任务的状态和进度，以便跟踪工作进展。

#### 验收标准

1. THE Platform SHALL 为每次视频生成流程创建一个 Task 记录，包含唯一任务 ID、创建时间、当前状态
2. THE Platform SHALL 维护 Task 的状态流转：草稿 → 文案已确认 → 语音已合成 → 视频已合成 → 待审核 → 已通过 → 已发布（或已拒绝）
3. WHEN 实习生查看任务列表时，THE Web_UI SHALL 展示任务的主题、状态、创建时间和最后更新时间
4. THE Web_UI SHALL 支持按任务状态筛选任务列表
5. WHEN 实习生点击某个任务时，THE Web_UI SHALL 展示该任务的完整详情，包括文案内容、素材选择、生成的视频预览

### 需求 9：批量视频生成

**用户故事：** 作为实习生，我希望能一次提交多个主题或对同一主题生成多个版本的视频，以便提高产出效率并从中挑选最优版本。

#### 验收标准

1. THE Platform SHALL 支持实习生一次提交多个主题（手动输入多条或上传 CSV 主题列表），为每个主题自动创建独立的 Task
2. THE Platform SHALL 支持实习生对同一主题指定生成版本数量（默认 1，最大由系统配置决定），每个版本独立调用 LLM 生成不同文案并匹配不同素材组合
3. THE Platform SHALL 将同一批次提交的所有 Task 关联到一个 Batch_Task 记录，包含批次 ID、提交时间、总任务数和已完成任务数
4. THE Platform SHALL 支持配置批量任务的最大并发数（默认 3），同时执行的 Task 数量不超过该限制，超出的 Task 排队等待
5. WHEN 实习生查看批量任务时，THE Web_UI SHALL 展示该批次下所有子任务的状态汇总和进度百分比
6. IF 批量任务中某个子任务失败，THEN THE Platform SHALL 继续执行其余子任务，不影响整批任务的进行
7. THE Web_UI SHALL 在批量任务完成后支持运营对同一批次的视频进行对比预览，便于挑选最优版本

### 需求 10：视频审核

**用户故事：** 作为运营，我希望能终审生成的视频并决定是否通过发布，以确保发布内容的质量。

#### 验收标准

1. WHEN 视频合成完成后，THE Platform SHALL 自动将 Task 状态更新为"待审核"
2. WHEN 运营查看待审核列表时，THE Review_System SHALL 展示待审核视频的预览、文案内容和素材信息
3. THE Review_System SHALL 支持运营对视频执行"通过"或"拒绝"操作
4. WHEN 运营拒绝一个视频时，THE Review_System SHALL 要求运营填写拒绝原因
5. WHEN 运营通过一个视频时，THE Platform SHALL 将 Task 状态更新为"已通过"
6. WHEN 运营拒绝一个视频时，THE Platform SHALL 将 Task 状态更新为"已拒绝"，并通知实习生查看拒绝原因
7. WHEN 运营拒绝一个视频时，THE Review_System SHALL 将拒绝原因记录到 RAG 知识库，用于后续 AI 自我迭代学习（未来可自动识别同类问题并在文案生成阶段提前规避）

### 需求 11：用户角色与权限

**用户故事：** 作为管理员，我希望系统能区分不同用户角色并控制操作权限，以确保系统安全和职责分离。

#### 验收标准

1. THE Platform SHALL 支持三种用户角色：Intern（实习生）、Operator（运营）、Admin（管理员）
2. THE Platform SHALL 限制 Intern 角色仅能执行以下操作：浏览素材、输入主题生成文案、编辑文案、触发语音合成、选择素材触发视频合成、查看自己的任务
3. THE Platform SHALL 限制 Operator 角色仅能执行以下操作：查看待审核任务列表、预览视频、执行通过或拒绝操作
4. THE Platform SHALL 允许 Admin 角色执行所有操作，包括：管理素材库、管理违禁词知识库、配置系统参数（LLM API、TTS 设置、视频输出参数）、管理用户账号和角色分配
5. WHEN 用户尝试执行超出角色权限的操作时，THE Platform SHALL 拒绝该操作并返回权限不足的提示
6. THE Platform SHALL 支持基于用户名和密码的登录认证

### 需求 12：前端界面易用性

**用户故事：** 作为实习生，我希望前端界面简单直观，以便无需培训即可完成日常视频生产操作。

#### 验收标准

1. THE Web_UI SHALL 提供清晰的导航结构，将素材库、文案生成、视频合成、任务列表作为主要导航入口
2. THE Web_UI SHALL 在视频生成流程中提供步骤引导（Step 1: 输入主题 → Step 2: 编辑文案 → Step 3: 选择素材 → Step 4: 合成视频）
3. THE Web_UI SHALL 在所有耗时操作（文案生成、语音合成、视频合成）期间展示进度指示器
4. THE Web_UI SHALL 采用响应式布局，支持在 1024px 及以上宽度的屏幕上正常使用
5. THE Web_UI SHALL 对所有用户操作提供即时反馈（成功提示、错误提示、加载状态）

### 需求 13：系统配置管理

**用户故事：** 作为管理员，我希望能在界面上配置系统参数，以便灵活调整平台行为而无需修改代码。

#### 验收标准

1. THE Platform SHALL 支持管理员通过 Web_UI 配置以下参数：LLM API 地址、API Key、模型名称
2. THE Platform SHALL 支持管理员通过 Web_UI 配置 TTS 引擎参数：语音角色列表、语速、音量
3. THE Platform SHALL 支持管理员通过 Web_UI 配置视频输出参数：默认分辨率、码率、输出格式
4. THE Platform SHALL 支持管理员通过 Web_UI 配置素材上传限制：最大文件大小、允许的文件格式
5. WHEN 管理员修改系统配置后，THE Platform SHALL 立即生效，无需重启服务
6. THE Platform SHALL 将系统配置持久化存储，确保服务重启后配置不丢失
