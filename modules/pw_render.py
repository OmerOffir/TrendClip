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
    product  {draw: can|bottle|box|..., label: ["ICED", "TEA"], lines: ["Should cost", {text: "$2.30", color: red}],
              photo: "a Wikimedia Commons search"}: the product drawn big (modules.pw_draw, our own drawing, never a
              logo), the lines written beside it; with only a photo (no drawing fits) it becomes a photo scene, credited
              in the description. As the first scene the product is already drawn on the first frame (the hook);
  and Borderline Why's map and photo scenes;
- a board is drawn as a "sketch": its title and lines, and a big drawing under them (board.picture, or the drawing of
  an icon it names: tv, family, truck...), so the picture explains and the words support;
- the question stays at the top of every drawn scene, on a small price tag;
- icons pop in on tag scenes and on boards without a drawing (Noto emoji, Apache 2.0; the owner's GIFs when one has the name);
- the money stories' cast are bean-head cartoon people (modules.pw_cast, the look the owner asked for on 2026-11-05):
  the hand draws the body, the face and arms live on every frame, blinking and reacting to the figures as they land
  (sad at the small result, arms up and a grin at the big one, thinking at the start of a move, a thumbs-up on the
  idea's name); a head stands under each bar and over each choice of the ask;
- a scene of kind "follow" ends the video: the narrator says the follow line while the hand draws the opening again,
  so the last frame is the first one and the Short loops.

use_style() changes module-wide colours, so a Price Tag Why video is drawn in a process of its own
(python -m modules.pw_channel make ...), never in the dashboard's process next to a Borderline Why render.
"""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np

from modules import bw_brand, bw_fx, bw_render, pw_cast, pw_draw
from modules.bw_render import Scene

NAME = "Price Tag Why"  # spelled the same everywhere: the YouTube name, the captions, the description
INK = (22, 22, 26)
GREEN = (22, 140, 72)
RED = (212, 36, 40)
SAID = (120, 232, 140)  # a caption word once it is said
TINT = {"green": (196, 232, 205), "red": (246, 204, 204), "ink": (210, 210, 216), "yellow": (255, 222, 110)}  # marker colour washed into a drawing
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


def densify(pts: list[tuple[float, float]], step: float = 26.0) -> list[tuple[float, float]]:
    """Points every `step` pixels along a polyline (so the wobble bends its edges, not just its corners)."""
    out = []
    for a, b in zip(pts, pts[1:]):
        out += segment(a, b, max(1, int(math.dist(a, b) / step)))
    return out + pts[-1:]


def predraw(board: bw_brand.Board, scene: Scene, late: int, timer=bw_brand.Board._time) -> None:
    """timer (the board's own timing), then the first `late` items already there on the scene's first frame (the hook, and what the
    thumbnail shows); the rest is written from a moment in."""
    timer(board, scene)
    early, rest = board.items[:late], board.items[late:]
    for it in early:
        it["at"], it["dur"] = scene.start - 10.0, 0.01
    if rest:
        shift = scene.start + 0.25 - rest[0]["at"]
        for it in rest:
            it["at"] += shift
        board.done += shift


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
        """The tag and its question are there on the first frame; the hand writes the price and the note."""
        predraw(self, scene, self.late)


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

    def __init__(self, scene: Scene, first: bool = False):
        o = scene.opts.get(scene.kind)
        self.items: list[dict[str, Any]] = []
        self.cast: list[dict[str, Any]] = []  # the living people and heads (modules.pw_cast.draw), drawn every frame
        portrait = bw_render.LAYOUT == "portrait"
        self.W = bw_render.W
        # under the question header; the opening sits under the yellow hook banner (hook_banner: y 135-385) instead
        self.top = (480 if first else 400) if portrait else bw_render.MAP_TOP + 30
        self.bottom = bw_render.CAPTION_Y - (90 if portrait else 80)
        self.late = 0  # items drawn before the first frame when this is the opening (the product)
        getattr(self, "draw_" + scene.kind)(o if isinstance(o, dict) else {})
        if first and self.late:
            predraw(self, scene, self.late, Drawn._time)
        else:
            self._time(scene)

    def sway(self, k: int, t: float) -> float:
        """How far person k's drawing is up or down at t: a slow breath on their own beat, and the hop when their
        mood changes (modules.pw_cast.pop), so the picture is never still."""
        dy = 9 * math.sin(2.8 * t + 1.7 * k)
        st = next((s for s in self.cast if s["kind"] == "person" and s["k"] == k), None)
        if st is not None:
            current, previous = pw_cast.mood_at(pw_cast.resolve(st["moods"], self.items), t)
            if previous is not None:
                dy += pw_cast.pop(t, current["time"])
        return dy

    def frame(self, t: float):
        for it in self.items:
            if "count" in it:
                it["text"] = counted(it, t)
            if "bob" in it:  # a person sways a little, each on their own beat, so the picture is never still
                dy = self.sway(it["bob"], t)
                if it["kind"] == "write":
                    it["y"] = it.setdefault("y0", it["y"]) + dy
                else:
                    base = it.setdefault("base", np.array(it["pts"], dtype=float))
                    it["pts"] = base + np.array([0.0, dy])
        return super().frame(t)

    def live(self, ov, t: float) -> None:
        """The faces and arms, fresh on every frame (modules.pw_cast): a person follows their body's sway."""
        for st in self.cast:
            dy = self.sway(st["k"], t) if st["kind"] == "person" else 4 * math.sin(2.8 * t + 1.7 * st["k"])
            pw_cast.draw(ov, st, self.items, t, INK, dy)

    def mood(self, k: int, expr: str, pose: str = "hang", ref: int | None = None, offset: float = 0.0, at: float | None = None) -> None:
        """Person (or head) k's mood from a moment on: when board item `ref` is done (its count-up too) plus
        `offset`, or at an absolute time; the first mood given is there from the start."""
        st = next(s for s in self.cast if s["k"] == k)
        st["moods"].append({"expr": expr, "pose": pose, "ref": ref, "offset": offset, **({"at": at} if at is not None else {})})

    def put_head(self, k: int, center: tuple[float, float], r: float, ref: int, expr: str = "neutral", seed: int = 0) -> None:
        """A head alone (the bars, the ask), popping in once item `ref` starts; its face lives like a person's."""
        scale = r / 50.0
        self.cast.append(dict(kind="head", k=k, scale=scale, dx=center[0] - pw_cast.HEAD[0] * scale, dy=center[1] - pw_cast.HEAD[1] * scale,
                              ref=ref, moods=[{"expr": expr, "pose": "hang", "ref": None, "at": -10.0}], seed=seed + 3 * k))

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

    def put_drawing(self, name, box: tuple[float, float, float, float], label: list[str] | None = None,
                    accent: str = "green", seed: int = 0, seconds: float = 1.5) -> None:
        """One of modules.pw_draw's drawings, as big as fits in box: its outline (drawn in about `seconds`), its marker
        colour (under the outline), then the label."""
        if isinstance(name, pw_draw.Drawing):  # a drawing of the board's own (the cast's bodies)
            d, k, dx, dy = pw_draw.fit(name, box)
        elif name in pw_draw.DRAWINGS:
            d, k, dx, dy = pw_draw.scaled(name, box)
        else:
            return
        at = lambda pts: [(dx + x * k, dy + y * k) for x, y in pts]
        first = len(self.items)
        total = sum(bw_brand.length(np.array(at(pts), dtype=float)) for pts, _ in d.strokes) or 1.0
        acc = RED if accent == "red" else GREEN
        wash = TINT.get(accent) or TINT["green"]
        width = int(max(9, min(15, k * 4.2)))
        for j, (pts, role) in enumerate(d.strokes):
            rgb = RED if role == "red" else acc if role == "accent" else INK
            pts_at = at(pts)
            cost = max(0.06, seconds * bw_brand.length(np.array(pts_at, dtype=float)) / total)
            self.put_line(wobble(densify(pts_at), seed=seed + j, amp=2.2), rgb, width if role != "thin" else max(6, int(width * 0.6)), cost)
        tint_w = int(width * 2.6)
        for poly, role in d.washes:
            tint = TINT["ink"] if role == "ink_wash" else TINT[role] if role in ("yellow", "red") else wash
            path = pw_draw.hatch(at(poly), tint_w * 0.75, tint_w / 2 + width / 2)
            if len(path) > 1:
                self.put_line(path, tint, tint_w, min(0.6, 0.2 + len(path) * 0.006))
                self.items[-1]["under"] = True  # the colour goes beneath the outline, so its inner lines stay seen
        words = [str(x) for x in (label or []) if str(x).strip()][:3]
        if words and d.label_at:
            lx, ly, lw = d.label_at
            room = lw * k
            px = min(fit(w, int(min(150, room * 0.5)), room) for w in words)
            lead = px * 1.05
            for j, w in enumerate(words):
                self.put_text(w, (dx + lx * k, dy + ly * k - lead * (len(words) - 1) / 2 + j * lead), px, INK)
        for it in self.items[first:]:
            it["quick"] = True  # one drawing: no pause between its strokes (Drawn._time)

    def _time(self, scene: Scene) -> None:
        """Board._time, but the strokes of one drawing follow each other without the pause between items, so a
        picture is finished early in its scene and stays up while it is talked about."""
        gap = 0.1
        need = sum(it["cost"] + (0.02 if it.get("quick") else gap) for it in self.items) - gap
        room = max(0.6, scene.end - scene.start - 1.4)
        k = min(0.8, room / max(0.1, need))  # a quick hand: the scene keeps moving
        t = scene.start + 0.35
        for it in self.items:
            it["at"], it["dur"] = t, it["cost"] * k
            t += it["dur"] + (0.02 if it.get("quick") else gap) * k
        self.done = t
    def draw_product(self, o: dict[str, Any]) -> None:
        """The product drawn big on the left, the lines (what it costs, what it should cost) written on the right."""
        lines = [bw_brand.pen_line(x) for x in (o.get("lines") or []) if str(bw_brand.pen_line(x)["text"]).strip()][:4]
        name = str(o.get("draw") or "")
        y0, y1 = self.top, self.bottom
        split = self.W * (0.5 if lines else 1.0)
        self.put_drawing(name, (50, y0, split - 20, y1), o.get("label") or [], str(o.get("color") or "green"), seed=21)
        self.late = len(self.items)
        if not lines:
            return
        cx, room = (split + self.W - 30) / 2, self.W - split - 60
        sizes = [fit(ln["text"], 210 if number_parts(ln["text"]) else 92, room) for ln in lines]
        total = sum(px * 1.25 for px in sizes)
        y = (y0 + y1) / 2 - total / 2
        for ln, px in zip(lines, sizes):
            y += px * 0.62
            default = RED if number_parts(ln["text"]) else INK
            self.put_text(ln["text"], (cx, y), px, bw_brand.pen_color(ln.get("color"), default))
            if ln.get("strike"):
                half = bw_fx.hand_font(px).getlength(ln["text"]) / 2 + 16
                self.put_line([(cx - half + k * 2 * half / 20, y + 8 - k * 1.5) for k in range(21)], RED, 12, 0.35)
            y += px * 0.63

    def draw_sketch(self, o: dict[str, Any]) -> None:
        """A board: its title underlined, its lines, and a big drawing filling the rest of the board."""
        writes = [bw_brand.pen_line(x) for x in (o.get("write") or []) if str(bw_brand.pen_line(x)["text"]).strip()][:4]
        y = self.put_title(writes[0]["text"]) if writes else self.top
        picture = str(o.get("picture") or "")
        lines = writes[1:]
        px = min([fit(ln["text"], 84 if picture else 96, self.W - 140) for ln in lines], default=84)
        if not picture:
            y = y + (self.bottom - y) / 2 - len(lines) * (px + 26) / 2
        for k, ln in enumerate(lines):
            at = y + px * 0.55
            self.put_text(ln["text"], (self.W / 2, at), px, bw_brand.pen_color(ln.get("color"), GREEN if k % 2 == 0 else RED))
            if ln.get("strike"):
                half = bw_fx.hand_font(px).getlength(ln["text"]) / 2 + 16
                self.put_line([(self.W / 2 - half + j * 2 * half / 20, at + 8 - j * 1.2) for j in range(21)], RED, 12, 0.35)
            y += px + 26
        if picture:
            self.put_drawing(picture, (90, y + 20, self.W - 90, self.bottom), o.get("label") or [], str(o.get("color") or "green"), seed=37)

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

    # ----------------------------------------------------------------- the money-story scenes (modules.pw_write STORY)

    SHIRTS = ("green", "yellow", "red", "ink")  # one tint per person in a cast, in order

    def put_person(self, k: int, name: str, box: tuple[float, float, float, float], seed: int = 0,
                   expr: str = "smile", pose: str = "hang") -> float:
        """Person k (from 0) drawn in box with their number on the badge, their name written under; returns the name's y.
        The hand draws the body (modules.pw_draw.body); the face and arms live from the moment the head is drawn
        (modules.pw_cast), starting in the mood given."""
        first = len(self.items)
        self.put_drawing(pw_draw.body(), box, [str(k + 1)], self.SHIRTS[k % len(self.SHIRTS)], seed=seed + 7 * k)
        for it in self.items[first:]:
            it["bob"] = k
        _, kk, dx, dy = pw_draw.fit(pw_draw.body(), box)
        self.cast.append(dict(kind="person", k=k, scale=kk, dx=dx, dy=dy, ref=first,  # the head's outline is the first stroke
                              moods=[{"expr": expr, "pose": pose, "ref": None, "at": -10.0}], seed=seed + 5 * k))
        y = dy + 260 * kk + 48
        if name:
            self.put_text(name.upper(), ((box[0] + box[2]) / 2, y), fit(name.upper(), 54, box[2] - box[0] + 20), INK)
        return y

    def draw_cast(self, o: dict[str, Any]) -> None:
        """The hook of a story: the people in a row (a number on each badge, the name under), swaying, there on the
        first frame. Then, each on the word that says it: their starting money under each, and the results the hook
        names (people[].result): an arrow down to it, the smallest in red, the biggest in green and circled, so the
        hand is busy through the whole hook."""
        people = [p if isinstance(p, dict) else {"name": str(p)} for p in (o.get("people") or [])][:4]
        y = self.put_title(str(o.get("title") or ""))
        n = max(1, len(people))
        slot = (self.W - 60) / n
        results = [str(p.get("result") or "") for p in people]
        if any(results):  # laid out from the bottom up, so the results sit above the captions
            res_y = self.bottom - 70
            money_y = res_y - 250
            h = money_y - 110 - (y + 20)
        else:
            h = min(560.0, (self.bottom - y) * 0.55)
            money_y = y + 20 + h + 110
        for k, p in enumerate(people):
            x0 = 30 + k * slot
            self.put_person(k, str(p.get("name") or ""), (x0 + 10, y + 20, x0 + slot - 10, y + 20 + h), seed=31,
                            expr="smile" if k % 2 == 0 else "neutral", pose="hang" if k % 2 == 0 else "hip")
        self.late = len(self.items)  # the people are there on the first frame (the hook, the thumbnail)
        cx = lambda k: 30 + k * slot + slot / 2
        for k, p in enumerate(people):
            money = str(p.get("money") or "")
            if money:
                self.put_text(money, (cx(k), money_y), fit(money, 90, slot - 24), INK)
                self.items[-1]["cue"] = money
        note = str(o.get("note") or "")
        if any(results):  # the results the hook names, each on its word; the note is left out (the figures say it)
            values = {k: (number_parts(r) or ("", 0.0, "", 0, False))[1] for k, r in enumerate(results) if r}
            best = max(values, key=values.get)
            for k in sorted(values, key=lambda j: (j != min(values, key=values.get), j)):  # the small one is said first
                r = results[k]
                self.put_line([(cx(k), money_y + 55), (cx(k), res_y - 80)], INK, 9, 0.18)
                self.put_line([(cx(k) - 22, res_y - 102), (cx(k), res_y - 76), (cx(k) + 22, res_y - 102)], INK, 9, 0.12)
                self.items[-2]["cue"] = r
                px = fit(r, 104 if k == best else 84, (self.W / 2 - 60) if k == best else slot + 20)
                half = bw_fx.hand_font(px).getlength(r) / 2
                x = min(max(cx(k), half + 50), self.W - half - 50)  # kept on the screen, the circle too
                self.put_text(r, (x, res_y), px, GREEN if k == best else RED, 0.0)
                landed = len(self.items) - 1  # the figure has counted up: the faces react to it
                if k == best:
                    self.mood(k, "grin", "up", ref=landed)
                    for j in range(len(people)):  # the others gape at the big one
                        if j != k and j not in values:
                            self.mood(j, "shocked", "hang", ref=landed)
                    self.put_line(loop(x, res_y, half + 34, px * 0.72), GREEN, 12, 0.45)
                else:
                    self.mood(k, "sad", "hang", ref=landed)
        elif note:
            self.put_text(note, (self.W / 2, min(self.bottom - 40, money_y + 150)), fit(note, 72, self.W - 120), RED)
            self.items[-1]["cue"] = note.split()[0] if note.split() else ""

    def draw_step(self, o: dict[str, Any]) -> None:
        """One person's move: what they did as the title, the person on the left, the thing they got drawn `count`
        times on the right (3 at most, big; "x151" written when there are more), the math line slammed in as it is
        said (its result counting up), and the idea's name in a green box: "This is called LEASING"."""
        y = self.put_title(str(o.get("did") or ""))
        n = max(1, int(o.get("n") or 1))
        who = str(o.get("who") or "")
        # laid out from the bottom up, so the lesson box clears the captions: the lesson, "This is called", the math
        # line, and the rest of the board for the person and the things (the owner: no empty half)
        name, count = str(o.get("draw") or ""), max(1, int(o.get("count") or 1))
        lesson = str(o.get("lesson") or "")
        lesson_px = fit(lesson.upper(), 104, self.W - 200) if lesson else 0
        lesson_y = self.bottom - lesson_px * 0.62 - 8
        ly = lesson_y - lesson_px * 0.62 - 50  # "This is called" clear of the lesson's box
        math_y = (ly - 135) if lesson else self.bottom - 90  # the math box (77 up and down) clear of that line
        pictures_bottom = math_y - 97
        h = min(500.0, pictures_bottom - (y + 10))
        h_person = min(h, math_y - 77 - 44 - 48 - (y + 10))  # the name under the person stays above the math box
        self.put_person(n - 1, who, (40, y + 10, 410, y + 10 + h_person), seed=41, expr="think", pose="chin")  # wondering what to do
        shown = min(count, 3)  # a row of big ones (tall things use the whole height)
        x0, x1 = 430, self.W - 40
        cw = (x1 - x0) / shown
        top = y + 10 + (70 if count > shown else 0)  # room for the count in the corner
        for j in range(shown):
            start = len(self.items)
            self.put_drawing(name, (x0 + j * cw + 8, top, x0 + (j + 1) * cw - 8, y + 10 + h), seed=51 + j,
                             seconds=0.7 if shown > 1 else 1.5)  # the same thing again and again is drawn quicker
            if j == 0 and len(self.items) > start:
                self.mood(n - 1, "smile", "point", ref=start)  # pointing at what they got, as it is drawn
        if count > shown:  # how many there really are, in the top right corner (the things sit centred, lower)
            self.put_text(f"x{count:,}", (x1 - 100, y + 10 + 34), 84, RED)
            self.items[-1]["cue"] = str(count)
        self.put_math(str(o.get("math") or ""), math_y)
        result = next((j for j in range(len(self.items) - 1, -1, -1) if "count" in self.items[j]), None)
        if result is not None:
            self.mood(n - 1, "grin", "up", ref=result)  # the result lands: arms up
        if lesson:
            self.put_text("This is called", (self.W / 2, ly), 54, INK)
            self.items[-1]["cue"] = "called"
            self.put_text(lesson.upper(), (self.W / 2, lesson_y), lesson_px, GREEN)
            self.items[-1]["sfx"] = "ding"
            self.mood(n - 1, "smug", "thumb", ref=len(self.items) - 1)  # the idea has a name: thumbs up
            half = bw_fx.hand_font(lesson_px).getlength(lesson.upper()) / 2 + 36
            self.put_line(wobble(densify(pw_draw.rrect(self.W / 2 - half, lesson_y - lesson_px * 0.62, self.W / 2 + half, lesson_y + lesson_px * 0.62, 24)), seed=8), GREEN, 10, 0.5)

    def put_math(self, text: str, y: float) -> None:
        """"5 x 365 x $10 = $18,250": the working in ink, the result in red counting up from 0, boxed; the result's
        digits are the cue, so it lands when the narrator says the number."""
        if not text:
            return
        left, _, result = text.rpartition("=")
        left, result = (left.strip() + " =" if left.strip() else ""), result.strip()
        px = 110
        f = bw_fx.hand_font(px)
        while px > 44 and f.getlength(left + " " + result) > self.W - 120:
            px -= 4
            f = bw_fx.hand_font(px)
        wl, wr = f.getlength(left + " ") if left else 0.0, f.getlength(result)
        x0 = self.W / 2 - (wl + wr) / 2
        if left:
            self.put_text(left, (x0 + wl / 2, y), px, INK)
            self.items[-1]["cue"] = re.sub(r"\D", "", left.split()[0])
        self.put_text(result, (x0 + wl + wr / 2, y), px, RED, 0.0)
        self.items[-1]["cue"] = re.sub(r"\D", "", result)
        self.put_line(wobble(densify(pw_draw.rrect(x0 - 40, y - px * 0.7, x0 + wl + wr + 40, y + px * 0.7, 26)), seed=4), INK, 8, 0.45)

    def draw_bars(self, o: dict[str, Any]) -> None:
        """The finale: one bar per person, their total written on top counting up, the biggest in green; heights on a
        square-root scale so the smallest bar is still a bar."""
        bars = [b for b in (o.get("bars") or []) if isinstance(b, dict)][:4]
        y = self.put_title(str(o.get("title") or ""))
        if not bars:
            return
        values = [max(0.0, float(b.get("value") or 0)) for b in bars]
        top = max(values) or 1.0
        n = len(bars)
        slot = (self.W - 100) / n
        r = min(52.0, slot * 0.2)  # each person's head under their bar, the name under it
        base, roof = self.bottom - 2 * r - 100, y + 150
        for k, (b, v) in enumerate(zip(bars, values)):
            cx = 50 + k * slot + slot / 2
            hgt = max(0.08, math.sqrt(v / top)) * (base - roof)
            w = slot * 0.62
            box = [(cx - w / 2, base - hgt), (cx + w / 2, base - hgt), (cx + w / 2, base), (cx - w / 2, base)]
            best = v >= top
            path = pw_draw.hatch(box, 30, 16)
            if len(path) > 1:
                self.put_line(path, TINT["green" if best else "ink"], 40, 0.3)
                self.items[-1]["under"] = True
            self.put_line(wobble(densify(box + box[:1]), seed=12 + k), GREEN if best else INK, 12, 0.5)
            self.put_head(k, (cx, base + r + 14), r, ref=len(self.items) - 1, seed=12)  # pops in with the bar
            text = str(b.get("text") or f"{v:,.0f}")
            self.put_text(text, (cx, base - hgt - 50), fit(text, 84, slot - 10), GREEN if best else RED, 0.0)
            self.items[-1]["cue"] = re.sub(r"\D", "", text)
            # the total lands: the big one grins, the smallest droops, the rest smile
            self.mood(k, "grin" if best else "sad" if v <= min(values) else "smile", ref=len(self.items) - 1)
            label = str(b.get("label") or "")
            if label:
                self.put_text(label.upper(), (cx, base + 2 * r + 54), fit(label.upper(), 48, slot - 10), INK)

    def draw_ask(self, o: dict[str, Any]) -> None:
        """The question to the viewer ("Which one are you?"), the choices in circles, and the line that asks for a
        comment or a share."""
        question = str(o.get("question") or "")
        y = self.top
        if question:
            px = fit(question, 112, self.W - 120)
            self.put_text(question, (self.W / 2, y + 20), px, INK)
            y += px + 80
        options = [str(x) for x in (o.get("options") or []) if str(x).strip()][:5]
        if options:
            slot = (self.W - 100) / len(options)
            r = min(90.0, slot * 0.36)
            rh = min(44.0, slot * 0.18)  # a head over each choice: it winks when its choice is said
            for k, opt in enumerate(options):
                cx = 50 + k * slot + slot / 2
                self.put_line(loop(cx, y + 110, r, r), self.SHIRTS_RGB[k % len(self.SHIRTS_RGB)], 12, 0.35)
                self.put_head(k, (cx, y + 110 + r + rh + 24), rh, ref=len(self.items) - 1, expr="smile", seed=7)
                self.put_text(opt, (cx, y + 110), fit(opt, int(r * 1.3), r * 1.6), INK)
                self.items[-1]["cue"] = opt
                self.mood(k, "wink", ref=len(self.items) - 1)
                self.mood(k, "smile", ref=len(self.items) - 1, offset=0.9)
            y += 300 + 2 * rh + 20
        if line := str(o.get("line") or ""):
            self.put_text(line, (self.W / 2, y + 40), fit(line, 76, self.W - 120), GREEN)
            self.items[-1]["cue"] = line.split()[0]

    SHIRTS_RGB = (GREEN, (214, 160, 0), RED, INK)

    def retime(self, scene: Scene, words_: list) -> None:
        """After _time: an item with a cue (the digits of a figure, a word) waits for the narrator to say it, and
        what follows it moves along, so the math lands on the number and never before."""
        window = [w for w in words_ if scene.start - 0.05 <= w.start < scene.end]
        if not window:
            return
        tok = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
        digits = lambda s: re.sub(r"\D", "", str(s))
        shift = 0.0
        for it in self.items:
            it["at"] += shift
            cue = it.get("cue")
            if not cue:
                continue
            want = digits(cue) if digits(cue) else tok(cue)
            hit = next((w.start for w in window if (digits(w.text) == want if digits(cue) else tok(w.text) == want)), None)
            if hit is None:
                continue
            at = min(hit - 0.15, scene.end - it["dur"] - 0.4)
            if at > it["at"]:
                shift += at - it["at"]
                it["at"] = at
        self.done = max(self.done, max(it["at"] + it["dur"] for it in self.items)) if self.items else self.done


def sounds(spec: bw_render.Spec, boards: dict[int, bw_brand.Board]) -> list[tuple[float, str, float]]:
    """The channel's sound moments (modules.bw_sfx mixes them, thinned): a whoosh on every cut, the marker while a
    drawing is drawn, a pop when words are written, a cash ring when a counted figure lands, a ding on the idea's name."""
    found: list[tuple[float, str, float]] = []
    for i, scene in enumerate(spec.scenes):
        if i > 0 and scene.kind != "follow":
            found.append((scene.start, "whoosh", 0.0))
        items = getattr(boards.get(i), "items", None) or []
        k = 0
        while k < len(items):
            it = items[k]
            at = float(it.get("at", -1.0))
            if at < 0.05 or at >= scene.end:  # the opening's predrawn items are there on the first frame
                k += 1
                continue
            if it.get("sfx"):
                found.append((at, it["sfx"], 0.0))
            elif it["kind"] == "write":
                found.append((at, "pop", 0.0))
            elif it.get("quick") and not it.get("under"):  # a drawing: one marker run over all its strokes
                j = k
                while j + 1 < len(items) and items[j + 1].get("quick") and items[j + 1]["kind"] == "line":
                    j += 1
                end = max(float(x.get("at", at)) + float(x.get("dur", 0)) for x in items[k:j + 1])
                found.append((at, "marker", round(min(2.0, max(0.3, end - at)), 1)))
                k = j
            if "count" in it:
                found.append((at + float(it.get("dur", 0)) + float(it["count"]["dur"]), "cash", 0.0))
            k += 1
    return found


DRAWN = ("number", "compare", "chart", "product", "sketch", "cast", "step", "bars", "ask")
STORY = ("cast", "step", "bars", "ask")  # the money-story scenes (modules.pw_write.STORY)


class Replay(bw_brand.Board):
    """The follow line's picture: the hand draws the opening's first frame again, done a moment before the video
    ends, so the last frame cuts back into the first one (the Short loops)."""

    def __init__(self, opening: bw_brand.Board, start: float, end: float):
        import copy

        late = getattr(opening, "late", 0) or len(opening.items)
        self.items = [copy.deepcopy(it) for it in opening.items[:late]]
        for it in self.items:
            it.pop("count", None)
        gap = 0.08
        need = sum(it["cost"] for it in self.items) + gap * max(0, len(self.items) - 1)
        k = max(0.05, end - start) / max(0.1, need)
        t = start
        for it in self.items:
            it["at"], it["dur"] = t, it["cost"] * k
            t += it["dur"] + gap * k
        self.done = t - gap * k


def header(question: str, size: tuple[int, int]):
    """The question on a small green price tag at the top of the frame (drawn once, pasted on every drawn scene)."""
    from PIL import Image, ImageDraw

    W = size[0]
    portrait = bw_render.LAYOUT == "portrait"
    x0, x1, y0, y1 = (110, W - 110, 190, 310) if portrait else (W * 0.25, W * 0.75, 28, 128)
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


YELLOW = (255, 214, 40)  # the hook's band, the thumbnail's yellow


def hook_banner(question: str, size: tuple[int, int]):
    """The opening's question, big: heavy black letters on a yellow marker band across the top, so the first frame
    reads like the thumbnail (the small tag header takes over from the second scene)."""
    from PIL import Image, ImageDraw

    W = size[0]
    portrait = bw_render.LAYOUT == "portrait"
    y0, y1 = (135, 385) if portrait else (20, 150)
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    words, best = question.upper().split(), None
    for n in (1, 2, 3):  # the biggest size at which it fits the band in 1-3 lines
        rows = [" ".join(words[round(k * len(words) / n):round((k + 1) * len(words) / n)]) for k in range(n)]
        rows = [r for r in rows if r]
        px = int((y1 - y0 - 30) / (len(rows) * 1.05))
        while px > 40 and max(bw_render.font(px).getlength(r) for r in rows) > W - 140:
            px -= 4
        if best is None or px > best[1]:
            best = (rows, px)
    rows, px = best
    rng = np.random.default_rng(7)  # a marker band: a slightly uneven yellow stroke, not a box
    band = [(40 + k * (W - 80) / 12, y0 + rng.uniform(-6, 6)) for k in range(13)] + \
           [(W - 40 - k * (W - 80) / 12, y1 + rng.uniform(-6, 6)) for k in range(13)]
    d.polygon(band, fill=YELLOW + (255,))
    lead = px * 1.05
    cy = (y0 + y1) / 2
    for k, r in enumerate(rows):
        d.text((W / 2, cy - lead * (len(rows) - 1) / 2 + k * lead), r, font=bw_render.font(px), fill=INK + (255,), anchor="mm")
    return img


def push(img, t: float, start: float, end: float):
    """The camera on a drawn scene: a quick punch-in as it starts, then a slow push in, so no frame stands still."""
    from PIL import Image

    u = max(0.0, t - start)
    k = 1.0 + 0.03 * min(1.0, u / max(1.0, end - start)) + 0.035 * max(0.0, 1 - u / 0.3) ** 2
    if k <= 1.001:
        return img
    W, H = img.size
    w, h = W / k, H / k
    x0, y0 = (W - w) / 2, (H - h) * 0.45
    return img.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + w, y0 + h))


class PhotoMark:
    """What the hand draws on a photo, from the scene's mark: {"draw": "sun", "note": "Glare"}: a white note stuck on
    the print's top corner, the drawing on it (modules.pw_draw), the note written under it in red, then a red arrow
    from the note into the picture. So the picture keeps moving and the drawing says what the narration says."""

    def __init__(self, mark: dict, right: bool = True):
        self.name = str(mark.get("draw") or "")
        self.note = str(mark.get("note") or "").strip()[:16]
        self.right = right

    def __call__(self, card, box: tuple[float, float, float, float], s: float):
        from PIL import Image, ImageDraw

        if self.name not in pw_draw.DRAWINGS:
            return None
        bx0, by0, bx1, by1 = box
        side = min((bx1 - bx0) * 0.36, (by1 - by0) * 0.62)
        strip = side * 0.3 if self.note else 0
        x0 = bx1 - side - 22 if self.right else bx0 + 22
        y0 = by0 + 22
        pop = bw_fx.backout(s / 0.25)
        if pop <= 0.02:
            return None
        sticky = Image.new("RGBA", (int(side), int(side + strip)), (255, 253, 245, 245))
        ImageDraw.Draw(sticky).rectangle([0, 0, sticky.width - 1, sticky.height - 1], outline=(200, 196, 180, 255), width=3)
        d, k, dx, dy = pw_draw.scaled(self.name, (side * 0.12, side * 0.1, side * 0.88, side * 0.9))
        at = lambda pts: [(dx + x * k, dy + y * k) for x, y in pts]
        width = int(max(6, min(11, k * 4)))
        for poly, role in d.washes:  # the colour first, under the outline (it is quick on a small note)
            path = pw_draw.hatch(at(poly), width * 1.6, width * 1.4)
            if len(path) > 1 and s > 0.25:
                bw_brand.marker(sticky, np.array(path), TINT[role if role in ("yellow", "red") else "green"], int(width * 2.2), shade=False)
        total = sum(bw_brand.length(np.array(at(pts))) for pts, _ in d.strokes) or 1.0
        u = (s - 0.25) / 1.0 * total  # the outline in a second
        tip = None
        for pts, role in d.strokes:
            if u <= 0:
                break
            line = np.array(densify(at(pts), 10))
            n = bw_brand.length(line) or 1.0
            drawn, tip = bw_brand.prefix(line, min(1.0, u / n))
            rgb = RED if role == "red" else INK
            bw_brand.marker(sticky, drawn, rgb, width if role != "thin" else max(4, int(width * 0.6)), shade=False)
            u -= n
        tip = (x0 + tip[0], y0 + tip[1]) if tip is not None and s < 1.25 else None
        v = (s - 1.25) / 0.35
        if self.note and v > 0:
            px = fit(self.note, int(strip * 0.8), side * 0.9)
            layer = Image.new("RGBA", sticky.size, (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((side / 2, side + strip * 0.4), self.note, font=bw_fx.hand_font(px), fill=RED + (255,), anchor="mm")
            reveal = int(sticky.width * min(1.0, v))
            sticky.alpha_composite(layer.crop((0, 0, reveal, sticky.height)), (0, 0))
            if v < 1:
                tip = (x0 + reveal, y0 + side + strip * 0.4)
        if pop < 1:
            sticky = sticky.resize((max(1, int(sticky.width * pop)), max(1, int(sticky.height * pop))), Image.BILINEAR)
        card.alpha_composite(sticky, (int(x0 + side / 2 - sticky.width / 2), int(y0 + (side + strip) / 2 - sticky.height / 2)))
        w = (s - 1.7) / 0.35
        if w > 0:  # the arrow from the note into the picture
            ax, ay = (x0 + side * 0.1, y0 + side + strip) if self.right else (x0 + side * 0.9, y0 + side + strip)
            ex, ey = (bx0 + bx1) / 2 + (-1 if self.right else 1) * (bx1 - bx0) * 0.08, by0 + (by1 - by0) * 0.62
            shaft = np.array([(ax + (ex - ax) * q, ay + (ey - ay) * q - 30 * math.sin(math.pi * q)) for q in np.linspace(0, 1, 30)])
            drawn, head = bw_brand.prefix(shaft, min(1.0, w))
            bw_brand.marker(card, drawn, RED, 11, shade=False)
            if w >= 1:
                ang = math.atan2(ey - shaft[-3][1], ex - shaft[-3][0])
                for side_a in (2.6, -2.6):
                    bw_brand.marker(card, np.array([(ex, ey), (ex + 36 * math.cos(ang + side_a), ey + 36 * math.sin(ang + side_a))]), RED, 11, shade=False)
            elif tip is None:
                tip = head
        return tip


class PwRenderer(bw_fx.FxRenderer):
    SAID = SAID
    LIVELY = False  # Borderline Why's opening, map beats and characters stay on that channel
    PHOTO = {"lively": True, "guest": False}  # flash, push-in, the hand underlines the caption and draws its mark

    def photo_options(self, scene: Scene) -> dict:
        mark = scene.opts.get("mark")
        k = sum(s.kind == "photo" for s in self.spec.scenes[:self.spec.scenes.index(scene)])
        return {**self.PHOTO, **({"mark": PhotoMark(mark, right=k % 2 == 0)} if isinstance(mark, dict) else {})}

    question = ""  # the script's question: the header when there is no tag scene

    def __init__(self, spec: bw_render.Spec):
        super().__init__(spec)
        self.moods = []  # no mascot: it is Borderline Why's
        self.tags = {i: TagBoard(s) for i, s in enumerate(spec.scenes) if s.kind == "tag"}
        self.drawn = {i: Drawn(s, first=i == 0) for i, s in enumerate(spec.scenes) if s.kind in DRAWN}
        for i, board in self.drawn.items():
            if spec.scenes[i].kind in STORY:
                board.retime(spec.scenes[i], spec.words)
        tag = next((s.opts.get("tag") for s in spec.scenes if s.kind == "tag" and isinstance(s.opts.get("tag"), dict)), {})
        question = " ".join(str(bw_brand.pen_line(q)["text"]) for q in tag.get("question") or []) or self.question
        spec.sfx_extra = sounds(spec, {**self.tags, **self.drawn})
        self.header = header(question, (bw_render.W, bw_render.H)) if question else None
        self.banner = hook_banner(question, (bw_render.W, bw_render.H)) if question else None
        self.replays: dict[int, Replay] = {}

    def with_header(self, i: int) -> bool:
        return self.header is not None and self.spec.scenes[i].kind in ("board", *DRAWN)

    def scene_frame(self, i: int, t: float):
        scene = self.spec.scenes[i]
        if scene.kind == "follow":
            opening = self.tags.get(0) or self.drawn.get(0)
            if opening is None:
                return self.scene_frame(max(0, i - 1), t)
            if i not in self.replays:
                self.replays[i] = Replay(opening, scene.start + 0.2, self.spec.duration - 0.75)
            img = self.replays[i].frame(t)
            if self.with_header(0):
                img.alpha_composite(self.banner)  # the last frame is the first one: the Short loops
            return img
        if i in self.tags:
            img = self.tags[i].frame(t)
        elif i in self.drawn:
            img = self.drawn[i].frame(t)
        else:
            img = super().scene_frame(i, t)
        if i in self.tags or i in self.drawn:
            img = push(img, t, scene.start, scene.end)
        if self.with_header(i):
            img.alpha_composite(self.banner if i == 0 else self.header)
        if scene.kind in ("tag", "board", *DRAWN) and scene.elements and not scene.opts.get("_drawn_icons"):
            from PIL import Image

            ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
            for el in scene.elements:
                if el.kind == "icon" and el.opts.get("icon"):
                    self.icon(ov, el, self.anchor(None, el), t)
            img.alpha_composite(ov)
        return img

    handle = "@PriceTagWhy"


def guess_drawing(o: dict[str, Any]) -> str:
    """The drawing a product's words name (its lines, label, photo search: "Clocks today" -> clock), else a receipt."""
    text = " ".join(str(x.get("text") if isinstance(x, dict) else x) for x in
                    [*(o.get("lines") or []), *(o.get("label") or []), o.get("photo") or ""]).lower()
    names = {**{n: n for n in pw_draw.DRAWINGS}, **pw_draw.FROM_ICON}
    for word in re.findall(r"[a-z]+", text):
        for name, drawing in names.items():
            if word in (name, name + "s", name + "es"):
                return drawing
    return "receipt"


def restyle(spec: bw_render.Spec) -> None:
    """Before the photos are fetched: a product with no drawing becomes a photo scene of it (drawn after all, with its
    lines, if the photo isn't found). After: Borderline Why's
    old-paper card (a photo that couldn't be found) becomes a board, and every board a sketch with a big drawing (its
    picture, else the drawing of the first icon it names; that icon is then not popped in as well)."""
    for scene in spec.scenes:
        o = scene.opts.get("product") if isinstance(scene.opts.get("product"), dict) else None
        if scene.kind == "product" and o is not None and str(o.get("draw") or "") not in pw_draw.DRAWINGS and o.get("photo"):
            scene.kind = "photo"
            scene.opts["_product"] = o  # drawn after all if the photo can't be found
            scene.opts["photo"] = str(o["photo"])
            scene.opts.setdefault("caption", " ".join(str(x) for x in o.get("label") or []) or str(o["photo"]))
        if scene.kind == "card" and isinstance(scene.opts.get("_product"), dict):
            o = scene.opts["_product"]  # the opening's photo wasn't found: draw something it names, with its lines
            scene.kind = "product"
            scene.opts["product"] = {**o, "draw": guess_drawing(o)}
            continue
        if scene.kind == "card":
            lines = [x for x in (scene.title or str(scene.opts.get("caption") or ""), scene.subtitle) if x]
            scene.kind = "board"
            scene.opts["board"] = {"write": lines or [scene.headline or "…"]}
        if scene.kind == "board":
            board = dict(scene.opts.get("board") or {})
            board.setdefault("write", [scene.headline or scene.title or "…"])
            if not board.get("picture"):
                icons = [el.opts.get("icon") for el in scene.elements if el.kind == "icon"]
                board["picture"] = next((pw_draw.FROM_ICON[n] for n in icons if n in pw_draw.FROM_ICON), "")
            if board["picture"] in pw_draw.DRAWINGS:
                scene.opts["_drawn_icons"] = True
            scene.kind = "sketch"
            scene.opts["sketch"] = board


def render(spec: bw_render.Spec, voice, out, music=None, music_volume: float = 0.08, progress=lambda f, m: None, handle: str = "",
           question: str = ""):
    """bw_render.render with this channel's renderer (call use_style first)."""
    restyle(spec)
    original = bw_fx.FxRenderer
    PwRenderer.handle = handle or PwRenderer.handle
    PwRenderer.question = question
    bw_fx.FxRenderer = PwRenderer  # bw_render.render builds bw_fx.FxRenderer for the satellite look
    try:
        return bw_render.render(spec, voice, out, music, music_volume, progress)
    finally:
        bw_fx.FxRenderer = original
