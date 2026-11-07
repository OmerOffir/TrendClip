"""Borderline Why's stage scene (the owner's idea, 2026-11-07): for a few seconds the video leaves the map for an
old painting of that very moment (public domain, from Wikimedia Commons: the owner preferred it to a picture made
on the GPU), with the owner's characters standing in it big, their flags at the shoulder, saying their line.

A script scene {"kind": "stage", "painting": "Hannibal crossing the Alps", "elements": [one or two "local"s]};
find_painting picks the picture (bw_render.fetch_photos), Stage(...).frame(t) draws it, and the renderer puts the
year, the mascot and the captions over it like any scene. The camera only drifts slowly (the owner's calm camera).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageEnhance

from modules import bw_local, bw_photos, bw_render

logger = logging.getLogger(__name__)

SIZE = 400  # a character's height on the stage (on the map they are 250)
GROUND = 1240  # where they stand: under the middle, above the captions
SPOTS = {1: [(0.62, 460)], 2: [(0.5, 380), (0.8, 380)]}  # (x, size): right of the mascot's corner


@dataclass
class Actor:
    icon: str  # the owner's GIF or family ("general", "soldier_surprised")
    x: float  # 0..1 across the frame
    at: float = 0.3  # seconds into the stage when it pops in
    color: str = "a"  # a side family's coat
    flag: str = ""
    say: str = ""
    say_at: float = 1.2  # seconds into the stage when it speaks
    mood: str = ""
    flip: bool = False
    size: int = SIZE


class Stage:
    PAN_TOP, PAN_HEIGHT = 120, 0.74  # a wide painting: shown this tall from this y, panning slowly across

    def __init__(self, background: Image.Image, cast: list[Actor], start: float, end: float, drift: tuple[float, float] = (0.02, -0.01)):
        from PIL import ImageFilter

        W, H = bw_render.W, bw_render.H
        art = background.convert("RGB")
        a = np.asarray(art, dtype=np.float32) / 255
        if (mean := float(a.mean())) < 0.4:  # an old varnished painting is dark on a phone: lift its shadows
            art = Image.fromarray((np.clip(a ** (np.log(0.42) / np.log(max(mean, 0.05))), 0, 1) * 255).astype(np.uint8))
        art = ImageEnhance.Contrast(ImageEnhance.Color(art).enhance(1.08)).enhance(1.04)
        self.cast, self.start, self.end, self.drift = cast, start, end, drift
        # a wide painting cropped to the tall frame is a close-up of one corner (Poussin's elephant filled the screen):
        # show most of it, panning across, over a blurred copy of itself
        self.wide = art.width / art.height > W / H * 1.25
        if self.wide:
            ph = int(H * self.PAN_HEIGHT)
            self.pan = art.resize((max(W, int(art.width * ph / art.height)), ph), Image.LANCZOS)
            k = max(W / art.width, H / art.height)
            cover = art.resize((int(art.width * k) + 1, int(art.height * k) + 1), Image.BILINEAR)
            cover = cover.crop(((cover.width - W) // 2, (cover.height - H) // 2, (cover.width - W) // 2 + W, (cover.height - H) // 2 + H))
            cover = Image.blend(cover.filter(ImageFilter.GaussianBlur(26)), Image.new("RGB", (W, H), (10, 12, 18)), 0.45)
            self.bg = self._shade(cover).convert("RGBA")
            mask = np.ones((ph, 1), dtype=np.float32)
            fade = 150
            mask[:60, 0] = np.linspace(0, 1, 60)
            mask[-fade:, 0] = np.linspace(1, 0, fade)  # the painting melts into the blur under it
            self.mask = Image.fromarray((np.repeat(mask, W, axis=1) * 255).astype(np.uint8))
        else:
            k = max(W / art.width, H / art.height) * 1.12  # room to drift
            self.bg = self._shade(art.resize((int(art.width * k + 0.5), int(art.height * k + 0.5)), Image.LANCZOS)).convert("RGBA")

    @staticmethod
    def _shade(img: Image.Image) -> Image.Image:
        """Darker toward the bottom, so the captions read."""
        a = np.asarray(img, dtype=np.float32)
        h = a.shape[0]
        fall = np.clip((np.arange(h) / h - 0.62) / 0.38, 0, 1)[:, None, None]
        a *= 1 - 0.45 * fall ** 1.5
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

    def frame(self, t: float) -> Image.Image:
        W, H = bw_render.W, bw_render.H
        u = min(1.0, max(0.0, (t - self.start) / max(1.0, self.end - self.start)))
        u = u * u * (3 - 2 * u)
        if self.wide:  # a slow pan across the painting, a third of its width at most (the calm camera)
            img = self.bg.copy()
            slack = self.pan.width - W
            x = int(slack * (0.5 + min(0.33, 0.33 * slack / max(1, self.pan.width)) * (u - 0.5) * 2))
            x = min(max(0, x), slack)
            img.paste(self.pan.crop((x, 0, x + W, self.pan.height)), (0, self.PAN_TOP), self.mask)
        else:
            zoom = 1.0 + 0.04 * u  # a slow push in, nothing quick
            cw, ch = W / zoom, H / zoom
            cx = self.bg.width / 2 + self.drift[0] * self.bg.width * (u - 0.5)
            cy = self.bg.height / 2 + self.drift[1] * self.bg.height * (u - 0.5)
            img = self.bg.resize((W, H), Image.BILINEAR, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bubbles = []
        for a in sorted(self.cast, key=lambda a: a.x):
            at = self.start + a.at
            code = bw_local.character(a.icon, a.color)
            say_at = self.start + a.say_at
            tall = round(a.size * 1.3) if code.startswith("@") else a.size
            xy = (a.x * W, min(GROUND, bw_render.CAPTION_Y - 90 - tall * 0.5))  # feet above the captions: words touch nothing
            bw_local.draw(ov, code, xy, a.size, t, at, a.mood, say_at if a.mood else None, flip=a.flip)
            if a.flag:
                bw_local.badge(ov, a.flag, xy, tall, t, at)
            if a.say:
                bubbles.append((a.say, (xy[0], xy[1] - tall * 0.5 + 6), t, say_at, -1 if a.x > 0.5 else 1))
        bubbles.sort(key=lambda b: b[3])
        for k, args in enumerate(bubbles):  # over every character; a line stays until the next one is said
            if k + 1 < len(bubbles) and t >= bubbles[k + 1][3]:
                continue
            bw_local.say(ov, *args)
        img.alpha_composite(ov)
        return img

    @classmethod
    def from_scene(cls, scene, painting: Path, react_time) -> "Stage":
        """The stage for a script scene: its painting, and its one or two locals standing in it (react_time(el): when
        the local says its line, bw_fx.FxRenderer.react_time)."""
        locals_ = [el for el in scene.elements if el.kind == "local"][:2]
        spots = SPOTS[len(locals_)] if locals_ else ()
        cast = []
        for el, (x, size) in zip(locals_, spots):
            o = el.opts
            cast.append(Actor(str(o.get("icon") or ""), x, at=max(0.15, el.at - scene.start), color=el.color or "a",
                              flag=str(o.get("flag") or ""), say=str(o.get("say") or ""), say_at=max(0.5, react_time(el) - scene.start),
                              mood=str(o.get("mood") or ""), flip=bool(o.get("flip")), size=size))
        return cls(Image.open(painting), cast, scene.start, scene.end)


# --------------------------------------------------------------------------- the painting

OLDEST_PRINT = 1930  # a painting, engraving or print made before this; newer pictures are photos, maps or posters
MIN_SIDE = 900
NOT_ART = re.compile(r"\b(map|maps|karte|carte|mapa|boundary|border|diagram|plan|location|relief|attack|deployment|"
                     r"flag|coat of arms|logo|stamp|coin|bust|statue|monument|photo|photograph|comic|caricature|cartoon|satire|punch)\b", re.I)
ART = re.compile(r"painting|engraving|lithograph|illustration|print|drawing|miniature|fresco|woodcut|etching|artwork|"
                 r"history paint|oil on canvas|art", re.I)
DOCUMENT = re.compile(r"document|manuscript|letter|text|signature|handwrit|charter|treaty text|newspaper|book page", re.I)
STOP = {"the", "of", "a", "an", "and", "in", "on", "at", "to", "by", "with", "from", "his", "her", "their", "painting"}


def _year(text: str) -> int | None:
    m = re.search(r"\b(1[0-9]{3}|[5-9][0-9]{2})\b", text or "")
    return int(m.group(1)) if m else None


def _keys(text: str) -> set[str]:
    """The words that matter, plural or possessive s dropped ("Hannibals" is Hannibal)."""
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in re.findall(r"[a-z]+", bw_photos._words(text))
            if w not in STOP and len(w) > 2}


def _pick(query: str, infos: list[dict]) -> dict | None:
    """The best painting among the files' imageinfo: an old work of art (no map, coin or photo), free to use, big
    enough, naming what was asked for; a tall picture fills the portrait frame best."""
    want = _keys(query)
    best, best_score = None, 0.0
    for info in infos:
        title, meta = info["title"], info.get("extmetadata") or {}
        made = _year(bw_photos._plain((meta.get("DateTimeOriginal") or {}).get("value", "")))
        licence = bw_photos._plain((meta.get("LicenseShortName") or {}).get("value", ""))
        if (info.get("mime") not in bw_photos.KINDS or min(info.get("width", 0), info.get("height", 0)) < MIN_SIDE
                or not bw_photos.FREE.match(licence) or NOT_ART.search(title.replace("_", " "))
                or made is None or made >= OLDEST_PRINT):
            continue
        cats = bw_photos._plain((meta.get("Categories") or {}).get("value", "")) + " " + \
            " ".join(re.sub("<[^>]+>", " ", (meta.get("Categories") or {}).get("value", "")).split("|"))
        if DOCUMENT.search(cats) or not ART.search(cats + " " + title):  # a work of art, not a page of text
            continue
        said = " ".join(re.sub("<[^>]+>", " ", (meta.get(k) or {}).get("value", "")) for k in ("ObjectName", "ImageDescription"))
        if NOT_ART.search(said[:300]) and re.search(r"comic|caricature|cartoon", said, re.I):
            continue
        hits = len(want & _keys(title + " " + said[:600]))
        if not want or hits * 2 < len(want):  # at least half of what was asked for
            continue
        painted = bool(re.search(r"painting|oil on|oils|watercolou?r|gouache|fresco|chromolitho|colou?r", cats + " " + said, re.I))
        score = (hits / len(want) + 0.3 * min(1.0, info["height"] / info["width"]) + 0.1 * ("cropped" in title.lower())
                 + 0.4 * painted)  # a painting in colour over a black-and-white engraving (the owner liked the colours)
        if score > best_score:
            best, best_score = info, score
    return best


def _infos(titles: list[str]) -> list[dict]:
    out = []
    for k in range(0, len(titles), 40):
        data = bw_photos._get(bw_photos.COMMONS_API, {"action": "query", "titles": "|".join(titles[k:k + 40]), "prop": "imageinfo",
                                                      "iiprop": "url|size|mime|extmetadata", "iiurlwidth": bw_photos.WIDTH})
        for page in data.get("query", {}).get("pages") or []:
            if page.get("imageinfo"):
                out.append({"title": page["title"], **page["imageinfo"][0]})
    return out


def _titles(query: str) -> list[str]:
    """Files to weigh: a Commons search for the moment, then the pictures of the Wikipedia article about it."""
    data = bw_photos._get(bw_photos.COMMONS_API, {"action": "query", "list": "search", "srnamespace": 6, "srlimit": 20,
                                                  "srsearch": f"{query} filetype:bitmap"})
    out = [h["title"] for h in data.get("query", {}).get("search") or []]
    data = bw_photos._get(bw_photos.WIKI_API, {"action": "query", "list": "search", "srsearch": query, "srlimit": 1})
    if hits := data.get("query", {}).get("search"):
        data = bw_photos._get(bw_photos.WIKI_API, {"action": "query", "titles": hits[0]["title"], "prop": "images", "imlimit": 50})
        out += [i["title"] for p in data.get("query", {}).get("pages") or [] for i in p.get("images") or []
                if i["title"].lower().endswith((".jpg", ".jpeg", ".png"))]
    return list(dict.fromkeys(out))


def find_painting(query: str) -> bw_photos.Photo | None:
    """An old painting of `query` ("Hannibal crossing the Alps"), downloaded and cached like the photos; None when
    Commons has none (the scene then shows its place on the map)."""
    query = " ".join((query or "").split())
    if not query:
        return None
    key = "painting-" + hashlib.sha1(query.lower().encode()).hexdigest()[:16]
    meta_path = bw_photos.PHOTO_DIR / f"{key}.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not meta:
            return None
        photo = bw_photos.Photo(**{**meta, "path": Path(meta["path"])})
        if photo.path.is_file():
            return photo
    try:
        info = _pick(query, _infos(_titles(query)))
        if info is not None:
            meta = info.get("extmetadata") or {}
            ext = ".png" if info["title"].lower().endswith(".png") else ".jpg"
            path = bw_photos.PHOTO_DIR / f"{key}{ext}"
            r = requests.get(info.get("thumburl") or info["url"], timeout=60, headers={"User-Agent": bw_photos.bw_geo.USER_AGENT})
            r.raise_for_status()
            bw_photos.PHOTO_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            photo = bw_photos.Photo(path, info["title"], info.get("descriptionurl", ""),
                                    bw_photos._plain((meta.get("Artist") or {}).get("value", "")),
                                    bw_photos._plain((meta.get("LicenseShortName") or {}).get("value", "")))
            meta_path.write_text(json.dumps({**asdict(photo), "path": str(path)}), encoding="utf-8")
            return photo
    except requests.RequestException as err:
        logger.warning("No painting for %r: %s", query, err)
        return None  # not cached: try again next time
    bw_photos.PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    meta_path.write_text("{}", encoding="utf-8")
    return None
