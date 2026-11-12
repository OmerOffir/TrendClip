"""Borderline Why's "balls" look (2026-11-12): the countries are balls with their flag and two eyes, and they talk.

Built after the research of 2026-11-12: of the Shorts channels opened since August 2026, the ones where flag balls
answer a geography question in their own voices were the only kind where most new channels passed 1,000 subscribers.
The owner put it into Borderline Why ("it is the same subject") and asked for a host of our own: the channel's red
panda, as a ball, asks the question and calls the result.

A script of format "balls" has scenes of kind "balls", one line of speech each:
    who        "host" (the red-panda ball) or a country's name ("Russia"): its ball rolls in and says the line
    narration  what it says (a short spoken line; shown word by word as the caption)
    expr       the speaker's eyes: neutral | happy | smug | angry | sad | shocked | suspicious
    map        the country the map goes to (default: the speaker's own; a host line keeps the map where it was)
    show       countries that light up on the map one by one while the line is said (the 14 neighbours: all 14)
    badge      {text, sub, cue}: the round counter on the map ("14" / "NEIGHBORS"); with `show` it counts up as they
               light up and lands on `text`; without, it pops on the cue word
    with       a host line only: the countries standing beside it (["Russia", "China"] for "it's a tie")
The script's cover_text is the question on the banner, on screen from frame 0. cast: {"Russia": {"voice", "flag"}}
overrides a ball's Kokoro voice or its Commons flag file.

Voice: every speaker in its own voice (modules.bw_balls.record); the map, flags and fonts are Borderline Why's.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from modules import bw_geo, bw_render
from modules.bw_render import FPS, H, W, Spec, Word

HOST = "host"
SS = 2  # sprites are drawn this much bigger and shrunk
INK = (22, 22, 28)
YELLOW = (255, 208, 64)
OCEAN, LAND, EDGE = (28, 62, 100), (233, 222, 197), (150, 136, 112)
FOCUS, SHOWN = (236, 92, 54), (250, 200, 62)
P = 16  # the world picture's pixels per degree
PAD_X, PAD_Y = 90, 12  # degrees of sea around it, so a view near the edge has something to show
MAP_C = (540, 540)  # where the map's subject sits on the screen
BALL_Y, BALL_R = 1185, 176
HOST_X, HOST_R = 205, 140
STAGE_X = 590
CAPTION_Y = 895
EXPRS = ("neutral", "happy", "smug", "angry", "sad", "shocked", "suspicious")
WORDS = (18, 110)  # 12-50 s
LINE_WORDS = 16
SCENES = (4, 16)
# Commons names that are not "Flag of <name>.svg"
FLAGS = {"United States": "Flag of the United States.svg", "USA": "Flag of the United States.svg",
         "United Kingdom": "Flag of the United Kingdom.svg", "UK": "Flag of the United Kingdom.svg",
         "Netherlands": "Flag of the Netherlands.svg", "Philippines": "Flag of the Philippines.svg",
         "Czechia": "Flag of the Czech Republic.svg", "China": "Flag of the People's Republic of China.svg",
         "DR Congo": "Flag of the Democratic Republic of the Congo.svg", "UAE": "Flag of the United Arab Emirates.svg",
         "United Arab Emirates": "Flag of the United Arab Emirates.svg", "Dominican Republic": "Flag of the Dominican Republic.svg",
         "Bahamas": "Flag of the Bahamas.svg", "Gambia": "Flag of The Gambia.svg", "Maldives": "Flag of Maldives.svg"}
MAP_NAMES = {"USA": "United States of America", "United States": "United States of America", "UK": "United Kingdom",
             "DR Congo": "Democratic Republic of the Congo", "UAE": "United Arab Emirates"}
VOICE_POOL = ("am_fenrir", "bm_george", "am_puck", "bm_lewis", "am_onyx", "af_nicole", "am_echo", "bm_fable", "am_eric",
              "bf_emma", "am_liam", "bm_daniel", "am_adam", "af_kore")
PITCHES = (0.94, 1.06, 0.9, 1.0, 1.1, 0.97, 1.04)


# --------------------------------------------------------------------------- helpers


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def back(x: float) -> float:
    """0 -> 1 with a little overshoot (a pop)."""
    x = min(1.0, max(0.0, x))
    return 1 + 2.4 * (x - 1) ** 3 + 1.4 * (x - 1) ** 2


@lru_cache(maxsize=64)
def font(size: int) -> ImageFont.FreeTypeFont:
    bw_render.ensure_fonts()
    return ImageFont.truetype(str(bw_render.FONTS_DIR / "Anton-Regular.ttf"), size)


def tok(text: str) -> str:
    return "".join(c for c in str(text).lower() if c.isalnum())


def flag_name(who: str, cast: dict | None = None) -> str:
    return str(((cast or {}).get(who) or {}).get("flag") or FLAGS.get(who) or f"Flag of {who}.svg")


def feature(name: str) -> bw_geo.Feature | None:
    return bw_geo.resolve(MAP_NAMES.get(name, name), ("country",)) or bw_geo.resolve(MAP_NAMES.get(name, name))


# --------------------------------------------------------------------------- the balls


def _eye_mask(expr: str, w: int, h: int, side: int) -> Image.Image:
    """One eye as a mask (side: -1 the left eye, 1 the right): an ellipse, cut by its lid."""
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    if expr == "blink":
        d.rounded_rectangle([0, h * 0.44, w - 1, h * 0.58], radius=h // 12, fill=255)
        return m
    d.ellipse([0, 0, w - 1, h - 1], fill=255)
    cut = ImageDraw.Draw(m)
    inner, outer = (w, 0) if side < 0 else (0, w)  # x of the eye's inner and outer corner
    if expr == "happy":  # a smile: the lower part cut away by a rising curve
        cut.ellipse([-w * 0.15, h * 0.42, w * 1.15, h * 1.5], fill=0)
    elif expr == "smug":
        cut.rectangle([0, 0, w, h * 0.46], fill=0)
    elif expr == "angry":
        cut.polygon([(outer, -1), (inner, -1), (inner, h * 0.58), (outer, h * 0.12)], fill=0)
    elif expr == "sad":
        cut.polygon([(inner, -1), (outer, -1), (outer, h * 0.55), (inner, h * 0.1)], fill=0)
    elif expr == "suspicious":
        cut.rectangle([0, 0, w, h * 0.36], fill=0)
        cut.rectangle([0, h * 0.7, w, h], fill=0)
    return m


def _eyes(im: Image.Image, R: int, expr: str, gaze: float, dark: bool = False) -> None:
    big = 1.22 if expr == "shocked" else 1.0
    ew, eh = int(0.2 * R * big) * 2, int(0.25 * R * big) * 2
    edge = max(3, int(0.035 * R))
    for side in (-1, 1):
        cx = R + side * 0.36 * R + gaze * 0.06 * R
        cy = R - 0.14 * R
        m = _eye_mask(expr, ew, eh, side)
        box = (int(cx - ew / 2), int(cy - eh / 2))
        pad = Image.new("L", (ew + 4 * edge, eh + 4 * edge), 0)
        pad.paste(m, (2 * edge, 2 * edge))
        rim = pad.filter(ImageFilter.MaxFilter(edge * 2 + 1))
        im.paste(Image.new("RGBA", rim.size, (*INK, 255)), (box[0] - 2 * edge, box[1] - 2 * edge), rim)
        fill = (30, 24, 22, 255) if dark else (255, 255, 255, 255)
        im.paste(Image.new("RGBA", m.size, fill), box, m)
        if dark and expr != "blink":  # the host's eyes: dark, with a glint
            g = max(4, int(0.05 * R))
            ImageDraw.Draw(im).ellipse([cx - ew * 0.22 - g, cy - eh * 0.22 - g, cx - ew * 0.22 + g, cy - eh * 0.22 + g], fill=(255, 255, 255, 255))


def _sphere(im: Image.Image, R: int) -> Image.Image:
    """Shade a flat disc into a ball: darker towards the lower right, a soft highlight at the upper left, an ink rim."""
    size = 2 * R
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    u, v = (xx - R) / R, (yy - R) / R
    d = np.hypot(u + 0.32, v + 0.38) / 1.55
    shade = np.clip(1.06 - 0.5 * d ** 2, 0.5, 1.06)[..., None]
    arr = np.asarray(im.convert("RGBA"), dtype=np.float32)
    arr[..., :3] = np.clip(arr[..., :3] * shade, 0, 255)
    out = Image.fromarray(arr.astype(np.uint8))
    shine = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shine).ellipse([R * 0.3, R * 0.2, R * 0.86, R * 0.56], fill=(255, 255, 255, 80))
    out.alpha_composite(shine.filter(ImageFilter.GaussianBlur(R * 0.08)))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    out.putalpha(mask)
    ImageDraw.Draw(out).ellipse([0, 0, size - 1, size - 1], outline=(*INK, 255), width=max(4, int(0.04 * R)))
    return out


@lru_cache(maxsize=64)
def _flag_disc(flag: str, R: int) -> Image.Image:
    """The flag wrapped on a ball: squeezed a little so its sides stay on, and bulged towards the viewer."""
    from modules import bw_fx

    size = 2 * R
    src = bw_fx.flag_image(flag).convert("RGB").resize((int(size * 1.22), size), Image.LANCZOS)
    left = (src.width - size) // 2
    arr = np.asarray(src.crop((left, 0, left + size, size)))
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    u, v = (xx - R + 0.5) / R, (yy - R + 0.5) / R
    r = np.clip(np.hypot(u, v), 1e-4, 1.0)
    k = 0.45 + 0.55 * (np.arcsin(r) / (math.pi / 2)) / r  # the middle of the flag comes forward
    sx = np.clip((u * k) * R + R, 0, size - 1).astype(np.int32)
    sy = np.clip((v * k) * R + R, 0, size - 1).astype(np.int32)
    return _sphere(Image.fromarray(np.ascontiguousarray(arr[sy, sx])), R)


@lru_cache(maxsize=256)
def country_ball(flag: str, r: int, expr: str = "neutral", gaze: int = -1) -> Image.Image:
    R = r * SS
    im = _flag_disc(flag, R).copy()
    _eyes(im, R, expr, gaze)
    return im.resize((2 * r, 2 * r), Image.LANCZOS)


@lru_cache(maxsize=64)
def host_ball(r: int, expr: str = "neutral", mouth: int = 0) -> Image.Image:
    """The channel's red panda as a ball: rust fur, white brows, cheeks and muzzle, dark ears and belly, a mouth
    that opens when it talks (mouth: 0 shut, 1-2 open)."""
    R = r * SS
    pad = int(R * 0.42)  # room for the ears
    size = 2 * R
    rust, dark, white = (208, 90, 42), (78, 40, 26), (250, 244, 232)
    body = Image.new("RGB", (size, size), rust)
    d = ImageDraw.Draw(body)
    d.ellipse([-R * 0.3, R * 1.42, size + R * 0.3, size + R * 0.9], fill=dark)  # the dark belly
    for side in (-1, 1):
        d.ellipse([R + side * 0.66 * R - 0.3 * R, R * 0.92, R + side * 0.66 * R + 0.3 * R, R * 1.5], fill=white)  # cheeks
        d.polygon([(R + side * 0.3 * R, R * 0.95), (R + side * 0.46 * R, R * 0.95), (R + side * 0.6 * R, R * 1.5),
                   (R + side * 0.4 * R, R * 1.5)], fill=(160, 62, 30))  # the tear marks
        d.ellipse([R + side * 0.36 * R - 0.17 * R, R * 0.5, R + side * 0.36 * R + 0.17 * R, R * 0.68], fill=white)  # brows
    d.ellipse([R - 0.42 * R, R * 1.0, R + 0.42 * R, R * 1.62], fill=white)  # the muzzle
    ball = _sphere(body, R)
    d = ImageDraw.Draw(ball)
    d.polygon([(R - 0.11 * R, R * 1.1), (R + 0.11 * R, R * 1.1), (R, R * 1.24)], fill=(*INK, 255))  # the nose
    if mouth:
        h = (0.1, 0.2)[min(mouth, 2) - 1] * R
        d.ellipse([R - 0.13 * R, R * 1.3, R + 0.13 * R, R * 1.3 + h], fill=(96, 28, 30, 255), outline=(*INK, 255), width=max(3, R // 40))
    else:
        w = max(4, R // 30)
        d.arc([R - 0.2 * R, R * 1.14, R, R * 1.4], 20, 160, fill=(*INK, 255), width=w)
        d.arc([R, R * 1.14, R + 0.2 * R, R * 1.4], 20, 160, fill=(*INK, 255), width=w)
    _eyes(ball, R, expr, 1, dark=True)
    im = Image.new("RGBA", (size + 2 * pad, size + 2 * pad), (0, 0, 0, 0))
    e = ImageDraw.Draw(im)
    for side in (-1, 1):  # the ears, behind the head
        cx, cy, er = pad + R + side * 0.66 * R, pad + R - 0.7 * R, 0.36 * R
        e.ellipse([cx - er, cy - er, cx + er, cy + er], fill=(*dark, 255), outline=(*INK, 255), width=max(4, int(0.04 * R)))
        e.ellipse([cx - er * 0.55, cy - er * 0.55, cx + er * 0.55, cy + er * 0.55], fill=(*white, 255))
    im.alpha_composite(ball, (pad, pad))
    return im.resize((im.width // SS, im.height // SS), Image.LANCZOS)


# --------------------------------------------------------------------------- the map


def _wxy(lon, lat):
    return (lon + 180 + PAD_X) * P, (90 + PAD_Y - lat) * P


@lru_cache(maxsize=1)
def world_image() -> Image.Image:
    """The whole world once, flat (equirectangular) with sea around it; every frame is a window of it."""
    im = Image.new("RGB", (int((360 + 2 * PAD_X) * P), int((180 + 2 * PAD_Y) * P)), OCEAN)
    d = ImageDraw.Draw(im)
    for feat in bw_geo.world().layers["countries"]:
        for part in feat.parts:
            ring = part.at(2)[0]
            for dx in (-360, 0, 360):  # the far side again in the sea beyond the edge
                if part.bbox[2] + dx < -180 - PAD_X or part.bbox[0] + dx > 180 + PAD_X or len(ring) < 3:
                    continue
                pts = [_wxy(float(x) + dx, float(y)) for x, y in ring]
                d.polygon(pts, fill=LAND)
                d.line([*pts, pts[0]], fill=EDGE, width=2)
    return im


@lru_cache(maxsize=96)
def patch(name: str, color: tuple[int, int, int]) -> tuple[Image.Image, float, float] | None:
    """A country painted in `color` as its own picture, with the lon/lat of its top left corner. Lands across the
    date line (Russia, the USA) are kept in one piece, so the picture may reach past 180."""
    feat = feature(name)
    if feat is None or not feat.parts:
        return None
    boxes = [p.bbox for p in feat.parts]
    shift = [0.0] * len(boxes)
    if max(b[2] for b in boxes) - min(b[0] for b in boxes) > 180:
        moved = [360.0 if b[0] < 0 else 0.0 for b in boxes]
        if max(b[2] + m for b, m in zip(boxes, moved)) - min(b[0] + m for b, m in zip(boxes, moved)) < 300:
            shift = moved
    x0 = min(b[0] + m for b, m in zip(boxes, shift)) - 0.3
    x1 = max(b[2] + m for b, m in zip(boxes, shift)) + 0.3
    y0, y1 = min(b[1] for b in boxes) - 0.3, max(b[3] for b in boxes) + 0.3
    im = Image.new("RGBA", (max(2, int((x1 - x0) * P)), max(2, int((y1 - y0) * P))), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    edge = tuple(int(c * 0.45) for c in color)
    for part, m in zip(feat.parts, shift):
        ring = part.at(2)[0]
        if len(ring) < 3:
            continue
        pts = [((float(x) + m - x0) * P, (y1 - float(y)) * P) for x, y in ring]
        d.polygon(pts, fill=(*color, 255))
        d.line([*pts, pts[0]], fill=(*edge, 255), width=4)
    return im, x0, y1


def _box(name: str) -> tuple[float, float, float, float] | None:
    feat = feature(name)
    if feat is None:
        return None
    x0, y0, x1, y1 = feat.bbox()
    if x1 - x0 > 180:  # across the date line: look at its bigger side
        xs = [p.bbox for p in feat.parts if p.bbox[0] >= 0]
        if xs:
            x0, x1 = min(b[0] for b in xs), max(b[2] for b in xs)
    return x0, y0, x1, y1


def view_of(*names: str) -> tuple[float, float, float] | None:
    """Where the map goes for a country (or for several, all in view): (lon, lat, pixels per degree of latitude),
    the land filling the top of the screen."""
    boxes = [b for b in map(_box, names) if b]
    if not boxes:
        return None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    lat = (y0 + y1) / 2
    k = max(0.35, math.cos(math.radians(lat)))
    ppd = min(W * 0.62 / max(0.5, (x1 - x0) * k), 430 / max(0.5, y1 - y0))
    return (x0 + x1) / 2, lat, float(min(64.0, max(10.5, ppd)))


class Cam:
    def __init__(self, lon: float, lat: float, ppd: float):
        self.lon, self.lat, self.ppd = lon, lat, ppd
        self.k = max(0.35, math.cos(math.radians(lat)))

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return MAP_C[0] + (lon - self.lon) * self.ppd * self.k, MAP_C[1] - (lat - self.lat) * self.ppd

    def frame(self) -> Image.Image:
        world = world_image()
        lon0, lon1 = self.lon - MAP_C[0] / (self.ppd * self.k), self.lon + (W - MAP_C[0]) / (self.ppd * self.k)
        lat0, lat1 = self.lat + MAP_C[1] / self.ppd, self.lat - (H - MAP_C[1]) / self.ppd
        (l, t), (r, b) = _wxy(lon0, lat0), _wxy(lon1, lat1)
        if l < 0 or t < 0 or r > world.width or b > world.height:  # past the picture: the sea goes on
            out = Image.new("RGB", (W, H), OCEAN)
            cl, ct, cr, cb = max(0.0, l), max(0.0, t), min(float(world.width), r), min(float(world.height), b)
            if cr - cl > 2 and cb - ct > 2:
                sx, sy = W / (r - l), H / (b - t)
                part = world.resize((max(1, round((cr - cl) * sx)), max(1, round((cb - ct) * sy))), Image.BICUBIC, box=(cl, ct, cr, cb))
                out.paste(part, (round((cl - l) * sx), round((ct - t) * sy)))
            return out
        return world.resize((W, H), Image.BICUBIC, box=(l, t, r, b))

    def paint(self, img: Image.Image, name: str, color: tuple[int, int, int], a: float) -> None:
        """A country in colour over the map, `a` its strength (0-1)."""
        got = patch(name, color)
        if got is None or a <= 0.01:
            return
        im, lon_a, lat_top = got
        sx, sy = self.ppd * self.k / P, self.ppd / P  # screen pixels per picture pixel
        for dx in (-360.0, 0.0, 360.0):
            x, y = self.xy(lon_a + dx, lat_top)
            w, h = im.width * sx, im.height * sy
            if x > W or y > H or x + w < 0 or y + h < 0:
                continue
            cl, ct = max(0.0, -x) / sx, max(0.0, -y) / sy
            cr, cb = min(float(im.width), (W - x) / sx), min(float(im.height), (H - y) / sy)
            if cr - cl < 1 or cb - ct < 1:
                continue
            piece = im.resize((max(1, round((cr - cl) * sx)), max(1, round((cb - ct) * sy))), Image.BILINEAR, box=(cl, ct, cr, cb))
            if a < 0.99:
                piece.putalpha(piece.getchannel("A").point(lambda v: int(v * a)))
            img.paste(piece, (round(x + cl * sx), round(y + ct * sy)), piece)


# --------------------------------------------------------------------------- the pieces on top


def banner(text: str) -> Image.Image:
    """The question, on from frame 0: a dark plate with a yellow edge, its last word in yellow."""
    words = text.upper().split()
    size = 84
    while size > 44:
        f = font(size)
        lines, cur = [], []
        for w in words:
            if cur and f.getlength(" ".join([*cur, w])) > W - 150:
                lines.append(cur)
                cur = []
            cur.append(w)
        lines.append(cur)
        if len(lines) <= 2:
            break
        size -= 6
    lh = int(size * 1.16)
    bw = int(max(f.getlength(" ".join(line)) for line in lines)) + 90
    bh = lh * len(lines) + 46
    im = Image.new("RGBA", (bw + 16, bh + 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([8, 8, bw + 7, bh + 7], radius=34, fill=(18, 22, 36, 242), outline=(*YELLOW, 255), width=7)
    for n, line in enumerate(lines):
        x = 8 + (bw - f.getlength(" ".join(line))) / 2
        for k, w in enumerate(line):
            last = n == len(lines) - 1 and k == len(line) - 1
            d.text((x, 8 + 18 + n * lh), w, font=f, fill=(*YELLOW, 255) if last else (255, 255, 255, 255))
            x += f.getlength(w + " ")
    return im


@lru_cache(maxsize=128)
def badge(text: str, sub: str) -> Image.Image:
    """The counter on the map: a white disc with a big number and what it counts."""
    r = 118
    im = Image.new("RGBA", (2 * r + 20, 2 * r + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([16, 18, 2 * r + 16, 2 * r + 18], fill=(0, 0, 0, 90))
    d.ellipse([6, 6, 2 * r + 6, 2 * r + 6], fill=(255, 255, 255, 255), outline=(*INK, 255), width=8)
    size = 128 if sub else 150
    f = font(size)
    while f.getlength(text) > 2 * r - 50 and size > 40:
        size -= 6
        f = font(size)
    box = d.textbbox((0, 0), text, font=f)
    cy = r + 6 - (26 if sub else 0)
    d.text((r + 6 - (box[0] + box[2]) / 2, cy - (box[1] + box[3]) / 2), text, font=f, fill=(*FOCUS, 255))
    if sub:
        fs = font(36)
        while fs.getlength(sub.upper()) > 2 * r - 56 and fs.size > 20:
            fs = font(fs.size - 2)
        d.text((r + 6 - fs.getlength(sub.upper()) / 2, r + 6 + 44), sub.upper(), font=fs, fill=(*INK, 255))
    return im


@lru_cache(maxsize=128)
def plate(name: str) -> Image.Image:
    f = font(50)
    w = int(f.getlength(name.upper())) + 56
    im = Image.new("RGBA", (w + 8, 84), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([4, 4, w + 3, 79], radius=22, fill=(255, 255, 255, 255), outline=(*INK, 255), width=6)
    d.text((4 + 28, 10), name.upper(), font=f, fill=(*INK, 255))
    return im


@lru_cache(maxsize=1)
def stage_shade() -> Image.Image:
    """The dark floor the balls stand on, and a little dark at the top under the banner."""
    a = np.zeros((H, W), dtype=np.float32)
    ys = np.arange(H, dtype=np.float32)[:, None]
    a += np.clip((ys - 760) / 420, 0, 1) * 215
    a += np.clip((260 - ys) / 260, 0, 1) * 90
    im = Image.new("RGBA", (W, H), (10, 14, 28, 255))
    im.putalpha(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
    return im


def chunks(words: list[Word]) -> list[list[Word]]:
    """The caption's groups: up to 3 words and 16 letters, never across a pause or a sentence end."""
    out: list[list[Word]] = []
    for w in words:
        cur = out[-1] if out else None
        if (cur is None or len(cur) >= 3 or len(" ".join(x.text for x in cur)) + len(w.text) > 15
                or w.start - cur[-1].end > 0.3 or cur[-1].text.rstrip()[-1:] in ".!?,"):
            out.append([])
        out[-1].append(w)
    return out


def caption(img: Image.Image, group: list[Word], t: float) -> None:
    size = 104
    text = " ".join(w.text.upper().strip() for w in group)
    f = font(size)
    while f.getlength(text) > W - 110 and size > 56:
        size -= 6
        f = font(size)
    s = 0.86 + 0.14 * back((t - group[0].start + 0.04) / 0.14)
    d = ImageDraw.Draw(img)
    x = (W - f.getlength(text)) / 2
    said = max((w for w in group if w.start - 0.03 <= t), key=lambda w: w.start, default=None)
    for w in group:
        word = w.text.upper().strip()
        now = w is said and t < w.end + 0.12
        fw = font(max(20, int(size * s * (1.05 if now else 1.0))))
        mid = x + f.getlength(word) / 2  # every word keeps its own place: the one being said only swells there
        d.text((mid - fw.getlength(word) / 2, CAPTION_Y - fw.size * 0.5), word, font=fw, fill=YELLOW if now else (255, 255, 255),
               stroke_width=max(6, size // 11), stroke_fill=INK)
        x += f.getlength(word + " ")


# --------------------------------------------------------------------------- the renderer


class BallRenderer:
    """Draws a balls Spec frame by frame. Scenes must have their start and end (render sets them from the turns)."""

    def __init__(self, spec: Spec, cast: dict | None = None):
        self.spec = spec
        self.cast = cast or getattr(spec, "cast", None) or {}
        self.turns = [self._turn(i) for i in range(len(spec.scenes))]
        self.groups = chunks(spec.words)
        self.banner = banner(spec.cover) if spec.cover else None
        self.views = self._views()
        sounds: list[tuple[float, str, float]] = []
        for tn in self.turns:
            if tn["who"] != HOST and tn["enter"]:
                sounds.append((tn["start"], "whoosh", 0.0))
            sounds += [(at, "pop", 0.0) for _, at in tn["show"]]
            if tn["badge"]:
                sounds.append((tn["badge"]["land"], "ding", 0.0))
        spec.sfx_extra = sorted(sounds)

    def _turn(self, i: int) -> dict[str, Any]:
        sc = self.spec.scenes[i]
        who = str(sc.opts.get("who") or HOST)
        words = [w for w in self.spec.words if sc.start - 0.02 <= w.start < sc.end - 0.02]
        said_end = words[-1].end if words else sc.end
        names = [str(n) for n in sc.opts.get("show") or []]
        first = sc.start + 0.3
        step = min(0.2, max(0.07, (said_end - first - 0.1) / max(1, len(names))))
        show = [(n, first + k * step) for k, n in enumerate(names)]
        bd = dict(sc.opts.get("badge") or {}) or None
        if bd:
            cue = tok(bd.get("cue", ""))
            hit = next((w.start for w in words if cue and tok(w.text).startswith(cue)), None)
            bd["at"] = show[0][1] if show else (hit if hit is not None else sc.start + 0.4)
            bd["land"] = show[-1][1] if show else bd["at"]
        prev = self.spec.scenes[i - 1].opts.get("who") if i else None
        return {"i": i, "who": who, "start": sc.start, "end": sc.end, "words": words, "expr": str(sc.opts.get("expr") or "neutral"),
                "show": show, "badge": bd, "map": str(sc.opts.get("map") or ("" if who == HOST else who)),
                "with": [str(n) for n in sc.opts.get("with") or []], "enter": who != HOST and prev != who}

    def _views(self) -> list[tuple[float, float, float]]:
        out: list[tuple[float, float, float]] = []
        first = next((view_of(tn["map"]) for tn in self.turns if tn["map"] and view_of(tn["map"])), (10.0, 25.0, 10.5))
        wide = (first[0], max(-20.0, min(40.0, first[1] * 0.5)), 10.5)  # the opening: the world around the first country
        for tn in self.turns:
            v = view_of(tn["map"]) if tn["map"] else view_of(*tn["with"]) if 0 < len(tn["with"]) <= 2 else None
            out.append(v or (out[-1] if out else wide))
        return out

    def turn_at(self, t: float) -> dict[str, Any]:
        return next((tn for tn in self.turns if tn["start"] <= t < tn["end"]), self.turns[-1] if t >= self.turns[-1]["start"] else self.turns[0])

    def cam(self, t: float) -> Cam:
        tn = self.turn_at(t)
        a = self.views[tn["i"] - 1] if tn["i"] else self.views[0]
        b = self.views[tn["i"]]
        u = ease((t - tn["start"]) / 0.8)
        lon, lat = a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u
        ppd = math.exp(math.log(a[2]) + (math.log(b[2]) - math.log(a[2])) * u)
        return Cam(lon, lat, ppd * (1 + 0.035 * min(1.0, (t - tn["start"]) / 6)))  # a slow drift in, never a punch

    def hop(self, tn: dict[str, Any], t: float) -> float:
        """How far up the speaker is at `t`: a small hop on every word it says."""
        w = next((w for w in tn["words"] if w.start <= t < w.start + 0.2), None)
        return math.sin(math.pi * (t - w.start) / 0.2) * 24 if w else 0.0

    def draw_ball(self, img: Image.Image, spr: Image.Image, x: float, y: float, r: int, angle: float = 0.0, scale: float = 1.0) -> None:
        if scale <= 0.03:
            return
        sh = Image.new("RGBA", (int(r * 2.6), int(r * 0.7)), (0, 0, 0, 0))
        ImageDraw.Draw(sh).ellipse([r * 0.3, r * 0.14, r * 2.3, r * 0.56], fill=(0, 0, 0, int(120 * min(1.0, scale))))
        img.paste(sh, (int(x - r * 1.3), int(BALL_Y + BALL_R * 0.86 - r * 0.2)), sh)
        if angle:
            spr = spr.rotate(angle, Image.BICUBIC)
        if abs(scale - 1) > 0.01:
            spr = spr.resize((max(2, int(spr.width * scale)), max(2, int(spr.height * scale))), Image.BILINEAR)
        img.paste(spr, (int(x - spr.width / 2), int(y - spr.height / 2)), spr)

    def frame(self, t: float) -> Image.Image:
        tn = self.turn_at(t)
        cam = self.cam(t)
        img = cam.frame()
        # the map: every country lit so far in this stretch of the story stays lit until the map moves on
        for old in self.turns[:tn["i"] + 1]:
            if old["map"] != tn["map"] and not (tn["who"] == HOST and not tn["map"] and old is self.turns[tn["i"] - 1]):
                continue
            if old["map"]:
                cam.paint(img, old["map"], FOCUS, ease((t - old["start"]) / 0.35) if old is tn else 1.0)
            for name, at in old["show"]:
                if t >= at:
                    cam.paint(img, name, SHOWN, 1.0)
        if tn["who"] == HOST and not tn["map"]:
            for name in tn["with"]:
                cam.paint(img, name, FOCUS, 1.0)
        img = img.convert("RGBA")
        img.alpha_composite(stage_shade())
        if self.banner is not None:
            img.alpha_composite(self.banner, ((W - self.banner.width) // 2, 118))
        bd = tn["badge"] or (self.turns[tn["i"] - 1]["badge"] if tn["i"] and tn["who"] == HOST and not tn["map"] else None)
        if bd and t >= bd["at"]:
            owner = tn if tn["badge"] else self.turns[tn["i"] - 1]
            n = sum(1 for _, at in owner["show"] if t >= at)
            text = str(bd.get("text", "")) if not owner["show"] or t >= bd["land"] else str(n)
            spr = badge(text, str(bd.get("sub", "")))
            last = max([at for _, at in owner["show"] if t >= at], default=bd["at"])
            s = back((t - bd["at"]) / 0.3) * (1 + 0.12 * max(0.0, 1 - (t - last) / 0.14))
            spr = spr.resize((max(2, int(spr.width * s)), max(2, int(spr.height * s))), Image.BILINEAR)
            img.alpha_composite(spr, (int(852 - spr.width / 2), int(452 - spr.height / 2)))
        self.draw_cast(img, tn, t)
        group = next((g for g in self.groups if g[0].start - 0.04 <= t < g[-1].end + 0.22), None)
        if group:
            caption(img, group, t)
        return img.convert("RGB")

    def draw_cast(self, img: Image.Image, tn: dict[str, Any], t: float) -> None:
        blink = (t * 0.37 + 0.2) % 1 < 0.035
        talking_host = tn["who"] == HOST
        # the host: bottom left, looking right; it hops and opens its mouth on its own words
        said = next((w for w in tn["words"] if w.start <= t < w.end), None) if talking_host else None
        mouth = 0 if said is None else 1 + int((t - said.start) * 9) % 2
        hexpr = "blink" if blink else (tn["expr"] if talking_host and tn["expr"] != "neutral" else "happy" if talking_host and tn["with"] else "neutral")
        hy = BALL_Y + 22 - (self.hop(tn, t) if talking_host else 0) + 5 * math.sin(2.1 * t)
        spr = host_ball(HOST_R, hexpr, mouth)
        self.draw_ball(img, spr, HOST_X, hy, HOST_R, scale=1.06 if talking_host else 1.0)
        # the countries on the stage
        if talking_host:
            names = tn["with"] or ([self.turns[tn["i"] - 1]["who"]] if tn["i"] and self.turns[tn["i"] - 1]["who"] != HOST else [])
            speaker = None
        else:
            names, speaker = [tn["who"]], tn["who"]
        n = len(names)
        for k, name in enumerate(names):
            r = BALL_R if n == 1 else int(min(124, (540 / n - 14) / 2))  # several share the room right of the host
            x = STAGE_X if n == 1 else 372 + (k + 0.5) * (540 / n)
            y = BALL_Y + (BALL_R - r) + 5 * math.sin(2.1 * t + 1.3 + k)
            angle, scale = 0.0, 1.0
            expr = "blink" if (t * 0.31 + 0.6 + 0.2 * k) % 1 < 0.035 else "neutral"
            if name == speaker:
                expr = "blink" if (t * 0.31 + 0.6) % 1 < 0.03 else tn["expr"]
                y -= self.hop(tn, t)
                if tn["enter"]:  # it rolls in from the right
                    u = ease((t - tn["start"] + 0.12) / 0.5)
                    x += (1 - u) * 720
                    angle = (1 - u) * 300
            elif talking_host and tn["with"]:
                scale = back((t - tn["start"]) / 0.3 - 0.25 * k)
                expr = expr if expr == "blink" else "happy"
            spr = country_ball(flag_name(name, self.cast), r, expr, -1)
            self.draw_ball(img, spr, x, y, r, angle, scale)
            if scale > 0.9 and not angle and n <= 2:
                pl = plate(name)
                if n > 1:
                    pl = pl.resize((int(pl.width * 0.72), int(pl.height * 0.72)), Image.LANCZOS)
                img.alpha_composite(pl, (int(x - pl.width / 2), int(BALL_Y + BALL_R + 34)))


# --------------------------------------------------------------------------- voice, render, checks


def voices(script: dict) -> dict[str, tuple[str, float]]:
    """Every country's (Kokoro voice, pitch): its own from the cast, else one from the pool, no two alike in a video."""
    cast = script.get("cast") or {}
    order = list(dict.fromkeys(str(s.get("who") or HOST) for s in script.get("scenes") or []))
    seed = int(hashlib.md5(str(script.get("title", "")).encode()).hexdigest()[:8], 16)
    pool = list(VOICE_POOL)
    random.Random(seed).shuffle(pool)
    out: dict[str, tuple[str, float]] = {}
    for k, who in enumerate(w for w in order if w != HOST):
        own = cast.get(who) or {}
        out[who] = (str(own.get("voice") or pool[k % len(pool)]), float(own.get("pitch") or PITCHES[(seed + k) % len(PITCHES)]))
    return out


def turns_path(voice: Path) -> Path:
    return voice.with_suffix(".turns.json")


def record(script: dict, out: Path, progress=lambda m: None, pace: float | None = None) -> list[Word]:
    """Every line in its speaker's voice, one after another: one MP3, its timed words, and when each line starts and
    ends (turns_path, which render reads: no guessing which scene a word belongs to)."""
    from modules import bw_channel

    pace = pace or float(bw_channel.setting("BW_SHORT_PACE", str(bw_channel.SHORT_PACE)))
    rate = bw_channel.VOICE_RATE
    cast = voices(script)
    gap = b"\0\0" * int(rate * 0.13)
    pcm = bytearray()
    words: list[Word] = []
    turns: list[list[float]] = []
    try:
        for k, s in enumerate(script["scenes"]):
            who = str(s.get("who") or HOST)
            text = " ".join(str(s.get("narration") or "").split())
            tmp = out.with_name(f"{out.stem}.part{k}.mp3")
            if who == HOST:
                got = bw_channel.record_voice(text, tmp, progress, who="panda", unload=False, pace=pace)
                pitch = float(bw_channel.setting("BW_PANDA_PITCH", "1.22"))
                speed = float(bw_channel.setting("BW_PANDA_SPEED", "1.08"))
            else:
                voice, pitch = cast[who]
                got = bw_channel.record_voice(text, tmp, progress, unload=False, pace=pace, voice=voice)
                speed = 1.0
            # a higher or lower voice: asetrate shifts the pitch (and the speed with it), atempo puts the speed back
            raw = bw_channel._pcm(tmp, f"asetrate={rate * pitch:.0f},aresample={rate},atempo={speed / pitch:.4f}")
            if pcm:
                pcm += gap
            offset = len(pcm) / 2 / rate
            words += [Word(w.text, round(offset + w.start / speed, 3), round(offset + w.end / speed, 3)) for w in got]
            turns.append([round(offset, 3), round(offset + len(raw) / 2 / rate, 3)])
            pcm += raw
            tmp.unlink(missing_ok=True)
            tmp.with_suffix(".words.json").unlink(missing_ok=True)
    finally:
        from trendclip import kokoro_voice

        kokoro_voice.unload()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ac", "1", "-ar", str(rate), "-i", "-",
                    "-c:a", "libmp3lame", "-b:a", "160k", str(out)], input=bytes(pcm), check=True)
    out.with_suffix(".words.json").write_text(json.dumps([{"word": w.text, "start": w.start, "end": w.end} for w in words], indent=1), encoding="utf-8")
    turns_path(out).write_text(json.dumps(turns), encoding="utf-8")
    return words


def set_times(spec: Spec, turns: list[list[float]] | None) -> None:
    """Every scene's start and end: from the recorded turns, else matched to the words like any other script."""
    if turns and len(turns) == len(spec.scenes):
        for k, sc in enumerate(spec.scenes):
            sc.start = 0.0 if k == 0 else turns[k][0] - 0.06
            sc.end = turns[k + 1][0] - 0.06 if k + 1 < len(turns) else spec.duration
    else:
        bw_render.align(spec)


def render(spec: Spec, voice: Path, out: Path, music: Path | None = None, music_volume: float = 0.08,
           progress=lambda f, m: None) -> Path:
    """bw_render.render with the balls renderer (its voice, music, sound effects and encoding)."""
    from modules import bw_fx

    path = turns_path(voice)
    set_times(spec, json.loads(path.read_text()) if path.is_file() else None)
    original, look = bw_fx.FxRenderer, spec.look
    bw_fx.FxRenderer = BallRenderer  # bw_render.render builds bw_fx.FxRenderer for the "satellite" look
    spec.look, spec.sfx = "satellite", True
    try:
        return bw_render.render(spec, voice, out, music, music_volume, progress)
    finally:
        bw_fx.FxRenderer = original
        spec.look = look


def is_balls(script: dict) -> bool:
    return script.get("format") == "balls"


def problems(script: dict) -> list[str]:
    """What a balls script needs before it is drawn: short lines, known speakers and places, a question on top."""
    found = []
    scenes = script.get("scenes") or []
    n = sum(len(str(s.get("narration") or "").split()) for s in scenes)
    if not WORDS[0] <= n <= WORDS[1]:
        found.append(f"{n} words in all (a balls Short has {WORDS[0]}-{WORDS[1]})")
    if not SCENES[0] <= len(scenes) <= SCENES[1]:
        found.append(f"{len(scenes)} lines (wanted {SCENES[0]}-{SCENES[1]})")
    if not 2 <= len(str(script.get("cover_text") or "").split()) <= 8:
        found.append("cover_text is the question on the banner, 2-8 words")
    if scenes and str(scenes[0].get("who") or HOST) != HOST:
        found.append("the first line is the host's: it asks the question")
    for k, s in enumerate(scenes, 1):
        where = f"line {k}"
        who = str(s.get("who") or HOST)
        words = str(s.get("narration") or "").split()
        if not words:
            found.append(f"{where}: nothing is said")
        elif len(words) > LINE_WORDS:
            found.append(f"{where}: {len(words)} words (at most {LINE_WORDS}: a ball says one short thing)")
        if s.get("expr") and s["expr"] not in EXPRS:
            found.append(f"{where}: expr is one of {', '.join(EXPRS)}")
        for name in [*( [who] if who != HOST else []), *(s.get("show") or []), *(s.get("with") or []), *([s["map"]] if s.get("map") else [])]:
            if feature(str(name)) is None:
                found.append(f"{where}: {name} is not a country on the map")
        if who == HOST and s.get("show"):
            found.append(f"{where}: only a country's line lights up countries (show)")
        bd = s.get("badge")
        if bd and (not str(bd.get("text", "")).strip() or len(str(bd.get("text"))) > 6 or len(str(bd.get("sub", ""))) > 12):
            found.append(f"{where}: a badge has a short text (at most 6 characters) and a sub of at most 12")
    if scenes and not str(scenes[-1].get("narration") or "").strip().endswith("?"):
        found.append("the last line asks the viewer one thing (it ends with ?)")
    return found


def fake_words(script: dict) -> tuple[list[Word], list[list[float]]]:
    """Timings without a voice (for looking at frames): every word 0.3 s."""
    words, turns, t = [], [], 0.0
    for s in script["scenes"]:
        start = t
        for w in str(s["narration"]).split():
            words.append(Word(w, round(t, 3), round(t + 0.26, 3)))
            t += 0.3
        turns.append([start, t])
        t += 0.2
    return words, turns


def still(spec: Spec, t: float) -> Image.Image:
    return BallRenderer(spec).frame(t)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--voice", help="an MP3 recorded by record() (with its .words.json and .turns.json); else fake timings")
    ap.add_argument("--record", action="store_true", help="record the voice to --voice first")
    ap.add_argument("--out", required=True)
    ap.add_argument("--stills", type=float, default=0.0, help="save a sheet of frames every this many seconds instead of a video")
    ap.add_argument("--music")
    a = ap.parse_args()
    data = json.loads(Path(a.script).read_text())
    print("problems:", problems(data))
    if a.record:
        record(data, Path(a.voice), print)
    if a.voice:
        v = Path(a.voice)
        ws = [Word(w["word"], w["start"], w["end"]) for w in json.loads(v.with_suffix(".words.json").read_text())]
        tr = json.loads(turns_path(v).read_text())
    else:
        ws, tr = fake_words(data)
    sp = bw_render.spec_from_dict(data, ws)
    sp.hold = 1.2
    sp.cast = data.get("cast") or {}
    if a.stills:
        set_times(sp, tr)
        rd = BallRenderer(sp)
        ts = [k * a.stills for k in range(int(sp.duration / a.stills) + 1)]
        cols = 6
        sheet = Image.new("RGB", (cols * 270, ((len(ts) + cols - 1) // cols) * 480), "black")
        for k, tt in enumerate(ts):
            sheet.paste(rd.frame(tt).resize((270, 480), Image.LANCZOS), ((k % cols) * 270, (k // cols) * 480))
        sheet.save(a.out, quality=88)
    else:
        render(sp, Path(a.voice), Path(a.out), Path(a.music) if a.music else None, progress=lambda f, m: print(f, m, flush=True))
    print("done", a.out, round(sp.duration, 2))
