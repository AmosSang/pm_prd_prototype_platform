"""截图落盘与红框标注（T1.2）。

流程：Vue 端收到 iframe 回传的 PNG Blob → FormData 上传 → Pillow 画红框 →
存 /data/shots/{project}/{request_id}.png → 返回访问路径。
红框永远由后端画（不依赖前端二次渲染）。
"""
import os
import re
import time

from flask import Blueprint, jsonify, request, send_file

from server.config import DATA_DIR

bp = Blueprint("shots", __name__)

SHOTS_DIR = os.path.join(DATA_DIR, "shots")

REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB 上限

RED = (220, 38, 38)  # 红框颜色
BORDER = 3  # 线宽


@bp.post("/api/projects/<project_id>/shots")
def upload_shot(project_id: str):
    if not re.fullmatch(r"^[a-z0-9-]{1,32}$", project_id):
        return jsonify(code=1, msg="非法项目 ID"), 400

    f = request.files.get("screenshot")
    if f is None:
        return jsonify(code=1, msg="缺少 screenshot 文件"), 400

    req_id = request.form.get("request_id", "")
    if not REQUEST_ID.fullmatch(req_id):
        return jsonify(code=1, msg="非法 request_id"), 400

    # highlight_rect（JSON 字符串，四字段均为非负整数）
    rect = {}
    try:
        import json

        raw = request.form.get("highlight_rect")
        if raw:
            rect = json.loads(raw)
        for k in ("x", "y", "w", "h"):
            rect[k] = int(rect.get(k, -1))
            if rect[k] < 0:
                raise ValueError
    except (ValueError, TypeError):
        return jsonify(code=1, msg="highlight_rect 格式非法"), 400

    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify(code=1, msg="截图超过 10MB 上限"), 413

    # 画红框（后端保证红框永远在）
    img_bytes = _draw_border(data, rect)

    out_dir = os.path.join(SHOTS_DIR, project_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{req_id}.png")
    with open(out_path, "wb") as out:
        out.write(img_bytes)

    return jsonify(code=0, data={"shot_url": f"/api/shots/{project_id}/{req_id}.png"}), 200


@bp.get("/api/shots/<project_id>/<filename>")
def get_shot(project_id: str, filename: str):
    if not re.fullmatch(r"^[a-z0-9-]{1,32}$", project_id):
        return jsonify(code=1, msg="非法项目 ID"), 400
    if not re.fullmatch(r"^[A-Za-z0-9_-]{1,64}\.png$", filename):
        return jsonify(code=1, msg="非法文件名"), 400
    path = os.path.join(SHOTS_DIR, project_id, filename)
    if not os.path.isfile(path):
        return jsonify(code=1, msg="截图不存在"), 404
    return send_file(path, mimetype="image/png")


def _draw_border(png_bytes: bytes, rect: dict) -> bytes:
    """在截图上画目标区域红框；rect 为空或 PIL 不可用时返回原图。"""
    if not rect or not all(k in rect for k in ("x", "y", "w", "h")):
        return png_bytes
    try:
        import io

        from PIL import Image, ImageDraw

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img)
        x, y, w, h = rect["x"], rect["y"], rect["w"], rect["h"]
        # 裁剪到图片范围内
        x = max(0, min(x, img.width))
        y = max(0, min(y, img.height))
        w = max(0, min(w, img.width - x))
        h = max(0, min(h, img.height - y))
        for i in range(BORDER):
            draw.rectangle(
                [x + i, y + i, x + w - 1 - i, y + h - 1 - i],
                outline=RED,
            )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        return png_bytes


def make_request_id() -> str:
    """生成截图请求 ID（临时演示用；评论系统接入后由 comment_id 承担）。"""
    return f"shot-{int(time.time() * 1000)}"
