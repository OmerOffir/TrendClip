"""Borderline Why's satellite look: the renderer of modules.bw_render with richer pictures.

- the land is NASA's Blue Marble (shaded relief and sea depths; public domain), fetched from NASA GIBS in tiles at the
  detail the camera needs and cached in assets/bw_fx/;
- a scene can open on a spinning 3D globe (scene option globe: true);
- a highlight can fill a country or region with its waving flag (element option flag: a Wikimedia Commons file);
- icons (Google's Noto 3D emoji, Apache 2.0) pop in on their cue word, or ride along a route (a car on a road);
- flags on poles, routes that draw themselves, and hand-drawn sketches (an ink line that traces itself).

Everything is drawn by code from real map data and free images; nothing is AI-generated.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from modules import bw_geo
from modules.bw_render import (CY, FIXED, H, MAP_BOTTOM, MAP_TOP, W, Cam, Element, Renderer, Scene, curve, ease, flat,
                               font, ramp, text)

logger = logging.getLogger(__name__)

FX_DIR = bw_geo.PROJECT_ROOT / "assets" / "bw_fx"
GIBS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
LAYER = "BlueMarble_ShadedRelief_Bathymetry"
GLOBAL_PX = 8192  # the whole world, 22.8 px per degree
LEVELS = ((90, 5.0), (220, 2.0))  # (pixels per degree, tile size in degrees); 220 is the layer's 500 m limit
FLAG_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width=1600"
ICON_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/3D/png/512/emoji_u{code}.png"
HAND_URL = "https://github.com/google/fonts/raw/main/ofl/patrickhand/PatrickHand-Regular.ttf"  # SIL Open Font License

# the icons a script may name (Noto 3D emoji code points)
ICONS = dict(
    car="1f697", truck="1f69a", train="1f682", plane="2708", ship="1f6a2", sailboat="26f5", canoe="1f6f6", horse="1f40e",
    camel="1f42b", walking="1f6b6", money_bag="1f4b0", cash="1f4b5", coin="1fa99", medal="1f947", gem="1f48e", oil="1f6e2",
    wheat="1f33e", fish="1f41f", otter="1f9a6", tree="1f332", mountain="26f0", volcano="1f30b", snowflake="2744", ice="1f9ca",
    sun="2600", desert="1f3dc", island="1f3dd", wave="1f30a", fire="1f525", explosion="1f4a5", map="1f5fa", scroll="1f4dc",
    pen="1f58a", ruler="1f4cf", compass="1f9ed", telescope="1f52d", magnifier="1f50d", calendar="1f4c5", hourglass="231b",
    clock="23f0", crown="1f451", castle="1f3f0", house="1f3e0", government="1f3db", factory="1f3ed", church="26ea",
    bridge="1f309", roadblock="1f6a7", customs="1f6c2", stop_hand="270b", cross="274c", check="2705", forbidden="1f6ab",
    question="2753", thinking="1f914", surprised="1f62e", people="1f465", guard="1f482", swords="2694", shield="1f6e1",
    bomb="1f4a3", chart_up="1f4c8", chart_down="1f4c9", globe="1f30d", key="1f511", lock="1f512", scissors="2702",
)
# where icons and poles stand when a script names no place for them (x, y on the 1080x1920 frame)
ICON_SLOTS = {"map": ((540, 900), (230, 680), (850, 680), (540, 1180)), "card": ((540, 1230), (260, 1230), (820, 1230))}
POLE_SLOTS = {"map": ((150, 1270), (420, 1270), (690, 1270)), "card": ((200, 1340), (640, 1340), (420, 1340))}
GLOBE_MAX_PPD = 30.0  # the opening globe stops at about a continent; the next scene zooms in from there

WATER = (46, 112, 158)
WATER_LINE = (150, 205, 235)
SPACE = ((4, 8, 20), (16, 32, 60))


def _get(url: str, params: dict[str, Any] | None = None) -> bytes:
    import requests

    r = requests.get(url, params=params, timeout=60, headers={"User-Agent": bw_geo.USER_AGENT})
    r.raise_for_status()
    return r.content


def _cached(path: Path, url: str, params: dict[str, Any] | None = None) -> Path:
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _get(url, params)
        path.with_suffix(".part").write_bytes(data)
        path.with_suffix(".part").replace(path)
    return path


def grade(img: Image.Image) -> Image.Image:
    """Blue Marble is dark; brighten it and lift the colors toward a picture-book look."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    a = a ** 0.72
    gray = a.mean(axis=2, keepdims=True)
    a = gray + (a - gray) * 1.35
    a[..., 2] += 0.04 * (1 - a[..., 2])  # a touch of blue in the shadows (the sea)
    return Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))


# --------------------------------------------------------------------------- the satellite picture


class Satellite:
    """The world picture at three levels of detail; each frame is a crop and scale of one of them."""

    def __init__(self):
        path = _cached(FX_DIR / "sat" / f"global_{GLOBAL_PX}.jpg", GIBS, self._wms(-180, -90, 180, 90, GLOBAL_PX, GLOBAL_PX // 2))
        self.world = grade(Image.open(path))
        self.globe_tex = np.asarray(self.world.resize((4096, 2048), Image.LANCZOS))
        self.tiles: dict[tuple[int, int, int], Image.Image] = {}
        self.mosaic_key: tuple | None = None
        self.mosaic: Image.Image | None = None

    @staticmethod
    def _wms(x0, y0, x1, y1, w, h) -> dict[str, Any]:
        return dict(SERVICE="WMS", REQUEST="GetMap", VERSION="1.3.0", LAYERS=LAYER, STYLES="", CRS="EPSG:4326",
                    BBOX=f"{y0},{x0},{y1},{x1}", WIDTH=w, HEIGHT=h, FORMAT="image/jpeg")

    def tile(self, level: int, i: int, j: int) -> Image.Image:
        key = (level, i, j)
        if key not in self.tiles:
            px, deg = LEVELS[level]
            x0, y1 = -180 + i * deg, 90 - j * deg
            n = int(round(px * deg))
            path = _cached(FX_DIR / "sat" / str(px) / f"{i}_{j}.jpg", GIBS, self._wms(x0, y1 - deg, x0 + deg, y1, n, n))
            self.tiles[key] = grade(Image.open(path))
        return self.tiles[key]

    def frame(self, cam: Cam) -> Image.Image:
        need = cam.ppd * 0.85
        x0, y1 = cam.lonlat(0, H)
        x1, y0 = cam.lonlat(W, 0)
        if need <= 30:
            src, px, lon0, lat0 = self.world, GLOBAL_PX / 360, -180.0, 90.0
        else:
            level = 0 if need <= 120 else 1
            px, deg = LEVELS[level]
            i0, i1 = int((x0 + 180) // deg), int((x1 + 180) // deg)
            j0, j1 = int((90 - y0) // deg), int((90 - y1) // deg)
            key = (level, i0, i1, j0, j1)
            if key != self.mosaic_key:
                n = int(round(px * deg))
                m = Image.new("RGB", ((i1 - i0 + 1) * n, (j1 - j0 + 1) * n))
                for i in range(i0, i1 + 1):
                    for j in range(j0, j1 + 1):
                        m.paste(self.tile(level, i, j), ((i - i0) * n, (j - j0) * n))
                self.mosaic_key, self.mosaic = key, m
            src, lon0, lat0 = self.mosaic, -180 + i0 * deg, 90 - j0 * deg
        sx = px / (cam.ppd * cam.k)
        sy = px / cam.ppd
        c = (cam.lon - W / 2 / (cam.ppd * cam.k) - lon0) * px
        f = (lat0 - cam.lat) * px - CY * sy
        return src.transform((W, H), Image.AFFINE, (sx, 0, c, 0, sy, f), resample=Image.BILINEAR)


_satellite: Satellite | None = None


def satellite() -> Satellite:
    global _satellite
    if _satellite is None:
        _satellite = Satellite()
    return _satellite


# --------------------------------------------------------------------------- the globe


class GlobeCam:
    """An orthographic globe of radius r (pixels) centered on (lon, lat), drawn at (cx, cy); the same xy/pt as Cam."""

    def __init__(self, lon: float, lat: float, r: float, cx: float = W / 2, cy: float = CY):
        self.lon, self.lat, self.r, self.cx, self.cy = lon, lat, r, cx, cy
        self.ppd = r * math.pi / 180
        self.k = 1.0

    def project(self, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        la, lo = np.radians(lat), np.radians(lon - self.lon)
        p0 = math.radians(self.lat)
        cosc = math.sin(p0) * np.sin(la) + math.cos(p0) * np.cos(la) * np.cos(lo)
        x = np.cos(la) * np.sin(lo)
        y = math.cos(p0) * np.sin(la) - math.sin(p0) * np.cos(la) * np.cos(lo)
        back = cosc < 0
        if back.any():  # the far side is pushed to the rim
            n = np.hypot(x, y) + 1e-9
            x = np.where(back, x / n, x)
            y = np.where(back, y / n, y)
        return self.cx + self.r * x, self.cy - self.r * y, cosc

    def xy(self, a: np.ndarray) -> np.ndarray:
        x, y, _ = self.project(a[:, 0], a[:, 1])
        return np.stack([x, y], axis=1).astype(np.float32)

    def pt(self, lon: float, lat: float) -> tuple[float, float]:
        x, y, _ = self.project(np.array([lon]), np.array([lat]))
        return float(x[0]), float(y[0])

    def facing(self, lon: float, lat: float) -> float:
        return float(self.project(np.array([lon]), np.array([lat]))[2][0])

    def step(self) -> int:
        return 16


_space: Image.Image | None = None


def space() -> Image.Image:
    global _space
    if _space is None:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.clip(np.hypot(xx - W / 2, yy - CY) / (H * 0.75), 0, 1)[..., None]
        a = np.array(SPACE[1], np.float32) * (1 - d) + np.array(SPACE[0], np.float32) * d
        rng = np.random.default_rng(3)
        for _ in range(260):  # a few stars
            x, y = rng.integers(0, W), rng.integers(0, H)
            a[y, x] = 120 + rng.integers(0, 120)
        _space = Image.fromarray(a.clip(0, 255).astype(np.uint8))
    return _space


def globe_image(cam: GlobeCam) -> Image.Image:
    """The textured globe with a shaded rim and a thin glow of atmosphere."""
    tex = satellite().globe_tex
    th, tw = tex.shape[:2]
    img = space().copy()
    r = cam.r
    x0, x1 = max(0, int(cam.cx - r - 40)), min(W, int(cam.cx + r + 40))
    y0, y1 = max(0, int(cam.cy - r - 40)), min(H, int(cam.cy + r + 40))
    if x1 <= x0 or y1 <= y0:
        return img
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    x = (xx - cam.cx) / r
    y = -(yy - cam.cy) / r
    rho2 = x * x + y * y
    inside = rho2 < 1
    z = np.sqrt(np.clip(1 - rho2, 0, 1))
    p0 = math.radians(cam.lat)
    lat = np.degrees(np.arcsin(np.clip(z * math.sin(p0) + y * math.cos(p0), -1, 1)))
    lon = cam.lon + np.degrees(np.arctan2(x, z * math.cos(p0) - y * math.sin(p0)))
    u = (((lon + 180) % 360) / 360 * tw).astype(np.int32) % tw
    v = np.clip(((90 - lat) / 180 * th).astype(np.int32), 0, th - 1)
    rgb = tex[v, u].astype(np.float32) * (0.45 + 0.55 * z[..., None] ** 0.6)[..., :]
    rho = np.sqrt(rho2)
    glow = np.clip(1 - (rho - 1) / 0.06, 0, 1) * (rho >= 1)  # the atmosphere outside the rim
    rim = np.clip((rho - 0.9) / 0.1, 0, 1) * inside  # and a blue haze just inside it
    region = np.asarray(img, dtype=np.float32)[y0:y1, x0:x1].copy()
    sky = np.array((110, 170, 255), np.float32)
    out = np.where(inside[..., None], rgb * (1 - 0.35 * rim[..., None]) + sky * 0.35 * rim[..., None], region)
    out = out * (1 - 0.7 * glow[..., None]) + sky * 0.7 * glow[..., None]
    arr = np.asarray(img).copy()
    arr[y0:y1, x0:x1] = out.clip(0, 255).astype(np.uint8)
    return Image.fromarray(arr)


# --------------------------------------------------------------------------- flags, icons, fonts


_flags: dict[str, Image.Image] = {}
_icons: dict[tuple[str, int], Image.Image] = {}


def flag_image(name: str) -> Image.Image:
    if name not in _flags:
        safe = "".join(c if c.isalnum() or c in "._-()" else "_" for c in name)
        _flags[name] = Image.open(_cached(FX_DIR / "flags" / f"{safe}.png", FLAG_URL.format(name=name))).convert("RGB")
    return _flags[name]


def icon_image(code: str, size: int) -> Image.Image:
    key = (code, size)
    if key not in _icons:
        if (code, 512) not in _icons:
            _icons[(code, 512)] = Image.open(_cached(FX_DIR / "icons" / f"{code}.png", ICON_URL.format(code=code))).convert("RGBA")
        _icons[key] = _icons[(code, 512)].resize((size, size), Image.LANCZOS)
    return _icons[key]


_hand: dict[int, Any] = {}


def hand_font(size: int):
    if size not in _hand:
        from PIL import ImageFont

        try:
            _hand[size] = ImageFont.truetype(str(_cached(FX_DIR / "fonts" / "PatrickHand-Regular.ttf", HAND_URL)), size)
        except Exception as err:  # noqa: BLE001 - the serif font still reads fine
            logger.warning("No handwriting font: %s", err)
            _hand[size] = font(size, serif=True)
    return _hand[size]


def backout(u: float) -> float:
    """0 -> 1 with a little overshoot (a pop)."""
    u = min(1.0, max(0.0, u))
    c = 1.9
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


def wave(img: Image.Image, t: float, amp: float = 0.03) -> Image.Image:
    """A flag in the wind: columns shift up and down and bands of light run along it."""
    a = np.asarray(img, dtype=np.float32)
    h, w = a.shape[:2]
    xs = np.arange(w, dtype=np.float32)
    shift = (amp * h * np.sin(xs / max(40, w / 3) - t * 4) * (0.3 + 0.7 * xs / w)).astype(np.int32)
    rows = np.clip(np.arange(h)[:, None] + shift[None, :], 0, h - 1)
    a = a[rows, np.arange(w)[None, :]]
    light = 0.88 + 0.2 * np.sin(xs / max(30, w / 4) - t * 4 + 1.2)
    return Image.fromarray(np.clip(a * light[None, :, None], 0, 255).astype(np.uint8))


# --------------------------------------------------------------------------- the renderer


def icon_code(name: str) -> str:
    """A script's icon name (or a code point) to its code point; empty when unknown."""
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    if key in ICONS:
        return ICONS[key]
    return key if key in ICONS.values() else ""


def flag_name(name: str) -> str:
    name = name.strip().replace("_", " ")
    if name and not name.lower().startswith("flag of"):
        name = f"Flag of {name}"
    if name and not name.lower().endswith((".svg", ".png")):
        name += ".svg"
    return name


def usable_flag(name: str) -> str:
    """The Commons file that exists for a script's flag ("Flag of the United States.svg" or "United States"), or ""."""
    tries = [flag_name(name)]
    if tries[0] and not tries[0].lower().startswith("flag of the "):
        tries.append("Flag of the " + tries[0][8:])
    for t in tries:
        try:
            flag_image(t)
            return t
        except Exception:  # noqa: BLE001 - try the next spelling, else go without the flag
            continue
    return ""


def dress(spec) -> list[str]:
    """Get a script's extras ready: flags and icons that exist (the rest dropped, so a bad name never stops a
    video), places for unplaced icons and poles, and the opening globe. Returns what was dropped."""
    dropped: list[str] = []
    for n, scene in enumerate(spec.scenes):
        keep = []
        icons = poles = 0
        for el in scene.elements:
            if el.opts.get("flag"):
                ok = usable_flag(str(el.opts["flag"]))
                if not ok:
                    dropped.append(f"flag {el.opts['flag']!r}")
                    el.opts.pop("flag")
                    if el.kind == "pole":
                        continue
                else:
                    el.opts["flag"] = ok
            if el.kind == "pole" and not el.opts.get("flag"):
                continue
            if el.kind == "icon" or (el.kind == "route" and el.opts.get("icon")):
                code = icon_code(str(el.opts.get("icon", "")))
                try:
                    if not code:
                        raise KeyError(el.opts.get("icon"))
                    icon_image(code, 64)
                except Exception:  # noqa: BLE001
                    dropped.append(f"icon {el.opts.get('icon')!r}")
                    el.opts.pop("icon", None)
                    if el.kind == "icon":
                        continue
                else:
                    el.opts["icon"] = code
            free = el.feature is None and "at" not in el.opts and "x" not in el.opts
            if el.kind == "icon" and free:
                slots = ICON_SLOTS[scene.kind]
                el.opts["x"], el.opts["y"] = slots[icons % len(slots)]
                icons += 1
            elif el.kind == "icon" and el.feature is not None and "dy" not in el.opts:
                el.opts["dy"] = -int(el.opts.get("size", 190) * 0.6)  # stand above the place, not on it
            if el.kind == "pole" and free:
                slots = POLE_SLOTS[scene.kind]
                el.opts["x"], el.opts["y"] = slots[poles % len(slots)]
                poles += 1
            keep.append(el)
        scene.elements = keep
    first = spec.scenes[0] if spec.scenes else None
    if first is not None and first.kind == "map" and first.view and not any(s.opts.get("globe") for s in spec.scenes):
        first.opts["globe"] = True
        lon, lat, ppd = first.view
        first.view = (lon, lat, min(ppd, GLOBE_MAX_PPD))
    return dropped


class FxRenderer(Renderer):
    def __init__(self, spec):
        super().__init__(spec)
        self.sat = satellite()
        if dropped := dress(spec):
            logger.info("Left out of the video: %s", ", ".join(dropped))

    # -- cameras --

    def globe_cam(self, i: int, t: float) -> GlobeCam:
        """The opening globe turns from the Atlantic to the scene's place and grows until it matches the flat map."""
        scene = self.spec.scenes[i]
        lon, lat, ppd = scene.view
        o = scene.opts.get("globe") if isinstance(scene.opts.get("globe"), dict) else {}
        r_end = ppd * 180 / math.pi
        start_lon, start_lat = o.get("from", (lon + 70, max(-10.0, lat - 25)))
        u = ease((t - scene.start) / o.get("spin", 2.2))
        grow = ease((t - scene.start - 1.0) / max(1.0, scene.end - scene.start - 1.0))
        r = r_end * (0.62 + 0.38 * u) * (1 + 0.12 * grow)
        return GlobeCam(start_lon + (lon - start_lon) * u, start_lat + (lat - start_lat) * u, r)

    # -- map --

    def base(self, cam: Cam, fills: dict[int, tuple[tuple[int, int, int], float]]) -> Image.Image:
        img = self.sat.frame(cam).convert("RGBA")
        self.vectors(img, cam, fills)
        return img

    def vectors(self, img: Image.Image, cam, fills) -> None:
        """Lakes in clean water, highlights as tinted glass, white borders with a soft shadow."""
        globe = isinstance(cam, GlobeCam)
        step = cam.step()
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for key in ("countries", "regions"):
            for feat, part in self.parts(key, cam):
                if id(feat) in fills:
                    rgb, a = fills[id(feat)]
                    pts = cam.xy(part.at(step)[0])
                    if len(pts) > 2:
                        od.polygon(flat(pts), fill=rgb + (int(95 * a),))
        img.alpha_composite(ov)
        if not globe:
            water = Image.new("L", (W, H), 0)
            wd = ImageDraw.Draw(water)
            outlines = []
            for feat, part in self.layers["lakes"].visible(cam, 3):
                rings = part.at(step)
                outer = cam.xy(rings[0])
                if len(outer) > 2:
                    wd.polygon(flat(outer), fill=235)
                    outlines.append(outer)
                for hole in rings[1:]:
                    h = cam.xy(hole)
                    if len(h) > 2:
                        wd.polygon(flat(h), fill=0)
            img.paste(WATER, (0, 0), water)
            d = ImageDraw.Draw(img)
            for o in outlines:
                d.line(flat(np.vstack([o, o[:1]])), fill=WATER_LINE + (200,), width=2)
        lines = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lines)
        if not globe and cam.ppd > 20:
            for feat, part in self.layers["regions"].visible(cam, 6):
                pts = cam.xy(part.at(step)[0])
                if len(pts) > 2:
                    ld.line(flat(np.vstack([pts, pts[:1]])), fill=(255, 255, 255, 110), width=2)
        for feat, part in self.parts("countries", cam, 3):
            pts = cam.xy(part.at(step)[0])
            if len(pts) > 2:
                ld.line(flat(np.vstack([pts, pts[:1]])), fill=(255, 255, 255, 230), width=3 if cam.ppd < 60 else 4)
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        shadow.putalpha(lines.getchannel("A").point(lambda v: v // 2).filter(ImageFilter.GaussianBlur(4)))
        img.alpha_composite(shadow, (3, 4))
        img.alpha_composite(lines)

    def parts(self, key: str, cam, min_px: float = 1.2):
        if not isinstance(cam, GlobeCam):
            return self.layers[key].visible(cam, min_px)
        layer = self.layers[key]
        if key == "regions" or not len(layer.items):
            return []
        b = layer.boxes
        big = np.maximum(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]) * cam.ppd >= max(3.0, min_px)
        face = np.maximum.reduce([cam.project(b[:, 0], b[:, 1])[2], cam.project(b[:, 2], b[:, 3])[2],
                                  cam.project((b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2)[2]])
        return [layer.items[i] for i in np.nonzero(big & (face > 0.05))[0]]

    def flag_fill(self, img: Image.Image, cam, feat: bw_geo.Feature, name: str, a: float, t: float) -> None:
        """The country filled with its waving flag, wiped in from the left."""
        step = cam.step()
        polys = [cam.xy(p.at(step)[0]) for p in feat.parts]
        polys = [p for p in polys if len(p) > 2]
        if not polys:
            return
        x0, y0, x1, y1 = feat.bbox()
        corners = cam.xy(np.array([[x0, y0], [x1, y1], [x0, y1], [x1, y0], [(x0 + x1) / 2, y0], [(x0 + x1) / 2, y1]], np.float32))
        bx0, by0 = corners[:, 0].min(), corners[:, 1].min()
        bx1, by1 = corners[:, 0].max(), corners[:, 1].max()
        # a place far bigger than the screen gets its flag sized to the part on screen
        bx0, by0, bx1, by1 = max(bx0, -40), max(by0, -40), min(bx1, W + 40), min(by1, H + 40)
        if bx1 - bx0 < 2 or by1 - by0 < 2:
            return
        allp = np.vstack(polys)
        ax0, ay0 = max(0, int(min(bx0, allp[:, 0].min()))), max(0, int(min(by0, allp[:, 1].min())))
        ax1, ay1 = min(W, int(max(bx1, allp[:, 0].max())) + 1), min(H, int(max(by1, allp[:, 1].max())) + 1)
        if ax1 - ax0 < 2 or ay1 - ay0 < 2:
            return
        fw, fh = max(2, int(bx1 - bx0)), max(2, int(by1 - by0))
        src = flag_image(name)
        scale = max(fw / src.width, fh / src.height)  # cover the mainland, keep the flag's shape
        sw, sh = max(2, int(src.width * scale)), max(2, int(src.height * scale))
        flagimg = wave(src.resize((sw, sh), Image.BILINEAR), t)
        canvas = np.asarray(flagimg)
        ox, oy = bx0 + (fw - sw) / 2, by0 + (fh - sh) / 2
        ys = np.clip(np.arange(ay0, ay1) - int(oy), 0, sh - 1)
        xs = np.clip(np.arange(ax0, ax1) - int(ox), 0, sw - 1)
        patch = Image.fromarray(canvas[ys[:, None], xs[None, :]])
        mask = Image.new("L", (ax1 - ax0, ay1 - ay0), 0)
        md = ImageDraw.Draw(mask)
        for p in polys:
            md.polygon(flat(p - np.array([ax0, ay0], np.float32)), fill=int(225 * min(1.0, a * 1.5)))
        if not isinstance(cam, GlobeCam):  # lakes stay water
            for _, part in self.layers["lakes"].visible(cam, 3):
                lake = cam.xy(part.at(step)[0])
                if len(lake) > 2:
                    md.polygon(flat(lake - np.array([ax0, ay0], np.float32)), fill=0)
        wipe = np.clip((np.linspace(0, 1, ax1 - ax0) - (a * 1.3 - 0.3)) / -0.3, 0, 1)
        m = (np.asarray(mask, dtype=np.float32) * wipe[None, :]).astype(np.uint8)
        img.paste(patch, (ax0, ay0), Image.fromarray(m))

    def fx_map_frame(self, i: int, t: float) -> Image.Image:
        scene = self.spec.scenes[i]
        globe = bool(scene.opts.get("globe"))
        cam = self.globe_cam(i, t) if globe else self.camera(i, t)
        fills: dict[int, tuple[tuple[int, int, int], float]] = {}
        flags = []
        for el in scene.elements:
            if el.kind == "highlight" and el.feature is not None and not el.feature.is_line and el.feature.parts:
                if el.opts.get("flag"):
                    flags.append(el)
                else:
                    fills[id(el.feature)] = (self.color(el.color), ramp(t, el.at))
        if globe:
            img = globe_image(cam).convert("RGBA")
            self.vectors(img, cam, fills)
        else:
            img = self.base(cam, fills)
        for el in flags:
            a = ramp(t, el.at, 0.7)
            if a > 0:
                self.flag_fill(img, cam, el.feature, el.opts["flag"], a, t)
        if flags:  # borders again on top of the flags
            lines = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ld = ImageDraw.Draw(lines)
            for el in flags:
                if ramp(t, el.at, 0.7) > 0:
                    for p in el.feature.parts:
                        pts = cam.xy(p.at(cam.step())[0])
                        if len(pts) > 2:
                            ld.line(flat(np.vstack([pts, pts[:1]])), fill=(255, 255, 255, 240), width=4)
            img.alpha_composite(lines)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        for el in scene.elements:
            self.element(ov, d, cam, el, t, scene)
        img.alpha_composite(ov)
        return img

    def element(self, ov, d, cam, el: Element, t: float, scene: Scene) -> None:
        if el.kind == "icon":
            self.icon(ov, el, self.anchor(cam, el), t)
        elif el.kind == "pole":
            self.pole(ov, el, self.anchor(cam, el), t)
        elif el.kind == "route":
            self.route(ov, cam, el, t)
        elif el.kind == "sketch":
            self.sketch_on_map(ov, cam, el, t)
        elif el.kind == "highlight" and el.opts.get("flag"):
            if el.text and el.feature is not None:
                text(ov, cam.pt(*el.feature.label_point()), el.text.upper(), 54, a=ramp(t, el.at), max_width=W - 80)
        else:
            super().element(ov, d, cam, el, t, scene)

    def anchor(self, cam, el: Element) -> tuple[float, float]:
        """Where an icon or pole stands: a place (plus dx, dy pixels), a [lon, lat], or a screen x, y on cards."""
        dx, dy = el.opts.get("dx", 0), el.opts.get("dy", 0)
        if cam is None:
            return el.opts.get("x", W / 2) + dx, el.opts.get("y", CY) + dy
        if el.feature is not None:
            x, y = cam.pt(*el.feature.label_point())
        elif "at" in el.opts:
            x, y = cam.pt(*el.opts["at"])
        else:
            x, y = el.opts.get("x", W / 2), el.opts.get("y", CY)
        return x + dx, y + dy

    # -- icons, poles, routes, sketches --

    def icon(self, ov: Image.Image, el: Element, xy: tuple[float, float], t: float, flip: bool = False) -> None:
        u = (t - el.at) / 0.4
        if u <= 0:
            return
        size = int(el.opts.get("size", 190))
        s = backout(u)
        n = max(1, int(el.opts.get("count", 1)))
        for k in range(n):  # a stack: each copy pops a little later, a little higher
            sk = backout((t - el.at - 0.12 * k) / 0.4)
            if sk <= 0.02:
                continue
            px = max(8, int(size * sk))
            im = icon_image(el.opts["icon"], size).resize((px, px), Image.BILINEAR)
            if flip or el.opts.get("flip"):
                im = im.transpose(Image.FLIP_LEFT_RIGHT)
            bob = 5 * math.sin(2.4 * t + k + el.at)
            x = xy[0] + (k - (n - 1) / 2) * size * 0.55
            y = xy[1] - k * size * 0.12 + bob
            sh = Image.new("RGBA", (px, px // 3), (0, 0, 0, 0))
            ImageDraw.Draw(sh).ellipse([px * 0.15, 0, px * 0.85, px // 3 - 1], fill=(0, 0, 0, int(90 * min(1, s))))
            ov.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)), (int(x - px / 2), int(y + px * 0.38)))
            ov.alpha_composite(im, (int(x - px / 2), int(y - px / 2)))
        if el.text:
            text(ov, (xy[0], xy[1] + size * 0.75), el.text.upper(), 46, a=min(1.0, max(0.0, u - 0.5)), max_width=W - 80)

    def pole(self, ov: Image.Image, el: Element, xy: tuple[float, float], t: float) -> None:
        """A flag on a pole that rises out of the ground and waves."""
        u = ease((t - el.at) / 0.5)
        if u <= 0:
            return
        hgt = el.opts.get("height", 230) * u
        x, y = xy
        d = ImageDraw.Draw(ov)
        d.ellipse([x - 22, y - 7, x + 22, y + 7], fill=(0, 0, 0, 90))
        d.line([x, y, x, y - hgt], fill=(70, 70, 76, 255), width=9)
        d.line([x - 2, y, x - 2, y - hgt], fill=(200, 200, 205, 255), width=3)
        d.ellipse([x - 9, y - hgt - 9, x + 9, y - hgt + 9], fill=(230, 190, 70, 255))
        fw = int(el.opts.get("width", 190))
        src = flag_image(el.opts["flag"])
        fh = int(fw * src.height / src.width)
        f = wave(src.resize((fw, fh), Image.BILINEAR), t + el.at, amp=0.06).convert("RGBA")
        f.putalpha(int(255 * min(1.0, u * 1.5)))
        ov.alpha_composite(f, (int(x + 4), int(y - hgt + 6)))
        if el.text:
            text(ov, (x + fw / 2, y + 40), el.text.upper(), 44, a=u, max_width=W - 80)

    def route_points(self, cam, el: Element) -> list[tuple[float, float]]:
        pts = [cam.pt(*p) for p in (el.opts.get("coords") or el.opts["points"])]
        out = [pts[0]]
        for a, b in zip(pts, pts[1:]):  # resample so the moving icon goes at an even speed
            n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 6))
            out += [(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n + 1)]
        return out

    def route(self, ov: Image.Image, cam, el: Element, t: float) -> None:
        """A dashed path that draws itself; an icon (a car, a canoe) can ride at its head."""
        if len(el.opts.get("coords") or el.opts.get("points") or []) < 2:
            return
        u = ease((t - el.at) / el.opts.get("dur", 2.0))
        if u <= 0:
            return
        pts = self.route_points(cam, el)
        n = max(2, int(len(pts) * u))
        rgb = self.color(el.color if el.color != "a" else "gold")
        d = ImageDraw.Draw(ov)
        march = int(t * 40) % 40
        dist = 0.0
        for (xa, ya), (xb, yb) in zip(pts[:n - 1], pts[1:n]):
            seg = math.hypot(xb - xa, yb - ya)
            if (dist + march) % 40 < 24:
                d.line([xa, ya, xb, yb], fill=(0, 0, 0, 120), width=14)
                d.line([xa, ya, xb, yb], fill=rgb + (255,), width=9)
            dist += seg
        if el.opts.get("icon"):
            (xa, ya), (xb, yb) = pts[max(0, n - 4)], pts[n - 1]
            ride = Element(kind="icon", at=el.at, text="", opts={**el.opts, "size": el.opts.get("size", 150)})
            self.icon(ov, ride, (xb, yb - 30), t, flip=xb > xa + 1)
        if el.text and u >= 1:
            text(ov, (pts[-1][0], pts[-1][1] - 120), el.text.upper(), 46, fill=rgb, a=ramp(t, el.at + el.opts.get("dur", 2.0)),
                 max_width=W - 80)

    @staticmethod
    def egg(cx: float, cy: float, rx: float, ry: float, seed: int = 1, corner: bool = True) -> list[tuple[float, float]]:
        """A hand-drawn egg, a little wobbly, with one pointed corner at the top left."""
        rng = np.random.default_rng(seed)
        wob = np.convolve(rng.normal(0, 1, 140), np.ones(9) / 9, mode="same")
        out = []
        for k in range(121):
            th = 2 * math.pi * k / 120 + math.radians(135)
            r = 1 + 0.02 * wob[k]
            x = math.cos(th) * (1 - 0.12 * math.sin(th))
            y = math.sin(th)
            if corner:  # pull the outline out to a point at 135 degrees (the northwest)
                pull = max(0.0, math.cos(th - math.radians(135))) ** 40
                r += 0.3 * pull
            out.append((cx + rx * x * r, cy - ry * y * r))
        return out

    def hand_line(self, ov: Image.Image, pts: list[tuple[float, float]], u: float, rgb, width: int = 10, a: float = 1.0) -> None:
        n = max(2, int(len(pts) * min(1.0, u)))
        if u <= 0:
            return
        d = ImageDraw.Draw(ov)
        al = int(255 * a)
        d.line(pts[:n], fill=rgb + (al,), width=width, joint="curve")
        d.line([(x + 3, y + 2) for x, y in pts[:n]], fill=rgb + (al // 3,), width=max(2, width // 3), joint="curve")

    def sketch_on_map(self, ov: Image.Image, cam, el: Element, t: float) -> None:
        """The 1755-style egg drawn over the real place, so the two can be compared."""
        if el.feature is None:
            return
        x0, y0, x1, y1 = el.feature.bbox()
        (ax, ay), (bx, by) = cam.pt(x0, y1), cam.pt(x1, y0)
        pts = self.egg((ax + bx) / 2, (ay + by) / 2, (bx - ax) * 0.42, (by - ay) * 0.42, corner=el.opts.get("corner", True))
        u = (t - el.at) / el.opts.get("dur", 1.4)
        self.hand_line(ov, pts, u, FIXED["white"], width=8, a=0.95)
        if el.text and u > 1:
            ImageDraw.Draw(ov).text((ax + 10, ay - 30), el.text, font=hand_font(64), fill=(255, 255, 255, 255),
                                    stroke_width=5, stroke_fill=(0, 0, 0, 255), anchor="ls")

    # -- cards --

    def card_frame(self, i: int, t: float) -> Image.Image:
        img = super().card_frame(i, t)
        scene = self.spec.scenes[i]
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for el in scene.elements:
            if el.kind == "icon":
                self.icon(ov, el, self.anchor(None, el), t)
            elif el.kind == "pole":
                self.pole(ov, el, self.anchor(None, el), t)
            elif el.kind == "sketch":
                cx, cy = el.opts.get("x", W / 2), el.opts.get("y", 1080)
                pts = self.egg(cx, cy, el.opts.get("rx", 300), el.opts.get("ry", 220))
                u = (t - el.at) / el.opts.get("dur", 1.6)
                self.hand_line(ov, pts, u, FIXED["ink"], width=11)
                if el.text and u > 1:
                    tip = min(pts, key=lambda p: p[0] + p[1])
                    a = ramp(t, el.at + el.opts.get("dur", 1.6))
                    r = 18 * backout((t - el.at - el.opts.get("dur", 1.6)) / 0.3)
                    d = ImageDraw.Draw(ov)
                    d.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], fill=FIXED["red"] + (int(255 * a),))
                    d.text((tip[0] + 30, tip[1] - 40), el.text, font=hand_font(76), fill=FIXED["red"] + (int(255 * a),), anchor="ls")
        img.alpha_composite(ov)
        return img

    # -- scenes --

    def map_frame(self, i: int, t: float) -> Image.Image:
        img = self.fx_map_frame(i, t)
        scenes = self.spec.scenes
        if i > 0 and scenes[i - 1].opts.get("globe") and t < scenes[i].start + 0.4:
            prev = self.fx_map_frame(i - 1, t)  # the globe keeps growing while the flat map fades in
            img = Image.blend(prev, img, ramp(t, scenes[i].start, 0.4))
        return img
