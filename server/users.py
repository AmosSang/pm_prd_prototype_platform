"""users 蓝图（T2.1 用户管理增强）：仅超级管理员可用的用户管理。

接口：
- GET   /api/users → 用户列表（仅超管）
- POST  /api/users {email, name} → 创建用户（仅超管；白名单邮箱接入）
- PATCH /api/users/<uid> {name} → 修改姓名（仅超管，含本人——同步 session）
- PATCH /api/users/<uid>/status {disabled} → 停用/启用（仅超管，不能停用超管账号）

权限：所有接口先过 app.before_request（已登录 + 未停用），再在蓝图内校验 is_admin。
停用链路：停用后该用户 request-code 发码被拒（账号已停用 toast）；
已登录会话在任意 /api/ 调用时被 before_request 401 强制登出（见 app.py）。
"""
from flask import Blueprint, jsonify, request, session

from server.models import User

bp = Blueprint("users", __name__, url_prefix="/api/users")


def _err(msg: str, status: int):
    return jsonify(code=status, msg=msg), status


def _current_admin() -> User | None:
    """当前会话用户若为超管（且未停用）则返回，否则 None。"""
    uid = session.get("uid")
    if not uid:
        return None
    u = User.get_or_none(User.id == uid)
    if not u or not u.is_admin or u.disabled:
        return None
    return u


def _deny_non_admin():
    """非超管 → 403；返回 None 表示放行。"""
    if not _current_admin():
        return _err("仅超级管理员可操作", 403)
    return None


def _user_public(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "is_admin": u.is_admin,
        "disabled": u.disabled,
        "created_at": u.created_at,
    }


@bp.get("")
def list_users():
    deny = _deny_non_admin()
    if deny:
        return deny
    rows = User.select().order_by(User.id)
    return jsonify(code=0, data=[_user_public(u) for u in rows]), 200


@bp.post("")
def create_user():
    deny = _deny_non_admin()
    if deny:
        return deny
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    name = str(data.get("name") or "").strip()
    if not email or "@" not in email:
        return _err("邮箱格式不正确", 400)
    if len(name) > 50:
        return _err("姓名 50 字内", 400)
    if not name:
        return _err("姓名必填", 400)
    if User.get_or_none(User.email == email):
        return _err("该邮箱已开通，请勿重复添加", 409)
    u = User.create(email=email, name=name)
    return jsonify(code=0, data=_user_public(u)), 200


@bp.patch("/<int:uid>")
def update_user(uid: int):
    deny = _deny_non_admin()
    if deny:
        return deny
    u = User.get_or_none(User.id == uid)
    if not u:
        return _err("用户不存在", 404)
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return _err("姓名必填", 400)
    if len(name) > 50:
        return _err("姓名 50 字内", 400)
    u.name = name
    u.save()
    # 改的是本人 → 同步 session name，顶栏即时刷新
    if u.id == session.get("uid"):
        session["name"] = u.name
    return jsonify(code=0, data=_user_public(u)), 200


@bp.patch("/<int:uid>/status")
def set_status(uid: int):
    deny = _deny_non_admin()
    if deny:
        return deny
    u = User.get_or_none(User.id == uid)
    if not u:
        return _err("用户不存在", 404)
    data = request.get_json(silent=True) or {}
    disabled = bool(data.get("disabled"))
    if disabled and u.is_admin:
        return _err("不能停用超级管理员账号", 400)
    u.disabled = disabled
    u.save()
    return jsonify(code=0, data=_user_public(u)), 200
