"""auth 蓝图：邮箱验证码登录（T2.1）+ Bearer 双轨认证（T12.1）。

规则（技术方案 §2.9）：
- 用户白名单：users 表由管理员维护，无自助注册；不在白名单的邮箱不发码
- 验证码：6 位数字、5 分钟有效、一次性
- 频控：同邮箱 60s 内仅发 1 条（DB 时间戳校验）
- SMTP：smtplib 同步发送，超时 5s；失败返回明确错误（码不落库）
- 登录态：Flask session（签名 cookie），HttpOnly + SameSite=Lax，30 天

T12.1 Bearer 双轨（PRD §5.2 / AGENTS.md §4.4）：
- Token 明文 `ppp_` + 32 字节随机 hex，仅生成时返回一次，不落库不进日志
- Authorization: Bearer ppp_xxx 与 session 双轨；Bearer 仅 /api/ 生效；
  显式携带 Bearer 时认证失败一律 401，不回退 session（防降级）
- 认证成功：身份注入 session（与登录等价，既有接口零改动），但响应
  不回种 session cookie（_BearerAwareSessionInterface）——客户端拿不到
  可复用登录态，Token 撤销即时生效不被绕过
- 单 token 进程内限流 60 次/分钟，超限 429
"""
import datetime as dt
import hashlib
import os
import random
import re
import secrets
import smtplib
import threading
import time
from collections import deque
from email.mime.text import MIMEText

from flask import Blueprint, jsonify, request, session
from flask.sessions import SecureCookieSessionInterface

from server.config import (
    PLATFORM_SECRET,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_USE_SSL,
    SMTP_USER,
)
from server.models import ApiToken, User, VerificationCode, parse_utc, utcnow_str


def email_file_name(email: str) -> str:
    """邮箱 → 安全文件名（FAKE mailbox 用）。"""
    return re.sub(r"[^a-z0-9@.-]", "_", email.lower())

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

CODE_TTL_SECONDS = 5 * 60
CODE_RESEND_SECONDS = 60
SESSION_TTL_DAYS = 30
SMTP_TIMEOUT_SECONDS = 5

# session 有效期（Flask permanent session）
bp.session_ttl_days = SESSION_TTL_DAYS


def _smtp_ready() -> bool:
    # SMTP_FAKE=1：E2E 测试模式，不依赖真实 SMTP（码写入 /tmp/ppp-fake-mailbox/）
    if os.environ.get("SMTP_FAKE") == "1":
        return True
    return bool(SMTP_HOST)


FAKE_MAILBOX_DIR = "/tmp/ppp-fake-mailbox"


def _send_code_email(to_email: str, code: str) -> None:
    """同步发送验证码邮件。失败抛异常，由调用方转 502。

    SMTP_FAKE=1 时不走 SMTP，码写入 /tmp/ppp-fake-mailbox/<email>.txt
    （E2E 从这里取码走真实 verify 流程）。
    """
    if os.environ.get("SMTP_FAKE") == "1":
        os.makedirs(FAKE_MAILBOX_DIR, exist_ok=True)
        safe = email_file_name(to_email)
        with open(os.path.join(FAKE_MAILBOX_DIR, safe), "w", encoding="utf-8") as f:
            f.write(f"To: {to_email}\nSubject: 登录验证码\n\n{code}\n")
        return

    subject = "产品方案展示平台 · 登录验证码"
    body = (
        f"你的登录验证码是：{code}\n\n"
        f"验证码 5 分钟内有效，仅可使用一次。\n"
        f"如果这不是你本人的操作，请忽略本邮件。"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM or SMTP_USER or "noreply@platform.local"
    msg["To"] = to_email

    if SMTP_USE_SSL:
        # 隐式 SSL（163 的 465/994 等）：连接即加密，无需 starttls
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            if SMTP_USER and SMTP_PASS:
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.sendmail(msg["From"], [to_email], msg.as_string())
    else:
        # 明文或 STARTTLS（587 等）：有账号密码时先 upgrade 到 TLS 再登录
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            if SMTP_USER and SMTP_PASS:
                smtp.starttls()
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.sendmail(msg["From"], [to_email], msg.as_string())


def _err(msg: str, status: int, extra: dict | None = None):
    body = {"code": status, "msg": msg}
    if extra:
        body.update(extra)
    return jsonify(body), status


@bp.post("/request-code")
def request_code():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()

    if not email or "@" not in email:
        return _err("邮箱格式不正确", 400)

    # 白名单校验：users 表无此邮箱 → 不发码（不暴露是否存在）
    user = User.get_or_none(User.email == email)
    if not user:
        return _err("该邮箱未开通访问权限，请联系管理员", 403)
    if user.disabled:
        return _err("账号已停用，请联系管理员", 403)

    # 频控：同邮箱 60s 内仅 1 条
    last = (
        VerificationCode.select()
        .where(VerificationCode.email == email)
        .order_by(VerificationCode.created_at.desc())
        .first()
    )
    if last:
        elapsed = (parse_utc(utcnow_str()) - parse_utc(last.created_at)).total_seconds()
        if elapsed < CODE_RESEND_SECONDS:
            wait = int(CODE_RESEND_SECONDS - elapsed) + 1
            return _err(f"发送过于频繁，请 {wait} 秒后重试", 429)

    # SMTP 未配置 → 明确报错（不静默）
    if not _smtp_ready():
        return _err("邮件服务未配置（SMTP_HOST 为空），请联系管理员", 503)

    code = f"{random.randint(0, 999999):06d}"

    # 先发送，成功才落库（失败时无脏数据）
    try:
        _send_code_email(email, code)
    except Exception as e:  # noqa: BLE001 — 对外统一转 502
        return _err(f"验证码邮件发送失败：{e}", 502)

    expires_at = (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=CODE_TTL_SECONDS)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    VerificationCode.create(email=email, code=code, expires_at=expires_at)

    return jsonify(code=0, data={"sent": True}), 200


@bp.post("/verify")
def verify():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    code = str(data.get("code") or "").strip()

    if not email or not code:
        return _err("邮箱与验证码不能为空", 400)

    user = User.get_or_none(User.email == email)
    if not user:
        return _err("该邮箱未开通访问权限，请联系管理员", 403)
    if user.disabled:
        return _err("账号已停用，请联系管理员", 403)

    vc = (
        VerificationCode.select()
        .where(VerificationCode.email == email, VerificationCode.used == False)  # noqa: E712
        .order_by(VerificationCode.created_at.desc())
        .first()
    )
    if not vc:
        return _err("验证码不存在或已使用，请重新获取", 400)

    now = parse_utc(utcnow_str())
    if parse_utc(vc.expires_at) < now:
        return _err("验证码已过期，请重新获取", 400)
    if vc.code != code:
        return _err("验证码错误", 400)

    # 一次性：核销后再建 session
    vc.used = True
    vc.save()

    session.clear()
    session.permanent = True
    session["uid"] = user.id
    session["email"] = user.email
    session["name"] = user.name
    session["is_admin"] = user.is_admin
    return jsonify(
        code=0,
        data={"user": {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin}},
    ), 200


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify(code=0, data={"ok": True}), 200


@bp.get("/me")
def me():
    uid = session.get("uid")
    if not uid:
        return _err("未登录", 401)
    user = User.get_or_none(User.id == uid)
    if not user:
        session.clear()
        return _err("未登录", 401)
    if user.disabled:
        session.clear()
        return _err("账号已停用，请联系管理员", 401)
    return jsonify(
        code=0,
        data={"user": {"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin}},
    ), 200


# ─── Bearer 双轨认证（T12.1）─────────────────────────────────────────

TOKEN_PREFIX = "ppp_"
TOKEN_RANDOM_BYTES = 32            # 32 字节 → 64 hex 字符
BEARER_RATE_LIMIT = 60             # 单 token 限流：60 次
BEARER_RATE_WINDOW_SECONDS = 60    # 限流窗口：60 秒
LAST_USED_FLUSH_SECONDS = 60       # last_used_at 写库节流间隔


def generate_token_plaintext() -> str:
    """生成 Token 明文：`ppp_` + 32 字节随机 hex。

    仅在生成接口响应中返回一次；调用方（tokens API）不写日志、不落库，
    库中只存 hash_token(plaintext)（有单测断言）。
    """
    return TOKEN_PREFIX + secrets.token_hex(TOKEN_RANDOM_BYTES)


def hash_token(plaintext: str) -> str:
    """Token 明文 → SHA-256 hex（含 ppp_ 前缀整体求哈希）。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class SlidingWindowLimiter:
    """进程内滑动窗口限流（线程安全）。

    单 token 60 次/分钟；多 worker 部署时为每进程独立额度（内部工具可
    接受，任务卡口径即「进程内限流」）。
    """

    def __init__(self, limit: int = BEARER_RATE_LIMIT, window: int = BEARER_RATE_WINDOW_SECONDS):
        self.limit = limit
        self.window = window
        self._hits: dict[int, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: int) -> bool:
        now = time.monotonic()
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            while dq and now - dq[0] > self.window:
                dq.popleft()
            if len(dq) >= self.limit:
                return False
            dq.append(now)
            return True

    def reset(self) -> None:
        """清空窗口（测试隔离用）。"""
        with self._lock:
            self._hits.clear()


bearer_limiter = SlidingWindowLimiter()


def try_bearer_auth():
    """Bearer 认证（T12.1）。仅对 /api/ 路径由 app.require_login 调用。

    返回 None = 认证成功（身份已注入 session，请求放行）；否则返回
    (错误响应, status)——显式携带 Bearer 时失败一律拒绝，不回退 session。
    """
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[len("Bearer "):].strip()
    if not token.startswith(TOKEN_PREFIX):
        return _err("无效的 Authorization 头（需 Bearer ppp_xxx）", 401)

    rec = ApiToken.get_or_none(ApiToken.token_hash == hash_token(token))
    if rec is None:
        return _err("Token 无效", 401)
    if rec.revoked:
        return _err("Token 已撤销", 401)

    user = User.get_or_none(User.id == rec.user_id)
    if user is None:
        return _err("Token 绑定用户不存在", 401)
    if user.disabled:
        return _err("账号已停用，请联系管理员", 401)

    if not bearer_limiter.allow(rec.id):
        return _err("请求过于频繁（Token 限流 60 次/分钟），请稍后重试", 429)

    # 身份注入：与 session 登录等价（uid/email/name/is_admin 全量），
    # 既有接口 session.get(...) 零改动即可读到正确身份。
    session["uid"] = user.id
    session["email"] = user.email
    session["name"] = user.name
    session["is_admin"] = user.is_admin

    # last_used_at 写库节流：距上次更新 ≥60s 才写（高频下避免写放大）
    now = utcnow_str()
    if rec.last_used_at is None or (
        parse_utc(now) - parse_utc(rec.last_used_at)
    ).total_seconds() >= LAST_USED_FLUSH_SECONDS:
        rec.last_used_at = now
        rec.save()
    return None


class _BearerAwareSessionInterface(SecureCookieSessionInterface):
    """Bearer 请求不回种 session cookie（T12.1）。

    Bearer 认证虽把身份写入 session（等价注入，既有接口零改动），但绝不
    向客户端下发签名 cookie——Agent 客户端（httpx 等）不会因此获得可复用
    的登录态，Token 撤销后无法靠残留 cookie 继续访问（撤销即时生效）。

    判据 = Authorization 头本身（无跨请求状态）：不要用 g 之类的请求间
    标志——Flask 在已激活的同 app context 内发请求（如测试 fixture 或
    CLI 脚本）会复用外层 context，g 上的标志会残留泄漏。
    """

    def save_session(self, app, session, response):  # noqa: A002 — Flask 签名
        p = request.path
        is_bearer_request = (
            p.startswith("/api/")
            and not p.startswith("/api/auth/")
            and p != "/api/health"
            and request.headers.get("Authorization", "").startswith("Bearer ")
        )
        if is_bearer_request:
            return
        super().save_session(app, session, response)


def apply_auth_to_app(app):
    """注入 session 配置（应用工厂调用）。"""
    app.secret_key = PLATFORM_SECRET
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = dt.timedelta(days=SESSION_TTL_DAYS)
    # 开发期 Vite :8080 与 Flask :8081 跨端口，session cookie 需携带
    app.config["SESSION_COOKIE_NAME"] = "pp_session"
    # T12.1：Bearer 感知的 session interface（Bearer 响应不回种 cookie）
    app.session_interface = _BearerAwareSessionInterface()
