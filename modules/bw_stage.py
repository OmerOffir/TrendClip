"""Borderline Why's stage scene (the owner's idea, 2026-11-07): for a few seconds the video leaves the map for a
picture of the story's place and time (a background made for it on the hub's GPU, or a public-domain painting),
with the owner's characters standing in it big, their flags at the shoulder, saying their line in a bubble.

Stage(background, cast, start, end).frame(t) draws it on the 1080x1920 frame; the renderer puts the year, the
mascot and the captions over it like any scene. The camera only drifts slowly (the owner's calm camera).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance

from modules import bw_fx, bw_local, bw_render

SIZE = 400  # a character's height on the stage (on the map they are 250)
GROUND = 1240  # where they stand: under the middle, above the captions


@dataclass
class Actor:
    icon: str  # the owner's GIF or family ("general", "soldier_surprised")
    x: float  # 0..1 across the frame
    at: float = 0.3  # seconds into the stage when it pops in
    color: str = "a"  # a side family's coat
    flag: str = ""
    say: str = ""
    say_at: float = 1.2  # seconds into the stage when it speaks
    mood: str = ""
    flip: bool = False
    size: int = SIZE


class Stage:
    def __init__(self, background: Image.Image, cast: list[Actor], start: float, end: float, drift: tuple[float, float] = (0.02, -0.01)):
        W, H = bw_render.W, bw_render.H
        bg = background.convert("RGB")
        k = max(W / bg.width, H / bg.height) * 1.12  # room to drift
        bg = bg.resize((int(bg.width * k + 0.5), int(bg.height * k + 0.5)), Image.LANCZOS)
        bg = ImageEnhance.Contrast(ImageEnhance.Color(bg).enhance(1.08)).enhance(1.04)
        a = np.asarray(bg, dtype=np.float32)
        h = a.shape[0]
        fall = np.clip((np.arange(h) / h - 0.62) / 0.38, 0, 1)[:, None, None]  # darker toward the bottom: the captions read
        a *= 1 - 0.45 * fall ** 1.5
        self.bg = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
        self.cast, self.start, self.end, self.drift = cast, start, end, drift

    def frame(self, t: float) -> Image.Image:
        W, H = bw_render.W, bw_render.H
        u = min(1.0, max(0.0, (t - self.start) / max(1.0, self.end - self.start)))
        u = u * u * (3 - 2 * u)
        zoom = 1.0 + 0.04 * u  # a slow push in, nothing quick
        cw, ch = W / zoom, H / zoom
        cx = self.bg.width / 2 + self.drift[0] * self.bg.width * (u - 0.5)
        cy = self.bg.height / 2 + self.drift[1] * self.bg.height * (u - 0.5)
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        img = self.bg.resize((W, H), Image.BILINEAR, box=box)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bubbles = []
        for a in sorted(self.cast, key=lambda a: a.x):
            at = self.start + a.at
            code = bw_local.character(a.icon, a.color)
            say_at = self.start + a.say_at
            tall = round(a.size * 1.3) if code.startswith("@") else a.size
            xy = (a.x * W, min(GROUND, bw_render.CAPTION_Y - 90 - tall * 0.5))  # feet above the captions: words touch nothing
            bw_local.draw(ov, code, xy, a.size, t, at, a.mood, say_at if a.mood else None, flip=a.flip)
            if a.flag:
                bw_local.badge(ov, a.flag, xy, tall, t, at)
            if a.say:
                bubbles.append((a.say, (xy[0], xy[1] - tall * 0.5 + 6), t, say_at, -1 if a.x > 0.5 else 1))
        for args in bubbles:  # over every character
            bw_local.say(ov, *args)
        img.alpha_composite(ov)
        return img
