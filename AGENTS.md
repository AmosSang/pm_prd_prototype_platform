# 产品方案展示平台 · 平台代码仓库

> 三份契约的完整定义见《产品方案-V1.md》第 3 节（仓库外文档）；本文件是开发上下文索引，契约精简版见 §4。
> 项目结构：本仓库是平台自身代码（monorepo）；用户产品项目目录（prototype/prd/reviews 三目录约定）由平台在 /data/projects 下按 project_id 创建（08-24 去 Git 本地化架构，原型 zip 与 PRD md 由用户上传），不在本仓库内。
> **当前进度：阶段 8 去 Git 本地化改造（T8.1–T8.6）已完成并合 main**；task 卡与设计见《架构调整方案-去Git本地化-V1.md》第 11 节；git 集成代码（gitops.py / git_tasks.py / crypto_util.py）已随 T8.1 移除，评论改直接写项目目录。
> **T2.1 用户管理增强（已完成）**：`ADMIN_EMAIL` 环境变量启动种子超管（name=admin，is_admin=True）；超管登录后顶栏出「用户管理」入口；`User.disabled` 停用账号（不发验证码、已登录任意 /api/ 调用即 401 强制登出）；用户 CRUD 仅超管可用，禁止停用超管本人；**超管可删除任意项目**。
> **T 增强（已完成）**：评论移除 priority/scope、状态五态（新增「延后再改」）、批量改状态任意→任意；原型 iframe 放开 allow-same-origin；bridge nonce 跨页持久 + `<head>` 自愈护栏 + Viewer READY 看门狗；`PROTO_ORIGIN` 可配置；启动自初始化（建目录/建表/种子）；`.env` 自动加载。

## 1 目录结构

```
platform/
├── AGENTS.md          本文件（AI 协作上下文，每次会话自动读取）
├── Makefile           dev / check / smoke / clean 命令入口
├── docker-compose.yml 一键起环境（开发与部署同构）
├── server/            Flask 后端
│   ├── app.py         工厂 + 蓝图注册
│   ├── config.py      环境变量（PLATFORM_SECRET、SMTP、路径、上传上限）
│   ├── requirements.txt
│   └── ...
├── web/               Vue 3 前端
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
├── bridge/            bridge.js 源码（原生 JS，注入原型 iframe）
├── skills/            平台配套的 AI 协作技能（见 §7）
├── tests/             契约测试 fixture 与 E2E（Playwright）
└── docs/              POC 报告、部署文档
```

### 1.1 skills/ 目录（平台配套 AI 技能）

存放随平台仓库分发的 AI 协作技能（每个子目录一个技能，含 `SKILL.md` 与可选脚本），供打锚点、内容生产等协作场景使用：

- `skills/prd-html-anchor/`：PRD ↔ HTML 原型双向锚点关联技能。含自检脚本 `check_orphan_anchors.py`（零依赖）：检查两侧孤儿锚点、重复 ID、PRD 锚点单独成行、命名违规，同时识别静态 `data-pa` 与 JS `setAttribute` 动态注入；退出码 0/1/2 可作 CI 门禁。用法与平台项目目录同构：`python skills/prd-html-anchor/check_orphan_anchors.py <项目目录>`（下含 prd/ 与 prototype/）。
- `skills/comment-revision-plan/`：评论导出包解压与修改计划梳理技能。消费平台评论导出 zip（manifest + comments + shots），含解压摘要脚本 `unpack_comments.py`（零依赖、安全解压）：解压导出包、逐条读评论、筛出「已确认待修改」并按宿主（PRD 文档/原型）分组输出定位提示；退出码 0/1/2。技能主流程：逐条梳理修改方案（PRD 评论→定位段落+判断原型联动；原型评论→定位元素+判断 PRD 联动，无 PRD 关联的原型评论需补 PRD 描述），不确定处提问产品经理，最终交付 PRD 与原型两份修改计划 md；**只产出计划，不执行修改**。这是二期 Agent 闭环（架构调整方案 §13 开放问题 1）中「导出包作为 Agent 输入契约」形态的先行落地。

## 2 常用命令

| 命令 | 用途 |
|------|------|
| `make dev` | 一键起环境（后端 :8081，前端 dev server :8080，代理已配好） |
| `make check` | lint + 单元测试 + 契约测试（全绿才算过） |
| `make smoke` | 端到端冒烟（Playwright，需环境已起） |
| `make clean` | 清理 node_modules / venv / 缓存 |

## 3 硬规则（任何改动不得违反）

1. **注入不改文件**：bridge.js 只在 HTTP 响应中注入原型 HTML，严禁修改 /data/projects 下项目文件（上传产物保持纯净）
2. **锚点保护**：既有的 `<!-- pa: xxx -->` 与 `data-pa` 锚点不许删除、不许改名（内容生产与平台共用铁律）
3. **评论状态流转权限**：状态流转（批量改状态）仅项目创建者可操作；**任意状态 → 任意目标状态**，无硬性状态机限制（五态：待确认/已确认待修改/已修改/忽略/延后再改）；「已确认待修改」是交付修改的标准范围
4. **沙箱**：原型 iframe 带 sandbox 属性并含 **allow-same-origin**（业务决策：内部系统不收紧，原型可用 localStorage；生产同域反代 /proto 时与宿主同源）；平台侧 message 监听必须校验 event.origin + nonce
5. **事实源**：评论以项目目录 reviews/ 为事实源，平台 DB 是展示缓存；评论导出包按 reviews/ 同构组织
6. **权限**：创建者专属操作（上传原型/PRD、导出评论、可评论开关、删除项目、编辑/删除任意评论、批量改状态）后端逐接口校验，越权一律 403；删除项目创建者或超管均可（T 增强）；「可评论」开关关闭 = 冻结一切写评论操作（浏览不受影响）
7. **上传安全**：原型 zip 解压必须过安全校验（路径穿越/解压总量/条目数/软链），校验通过才原子替换 prototype/，失败保留旧版本
8. **用户停用**：`User.disabled` 账号不发验证码、已登录会话在任意 /api/ 调用被 401 强制登出；用户增删改（建/改名/停启用）仅超管，禁止停用超管本人；超管由 `ADMIN_EMAIL` 幂等种子

## 4 三份契约精简版（完整版见《产品方案-V1.md》§3）

### 4.1 锚点语法

- 原型侧：`<main data-pa="page-login">`（页面级挂根容器，组件级挂组件）
- PRD 侧：`## 3.2 登录页 <!-- pa: page-login -->`（同行式 HTML 注释，锚点归属下一个块级元素）
- 值全局唯一、kebab-case、语义化 `页面-区块-组件`；`pa` 值 = `data-pa` 值即配对
- T 增强：独立注释行（单独一行 `<!-- pa: x -->`）**归并到前一段落/标题**（前块为列表/表格则给下一块）；**同行多个锚点 → `data-pa="a b"` 存多值**，点击「定位」弹 ID 列表选择

### 4.2 评论 JSON（reviews/comments/{comment_id}.json）

字段组：元信息（comment_id/author/status/content/created_at）、DOM 定位（target_type/prototype_page/anchor_id/nearest_anchor_id/css_path/outer_html/text_excerpt）、视觉上下文（screenshot/highlight_rect）、交互状态（interaction_state）、文档关联（doc_anchor_id/doc_excerpt/doc_block_fingerprint）。priority/scope 已移除（T 增强）。

status 五态：待确认 / 已确认待修改 / 已修改 / 忽略 / 延后再改；批量改状态任意→任意，无硬性状态机限制（创建者可操作）。

### 4.3 项目目录约定（/data/projects/{project_id}/）

```
{project_id}/
├── prototype/   原型 zip 解压产物（≤100MB 包；入口 index.html，多页放 pages/；
│                T8.2：zip 根顶层无 html 时自动下钻唯一子目录一层，如 dist/ 构建产物壳）
├── prd/         唯一一份 markdown 文档
└── reviews/
    ├── comments/  每条评论一个 JSON
    └── shots/     整页截图 PNG
```

## 5 代码规范

- **后端**：Flask 蓝图分层（auth/users/projects/proto_proxy/reviews/reconcile/storage；git 集成已随 T8.1 移除）；SQLite WAL；peewee；接口返回统一 `{code, data, msg}` 结构
- **前端**：Vue 3 + `<script setup>` + TypeScript；组件目录按功能划分（CommentBox/CommentDrawer 等）；不引入除 Element Plus 外的 UI 库
- **bridge.js**：原生 JS、零第三方依赖（modern-screenshot 除外）、全部行为幂等（`__PP_BRIDGE__`），禁止干扰原型自身逻辑
  - nonce 读取：URL hash → `window.__PP_NONCE__` → `sessionStorage.pp_nonce`（内部跳转丢 hash，跨页靠 sessionStorage 恢复）
  - 注入两段：`<head>` 后自愈护栏（记住 nonce + 轮询补挂 bridge，应对原型脚本崩溃/跳走）+ `</body>` 前 bridge.js
  - 消息：发送 `'*'`；宿主/接收侧以 event.origin + nonce 校验（不要用 document.referrer 推 origin）
- **Viewer**：READY 看门狗（8s 未就绪自动重载 iframe ≤3 次，仍失败给「点击重试」）；`PROTO_ORIGIN` 来自 `web/src/proto-origin.ts`（`VITE_PROTO_ORIGIN` > 开发 :8081 > 生产同源）
- **测试**：契约测试 fixture 放 tests/fixtures/；E2E 断言以任务卡预定义为准，不自由发挥

## 6 分支纪律

- 每张任务卡一个分支：t{阶段}.{序号}-{短名}，如 t8.1-model-storage
- commit message 格式：`[T8.1] 数据模型与目录基建：去 git 字段 + creator + PROJECTS_DIR`
- main 始终全绿可演示，验收通过才合回
