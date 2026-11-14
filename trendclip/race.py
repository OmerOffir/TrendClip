"""Pause + Race: the pause cards, then the tiers race (the owner, 2026-11-14, after Qunky's "Which Pickaxe is Faster"
at 2.9M views and R4tium's "Which Armor Survives a Cactus" at 493K).

The Short opens on the Block winner's flicking card, pickaxes this time: the one you pause on is yours. At "Now they
race" the card gives way to a sky over grass where the seven pickaxes each sit on a pillar of stone and mine straight
down, one block at a time, at the real Java speed (minecraft.wiki/w/Breaking: speed / hardness / 30 per tick,
rounded up to whole ticks). The pillars finish in true order and a viewer watches their own pickaxe the whole way:
gold beats netherite, wood is still mining when the video ends. The comment is the place they got.
"""

from __future__ import annotations

import math
import random
import re
from pathlib import Path
from types import SimpleNamespace

from .config import Settings
from .video_assembler import Overlay, Word

KIND = "race-"
RACES = {
    "pickaxe": {
        "tool": "pickaxe", "emoji": "⛏️", "folder": "weapon",
        "block": "Stone", "hardness": 1.5, "blocks": 8, "world": "Plains",
        # name -> (label under the pillar, rarity on the pause card, mining speed in Java)
        "items": {"Wooden Pickaxe": ("WOOD", "common", 2), "Stone Pickaxe": ("STONE", "common", 4),
                  "Copper Pickaxe": ("COPPER", "common", 5), "Iron Pickaxe": ("IRON", "rare", 6),
                  "Golden Pickaxe": ("GOLD", "rare", 12), "Diamond Pickaxe": ("DIAMOND", "epic", 8),
                  "Netherite Pickaxe": ("NETHERITE", "legendary", 9)},
        "twist": "Gold beats netherite!",
    },
}
MARK = "you stop on is yours. Now they race"
SCRIPT = ("{game} fans only. Pause the video now. The {tool} you stop on is yours. Now they race down {count} blocks "
          "of {block}. {twist} Did yours win? Comment your place.")
TITLES = ["Pause To Get Your {Tool}... Then It Races {emoji}🏁", "Your {Tool} vs 6 Others: Which Mines Fastest? {emoji}",
          "Pause For A {Tool}, Then Watch It Race {emoji}🏁"]
PINNED = "What place did your {tool} get? 👇 Comment your place 🏁\nSend this to a friend and race them."
DESCRIPTION = ("Pause the video: the {game} {tool} you stop on is yours. Then all seven race down {count} blocks of "
               "{block} at real Java mining speed. Comment your place.")
WORDS = {1: "ONE", 2: "TWO", 3: "THREE", 4: "FOUR", 5: "FIVE", 6: "SIX", 7: "SEVEN", 8: "EIGHT", 9: "NINE", 10: "TEN"}

SIZE = (1040, 1000)  # the race panel: as wide as the screen allows, its bottom where the card's is (1190)
CENTER = (540, 690)
GROUND = 870  # where the pillars stand on the screenshot's grass, in panel pixels
TILE = 64  # one block's front face on screen: 16 texture pixels at 4
TOP = 20  # the pillar's top face, seen from a little above
SIDE = 14  # and its right side
COL = SIZE[0] // 7
PX = 4  # one skin pixel of the player on screen: 32 skin pixels tall = 128, 1.8 blocks
FPS = 30
PLACE_COLOURS = [(255, 196, 0), (215, 220, 230), (214, 140, 70)]
WORLD_FOLDER = "prize-spawn"  # the biome screenshots the spawn Short fetched (real in-game pictures)
TEXTURE_DIR = "race"  # the blocks' flat 16x16 textures from the wiki


def wanted(notes: str = "") -> str:
    low = (notes or "").lower()
    return next((KIND + n for n, r in RACES.items() if f"{r['tool']} race" in low), "")


def of_title(title: str) -> str:
    low = (title or "").lower()
    return next((n for n, r in RACES.items() if r["tool"] in low and ("race" in low or "fastest" in low)), "")


def kind_of(script_text: str) -> str:
    text = script_text or ""
    return next((KIND + n for n, r in RACES.items() if f"The {r['tool']} {MARK}" in text), "")


def catalog(settings: Settings, name: str) -> dict:
    """{"items": [entries in race order, each with path / label / tier / speed], "block": entry}; empty when a
    picture is missing."""
    import json

    race = RACES[name]

    def read(folder: str) -> dict:
        try:
            entries = json.loads((settings.assets_dir / folder / "catalog.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {e["name"]: {**e, "path": str(settings.assets_dir / folder / e["file"])} for e in entries
                if (settings.assets_dir / folder / e["file"]).is_file()}

    have, worlds = read(race["folder"]), read(WORLD_FOLDER)
    texture = texture_path(settings, race["block"])
    if not all(n in have for n in race["items"]) or race["world"] not in worlds or not texture.is_file():
        return {}
    items = [{**have[n], "label": lab, "tier": tier, "speed": speed}
             for n, (lab, tier, speed) in race["items"].items()]
    return {"items": items, "texture": str(texture), "world": worlds[race["world"]]["path"]}


def texture_path(settings: Settings, block: str) -> Path:
    return settings.assets_dir / TEXTURE_DIR / (re.sub(r"[^a-z0-9]+", "-", block.lower()).strip("-") + ".png")


def fetch_texture(settings: Settings, block: str, session=None) -> Path:
    """The block's newest Java texture from the Minecraft Wiki ("Stone (texture) JE5 BE3.png"), 16x16."""
    import io

    import requests
    from PIL import Image

    from .reveal import USER_AGENT, WIKI_API

    path = texture_path(settings, block)
    if path.is_file():
        return path
    session = session or requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    found = session.get(WIKI_API, params={"action": "query", "list": "allimages", "aiprefix": f"{block} (texture)",
                                          "ailimit": 50, "format": "json"}, timeout=30).json()["query"]["allimages"]

    def version(item: dict) -> int:
        m = re.search(r"_JE(\d+)", item["name"])
        return int(m.group(1)) if m and "_pJE" not in item["name"] else -1

    best = max(found, key=version)
    pic = Image.open(io.BytesIO(session.get(best["url"], timeout=30).content)).convert("RGBA")
    path.parent.mkdir(parents=True, exist_ok=True)
    pic.crop((0, 0, 16, 16)).save(path)
    return path


def block_seconds(speed: float, hardness: float) -> float:
    """One block with the right tool in Java: speed / hardness / 30 per tick, whole ticks of 1/20 s."""
    damage = speed / hardness / 30
    return 0.0 if damage >= 1 else math.ceil(1 / damage) / 20


def finish_times(name: str) -> dict[str, float]:
    race = RACES[name]
    return {n: round(block_seconds(speed, race["hardness"]) * race["blocks"], 2)
            for n, (_, _, speed) in race["items"].items()}


def words(name: str, game: str = "Minecraft") -> dict:
    race = RACES[name]
    return {"game": game, "tool": race["tool"], "Tool": race["tool"].title(), "emoji": race["emoji"],
            "count": WORDS.get(race["blocks"], str(race["blocks"])).lower(), "block": race["block"].lower(),
            "twist": race["twist"]}


def script(settings: Settings, game: str, name: str, *, format: str = "short", rng: random.Random | None = None):
    from . import quiz, spin

    if not catalog(settings, name):
        raise ValueError(f"No {name} race pictures yet (assets/{RACES[name]['folder']} and the block)")
    fill = words(name, quiz.game_name(game))
    title = (rng or random.Random()).choice(TITLES).format(**fill)
    pack = SimpleNamespace(title=title, description=DESCRIPTION.format(**fill),
                           hashtags=["#shorts", "#minecraft", "#gaming", "#pausechallenge", f"#minecraft{name}"],
                           title_card=spin.CARD)
    out = quiz._script(settings, game, "builtin", "gamer", format, SCRIPT.format(**fill),
                       spin.HOOK.format(game=fill["game"]), [], "", PINNED.format(**fill), pack,
                       on_screen=f"Pause to get your {fill['tool']}", card=spin.CARD, end_card="Comment your place")
    return out.model_copy(update={"spin": True, "reactions": []})


# --------------------------------------------------------------------------- drawing


def _tint(img, k: float):
    """Darker (k < 1) or lighter (k > 1), alpha kept: the game's flat light on each face of a block."""
    from PIL import Image, ImageEnhance

    out = ImageEnhance.Brightness(img.convert("RGB")).enhance(k).convert("RGBA")
    out.putalpha(img.getchannel("A"))
    return out


def _world(found: dict, cache: dict):
    """The real in-game screenshot (Plains), cropped from its middle to the panel."""
    from PIL import Image

    if "world" not in cache:
        pic = Image.open(found["world"]).convert("RGBA")
        w = round(pic.height * SIZE[0] / SIZE[1])
        x = (pic.width - w) // 2
        cache["world"] = pic.crop((x, 0, x + w, pic.height)).resize(SIZE, Image.LANCZOS)
    return cache["world"]


def _crack_order(seed: int = 11) -> list[tuple[int, int]]:
    """The texture pixels the crack takes, in order: a few jagged lines spreading from the middle and branching,
    like the game's destroy stages (no wiki file for those)."""
    rng = random.Random(seed)
    out: list[tuple[int, int]] = []
    heads = [(7, 7), (8, 8)]
    while len(out) < 70 and heads:
        x, y = heads.pop(0)
        dx, dy = rng.choice([(1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (0, 1), (-1, 0), (0, -1)])
        for _ in range(rng.randint(3, 6)):
            x, y = x + dx, y + dy
            if not (0 <= x < 16 and 0 <= y < 16):
                break
            if (x, y) not in out:
                out.append((x, y))
            if rng.random() < 0.4:  # a jag
                dx, dy = (dx, 0) if dy and rng.random() < 0.5 else (0, dy) if dx and dy else (dx, dy)
                dx, dy = dx or rng.choice([-1, 1]) * (rng.random() < 0.5), dy or rng.choice([-1, 1])
        heads += [(x, y)] * (2 if rng.random() < 0.5 else 1) if 0 <= x < 16 and 0 <= y < 16 else []
        heads += [(rng.randint(5, 10), rng.randint(5, 10))] if not heads else []
    return out


def _faces(found: dict, stage: int, cache: dict):
    """(front, top, side) of one block: the 16x16 texture at TILE, cracked to `stage` (-1 = whole, 9 = nearly
    gone), its top lighter and sheared back, its right side darker and sheared up."""
    from PIL import Image

    key = ("faces", stage)
    if key not in cache:
        tex = Image.open(found["texture"]).convert("RGBA").crop((0, 0, 16, 16))
        if stage >= 0:
            order = cache.setdefault("cracks", _crack_order())
            px = tex.load()
            for x, y in order[:round(len(order) * (stage + 1) / 10)]:
                r, g, b, a = px[x, y]
                px[x, y] = (r * 30 // 100, g * 30 // 100, b * 30 // 100, a)
        front = _tint(tex.resize((TILE, TILE), Image.NEAREST), 0.92)
        flat = _tint(tex.resize((TILE, TOP), Image.NEAREST), 1.12)
        top = flat.transform((TILE + SIDE, TOP), Image.AFFINE, (1, SIDE / TOP, -SIDE, 0, 1, 0), Image.NEAREST)
        thin = _tint(tex.resize((SIDE, TILE), Image.NEAREST), 0.62)
        side = thin.transform((SIDE, TILE + TOP), Image.AFFINE, (1, 0, 0, TOP / SIDE, 1, -TOP), Image.NEAREST)
        cache[key] = (front, top, side)
    return cache[key]


def _pillar(img, found: dict, x0: int, left: int, stage: int, cache: dict, drop: float = 0.0) -> float:
    """`left` blocks standing on the grass at x0, the top one cracked to `stage`; returns where the feet go."""
    from PIL import ImageDraw

    shadow = ImageDraw.Draw(img, "RGBA")
    shadow.ellipse([x0 - 10, GROUND - 10, x0 + TILE + SIDE + 14, GROUND + 12], fill=(0, 0, 0, 80))
    for k in range(left):
        front, top, side = _faces(found, stage if k == left - 1 else -1, cache)
        y = GROUND - (k + 1) * TILE
        img.alpha_composite(side, (x0 + TILE, y - TOP))
        img.alpha_composite(front, (x0, y))
    top_y = GROUND - left * TILE
    if left:
        img.alpha_composite(_faces(found, -1, cache)[1], (x0, top_y - TOP))
    return top_y - (TOP / 2 if left else 0) - drop


# The player: our hero in Minecraft's own proportions (head 8, body 8x12, arms and legs 4x12 skin pixels), seen from
# the front like the players on the pillars in the game. An orange hoodie, not Steve.
HAIR, SKIN, EYE, PUPIL, MOUTH = (70, 44, 24), (224, 172, 132), (245, 245, 245), (58, 44, 120), (160, 96, 74)
HOOD, HOOD_DARK, JEANS, SHOE = (236, 120, 36), (192, 88, 24), (50, 64, 146), (52, 52, 60)
FACE = ["HHHHHHHH", "HHHHHHHH", "HSSSSSSH", "SSSSSSSS", "SEPSSPES", "SSSNNSSS", "SSMMMMSS", "SSSSSSSS"]


def _skin_part(rows: list[str], colours: dict, seed: int):
    """One part from its pixel rows, each pixel a touch lighter or darker like a real skin."""
    from PIL import Image

    rng = random.Random(seed)
    im = Image.new("RGBA", (len(rows[0]), len(rows)))
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            c = colours[ch]
            k = rng.uniform(0.93, 1.06)
            im.putpixel((x, y), tuple(max(0, min(255, int(v * k))) for v in c) + (255,))
    return im


def _player_parts(cache: dict):
    """(body without the mining arm, the arm) at PX, outlined so they read against the world."""
    from PIL import Image, ImageFilter

    if "player" not in cache:
        shade = tuple(int(v * 0.86) for v in SKIN)
        head = _skin_part(FACE, {"H": HAIR, "S": SKIN, "E": EYE, "P": PUPIL, "N": shade, "M": MOUTH}, 1)
        body = _skin_part(["DDDDDDDD"] + ["OOOZZOOO"] * 6 + ["ODDZZDDO", "ODDZZDDO"] + ["OOOZZOOO"] * 2 + ["DDDDDDDD"],
                          {"O": HOOD, "D": HOOD_DARK, "Z": tuple(int(v * 0.8) for v in HOOD)}, 2)
        arm = _skin_part(["DOOO"] * 9 + ["sSSS"] * 3, {"O": HOOD, "D": HOOD_DARK, "S": SKIN, "s": shade}, 3)
        leg = _skin_part(["dJJJ"] * 10 + ["KKKK"] * 2,
                         {"J": JEANS, "d": tuple(int(v * 0.82) for v in JEANS), "K": SHOE}, 4)
        figure = Image.new("RGBA", (16, 32))
        figure.paste(head, (4, 0))
        figure.paste(body, (4, 8))
        figure.paste(arm.transpose(Image.FLIP_LEFT_RIGHT), (12, 8))  # his left arm, on our right, hangs
        figure.paste(leg, (4, 20))
        figure.paste(leg.transpose(Image.FLIP_LEFT_RIGHT), (8, 20))

        def outlined(im):
            big = im.resize((im.width * PX, im.height * PX), Image.NEAREST)
            pad = Image.new("RGBA", (big.width + 6, big.height + 6))
            pad.paste(big, (3, 3))
            out = Image.new("RGBA", pad.size, (16, 16, 22, 0))
            out.putalpha(pad.getchannel("A").filter(ImageFilter.MaxFilter(5)))
            out.alpha_composite(pad)
            return out

        cache["player"] = (outlined(figure), outlined(arm))
    return cache["player"]


def _arm_with_tool(tool_path: str, angle: float, cache: dict):
    """His right arm (our left) holding the tool, turned `angle` degrees about the shoulder (0 hangs down, -90
    points out to our left, -160 is over his head); the picture's middle is the shoulder."""
    from PIL import Image

    key = ("arm", tool_path, round(angle))
    if key not in cache:
        arm = _player_parts(cache)[1]
        tool = Image.open(tool_path).convert("RGBA").resize((14 * PX, 14 * PX), Image.NEAREST)
        tool = tool.rotate(-135, resample=Image.NEAREST, expand=True)  # handle at the hand, the head out past it
        side = 2 * (arm.height + tool.height)
        canvas = Image.new("RGBA", (side, side))
        c = side // 2
        hand = c + arm.height - 3 * PX
        canvas.alpha_composite(tool, (c - tool.width // 2, hand - tool.height // 4))
        canvas.alpha_composite(arm, (c - arm.width // 2, c - 2 * PX))
        cache[key] = canvas.rotate(angle, resample=Image.NEAREST)
    return cache[key]


def _player(img, found: dict, entry: dict, feet: tuple[float, float], angle: float, cache: dict,
            cheer: float = 0.0) -> None:
    figure = _player_parts(cache)[0]
    x, y = int(feet[0] - figure.width / 2), int(feet[1] - figure.height + 3 - cheer)
    shoulder = (x + 3 + 2 * PX, y + 3 + 9 * PX)
    arm = _arm_with_tool(entry["path"], angle, cache)
    img.alpha_composite(figure, (x, y))
    img.alpha_composite(arm, (int(shoulder[0] - arm.width / 2), int(shoulder[1] - arm.height / 2)))


SWING = 0.3  # one swing of the arm in the game: 6 ticks


def frame(found: dict, name: str, t: float, cache: dict):
    """The race at `t` seconds after it starts."""
    from PIL import Image, ImageDraw

    from .reveal import _font

    race = RACES[name]
    times = finish_times(name)
    per = {e["name"]: block_seconds(e["speed"], race["hardness"]) for e in found["items"]}
    order = sorted(times, key=times.get)
    img = _world(found, cache).copy()
    d = ImageDraw.Draw(img)
    done = [n for n in order if times[n] <= t]
    if done:
        head, colour = f"{race['items'][order[0]][0]} IS FASTEST!", PLACE_COLOURS[0]
    else:
        head, colour = f"WHICH {race['tool'].upper()} IS FASTEST?", (255, 255, 255)
    size = 60
    while d.textlength(head, font=_font(size)) > SIZE[0] - 60:
        size -= 2
    d.text((SIZE[0] / 2, 54), head, font=_font(size), fill=colour, anchor="mm", stroke_width=6,
           stroke_fill=(16, 16, 22))
    d.text((SIZE[0] / 2, 120), f"{min(t, 99):.1f}s", font=_font(50), fill=(255, 214, 0), anchor="mm",
           stroke_width=5, stroke_fill=(16, 16, 22))
    for i, e in enumerate(found["items"]):
        n = e["name"]
        cx = COL * i + COL // 2
        x0 = cx - (TILE + SIDE) // 2
        mined = min(race["blocks"], int(t / per[n] + 1e-9))
        left = race["blocks"] - mined
        into = t - mined * per[n]
        stage = min(9, int(into / per[n] * 10)) if left and into > 0.04 else -1
        fall = max(0.0, t - mined * per[n]) / 0.07  # the block under him gone: he drops, faster and faster
        drop = TILE * (1 - fall ** 2) if 0 < mined and fall < 1 else 0
        feet_y = _pillar(img, found, x0, left, stage, cache)
        feet = (x0 + TILE / 2 + SIDE / 2, feet_y - drop)
        place = order.index(n)
        if left:
            phase = (t % SWING) / SWING
            angle = -150 + 125 * phase ** 2  # up, then a fast strike down at the block by his feet
            _player(img, found, e, feet, angle, cache)
            if phase > 0.7 and into > 0.04:  # chips off the block on each hit
                rng = random.Random(int(t * FPS) * 13 + i)
                tex = cache.setdefault("tex", Image.open(found["texture"]).convert("RGB"))
                for _ in range(4):
                    px, py = feet[0] - 30 + rng.randint(-24, 24), feet[1] - rng.randint(0, 34)
                    c = tex.getpixel((rng.randrange(16), rng.randrange(16)))
                    d.rectangle([px, py, px + 2 * PX, py + 2 * PX], fill=c)
            d.text((cx, GROUND + 78), "MINING..." if t > 0.6 else "", font=_font(26), fill=(255, 255, 255),
                   anchor="mm", stroke_width=4, stroke_fill=(16, 16, 22))
        else:
            hop = 10 * abs(math.sin((t - times[n]) * 8)) if place < 3 else 0  # the podium jumps
            _player(img, found, e, feet, -170 if place < 3 else -20, cache, cheer=hop)
            fill = PLACE_COLOURS[place] if place < 3 else (255, 255, 255)
            label = ["1ST", "2ND", "3RD"][place] if place < 3 else f"{place + 1}TH"
            d.text((cx, GROUND - 200), label, font=_font(48), fill=fill, anchor="mm", stroke_width=6,
                   stroke_fill=(16, 16, 22))
            d.text((cx, GROUND + 78), f"{times[n]:.1f}s", font=_font(40), fill=fill, anchor="mm", stroke_width=4,
                   stroke_fill=(16, 16, 22))
        label = e["label"]
        size = 34
        while size > 18 and d.textlength(label, font=_font(size)) > COL - 8:
            size -= 2
        d.text((cx, GROUND + 34), label, font=_font(size), fill=(255, 255, 255), anchor="mm", stroke_width=4,
               stroke_fill=(16, 16, 22))
    if t < 0.6:  # GO! over the start
        d.text((SIZE[0] / 2, 470), "GO!", font=_font(190), fill=(255, 214, 0), anchor="mm", stroke_width=12,
               stroke_fill=(16, 16, 22))
    mask = Image.new("L", SIZE, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE[0] - 1, SIZE[1] - 1], radius=36, fill=255)
    out = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    ImageDraw.Draw(out).rounded_rectangle([0, 0, SIZE[0] - 1, SIZE[1] - 1], radius=36, outline=(16, 16, 22),
                                          width=10)
    return out


def start_time(words: list[Word]) -> float:
    """The race starts on the narration's "Now" (after "yours"), or 40% of the way when it isn't there."""
    seen = False
    for w in words:
        clean = re.sub(r"[^a-z]", "", w.text.lower())
        if clean == "yours":
            seen = True
        elif seen and clean == "now":
            return w.start
    return round(words[-1].end * 0.4, 2) if words else 0.0


def plan(settings: Settings, words: list[Word], name: str, seed: str = "", tail: float = 2.0,
         fps: int = 30) -> list[Overlay]:
    """Two overlays: the pause cards from frame 0 to "Now", then the race to the end."""
    from . import spin

    if not words:
        return []
    found = catalog(settings, name)
    rng = random.Random(seed or "race")
    total = words[-1].end + tail
    start = start_time(words)
    cache: dict = {}
    count = max(2, int(start * fps / spin.HOLD) + 1)
    picked = spin.deck(found["items"], count, rng)
    cards = [spin.card(settings, e, cache) for e in picked]
    out_dir = settings.assets_dir / "cache" / "spin"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-z0-9]+", "-", (seed or "race").lower())[:40].strip("-")
    path = out_dir / f"{stem}-cards.webp"
    cards[0].save(path, save_all=True, append_images=cards[1:], duration=round(1000 * spin.HOLD / fps), loop=0,
                  lossless=False, quality=90)
    length = max(0.5, total + 0.5 - start)
    drawn: dict = {}
    pics = [frame(found, name, i / FPS, drawn) for i in range(int(length * FPS) + 1)]
    race_path = out_dir / f"{stem}-race.webp"
    pics[0].save(race_path, save_all=True, append_images=pics[1:], duration=round(1000 / FPS), loop=1,
                 lossless=False, quality=88)
    return [Overlay(path, 0.0, round(start, 3), center=spin.CENTER, box=max(cards[0].size), style="asis",
                    cut=True, animated=True),
            Overlay(race_path, round(start, 3), round(total + 0.5, 3), center=CENTER, box=max(SIZE), style="asis",
                    cut=True, animated=True)]
