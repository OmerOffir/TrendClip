"""Borderline Why map data: Natural Earth (public domain) countries, states and provinces, lakes, rivers and
cities, found by name. The script names places; the coordinates and outlines always come from here, so the
video never draws geography a language model made up.

The GeoJSON files are downloaded once into assets/geodata/ (about 90 MB) and turned into a compact cache
(world.pkl) on first use.
"""

from __future__ import annotations

import json
import logging
import pickle
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from trendclip.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "assets" / "geodata"
CACHE = DATA_DIR / "world.pkl"
CACHE_VERSION = 2
NE_VERSION = "v5.1.2"
URLS = (  # the first that answers wins (jsDelivr refuses files over 20 MB)
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{v}/geojson/{f}.geojson",
    "https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@{v}/geojson/{f}.geojson",
)
FILES = {
    "countries": "ne_10m_admin_0_countries",
    "regions": "ne_10m_admin_1_states_provinces",
    "lakes": "ne_10m_lakes",
    "rivers": "ne_10m_rivers_lake_centerlines",
    "places": "ne_10m_populated_places_simple",
}
STEPS = (1, 2, 4, 8, 16, 32)  # vertex decimation levels, picked by zoom
NEAR = 2.0  # degrees: a part this close to the main land belongs in the camera's box (Feature.bbox)


def _union(boxes) -> tuple[float, float, float, float]:
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


class GeoError(RuntimeError):
    pass


@dataclass
class Part:
    """One polygon (outer ring + holes) or one line of a feature, with its bounding box."""
    rings: list[np.ndarray]  # (n, 2) lon/lat; rings[0] is the outline
    bbox: tuple[float, float, float, float]
    _steps: dict[int, list[np.ndarray]] = field(default_factory=dict, repr=False)

    def at(self, step: int) -> list[np.ndarray]:
        if step == 1:
            return self.rings
        if step not in self._steps:
            self._steps[step] = [r[::step] if len(r) > 8 * step else r for r in self.rings]
        return self._steps[step]

    @property
    def area(self) -> float:
        x0, y0, x1, y1 = self.bbox
        return (x1 - x0) * (y1 - y0)


@dataclass
class Feature:
    kind: str  # country | region | lake | river | place
    name: str
    country: str = ""  # a region's or a place's country
    iso: str = ""  # countries and regions: ISO A3 of the country
    parts: list[Part] = field(default_factory=list)
    point: tuple[float, float] | None = None  # places; the label point of the others
    rank: float = 0.0  # bigger wins a name clash (population, area)

    @property
    def is_line(self) -> bool:
        return self.kind == "river"

    def bbox(self, main_only: bool = True) -> tuple[float, float, float, float]:
        """The box around the feature. `main_only`: its biggest part and every part chained to it by gaps of at
        most NEAR degrees, so an archipelago (Indonesia, Japan) stays whole while far territories (French Guiana,
        Alaska, Hawaii, the Canaries, Svalbard) do not zoom the camera out to half the world."""
        if not self.parts:
            x, y = self.point or (0.0, 0.0)
            return (x, y, x, y)
        if not main_only or self.is_line:
            return _union([p.bbox for p in self.parts])
        rest = sorted((p.bbox for p in self.parts), key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))
        box = rest.pop(0)
        grew = True
        while grew:
            near = [b for b in rest if max(b[0] - box[2], box[0] - b[2], b[1] - box[3], box[1] - b[3]) <= NEAR]
            grew = bool(near)
            if grew:
                box = _union([box, *near])
                rest = [b for b in rest if b not in near]
        return box

    def label_point(self) -> tuple[float, float]:
        if self.point:
            return self.point
        biggest = max(self.parts, key=lambda p: p.area)
        ring = biggest.rings[0]
        return float(ring[:, 0].mean()), float(ring[:, 1].mean())


def norm(name: str) -> str:
    """Lowercase ASCII words: "Lake of the Woods" == "lake-of-the woods", "Québec" == "quebec"."""
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    return re.sub(r"^the ", "", text)


# --------------------------------------------------------------------------- download and cache


def download(progress=lambda m: None, timeout: float = 120) -> None:
    """Fetch the Natural Earth files that are not in assets/geodata/ yet."""
    import requests

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in FILES.values():
        path = DATA_DIR / f"{name}.geojson"
        if path.is_file() and path.stat().st_size > 10_000:
            continue
        progress(f"Downloading map data: {name}")
        last: Exception | None = None
        for url in URLS:
            try:
                r = requests.get(url.format(v=NE_VERSION, f=name), timeout=timeout)
                r.raise_for_status()
                if not r.content.lstrip().startswith(b"{"):
                    raise GeoError(f"not GeoJSON: {r.content[:60]!r}")
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(r.content)
                tmp.replace(path)
                break
            except Exception as err:  # noqa: BLE001 - try the next mirror
                last = err
        else:
            raise GeoError(f"Could not download {name}: {last}")


def _parts(geom: dict | None) -> list[Part]:
    if not geom:
        return []
    kind, coords = geom["type"], geom["coordinates"]
    polys = {"Polygon": [coords], "MultiPolygon": coords, "LineString": [[coords]],
             "MultiLineString": [[c] for c in coords]}.get(kind, [])
    out = []
    for poly in polys:
        rings = [np.asarray(r, dtype=np.float32)[:, :2] for r in poly if len(r) >= 2]
        if not rings:
            continue
        r0 = rings[0]
        out.append(Part(rings, (float(r0[:, 0].min()), float(r0[:, 1].min()), float(r0[:, 0].max()), float(r0[:, 1].max()))))
    return out


def _load_geojson(key: str) -> list[dict]:
    path = DATA_DIR / f"{FILES[key]}.geojson"
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def build_cache() -> dict[str, list[Feature]]:
    layers: dict[str, list[Feature]] = {k: [] for k in FILES}
    for f in _load_geojson("countries"):
        p = f["properties"]
        parts = _parts(f["geometry"])
        if parts:
            layers["countries"].append(Feature("country", p.get("NAME") or p.get("ADMIN", ""), iso=p.get("ADM0_A3", ""),
                                               parts=parts, rank=float(p.get("POP_EST") or 0),
                                               point=(float(p["LABEL_X"]), float(p["LABEL_Y"])) if p.get("LABEL_X") is not None else None))
    for f in _load_geojson("regions"):
        p = f["properties"]
        parts = _parts(f["geometry"])
        if parts and p.get("name"):
            layers["regions"].append(Feature("region", p["name"], country=p.get("admin", ""), iso=p.get("adm0_a3", ""), parts=parts,
                                             point=(float(p["longitude"]), float(p["latitude"])) if p.get("longitude") is not None else None))
    for f in _load_geojson("lakes"):
        p = f["properties"]
        parts = _parts(f["geometry"])
        if parts:
            layers["lakes"].append(Feature("lake", p.get("name") or "", parts=parts, rank=-float(p.get("scalerank") or 0)))
    rivers: dict[str, Feature] = {}
    for f in _load_geojson("rivers"):
        p = f["properties"]
        name = p.get("name") or ""
        parts = _parts(f["geometry"])
        if not parts or not name:
            continue
        rivers.setdefault(name, Feature("river", name, rank=-float(p.get("scalerank") or 0))).parts.extend(parts)
    layers["rivers"] = list(rivers.values())
    for f in _load_geojson("places"):
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        layers["places"].append(Feature("place", p.get("name") or p.get("nameascii", ""), country=p.get("adm0name", ""),
                                        point=(float(lon), float(lat)), rank=float(p.get("pop_max") or 0)))
    return layers


class World:
    """Every feature, and a name index over them."""

    def __init__(self, layers: dict[str, list[Feature]]):
        self.layers = layers
        self.index: dict[str, list[Feature]] = {}
        for kind in ("countries", "regions", "lakes", "rivers", "places"):
            for feat in layers.get(kind, []):
                names = {feat.name}
                if feat.country:
                    names.add(f"{feat.name}, {feat.country}")
                for n in names:
                    if norm(n):
                        self.index.setdefault(norm(n), []).append(feat)
        for country in layers.get("countries", []):  # common long and short forms
            for alias in COUNTRY_ALIASES.get(country.name, ()):
                self.index.setdefault(norm(alias), []).append(country)

    PRIORITY = {"country": 0, "region": 1, "lake": 2, "river": 3, "place": 4}

    def find(self, name: str, kinds: tuple[str, ...] | None = None) -> Feature | None:
        """The feature called `name` ("Minnesota", "Lake of the Woods", "Paris, France", "Spain"). A clash
        goes to countries, then states, lakes, rivers, places; the biggest wins within a kind. `kinds`
        limits the search ("place" for a pin)."""
        key = norm(name)
        if kinds is None and re.search(r"\briver\b", name or "", re.I):
            kinds = ("river",)
        elif kinds is None and re.search(r"\blake\b", name or "", re.I):
            kinds = ("lake",)
        bare = re.sub(r"^(lake|river|the)\s+|\s+(river|lake)$", "", (name or "").strip(), flags=re.I)
        found = self.index.get(key) or self.index.get(norm(bare))
        if not found and "," in (name or ""):
            found = self.index.get(norm(name.split(",")[0]))
        if not found:
            return None
        if kinds:
            found = [f for f in found if f.kind in kinds] or []
            if not found:
                return None
        return min(found, key=lambda f: (self.PRIORITY.get(f.kind, 9), -f.rank))


COUNTRY_ALIASES = {
    "United States of America": ("United States", "USA", "US", "America", "the United States"),
    "United Kingdom": ("UK", "Britain", "Great Britain"),
    "Russia": ("Russian Federation",),
    "Dem. Rep. Congo": ("Democratic Republic of the Congo", "DR Congo", "DRC"),
    "Congo": ("Republic of the Congo",),
    "Czechia": ("Czech Republic",),
    "Bosnia and Herz.": ("Bosnia and Herzegovina", "Bosnia"),
    "Central African Rep.": ("Central African Republic",),
    "Dominican Rep.": ("Dominican Republic",),
    "S. Sudan": ("South Sudan",),
    "Eq. Guinea": ("Equatorial Guinea",),
    "Gambia": ("The Gambia",),
    "Côte d'Ivoire": ("Ivory Coast",),
    "eSwatini": ("Eswatini", "Swaziland"),
    "Timor-Leste": ("East Timor",),
    "N. Cyprus": ("Northern Cyprus",),
    "W. Sahara": ("Western Sahara",),
    "Solomon Is.": ("Solomon Islands",),
    "Falkland Is.": ("Falkland Islands",),
    "Vatican": ("Vatican City", "Holy See"),
    "Macedonia": ("North Macedonia",),
    "North Macedonia": ("Macedonia",),
}

WIKI_POINTS = DATA_DIR / "wiki_points.json"
WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "TrendClip-BorderlineWhy/1.0 (https://www.youtube.com/@BorderlineWhy)"  # Wikimedia wants a way to reach us


def wiki_point(name: str, timeout: float = 15) -> Feature | None:
    """A place too small for Natural Earth (an enclave, a village, an island): its coordinates from its
    Wikipedia article, cached in assets/geodata/wiki_points.json. None when Wikipedia has none."""
    import requests

    cache: dict[str, list[float] | None] = {}
    if WIKI_POINTS.is_file():
        try:
            cache = json.loads(WIKI_POINTS.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    key = norm(name)
    if key not in cache:
        try:
            r = requests.get(WIKI_API, params={"action": "query", "prop": "coordinates", "titles": name, "redirects": 1,
                                               "format": "json", "formatversion": 2}, headers={"User-Agent": USER_AGENT},
                             timeout=timeout)
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", [])
            coords = next((p["coordinates"][0] for p in pages if p.get("coordinates")), None)
            cache[key] = [float(coords["lon"]), float(coords["lat"])] if coords else None
        except Exception as err:  # noqa: BLE001 - the API is down: the REST summary has the coordinates too
            logger.warning("Wikipedia coordinates for %r failed (%s); trying the REST API", name, err)
            try:
                from urllib.parse import quote

                r = requests.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(name.replace(' ', '_'), safe='')}",
                                 headers={"User-Agent": USER_AGENT}, timeout=timeout)
                if r.status_code == 404:
                    cache[key] = None
                else:
                    r.raise_for_status()
                    coords = r.json().get("coordinates")
                    cache[key] = [float(coords["lon"]), float(coords["lat"])] if coords else None
            except Exception as err2:  # noqa: BLE001 - not cached: try again next time
                logger.warning("Wikipedia REST coordinates for %r failed: %s", name, err2)
                return None
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        WIKI_POINTS.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")
    value = cache.get(key)
    return Feature("place", name.split(",")[0].strip(), point=(value[0], value[1])) if value else None


def resolve(name: str, kinds: tuple[str, ...] | None = None) -> Feature | None:
    """Natural Earth first; a point from Wikipedia for anything smaller (never an outline)."""
    found = world().find(name, kinds)
    if found is None and (kinds is None or "place" in kinds):
        found = wiki_point(name)
    return found


_world: World | None = None
_lock = threading.Lock()


def world(progress=lambda m: None) -> World:
    """The map data, loaded once (downloaded and cached on first use)."""
    global _world
    with _lock:
        if _world is not None:
            return _world
        layers = None
        if CACHE.is_file():
            try:
                with CACHE.open("rb") as fh:
                    version, layers = pickle.load(fh)
                if version != CACHE_VERSION:
                    layers = None
            except Exception:  # noqa: BLE001 - rebuild a broken cache
                layers = None
        if layers is None:
            download(progress)
            progress("Preparing the map data (once)")
            layers = build_cache()
            tmp = CACHE.with_suffix(".tmp")
            with tmp.open("wb") as fh:
                pickle.dump((CACHE_VERSION, layers), fh, protocol=pickle.HIGHEST_PROTOCOL)
            tmp.replace(CACHE)
        _world = World(layers)
        return _world


def set_world(value: World | None) -> None:
    """Tests: a small hand-made world instead of Natural Earth."""
    global _world
    with _lock:
        _world = value
