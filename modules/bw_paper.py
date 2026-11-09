"""Borderline Why's newspaper of the era (the owner chose it, 2026-11-08): for a few seconds the story's moment is a
yellowed front page of its year, a gothic masthead, the headline in an old type, an engraved map of the place, and
the paper lands softly over the map behind (the calm camera: no spin, a slow settle and a slow push in).

    paper = Paper(name=MASTHEAD, date="WASHINGTON, MARCH 30, 1867", headline="ALASKA SOLD TO THE
                  UNITED STATES", deck="Russia parts with its American colony for $7,200,000", place="Alaska")
    paper.frame(t, behind)  # behind: the scene's map at that moment; t: seconds since the paper came in

The masthead is a made-up name ("The Evening Courier"), never a real paper's. Fonts are open licence (SIL OFL) from
Google Fonts, fetched once like the handwriting font.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from modules import bw_geo
from modules.bw_render import H, W

FONTS = "https://github.com/google/fonts/raw/main/ofl/"
FACES = {
    "gothic": "unifrakturmaguntia/UnifrakturMaguntia-Book.ttf",
    "bold": "oldstandardtt/OldStandard-Bold.ttf",
    "italic": "oldstandardtt/OldStandard-Italic.ttf",
    "regular": "oldstandardtt/OldStandard-Regular.ttf",
}
INK = (34, 26, 18)
PAPER = (231, 216, 182)
PW, PH = 900, 1180  # the page on screen
CENTER_Y = 800  # its centre: under the year, above the captions
SS = 2  # drawn at twice the size, then shrunk (clean edges)
LAND = 1.2  # seconds the paper takes to land
MASTHEAD = "The Evening Courier"  # made up: never a real paper's name


@lru_cache(maxsize=32)
def face(kind: str, size: int) -> ImageFont.FreeTypeFont:
    from modules.bw_fx import FX_DIR, _cached

    rel = FACES[kind]
    return ImageFont.truetype(str(_cached(FX_DIR / "fonts" / rel.split("/")[-1], FONTS + rel)), size)


def _fit(text: str, kind: str, size: int, width: float, lines: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """The biggest size (from `size` down) at which `text` wraps into at most `lines` lines of `width`."""
    while True:
        f = face(kind, size)
        out, cur = [], ""
        for w in text.split():
            test = (cur + " " + w).strip()
            if f.getlength(test) <= width or not cur:
                cur = test
            else:
                out.append(cur)
                cur = w
        out.append(cur)
        if (len(out) <= lines and max(f.getlength(x) for x in out) <= width) or size <= 20:
            return f, out
        size -= 4


def _aged(w: int, h: int, seed: int) -> Image.Image:
    """Yellowed newsprint: grain, a few soft stains, darker toward the edges, a fold across the middle."""
    rng = np.random.default_rng(seed)
    a = np.ones((h, w, 3), dtype=np.float32) * np.array(PAPER, dtype=np.float32)
    small = rng.normal(0, 1, (h // 8 + 1, w // 8 + 1)).astype(np.float32)
    blot = np.asarray(Image.fromarray(((small + 3) * 40).clip(0, 255).astype(np.uint8)).resize((w, h), Image.BICUBIC), dtype=np.float32) / 255
    a *= (0.93 + 0.1 * blot)[..., None]
    a += rng.normal(0, 5, (h, w, 1))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    edge = np.minimum.reduce([xx, w - xx, yy, h - yy]) / (0.09 * min(w, h))
    a *= (0.8 + 0.2 * np.clip(edge, 0, 1) ** 0.6)[..., None] * np.array([1.0, 0.97, 0.9])
    for _ in range(3):  # tea-coloured stains
        cx, cy, r = rng.uniform(0.1, 0.9) * w, rng.uniform(0.1, 0.9) * h, rng.uniform(0.05, 0.12) * w
        d = np.hypot(xx - cx, yy - cy) / r
        a *= 1 - 0.07 * np.exp(-d * d)[..., None] * np.array([0.6, 0.9, 1.4])
        ring = np.exp(-((d - 1) ** 2) * 40)
        a *= (1 - 0.05 * ring)[..., None]
    fold = np.exp(-((yy - h * 0.5) ** 2) / (2 * (h * 0.004) ** 2))
    a *= (1 - 0.08 * fold)[..., None]
    return Image.fromarray(a.clip(0, 255).astype(np.uint8))


def _hatch(size: tuple[int, int], gap: int, width: int, angle: float, alpha: int) -> Image.Image:
    """Parallel engraved lines over the whole box (masked to land or sea by the caller)."""
    w, h = size
    img = Image.new("L", size, 0)
    d = ImageDraw.Draw(img)
    dx = math.tan(math.radians(angle)) * h
    x = -abs(dx) - gap
    while x < w + abs(dx) + gap:
        d.line([(x, 0), (x + dx, h)], fill=alpha, width=width)
        x += gap
    return img


def engraving(place: str, w: int, h: int, label: str = "") -> Image.Image:
    """The place as an old engraved map: the sea in fine horizontal lines, other land plain with an inked coast,
    the place itself cross-hatched with a heavy outline and its name in spaced capitals."""
    world = bw_geo.world()
    feat = bw_geo.resolve(place)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if feat is None or not feat.parts:
        return img
    x0, y0, x1, y1 = feat.bbox()
    wrap = x0 < -150 or x1 > 150  # near the date line: put the far side next to it
    shift = (lambda lon: np.where(lon > 0, lon - 360, lon)) if x0 < -150 else (lambda lon: np.where(lon < 0, lon + 360, lon)) if x1 > 150 else (lambda lon: lon)
    k = 1 / max(0.2, math.cos(math.radians((y0 + y1) / 2)))
    pad = 0.35
    bw_, bh_ = (x1 - x0), (y1 - y0) * k
    scale = min(w / (bw_ * (1 + 2 * pad)), h / (bh_ * (1 + 2 * pad)))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def proj(ring: np.ndarray) -> list[tuple[float, float]]:
        lon = shift(ring[:, 0]) if wrap else ring[:, 0]
        return list(zip(w / 2 + (lon - cx) * scale, h / 2 - (ring[:, 1] - cy) * k * scale))

    lo, hi = cx - w / scale, cx + w / scale
    land, mine = Image.new("L", (w, h), 0), Image.new("L", (w, h), 0)
    coast = ImageDraw.Draw(img)
    dl, dm = ImageDraw.Draw(land), ImageDraw.Draw(mine)
    for layer, draw in (("countries", dl), ("regions", None)):
        for f in world.layers.get(layer, []):
            if layer == "regions" and f is not feat:
                continue
            for p in f.parts:
                bx0 = float(shift(np.array([p.bbox[0]]))[0]) if wrap else p.bbox[0]
                if p.bbox[3] < cy - h / scale or p.bbox[1] > cy + h / scale or bx0 > hi or bx0 + (p.bbox[2] - p.bbox[0]) < lo:
                    continue
                pts = proj(p.at(2)[0])
                if len(pts) < 3:
                    continue
                (dm if f is feat else dl).polygon(pts, fill=255)
                coast.line(pts + pts[:1], fill=INK + (200 if f is feat else 150,), width=6 if f is feat else 3)
    if feat.kind == "country":
        for p in feat.parts:
            pts = proj(p.at(2)[0])
            if len(pts) >= 3:
                dm.polygon(pts, fill=255)
    from PIL import ImageChops

    sea = ImageChops.invert(ImageChops.lighter(land, mine))
    lines = _hatch((h, w), 13, 2, 0, 110).transpose(Image.Transpose.ROTATE_90)  # the sea: level lines
    img.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (w, h), c) for c in INK], Image.composite(lines, Image.new("L", (w, h), 0), sea))))
    cross = Image.composite(_hatch((w, h), 10, 3, 35, 200), Image.new("L", (w, h), 0), mine)
    cross2 = Image.composite(_hatch((w, h), 10, 3, -35, 140), Image.new("L", (w, h), 0), mine)
    for c in (cross, cross2):
        img.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (w, h), v) for v in INK], c)))
    edge = mine.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 40 else 0).filter(ImageFilter.MaxFilter(3))
    img.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (w, h), v) for v in INK], edge)))
    name = " ".join((label or feat.name).upper())
    f = face("bold", max(28, w // 16))
    lx, ly = feat.label_point()
    px, py = proj(np.array([[lx, ly]]))[0]
    px, py = min(max(px, w * 0.25), w * 0.75), min(max(py, h * 0.2), h * 0.8)
    d = ImageDraw.Draw(img)
    d.text((px, py), name, font=f, fill=INK + (255,), anchor="mm", stroke_width=8, stroke_fill=PAPER + (255,))
    keep = Image.new("L", (w, h), 0)
    ImageDraw.Draw(keep).rectangle([10, 10, w - 10, h - 10], fill=255)
    img.putalpha(ImageChops.multiply(img.getchannel("A"), keep))  # nothing past the frame
    rule = [(10, 10), (w - 10, 10), (w - 10, h - 10), (10, h - 10), (10, 10)]
    d.line(rule, fill=INK + (255,), width=5)
    return img


@lru_cache(maxsize=4)
def page(name: str, date: str, headline: str, deck: str, place: str, label: str = "", price: str = "PRICE THREE CENTS") -> Image.Image:
    """The front page (RGBA, with its shadow)."""
    s = SS
    w, h = PW * s, PH * s
    seed = sum(map(ord, headline))
    img = _aged(w, h, seed).convert("RGBA")
    d = ImageDraw.Draw(img)
    m = 46 * s
    f, _ = _fit(name, "gothic", 104 * s, w - 2 * m, 1)
    d.text((w / 2, 112 * s), name, font=f, fill=INK, anchor="mm")
    d.line([(m, 176 * s), (w - m, 176 * s)], fill=INK, width=6 * s)
    d.line([(m, 186 * s), (w - m, 186 * s)], fill=INK, width=2 * s)
    small = face("regular", 24 * s)
    d.text((m, 207 * s), date, font=small, fill=INK, anchor="lm")
    d.text((w - m, 207 * s), price, font=small, fill=INK, anchor="rm")
    d.line([(m, 228 * s), (w - m, 228 * s)], fill=INK, width=2 * s)
    hf, lines = _fit(headline.upper(), "bold", 100 * s, w - 2 * m, 3)
    y = 258 * s
    for ln in lines:
        d.text((w / 2, y), ln, font=hf, fill=INK, anchor="mt")
        y += hf.size * 1.06
    if deck:
        y += 8 * s
        df, dlines = _fit(deck, "italic", 44 * s, w - 2 * m - 60 * s, 2)
        for ln in dlines:
            d.text((w / 2, y), ln, font=df, fill=INK, anchor="mt")
            y += df.size * 1.2
    y += 14 * s
    d.line([(w / 2 - 120 * s, y), (w / 2 + 120 * s, y)], fill=INK, width=2 * s)
    y += 26 * s
    map_w = int((w - 2 * m) * 0.62)
    map_h = int(min(h - y - 190 * s, map_w * 0.95))  # leaves room for a few lines of type under it
    eng = engraving(place, map_w, map_h, label)
    img.alpha_composite(eng, (w - m - map_w, int(y)))  # on the right: the mascot stands bottom left, over plain type
    rng = random.Random(seed)
    tf = face("regular", 17 * s)
    col_x0, col_x1 = m, w - m - map_w - 26 * s

    def column(x_a: float, x_b: float, top: float, bottom: float) -> None:
        yy = top
        while yy < bottom - 18 * s:  # columns of small type: unreadable at phone size, like a real page
            line, end = "", x_b - x_a - (rng.uniform(0, 60) * s if rng.random() < 0.12 else 0)
            while True:
                word = "".join(rng.choice("etaoinshrdlucmfwyp") for _ in range(rng.randint(2, 8)))
                if tf.getlength(line + " " + word) > end:
                    break
                line = (line + " " + word).strip()
            d.text((x_a, yy), line, font=tf, fill=INK + (190,), anchor="lt")
            yy += 25 * s

    d.line([(col_x1 + 13 * s, y), (col_x1 + 13 * s, y + map_h)], fill=INK, width=2 * s)
    column(col_x0, col_x1, y, y + map_h)
    y2 = y + map_h + 24 * s
    d.line([(m, y2 - 12 * s), (w - m, y2 - 12 * s)], fill=INK, width=2 * s)
    half = (w - 2 * m - 26 * s) / 2
    column(m, m + half, y2, h - 30 * s)
    column(m + half + 26 * s, w - m, y2, h - 30 * s)
    d.line([(m + half + 13 * s, y2), (m + half + 13 * s, h - 40 * s)], fill=INK, width=2 * s)
    img = img.resize((PW, PH), Image.LANCZOS)
    out = Image.new("RGBA", (PW + 80, PH + 80), (0, 0, 0, 0))
    shadow = Image.new("L", out.size, 0)
    ImageDraw.Draw(shadow).rectangle([48, 52, PW + 44, PH + 50], fill=150)
    out = Image.merge("RGBA", (*[Image.new("L", out.size, 0)] * 3, shadow.filter(ImageFilter.GaussianBlur(18))))
    out.alpha_composite(img, (30, 26))
    return out


@dataclass
class Paper:
    name: str
    date: str
    headline: str
    deck: str = ""
    place: str = ""
    label: str = ""

    def frame(self, t: float, behind: Image.Image) -> Image.Image:
        """The paper `t` seconds after it came in, over `behind` (dimmed and blurred as it lands)."""
        u = min(1.0, max(0.0, t / LAND))
        e = 1 - (1 - u) ** 3  # eases out: lands softly
        dim = min(1.0, t / 0.6)
        bg = behind.convert("RGB")
        if dim > 0:
            soft = bg.filter(ImageFilter.GaussianBlur(10 * dim))
            bg = Image.blend(soft, Image.new("RGB", bg.size, (12, 10, 8)), 0.5 * dim)
        img = bg.convert("RGBA")
        art = page(self.name, self.date, self.headline, self.deck, self.place, self.label)
        zoom = (0.9 + 0.08 * e) * (1 + 0.025 * max(0.0, t - LAND) / 4)  # settles, then a slow push in
        angle = -6 + 4.5 * e  # turns a little as it lands, rests slightly askew
        sprite = art.resize((int(art.width * zoom), int(art.height * zoom)), Image.LANCZOS).rotate(angle, Image.BICUBIC, expand=True)
        y = CENTER_Y + (1 - e) * 900
        if u < 1:
            sprite.putalpha(sprite.getchannel("A").point(lambda a: int(a * min(1.0, u * 3))))
        img.alpha_composite(sprite, (int(W / 2 - sprite.width / 2), int(y - sprite.height / 2)))
        return img

    @classmethod
    def from_scene(cls, scene) -> "Paper":
        """A script's paper scene: "title" the headline, "subtitle" the line under it, "kicker" the dateline (or the
        year), "focus" the place printed as an engraved map."""
        year = str(scene.opts.get("year") or "").strip()
        return cls(MASTHEAD, (scene.kicker or year).upper(), scene.title, scene.subtitle,
                   scene.focus[0] if scene.focus else "")
