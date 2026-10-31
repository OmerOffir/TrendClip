"""Price Tag Why's look: Borderline Why's whiteboard renderer (modules.bw_fx, modules.bw_brand) in the channel's own
colours, so the two channels never look alike.

- black ink, money-green accents, red for what something costs (board lines may name their colour: green / red, and
  strike: true crosses a line out in red);
- the signature opening, a scene of kind "tag": the marker hand draws a price tag on the whiteboard, writes the
  question on it, then the shocking price above it (scene option tag: {question: [...], price: "99¢", note: "since 1992"});
- no mascot (the red panda is Borderline Why's), the marker hand stays;
- the tag and its question are already on the first frame (the hook and the thumbnail); the hand writes the price;
- drawn scenes of their own (all written by the marker hand, a scene option named like the kind):
    number   {label, from, to, note}: a big figure, counting up from `from` to `to` (an arrow between them)
    compare  {title, left: {label, price}, right: {label, price}, winner: left|right}: two price tags side by side
    chart    {title, unit, lines: [{label, color, points: [[x, value], ...], end}]}: a hand-drawn line chart
  and Borderline Why's map and photo scenes;
- the question stays at the top of every drawn scene, on a small price tag;
- icons pop in on board and tag scenes too (Noto emoji, Apache 2.0; the owner's GIFs when one has the name);
- a scene of kind "follow" ends the video: the narrator says the follow line over the channel's end card.

use_style() changes module-wide colours, so a Price Tag Why video is drawn in a process of its own
(python -m modules.pw_channel make ...), never in the dashboard's process next to a Borderline Why render.
"""

from __future__ import annotations

import math
import re
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
        self.late = len(self.items)  # what comes after this is written while the voice says it
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
        """The tag and its question are there on the first frame (the hook, and what the thumbnail shows); the hand
        writes the price and the note from a moment in."""
        super()._time(scene)
        early, late = self.items[:self.late], self.items[self.late:]
        for it in early:
            it["at"], it["dur"] = scene.start - 10.0, 0.01
        if late:
            shift = scene.start + 0.25 - late[0]["at"]
            for it in late:
                it["at"] += shift
            self.done += shift


NUMBER = re.compile(r"^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$")


def number_parts(text: str) -> tuple[str, float, str, int, bool] | None:
    """"$2.27" -> ("$", 2.27, "", 2, False); None when there is no number to count."""
    m = NUMBER.match(text.strip())
    if not m:
        return None
    digits = m.group(2)
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return m.group(1), float(digits.replace(",", "")), m.group(3), decimals, "," in digits


def counted(it: dict[str, Any], t: float) -> str:
    """A count item's text at `t`: its start value while it is written, then counting up to the final one."""
    prefix, v1, suffix, decimals, commas = it["count"]["to"]
    u = min(1.0, max(0.0, (t - it["at"] - it["dur"]) / it["count"]["dur"]))
    value = it["count"]["from"] + (v1 - it["count"]["from"]) * bw_render.ease(u)
    shown = f"{value:,.{decimals}f}" if commas else f"{value:.{decimals}f}"
    return f"{prefix}{shown}{suffix}" if u < 1 else it["count"]["text"]


def vertical_tag(cx: float, y0: float, y1: float, w: float) -> list[tuple[float, float]]:
    """A price tag standing up, its point at the top."""
    r, neck = 22, min(110.0, (y1 - y0) * 0.22)
    return (segment((cx, y0), (cx + w / 2, y0 + neck)) + segment((cx + w / 2, y0 + neck), (cx + w / 2, y1 - r), 24)
            + [(cx + w / 2 - r + r * math.cos(a), y1 - r + r * math.sin(a)) for a in np.linspace(0, math.pi / 2, 6)]
            + segment((cx + w / 2 - r, y1), (cx - w / 2 + r, y1), 24)
            + [(cx - w / 2 + r - r * math.sin(a), y1 - r + r * math.cos(a)) for a in np.linspace(0, math.pi / 2, 6)]
            + segment((cx - w / 2, y1 - r), (cx - w / 2, y0 + neck), 24) + segment((cx - w / 2, y0 + neck), (cx, y0)) + [(cx, y0)])


def loop(cx: float, cy: float, rx: float, ry: float) -> np.ndarray:
    """A hand-drawn circle around something, its ends overlapping a little."""
    return np.array([(cx + rx * math.cos(th) * (1 + 0.03 * math.sin(3 * th)), cy + ry * math.sin(th) * (1 + 0.03 * math.cos(2 * th)))
                     for th in np.linspace(math.radians(200), math.radians(200 + 380), 70)])


class Drawn(bw_brand.Board):
    """One of the channel's drawn scenes: what the hand writes and draws (Board's items), from the scene option named
    like its kind. Laid out under the question header, above the captions."""

    def __init__(self, scene: Scene):
        o = scene.opts.get(scene.kind)
        self.items: list[dict[str, Any]] = []
        portrait = bw_render.LAYOUT == "portrait"
        self.W = bw_render.W
        self.top = bw_render.MAP_TOP + (60 if portrait else 30)
        self.bottom = bw_render.CAPTION_Y - (110 if portrait else 80)
        getattr(self, "draw_" + scene.kind)(o if isinstance(o, dict) else {})
        self._time(scene)

    def frame(self, t: float):
        for it in self.items:
            if "count" in it:
                it["text"] = counted(it, t)
        return super().frame(t)

    def put_text(self, text: str, center: tuple[float, float], px: int, rgb, count_from: float | None = None) -> None:
        f = bw_fx.hand_font(px)
        width = f.getlength(text)
        it = dict(kind="write", text=text, x0=center[0] - width / 2, y=center[1], width=width, px=px, rgb=rgb,
                  cost=0.15 + 0.04 * len(text))
        parts = number_parts(text) if count_from is not None else None
        if parts:
            it["count"] = {"from": count_from, "to": parts, "dur": 1.0, "text": text}
            it["cost"] += 0.9  # time to count
        self.items.append(it)

    def put_line(self, pts, rgb, width: int = 12, cost: float | None = None) -> None:
        pts = np.array(pts, dtype=float)
        self.items.append(dict(kind="line", pts=pts, rgb=rgb, width=width,
                               cost=cost if cost is not None else min(1.6, max(0.25, bw_brand.length(pts) / 1400))))

    def put_title(self, text: str) -> float:
        """The scene's title, underlined in green; returns where the content below it starts."""
        if not text:
            return self.top
        px = fit(text, 104, self.W - 160)
        self.put_text(text, (self.W / 2, self.top), px, INK)
        half = min(self.W / 2 - 80, bw_fx.hand_font(px).getlength(text) / 2 + 20)
        y = self.top + px * 0.62
        self.put_line([(self.W / 2 - half + k * 2 * half / 30, y + 5 * math.sin(k / 4)) for k in range(31)], GREEN, 10, 0.35)
        return self.top + px + 50

    def draw_number(self, o: dict[str, Any]) -> None:
        y = self.put_title(str(o.get("label") or ""))
        x = self.W / 2
        start, final = str(o.get("from") or ""), str(o.get("to") or o.get("value") or "")
        room = self.bottom - y
        if start:
            self.put_text(start, (x, y + room * 0.12), fit(start, 170, self.W - 200), GREEN)
            a, b = y + room * 0.26, y + room * 0.42
            self.put_line(segment((x, a), (x, b), 10) + [(x, b)], INK, 11, 0.3)
            self.put_line([(x - 42, b - 48), (x, b), (x + 42, b - 48)], INK, 11, 0.2)
            first = number_parts(start)
            self.put_text(final, (x, y + room * 0.62), fit(final, 230, self.W - 160), RED, first[1] if first else 0.0)
        else:
            self.put_text(final, (x, y + room * 0.35), fit(final, 260, self.W - 160), RED, 0.0)
        if note := str(o.get("note") or ""):
            self.put_text(note, (x, y + room * (0.88 if start else 0.68)), fit(note, 64, self.W - 160), INK)

    def draw_compare(self, o: dict[str, Any]) -> None:
        y = self.put_title(str(o.get("title") or ""))
        sides = [o.get("left") or {}, o.get("right") or {}]
        winner = 0 if o.get("winner", "left") == "left" else 1
        w = min(350.0, self.W * 0.32)  # room for "vs" between them
        y0, y1 = y + 30, self.bottom - 40
        for k, side in enumerate(sides):
            cx = self.W * (0.25 if k == 0 else 0.75)
            self.put_line(wobble(vertical_tag(cx, y0, y1, w), seed=5 + k), INK, 12, 0.7)
            self.put_line([(cx + 20 * math.cos(a), y0 + 70 + 20 * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 24)], INK, 8, 0.15)
            label, price = str(side.get("label") or ""), str(side.get("price") or "")
            self.put_text(label, (cx, y0 + (y1 - y0) * 0.36), fit(label, 76, w - 60), INK)
            self.put_text(price, (cx, y0 + (y1 - y0) * 0.66), fit(price, 150, w - 50), GREEN if k == winner else RED)
        self.put_text("vs", (self.W / 2, (y0 + y1) / 2), 72, INK)
        cx = self.W * (0.25 if winner == 0 else 0.75)
        self.put_line(loop(cx, (y0 + y1) / 2, w / 2 + 34, (y1 - y0) / 2 + 30), GREEN, 13, 0.6)

    def draw_chart(self, o: dict[str, Any]) -> None:
        y = self.put_title(str(o.get("title") or ""))
        lines = [ln for ln in (o.get("lines") or []) if isinstance(ln, dict) and len(ln.get("points") or []) >= 2][:2]
        if not lines:
            return
        x0, x1, y0, y1 = 150, self.W - 120, y + 40, self.bottom - 60
        self.put_line([(x0, y0)] + segment((x0, y0), (x0, y1), 12) + segment((x0, y1), (x1, y1), 16) + [(x1, y1)], INK, 8, 0.5)
        values = [float(p[1]) for ln in lines for p in ln["points"]]
        top = max(values) * 1.25 or 1.0
        unit = str(o.get("unit") or "")
        first = lines[0]["points"]
        n = len(first)
        xs = [x0 + 60 + k * (x1 - x0 - 120) / max(1, n - 1) for k in range(n)]
        for k in (0, n - 1):
            self.put_text(str(first[k][0]), (xs[k], y1 + 48), 50, INK)
        for j, ln in enumerate(lines):
            rgb = bw_brand.pen_color(ln.get("color"), GREEN if j == 0 else RED)
            pts = [(xs[min(k, n - 1)], y1 - float(p[1]) / top * (y1 - y0)) for k, p in enumerate(ln["points"][:n])]
            dense = [q for a, b in zip(pts, pts[1:]) for q in segment(a, b, 12)] + [pts[-1]]
            self.put_line(wobble(dense, seed=9 + j, amp=2.0), rgb, 13, 1.1)
            last = float(ln["points"][min(n, len(ln["points"])) - 1][1])
            end = str(ln.get("end") or f"{unit}{last:g}")
            text = f"{ln.get('label', '')} {end}".strip()
            px = fit(text, 60, self.W * 0.5)
            lx = min(self.W - 60 - bw_fx.hand_font(px).getlength(text) / 2, pts[-1][0])
            self.put_text(text, (lx, pts[-1][1] - 52 - 64 * j), px, rgb)


DRAWN = ("number", "compare", "chart")


def header(question: str, size: tuple[int, int]):
    """The question on a small green price tag at the top of the frame (drawn once, pasted on every drawn scene)."""
    from PIL import Image, ImageDraw

    W = size[0]
    portrait = bw_render.LAYOUT == "portrait"
    x0, x1, y0, y1 = (110, W - 110, 300, 430) if portrait else (W * 0.25, W * 0.75, 28, 128)
    cy = (y0 + y1) / 2
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    outline = [(x0, cy), (x0 + 50, y0), (x1, y0), (x1, y1), (x0 + 50, y1), (x0, cy)]
    d.polygon(outline, fill=(255, 255, 255, 235))
    d.line(outline, fill=GREEN + (255,), width=7, joint="curve")
    d.ellipse([x0 + 34, cy - 11, x0 + 56, cy + 11], outline=INK + (255,), width=5)
    room = x1 - x0 - 120
    px = fit(question, 60 if portrait else 52, room)
    d.text(((x0 + 80 + x1) / 2, cy), question, font=bw_fx.hand_font(px), fill=INK + (255,), anchor="mm")
    return img


class PwRenderer(bw_fx.FxRenderer):
    SAID = SAID

    def __init__(self, spec: bw_render.Spec):
        super().__init__(spec)
        self.moods = []  # no mascot: it is Borderline Why's
        self.tags = {i: TagBoard(s) for i, s in enumerate(spec.scenes) if s.kind == "tag"}
        self.drawn = {i: Drawn(s) for i, s in enumerate(spec.scenes) if s.kind in DRAWN}
        tag = next((s.opts.get("tag") for s in spec.scenes if s.kind == "tag" and isinstance(s.opts.get("tag"), dict)), {})
        question = " ".join(str(bw_brand.pen_line(q)["text"]) for q in tag.get("question") or [])
        self.header = header(question, (bw_render.W, bw_render.H)) if question else None

    def scene_frame(self, i: int, t: float):
        scene = self.spec.scenes[i]
        if scene.kind == "follow":
            if i not in self.ends:
                prev = max(0, i - 1)
                self.ends[i] = bw_brand.EndCard(scene, self.scene_frame(prev, self.spec.scenes[prev].end - 0.05), name=NAME,
                                                handle=self.handle)
            return self.ends[i].frame(t)
        if i in self.tags:
            img = self.tags[i].frame(t)
        elif i in self.drawn:
            img = self.drawn[i].frame(t)
        else:
            img = super().scene_frame(i, t)
        if self.header is not None and scene.kind in ("board", *DRAWN):
            img.alpha_composite(self.header)
        if scene.kind in ("tag", "board", *DRAWN) and scene.elements:
            from PIL import Image

            ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
            for el in scene.elements:
                if el.kind == "icon" and el.opts.get("icon"):
                    self.icon(ov, el, self.anchor(None, el), t)
            img.alpha_composite(ov)
        return img

    handle = "@PriceTagWhy"


def restyle(spec: bw_render.Spec) -> None:
    """Borderline Why's old-paper card (what a photo scene becomes when its picture can't be found) as this channel's
    whiteboard instead: its caption or title written as the board's title, its subtitle under it."""
    for scene in spec.scenes:
        if scene.kind == "card":
            lines = [x for x in (scene.title or str(scene.opts.get("caption") or ""), scene.subtitle) if x]
            scene.kind = "board"
            scene.opts["board"] = {"write": lines or [scene.headline or "…"]}


def render(spec: bw_render.Spec, voice, out, music=None, music_volume: float = 0.08, progress=lambda f, m: None, handle: str = ""):
    """bw_render.render with this channel's renderer (call use_style first)."""
    restyle(spec)
    original = bw_fx.FxRenderer
    PwRenderer.handle = handle or PwRenderer.handle
    bw_fx.FxRenderer = PwRenderer  # bw_render.render builds bw_fx.FxRenderer for the satellite look
    try:
        return bw_render.render(spec, voice, out, music, music_volume, progress)
    finally:
        bw_fx.FxRenderer = original
