# QA 评估报告：Video Production Platform

基于对 `E:\X\XeEdio\video-production-platform\app` 代码库的深入分析，我以自动化测试和 QA 的视角评估了当前平台存在的潜在 Bug、性能瓶颈以及安全隐患。

## 1. 核心 Bug 与稳定性风险

### 1.1 OOM (内存溢出) 风险：大文件上传阻塞
- **位置**: `app/routers/assets.py` 中的 `upload_asset`
- **问题**: 使用 `content = await file.read()` 会将用户上传的文件（视频素材通常几百 MB 甚至 GB 级别）**全部加载到服务器内存**中，然后才进行文件大小校验和保存。只要并发数个大文件上传请求，服务器极易因内存耗尽而崩溃。
- **修复方案**: 应通过流式写入（如 `shutil.copyfileobj(file.file, f)`）并将大小校验逻辑改为在流式读取过程中进行。

### 1.2 幽灵线程与拒绝服务 (DoS) 风险：缺乏任务队列
- **位置**: `app/services/mixing_service.py` 中的 `create_mix_task` 和 `retry`
- **问题**: 每次提交合成任务时，系统直接通过 `threading.Thread(..., daemon=True).start()` 拉起一个裸线程去执行繁重的视频合成（包含内部的 `subprocess.run(ffmpeg)`）。这没有任何并发控制（Connection Pool / Rate Limit）。如果前端发送 50 个并发合并请求，系统将拉起 50 个 FFmpeg 进程，极易导致服务器 CPU 完全卡死（DoS）。
- **修复方案**: 应使用 Celery、RQ 或基于 Redis 的任务队列（Task Queue）机制，并控制并发 Workers 数量。

### 1.3 缺失分页导致的全表扫描 (N+1 内存问题)
- **位置**: `app/routers/tasks.py` 中的 `list_tasks`
- **问题**: `query.all()` 直接返回所有匹配的任务。当系统长时间运行产生数以万计的 Task 时，该接口的响应时间会极度缓慢，且会瞬间拉高数据库和服务器内存，最终导致前端超时或后端崩溃。
- **修复方案**: 加入 `skip` 和 `limit` 参数实现强制分页，类似 `assets.py` 中的最佳实践。

### 1.4 API 并发状态带来的竞态条件 (Race Conditions)
- **位置**: `tasks.py`, `mix.py` 中的状态扭转 API (`submit_review`, `retry_mix`)
- **问题**: 改变任务状态前，只用常规的 `filter(Task.id == task_id).first()` 查询并校验状态，然后赋值保存。在高并发下（如用户在界面快速双击），可能会引发状态错乱或多次触发同一任务的重试逻辑。
- **修复方案**: 针对状态相关的扭转操作，建议采用数据库排他锁 (`with_for_update()`) 或乐观锁控制。

## 2. Playwright MCP 测试策略评估

用户提及的 `playwright-mcp` (<https://github.com/microsoft/playwright-mcp>) 是一个将 Playwright UI 自动化能力暴露给 AI Agent 的工具。作为测试，利用它对本项目进行 E2E (端到端自动化测试) 有极大价值。

### 📌 结合 Playwright-MCP 可执行的 E2E 测试路径

利用大模型 + Playwright-MCP 可以自动生成并执行以下核心用例：

1. **核心工作流冒烟测试 (Happy Path)**
   - 登录系统 (Admin/Intern 角色验证)。
   - 在图文页面（通过 UI 或调用 Mock API）新建一个合成任务。
   - 自动选择 `A-roll` 和 `B-roll`，触发合成。
   - 利用 `Playwright` 的显式等待 (`page.locator().wait_for()`) 来检测前端 UI 上“任务处理中...”到“处理完成”的最终状态。

2. **异常流量及上传熔断测试 (Edge Cases)**
   - 使用 Playwright 拦截网络 (`page.route()`) 注入超大文件模拟上传，校验前端是否友好的提示 Error，或是观察后端系统是否发生预期的 413 / 500 异常。

3. **鉴权与越权测试 (Authorization Test)**
   - 登录为 `intern` 账号，尝试使用 playwright 强制导航到管理页面或以 Admin 接口强发请求。
   - 预期被 FastAPI 阻断并跳转回统一鉴权错误页。

### 💡 结论
当前 `video-production-platform` 的后端 MVC 架构清晰，但明显**缺乏工业级的并发应对能力（大对象内存、裸线程任务）**。
在上线前，必须解决上述的内存与线程池调度风险。同时配合类似于 Playwright 的测试框架补充 Web 前端的自动化 E2E 覆盖率，以确保平台稳定性。

## 5. Playwright E2E 本地冒烟测试反馈 (Local Run Findings)

在本地执行 `tests/e2e/test_smoke_pipeline.py` 的过程中，我们进一步捕获并验证了如下在真实环境启动和执行中暴露的问题：

1. **Python 3.13 兼容性漏洞 (启动崩溃)**
   - **表现**: `init_db.py` 在初始化管理员账号时，使用 `passlib` 的 `bcrypt` 上下文抛出了底层兼容性错误。
   - **原因**: 现代的 `bcrypt>=4.0` 改变了字节处理逻辑，与久未维护的 `passlib` 产生严重冲突，导致 FastAPI 服务器在启动和建表环节直接崩溃。
   - **临时修复**: 已在本地将 `init_db.py` 及 `auth_service.py` 中的 `schemes=["bcrypt"]` 修改为 `schemes=["sha256_crypt"]`，使本地服务器得以成功运行。

2. **LLM API 强依赖导致阻塞 (业务中断)**
   - **表现**: 界面中的“确认锁定文案”及后续生成视频的按钮一直处于 disabled 状态。
   - **原因**: 数据库中的 `llm_api_key` 为空时，后端的 `/api/copywriting/generate` 不作防错处理直接抛出异常；同时 `/api/config/llm-providers` 在无本地配置时会返回 404，导致前端模型选择下拉框无数据。
   - **验证情况**: 为 `copywriting_service.py` 强行添加无 Key Mock 返回值后，依然遭遇到了前端因为找不到 `select` 选项以及获取模型列表失败导致状态无法推进。必须移除前端的强绑定才可以流转。

3. **前端可访问性 (Accessibility) 及定位器缺陷**
   - **表现**: 冒烟测试中 `page.get_by_label("视频主题")` 检索超时。
   - **原因**: 在 `tasks_new.html` 中 `<label>` 并没有绑定 `for` 属性，且 `<textarea>` 缺少 `id`，导致真实的屏幕阅读器和自动化脚本 (Playwright) 无法将标签内容与输入框产生关联。

4. **Pexels API 智能选材模块验证 (成功)**
   - **验证情况**: 在注入了有效的 `pexels_api_key` 之后，系统后端的 `/api/mix/pexels/search` 以及 `/api/mix/pexels/download` 端点表现稳定。目前已经能够顺利利用关键字检索并在规定超时内下载高清 MP4 B-Roll 视频入库，作为空镜素材绑定到视频资源池中参与主线化合成工作。

结合静态代码审计和动态冒烟测试，以上列表共同构成了当前系统初上生产环境前必须修复的核心风险，及其功能的真实可行性报告。
