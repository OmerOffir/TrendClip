"""Dungeon Run: a series of Pause Shorts, one room of a Minecraft dungeon per Short (the owner's idea, 2026-11-10).

Each Short opens on the dungeon's map, a maze of rooms with a question mark on every room still ahead, the rooms
already passed showing what was in them and this Short's room lit up ("YOU ARE HERE"); then the room's pause game:

- a mob (and the final boss): the weapons flick under it, the paused one says how many hits it needs (spin.py's
  boss card and Java numbers);
- a chest: the loot flicks, from rotten flesh (common) to a totem of undying (legendary);
- a wall: the tools flick (pickaxes, axes and shovels in every metal), the paused one says how long it takes to
  break through, by the wiki's breaking formula with the mining levels (only a diamond or netherite pickaxe mines
  obsidian; anything else takes 4 min 10 s and drops nothing).

The map shows while the first sentence ("Minecraft dungeon run, part 3 of 8.") is said, so every part also works
for a viewer who never saw the others. Each part is asked for in the Create tab as "dungeon run part N".
"""

from __future__ import annotations

import math
import random
import re
from pathlib import Path
from types import SimpleNamespace

from . import spin
from .config import Settings
from .video_assembler import Overlay, Word

MARK = "dungeon run, part"
ROOMS = [  # the first dungeon: (kind, what)
    ("mob", "Zombie"), ("chest", "Chest"), ("mob", "Skeleton"), ("wall", "Cobblestone"),
    ("mob", "Enderman"), ("chest", "Chest"), ("wall", "Obsidian"), ("boss", "Wither"),
]
PARTS = len(ROOMS)

LOOT = {  # name -> tier
    "Bread": "common", "Rotten Flesh": "common", "Bone": "common", "Arrow": "common", "Torch": "common",
    "Coal": "common", "Stick": "common",
    "Iron Ingot": "rare", "Gold Ingot": "rare", "Ender Pearl": "rare", "Golden Carrot": "rare", "Shield": "rare",
    "Diamond": "epic", "Golden Apple": "epic", "Emerald": "epic", "Enchanted Book": "epic",
    "Enchanted Golden Apple": "legendary", "Totem of Undying": "legendary", "Netherite Ingot": "legendary",
}
METALS = {  # metal -> (tier, mining level, mining speed): minecraft.wiki/w/Tiers
    "Wooden": ("common", 0, 2), "Golden": ("common", 0, 12), "Stone": ("common", 1, 4), "Copper": ("common", 1, 5),
    "Iron": ("rare", 2, 6), "Diamond": ("epic", 3, 8), "Netherite": ("legendary", 4, 9),
}
TOOLS = {f"{m} {t}": (m, t) for m in METALS for t in ("Pickaxe", "Axe", "Shovel")}
WALLS = {"Cobblestone": (2.0, 0), "Obsidian": (50.0, 3)}  # name -> (hardness, mining level a pickaxe needs)

NARRATION = {
    "mob": ("{game} dungeon run, part {n} of {total}. {article} {what} blocks the way. Pause the video right now. "
            "The weapon you stop on is yours. How many hits to win? Comment your weapon and your hits."),
    "chest": ("{game} dungeon run, part {n} of {total}. You found a chest. Pause the video right now. "
              "The loot you stop on is yours. Keep it for the final boss. Comment what you got."),
    "wall": ("{game} dungeon run, part {n} of {total}. {article} {what} wall blocks the way. Pause the video right "
             "now. The tool you stop on is yours. How long to break through? Comment your tool and your time."),
    "boss": ("{game} dungeon run, part {n} of {total}. The final boss. {article} {what} is waiting. Pause the video "
             "right now. The weapon you stop on is yours. How many hits to win? Got a totem from a chest? You get "
             "one more life. Comment your weapon and your hits."),
}
TITLES = {
    "mob": "Pause To Beat The {what} | Dungeon Run {n}/{total} ⚔️",
    "chest": "Pause To Open The Chest | Dungeon Run {n}/{total} 🎁",
    "wall": "Pause To Break The Wall | Dungeon Run {n}/{total} ⛏️",
    "boss": "Pause To Beat The Final Boss | Dungeon Run {n}/{total} 💀",
}
END_CARDS = {"mob": "Comment your hits", "chest": "Comment your loot", "wall": "Comment your time",
             "boss": "Comment your hits"}
PINNED = "What did you get? 👇\nDungeon Run part {n} of {total}: part {next} is next. Can you reach the boss?"
PINNED_LAST = "What did you get? 👇\nThe end of the Dungeon Run: did you beat the {what}?"
DESCRIPTION = ("A Minecraft dungeon run, one room per Short: part {n} of {total}. Pause the video: what you stop on "
               "is yours. Comment what you got.")
HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#minecraftdungeon"]

# The map card: as big as the boss card, in its place
GRID = {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (2, 1), 5: (1, 1), 6: (0, 1), 7: (0, 2), 8: (1.5, 2)}  # snake
COL_X = (200, 500, 800)
ROW_Y = (300, 570, 840)  # room for YOU ARE HERE over the top row and BOSS under the bottom one
ROOM = 190
BOSS_ROOM = (380, 210)
STUBS = [((500, 300), (500, 170)), ((800, 570), (950, 570)), ((200, 570), (60, 570)), ((500, 570), (500, 720)),
         ((200, 840), (200, 1000))]  # dead ends: it reads as a maze, not a line
GOLD = (255, 196, 0)


def dungeon_dir(settings: Settings) -> Path:
    return settings.assets_dir / "dungeon"


def fetch_library(settings: Settings, session=None) -> list[dict]:
    """The loot and the shovels (the pickaxes and axes are in assets/weapon)."""
    shovels = [t for t, (_, kind) in TOOLS.items() if kind == "Shovel"]
    return spin.fetch_icons(settings, list(LOOT) + shovels, dungeon_dir(settings), session=session)


def pictures(settings: Settings) -> dict[str, dict]:
    """Every picture a dungeon needs, by name, with its full path."""
    import json

    from . import reveal

    found: dict[str, dict] = {}
    for folder in (reveal.library_dir(settings), spin.library_dir(settings), spin.axe_dir(settings),
                   spin.weapon_dir(settings), dungeon_dir(settings)):
        try:
            for e in json.loads((folder / "catalog.json").read_text(encoding="utf-8")):
                found[e["name"]] = {**e, "path": str(folder / e["file"])}
        except (OSError, ValueError):
            continue
    return found


def ready(settings: Settings) -> bool:
    found = pictures(settings)
    needed = {what for _, what in ROOMS} | set(LOOT) | set(TOOLS) | set(spin.WEAPONS)
    return needed <= set(found)


def part_of(text: str) -> int:
    m = re.search(r"part (\d+) of \d+", text or "", re.I)
    return int(m.group(1)) if m else 1


def wanted(notes: str = "") -> int:
    """The part asked for in the Create tab's notes ("dungeon run part 3"), 0 when none."""
    m = re.search(r"dungeon(?: run)?(?: part)? ?(\d+)", (notes or "").lower())
    if m:
        return max(1, min(PARTS, int(m.group(1))))
    return 1 if "dungeon" in (notes or "").lower() else 0


# --------------------------------------------------------------------------- the numbers


def break_seconds(wall: str, tool: str) -> float:
    """Seconds for `tool` to break `wall` in Java Edition (minecraft.wiki/w/Breaking): a pickaxe of a high enough
    mining level mines at its speed and drops the block (/30); any other tool works at 1 and drops nothing (/100)."""
    hardness, level = WALLS[wall]
    metal, kind = TOOLS[tool]
    _, tool_level, speed = METALS[metal]
    right = kind == "Pickaxe" and tool_level >= level
    damage = (speed if right else 1) / hardness / (30 if right else 100)
    return 0.0 if damage >= 1 else math.ceil(1 / damage) / 20


def loot_verdict(item: str) -> tuple[str, tuple[int, int, int], tuple[int, int, int]]:
    """The bar's words, colour and text colour for a loot item."""
    tier = LOOT[item]
    words = {"common": "COMMON LOOT", "rare": "RARE LOOT!", "epic": "EPIC LOOT!", "legendary": "LEGENDARY LOOT!!"}
    colour = {"common": (110, 110, 118)}.get(tier, spin.TIERS[tier][0])
    return words[tier], colour, (20, 20, 20) if tier == "legendary" else (255, 255, 255)


def tier_of(name: str) -> str:
    if name in LOOT:
        return LOOT[name]
    if name in TOOLS:
        return METALS[TOOLS[name][0]][0]
    return spin.WEAPONS[name][0]


# --------------------------------------------------------------------------- the script


def script(settings: Settings, game: str, part: int, *, format: str = "short"):
    from . import quiz

    if not ready(settings):
        raise ValueError("Dungeon pictures missing: run spin.fetch_weapons, spin.fetch_axes and "
                         "dungeon.fetch_library first")
    part = max(1, min(PARTS, part))
    kind, what = ROOMS[part - 1]
    game_name = quiz.game_name(game)
    fill = {"game": game_name, "n": part, "total": PARTS, "what": what.lower() if kind == "wall" else what,
            "article": spin.article(what), "next": part + 1}
    title = TITLES[kind].format(**fill)
    pack = SimpleNamespace(title=title, description=DESCRIPTION.format(**fill), hashtags=HASHTAGS,
                           title_card=f"PART {part} OF {PARTS}")
    pinned = (PINNED_LAST if part == PARTS else PINNED).format(**fill)
    out = quiz._script(settings, game, "builtin", "gamer", format, NARRATION[kind].format(**fill),
                       f"{game_name} dungeon run, part {part} of {PARTS}.", [], "", pinned, pack,
                       on_screen=f"Dungeon run {part}/{PARTS}", card=f"PART {part} OF {PARTS}",
                       end_card=END_CARDS[kind])
    return out.model_copy(update={"spin": True, "reactions": []})


# --------------------------------------------------------------------------- the map


def _texture(d, box, base: tuple, rng: random.Random, cell: int = 22, spread: int = 14) -> None:
    """Minecraft-ish pixel noise: `box` filled with `cell`-px squares around `base`."""
    x0, y0, x1, y1 = (round(v) for v in box)
    for y in range(y0, y1, cell):
        for x in range(x0, x1, cell):
            k = rng.randint(-spread, spread)
            d.rectangle([x, y, min(x + cell, x1) - 1, min(y + cell, y1) - 1],
                        fill=tuple(max(0, min(255, c + k)) for c in base))


def _centre(part: int) -> tuple[float, float]:
    col, row = GRID[part]
    x = COL_X[0] + (COL_X[1] - COL_X[0]) * col
    return x, ROW_Y[row]


def _room_box(part: int) -> tuple[float, float, float, float]:
    x, y = _centre(part)
    w, h = BOSS_ROOM if ROOMS[part - 1][0] == "boss" else (ROOM, ROOM)
    return x - w / 2, y - h / 2, x + w / 2, y + h / 2


def map_card(settings: Settings, part: int, cache: dict | None = None):
    """The dungeon from above: corridors, a question mark on every room still ahead, the rooms passed showing what
    was in them (ticked), this part's room lit gold with YOU ARE HERE, the boss room in red."""
    from PIL import Image, ImageDraw

    from .reveal import _font

    cache = cache if cache is not None else {}
    found = pictures(settings)
    rng = random.Random(f"map-{part}")
    W, H = spin.BW, spin.BH
    img = Image.new("RGBA", (W, H), (30, 30, 36, 255))
    d = ImageDraw.Draw(img)
    _texture(d, (0, 0, W, H), (44, 44, 52), rng)  # deepslate
    d.rectangle([0, 0, W - 1, H - 1], outline=GOLD, width=spin.FRAME)
    d.rectangle([spin.FRAME, spin.FRAME, W - spin.FRAME, spin.FRAME + 92], fill=spin.BOSS_RED)
    head = f"DUNGEON RUN  ·  PART {part}/{PARTS}"
    font = _font(58)
    d.text(((W - d.textlength(head, font=font)) / 2, spin.FRAME + 10), head, font=font, fill=(255, 255, 255))

    floor = (118, 112, 104)
    corridors = [((COL_X[0] - 200, ROW_Y[0]), _centre(1))]  # the way in, from the left edge
    corridors += [(_centre(i), _centre(i + 1)) for i in range(1, PARTS)]
    corridors += STUBS
    for (ax, ay), (bx, by) in corridors:
        half = 34
        box = (min(ax, bx) - half, min(ay, by) - half, max(ax, bx) + half, max(ay, by) + half)
        _texture(d, box, floor, rng, cell=17, spread=10)
    d.text((spin.FRAME + 6, ROW_Y[0] + 40), "START", font=_font(26), fill=(255, 255, 255))  # under the way in

    for n in range(1, PARTS + 1):
        kind, what = ROOMS[n - 1]
        box = _room_box(n)
        boss = kind == "boss"
        _texture(d, box, (128, 128, 134) if n < part else (96, 96, 104), rng, cell=19)
        edge = GOLD if n == part else (200, 40, 40) if boss else (20, 20, 24)
        d.rectangle(box, outline=edge, width=12 if n == part else 8)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if n < part:  # passed: what was in it, and a tick
            entry = found.get(what)
            if entry:
                pic = spin._fit(entry["path"], ROOM - 50, cache)
                img.alpha_composite(pic, (round(cx - pic.width / 2), round(cy - pic.height / 2)))
            d.ellipse([box[2] - 62, box[1] + 10, box[2] - 10, box[1] + 62], fill=(40, 170, 70))
            d.line([(box[2] - 50, box[1] + 36), (box[2] - 40, box[1] + 48), (box[2] - 22, box[1] + 24)],
                   fill=(255, 255, 255), width=8)
            continue
        q = _font(150 if n == part else 120)
        fill = GOLD if n == part else (230, 60, 60) if boss else (255, 255, 255)
        tw = d.textlength("?", font=q)
        d.text((cx - tw / 2 + 5, cy - 100 + 5), "?", font=q, fill=(0, 0, 0))
        d.text((cx - tw / 2, cy - 100), "?", font=q, fill=fill)
        if boss:
            label = _font(40)
            d.text((cx - d.textlength("BOSS", font=label) / 2, box[3] + 6), "BOSS", font=label, fill=(255, 90, 90))
    # YOU ARE HERE over this part's room
    box = _room_box(part)
    cx = (box[0] + box[2]) / 2
    tag = "YOU ARE HERE"
    font = _font(38)
    tw = d.textlength(tag, font=font)
    ty = box[1] - 64
    d.rounded_rectangle([cx - tw / 2 - 16, ty, cx + tw / 2 + 16, ty + 54], radius=14, fill=GOLD)
    d.polygon([(cx - 14, ty + 54), (cx + 14, ty + 54), (cx, ty + 70)], fill=GOLD)
    d.text((cx - tw / 2, ty + 4), tag, font=font, fill=(20, 20, 20))
    return img


# --------------------------------------------------------------------------- the room's cards


def deck(names: list[str], count: int, rng: random.Random) -> list[str]:
    """`count` names: each tier its share of the cards (spin.TIERS), never the same twice in a row."""
    per_tier = {t: sum(tier_of(n) == t for n in names) for t in spin.TIERS}
    weights = [spin.TIERS[tier_of(n)][1] / per_tier[tier_of(n)] for n in names]
    out: list[str] = []
    for _ in range(count):
        while True:
            pick = rng.choices(names, weights)[0]
            if not out or pick != out[-1]:
                break
        out.append(pick)
    return out


def room_card(settings: Settings, part: int, name: str, found: dict, cache: dict):
    kind, what = ROOMS[part - 1]
    top, item = found[what], found[name]
    if kind in ("mob", "boss"):
        return spin.boss_card(settings, top, item, cache, title="FINAL BOSS" if kind == "boss" else "THE MOB")
    if kind == "chest":
        text, bar, ink = loot_verdict(name)
        return spin.stack_card("THE CHEST", top, "WHAT'S INSIDE?", "YOUR LOOT", LOOT[name], item, text, bar, cache,
                               text_fill=ink)
    text, bar = spin.verdict(break_seconds(what, name))
    return spin.stack_card(f"THE WALL: {what.upper()}", top, "BREAK THROUGH!", "YOUR TOOL", tier_of(name), item,
                           text, bar, cache)


def plan(settings: Settings, words: list[Word], script_text: str, seed: str = "", tail: float = 2.0,
         fps: int = 30) -> list[Overlay]:
    """The map while the first sentence is said, then the room's flicking cards to the end."""
    part = part_of(script_text)
    kind, _ = ROOMS[part - 1]
    total = words[-1].end + tail
    i = next((k for k, w in enumerate(words) if w.text.rstrip().endswith((".", "!", "?"))), min(6, len(words) - 1))
    after = words[i + 1].start if i + 1 < len(words) else words[i].end + 0.25  # the map holds through the breath
    switch = round(min(max(after, words[i].end + 0.25), total / 3), 3)
    out_dir = settings.assets_dir / "cache" / "dungeon"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-z0-9]+", "-", (seed or f"part{part}").lower())[:40].strip("-")
    cache: dict = {}
    map_path = out_dir / f"{stem}-map.png"
    map_card(settings, part, cache).save(map_path)

    found = pictures(settings)
    names = list(spin.WEAPONS) if kind in ("mob", "boss") else list(LOOT) if kind == "chest" else list(TOOLS)
    rng = random.Random(seed or f"dungeon-{part}")
    count = int((total - switch) * fps / spin.HOLD) + 2
    cards = [room_card(settings, part, n, found, cache) for n in deck(names, count, rng)]
    path = out_dir / f"{stem}.webp"
    cards[0].save(path, save_all=True, append_images=cards[1:], duration=round(1000 * spin.HOLD / fps), loop=0,
                  lossless=False, quality=90)
    box = max(spin.BW, spin.BH)
    return [Overlay(map_path, 0.0, switch, center=spin.BOSS_CENTER, box=box, style="asis", cut=True),
            Overlay(path, switch, round(total + 0.5, 3), center=spin.BOSS_CENTER, box=box, style="asis", cut=True,
                    animated=True)]
