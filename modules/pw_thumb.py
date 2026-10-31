"""Price Tag Why's thumbnails: a picture made to be clicked, not a frame of the video. It must say what the video is
about at a glance: the title's question in 2-4 heavy words at the top, the product drawn big and solid
(modules.pw_draw), the two numbers that don't add up (the first crossed out, the last in a starburst), and an
optional 1-3 word punch at the bottom, on a loud background.

Styles (THUMB_STYLE, or a script's thumbnail.style):
    shock   yellow burst behind the product, the price it should be crossed out, the real one in a red starburst
    versus  red half / green half: what it should cost against what it costs, the product between them
    tag     dark board, a giant white price tag with the real price, the product hanging beside it

What goes on it comes from the script's "thumbnail" ({title, hook, draw, label, old, now, style}), else its product
scene: the drawing, the first and the last line with a number in it, each with the line before it as its label.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from modules import bw_brand, bw_render, pw_draw

W, H = 1080, 1920
SS = 2  # drawn at twice the size, then scaled down (smooth edges)
INK = (18, 18, 22)
GREEN = (20, 150, 74)
RED = (222, 34, 38)
YELLOW = (255, 214, 40)
WHITE = (255, 255, 255)
TINT = {"green": (150, 222, 170), "red": (250, 170, 170), "ink": (200, 200, 208)}
STYLES = ("shock", "versus", "tag")
DIGIT = re.compile(r"^[$€£¥~≈]?\s?\d")  # a line that IS a figure ("160 marks", "$2.38"), not one with a year in it


def bold(px: int) -> ImageFont.FreeTypeFont:
    bw_render.ensure_fonts()
    return bw_render.font(px * SS)


def fit(text: str, px: int, width: float) -> int:
    while px > 30 and bold(px).getlength(text) > width * SS:
        px -= 6
    return px


def words(script: dict) -> dict[str, Any]:
    """What the thumbnail shows: {title, hook, draw, label, old, old_label, now, now_label, style}."""
    t = dict(script.get("thumbnail") or {})
    product = next(((s.get("product") or {}) for s in script.get("scenes") or [] if s.get("kind") == "product"), {})
    lines = [bw_brand.pen_line(x)["text"].strip() for x in product.get("lines") or []]
    numbered = [k for k, ln in enumerate(lines) if DIGIT.search(ln)]
    label = lambda k: lines[k - 1] if k > 0 and not DIGIT.search(lines[k - 1]) else ""
    first, last = (numbered[0], numbered[-1]) if len(numbered) > 1 else (None, numbered[0] if numbered else None)
    title = str(t.get("title") or script.get("question") or script.get("title") or "")
    return {"title": title.upper(), "hook": str(t.get("hook") or "").upper(),
            "draw": t.get("draw") or product.get("draw") or "", "label": t.get("label") or product.get("label") or [],
            "old": t.get("old", lines[first] if first is not None else ""), "old_label": t.get("old_label", label(first) if first is not None else ""),
            "now": t.get("now", lines[last] if last is not None else ""), "now_label": t.get("now_label", label(last) if last is not None else ""),
            "style": t.get("style") if t.get("style") in STYLES else ""}


def lines_of(s: str, px: int, width: float, most: int = 2) -> tuple[list[str], int]:
    """The words broken into at most `most` lines and the biggest size at which they fit `width`."""
    words_ = s.split()
    best: tuple[list[str], int] = ([s], fit(s, px, width))
    for n in range(2, min(most, len(words_)) + 1):
        cut = [words_[round(k * len(words_) / n):round((k + 1) * len(words_) / n)] for k in range(n)]
        rows = [" ".join(r) for r in cut if r]
        size = min(fit(r, px, width) for r in rows)
        if size > best[1] * 1.15:
            best = (rows, size)
    return best


def headline(img: Image.Image, s: str, cy: float, px: int, fill=WHITE, width: float = 980) -> None:
    """The title's question at the top, in one or two heavy lines."""
    if not s:
        return
    rows, size = lines_of(s, px, width)
    lead = size * 1.02
    def draw(g):
        for k, r in enumerate(rows):
            text(g, (540, cy - lead * (len(rows) - 1) / 2 + k * lead), r, size, fill, 12)
    shadowed(img, draw)


# --------------------------------------------------------------------------- drawing pieces (all in SS pixels)


def text(d: ImageDraw.ImageDraw, xy, s: str, px: int, fill, stroke: int = 0, stroke_fill=INK, anchor: str = "mm") -> None:
    d.text((xy[0] * SS, xy[1] * SS), s, font=bold(px), fill=fill, anchor=anchor, stroke_width=stroke * SS, stroke_fill=stroke_fill)


def shadowed(img: Image.Image, draw_fn, offset=(10, 14), blur: int = 10, alpha: int = 110) -> None:
    """draw_fn(ImageDraw) twice: once as a soft shadow, then for real."""
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(sh))
    a = sh.split()[3].point(lambda v: v * alpha // 255)
    black = Image.new("RGBA", img.size, (0, 0, 0, 0))
    black.putalpha(a)
    black = black.filter(ImageFilter.GaussianBlur(blur * SS))
    img.alpha_composite(black, (offset[0] * SS, offset[1] * SS))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    img.alpha_composite(layer)


def product(img: Image.Image, name: str, box, label: list[str], accent: str = "green", angle: float = 0.0) -> None:
    """The drawing as a solid sticker: white body, colour washes, thick black outline, the label in heavy letters."""
    if name not in pw_draw.DRAWINGS:
        return
    x0, y0, x1, y1 = box
    side = int(max(x1 - x0, y1 - y0) * 1.5) * SS
    layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    pad = side * 0.17
    d, k, dx, dy = pw_draw.scaled(name, (pad, pad, side - pad, side - pad))
    at = lambda pts: [(dx + x * k, dy + y * k) for x, y in pts]
    g = ImageDraw.Draw(layer)
    width = int(max(10, k * 6.5))
    if d.body:
        g.polygon(at(d.body), fill=WHITE + (255,))
    for poly, role in d.washes:
        g.polygon(at(poly), fill=(TINT["ink"] if role == "ink_wash" else TINT[accent]) + (255,))
    for pts, role in d.strokes:
        rgb = RED if role == "red" else INK
        g.line(at(pts), fill=rgb + (255,), width=width if role != "thin" else max(6, width // 2), joint="curve")
        r = (width if role != "thin" else max(6, width // 2)) / 2
        for px, py in (at(pts)[0], at(pts)[-1]):
            g.ellipse([px - r, py - r, px + r, py + r], fill=rgb + (255,))
    words_ = [str(w) for w in label if str(w).strip()][:3]
    if words_ and d.label_at:
        lx, ly, lw = d.label_at
        room = lw * k * 0.9
        px = min(fit(w, int(lw * k / SS * 0.42), room / SS) for w in words_)
        lead = px * SS * 1.0
        for j, w in enumerate(words_):
            g.text((dx + lx * k, dy + ly * k - lead * (len(words_) - 1) / 2 + j * lead), w, font=bold(px), fill=INK + (255,), anchor="mm")
    layer = layer.rotate(angle, resample=Image.BICUBIC)
    cx, cy = (x0 + x1) / 2 * SS, (y0 + y1) / 2 * SS
    pos = (int(cx - side / 2), int(cy - side / 2))
    a = layer.split()[3]
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(a.point(lambda v: v * 120 // 255))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(12 * SS)), (pos[0] + 16 * SS, pos[1] + 22 * SS))
    img.alpha_composite(layer, pos)


def burst(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill, points: int = 16, inner: float = 0.78, outline=INK) -> None:
    pts = [((cx + (r if k % 2 == 0 else r * inner) * math.cos(math.pi * k / points + 0.2)) * SS,
            (cy + (r if k % 2 == 0 else r * inner) * math.sin(math.pi * k / points + 0.2)) * SS) for k in range(2 * points)]
    d.polygon(pts, fill=fill, outline=outline, width=10 * SS)


def strike(d: ImageDraw.ImageDraw, cx: float, cy: float, half: float, width: int = 22) -> None:
    d.line([((cx - half) * SS, (cy + 26) * SS), ((cx + half) * SS, (cy - 26) * SS)], fill=RED, width=width * SS)


def radial(c0, c1) -> Image.Image:
    """A background from c0 in the middle to c1 at the corners."""
    small = Image.new("RGB", (108, 192))
    px = small.load()
    for y in range(192):
        for x in range(108):
            t = min(1.0, math.hypot((x - 54) / 54, (y - 86) / 96) / 1.25)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c0, c1))
    return small.resize((W * SS, H * SS), Image.BICUBIC).convert("RGBA")


def rays(img: Image.Image, cx: float, cy: float, rgb, n: int = 18, alpha: int = 70) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)
    R = 3000 * SS
    for k in range(n):
        a0, a1 = 2 * math.pi * k / n, 2 * math.pi * (k + 0.5) / n
        g.polygon([(cx * SS, cy * SS), (cx * SS + R * math.cos(a0), cy * SS + R * math.sin(a0)),
                   (cx * SS + R * math.cos(a1), cy * SS + R * math.sin(a1))], fill=rgb + (alpha,))
    img.alpha_composite(layer)


# --------------------------------------------------------------------------- styles


def shock(w: dict[str, Any]) -> Image.Image:
    img = radial((255, 232, 92), (255, 160, 0))
    rays(img, 540, 1050, (255, 255, 255), alpha=55)
    headline(img, w["title"], 270, 190)
    product(img, w["draw"], (40, 640, 660, 1600), w["label"], "green", angle=9)
    if w["old"]:
        px = fit(w["old"], 170, 430)
        def old(g):
            if w["old_label"]:
                text(g, (810, 560), w["old_label"].upper(), fit(w["old_label"].upper(), 64, 420), INK)
            text(g, (810, 680), w["old"], px, INK, 0)
        shadowed(img, old, alpha=60)
        strike(ImageDraw.Draw(img), 810, 690, bold(px).getlength(w["old"]) / SS / 2 + 24, 18)
    if w["now"]:
        def star(g):
            burst(g, 790, 1130, 250, RED)
            figure, _, unit = w["now"].partition(" ")  # "200B marks": the figure big, the unit under it
            if unit:
                px = fit(figure, 200, 320)
                text(g, (790, 1110), figure, px, WHITE, 6)
                text(g, (790, 1110 + px * 0.75), unit.upper(), fit(unit.upper(), 70, 260), WHITE, 4)
            else:
                text(g, (790, 1135), w["now"], fit(w["now"], 220, 330), WHITE, 6)
        shadowed(img, star)
    if w["hook"]:
        shadowed(img, lambda g: text(g, (540, 1720), w["hook"], fit(w["hook"], 200, 960), WHITE, 14))
    return img


def versus(w: dict[str, Any]) -> Image.Image:
    img = Image.new("RGBA", (W * SS, H * SS), RED + (255,))
    d = ImageDraw.Draw(img)
    d.polygon([(W * 0.62 * SS, 0), (W * SS, 0), (W * SS, H * SS), (W * 0.38 * SS, H * SS)], fill=GREEN)
    d.line([(W * 0.62 * SS, 0), (W * 0.38 * SS, H * SS)], fill=WHITE, width=18 * SS)
    headline(img, w["title"], 230, 170)
    if w["old_label"]:
        shadowed(img, lambda g: text(g, (290, 470), w["old_label"].upper(), fit(w["old_label"].upper(), 90, 500), WHITE, 6))
    if w["old"]:
        shadowed(img, lambda g: text(g, (290, 610), w["old"], fit(w["old"], 200, 500), WHITE, 10))
    product(img, w["draw"], (250, 700, 790, 1280), w["label"], "green", angle=-6)
    if w["now_label"]:
        shadowed(img, lambda g: text(g, (780, 1370), w["now_label"].upper(), fit(w["now_label"].upper(), 90, 500), WHITE, 6))
    if w["now"]:
        shadowed(img, lambda g: text(g, (780, 1530), w["now"], fit(w["now"], 240, 520), YELLOW, 12))
    if w["hook"]:
        shadowed(img, lambda g: text(g, (540, 1730), w["hook"], fit(w["hook"], 200, 960), WHITE, 14))
    return img


def tag(w: dict[str, Any]) -> Image.Image:
    img = radial((52, 56, 60), (14, 14, 16))
    rays(img, 600, 900, (255, 255, 255), alpha=12)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)
    x0, x1, y0, y1 = 300, 1020, 520, 1180
    cy = (y0 + y1) / 2
    body = [(x0, cy), (x0 + 170, y0), (x1, y0), (x1, y1), (x0 + 170, y1)]
    g.polygon([(x * SS, y * SS) for x, y in body], fill=WHITE, outline=GREEN, width=20 * SS)
    g.ellipse([(x0 + 95) * SS, (cy - 34) * SS, (x0 + 163) * SS, (cy + 34) * SS], fill=(52, 56, 60), outline=INK, width=8 * SS)
    g.line([((x0 + 129) * SS, cy * SS), (200 * SS, 300 * SS)], fill=(235, 235, 235), width=9 * SS)
    if w["now"]:
        text(g, ((x0 + 190 + x1) / 2, cy + 10), w["now"], fit(w["now"], 330, x1 - x0 - 230), RED, 0)
    layer = layer.rotate(-8, center=(660 * SS, 850 * SS), resample=Image.BICUBIC)
    a = layer.split()[3]
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(a.point(lambda v: v * 150 // 255))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14 * SS)), (14 * SS, 24 * SS))
    img.alpha_composite(layer)
    product(img, w["draw"], (20, 980, 560, 1640), w["label"], "green", angle=-10)
    if w["old"]:
        def old(g):
            if w["old_label"]:
                text(g, (790, 1330), w["old_label"].upper(), fit(w["old_label"].upper(), 80, 440), WHITE, 5)
            text(g, (790, 1480), w["old"], fit(w["old"], 170, 420), WHITE, 6)
        shadowed(img, old)
        strike(ImageDraw.Draw(img), 790, 1490, bold(fit(w["old"], 170, 420)).getlength(w["old"]) / SS / 2 + 24, 18)
    headline(img, w["title"], 250, 180, YELLOW)
    if w["hook"]:
        shadowed(img, lambda g: text(g, (540, 1740), w["hook"], fit(w["hook"], 180, 960), WHITE, 12))
    return img


def make(script: dict, out: Path, style: str = "") -> Path:
    """The thumbnail of a script, as a 1080x1920 JPEG at out."""
    w = words(script)
    style = style or w["style"] or "shock"
    img = {"shock": shock, "versus": versus, "tag": tag}[style](w)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.resize((W, H), Image.LANCZOS).convert("RGB").save(out, quality=92)
    return out
