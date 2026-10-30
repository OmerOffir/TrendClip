"""Real pictures for Borderline Why's photo scenes (kind "photo"): the lead picture of an English Wikipedia article
("Benjamin Franklin", "Victoria Falls", "Mitchell Map"), else the best picture a Wikimedia Commons search finds.
Photos and paintings only (JPEG, PNG): an article whose lead picture is a flag, a logo or a map drawing (SVG) falls
back to the search. Downloaded once into assets/bw_fx/photos with the file's author and license, which go into the
video's description (most Commons pictures are CC BY / CC BY-SA and want the credit)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from modules import bw_geo

logger = logging.getLogger(__name__)

PHOTO_DIR = bw_geo.PROJECT_ROOT / "assets" / "bw_fx" / "photos"
WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIDTH = 1600
MIN_SIDE = 500  # smaller pictures look blurry on a 1080 frame
KINDS = ("image/jpeg", "image/png")


@dataclass
class Photo:
    path: Path
    file: str  # "File:Benjamin Franklin by Joseph Duplessis 1778.jpg"
    page: str  # its Commons page
    author: str = ""
    license: str = ""

    def credit(self) -> str:
        """One line for the description: the file, its author and license."""
        name = self.file.removeprefix("File:").rsplit(".", 1)[0]
        parts = [name] + ([f"by {self.author}"] if self.author else []) + ([f"({self.license})"] if self.license else [])
        return " ".join(parts) + f" - {self.page}"


def _get(url: str, params: dict) -> dict:
    r = requests.get(url, params={**params, "format": "json", "formatversion": 2}, timeout=30,
                     headers={"User-Agent": bw_geo.USER_AGENT})
    r.raise_for_status()
    return r.json()


def _plain(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html or "")
    return " ".join(text.split())[:80]


def _info(title: str) -> dict | None:
    """A Commons file's thumbnail URL, size, type, author and license (None when it's no picture we can use)."""
    data = _get(COMMONS_API, {"action": "query", "titles": title, "prop": "imageinfo",
                              "iiprop": "url|size|mime|extmetadata", "iiurlwidth": WIDTH})
    pages = data.get("query", {}).get("pages") or []
    if not pages or not pages[0].get("imageinfo"):
        return None
    ii = pages[0]["imageinfo"][0]
    if ii.get("mime") not in KINDS or min(ii.get("width", 0), ii.get("height", 0)) < MIN_SIDE:
        return None
    meta = ii.get("extmetadata") or {}
    return {"file": pages[0]["title"], "url": ii.get("thumburl") or ii["url"], "page": ii.get("descriptionurl", ""),
            "author": _plain((meta.get("Artist") or {}).get("value", "")),
            "license": _plain((meta.get("LicenseShortName") or {}).get("value", ""))}


def _candidates(query: str) -> list[str]:
    """File titles to try, best first: the article's lead picture, then a Commons search."""
    out: list[str] = []
    data = _get(WIKI_API, {"action": "query", "titles": query, "prop": "pageimages", "piprop": "name", "redirects": 1})
    for page in data.get("query", {}).get("pages") or []:
        if page.get("pageimage"):
            out.append("File:" + page["pageimage"])
    data = _get(COMMONS_API, {"action": "query", "list": "search", "srnamespace": 6, "srlimit": 10,
                              "srsearch": f"{query} filetype:bitmap"})
    out += [hit["title"] for hit in data.get("query", {}).get("search") or [] if names(query, hit["title"])]
    return list(dict.fromkeys(out))


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower().replace("'", "")))


def names(query: str, file: str) -> bool:
    """A search hit counts only when its file name holds the whole name searched for ("Elm Point" is not "Oak Point
    Elementary, Little Elm"); a "(Minnesota)" in the query is left out."""
    want = _words(re.sub(r"\(.*?\)", "", query))
    return bool(want) and f" {want} " in f" {_words(file)} "


def find(query: str) -> Photo | None:
    """The picture for `query`, downloaded (cached by query); None when nothing usable is found."""
    query = " ".join((query or "").split())
    if not query:
        return None
    key = hashlib.sha1(query.lower().encode()).hexdigest()[:16]
    meta_path = PHOTO_DIR / f"{key}.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not meta:
            return None
        photo = Photo(**{**meta, "path": Path(meta["path"])})
        if photo.path.is_file():
            return photo
    try:
        for title in _candidates(query):
            info = _info(title)
            if info is None:
                continue
            ext = ".png" if info["file"].lower().endswith(".png") else ".jpg"
            path = PHOTO_DIR / f"{key}{ext}"
            r = requests.get(info["url"], timeout=60, headers={"User-Agent": bw_geo.USER_AGENT})
            r.raise_for_status()
            PHOTO_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            photo = Photo(path, info["file"], info["page"], info["author"], info["license"])
            meta_path.write_text(json.dumps({**asdict(photo), "path": str(path)}), encoding="utf-8")
            return photo
    except requests.RequestException as err:
        logger.warning("No photo for %r: %s", query, err)
        return None  # not cached: try again next time
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    meta_path.write_text("{}", encoding="utf-8")
    return None


def credits(photos: list[Photo]) -> str:
    """The description's picture credits."""
    seen = list({p.file: p for p in photos}.values())
    return ("Pictures: Wikimedia Commons\n" + "\n".join(f"- {p.credit()}" for p in seen)) if seen else ""


# --------------------------------------------------------------------------- the photo scene


class PhotoScene:
    """A photo scene's frames: the picture blurred to fill the frame, and the picture itself as a taped print with a
    slow push-in (a pan along a long side), popping in at the scene's start, its caption written under it."""

    def __init__(self, scene, photo: Photo, k: int = 0):
        from PIL import Image, ImageFilter

        from modules import bw_render

        self.scene, self.photo, self.k = scene, photo, k
        W, H = bw_render.W, bw_render.H
        portrait = bw_render.LAYOUT == "portrait"
        self.img = Image.open(photo.path).convert("RGB")
        iw, ih = self.img.size
        s = max(W / iw, H / ih)
        cover = self.img.resize((int(iw * s) + 1, int(ih * s) + 1), Image.BILINEAR)
        cover = cover.crop(((cover.width - W) // 2, (cover.height - H) // 2, (cover.width - W) // 2 + W, (cover.height - H) // 2 + H))
        cover = cover.filter(ImageFilter.GaussianBlur(28))
        self.bg = Image.blend(cover, Image.new("RGB", (W, H), (10, 12, 18)), 0.5).convert("RGBA")
        box_w, box_h = (880, 760) if portrait else (1180, 620)
        self.center = (W * 0.55, 830) if portrait else (W * 0.55, 530)  # under the headline, right of the mascot
        f = min(box_w / iw, box_h / ih)
        self.size = (int(iw * f), int(ih * f))
        self.angle = (-2.2, 1.8, -1.4, 2.4)[k % 4]
        self.caption = str(scene.opts.get("caption") or "").strip()

    def frame(self, t: float):
        from PIL import Image, ImageDraw

        from modules import bw_brand, bw_fx, bw_render

        img = self.bg.copy()
        sc = self.scene
        u = max(0.0, min(1.0, (t - sc.start) / max(0.5, sc.end - sc.start)))
        zoom = 1.0 + 0.12 * bw_render.ease(u)
        iw, ih = self.img.size
        cw, ch = iw / zoom, ih / zoom
        dx = (iw - cw) * (0.5 + 0.35 * (u - 0.5) * (1 if self.k % 2 else -1))
        crop = self.img.crop((int(dx), int((ih - ch) * 0.4), int(dx + cw), int((ih - ch) * 0.4 + ch))).resize(self.size, Image.BILINEAR)
        border, strip = 18, (86 if self.caption else 18)
        card = Image.new("RGBA", (self.size[0] + 2 * border, self.size[1] + border + strip), (250, 248, 242, 255))
        card.paste(crop, (border, border))
        d = ImageDraw.Draw(card)
        if self.caption:
            f = bw_fx.hand_font(56)
            d.text((card.width / 2, self.size[1] + border + strip / 2), self.caption, font=f, fill=bw_brand.INK + (255,), anchor="mm")
        for x in (card.width * 0.12, card.width * 0.88):  # two strips of tape on the top corners
            tape = Image.new("RGBA", (150, 46), (236, 226, 190, 170))
            tape = tape.rotate(-28 if x < card.width / 2 else 28, expand=True, resample=Image.BICUBIC)
            bw_brand.paste(card, tape, int(x - tape.width / 2), int(-tape.height / 2 + 10))
        pop = bw_fx.backout((t - sc.start) / 0.45)
        k = 0.86 + 0.14 * pop
        card = card.rotate(self.angle * (2 - pop), expand=True, resample=Image.BICUBIC)
        if abs(k - 1) > 0.01:
            card = card.resize((max(1, int(card.width * k)), max(1, int(card.height * k))), Image.BILINEAR)
        x, y = int(self.center[0] - card.width / 2), int(self.center[1] - card.height / 2)
        bw_brand.paste(img, bw_brand.shadow(card, 16, 0.55), x - 32 + 10, y - 32 + 18)
        bw_brand.paste(img, card, x, y)
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
        if sc.headline:
            bw_render.headline(ov, sc.headline, bw_render.ramp(t, sc.start + 0.15), color=bw_render.FIXED["gold"])
        d = ImageDraw.Draw(ov)
        small = bw_render.font(26)
        d.text((bw_render.W - 24, bw_render.H - 22), "Photo: Wikimedia Commons", font=small, fill=(255, 255, 255, 150), anchor="rs")
        img.alpha_composite(ov)
        return img
