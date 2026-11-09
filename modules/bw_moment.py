"""Borderline Why's moment scene (the owner, 2026-11-09): the video leaves the map for the everyday situation the
narration describes, drawn in code: a street of the place at the hour it speaks of, with the owner's characters in
it ("in Kashgar the sun doesn't rise until past 10 a.m." -> a dark bazaar street, the clock on 10:00 Beijing time,
the shopkeeper raising the shutter in the dark, the sun coming up late).

    Moment(sky="night", clock="10:00", clock_label="BEIJING TIME", shop="BAZAAR", sunrise=True, cast=[...]).frame(t)

The street is the flat world look (modules.pw_world.backdrop); the people are the owner's GIFs (modules.bw_local).
The camera only drifts (the owner's calm camera).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw

from modules import bw_local, pw_world
from modules.bw_render import H, W

INK = pw_world.INK
SS = pw_world.SS
GROUND = pw_world.GROUND
SHOP_X, SHOP_W, SHOP_H = 520, 470, 520  # the shop front: right of the mascot (bottom left)
K = 1.3  # the shop is drawn this much bigger on screen
CLOCK = re.compile(r"(?:[01]?\d|2[0-3]):[0-5]\d")
CLOCK_XY, CLOCK_R = (520, 300), 140  # over the shop


def ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


@lru_cache(maxsize=4)
def street(sky: str, seed: int) -> Image.Image:
    return pw_world.backdrop(sky, seed).convert("RGBA")


@lru_cache(maxsize=4)
def shop_front(sign: str) -> Image.Image:
    """A shop: its sign, a striped awning and the doorway the shutter covers (drawn separately)."""
    s = SS
    w, h = SHOP_W, SHOP_H
    img = Image.new("RGBA", ((w + 20) * s, (h + 20) * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([10 * s, 10 * s, (w + 10) * s, (h + 10) * s], fill=(214, 168, 112), outline=INK, width=6 * s)  # the wall
    d.rectangle([30 * s, 30 * s, (w - 10) * s, 120 * s], fill=(120, 40, 50), outline=INK, width=5 * s)  # the sign
    f = pw_world.font(64 * s, "Black")
    d.text(((w + 20) / 2 * s, 76 * s), sign.upper(), font=f, fill=(255, 214, 120), anchor="mm")
    stripes = 8
    for k in range(stripes):  # the awning
        x0 = 10 + k * w / stripes
        d.polygon([(x0 * s, 132 * s), ((x0 + w / stripes) * s, 132 * s), ((x0 + w / stripes + 6) * s, 200 * s), ((x0 - 6) * s, 200 * s)],
                  fill=(232, 62, 62) if k % 2 == 0 else (250, 240, 220), outline=INK)
    d.rectangle([60 * s, 220 * s, (w - 40) * s, (h + 10) * s], fill=(40, 30, 40), outline=INK, width=5 * s)  # the doorway
    for k, color in enumerate(((240, 180, 60), (90, 170, 90), (220, 90, 60), (240, 200, 90))):  # goods inside
        x = 100 + k * 90
        d.ellipse([x * s, (h - 70) * s, (x + 70) * s, h * s], fill=color, outline=INK, width=3 * s)
        d.ellipse([(x + 15) * s, (h - 120) * s, (x + 75) * s, (h - 60) * s], fill=tuple(int(c * 0.85) for c in color), outline=INK, width=3 * s)
    return pw_pw(img)


def pw_pw(img: Image.Image) -> Image.Image:
    return pw_world.shrink(img)


def shutter(u: float) -> Image.Image:
    """The rolling shutter over the doorway, `u` of the way up."""
    w, h = SHOP_W - 100, SHOP_H - 210
    shown = max(0, int(h * (1 - ease(u))))
    img = Image.new("RGBA", (w, max(1, shown)), (0, 0, 0, 0))
    if shown <= 0:
        return img
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, shown - 1], fill=(160, 166, 176), outline=INK, width=4)
    for y in range(shown - 1, 0, -22):
        d.line([(4, y), (w - 5, y)], fill=(118, 124, 136), width=4)
    d.rectangle([w / 2 - 40, shown - 18, w / 2 + 40, shown - 6], fill=(90, 96, 108))  # the handle
    return img


def clock(hh: int, mm: float, label: str) -> Image.Image:
    """A big station clock with its hands at hh:mm and a label plate under it."""
    s = SS
    r = CLOCK_R
    size = (2 * r + 60) * s
    img = Image.new("RGBA", (size, size + 90 * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    d.ellipse([c - (r + 14) * s, c - (r + 14) * s, c + (r + 14) * s, c + (r + 14) * s], fill=(60, 50, 70), outline=INK, width=6 * s)
    d.ellipse([c - r * s, c - r * s, c + r * s, c + r * s], fill=(250, 246, 232), outline=INK, width=5 * s)
    for k in range(12):
        a = math.radians(k * 30)
        r0 = r * (0.8 if k % 3 else 0.72)
        d.line([(c + math.sin(a) * r0 * s, c - math.cos(a) * r0 * s), (c + math.sin(a) * r * 0.92 * s, c - math.cos(a) * r * 0.92 * s)],
               fill=INK, width=(8 if k % 3 == 0 else 4) * s)
    ha = math.radians((hh % 12 + mm / 60) * 30)
    ma = math.radians(mm * 6)
    d.line([(c, c), (c + math.sin(ha) * r * 0.5 * s, c - math.cos(ha) * r * 0.5 * s)], fill=INK, width=14 * s)
    d.line([(c, c), (c + math.sin(ma) * r * 0.78 * s, c - math.cos(ma) * r * 0.78 * s)], fill=INK, width=8 * s)
    d.ellipse([c - 12 * s, c - 12 * s, c + 12 * s, c + 12 * s], fill=(232, 62, 62), outline=INK, width=3 * s)
    f = pw_world.font(38 * s, "Black")
    tw = f.getlength(label) + 50 * s
    y = size + 10 * s
    d.rounded_rectangle([c - tw / 2, y, c + tw / 2, y + 70 * s], radius=14 * s, fill=(34, 30, 50), outline=INK, width=4 * s)
    d.text((c, y + 35 * s), label, font=f, fill=(255, 214, 120), anchor="mm")
    return pw_world.shrink(img)


@dataclass
class Actor:
    icon: str
    x: float
    at: float = 0.3
    say: str = ""
    say_at: float = 1.4
    mood: str = ""
    flip: bool = False
    size: int = 420


@dataclass
class Moment:
    sky: str = "night"
    clock: str = ""  # "10:00": the clock's hands come round to it
    clock_label: str = ""
    shop: str = ""  # the shop's sign; empty: no shop
    open_at: float = -1.0  # seconds in when the shutter rolls up (-1: it stays shut)
    sunrise_at: float = -1.0  # seconds in when the sun starts to come up (-1: the sky stays as it is)
    duration: float = 5.0
    seed: int = 1
    cast: list[Actor] = field(default_factory=list)

    def frame(self, t: float) -> Image.Image:
        base = street(self.sky, self.seed)
        x0 = int(60 * ease(t / max(1.0, self.duration)))  # the camera drifts slowly
        img = base.crop((x0, 0, x0 + W, H)).copy()
        if self.sunrise_at >= 0 and t > self.sunrise_at:  # the late sun: the sky warms toward dawn from the horizon
            u = ease((t - self.sunrise_at) / 2.5)
            dawn = street("dawn", self.seed).crop((x0, 0, x0 + W, H))
            mask = np.clip((np.linspace(-0.6, 1.0, H) + u * 1.2), 0, 1)[:, None] * u
            m = Image.fromarray((np.repeat(mask, W, axis=1) * 200).astype(np.uint8))
            img = Image.composite(dawn.convert("RGBA"), img, m)
        if self.shop:
            front = shop_front(self.shop)
            front = front.resize((int(front.width * K), int(front.height * K)), Image.LANCZOS)
            sx, sy = SHOP_X - front.width // 2 - x0 // 2, GROUND - front.height + 12
            img.alpha_composite(front, (sx, sy))
            u = (t - self.open_at) / 1.2 if self.open_at >= 0 else 0.0
            sh = shutter(max(0.0, u))
            if sh.height > 1:
                sh = sh.resize((int(sh.width * K), max(1, int(sh.height * K))), Image.BILINEAR)
                img.alpha_composite(sh, (sx + int(60 * K), sy + int(220 * K)))
            if self.sky == "night" and u > 0.3:  # light spills from the open shop
                glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                ImageDraw.Draw(glow).ellipse([sx - 60, GROUND - 60, sx + front.width + 60, GROUND + 140],
                                             fill=(255, 210, 120, int(70 * min(1, u))))
                img.alpha_composite(glow)
        if self.clock:
            hh, mm = (int(v) for v in self.clock.split(":"))
            back = max(0.0, 1 - t / 1.2) * 4  # the minute hand comes round the last minutes, then rests
            face = clock(hh, mm - back, self.clock_label)
            img.alpha_composite(face, (CLOCK_XY[0] - face.width // 2 - x0 // 2, CLOCK_XY[1] - face.width // 2))
        bubbles = []
        for a in self.cast:
            xy = (a.x * W - x0 // 2, GROUND - a.size * 0.38)
            code = bw_local.character(a.icon)
            bw_local.draw(img, code, xy, a.size, t, a.at, a.mood, a.say_at if a.mood else None, flip=a.flip)
            if a.say:
                bubbles.append((a.say, (xy[0], xy[1] - a.size * 0.5), t, a.say_at, -1 if a.x > 0.5 else 1))
        for args in bubbles:
            bw_local.say(img, *args)
        return img


SKIES = ("night", "dawn", "day", "sunset")
SPOTS = {0: [], 1: [0.86], 2: [0.8, 0.93]}  # right of the shop (the mascot stands bottom left)


def cue_at(scene, words, cue: str) -> float:
    """Seconds into the scene when the narration says `cue` (-1: never)."""
    from modules import bw_render

    tok = bw_render._tok(cue or "")
    if not tok:
        return -1.0
    hit = next((w.start for w in words if scene.start - 0.05 <= w.start < scene.end
                and (bw_render._tok(w.text) == tok or (len(tok) > 3 and bw_render._tok(w.text).startswith(tok)))), None)
    return -1.0 if hit is None else max(0.0, hit - scene.start)


def from_scene(scene, words, react_time) -> Moment:
    """A script's moment scene: "sky", "clock" ("10:00") and "clock_label", "shop" (its sign), "shutter" (the word
    the shutter rolls up on), "sunrise" (the word the sun starts coming up on), and 0-2 locals standing in it."""
    o = scene.opts
    locals_ = [el for el in scene.elements if el.kind == "local"][:2]
    cast = [Actor(str(el.opts.get("icon") or ""), x, at=max(0.15, el.at - scene.start), say=str(el.opts.get("say") or ""),
                  say_at=max(0.5, react_time(el) - scene.start), mood=str(el.opts.get("mood") or ""), flip=True)
            for el, x in zip(locals_, SPOTS[len(locals_)])]
    clock = str(o.get("clock") or "")
    return Moment(sky=str(o.get("sky") or "day") if o.get("sky") in SKIES else "day",
                  clock=clock if CLOCK.fullmatch(clock) else "", clock_label=str(o.get("clock_label") or "").upper()[:18],
                  shop=str(o.get("shop") or "")[:12], open_at=cue_at(scene, words, str(o.get("shutter") or "")),
                  sunrise_at=cue_at(scene, words, str(o.get("sunrise") or "")), duration=scene.end - scene.start,
                  seed=len(scene.narration), cast=cast)
