#!/usr/bin/env python3
"""把四张表情原图烘焙成固件可用的 RGB565 位图表情。

输入：assets/dotii_faces/face1..4.jpg（1254×1254，黑底或白底）
输出：
  main/generated/dotii_faces.c / .h   —— 固件用的 LVGL 图像描述符
  bridge/web/assets/expressions/dotii-face-*.webp —— 管理台预览

做法：按内容包围盒裁切，缩放到设备 466 屏对应的位置，保持原构图。
白底图（face3）按亮度抠底，避免在黑色屏幕上出现白块。
"""

from __future__ import annotations

import pathlib
import sys

from PIL import Image, ImageFilter

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "assets" / "dotii_faces"
OUT_C = ROOT / "main" / "generated" / "dotii_faces.c"
OUT_H = ROOT / "main" / "generated" / "dotii_faces.h"
WEB_DIR = ROOT / "bridge" / "web" / "assets" / "expressions"

SCREEN = 466
SOURCE_BOX = 1254
MARGIN = 16  # 源图坐标上的边距，保住光晕

FACES = (
    ("smile", "face1.jpg", False),
    ("wink", "face2.jpg", False),
    ("cozy", "face3.jpg", True),
    ("sparkle", "face4.jpg", False),
)


def content_bbox(image: Image.Image, background: tuple[int, int, int]) -> tuple[int, int, int, int]:
    width, height = image.size
    pixels = image.load()
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            r, g, b = pixels[x, y][:3]
            if abs(r - background[0]) + abs(g - background[1]) + abs(b - background[2]) > 60:
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if max_x < 0:
        return 0, 0, width, height
    return (max(0, min_x - MARGIN), max(0, min_y - MARGIN),
            min(width, max_x + MARGIN), min(height, max_y + MARGIN))


def to_rgb565(image: Image.Image) -> bytes:
    out = bytearray()
    for r, g, b in image.convert("RGB").getdata():
        value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        out += bytes((value & 0xFF, value >> 8))
    return bytes(out)


def render_face(source: pathlib.Path, key_white: bool) -> tuple[Image.Image, int, int]:
    image = Image.open(source).convert("RGB")
    background = (255, 255, 255) if key_white else (0, 0, 0)
    left, top, right, bottom = content_bbox(image, background)
    scale = SCREEN / SOURCE_BOX
    crop = image.crop((left, top, right, bottom))
    width = max(1, round(crop.width * scale))
    height = max(1, round(crop.height * scale))
    crop = crop.resize((width, height), Image.LANCZOS)
    if key_white:
        # 白底抠掉：亮度越高越透明，再合成到黑底，避免屏幕上出现白块。
        gray = crop.convert("L")
        alpha = gray.point(lambda v: max(0, min(255, (255 - v) * 3)))
        black = Image.new("RGB", crop.size, (0, 0, 0))
        crop = Image.composite(crop, black, alpha)
        crop = crop.filter(ImageFilter.GaussianBlur(0.4))
    center_x = round(((left + right) / 2) * scale) - SCREEN // 2
    center_y = round(((top + bottom) / 2) * scale) - SCREEN // 2
    return crop, center_x, center_y


def emit_c(assets: list[tuple[str, Image.Image, int, int]]) -> None:
    lines = [
        "/* 由 scripts/generate_dotii_face_assets.py 生成，请勿手改。 */",
        '#include "dotii_faces.h"',
        "",
    ]
    for name, image, _, _ in assets:
        data = to_rgb565(image)
        lines.append(f"static const uint8_t dotii_face_{name}_map[] = {{")
        for offset in range(0, len(data), 20):
            chunk = data[offset:offset + 20]
            lines.append("    " + ",".join(f"0x{byte:02x}" for byte in chunk) + ",")
        lines.append("};")
        lines.append("")
        lines.append(f"static const lv_image_dsc_t dotii_face_{name}_dsc = {{")
        lines.append("    .header.magic = LV_IMAGE_HEADER_MAGIC,")
        lines.append("    .header.cf = LV_COLOR_FORMAT_RGB565,")
        lines.append(f"    .header.w = {image.width},")
        lines.append(f"    .header.h = {image.height},")
        lines.append(f"    .data_size = sizeof(dotii_face_{name}_map),")
        lines.append(f"    .data = dotii_face_{name}_map,")
        lines.append("};")
        lines.append("")
    lines.append("const dotii_face_asset_t dotii_face_assets[DOTII_FACE_COUNT] = {")
    for name, _, x, y in assets:
        lines.append(f"    {{&dotii_face_{name}_dsc, {x}, {y}}},")
    lines.append("};")
    lines.append("")
    OUT_C.write_text("\n".join(lines), encoding="utf-8", newline="\n")

    OUT_H.write_text("\n".join([
        "/* 由 scripts/generate_dotii_face_assets.py 生成，请勿手改。 */",
        "#pragma once",
        "",
        '#include "lvgl.h"',
        "",
        "#define DOTII_FACE_COUNT 4",
        "",
        "typedef struct {",
        "    const lv_image_dsc_t *image;",
        "    int x;",
        "    int y;",
        "} dotii_face_asset_t;",
        "",
        "extern const dotii_face_asset_t dotii_face_assets[DOTII_FACE_COUNT];",
        "",
        "enum {",
        "    DOTII_FACE_SMILE = 0,",
        "    DOTII_FACE_WINK,",
        "    DOTII_FACE_COZY,",
        "    DOTII_FACE_SPARKLE,",
        "};",
        "",
    ]), encoding="utf-8", newline="\n")


def emit_previews(assets: list[tuple[str, Image.Image, int, int]]) -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    for name, image, x, y in assets:
        canvas = Image.new("RGB", (SCREEN, SCREEN), (0, 0, 0))
        canvas.paste(image, (SCREEN // 2 + x - image.width // 2,
                             SCREEN // 2 + y - image.height // 2))
        canvas.save(WEB_DIR / f"dotii-face-{name}.webp", "WEBP", quality=92, method=6)


def main() -> int:
    if not SOURCE_DIR.is_dir():
        print(f"缺少源图目录：{SOURCE_DIR}", file=sys.stderr)
        return 1
    assets = []
    for name, filename, key_white in FACES:
        source = SOURCE_DIR / filename
        if not source.is_file():
            print(f"缺少源图：{source}", file=sys.stderr)
            return 1
        image, x, y = render_face(source, key_white)
        assets.append((name, image, x, y))
        print(f"{name:8s} {image.width}x{image.height} at ({x},{y}) "
              f"{image.width * image.height * 2 // 1024} KB")
    emit_c(assets)
    emit_previews(assets)
    total = sum(image.width * image.height * 2 for _, image, _, _ in assets)
    print(f"合计 {total / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
