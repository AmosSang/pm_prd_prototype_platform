"""截图红框单测：纯 PIL 生成基准图 → 画框 → 像素级断言。"""
import io
import os
import sys

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
