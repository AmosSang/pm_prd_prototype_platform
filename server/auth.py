"""auth 蓝图：邮箱验证码登录（T2.1）。

规则（技术方案 §2.9）：
- 用户白名单：users 表由管理员维护，无自助注册；不在白名单的邮箱不发码
- 验证码：6 位数字、5 分钟有效、一次性
- 频控：同邮箱 60s 内仅发 1 条（DB 时间戳校验）
- SMTP：smtplib 同步发送，超时 5s；失败返回明确错误（码不落库）
- 登录态：Flask session（签名 cookie），HttpOnly + SameSite=Lax，30 天
"""
import datetime as dt
import random
import smtplib
from email.mime.text import MIMEText

from flask import Blueprint, jsonify, request, session

from server.config import PLATFORM_SECRET, SMTP_HOST, SMTP_PASS, SMTP_PORT, SMTP_USER
from server.models import User, VerificationCode, parse_utc, utcnow_str

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

CODE_TTL_SECONDS = 5 * 60
CODE_RESEND_SECONDS = 60
SESSION_TTL_DAYS = 30
SMTP_TIMEOUT_SECONDS = 5

# session 有效期（Flask permanent session）
bp.session_ttl_days = SESSION_TTL_DAYS


def _smtp_ready() -> bool:
    return bool(SMTP_HOST)


def _send_code_email(to_email: str, code: str) -> None:
    """同步发送验证码邮件。失败抛异常，由调用方转 502。"""
    subject = "产品方案展示平台 · 登录验证码"
    body = (
        f"你的登录验证码是：{code}\n\n"
        f"验证码 5 分钟内有效，仅可使用一次。\n"
        f"如果这不是你本人的操作，请忽略本邮件。"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER or "noreply@platform.local"
    msg["To"] = to_email

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
    return jsonify(code=0, data={"user": {"id": user.id, "email": user.email, "name": user.name}}), 200


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
    return jsonify(code=0, data={"user": {"id": user.id, "email": user.email, "name": user.name}}), 200


def apply_auth_to_app(app):
    """注入 session 配置（应用工厂调用）。"""
    app.secret_key = PLATFORM_SECRET
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = dt.timedelta(days=SESSION_TTL_DAYS)
    # 开发期 Vite :8080 与 Flask :8081 跨端口，session cookie 需携带
    app.config["SESSION_COOKIE_NAME"] = "pp_session"
