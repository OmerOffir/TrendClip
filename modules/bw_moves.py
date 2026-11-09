"""Borderline Why's bank of scene transitions (the owner, 2026-11-09: "keep the pace high, something always happens;
special transitions like the rewind and the newspaper, a stock of them for future videos").

Each move takes the scene before (a), the scene after (b) and u in 0..1 and returns the frame between them. They play
under the mascot and the captions, which stay put. None is a quick zoom (the owner's calm camera): the page turns,
tears, closes like a spyglass or soaks in like ink, each in well under a second.

    page    an old atlas page turns from the bottom right corner, its back in paper colour, a shadow along the fold
    tear    the scene tears across like old paper and the halves part, up and down, onto the next
    spyglass the view closes into a brass spyglass ring and opens on the next place
    ink     the next scene soaks through in spreading ink blots with a dark rim
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from PIL import Image, ImageFilter

PAPER = np.array([231, 216, 182], dtype=np.float32)
INK = np.array([34, 26, 18], dtype=np.float32)
BRASS = np.array([181, 140, 62], dtype=np.float32)
SECONDS = {"page": 0.8, "tear": 0.7, "spyglass": 0.9, "ink": 0.8}


def ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


@lru_cache(maxsize=4)
def _grid(w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return xx, yy


@lru_cache(maxsize=4)
def _noise(w: int, h: int, seed: int, cell: int) -> np.ndarray:
    """Smooth noise in 0..1 (blotchy at `cell` pixels)."""
    rng = np.random.default_rng(seed)
    small = rng.random((h // cell + 2, w // cell + 2)).astype(np.float32)
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(img.filter(ImageFilter.GaussianBlur(cell / 4)), dtype=np.float32) / 255


def _arr(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB"), dtype=np.float32)


def _img(a: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def page(a: Image.Image, b: Image.Image, u: float) -> Image.Image:
    """The page turns from the bottom right corner to the top left."""
    A, B = _arr(a), _arr(b)
    h, w = A.shape[:2]
    xx, yy = _grid(w, h)
    e = ease(u)
    n = np.array([w, h], dtype=np.float32) / math.hypot(w, h)  # toward the corner the page lifts from
    reach = w * n[0] + h * n[1]
    c = reach * (1 - e * 1.08)  # where the fold line is, along n
    d = xx * n[0] + yy * n[1] - c  # > 0: past the fold, the page is lifted off: the next scene shows
    out = np.where((d > 0)[..., None], B * (1 - 0.45 * (1 - e) * np.exp(-d / 60))[..., None], A)  # a shadow along the fold
    # the turned flap: the lifted part mirrored back over the page (its back, in paper colour)
    mx, my = xx - 2 * np.maximum(-d, 0) * -n[0], yy - 2 * np.maximum(-d, 0) * -n[1]
    flap = (d < 0) & (mx >= 0) & (mx < w) & (my >= 0) & (my < h) & (e > 0)  # its mirror lies on the page
    shade = (0.74 + 0.26 * np.clip(-d / 260, 0, 1))[..., None]  # lit away from the fold
    out = np.where(flap[..., None], PAPER * shade, out)
    rim = flap & ~np.roll(flap, 3, axis=1) | flap & ~np.roll(flap, 3, axis=0)
    out[rim] = INK * 0.9 + out[rim] * 0.1  # the flap's inked edge
    return _img(out)


def tear(a: Image.Image, b: Image.Image, u: float, seed: int = 3) -> Image.Image:
    """The scene tears across and the halves part onto the next."""
    A, B = _arr(a), _arr(b)
    h, w = A.shape[:2]
    rng = np.random.default_rng(seed)
    xs = np.arange(w, dtype=np.float32)
    line = h * 0.48 + np.interp(xs, np.linspace(0, w, 28), rng.normal(0, 22, 28)) + rng.normal(0, 3, w)  # jagged
    e = ease(u) ** 1.3
    gap = e * h * 0.62
    out = B.copy()
    xx, yy = _grid(w, h)
    top_src = yy + gap  # the top half moves up by gap
    bot_src = yy - gap
    lift = line[None, :]
    fiber = 9 + 5 * _noise(w, 8, seed, 4)[0][None, :]  # the torn white edge
    for src, keep, shift in ((top_src, lambda y: y < lift, -1), (bot_src, lambda y: y >= lift, 1)):
        ys = np.clip(src, 0, h - 1).astype(np.int32)
        inside = (src >= 0) & (src < h) & keep(src)
        out = np.where(inside[..., None], A[ys, xx.astype(np.int32)], out)
        border = (src >= 0) & (src < h) & ~keep(src) & (np.abs(src - lift) < fiber)
        out = np.where(border[..., None], PAPER, out)
    if 0 < e < 1:  # a soft shadow on the next scene under each half's edge
        top_edge, bot_edge = lift - gap, lift + gap
        dist = np.minimum(np.abs(yy - top_edge), np.abs(yy - bot_edge))
        between = (yy > top_edge) & (yy < bot_edge)
        out = np.where(between[..., None], out * (1 - 0.4 * np.exp(-dist / 40))[..., None], out)
    return _img(out)


def spyglass(a: Image.Image, b: Image.Image, u: float) -> Image.Image:
    """The view closes into a brass spyglass ring, then opens on the next place."""
    A, B = _arr(a), _arr(b)
    h, w = A.shape[:2]
    xx, yy = _grid(w, h)
    cx, cy = w / 2, h * 0.42
    far = math.hypot(max(cx, w - cx), max(cy, h - cy))
    src = A if u < 0.5 else B
    k = ease(1 - u / 0.5) if u < 0.5 else ease((u - 0.5) / 0.5)
    r = 60 + (far - 60) * k
    d = np.hypot(xx - cx, yy - cy)
    out = np.where((d < r)[..., None], src, np.array([10, 9, 8], dtype=np.float32))
    ring = (d >= r) & (d < r + 26)
    glint = 0.75 + 0.25 * np.cos(np.arctan2(yy - cy, xx - cx) + 0.8)
    out = np.where(ring[..., None], BRASS * glint[..., None], out)
    inner = (d < r) & (d > r - 40)
    out = np.where(inner[..., None], out * (0.6 + 0.4 * ((r - d) / 40))[..., None], out)  # the lens darkens at its edge
    return _img(out)


def ink(a: Image.Image, b: Image.Image, u: float, seed: int = 7) -> Image.Image:
    """The next scene soaks through in spreading ink blots with a dark rim."""
    A, B = _arr(a), _arr(b)
    h, w = A.shape[:2]
    xx, yy = _grid(w, h)
    rng = np.random.default_rng(seed)
    seeds = [(rng.uniform(0.15, 0.85) * w, rng.uniform(0.15, 0.8) * h) for _ in range(5)]
    dist = np.min([np.hypot(xx - x, yy - y) for x, y in seeds], axis=0) / math.hypot(w, h)
    field = dist + 0.18 * _noise(w, h, seed, 60) + 0.04 * _noise(w, h, seed + 1, 12)
    e = ease(u)
    level = -0.05 + 0.8 * e ** 1.4  # slow at first: the blots are seen spreading
    m = field < level
    rim = (field >= level) & (field < level + 0.012) & (e > 0) & (e < 1)
    out = np.where(m[..., None], B, A)
    out = np.where(rim[..., None], INK, out)
    halo = (field >= level) & (field < level + 0.05) & (e < 1)
    out = np.where(halo[..., None], out * (1 - 0.25 * (1 - (field - level) / 0.05))[..., None], out)
    return _img(out)


MOVES = {"page": page, "tear": tear, "spyglass": spyglass, "ink": ink}

# --------------------------------------------------------------------------- where they play

MAX_MOVES = 3  # in a Short: the story's big jumps; the other scene changes keep the plain fade
GAP = 2  # two moves are more than this many scenes apart
ORDER = ("tear", "page", "spyglass", "ink")


def _jump(prev, scene) -> int:
    """How big a change the cut is (0: none worth a move)."""
    score = 0
    if prev.kind != scene.kind:
        score += 2
    if scene.kind == "map" and prev.kind == "map" and set(prev.focus) != set(scene.focus) and scene.focus:
        score += 2
    if scene.opts.get("year") and scene.opts.get("year") != prev.opts.get("year"):
        score += 1
    return score


def _pick(prev, scene) -> str:
    if scene.kind == "stage":
        return "ink"  # into the old painting: it soaks in
    if scene.kind in ("photo", "moment"):
        return "page"
    if prev.kind == "map" and scene.kind == "map":
        return "spyglass"  # off to another place
    return "tear"


def plan(spec, skip: tuple[int, ...] = ()) -> dict[int, str]:
    """{scene index: move} for the cuts that get a special transition: the biggest jumps, spread out, never the
    same move twice in a row. `skip`: cuts with an effect of their own (the rewind's first past scene)."""
    scenes = spec.scenes
    own = {"paper", "end"}  # the newspaper lands on its own; the end card has its own fade
    cand = [(i, _jump(scenes[i - 1], scenes[i])) for i in range(2, len(scenes))
            if scenes[i].kind not in own and scenes[i - 1].kind not in own and i not in skip]
    chosen: list[int] = []
    for i, score in sorted(cand, key=lambda c: (-c[1], c[0])):
        if score and len(chosen) < MAX_MOVES and all(abs(i - j) > GAP for j in chosen):
            chosen.append(i)
    out: dict[int, str] = {}
    last = ""
    for i in sorted(chosen):
        move = _pick(scenes[i - 1], scenes[i])
        if move == last or move in out.values():  # a different one each time
            move = next((m for m in ORDER if m != last and m not in out.values()), move if move != last else "tear")
        out[i] = last = move
    return out


SOUNDS = {"page": "paper", "tear": "paper", "spyglass": "whoosh", "ink": "whoosh"}


def planned(spec) -> dict[int, str]:
    """plan() as the renderer makes it (the rewind's first past scene keeps its own effect)."""
    if not getattr(spec, "moves", False):
        return {}
    past: list[int] = []
    if getattr(spec, "rewind", False):
        from modules import bw_era

        past = bw_era.stretch(spec)
    return plan(spec, skip=tuple(past[:1]))


def frame(name: str, a: Image.Image, b: Image.Image, u: float) -> Image.Image:
    """The move `name` between a and b, in b's mode."""
    if u >= 1:
        return b
    if u <= 0:
        return a.convert(b.mode)
    return MOVES[name](a, b, u).convert(b.mode)
