# 产品方案展示平台 · 平台代码仓库

> 三份契约的完整定义见《产品方案-V1.md》第 3 节（仓库外文档）；本文件是开发上下文索引，契约精简版见 §4。
> 项目结构：本仓库是平台自身代码（monorepo）；用户产品项目仓库（prototype/prd/reviews 三目录约定）由平台运行时 clone 到 /data/repos，不在本仓库内。

## 1 目录结构

```
platform/
├── AGENTS.md          本文件（AI 协作上下文，每次会话自动读取）
├── Makefile           dev / check / smoke / clean 命令入口
├── docker-compose.yml 一键起环境（开发与部署同构）
├── server/            Flask 后端
│   ├── app.py         工厂 + 蓝图注册
│   ├── config.py      环境变量（PLATFORM_SECRET、SMTP、路径）
│   ├── requirements.txt
│   └── ...
├── web/               Vue 3 前端
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
├── bridge/            bridge.js 源码（原生 JS，注入原型 iframe）
├── tests/             契约测试 fixture 与 E2E（Playwright）
└── docs/              POC 报告、部署文档、skill 指令文档（T6.1 起建）
```

## 2 常用命令

| 命令 | 用途 |
|------|------|
| `make dev` | 一键起环境（后端 :8081，前端 dev server :8080，代理已配好） |
| `make check` | lint + 单元测试 + 契约测试（全绿才算过） |
| `make smoke` | 端到端冒烟（Playwright，需环境已起） |
| `make clean` | 清理 node_modules / venv / 缓存 |

## 3 硬规则（任何改动不得违反）

1. **注入不改文件**：bridge.js 只在 HTTP 响应中注入原型 HTML，严禁修改 /data/repos 下仓库文件
2. **锚点保护**：既有的 `<!-- pa: xxx -->` 与 `data-pa` 锚点不许删除、不许改名（skill 与平台共用铁律）
3. **评论状态**：只有「已确认待修改」状态的评论可被修改执行；其余状态一律不动
4. **沙箱**：原型 iframe 独立 origin（:8081）+ sandbox 属性；平台侧 message 监听必须校验 event.origin
5. **事实源**：评论以仓库 reviews/ 为事实源，平台 DB 是展示缓存
6. **凭据**：git token 用 Fernet 加密落库，密钥来自环境变量 PLATFORM_SECRET；token 不进代码、不进日志、不进 .git/config

## 4 三份契约精简版（完整版见《产品方案-V1.md》§3）

### 4.1 锚点语法

- 原型侧：`<main data-pa="page-login">`（页面级挂根容器，组件级挂组件）
- PRD 侧：`## 3.2 登录页 <!-- pa: page-login -->`（同行式 HTML 注释，锚点归属下一个块级元素）
- 值全局唯一、kebab-case、语义化 `页面-区块-组件`；`pa` 值 = `data-pa` 值即配对

### 4.2 评论 JSON（reviews/comments/{comment_id}.json）

字段组：元信息（comment_id/author/status/priority/scope/content/created_at）、DOM 定位（target_type/prototype_page/anchor_id/nearest_anchor_id/css_path/outer_html/text_excerpt）、视觉上下文（screenshot/highlight_rect）、交互状态（interaction_state）、文档关联（doc_anchor_id/doc_excerpt/doc_block_fingerprint）。

status 四态：待确认 → 已确认待修改 → 已修改；忽略为旁路。scope：prototype/doc/both。

### 4.3 项目仓库目录约定

```
project-repo/
├── prototype/   入口 index.html，多页放 pages/
├── prd/         markdown 文档
└── reviews/
    ├── comments/  每条评论一个 JSON
    └── shots/     整页截图 PNG
```

## 5 代码规范

- **后端**：Flask 蓝图分层（auth/projects/gitops/proto_proxy/reviews/reconcile）；SQLite WAL；peewee 或裸 sqlite3；接口返回统一 `{code, data, msg}` 结构
- **前端**：Vue 3 + `<script setup>` + TypeScript；组件目录按功能划分（SplitPane/ProtoFrame/PrdRenderer/CommentBox/CommentDrawer）；不引入除 Element Plus 外的 UI 库
- **bridge.js**：原生 JS、零第三方依赖（html2canvas 除外）、全部行为幂等，禁止干扰原型自身逻辑
- **测试**：契约测试 fixture 放 tests/fixtures/；E2E 断言以任务卡预定义为准，不自由发挥

## 6 分支纪律

- 每张任务卡一个分支：t{阶段}.{序号}-{短名}，如 t0.2-check-infra
- commit message 格式：`[T0.2] 验收设施：make check + Playwright 骨架`
- main 始终全绿可演示，验收通过才合回
