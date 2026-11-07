"""Borderline Why's structures and measures on the map (from the 10M-view "Why NO Bridge Between Sicily and Italy"
Short the owner shared, 2026-11-07): drawn in code, no pictures.

  build    a bridge, wall, fence or canal: "icon" bridge | wall | fence | canal, between "place" and "to" (a bridge
           spans the two places where their coasts are closest: Sicily and Calabria at Messina) or along "points"
           (a wall from city to city). A bridge drops onto the map with its shadow; a wall, fence or canal builds
           itself along its path.
  measure  a dashed line between "place" and "to" (their closest edges, like the bridge) that draws itself, then
           its length along it: "3.1 KM (1.9 MI)" ("text" in place of the number when given).

bw_fx.FxRenderer.element draws them; ends() finds where they stand and caches it on the element.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from modules import bw_render

STRUCTURES = ("bridge", "wall", "fence", "canal")
DROP = 0.55  # seconds a bridge takes to fall into place
BUILD = 1.6  # seconds a wall, fence or canal takes to build along its path
DRAW = 0.7  # seconds a measure's line takes to draw
YELLOW, INK = (255, 214, 0), (0, 0, 0)
CLOSE_SPAN = 1.2  # degrees the camera shows round a short bridge or measure: as close as the 500 m satellite stays sharp
MIN_BRIDGE, MIN_MEASURE = 320, 170  # on-screen pixels at least: across a narrow strait it is drawn as a symbol, reaching
# onto both shores (the 10M Short's bridge did the same); a measure's number still says the real distance


def stretch(p0, p1, least: float):
    """The segment grown about its middle to at least `least` pixels."""
    L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    if L >= least or L < 1e-6:
        return p0, p1
    k = least / L
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    return (mx + (p0[0] - mx) * k, my + (p0[1] - my) * k), (mx + (p1[0] - mx) * k, my + (p1[1] - my) * k)


def ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 3


def backout(u: float) -> float:
    u = min(1.0, max(0.0, u))
    s = 1.6
    return 1 + (s + 1) * (u - 1) ** 3 + s * (u - 1) ** 2


# --------------------------------------------------------------------------- where it stands


def _rings(f, main: bool = False) -> list[np.ndarray]:
    """The outlines of a place's parts; `main`: only its mainland and the islands chained to it (bw_geo's main box),
    so Spain is Spain, not the rocks it keeps off the Moroccan coast."""
    box = f.bbox(True) if main else None
    area = lambda b: (b[2] - b[0]) * (b[3] - b[1])
    least = 0.02 * max((area(p.bbox) for p in f.parts), default=0.0) if main else 0.0  # a speck of rock is no shore
    keep = lambda b: box is None or (b[0] <= box[2] and b[2] >= box[0] and b[1] <= box[3] and b[3] >= box[1] and area(b) >= least)
    return [np.asarray(p.rings[0], dtype=float) for p in f.parts if p.rings and len(p.rings[0]) and keep(p.bbox)]


def _near(a: np.ndarray, box: tuple[float, float, float, float], pad: float) -> np.ndarray:
    keep = (a[:, 0] > box[0] - pad) & (a[:, 0] < box[2] + pad) & (a[:, 1] > box[1] - pad) & (a[:, 1] < box[3] + pad)
    return a[keep]


def _min(a: np.ndarray, b: np.ndarray, k: float) -> tuple[float, int, int]:
    """The smallest squared (cos-scaled) distance between two point sets, and where."""
    best, ij = math.inf, (0, 0)
    for s in range(0, len(a), 500):
        chunk = a[s:s + 500]
        dx = (chunk[:, None, 0] - b[None, :, 0]) * k
        dy = chunk[:, None, 1] - b[None, :, 1]
        dd = dx * dx + dy * dy
        j = int(np.argmin(dd))
        if dd.flat[j] < best:
            best, ij = float(dd.flat[j]), (s + j // len(b), j % len(b))
    return best, ij[0], ij[1]


TOUCH_KM = 0.5  # a part this close to the other place shares a land border with it (Ceuta and Morocco): not a shore


def closest(fa, fb, most: int = 3000) -> tuple[tuple[float, float], tuple[float, float]]:
    """The two points, one on each place's outline, closest to each other across the water (lon, lat): where a
    bridge goes. Parts of one that touch the other over land are left out, so Spain to Morocco is Gibraltar's
    strait, not Ceuta's fence. A place that is only a point is that point."""
    if not fa.parts or not fb.parts:
        return tuple(fa.label_point()), tuple(fb.label_point())
    near = lambda f, other: [r for r in (_near(r, other.bbox(False), 2.0) for r in _rings(f, True)) if len(r)]
    ra, rb = near(fa, fb) or _rings(fa, True), near(fb, fa) or _rings(fb, True)
    thin = lambda rs: [r[:: max(1, sum(map(len, rs)) // most)] for r in rs]
    ra, rb = thin(ra), thin(rb)
    k = math.cos(math.radians(float(np.mean([r[:, 1].mean() for r in ra + rb]))))
    deg = TOUCH_KM / 111.0
    allb = np.concatenate(rb)
    alla = np.concatenate(ra)
    sea_a = [r for r in ra if _min(r, allb, k)[0] > deg * deg] or ra  # every part touches: a land border after all
    sea_b = [r for r in rb if _min(r, alla, k)[0] > deg * deg] or rb
    a, b = np.concatenate(sea_a), np.concatenate(sea_b)
    _, i, j = _min(a, b, k)
    return (float(a[i][0]), float(a[i][1])), (float(b[j][0]), float(b[j][1]))


def km(p: tuple[float, float], q: tuple[float, float]) -> float:
    """Great-circle distance in kilometres between two (lon, lat) points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (*p, *q))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, h)))


def ends(el) -> list[tuple[float, float]]:
    """The path in (lon, lat): the route's places in order, or the two places' closest points (cached)."""
    if "_path" not in el.opts:
        if el.opts.get("coords"):
            path = [tuple(map(float, c)) for c in el.opts["coords"]]
        elif el.feature is not None and el.feature_to is not None:
            path = list(closest(el.feature, el.feature_to))
        else:
            path = []
        el.opts["_path"] = path
    return el.opts["_path"]


LOCAL_KM = 30.0  # how much of each shore's land decides which way the crossing runs


def heading(el) -> tuple[float, float]:
    """The way a bridge crosses, as a unit (east, north) vector: from the land round one end to the land round the
    other (each shore's outline within LOCAL_KM of the crossing, averaged). The exact narrowest line can run along
    the coast (Messina's runs north-south), and a bridge drawn that way at a readable size lay on the shore."""
    if "_dir" not in el.opts:
        p, q = ends(el)[:1] + ends(el)[-1:]
        k = math.cos(math.radians((p[1] + q[1]) / 2))
        def land(f, c):
            if not f.parts:
                return np.asarray(c, dtype=float)
            v = np.concatenate(_rings(f))
            dist = np.hypot((v[:, 0] - c[0]) * 111.0 * k, (v[:, 1] - c[1]) * 111.0)
            near = v[dist < LOCAL_KM]
            return near.mean(axis=0) if len(near) else np.asarray(c, dtype=float)
        a, b = land(el.feature, p), land(el.feature_to, q)
        e, n = (b[0] - a[0]) * 111.0 * k, (b[1] - a[1]) * 111.0
        if math.hypot(e, n) < 1e-6:
            e, n = (q[0] - p[0]) * 111.0 * k, (q[1] - p[1]) * 111.0
        size = math.hypot(e, n) or 1.0
        el.opts["_dir"] = (e / size, n / size)
    return el.opts["_dir"]


def inside(f, lon: float, lat: float) -> bool:
    """Whether a point is on a place's land (even-odd over its outlines)."""
    hit = False
    for part in f.parts:
        b = part.bbox
        if not (b[0] <= lon <= b[2] and b[1] <= lat <= b[3]):
            continue
        for ring in part.rings:
            r = np.asarray(ring, dtype=float)
            x0, y0, x1, y1 = r[:-1, 0], r[:-1, 1], r[1:, 0], r[1:, 1]
            cross = ((y0 > lat) != (y1 > lat)) & (lon < (x1 - x0) * (lat - y0) / np.where(y1 == y0, 1e-12, y1 - y0) + x0)
            hit ^= bool(np.count_nonzero(cross) % 2)
    return hit


def span(cam, el, least: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """A bridge's or measure's two ends on screen: across the water from land to land (heading), as long as the
    real crossing or `least` pixels, whichever is more, slid along itself (still over the crossing) so that each
    end rests on its own shore's land."""
    path = ends(el)
    p, q = path[0], path[-1]
    (x0, y0), (x1, y1) = cam.pt(*p), cam.pt(*q)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    e, n = heading(el)
    L = max(least, math.hypot(x1 - x0, y1 - y0))
    real = math.hypot(x1 - x0, y1 - y0)
    room = max(0.0, (L - real) / 2 - 10)  # how far it may slide and still cover the whole crossing
    best, best_score = 0.0, -1
    for s in np.linspace(-room, room, 15) if room > 0 else [0.0]:
        a = (mx + e * (s - L / 2), my - n * (s - L / 2))
        b = (mx + e * (s + L / 2), my - n * (s + L / 2))
        score = inside(el.feature, *cam.lonlat(*a)) + inside(el.feature_to, *cam.lonlat(*b))
        if score > best_score or (score == best_score and abs(s) < abs(best)):
            best, best_score = float(s), score
    return (mx + e * (best - L / 2), my - n * (best - L / 2)), (mx + e * (best + L / 2), my - n * (best + L / 2))


def length_label(el) -> tuple[str, str]:
    """("3.1 KM", "(1.9 MI)"), or the element's own text."""
    if el.text:
        return el.text.upper(), ""
    path = ends(el)
    d = sum(km(a, b) for a, b in zip(path, path[1:]))
    big = f"{d:.1f}" if d < 10 else f"{d:,.0f}"
    mi = d * 0.621371
    return f"{big} KM", f"({mi:.1f} MI)" if mi < 10 else f"({mi:,.0f} MI)"


# --------------------------------------------------------------------------- drawing


def _layer(ov: Image.Image) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    lay = Image.new("RGBA", ov.size, (0, 0, 0, 0))
    return lay, ImageDraw.Draw(lay)


def _fade(lay: Image.Image, a: float) -> Image.Image:
    if a < 1:
        lay.putalpha(lay.getchannel("A").point(lambda v: int(v * a)))
    return lay


def _along(pts: list[tuple[float, float]], u: float) -> list[tuple[float, float]]:
    """The first u of a polyline (by length)."""
    segs = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    total = sum(segs) * u
    out = [pts[0]]
    for (a, b), s in zip(zip(pts, pts[1:]), segs):
        if total <= 0:
            break
        f = min(1.0, total / s) if s else 1.0
        out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        total -= s
    return out


def _bridge_art(L: float) -> tuple[Image.Image, float]:
    """The bridge seen from the side, L pixels long, deck along the canvas's middle: white deck, two portal towers,
    the main cable sagging between them, hangers. Returns the picture and the deck's y on it."""
    deck = max(10, min(26, L * 0.06))
    tower = max(56, min(220, L * 0.3))
    pad = 24
    w, h = int(L + 2 * pad), int(tower + deck * 2 + 2 * pad)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    y = pad + tower  # the deck's top
    x0, x1 = pad, pad + L
    tops = []
    for s in (0.17, 0.83):
        bx = x0 + L * s
        tw = max(7, deck * 0.6)
        d.rectangle([bx - tw / 2, y - tower, bx + tw / 2, y + deck * 1.6], fill=(232, 232, 238, 255), outline=(60, 62, 72, 255), width=2)
        d.rectangle([bx - tw * 1.2, y - tower * 0.9, bx + tw * 1.2, y - tower * 0.84], fill=(232, 232, 238, 255),
                    outline=(60, 62, 72, 255), width=2)
        tops.append((bx, y - tower))

    def cable(a, b, sag):
        return [(a[0] + (b[0] - a[0]) * k / 24, a[1] + (b[1] - a[1]) * k / 24 + sag * 4 * (k / 24) * (1 - k / 24)) for k in range(25)]
    line = cable((x0, y), tops[0], tower * 0.1) + cable(tops[0], tops[1], tower * 0.78)[1:] + cable(tops[1], (x1, y), tower * 0.1)[1:]
    for x, cy in line[3:-3:2]:
        if y - cy > 5:
            d.line([x, cy, x, y], fill=(96, 98, 108, 230), width=2)
    d.line(line, fill=(50, 52, 62, 255), width=4, joint="curve")
    d.rectangle([x0, y, x1, y + deck], fill=(240, 240, 244, 255), outline=(60, 62, 72, 255), width=3)
    d.line([x0 + 4, y + deck * 0.55, x1 - 4, y + deck * 0.55], fill=(196, 198, 206, 255), width=max(2, int(deck * 0.16)))
    return img, y + deck / 2


def bridge(ov: Image.Image, p0, p1, t: float, at: float) -> None:
    """A suspension bridge across p0-p1: it falls from above onto its shadow and settles with a little bounce; seen
    from the side and turned along the crossing, towers up (a crossing that runs north-south leans, never lies flat
    on its towers)."""
    u = (t - at) / DROP
    if u <= 0:
        return
    if p1[0] < p0[0]:
        p0, p1 = p1, p0  # drawn left to right, so its towers stand up
    L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    if L < 16:
        return
    ang = math.degrees(math.atan2(-(p1[1] - p0[1]), p1[0] - p0[0]))  # -90..90
    art, deck_y = _bridge_art(L)
    lay, d = _layer(ov)
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    land = min(1.0, max(0.0, u))
    sh, sd = _layer(ov)  # the shadow on the water, sharper as it lands
    sd.line([p0[0] + 10, p0[1] + 16, p1[0] + 10, p1[1] + 16], fill=(0, 0, 0, int(130 * land)), width=22)
    lay.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6 + 12 * (1 - land))))
    lift = -320 * (1 - min(1.0, backout(u)))  # falls, overshoots a touch, settles
    # the art's deck centre goes on the crossing's middle: rotate about the canvas centre, then shift by the deck's offset
    rot = art.rotate(ang, resample=Image.BICUBIC, expand=True)
    off = deck_y - art.height / 2  # the deck sits below the canvas centre by this much, along the art's own down
    ox, oy = off * math.sin(math.radians(ang)), off * math.cos(math.radians(ang))
    lay.alpha_composite(rot, (int(mx - rot.width / 2 - ox), int(my - rot.height / 2 - oy + lift)))
    ov.alpha_composite(_fade(lay, min(1.0, u * 3)))


def builds(ov: Image.Image, pts: list[tuple[float, float]], kind: str, t: float, at: float) -> None:
    """A wall, fence or canal building itself along its path."""
    u = ease((t - at) / BUILD)
    if u <= 0 or len(pts) < 2:
        return
    path = _along(pts, u)
    if len(path) < 2:
        return
    lay, d = _layer(ov)
    if kind == "canal":
        d.line(path, fill=(214, 196, 150, 255), width=30, joint="curve")  # its banks
        d.line(path, fill=(40, 120, 200, 255), width=18, joint="curve")
        d.line(path, fill=(120, 190, 240, 255), width=5, joint="curve")
    elif kind == "fence":
        sh, sd = _layer(ov)
        sd.line([(x + 6, y + 8) for x, y in path], fill=(0, 0, 0, 110), width=6, joint="curve")
        lay.alpha_composite(sh.filter(ImageFilter.GaussianBlur(4)))
        for h in (10, 22):
            d.line([(x, y - h) for x, y in path], fill=(60, 60, 66, 255), width=3, joint="curve")
        dist, step = 0.0, 26.0
        for (xa, ya), (xb, yb) in zip(path, path[1:]):
            seg = math.hypot(xb - xa, yb - ya)
            while dist <= seg:
                f = dist / seg if seg else 0
                x, y = xa + (xb - xa) * f, ya + (yb - ya) * f
                d.line([x, y + 2, x, y - 30], fill=(80, 64, 48, 255), width=4)
                dist += step
            dist -= seg
    else:  # a stone wall rising from the ground, battlements on top
        h = 40
        sh, sd = _layer(ov)
        sd.line([(x + 9, y + 10) for x, y in path], fill=(0, 0, 0, 130), width=16, joint="curve")
        lay.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)))
        for (xa, ya), (xb, yb) in zip(path, path[1:]):
            d.polygon([(xa, ya), (xb, yb), (xb, yb - h), (xa, ya - h)], fill=(178, 150, 108, 255), outline=(96, 74, 50, 255))
        d.line([(x, y - h) for x, y in path], fill=(214, 190, 146, 255), width=7, joint="curve")
        d.line(path, fill=(96, 74, 50, 255), width=3, joint="curve")
        dist, step = 0.0, 22.0
        for (xa, ya), (xb, yb) in zip(path, path[1:]):
            seg = math.hypot(xb - xa, yb - ya)
            while dist <= seg:
                f = dist / seg if seg else 0
                x, y = xa + (xb - xa) * f, ya + (yb - ya) * f - h
                d.rectangle([x - 5, y - 11, x + 5, y - 2], fill=(196, 170, 126, 255), outline=(96, 74, 50, 255))
                dist += step
            dist -= seg
    ov.alpha_composite(lay)


def measure(ov: Image.Image, p0, p1, big: str, small: str, t: float, at: float, clear: bool = False) -> None:
    """A dashed white line that draws itself between the two points, ticks at its ends, then its length along it."""
    u = ease((t - at) / DRAW)
    if u <= 0:
        return
    d = ImageDraw.Draw(ov)
    L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    if L < 4:
        return
    nx, ny = -(p1[1] - p0[1]) / L, (p1[0] - p0[0]) / L
    end = (p0[0] + (p1[0] - p0[0]) * u, p0[1] + (p1[1] - p0[1]) * u)
    bw_render.dashed(d, p0, end, INK + (200,), width=11, dash=26, gap=14)
    bw_render.dashed(d, p0, end, (255, 255, 255, 255), width=6, dash=26, gap=14)
    for (x, y), show in ((p0, True), (p1, u >= 1)):
        if show:
            d.line([x - nx * 18, y - ny * 18, x + nx * 18, y + ny * 18], fill=(255, 255, 255, 255), width=6)
    a = ease((t - at - DRAW) / 0.35)
    if a <= 0:
        return
    f_big, f_small = bw_render.font(64), bw_render.font(38)
    w = int(max(f_big.getlength(big), f_small.getlength(small) if small else 0)) + 40
    h = 64 + (46 if small else 0) + 30
    tag = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(tag)
    td.text((w / 2, 10), big, font=f_big, fill=YELLOW, stroke_width=7, stroke_fill=INK, anchor="ma")
    if small:
        td.text((w / 2, 78), small, font=f_small, fill=YELLOW, stroke_width=5, stroke_fill=INK, anchor="ma")
    ang = math.degrees(math.atan2(-(p1[1] - p0[1]), p1[0] - p0[0]))
    if ang > 90:
        ang -= 180
    elif ang < -90:
        ang += 180
    if abs(ang) > 70:  # an almost vertical line: the words stay level beside it
        ang = 0.0
    tag = tag.rotate(ang, resample=Image.BICUBIC, expand=True)
    s = backout(a)
    if s < 0.98:
        tag = tag.resize((max(1, int(tag.width * s)), max(1, int(tag.height * s))), Image.BILINEAR)
    side = 1 if ny <= 0 else -1  # the words on the upper side of the line, clear of a bridge drawn over it
    gap = h * 0.55 + 12 + (90 if clear else 0)
    if abs(nx) > 0.8:  # a north-south line: the words beside it, to the left when there is room
        side = 1 if nx > 0 and (p0[0] + p1[0]) / 2 > ov.width * 0.4 else -1 if nx > 0 else 1
    mx, my = (p0[0] + p1[0]) / 2 + nx * side * gap, (p0[1] + p1[1]) / 2 + ny * side * gap
    mx = min(max(tag.width / 2 + 20, mx), ov.width - tag.width / 2 - 20)
    ov.alpha_composite(tag, (int(mx - tag.width / 2), int(my - tag.height / 2)))
