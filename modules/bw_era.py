"""Borderline Why's trip back in time (the owner's idea, 2026-11-07): when the story jumps to an old year, the video
rewinds like a tape (the last seconds play backward fast, the locals and the panda walking backward, tape lines
and a rewind sign), then the past scenes play as an old colour print (faded warm colours, a soft vignette, fine grain) until the narration
comes back to today, where the colour returns.

Only once per video, and only for a stretch of at most MAX_SCENES scenes starting at the first scene (not the
opening one) whose year is at least OLD years ago. The mascot and the captions stay in colour: the panda is telling
the story from today. bw_render.Renderer.frame calls it when spec.rewind is on (bw_channel: BW_REWIND).
"""

from __future__ import annotations

import math
import re
import time

import numpy as np
from PIL import Image, ImageDraw

REWIND = 0.9  # seconds the tape rewinds, at the start of the first past scene
SPAN = 6.0  # seconds of the video it rewinds through
OLD = 40  # a year at least this long ago is "the past"
MAX_SCENES = 3
FADE_IN, FADE_OUT = 0.3, 0.45
TODAY = re.compile(r"\b(today|nowadays|now|modern|still)\b", re.I)
YEAR = re.compile(r"\b(1\d{3}|20\d{2})s?\b")


def scene_year(scene) -> int | None:
    for text in (str(scene.opts.get("year") or ""), str(scene.headline or "")):
        if m := YEAR.search(text):
            return int(m.group(1))
    return None


def stretch(spec) -> list[int]:
    """The past scenes' indexes (empty: no trip back in this video)."""
    cutoff = time.localtime().tm_year - OLD
    scenes = spec.scenes
    for i, s in enumerate(scenes):
        if i == 0 or s.kind not in ("map", "photo", "board") or (scene_year(s) or 9999) > cutoff:
            continue
        out = [i]
        for j in range(i + 1, len(scenes)):
            nxt = scenes[j]
            year = scene_year(nxt)
            if len(out) >= MAX_SCENES or nxt.kind not in ("map", "photo", "board") or TODAY.search(nxt.narration or "") \
                    or (year is not None and year > cutoff):
                break
            out.append(j)
        return out
    return []


def ramp(t: float, a: float, length: float) -> float:
    return min(1.0, max(0.0, (t - a) / length)) if length > 0 else float(t >= a)


class Era:
    def __init__(self, spec):
        self.spec = spec
        self.past = stretch(spec)
        self._vig = None

    @property
    def start(self) -> float:
        return self.spec.scenes[self.past[0]].start

    @property
    def end(self) -> float:
        return self.spec.scenes[self.past[-1]].end

    def rewinding(self, t: float) -> bool:
        return bool(self.past) and self.start <= t < self.start + REWIND and self.start > 1.0

    # -- the tape rewinding --

    def rewind(self, r, t: float) -> Image.Image:
        """The frame while the tape rewinds: earlier moments, backward and fast, with tape lines over them."""
        u = (t - self.start) / REWIND
        span = min(SPAN, self.start - 0.1)
        src = max(0.0, self.start - 0.04 - span * (u ** 1.4))
        i = r.scene_at(src)
        img = r.scene_frame(i, src).convert("RGBA")
        r.overlay(img, src)  # the panda walks back too
        img = self.tape(img.convert("RGB"), t, u)
        if u > 0.8:  # a white flash into the old film
            img = Image.blend(img, Image.new("RGB", img.size, (255, 248, 230)), (u - 0.8) / 0.2 * 0.85)
        return img.convert("RGBA")

    def tape(self, img: Image.Image, t: float, u: float) -> Image.Image:
        a = np.asarray(img, dtype=np.float32)
        h, w, _ = a.shape
        rng = np.random.default_rng(int(t * 1000))
        gray = a.mean(axis=2, keepdims=True)
        a = gray + (a - gray) * 0.55  # washed out
        a[..., 0] = np.roll(a[..., 0], 7, axis=1)  # the colours slip apart
        a[..., 2] = np.roll(a[..., 2], -7, axis=1)
        a[::4] *= 0.72  # scanlines
        a[1::4] *= 0.86
        for _ in range(2):  # tearing bands rolling up the screen
            y = int((h * (1 - (u * 2.3 + rng.uniform(0, 1)) % 1)))
            band = slice(max(0, y), min(h, y + int(rng.uniform(30, 90))))
            a[band] = np.roll(a[band], int(rng.uniform(-60, 60)), axis=1) * 0.8 + 50
        a += rng.normal(0, 14, (h // 2, w // 2, 1)).repeat(2, 0).repeat(2, 1)[:h, :w]
        out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        d = ImageDraw.Draw(out)
        x, y, s = 70, 150, 64  # the rewind sign, blinking
        if int(t * 6) % 2 == 0:
            for k in range(2):
                ox = x + k * s * 0.85
                d.polygon([(ox + s, y), (ox + s, y + s), (ox, y + s / 2)], fill=(255, 255, 255), outline=(0, 0, 0))
        return out

    # -- the old film --

    def strength(self, t: float) -> float:
        if not self.past or t < self.start + REWIND - 0.2:
            return 0.0
        if t < self.end:
            return ramp(t, self.start + REWIND - 0.2, FADE_IN)
        return 1.0 - ramp(t, self.end, FADE_OUT)

    def vignette(self, w: int, h: int) -> np.ndarray:
        if self._vig is None or self._vig.shape[:2] != (h, w):
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            d = ((xx - w / 2) / (w * 0.62)) ** 2 + ((yy - h / 2) / (h * 0.6)) ** 2
            self._vig = np.clip(1 - 0.28 * d, 0.62, 1)[..., None]
        return self._vig

    def grade(self, img: Image.Image, t: float) -> Image.Image:
        """The past scenes as an old colour print (the frame unchanged outside them): the colours kept but faded and
        warm, the blacks lifted, a soft vignette and a fine grain. No black-and-white, flicker or scratches: the
        sepia film was hard on the eyes (the owner, 2026-11-07)."""
        k = self.strength(t)
        if k <= 0:
            return img
        mode = img.mode
        a = np.asarray(img.convert("RGB"), dtype=np.float32)
        h, w, _ = a.shape
        rng = np.random.default_rng(int(t * 30))
        gray = a.mean(axis=2, keepdims=True)
        a = gray + (a - gray) * 0.72  # the colours faded, not gone
        a = a * np.array([1.04, 0.98, 0.84], dtype=np.float32) + np.array([16, 10, 0], dtype=np.float32)  # warm, yellowed
        a = a * 0.9 + 18  # lifted blacks, softer whites, like a print in the sun
        a *= self.vignette(w, h)
        a += rng.normal(0, 5, (h // 2 + 1, w // 2 + 1, 1)).repeat(2, 0).repeat(2, 1)[:h, :w]  # a fine grain
        old = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        out = Image.blend(img.convert("RGB"), old, k)
        return out.convert(mode)
