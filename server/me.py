"""当前用户信息（T12.4）：供 MCP 审计日志等消费方以 Bearer/session 取身份。

与 /api/auth/me 的区别：本蓝图挂在普通 /api/ 前缀下——`require_login` 会对
它执行 **Bearer 双轨认证**（/api/auth/* 在拦截器里放行、不解析 Bearer）。
返回当前请求上下文注入的身份（session 与 Bearer 等价）。
"""
from flask import Blueprint, jsonify, session

from server.models import User

bp = Blueprint("me", __name__, url_prefix="/api/me")


@bp.get("")
def me():
    uid = session.get("uid")
    user = User.get_or_none(User.id == uid) if uid else None
    if not user:
        return jsonify(code=401, msg="未登录"), 401
    if user.disabled:
        return jsonify(code=401, msg="账号已停用，请联系管理员"), 401
    return jsonify(
        code=0,
        data={"id": user.id, "email": user.email, "name": user.name, "is_admin": user.is_admin},
    ), 200
