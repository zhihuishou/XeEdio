# Tasks: AI 驱动智能混剪 v2（双 Pipeline 架构）

## Phase 1: 基础设施（已完成 ✅）

- [x] 1. 新增 `asset_analysis` 数据库表和 SQLAlchemy 模型
- [x] 2. 新建 `AssetAnalysisService` — 上传时异步分析（VLM 帧分析 + 音频检测 + Whisper 转录）
- [x] 3. 上传接口（单个 + 批量）接入异步分析触发
- [x] 4. 素材列表 API 返回分析状态（analysis_status / analysis_role / analysis_description）
- [x] 5. 新建对话式前端 `mix_chat.html`（消息流 + 素材上传 + 素材库选择 + 参数面板）
- [x] 6. 侧边栏新增"智能混剪"导航，`/mix` 路由指向新页面
- [x] 7. Montage 模式音画同步（每个 segment 带源音频渲染）
- [x] 8. Montage 模式自动生成 Whisper 字幕
- [x] 9. 前端自然语言解析时长和数量（"3分钟" → 180s，"3条" → 3）
- [x] 10. ai_director.log 记录 VLM API URL、Model、耗时
- [x] 11. 词级切点优化（snap_timeline_to_breath_gaps）
- [x] 12. 退化 timeline 检测（entry 太少自动拒绝走 fallback）
- [x] 13. 字幕字体系统（中文字体优先级 + fontsdir）

## Phase 2: 文本驱动 Pipeline（口播/直播类素材）

- [x] 14. 新建 `TextDrivenEditingService` 服务
  - `select_segments_with_llm()` — 调用 LLM（qwen3.6-plus）选段
  - `map_text_to_timestamps()` — 文本段落 → 词级时间戳映射
  - `generate_text_driven_timeline()` — 完整文本驱动 pipeline
- [x] 15. LLM 选段 Prompt 设计与调优
  - 输入：完整转录文本（带时间标记）+ 用户指令 + 目标时长
  - 输出：选中段落列表（start_text / end_text / reason）
- [x] 16. 文本→时间戳模糊匹配算法
  - 在 Whisper 词序列中定位 LLM 选的句子起止位置
  - 处理 ASR 转录误差（同音字、漏字）
- [x] 17. 口头禅过滤
  - 标记"嗯/啊/那个/就是说"等 filler words
  - 在切点计算时跳过口头禅片段
- [x] 18. 呼吸口切点优化（复用已有的 `_find_breath_gap`）
  - 起点：段落第一个词 start - 0.1s
  - 终点：段落最后一个词 end → 下一个词 start 的间隙中点

## Phase 3: 双 Pipeline 路由

- [x] 19. `AIDirectorService` 新增 `run_auto_pipeline()` — 自动路由
  - 从 `asset_analysis` 加载 `has_speech` 和 `role`
  - has_speech + presenter → 文本驱动 pipeline
  - 其他 → 视觉驱动 pipeline
- [x] 20. 新增 `mixing_mode = "auto"` 到 `MixingService.execute_mix()`
- [x] 21. 前端 `mix_chat.html` 改用 `mixing_mode: "auto"`
- [x] 22. 路由日志：ai_director.log 记录选择了哪条 pipeline 及原因

## Phase 4: 统一视觉驱动 Pipeline 重构

- [x] 23. 重构 `VLMService` — 新增 `analyze_single_clip()` 方法
- [x] 24. 重构 `VLMService` — 新增 `generate_unified_timeline()` 方法
- [x] 25. 重构 `AIDirectorService` — 从 DB 加载素材摘要（缓存命中）
- [x] 26. 统一 timeline 格式验证

## Phase 5: 素材智能层增强

- [x] 27. 素材库页面显示分析标签（role badge、分析状态 icon）
- [x] 28. 素材详情 API 返回完整分析结果
- [x] 29. 新增 `GET /api/assets/{id}/analysis` 接口
- [x] 30. 新增 `POST /api/assets/{id}/reanalyze` 接口
- [x] 31. 对话窗口中素材卡片实时更新分析状态

## Phase 6: 向量检索与语义搜索

- [x] 32. 集成 Embedding 生成
- [x] 33. 素材分析完成后自动生成 embedding
- [x] 34. 新增语义搜索接口
- [x] 35. 对话窗口素材选择面板支持语义搜索

## Phase 7: 前端优化

- [x] 36. 对话窗口支持多轮对话
- [x] 37. 视频结果卡片支持单条重试
- [x] 38. 参数面板支持脚本文案输入 + TTS 预览
- [x] 39. 删除旧的 `mix.html` 模板文件
- [x] 40. `/tasks` 页面"新建任务"按钮跳转到 `/mix`

## Phase 8: 混合模式（文本+视觉协同，未来）

- [x] 41. 有语音长视频 + B-roll 素材场景
  - 文本驱动选主轴段落
  - 视觉驱动决定 B-roll 插入点
  - 合并两条 pipeline 的 timeline
- [x] 42. 多素材混合路由（部分有语音、部分纯画面）

## Phase 9: 清理与迁移

- [x] 43. `TaskAsset.roll_type` 统一为 `"clip"`
- [x] 44. 标记旧 mixing_mode 为 deprecated
- [x] 45. 移除旧模式代码分支
- [ ] 46. 更新 API 文档
- [ ] 47. 更新设计文档反映最终实现
