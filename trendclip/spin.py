"""Pause to Get Your Block: a Gamer IQ Short with no question. A card over the gameplay flicks through Minecraft
blocks, a new one every two frames, too fast to read; viewers pause, and the block they stop on is "theirs". Each
block has a rarity (grey common, blue rare, purple epic, gold legendary) on the card's frame and label, so a paused
viewer sees at once how lucky they got. They comment what they got and send it to a friend to compare: the format
is for comments and shares (the owner's idea, 2026-11-08; no "how do you craft it", which most can't answer).

The pictures are the game's own renders from the Minecraft Wiki, like Guess the Picture (reveal.py), fetched once
into assets/spin/ with catalog.json. Asked for by "spin" or "pause to get" in the Create tab's notes; it needs no
Gemini.
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from types import SimpleNamespace

from .config import Settings
from .video_assembler import WIDTH, Overlay, Word

logger = logging.getLogger(__name__)

SHAPE = "spin"
# name -> (tier, wiki page when it differs from the name)
BLOCKS = {
    "Dirt": ("common", ""), "Stone": ("common", ""), "Cobblestone": ("common", ""), "Sand": ("common", ""),
    "Gravel": ("common", ""), "Grass Block": ("common", ""), "Oak Log": ("common", ""),
    "Oak Planks": ("common", ""), "Glass": ("common", ""), "Pumpkin": ("common", ""), "Melon": ("common", ""),
    "Hay Bale": ("common", ""), "Crafting Table": ("common", ""), "Furnace": ("common", ""),
    "Iron Block": ("rare", "Block of Iron"), "Gold Block": ("rare", "Block of Gold"),
    "Redstone Block": ("rare", "Block of Redstone"), "Lapis Ore": ("rare", "Lapis Lazuli Ore"),
    "Bookshelf": ("rare", ""), "Obsidian": ("rare", ""), "Slime Block": ("rare", ""), "TNT": ("rare", ""),
    "Jukebox": ("rare", ""),
    "Diamond Ore": ("epic", ""), "Emerald Ore": ("epic", ""), "Enchanting Table": ("epic", ""),
    "Ender Chest": ("epic", ""), "Ancient Debris": ("epic", ""), "Sponge": ("epic", ""),
    "Crying Obsidian": ("epic", ""), "Respawn Anchor": ("epic", ""), "Budding Amethyst": ("epic", ""),
    "Diamond Block": ("legendary", "Block of Diamond"), "Netherite Block": ("legendary", "Block of Netherite"),
    "Emerald Block": ("legendary", "Block of Emerald"), "Beacon": ("legendary", ""),
    "Dragon Egg": ("legendary", ""), "Conduit": ("legendary", ""),
}
TIERS = {  # colour, share of the cards
    "common": ((150, 150, 150), 0.50),
    "rare": ((60, 140, 255), 0.27),
    "epic": ((176, 70, 255), 0.16),
    "legendary": ((255, 196, 0), 0.07),
}

HOLD = 2  # video frames per block: too fast to read while it plays, every frame a block when paused
W, H = 640, 760  # the card
PIC = 430  # the block's square on it
CENTER = (WIDTH // 2, 800)
FRAME = 18
PANEL = (128, 132, 146)

HOOK = "{game} fans only."
SCRIPT = ("{game} fans only. Pause the video right now. The block you stop on is yours. Grey is common. Blue is rare. "
          "Purple is epic. Gold is legendary. Got gold? Comment your block. Then send this to a friend, "
          "and see who gets the better one.")
TITLE = "Pause To Get Your {game} Block 🎮"
CARD = "PAUSE NOW!"  # one line: the four-line "PAUSE NOW - WHAT DID YOU GET?" covered the rarity label
END_CARD = "Comment your block"
PINNED = "What block did you get? 👇 Gold = legendary 🏆\nSend this to a friend and see who gets the better one."
DESCRIPTION = "Pause the video: the {game} block you stop on is yours. Comment what you got and challenge a friend."
HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#minecraftblocks"]


def wanted(notes: str = "") -> bool:
    low = (notes or "").lower()
    return "spin" in low or "pause to get" in low


def axe_wanted(notes: str = "") -> bool:
    """Pause to Get Your Axe: the axe-vs-wall variant (the owner's idea, 2026-11-10)."""
    return "axe" in (notes or "").lower()


def kind_of(script_text: str) -> str:
    """Which cards a spin Short flicks through, read off its fixed narration."""
    return "axe" if AXE_MARK in (script_text or "") else "block"


def library_dir(settings: Settings) -> Path:
    return settings.assets_dir / "spin"


def catalog(settings: Settings) -> list[dict]:
    import json

    try:
        entries = json.loads((library_dir(settings) / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in entries if e["name"] in BLOCKS]


def fetch_library(settings: Settings, session=None) -> list[dict]:
    from . import reveal

    return reveal.fetch_library(settings, "block", list(BLOCKS), session=session, folder=library_dir(settings),
                                titles={n: page for n, (_, page) in BLOCKS.items() if page})


def script(settings: Settings, game: str, *, format: str = "short", kind: str = "block"):
    """The Short's ShortScript: fixed narration, `spin` set, no answer (every viewer gets their own)."""
    from . import quiz

    axe = kind == "axe"
    if not (axe_catalog(settings) if axe else catalog(settings)):
        raise ValueError("No pictures yet: run spin.fetch_library / spin.fetch_axes first")
    game_name = quiz.game_name(game)
    title, description, hashtags = (AXE_TITLE, AXE_DESCRIPTION, AXE_HASHTAGS) if axe else (TITLE, DESCRIPTION,
                                                                                            HASHTAGS)
    pack = SimpleNamespace(title=title.format(game=game_name), description=description.format(game=game_name),
                           hashtags=hashtags, title_card=CARD)
    out = quiz._script(settings, game, "builtin", "gamer", format,
                       (AXE_SCRIPT if axe else SCRIPT).format(game=game_name), HOOK.format(game=game_name), [], "",
                       AXE_PINNED if axe else PINNED, pack,
                       on_screen="Pause to get your axe" if axe else "Pause to get your block", card=CARD,
                       end_card=AXE_END_CARD if axe else END_CARD)
    return out.model_copy(update={"spin": True, "reactions": []})


# --------------------------------------------------------------------------- the cards


def deck(entries: list[dict], count: int, rng: random.Random) -> list[dict]:
    """`count` blocks: each tier its share of the cards, never the same block twice in a row."""
    by_tier = {t: [e for e in entries if BLOCKS[e["name"]][0] == t] for t in TIERS}
    tiers = [t for t in TIERS if by_tier[t]]
    weights = [TIERS[t][1] for t in tiers]
    out: list[dict] = []
    for _ in range(count):
        while True:
            pick = rng.choice(by_tier[rng.choices(tiers, weights)[0]])
            if not out or pick["name"] != out[-1]["name"]:
                break
        out.append(pick)
    return out


def card(settings: Settings, entry: dict, cache: dict) -> "object":
    """The block on a dark card framed in its tier's colour, the tier on top and the name under it."""
    from PIL import Image, ImageDraw

    from .reveal import _font

    tier = BLOCKS[entry["name"]][0]
    colour = TIERS[tier][0]
    if entry["name"] not in cache:
        pic = Image.open(library_dir(settings) / entry["file"]).convert("RGBA")
        scale = min(PIC / pic.width, PIC / pic.height)
        cache[entry["name"]] = pic.resize((max(1, round(pic.width * scale)), max(1, round(pic.height * scale))),
                                          Image.NEAREST if max(pic.size) < 160 else Image.LANCZOS)
    pic = cache[entry["name"]]
    img = Image.new("RGBA", (W, H), colour + (255,))
    d = ImageDraw.Draw(img)
    d.rectangle([FRAME, FRAME, W - FRAME, H - FRAME], fill=(24, 24, 30))
    d.rectangle([FRAME, FRAME, W - FRAME, FRAME + 92], fill=colour)
    label = _font(64)
    text = tier.upper()
    d.text(((W - d.textlength(text, font=label)) / 2, FRAME + 6), text, font=label,
           fill=(20, 20, 20) if tier in ("common", "legendary") else (255, 255, 255))
    top = FRAME + 110
    d.rounded_rectangle([(W - PIC) // 2 - 24, top - 14, (W + PIC) // 2 + 24, top + PIC + 14], radius=28,
                        fill=PANEL)  # mid grey: dark blocks (Dragon Egg, Obsidian) vanished on the dark card
    img.alpha_composite(pic, ((W - pic.width) // 2, top + (PIC - pic.height) // 2))
    name = entry["name"].upper()
    size = 60
    while size > 34 and d.textlength(name, font=_font(size)) > W - 2 * FRAME - 30:
        size -= 4
    font = _font(size)
    d.text(((W - d.textlength(name, font=font)) / 2, H - FRAME - 30 - size), name, font=font, fill=(255, 255, 255))
    return img


def plan(settings: Settings, words: list[Word], seed: str = "", tail: float = 2.0, fps: int = 30,
         kind: str = "block") -> list[Overlay]:
    """One overlay for the whole video: an animated WebP of the cards, a new block (or axe and wall) every HOLD
    frames, from frame 0 to the end (the loop starts on a card too)."""
    if not words:
        return []
    rng = random.Random(seed or "spin")
    total = words[-1].end + tail
    count = int(total * fps / HOLD) + 2
    cache: dict = {}
    if kind == "axe":
        cards = [axe_card(settings, a, w, cache) for a, w in axe_deck(axe_catalog(settings), count, rng)]
    else:
        cards = [card(settings, e, cache) for e in deck(catalog(settings), count, rng)]
    out_dir = settings.assets_dir / "cache" / "spin"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (re.sub(r"[^a-z0-9]+", "-", (seed or "spin").lower())[:40].strip("-") + ".webp")
    cards[0].save(path, save_all=True, append_images=cards[1:], duration=round(1000 * HOLD / fps), loop=0,
                  lossless=False, quality=90)
    return [Overlay(path, 0.0, round(total + 0.5, 3), center=CENTER, box=max(cards[0].size), style="asis",
                    cut=True, animated=True)]


# --------------------------------------------------------------------------- Pause to Get Your Axe
# The left half is a wall (a block), the right half an axe; the bottom bar says how long that axe takes to break
# that wall in Minecraft Java, by the wiki's formula (minecraft.wiki/w/Breaking): an axe speeds up only the blocks
# it's the right tool for (wood), a block that needs a pickaxe is mined at 1/100 per hardness instead of 1/30,
# rounded up to whole ticks of 1/20 s. Bedrock never breaks. The gold axe is the fastest on wood: a fact fans
# argue about in the comments.

AXE_MARK = "The axe you stop on is yours"
AXES = {  # name -> (tier, mining speed, share of the cards)
    "Wooden Axe": ("common", 2, 0.32), "Stone Axe": ("common", 4, 0.25), "Iron Axe": ("rare", 6, 0.18),
    "Golden Axe": ("rare", 12, 0.12), "Diamond Axe": ("epic", 8, 0.09), "Netherite Axe": ("legendary", 9, 0.04),
}
WALL = {  # name -> (hardness or None for unbreakable, needs a pickaxe to drop, the axe is its tool)
    "Oak Log": (2.0, False, True), "Oak Planks": (2.0, False, True), "Crafting Table": (2.5, False, True),
    "Bookshelf": (1.5, False, True), "Chest": (2.5, False, True), "Jukebox": (2.0, False, True),
    "Pumpkin": (1.0, False, True), "Melon": (1.0, False, True),
    "Dirt": (0.5, False, False), "Grass Block": (0.6, False, False), "Sand": (0.5, False, False),
    "Gravel": (0.6, False, False), "Glass": (0.3, False, False), "Hay Bale": (0.5, False, False),
    "TNT": (0.0, False, False),
    "Stone": (1.5, True, False), "Cobblestone": (2.0, True, False), "Iron Block": (5.0, True, False),
    "Diamond Block": (5.0, True, False), "Obsidian": (50.0, True, False), "Crying Obsidian": (50.0, True, False),
    "Ancient Debris": (30.0, True, False), "Netherite Block": (50.0, True, False),
    "Bedrock": (None, False, False),
}
AXE_SCRIPT = ("{game} fans only. Pause the video right now. The axe you stop on is yours, and so is the wall next "
              "to it. Can your axe break through? Netherite is legendary. But on wood, gold is the fastest. "
              "Comment your axe, your wall and your time. Then send this to a friend, and see who breaks out "
              "first.")
assert AXE_MARK in AXE_SCRIPT
AXE_TITLE = "Pause To Get Your {game} Axe 🪓"
AXE_END_CARD = "Comment your time"
AXE_PINNED = "What did you get? 👇 Axe + wall + time ⏱️\nSend this to a friend and see who breaks out first."
AXE_DESCRIPTION = ("Pause the video: the {game} axe you stop on is yours, and so is the wall. How long does it take "
                   "to break? Comment your time and challenge a friend.")
AXE_HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#minecraftaxe"]
AW, AH = 1000, 760  # the axe-vs-wall card: as tall as the block card, so the captions under it stay clear
APIC = 300
WALL_COLOUR = (90, 94, 108)


def axe_dir(settings: Settings) -> Path:
    return settings.assets_dir / "axe"


def fetch_axes(settings: Settings, session=None) -> list[dict]:
    """The axes and the wall blocks the block library lacks (Bedrock, Chest), into assets/axe/."""
    from . import reveal

    return reveal.fetch_library(settings, "axe", list(AXES) + ["Bedrock", "Chest"], session=session,
                                folder=axe_dir(settings))


def axe_catalog(settings: Settings) -> dict[str, list[dict]]:
    """{"axes": [...], "walls": [...]} with each picture's full path; empty when either side has none."""
    import json

    found: dict[str, dict] = {}
    for folder in (library_dir(settings), axe_dir(settings)):
        try:
            for e in json.loads((folder / "catalog.json").read_text(encoding="utf-8")):
                found[e["name"]] = {**e, "path": str(folder / e["file"])}
        except (OSError, ValueError):
            continue
    axes = [found[n] for n in AXES if n in found]
    walls = [found[n] for n in WALL if n in found]
    return {"axes": axes, "walls": walls} if axes and walls else {}


def break_seconds(wall: str, axe: str) -> float | None:
    """Seconds for `axe` to break `wall` in Java Edition (no enchantments, on the ground); None = never."""
    import math

    hardness, needs_pickaxe, axe_tool = WALL[wall]
    if hardness is None:
        return None
    if hardness == 0:
        return 0.0
    speed = AXES[axe][1] if axe_tool else 1
    damage = speed / hardness / (100 if needs_pickaxe else 30)
    return 0.0 if damage >= 1 else math.ceil(1 / damage) / 20


def verdict(seconds: float | None) -> tuple[str, tuple[int, int, int]]:
    """The bottom bar's words and colour."""
    if seconds is None:
        return "NEVER BREAKS", (150, 20, 20)
    if seconds == 0:
        return "INSTANT BREAK!", (40, 170, 70)
    if seconds >= 60:
        whole = round(seconds)
        text = f"{whole // 60} MIN {whole % 60} S" if whole % 60 else f"{whole // 60} MINUTES"
    else:
        text = f"{seconds:g} SECONDS" if seconds != 1 else "1 SECOND"
    colour = (40, 170, 70) if seconds <= 2 else (230, 140, 20) if seconds <= 15 else (200, 40, 40)
    return ("BREAKS IN " if seconds <= 2 else "TAKES ") + text, colour


def axe_deck(found: dict, count: int, rng: random.Random) -> list[tuple[dict, dict]]:
    """`count` (axe, wall) pairs: each axe its share, wooden walls three times as often as the rest (only on wood
    does the axe you got change the time), never the same wall twice in a row (so every frame visibly changes)."""
    axes, walls = found["axes"], found["walls"]
    weights = [AXES[a["name"]][2] for a in axes]
    wall_weights = [3 if WALL[w["name"]][2] else 1 for w in walls]
    out: list[tuple[dict, dict]] = []
    for _ in range(count):
        while True:
            pair = (rng.choices(axes, weights)[0], rng.choices(walls, wall_weights)[0])
            if not out or pair[1]["name"] != out[-1][1]["name"]:
                break
        out.append(pair)
    return out


def _fit(path: str, size: int, cache: dict):
    from PIL import Image

    if path not in cache:
        pic = Image.open(path).convert("RGBA")
        scale = min(size / pic.width, size / pic.height)
        cache[path] = pic.resize((max(1, round(pic.width * scale)), max(1, round(pic.height * scale))),
                                 Image.NEAREST if max(pic.size) < 160 else Image.LANCZOS)
    return cache[path]


def axe_card(settings: Settings, axe: dict, wall: dict, cache: dict):
    """The wall on the left, the axe on the right framed in its tier's colour, the break time across the bottom."""
    from PIL import Image, ImageDraw

    from .reveal import _font

    tier = AXES[axe["name"]][0]
    colour = TIERS[tier][0]
    img = Image.new("RGBA", (AW, AH), (24, 24, 30, 255))
    d = ImageDraw.Draw(img)
    half = AW // 2
    d.rectangle([0, 0, AW - 1, AH - 1], outline=colour, width=FRAME)
    head = 92
    d.rectangle([FRAME, FRAME, half - 6, FRAME + head], fill=WALL_COLOUR)
    d.rectangle([half + 6, FRAME, AW - FRAME, FRAME + head], fill=colour)
    label = _font(58)
    for text, x0, x1, fill in (("THE WALL", FRAME, half - 6, (255, 255, 255)),
                               (tier.upper(), half + 6, AW - FRAME,
                                (20, 20, 20) if tier in ("common", "legendary") else (255, 255, 255))):
        d.text(((x0 + x1 - d.textlength(text, font=label)) / 2, FRAME + 8), text, font=label, fill=fill)
    top = FRAME + head + 24
    for entry, x0, x1 in ((wall, FRAME, half - 6), (axe, half + 6, AW - FRAME)):
        mid = (x0 + x1) // 2
        d.rounded_rectangle([mid - APIC // 2 - 20, top, mid + APIC // 2 + 20, top + APIC + 40], radius=26,
                            fill=PANEL)
        pic = _fit(entry["path"], APIC, cache)
        img.alpha_composite(pic, (mid - pic.width // 2, top + 20 + (APIC - pic.height) // 2))
        name = entry["name"].upper()
        size = 50
        while size > 28 and d.textlength(name, font=_font(size)) > x1 - x0 - 24:
            size -= 4
        d.text((mid - d.textlength(name, font=_font(size)) / 2, top + APIC + 56), name, font=_font(size),
               fill=(255, 255, 255))
    vs = _font(64)
    d.ellipse([half - 52, top + APIC // 2 - 32, half + 52, top + APIC // 2 + 72], fill=(255, 255, 255))
    d.text((half - d.textlength("VS", font=vs) / 2, top + APIC // 2 - 22), "VS", font=vs, fill=(20, 20, 20))
    text, bar = verdict(break_seconds(wall["name"], axe["name"]))
    y0 = AH - FRAME - 150
    d.rectangle([FRAME, y0, AW - FRAME, AH - FRAME], fill=bar)
    size = 76
    while size > 40 and d.textlength(text, font=_font(size)) > AW - 2 * FRAME - 40:
        size -= 4
    d.text(((AW - d.textlength(text, font=_font(size))) / 2, y0 + (150 - size) / 2 - 8), text, font=_font(size),
           fill=(255, 255, 255))
    return img
