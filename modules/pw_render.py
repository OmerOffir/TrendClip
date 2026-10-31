"""Price Tag Why's look: Borderline Why's whiteboard renderer (modules.bw_fx, modules.bw_brand) in the channel's own
colours, so the two channels never look alike.

- black ink, money-green accents, red for what something costs (board lines may name their colour: green / red, and
  strike: true crosses a line out in red);
- the signature opening, a scene of kind "tag": the marker hand draws a price tag on the whiteboard, writes the
  question on it, then the shocking price above it (scene option tag: {question: [...], price: "99¢", note: "since 1992"});
- no mascot (the red panda is Borderline Why's), the marker hand stays;
- icons pop in on board and tag scenes too (Noto emoji, Apache 2.0; the owner's GIFs when one has the name);
- a scene of kind "follow" ends the video: the narrator says the follow line over the channel's end card.

use_style() changes module-wide colours, so a Price Tag Why video is drawn in a process of its own
(python -m modules.pw_channel make ...), never in the dashboard's process next to a Borderline Why render.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from modules import bw_brand, bw_fx, bw_render
from modules.bw_render import Scene

NAME = "PRICE TAG WHY"
INK = (22, 22, 26)
GREEN = (22, 140, 72)
RED = (212, 36, 40)
SAID = (120, 232, 140)  # a caption word once it is said
# Noto 3D emoji (Apache 2.0) the channel's scripts may name, on top of Borderline Why's list (modules.bw_fx.ICONS)
ICONS = dict(cup_straw="1f964", tea="1f375", tv="1f4fa", moon="1f319", receipt="1f9fe", label="1f3f7", store="1f3ea",
             money_wings="1f4b8", bank="1f3e6", handshake="1f91d", megaphone="1f4e3", family="1f46a", dollar="1f4b2",
             credit_card="1f4b3", shopping_cart="1f6d2", slot_machine="1f3b0", dice="1f3b2", tulip="1f337",
             printer="1f5a8", gym="1f3cb", mobile="1f4f1", party="1f389", sparkles="2728", eyes="1f440")


def use_style() -> None:
    """Switch this process to Price Tag Why's colours (call once, before drawing)."""
    bw_brand.INK, bw_brand.BLUE, bw_brand.RED, bw_brand.GREEN = INK, GREEN, RED, GREEN
    for name, code in ICONS.items():
        bw_fx.ICONS.setdefault(name, code)


def fit(s: str, px: int, width: float) -> int:
    """The largest handwriting size up to `px` at which `s` fits in `width`."""
    while px > 40 and bw_fx.hand_font(px).getlength(s) > width:
        px -= 4
    return px


def wobble(pts: list[tuple[float, float]], seed: int = 3, amp: float = 3.0) -> np.ndarray:
    """A ruler-straight outline made a little hand-drawn."""
    rng = np.random.default_rng(seed)
    noise = np.convolve(rng.normal(0, 1, len(pts) + 8), np.ones(9) / 9, mode="same")[4:-4]
    out = np.array(pts, dtype=float)
    out[:, 0] += amp * noise
    out[:, 1] += amp * noise[::-1]
    return out


def segment(a: tuple[float, float], b: tuple[float, float], n: int = 14) -> list[tuple[float, float]]:
    return [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n)]


class TagBoard(bw_brand.Board):
    """The opening: a price tag drawn in green with its hole and string, the question written on it, then the price
    in big red letters above it and a note under the price."""

    def __init__(self, scene: Scene):
        o = scene.opts.get("tag") if isinstance(scene.opts.get("tag"), dict) else {}
        W = bw_render.W
        portrait = bw_render.LAYOUT == "portrait"
        self.items: list[dict[str, Any]] = []
        question = [bw_brand.pen_line(x) for x in (o.get("question") or [scene.headline or scene.title]) if str(x).strip()][:3]
        price, note = str(o.get("price") or ""), str(o.get("note") or "")
        top = bw_render.MAP_TOP
        if portrait:
            left, notch, right, t0, t1 = 70, 250, W - 70, top + 380, top + 840
        else:
            left, notch, right, t0, t1 = W * 0.30, W * 0.30 + 150, W - 120, top + 40, top + 560
        cy = (t0 + t1) / 2
        r = 26
        body = (segment((left, cy), (notch, t0)) + segment((notch, t0), (right - r, t0), 30)
                + [(right - r + r * math.sin(a), t0 + r - r * math.cos(a)) for a in np.linspace(0, math.pi / 2, 6)]
                + segment((right, t0 + r), (right, t1 - r), 20)
                + [(right - r + r * math.cos(a), t1 - r + r * math.sin(a)) for a in np.linspace(0, math.pi / 2, 6)]
                + segment((right - r, t1), (notch, t1), 30) + segment((notch, t1), (left, cy)) + [(left, cy)])
        self.items.append(dict(kind="line", pts=wobble(body), rgb=GREEN, width=14, cost=0.9))
        hx = (left + notch) / 2 + 40
        hole = [(hx + 24 * math.cos(a), cy + 24 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 28)]
        self.items.append(dict(kind="line", pts=np.array(hole), rgb=INK, width=9, cost=0.2))
        if portrait:  # the string: from the hole up and out of the tag's corner
            string = bw_render.curve((hx - 10, cy - 20), (40, t0 - 150), bend=-0.25, n=30)
        else:
            string = bw_render.curve((hx - 10, cy - 20), (left - 160, t0 - 40), bend=-0.25, n=30)
        self.items.append(dict(kind="line", pts=np.array(string), rgb=INK, width=7, cost=0.25))
        mid, room = (notch + right) / 2 + 20, right - notch - 110
        size = min(fit(q["text"], 118 if portrait else 100, room) for q in question) if question else 100
        lead = size * 1.15
        for k, q in enumerate(question):
            self._write_at(q, (mid, cy - lead * (len(question) - 1) / 2 + k * lead), size, INK)
        if price:
            if portrait:
                at = (W / 2, top + 120)
            else:
                at = (left / 2 + 40, top + 200)
            self._write_at(bw_brand.pen_line({"text": price, "color": "red"}), at, fit(price, 250, W - 140 if portrait else left - 40), RED)
        if note:
            at = (W / 2, top + 290) if portrait else (left / 2 + 40, top + 380)
            self._write_at(bw_brand.pen_line(note), at, fit(note, 84, W - 140 if portrait else left - 40), INK)
        self._time(scene)

    def _write_at(self, line: dict[str, Any], center: tuple[float, float], px: int, rgb) -> None:
        """Board._write at exactly this size (the tag's text is fitted to the tag, not to the frame)."""
        f = bw_fx.hand_font(px)
        width = f.getlength(line["text"])
        rgb = bw_brand.pen_color(line.get("color"), rgb)
        self.items.append(dict(kind="write", text=line["text"], x0=center[0] - width / 2, y=center[1], width=width, px=px,
                               rgb=rgb, cost=0.15 + 0.04 * len(line["text"])))
        if line.get("strike"):
            y = center[1] + px * 0.08
            x0 = center[0] - width / 2
            pts = [(x0 - 18 + k * (width + 36) / 20, y + 10 - k * 1.2) for k in range(21)]
            self.items.append(dict(kind="line", pts=np.array(pts), rgb=RED, width=12, cost=0.35))

    def _time(self, scene: Scene) -> None:
        """Board._time, started at once: the tag is the video's first frame, so its outline is drawing from frame one."""
        super()._time(scene)
        for it in self.items:
            it["at"] -= 0.3
        self.done -= 0.3


class PwRenderer(bw_fx.FxRenderer):
    SAID = SAID

    def __init__(self, spec: bw_render.Spec):
        super().__init__(spec)
        self.moods = []  # no mascot: it is Borderline Why's
        self.tags = {i: TagBoard(s) for i, s in enumerate(spec.scenes) if s.kind == "tag"}

    def scene_frame(self, i: int, t: float):
        scene = self.spec.scenes[i]
        if scene.kind == "follow":
            if i not in self.ends:
                prev = max(0, i - 1)
                self.ends[i] = bw_brand.EndCard(scene, self.scene_frame(prev, self.spec.scenes[prev].end - 0.05), name=NAME,
                                                handle=self.handle)
            return self.ends[i].frame(t)
        img = self.tags[i].frame(t) if i in self.tags else super().scene_frame(i, t)
        if scene.kind in ("tag", "board") and scene.elements:
            from PIL import Image

            ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
            for el in scene.elements:
                if el.kind == "icon" and el.opts.get("icon"):
                    self.icon(ov, el, self.anchor(None, el), t)
            img.alpha_composite(ov)
        return img

    handle = "@PriceTagWhy"


def render(spec: bw_render.Spec, voice, out, music=None, music_volume: float = 0.08, progress=lambda f, m: None, handle: str = ""):
    """bw_render.render with this channel's renderer (call use_style first)."""
    original = bw_fx.FxRenderer
    PwRenderer.handle = handle or PwRenderer.handle
    bw_fx.FxRenderer = PwRenderer  # bw_render.render builds bw_fx.FxRenderer for the satellite look
    try:
        return bw_render.render(spec, voice, out, music, music_volume, progress)
    finally:
        bw_fx.FxRenderer = original
