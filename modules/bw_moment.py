"""Borderline Why's moment scene (the owner, 2026-11-09): the video leaves the map for the everyday situation the
narration describes, drawn in code: a street of the place at the hour it speaks of, with the owner's characters in
it ("in Kashgar the sun doesn't rise until past 10 a.m." -> a dark bazaar street, the clock on 10:00 Beijing time,
the shopkeeper raising the shutter in the dark, the sun coming up late).

    Moment(sky="night", clock="10:00", clock_label="BEIJING TIME", shop="BAZAAR", sunrise=True, cast=[...]).frame(t)

The look is Borderline Why's own sketchbook (ink and watercolour on cream paper, the hand font), not Price Tag
Why's flat street; the people are the owner's GIFs (modules.bw_local).
The camera only drifts (the owner's calm camera).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

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


# --------------------------------------------------------------------------- the sketchbook look
# Borderline Why's own look (the owner, 2026-11-09: the flat street looked like Price Tag Why): ink lines with a
# wobble and watercolour washes on cream paper, the explorer's sketchbook, the hand font of the marker hand.

PEN = (44, 36, 32)
WASH = {  # sky top, sky bottom, far houses, near houses, ground, lit windows, the orb
    "night": ((34, 44, 96), (82, 86, 140), (96, 100, 140), (70, 74, 112), (88, 86, 112), (250, 210, 110), (246, 240, 214)),
    "dawn": ((150, 178, 222), (250, 210, 170), (196, 186, 190), (170, 156, 160), (214, 196, 170), (250, 236, 200), (255, 214, 120)),
    "day": ((150, 196, 236), (214, 232, 244), (224, 200, 160), (200, 168, 128), (222, 204, 170), (250, 240, 210), (255, 220, 110)),
    "sunset": ((96, 70, 150), (250, 150, 100), (190, 120, 120), (140, 90, 110), (150, 110, 110), (255, 210, 140), (255, 200, 120)),
}


def _jitter(pts, rng, amp: float = 1.6, step: float = 18.0) -> list[tuple[float, float]]:
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        for k in range(n):
            u = k / n
            out.append((x0 + (x1 - x0) * u + rng.normal(0, amp), y0 + (y1 - y0) * u + rng.normal(0, amp)))
    out.append((pts[-1][0] + rng.normal(0, amp), pts[-1][1] + rng.normal(0, amp)))
    return out


def ink(d: ImageDraw.ImageDraw, pts, rng, width: int = 4, color=PEN) -> None:
    """A hand-inked line: wobbling, gone over twice."""
    for _ in range(2):
        d.line(_jitter(list(pts), rng), fill=color + (235,), width=width, joint="curve")


def rect_pts(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def wash(img: Image.Image, poly, color, rng, alpha: float = 0.85, even: float = 0.72) -> None:
    """A watercolour fill: soft edges, uneven pigment, a darker rim where the paint dried."""
    w, h = img.size
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).polygon(_jitter(list(poly), rng, 2.5, 30), fill=255)
    m = m.filter(ImageFilter.GaussianBlur(2.2))
    a = np.asarray(m, dtype=np.float32) / 255
    grain = _blotch(w, h, int(rng.integers(0, 9999)))
    edge = np.clip(a * (1 - a) * 4, 0, 1)  # the dried rim
    alpha_map = a * alpha * (even + (1 - even) * grain) + edge * 0.18
    layer = np.zeros((h, w, 4), dtype=np.float32)
    layer[..., :3] = np.array(color, np.float32) * (1 - 0.18 * edge[..., None])
    layer[..., 3] = np.clip(alpha_map, 0, 1) * 255
    img.alpha_composite(Image.fromarray(layer.astype(np.uint8), "RGBA"))


@lru_cache(maxsize=16)
def _blotch(w: int, h: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    small = rng.random((h // 40 + 2, w // 40 + 2)).astype(np.float32)
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(12))
    return np.asarray(img, dtype=np.float32) / 255


@lru_cache(maxsize=4)
def street(sky: str, seed: int, village: bool = False) -> Image.Image:
    """The sketchbook street: a watercolour sky, two rows of inked houses, the street; wider than the frame (drift).
    A village has low houses with pitched roofs and pines behind them instead of a town's tall fronts."""
    from modules import bw_paper

    top, bottom, far, near, ground, lit, orb = WASH.get(sky, WASH["day"])
    wide = W + 120
    rng = np.random.default_rng(seed)
    img = bw_paper._aged(wide, H, seed).convert("RGBA")
    yy = np.linspace(0, 1, GROUND)[:, None, None]
    sky_rgb = np.array(top, np.float32) * (1 - yy) + np.array(bottom, np.float32) * yy
    grain = _blotch(wide, GROUND, seed)[..., None]
    layer = np.concatenate([np.repeat(sky_rgb, wide, axis=1), (0.78 + 0.2 * grain) * 255], axis=2)
    img.alpha_composite(Image.fromarray(np.clip(layer, 0, 255).astype(np.uint8), "RGBA"))
    d = ImageDraw.Draw(img)
    if sky == "night":
        for _ in range(60):
            x, y, r = rng.uniform(0, wide), rng.uniform(40, GROUND - 450), rng.uniform(1.5, 3.2)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 250, 230, 220))
    ox, oy, orr = wide * 0.82, GROUND - 600, 70
    wash(img, [(ox + orr * math.cos(a), oy + orr * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 40)], orb, rng, 0.95)
    rows = ((150, 230, far), (120, 190, near)) if village else ((300, 600, far), (170, 420, near))
    for row, (hmin, hmax, color) in enumerate(rows):
        x = -rng.uniform(0, 60)
        while x < wide:
            bw = rng.uniform(170, 260) if village else rng.uniform(110, 200)
            bh = rng.uniform(hmin, hmax)
            if village and row == 0:  # a pine between the far houses
                px, ph = x + rng.uniform(30, 60), rng.uniform(300, 460)
                tree = [(px - 62, GROUND), (px, GROUND - ph), (px + 62, GROUND)]
                green = (38, 70, 66) if sky == "night" else (70, 120, 84)
                wash(img, tree, green, rng, 0.92)
                ink(d, tree, rng, 3)
                x = px + 50
            x0, x1, y0 = x, x + bw, GROUND - bh
            body = rect_pts(x0, y0, x1, GROUND)
            dome = rng.random() < 0.25 and row == 0 and not village
            wash(img, body[:-1], color, rng, 0.9)
            if village:  # a pitched roof
                roof = [(x0 - 14, y0), ((x0 + x1) / 2, y0 - bw * 0.3), (x1 + 14, y0)]
                wash(img, roof, tuple(int(c * 0.8) for c in color), rng, 0.95)
                ink(d, roof, rng, 4)
            if dome:  # a dome or a flat roof with a little tower, like an old town
                pts = [(x0 + bw * 0.2 + bw * 0.6 * u, y0 - math.sin(math.pi * u) * bw * 0.32) for u in np.linspace(0, 1, 16)]
                wash(img, pts, color, rng, 0.9)
                ink(d, pts, rng, 3)
            ink(d, body, rng, 3 if row == 0 else 4)
            for wy in np.arange(y0 + 34, GROUND - 60, 62):  # windows, arched
                for wx in np.arange(x0 + 22, x1 - 40, 46):
                    on = sky in ("night", "sunset") and rng.random() < (0.3 if village else 0.45)
                    win = [(wx, wy + 34), (wx, wy + 10), (wx + 9, wy), (wx + 18, wy + 10), (wx + 18, wy + 34)]
                    if on:
                        wash(img, win, lit, rng, 0.95)
                    ink(d, win + [win[0]], rng, 2)
            x = x1 + (rng.uniform(40, 110) if village else rng.uniform(4, 22))
    wash(img, rect_pts(0, GROUND, wide, H)[:-1], ground, rng, 0.95, even=0.9)
    ink(d, [(0, GROUND), (wide, GROUND)], rng, 5)
    for k in range(0, wide, 140):  # cobbles, sketched
        ink(d, [(k, GROUND + 60), (k + 60, GROUND + 60)], rng, 2, (90, 80, 70))
        ink(d, [(k + 70, GROUND + 140), (k + 120, GROUND + 140)], rng, 2, (90, 80, 70))
    return img


@lru_cache(maxsize=4)
def shop_front(sign: str) -> Image.Image:
    """A market stall front: a hand-lettered sign, a striped awning, the doorway the shutter covers."""
    from modules.bw_fx import hand_font

    w, h = SHOP_W, SHOP_H
    rng = np.random.default_rng(len(sign) + 3)
    img = Image.new("RGBA", (w + 20, h + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    wash(img, rect_pts(10, 10, w + 10, h + 10)[:-1], (206, 160, 112), rng)
    ink(d, rect_pts(10, 10, w + 10, h + 10), rng, 5)
    wash(img, rect_pts(30, 28, w - 10, 118)[:-1], (150, 52, 52), rng, 0.95)
    ink(d, rect_pts(30, 28, w - 10, 118), rng, 4)
    d.text(((w + 20) / 2, 74), sign.upper(), font=hand_font(78), fill=(255, 230, 160), anchor="mm")
    stripes = 8
    for k in range(stripes):
        x0 = 10 + k * w / stripes
        poly = [(x0, 132), (x0 + w / stripes, 132), (x0 + w / stripes + 6, 196), (x0 - 6, 196)]
        wash(img, poly, (214, 70, 64) if k % 2 == 0 else (246, 236, 214), rng, 0.95)
        ink(d, poly + [poly[0]], rng, 2)
    d.rectangle([60, 216, w - 40, h + 10], fill=(52, 40, 44, 255))  # the dark doorway (no street showing through)
    ink(d, rect_pts(60, 216, w - 40, h + 10), rng, 4)
    if "GAS" in sign.upper() or "FUEL" in sign.upper():  # a gas station: two pumps with a screen and a hose
        for x in (120, 290):
            body = rect_pts(x, h - 190, x + 96, h + 6)
            wash(img, body[:-1], (214, 60, 56), rng, 0.97)
            ink(d, body, rng, 4)
            screen = rect_pts(x + 16, h - 170, x + 80, h - 118)
            wash(img, screen[:-1], (240, 240, 226), rng, 0.97)
            ink(d, screen, rng, 3)
            ink(d, [(x + 26, h - 144), (x + 70, h - 144)], rng, 3)
            hose = [(x + 96, h - 150), (x + 128, h - 120), (x + 132, h - 60), (x + 110, h - 40), (x + 100, h - 84)]
            ink(d, hose, rng, 6, (206, 206, 212))
            ink(d, [(x + 100, h - 84), (x + 86, h - 96)], rng, 9, (206, 206, 212))  # the nozzle
        return img
    for k, color in enumerate(((236, 176, 70), (110, 170, 90), (214, 96, 66), (236, 200, 100))):  # baskets of goods
        x = 96 + k * 88
        pile = [(x, h), (x + 6, h - 56), (x + 34, h - 84), (x + 62, h - 56), (x + 68, h)]
        wash(img, pile, color, rng, 0.95)
        ink(d, pile + [pile[0]], rng, 2)
    return img


def shutter(u: float) -> Image.Image:
    """The rolling shutter over the doorway, `u` of the way up."""
    w, h = SHOP_W - 100, SHOP_H - 206
    shown = max(0, int(h * (1 - ease(u))))
    img = Image.new("RGBA", (w, max(1, shown)), (0, 0, 0, 0))
    if shown <= 0:
        return img
    rng = np.random.default_rng(5)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, shown - 1], fill=(170, 172, 176, 255))
    for y in range(shown - 1, 0, -24):
        ink(d, [(4, y), (w - 5, y)], rng, 2, (110, 112, 120))
    ink(d, rect_pts(1, 1, w - 2, shown - 2), rng, 3)
    return img


def clock(hh: int, mm: float, label: str) -> Image.Image:
    """A town clock in ink and wash, its hands at hh:mm, the label hand-lettered under it."""
    from modules.bw_fx import hand_font

    r = CLOCK_R
    size = 2 * r + 60
    rng = np.random.default_rng(11)
    lw = int(hand_font(56).getlength(label)) + 40 if label else 0
    width = max(size, lw)  # a long label ("US SAMOA · SUNDAY") gets room, the face stays centred
    img = Image.new("RGBA", (width, size + 90), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    cx = width / 2
    ring = [(cx + (r + 14) * math.cos(a), c + (r + 14) * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 48)]
    wash(img, ring, (120, 90, 60), rng, 0.95)
    face_pts = [(cx + r * math.cos(a), c + r * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 48)]
    wash(img, face_pts, (250, 244, 226), rng, 1.0)
    ink(d, ring, rng, 4)
    ink(d, face_pts, rng, 3)
    for k in range(12):
        a = math.radians(k * 30)
        r0 = r * (0.8 if k % 3 else 0.7)
        d.line([(cx + math.sin(a) * r0, c - math.cos(a) * r0), (cx + math.sin(a) * r * 0.9, c - math.cos(a) * r * 0.9)],
               fill=PEN, width=7 if k % 3 == 0 else 3)
    ha, ma = math.radians((hh % 12 + mm / 60) * 30), math.radians(mm * 6)
    d.line([(cx, c), (cx + math.sin(ha) * r * 0.5, c - math.cos(ha) * r * 0.5)], fill=PEN, width=11)
    d.line([(cx, c), (cx + math.sin(ma) * r * 0.78, c - math.cos(ma) * r * 0.78)], fill=PEN, width=6)
    d.ellipse([cx - 9, c - 9, cx + 9, c + 9], fill=(196, 50, 50))
    if label:
        d.text((cx, size + 40), label, font=hand_font(56), fill=(250, 244, 226), anchor="mm", stroke_width=6, stroke_fill=PEN)
    return img


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
    village: bool = False  # low houses and pines instead of a town's tall fronts
    cast: list[Actor] = field(default_factory=list)

    def frame(self, t: float) -> Image.Image:
        base = street(self.sky, self.seed, self.village)
        x0 = int(60 * ease(t / max(1.0, self.duration)))  # the camera drifts slowly
        img = base.crop((x0, 0, x0 + W, H)).copy()
        if self.sunrise_at >= 0 and t > self.sunrise_at:  # the late sun: the sky warms toward dawn from the horizon
            u = ease((t - self.sunrise_at) / 2.5)
            dawn = street("dawn", self.seed, self.village).crop((x0, 0, x0 + W, H))
            mask = np.clip((np.linspace(-0.6, 1.0, H) + u * 1.2), 0, 1)[:, None] * u
            mask[GROUND:] *= 0.35  # the street lightens less than the sky
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
                  seed=len(scene.narration), village=bool(o.get("village")), cast=cast)
