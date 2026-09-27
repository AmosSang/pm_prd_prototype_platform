"""tokens API（T12.1）：Agent 接入 Token 的生成 / 列表 / 撤销。

- POST /api/tokens      生成：{name} → 明文 plaintext 仅本次响应返回一次，
                        不落库、不进日志（有单测断言）；库中只存 SHA-256 哈希
- GET  /api/tokens      列表：本人全部 token（名称/生成时间/最近使用/撤销态）
- DELETE /api/tokens/<id> 撤销：置 revoked=True，即时生效（逐请求校验）

权限 = 当前登录用户（session 或 Bearer 均可），只能操作自己的 token。
"""
import os
import sys

from flask import Blueprint, jsonify, request, session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.auth import generate_token_plaintext, hash_token  # noqa: E402
from server.models import ApiToken  # noqa: E402

bp = Blueprint("tokens", __name__, url_prefix="/api/tokens")

NAME_MAX_LEN = 50


def _err(msg: str, status: int):
    return jsonify(code=status, msg=msg), status


def _token_public(rec: ApiToken) -> dict:
    """列表/撤销响应对外字段（绝不含 token_hash 与明文）。"""
    return {
        "id": rec.id,
        "name": rec.name,
        "created_at": rec.created_at,
        "last_used_at": rec.last_used_at,
        "revoked": rec.revoked,
    }


def _require_uid() -> int | None:
    return session.get("uid")


@bp.post("")
def create_token():
    """生成 Token：明文一次性返回，前端提示「仅显示一次」。"""
    uid = _require_uid()
    if not uid:
        return _err("未登录", 401)

    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return _err("Token 名称不能为空", 400)
    if len(name) > NAME_MAX_LEN:
        return _err(f"Token 名称过长（{NAME_MAX_LEN} 字内）", 400)

    plaintext = generate_token_plaintext()
    rec = ApiToken.create(user=uid, token_hash=hash_token(plaintext), name=name)
    return jsonify(code=0, data={**_token_public(rec), "plaintext": plaintext}), 200


@bp.get("")
def list_tokens():
    uid = _require_uid()
    if not uid:
        return _err("未登录", 401)
    rows = (
        ApiToken.select()
        .where(ApiToken.user == uid)
        .order_by(ApiToken.id.desc())
    )
    return jsonify(code=0, data=[_token_public(r) for r in rows]), 200


@bp.delete("/<int:token_id>")
def revoke_token(token_id: int):
    """撤销 Token（幂等）：撤销后下一次请求即 401。"""
    uid = _require_uid()
    if not uid:
        return _err("未登录", 401)
    rec = ApiToken.get_or_none(ApiToken.id == token_id, ApiToken.user == uid)
    if rec is None:
        return _err("Token 不存在", 404)
    rec.revoked = True
    rec.save()
    return jsonify(code=0, data=_token_public(rec)), 200
