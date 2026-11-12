#!/usr/bin/env python3
"""Generate management-center previews for the firmware's geometric Dotii face."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "bridge" / "web" / "assets" / "expressions"
MANIFEST_PATH = ASSET_DIR / "animations.json"
CANVAS_SIZE = 466
CENTER = CANVAS_SIZE // 2
FACE_SCALE_NUM = 6
FACE_SCALE_DEN = 5
DURATIONS = [100] * 8
WARM = (244, 234, 210)
AMBER = (242, 198, 109)
CYAN = (85, 216, 208)
CORAL = (255, 130, 122)


def scale_face_value(value: int) -> int:
    magnitude = (abs(value) * FACE_SCALE_NUM + FACE_SCALE_DEN // 2) // FACE_SCALE_DEN
    return magnitude if value >= 0 else -magnitude


def part_box(width: int, height: int, x: int, y: int) -> tuple[int, int, int, int]:
    left = CENTER + x - width // 2
    top = CENTER + y - height // 2
    return left, top, left + width, top + height


def draw_parts(parts: list[tuple], specials: list[tuple] | None = None) -> Image.Image:
    glow = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    core = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    core_draw = ImageDraw.Draw(core)
    for part in parts:
        width, height, x, y, color = part[:5]
        alpha = part[5] if len(part) > 5 else 255
        width = scale_face_value(width)
        height = scale_face_value(height)
        x = scale_face_value(x)
        y = scale_face_value(y)
        box = part_box(width, height, x, y)
        radius = min(width, height) // 2
        glow_draw.rounded_rectangle(box, radius=radius, fill=(*color, min(105, alpha)))
        core_draw.rounded_rectangle(box, radius=radius, fill=(*color, alpha))
    if specials:
        draw_specials(glow_draw, core_draw, specials)
    base = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 255))
    base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(7)))
    return Image.alpha_composite(base, core).convert("RGB")


def arc_box(x: int, y: int, diameter: int) -> tuple[float, float, float, float]:
    diameter = scale_face_value(diameter)
    x = scale_face_value(x)
    y = scale_face_value(y)
    cx = CENTER + x
    cy = CENTER + y
    half = diameter / 2
    return (cx - half, cy - half, cx + half, cy + half)


def draw_specials(
    glow_draw: ImageDraw.ImageDraw,
    core_draw: ImageDraw.ImageDraw,
    specials: list[tuple],
) -> None:
    for item in specials:
        kind = item[0]
        if kind == "arc":
            _, x, y, diameter, thickness, color, start, end = item
            width = max(1, scale_face_value(thickness))
            box = arc_box(x, y, diameter)
            glow_draw.arc(box, start, end, fill=(*color, 105), width=width + 4)
            core_draw.arc(box, start, end, fill=(*color, 255), width=width)
            # PIL 的 arc 是方头，固件用 rounded 端点；这里补两个圆帽对齐观感。
            radius = (box[2] - box[0]) / 2
            center_x = (box[0] + box[2]) / 2
            center_y = (box[1] + box[3]) / 2
            cap = width / 2
            for angle in (start, end):
                radians = math.radians(angle)
                px = center_x + radius * math.cos(radians)
                py = center_y + radius * math.sin(radians)
                glow_draw.ellipse((px - cap, py - cap, px + cap, py + cap), fill=(*color, 105))
                core_draw.ellipse((px - cap, py - cap, px + cap, py + cap), fill=(*color, 255))
        elif kind == "heart":
            _, x, y, size, color = item
            cx = CENTER + scale_face_value(x)
            cy = CENTER + scale_face_value(y)
            radius = scale_face_value(18)
            offset = scale_face_value(13)
            half = scale_face_value(25)
            for sign in (-1, 1):
                lx = cx + sign * offset - radius
                ly = cy - offset - radius
                glow_draw.ellipse((lx, ly, lx + radius * 2, ly + radius * 2), fill=(*color, 105))
                core_draw.ellipse((lx, ly, lx + radius * 2, ly + radius * 2), fill=(*color, 255))
            diamond = [(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)]
            glow_draw.polygon(diamond, fill=(*color, 105))
            core_draw.polygon(diamond, fill=(*color, 255))
        elif kind == "semi":
            _, x, y, width, height, color = item
            # y 是平顶位置，向下取半椭圆；box 上沿要上移一个 height，
            # 否则 pieslice 的下半部分会整体下移一档。
            cx = CENTER + scale_face_value(x)
            top = CENTER + scale_face_value(y)
            half_w = scale_face_value(width) / 2
            half_h = scale_face_value(height)
            box = (cx - half_w, top - half_h, cx + half_w, top + half_h)
            glow_draw.pieslice(box, 0, 180, fill=(*color, 105))
            core_draw.pieslice(box, 0, 180, fill=(*color, 255))
        elif kind == "ellipse":
            _, x, y, width, height, color = item
            cx = CENTER + scale_face_value(x)
            cy = CENTER + scale_face_value(y)
            half_w = scale_face_value(width) / 2
            half_h = scale_face_value(height) / 2
            box = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
            glow_draw.ellipse(box, fill=(*color, 105))
            core_draw.ellipse(box, fill=(*color, 255))


def frame_parts(expression: str, phase: int) -> list[tuple[int, int, int, int, tuple[int, int, int]]]:
    parts: list[tuple[int, int, int, int, tuple[int, int, int]]] = [
        (52, 72, -78, -30, WARM),
        (52, 72, 78, -30, WARM),
        (56, 12, 0, 70, WARM),
    ]
    wave = phase if phase < 4 else 8 - phase

    if expression == "idle_breath":
        breath = wave * 2
        return [(52, 68 + breath, -78, -30, WARM), (52, 68 + breath, 78, -30, WARM),
                (56, 12, 0, 70, WARM)]
    if expression == "blink":
        eye_height = 7 if phase in (2, 3) else 24 if phase in (1, 4) else 66
        return [(58, eye_height, -78, -26, WARM), (58, eye_height, 78, -26, WARM),
                (56, 12, 0, 70, WARM)]
    if expression == "curious":
        return [(62, 82, -76, -34, WARM), (46, 62, 82, -26, WARM),
                (20 + wave, 20 + wave, 8, 70, AMBER)]
    if expression == "sleepy_yawn":
        yawn = wave * 3
        return [(68, 10, -76, -18, WARM), (68, 10, 76, -18, WARM),
                (26 + yawn, 26 + yawn, 0, 72, AMBER)]
    if expression == "touch_response":
        pulse = wave * 4
        spread = wave * 3
        return [(62 + pulse // 2, 80 + pulse, -76, -34, WARM),
                (62 + pulse // 2, 80 + pulse, 76, -34, WARM),
                (30 + pulse, 16 + pulse // 2, 0, 76, CYAN),
                (13, 13, -120 - spread, 52, CYAN), (13, 13, 120 + spread, 52, CYAN)]
    if expression == "connecting":
        left = CYAN if phase < 4 else (45, 112, 108)
        right = (45, 112, 108) if phase < 4 else CYAN
        return [(48, 64, -76, -28, left), (48, 64, 76, -28, right),
                (56, 12, 0, 70, WARM), (10, 10, phase * 22 - 77, 88, CYAN)]
    if expression == "working":
        shift = wave * 4
        return [(48, 68, -86 + shift, -30, WARM), (48, 68, 70 + shift, -30, WARM),
                (62, 10, 0, 72, CYAN), (10, 10, 134, -82 + phase * 7, CYAN)]
    if expression == "complete":
        return [(68, 14, -76, -28, WARM), (68, 14, 76, -28, WARM),
                (108 + wave, 20 + wave // 2, 0, 62, AMBER),
                (10 + wave, 10 + wave, -132, -76, AMBER),
                (10 + wave, 10 + wave, 132, -76, CYAN)]
    if expression == "failure":
        return [(58, 12, -76, -22, CORAL), (58, 12, 76, -22, CORAL),
                (72, 12, 0, 80 + wave // 2, CORAL),
                (10 + wave, 10 + wave, 0, 52, CORAL)]
    if expression == "heart_eyes":
        mouth = 22 + wave
        return [(mouth, mouth, 0, 63, AMBER),
                (12, 12, -136, -102 + wave, CORAL),
                (12, 12, 136, -102 + wave, CYAN)]
    if expression == "cheeky":
        scan = wave * 4
        return [(58, 12, -65, -29, WARM),
                (40, 64, 57 + scan // 4, -17, WARM),
                (40, 13, 19, 53, AMBER),
                (18, 18, 39, 63, CORAL),
                (11, 11, 92, -85, CYAN)]
    if expression == "smile":
        return [(39, 24, -102, 29, CORAL, 120),
                (39, 24, 102, 29, CORAL, 120)]
    if expression == "laugh":
        return [(39, 24, -112, -10, CORAL, 110),
                (39, 24, 112, -10, CORAL, 110),
                (12, 12, 136, -100, CYAN)]
    raise ValueError(f"Unknown Dotii expression: {expression}")


def frame_specials(expression: str, phase: int) -> list[tuple]:
    """弧线与心形没有矩形图元可表达，单独走 PIL 的 arc / polygon。"""
    wave = phase if phase < 4 else 8 - phase
    if expression == "heart_eyes":
        return [("heart", -65, -29 + wave - 2, 64, CORAL),
                ("heart", 65, -29 + wave - 2, 64, CORAL)]
    if expression == "smile":
        return [("arc", -65, -29 + wave // 2, 68, 12, WARM, 225, 315),
                ("arc", 65, -29 + wave // 2, 68, 12, WARM, 225, 315),
                ("arc", 0, 52, 72, 12, AMBER, 30, 150)]
    if expression == "laugh":
        visible = 42 + wave * 3
        return [("arc", -65, -29, 68, 12, WARM, 225, 315),
                ("arc", 65, -29, 68, 12, WARM, 225, 315),
                ("semi", 0, 30, 104, visible, AMBER),
                ("ellipse", 0, 30 + visible * 68 // 100, 34, 22, CORAL)]
    return []


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for animation in manifest["animations"]:
        expression = animation["id"]
        frames = [draw_parts(frame_parts(expression, phase), frame_specials(expression, phase))
                  for phase in range(8)]
        # WebP may coalesce adjacent identical geometric phases (notably the
        # closed-eye hold). Preserve the eight-phase contract with a unique,
        # effectively black marker inside the round-screen safe area.
        for phase, frame in enumerate(frames):
            frame.putpixel((CENTER - 4 + phase, 402), (phase + 1, phase + 1, phase + 1))
        output_path = ROOT / "bridge" / "web" / animation["src"].lstrip("/")
        frames[0].save(
            output_path,
            "WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=DURATIONS,
            loop=0,
            lossless=True,
            method=6,
        )
        payload = output_path.read_bytes()
        with Image.open(output_path) as check:
            if check.size != (CANVAS_SIZE, CANVAS_SIZE) or check.n_frames != 8:
                raise ValueError(f"Invalid generated animation: {output_path.name}")
        animation["durations_ms"] = DURATIONS
        animation["bytes"] = len(payload)
        animation["sha256"] = hashlib.sha256(payload).hexdigest()
        print(f"{expression}: {len(payload)} bytes")

    manifest["style"] = "photo_geometric_v2"
    manifest["notes"] = (
        "照片版圆角条形脸；九种表情相对上一版统一放大 20%，"
        "网页预览与固件使用相同状态造型和八相位几何参数，网页额外模拟 AMOLED 光晕。"
    )
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
