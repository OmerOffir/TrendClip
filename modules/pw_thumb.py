"""Price Tag Why's thumbnails: a picture made to be clicked, not a frame of the video. The product drawn big and
solid (modules.pw_draw), two prices that don't add up, and a 1-3 word hook in heavy letters, on a loud background.

Styles (THUMB_STYLE, or a script's thumbnail.style):
    shock   yellow burst behind the product, the price it should be crossed out, the real one in a red starburst
    versus  red half / green half: what it should cost against what it costs, the product between them
    tag     dark board, a giant white price tag with the real price, the product hanging beside it

What goes on it comes from the script's "thumbnail" ({draw, label, old, now, hook, style}), else its product scene
(the drawing, the first red price, the last green price) and "WHY?!".
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
PRICE = re.compile(r"[$€£¥]?\s?\d[\d,.]*\s?(¢|%|[kKmMbBT])?")


def bold(px: int) -> ImageFont.FreeTypeFont:
    bw_render.ensure_fonts()
    return bw_render.font(px * SS)


def fit(text: str, px: int, width: float) -> int:
    while px > 30 and bold(px).getlength(text) > width * SS:
        px -= 6
    return px


def words(script: dict) -> dict[str, Any]:
    """What the thumbnail shows: {draw, label, old, now, hook, style}."""
    t = dict(script.get("thumbnail") or {})
    product = next(((s.get("product") or {}) for s in script.get("scenes") or [] if s.get("kind") == "product"), {})
    lines = [bw_brand.pen_line(x) for x in product.get("lines") or []]
    prices = [ln for ln in lines if PRICE.fullmatch(ln["text"].strip())]
    old = next((ln["text"] for ln in prices if ln.get("color") == "red"), prices[0]["text"] if len(prices) > 1 else "")
    now = next((ln["text"] for ln in reversed(prices) if ln.get("color") == "green"), prices[-1]["text"] if prices else "")
    return {"draw": t.get("draw") or product.get("draw") or "", "label": t.get("label") or product.get("label") or [],
            "old": t.get("old", old), "now": t.get("now", now), "hook": t.get("hook") or "WHY?!",
            "style": t.get("style") if t.get("style") in STYLES else ""}


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
    rays(img, 540, 1000, (255, 255, 255), alpha=55)
    product(img, w["draw"], (70, 560, 720, 1560), w["label"], "green", angle=9)
    d = ImageDraw.Draw(img)
    if w["old"]:
        px = fit(w["old"], 230, 560)
        shadowed(img, lambda g: text(g, (800, 470), w["old"], px, INK, 0))
        strike(ImageDraw.Draw(img), 800, 480, bold(px).getlength(w["old"]) / SS / 2 + 30)
    if w["now"]:
        def star(g):
            burst(g, 790, 1060, 270, RED)
            text(g, (790, 1060), w["now"], fit(w["now"], 250, 400), WHITE, 6)
        shadowed(img, star)
    hook = w["hook"].upper()
    shadowed(img, lambda g: text(g, (540, 1700), hook, fit(hook, 250, 960), WHITE, 14))
    return img


def versus(w: dict[str, Any]) -> Image.Image:
    img = Image.new("RGBA", (W * SS, H * SS), RED + (255,))
    d = ImageDraw.Draw(img)
    d.polygon([(W * 0.62 * SS, 0), (W * SS, 0), (W * SS, H * SS), (W * 0.38 * SS, H * SS)], fill=GREEN)
    d.line([(W * 0.62 * SS, 0), (W * 0.38 * SS, H * SS)], fill=WHITE, width=18 * SS)
    shadowed(img, lambda g: text(g, (290, 330), "SHOULD BE", fit("SHOULD BE", 110, 500), WHITE, 6))
    if w["old"]:
        shadowed(img, lambda g: text(g, (290, 520), w["old"], fit(w["old"], 230, 520), WHITE, 10))
    shadowed(img, lambda g: text(g, (800, 1250), "IT'S", 120, WHITE, 6))
    if w["now"]:
        shadowed(img, lambda g: text(g, (790, 1470), w["now"], fit(w["now"], 300, 520), YELLOW, 12))
    product(img, w["draw"], (230, 640, 850, 1400), w["label"], "green", angle=-6)
    hook = w["hook"].upper()
    shadowed(img, lambda g: text(g, (540, 1730), hook, fit(hook, 220, 960), WHITE, 14))
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
            text(g, (790, 1330), "NOT", 110, WHITE, 5)
            text(g, (790, 1480), w["old"], fit(w["old"], 170, 420), WHITE, 6)
        shadowed(img, old)
        strike(ImageDraw.Draw(img), 790, 1490, bold(fit(w["old"], 170, 420)).getlength(w["old"]) / SS / 2 + 24, 18)
    hook = w["hook"].upper()
    shadowed(img, lambda g: text(g, (540, 260), hook, fit(hook, 240, 960), YELLOW, 12))
    return img


def make(script: dict, out: Path, style: str = "") -> Path:
    """The thumbnail of a script, as a 1080x1920 JPEG at out."""
    w = words(script)
    style = style or w["style"] or "shock"
    img = {"shock": shock, "versus": versus, "tag": tag}[style](w)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.resize((W, H), Image.LANCZOS).convert("RGB").save(out, quality=92)
    return out
