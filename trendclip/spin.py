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


def weapon_wanted(notes: str = "") -> bool:
    """Pause to Get Your Weapon: a mob against a weapon (the owner's idea, 2026-11-10)."""
    return "weapon" in (notes or "").lower()


def kind_of(script_text: str) -> str:
    """Which cards a spin Short flicks through, read off its fixed narration."""
    text = script_text or ""
    return "weapon" if WEAPON_MARK in text else "axe" if AXE_MARK in text else "block"


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


def script(settings: Settings, game: str, *, format: str = "short", kind: str = "block",
           rng: random.Random | None = None):
    """The Short's ShortScript: fixed narration, `spin` set, no answer (every viewer gets their own)."""
    from . import quiz

    have = {"axe": axe_catalog, "weapon": weapon_catalog}.get(kind, catalog)
    if not have(settings):
        raise ValueError("No pictures yet: run spin.fetch_library / fetch_axes / fetch_weapons first")
    game_name = quiz.game_name(game)
    words, title, description, hashtags, pinned, end_card = {
        "axe": (AXE_SCRIPT, AXE_TITLE, AXE_DESCRIPTION, AXE_HASHTAGS, AXE_PINNED, AXE_END_CARD),
        "weapon": (WEAPON_SCRIPT, WEAPON_TITLE, WEAPON_DESCRIPTION, WEAPON_HASHTAGS, WEAPON_PINNED, WEAPON_END_CARD),
    }.get(kind, (SCRIPT, TITLE, DESCRIPTION, HASHTAGS, PINNED, END_CARD))
    boss = (rng or random.Random()).choice([b["name"] for b in weapon_catalog(settings)["bosses"]]) \
        if kind == "weapon" else ""
    fill = {"game": game_name, "boss": boss, "article": article(boss) if boss else ""}
    pack = SimpleNamespace(title=title.format(**fill), description=description.format(**fill),
                           hashtags=hashtags, title_card=CARD)
    out = quiz._script(settings, game, "builtin", "gamer", format, words.format(**fill),
                       HOOK.format(game=game_name), [], "", pinned.format(**fill), pack,
                       on_screen=f"Pause to get your {kind if kind in ('axe', 'weapon') else 'block'}", card=CARD,
                       end_card=end_card)
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
         kind: str = "block", script_text: str = "") -> list[Overlay]:
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
    elif kind == "weapon":
        found = weapon_catalog(settings)
        boss = next(b for b in found["bosses"] if b["name"] == boss_of(script_text or " ".join(w.text for w in words)))
        cards = [boss_card(settings, boss, w, cache) for w in weapon_deck(found, count, rng)]
    else:
        cards = [card(settings, e, cache) for e in deck(catalog(settings), count, rng)]
    out_dir = settings.assets_dir / "cache" / "spin"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (re.sub(r"[^a-z0-9]+", "-", (seed or "spin").lower())[:40].strip("-") + ".webp")
    cards[0].save(path, save_all=True, append_images=cards[1:], duration=round(1000 * HOLD / fps), loop=0,
                  lossless=False, quality=90)
    return [Overlay(path, 0.0, round(total + 0.5, 3), center=BOSS_CENTER if kind == "weapon" else CENTER,
                    box=max(cards[0].size), style="asis",
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
    text, bar = verdict(break_seconds(wall["name"], axe["name"]))
    return versus_card(wall, "THE WALL", axe, AXES[axe["name"]][0], text, bar, cache)


def versus_card(left: dict, left_label: str, right: dict, tier: str, text: str, bar: tuple, cache: dict,
                right_size: int | None = None):
    """Two pictures side by side with VS between them: `left` under a grey label, `right` under its tier, the
    frame in the tier's colour and `text` on a `bar`-coloured strip across the bottom."""
    from PIL import Image, ImageDraw

    from .reveal import _font

    colour = TIERS[tier][0]
    img = Image.new("RGBA", (AW, AH), (24, 24, 30, 255))
    d = ImageDraw.Draw(img)
    half = AW // 2
    d.rectangle([0, 0, AW - 1, AH - 1], outline=colour, width=FRAME)
    head = 92
    d.rectangle([FRAME, FRAME, half - 6, FRAME + head], fill=WALL_COLOUR)
    d.rectangle([half + 6, FRAME, AW - FRAME, FRAME + head], fill=colour)
    label = _font(58)
    for words, x0, x1, fill in ((left_label, FRAME, half - 6, (255, 255, 255)),
                                (tier.upper(), half + 6, AW - FRAME,
                                 (20, 20, 20) if tier in ("common", "legendary") else (255, 255, 255))):
        d.text(((x0 + x1 - d.textlength(words, font=label)) / 2, FRAME + 8), words, font=label, fill=fill)
    top = FRAME + head + 24
    for entry, x0, x1, size in ((left, FRAME, half - 6, APIC), (right, half + 6, AW - FRAME, right_size or APIC)):
        mid = (x0 + x1) // 2
        d.rounded_rectangle([mid - APIC // 2 - 20, top, mid + APIC // 2 + 20, top + APIC + 40], radius=26,
                            fill=PANEL)
        pic = _fit(entry["path"], size, cache)
        img.alpha_composite(pic, (mid - pic.width // 2, top + 20 + (APIC - pic.height) // 2))
        name = entry["name"].upper()
        font_size = 50
        while font_size > 28 and d.textlength(name, font=_font(font_size)) > x1 - x0 - 24:
            font_size -= 4
        d.text((mid - d.textlength(name, font=_font(font_size)) / 2, top + APIC + 56), name,
               font=_font(font_size), fill=(255, 255, 255))
    vs = _font(64)
    d.ellipse([half - 52, top + APIC // 2 - 32, half + 52, top + APIC // 2 + 72], fill=(255, 255, 255))
    d.text((half - d.textlength("VS", font=vs) / 2, top + APIC // 2 - 22), "VS", font=vs, fill=(20, 20, 20))
    y0 = AH - FRAME - 150
    d.rectangle([FRAME, y0, AW - FRAME, AH - FRAME], fill=bar)
    font_size = 76
    while font_size > 40 and d.textlength(text, font=_font(font_size)) > AW - 2 * FRAME - 40:
        font_size -= 4
    d.text(((AW - d.textlength(text, font=_font(font_size))) / 2, y0 + (150 - font_size) / 2 - 8), text,
           font=_font(font_size), fill=(255, 255, 255))
    return img


# --------------------------------------------------------------------------- Pause to Get Your Weapon
# One of the strongest mobs stands at the top of the card for the whole Short (the boss, named in the narration);
# under it the game's weapons flick by, a new one every HOLD frames. A paused viewer sees their weapon and how many
# hits it needs to beat the boss in Java Edition: the weapon's damage (minecraft.wiki Sword / Axe / Pickaxe /
# Spear jab / Mace / Trident; a bow's full-charge arrow, 6), less the boss's armour by the wiki's formula, into its
# health, rounded up. No crits, enchantments or mace smashes; the dragon is hit on the head. Arrows can't finish a
# wither (shielded from half health). The crossbow is left out: its 6-11 damage is random. The owner's design,
# 2026-11-10: one boss on top, the weapons flicking under it, "what did you get and in how many hits do you win?".

WEAPON_MARK = "The weapon you stop on is yours"
WEAPONS: dict[str, tuple[str, float]] = {}  # name -> (tier, damage)
for _metal, _tier, _sword, _axe, _pick, _spear in (
        ("Wooden", "common", 4, 7, 2, 1), ("Golden", "common", 4, 7, 2, 1), ("Stone", "common", 5, 9, 3, 2),
        ("Copper", "common", 5, 9, 3, 2), ("Iron", "rare", 6, 9, 4, 3), ("Diamond", "epic", 7, 9, 5, 4),
        ("Netherite", "legendary", 8, 10, 6, 5)):
    WEAPONS[f"{_metal} Sword"] = (_tier, _sword)
    WEAPONS[f"{_metal} Axe"] = (_tier, _axe)
    WEAPONS[f"{_metal} Pickaxe"] = (_tier, _pick)
    WEAPONS[f"{_metal} Spear"] = (_tier, _spear)
WEAPONS.update({"Bow": ("rare", 6), "Trident": ("epic", 9), "Mace": ("legendary", 6)})
BOSSES = {  # name -> (health, armour) in Java Edition: the strongest mobs, one per Short
    "Warden": (500, 0), "Wither": (300, 4), "Ender Dragon": (200, 0), "Elder Guardian": (80, 0),
    "Ravager": (100, 0), "Iron Golem": (100, 0),
}
ARROW_PROOF = {"Wither": "ARROWS BOUNCE OFF"}
WEAPON_SCRIPT = ("{game} fans only. {article} {boss} is coming for you. Pause the video right now. "
                 "The weapon you stop on is yours. How many hits to win? Netherite and the mace are legendary. "
                 "Comment your weapon and your hits. Then send this to a friend, and see who wins the fight.")
assert WEAPON_MARK in WEAPON_SCRIPT
WEAPON_TITLE = "Pause To Get Your Weapon vs The {boss} ⚔️"
WEAPON_END_CARD = "Comment your hits"
WEAPON_PINNED = ("What did you get vs the {boss}? 👇 Weapon + hits ⚔️\n"
                 "Send this to a friend and see who wins the fight.")
WEAPON_DESCRIPTION = ("Pause the video: the {game} weapon you stop on is yours. How many hits to beat the {boss}? "
                      "Comment your hits and challenge a friend.")
WEAPON_HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#minecraftweapons"]
BW, BH = 1000, 1050  # the boss card: the boss on top, the weapon under it
BOSS_CENTER = (WIDTH // 2, 950)
BOSS_CAPTION_MARGIN = 170  # two-line captions clear of the taller card (280 put their top line on the hits)
BOSS_PIC = 300
ICON = 208  # 16 px icons at 13x, whole pixels (224 ran the panel into the hits bar)
BOSS_RED = (150, 20, 20)


def article(boss: str) -> str:
    return "The" if boss == "Ender Dragon" else "An" if boss[0] in "AEIOU" else "A"


def boss_of(script_text: str) -> str:
    """The boss a weapon Short's narration names."""
    m = re.search(r"(?:A|An|The) ([A-Z][A-Za-z ]+?) is coming for you", script_text or "")
    return m.group(1) if m and m.group(1) in BOSSES else "Warden"


def weapon_dir(settings: Settings) -> Path:
    return settings.assets_dir / "weapon"


def fetch_weapons(settings: Settings, session=None) -> list[dict]:
    """Each weapon's 16 px inventory icon ("File:Invicon Wooden Sword.png") into assets/weapon/ with catalog.json."""
    import json

    import requests

    from . import reveal

    session = session or requests.Session()
    session.headers["User-Agent"] = reveal.USER_AGENT
    folder = weapon_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    files = {f"File:Invicon {n}.png": n for n in WEAPONS}
    entries = []
    names = list(files)
    for i in range(0, len(names), 40):
        r = session.get(reveal.WIKI_API, params={"action": "query", "titles": "|".join(names[i:i + 40]),
                                                 "prop": "imageinfo", "iiprop": "url", "format": "json"}, timeout=30)
        r.raise_for_status()
        for page in r.json()["query"]["pages"].values():
            name, info = files.get(page["title"]), page.get("imageinfo")
            if not name or not info:
                logger.warning("No icon for %s", page.get("title"))
                continue
            out = folder / f"{reveal.slug(name)}.png"
            out.write_bytes(session.get(info[0]["url"], timeout=30).content)
            entries.append({"name": name, "kind": "weapon", "file": out.name,
                            "source": info[0]["url"].split("?")[0]})
    entries.sort(key=lambda e: e["name"])
    (folder / "catalog.json").write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
    return entries


def weapon_catalog(settings: Settings) -> dict[str, list[dict]]:
    """{"weapons": [...], "bosses": [...]} with full paths; empty when either side has none."""
    import json

    from . import reveal

    found: dict[str, dict] = {}
    for folder in (reveal.library_dir(settings), weapon_dir(settings)):
        try:
            for e in json.loads((folder / "catalog.json").read_text(encoding="utf-8")):
                found[e["name"]] = {**e, "path": str(folder / e["file"])}
        except (OSError, ValueError):
            continue
    weapons = [found[n] for n in WEAPONS if n in found]
    bosses = [found[n] for n in BOSSES if n in found]
    return {"weapons": weapons, "bosses": bosses} if weapons and bosses else {}


def hits_to_kill(boss: str, weapon: str) -> int | None:
    """Hits for `weapon` to beat `boss` in Java Edition; None when it can't (arrows on a wither)."""
    import math

    if weapon == "Bow" and boss in ARROW_PROOF:
        return None
    health, armour = BOSSES[boss]
    damage = WEAPONS[weapon][1]
    damage *= 1 - min(20, max(armour / 5, armour - 4 * damage / 8)) / 25
    return math.ceil(health / damage - 1e-9)


def weapon_verdict(boss: str, weapon: str) -> tuple[str, tuple[int, int, int]]:
    hits = hits_to_kill(boss, weapon)
    if hits is None:
        return ARROW_PROOF[boss], BOSS_RED
    word = "SHOTS" if weapon == "Bow" else "HITS"
    best = min(hits_to_kill(boss, w) or 10 ** 6 for w in WEAPONS)
    colour = (40, 170, 70) if hits <= best * 1.25 else (230, 140, 20) if hits <= best * 2 else (200, 40, 40)
    return f"{hits} {word} TO WIN", colour


def weapon_deck(found: dict, count: int, rng: random.Random) -> list[dict]:
    """`count` weapons: each tier its share, never the same weapon twice in a row."""
    weapons = found["weapons"]
    per_tier = {t: sum(WEAPONS[w["name"]][0] == t for w in weapons) for t in TIERS}
    weights = [TIERS[WEAPONS[w["name"]][0]][1] / per_tier[WEAPONS[w["name"]][0]] for w in weapons]
    out: list[dict] = []
    for _ in range(count):
        while True:
            pick = rng.choices(weapons, weights)[0]
            if not out or pick["name"] != out[-1]["name"]:
                break
        out.append(pick)
    return out


def boss_card(settings: Settings, boss: dict, weapon: dict, cache: dict):
    """The boss on top (name, picture, health), the weapon under it in its tier's colour, the hits at the bottom."""
    from PIL import Image, ImageDraw

    from .reveal import _font

    tier = WEAPONS[weapon["name"]][0]
    colour = TIERS[tier][0]
    img = Image.new("RGBA", (BW, BH), (24, 24, 30, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, BW - 1, BH - 1], outline=colour, width=FRAME)

    def centred(text, y, size, fill, x0=FRAME, x1=BW - FRAME):
        while size > 30 and d.textlength(text, font=_font(size)) > x1 - x0 - 30:
            size -= 4
        d.text(((x0 + x1 - d.textlength(text, font=_font(size))) / 2, y), text, font=_font(size), fill=fill)

    # the boss
    d.rectangle([FRAME, FRAME, BW - FRAME, FRAME + 92], fill=BOSS_RED)
    centred(f"THE BOSS: {boss['name'].upper()}", FRAME + 10, 58, (255, 255, 255))
    top = FRAME + 112
    d.rounded_rectangle([BW // 2 - 260, top, BW // 2 + 260, top + BOSS_PIC + 30], radius=26, fill=PANEL)
    pic = _fit(boss["path"], BOSS_PIC, cache)
    img.alpha_composite(pic, ((BW - pic.width) // 2, top + 15 + (BOSS_PIC - pic.height) // 2))
    centred(f"{BOSSES[boss['name']][0]} HEALTH", top + BOSS_PIC + 40, 48, (255, 110, 110))
    # the weapon
    y = top + BOSS_PIC + 112
    d.rectangle([FRAME, y, BW - FRAME, y + 76], fill=colour)
    centred(f"YOUR WEAPON: {tier.upper()}", y + 6, 52,
            (20, 20, 20) if tier in ("common", "legendary") else (255, 255, 255))
    y += 92
    d.rounded_rectangle([FRAME + 40, y, FRAME + 40 + ICON + 30, y + ICON + 30], radius=26, fill=PANEL)
    icon = _fit(weapon["path"], ICON, cache)
    img.alpha_composite(icon, (FRAME + 55 + (ICON - icon.width) // 2, y + 15 + (ICON - icon.height) // 2))
    name_x0 = FRAME + 40 + ICON + 50
    words = weapon["name"].upper().split()
    lines = [" ".join(words[:-1]), words[-1]] if len(words) > 1 else words
    for k, line in enumerate(lines):
        centred(line, y + (ICON + 30) / 2 - 62 * len(lines) / 2 + 62 * k - 4, 64, (255, 255, 255), name_x0,
                BW - FRAME - 20)
    # the hits
    text, bar = weapon_verdict(boss["name"], weapon["name"])
    y0 = BH - FRAME - 140
    d.rectangle([FRAME, y0, BW - FRAME, BH - FRAME], fill=bar)
    centred(text, y0 + 28, 76, (255, 255, 255))
    return img
