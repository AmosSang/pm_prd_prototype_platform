# 产品方案展示平台 · 平台代码仓库

> 三份契约的完整定义见《产品方案-V1.md》第 3 节（仓库外文档）；本文件是开发上下文索引，契约精简版见 §4。
> 项目结构：本仓库是平台自身代码（monorepo）；用户产品项目目录（prototype/prd/reviews 三目录约定）由平台在 /data/projects 下按 project_id 创建（08-24 去 Git 本地化架构，原型 zip 与 PRD md 由用户上传），不在本仓库内。
> **当前进度：阶段 8 去 Git 本地化改造（T8.1–T8.6）已完成并合 main**；task 卡与设计见《架构调整方案-去Git本地化-V1.md》第 11 节；git 集成代码（gitops.py / git_tasks.py / crypto_util.py）已随 T8.1 移除，评论改直接写项目目录。
> **T2.1 用户管理增强（已完成）**：`ADMIN_EMAIL` 环境变量启动种子超管（name=admin，is_admin=True）；超管登录后顶栏出「用户管理」入口；`User.disabled` 停用账号（不发验证码、已登录任意 /api/ 调用即 401 强制登出）；用户 CRUD 仅超管可用，禁止停用超管本人。

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
├── tests/             契约测试 fixture 与 E2E（Playwright）
└── docs/              POC 报告、部署文档（skill 指令文档随 Agent 方案二期再建）
```

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
3. **评论状态流转权限**：状态流转（确认/忽略/标记已修改）仅项目创建者可操作；「已确认待修改」是交付修改的标准范围
4. **沙箱**：原型 iframe 独立 origin（:8081）+ sandbox 属性；平台侧 message 监听必须校验 event.origin
5. **事实源**：评论以项目目录 reviews/ 为事实源，平台 DB 是展示缓存；评论导出包按 reviews/ 同构组织
6. **权限**：创建者专属操作（上传原型/PRD、导出评论、可评论开关、删除项目、编辑/删除任意评论、状态流转）后端逐接口校验，越权一律 403；「可评论」开关关闭 = 冻结一切写评论操作（浏览不受影响）
7. **上传安全**：原型 zip 解压必须过安全校验（路径穿越/解压总量/条目数/软链），校验通过才原子替换 prototype/，失败保留旧版本
8. **用户停用**：`User.disabled` 账号不发验证码、已登录会话在任意 /api/ 调用被 401 强制登出；用户增删改（建/改名/停启用）仅超管，禁止停用超管本人；超管由 `ADMIN_EMAIL` 幂等种子

## 4 三份契约精简版（完整版见《产品方案-V1.md》§3）

### 4.1 锚点语法

- 原型侧：`<main data-pa="page-login">`（页面级挂根容器，组件级挂组件）
- PRD 侧：`## 3.2 登录页 <!-- pa: page-login -->`（同行式 HTML 注释，锚点归属下一个块级元素）
- 值全局唯一、kebab-case、语义化 `页面-区块-组件`；`pa` 值 = `data-pa` 值即配对

### 4.2 评论 JSON（reviews/comments/{comment_id}.json）

字段组：元信息（comment_id/author/status/priority/scope/content/created_at）、DOM 定位（target_type/prototype_page/anchor_id/nearest_anchor_id/css_path/outer_html/text_excerpt）、视觉上下文（screenshot/highlight_rect）、交互状态（interaction_state）、文档关联（doc_anchor_id/doc_excerpt/doc_block_fingerprint）。

status 四态：待确认 → 已确认待修改 → 已修改（创建者手动标记）；忽略为旁路。scope：prototype/doc/both。

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

- **后端**：Flask 蓝图分层（auth/projects/proto_proxy/reviews/reconcile/storage；git 集成已随 T8.1 移除）；SQLite WAL；peewee；接口返回统一 `{code, data, msg}` 结构
- **前端**：Vue 3 + `<script setup>` + TypeScript；组件目录按功能划分（CommentBox/CommentDrawer 等）；不引入除 Element Plus 外的 UI 库
- **bridge.js**：原生 JS、零第三方依赖（modern-screenshot 除外）、全部行为幂等，禁止干扰原型自身逻辑
- **测试**：契约测试 fixture 放 tests/fixtures/；E2E 断言以任务卡预定义为准，不自由发挥

## 6 分支纪律

- 每张任务卡一个分支：t{阶段}.{序号}-{短名}，如 t8.1-model-storage
- commit message 格式：`[T8.1] 数据模型与目录基建：去 git 字段 + creator + PROJECTS_DIR`
- main 始终全绿可演示，验收通过才合回
