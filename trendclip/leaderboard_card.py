"""The fan leaderboard as a 1080x1350 image for a YouTube / Instagram post: a podium for the top 3 and
the next 10 in a list, in the channel's style (dark navy, neon cyan light, yellow). A channel logo is
used when channels/<channel>/logo.png exists."""

from __future__ import annotations

import io
import random
import re
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from .video_assembler import _FONT_DIRS, find_font_file

W, H = 1080, 1350
NAVY, DEEP = (8, 14, 40), (3, 6, 20)
CYAN, BLUE, YELLOW, WHITE = (25, 205, 255), (40, 110, 255), (255, 214, 10), (255, 255, 255)
GOLD, SILVER, BRONZE = (255, 214, 10), (205, 220, 235), (235, 150, 80)
LIST_SIZE = 10
TITLE = "KINGS OF LOGIC"
_UNICODE_FONTS = ["Arial Unicode.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf",
                  "NotoSansHebrew-Bold.ttf"]
_UNICODE_DIRS = [*_FONT_DIRS, Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/truetype/noto"),
                 Path("/usr/share/fonts/noto"), Path("/usr/share/fonts/TTF")]
_RTL = re.compile(r"[\u0590-\u08ff][\u0590-\u08ff\s\-_.0-9]*[\u0590-\u08ff]|[\u0590-\u08ff]")


def _font(size: int) -> ImageFont.FreeTypeFont:
    _, path = find_font_file()
    return ImageFont.truetype(str(path), size) if path else ImageFont.load_default(size)


def _unicode_font(size: int) -> ImageFont.FreeTypeFont | None:
    for directory in _UNICODE_DIRS:
        for name in _UNICODE_FONTS:
            if (directory / name).is_file():
                return ImageFont.truetype(str(directory / name), size)
    return None


def _display(name: str, size: int) -> tuple[str, ImageFont.FreeTypeFont]:
    """Names in Hebrew / Arabic / other scripts: a font that has them, right-to-left runs drawn in order."""
    if all(ord(ch) < 0x250 for ch in name):
        return name, _font(size)
    if not features.check("raqm"):
        name = _RTL.sub(lambda m: "".join(reversed(re.findall(r"[0-9]+|.", m.group(0)))), name)
    return name, _unicode_font(size) or _font(size)


def _shrink(draw: ImageDraw.ImageDraw, name: str, size: int, width: int, smallest: int):
    """The biggest font (down to `smallest`) the name fits in, then cut with … if it still doesn't."""
    for s in range(size, smallest - 1, -2):
        text, font = _display(name, s)
        if draw.textlength(text, font=font) <= width:
            return text, font
    return _fit(draw, text, font, width), font


def _pts(points: int) -> str:
    return f"{points} PT" if points == 1 else f"{points} PTS"


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def _glow_text(img: Image.Image, xy: tuple[float, float], text: str, font, fill, glow, radius: int = 14,
               anchor: str = "mm") -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, text, font=font, fill=glow + (255,), anchor=anchor)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius)))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius // 3)))
    ImageDraw.Draw(img).text(xy, text, font=font, fill=fill, anchor=anchor)


def _background(rng: random.Random) -> Image.Image:
    img = Image.new("RGBA", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        px.line([(0, y), (W, y)], fill=tuple(int(DEEP[i] + (NAVY[i] - DEEP[i]) * (1 - abs(2 * t - 0.8))) for i in range(3)))

    light = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(light)
    for _ in range(9):  # neon speed streaks, like the banner
        y0 = rng.randint(-200, H // 2)
        x0 = rng.randint(-300, W)
        length = rng.randint(400, 900)
        color = rng.choice([CYAN, BLUE, (120, 80, 255)])
        d.line([(x0, y0), (x0 + length, y0 + length * 0.35)], fill=color + (rng.randint(70, 150),),
               width=rng.randint(2, 6))
    img.alpha_composite(light.filter(ImageFilter.GaussianBlur(3)))
    img.alpha_composite(light.filter(ImageFilter.GaussianBlur(18)))

    blocks = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(blocks)
    size = 54
    for col in range(-1, W // size + 2):  # voxel hills at the bottom
        top = H - rng.randint(1, 4) * size - (40 if col % 5 in (1, 2) else 0)
        for y in range(top, H, size):
            shade = rng.randint(30, 80)
            green = y == top and col % 3 != 0  # grass tops, like the banner's blocks
            d.rectangle([col * size, y, col * size + size - 3, y + size - 3],
                        fill=(40, 120 + shade, 70, 120) if green else (30, 50 + shade, 110 + shade, 105),
                        outline=CYAN + (70,))
    img.alpha_composite(blocks)
    return img


def _spotlights(img: Image.Image, centers: list[int], floor: int) -> None:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in centers:
        d.polygon([(x - 40, 250), (x + 40, 250), (x + 190, floor), (x - 190, floor)], fill=(170, 230, 255, 46))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(28)))


def _crown(d: ImageDraw.ImageDraw, cx: float, y: float, w: float = 76) -> None:
    h = w * 0.6
    pts = [(cx - w / 2, y + h), (cx - w / 2, y + h * 0.25), (cx - w / 4, y + h * 0.6), (cx, y),
           (cx + w / 4, y + h * 0.6), (cx + w / 2, y + h * 0.25), (cx + w / 2, y + h)]
    d.polygon(pts, fill=GOLD, outline=(255, 245, 180))
    for px, py in pts[1:6:2] + [pts[3]]:
        d.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(255, 245, 180))


def _pill(d: ImageDraw.ImageDraw, cx: float, cy: float, text: str, font, fill, color=DEEP) -> None:
    w = d.textlength(text, font=font) + 36
    d.rounded_rectangle([cx - w / 2, cy - 24, cx + w / 2, cy + 24], radius=24, fill=fill)
    d.text((cx, cy + 1), text, font=font, fill=color, anchor="mm")


def _logo(img: Image.Image, logo: Path | None) -> None:
    if not logo or not logo.is_file():
        return
    size = 120
    pic = Image.open(logo).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    ring = Image.new("RGBA", (size + 12, size + 12), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([0, 0, size + 11, size + 11], fill=CYAN + (255,))
    img.alpha_composite(ring, (40 - 6, 40 - 6))
    img.paste(pic, (40, 40), mask)


def render(people: list[dict[str, Any]], handle: str = "@SideQuestLogic", *, day: date | None = None,
           logo: Path | None = None, seed: int = 7) -> bytes:
    """PNG bytes. `people`: best first, each with "name" and "points" (as leaderboard.score returns)."""
    rng = random.Random(seed)
    img = _background(rng)
    d = ImageDraw.Draw(img)
    _logo(img, logo)

    d.text((W / 2, 62), "S I D E Q U E S T   L O G I C", font=_font(30), fill=CYAN, anchor="mm")
    size = 150
    while size > 60 and d.textlength(TITLE, font=_font(size)) > W - 100:
        size -= 4
    _glow_text(img, (W / 2, 160), TITLE, _font(size), YELLOW, (255, 170, 0), radius=18)
    day = day or date.today()
    sub = f"{day:%B} LEADERBOARD · {day:%d %b %Y}".upper()  # it starts again on the 1st
    d.text((W / 2, 262), sub, font=_font(30), fill=WHITE, anchor="mm")

    floor = 720
    podium = [(1, W / 2, 205, GOLD), (2, W / 2 - 290, 150, SILVER), (3, W / 2 + 290, 115, BRONZE)]
    _spotlights(img, [int(x) for _, x, _, _ in podium], floor)
    d = ImageDraw.Draw(img)
    name_size = 40
    for rank, cx, height, color in podium:
        top = floor - height
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle([cx - 135, top, cx + 135, floor], radius=14, outline=color + (255,),
                                               width=10)
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(14)))
        d = ImageDraw.Draw(img)
        for y in range(int(top), floor):  # block: lit from the top
            t = (y - top) / height
            d.line([(cx - 132, y), (cx + 132, y)], fill=(int(30 + 40 * (1 - t)), int(50 + 70 * (1 - t)),
                                                          int(110 + 80 * (1 - t))))
        d.rounded_rectangle([cx - 135, top, cx + 135, floor], radius=14, outline=color, width=5)
        d.rectangle([cx - 132, top + 3, cx + 132, top + 12], fill=color)
        _glow_text(img, (cx, top + height / 2 + 8), str(rank), _font(min(130, height - 30)), WHITE, color, 10)
        d = ImageDraw.Draw(img)
        if rank > len(people):
            d.text((cx, top - 60), "YOU?", font=_font(name_size), fill=(140, 170, 210), anchor="mm")
            continue
        p = people[rank - 1]
        text, font = _shrink(d, p["name"], name_size, 280, 24)
        d.text((cx, top - 92), text, font=font, fill=WHITE, anchor="mm", stroke_width=3, stroke_fill=DEEP)
        _pill(d, cx, top - 40, _pts(p["points"]), _font(30), color)
        if rank == 1:
            _crown(d, cx, top - 175)

    row_h, row_gap, y = 46, 5, floor + 22
    rest = people[3:3 + LIST_SIZE]
    small = _font(30)
    for i in range(LIST_SIZE):
        rank = i + 4
        box = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(box).rounded_rectangle([90, y, W - 90, y + row_h], radius=row_h // 2,
                                              fill=(12, 24, 64, 215), outline=CYAN + (110,), width=2)
        img.alpha_composite(box)
        d = ImageDraw.Draw(img)
        d.ellipse([96, y + 5, 96 + row_h - 10, y + row_h - 5], fill=CYAN)
        d.text((96 + (row_h - 10) / 2, y + row_h / 2 + 1), str(rank), font=_font(26), fill=DEEP, anchor="mm")
        if i < len(rest):
            p = rest[i]
            text, font = _display(p["name"], 30)
            d.text((165, y + row_h / 2 + 1), _fit(d, text, font, 640), font=font, fill=WHITE, anchor="lm")
            d.text((W - 120, y + row_h / 2 + 1), _pts(p["points"]), font=small, fill=YELLOW, anchor="rm")
        else:
            d.text((165, y + row_h / 2 + 1), "your name here", font=small, fill=(110, 140, 190), anchor="lm")
        y += row_h + row_gap

    d.text((W / 2, H - 70), "FIRST RIGHT ANSWER 10 · CORRECT 2 · COMMENT 1 · SUBSCRIBE +10",
           font=_font(27), fill=CYAN, anchor="mm", stroke_width=3, stroke_fill=DEEP)
    _glow_text(img, (W / 2, H - 30), f"COMMENT TO CLIMB · {handle.upper()}", _font(30), YELLOW, (255, 150, 0), 8)

    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()
