# 产品方案展示平台（Product Plan Platform）

一个面向产品/研发协作的**产品方案在线展示与评审**平台：上传产品 PRD 文档 + 用 HTML 制作的原型，在原型与 PRD 两侧进行锚点联动、评论标注、批量状态流转与导出，帮助团队完成从“需求文档 → 可交互原型 → 评审反馈 → 修改闭环”的全流程。

- 前端：Vue 3 + TypeScript + Element Plus + Vite
- 后端：Flask + peewee + SQLite（WAL），评论以**项目目录 JSON 为事实源**
- 测试：pytest（单元 + 契约）+ Playwright（E2E）

---

## 目录

- [已实现功能](#已实现功能)
- [技术架构与实现方式](#技术架构与实现方式)
- [目录结构](#目录结构)
- [快速开始（开发）](#快速开始开发)
- [环境变量说明](#环境变量说明)
- [测试](#测试)
- [注意事项](#注意事项)
- [服务器部署](#服务器部署)

---

## 已实现功能

### 1. 登录与用户体系（T2.1 / T2.2）

- **邮箱验证码登录（白名单制）**：`users` 表由管理员维护，无自助注册；不在白名单的邮箱不发送验证码（提示“该邮箱未开通访问权限”）。
- **验证码策略**：6 位数字、5 分钟有效、一次性使用；同一邮箱 **60s 频控**；`smtplib` 同步发送，超时 5s，发送失败不落库。
- **登录态**：Flask session（签名 Cookie），`HttpOnly` + `SameSite=Lax`，有效期 30 天；全局 `before_request` 拦截未登录的 `/api/*`。
- **超级管理员**：通过 `ADMIN_EMAIL` 环境变量在启动时幂等种子（`is_admin=True`、`name=admin`），该邮箱可直接登录。登录后首页顶栏出现「用户管理」入口。
- **用户管理（仅超管）**：
  - 新建用户（邮箱 + 姓名）
  - 修改姓名（含超级管理员本人，顶栏即时刷新）
  - 停用/启用账号；**不能停用超级管理员**。
  - 被停用账号：登录时不发验证码（toast「账号已停用」）；若已登录，进行任意接口调用会返回 401 并在前端自动退出登录。
- **CLI 管理（一期入口）**：`python -m server.cli user-add <邮箱> <姓名> [--admin]` / `user-list` / `user-del`。

### 2. 项目管理（T2.3 / T2.4 / T8.1 / T8.2 / T8.4）

- **创建项目**：只需填写名称；后端创建本地目录骨架（`prototype/`、`prd/`、`reviews/`）并写入 DB，`creator` 为当前登录用户。
- **项目列表**：含创建者信息与 `is_creator` 标记，不再含任何 git 字段。
- **上传原型 zip（≤100MB）**：安全解压校验（路径穿越 / 解压总量 300MB / 条目数 5000 / 软链拒绝），智能下钻唯一一级子目录，跳过 `__MACOSX`、`.DS_Store`、`._*` 垃圾条目，**原子替换** `prototype/`，失败保留旧版本。前端使用 XHR 上传并展示进度。
- **上传 PRD markdown（≤5MB）**：幂等替换 `prd/` 旧文档。
- **删除项目**：删除本地目录 + 评论 + DB 记录；`demo` 演示项目禁止删除。
- **可评论开关（`commentable`）**：项目级开关，**仅创建者可切换**；关闭后全员评论入口置灰、写评论操作被拦截（已有评论仍可查看）。
- **查看器数据接口**：`overview`（文档列表 + 入口原型页 + 页面地图 + 对账摘要）、`prd` 原文、`reconcile` 对账明细。

### 3. 评论标注与状态闭环（T4.2 / T4.4 / T4.5 / T8.3 / T8.5 / T8.6）

- **评论定位（DOM 采集）**：浏览器端通过注入的 `bridge.js` 采集评论目标——`dom` / `page` / `doc_block` 三种类型，记录锚点 ID、`css_path`、`outer_html`、`text_excerpt`、`interaction_state`（弹窗状态 / 视口 / 滚动位置 / 路由）。
- **截图标注**：Vue 端接收 iframe 回传的 PNG Blob → 上传 → 后端用 Pillow 画红框 → 存 `shots/`；**红框始终由后端绘制**，不依赖前端二次渲染；评论抽屉支持缩略图点击放大。
- **状态机**：`待确认 → 已确认待修改 → 已修改`；`忽略`；`已修改 → 已确认待修改`（返工闭环）。
- **批量状态流转**：`confirm` / `ignore` / `mark_done` / `rework`，**仅项目创建者可操作**，逐条校验并报告跳过原因。
- **评论编辑 / 删除**：编辑内容、优先级、范围；删除为软删。
- **评论导出 zip（仅创建者）**：`scope=all`（四态全量）或 `scope=confirmed`（已确认待修改，标准交付范围）；导出包 `manifest.json + comments/ + shots/`，与项目目录同构。
- **评论抽屉（前端）**：右侧三栏布局（原型 / PRD / 评论，均可拖拽调宽）；标题栏显示评论与已选数量；宿主筛选 + 状态筛选；四按钮操作行（批量确认 / 标记已修改 / 批量忽略 / 返工）；按页面分组、两级折叠默认全部展开；无锚点段落按“页面 / 无锚点段落”分组展示。

### 4. 原型 ↔ 文档锚点联动（T3.2 / T3.3）

- **原型代理**：`/proto/{project_id}/...` 读取 `data/projects/{project_id}/prototype/`，并在 HTTP 响应中注入 `bridge.js`（**不修改磁盘项目文件**），路径校验防目录穿越。
- **页面地图解析**：从 PRD 第 4 章「页面地图」表格解析页面清单（`{name, proto_file, anchor}`），支持列名变体与坏行静默跳过。
- **锚点对账**：`matched`（双向命中）、`missing_in_proto`（文档有、原型无）、`undescribed`（原型有、文档未描述）、`duplicate_prd` / `duplicate_proto`（重复 ID）、`map_broken`（页面地图坏引用）。
- **双向联动**：点原型锚点图标 → 右侧文档定位；点文档锚点 → iframe 切页并滚动定位。

### 5. 前端页面（Vue 3）

- **Login**：邮箱验证码登录。
- **Home**：项目列表 + 创建项目 + 原型/PRD 上传（带进度）+ 删除项目 + 创建者工具（可评论开关、导出评论等）。
- **Viewer（分屏查看器）**：以 `slug`（避开数字主键防猜测）打开；三栏布局（原型 iframe / PRD 文档 / 评论抽屉），支持拖拽调宽。
- **UserManage（仅超管）**：用户列表 + 新建 / 改名 / 停用启用。
- **BridgeDemo / ShotDemo**：bridge 与截图链路的演示页。

---

## 技术架构与实现方式

### 后端（Flask + peewee + SQLite）

- **数据源拆分**：项目内容（原型 / PRD / 评论 JSON / 截图）全部落本地文件系统 `data/projects/{project_id}/`；SQLite 只存元数据。**评论 JSON 是事实源**，DB `comments` 表为展示缓存，状态流转会同时改写两者，避免“DB 与文件不一致”。
- **去 Git 本地化（T8.1）**：项目不再 clone/commit 到 git，上传即解压落盘；`project_id` 使用随机短 slug（kebab-case），与数字主键分离，路径不可猜测遍历。
- **轻量迁移**：`init_tables()` 幂等建表 + `_migrate()` 用 `ALTER TABLE` 补列（`users.disabled`），`seed_admin()` 幂等种子超管。
- **权限模型**：
  - 登录：`session["uid"]` + `before_request` 全局拦截（失效即 401 强制登出）。
  - 超管：`is_admin`，可访问 `/api/users`。
  - 创建者：上传 / 导出 / 可评论开关 / 批量状态流转，均校验 `creator_id`。
  - 停用：`disabled` 账号在发码处 403；已登录会话在任意 `/api/` 调用处 401 清 session。
- **安全**：
  - 上传体积上限：原型 zip 100MB、解压总量 300MB、条目 5000、PRD 5MB、截图 10MB。
  - zip 安全解压：拒绝路径穿越、软链；解压后原子替换。
  - 路径注入防护：slug / 路径段 / 文件名均用白名单正则校验。
  - session Cookie：`HttpOnly`、`SameSite=Lax`、`PERMANENT_SESSION_LIFETIME=30天`。

### 前端（Vue 3 + TS + Element Plus + Vite）

- Composition API + `<script setup>`；路由 `vue-router`，全局守卫做登录与超管页鉴权（`requiresAdmin`）。
- API 客户端 `api.ts`：统一封装 `GET/POST/PATCH/DELETE` 与带进度上传，**401 统一处理**（清除登录态并跳转 `/login`）。
- 分屏查看器为三栏可拖拽布局；原型通过 iframe 加载，`bridge.js` 注入实现采集与定位。

### 测试

- **单元 + 契约测试**：`server/tests/test_*.py`，覆盖 seed 超管、停用拦截、用户 CRUD 权限、上传安全、评论状态机、对账、导出等。
- **E2E**：Playwright，`tests/e2e/*.spec.ts`，覆盖登录、项目、评论、权限、抽屉、反向联动、截图、用户管理。

---

## 目录结构

```
platform/
├── server/                 # Flask 后端
│   ├── app.py              # 应用工厂 + 全局登录守卫
│   ├── config.py           # 环境变量驱动配置（含 .env 自动加载）
│   ├── models.py           # peewee 模型 + 建表/迁移/种子
│   ├── auth.py             # 邮箱验证码登录 / session / 停用校验
│   ├── users.py            # 用户管理（仅超管，CRUD/停启用）
│   ├── projects.py         # 项目创建/列表/设置/上传/删除/overview/reconcile
│   ├── reviews.py          # 评论增删改/批量状态/导出/截图读取
│   ├── page_map.py         # PRD 页面地图解析
│   ├── reconcile.py        # 锚点对账
│   ├── storage.py          # 项目本地目录工具
│   ├── proto_proxy/        # 原型代理 + bridge.js 注入
│   ├── shots/              # 截图落盘 + 红框标注
│   ├── cli.py              # 用户管理 CLI
│   └── requirements.txt
├── web/                    # Vue 3 前端
│   ├── src/
│   │   ├── views/          # Login/Home/Viewer/UserManage/BridgeDemo/ShotDemo
│   │   ├── components/     # CommentBox / CommentDrawer
│   │   ├── router/         # 路由 + 守卫
│   │   ├── api.ts          # API 客户端
│   │   ├── auth.ts         # 登录态 + 用户管理 API
│   │   └── projects.ts     # 项目/评论 API 与类型
│   ├── vite.config.ts      # dev 代理 /api → :8081
│   └── package.json
├── bridge/                 # 原型注入脚本（bridge.js + modern-screenshot）
├── tests/                  # pytest + Playwright E2E + run-smoke.sh
├── scripts/                # 存量数据迁移脚本
├── docs/                   # 阶段报告
├── Makefile                # dev / check / smoke / clean
├── docker-compose.yml      # server 容器编排
├── .env.example            # 环境变量模板
└── AGENTS.md               # AI 协作说明书（含硬规则）
```

---

## 快速开始（开发）

> 前置：`python3`（≥3.10）、`node`（≥18）、`git`。

```bash
# 1. 后端依赖
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# 2. 前端依赖
cd web
npm install
cd ..

# 3. 配置环境变量
cp .env.example .env   # 按需修改（至少填 ADMIN_EMAIL；生产填 SMTP_*）
```

### 一键启动开发环境（前后端并行）

```bash
make dev
# 后端 :8081（SMTP_FAKE=1，验证码写入 /tmp/ppp-fake-mailbox/<邮箱>.txt）
# 前端 :8080，Vite 代理 /api → :8081
# Ctrl+C 一起停止
```

也可分别启动：`make dev-server`（后端）与 `make dev-web`（前端）。

> 开发期后端默认 `SMTP_FAKE=1`，验证码写在 `/tmp/ppp-fake-mailbox/<邮箱>.txt`，打开该文件即可看到验证码，无需真实邮件服务。**部署到服务器时请用真实 SMTP（见下文）。**

### 首次登录

1. 用 `.env` 里的 `ADMIN_EMAIL` 邮箱在首页右上角登录。
2. 若想给其他同事开权限：进入「用户管理」→ 新建用户（邮箱 + 姓名）；普通邮箱不在白名单内会提示“该邮箱未开通访问权限”。

---

## 环境变量说明

项目启动时会**自动加载 `platform/.env`**（已存在的进程环境变量优先级更高，不会被覆盖）。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `PORT` | `8081` | 后端监听端口 |
| `PLATFORM_SECRET` | `dev-secret-change-me` | Flask session 签名密钥，**生产务必改为强随机串** |
| `ADMIN_EMAIL` | 空 | 超级管理员邮箱，启动时种子超管（`name=admin`、`is_admin=True`）；留空则不种子 |
| `SMTP_HOST` | 空 | SMTP 服务器地址（如 `smtp.163.com`） |
| `SMTP_PORT` | `25` | SMTP 端口（163 免费邮箱用 `465`） |
| `SMTP_USER` | 空 | SMTP 账号（163 通常是完整邮箱地址） |
| `SMTP_PASS` | 空 | SMTP 授权码（**不是邮箱登录密码**） |
| `SMTP_USE_SSL` | `0` | `1` 表示隐式 SSL（163 的 465/994 必需）；否则走 STARTTLS（587） |
| `SMTP_FROM` | 空 | 发件人地址，缺省沿用 `SMTP_USER` |
| `DATA_DIR` | `{项目}/data` | 数据根目录（SQLite + projects + shots） |
| `WEB_ORIGIN` | `http://localhost:8080` | 允许跨域的前端 origin（仅后端开发直连时用到；生产 Nginx 同域反代则无需） |

> 前端（Vite）另有构建期变量 `VITE_PROTO_ORIGIN`（放 `web/.env`）：控制原型 iframe 的 origin。开发留空自动用 `http://localhost:8081`；生产 Nginx **同域反代 `/proto`**（推荐）留空自动用当前站点 origin；后端在独立 `host:port` 提供时填 `http://<host>:<PORT>`。详见 `web/.env.example`。

> 163 免费邮箱配置示例（`SMTP_USE_SSL=1`、`SMTP_PORT=465`、密码填**授权码**）。更多邮箱配置见上文“注意事项”。

---

## 测试

```bash
make check   # ruff lint + pytest（单元/契约）+ 前端 vue-tsc 构建，全绿才算过
make smoke   # 自动起环境 → Playwright E2E（79 条）→ 清理
```

- `make check`：校验代码风格与类型，防止回归。
- `make smoke`：依赖已装好的前后端（会临时起服务，结束后自动清理）。

---

## 注意事项

1. **`.env` 是本地机密，不入库**（已在 `.gitignore`）；提交前务必备份 `.env.example` 而非 `.env`。
2. **用户为白名单制**：任何登录邮箱都必须先在 `users` 表（超管界面或 CLI）中登记，否则提示“该邮箱未开通访问权限”。
3. **停用账号**：不发验证码；已登录会话在任意接口调用处返回 401 并强制登出；**不能停用超级管理员**。
4. **demo 项目只读、不可删除**，内容指向 `tests/fixtures`。
5. **上传安全**：原型 zip 会做路径穿越 / 解压总量 / 条目数 / 软链校验，满足才原子替换 `prototype/`；请勿关闭这些校验。
6. **红框由后端绘制**：请勿在前端重复渲染，避免与后端不一致。
7. **评论事实源是项目目录 JSON**：编辑/状态流转/删除会同时改 DB 缓存与文件；如需备份，请连同 `data/projects/` 一起备份。
8. **生产多 worker**：并发提交评论时 `comment_id` 依靠 DB UNIQUE 约束兜底重试（最多 5 次），极端并发下可能返回 500；单机小团队规模下无碍。
9. **163 / QQ 等免费邮箱**：必须使用**授权码**而非登录密码，且 163 需 `SMTP_USE_SSL=1` + `SMTP_PORT=465`。

---

## 服务器部署

### 推荐拓扑（单机）

```
                             ┌───────────────┐
  浏览器 ──► Nginx :80        │  Nginx 静态    │
             │               │  web/dist     │
             │               └───┬───────────┘
             │ /api, /proto,      │
             │ /bridge.js,        │ 反向代理
             │ /vendor, /shots    ▼
             │               ┌───────────────┐
             │               │  Flask 后端    │   gunicorn :8081
             └──────────────►│  (单进程/多worker)│
                             └───┬───────────┘
                                 │  SQLite + data/projects + data/shots
```

- **后端**用 `gunicorn` 托管（生产不建议 `python app.py` 的 dev server），数据目录 `data/` 持久化。
- **前端**：`npm run build` 产出 `web/dist`，由 Nginx 托管；`/api` `/proto` `/bridge.js` `/vendor` `/shots` 等路径反代到后端。

### 方式 A：Docker Compose（后端容器）

仓库自带 `docker-compose.yml`（仅 `server` 服务）：

```bash
# 1. 准备 .env（供 compose 使用）
cp .env.example .env

# 2. 构建并启动后端
docker compose up -d --build

# 3. 前端：本地 build 后由 Nginx 或任意静态服务托管 web/dist
```

compose 会注入 `PLATFORM_SECRET`、`SMTP_*`，并把 `DATA_DIR=/data` 挂载到命名卷 `server-data`（持久化 SQLite 与项目文件）。

### 方式 B：手动部署（systemd + Nginx 示例）

**后端**

```bash
cd platform
python3 -m venv server/.venv
server/.venv/bin/pip install -r server/requirements.txt   # + pip install gunicorn
cp .env.example .env   # 填好 ADMIN_EMAIL / PLATFORM_SECRET / SMTP_*
cd server
.venv/bin/gunicorn -b 0.0.0.0:8081 -w 2 app:app
```

> `app.py` 顶部已把 `platform/` 加入 `sys.path`，故 `cd server && gunicorn app:app` 可直接导入 `server.*`（或始终在 `platform/` 根执行 `server/.venv/bin/gunicorn -b 0.0.0.0:8081 -w 2 server.app:app`）。

**前端**

```bash
cd web
npm ci
npm run build          # 产 web/dist
```

**Nginx 示例**

```nginx
server {
    listen 80;
    server_name your.domain;

    # 前端静态
    root /srv/platform/web/dist;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }

    # 后端反代（/api/ 已含 /api/shots/ 截图读取）
    location /api/   { proxy_pass http://127.0.0.1:8081; }
    location /proto/ { proxy_pass http://127.0.0.1:8081; }
    location /bridge.js { proxy_pass http://127.0.0.1:8081; }
    location /vendor/ { proxy_pass http://127.0.0.1:8081; }

    # 上传体积对齐后端 MAX_CONTENT_LENGTH(110MB)
    client_max_body_size 110m;
}
```

> 注意：Nginx 反代时 `/api` 等路径需与后端保持一致；若前后端不同源，请同时配置 `WEB_ORIGIN` 或直接让 Nginx 同域反代以规避 CORS。

### 数据持久化与备份

- SQLite：`data/platform.db`
- 项目内容：`data/projects/{project_id}/`（prototype / prd / reviews）
- 截图临时区：`data/shots/`

建议定期备份整个 `data/` 目录，并在升级前备份（仓库提供 `scripts/migrate-t8.1.sh` 示例）。

### 上线前 Checklist

- [ ] `PLATFORM_SECRET` 改为强随机值
- [ ] `ADMIN_EMAIL` 已配置（否则无超管，无法建用户）
- [ ] SMTP 真实可用（163 需 `SMTP_USE_SSL=1` + 授权码）
- [ ] `client_max_body_size` ≥ 后端 `MAX_CONTENT_LENGTH`（上传原型 zip）
- [ ] `make check` 与 `make smoke` 全绿
- [ ] 生产用 `gunicorn`，不要用 dev server
