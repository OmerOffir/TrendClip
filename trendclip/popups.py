"""Pop-up images for Shorts: Gemini names 3-6 things the voiceover mentions; each becomes a transparent
sticker that pops up when its word is spoken.

Images come free from Wikimedia Commons (no key): first the matching emoji from the Microsoft Fluent
Emoji set (MIT), Noto Emoji or Twemoji, which are clean transparent stickers; otherwise a Commons
search for SVG/PNG files that really have a transparent background. Results are cached in
assets/popups/.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

import requests
from pydantic import BaseModel, Field

from .config import Settings

logger = logging.getLogger(__name__)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "TrendClip/1.0 (local YouTube Shorts maker; python-requests)"
THUMB_WIDTH = 640
STICKER_SIZE = 460  # longest side of the sticker on the 1080x1920 canvas
MIN_SECONDS, MAX_SECONDS = 1.5, 2.5
MIN_SPACING = 2.5  # seconds between two pop-ups starting; the target pace is one every 3-5 s
MAX_POPUPS = 15
# (file name pattern, credit) in order of preference; {} is the emoji's codepoints, e.g. 1f408-200d-2b1b.
EMOJI_SETS = [
    ("Fluent Emoji Color {}.svg", "Fluent Emoji by Microsoft (MIT)"),
    ("Noto Emoji v2.034 {}.svg", "Noto Emoji by Google (Apache 2.0)"),
    ("Twemoji12 {}.svg", "Twemoji by Twitter (CC BY 4.0)"),
]
# Commons files that are rarely a good "thing" sticker.
BAD_TITLE = re.compile(r"logo|map|diagram|chart|graph|structure|cutaway|pattern|flag of|coat of arms|"
                       r"blazon|signature|text|font|seal|icon set|sprite", re.IGNORECASE)


class Popup(BaseModel):
    """A pop-up image requested by Gemini (also sent back from the Create tab)."""

    word: str = Field(min_length=1, max_length=40,
                      description="The exact word from the script that triggers the pop-up (as written in the script).")
    emoji: str = Field("", max_length=16, description="One emoji that shows the thing, e.g. 🐈 or 🧺.")
    query: str = Field("", max_length=60, description="1-3 word English image search for the thing, e.g. 'laundry basket'.")


class PopupAsset(BaseModel):
    key: str
    filename: str
    source: str  # "emoji" or "commons"
    title: str
    page_url: str
    credit: str


@dataclass
class TimedPopup:
    popup: Popup
    start: float
    end: float


class PopupError(RuntimeError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _matches(spoken: str, trigger: str) -> bool:
    if not spoken or not trigger:
        return False
    if spoken == trigger:
        return True
    # cat/cats, basket/baskets, box/boxes; not "car" for "cardboard"
    longer, shorter = (spoken, trigger) if len(spoken) > len(trigger) else (trigger, spoken)
    return len(shorter) >= 3 and longer.startswith(shorter) and len(longer) - len(shorter) <= 2


def schedule(popups: list[Popup], words: list, total: float | None = None, *,
             min_seconds: float = MIN_SECONDS, max_seconds: float = MAX_SECONDS) -> list[TimedPopup]:
    """Place each pop-up on the first matching spoken word after the previous pop-up; drop pop-ups
    whose word is never spoken or that would crowd the previous one."""
    if not words:
        return []
    total = total if total is not None else words[-1].end + 0.3
    spoken = [_norm(w.text) for w in words]
    starts: list[tuple[Popup, float]] = []
    cursor = 0
    for popup in popups:
        tokens = [_norm(t) for t in popup.word.split() if _norm(t)]
        if not tokens:
            continue
        hit = next((i for i in range(cursor, len(words)) if _matches(spoken[i], tokens[0])), None)
        if hit is None:
            hit = next((i for i in range(cursor, len(words)) if any(_matches(spoken[i], t) for t in tokens)), None)
        if hit is None:
            continue
        start = max(words[hit].start - 0.05, 0.0)
        if starts and start < starts[-1][1] + max(min_seconds + 0.1, MIN_SPACING):
            continue
        starts.append((popup, start))
        cursor = hit + 1
    out = []
    for i, (popup, start) in enumerate(starts):
        limit = starts[i + 1][1] - 0.1 if i + 1 < len(starts) else total
        end = min(start + max_seconds, limit)
        if end - start >= min(min_seconds, 1.0):
            out.append(TimedPopup(popup, round(start, 3), round(end, 3)))
    return out


# --------------------------------------------------------------------------- Wikimedia Commons


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def emoji_codes(emoji: str) -> list[str]:
    """Commons file-name codepoints for an emoji: with and without the FE0F variation selector."""
    cps = [f"{ord(c):x}" for c in emoji.strip()]
    if not cps:
        return []
    full = "-".join(cps)
    bare = "-".join(c for c in cps if c != "fe0f")
    return list(dict.fromkeys([full, bare]))


def _imageinfo(session: requests.Session, params: dict, timeout: float) -> list[dict]:
    base = {"action": "query", "format": "json", "prop": "imageinfo",
            "iiprop": "url|size|mime", "iiurlwidth": THUMB_WIDTH}
    resp = session.get(COMMONS_API, params={**base, **params}, timeout=timeout)
    resp.raise_for_status()
    pages = (resp.json().get("query") or {}).get("pages") or {}
    ordered = sorted(pages.values(), key=lambda p: p.get("index", 0))
    return [{"title": p["title"], **p["imageinfo"][0]} for p in ordered if p.get("imageinfo")]


def _emoji_candidates(session: requests.Session, emoji: str, timeout: float) -> list[tuple[dict, str]]:
    titles = [f"File:{pattern.format(code)}" for pattern, _ in EMOJI_SETS for code in emoji_codes(emoji)]
    if not titles:
        return []
    found = {i["title"]: i for i in _imageinfo(session, {"titles": "|".join(titles)}, timeout)}
    out = []
    for pattern, credit in EMOJI_SETS:
        for code in emoji_codes(emoji):
            if info := found.get(f"File:{pattern.format(code)}"):
                out.append((info, credit))
                break
    return out


def _search_candidates(session: requests.Session, query: str, timeout: float) -> list[dict]:
    out = []
    for mime in ("image/svg+xml", "image/png"):
        out += _imageinfo(session, {"generator": "search", "gsrnamespace": 6, "gsrlimit": 12,
                                    "gsrsearch": f"{query} filemime:{mime}"}, timeout)
    return [c for c in out if not BAD_TITLE.search(c["title"])]


def transparency(img) -> tuple[int, float]:
    """(transparent corners 0-4, share of fully transparent pixels)."""
    alpha = img.convert("RGBA").getchannel("A")
    w, h = alpha.size
    corners = sum(alpha.getpixel(p) < 10 for p in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)])
    clear = sum(alpha.histogram()[:10]) / (w * h)
    return corners, clear


def _good_cutout(img) -> bool:
    corners, clear = transparency(img)
    ratio = img.width / max(img.height, 1)
    return corners >= 3 and 0.12 <= clear <= 0.9 and 0.4 <= ratio <= 2.5


def make_sticker(img, size: int = STICKER_SIZE):
    """Trim, scale to `size`, add a white sticker outline and a soft drop shadow (RGBA)."""
    from PIL import Image, ImageFilter

    img = img.convert("RGBA")
    if bbox := img.getchannel("A").getbbox():
        img = img.crop(bbox)
    scale = size / max(img.size)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    stroke, pad = 12, 40
    canvas = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (0, 0, 0, 0))
    canvas.paste(img, (pad, pad), img)
    alpha = canvas.getchannel("A").point(lambda a: 255 if a > 40 else 0)
    outline = alpha.filter(ImageFilter.MaxFilter(2 * stroke + 1)).filter(ImageFilter.GaussianBlur(1.2))
    shadow = outline.filter(ImageFilter.GaussianBlur(10)).point(lambda a: a * 0.45)
    out = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    out.paste((0, 0, 0, 255), (0, 10), shadow)
    out.paste((255, 255, 255, 255), (0, 0), outline)
    out.alpha_composite(canvas)
    return out


def _key(popup: Popup) -> str:
    if codes := emoji_codes(popup.emoji):
        return "emoji-" + codes[-1]
    return "q-" + (re.sub(r"[^a-z0-9]+", "-", (popup.query or popup.word).lower()).strip("-")[:50] or "x")


def fetch_asset(settings: Settings, popup: Popup, session: requests.Session | None = None) -> PopupAsset | None:
    """Sticker PNG for a pop-up (cached), or None when nothing suitable was found."""
    from PIL import Image

    out_dir = settings.assets_dir / "popups"
    key = _key(popup)
    sidecar = out_dir / f"{key}.json"
    if sidecar.is_file():
        try:
            asset = PopupAsset.model_validate_json(sidecar.read_text(encoding="utf-8"))
            if (out_dir / asset.filename).is_file():
                return asset
        except ValueError:
            pass

    session = session or _session()
    timeout = settings.request_timeout
    candidates: list[tuple[dict, str, str]] = []
    try:
        if popup.emoji:
            candidates += [(info, "emoji", credit) for info, credit in _emoji_candidates(session, popup.emoji, timeout)]
        if not candidates:
            for query in dict.fromkeys(q for q in (popup.query, popup.word) if q.strip()):
                candidates += [(info, "commons", "") for info in _search_candidates(session, query, timeout)]
                if candidates:
                    break
    except (requests.RequestException, ValueError) as err:
        logger.warning("Pop-up search failed for %r: %s", popup.word, err)
        return None

    for info, source, credit in candidates[:10]:
        try:
            resp = session.get(info.get("thumburl") or info["url"], timeout=timeout)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content))
            img.load()
        except (requests.RequestException, OSError) as err:
            logger.debug("Skipping %s: %s", info["title"], err)
            continue
        if source == "commons" and not _good_cutout(img):
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{key}.png"
        make_sticker(img).save(out_dir / filename)
        name = info["title"].removeprefix("File:")
        asset = PopupAsset(
            key=key, filename=filename, source=source, title=name,
            page_url=info.get("descriptionurl", ""),
            credit=credit or f"{name} - Wikimedia Commons {info.get('descriptionurl', '')}".strip(),
        )
        sidecar.write_text(asset.model_dump_json(indent=2), encoding="utf-8")
        return asset
    logger.info("No pop-up image found for %r", popup.word)
    return None


def clean_popups(popups: list[Popup], script: str, limit: int = MAX_POPUPS) -> list[Popup]:
    """Keep pop-ups whose trigger word is actually in the script, once each, in script order."""
    tokens = [_norm(t) for t in script.split()]
    seen, out = set(), []
    for p in popups:
        first = _norm(p.word.split()[0]) if p.word.split() else ""
        pos = next((i for i, t in enumerate(tokens) if _matches(t, first)), None)
        key = (first, p.emoji)
        if pos is None or key in seen:
            continue
        seen.add(key)
        out.append((pos, Popup(word=p.word.strip(), emoji=p.emoji.strip(), query=p.query.strip())))
    return [p for _, p in sorted(out, key=lambda x: x[0])][:limit]


def credits(assets: list[PopupAsset]) -> str:
    lines = list(dict.fromkeys(a.credit for a in assets if a.credit))
    return ("Pop-up images: " + "; ".join(lines)) if lines else ""
