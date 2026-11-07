"""Borderline Why's split screen (from the 10M-view Sicily bridge Short the owner shared, 2026-11-07): two or three
satellite strips side by side, one place each, so a size or a distance is compared at a glance instead of the
camera jumping from one part of the world to the next.

A script scene {"kind": "compare", "elements": [one "measure", "build" (a bridge) or "highlight" per strip]}: each
strip frames its element's place (or the crossing between "place" and "to"), its flag and name at the top with an
optional "title" ("Øresund Bridge"), and draws its measure or bridge on its cue. White lines part the strips.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from modules import bw_build, bw_local, bw_render
from modules.bw_render import Cam

MAX_PPD = 1100.0  # as close as the 500 m satellite stays sharp in a narrow strip
SLIDE = 0.35  # seconds a strip takes to slide down into place
HEADER_Y = 250  # each strip's flags and name, under the headline band


class Strip:
    def __init__(self, el, width: int):
        self.el, self.width = el, width
        W, H = bw_render.W, bw_render.H
        if el.feature is not None and el.feature_to is not None and el.kind in ("measure", "build"):
            path = bw_build.ends(el)
            xs, ys = [p[0] for p in path], [p[1] for p in path]
            box = (min(xs), min(ys), max(xs), max(ys))
            span = 0.45  # a crossing: its shores round it
        else:
            box = el.feature.bbox() if el.feature is not None else (0.0, 0.0, 0.0, 0.0)
            span = 0.0
        lon, lat = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        k = max(0.2, math.cos(math.radians(lat)))
        dx = max(span, (box[2] - box[0]) * 1.5, 0.05)
        dy = max(span, (box[3] - box[1]) * 1.5, 0.05)
        self.view = (lon, lat, max(2.5, min(MAX_PPD, width * 0.9 / (dx * k), (H * 0.45) / dy)))
        self.mosaics: dict[tuple, Image.Image] = {}
        names: list[str] = []
        for f in (el.feature, el.feature_to):
            if f is None:
                continue
            country = f.name if f.kind == "country" else (f.country or f.name)
            if country and country not in names:
                names.append(country)
        self.names = names
        from modules import bw_fx

        self.flags = [fl for fl in (bw_fx.usable_flag(n) for n in names) if fl]
        self.title = str(el.opts.get("title") or "")

    def camera(self, u: float) -> Cam:
        lon, lat, ppd = self.view
        return Cam(lon, lat, ppd * (1 + 0.06 * u))  # a slow push in, nothing quick

    def picture(self, sat, cam: Cam) -> Image.Image:
        """The satellite strip through `cam`: the middle `width` pixels of a full frame, its own mosaic kept."""
        from modules import bw_fx

        W, H, CY = bw_render.W, bw_render.H, bw_render.CY
        if not hasattr(sat, "span"):  # a picture source that only draws whole frames: its middle
            left = int((W - self.width) / 2)
            return sat.frame(cam).crop((left, 0, left + self.width, H))
        span = sat.span(cam)
        if span is None:
            src, px, lon0, lat0 = sat.world, bw_fx.GLOBAL_PX / 360, -180.0, 90.0
        else:
            level, i0, i1, j0, j1 = span
            px, deg = bw_fx.LEVELS[level]
            if span not in self.mosaics:
                n = int(round(px * deg))
                m = Image.new("RGB", ((i1 - i0 + 1) * n, (j1 - j0 + 1) * n))
                for i in range(i0, i1 + 1):
                    for j in range(j0, j1 + 1):
                        m.paste(sat.tile(level, i, j), ((i - i0) * n, (j - j0) * n))
                self.mosaics = {span: m}
            src, lon0, lat0 = self.mosaics[span], -180 + i0 * deg, 90 - j0 * deg
        sx, sy = px / (cam.ppd * cam.k), px / cam.ppd
        c = (cam.lon - self.width / 2 / (cam.ppd * cam.k) - lon0) * px
        f = (lat0 - cam.lat) * px - CY * sy
        return src.transform((self.width, H), Image.AFFINE, (sx, 0, c, 0, sy, f), resample=Image.BILINEAR)


class Compare:
    def __init__(self, scene, sat):
        W = bw_render.W
        els = [el for el in scene.elements if el.feature is not None][:3]
        self.scene, self.sat = scene, sat
        n = max(1, len(els))
        self.width = W // n
        self.strips = [Strip(el, self.width) for el in els]

    def tiles(self) -> set[tuple[int, int, int]]:
        """Every satellite tile the strips will cut from (for bw_fx.FxRenderer.prefetch)."""
        keys: set[tuple[int, int, int]] = set()
        if not hasattr(self.sat, "span"):
            return keys
        for s in self.strips:
            for u in (0.0, 1.0):
                span = self.sat.span(s.camera(u))
                if span is not None:
                    level, i0, i1, j0, j1 = span
                    keys |= {(level, a, b) for a in range(i0, i1 + 1) for b in range(j0, j1 + 1)}
        return keys

    def frame(self, t: float) -> Image.Image:
        W, H = bw_render.W, bw_render.H
        sc = self.scene
        u = min(1.0, max(0.0, (t - sc.start) / max(1.0, sc.end - sc.start)))
        img = Image.new("RGBA", (W, H), (12, 16, 24, 255))
        left = (W - self.width) / 2  # where a strip's middle sits on a full frame
        for k, s in enumerate(self.strips):
            shown = min(s.el.at, sc.start + 0.3 * k)
            a = bw_build.ease((t - shown) / SLIDE)
            if a <= 0:
                continue
            cam = s.camera(u)
            pic = s.picture(self.sat, cam).convert("RGBA")
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            el = s.el
            kind = str(el.opts.get("icon") or "").lower()
            if el.kind in ("measure", "build") and el.feature_to is not None:
                least = self.width * (0.75 if el.kind == "build" else 0.55)
                p0, p1 = bw_build.span(cam, el, least)
                if el.kind == "measure":
                    measure_in(ov, p0, p1, el, t, (left, left + self.width))
                elif kind == "bridge":
                    bw_build.bridge(ov, p0, p1, t, el.at)
            pic.alpha_composite(ov.crop((int(left), 0, int(left) + self.width, H)))
            self.header(pic, s, t, max(shown, sc.start))
            y = int(-H * 0.12 * (1 - a))
            img.alpha_composite(pic, (k * self.width, y))
        d = ImageDraw.Draw(img)
        for k in range(1, len(self.strips)):  # the white lines between the strips
            x = k * self.width
            d.line([x, 0, x, H], fill=(0, 0, 0, 120), width=10)
            d.line([x, 0, x, H], fill=(255, 255, 255, 255), width=5)
        return img

    def header(self, pic: Image.Image, s: Strip, t: float, at: float) -> None:
        """The strip's flags and name at its top, popping in (the name on its own line, as big as the strip
        allows); its title in a small yellow tag under them."""
        a = bw_build.backout((t - at - 0.2) / 0.4)
        if a <= 0.02:
            return
        W = self.width
        name = " + ".join(n.upper() for n in s.names[:2])
        size = 54
        while size > 28 and bw_render.font(size).getlength(name) > W - 36:
            size -= 2
        f = bw_render.font(size)
        tag = Image.new("RGBA", (W, 230), (0, 0, 0, 0))
        d = ImageDraw.Draw(tag)
        discs = [bw_local.flag_disc(fl, 30) for fl in s.flags[:2]]
        x = (W - (sum(dd.width for dd in discs) + 10 * (len(discs) - 1))) / 2
        for disc in discs:
            tag.alpha_composite(disc, (int(x), 4))
            x += disc.width + 10
        top = 4 + (discs[0].height + 8 if discs else 0)
        d.text((W / 2, top), name, font=f, fill=(255, 255, 255, 255), stroke_width=6, stroke_fill=(0, 0, 0, 255), anchor="ma")
        if s.title:
            ft = bw_render.font(34)
            tw = min(ft.getlength(s.title), W - 40)
            y = top + size + 16
            d.rounded_rectangle([W / 2 - tw / 2 - 14, y, W / 2 + tw / 2 + 14, y + 46], radius=10, fill=(255, 214, 0, 235))
            d.text((W / 2, y + 23), s.title, font=ft, fill=(20, 20, 20, 255), anchor="mm")
        if a < 0.98:
            tag = tag.resize((max(1, int(W * a)), max(1, int(230 * a))), Image.BILINEAR)
        pic.alpha_composite(tag, (int((W - tag.width) / 2), HEADER_Y))


def measure_in(ov: Image.Image, p0, p1, el, t: float, bounds: tuple[float, float]) -> None:
    """A strip's measure: the dashed line, then its length in big yellow letters along it, kept inside the strip."""
    big, small = bw_build.length_label(el)
    bw_build.measure(ov, p0, p1, big, small, t, el.at, bounds=bounds)
