"""Guess the Picture: a Gamer IQ level with no question. A picture from the game (a mob, a block) sits behind a grid
of stone blocks over the gameplay; the blocks break one by one, every half second, the plainest parts first, until
about half of it shows. It never shows whole. A timer counts the seconds, so viewers comment the second they knew
("4s"), and the answer is on one frame near the end (the usual Gamer IQ pause-to-see).

The pictures are the game's own renders from the Minecraft Wiki (Mojang's game content, the same as the gameplay
behind it; never fan art), fetched once into assets/reveal/ with catalog.json. Asked for by "reveal" or "guess the
picture" in the Create tab's notes; it needs no Gemini.
"""

from __future__ import annotations

import io
import json
import logging
import random
import re
from pathlib import Path
from types import SimpleNamespace

from .config import Settings
from .video_assembler import WIDTH, Overlay, Word

logger = logging.getLogger(__name__)

SHAPE = "reveal"
WIKI_API = "https://minecraft.wiki/api.php"
USER_AGENT = "TrendClip/1.0 (Gamer IQ picture levels)"
# Mobs most players know by sight; the hint says "a mob".
MOBS = ["Creeper", "Enderman", "Zombie", "Skeleton", "Spider", "Witch", "Villager", "Iron Golem", "Snow Golem",
        "Wolf", "Cat", "Ocelot", "Fox", "Panda", "Polar Bear", "Bee", "Axolotl", "Allay", "Frog", "Goat", "Sniffer",
        "Camel", "Armadillo", "Breeze", "Warden", "Ghast", "Blaze", "Piglin", "Hoglin", "Strider", "Wither",
        "Ender Dragon", "Guardian", "Elder Guardian", "Shulker", "Phantom", "Pillager", "Ravager", "Evoker",
        "Wandering Trader", "Slime", "Magma Cube", "Silverfish", "Endermite", "Turtle", "Dolphin", "Squid",
        "Glow Squid", "Parrot", "Llama", "Horse", "Mooshroom", "Chicken", "Pig", "Sheep", "Cow", "Rabbit", "Drowned",
        "Husk", "Stray", "Zoglin", "Vex", "Trader Llama", "Tadpole", "Bat", "Pufferfish", "Tropical Fish"]
KINDS = {"mob": MOBS}

GRID = 6  # about 6 x 6 blocks over the picture (its own box: a tall mob gets more rows)
STEP = 0.5  # one block breaks every half second (on the half second, so the timer and the picture change together)
LEFT = 0.4  # share of the blocks still standing at the end: it never shows whole
SIDE = 600  # the picture's square on the 1080 x 1920 canvas
STRIP = 116  # the timer strip under it
CENTER = (WIDTH // 2, 790)  # under the end card (top), above the captions
CAPTION_MARGIN = 520  # the captions end 520 px from the bottom, under the card (the quiz default 620 covered it)
FRAME = 14
BG = (198, 198, 198)  # the inventory grey behind the picture
DARK, LIGHT = (55, 55, 55), (255, 255, 255)
STONE = ((125, 125, 125), (112, 112, 112), (138, 138, 138), (100, 100, 100))

HOOK = "{game} fans only."
SCRIPT = ("{game} fans only. Something is hiding behind these blocks. One breaks every half second. "
          "Guess it before you see it all. Still no idea? It's a {kind}. Three. Two. One.")
WINDOW = ("Something", "One")  # the blocks break between these two spoken words
TITLE = "Guess The {game} {Kind} | Level {level} 🎮"
CARD = "LEVEL {level} - WHAT IS IT?"
END_CARD = "Comment the second you knew"
PINNED = ("What is it? And which second did you know? 👇\n🏆 First correct answer gets 10 leaderboard points. "
          "Level {next} drops next!")
DESCRIPTION = "A {game} {kind} hides behind the blocks. Guess it before it shows, and comment the second you knew."
HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#guessthepicture", "#quiz"]


def wanted(notes: str = "") -> bool:
    low = (notes or "").lower()
    return "reveal" in low or "guess the picture" in low


def library_dir(settings: Settings) -> Path:
    return settings.assets_dir / "reveal"


def catalog(settings: Settings) -> list[dict]:
    path = library_dir(settings) / "catalog.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def fetch_library(settings: Settings, kind: str = "mob", names: list[str] | None = None, session=None) -> list[dict]:
    """Download each page's main image from the Minecraft Wiki (first frame of a GIF, trimmed, PNG) and record it
    in catalog.json. Pictures already there are kept."""
    import requests
    from PIL import Image

    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    folder = library_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    found = {e["name"]: e for e in catalog(settings)}
    todo = [n for n in (names or KINDS[kind]) if n not in found]
    for i in range(0, len(todo), 20):
        batch = todo[i:i + 20]
        r = session.get(WIKI_API, params={"action": "query", "titles": "|".join(batch), "prop": "pageimages",
                                          "piprop": "original", "redirects": 1, "format": "json"}, timeout=30)
        r.raise_for_status()
        data = r.json()["query"]
        back = {x["to"]: x["from"] for x in data.get("redirects", [])}
        for page in data.get("pages", {}).values():
            src = (page.get("original") or {}).get("source")
            name = back.get(page.get("title"), page.get("title"))
            if not src or name not in batch:
                logger.warning("No wiki picture for %s", name)
                continue
            try:
                img = Image.open(io.BytesIO(session.get(src, timeout=30).content))
                img.seek(0)
                img = img.convert("RGBA")
            except Exception as err:  # noqa: BLE001 - a bad file skips one picture
                logger.warning("Picture for %s unreadable: %s", name, err)
                continue
            box = img.getchannel("A").getbbox()
            if box:
                img = img.crop(box)
            out = folder / f"{slug(name)}.png"
            img.save(out)
            found[name] = {"name": name, "kind": kind, "file": out.name, "source": src.split("?")[0]}
    entries = sorted(found.values(), key=lambda e: e["name"])
    (folder / "catalog.json").write_text(json.dumps(entries, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return entries


def pick(settings: Settings, rng: random.Random, name: str | None = None) -> dict:
    """A picture not used in the recent levels (or `name`)."""
    from . import quiz

    entries = catalog(settings)
    if not entries:
        raise ValueError("No pictures yet: run reveal.fetch_library first")
    if name:
        return next(e for e in entries if e["name"].lower() == name.lower())
    used = {d.get("answer") for d in quiz.history(settings, "gamer") if d.get("shape") == SHAPE}
    fresh = [e for e in entries if e["name"] not in used] or entries
    return rng.choice(fresh)


def script(settings: Settings, game: str, *, format: str = "short", rng: random.Random | None = None,
           level: int | None = None, record: bool = True, name: str | None = None):
    """The level's ShortScript: fixed narration, the picture in `reveal`, its name as the answer."""
    from . import quiz

    rng = rng or random.Random()
    level = level or quiz.gamer_level(settings)
    entry = pick(settings, rng, name)
    game_name = quiz.game_name(game)
    kind = entry["kind"]
    if record:
        quiz._remember(settings, "gamer", answer=entry["name"], question="Guess the picture", game=game, shape=SHAPE,
                       level=str(level))
    pack = SimpleNamespace(title=TITLE.format(game=game_name, Kind=kind.title(), level=level),
                           description=DESCRIPTION.format(game=game_name, kind=kind), hashtags=HASHTAGS,
                           title_card=CARD.format(level=level))
    out = quiz._script(settings, game, "builtin", "gamer", format, SCRIPT.format(game=game_name, kind=kind),
                       HOOK.format(game=game_name), [], entry["name"], PINNED.format(next=level + 1), pack,
                       on_screen=f"The picture: {entry['name']}", card=CARD.format(level=level), end_card=END_CARD)
    return out.model_copy(update={"reveal": entry["file"], "reactions": []})


# --------------------------------------------------------------------------- the card


def _stone(rng: random.Random, w: int, h: int):
    """One code-drawn stone block: grey noise in 8 x 8 pixels with a light top-left and dark bottom-right edge."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    cw, ch = w / 8, h / 8
    for y in range(8):
        for x in range(8):
            d.rectangle([round(x * cw), round(y * ch), round((x + 1) * cw), round((y + 1) * ch)], fill=rng.choice(STONE))
    edge = max(4, min(w, h) // 22)
    d.rectangle([0, 0, w, edge], fill=(160, 160, 160))
    d.rectangle([0, 0, edge, h], fill=(160, 160, 160))
    d.rectangle([0, h - edge, w, h], fill=(70, 70, 70))
    d.rectangle([w - edge, 0, w, h], fill=(70, 70, 70))
    return img


def body_box(picture) -> tuple[int, int, int, int]:
    """The picture's box without loose specks (the Enderman's particles): rows and columns with a real share of
    solid pixels."""
    import numpy as np

    solid = np.asarray(picture.getchannel("A")) > 128
    cols, rows = solid.sum(axis=0), solid.sum(axis=1)
    if not cols.any():
        return 0, 0, picture.width, picture.height
    xs = np.nonzero(cols >= max(2, cols.max() * 0.06))[0]
    ys = np.nonzero(rows >= max(2, rows.max() * 0.06))[0]
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


def tiles(picture) -> list[tuple[int, int, int, int]]:
    """About GRID² blocks over the picture's own box (a tall mob gets tall rows of narrow blocks), leaving out the
    ones with nothing of the picture under them."""
    from PIL import ImageStat

    x0, y0, x1, y1 = body_box(picture)
    aspect = (x1 - x0) / max(1, y1 - y0)
    cols = max(3, min(9, round((GRID * GRID * aspect) ** 0.5)))
    rows = max(3, min(9, round(GRID * GRID / cols)))
    out = []
    for r in range(rows):
        for c in range(cols):
            box = (round(x0 + (x1 - x0) * c / cols), round(y0 + (y1 - y0) * r / rows),
                   round(x0 + (x1 - x0) * (c + 1) / cols), round(y0 + (y1 - y0) * (r + 1) / rows))
            if ImageStat.Stat(picture.crop(box).getchannel("A")).mean[0] / 255 >= 0.01:
                out.append(box)
    return out


def order(picture, boxes: list, rng: random.Random) -> list[int]:
    """Blocks in breaking order: plain ones (edges, flat colour) first, the busiest (faces, eyes) last."""
    from PIL import ImageStat

    scores = []
    for i, box in enumerate(boxes):
        tile = picture.crop(box)
        alpha = ImageStat.Stat(tile.getchannel("A")).mean[0] / 255
        detail = sum(ImageStat.Stat(tile.convert("RGB")).stddev) / 3 / 128
        scores.append((alpha * 0.5 + detail + rng.uniform(0, 0.3), i))
    return [i for _, i in sorted(scores)]


def _font(size: int):
    from PIL import ImageFont

    from .video_assembler import find_font_file

    _, path = find_font_file()
    try:
        return ImageFont.truetype(str(path), size) if path else ImageFont.load_default()
    except OSError:
        return ImageFont.load_default()


def card(picture, boxes: list, broken: set[int], second: int, stones: list) -> "object":
    """The framed picture with the standing blocks on it and the timer strip under it."""
    from PIL import Image, ImageDraw

    w, h = SIDE + 2 * FRAME, SIDE + STRIP + 3 * FRAME
    img = Image.new("RGBA", (w, h), DARK + (255,))
    inner = Image.new("RGBA", (SIDE, SIDE), BG + (255,))
    inner.alpha_composite(picture)
    for i, box in enumerate(boxes):
        if i not in broken:
            inner.paste(stones[i], box[:2])
    img.paste(inner, (FRAME, FRAME))
    d = ImageDraw.Draw(img)
    top = SIDE + 2 * FRAME
    d.rectangle([FRAME, top, w - FRAME, h - FRAME], fill=(30, 30, 30))
    font = _font(88)
    text = f"TIME  {second}s"
    tw = d.textlength(text, font=font)
    d.text(((w - tw) / 2, top + (STRIP - 88) / 2 - 8), text, font=font, fill=(255, 221, 0))
    return img


def _fit(settings: Settings, file: str):
    """The picture centred on a SIDE x SIDE square, as large as fits with a small margin."""
    from PIL import Image

    pic = Image.open(library_dir(settings) / file).convert("RGBA")
    pic = pic.crop(body_box(pic))  # loose specks around it (the Enderman's particles) would give it away
    room = round(SIDE * 0.9)
    scale = min(room / pic.width, room / pic.height)
    pic = pic.resize((max(1, round(pic.width * scale)), max(1, round(pic.height * scale))), Image.NEAREST
                     if max(pic.size) < 200 else Image.LANCZOS)
    out = Image.new("RGBA", (SIDE, SIDE), (0, 0, 0, 0))
    out.alpha_composite(pic, ((SIDE - pic.width) // 2, (SIDE - pic.height) // 2))
    return out


def _word_time(words: list[Word], text: str, start: bool, default: float) -> float:
    low = text.lower()
    hits = [w for w in words if re.sub(r"[^a-z]", "", w.text.lower()) == low]
    if not hits:
        return default
    return hits[0].start if start else hits[-1].end


def plan(settings: Settings, file: str, words: list[Word], seed: str = "", tail: float = 2.0) -> list[Overlay]:
    """One overlay per half second: the card with the blocks standing then. All blocks up to the spoken
    "Something", then one breaks every STEP until GRID² * (1 - LEFT) are gone; the rest stay to the end."""
    if not words:
        return []
    rng = random.Random(seed or file)
    picture = _fit(settings, file)
    boxes = tiles(picture)
    stones = [_stone(rng, b[2] - b[0], b[3] - b[1]) for b in boxes]
    breaking = order(picture, boxes, rng)
    total = words[-1].end + tail
    begin = max(1.0, _word_time(words, WINDOW[0], True, 1.5))
    begin = round(begin * 2) / 2  # on the half second
    close = _word_time(words, WINDOW[1], False, total - tail)
    goal = round(len(boxes) * (1 - LEFT))
    steps = max(1, min(goal, int((close - begin) / STEP) + 1))
    out_dir = settings.assets_dir / "cache" / "reveal" / slug(Path(file).stem + "-" + (seed[:24] or "x"))
    out_dir.mkdir(parents=True, exist_ok=True)
    marks = sorted({0.0, *[begin + k * STEP for k in range(steps)], *[float(s) for s in range(1, int(total) + 1)]})
    marks = [m for m in marks if m < total]
    out = []
    for n, at in enumerate(marks):
        broken = set(breaking[:sum(1 for k in range(steps) if begin + k * STEP <= at + 1e-6)])
        path = out_dir / f"{n:03d}.png"
        card(picture, boxes, broken, int(at), stones).save(path)
        end = marks[n + 1] if n + 1 < len(marks) else total + 0.5
        out.append(Overlay(path, round(at, 3), round(end, 3), center=CENTER, box=SIDE + STRIP + 3 * FRAME,
                           style="asis", cut=True))
    return out
