"""Borderline Why renderer: a 1080x1920 video drawn frame by frame from a list of scenes (PIL), piped to ffmpeg.

A scene is either a map (the camera fits the places it names, then highlights, labels, pins, arrows, lines and
parallels appear on their cue words) or a card (an old-paper title card for a date, a treaty, a name). Every
place is looked up in modules.bw_geo; nothing is drawn from coordinates a script made up. Captions follow the
voice word by word. The palette changes from video to video so the channel does not look like one template.
"""

from __future__ import annotations

import difflib
import logging
import math
import random
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from modules import bw_geo

logger = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
MAP_TOP, MAP_BOTTOM = 470, 1330  # the camera fits places between the headline and the captions
CY = (MAP_TOP + MAP_BOTTOM) // 2
CAPTION_Y = 1420
MOVE = 0.9  # seconds the camera takes from one scene's view to the next
FADE = 0.4
TAIL = 1.2  # seconds after the last word

FONTS_DIR = bw_geo.PROJECT_ROOT / "assets" / "fonts"
FONT_DIRS = (str(FONTS_DIR), "/System/Library/Fonts/Supplemental", "/usr/share/fonts/truetype/msttcorefonts",
             "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation")
BOLD_FONTS = ("Impact.ttf", "impact.ttf", "Anton-Regular.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf")
ANTON_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf"  # SIL Open Font License
SERIF_FONTS = ("Georgia Bold.ttf", "georgiab.ttf", "DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf")

ELEMENT_KINDS = ("highlight", "label", "pin", "ring", "line", "arrow", "parallel", "meridian", "stamp", "icon", "pole", "route")
NO_PLACE_KINDS = ("stamp", "parallel", "meridian", "route", "sketch", "icon", "pole")  # their place, if any, is optional
COLOR_ROLES = ("a", "b", "c", "gold", "red", "white")

THEMES: dict[str, dict[str, tuple[int, int, int]]] = {
    "atlas": dict(ocean=(16, 34, 58), land=(78, 86, 99), border=(120, 130, 146), region=(98, 106, 120), lake=(24, 56, 92),
                  lake_line=(70, 120, 170), a=(58, 104, 168), b=(170, 72, 60), c=(80, 150, 96)),
    "slate": dict(ocean=(20, 24, 31), land=(58, 62, 70), border=(104, 110, 122), region=(76, 80, 90), lake=(30, 40, 54),
                  lake_line=(80, 100, 128), a=(66, 128, 230), b=(226, 90, 80), c=(236, 176, 58)),
    "sea": dict(ocean=(30, 70, 110), land=(170, 160, 132), border=(120, 110, 88), region=(150, 140, 114), lake=(44, 92, 138),
                lake_line=(90, 140, 190), a=(196, 82, 64), b=(52, 104, 160), c=(92, 140, 70)),
    "dusk": dict(ocean=(34, 26, 52), land=(86, 76, 98), border=(132, 120, 146), region=(104, 94, 118), lake=(44, 38, 74),
                 lake_line=(110, 96, 150), a=(84, 170, 200), b=(232, 110, 90), c=(240, 196, 90)),
}
FIXED = dict(gold=(242, 193, 78), red=(214, 48, 49), white=(245, 245, 240), river=(90, 190, 255), ink=(80, 52, 28),
             paper=(232, 214, 174))


class RenderError(RuntimeError):
    pass


# --------------------------------------------------------------------------- the spec


@dataclass
class Element:
    kind: str
    place: str = ""
    to: str = ""
    value: float | None = None  # a parallel's latitude, a meridian's longitude
    text: str = ""
    color: str = "a"
    cue: str = ""  # the narration word it appears on
    at: float = 0.0
    opts: dict[str, Any] = field(default_factory=dict)  # the satellite look's extras (modules.bw_fx): flag, icon, points...
    feature: bw_geo.Feature | None = field(default=None, repr=False)
    feature_to: bw_geo.Feature | None = field(default=None, repr=False)


@dataclass
class Scene:
    kind: str  # map | card
    narration: str
    focus: list[str] = field(default_factory=list)
    headline: str = ""
    kicker: str = ""  # cards
    title: str = ""
    subtitle: str = ""
    elements: list[Element] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0
    view: tuple[float, float, float] | None = None  # lon, lat, pixels per degree
    opts: dict[str, Any] = field(default_factory=dict)  # e.g. globe: true (modules.bw_fx)


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Spec:
    scenes: list[Scene]
    words: list[Word]
    cover: str = ""  # the first frame's big text (the thumbnail)
    theme: str = "atlas"
    seed: int = 0
    look: str = "flat"  # flat | satellite (modules.bw_fx)

    @property
    def duration(self) -> float:
        return (self.words[-1].end if self.words else 0.0) + TAIL


# --------------------------------------------------------------------------- resolving places and timing


def resolve(spec: Spec, find: Callable[[str], bw_geo.Feature | None] = bw_geo.resolve) -> list[str]:
    """Look up every place the scenes name; returns the names that could not be found (empty = fine)."""
    missing: list[str] = []
    for scene in spec.scenes:
        if scene.kind != "map":
            continue
        for el in scene.elements:
            if el.kind == "route":
                coords = []
                for name in el.opts.get("points") or []:
                    if not isinstance(name, str):
                        coords.append(list(name))
                    elif f := find(name):
                        coords.append(list(f.label_point()))
                    else:
                        missing.append(name)
                el.opts["coords"] = coords
                if len(el.opts.get("points") or []) < 2:
                    missing.append("(route with fewer than 2 points)")
                continue
            if el.kind in NO_PLACE_KINDS and not el.place:
                continue
            el.feature = find(el.place) if el.place else None
            if el.feature is None:
                missing.append(el.place or f"({el.kind} without a place)")
            if el.kind in ("line", "arrow"):
                el.feature_to = find(el.to) if el.to else None
                if el.feature_to is None:
                    missing.append(el.to or f"({el.kind} without an end)")
        for name in scene.focus:
            if find(name) is None:
                missing.append(name)
    return list(dict.fromkeys(missing))


def _tok(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def align(spec: Spec) -> None:
    """Give every scene its start and end, and every element its time, from the voice's word timings: the
    scenes' narration is matched to the spoken words (difflib, so a number read differently or a merged token
    does not shift everything after it)."""
    script_words: list[tuple[int, str]] = []  # (scene index, token)
    for i, scene in enumerate(spec.scenes):
        script_words += [(i, _tok(w)) for w in scene.narration.split() if _tok(w)]
    spoken = [_tok(w.text) for w in spec.words]
    matcher = difflib.SequenceMatcher(None, [t for _, t in script_words], spoken, autojunk=False)
    at: dict[int, int] = {}  # script word -> spoken word
    for a, b, n in matcher.get_matching_blocks():
        for k in range(n):
            at[a + k] = b + k
    total = spec.duration
    n_script = max(1, len(script_words))
    first: dict[int, float] = {}
    lead: dict[int, int] = {}  # a scene's words before its first matched one ("In 1783" spoken as one token)
    last_end = 0.0
    for k, (scene_i, _) in enumerate(script_words):
        if scene_i in first:
            if k in at:
                last_end = spec.words[at[k]].end
            continue
        if k in at:
            matched = spec.words[at[k]].start
            # unmatched words before it were spoken right after the previous scene's last word
            first[scene_i] = min(matched, last_end + 0.1) if lead.get(scene_i) else matched
            last_end = spec.words[at[k]].end
        else:
            lead[scene_i] = lead.get(scene_i, 0) + 1
    for k, (scene_i, _) in enumerate(script_words):  # a scene with no matched word at all: proportional
        first.setdefault(scene_i, total * k / n_script)
    starts = [first.get(i, 0.0) for i in range(len(spec.scenes))]
    starts[0] = 0.0
    for i in range(1, len(starts)):
        starts[i] = max(starts[i], starts[i - 1] + 0.5)
    for i, scene in enumerate(spec.scenes):
        scene.start = starts[i]
        scene.end = starts[i + 1] if i + 1 < len(starts) else total
    for scene in spec.scenes:
        window = [w for w in spec.words if scene.start - 0.05 <= w.start < scene.end]
        for j, el in enumerate(scene.elements):
            cue = _tok(el.cue)
            hit = next((w.start for w in window if cue and (_tok(w.text) == cue or (len(cue) > 3 and _tok(w.text).startswith(cue)))), None)
            el.at = hit if hit is not None else min(scene.start + 0.3 + 0.7 * j, max(scene.start, scene.end - 0.6))


def fit_view(boxes: list[tuple[float, float, float, float]], pad: float = 0.3, min_span: float = 0.25) -> tuple[float, float, float]:
    """The camera (center lon, lat, pixels per degree) that shows every box with some margin."""
    x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
    lon, lat = (x0 + x1) / 2, (y0 + y1) / 2
    k = max(0.2, math.cos(math.radians(lat)))
    dx = max(min_span, (x1 - x0) * (1 + pad)); dy = max(min_span, (y1 - y0) * (1 + pad))
    ppd = min(W * 0.9 / (dx * k), (MAP_BOTTOM - MAP_TOP) / dy)
    return lon, max(-80.0, min(80.0, lat)), max(2.5, min(6000.0, ppd))


def plan_views(spec: Spec) -> None:
    """Each map scene's camera: its focus places, else everything its elements name, else the previous view."""
    prev: tuple[float, float, float] | None = None
    for scene in spec.scenes:
        if scene.kind != "map":
            scene.view = prev
            continue
        feats = [f for f in (bw_geo.resolve(n) for n in scene.focus) if f]
        if not feats:
            feats = [f for el in scene.elements for f in (el.feature, el.feature_to) if f]
        boxes = [f.bbox() for f in feats]
        for el in scene.elements:
            if el.kind == "parallel" and el.value is not None and boxes:
                boxes.append((boxes[0][0], el.value, boxes[0][2], el.value))
        scene.view = fit_view(boxes, min_span=1.5 if all(f.kind == "place" for f in feats) else 0.25) if boxes else (prev or (0.0, 20.0, 3.0))
        prev = scene.view
    first = next((s.view for s in spec.scenes if s.view), (0.0, 20.0, 3.0))
    for scene in spec.scenes:
        scene.view = scene.view or first


# --------------------------------------------------------------------------- drawing helpers


def ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def ramp(t: float, t0: float, dur: float = FADE) -> float:
    return ease((t - t0) / dur)


_fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font_file(names: tuple[str, ...]) -> str | None:
    for d in FONT_DIRS:
        for n in names:
            if (Path(d) / n).is_file():
                return str(Path(d) / n)
    return None


def ensure_fonts() -> None:
    """Machines without Impact (Linux) get Anton, its free look-alike, once into assets/fonts/."""
    if _font_file(BOLD_FONTS[:3]):
        return
    try:
        import requests

        r = requests.get(ANTON_URL, timeout=30)
        r.raise_for_status()
        FONTS_DIR.mkdir(parents=True, exist_ok=True)
        (FONTS_DIR / "Anton-Regular.ttf").write_bytes(r.content)
        _fonts.clear()
    except Exception as err:  # noqa: BLE001 - DejaVu Sans Bold still works
        logger.warning("Could not download the Anton font: %s", err)


def font(size: int, serif: bool = False) -> ImageFont.FreeTypeFont:
    key = ("serif" if serif else "bold", size)
    if key not in _fonts:
        path = _font_file(SERIF_FONTS if serif else BOLD_FONTS) or _font_file(BOLD_FONTS)
        _fonts[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default(size)
    return _fonts[key]


def text(ov: Image.Image, xy: tuple[float, float], s: str, size: int, fill=(245, 245, 240), a: float = 1.0, serif: bool = False,
         stroke: int = 6, anchor: str = "mm", max_width: int | None = None) -> None:
    """Text with a black outline, faded by `a`; shrunk to `max_width` when it is too wide."""
    if a <= 0 or not s:
        return
    f = font(size, serif)
    if max_width:
        while size > 24 and f.getlength(s) > max_width:
            size -= 4
            f = font(size, serif)
    al = int(255 * a)
    ImageDraw.Draw(ov).text(xy, s, font=f, fill=tuple(fill) + (al,), stroke_width=stroke,
                            stroke_fill=(0, 0, 0, al) if stroke else None, anchor=anchor)


def wrap(s: str, size: int, width: int, serif: bool = False) -> list[str]:
    f = font(size, serif)
    lines, cur = [], ""
    for w in s.split():
        trial = f"{cur} {w}".strip()
        if cur and f.getlength(trial) > width:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    return lines + ([cur] if cur else [])


def headline(ov: Image.Image, s: str, a: float, color=(245, 245, 240), size: int = 84) -> None:
    """Up to three lines of capitals at the top, shrunk until they fit."""
    s = s.upper()
    while size > 44:
        lines = wrap(s, size, W - 120)
        if len(lines) <= 3:
            break
        size -= 6
    lines = wrap(s, size, W - 120)[:3]
    y = 250 - (len(lines) - 1) * size * 0.5
    for line in lines:
        text(ov, (W / 2, y), line, size, fill=color, a=a, stroke=8)
        y += size * 1.05


class Cam:
    def __init__(self, lon: float, lat: float, ppd: float):
        self.lon, self.lat, self.ppd = lon, lat, ppd
        self.k = max(0.2, math.cos(math.radians(lat)))

    def xy(self, a: np.ndarray) -> np.ndarray:
        out = np.empty((len(a), 2), dtype=np.float32)
        out[:, 0] = W / 2 + (a[:, 0] - self.lon) * (self.ppd * self.k)
        out[:, 1] = CY - (a[:, 1] - self.lat) * self.ppd
        return out

    def pt(self, lon: float, lat: float) -> tuple[float, float]:
        return W / 2 + (lon - self.lon) * self.ppd * self.k, CY - (lat - self.lat) * self.ppd

    def lonlat(self, x: float, y: float) -> tuple[float, float]:
        return self.lon + (x - W / 2) / (self.ppd * self.k), self.lat - (y - CY) / self.ppd

    def step(self) -> int:
        p = self.ppd
        return 32 if p < 2 else 16 if p < 5 else 8 if p < 12 else 4 if p < 30 else 2 if p < 80 else 1


def blend_view(a: tuple[float, float, float], b: tuple[float, float, float], u: float) -> Cam:
    u = ease(u)
    ppd = math.exp(math.log(a[2]) + (math.log(b[2]) - math.log(a[2])) * u)
    return Cam(a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, ppd)


class Layer:
    """One kind of feature, with every part's box in one array so a frame culls them at once."""

    def __init__(self, features: list[bw_geo.Feature]):
        self.items = [(f, p) for f in features for p in f.parts]
        self.boxes = np.array([p.bbox for _, p in self.items], dtype=np.float32).reshape(-1, 4)

    def visible(self, cam: Cam, min_px: float = 1.2):
        if not len(self.items):
            return []
        x0, y1 = cam.lonlat(-40, H + 40)
        x1, y0 = cam.lonlat(W + 40, -40)
        b = self.boxes
        on = (b[:, 2] >= x0) & (b[:, 0] <= x1) & (b[:, 3] >= y1) & (b[:, 1] <= y0)
        size = np.maximum((b[:, 2] - b[:, 0]) * cam.ppd * cam.k, (b[:, 3] - b[:, 1]) * cam.ppd)
        on &= size >= min_px
        return [self.items[i] for i in np.nonzero(on)[0]]


_layers: dict[int, dict[str, Layer]] = {}


def layers() -> dict[str, Layer]:
    w = bw_geo.world()
    if id(w) not in _layers:
        _layers.clear()
        _layers[id(w)] = {k: Layer(v) for k, v in w.layers.items() if k != "places"}
    return _layers[id(w)]


def flat(pts: np.ndarray) -> list[float]:
    return pts.ravel().tolist()


def dashed(d: ImageDraw.ImageDraw, p0, p1, color, width: int = 8, dash: int = 28, gap: int = 18, frac: float = 1.0) -> None:
    (x0, y0), (x1, y1) = p0, p1
    full = math.hypot(x1 - x0, y1 - y0)
    if full < 1:
        return
    ux, uy = (x1 - x0) / full, (y1 - y0) / full
    s, end = 0.0, full * frac
    while s < end:
        e = min(end, s + dash)
        d.line([x0 + ux * s, y0 + uy * s, x0 + ux * e, y0 + uy * e], fill=color, width=width)
        s = e + gap


def curve(p0, p1, bend: float = 0.18, n: int = 40) -> list[tuple[float, float]]:
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    cx, cy = mx - (y1 - y0) * bend, my + (x1 - x0) * bend
    return [((1 - u) ** 2 * x0 + 2 * (1 - u) * u * cx + u * u * x1, (1 - u) ** 2 * y0 + 2 * (1 - u) * u * cy + u * u * y1)
            for u in (i / n for i in range(n + 1))]


# --------------------------------------------------------------------------- frames


class Renderer:
    def __init__(self, spec: Spec):
        self.spec = spec
        self.pal = {**THEMES.get(spec.theme, THEMES["atlas"]), **FIXED}
        self.layers = layers()
        rng = np.random.default_rng(spec.seed or 7)
        noise = rng.normal(0, 9, (H // 4, W // 4)).astype(np.float32)
        noise = np.array(Image.fromarray(noise).resize((W, H), Image.BICUBIC))
        yy, xx = np.mgrid[0:H, 0:W]
        vignette = 1 - 0.45 * (((xx - W / 2) / (W * 0.7)) ** 2 + ((yy - CY) / (H * 0.6)) ** 2)
        paper = (np.array(self.pal["paper"], dtype=np.float32)[None, None, :] + noise[..., None]) * vignette[..., None]
        self.paper = Image.fromarray(np.clip(paper, 0, 255).astype(np.uint8)).convert("RGBA")
        groups, cur = [], []
        words = spec.words
        for i, w in enumerate(words):
            cur.append(i)
            nxt = words[i + 1] if i + 1 < len(words) else None
            gap = (nxt.start - w.end) if nxt else 9
            if len(cur) >= 3 or gap > 0.3 or nxt is None or w.text.rstrip().endswith((".", "?", "!")):
                groups.append(cur)
                cur = []
        self.groups = groups

    def color(self, role: str) -> tuple[int, int, int]:
        return self.pal.get(role) or self.pal["a"]

    def scene_at(self, t: float) -> int:
        for i, s in enumerate(self.spec.scenes):
            if s.start <= t < s.end:
                return i
        return len(self.spec.scenes) - 1

    def camera(self, i: int, t: float) -> Cam:
        scenes = self.spec.scenes
        scene = scenes[i]
        prev = next((scenes[j].view for j in range(i - 1, -1, -1) if scenes[j].kind == "map"), None)
        view = scene.view
        drift = 1 + 0.05 * ease((t - scene.start) / max(1.0, scene.end - scene.start))  # a slow push in
        if prev and prev != view and t < scene.start + MOVE:
            cam = blend_view(prev, view, (t - scene.start) / MOVE)
        else:
            cam = Cam(*view)
        cam.ppd *= drift
        return cam

    # -- map --

    def base(self, cam: Cam, fills: dict[int, tuple[tuple[int, int, int], float]]) -> Image.Image:
        pal = self.pal
        img = Image.new("RGB", (W, H), pal["ocean"])
        d = ImageDraw.Draw(img)
        step = cam.step()
        for feat, part in self.layers["countries"].visible(cam):
            pts = cam.xy(part.at(step)[0])
            if len(pts) > 2:
                d.polygon(flat(pts), fill=pal["land"])
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for key in ("countries", "regions"):
            for feat, part in self.layers[key].visible(cam):
                if id(feat) in fills:
                    rgb, a = fills[id(feat)]
                    pts = cam.xy(part.at(step)[0])
                    if len(pts) > 2:
                        od.polygon(flat(pts), fill=rgb + (int(235 * a),))
        img = img.convert("RGBA")
        img.alpha_composite(overlay)
        d = ImageDraw.Draw(img)
        if cam.ppd > 20:
            for feat, part in self.layers["regions"].visible(cam, 6):
                pts = cam.xy(part.at(step)[0])
                if len(pts) > 2:
                    d.line(flat(np.vstack([pts, pts[:1]])), fill=pal["region"], width=1 if cam.ppd < 120 else 2)
        for feat, part in self.layers["countries"].visible(cam, 3):
            pts = cam.xy(part.at(step)[0])
            if len(pts) > 2:
                d.line(flat(np.vstack([pts, pts[:1]])), fill=pal["border"], width=2 if cam.ppd < 60 else 3)
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        outlines = []
        for feat, part in self.layers["lakes"].visible(cam, 3):
            rings = part.at(step)
            outer = cam.xy(rings[0])
            if len(outer) > 2:
                md.polygon(flat(outer), fill=255)
                outlines.append(outer)
            for hole in rings[1:]:
                h = cam.xy(hole)
                if len(h) > 2:
                    md.polygon(flat(h), fill=0)
        img.paste(pal["lake"], (0, 0), mask)
        for o in outlines:
            d.line(flat(np.vstack([o, o[:1]])), fill=pal["lake_line"], width=2)
        return img

    def map_frame(self, i: int, t: float) -> Image.Image:
        scene = self.spec.scenes[i]
        cam = self.camera(i, t)
        fills: dict[int, tuple[tuple[int, int, int], float]] = {}
        for el in scene.elements:
            if el.kind == "highlight" and el.feature is not None and not el.feature.is_line and el.feature.parts:
                fills[id(el.feature)] = (self.color(el.color), ramp(t, el.at))
        img = self.base(cam, fills)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        for el in scene.elements:
            self.element(ov, d, cam, el, t, scene)
        img.alpha_composite(ov)
        return img

    def element(self, ov: Image.Image, d: ImageDraw.ImageDraw, cam: Cam, el: Element, t: float, scene: Scene) -> None:
        a = ramp(t, el.at)
        if a <= 0:
            return
        al = int(255 * a)
        rgb = self.color(el.color)
        kind, feat = el.kind, el.feature
        if kind == "highlight" and feat is not None:
            if feat.is_line:
                step = cam.step()
                for part in feat.parts:
                    pts = cam.xy(part.at(step)[0])
                    if len(pts) > 1:
                        d.line(flat(pts), fill=FIXED["river"] + (al,), width=9)
            elif not feat.parts:  # a point (Wikipedia coordinates): a pulsing ring instead of an outline
                self.ring(d, cam, feat, rgb, al, t)
            if el.text:
                text(ov, cam.pt(*feat.label_point()), el.text.upper(), 54, a=a, max_width=W - 80)
        elif kind == "label" and feat is not None:
            text(ov, cam.pt(*feat.label_point()), (el.text or feat.name).upper(), 52, fill=rgb if el.color != "a" else FIXED["white"],
                 a=a, max_width=W - 80)
        elif kind == "ring" and feat is not None:
            self.ring(d, cam, feat, rgb, al, t)
            if el.text:
                x, y = cam.pt(*feat.label_point())
                text(ov, (x, y - 90), el.text.upper(), 50, fill=rgb, a=a, max_width=W - 80)
        elif kind == "pin" and feat is not None:
            x, y = cam.pt(*feat.label_point())
            r = 15
            d.ellipse([x - r, y - r, x + r, y + r], fill=rgb + (al,), outline=(0, 0, 0, al), width=4)
            d.line([x, y - r, x, y - 60], fill=(255, 255, 255, al), width=4)
            text(ov, (x, y - 95), (el.text or feat.name).upper(), 48, a=a, max_width=W - 60)
        elif kind in ("line", "arrow") and feat is not None and el.feature_to is not None:
            p0, p1 = cam.pt(*feat.label_point()), cam.pt(*el.feature_to.label_point())
            u = ramp(t, el.at, 1.2)
            if kind == "line":
                dashed(d, p0, p1, rgb + (al,), frac=u)
            else:
                pts = curve(p0, p1)
                n = max(2, int(len(pts) * u))
                d.line(pts[:n], fill=rgb + (al,), width=12, joint="curve")
                (xa, ya), (xb, yb) = pts[n - 2], pts[n - 1]
                ang = math.atan2(yb - ya, xb - xa)
                head = [(xb + 34 * math.cos(ang), yb + 34 * math.sin(ang)),
                        (xb + 26 * math.cos(ang + 2.4), yb + 26 * math.sin(ang + 2.4)),
                        (xb + 26 * math.cos(ang - 2.4), yb + 26 * math.sin(ang - 2.4))]
                d.polygon(head, fill=rgb + (al,))
            if el.text:
                mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
                text(ov, (mx, my - 50), el.text.upper(), 46, fill=rgb, a=a * ramp(t, el.at + 0.8), max_width=W - 80)
        elif kind == "parallel" and el.value is not None:
            _, y = cam.pt(cam.lon, el.value)
            u = ramp(t, el.at, 1.2)
            d.line([0, y, W * u, y], fill=rgb + (al,), width=8)
            label = el.text or f"{abs(el.value):g}°{'N' if el.value >= 0 else 'S'}"
            text(ov, (160, y - 40), label.upper(), 44, fill=rgb, a=a * ramp(t, el.at + 0.6), max_width=W - 80)
        elif kind == "meridian" and el.value is not None:
            x, _ = cam.pt(el.value, cam.lat)
            u = ramp(t, el.at, 1.2)
            d.line([x, MAP_TOP - 80, x, MAP_TOP - 80 + (MAP_BOTTOM - MAP_TOP + 160) * u], fill=rgb + (al,), width=8)
            label = el.text or f"{abs(el.value):g}°{'E' if el.value >= 0 else 'W'}"
            text(ov, (x, MAP_TOP - 110), label.upper(), 44, fill=rgb, a=a * ramp(t, el.at + 0.6), max_width=W - 80)
        elif kind == "stamp" and el.text:
            self.stamp(ov, el.text, el.at, t, self.color(el.color if el.color != "a" else "red"))

    def ring(self, d, cam: Cam, feat: bw_geo.Feature, rgb, al: int, t: float) -> None:
        x, y = cam.pt(*feat.label_point())
        r = 34 + 8 * math.sin(t * 5)
        d.ellipse([x - r, y - r, x + r, y + r], outline=rgb + (al,), width=8)
        d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=rgb + (al,))

    def stamp(self, ov: Image.Image, s: str, at: float, t: float, rgb) -> None:
        u = ramp(t, at, 0.25)
        if u <= 0:
            return
        s = s.upper()
        f = font(140)
        width = int(min(980, f.getlength(s) + 120))
        size = 140
        while size > 60 and font(size).getlength(s) + 120 > 980:
            size -= 10
        st = Image.new("RGBA", (width, 300), (0, 0, 0, 0))
        sd = ImageDraw.Draw(st)
        sd.rounded_rectangle([16, 16, width - 16, 284], radius=24, outline=rgb + (255,), width=16)
        sd.text((width / 2, 150), s, font=font(size), fill=rgb + (255,), anchor="mm")
        sc = 1.5 - 0.5 * u
        st = st.resize((int(width * sc), int(300 * sc))).rotate(-10, expand=True)
        st.putalpha(st.getchannel("A").point(lambda v: int(v * u)))
        ov.alpha_composite(st, (int(W / 2 - st.width / 2), int(CY + 60 - st.height / 2)))

    # -- card --

    def card_frame(self, i: int, t: float) -> Image.Image:
        scene = self.spec.scenes[i]
        img = self.paper.copy()
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ink, red = FIXED["ink"], FIXED["red"]
        y = 720
        if scene.kicker:
            text(ov, (W / 2, y), scene.kicker.upper(), 56, fill=ink, a=ramp(t, scene.start + 0.2), serif=True, stroke=0, max_width=W - 120)
            y += 120
        if scene.title:
            lines = wrap(scene.title, 130, W - 140, serif=True)[:3]
            for k, line in enumerate(lines):
                text(ov, (W / 2, y + k * 140), line, 130, fill=red, a=ramp(t, scene.start + 0.5), serif=True, stroke=0, max_width=W - 100)
            y += 140 * len(lines) + 30
        if scene.subtitle:
            for k, line in enumerate(wrap(scene.subtitle, 50, W - 180, serif=True)[:3]):
                text(ov, (W / 2, y + k * 64), line, 50, fill=ink, a=ramp(t, scene.start + 1.0), serif=True, stroke=0)
        for el in scene.elements:
            if el.kind == "stamp" and el.text:
                self.stamp(ov, el.text, el.at, t, self.color(el.color if el.color != "a" else "red"))
        img.alpha_composite(ov)
        return img

    # -- shared --

    def scene_frame(self, i: int, t: float) -> Image.Image:
        scene = self.spec.scenes[i]
        img = self.map_frame(i, t) if scene.kind == "map" else self.card_frame(i, t)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        if i == 0 and self.spec.cover:
            headline(ov, self.spec.cover, 1.0, color=FIXED["gold"], size=96)
        elif scene.headline:
            headline(ov, scene.headline, ramp(t, scene.start + 0.15), color=FIXED["gold"] if scene.kind == "map" else FIXED["red"])
        img.alpha_composite(ov)
        return img

    def captions(self, img: Image.Image, t: float) -> None:
        words = self.spec.words
        group = None
        for k, g in enumerate(self.groups):
            start = words[g[0]].start
            stop = words[self.groups[k + 1][0]].start if k + 1 < len(self.groups) else self.spec.duration
            if start <= t < stop:
                group = g
        if not group:
            return
        size = 92
        parts = [words[i].text.upper() for i in group]
        while size > 56 and sum(font(size).getlength(p + " ") for p in parts) > W - 80:
            size -= 6
        f = font(size)
        widths = [f.getlength(p + " ") for p in parts]
        x = W / 2 - sum(widths) / 2 + f.getlength(" ") / 2
        d = ImageDraw.Draw(img)
        for i, p, width in zip(group, parts, widths):
            on = words[i].start <= t
            d.text((x, CAPTION_Y), p, font=f, fill=(255, 220, 60) if on else (255, 255, 255), stroke_width=9,
                   stroke_fill=(0, 0, 0), anchor="lm")
            x += width

    def frame(self, t: float) -> Image.Image:
        i = self.scene_at(t)
        scene = self.spec.scenes[i]
        img = self.scene_frame(i, t)
        if i > 0 and t < scene.start + FADE and self.spec.scenes[i - 1].kind != scene.kind:
            img = Image.blend(self.scene_frame(i - 1, t), img, ramp(t, scene.start, FADE))
        self.captions(img, t)
        return img.convert("RGB")


# --------------------------------------------------------------------------- the video


def prepare(spec: Spec) -> list[str]:
    """Resolve the places, time the scenes and pick the cameras; the missing places (empty = ready)."""
    missing = resolve(spec)
    align(spec)
    plan_views(spec)
    return missing


def still(spec: Spec, t: float) -> Image.Image:
    return Renderer(spec).frame(t)


def encoder() -> list[str]:
    from modules.scene_images import setting

    if setting("BW_NVENC", "0").lower() in ("1", "true", "on"):
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "19"]


def render(spec: Spec, voice: Path, out: Path, music: Path | None = None, music_volume: float = 0.10,
           progress: Callable[[float | None, str], None] = lambda f, m: None) -> Path:
    """Draw every frame, encode, and mix the voice with the music under it."""
    ensure_fonts()
    renderer: Renderer | None = None
    if spec.look == "satellite":
        from modules import bw_fx

        try:
            renderer = bw_fx.FxRenderer(spec)
        except Exception as err:  # noqa: BLE001 - NASA or GitHub down: the flat look still makes the video
            logger.warning("The satellite look is unavailable (%s); drawing the flat look", err)
    renderer = renderer or Renderer(spec)
    out.parent.mkdir(parents=True, exist_ok=True)
    silent = out.with_name(out.stem + ".silent.mp4")
    n = int(math.ceil(spec.duration * FPS))
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", *encoder(), "-pix_fmt", "yuv420p", str(silent)], stdin=subprocess.PIPE)
    try:
        for k in range(n):
            ff.stdin.write(renderer.frame(k / FPS).tobytes())
            if k % 60 == 0:
                progress(k / n, f"Drawing the maps: frame {k}/{n}")
    finally:
        ff.stdin.close()
        ff.wait()
    if ff.returncode:
        raise RenderError(f"ffmpeg failed while encoding ({ff.returncode})")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(voice)]
    if music is not None and music.is_file():
        cmd += ["-stream_loop", "-1", "-i", str(music), "-filter_complex",
                f"[2:a]volume={music_volume:.3f},afade=t=out:st={max(0.0, spec.duration - 1.5):.2f}:d=1.5[m];"
                "[1:a][m]amix=inputs=2:duration=first:normalize=0[a]", "-map", "0:v", "-map", "[a]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{spec.duration:.2f}", "-movflags", "+faststart", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode:
        raise RenderError(f"ffmpeg failed while mixing the audio: {result.stderr[-400:]}")
    silent.unlink(missing_ok=True)
    progress(1.0, "Video ready")
    return out


def spec_from_dict(data: dict[str, Any], words: list[Word]) -> Spec:
    """A Spec from the script JSON (modules.bw_script) and the voice's words."""
    scenes = []
    el_keys = {"kind", "place", "to", "value", "text", "color", "cue"}
    scene_keys = {"kind", "narration", "focus", "headline", "kicker", "title", "subtitle", "elements"}
    for s in data["scenes"]:
        els = [Element(kind=e["kind"], place=e.get("place", ""), to=e.get("to", ""), value=e.get("value"), text=e.get("text", ""),
                       color=e.get("color") or "a", cue=e.get("cue", ""),
                       opts={k: v for k, v in e.items() if k not in el_keys and v not in (None, "", [])}) for e in s.get("elements", [])]
        scenes.append(Scene(kind=s.get("kind", "map"), narration=s["narration"], focus=list(s.get("focus") or []),
                            headline=s.get("headline", ""), kicker=s.get("kicker", ""), title=s.get("title", ""),
                            subtitle=s.get("subtitle", ""), elements=els,
                            opts={k: v for k, v in s.items() if k not in scene_keys and v not in (None, "", [])}))
    seed = int(data.get("seed") or 0)
    theme = data.get("theme") or random.Random(seed).choice(sorted(THEMES))
    return Spec(scenes, words, cover=data.get("cover_text", ""), theme=theme, seed=seed, look=data.get("look") or "flat")
