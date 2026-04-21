# UI 重构变更清单 — 左侧导航栏与首页卡片改版

**变更日期**：2026-04-21  
**负责工程师**：前端工程师 (Antigravity)  
**变更范围**：所有 Jinja2 模板文件 (`app/templates/`)

---

## 1. 核心架构变更

### `base.html` — **全文重写**
| 改动点 | 旧值 | 新值 |
|--------|------|------|
| 导航栏位置 | 顶部水平 `<nav>` | 固定左侧 56px 宽 `<aside>` |
| 导航交互 | 始终展示文字 | 鼠标悬停 → 展宽至 180px 并显示文字 |
| 主内容区域 | `max-w-7xl mx-auto` 全页 | `margin-left: 56px` + `bg-slate-50` 工作台 |
| 顶部条 | 无 | 52px 高 `<header>` 显示页面标题与用户角色 |
| 字体 | 系统默认 | Inter (Google Fonts) |
| 配色方案 | `bg-gray-800` 深色导航 | 纯白侧边栏 + `#f8fafc` 背景 + `border-slate-100` 线条 |
| Toast 样式 | 带颜色的 `border-l-4` | 圆角 `rounded-xl` + 更柔和色系 |
| 移动端菜单 | 汉堡展开菜单 | 简化（保留侧边栏折叠效果） |
| 悬停效果 | 颜色切换 | 背景色 `#f8fafc` 过渡 |
| 全局 CSS 类 | —— | 新增 `.card-lift`, `.btn-primary`, `.btn-outline` |

### 导航项变更（左侧边栏）

| 导航项 | 路由 | 图标 |
|--------|------|------|
| 首页 | `/` | Home |
| **素材库** | `/assets` | 图片库图标 |
| 文案生成 | `/tasks/new` | 编辑图标 |
| 任务列表 | `/tasks` | 清单图标 |
| **智能剪辑** | `/mix` | 剪辑图标 |
| 批量任务 | `/batches` | 堆叠图标 |
| 审核 (operator/admin 可见) | `/reviews` | 圆形勾选 |
| 系统设置 (admin 可见) | `/admin/config` | 齿轮图标 |
| 用户（底部，点击=退出） | — | 渐变头像 |

---

## 2. 页面级变更

### `home.html` — **全文重写**
- **继承** 新 `base.html`，不再是独立文件
- 5 个功能卡片布局调整为 **4 列 Bento 网格**：
  - **Card 1** 智能视频剪辑（2 列 2 行 Hero 卡）→ `/mix` ✅ 可点击
  - **Card 2** 素材库 → `/assets` ✅ 可点击
  - **Card 3** 任务列表 → `/tasks` ✅ 可点击
  - **Card 4** AI 视频翻译（即将上线，`opacity-60`）
  - **Card 5** AI 一键成片（即将上线，`opacity-60`）
- 新增底部 **3 列快捷数据统计行**（本周生成 / 素材总数 / 待审核）
- 素材总数通过 `GET /api/assets` 动态加载

### `assets.html` — 追加 `nav_assets`、`topbar` block
- 侧边栏"素材库"项高亮 `active`
- 顶部栏显示"素材库"标题

### `tasks.html` — 追加 `nav_tasks`、`topbar` block
- 侧边栏"任务列表"项高亮 `active`
- 顶部栏显示"任务列表"标题

### `mix.html` — 追加 `nav_mix`、`topbar` block
### `tasks_new.html` — 追加 `nav_tasks_new`、`topbar` block
### `batches.html` — 追加 `nav_batches`、`topbar` block
### `reviews.html` — 追加 `nav_reviews`、`topbar` block
### `admin_config.html` — 追加 `nav_admin`、`topbar` block
### `admin_users.html` — 追加 `nav_admin`、`topbar` block
### `admin_forbidden_words.html` — 追加 `nav_admin`、`topbar` block

---

## 3. 设计语言规范（统一标准）

| 元素 | 规范值 |
|------|-------|
| 主背景色 | `#f8fafc` (slate-50) |
| 卡片背景 | `#ffffff` |
| 边框 | `1px solid #f1f5f9` (slate-100) |
| 圆角 — 卡片 | `rounded-2xl` (16px) |
| 圆角 — 图标容器 | `rounded-xl` (12px) |
| 圆角 — 按钮 | `rounded-lg` (8px) |
| 主强调色 | `#334155` (slate-700) |
| 辅助文字色 | `#94a3b8` (slate-400) |
| 品牌渐变 | `linear-gradient(135deg, #9b8ec4, #c8a0aa)` |
| 字体 | Inter, system-ui |
| 卡片悬停效果 | `translateY(-3px)` + shadow |

---

## 4. 后端变更说明（配合前端）

| 项目 | 说明 |
|------|------|
| 无需修改路由 | 所有页面路由不变，Jinja2 模板继承已完全重构 |
| 首页统计接口 | home.html 调用 `GET /api/assets` 的返回需包含 `total` 字段 |
| `base.html` 不再独立 | 已废弃旧的顶部 nav 结构，所有子页都必须 `{% extends "base.html" %}` |
