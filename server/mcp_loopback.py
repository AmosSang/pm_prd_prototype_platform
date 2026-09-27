"""MCP → Flask 回环 HTTP 客户端（T12.3）。

FastMCP 工具不在进程内直连 DB：统一经 httpx 回环调用既有 Flask API——
权限校验（管理者判定）、上传安全校验（zip-slip/炸弹/软链）、评论写文件逻辑
全部复用 Flask 既有实现，单一权限入口（PRD §5.2.2 / AGENTS.md §7）。

- Bearer 头透传：Agent →（MCP 请求头）→ FastMCP → 本模块 → Flask
  （Flask 侧 ``auth.try_bearer_auth`` 解析为绑定用户身份）。
- 测试注入点：``transport_factory`` 可替换为指向 Flask test_client 的 httpx
  transport（集成测试免起真实端口）；生产/开发保持默认（真实网络回环）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

import httpx

# 回环目标：compose 内为 http://server:8081（compose 服务名），本地开发 127.0.0.1
API_BASE = os.environ.get("PPP_API_BASE", "http://127.0.0.1:8081")
DEFAULT_TIMEOUT = 15.0    # 读接口
UPLOAD_TIMEOUT = 180.0    # 上传类（大 zip 落盘 + 校验解压；T12.4 用）

def _default_transport() -> httpx.BaseTransport | None:
    """默认真实网络；测试可替换 ``transport_factory`` 注入 Flask test_client。"""
    return None


# 测试注入点：返回 None → 真实网络
transport_factory: Callable[[], httpx.BaseTransport | None] = _default_transport


@dataclass
class ApiResult:
    """回环请求结果（Flask 统一 ``{code, data, msg}`` 结构）。

    status_code=0 表示网络层失败（连接拒绝/超时等），payload 恒为 None。
    """

    status_code: int
    payload: dict[str, Any] | None = None
    raw_text: str = ""

    @property
    def network_error(self) -> bool:
        return self.status_code == 0

    @property
    def ok(self) -> bool:
        return (
            self.status_code == 200
            and isinstance(self.payload, dict)
            and self.payload.get("code") == 0
        )

    @property
    def data(self) -> Any:
        return (self.payload or {}).get("data")

    @property
    def msg(self) -> str:
        return str((self.payload or {}).get("msg") or "")


def call_api(
    method: str,
    path: str,
    bearer: str | None,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    files: Any = None,
    data: Any = None,
    timeout: float | None = None,
) -> ApiResult:
    """同步回环调用 Flask API（FastMCP 对同步工具在线程池执行，不阻塞事件循环）。"""
    headers: dict[str, str] = {}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"

    transport = transport_factory()
    try:
        with httpx.Client(
            base_url=API_BASE,
            transport=transport,
            timeout=timeout or DEFAULT_TIMEOUT,
            follow_redirects=False,
        ) as client:
            resp = client.request(
                method,
                path,
                headers=headers,
                params=params,
                json=json_body,
                files=files,
                data=data,
            )
    except httpx.HTTPError as e:  # 连接拒绝/超时/DNS 等
        return ApiResult(status_code=0, raw_text=f"{type(e).__name__}: {e}")

    try:
        payload = resp.json()
    except ValueError:
        payload = None
    return ApiResult(
        status_code=resp.status_code,
        payload=payload if isinstance(payload, dict) else None,
        raw_text=resp.text,
    )
