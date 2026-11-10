"""Price Tag Why's "pegboard" look (2026-11-10): the whole story hangs on a shop's pegboard, as price tags on strings.

Built after the owner said the channel looks like too many others (flat people in a flat city) and asked for an
animation style nobody else has. Here everything is a tag hanging from a rail: the people are tags with faces, their
money is a price tag tied under them, documents and boards are tags with a drawing. Nothing is ever still: every tag
swings on its string like a pendulum, with its shadow on the board. One continuous take, no cuts: at a scene change
the old tags are pulled up and the new ones drop in.

A script of format "tags" has scenes of kind "tags", each with (all optional):
    year, place  the aisle sign at the top ("1904", "MARYLAND"); it flips when it changes; year: "PRICE TAG WHY" takes the date down
    board        the board's colour: teal | navy | plum | brick | olive | coal (kept until changed); dim: true darkens it
    hang         what hangs, left to right: [{id, kind, x: 0-1, len, cue, scale, child: {...}, ...}]
                 a tag with the same id in the next scene stays up (and slides to its new x); the others are pulled up
        kind person  name, sub, look: woman|man|tenant|boss|suit, expr, moods: [{cue, expr}]
        kind price   text, color: white|red|gold|green|black, size: s|m|l, sub, from + land (races up, lands on the cue)
        kind card    draw: board|doc|news|paper|box|bottle|coin|machine|text, title/lines/headline/label, sub...
        child        one tag tied under it (price or card fields), with cue; it stays while the next scene repeats it
    stickers     slapped on the board: [{text, kind: label|dark|gold|burst|stamp, x, y, rot, cue, size}]
    cut          {id, cue}: scissors cut its string and it falls off the board
    belt         {label, cue, draw: box|bottle|coin}: boxes (or bottles, coins) riding along the rail (a factory line)
    kick         [{id, cue, k}]: a push on a tag when the word is said
A cue is a word of the scene's narration (modules.pw_world.cue_time). The first scene's tags are up on frame 0.

Fonts: Anton and Space Mono (SIL OFL), fetched into assets/fonts on first use. Voice, music, sound effects and encoding
are modules.bw_render.render's.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from modules import bw_render
from modules.bw_render import FPS, H, W, Scene, Spec
from modules.pw_world import amount, back, cue_time, ease, racing

SS = 2
RAIL_Y = 250
CAPTION_Y = 1452
INK = (26, 24, 22)
FONTS = {"bold": ("Anton-Regular.ttf", "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf"),
         "mono": ("SpaceMono-Bold.ttf", "https://raw.githubusercontent.com/google/fonts/main/ofl/spacemono/SpaceMono-Bold.ttf")}
BOARDS = dict(teal=(22, 92, 88), navy=(26, 48, 104), plum=(92, 40, 86), brick=(150, 58, 44), olive=(84, 98, 40),
              coal=(44, 46, 54))
TWINE, TWINE_EDGE = (214, 184, 132), (120, 92, 50)


# --------------------------------------------------------------------------- fonts and helpers


def ensure_fonts(folder: Path | None = None) -> None:
    folder = folder or bw_render.FONTS_DIR
    for name, url in FONTS.values():
        f = folder / name
        if f.is_file():
            continue
        try:
            import requests

            r = requests.get(url, timeout=30)
            r.raise_for_status()
            folder.mkdir(parents=True, exist_ok=True)
            f.write_bytes(r.content)
        except Exception:  # noqa: BLE001 - the channel's bold font still draws the video
            bw_render.logger.warning("Could not download %s", name)
    font.cache_clear()


@lru_cache(maxsize=None)
def font(size: int, kind: str = "bold") -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(bw_render.FONTS_DIR / FONTS[kind][0]), size)
    except OSError:
        return bw_render.font(size)


def fit(text: str, width: float, size: int, kind: str = "bold") -> ImageFont.FreeTypeFont:
    while size > 20 and font(size, kind).getlength(text) > width:
        size -= 4
    return font(size, kind)


def wrap(text: str, f: ImageFont.FreeTypeFont, width: float) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if lines and f.getlength(lines[-1] + " " + word) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def shrink(img: Image.Image) -> Image.Image:
    return img.resize((max(1, img.width // SS), max(1, img.height // SS)), Image.LANCZOS)


def shade(c: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(v * k))) for v in c)  # type: ignore[return-value]


Sprite = tuple[Image.Image, tuple[float, float], tuple[float, float]]  # image, its hole, its bottom middle


def blank_tag(w: int, h: int, col, edge, cut: int | None = None):
    """A tag's paper (twice the size): cut top corners, a ringed hole. -> image, draw, hole, padding."""
    cut = cut or int(w * 0.2)
    pad = 16
    im = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pts = [(pad + cut, pad), (pad + w - cut, pad), (pad + w, pad + cut), (pad + w, pad + h), (pad, pad + h), (pad, pad + cut)]
    d.polygon(pts, fill=col)
    d.line(pts + [pts[0]], fill=edge, width=8, joint="curve")
    hx, hy = pad + w // 2, pad + max(56, int(cut * 0.7))
    d.ellipse((hx - 34, hy - 34, hx + 34, hy + 34), fill=(245, 236, 214), outline=edge, width=5)
    d.ellipse((hx - 15, hy - 15, hx + 15, hy + 15), fill=(0, 0, 0, 0))
    return im, d, (hx, hy), pad


def done(im: Image.Image, hole, w: int, h: int, pad: int, scale: float = 1.0) -> Sprite:
    out = shrink(im)
    piv, bot = (hole[0] / SS, hole[1] / SS), ((pad + w / 2) / SS, (pad + h - 12) / SS)
    if abs(scale - 1) > 0.01:
        out = out.resize((max(1, int(out.width * scale)), max(1, int(out.height * scale))), Image.LANCZOS)
        piv, bot = (piv[0] * scale, piv[1] * scale), (bot[0] * scale, bot[1] * scale)
    return out, piv, bot


# --------------------------------------------------------------------------- the people: tags with a face

LOOKS: dict[str, dict[str, Any]] = {
    "woman": dict(col=(236, 200, 140), edge=(120, 84, 40), hair="bun", hair_col=(92, 52, 30), lashes=True, cheeks=True),
    "man": dict(col=(236, 240, 244), edge=(70, 84, 100), stache=True, monocle=True, bow=True, brows=True),
    "tenant": dict(col=(170, 206, 236), edge=(50, 84, 120), hair="cap", hair_col=(60, 70, 96), cheeks=True),
    "boss": dict(col=(176, 226, 180), edge=(40, 100, 60), hair="slick", hair_col=(40, 40, 40), stache=True, bow=True, brows=True),
    "suit": dict(col=(226, 222, 232), edge=(80, 70, 100), hair="slick", hair_col=(70, 50, 40), tie=True, brows=True),
}
EXPRESSIONS = ("neutral", "smile", "grin", "sad", "shocked", "think", "smug")


@lru_cache(maxsize=256)
def person(look: str, name: str, sub: str, expr: str, blink: bool, gaze: int, scale: float = 1.0) -> Sprite:
    L = LOOKS.get(look, LOOKS["woman"])
    w, h = 600, 800
    col, edge = L["col"], L["edge"]
    im, d, hole, p = blank_tag(w, h, col, edge)
    cx = p + w // 2
    ey = p + 330
    if L.get("hair") == "bun":
        d.pieslice((p + 70, p + 130, p + w - 70, p + 520), 180, 360, fill=L["hair_col"])
        d.ellipse((cx + 140, p + 100, cx + 270, p + 230), fill=L["hair_col"])
        d.ellipse((p + 105, p + 220, p + w - 105, p + 590), fill=col)
    elif L.get("hair") == "cap":
        d.rounded_rectangle((p + 110, p + 150, p + w - 110, p + 250), 50, fill=L["hair_col"])
        d.rounded_rectangle((p + 90, p + 226, p + w - 40, p + 256), 14, fill=shade(L["hair_col"], 0.7))
    elif L.get("hair") == "slick":
        d.chord((p + 110, p + 150, p + w - 110, p + 420), 190, 350, fill=L["hair_col"])
    # eyes
    r = 52 if expr == "shocked" else 44
    for side in (-1, 1):
        x = cx + side * 92
        if blink:
            d.arc((x - r, ey - 10, x + r, ey + 40), 200, 340, fill=INK, width=10)
            continue
        d.ellipse((x - r, ey - r * 1.2, x + r, ey + r * 1.2), fill=(255, 255, 255), outline=INK, width=7)
        px, py = x + gaze * 16, ey + (-16 if expr == "think" else 4)
        d.ellipse((px - 21, py - 21, px + 21, py + 21), fill=INK)
        d.ellipse((px - 4, py - 16, px + 10, py - 2), fill=(255, 255, 255))
        if expr == "smug":
            d.rectangle((x - r - 4, ey - r * 1.2 - 4, x + r + 4, ey - 10), fill=col)
            d.line((x - r, ey - 10, x + r, ey - 10), fill=INK, width=8)
        if L.get("lashes"):
            d.line((x + side * 30, ey - 50, x + side * 58, ey - 78), fill=INK, width=7)
    # brows
    by = ey - 86
    slant = {"sad": (-22, 8), "shocked": (-34, -34), "think": (-30, 0), "smug": (0, -24)}.get(expr)
    if slant or L.get("brows"):
        a, b = slant or (0, 0)
        for side in (-1, 1):
            inner, outer = (a, b) if expr not in ("think", "smug") else ((a, a) if side < 0 else (b, b))
            d.line((cx + side * 40, by + inner, cx + side * 150, by + outer), fill=INK, width=13)
    if L.get("monocle"):
        d.ellipse((cx + 92 - 64, ey - 70, cx + 92 + 64, ey + 70), outline=(196, 150, 40), width=9)
        d.line((cx + 148, ey + 40, cx + 196, ey + 230), fill=(196, 150, 40), width=5)
    if L.get("cheeks") and expr not in ("sad", "shocked"):
        for side in (-1, 1):
            d.ellipse((cx + side * 160 - 36, ey + 70, cx + side * 160 + 36, ey + 118), fill=(238, 142, 122))
    my = p + 470
    if L.get("stache"):
        d.pieslice((cx - 160, p + 400, cx + 8, p + 520), 180, 360, fill=INK)
        d.pieslice((cx - 8, p + 400, cx + 160, p + 520), 180, 360, fill=INK)
        my = p + 500
    if expr == "neutral":
        d.line((cx - 40, my + 6, cx + 40, my + 6), fill=INK, width=10)
    elif expr == "smile":
        d.arc((cx - 60, my - 50, cx + 60, my + 34), 20, 160, fill=INK, width=10)
    elif expr == "grin":
        d.chord((cx - 72, my - 62, cx + 72, my + 52), 0, 180, fill=(70, 20, 24), outline=INK, width=6)
        d.rectangle((cx - 56, my - 4, cx + 56, my + 12), fill=(255, 255, 255))
    elif expr == "sad":
        d.arc((cx - 55, my - 6, cx + 55, my + 70), 200, 340, fill=INK, width=10)
    elif expr == "shocked":
        d.ellipse((cx - 30, my - 30, cx + 30, my + 44), fill=(70, 20, 24), outline=INK, width=6)
    elif expr == "think":
        d.line((cx - 6, my + 12, cx + 52, my - 4), fill=INK, width=10)
    else:  # smug
        d.arc((cx - 20, my - 44, cx + 74, my + 22), 10, 130, fill=INK, width=10)
    if L.get("bow"):
        for side in (-1, 1):
            d.polygon([(cx, p + 580), (cx + side * 70, p + 548), (cx + side * 70, p + 612)], fill=(204, 38, 42))
        d.ellipse((cx - 14, p + 566, cx + 14, p + 594), fill=(150, 20, 26))
    if L.get("tie"):
        d.polygon([(cx - 22, p + 548), (cx + 22, p + 548), (cx + 30, p + 606), (cx, p + 622), (cx - 30, p + 606)], fill=(40, 70, 150))
    d.line((p + 50, p + 632, p + w - 50, p + 632), fill=edge, width=4)
    d.text((cx, p + (692 if sub else 712)), name, font=fit(name, w - 90, 92), fill=INK, anchor="mm")
    if sub:
        d.text((cx, p + 762), sub, font=fit(sub, w - 80, 38, "mono"), fill=shade(edge, 0.8), anchor="mm")
    return done(im, hole, w, h, p, scale)


# --------------------------------------------------------------------------- prices and cards

PRICE_COLORS = dict(white=((255, 255, 255), (70, 70, 70), INK), red=((214, 40, 40), (120, 10, 16), (255, 255, 255)),
                    gold=((255, 206, 40), (150, 110, 10), INK), green=((38, 160, 92), (14, 90, 46), (255, 255, 255)),
                    black=((28, 28, 32), (0, 0, 0), (255, 214, 0)))
PRICE_SIZES = dict(s=(440, 300), m=(600, 400), l=(960, 500))


@lru_cache(maxsize=512)
def price(text: str, color: str = "white", size: str = "m", sub: str = "", scale: float = 1.0) -> Sprite:
    w, h = PRICE_SIZES.get(size, PRICE_SIZES["m"])
    col, edge, fg = PRICE_COLORS.get(color, PRICE_COLORS["white"])
    im, d, hole, p = blank_tag(w, h, col, edge, cut=int(h * 0.22))
    top = p + int(h * 0.3)
    d.rectangle((p + 26, top, p + w - 26, p + h - 26), outline=fg, width=4)
    room = h - 26 - int(h * 0.3)
    cy = top + room * (0.42 if sub else 0.5)
    d.text((p + w // 2, cy), text, font=fit(text, w - 90, int(room * (0.62 if sub else 0.8))), fill=fg, anchor="mm")
    if sub:
        d.text((p + w // 2, p + h - 62), sub, font=fit(sub, w - 90, 40, "mono"), fill=fg, anchor="mm")
    return done(im, hole, w, h, p, scale)


def _board(d: ImageDraw.ImageDraw, x: float, y: float, s: float, title: str, tint) -> None:
    edge = (60, 50, 40)
    d.rectangle((x, y, x + s, y + s), fill=tint, outline=edge, width=8)
    c = s / 9
    colors = [(204, 60, 50), (240, 196, 50), (60, 150, 90), (60, 110, 190), (230, 130, 50), (150, 80, 160)]
    for k in range(9):
        for (cx0, cy0, horiz) in ((x + k * c, y, True), (x + k * c, y + s - c, True), (x, y + k * c, False), (x + s - c, y + k * c, False)):
            d.rectangle((cx0, cy0, cx0 + c, cy0 + c), outline=edge, width=4)
            if k not in (0, 8):
                band = (cx0 + 4, cy0 + 4, cx0 + c - 4, cy0 + c * 0.34) if horiz else (cx0 + 4, cy0 + 4, cx0 + c * 0.34, cy0 + c - 4)
                d.rectangle(band, fill=colors[(k + int(cx0 + cy0)) % len(colors)])
    d.rectangle((x + c, y + c, x + s - c, y + s - c), outline=edge, width=6)
    if title:
        f = fit(max(title.split(), key=len), s - 2 * c - 60, 130)
        lines = wrap(title, f, s - 2 * c - 50)
        y0 = y + s / 2 - (len(lines) - 1) * f.size * 0.56
        for k, ln in enumerate(lines):
            d.text((x + s / 2, y0 + k * f.size * 1.12), ln, font=f, fill=(190, 40, 40), anchor="mm")


@lru_cache(maxsize=128)
def card(draw: str, a: str = "", b: str = "", c: str = "", scale: float = 1.0) -> Sprite:
    """A tag with a drawing: board (a: title, b: tint), doc (a: title, b: number, c: year), news (a: headline, b: date,
    c: paper's name), box (a: label), bottle (a: label, b: price dot), coin (a: value, b: small line), machine (a: name,
    b: its coin), text (a: lines split by |, b: color)."""
    if draw == "board":
        w, h = 880, 1010
        tint = {"blue": (206, 224, 240), "pink": (244, 214, 214), "yellow": (246, 236, 190)}.get(b, (214, 236, 214))
        im, d, hole, p = blank_tag(w, h, (248, 240, 220), (120, 96, 60), cut=150)
        _board(d, p + 60, p + 180, 760, a, tint)
    elif draw == "doc":
        w, h = 640, 840
        im, d, hole, p = blank_tag(w, h, (250, 246, 236), (110, 100, 84))
        cx = p + w // 2
        d.text((cx, p + 200), a or "PATENT", font=fit(a or "PATENT", w - 120, 110), fill=INK, anchor="mm")
        d.line((p + 60, p + 268, p + w - 60, p + 268), fill=INK, width=6)
        d.line((p + 60, p + 282, p + w - 60, p + 282), fill=INK, width=2)
        rnd = random.Random(len(a) + 3)
        for k in range(6):
            d.rectangle((p + 70, p + 330 + k * 44, p + w - 70 - rnd.randint(0, 180), p + 344 + k * 44), fill=(176, 168, 150))
        if b:
            d.text((p + 70, p + 640), b, font=fit(b, w - 300, 44, "mono"), fill=INK, anchor="lm")
        if c:
            d.text((p + 70, p + 730), c, font=font(100), fill=(190, 40, 40), anchor="lm")
        sx, sy = p + w - 150, p + 690
        d.polygon([(sx - 40, sy + 30), (sx - 60, sy + 130), (sx - 20, sy + 100), (sx, sy + 40)], fill=(204, 38, 42))
        d.polygon([(sx + 40, sy + 30), (sx + 60, sy + 130), (sx + 20, sy + 100), (sx, sy + 40)], fill=(204, 38, 42))
        pts = []
        for k in range(32):
            rr = 84 if k % 2 == 0 else 70
            pts.append((sx + rr * math.cos(k * math.pi / 16), sy + rr * math.sin(k * math.pi / 16)))
        d.polygon(pts, fill=(240, 190, 40))
        d.ellipse((sx - 48, sy - 48, sx + 48, sy + 48), outline=(170, 120, 10), width=6)
    elif draw in ("news", "paper"):  # paper: a front page with no portrait
        w, h = 960, 1060
        im, d, hole, p = blank_tag(w, h, (234, 230, 218), (110, 104, 92), cut=150)
        cx = p + w // 2
        d.text((cx, p + 210), c or "THE DAILY NEWS", font=fit(c or "THE DAILY NEWS", w - 160, 96, "mono"), fill=INK, anchor="mm")
        d.line((p + 50, p + 276, p + w - 50, p + 276), fill=INK, width=8)
        d.text((cx, p + 312), b, font=font(34, "mono"), fill=(90, 86, 78), anchor="mm")
        d.line((p + 50, p + 346, p + w - 50, p + 346), fill=INK, width=3)
        f = font(112)
        lines = wrap(a, f, w - 110)
        while len(lines) > 3 and f.size > 60:
            f = font(f.size - 8)
            lines = wrap(a, f, w - 110)
        y = p + 420
        for ln in lines:
            d.text((cx, y), ln, font=f, fill=INK, anchor="mm")
            y += f.size * 1.14
        y += 4
        if draw == "paper":
            rnd = random.Random(len(a))
            for k in range(int((p + h - 70 - y) // 36)):
                for x0, x1 in ((p + 60, p + w // 2 - 20), (p + w // 2 + 20, p + w - 60)):
                    d.rectangle((x0, y + 6 + k * 36, x1 - rnd.randint(0, 90), y + 18 + k * 36), fill=(160, 154, 140))
            return done(im, hole, w, h, p, scale)
        d.rectangle((p + 60, y, p + 360, p + h - 60), fill=(200, 194, 178), outline=INK, width=4)
        fx, fy = p + 210, (y + p + h - 60) / 2 + 20
        d.pieslice((fx - 90, fy - 120, fx + 90, fy + 60), 180, 360, fill=(92, 52, 30))
        d.ellipse((fx + 40, fy - 130, fx + 110, fy - 60), fill=(92, 52, 30))
        d.ellipse((fx - 70, fy - 80, fx + 70, fy + 70), fill=(236, 200, 140), outline=INK, width=4)
        d.arc((fx - 30, fy, fx + 30, fy + 40), 20, 160, fill=INK, width=6)
        for ex in (fx - 26, fx + 26):
            d.ellipse((ex - 8, fy - 22, ex + 8, fy - 6), fill=INK)
        rnd = random.Random(len(a))
        for k in range(int((p + h - 70 - y) // 36)):
            d.rectangle((p + 400, y + 6 + k * 36, p + w - 60 - rnd.randint(0, 120), y + 18 + k * 36), fill=(160, 154, 140))
    elif draw == "box":
        w, h = 400, 420
        im, d, hole, p = blank_tag(w, h, (250, 246, 236), (110, 100, 84))
        d.rounded_rectangle((p + 40, p + 150, p + w - 40, p + h - 40), 10, fill=(204, 38, 42), outline=(110, 14, 18), width=6)
        d.rectangle((p + 40, p + 220, p + w - 40, p + 310), fill=(255, 255, 255))
        d.text((p + w // 2, p + 264), a, font=fit(a, w - 110, 70), fill=INK, anchor="mm")
        d.line((p + 40, p + 188, p + w - 40, p + 188), fill=(110, 14, 18), width=4)
    elif draw == "bottle":  # a glass bottle with a paper label (a) and a small price dot (b)
        w, h = 400, 760
        im, d, hole, p = blank_tag(w, h, (250, 246, 236), (110, 100, 84))
        cx, glass, dark = p + w // 2, (150, 70, 40), (84, 34, 18)
        d.rounded_rectangle((cx - 44, p + 150, cx + 44, p + 190), 8, fill=(200, 60, 50), outline=dark, width=5)
        d.polygon([(cx - 34, p + 190), (cx + 34, p + 190), (cx + 40, p + 300), (cx + 96, p + 400), (cx + 96, p + h - 80),
                   (cx - 96, p + h - 80), (cx - 96, p + 400), (cx - 40, p + 300)], fill=glass)
        d.rounded_rectangle((cx - 96, p + h - 120, cx + 96, p + h - 50), 26, fill=glass)
        d.line([(cx - 34, p + 190), (cx - 40, p + 300), (cx - 96, p + 400), (cx - 96, p + h - 76)], fill=dark, width=6, joint="curve")
        d.line([(cx + 34, p + 190), (cx + 40, p + 300), (cx + 96, p + 400), (cx + 96, p + h - 76)], fill=dark, width=6, joint="curve")
        d.line((cx - 66, p + 420, cx - 66, p + h - 110), fill=(214, 140, 100), width=10)
        d.rectangle((cx - 96, p + 440, cx + 96, p + 580), fill=(255, 255, 255), outline=dark, width=5)
        d.text((cx, p + 508), a, font=fit(a, 170, 76), fill=(190, 30, 34), anchor="mm")
        if b:
            d.ellipse((cx + 30, p + 590, cx + 150, p + 710), fill=(255, 214, 0), outline=INK, width=5)
            d.text((cx + 90, p + 648), b, font=fit(b, 96, 60), fill=INK, anchor="mm")
    elif draw == "coin":  # a gold coin: its value (a) in the middle, a line (b) round the bottom
        w, h = 640, 760
        im, d, hole, p = blank_tag(w, h, (250, 246, 236), (110, 100, 84))
        cx, cy, r = p + w // 2, p + 440, 250
        gold, deep = (244, 196, 48), (160, 110, 10)
        d.ellipse((cx - r, cy - r + 14, cx + r, cy + r + 14), fill=deep)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=gold, outline=deep, width=8)
        for k in range(48):
            an = k * math.pi / 24
            d.line((cx + (r - 30) * math.cos(an), cy + (r - 30) * math.sin(an), cx + (r - 12) * math.cos(an), cy + (r - 12) * math.sin(an)), fill=deep, width=5)
        d.ellipse((cx - r + 44, cy - r + 44, cx + r - 44, cy + r - 44), outline=deep, width=5)
        d.arc((cx - r + 70, cy - r + 70, cx + r - 110, cy + r - 110), 190, 260, fill=(255, 240, 170), width=14)
        d.text((cx, cy - (30 if b else 0)), a, font=fit(a, 2 * r - 150, 190), fill=(120, 76, 0), anchor="mm")
        if b:
            d.text((cx, cy + 110), b, font=fit(b, 2 * r - 190, 64, "mono"), fill=(120, 76, 0), anchor="mm")
    elif draw == "machine":  # a vending machine: its name (a), one coin slot marked b
        w, h = 620, 940
        im, d, hole, p = blank_tag(w, h, (250, 246, 236), (110, 100, 84))
        x0, y0, x1, y1 = p + 70, p + 160, p + w - 70, p + h - 60
        d.rounded_rectangle((x0, y0, x1, y1), 26, fill=(204, 38, 42), outline=(110, 14, 18), width=8)
        d.rectangle((x0 + 30, y0 + 30, x1 - 30, y0 + 150), fill=(255, 255, 255), outline=(110, 14, 18), width=5)
        d.text(((x0 + x1) / 2, y0 + 90), a, font=fit(a, x1 - x0 - 100, 90), fill=(190, 30, 34), anchor="mm")
        d.rectangle((x0 + 30, y0 + 180, x1 - 180, y1 - 200), fill=(60, 30, 26), outline=(110, 14, 18), width=5)
        for k in range(3):
            bx = x0 + 70 + k * 78
            d.rounded_rectangle((bx, y0 + 230, bx + 50, y1 - 230), 14, fill=(150, 70, 40), outline=(84, 34, 18), width=4)
            d.rectangle((bx, y0 + 330, bx + 50, y0 + 390), fill=(255, 255, 255))
        sx = x1 - 105
        d.rounded_rectangle((sx - 60, y0 + 200, sx + 60, y0 + 420), 12, fill=(240, 236, 226), outline=INK, width=5)
        d.rounded_rectangle((sx - 8, y0 + 230, sx + 8, y0 + 310), 6, fill=INK)
        d.text((sx, y0 + 366), b, font=fit(b, 100, 70), fill=INK, anchor="mm")
        d.rounded_rectangle((x0 + 60, y1 - 160, x1 - 60, y1 - 50), 12, fill=(40, 20, 18), outline=(110, 14, 18), width=5)
    else:  # text
        lines = [ln for ln in a.split("|") if ln]
        col, edge, fg = PRICE_COLORS.get(b or "white", PRICE_COLORS["white"])
        w, h = 600, 190 + 120 * len(lines)
        im, d, hole, p = blank_tag(w, h, col, edge, cut=90)
        for k, ln in enumerate(lines):
            d.text((p + w // 2, p + 200 + k * 120), ln, font=fit(ln, w - 80, 104), fill=fg, anchor="mm")
    return done(im, hole, w, h, p, scale)


# --------------------------------------------------------------------------- stickers, the sign, scissors

STICKERS = dict(label=((255, 255, 255), INK, (214, 40, 40)), dark=((24, 24, 28), (255, 255, 255), (255, 214, 0)),
                gold=((255, 214, 0), INK, INK))


@lru_cache(maxsize=256)
def label(text: str, kind: str = "label", size: int = 60, said: int = -1) -> Image.Image:
    """A price-gun label. `said`: the index of the word being spoken (the caption), drawn in red."""
    bg, fg, band = STICKERS.get(kind, STICKERS["label"])
    f = font(size * SS)
    words = text.split()
    gap = f.getlength(" ")
    widths = [f.getlength(x) for x in words]
    w, h = int(sum(widths) + gap * (len(words) - 1)) + 150, size * SS + 110
    im = Image.new("RGBA", (w + 16, h + 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((14, 14, w + 14, h + 14), 30, fill=(0, 0, 0, 90))
    d.rounded_rectangle((0, 0, w, h), 30, fill=bg)
    d.rectangle((0, 18, w, 34), fill=band)
    d.rectangle((0, h - 34, w, h - 18), fill=band)
    for y in range(60, h - 40, 44):
        for x in (0, w):
            d.ellipse((x - 15, y - 15, x + 15, y + 15), fill=(0, 0, 0, 0))
    x = 75.0
    for k, (wd, ww) in enumerate(zip(words, widths)):
        d.text((x, h / 2 - 4), wd, font=f, fill=(214, 40, 40) if k == said else fg, anchor="lm")
        x += ww + gap
    out = shrink(im)
    if out.width > W - 50:
        k = (W - 50) / out.width
        out = out.resize((int(out.width * k), int(out.height * k)), Image.LANCZOS)
    return out


@lru_cache(maxsize=64)
def burst(text: str, size: int = 150) -> Image.Image:
    R = size * SS
    lines = text.split("|")
    im = Image.new("RGBA", (2 * R + 30, 2 * R + 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for ox, col in ((16, (0, 0, 0, 90)), (0, (255, 214, 0))):
        d.polygon([(R + ox + (R if k % 2 == 0 else R * 0.84) * math.cos(k * math.pi / 20),
                    R + ox + (R if k % 2 == 0 else R * 0.84) * math.sin(k * math.pi / 20)) for k in range(40)], fill=col)
    d.ellipse((R * 0.26, R * 0.26, R * 1.74, R * 1.74), outline=(200, 30, 34), width=8)
    f = fit(max(lines, key=len), R * 1.3, int(R * 0.6))
    y0 = R - (len(lines) - 1) * f.size * 0.54
    for k, ln in enumerate(lines):
        d.text((R, y0 + k * f.size * 1.08), ln, font=f, fill=(200, 30, 34), anchor="mm")
    return shrink(im)


@lru_cache(maxsize=64)
def stamp(text: str, size: int = 84) -> Image.Image:
    f = font(size * SS)
    w, h = int(f.getlength(text)) + 120, size * SS + 90
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    red = (214, 30, 36, 255)
    d.rounded_rectangle((6, 6, w - 6, h - 6), 24, fill=(255, 244, 240, 235), outline=red, width=14)
    d.rounded_rectangle((28, 28, w - 28, h - 28), 14, outline=red, width=5)
    d.text((w / 2, h / 2 - 6), text, font=f, fill=red, anchor="mm")
    return shrink(im)


@lru_cache(maxsize=32)
def sign(year: str, place: str) -> Image.Image:
    fy, fp = font(92 * SS), font(40 * SS, "mono")
    wy, wp = fy.getlength(year), fp.getlength(place) if place else 0
    w, h = int(wy + (wp + 50 if place else 0)) + 130, 150 * SS
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, w - 1, h - 1), 30, fill=(22, 22, 28), outline=(255, 206, 40), width=8)
    d.text((65, h / 2 - 6), year, font=fy, fill=(255, 206, 40), anchor="lm")
    if place:
        d.text((65 + wy + 50, h / 2), place, font=fp, fill=(255, 255, 255), anchor="lm")
    return shrink(im)


@lru_cache(maxsize=64)
def scissors(a10: int, mirror: bool) -> Image.Image:
    a, S = a10 / 100.0, 760
    c = S // 2

    def blade() -> Image.Image:
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.polygon([(c - 40, c - 34), (c + 120, c - 30), (c + 340, c - 2), (c + 340, c + 6), (c - 40, c + 26)],
                  fill=(214, 220, 228), outline=(80, 88, 100), width=4)
        d.line((c - 20, c + 6, c - 170, c + 64), fill=(214, 220, 228), width=40)
        d.ellipse((c - 340, c + 20, c - 140, c + 170), outline=(214, 40, 40), width=40)
        return im

    b1 = blade().rotate(-math.degrees(a), resample=Image.BICUBIC)
    b2 = ImageOps.flip(blade()).rotate(math.degrees(a), resample=Image.BICUBIC)
    b1.alpha_composite(b2)
    ImageDraw.Draw(b1).ellipse((c - 16, c - 16, c + 16, c + 16), fill=(60, 66, 76))
    return ImageOps.mirror(b1) if mirror else b1


@lru_cache(maxsize=8)
def board_image(name: str) -> Image.Image:
    base = BOARDS.get(name, BOARDS["teal"])
    img = Image.new("RGB", (W, H), base)
    d = ImageDraw.Draw(img)
    hi, lo = shade(base, 1.45), shade(base, 0.5)
    for y in range(44, H, 72):
        for x in range(36, W, 72):
            d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=hi)
            d.ellipse((x - 9, y - 9, x + 6, y + 6), fill=lo)
    vig = Image.new("L", (W // 8, H // 8), 0)
    ImageDraw.Draw(vig).ellipse((-30, -20, W // 8 + 30, H // 8 + 20), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(40)).resize((W, H))
    img = Image.composite(img, Image.new("RGB", (W, H), shade(base, 0.42)), vig)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 1716, W, 1730), fill=shade(base, 0.3))  # the shelf's edge, with the channel's label
    d.rectangle((0, 1640, W, 1716), fill=(238, 236, 228))
    d.rectangle((0, 1640, W, 1648), fill=(255, 255, 255))
    d.rounded_rectangle((60, 1652, 420, 1706), 8, fill=(255, 214, 0))
    d.text((240, 1680), "PRICE TAG WHY", font=font(36), fill=INK, anchor="mm")
    return img


def rotated(spr: Image.Image, piv: tuple[float, float], ang: float) -> tuple[Image.Image, int]:
    """The sprite turned by `ang` radians around its hole, in a square whose middle is the hole."""
    px, py = piv
    w, h = spr.size
    R = int(max(math.hypot(x - px, y - py) for x, y in ((0, 0), (w, 0), (0, h), (w, h)))) + 2
    c = Image.new("RGBA", (2 * R, 2 * R), (0, 0, 0, 0))
    c.paste(spr, (int(round(R - px)), int(round(R - py))))
    return c.rotate(math.degrees(ang), resample=Image.BICUBIC), R


def child_key(c: dict | None) -> tuple:
    return () if not c else (c.get("kind", "price"), c.get("text"), c.get("draw"), c.get("title"), c.get("color"))


DEFAULT_LEN = dict(person=190, price=170, card=150)


# --------------------------------------------------------------------------- the renderer


class TagRenderer:
    """frame(t) for bw_render.render: the scenes of a "tags" script on one pegboard, in one take."""

    def __init__(self, spec: Spec):
        self.spec = spec
        ensure_fonts()
        from modules.pw_world import WorldRenderer

        self.groups = WorldRenderer.caption_groups(spec.words)
        self.shakes: list[tuple[float, float]] = []  # (when, how hard): a sticker slapped on shakes every tag
        self.sounds: list[tuple[float, str, float]] = []
        self.hangers = self.plan_hangers()
        self.stickers = self.plan_stickers()
        self.signs = self.plan_signs()
        self.boards = self.plan_boards()
        spec.sfx_extra = sorted(self.sounds)

    # -- planning --

    def cue(self, i: int, cue: Any, k: int = 0) -> float:
        return cue_time(self.spec.scenes[i], self.spec.words, cue, k)

    def plan_hangers(self) -> list[dict[str, Any]]:
        scenes, out, alive = self.spec.scenes, [], {}
        for i, scene in enumerate(scenes):
            listed = [h for h in (scene.opts.get("hang") or []) if isinstance(h, dict) and h.get("id")]
            ids = {h["id"] for h in listed}
            for hid in [x for x in alive if x not in ids]:
                alive.pop(hid)["t_out"] = scene.start - 0.14
            if i:
                self.sounds.append((scene.start - 0.1, "whoosh", 0.0))
            for k, h in enumerate(listed):
                if h["id"] in alive:
                    hg = alive[h["id"]]
                    if abs(float(h.get("x", 0.5)) - float(hg["segs"][-1][1].get("x", 0.5))) > 0.02:
                        hg["kicks"].append((scene.start, 0.12 if float(h.get("x", 0.5)) < float(hg["segs"][-1][1].get("x", 0.5)) else -0.12))
                    hg["segs"].append((i, h))
                else:
                    if i == 0 and not h.get("cue"):
                        t_in = -0.75
                    else:
                        t_in = self.cue(i, h["cue"], k) if h.get("cue") else scene.start + 0.05 + 0.1 * k
                        self.sounds.append((t_in + 0.3, "pop", 0.0))
                    n = len(out)
                    hg = dict(id=h["id"], segs=[(i, h)], t_in=t_in, t_out=None, cut_at=None, kicks=[],
                              sgn=1 if n % 2 == 0 else -1, ph=n * 1.7, seed=sum(map(ord, h["id"])))
                    alive[h["id"]] = hg
                    out.append(hg)
                c = h.get("child")
                if isinstance(c, dict):  # its child tag popping out tugs it
                    prev = alive[h["id"]]["segs"][-2][1].get("child") if len(alive[h["id"]]["segs"]) > 1 else None
                    if child_key(prev) != child_key(c):
                        tc = self.child_time(alive[h["id"]], i, c)
                        if tc > 0:
                            alive[h["id"]]["kicks"].append((tc, 0.06))
                            self.sounds.append((tc, "pop", 0.0))
                    if c.get("from") and c.get("land"):
                        self.sounds.append((self.cue(i, c["land"]), "cash", 0.0))
            for kk in scene.opts.get("kick") or []:
                if kk.get("id") in alive:
                    alive[kk["id"]]["kicks"].append((self.cue(i, kk.get("cue")), float(kk.get("k", 0.22))))
            cut = scene.opts.get("cut")
            if isinstance(cut, dict) and cut.get("id") in alive:
                hg = alive.pop(cut["id"])
                hg["cut_at"] = self.cue(i, cut.get("cue"))
                self.sounds += [(hg["cut_at"], "paper", 0.0), (hg["cut_at"] + 0.45, "stamp", 0.0)]
        return out

    def child_time(self, hg: dict, i: int, c: dict) -> float:
        scene = self.spec.scenes[i]
        base = max(scene.start, hg["t_in"] + 0.25)
        return self.cue(i, c["cue"]) if c.get("cue") else (-1.0 if hg["t_in"] < 0 and i == 0 else base)

    def plan_stickers(self) -> list[dict[str, Any]]:
        out = []
        for i, scene in enumerate(self.spec.scenes):
            for k, s in enumerate(scene.opts.get("stickers") or []):
                at = self.cue(i, s.get("cue"), k)
                kind = str(s.get("kind") or "label")
                text = str(s.get("text", ""))
                spr = burst(text, int(s.get("size", 150))) if kind == "burst" else \
                    stamp(text, int(s.get("size", 84))) if kind == "stamp" else label(text, kind, int(s.get("size", 60)))
                out.append(dict(at=at, end=scene.end - 0.1, spr=spr, x=float(s.get("x", 0.5)) * W,
                                y=float(s.get("y", 1180 + 130 * k)), rot=float(s.get("rot", (-4, 3, -2)[k % 3]))))
                self.shakes.append((at + 0.1, 0.05 if kind != "stamp" else 0.08))
                self.sounds.append((at + 0.08, "stamp", 0.0))
        return out

    def plan_signs(self) -> list[tuple[float, str, str]]:
        out = [(-9.0, "PRICE TAG WHY", "")]
        for scene in self.spec.scenes:
            y, p = str(scene.opts.get("year") or ""), str(scene.opts.get("place") or "")
            if y and (y, p) != out[-1][1:]:
                out.append((scene.start, y, p) if scene.start > 0.2 else (-9.0, y, p))
                if scene.start > 0.2:
                    self.sounds.append((scene.start + 0.2, "ding", 0.0))
        return out

    def plan_boards(self) -> list[tuple[float, str]]:
        out, cur = [], "teal"
        for scene in self.spec.scenes:
            cur = str(scene.opts.get("board") or cur)
            out.append((scene.start, cur))
        return out

    # -- motion --

    def theta(self, hg: dict, t: float, L: float) -> float:
        w = max(2.0, 3.4 - L / 250)
        tau = max(0.0, t - hg["t_in"])
        v = 0.17 * hg["sgn"] * math.exp(-0.8 * tau) * math.cos(w * tau) + 0.03 * math.sin(1.7 * t + hg["ph"])
        for tk, K in hg["kicks"]:
            if tk < t < tk + 5:
                v += K * math.exp(-1.5 * (t - tk)) * math.sin(2.1 * w * (t - tk))
        for tk, K in self.shakes:
            if tk < t < tk + 4:
                v += K * hg["sgn"] * math.exp(-1.8 * (t - tk)) * math.sin(2.3 * w * (t - tk))
        return v

    def scene_at(self, t: float) -> int:
        scenes = self.spec.scenes
        for i in range(len(scenes) - 1, -1, -1):
            if t >= scenes[i].start:
                return i
        return 0

    def tag_sprite(self, o: dict, i: int, t: float, shown: float, x: float, seed: int = 0) -> Sprite:
        kind = o.get("kind") or ("card" if o.get("draw") else "price" if "text" in o else "person")
        scale = float(o.get("scale", 1.0))
        if kind == "person":
            expr = str(o.get("expr") or "smile")
            for m in o.get("moods") or []:
                if t >= self.cue(i, m.get("cue")):
                    expr = str(m.get("expr") or expr)
            blink = (t + seed * 0.37) % 3.3 < 0.12
            return person(str(o.get("look", "woman")), str(o.get("name", "")), str(o.get("sub", "")),
                          expr if expr in EXPRESSIONS else "smile", blink, 1 if x < W / 2 else -1, scale)
        if kind == "card":
            return card(str(o["draw"]), str(o.get("title") or o.get("headline") or o.get("label") or o.get("lines") or ""),
                        str(o.get("tint") or o.get("no") or o.get("date") or o.get("color") or o.get("sub") or ""),
                        str(o.get("when") or o.get("paper") or ""), scale)
        text = str(o.get("text", ""))
        if o.get("from") and amount(str(o["from"])) is not None and amount(text) is not None:
            land = self.cue(i, o.get("land")) if o.get("land") else shown + 1.2
            start = max(shown, self.spec.scenes[i].start)
            if t < land:
                text = racing(str(o["from"]), text, ease((t - start) / max(0.2, land - start)) * 0.999)
        return price(text, str(o.get("color", "white")), str(o.get("size", "m")), str(o.get("sub", "")), scale)

    # -- drawing --

    def place(self, hg: dict, t: float, items: list, lines: list, hooks: list) -> None:
        if t < hg["t_in"]:
            return
        scenes = self.spec.scenes
        k = max([n for n, (i, _) in enumerate(hg["segs"]) if scenes[i].start <= t], default=0)
        i, o = hg["segs"][k]
        kind = o.get("kind") or ("card" if o.get("draw") else "price" if "text" in o else "person")
        x, L = float(o.get("x", 0.5)) * W, float(o.get("len", DEFAULT_LEN.get(kind, 170)))
        if k > 0:  # sliding along the rail to its place in this scene
            po = hg["segs"][k - 1][1]
            pk = po.get("kind") or ("card" if po.get("draw") else "price" if "text" in po else "person")
            e = ease((t - scenes[i].start) / 0.5)
            x = float(po.get("x", 0.5)) * W * (1 - e) + x * e
            L = float(po.get("len", DEFAULT_LEN.get(pk, 170))) * (1 - e) + L * e
        spr, piv, bot = self.tag_sprite(o, i, t, hg["t_in"], x, hg["seed"])
        th = self.theta(hg, t, L)
        up = (1 - back((t - hg["t_in"]) / 0.5)) * (L + spr.height + 320)
        if hg["t_out"] is not None and t > hg["t_out"]:
            p = (t - hg["t_out"]) / 0.3
            if p >= 1:
                return
            up += p * p * (L + spr.height * 2 + 500)
        hook = (x, RAIL_Y)
        ang = th * 1.25
        hole = (x + L * math.sin(th), RAIL_Y + L * math.cos(th) - up)
        cut = hg["cut_at"]
        if cut is not None and t > cut:
            u = t - cut
            th0 = self.theta(hg, cut, L)
            s = th0 + 0.3 * math.exp(-2.5 * u) * math.sin(11 * u)
            if t < self.spec.scenes[i].end:
                lines.append((hook, (x + L * 0.42 * math.sin(s), RAIL_Y + L * 0.42 * math.cos(s))))
            hole = (x + L * math.sin(th0) + 70 * hg["sgn"] * u, RAIL_Y + L * math.cos(th0) + 2100 * u * u)
            if hole[1] > H + 700:
                if t < self.spec.scenes[i].end:
                    hooks.append(x)
                return
            ang = th0 + hg["sgn"] * (2.4 * u * u + 0.5 * u)
            a2 = ang + 0.5 * math.sin(9 * u)
            lines.append((hole, (hole[0] - L * 0.5 * math.sin(a2), hole[1] - L * 0.5 * math.cos(a2))))
        elif hole[1] > RAIL_Y + 6:
            lines.append((hook, hole))
        hooks.append(x)
        r, R = rotated(spr, piv, ang)
        items.append((r, hole[0] - R, hole[1] - R))
        c = o.get("child")
        if not isinstance(c, dict):
            return
        prev = hg["segs"][k - 1][1].get("child") if k > 0 else None
        tc = -1.0 if child_key(prev) == child_key(c) else self.child_time(hg, i, c)
        if t < tc:
            return
        grow = back((t - tc) / 0.3)
        nxt = hg["segs"][k + 1] if k + 1 < len(hg["segs"]) else None
        if nxt and child_key(nxt[1].get("child")) != child_key(c):  # untied as the scene changes
            grow *= 1 - ease((t - (scenes[nxt[0]].start - 0.18)) / 0.16)
        if c.get("until") and t > self.cue(i, c["until"]):
            grow *= 1 - ease((t - self.cue(i, c["until"])) / 0.2)
        if grow < 0.05:
            return
        cs, cp, _ = self.tag_sprite({**c, "kind": c.get("kind") or ("card" if c.get("draw") else "price")}, i, t, max(tc, hg["t_in"]), x)
        if grow < 0.99:
            cs = cs.resize((max(1, int(cs.width * grow)), max(1, int(cs.height * grow))), Image.BILINEAR)
            cp = (cp[0] * grow, cp[1] * grow)
        dy = bot[1] - piv[1]
        b = (hole[0] + dy * math.sin(ang), hole[1] + dy * math.cos(ang))
        thc = ang * 0.5 + (self.theta(hg, t - 0.16, L) * 1.6 if cut is None or t <= cut else -0.8 * (t - cut))
        gap = 54 * grow
        ch = (b[0] + gap * math.sin(thc), b[1] + gap * math.cos(thc))
        lines.append((b, ch))
        r2, R2 = rotated(cs, cp, thc * 1.1)
        items.append((r2, ch[0] - R2, ch[1] - R2))

    def belt(self, t: float, items: list, lines: list, hooks: list) -> None:
        for i, scene in enumerate(self.spec.scenes):
            b = scene.opts.get("belt")
            if not isinstance(b, dict) or not (scene.start - 0.2 <= t < scene.end + 0.4):
                continue
            t0 = self.cue(i, b["cue"]) if b.get("cue") else scene.start - 0.8
            spr, piv, _ = card(str(b.get("draw", "box")), str(b.get("label", "")), str(b.get("sub", "")), scale=float(b.get("scale", 0.9)))
            speed, gap, L = float(b.get("speed", 260)), float(b.get("gap", 300)), float(b.get("len", 70))
            up = max(0.0, (t - (scene.end - 0.14)) / 0.3) ** 2 * 900
            for k in range(int(b.get("n", 14))):
                x = W + 140 + k * gap - speed * (t - t0)
                if not -140 < x < W + 140:
                    continue
                th = 0.1 * math.sin(3.1 * t + k * 1.3) - 0.06
                hole = (x + L * math.sin(th), RAIL_Y + L * math.cos(th) - up)
                if up < 60:
                    lines.append(((x, RAIL_Y), hole))
                    hooks.append(x)
                r, R = rotated(spr, piv, th * 1.3)
                items.append((r, hole[0] - R, hole[1] - R))

    def background(self, t: float) -> Image.Image:
        k = max([n for n, (at, _) in enumerate(self.boards) if at - 0.2 <= t], default=0)
        at, name = self.boards[k]
        img = board_image(name)
        if k and self.boards[k - 1][1] != name and t < at + 0.2:
            img = Image.blend(board_image(self.boards[k - 1][1]), img, ease((t - (at - 0.2)) / 0.4))
        else:
            img = img.copy()
        dim = 0.0
        for scene in self.spec.scenes:
            if scene.opts.get("dim"):
                dim = max(dim, min(ease((t - scene.start) / 0.3), 1 - ease((t - scene.end + 0.15) / 0.3)))
        if dim > 0.01:
            img = Image.blend(img, Image.new("RGB", (W, H), (4, 6, 12)), 0.55 * dim)
        return img

    def draw_sign(self, img: Image.Image, t: float) -> None:
        k = max([n for n, s in enumerate(self.signs) if s[0] <= t], default=0)
        at, year, place = self.signs[k]
        p = (t - at) / 0.4
        if p < 0.5 and k > 0:
            _, year, place = self.signs[k - 1]
        squash = max(0.04, abs(1 - 2 * p)) if p < 1 else 1.0
        spr = sign(year, place)
        if squash < 0.99:
            spr = spr.resize((spr.width, max(2, int(spr.height * squash))), Image.BILINEAR)
        d = ImageDraw.Draw(img)
        x0, y0 = int(W / 2 - spr.width / 2), int(118 - spr.height / 2)
        for cx in (x0 + 40, x0 + spr.width - 40):
            d.line((cx, 0, cx, y0 + 6), fill=(30, 30, 36), width=8)
            d.line((cx, 0, cx, y0 + 6), fill=(170, 176, 186), width=4)
        img.paste(spr, (x0, y0), spr)

    def draw_stickers(self, img: Image.Image, t: float) -> None:
        for s in self.stickers:
            if not (s["at"] <= t < s["end"] + 0.14):
                continue
            p = ease((t - s["at"]) / 0.16)
            sc = 1 + 0.9 * (1 - p) + 0.03 * math.exp(-6 * (t - s["at"])) * math.sin(30 * (t - s["at"]))
            a = p * (1 - ease((t - s["end"]) / 0.14))
            spr = s["spr"].rotate(s["rot"], resample=Image.BICUBIC, expand=True)
            if abs(sc - 1) > 0.005:
                spr = spr.resize((max(1, int(spr.width * sc)), max(1, int(spr.height * sc))), Image.BILINEAR)
            mask = spr.getchannel("A") if a > 0.99 else spr.getchannel("A").point(lambda v: int(v * a))
            img.paste(spr, (int(s["x"] - spr.width / 2), int(s["y"] - spr.height / 2)), mask)

    def draw_scissors(self, img: Image.Image, t: float) -> None:
        for hg in self.hangers:
            cut = hg["cut_at"]
            if cut is None or not (cut - 0.55 < t < cut + 0.6):
                continue
            i, o = hg["segs"][-1]
            x, L = float(o.get("x", 0.5)) * W, float(o.get("len", 190))
            left = x < W / 2
            slide = 800 * (1 - ease((t - (cut - 0.55)) / 0.35)) + 900 * ease((t - (cut + 0.2)) / 0.4) ** 2
            a = 0.38 if t < cut - 0.1 else max(0.02, 0.38 * (cut - t) / 0.1)
            spr = scissors(int(round(a * 100)), not left)
            px = x - 170 - slide if left else x + 170 + slide
            img.paste(spr, (int(px - 380), int(RAIL_Y + L * 0.42 - 380)), spr)

    def caption(self, img: Image.Image, t: float) -> None:
        words = self.spec.words
        for gi, g in enumerate(self.groups):
            start = words[g[0]].start - 0.05
            end = words[self.groups[gi + 1][0]].start - 0.05 if gi + 1 < len(self.groups) else words[g[-1]].end + 0.4
            if start <= t < end:
                now = max([k for k in g if words[k].start <= t + 0.02], default=g[0])
                text = " ".join((words[k].text.strip().rstrip(",.:;") or words[k].text).upper() for k in g)
                spr = label(text, "label", 62, g.index(now)).rotate((-1.5, 1.2, -0.6, 1.8)[gi % 4], resample=Image.BICUBIC, expand=True)
                sc = 0.88 + 0.12 * back((t - start) / 0.16)
                if abs(sc - 1) > 0.005:
                    spr = spr.resize((int(spr.width * sc), int(spr.height * sc)), Image.BILINEAR)
                img.paste(spr, (int(W / 2 - spr.width / 2), int(CAPTION_Y - spr.height / 2)), spr)
                return

    def frame(self, t: float) -> Image.Image:
        img = self.background(t)
        self.draw_sign(img, t)
        self.draw_stickers(img, t)
        items: list[tuple[Image.Image, float, float]] = []
        lines: list[tuple[tuple[float, float], tuple[float, float]]] = []
        hooks: list[float] = []
        self.belt(t, items, lines, hooks)
        for hg in self.hangers:
            self.place(hg, t, items, lines, hooks)
        sh = Image.new("L", (W, H), 0)
        sd = ImageDraw.Draw(sh)
        for a, b in lines:
            sd.line((a[0] + 14, a[1] + 20, b[0] + 14, b[1] + 20), fill=150, width=7)
        for spr, x, y in items:
            sh.paste(170, (int(x) + 20, int(y) + 28), spr.getchannel("A"))
        img.paste((2, 14, 18), (0, 0), sh.filter(ImageFilter.GaussianBlur(11)))
        for spr, x, y in items:
            img.paste(spr, (int(round(x)), int(round(y))), spr)
        d = ImageDraw.Draw(img)
        for a, b in lines:
            d.line((a[0], a[1], b[0], b[1]), fill=TWINE_EDGE, width=8)
            d.line((a[0], a[1], b[0], b[1]), fill=TWINE, width=4)
        d.rectangle((0, RAIL_Y - 4, W, RAIL_Y + 24), fill=(6, 20, 24))  # the rail, with its shadow under it
        d.rectangle((0, RAIL_Y - 12, W, RAIL_Y + 10), fill=(196, 202, 212))
        d.rectangle((0, RAIL_Y - 12, W, RAIL_Y - 6), fill=(240, 244, 250))
        d.rectangle((0, RAIL_Y + 6, W, RAIL_Y + 10), fill=(120, 128, 140))
        for hx in hooks:
            d.rounded_rectangle((hx - 15, RAIL_Y - 20, hx + 15, RAIL_Y + 18), 8, fill=(50, 56, 66))
            d.ellipse((hx - 7, RAIL_Y - 8, hx + 7, RAIL_Y + 6), fill=(214, 220, 228))
        self.draw_scissors(img, t)
        self.caption(img, t)
        return img


def render(spec: Spec, voice: Path, out: Path, music: Path | None = None, music_volume: float = 0.08,
           progress=lambda f, m: None) -> Path:
    """bw_render.render with the pegboard renderer (its voice, music, sound effects and encoding)."""
    from modules import bw_fx

    bw_render.align(spec)
    original, look = bw_fx.FxRenderer, spec.look
    bw_fx.FxRenderer = TagRenderer  # bw_render.render builds bw_fx.FxRenderer for the "satellite" look
    spec.look = "satellite"
    try:
        return bw_render.render(spec, voice, out, music, music_volume, progress)
    finally:
        bw_fx.FxRenderer = original
        spec.look = look


CARDS = ("board", "doc", "news", "paper", "box", "bottle", "coin", "machine", "text")
WORDS = (120, 215)  # 50-75 s
SCENE_WORDS = 30


def problems(script: dict) -> list[str]:
    """What a tags script needs before it is drawn: its length, known tag kinds, and cues that are really said."""
    import re

    found = []
    scenes = script.get("scenes") or []
    n = sum(len(str(sc.get("narration") or "").split()) for sc in scenes)
    if not WORDS[0] <= n <= WORDS[1]:
        found.append(f"{n} words of narration (a tags story wants {WORDS[0]}-{WORDS[1]}, about 50-75 s)")
    if scenes and not str(scenes[-1].get("narration") or "").strip().endswith("?"):
        found.append("the last scene asks the viewer a question (its narration ends with ?)")
    for k, sc in enumerate(scenes, 1):
        where = f"scene {k}"
        spoken = [re.sub(r"[^\w$]", "", w.lower()) for w in str(sc.get("narration") or "").split()]
        if not spoken:
            found.append(f"{where}: no narration")
        elif len(spoken) > SCENE_WORDS:
            found.append(f"{where}: {len(spoken)} words (at most {SCENE_WORDS}: split it into two scenes)")
        if sc.get("board") and sc["board"] not in BOARDS:
            found.append(f"{where}: board is one of {', '.join(BOARDS)}")
        cues = [sc.get("cut", {}).get("cue"), sc.get("belt", {}).get("cue")]
        cues += [x.get("cue") for x in (sc.get("stickers") or []) + (sc.get("kick") or [])]
        for h in sc.get("hang") or []:
            for tag in (h, h.get("child") or {}):
                if not tag:
                    continue
                kind = tag.get("kind")
                if kind not in ("person", "price", "card"):
                    found.append(f"{where}: a tag is a person, a price or a card (not {kind})")
                if kind == "person" and tag.get("look", "man") not in LOOKS:
                    found.append(f"{where}: look is one of {', '.join(LOOKS)}")
                if kind == "price" and tag.get("color", "white") not in PRICE_COLORS:
                    found.append(f"{where}: a price's color is one of {', '.join(PRICE_COLORS)}")
                if kind == "card" and tag.get("draw", "text") not in CARDS:
                    found.append(f"{where}: a card's draw is one of {', '.join(CARDS)}")
                moods = tag.get("moods") or []
                if any(m.get("expr") not in EXPRESSIONS for m in [tag] * bool(tag.get("expr")) + moods):
                    found.append(f"{where}: expr is one of {', '.join(EXPRESSIONS)}")
                cues += [tag.get("cue"), tag.get("land")] + [m.get("cue") for m in moods]
        for s in sc.get("stickers") or []:
            if s.get("kind", "label") not in (*STICKERS, "burst", "stamp"):
                found.append(f"{where}: a sticker's kind is label, dark, gold, burst or stamp")
        for cue in cues:
            tok = re.sub(r"[^\w$]", "", str(cue or "").lower())
            if cue and not any(w == tok or (len(tok) > 2 and w.startswith(tok)) for w in spoken):
                found.append(f"{where}: the cue \"{cue}\" is not a word of its narration")
    return found + layout_problems(script)


SCENES = (7, 14)
HANG_MOST = 3  # tags side by side on the rail
HANG_X = {1: (0.5,), 2: (0.27, 0.73), 3: (0.17, 0.5, 0.83)}
HANG_SCALE = {1: 1.3, 2: 1.3, 3: 0.95}  # three in a row overlap when they swing at more than this
STICKER_TEXT = 22  # characters on a sticker
STICKER_Y = (1010, 1260, 1330)  # the first sticker's row: under plain tags, under a price tied below, under a card tied below
STICKER_ROOM = (3, 2, 1)  # label stickers that fit under each
STICKER_STEP = 110
STICKER_LOW = 1340  # lower than this and a sticker sits on the caption
WIDE = ("paper", "news", "board", "doc")  # cards too wide to hang beside another tag at full size


def under(hang: list[dict]) -> int:
    """What is tied under the scene's tags: 0 nothing, 1 a price, 2 a card (the tallest decides the stickers' room)."""
    kids = [h["child"] for h in hang if h.get("child")]
    return 2 if any(c.get("kind") == "card" or c.get("draw") for c in kids) else 1 if kids else 0


def arrange(script: dict) -> dict:
    """A copy of the script with every place left out filled in: where each tag hangs (x, scale, len) and where each
    sticker sticks (x, y). A script from the LLM names what hangs, never where; a hand-placed value is kept."""
    import copy

    out = copy.deepcopy(script)
    for sc in out.get("scenes") or []:
        hang = sc.get("hang") or []
        n = min(max(len(hang), 1), HANG_MOST)
        for k, h in enumerate(hang):
            if "x" in h:  # placed by hand: left as it is
                continue
            h["x"] = HANG_X[n][k] if k < n else 0.5
            h.setdefault("scale", 1.0 if n > 1 and h.get("draw") in WIDE else 1.1 if sc.get("belt") else HANG_SCALE[n])
            # under a belt the tag hangs below the line; the middle of three hangs lower, so they swing clear
            h.setdefault("len", 380 if sc.get("belt") else 210 if n == 3 and k == 1 else 120)
            if h.get("child"):
                h["child"].setdefault("scale", h["scale"])
        if sc.get("belt"):  # small things riding the rail, clear of the tag below them
            for key, value in (("scale", 0.7), ("len", 50), ("gap", 250), ("n", 40)):
                sc["belt"].setdefault(key, value)
        base, free = STICKER_Y[under(hang)], None
        if under(hang) and len(hang) == 2 and not all(h.get("child") for h in hang):
            # one tag has nothing tied under it: the labels go under that one, clear of the other's child
            base, free = STICKER_Y[0], next(h.get("x", 0.5) for h in hang if not h.get("child"))
        row = 0
        round_one = any(st.get("kind") in ("burst", "stamp") for st in sc.get("stickers") or [])
        for st in sc.get("stickers") or []:
            if "x" in st or "y" in st:  # placed by hand
                continue
            if st.get("kind") in ("burst", "stamp"):  # round ones go to the right, beside the labels
                labels = sum(1 for o in sc["stickers"] if o.get("kind") not in ("burst", "stamp"))
                st.setdefault("x", free or 0.83)  # beside a tied tag it goes under the labels, not under the child
                st.setdefault("y", min(base + (STICKER_STEP * labels + 50 if free else 60), STICKER_LOW - 60))
                st.setdefault("size", 140 if st.get("kind") == "burst" else 84)
                st.setdefault("rot", 7 if st.get("kind") == "burst" else -5)
            else:
                st.setdefault("x", free + (0.03 if free > 0.5 else -0.03) if free else 0.36 if round_one else 0.5 if len(str(st.get("text", ""))) > 16 else (0.36, 0.5, 0.4)[row % 3])
                st.setdefault("y", min(base + STICKER_STEP * row, STICKER_LOW))
                row += 1
    return out


def layout_problems(script: dict) -> list[str]:
    """What cannot be laid out: too many tags on the rail, too many stickers for the room under them, long words."""
    found = []
    scenes = script.get("scenes") or []
    if not SCENES[0] <= len(scenes) <= SCENES[1]:
        found.append(f"{len(scenes)} scenes (a tags story has {SCENES[0]}-{SCENES[1]})")
    for k, sc in enumerate(scenes, 1):
        where = f"scene {k}"
        hang = sc.get("hang") or []
        if not hang and not sc.get("belt"):
            found.append(f"{where}: nothing hangs (give it 1-{HANG_MOST} tags in hang, or a belt)")
        if len(hang) > HANG_MOST:
            found.append(f"{where}: {len(hang)} tags on the rail (at most {HANG_MOST}: tie one under another as its child)")
        loose = [s for s in sc.get("stickers") or [] if "y" not in s]
        labels = [s for s in loose if s.get("kind") not in ("burst", "stamp")]
        room = STICKER_ROOM[under(hang)]
        if len(hang) == 2 and not all(h.get("child") for h in hang):
            room = STICKER_ROOM[0]  # beside the child, under the tag that has none
        if len(labels) > room:
            found.append(f"{where}: {len(labels)} label stickers (room for {room} here)")
        if len(loose) - len(labels) > 1:
            found.append(f"{where}: one burst or stamp a scene")
        for s in loose:
            if len(str(s.get("text", "")).replace("|", " ")) > STICKER_TEXT:
                found.append(f"{where}: the sticker \"{s.get('text')}\" is over {STICKER_TEXT} characters")
        for h in hang:
            for tag in (h, h.get("child") or {}):
                if len(str(tag.get("name", ""))) > 12:
                    found.append(f"{where}: the name \"{tag['name']}\" is over 12 characters")
                if tag.get("kind") == "price" and len(str(tag.get("text", ""))) > 14:
                    found.append(f"{where}: the price \"{tag['text']}\" is over 14 characters")
        if sc.get("cut") and sc["cut"].get("id") not in [h.get("id") for h in hang]:
            found.append(f"{where}: cut names the id of a tag hanging in that scene")
    return found


def still(spec: Spec, t: float) -> Image.Image:
    """One frame (tests, the thumbnail): the scenes must be aligned already."""
    return TagRenderer(spec).frame(t)


if __name__ == "__main__":  # a sample from a recorded voice: python -m modules.pw_tags script.json --voice voice.mp3 --out v.mp4
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--voice", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--music", default="")
    ap.add_argument("--stills", default="", help="times, comma separated: save contact sheets instead of the video")
    args = ap.parse_args()
    data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    said = json.loads(Path(args.voice).with_suffix(".words.json").read_text(encoding="utf-8"))
    sp = bw_render.spec_from_dict(arrange(data), [bw_render.Word(w["word"], float(w["start"]), float(w["end"])) for w in said])
    sp.hold, sp.sfx = 0.5, True
    if args.stills:
        bw_render.align(sp)
        rd = TagRenderer(sp)
        times = [k * 2.0 for k in range(int(sp.duration // 2) + 1)] if args.stills == "all" else [float(x) for x in args.stills.split(",")]
        for n in range(0, len(times), 8):
            part = times[n:n + 8]
            sheet = Image.new("RGB", (270 * len(part), 480), (255, 255, 255))
            for k, tt in enumerate(part):
                sheet.paste(rd.frame(tt).resize((270, 480), Image.LANCZOS), (270 * k, 0))
            sheet.save(f"{args.out}-{n // 8}.jpg", quality=88)
            print(part)
    else:
        render(sp, Path(args.voice), Path(args.out), Path(args.music) if args.music else None, 0.09,
               lambda f, m: print(m) if f is not None and int(f * 100) % 10 == 0 else None)
        print("Video ready", args.out)
