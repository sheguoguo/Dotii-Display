#!/usr/bin/env python3
"""生成奶油主题时钟表盘背景位图（466×466 RGB565）。

出处：资料库 rabbit-clock.html 的「奶油」主题，坐标按 SVG viewBox 0 0 466 466 1:1 还原。
输出：main/generated/clock_face.c / .h

分工：本脚本只烘焙「静态底图」——奶油径向渐变、左上高光、边缘暗角、12 个刻度点、
r=216 的轨道环、兔兔本体。秒弧由 lv_arc 运行时绘制，时间和日期由 LVGL 文字绘制。

字体：scripts/fonts/Baloo2-ExtraBold.ttf（OFL），数字等宽化后用
    npx lv_font_conv --size 93 --bpp 4 --font Baloo2-ExtraBold-tnum.ttf \\
        --range 0x30-0x3A --format lvgl --no-compress --lv-include lvgl.h \\
        -o main/generated/ui_font_clock_93.c
"""

from __future__ import annotations

import pathlib

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_C = ROOT / "main" / "generated" / "clock_face.c"
OUT_H = ROOT / "main" / "generated" / "clock_face.h"

S = 466
SS = 3  # 超采样倍数

# 奶油主题色板（取自参考页 .stage 的 CSS 变量）
BG1 = (0xFF, 0xFA, 0xF3)
BG2 = (0xF7, 0xE9, 0xD8)
FACE = (0xFF, 0xFD, 0xF8)
INK = (0x3B, 0x32, 0x30)
TICK = (0xCB, 0xAB, 0x93)
BLUSH = (0xFF, 0xB0, 0xA0)
TRACK = (0xF3, 0xE3, 0xD5)
VIG = (0x5A, 0x3A, 0x26)
EAR = (0xF4, 0xA9, 0xA4)


def px(v: float) -> float:
    return v * SS


def radial_background(size: int) -> Image.Image:
    """bgGrad：cx 50% cy 32% r 80%（SVG 百分比半径按归一化对角线算 → 466）。"""
    k = size / S
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    cx, cy = S * 0.5 * k, S * 0.32 * k
    radius = 0.8 * S * k
    t = np.clip(np.hypot(xx - cx, yy - cy) / radius, 0.0, 1.0)[..., None]
    c1 = np.array(BG1, dtype=np.float32)
    c2 = np.array(BG2, dtype=np.float32)
    rgb = np.clip(c1 + (c2 - c1) * t, 0, 255).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def apply_gloss(image: Image.Image) -> Image.Image:
    """白色径向高光椭圆，中心 alpha .40 → 边缘 0。"""
    n = image.size[0]
    k = n / S
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) + 0.5
    inside = ((xx - 188 * k) / (150 * k)) ** 2 + ((yy - 118 * k) / (112 * k)) ** 2 <= 1.0
    dist = np.hypot(xx - 188 * k, yy - 118 * k)
    radius = 0.5 * np.hypot(150 * 2, 112 * 2) / np.sqrt(2) * k
    alpha = np.clip(1.0 - dist / radius, 0.0, 1.0) * 0.40 * inside
    arr = np.asarray(image, dtype=np.float32)
    arr = arr * (1 - alpha[..., None]) + 255.0 * alpha[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def apply_vignette(image: Image.Image) -> Image.Image:
    """边缘暗角：#5a3a26，72% 处 0 → 95% 处 .085 → 100% 处 .16。"""
    n = image.size[0]
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) + 0.5
    dist = np.hypot(xx - S / 2 * (n / S), yy - S / 2 * (n / S)) / (0.5 * S * (n / S))
    alpha = np.zeros_like(dist)
    mid = (dist > 0.72) & (dist <= 0.95)
    alpha[mid] = 0.085 * (dist[mid] - 0.72) / (0.95 - 0.72)
    outer = dist > 0.95
    alpha[outer] = 0.085 + (0.16 - 0.085) * np.clip((dist[outer] - 0.95) / 0.05, 0, 1)
    arr = np.asarray(image, dtype=np.float32)
    vig = np.array(VIG, dtype=np.float32)
    arr = arr * (1 - alpha[..., None]) + vig * alpha[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def draw_ticks_and_track(image: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for i in range(12):
        ang = (i / 12) * 2 * np.pi - np.pi / 2
        cx = S / 2 + 199 * np.cos(ang)
        cy = S / 2 + 199 * np.sin(ang)
        r = 3.6 if i % 3 == 0 else 2.4
        a = int(255 * (0.95 if i % 3 == 0 else 0.55))
        draw.ellipse([px(cx - r), px(cy - r), px(cx + r), px(cy + r)], fill=TICK + (a,))
    # PIL 描边向内收，+半个线宽还原 SVG 的居中描边（环心 r=216、线宽 6.2）
    r = px(216 + 3.1)
    draw.ellipse([px(S / 2) - r, px(S / 2) - r, px(S / 2) + r, px(S / 2) + r],
                 outline=TRACK + (255,), width=int(round(px(6.2))))
    return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")


def _ear(cx: float, cy: float, angle: float, rx: float, ry: float,
         fill, outline, width: float):
    """旋转椭圆：画在独立图层上再旋转（PIL 不支持旋转图元）。"""
    pad = int(px(max(rx, ry) + width + 6))
    layer = Image.new("RGBA", (pad * 2, pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    grow = width / 2 if outline[3] else 0
    d.ellipse([pad - px(rx + grow), pad - px(ry + grow),
               pad + px(rx + grow), pad + px(ry + grow)],
              fill=fill, outline=outline, width=int(round(px(width))))
    layer = layer.rotate(-angle, resample=Image.BICUBIC, center=(pad, pad))
    return layer, (int(px(cx)) - pad, int(px(cy)) - pad)


def draw_mascot(image: Image.Image) -> Image.Image:
    canvas = image.convert("RGBA")
    for cx, ang in ((208, -7), (258, 7)):
        layer, pos = _ear(cx, 95, ang, 15.5, 38, FACE + (255,), INK + (255,), 5.2)
        canvas.alpha_composite(layer, pos)
        inner, ipos = _ear(cx, 98, ang, 7.6, 27.5, EAR + (255,), (0, 0, 0, 0), 0)
        inner.putalpha(inner.split()[3].point(lambda v: int(v * 0.55)))
        canvas.alpha_composite(inner, ipos)

    draw = ImageDraw.Draw(canvas)
    # 头：SVG 描边居中 → PIL 向内，半径各 +2.6
    draw.ellipse([px(233 - 68.6), px(172 - 65.6), px(233 + 68.6), px(172 + 65.6)],
                 fill=FACE + (255,), outline=INK + (255,), width=int(round(px(5.2))))

    blush = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(blush)
    for cx in (190, 276):
        bd.ellipse([px(cx - 12.6), px(188 - 7.2), px(cx + 12.6), px(188 + 7.2)],
                   fill=BLUSH + (255,))
    blush = blush.filter(ImageFilter.GaussianBlur(px(5)))
    blush.putalpha(blush.split()[3].point(lambda v: int(v * 0.55)))
    canvas.alpha_composite(blush)

    draw = ImageDraw.Draw(canvas)
    for cx in (209, 257):
        draw.ellipse([px(cx - 12.2), px(167 - 17), px(cx + 12.2), px(167 + 17)],
                     fill=INK + (255,))
        hx, hy, hr = cx - 4.7, 159.6, 3.9
        draw.ellipse([px(hx - hr), px(hy - hr), px(hx + hr), px(hy + hr)],
                     fill=(255, 255, 255, 237))

    pts = []
    for i in range(41):
        t = i / 40
        x = (1 - t) ** 2 * 224.6 + 2 * (1 - t) * t * 233 + t ** 2 * 241.4
        y = (1 - t) ** 2 * 193.6 + 2 * (1 - t) * t * 202.8 + t ** 2 * 193.6
        pts.append((px(x), px(y)))
    draw.line(pts, fill=INK + (255,), width=int(round(px(4.1))), joint="curve")
    for p in (pts[0], pts[-1]):
        r = px(4.1) / 2
        draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=INK + (255,))
    return canvas.convert("RGB")


def render_face() -> Image.Image:
    image = radial_background(S * SS)
    image = apply_gloss(image)
    image = draw_ticks_and_track(image)
    image = draw_mascot(image)
    image = apply_vignette(image)
    return image.resize((S, S), Image.LANCZOS)


def to_rgb565(image: Image.Image) -> bytes:
    out = bytearray()
    for r, g, b in image.convert("RGB").getdata():
        value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        out += bytes((value & 0xFF, value >> 8))
    return bytes(out)


def emit_c(image: Image.Image) -> None:
    data = to_rgb565(image)
    lines = [
        "/* 由 scripts/generate_clock_face.py 生成，请勿手改。 */",
        '#include "clock_face.h"',
        "",
        "static LV_ATTRIBUTE_LARGE_CONST const uint8_t clock_face_map[] = {",
    ]
    for offset in range(0, len(data), 24):
        chunk = data[offset:offset + 24]
        lines.append("    " + ",".join(f"0x{byte:02x}" for byte in chunk) + ",")
    lines.append("};")
    lines.append("")
    lines.append("const lv_image_dsc_t clock_face_dsc = {")
    lines.append("    .header.magic = LV_IMAGE_HEADER_MAGIC,")
    lines.append("    .header.cf = LV_COLOR_FORMAT_RGB565,")
    lines.append(f"    .header.w = {image.width},")
    lines.append(f"    .header.h = {image.height},")
    lines.append("    .data_size = sizeof(clock_face_map),")
    lines.append("    .data = clock_face_map,")
    lines.append("};")
    OUT_C.write_text("\n".join(lines), encoding="utf-8")

    OUT_H.write_text("\n".join([
        "/* 由 scripts/generate_clock_face.py 生成，请勿手改。 */",
        "#pragma once",
        "",
        '#include "lvgl.h"',
        "",
        f"#define CLOCK_FACE_W {image.width}",
        f"#define CLOCK_FACE_H {image.height}",
        "",
        "/* 奶油主题静态底图：渐变 + 高光 + 暗角 + 刻度 + 轨道环 + 兔兔 */",
        "extern const lv_image_dsc_t clock_face_dsc;",
        "",
        "/* 时间/日期文字的基线落点（与参考页 SVG 的 y 值一致） */",
        "#define CLOCK_TIME_BASELINE 325",
        "#define CLOCK_DATE_BASELINE 379",
        "",
    ]), encoding="utf-8")


def main() -> int:
    face = render_face()
    emit_c(face)
    total = len(to_rgb565(face))
    print(f"表盘: {S}x{S} RGB565 {total / 1024:.0f} KB → {OUT_C.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
