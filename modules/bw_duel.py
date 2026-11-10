"""Borderline Why: a quarrel's two sides, on screen all video long.

The "two countries argue" format: every scene is one side's line (the scene's "side": a | b, and its "voice").
The two characters (the owner's arguing chibi, each in its side's coat with its flag badge and a name plate) stand
at the bottom of the frame over EVERY scene - map, board, newspaper, street - and never leave: they pop in once, at
the first frame. The one talking plays its arguing clip (the pointing one for a shouted line) and its words appear
in a speech bubble over its own head, rimmed in its colour, each word darkening as it is said; the other one stands
and listens. So the viewer always sees who is speaking, and the bottom captions are off.

The script turns it on with the first scene's "duel": {"a": {"flag": ..., "name": "USA"}, "b": {...}, "left": "b"}.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

from modules import bw_fx, bw_local, bw_render

SIZE = 250  # bw_local draws a side family a third taller
LEFT_X, RIGHT_X, Y = 240, 800, 1425  # the characters' centres: clear of the Shorts buttons on the right edge
MASCOT_SPOT = (475, 1530)  # the mascot's last line: it steps in between the two
GUEST_SPOT, GUEST_PX = (520, 1285), 240  # a board's guest: between the two, under their bubbles
RIM = {"a": (45, 120, 225), "b": (214, 48, 49)}  # the bubble's rim and the word being said, by side
INK, UNSAID = (30, 32, 40), (165, 168, 178)
SAY_PX, ROW_W, CHUNK = 68, 520, 6  # the bubble's lettering, its rows' width, the most words it holds at once
CLIP = 2.2  # seconds before the talking clip starts over
BREAK = re.compile(r"[.!?,;:]$")


@dataclass
class Line:
    side: str
    words: list  # bw_render.Word
    start: float
    end: float
    shout: bool
    chunks: list[list[int]]  # word indexes shown together in the bubble


def chunks(words: list) -> list[list[int]]:
    """The line cut where the speaker breathes (punctuation), a long stretch cut again so the bubble stays small."""
    out: list[list[int]] = [[]]
    for k, w in enumerate(words):
        out[-1].append(k)
        if BREAK.search(w.text) and k + 1 < len(words):
            out.append([])
    cut: list[list[int]] = []
    for c in out:
        parts = -(-len(c) // CHUNK)
        size = -(-len(c) // parts)
        cut += [c[i:i + size] for i in range(0, len(c), size)]
    merged: list[list[int]] = []
    for c in cut:  # a one-word start ("Hey,") joins what follows when it fits
        if merged and len(merged[-1]) + len(c) <= 4:
            merged[-1] += c
        else:
            merged.append(c)
    return merged


class Duel:
    def __init__(self, spec, cast: dict):
        self.spec = spec
        self.cast = cast
        self.hidden = False  # while the rewind draws its earlier frames
        left = str(cast.get("left") or "b")
        self.x = {left: LEFT_X, ("a" if left == "b" else "b"): RIGHT_X}
        self.lines: list[Line] = []
        for sc in spec.scenes:
            side = str(sc.opts.get("side") or "")
            words = [w for w in spec.words if not w.who and sc.start <= w.start < sc.end]
            if side in self.x and words:
                self.lines.append(Line(side, words, words[0].start, words[-1].end, sc.narration.rstrip().endswith("!"), chunks(words)))

    @classmethod
    def from_spec(cls, spec) -> "Duel | None":
        cast = spec.scenes[0].opts.get("duel") if spec.scenes else None
        return cls(spec, cast) if isinstance(cast, dict) else None

    def line_at(self, t: float) -> Line | None:
        """The line being said, kept up until the other side answers (a short while at most)."""
        cur = None
        for k, ln in enumerate(self.lines):
            nxt = self.lines[k + 1].start if k + 1 < len(self.lines) else ln.end + 0.9
            if ln.start - 0.05 <= t < min(nxt - 0.03, ln.end + 0.9):
                cur = ln
        if cur and self.spec.line_at(t):
            return None  # the mascot is talking
        return cur

    def mascot_on(self, t: float) -> bool:
        """The mascot steps in for its own lines only: the two sides own the screen."""
        return any(line and t >= line[0].start - 0.45 for line in self.spec.lines)

    def draw(self, img: Image.Image, t: float) -> None:
        if self.hidden:
            return
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        cur = self.line_at(t)
        for n, (side, x) in enumerate(sorted(self.x.items(), key=lambda kv: kv[1])):
            who = self.cast.get(side) or {}
            code = bw_local.character(str(who.get("icon") or "character_arguing"), side)
            talking = cur is not None and cur.side == side
            on_right = x > img.width / 2
            mood = ("point" if cur.shout else "smug") if talking else ""
            # the clips face right: the one standing on the right is mirrored, so the two face each other
            since = None
            if talking:  # its clip plays again and again while it talks, then it stands and listens
                since = cur.start + (t - cur.start) // CLIP * CLIP if t < cur.end else cur.start + (cur.end - cur.start) // CLIP * CLIP
            bw_local.draw(ov, code, (x, Y), SIZE, t, 0.12 * n, mood, since, flip=on_right)
            tall = round(SIZE * 1.3)
            if who.get("flag"):  # the badge on the outer shoulder, off the face
                bw_local.badge(ov, str(who["flag"]), (x + (2 * tall * 0.36 if on_right else 0), Y), tall, t, 0.12 * n)
            if who.get("name"):
                self.plate(ov, str(who["name"]).upper(), side, x, t)
        if cur is not None:
            self.bubble(ov, cur, t)
        img.alpha_composite(ov)

    def plate(self, ov: Image.Image, name: str, side: str, x: float, t: float) -> None:
        a = min(1.0, max(0.0, (t - 0.3) / 0.3))
        if a <= 0:
            return
        f = bw_render.font(40)
        w, h = int(f.getlength(name) + 44), 58
        y = Y + round(SIZE * 1.3) * 0.5 + 14
        pill = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(pill)
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=RIM[side] + (int(240 * a),), outline=(255, 255, 255, int(255 * a)), width=3)
        d.text((w / 2, h / 2), name, font=f, fill=(255, 255, 255, int(255 * a)), anchor="mm")
        ov.alpha_composite(pill, (int(x - w / 2), int(y)))

    def bubble(self, ov: Image.Image, ln: Line, t: float) -> None:
        ch = ln.chunks[0]
        for c in ln.chunks:
            if t >= ln.words[c[0]].start - 0.05:
                ch = c
        since = ln.start - 0.05 if ch is ln.chunks[0] else ln.words[ch[0]].start - 0.05
        s = bw_fx.backout((t - since) / 0.22) if ch is ln.chunks[0] else 0.9 + 0.1 * min(1.0, (t - since) / 0.12)
        if s <= 0.02:
            return
        f = bw_fx.hand_font(SAY_PX)
        rows: list[list[int]] = [[]]
        for k in ch:
            test = " ".join(ln.words[j].text for j in rows[-1] + [k])
            if rows[-1] and f.getlength(test) > ROW_W:
                rows.append([])
            rows[-1].append(k)
        pad_x, pad_y, lead, tail, rim = 40, 26, int(SAY_PX * 1.14), 50, 9
        bw = int(max(f.getlength(" ".join(ln.words[j].text for j in r)) for r in rows) + 2 * pad_x)
        bh = int(len(rows) * lead + 2 * pad_y)
        x = self.x[ln.side]
        head = (x, Y - round(SIZE * 1.3) * 0.5 + 2)
        lo, hi = 28, ov.width - 28 - (110 if x > ov.width / 2 else 0)  # the right bubble keeps off the Shorts buttons
        x0 = int(min(max(lo, x - bw / 2 + (70 if x < ov.width / 2 else -70)), hi - bw))
        y1 = int(head[1] - tail)
        m = rim + 4
        shape = Image.new("L", (bw + 2 * m, bh + tail + 2 * m), 0)
        ds = ImageDraw.Draw(shape)
        ds.rounded_rectangle([m, m, m + bw, m + bh], radius=40, fill=255)
        tx = int(min(max(x, x0 + 70), x0 + bw - 70)) - x0 + m
        tip = (tx, m + bh + tail - 6)
        ds.polygon([(tx - 30, m + bh - 4), (tx + 30, m + bh - 4), tip], fill=255)
        layer = Image.new("RGBA", shape.size, RIM[ln.side] + (0,))
        layer.putalpha(shape.filter(ImageFilter.MaxFilter(2 * rim + 1)))  # the rim, in the speaker's colour
        layer.paste(Image.new("RGBA", shape.size, (255, 255, 255, 255)), (0, 0), shape)
        d = ImageDraw.Draw(layer)
        for r, row in enumerate(rows):
            width = f.getlength(" ".join(ln.words[j].text for j in row))
            cx = m + bw / 2 - width / 2
            cy = m + pad_y + r * lead + lead // 2
            for j in row:
                w = ln.words[j]
                fill = RIM[ln.side] if w.start <= t < w.end + 0.05 else INK if t >= w.start else UNSAID
                d.text((cx, cy), w.text, font=f, fill=fill + (255,), anchor="lm")
                cx += f.getlength(w.text + " ")
        if abs(s - 1) > 0.01:
            layer = layer.resize((max(1, int(layer.width * s)), max(1, int(layer.height * s))), Image.BILINEAR)
        px, py = int(x0 - m + tip[0] - tip[0] * s), int(y1 - bh - m + tip[1] - tip[1] * s)  # it grows from the tail's tip
        shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        shadow.putalpha(layer.getchannel("A").point(lambda v: int(v * 0.4)))
        ov.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(9)), (px + 6, py + 12))
        ov.alpha_composite(layer, (px, py))
