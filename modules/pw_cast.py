"""Price Tag Why's cast: bean-head cartoon people whose faces live.

The look the owner asked for (2026-11-05, after the bean-head cartoon Shorts with millions of views): a big round
white head on a small body, big eyes, a face that reacts to what the narrator says, and nobody ever standing still.
The marker hand draws the body (modules.pw_draw.person without its face: head, shirt, badge, legs); the face and the
arms are alive: modules.pw_render.Drawn draws them again on every frame, in the same marker ink, from the person's
mood at that moment. A mood is an expression and an arm pose, scheduled on the board's own items (when the result
lands, when the idea's name is written), so it moves with the narrator's words like everything else on the board.

A person: blinks on their own beat, eyes wander a little, breathes (the board's sway), pops (a hop of the whole
drawing) when the mood changes. Expressions: neutral, smile, grin, sad, shocked, think, smug, wink. Poses: hang,
hip, up, point, chin, cross, thumb. All in the drawing's own units (modules.pw_draw.person: 160 x 260, the head's
centre at HEAD); scaled into the board like any drawing.
"""

from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageDraw

from modules import bw_brand
from modules.pw_draw import Pt, ellipse, poly

HEAD = (80.0, 58.0)  # the head's centre in the drawing (modules.pw_draw.person)
EYES = ((62.0, 56.0), (98.0, 56.0))
SHOULDERS = ((46.0, 116.0), (114.0, 116.0))
EXPRESSIONS = ("neutral", "smile", "grin", "sad", "shocked", "think", "smug", "wink")
POSES = ("hang", "hip", "up", "point", "chin", "cross", "thumb")
POP = 0.4  # seconds of the hop when a mood changes
SWAP = 0.25  # seconds an arm takes to move to its next pose


# --------------------------------------------------------------------------- the face, in drawing units


def blinking(t: float, seed: int) -> bool:
    """A blink every 2-3.5 s, each person on their own beat, 0.13 s long."""
    per = 2.1 + (seed * 0.37) % 1.4
    return (t + seed * 0.71) % per < 0.13


def gaze(t: float, seed: int) -> tuple[float, float]:
    """Where the pupils wander (a few units), slowly, so the eyes are never fixed."""
    return 2.6 * math.sin(0.9 * t + seed), 1.2 * math.sin(0.6 * t + 2 * seed)


def face(expr: str, t: float, seed: int) -> tuple[list[tuple[list[Pt], str]], list[tuple[Pt, float]]]:
    """The strokes of an expression at time t: (lines as (points, role), filled dots as (centre, radius)).
    Roles: ink (the marker's width) | thin."""
    lines: list[tuple[list[Pt], str]] = []
    dots: list[tuple[Pt, float]] = []
    gx, gy = gaze(t, seed)
    blink = blinking(t, seed) and expr not in ("grin", "wink")
    for j, (ex, ey) in enumerate(EYES):
        if expr == "grin" or (expr == "wink" and j == 0):  # happy arcs: an upside-down U
            lines.append((ellipse(ex, ey + 3, 9, 8, 200, 340, 10), "ink"))
        elif blink:
            lines.append((poly((ex - 8, ey), (ex + 8, ey)), "ink"))
        elif expr == "shocked":
            lines.append((ellipse(ex, ey, 10, 11), "thin"))
            dots.append(((ex + gx * 0.5, ey + gy * 0.5), 4.0))
        elif expr == "smug":
            lines.append((poly((ex - 9, ey - 3), (ex + 9, ey - 3)), "ink"))  # the lid half down
            dots.append(((ex + gx, ey + 2), 5.0))
        elif expr == "think":
            dots.append(((ex + 3 + gx * 0.4, ey - 4), 6.0))  # looking up and away
        elif expr == "sad":
            dots.append(((ex + gx * 0.5, ey + 2), 6.0))
        else:
            dots.append(((ex + gx, ey + gy), 6.0))
    # brows
    if expr == "grin":
        for ex, _ in EYES:
            lines.append((poly((ex - 10, 38), (ex + 10, 38)), "ink"))  # raised, plain: the happy eyes are arcs already
    elif expr == "shocked":
        for ex, _ in EYES:
            lines.append((ellipse(ex, 34, 11, 5, 200, 340, 6), "ink"))
    elif expr == "sad":
        lines += [(poly((52, 44), (72, 38)), "ink"), (poly((88, 38), (108, 44)), "ink")]
    elif expr == "think":
        lines += [(poly((52, 44), (72, 42)), "ink"), (poly((88, 38), (108, 36)), "ink")]  # one brow up
    elif expr == "smug":
        lines += [(poly((52, 46), (72, 47)), "ink"), (poly((88, 47), (108, 46)), "ink")]
    else:
        for ex, _ in EYES:
            lines.append((poly((ex - 10, 42), (ex + 10, 42)), "ink"))
    # mouth
    if expr == "grin":
        lines += [(ellipse(80, 80, 24, 16, 10, 170, 14), "ink"), (poly((56, 81), (104, 81)), "ink")]
    elif expr == "smile" or expr == "wink":
        lines.append((ellipse(80, 82, 18, 10, 15, 165, 12), "ink"))
    elif expr == "sad":
        lines.append((ellipse(80, 94, 14, 8, 200, 340, 10), "ink"))
    elif expr == "shocked":
        lines.append((ellipse(80, 90, 9, 12), "ink"))
    elif expr == "think":
        lines.append((poly((70, 87), (90, 84)), "ink"))
    elif expr == "smug":
        lines.append((ellipse(86, 82, 14, 7, 20, 150, 10), "ink"))
    else:
        lines.append((ellipse(80, 84, 12, 6, 20, 160, 8), "ink"))
    return lines, dots


# --------------------------------------------------------------------------- the arms, in drawing units


def arm_points(pose: str, side: int) -> list[Pt]:
    """Shoulder, elbow, hand of one arm (side -1 left, 1 right) in a pose; a pose that needs one arm puts the
    other one hanging."""
    sx, sy = SHOULDERS[0] if side < 0 else SHOULDERS[1]
    s = float(side)
    hang = [(sx, sy), (sx + 12 * s, 150.0), (sx + 16 * s, 186.0)]  # outward is + on the right, - on the left
    if pose == "up":
        return [(sx, sy), (sx + 22 * s, 96.0), (sx + 26 * s, 64.0)]
    if pose == "hip":
        return [(sx, sy), (sx + 26 * s, 146.0), (sx + 8 * s, 164.0)]
    if pose == "cross":
        return [(sx, sy), (sx - 14 * s, 146.0), (sx - 58 * s, 142.0)]
    if side > 0:
        if pose == "point":
            return [(sx, sy), (sx + 28, 110.0), (sx + 44, 100.0)]
        if pose == "chin":
            return [(sx, sy), (sx + 18, 134.0), (sx - 16, 110.0)]
        if pose == "thumb":
            return [(sx, sy), (sx + 26, 124.0), (sx + 26, 96.0)]
    return hang


def arms(pose: str, prev: str, u: float) -> list[tuple[list[Pt], str]]:
    """Both arms, each a curve shoulder-elbow-hand, between the previous pose and this one (u 0..1), with a finger
    on a point and a thumb on a thumbs-up once the arm has arrived."""
    out: list[tuple[list[Pt], str]] = []
    for side in (-1, 1):
        a, b = arm_points(prev, side), arm_points(pose, side)
        pts = [(x0 + (x1 - x0) * u, y0 + (y1 - y0) * u) for (x0, y0), (x1, y1) in zip(a, b)]
        out.append((_curve(*pts), "arm"))
        hx, hy = pts[-1]
        if u >= 1 and side > 0:
            if pose == "point":
                out.append((poly((hx, hy), (hx + 14, hy - 6)), "thin"))
            elif pose == "thumb":
                out.append((poly((hx, hy), (hx + 4, hy - 12)), "thin"))
    return out


def _curve(a: Pt, b: Pt, c: Pt, n: int = 10) -> list[Pt]:
    """A quadratic curve through the elbow's pull."""
    return [((1 - s) ** 2 * a[0] + 2 * (1 - s) * s * b[0] + s * s * c[0], (1 - s) ** 2 * a[1] + 2 * (1 - s) * s * b[1] + s * s * c[1])
            for s in (k / n for k in range(n + 1))]


# --------------------------------------------------------------------------- a person's state on a board


def mood_at(moods: list[dict[str, Any]], t: float) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The mood in force at t and the one before it; moods carry "time" (resolved), "expr", "pose"."""
    current, previous = moods[0], None
    for m in moods[1:]:
        if m["time"] <= t:
            previous, current = current, m
    return current, previous


def resolve(moods: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each mood's time: an absolute "at", or the end of the board item it waits for ("ref": its index, plus the
    count-up when that item counts) plus "offset". Sorted."""
    out = []
    for m in moods:
        if m.get("ref") is not None and 0 <= m["ref"] < len(items):
            it = items[m["ref"]]
            time = float(it["at"]) + float(it["dur"]) + (float(it["count"]["dur"]) if "count" in it else 0.0)
        else:
            time = float(m.get("at", -10.0))
        out.append({**m, "time": time + float(m.get("offset", 0.0))})
    out.sort(key=lambda m: m["time"])
    return out


def smooth(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def pop(t: float, since: float) -> float:
    """The hop (pixels up) a mood change gives the whole drawing."""
    s = (t - since) / POP
    return -22 * math.sin(math.pi * s) if 0 <= s < 1 else 0.0


def draw(ov: Image.Image, st: dict[str, Any], items: list[dict[str, Any]], t: float, ink, dy: float = 0.0) -> None:
    """The living part of a person: the face (and, for a whole person, the arms) at time t, drawn in marker ink.
    st: kind person|head, scale, dx, dy (the drawing's place on the board), at (when the body is there, or the
    item index "ref" it follows), moods, seed. A head (the bars and the ask) draws its own outline, popping in."""
    moods = resolve(st["moods"], items)
    at = st.get("at")
    if at is None and st.get("ref") is not None:
        it = items[st["ref"]]
        at = float(it["at"]) + float(it["dur"]) if st["kind"] == "person" else float(it["at"])
    if at is None or t < at:
        return
    current, previous = mood_at(moods, t)
    k, dx, dy0 = st["scale"], st["dx"], st["dy"]
    dyy = dy + (pop(t, current["time"]) if previous is not None else 0.0)
    P = lambda pts: [(dx + x * k, dy0 + y * k + dyy) for x, y in pts]
    width = int(max(6, min(14, k * 4.2)))
    d = ImageDraw.Draw(ov)
    if st["kind"] == "head":  # the outline is live too: it pops in
        s = smooth(min(1.0, (t - at) / 0.35))
        if s < 0.05:
            return
        hx, hy = HEAD
        P = lambda pts, s=s: [(dx + (hx + (x - hx) * s) * k, dy0 + (hy + (y - hy) * s) * k + dyy) for x, y in pts]
        ring = P(ellipse(hx, hy, 50, 54, 0, 360, 40))
        d.polygon(ring, fill=(255, 255, 255, 255))
        bw_brand.marker(ov, ring, ink, width, shade=False)
    lines, dots = face(current["expr"], t, st["seed"])
    if st["kind"] == "person":
        u = min(1.0, (t - current["time"]) / SWAP) if previous is not None else 1.0
        lines += arms(current["pose"], previous["pose"] if previous is not None else current["pose"], u)
    for pts, role in lines:
        w = width if role in ("ink", "arm") else max(4, int(width * 0.6))
        bw_brand.marker(ov, P(pts), ink, w, shade=False)
        if role == "arm":  # the hand
            x, y = P(pts)[-1]
            r = width * 0.62
            d.ellipse([x - r, y - r, x + r, y + r], fill=ink + (255,))
    for (cx, cy), r in dots:
        (x, y), rr = P([(cx, cy)])[0], r * k
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=ink + (255,))


def static_strokes() -> list[tuple[list[Pt], str]]:
    """A neutral face and hanging arms as plain strokes, for the still drawing (the thumbnail)."""
    lines, dots = face("smile", 0.0, 0)
    out = [(pts, "ink" if role == "ink" else "thin") for pts, role in lines]
    out += [(pts, "ink" if role == "arm" else "thin") for pts, role in arms("hang", "hang", 1.0)]
    for (cx, cy), r in dots:
        out.append((ellipse(cx, cy, r, r, 0, 360, 12), "ink"))
        out.append((ellipse(cx, cy, r * 0.5, r * 0.5, 0, 360, 8), "ink"))
    return out
