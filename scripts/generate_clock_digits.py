#!/usr/bin/env python3
"""把时钟数字烘焙成两套渐变 RGB565 字形。

输入：Windows 自带的 Arial Rounded MT Bold（ARLRDBD.TTF）
输出：main/generated/clock_digits.c / .h —— 0-9 各两套（橙=时、绿=分）
     build-out/clock_preview.png —— 466×466 效果预览，方便人工核对

LVGL 只能给文字上单色，渐变字只能走位图。底色是纯黑，所以直接
把渐变合成在黑底上导出 RGB565，不需要 alpha 通道。
"""

from __future__ import annotations

import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_C = ROOT / "main" / "generated" / "clock_digits.c"
OUT_H = ROOT / "main" / "generated" / "clock_digits.h"
PREVIEW = ROOT.parent / "build-out" / "clock_preview.png"

SCREEN = 466
CAP_HEIGHT = 112          # 字面高度，和 UI 里两行数字块的高度对齐
CELL_PAD = 2              # 每个字形四周留的透明度安全边
SUPERSAMPLE = 4           # 字形先放大渲染再缩，抗锯齿更干净

FONT_CANDIDATES = (
    pathlib.Path("C:/Windows/Fonts/ARLRDBD.TTF"),   # Arial Rounded MT Bold
    pathlib.Path("C:/Windows/Fonts/arialbd.ttf"),
)

# 每套渐变三个停靠点：(位置, RGB)
GRADIENTS = (
    ("hours", ((0.0, (0xFF, 0xC2, 0x4E)), (0.55, (0xF5, 0xA0, 0x1F)), (1.0, (0xD9, 0x7C, 0x0B)))),
    ("minutes", ((0.0, (0x4C, 0xE8, 0x6A)), (0.55, (0x22, 0xC9, 0x4E)), (1.0, (0x0B, 0x9C, 0x3E)))),
)


def pick_font() -> pathlib.Path:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise SystemExit("找不到可用的圆体字体（ARLRDBD.TTF / arialbd.ttf）")


def size_for_cap_height(font_path: pathlib.Path, cap: int) -> int:
    """二分找到让数字字面高度正好等于 cap 的字号。"""
    low, high = 40, 400
    best = low
    while low <= high:
        mid = (low + high) // 2
        font = ImageFont.truetype(str(font_path), mid * SUPERSAMPLE)
        boxes = [font.getbbox(str(d)) for d in range(10)]
        height = max(box[3] - box[1] for box in boxes)
        if height <= cap * SUPERSAMPLE:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def gradient_color(stops, t: float) -> tuple[int, int, int]:
    if t <= stops[0][0]:
        return stops[0][1]
    for index in range(1, len(stops)):
        previous = stops[index - 1]
        current = stops[index]
        if t <= current[0]:
            span = current[0] - previous[0]
            ratio = 0.0 if span <= 0 else (t - previous[0]) / span
            return tuple(
                int(previous[1][channel] + (current[1][channel] - previous[1][channel]) * ratio)
                for channel in range(3)
            )
    return stops[-1][1]


def render_digit(font: ImageFont.FreeTypeFont, digit: int, stops, cell: tuple[int, int]) -> Image.Image:
    """渲染单个数字：字形遮罩 × 渐变，合成到黑底。"""
    width, height = cell
    canvas_size = (width * SUPERSAMPLE, height * SUPERSAMPLE)
    mask = Image.new("L", canvas_size, 0)
    draw = ImageDraw.Draw(mask)
    text = str(digit)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    glyph_w = right - left
    glyph_h = bottom - top
    draw.text(
        ((canvas_size[0] - glyph_w) / 2 - left, (canvas_size[1] - glyph_h) / 2 - top),
        text,
        font=font,
        fill=255,
    )
    mask = mask.resize((width, height), Image.LANCZOS)

    out = Image.new("RGB", (width, height), (0, 0, 0))
    pixels = out.load()
    mask_pixels = mask.load()
    for y in range(height):
        ny = y / max(1, height - 1)
        for x in range(width):
            alpha = mask_pixels[x, y]
            if alpha == 0:
                continue
            nx = x / max(1, width - 1)
            # 左上略亮、右下更深的斜向渐变，和参考图一致
            color = gradient_color(stops, min(1.0, 0.30 * nx + 0.70 * ny))
            scale = alpha / 255.0
            pixels[x, y] = (
                int(color[0] * scale),
                int(color[1] * scale),
                int(color[2] * scale),
            )
    return out


def to_rgb565(image: Image.Image) -> bytes:
    out = bytearray()
    for r, g, b in image.convert("RGB").getdata():
        value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        out += bytes((value & 0xFF, value >> 8))
    return bytes(out)


def build_assets():
    font_path = pick_font()
    size = size_for_cap_height(font_path, CAP_HEIGHT)
    font = ImageFont.truetype(str(font_path), size * SUPERSAMPLE)

    boxes = [font.getbbox(str(d)) for d in range(10)]
    ink_w = max(box[2] - box[0] for box in boxes) // SUPERSAMPLE + 1
    ink_h = max(box[3] - box[1] for box in boxes) // SUPERSAMPLE + 1
    cell = (ink_w + CELL_PAD * 2, ink_h + CELL_PAD * 2)

    assets = []
    for name, stops in GRADIENTS:
        for digit in range(10):
            assets.append((f"{name}_{digit}", render_digit(font, digit, stops, cell)))
    return font_path, size, cell, assets


def emit_c(cell: tuple[int, int], assets) -> None:
    lines = [
        "/* 由 scripts/generate_clock_digits.py 生成，请勿手改。 */",
        '#include "clock_digits.h"',
        "",
    ]
    for name, image in assets:
        data = to_rgb565(image)
        lines.append(f"static const uint8_t clock_digit_{name}_map[] = {{")
        for offset in range(0, len(data), 20):
            chunk = data[offset:offset + 20]
            lines.append("    " + ",".join(f"0x{byte:02x}" for byte in chunk) + ",")
        lines.append("};")
        lines.append("")
        lines.append(f"static const lv_image_dsc_t clock_digit_{name}_dsc = {{")
        lines.append("    .header.magic = LV_IMAGE_HEADER_MAGIC,")
        lines.append("    .header.cf = LV_COLOR_FORMAT_RGB565,")
        lines.append(f"    .header.w = {image.width},")
        lines.append(f"    .header.h = {image.height},")
        lines.append(f"    .data_size = sizeof(clock_digit_{name}_map),")
        lines.append(f"    .data = clock_digit_{name}_map,")
        lines.append("};")
        lines.append("")
    for group in ("hours", "minutes"):
        lines.append(f"const lv_image_dsc_t *const clock_digit_{group}[10] = {{")
        for digit in range(10):
            lines.append(f"    &clock_digit_{group}_{digit}_dsc,")
        lines.append("};")
        lines.append("")
    OUT_C.write_text("\n".join(lines), encoding="utf-8")

    header = [
        "/* 由 scripts/generate_clock_digits.py 生成，请勿手改。 */",
        "#pragma once",
        "",
        '#include "lvgl.h"',
        "",
        f"#define CLOCK_DIGIT_W {cell[0]}",
        f"#define CLOCK_DIGIT_H {cell[1]}",
        "",
        "/* hours = 橙色渐变（时），minutes = 绿色渐变（分） */",
        "extern const lv_image_dsc_t *const clock_digit_hours[10];",
        "extern const lv_image_dsc_t *const clock_digit_minutes[10];",
        "",
    ]
    OUT_H.write_text("\n".join(header), encoding="utf-8")


def emit_preview(cell: tuple[int, int], assets) -> None:
    """按设备实际坐标画一张 466 预览：环 16px / r214 / 12 点起，数字两行。"""
    scale = SUPERSAMPLE
    canvas = Image.new("RGB", (SCREEN * scale, SCREEN * scale), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    center = SCREEN * scale // 2
    radius = 214 * scale
    width = 16 * scale

    draw.arc(
        [center - radius - width // 2, center - radius - width // 2,
         center + radius + width // 2, center + radius + width // 2],
        start=0, end=360, fill=(0x0E, 0x2A, 0x29), width=width,
    )
    # 24 秒 = 40%
    draw.arc(
        [center - radius - width // 2, center - radius - width // 2,
         center + radius + width // 2, center + radius + width // 2],
        start=-90, end=-90 + 144, fill=(0x20, 0xD6, 0xE6), width=width,
    )

    image = canvas.resize((SCREEN, SCREEN), Image.LANCZOS)
    lookup = {name: img for name, img in assets}

    def paste_row(group: str, text: str, row_center: int) -> None:
        gap = 6
        cell_w = cell[0]
        total = cell_w * len(text) + gap * (len(text) - 1)
        x = (SCREEN - total) // 2
        for char in text:
            glyph = lookup[f"{group}_{char}"]
            image.paste(glyph, (x, row_center - cell[1] // 2))
            x += cell_w + gap

    paste_row("hours", "10", 159)
    paste_row("minutes", "13", 307)
    image.save(PREVIEW)


def main() -> int:
    font_path, size, cell, assets = build_assets()
    emit_c(cell, assets)
    emit_preview(cell, assets)
    total = sum(len(to_rgb565(image)) for _, image in assets)
    print(f"字体: {font_path.name} 字号(x{SUPERSAMPLE}): {size}")
    print(f"字形: {cell[0]}x{cell[1]} px, 20 张, 合计 {total / 1024:.0f} KB")
    print(f"输出: {OUT_C.name} / {OUT_H.name} / {PREVIEW}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
