# XeEdio: 多模态 AI 智能编导落地架构方案 (VLM AI Director)

## 1. 核心理念与痛点解决

目前绝大多数传统一键成片自动化系统（包含 XeEdio 现有的随机/顺序混剪模式）停留在“**物理时间的盲目切割与拼接**”。这种方式常会导致在混入空镜（B-Roll）时，遮挡住主视频（A-Roll）中的关键动作（例如主播展示商品、核心手势暗示等），造成视频逻辑割裂。

本方案提出**决策与执行分离**的“多模态 AI 编导架构”，利用大语言多模态视觉模型（如 GPT-4o / Gemini 1.5），引入**智能视觉动作防遮盖**和**语画强同步关联**。

## 2. 工作流重构 (Workflow)

整个处理管线从原先的两步拆分为了智能化的三步：

- `Original:`  [提取素材] ➔ [瞎切盲拼 MoviePy / FFmpeg] ➔ [成品]
- `Smart:`    [提取素材并按秒抽帧] ➔ **[VLM视觉大模型判定写剧本]** ➔ [执行精确手术刀剪辑] ➔ [成品]

### 步骤 A：多模态抽帧提炼 (Frame Extraction & Compression)
利用服务端内置的 `FFmpeg` 将源自达人的带货/讲解视频（A-Roll）每隔 1~2 秒抽取一个极低分辨率的画面（1 fps 或 0.5 fps 抽取），组成图片序列（Frame Sequences），再统一实施 Base64 编码以节约网络开销。

### 步骤 B：多模态大模型剧本生成 (VLM Script Generation)
向 GPT-5.4 / GPT-4o-mini 等支持多模态输入的端点发起询问：
- **输入**：抽离的 A-Roll 图片帧序列、对应的背景声音旁白（TTS文案或 ASR识别结果）、可用 B-Roll 素材列表描述。
- **大模型思考逻辑**：判断画面中人物的情感、是否在进行实体展示；判断文案内容是否在描绘风景、场景或空泛概念。如果是展示核心物体，则锁定为保留 `a_roll`；如果是概念渲染，则调度切入 `b_roll`。
- **输出**：大模型严格回复带秒数时间戳的 JSON 剧本（Timeline Array）。

**样本输出格式**：
```json
[
  {"type": "a_roll", "start": 0, "end": 4, "reason": "开场展示必须由达人主视频引导。"},
  {"type": "b_roll", "start": 4, "end": 8, "reason": "文案恰好提到‘大自然’且画面平缓，切入准备好的 B-roll_1。"},
  {"type": "a_roll", "start": 8, "end": 16, "reason": "文案提到新产品，同时画面达人举起手，交还给主视频演示。"},
  {"type": "b_roll", "start": 16, "end": 20, "reason": "结尾情感升华，切入 B-roll_2。"}
]
```

### 步骤 C：原生智能拼流执行 (Smart Assembly using MoviePy 2.x)
后端的引擎 `mixing_engine.py` 抛弃掉原先的暴力随机 `random.shuffle`：
将接收上述的 JSON 序列：
1. 若遇到 `a_roll` 节点：调用 `original_clip.subclipped(start, end)` 截取安全片段。
2. 若遇到 `b_roll` 节点：智能轮询可用的 B-roll 素材池（通过 `itertools.cycle` 轮换不同的素材视频），在目标时间段用空镜进行覆盖。
3. 严格等比例遮幅（Resize & Pad），使用 `composite` 重组阵列后与 TTS 生成轨道同步推送到 `FFmpeg` 输出成品。

---

## 3. 技术收益与落地产出 (Business Value)

1. **零遮挡风险**：精准避开带货视频中的产品举起、人物高光时刻。
2. **语画极度同步**：不再是“我说大海，你放草地”，而是让 B-roll 素材在最合适的上下文空洞期“卡点”自然插入。
3. **彻底的模块化**：`AI_Director` 本身可以抽离为一个独立微服务，不仅能给全自动工作流使用，哪怕未来我们准备向用户展示一个**“可编辑时间线编辑器”**，也可以预先灌入这套 AI JSON 让用户自己拖拽微调。

## 4. 后续适配计划
1. 将当前证明完全走通的 `test_smart_mix.py` 原型代码集成剥离进正式工程文件 `app/services/ai_director_service.py`。
2. 更改 `MixCreateRequest` 的 Payload 限制请求参数。
3. 把处理防断流的逻辑容错机制融入系统，面对大模型 API 不稳定/宕机时平滑退坡到旧版的 `random` 逻辑模式。
