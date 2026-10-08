"""Your own sticker library (the `stickers/` folder): reaction memes, subscribe / Part 2 graphics,
arrows, sound-effect words. PNG, GIF (animated too), WebP and JPG.

Scanning: every file is inspected (size, animation, transparency) and tagged with a category and
moods. Gemini looks at new files once (the tags are cached in assets/cache/stickers.json); without
Gemini the file name is used ("oh_no.png" -> shocked). Fix or add tags by hand in
`stickers/stickers.json`, e.g. {"image.png": {"category": "reaction", "moods": ["awkward"]}}, or
hide a file with {"category": "off"}.

Placement: reaction stickers pop up at the funny / awkward / shocking beats of the voiceover (from
Gemini's `reactions`, else a keyword scan of the spoken words), a subscribe / Part 2 sticker pops up
when the call to action starts, and every sticker goes into a free screen slot that does not cover
the captions, the title / end banner, the pop-up images or the YouTube buttons.
"""

from __future__ import annotations

import bisect
import io
import json
import logging
import random
import re
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .video_assembler import WIDTH, Overlay

logger = logging.getLogger(__name__)

EXTENSIONS = {".png", ".gif", ".webp", ".jpg", ".jpeg", ".apng"}
OVERRIDES_FILE = "stickers.json"
Category = Literal["reaction", "cta", "arrow", "sfx", "other"]
MOODS = {
    "funny": "something hilarious or absurd",
    "awkward": "an awkward, cringe or uncomfortable moment",
    "shocked": "a shocking reveal, 'oh no', panic",
    "approve": "yes, a good idea, approval",
    "reject": "no, a bad idea, disapproval",
    "proud": "a win, cheers, being smug",
    "pain": "hiding the pain, fake smile while things go wrong",
    "nope": "leaving, getting out, 'I'm done'",
    "innocent": "'who, me?', being blamed, feigned innocence",
    "crazy": "chaos, unhinged, losing it",
    "embarrassed": "facepalm, embarrassment, regret",
    "suspicious": "side-eye, something is off, a sly look",
}
MOOD_LIST = ", ".join(MOODS)
RELATED = {
    "funny": ["crazy", "proud", "suspicious", "awkward"],
    "awkward": ["pain", "embarrassed", "suspicious", "innocent"],
    "shocked": ["crazy", "awkward", "embarrassed"],
    "approve": ["proud", "funny"],
    "reject": ["nope", "awkward"],
    "proud": ["approve", "funny"],
    "pain": ["awkward", "embarrassed"],
    "nope": ["reject", "shocked"],
    "innocent": ["suspicious", "awkward"],
    "crazy": ["shocked", "funny"],
    "embarrassed": ["awkward", "pain"],
    "suspicious": ["innocent", "awkward"],
}
CTA_KINDS = ("subscribe", "part2", "follow", "comment")
# Spoken-word cues for scripts without Gemini reactions (matched on lowercase words).
LEXICON = {
    "shocked": r"\boh no\b|\bsuddenly\b|\brealized\b|\bturned around\b|\bfroze\b|\bscreamed\b|\bgasped\b|"
               r"\bheart (just )?dropped\b|\bcouldn'?t believe\b|\bshock",
    "awkward": r"\bawkward|\bcringe|\bsilence\b|\bstared\b|\bstaring\b|\bweird\b|\buncomfortable",
    "funny": r"\blaugh|\bhilarious|\bjoke\b|\bfunny\b",
    "embarrassed": r"\bembarrass|\bfacepalm|\bregret|\bwhy did i\b",
    "nope": r"\bran away\b|\bran out\b|\bget out\b|\bnope\b|\bi'?m done\b|\bescape",
    "innocent": r"\bwho,? me\b|\bnot me\b|\bblamed?\b",
    "crazy": r"\bcrazy\b|\binsane\b|\bchaos\b|\bpanic",
    "proud": r"\bnailed it\b|\bgenius\b|\bproud\b|\bcheers\b",
    "pain": r"\bsmiled through\b|\bfake smile\b|\bit'?s fine\b",
    "reject": r"\bno way\b|\bnever again\b|\bterrible idea\b|\bbad idea\b",
    "approve": r"\bgood idea\b|\bperfect\b",
    "suspicious": r"\bsuspicious|\bsmirk|\bside-?eye|\bsomething was off\b",
}
NAME_HINTS = [  # file-name fallback when Gemini can't tag
    (r"oh.?no|omg|shock|wow|surpris", "reaction", ["shocked"], ""),
    (r"crazy|insane|chaos", "reaction", ["crazy"], ""),
    (r"go.?out|exit|leave|bye|nope", "reaction", ["nope"], ""),
    (r"me\?|who.?me|innocent", "reaction", ["innocent"], ""),
    (r"awk|cringe", "reaction", ["awkward"], ""),
    (r"lol|laugh|lmao|funny", "reaction", ["funny"], ""),
    (r"facepalm|embarr", "reaction", ["embarrassed"], ""),
    (r"sub|bell", "cta", [], "subscribe"),
    (r"part.?2|part.?two", "cta", [], "part2"),
    (r"follow", "cta", [], "follow"),
    (r"arrow|point", "arrow", [], ""),
    (r"boom|pow|bam|sfx", "sfx", [], ""),
]

# Screen layout on the 1080x1920 canvas (x0, y0, x1, y1).
CAPTIONS = (40, 1040, 1040, 1310)
TOP_BANNER = (60, 170, 1020, 560)
SHORTS_UI = [(940, 1000, 1080, 1700), (0, 1660, 1080, 1920)]  # like/comment buttons, title/description
REACTION_BOX = 300
SLOTS = {  # centres of the reaction sticker positions
    "bottom-left": (215, 1480),
    "top-right": (865, 600),
    "center-left": (205, 800),
    "center-right": (875, 800),
}
CTA_CENTER = (WIDTH // 2, 1490)
REACTION_SECONDS = 1.9
ANIMATED_MAX_SECONDS = 3.2  # an animated reaction stays until its loop ends, at most this long
SILENT_CTA_SECONDS = 3.0  # without a spoken "subscribe…" line, the CTA sticker covers the last 3 s
MIN_GAP = 3.0
PACE = (3.0, 5.0)  # something new pops up every 3-5 s: reaction stickers fill longer gaps between pop-ups
FILLER_MOODS = ("funny", "suspicious", "shocked", "awkward", "crazy", "innocent")


class StickerInfo(BaseModel):
    filename: str
    category: str = "reaction"  # reaction | cta | arrow | sfx | other | off
    moods: list[str] = Field(default_factory=list)
    cta: str = ""
    description: str = ""
    width: int = 0
    height: int = 0
    frames: int = 1
    loop_seconds: float = 0.0  # one pass of the animation (GIF / WebP)
    transparent: bool = False
    source: str = "filename"  # filename | gemini | manual
    signature: str = ""

    @property
    def animated(self) -> bool:
        return self.frames > 1


class Reaction(BaseModel):
    """A reaction beat in the script (Gemini fills these in; the Create tab sends them back)."""

    word: str = Field(min_length=1, max_length=40,
                      description="The exact word from the script where the funny/awkward/shocking beat lands.")
    mood: str = Field("funny", description=f"One of: {MOOD_LIST}.")

    @field_validator("mood", mode="before")
    @classmethod
    def _known(cls, value: object) -> str:
        mood = str(value or "").strip().lower()
        return mood if mood in MOODS else "funny"


class StickerTag(BaseModel):
    index: int
    category: Category = Field(description=(
        "reaction = a meme / emoji / face reacting to something; cta = subscribe, follow, like or "
        "'part 2' graphic; arrow = arrow or pointer; sfx = a sound-effect word like BOOM; other."))
    moods: list[str] = Field(default_factory=list, description=f"For reactions, 1-3 of: {MOOD_LIST}.")
    cta: str = Field("", description="For cta: subscribe, part2, follow or comment. Otherwise empty.")
    description: str = Field(description="At most 12 words: who/what it shows and the expression.")


class StickerTags(BaseModel):
    stickers: list[StickerTag]


# --------------------------------------------------------------------------- scanning


_lock = threading.Lock()
_memory: dict[str, list[StickerInfo]] = {}
_overrides_seen: dict[str, str] = {}  # stickers.json signature at the last scan
_gemini_failed_at: dict[str, float] = {}
GEMINI_RETRY_SECONDS = 600


def _cache_file(settings: Settings) -> Path:
    return settings.assets_dir / "cache" / "stickers.json"


def _signature(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}-{int(st.st_mtime)}"


def sticker_files(settings: Settings) -> list[Path]:
    folder = settings.stickers_dir
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in EXTENSIONS and not p.name.startswith("."))


def has_transparency(img) -> bool:
    """True for real cut-outs (transparent corners), not for photos saved as RGBA."""
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    w, h = alpha.size
    corners = sum(alpha.getpixel(p) < 20 for p in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)])
    return corners >= 3


def inspect(path: Path) -> StickerInfo:
    from PIL import Image

    with Image.open(path) as img:
        frames = getattr(img, "n_frames", 1)
        loop = 0.0
        if frames > 1:
            for i in range(frames):
                img.seek(i)
                loop += max(img.info.get("duration", 100), 20) / 1000
            img.seek(0)
        info = StickerInfo(filename=path.name, width=img.width, height=img.height, frames=frames,
                           loop_seconds=round(loop, 2), transparent=has_transparency(img), signature=_signature(path))
    return guess(info)


def guess(info: StickerInfo) -> StickerInfo:
    name = Path(info.filename).stem.lower()
    for pattern, category, moods, cta in NAME_HINTS:
        if re.search(pattern, name):
            return info.model_copy(update={"category": category, "moods": moods, "cta": cta, "source": "filename"})
    return info.model_copy(update={"category": "reaction", "moods": ["funny"], "source": "filename"})


def _thumb_png(path: Path, size: int = 256) -> bytes:
    """A PNG for Gemini: the image, or for animations a strip of 4 frames from start to end, so the
    expression the GIF builds up to is judged, not just its first frame."""
    from PIL import Image

    with Image.open(path) as img:
        count = getattr(img, "n_frames", 1)
        picks = sorted({round(i * (count - 1) / 3) for i in range(4)}) if count > 1 else [0]
        frames = []
        for i in picks:
            img.seek(i)
            frame = img.convert("RGBA")
            frame.thumbnail((size, size))
            frames.append(frame)
    gap = 8
    sheet = Image.new("RGBA", (sum(f.width for f in frames) + gap * (len(frames) - 1), max(f.height for f in frames)),
                      (255, 255, 255, 255))
    x = 0
    for frame in frames:
        sheet.alpha_composite(frame, (x, 0))
        x += frame.width + gap
    buf = io.BytesIO()
    sheet.convert("RGB").save(buf, "PNG")
    return buf.getvalue()


TAG_PROMPT = """You label sticker images for a vertical-video editor. Each image is a sticker that pops up
over gameplay while a funny story is narrated. For every image (by index) give its category, the moods
it expresses (reactions only), the CTA kind (cta only) and a short description. Judge from the picture;
the file names are often meaningless. Animated GIFs come as a strip of frames from start to end (left to
right): tag the reaction the animation shows as a whole and say what happens in the description."""


def tag_with_gemini(settings: Settings, infos: list[StickerInfo], client=None) -> list[StickerInfo]:
    """One Gemini call (images + names) returns category / moods / description for each sticker."""
    from . import script_writer

    api_key = script_writer.gemini_api_key(settings)
    if client is None and not api_key:
        return infos
    from google import genai
    from google.genai import errors, types

    client = client or genai.Client(api_key=api_key)
    contents: list = [TAG_PROMPT]
    for i, info in enumerate(infos):
        contents.append(f"Image {i}: file name '{info.filename}'")
        contents.append(types.Part.from_bytes(data=_thumb_png(settings.stickers_dir / info.filename),
                                              mime_type="image/png"))
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=StickerTags,
                                         temperature=0.2)
    try:
        response, _ = script_writer._generate_with_fallback(
            client, script_writer.gemini_model(settings), contents, config, lambda f, m: None, errors.APIError)
    except errors.APIError as err:
        raise script_writer._explain(err, script_writer.gemini_model(settings)) from err
    result = response.parsed if isinstance(response.parsed, StickerTags) else StickerTags.model_validate_json(response.text)
    out = list(infos)
    for tag in result.stickers:
        if not 0 <= tag.index < len(out):
            continue
        moods = [m for m in (x.strip().lower() for x in tag.moods) if m in MOODS][:3]
        cta = tag.cta.strip().lower() if tag.cta.strip().lower() in CTA_KINDS else ""
        if tag.category == "reaction" and not moods:
            moods = ["funny"]
        out[tag.index] = out[tag.index].model_copy(update={
            "category": tag.category, "moods": moods, "cta": cta if tag.category == "cta" else "",
            "description": tag.description.strip()[:120], "source": "gemini"})
    return out


def _overrides(settings: Settings) -> dict[str, dict]:
    path = settings.stickers_dir / OVERRIDES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as err:
        logger.warning("Ignoring %s: %s", path, err)
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def scan(settings: Settings, *, use_gemini: bool = True, client=None) -> list[StickerInfo]:
    """Inspect the stickers folder; only new or changed files are inspected / tagged again."""
    with _lock:
        cache_path = _cache_file(settings)
        try:
            cached = {k: StickerInfo.model_validate(v)
                      for k, v in json.loads(cache_path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            cached = {}
        infos: list[StickerInfo] = []
        for path in sticker_files(settings):
            old = cached.get(path.name)
            try:
                infos.append(old if old and old.signature == _signature(path) else inspect(path))
            except OSError as err:
                logger.warning("Skipping sticker %s: %s", path.name, err)

        untagged = [i for i, s in enumerate(infos) if s.source == "filename"]
        key = str(settings.stickers_dir)
        recently_failed = time.monotonic() - _gemini_failed_at.get(key, -1e9) < GEMINI_RETRY_SECONDS
        if use_gemini and untagged and (client is not None or not recently_failed):
            try:
                for chunk in range(0, len(untagged), 16):
                    idx = untagged[chunk:chunk + 16]
                    tagged = tag_with_gemini(settings, [infos[i] for i in idx], client=client)
                    for i, info in zip(idx, tagged):
                        infos[i] = info
                logger.info("Tagged %d stickers with Gemini", len(untagged))
            except Exception as err:  # noqa: BLE001 - keep file-name tags, retry later
                _gemini_failed_at[key] = time.monotonic()
                logger.warning("Could not tag stickers with Gemini (%s); using file names", err)

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({s.filename: s.model_dump() for s in infos}, indent=2), encoding="utf-8")

        manual = _overrides(settings)
        final = []
        for info in infos:
            if over := manual.get(info.filename):
                allowed = {k: v for k, v in over.items() if k in ("category", "moods", "cta", "description")}
                info = info.model_copy(update={**allowed, "source": "manual"})
            final.append(info)
        _memory[key] = final
        return final


def library(settings: Settings) -> list[StickerInfo]:
    """The tagged library; rescans when files were added, changed or removed, or stickers.json was edited."""
    key = str(settings.stickers_dir)
    current = _memory.get(key)
    files = {p.name: _signature(p) for p in sticker_files(settings)}
    overrides = settings.stickers_dir / OVERRIDES_FILE
    tags_sig = _signature(overrides) if overrides.is_file() else ""
    if current is not None and {s.filename: s.signature for s in current} == files \
            and _overrides_seen.get(key) == tags_sig:
        return current
    _overrides_seen[key] = tags_sig
    return scan(settings)


def summary(stickers: list[StickerInfo]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in stickers:
        out[s.category] = out.get(s.category, 0) + 1
    return out


# --------------------------------------------------------------------------- styling


def make_card(img, size: int = REACTION_BOX):
    """Photo / meme without transparency: rounded card with a white border and a soft shadow."""
    from PIL import Image, ImageDraw, ImageFilter

    img = img.convert("RGBA")
    border, pad, radius = 10, 30, 28
    inner = size - 2 * border
    scale = inner / max(img.size)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    w, h = img.width + 2 * border, img.height + 2 * border
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius - border // 2, fill=255)
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    shape = Image.new("L", out.size, 0)
    ImageDraw.Draw(shape).rounded_rectangle((pad, pad, pad + w - 1, pad + h - 1), radius, fill=255)
    shadow = shape.filter(ImageFilter.GaussianBlur(12)).point(lambda a: a * 0.5)
    out.paste((0, 0, 0, 255), (0, 10), shadow)
    out.paste((255, 255, 255, 255), (0, 0), shape)
    out.paste(img, (pad + border, pad + border), mask)
    return out


def style(img, info: StickerInfo, size: int):
    from .popups import make_sticker

    return make_sticker(img, size=size) if info.transparent else make_card(img, size=size)


def builtin_cta(settings: Settings, kind: str) -> Path:
    """Generated red 'SUBSCRIBE' / 'SUB FOR PART 2' button (cached in assets/stickers/)."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    from .video_assembler import find_font_file

    text = {"part2": "SUB FOR PART 2", "follow": "FOLLOW", "comment": "COMMENT"}.get(kind, "SUBSCRIBE")
    path = settings.assets_dir / "stickers" / f"cta_{re.sub(r'[^a-z0-9]+', '-', text.lower())}.png"
    if path.is_file():
        return path
    _, font_file = find_font_file("Impact")
    font = ImageFont.truetype(str(font_file), 88) if font_file else ImageFont.load_default(size=88)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    x0, y0, x1, y1 = probe.textbbox((0, 0), text, font=font)
    tw, th = x1 - x0, y1 - y0
    icon, gap, padx, pady, stroke, pad = 92, 26, 44, 34, 8, 30
    w, h = padx * 2 + icon + gap + tw, max(th, icon) + pady * 2
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    shape = Image.new("L", out.size, 0)
    ImageDraw.Draw(shape).rounded_rectangle((pad, pad, pad + w - 1, pad + h - 1), h // 2, fill=255)
    shadow = shape.filter(ImageFilter.GaussianBlur(12)).point(lambda a: a * 0.5)
    out.paste((0, 0, 0, 255), (0, 10), shadow)
    out.paste((255, 255, 255, 255), (0, 0), shape)
    d = ImageDraw.Draw(out)
    d.rounded_rectangle((pad + stroke, pad + stroke, pad + w - 1 - stroke, pad + h - 1 - stroke),
                        h // 2 - stroke, fill=(255, 0, 51, 255))
    ix, iy = pad + padx, pad + (h - icon) // 2  # white "play" tile like the YouTube logo
    d.rounded_rectangle((ix, iy + 10, ix + icon, iy + icon - 10), 22, fill=(255, 255, 255, 255))
    cx, cy = ix + icon // 2 + 4, iy + icon // 2
    d.polygon([(cx - 15, cy - 20), (cx - 15, cy + 20), (cx + 20, cy)], fill=(255, 0, 51, 255))
    d.text((ix + icon + gap - x0, pad + (h - th) // 2 - y0), text, font=font, fill=(255, 255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path)
    return path


# --------------------------------------------------------------------------- planning

Rect = tuple[int, int, int, int]


def _rect(center: tuple[int, int], w: int, h: int) -> Rect:
    return (center[0] - w // 2, center[1] - h // 2, center[0] + w // 2, center[1] + h // 2)


def _hits(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _clean(text: str) -> str:
    return re.sub(r"[^\w' ]", "", text.lower()).strip()


def find_cta_start(words: list) -> int | None:
    """Index of the first word of the closing call-to-action sentence ('Sub to…', 'Hit that subscribe…',
    'Follow…'): the last sentence that says sub / subscribe / follow, within the last 22 words."""
    tail = max(0, len(words) - 22)
    for i in range(len(words) - 1, tail - 1, -1):
        if _clean(words[i].text) in ("sub", "subscribe", "follow"):
            start = sentence_start(words, i)
            return start if i - start <= 4 else i  # "Hit that subscribe…", not a whole unpunctuated script
    return None


def sentence_start(words: list, index: int) -> int:
    i = index
    while i > 0 and not re.search(r"[.!?]['\"]?$", words[i - 1].text):
        i -= 1
    return i


def keyword_moments(words: list) -> list[tuple[int, str]]:
    """(word index, mood) for spoken cues like 'oh no', 'awkward', 'silence', 'laughing'."""
    tokens = [_clean(w.text) for w in words]
    starts, pos = [], 0
    for t in tokens:
        starts.append(pos)
        pos += len(t) + 1
    joined = " ".join(tokens)
    hits = []
    for mood, pattern in LEXICON.items():
        for m in re.finditer(pattern, joined):
            hits.append((bisect.bisect_right(starts, m.start()) - 1, mood))
    return sorted(set(hits))


def _match_word(words: list, word: str, cursor: int) -> int | None:
    from .popups import _matches, _norm

    target = _norm(word.split()[0]) if word.split() else ""
    if not target:
        return None
    return next((i for i in range(cursor, len(words)) if _matches(_norm(words[i].text), target)), None)


def pick(lib: list[StickerInfo], mood: str, used: set[str], rng: random.Random) -> StickerInfo | None:
    reactions = [s for s in lib if s.category == "reaction"]
    for m in [mood, *RELATED.get(mood, [])]:
        options = [s for s in reactions if m in s.moods]
        if options:
            fresh = [s for s in options if s.filename not in used] or options
            return rng.choice(fresh)
    fresh = [s for s in reactions if s.filename not in used] or reactions
    return rng.choice(fresh) if fresh else None


def plan(
    settings: Settings,
    lib: list[StickerInfo],
    words: list,
    reactions: list[Reaction] | None = None,
    *,
    cta: str | None = "subscribe",
    auto: bool = True,
    popups: list[Overlay] | None = None,
    title_seconds: float = 0.0,
    end_seconds: float = 0.0,
    seed: str = "",
    max_reactions: int = 5,
    pace: bool = True,
) -> tuple[list[Overlay], list[str]]:
    """Sticker overlays for one Short: (overlays, sticker file names used). With `pace`, extra reactions
    fill every stretch longer than 5 s without a new pop-up or sticker."""
    if not words:
        return [], []
    total = words[-1].end + 0.3
    rng = random.Random(seed)
    busy: list[tuple[Rect, float, float]] = [(CAPTIONS, 0.0, 1e9)] + [(r, 0.0, 1e9) for r in SHORTS_UI]
    if title_seconds:
        busy.append((TOP_BANNER, 0.0, title_seconds))
    if end_seconds:
        busy.append((TOP_BANNER, max(words[-1].end - end_seconds, 0.0), 1e9))
    for ov in popups or []:
        busy.append((_rect(ov.xy(), ov.box, ov.box), ov.start, ov.end))

    def free(rect: Rect, start: float, end: float) -> bool:
        return not any(_hits(rect, r) and start < e and s < end for r, s, e in busy)

    out: list[Overlay] = []
    used: list[str] = []

    cta_index = find_cta_start(words) if cta else None
    cta_time = words[cta_index].start - 0.05 if cta_index is not None else None
    if cta and cta_time is None:  # no spoken CTA (viral Shorts): the sticker still closes the video
        cta_time = max(words[-1].end - SILENT_CTA_SECONDS, words[0].start + 1.0)
    if cta_time is not None:
        own = [s for s in lib if s.category == "cta" and s.cta in (cta, "subscribe")]
        own.sort(key=lambda s: s.cta != cta)
        if own:
            info = own[0]
            image, box, kind = settings.stickers_dir / info.filename, 380, "sticker" if info.transparent else "card"
            used.append(info.filename)
        else:
            image, box, kind = builtin_cta(settings, cta), 600, "asis"
        out.append(Overlay(image, cta_time, total, center=CTA_CENTER, box=box, style=kind, pulse=True,
                           animated=bool(own and own[0].animated)))
        busy.append((_rect(CTA_CENTER, box, box // 2 if kind == "asis" else box), cta_time, 1e9))

    moments: list[tuple[int, str]] = []
    cursor = 0
    for r in reactions or []:
        hit = _match_word(words, r.word, cursor)
        if hit is not None:
            moments.append((hit, r.mood))
            cursor = hit + 1
    if not moments and auto:
        moments = keyword_moments(words)
    if cta == "part2" and cta_index is not None:  # the cliffhanger: the sentence before the question + CTA
        cliff = sentence_start(words, max(cta_index - 1, 0))
        if cliff > 0 and words[cta_index - 1].text.endswith("?"):
            cliff = sentence_start(words, cliff - 1)
        if not any(cta_time - 4.0 <= words[i].start < cta_time for i, _ in moments):
            moments.append((cliff, "shocked"))
    moments.sort()

    stop = cta_time if cta_time is not None else total
    placed, last = 0, -1e9
    slot_names = list(SLOTS)

    def place(start: float, end: float, mood: str, room: float | None = None) -> bool:
        """`room`: how long an animated sticker may stay so its animation plays through once."""
        nonlocal placed, last
        info = pick(lib, mood, set(used), rng)
        if info is None:
            return False
        if info.animated and room is not None:
            end = max(end, min(start + min(info.loop_seconds, ANIMATED_MAX_SECONDS), room))
        order = slot_names[placed % len(slot_names):] + slot_names[: placed % len(slot_names)]
        for name in order:
            center = SLOTS[name]
            rect = _rect(center, REACTION_BOX + 30, REACTION_BOX + 30)
            if free(rect, start, end):
                tilt = (-6.0 if center[0] < WIDTH // 2 else 6.0) * (1 if placed % 2 == 0 else 0.6)
                out.append(Overlay(settings.stickers_dir / info.filename, start, end, center=center,
                                   box=REACTION_BOX, tilt=tilt, style="sticker" if info.transparent else "card",
                                   animated=info.animated))
                busy.append((rect, start, end))
                used.append(info.filename)
                placed += 1
                last = start
                return True
        return False

    for index, mood in moments:
        if placed >= max_reactions + (1 if cta == "part2" else 0):
            break
        start = max(words[index].start - 0.05, 0.0)
        if start < 0.8 or start - last < MIN_GAP:
            continue
        end = min(start + REACTION_SECONDS, stop - 0.05)
        if end - start < 1.0:
            continue
        if not any(s.category == "reaction" for s in lib):
            break
        nxt = next((words[i].start for i, _ in moments if words[i].start > start + MIN_GAP), stop)
        place(start, end, mood, room=min(nxt - 0.1, stop - 0.05))

    if pace and auto and any(s.category == "reaction" for s in lib):
        _fill_gaps(words, moments, [o.start for o in popups or []] + [o.start for o in out],
                   max(title_seconds, words[0].start), stop, place)
    out.sort(key=lambda o: o.start)
    return out, list(dict.fromkeys(used))


def _fill_gaps(words: list, moments: list[tuple[int, str]], starts: list[float], begin: float, stop: float,
               place) -> None:
    """Walk the timeline and drop a reaction about 4 s after the last visual whenever the next one is
    more than 5 s away. Fillers start on a spoken word, using the nearest keyword mood if there is one."""
    low, high = PACE
    events = sorted(starts)
    prev, n = begin, 0
    while True:
        nxt = next((t for t in events if t > prev + 0.01), stop)
        if nxt - prev <= high:
            if nxt >= stop:
                return
            prev = nxt
            continue
        target = prev + min((low + high) / 2, (nxt - prev) / 2)  # a 6 s gap is split 3 + 3, not 4 + 2
        options = [i for i, w in enumerate(words) if prev + low <= w.start <= min(prev + high, stop - 1.1)]
        if not options:
            later = [i for i, w in enumerate(words) if w.start >= prev + low]
            if not later or words[later[0]].start > stop - 1.1:
                return
            options = later[:1]
        index = min(options, key=lambda i: abs(words[i].start - target))
        start = max(words[index].start - 0.05, 0.0)
        end = min(start + REACTION_SECONDS, nxt - 0.1, stop - 0.05)
        near = [m for i, m in moments if abs(i - index) <= 3]
        mood = near[0] if near else FILLER_MOODS[n % len(FILLER_MOODS)]
        if end - start >= 1.0 and place(start, end, mood, room=min(nxt - 0.1, stop - 0.05)):
            events.append(start)
            events.sort()
            n += 1
        prev = start
