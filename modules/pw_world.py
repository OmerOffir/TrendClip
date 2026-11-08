"""Price Tag Why's "story world" look (2026-11-08): true money stories told in a full-colour cartoon world.

Built after the owner asked for a real plan for the channel: every whiteboard Short stopped at the ~1,200-view seed
batch with 0 shares, while The Curious Plot (13.6K subscribers in its first month) tells the same kind of money story
in a warm, full-frame flat world: a city skyline under a sky that changes with the mood (day, sunset, night, dawn),
named cartoon people standing in the street, the year on a badge, the place on a pill, the numbers popping big, and
the words said in a caption pill. This module draws that look for scripts of format "world" (modules.pw_channel).

A world scene (kind "world") may carry, all optional:
    sky      day | sunset | night | dawn
    year     the badge at the top right ("1998"); place: the pill under it ("ATLANTA")
    head     big lines in the sky: [{text, size, color: white|gold|green|red|ink, cue} | {arrow: true}]
    people   [{name, look: sara|woman|man|old|suit|kid, x: 0-1, cue, expr, pose, moods: [{cue, expr, pose}]}]
             expressions: neutral smile grin sad shocked think smug; poses: hang hip up point chin thumb
    prop     {draw: door|scissors|cash|factory|store|tv|magazine|plane|laptop|fax, x, cue, label, scale, lift}
    tags     pills in the sky, stacked: [{text, cue, style: dark|light|gold|green|red, size}]
    stamps   red NO-stamps slammed over the prop: [{text, cue}]
    big      a figure that counts up when said: {text: "$5,000", cue, color}
    bars     horizontal bars growing when said: [{label, value, text, cue, color}]
    grid     a row of small drawings popping one by one: {n, draw, cue}
    set      the whole scene drawn as the story's own thing instead of the street: {draw: board, ...}
             (modules.pw_sets: a board game moving...)
A cue is a word of the scene's narration: the thing appears when the voice says it (else in turn after the start).
The first scene's things are all there on the first frame: the hook, and the thumbnail.

Everything is drawn here with Pillow at twice the size and scaled down (smooth edges); Poppins (SIL OFL) is fetched
into assets/fonts on first use. The frame pipeline (voice, music, sound effects, encoding) is modules.bw_render.render.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from modules import bw_render
from modules.bw_render import FPS, H, W, Scene, Spec

SS = 2  # drawn at twice the size, then scaled down
GROUND = 1250  # where the street meets the buildings: people stand here
CAPTION_Y = 1430
INK = (38, 34, 56)
GOLD = (255, 200, 61)
GREEN = (46, 196, 104)
RED = (232, 62, 62)
WHITE = (255, 255, 255)
SAID = (255, 205, 60)
COLORS = dict(white=WHITE, gold=GOLD, green=GREEN, red=RED, ink=INK)
FADE = 0.18  # the cross-fade between scenes
POP = 0.32  # seconds a thing takes to pop in

FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/"
WEIGHTS = ("SemiBold", "Bold", "ExtraBold", "Black")

SKIES: dict[str, dict[str, Any]] = {
    "day": dict(top=(255, 241, 216), bottom=(255, 214, 168), far=(242, 210, 176), near=(228, 190, 152),
                win=(250, 228, 200), lit=0.0, ground=(236, 204, 160), curb=(218, 184, 140), orb=(255, 204, 84), dark=False),
    "sunset": dict(top=(84, 58, 138), bottom=(255, 152, 94), far=(214, 122, 122), near=(148, 84, 118),
                   win=(255, 204, 140), lit=0.25, ground=(118, 72, 106), curb=(98, 58, 90), orb=(255, 218, 128), dark=True),
    "night": dict(top=(20, 17, 50), bottom=(68, 46, 116), far=(56, 45, 102), near=(36, 29, 74),
                  win=(255, 214, 120), lit=0.45, ground=(40, 33, 76), curb=(30, 25, 60), orb=(242, 238, 222), dark=True),
    "dawn": dict(top=(168, 198, 238), bottom=(255, 226, 196), far=(198, 208, 228), near=(160, 174, 206),
                 win=(236, 240, 250), lit=0.0, ground=(208, 198, 192), curb=(188, 178, 174), orb=(255, 236, 176), dark=False),
}


# --------------------------------------------------------------------------- fonts and small helpers


def ensure_fonts(folder: Path | None = None) -> None:
    """Poppins (SIL Open Font License) into assets/fonts, once."""
    folder = folder or bw_render.FONTS_DIR
    for w in WEIGHTS:
        f = folder / f"Poppins-{w}.ttf"
        if f.is_file():
            continue
        try:
            import requests

            r = requests.get(FONT_URL + f.name, timeout=30)
            r.raise_for_status()
            folder.mkdir(parents=True, exist_ok=True)
            f.write_bytes(r.content)
        except Exception:  # noqa: BLE001 - the channel's bold font still draws the video
            bw_render.logger.warning("Could not download %s", f.name)
    font.cache_clear()


@lru_cache(maxsize=None)
def font(size: int, weight: str = "ExtraBold") -> ImageFont.FreeTypeFont:
    path = bw_render.FONTS_DIR / f"Poppins-{weight}.ttf"
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return bw_render.font(size)


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def back(x: float) -> float:
    """Ease out with a little overshoot: a pop."""
    x = min(1.0, max(0.0, x))
    c = 1.9
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def shrink(img: Image.Image) -> Image.Image:
    return img.resize((max(1, img.width // SS), max(1, img.height // SS)), Image.LANCZOS)


def put(base: Image.Image, sprite: Image.Image, center: tuple[float, float], scale: float = 1.0, alpha: float = 1.0,
        anchor: str = "c", angle: float = 0.0) -> None:
    """Paste an RGBA sprite on the frame: centred on `center` (anchor "b": its bottom middle there), scaled, faded."""
    if alpha <= 0.01 or scale <= 0.02:
        return
    s = sprite
    if abs(scale - 1) > 0.01:
        s = s.resize((max(1, int(s.width * scale)), max(1, int(s.height * scale))), Image.BILINEAR)
    if angle:
        s = s.rotate(angle, resample=Image.BICUBIC, expand=True)
    if alpha < 0.99:
        a = s.getchannel("A").point(lambda v: int(v * alpha))
        s = s.copy()
        s.putalpha(a)
    x, y = center
    top = y - s.height if anchor == "b" else y - s.height / 2
    base.paste(s, (int(round(x - s.width / 2)), int(round(top))), s)


def number_parts(text: str) -> tuple[str, float, str, int] | None:
    """'$1.2 BILLION' -> ('$', 1.2, ' BILLION', 1 decimal): a figure that can count up."""
    m = re.match(r"^([^\d]*)(\d[\d,]*(?:\.\d+)?)(.*)$", text)
    if not m:
        return None
    digits = m.group(2).replace(",", "")
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return m.group(1), float(digits), m.group(3), decimals


SCALES = {"THOUSAND": 1e3, "K": 1e3, "MILLION": 1e6, "M": 1e6, "BILLION": 1e9, "B": 1e9, "TRILLION": 1e12, "T": 1e12}


def amount(text: str) -> float | None:
    """'$1.2 BILLION' -> 1.2e9, '$5,000' -> 5000.0 (None when it isn't a figure)."""
    parts = number_parts(text.strip())
    if parts is None:
        return None
    word = parts[2].strip().upper().split(" ")[0] if parts[2].strip() else ""
    return parts[1] * SCALES.get(word, 1.0)


def racing(frm: str, to: str, u: float) -> str:
    """A figure racing from `frm` to `to` (every digit spinning, faster and faster), e.g. $5,000 -> $1,200,000,000."""
    a, b = amount(frm) or 1.0, amount(to) or 1.0
    u = min(1.0, max(0.0, u))
    v = a * (b / a) ** (u ** 1.5) if a > 0 and b > 0 else a + (b - a) * u
    pre = number_parts(frm)[0] if number_parts(frm) else ""
    return f"{pre}{int(v):,}"


BILL, COIN = (112, 56), 46


@lru_cache(maxsize=64)
def falling(kind: str, angle: int) -> Image.Image:
    """A banknote or a coin for the money rain, turned by `angle` degrees."""
    if kind == "bill":
        w, h = BILL
        img = Image.new("RGBA", ((w + 8) * SS, (h + 8) * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([4 * SS, 4 * SS, (w + 4) * SS, (h + 4) * SS], radius=8 * SS, fill=(120, 204, 140), outline=INK, width=4 * SS)
        d.ellipse([(w / 2 - 12) * SS, (h / 2 - 12) * SS, (w / 2 + 20) * SS, (h / 2 + 20) * SS], fill=(90, 170, 110), outline=INK, width=3 * SS)
    else:
        img = Image.new("RGBA", ((COIN + 8) * SS, (COIN + 8) * SS), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([4 * SS, 4 * SS, (COIN + 4) * SS, (COIN + 4) * SS], fill=GOLD, outline=INK, width=4 * SS)
        d.ellipse([14 * SS, 14 * SS, (COIN - 6) * SS, (COIN - 6) * SS], outline=(214, 150, 30), width=3 * SS)
    return shrink(img).rotate(angle, resample=Image.BICUBIC, expand=True)


def rain(img: Image.Image, t: float, seed: int, n: int = 26) -> None:
    """Banknotes and coins falling and turning over the whole frame (a scene's rain: cash), already falling at t=0."""
    rng = np.random.default_rng(seed)
    for k in range(n):
        x0, y0 = rng.uniform(-40, W + 40), rng.uniform(-200, H)
        speed, spin, sway = rng.uniform(260, 520), rng.uniform(-160, 160), rng.uniform(20, 60)
        kind = "bill" if k % 3 else "coin"
        y = (y0 + speed * t) % (H + 260) - 160
        x = x0 + sway * math.sin(t * 2.4 + k)
        angle = int((rng.uniform(0, 360) + spin * t) // 15 * 15) % 360
        put(img, falling(kind, angle), (x, y))


def counted(text: str, u: float) -> str:
    parts = number_parts(text)
    if parts is None or u >= 1:
        return text
    pre, value, post, decimals = parts
    v = value * ease(u)
    body = f"{v:,.{decimals}f}" if "," in text or decimals else f"{v:.0f}"
    return pre + body + post


# --------------------------------------------------------------------------- sprites


PILLS = dict(dark=((34, 30, 50), WHITE, None), light=(WHITE, INK, INK), gold=(GOLD, INK, INK), green=(GREEN, WHITE, INK),
             red=(RED, WHITE, INK))


@lru_cache(maxsize=512)
def pill(text: str, style: str = "dark", size: int = 44, dot: tuple[int, int, int] | None = None) -> Image.Image:
    fill, ink, edge = PILLS.get(style, PILLS["dark"])
    f = font(size * SS, "ExtraBold")
    l, t, r, b = f.getbbox(text)
    padx, pady = int(size * 0.62 * SS), int(size * 0.34 * SS)
    extra = int(size * 0.55 * SS) if dot else 0
    w, h = r - l + 2 * padx + extra, int(size * 1.0 * SS) + 2 * pady
    img = Image.new("RGBA", (w + 8 * SS, h + 8 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([3 * SS, 6 * SS, w + 3 * SS, h + 6 * SS], radius=h // 2, fill=(0, 0, 0, 60))  # a soft shadow
    d.rounded_rectangle([0, 0, w, h], radius=h // 2, fill=fill, outline=edge, width=4 * SS if edge else 0)
    if dot:
        d.ellipse([padx * 0.7, h / 2 - size * 0.16 * SS, padx * 0.7 + size * 0.32 * SS, h / 2 + size * 0.16 * SS], fill=dot)
    d.text((padx + extra - l, h / 2), text, font=f, fill=ink, anchor="lm")
    return shrink(img)


@lru_cache(maxsize=512)
def big_text(text: str, size: int, color: tuple[int, int, int]) -> Image.Image:
    """A headline figure: filled colour, a dark outline and a drop shadow, reads on any sky."""
    f = font(size * SS, "Black")
    stroke = max(2, int(size * 0.07)) * SS
    l, t, r, b = f.getbbox(text, stroke_width=stroke)
    pad = 10 * SS
    img = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad + 8 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    o = (pad - l, pad - t)
    edge = WHITE if color == INK else INK  # dark words on a light sky get a white edge
    d.text((o[0] + 5 * SS, o[1] + 8 * SS), text, font=f, fill=(0, 0, 0, 90), stroke_width=stroke, stroke_fill=(0, 0, 0, 90))
    d.text(o, text, font=f, fill=color, stroke_width=stroke, stroke_fill=edge)
    return shrink(img)


@lru_cache(maxsize=8)
def arrow(color: tuple[int, int, int] = GOLD) -> Image.Image:
    img = Image.new("RGBA", (90 * SS, 110 * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(30, 0), (60, 0), (60, 58), (84, 58), (45, 104), (6, 58), (30, 58)]
    d.polygon([(x * SS, y * SS) for x, y in pts], fill=color, outline=INK, width=4 * SS)
    return shrink(img)


# people -------------------------------------------------------------------------------------------------------------

LOOKS: dict[str, dict[str, Any]] = {
    "sara": dict(skin=(250, 214, 184), hair=(240, 194, 92), style="long", top=(228, 66, 88), legs=None, dress=True),
    "woman": dict(skin=(214, 160, 122), hair=(58, 40, 36), style="bob", top=(128, 88, 200), legs=(56, 50, 90)),
    "man": dict(skin=(240, 198, 160), hair=(74, 54, 40), style="short", top=(64, 142, 212), legs=(54, 58, 86)),
    "old": dict(skin=(238, 198, 166), hair=(214, 214, 220), style="bald", top=(116, 150, 86), legs=(86, 74, 62), glasses=True),
    "suit": dict(skin=(206, 152, 114), hair=(36, 30, 32), style="short", top=(42, 54, 96), legs=(36, 44, 80), tie=(214, 60, 60)),
    "kid": dict(skin=(246, 206, 172), hair=(120, 70, 40), style="short", top=(255, 170, 60), legs=(60, 90, 160)),
}
EXPRESSIONS = ("neutral", "smile", "grin", "sad", "shocked", "think", "smug")
POSES = ("hang", "hip", "up", "point", "chin", "thumb")
PW, PH, PX, PY = 380, 500, 190, 488  # the person's canvas and where the feet stand in it (1x)
PERSON_SCALE = 1.2  # drawn bigger on screen than in its canvas
PROP_SCALE = 1.1
HEAD = (0, -292)


def arm_points(pose: str, side: int) -> list[tuple[float, float]]:
    """Shoulder, elbow, hand (1x, feet at 0,0); side -1 the left arm on screen, +1 the right one."""
    sx = 54 * side
    shoulder = (sx, -204)
    if pose == "up":
        return [shoulder, (92 * side, -258), (112 * side, -330)]
    if pose == "hip":
        return [shoulder, (98 * side, -160), (58 * side, -122)]
    if pose == "point" and side == 1:
        return [shoulder, (104 * side, -212), (156 * side, -236)]
    if pose == "chin" and side == 1:
        return [shoulder, (74, -148), (30, -214)]
    if pose == "thumb" and side == 1:
        return [shoulder, (96, -170), (120, -224)]
    return [shoulder, (66 * side, -150), (70 * side, -104)]


@lru_cache(maxsize=256)
def person(look: str, expr: str = "smile", pose: str = "hang", blink: bool = False) -> Image.Image:
    lk = LOOKS.get(look, LOOKS["man"])
    img = Image.new("RGBA", (PW * SS, PH * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def P(x: float, y: float) -> tuple[float, float]:
        return ((PX + x) * SS, (PY + y) * SS)

    def box(x0: float, y0: float, x1: float, y1: float) -> list[float]:
        return [*P(x0, y0), *P(x1, y1)]

    line = 4 * SS
    skin, top, hair = lk["skin"], lk["top"], lk["hair"]
    hx, hy = HEAD
    d.ellipse(box(-70, -12, 70, 8), fill=(0, 0, 0, 50))  # the shadow on the street
    if lk["style"] == "long":  # hair behind the head
        d.rounded_rectangle(box(hx - 86, hy - 40, hx + 86, hy + 96), radius=40 * SS, fill=hair, outline=INK, width=line)
    if lk["style"] == "bob":
        d.rounded_rectangle(box(hx - 84, hy - 50, hx + 84, hy + 52), radius=46 * SS, fill=hair, outline=INK, width=line)
    legs = lk.get("legs") or skin
    lw = 16 if lk.get("dress") else 26
    for side in (-1, 1):
        x = 24 * side
        d.rounded_rectangle(box(x - lw / 2, -110, x + lw / 2, -16), radius=6 * SS, fill=legs, outline=INK, width=line)
        d.ellipse(box(x - 26 + 6 * side, -26, x + 26 + 6 * side, -2), fill=INK)
    if lk.get("dress"):
        d.polygon([P(-48, -214), P(48, -214), P(74, -96), P(-74, -96)], fill=top, outline=INK, width=line)
    else:
        d.rounded_rectangle(box(-54, -216, 54, -100), radius=22 * SS, fill=top, outline=INK, width=line)
    if lk.get("tie"):
        d.polygon([P(0, -214), P(10, -196), P(4, -140), P(0, -130), P(-4, -140), P(-10, -196)], fill=lk["tie"], outline=INK, width=2 * SS)
    for side in (-1, 1):  # arms in front of the body
        pts = [P(*p) for p in arm_points(pose, side)]
        d.line(pts, fill=INK, width=30 * SS, joint="curve")
        d.line(pts, fill=top, width=22 * SS, joint="curve")
        hand = arm_points(pose, side)[-1]
        d.ellipse(box(hand[0] - 15, hand[1] - 15, hand[0] + 15, hand[1] + 15), fill=skin, outline=INK, width=line)
        if pose == "thumb" and side == 1:
            d.rounded_rectangle(box(hand[0] - 6, hand[1] - 34, hand[0] + 6, hand[1] - 8), radius=6 * SS, fill=skin, outline=INK, width=3 * SS)
    d.ellipse(box(hx - 76, hy - 80, hx + 76, hy + 74), fill=skin, outline=INK, width=line)  # the bean head
    style = lk["style"]
    if style in ("long", "bob"):
        d.chord(box(hx - 82, hy - 88, hx + 82, hy + 40), 180, 360, fill=hair, outline=INK, width=line)
        d.ellipse(box(hx - 30, hy - 96, hx + 40, hy - 60), fill=hair)
    elif style == "short":
        d.chord(box(hx - 80, hy - 86, hx + 80, hy + 20), 188, 352, fill=hair, outline=INK, width=line)
    elif style == "bald":
        for side in (-1, 1):
            d.ellipse(box(hx + 62 * side - 18, hy - 30, hx + 62 * side + 18, hy + 6), fill=hair, outline=INK, width=3 * SS)
    face(d, P, expr, blink, glasses=bool(lk.get("glasses")))
    return shrink(img)


def face(d: ImageDraw.ImageDraw, P, expr: str, blink: bool, glasses: bool = False) -> None:
    hx, hy = HEAD
    w = 5 * SS

    def box(x0, y0, x1, y1):
        return [*P(hx + x0, hy + y0), *P(hx + x1, hy + y1)]

    for side in (-1, 1):  # cheeks
        d.ellipse(box(42 * side - 13, 16, 42 * side + 13, 30), fill=(255, 150, 150, 140))
    for side in (-1, 1):
        ex = 27 * side
        if blink and expr != "grin":
            d.line([P(hx + ex - 10, hy - 2), P(hx + ex + 10, hy - 2)], fill=INK, width=w)
        elif expr == "grin":
            d.arc(box(ex - 12, -12, ex + 12, 8), 200, 340, fill=INK, width=w)
        elif expr == "shocked":
            d.ellipse(box(ex - 13, -18, ex + 13, 16), fill=WHITE, outline=INK, width=3 * SS)
            d.ellipse(box(ex - 6, -6, ex + 6, 6), fill=INK)
        else:
            dy = -5 if expr == "think" else 0
            d.ellipse(box(ex - 8, -12 + dy, ex + 8, 10 + dy), fill=INK)
            d.ellipse(box(ex - 1, -9 + dy, ex + 5, -3 + dy), fill=WHITE)
        brow_y = -30 if expr != "shocked" else -40
        if expr == "sad":
            d.line([P(hx + ex - 12 * side, hy + brow_y - 6), P(hx + ex + 10 * side, hy + brow_y + 2)], fill=INK, width=w)
        elif expr == "think" and side == 1:
            d.line([P(hx + ex - 12, hy + brow_y - 8), P(hx + ex + 12, hy + brow_y - 12)], fill=INK, width=w)
        elif expr == "smug":
            d.line([P(hx + ex - 12, hy + brow_y + 2), P(hx + ex + 12, hy + brow_y + 2)], fill=INK, width=w)
        else:
            d.arc(box(ex - 12, brow_y - 4, ex + 12, brow_y + 10), 200, 340, fill=INK, width=w)
        if glasses:
            d.ellipse(box(ex - 17, -17, ex + 17, 17), outline=INK, width=3 * SS)
    if glasses:
        d.line([P(hx - 10, hy), P(hx + 10, hy)], fill=INK, width=3 * SS)
    if expr == "grin":
        d.chord(box(-26, 18, 26, 54), 0, 180, fill=(150, 36, 58), outline=INK, width=w)
        d.chord(box(-14, 34, 14, 54), 200, 340, fill=(240, 110, 120))
    elif expr == "smile":
        d.arc(box(-20, 14, 20, 40), 20, 160, fill=INK, width=w)
    elif expr == "sad":
        d.arc(box(-18, 34, 18, 54), 200, 340, fill=INK, width=w)
    elif expr == "shocked":
        d.ellipse(box(-12, 28, 12, 56), fill=(150, 36, 58), outline=INK, width=w)
    elif expr == "think":
        d.line([P(hx - 10, hy + 40), P(hx + 14, hy + 36)], fill=INK, width=w)
    elif expr == "smug":
        d.arc(box(-6, 16, 26, 40), 20, 150, fill=INK, width=w)
    else:
        d.arc(box(-12, 24, 12, 40), 20, 160, fill=INK, width=w)


# props -------------------------------------------------------------------------------------------------------------

PROPS = ("door", "scissors", "cash", "factory", "store", "tv", "magazine", "plane", "laptop", "fax")


def _label(d: ImageDraw.ImageDraw, text: str, center: tuple[float, float], width: float, color=INK, size: int = 34) -> None:
    """Words written on a prop, wrapped to `width` (in canvas pixels, already at SS)."""
    if not text:
        return
    f = font(size * SS, "ExtraBold")
    words, lines, cur = text.split(), [], ""
    for wd in words:
        test = (cur + " " + wd).strip()
        if f.getlength(test) <= width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = wd
    lines.append(cur)
    while size > 14 and max(f.getlength(x) for x in lines) > width:
        size -= 2
        f = font(size * SS, "ExtraBold")
    lh = size * SS * 1.15
    y0 = center[1] - lh * (len(lines) - 1) / 2
    for k, ln in enumerate(lines):
        d.text((center[0], y0 + k * lh), ln, font=f, fill=color, anchor="mm")


@lru_cache(maxsize=64)
def prop(name: str, label: str = "") -> Image.Image:
    """A flat cartoon object, 1x about 340 x 360, standing on its bottom edge."""
    cw, ch = 400, 400
    img = Image.new("RGBA", (cw * SS, ch * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = SS
    lw = 5 * s

    def B(x0, y0, x1, y1):
        return [x0 * s, y0 * s, x1 * s, y1 * s]

    if name == "door":
        d.rectangle(B(90, 20, 310, 396), fill=(150, 140, 160), outline=INK, width=lw)
        d.rounded_rectangle(B(110, 40, 290, 396), radius=8 * s, fill=(176, 102, 62), outline=INK, width=lw)
        for y in (70, 230):
            d.rounded_rectangle(B(134, y, 266, y + 130), radius=6 * s, outline=(130, 72, 40), width=4 * s)
        d.ellipse(B(254, 214, 278, 238), fill=GOLD, outline=INK, width=3 * s)
    elif name.startswith("scissors"):
        shut = int(name[8:] or 0) / 4  # scissors0..4: open .. closed (the snip)
        for side, blade in ((1, (226, 232, 240)), (-1, (206, 214, 226))):
            a = math.radians(side * 26 * shut)  # each piece turns about the pivot: its blade in, its handle out

            def R(x, y, a=a):
                dx, dy = x - 200, y - 210
                return ((200 + dx * math.cos(a) - dy * math.sin(a)) * s, (210 + dx * math.sin(a) + dy * math.cos(a)) * s)

            tip = 330 if side == -1 else 70
            d.polygon([R(200, 210), R(tip, 40), R(tip + 18 * (1 if side == -1 else -1), 52), R(200 + 22 * (1 if side == -1 else -1), 222)],
                      fill=blade, outline=INK, width=lw)
            hx = 130 if side == -1 else 270
            cx, cy = R(hx, 292)
            d.ellipse([cx - 62 * s, cy - 62 * s, cx + 62 * s, cy + 62 * s], fill=RED, outline=INK, width=lw)
            d.ellipse([cx - 32 * s, cy - 30 * s, cx + 32 * s, cy + 30 * s], fill=(0, 0, 0, 0), outline=INK, width=lw)
        d.ellipse(B(188, 198, 212, 222), fill=GOLD, outline=INK, width=3 * s)
    elif name == "cash":
        for k in range(5):
            y = 300 - k * 46
            d.rounded_rectangle(B(60 + k * 6, y, 340 + k * 6, y + 90), radius=10 * s, fill=(120, 204, 140), outline=INK, width=lw)
            d.ellipse(B(170 + k * 6, y + 22, 230 + k * 6, y + 68), outline=(40, 130, 70), width=4 * s)
        d.rectangle(B(170 + 24, 116, 230 + 24, 400), fill=(255, 240, 200), outline=INK, width=4 * s)
        _label(d, "$", ((200 + 24) * s, 300 * s), 50 * s, size=40)
    elif name == "factory":
        d.rectangle(B(250, 40, 300, 200), fill=(150, 120, 120), outline=INK, width=lw)
        for k, (x, y, r) in enumerate(((290, 30, 26), (318, 4, 20))):
            d.ellipse(B(x - r, y - r, x + r, y + r), fill=(236, 236, 240), outline=INK, width=3 * s)
        d.polygon([(30 * s, 396 * s), (30 * s, 180 * s), (120 * s, 120 * s), (120 * s, 180 * s), (210 * s, 120 * s),
                   (210 * s, 180 * s), (300 * s, 120 * s), (370 * s, 180 * s), (370 * s, 396 * s)],
                  fill=(206, 120, 96), outline=INK, width=lw)
        for x in (70, 170, 270):
            d.rectangle(B(x, 230, x + 60, 290), fill=(255, 226, 150), outline=INK, width=4 * s)
        d.rectangle(B(160, 320, 240, 396), fill=(120, 80, 70), outline=INK, width=4 * s)
        if label:
            d.rounded_rectangle(B(100, 186, 300, 226), radius=8 * s, fill=WHITE, outline=INK, width=3 * s)
            _label(d, label, (200 * s, 206 * s), 180 * s, size=26)
    elif name == "store":
        d.rectangle(B(40, 150, 360, 396), fill=(255, 240, 220), outline=INK, width=lw)
        for k in range(8):
            d.rectangle(B(30 + k * 42.5, 110, 30 + (k + 1) * 42.5, 170), fill=RED if k % 2 == 0 else WHITE, outline=INK, width=3 * s)
        d.rectangle(B(60, 30, 340, 106), fill=(34, 30, 50), outline=INK, width=lw)
        _label(d, label or "STORE", (200 * s, 68 * s), 260 * s, color=WHITE, size=36)
        d.rectangle(B(70, 200, 190, 320), fill=(180, 220, 240), outline=INK, width=4 * s)
        d.rectangle(B(220, 220, 320, 396), fill=(140, 96, 70), outline=INK, width=4 * s)
    elif name == "tv":
        d.line([(150 * s, 60 * s), (110 * s, 4 * s)], fill=INK, width=6 * s)
        d.line([(250 * s, 60 * s), (290 * s, 4 * s)], fill=INK, width=6 * s)
        d.rounded_rectangle(B(20, 60, 380, 330), radius=26 * s, fill=(70, 60, 90), outline=INK, width=lw)
        d.rounded_rectangle(B(44, 84, 356, 306), radius=16 * s, fill=(255, 236, 170), outline=INK, width=4 * s)
        for cx, cy, r in ((90, 120, 18), (320, 130, 14), (300, 270, 12)):
            star(d, cx * s, cy * s, r * s, GOLD)
        _label(d, label, (200 * s, 196 * s), 260 * s, size=40)
        d.rectangle(B(150, 330, 250, 380), fill=(70, 60, 90), outline=INK, width=lw)
        d.rectangle(B(110, 376, 290, 396), fill=(70, 60, 90), outline=INK, width=lw)
    elif name == "magazine":
        d.rectangle(B(70, 10, 340, 396), fill=(250, 250, 252), outline=INK, width=lw)
        d.rectangle(B(70, 10, 340, 80), fill=RED, outline=INK, width=lw)
        _label(d, "MAGAZINE", (205 * s, 45 * s), 230 * s, color=WHITE, size=34)
        d.ellipse(B(130, 210, 280, 360), fill=(250, 214, 184), outline=INK, width=4 * s)
        d.chord(B(122, 196, 288, 330), 180, 360, fill=(240, 194, 92), outline=INK, width=4 * s)
        for ex in (178, 232):
            d.ellipse(B(ex - 7, 282, ex + 7, 300), fill=INK)
        d.arc(B(180, 300, 230, 336), 20, 160, fill=INK, width=4 * s)
        _label(d, label, (205 * s, 145 * s), 240 * s, size=30)
    elif name == "plane":
        d.polygon([(40 * s, 230 * s), (300 * s, 200 * s), (380 * s, 236 * s), (300 * s, 270 * s), (40 * s, 262 * s)],
                  fill=WHITE, outline=INK, width=lw)
        d.polygon([(150 * s, 236 * s), (250 * s, 236 * s), (170 * s, 330 * s), (120 * s, 330 * s)], fill=(90, 150, 230), outline=INK, width=lw)
        d.polygon([(50 * s, 236 * s), (40 * s, 160 * s), (80 * s, 160 * s), (110 * s, 232 * s)], fill=(90, 150, 230), outline=INK, width=lw)
        for x in range(130, 300, 30):
            d.ellipse(B(x, 220, x + 16, 236), fill=(120, 190, 250), outline=INK, width=2 * s)
    elif name == "laptop":
        d.rounded_rectangle(B(60, 70, 340, 300), radius=14 * s, fill=(70, 70, 90), outline=INK, width=lw)
        d.rectangle(B(80, 90, 320, 280), fill=(220, 236, 255))
        _label(d, label, (200 * s, 185 * s), 220 * s, size=40)
        d.polygon([(20 * s, 330 * s), (380 * s, 330 * s), (340 * s, 300 * s), (60 * s, 300 * s)], fill=(160, 160, 176), outline=INK, width=lw)
    else:  # fax
        d.rectangle(B(130, 40, 270, 170), fill=WHITE, outline=INK, width=4 * s)
        for y in (80, 110, 140):
            d.line([(150 * s, y * s), (250 * s, y * s)], fill=(160, 160, 176), width=4 * s)
        d.rounded_rectangle(B(50, 160, 350, 380), radius=24 * s, fill=(206, 210, 222), outline=INK, width=lw)
        d.rectangle(B(80, 200, 220, 260), fill=(160, 200, 170), outline=INK, width=4 * s)
        for k in range(9):
            x, y = 250 + (k % 3) * 28, 200 + (k // 3) * 28
            d.rectangle(B(x, y, x + 18, y + 18), fill=WHITE, outline=INK, width=2 * s)
    return shrink(img)


def star(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color) -> None:
    pts = []
    for k in range(10):
        a = -math.pi / 2 + k * math.pi / 5
        rr = r if k % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=color, outline=INK, width=max(2, int(r / 6)))


@lru_cache(maxsize=16)
def stamp(text: str) -> Image.Image:
    f = font(70 * SS, "Black")
    l, t, r, b = f.getbbox(text)
    pad = 22 * SS
    img = Image.new("RGBA", (r - l + 2 * pad, b - t + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4 * SS, 4 * SS, img.width - 4 * SS, img.height - 4 * SS], radius=14 * SS, fill=(255, 255, 255, 200),
                        outline=RED, width=8 * SS)
    d.text((pad - l, pad - t), text, font=f, fill=RED)
    return shrink(img)


# --------------------------------------------------------------------------- the world behind


@lru_cache(maxsize=16)
def backdrop(sky: str, seed: int) -> Image.Image:
    """The sky, sun or moon, two rows of buildings and the street: wider than the frame, so the camera can drift."""
    pal = SKIES.get(sky, SKIES["day"])
    wide = W + 120
    rng = np.random.default_rng(seed)
    yy = np.linspace(0, 1, GROUND)[:, None, None]
    grad = np.array(pal["top"], np.float32) * (1 - yy) + np.array(pal["bottom"], np.float32) * yy
    sky_img = np.repeat(grad, wide, axis=1)
    img = Image.new("RGB", (wide, H), pal["ground"])
    img.paste(Image.fromarray(np.clip(sky_img, 0, 255).astype(np.uint8)), (0, 0))
    big = Image.new("RGBA", (wide * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    if pal["dark"] and sky == "night":
        for _ in range(70):
            x, y, r = rng.uniform(0, wide), rng.uniform(40, GROUND - 400), rng.uniform(1.5, 3.5)
            d.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=(255, 255, 240, int(rng.uniform(120, 230))))
    ox, oy, orr = (wide * 0.8, GROUND - 560, 85) if sky != "sunset" else (wide * 0.5, GROUND - 330, 150)  # low, under the words
    for k, a in ((1.9, 30), (1.45, 50), (1.0, 255)):
        d.ellipse([(ox - orr * k) * SS, (oy - orr * k) * SS, (ox + orr * k) * SS, (oy + orr * k) * SS], fill=(*pal["orb"], a))
    for row, (hmin, hmax, color) in enumerate(((300, 640, pal["far"]), (160, 430, pal["near"]))):
        x = -rng.uniform(0, 60)
        while x < wide:
            bw = rng.uniform(80, 170)
            bh = rng.uniform(hmin, hmax)
            x0, x1, y0 = x, x + bw, GROUND - bh
            d.rectangle([x0 * SS, y0 * SS, x1 * SS, GROUND * SS], fill=color)
            roof = rng.random()
            if roof < 0.2:
                d.rectangle([(x0 + bw * 0.45) * SS, (y0 - 50) * SS, (x0 + bw * 0.5) * SS, y0 * SS], fill=color)
            elif roof < 0.45:
                d.rectangle([(x0 + bw * 0.18) * SS, (y0 - 34) * SS, (x1 - bw * 0.18) * SS, y0 * SS], fill=color)
            if row == 1 or pal["lit"]:
                win = pal["win"] if row == 1 else tuple(int(c * 0.9) for c in pal["win"])
                for wy in np.arange(y0 + 26, GROUND - 40, 40):
                    for wx in np.arange(x0 + 16, x1 - 22, 30):
                        if pal["lit"] and rng.random() > pal["lit"] + (0.2 if row == 1 else 0):
                            continue
                        if not pal["lit"] and row == 0:
                            continue
                        d.rectangle([wx * SS, wy * SS, (wx + 12) * SS, (wy + 18) * SS], fill=(*win, 255 if pal["lit"] else 150))
            x = x1 + rng.uniform(-14, 10)
    d.rectangle([0, GROUND * SS, wide * SS, (GROUND + 26) * SS], fill=pal["curb"])
    d.rectangle([0, (GROUND + 26) * SS, wide * SS, H * SS], fill=pal["ground"])
    for k in range(0, wide, 160):  # paving lines
        d.line([(k * SS, (GROUND + 26) * SS), ((k - 120) * SS, (GROUND + 200) * SS)], fill=(*pal["curb"], 120), width=3 * SS)
    img.paste(shrink(big), (0, 0), shrink(big))
    return img


# --------------------------------------------------------------------------- the renderer


def cue_time(scene: Scene, words: list[bw_render.Word], cue: Any, k: int, start_at: float | None = None) -> float:
    """When the voice says `cue` in this scene (its first word starting so), else a moment after the start."""
    if cue not in (None, ""):
        tok = bw_render._tok(str(cue))
        for w in words:
            if scene.start - 0.05 <= w.start < scene.end and tok:
                said = bw_render._tok(w.text)
                if said == tok or (len(tok) > 2 and said.startswith(tok)):
                    return w.start
    base = scene.start if start_at is None else start_at
    return min(base + 0.25 + 0.45 * k, max(scene.start, scene.end - 0.6))


class WorldRenderer:
    """frame(t) for bw_render.render: the scenes of a "world" script, with a caption pill and cross-fades."""

    def __init__(self, spec: Spec):
        self.spec = spec
        ensure_fonts()
        self.items: list[list[dict[str, Any]]] = [self.plan(i, s) for i, s in enumerate(spec.scenes)]
        from modules import pw_sets  # set pieces: a scene drawn as the story's own thing (a board game...)

        self.sets = {i: pw_sets.SETS[s.opts["set"]["draw"]](s.opts["set"], s, spec.words) for i, s in enumerate(spec.scenes)
                     if isinstance(s.opts.get("set"), dict) and s.opts["set"].get("draw") in pw_sets.SETS}
        self.groups = self.caption_groups(spec.words)
        spec.sfx_extra = self.sounds()

    # -- planning: when each thing appears --

    def plan(self, i: int, scene: Scene) -> list[dict[str, Any]]:
        o, words, first = scene.opts, self.spec.words, i == 0
        items: list[dict[str, Any]] = []

        def at(cue, k):
            return -1.0 if first and not cue else cue_time(scene, words, cue, k)

        prev_names = {p.get("name") for p in (self.spec.scenes[i - 1].opts.get("people") or [])} if i else set()
        for k, p in enumerate(o.get("people") or []):
            shown = -1.0 if (first or (p.get("name") in prev_names and not p.get("cue"))) else at(p.get("cue"), k)
            moods = [{"at": cue_time(scene, words, m.get("cue"), j), "expr": m.get("expr", "smile"), "pose": m.get("pose", "hang")}
                     for j, m in enumerate(p.get("moods") or [])]
            items.append({"type": "person", "at": shown, "p": p, "moods": sorted(moods, key=lambda m: m["at"]),
                          "seed": sum(map(ord, str(p.get("name", "")))) % 97})
        if isinstance(o.get("prop"), dict):
            items.append({"type": "prop", "at": at(o["prop"].get("cue"), 1), "o": o["prop"]})
        y = 170.0 if isinstance(o.get("set"), dict) else 280.0  # a set piece fills the middle: its words go higher
        for k, h in enumerate(o.get("head") or []):
            size = int(h.get("size", 96))
            if h.get("arrow"):
                items.append({"type": "arrow", "at": -1.0 if first else at(h.get("cue"), k), "y": y + 50})
                y += 112
                continue
            color = COLORS.get(str(h.get("color") or ("white" if SKIES.get(o.get("sky", "day"), SKIES["day"])["dark"] else "ink")), WHITE)
            item = {"type": "head", "at": -1.0 if first and not h.get("cue") else at(h.get("cue"), k), "y": y + size * 0.62,
                    "text": str(h["text"]), "size": size, "color": color}
            if h.get("from") and amount(str(h["from"])) and amount(str(h["text"])):  # races up from `from`, lands on its cue
                item.update(at=-1.0 if first else scene.start, start=scene.start, land=cue_time(scene, words, h.get("cue"), 2),
                            frm=str(h["from"]))
            items.append(item)
            y += size * 1.15
        if isinstance(o.get("big"), dict):
            b = o["big"]
            items.append({"type": "big", "at": at(b.get("cue"), 0), "y": y + 110, "text": str(b["text"]),
                          "color": COLORS.get(str(b.get("color") or "gold"), GOLD)})
            y += 240
        for k, b in enumerate(o.get("bars") or []):
            items.append({"type": "bar", "at": at(b.get("cue"), k), "y": y + 70, "b": b,
                          "max": max(float(x.get("value") or 1) for x in o["bars"])})
            y += 190
        if isinstance(o.get("grid"), dict):
            g = o["grid"]
            items.append({"type": "grid", "at": at(g.get("cue"), 0), "y": y + 90, "g": g})
            y += 210
        for k, tg in enumerate(o.get("tags") or []):
            size = int(tg.get("size", 46))
            items.append({"type": "tag", "at": at(tg.get("cue"), k), "y": y + size * 0.9, "text": str(tg["text"]),
                          "style": str(tg.get("style") or "dark"), "size": size})
            y += size * 1.9
        for k, st in enumerate(o.get("stamps") or []):
            items.append({"type": "stamp", "at": at(st.get("cue"), k), "text": str(st.get("text") or "NO"), "k": k})
        return items

    def sounds(self) -> list[tuple[float, str, float]]:
        found: list[tuple[float, str, float]] = []
        prev_year = None
        for i, scene in enumerate(self.spec.scenes):
            if i > 0:
                found.append((scene.start, "whoosh", 0.0))
            year = scene.opts.get("year")
            if year and year != prev_year and i > 0:
                found.append((scene.start + 0.08, "stamp", 0.0))
            prev_year = year
            if i in self.sets:
                found += self.sets[i].sounds()
            for it in self.items[i]:
                if "land" in it:
                    found.append((it["land"], "cash", 0.0))
                if it["at"] < 0.05:
                    continue
                if it["type"] == "stamp":
                    found.append((it["at"], "stamp", 0.0))
                elif it["type"] in ("big", "bar"):
                    found.append((it["at"] + 0.75, "cash", 0.0))
                else:
                    found.append((it["at"], "pop", 0.0))
        return found

    # -- captions --

    @staticmethod
    def caption_groups(words: list[bw_render.Word]) -> list[list[int]]:
        groups, cur = [], []
        for k, w in enumerate(words):
            cur.append(k)
            nxt = words[k + 1] if k + 1 < len(words) else None
            if len(cur) >= 3 or nxt is None or nxt.start - w.end > 0.28 or w.text.rstrip().endswith((".", "?", "!", ",", ":")):
                groups.append(cur)
                cur = []
        return groups

    def caption(self, img: Image.Image, t: float) -> None:
        words = self.spec.words
        for gi, g in enumerate(self.groups):
            start = words[g[0]].start - 0.05
            end = words[self.groups[gi + 1][0]].start - 0.05 if gi + 1 < len(self.groups) else words[g[-1]].end + 0.4
            if start <= t < end:
                now = max([k for k in g if words[k].start <= t + 0.02], default=g[0])
                put(img, caption_pill(tuple(words[k].text for k in g), g.index(now)), (W / 2, CAPTION_Y),
                    scale=0.9 + 0.1 * back((t - start) / 0.16))
                return

    # -- drawing --

    def scene_image(self, i: int, t: float) -> Image.Image:
        scene = self.spec.scenes[i]
        o = scene.opts
        sky = o.get("sky", "day") if o.get("sky") in SKIES else "day"
        dur = max(0.5, scene.end - scene.start)
        u = min(1.0, max(0.0, (t - scene.start) / dur))
        if i in self.sets:
            img = self.sets[i].draw(t)
        else:
            back_img = backdrop(sky, (self.spec.seed or 7) + i)
            drift = int(120 * ease(u) * (1 if i % 2 == 0 else -1))
            x0 = 60 + drift // 2
            img = back_img.crop((x0, 0, x0 + W, H))
        dark = SKIES[sky]["dark"]
        if o.get("rain"):
            rain(img, t - scene.start, (self.spec.seed or 7) + i)
        for it in self.items[i]:
            self.draw_item(img, it, t, dark, i)
        self.top_bar(img, scene)
        z = 1 + 0.035 * ease(u)  # a slow push in over the scene
        if z > 1.002:
            zw, zh = int(W * z), int(H * z)
            img = img.resize((zw, zh), Image.BILINEAR).crop(((zw - W) // 2, (zh - H) // 2, (zw - W) // 2 + W, (zh - H) // 2 + H))
        return img

    def top_bar(self, img: Image.Image, scene: Scene) -> None:
        brand = pill("PRICE TAG WHY", "dark", 26, dot=GOLD)
        put(img, brand, (40 + brand.width / 2, 70))
        if scene.opts.get("year"):
            yb = pill(str(scene.opts["year"]), "gold", 34)
            put(img, yb, (W - 40 - yb.width / 2, 72))
        if scene.opts.get("place"):
            pl = pill(str(scene.opts["place"]), "dark", 34)
            put(img, pl, (W - 40 - pl.width / 2, 160) if scene.opts.get("year") else (W - 40 - pl.width / 2, 72))

    def pop(self, it: dict[str, Any], t: float) -> tuple[float, float]:
        """(scale, alpha) of a thing popping in at it["at"]."""
        if it["at"] < 0:
            return 1.0, 1.0
        dt = t - it["at"]
        if dt < 0:
            return 0.0, 0.0
        return 0.35 + 0.65 * back(dt / POP), min(1.0, dt / 0.12)

    def draw_item(self, img: Image.Image, it: dict[str, Any], t: float, dark: bool, i: int) -> None:
        kind = it["type"]
        s, a = self.pop(it, t)
        if a <= 0:
            return
        if kind == "person":
            self.draw_person(img, it, t, s, a)
        elif kind == "prop":
            o = it["o"]
            name = str(o.get("draw") or "fax")
            name = name if name in PROPS else "fax"
            if name == "scissors":  # snipping, two snips a second
                name = "scissors%d" % round(4 * abs(math.sin(t * math.pi * 2)))
            sp = prop(name, str(o.get("label") or ""))
            sc = float(o.get("scale") or 1.0) * PROP_SCALE
            bob = 6 * math.sin(t * 2.2) if o.get("lift") else 0.0
            put(img, sp, (W * float(o.get("x", 0.72)), GROUND + 6 - float(o.get("lift") or 0) + bob), scale=sc * s, alpha=a, anchor="b")
        elif kind == "head":
            text, pulse = it["text"], 1.0
            if "land" in it:
                if t < it["land"]:
                    text = racing(it["frm"], it["text"], (t - it["start"]) / max(0.3, it["land"] - it["start"]))
                else:  # landed: a punch of size, then still
                    dt = t - it["land"]
                    pulse = 1 + 0.18 * math.sin(math.pi * min(1.0, dt / 0.35)) * (dt < 0.35)
            sp = big_text(text, it["size"], it["color"])
            fit = min(1.0, (W - 70) / sp.width)
            put(img, sp, (W / 2, it["y"]), scale=s * fit * pulse, alpha=a)
        elif kind == "arrow":
            put(img, arrow(), (W / 2, it["y"] + 9 * math.sin(t * 7)), scale=s, alpha=a)
        elif kind == "big":
            u = (t - it["at"]) / 0.75 if it["at"] >= 0 else 1.0
            put(img, big_text(counted(it["text"], u), 150, it["color"]), (W / 2, it["y"]), scale=s, alpha=a)
        elif kind == "tag":
            put(img, pill(it["text"], it["style"], it["size"]), (W / 2, it["y"]), scale=s, alpha=a)
        elif kind == "stamp":
            spots = ((0.66, 0.50), (0.80, 0.56), (0.72, 0.64))
            sx, sy = spots[it["k"] % 3]
            dt = t - it["at"]
            slam = 1.0 if dt > 0.12 else 1.7 - 0.7 * (dt / 0.12)
            put(img, stamp(it["text"]), (W * sx, H * sy), scale=slam, alpha=a, angle=(-14, 10, -6)[it["k"] % 3])
        elif kind == "bar":
            self.draw_bar(img, it, t, dark)
        elif kind == "grid":
            g = it["g"]
            n = max(1, min(12, int(g.get("n", 5))))
            small = prop(str(g.get("draw") or "store") if str(g.get("draw")) in PROPS else "store", "")
            per = min(n, 7)
            step = (W - 120) / per
            for k in range(n):
                sub = {"at": it["at"] + 0.12 * k if it["at"] >= 0 else -1.0}
                ks, ka = self.pop(sub, t)
                row, col = divmod(k, per)
                put(img, small, (60 + step * (col + 0.5), it["y"] + row * 150), scale=0.36 * ks, alpha=ka)

    def draw_person(self, img: Image.Image, it: dict[str, Any], t: float, s: float, a: float) -> None:
        p = it["p"]
        expr, pose, since = p.get("expr", "smile"), p.get("pose", "hang"), -9.0
        for m in it["moods"]:
            if m["at"] <= t:
                expr, pose, since = m["expr"], m["pose"], m["at"]
        expr = expr if expr in EXPRESSIONS else "smile"
        pose = pose if pose in POSES else "hang"
        seed = it["seed"]
        per = 2.3 + (seed % 7) * 0.2
        blink = (t + seed * 0.37) % per < 0.12
        hop = -26 * math.sin(math.pi * min(1.0, (t - since) / 0.36)) if 0 <= t - since < 0.36 else 0.0
        sway = 3 * math.sin(t * 2.0 + seed)
        x = W * float(p.get("x", 0.3))
        k = PERSON_SCALE
        put(img, person(str(p.get("look") or "man"), expr, pose, blink), (x + sway * 0.5, GROUND + 14 + hop + (PH - PY) * k),
            scale=s * k, alpha=a, anchor="b")
        if p.get("name"):
            put(img, pill(str(p["name"]), "dark", 32), (x, GROUND + 14 + hop - (372 * k + 42) * s), scale=s, alpha=a)

    def draw_bar(self, img: Image.Image, it: dict[str, Any], t: float, dark: bool) -> None:
        b = it["b"]
        dt = t - it["at"] if it["at"] >= 0 else 9.0
        if dt < 0:
            return
        grow = ease(dt / 0.75)
        full = 820 * float(b.get("value") or 1) / max(1e-9, it["max"])
        x0, y = 130, it["y"]
        lab = pill(str(b.get("label") or ""), "light", 28)
        put(img, lab, (x0 + lab.width / 2, y - 62))
        d = ImageDraw.Draw(img)
        w = max(30.0, full * grow)
        color = COLORS.get(str(b.get("color") or "gold"), GOLD)
        d.rounded_rectangle([x0, y - 34, x0 + w, y + 34], radius=18, fill=color, outline=INK, width=4)
        if grow > 0.2:
            txt = big_text(counted(str(b.get("text") or ""), dt / 0.75), 54, WHITE)
            inside = w > txt.width + 40
            put(img, txt, (x0 + w - txt.width / 2 - 18 if inside else x0 + w + txt.width / 2 + 16, y))

    def frame(self, t: float) -> Image.Image:
        scenes = self.spec.scenes
        i = next((k for k, s in enumerate(scenes) if s.start <= t < s.end), len(scenes) - 1)
        img = self.scene_image(i, t)
        if i > 0 and t - scenes[i].start < FADE:
            img = Image.blend(self.scene_image(i - 1, t), img, ease((t - scenes[i].start) / FADE))
        self.caption(img, t)
        return img


@lru_cache(maxsize=1024)
def caption_pill(words: tuple[str, ...], now: int) -> Image.Image:
    size = 64
    f = font(size * SS, "ExtraBold")
    shown = [w.strip().rstrip(",.:;") or w for w in words]
    gap = f.getlength(" ")
    widths = [f.getlength(w) for w in shown]
    padx, pady = int(size * 0.55 * SS), int(size * 0.3 * SS)
    w = int(sum(widths) + gap * (len(shown) - 1)) + 2 * padx
    h = int(size * 1.15 * SS) + 2 * pady
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w, h], radius=h // 2, fill=(28, 24, 40, 215))
    x = padx
    for k, (wd, ww) in enumerate(zip(shown, widths)):
        d.text((x, h / 2), wd, font=f, fill=SAID if k == now else WHITE, anchor="lm")
        x += ww + gap
    out = shrink(img)
    if out.width > W - 60:
        k = (W - 60) / out.width
        out = out.resize((int(out.width * k), int(out.height * k)), Image.LANCZOS)
    return out


def render(spec: Spec, voice: Path, out: Path, music: Path | None = None, music_volume: float = 0.08,
           progress=lambda f, m: None) -> Path:
    """bw_render.render with the world renderer (its voice, music, sound effects and encoding)."""
    from modules import bw_fx

    bw_render.align(spec)
    original, look = bw_fx.FxRenderer, spec.look
    bw_fx.FxRenderer = WorldRenderer  # bw_render.render builds bw_fx.FxRenderer for the "satellite" look
    spec.look = "satellite"
    try:
        return bw_render.render(spec, voice, out, music, music_volume, progress)
    finally:
        bw_fx.FxRenderer = original
        spec.look = look


def thumb_time(spec: Spec) -> float:
    """The hook once its racing figure has landed (the thumbnail): the end of the first scene."""
    first = spec.scenes[0]
    return max(0.0, first.end - 0.25)


def still(spec: Spec, t: float) -> Image.Image:
    """One frame (tests, the thumbnail): the scenes must be aligned already."""
    return WorldRenderer(spec).frame(t)
