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
CARD = "PAUSE NOW - WHAT DID YOU GET?"
END_CARD = "Comment your block"
PINNED = "What block did you get? 👇 Gold = legendary 🏆\nSend this to a friend and see who gets the better one."
DESCRIPTION = "Pause the video: the {game} block you stop on is yours. Comment what you got and challenge a friend."
HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#minecraftblocks"]


def wanted(notes: str = "") -> bool:
    low = (notes or "").lower()
    return "spin" in low or "pause to get" in low


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


def script(settings: Settings, game: str, *, format: str = "short"):
    """The Short's ShortScript: fixed narration, `spin` set, no answer (every viewer gets their own)."""
    from . import quiz

    if not catalog(settings):
        raise ValueError("No blocks yet: run spin.fetch_library first")
    game_name = quiz.game_name(game)
    pack = SimpleNamespace(title=TITLE.format(game=game_name), description=DESCRIPTION.format(game=game_name),
                           hashtags=HASHTAGS, title_card=CARD)
    out = quiz._script(settings, game, "builtin", "gamer", format, SCRIPT.format(game=game_name),
                       HOOK.format(game=game_name), [], "", PINNED, pack, on_screen="Pause to get your block",
                       card=CARD, end_card=END_CARD)
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


def plan(settings: Settings, words: list[Word], seed: str = "", tail: float = 2.0, fps: int = 30) -> list[Overlay]:
    """One overlay for the whole video: an animated WebP of the cards, a new block every HOLD frames, from frame
    0 to the end (the loop starts on a card too)."""
    if not words:
        return []
    rng = random.Random(seed or "spin")
    total = words[-1].end + tail
    count = int(total * fps / HOLD) + 2
    cache: dict = {}
    cards = [card(settings, e, cache) for e in deck(catalog(settings), count, rng)]
    out_dir = settings.assets_dir / "cache" / "spin"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (re.sub(r"[^a-z0-9]+", "-", (seed or "spin").lower())[:40].strip("-") + ".webp")
    cards[0].save(path, save_all=True, append_images=cards[1:], duration=round(1000 * HOLD / fps), loop=0,
                  lossless=False, quality=90)
    return [Overlay(path, 0.0, round(total + 0.5, 3), center=CENTER, box=max(W, H), style="asis", cut=True,
                    animated=True)]
