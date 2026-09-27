# POC 结论报告：MCP 大文件上传（base64 参数经 streamable-http）

> 任务卡 T12.4 产出 · 验证日期：2026-09-27 · 结论等级：**可行，已定型（含关键补丁）**
> 关联：PRD §5.2.3 / AGENTS.md §7；脚本：`.devtools/poc-mcp-upload.py`（本机 `.devtools` 为不入库的开发辅助目录）

## 一、结论速览

| 验证项 | 结论 | 依据 |
|--------|------|------|
| base64 单参数大文件经 streamable-http | ✅ 可行 | 5/20/50MB zip 全通过；加测 100MB（Flask 全量程上界）通过 |
| 默认配置可用性 | ❌ **不可用，必须补丁** | mcp SDK 默认请求体上限 **4 MiB**：5MB zip（7MB base64）即被 413 秒拒 |
| 补丁后上限 | ✅ 160 MiB（可配 `MCP_MAX_REQUEST_BODY_BYTES`） | 覆盖「100MB zip → ~134MB base64」含信封余量 |
| 超限报错体验 | 传输层 413（非工具级结构化错误） | 超限请求在 MCP 会话建立前被拒；**靠工具描述写明上限让 Agent 预检** |
| 降级方案 | **未触发**（预案保留） | 分块/curl 直传方案不需要启用 |

## 二、关键发现记录

### 2.1 失败现象与根因（第一轮，补丁前）

三档（5/20/50MB）**全部失败**，且均为 0.09~0.39s 秒拒：

```
HTTPStatusError: Client error '413 Content Too Large' for url 'http://127.0.0.1:8092/mcp'
```

根因定位（自底向上）：

1. `mcp/server/transport_security.py` 的 `RequestBodyLimitMiddleware`：在进 ASGI 应用前先检查 `content-length`，超限即回 `413 Request body too large`；
2. 该中间件由 `StreamableHTTPSessionManager.__init__` 构造，参数 `max_request_body_size: int = DEFAULT_MAX_REQUEST_BODY_SIZE`；
3. **`DEFAULT_MAX_REQUEST_BODY_SIZE = 4194304`（4 MiB）**，即 SDK 默认值；
4. **FastMCP 3.4.7 未透出该参数**：`FastMCPStreamableHTTPSessionManager.__init__` 签名不含该参数、也不接收 `**kwargs`，构造点（`fastmcp/server/http.py` lifespan）无法从外部传入。

### 2.2 修复方式（已落地进 `server/mcp_server.py`）

子类补丁 `_BigBodySessionManager`：复刻 FastMCP 子类自身初始化（仅 `_shared_event_store` 一条状态），直接调 SDK 基类 `StreamableHTTPSessionManager.__init__` 并注入 `max_request_body_size`，再替换 `fastmcp.server.http.FastMCPStreamableHTTPSessionManager`（lifespan 运行时按模块全局名查找 → 生效）。

- 上限配置：环境变量 `MCP_MAX_REQUEST_BODY_BYTES`，默认 **160 MiB**；
- ⚠️ **依赖 fastmcp 内部构造点**：fastmcp 升级需回归本补丁（requirements 已锁 `fastmcp==3.4.7`）。

### 2.3 补丁后实测（第二轮）

| zip 大小 | base64 大小 | 结果 | 耗时（本机回环） |
|---------|------------|------|----------------|
| 5 MB | 6.7 MB | ✅ | 0.10s |
| 20 MB | 26.7 MB | ✅ | 0.21s |
| 50 MB | 66.7 MB | ✅ | 0.47s |
| **100 MB**（加测，Flask 侧上限） | 133.4 MB | ✅ | 0.95s |

> 100MB 加测原因：Flask 侧原型 zip 上限为 100MB（`PROTO_ZIP_MAX_BYTES`），需确认 160 MiB 信封能覆盖「全量程 + base64 膨胀 33% + JSON 信封」。实测通过。

### 2.4 超限报错体验（写进工具描述的依据）

- 超限时是**传输层 HTTP 413**，MCP 客户端报 `HTTPStatusError`，**不经过工具函数**，因此不会产生工具级 `{error_code,...}` 三段式；
- 缓解：工具描述中明示上限（原型 zip ≤100MB、PRD ≤5MB），引导 Agent 在调用前预检文件大小；
- 服务端双保险：Flask 侧超限报 413 也会被工具映射为 `too_large` 三段式（文件在限内、但超 Flask 规则时生效）。

## 三、对后续开发/部署的影响

1. **T12.4 实现**：`upload_prototype` / `upload_prd` 以 base64 字符串为入参（Agent 读本机文件 → base64 → 传入），工具内转 bytes 走 multipart 回环调 Flask 既有上传接口（复用 zip 安全校验全链路）；
2. **T12.5 部署**：Nginx `client_max_body_size` 需 ≥ 200m（按 134MB base64 信封+余量），否则 413 提前发生在 Nginx；
3. **内存提示**：单次 134MB base64 在 MCP 进程约产生 ~0.3–0.5GB 瞬时内存（解码+JSON），内部工具规模可接受；如后续出现并发大文件上传，再评估流式/分块协议（预案 a）；
4. 降级预案（分块两步协议 / 返回 curl 直传指导）**保留不启用**，写入本报告备查。

## 四、复现方式

```bash
# 补丁前进程（mcp_server.py 不导入时对应 SDK 默认 4MiB，可见 413）：
POC_TARGETS=5,20,50 server/.venv/bin/python .devtools/poc-mcp-upload.py
# 加测边界：
POC_TARGETS=100 server/.venv/bin/python .devtools/poc-mcp-upload.py
```
