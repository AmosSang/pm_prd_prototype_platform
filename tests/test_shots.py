"""截图红框单测：纯 PIL 生成基准图 → 画框 → 像素级断言。"""
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server.shots import _draw_border

RED = (220, 38, 38)


def make_png(w: int, h: int, color=(240, 240, 240)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_size(png: bytes) -> tuple:
    from PIL import Image

    img = Image.open(io.BytesIO(png))
    return img.size


def pixel(png: bytes, x: int, y: int) -> tuple:
    from PIL import Image

    return Image.open(io.BytesIO(png)).getpixel((x, y))


BASE = make_png(200, 100)


def test_draw_border_pixels():
    """红框画在指定矩形边缘（3px 线宽），中心不受影响。"""
    out = _draw_border(BASE, {"x": 50, "y": 20, "w": 60, "h": 40})
    # 尺寸不变
    assert png_size(out) == (200, 100)
    # 边框线上的点是红色（外圈 x=50, 内圈 x=52）
    for x in (50, 51, 52):
        assert pixel(out, x, 20) == RED
        assert pixel(out, x, 59) == RED
    for y in (20, 21, 22):
        assert pixel(out, 50, y) == RED
        assert pixel(out, 109, y) == RED
    # 矩形中心保持原色
    assert pixel(out, 80, 40) == (240, 240, 240)
    # 矩形外不受影响
    assert pixel(out, 10, 10) == (240, 240, 240)


def test_draw_border_clamped():
    """越界矩形被裁剪到图片范围内，不抛异常。"""
    out = _draw_border(BASE, {"x": 150, "y": 80, "w": 200, "h": 200})
    assert png_size(out) == (200, 100)
    # 右下角落入边界内画框
    assert pixel(out, 199, 99) == RED


def test_draw_border_no_rect():
    """无 rect 时返回原图。"""
    assert _draw_border(BASE, {}) == BASE
    assert _draw_border(BASE, None) == BASE


# ───────────────────────── API 层（upload_shot）─────────────────────────

import pytest  # noqa: E402

from server.app import create_app  # noqa: E402
from server.models import db  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db.close()
    db.init(str(tmp_path / "test.db"))
    shots_dir = tmp_path / "shots"
    monkeypatch.setattr("server.shots.SHOTS_DIR", str(shots_dir))
    app = create_app()
    app.config["TESTING"] = True
    app.secret_key = "test-secret"
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["uid"] = 1
        yield c, str(shots_dir)
    db.close()


def _upload(c, **fields):
    from io import BytesIO

    # 文件用 (stream, filename) 元组；文本字段直接放字符串（元组会被
    # werkzeug 当文件流解析，request.form 取不到）
    data = {"screenshot": (BytesIO(BASE), "s.png"), **fields}
    return c.post("/api/projects/proj-x/shots", data=data, content_type="multipart/form-data")


def test_upload_without_rect_ok(client):
    """T4.2 页面评论：无红框（不传 highlight_rect）合法——无框截图。"""
    c, shots_dir = client
    resp = _upload(c, request_id="r1")
    assert resp.status_code == 200, resp.get_json()
    assert (Path(shots_dir) / "proj-x" / "r1.png").is_file()


def test_upload_with_rect_ok(client):
    c, shots_dir = client
    resp = _upload(c, request_id="r2", highlight_rect='{"x": 10, "y": 5, "w": 30, "h": 20}')
    assert resp.status_code == 200, resp.get_json()
    # 红框画上了（左上角边线红色）
    assert pixel((Path(shots_dir) / "proj-x" / "r2.png").read_bytes(), 10, 5) == RED


def test_upload_bad_rect_rejected(client):
    c, _ = client
    assert _upload(c, request_id="r3", highlight_rect='{"x": -1}').status_code == 400
    assert _upload(c, request_id="r4", highlight_rect="not-json").status_code == 400
    assert _upload(c, request_id="r5", highlight_rect="null").status_code == 400
