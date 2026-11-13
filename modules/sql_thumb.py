"""SideQuestLogic action thumbnails: the Short's moment drawn as a scene, not just its title card (the owner,
2026-11-11: "if we fight something, show it in a cool way; if we break a wall, make it cooler").

The scene is read off the Short's title (plan()):
  - fight   the mob huge with Minecraft's boss bar over it, flashing red as a giant pixel weapon hits it
            (Pause to Get Your Weapon, the dungeon's mobs and boss, the quest's dragon / first night / blaze)
  - wall    a wall of pixel blocks cracking open, chunks flying, light through the hole, a giant axe or pickaxe
            mid-swing (Pause to Get Your Axe, the dungeon's wall, the quest's cave)
  - chest   the chest glowing with gold rays and the loot flying out (the dungeon's chest)
  - blocks  the legendary block shining in the middle, the other rarities tumbling around it (Get Your Block)
  - lava    a lava lake with the hearts over it (the quest's lava stop)
Everything else (the question levels) keeps the title-card look (modules.thumbs.sidequest).

The pictures are the ones the Shorts already use: mob renders in assets/reveal, block renders in assets/spin, 16 px
weapon icons in assets/weapon, axes and the chest in assets/axe. The block textures of the wall and the lava are
drawn here, 16 px like the game's and scaled with hard pixels.
"""

from __future__ import annotations

import math
import random
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

W, H = 1080, 1920
YELLOW, RED, WHITE, BLACK = (255, 214, 0), (226, 38, 38), (255, 255, 255), (0, 0, 0)
ASSETS = Path(__file__).resolve().parents[1] / "assets"
FOLDERS = ("reveal", "weapon", "axe", "spin", "dungeon", "prize-armor", "prize-food", "prize-potion", "prize-death",
           "prize-spawn", "prize-base")
DUNGEON_BOSS = "Wither"  # trendclip.dungeon's final room


# --------------------------------------------------------------------------- what to draw


def plan(info: dict) -> dict | None:
    """The scene for a Short (its saved JSON), or None for the title-card look. A drawn Short's rule, the
    "(Hardcore)" at the end of its title, goes on top instead of PAUSE NOW!."""
    title = re.sub(r"[^\x00-\x7f]+", "", str(info.get("title") or "")).strip()
    rule = re.search(r"\(([^)]+)\)$", title)
    scene = _plan({**info, "title": title[:rule.start()].strip() if rule else title})
    if scene and rule:
        scene["top"] = rule.group(1).upper() + "!"
    return scene


def _plan(info: dict) -> dict | None:
    title = str(info.get("title") or "")
    script = str(info.get("script") or "")
    start = re.match(r"(Ten|Five|Three) hearts", script)  # a drawn Short says what it starts with
    quest = bool(start) or "Pause Edition" in title
    hearts = {"Ten": 10, "Five": 5, "Three": 3}[start.group(1)] if start else 10 if quest else 0
    low = title.lower()
    if m := re.search(r"Weapon vs The ([A-Za-z ]+)", title):
        return fight(m.group(1).strip(), "HOW MANY HITS?", "Netherite Sword")
    if m := re.search(r"Your Mob Beat The ([A-Za-z ]+)", title):  # the drawn mob Short: ten hearts
        ally = "Iron Golem" if m.group(1).strip() == "Wolf" else "Wolf"
        return {**fight(m.group(1).strip(), "CAN YOUR MOB WIN?", ally, hearts=10), "ally": True}
    if m := re.search(r"Your Mob vs The ([A-Za-z ]+)", title):  # your mob (a wolf here) bites the boss
        ally = "Iron Golem" if m.group(1).strip() == "Wolf" else "Wolf"
        return {**fight(m.group(1).strip(), "CAN YOUR MOB WIN?", ally), "ally": True}
    if "fight your friend" in low:
        return {"kind": "duel", "text": "WHO WINS?"}
    if m := re.search(r"Pause Twice To Beat The ([A-Za-z ]+)", title):
        return fight(m.group(1).strip(), "CAN YOU SURVIVE?", "Diamond Sword", hearts=5, top="PAUSE TWICE!")
    if "final boss" in low:
        m = re.search(r"(?:A|An|The) ([A-Z][A-Za-z ]+?) is waiting", script)
        return fight(m.group(1) if m else DUNGEON_BOSS, "FINAL BOSS", "Diamond Sword", top="CAN YOU WIN?")
    if "can you beat minecraft" in low:
        return fight("Ender Dragon", "CAN YOU BEAT MINECRAFT?", "Diamond Sword", hearts=hearts, top="PAUSE EDITION")
    if "first night" in low:
        return fight("Zombie", "SURVIVE THE NIGHT?", "Stone Sword", hearts=hearts, night=True)
    if "block the blaze" in low:
        return fight("Blaze", "CAN YOU BLOCK IT?", "Iron Sword", hearts=hearts)
    if m := re.search(r"(?:Beat|Fight) The ([A-Za-z ]+?)(?: \||$)", title):
        return fight(m.group(1).strip(), "CAN YOU WIN?", "Diamond Sword", hearts=hearts)
    if m := re.search(r"Break The ([A-Za-z ]+) Wall", title):  # the drawn axe Short: its own block
        return {"kind": "wall", "block": m.group(1).strip().lower().replace(" ", "-"), "tool": "Golden Axe",
                "text": "CAN YOU BREAK IT?", "hearts": hearts}
    if "dig out" in low:
        return {"kind": "wall", "block": "stone", "tool": "Diamond Pickaxe", "text": "CAN YOU DIG OUT?",
                "hearts": hearts}
    if "axe" in low:
        return {"kind": "wall", "block": "obsidian", "tool": "Netherite Axe", "text": "CAN YOU BREAK IT?", "hearts": 0}
    if "break the wall" in low:
        return {"kind": "wall", "block": "cobble" if "cobblestone" in script.lower() else "obsidian", "tool": "Diamond Pickaxe", "text": "CAN YOU BREAK IT?",
                "hearts": 0}
    if "open the chest" in low:
        return {"kind": "chest", "text": "WHAT'S INSIDE?"}
    if re.search(r"get your .*block", low):
        return {"kind": "blocks", "text": "WHAT DID YOU GET?"}
    from trendclip import spin

    if noun := spin.prize_of_title(title):  # Pause To Get Your Pet / See How You Die ...: the set's legendary
        prize = spin.PRIZES[noun]
        return {"kind": "blocks", "text": spin.prize_words(noun)["ask"], "hero": prize["hero"],
                "around": prize["around"], "set": noun}
    if "lava" in low:
        return {"kind": "lava", "text": "CROSS THE LAVA?", "hearts": hearts}
    return None


def fight(mob: str, text: str, weapon: str, hearts: int = 0, night: bool = False, top: str = "PAUSE NOW!") -> dict:
    return {"kind": "fight", "mob": mob, "text": text, "weapon": weapon, "hearts": hearts, "night": night, "top": top}


def picture(name: str) -> Image.Image | None:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    for folder in FOLDERS:
        path = ASSETS / folder / f"{slug}.png"
        if path.is_file():
            return Image.open(path).convert("RGBA")
    return None


def available(scene: dict | None) -> bool:
    """Whether the pictures the scene needs are on this machine."""
    if not scene:
        return False
    if scene["kind"] == "fight":
        return picture(scene["mob"]) is not None and picture(scene["weapon"]) is not None
    if scene["kind"] == "wall":
        return picture(scene["tool"]) is not None
    if scene["kind"] == "chest":
        return picture("Chest") is not None
    if scene["kind"] == "duel":
        return picture("Diamond Sword") is not None and picture("Netherite Axe") is not None
    if scene["kind"] == "blocks":
        return picture(scene.get("hero", "Netherite Block")) is not None
    return True


# --------------------------------------------------------------------------- helpers


def font(size: int):
    from modules import bw_render

    return bw_render.font(size)


def scaled(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """`img` fitted into the box; tiny pixel icons grow with hard pixels."""
    k = min(box_w / img.width, box_h / img.height)
    size = (max(1, round(img.width * k)), max(1, round(img.height * k)))
    return img.resize(size, Image.NEAREST if max(img.size) <= 64 else Image.LANCZOS)


def glow(sprite: Image.Image, colour: tuple, radius: int, strength: float = 1.0) -> Image.Image:
    """A soft coloured halo the sprite's shape, to put under it."""
    pad = radius * 3
    alpha = Image.new("L", (sprite.width + 2 * pad, sprite.height + 2 * pad), 0)
    alpha.paste(sprite.getchannel("A"), (pad, pad))
    alpha = alpha.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(radius))
    alpha = alpha.point(lambda a: min(255, int(a * strength)))
    out = Image.new("RGBA", alpha.size, colour + (0,))
    out.putalpha(alpha)
    return out


def paste_glowing(img: Image.Image, sprite: Image.Image, at: tuple[int, int], colour: tuple, radius: int = 26,
                  strength: float = 1.4) -> None:
    halo = glow(sprite, colour, radius, strength)
    pad = radius * 3
    img.alpha_composite(halo, (at[0] - pad, at[1] - pad))
    img.alpha_composite(sprite, at)


def outline(sprite: Image.Image, colour=BLACK, width: int = 8) -> Image.Image:
    """The sprite with a hard outline (reads at phone size)."""
    out = Image.new("RGBA", (sprite.width + 2 * width, sprite.height + 2 * width), (0, 0, 0, 0))
    a = Image.new("L", out.size, 0)
    a.paste(sprite.getchannel("A"), (width, width))
    a = a.filter(ImageFilter.MaxFilter(2 * width + 1)).point(lambda v: 255 if v > 40 else 0)
    solid = Image.new("RGBA", out.size, colour + (255,))
    solid.putalpha(a)
    out.alpha_composite(solid)
    out.alpha_composite(sprite, (width, width))
    return out


def hurt(sprite: Image.Image, amount: float = 0.2) -> Image.Image:
    """Minecraft's hit flash: the mob tinted red."""
    red = Image.new("RGBA", sprite.size, (255, 30, 30, 255))
    mixed = Image.blend(sprite, red, amount)
    mixed.putalpha(sprite.getchannel("A"))
    return mixed


def backdrop(base: Image.Image, tint: tuple, focus: tuple[int, int], night: bool = False) -> Image.Image:
    """The gameplay frame blurred and darkened, a coloured light from `focus`, speed lines out of it."""
    img = base.filter(ImageFilter.GaussianBlur(7))
    img = ImageEnhance.Brightness(img).enhance(0.38 if night else 0.55).convert("RGBA")
    if night:
        img.alpha_composite(Image.new("RGBA", (W, H), (10, 20, 70, 110)))
    light = Image.new("L", (W, H), 0)
    ImageDraw.Draw(light).ellipse([focus[0] - 620, focus[1] - 620, focus[0] + 620, focus[1] + 620], fill=170)
    light = light.filter(ImageFilter.GaussianBlur(160))
    layer = Image.new("RGBA", (W, H), tint + (0,))
    layer.putalpha(light)
    img.alpha_composite(layer)
    lines = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lines)
    rng = random.Random(7)
    for k in range(64):
        a = k / 64 * 2 * math.pi + rng.uniform(-0.03, 0.03)
        r0, r1 = rng.uniform(520, 760), 2400
        width = rng.choice((3, 5, 8))
        d.line([(focus[0] + r0 * math.cos(a), focus[1] + r0 * math.sin(a)),
                (focus[0] + r1 * math.cos(a), focus[1] + r1 * math.sin(a))], fill=(255, 255, 255, 70), width=width)
    img.alpha_composite(lines.filter(ImageFilter.GaussianBlur(1.5)))
    # dark edges keep the words readable
    edge = Image.new("L", (W, H), 0)
    de = ImageDraw.Draw(edge)
    for y in range(0, H, 4):
        t = max(0.0, 1 - y / 520) + max(0.0, 1 - (H - y) / 520)
        de.rectangle([0, y, W, y + 4], fill=int(200 * min(1.0, t)))
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shade.putalpha(edge)
    img.alpha_composite(shade)
    return img


def burst(img: Image.Image, centre: tuple[int, int], r: int, inner=YELLOW, outer=WHITE, spikes: int = 14,
          seed: int = 3) -> None:
    """A comic hit star."""
    rng = random.Random(seed)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for colour, k in ((outer, 1.0), (inner, 0.62)):
        pts = []
        for i in range(spikes * 2):
            a = i / (spikes * 2) * 2 * math.pi
            rr = r * k * (1 if i % 2 == 0 else rng.uniform(0.38, 0.5))
            pts.append((centre[0] + rr * math.cos(a), centre[1] + rr * math.sin(a)))
        d.polygon(pts, fill=colour + (255,))
    halo = layer.filter(ImageFilter.GaussianBlur(28))
    img.alpha_composite(halo)
    img.alpha_composite(layer)


def swing_trail(img: Image.Image, centre: tuple[int, int], radius: int, start: float, end: float) -> None:
    """White arcs behind a swing."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for k, (width, alpha) in enumerate(((46, 90), (26, 150), (10, 230))):
        rr = radius - k * 34
        d.arc([centre[0] - rr, centre[1] - rr, centre[0] + rr, centre[1] + rr], start, end,
              fill=(255, 255, 255, alpha), width=width)
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(3)))


def headline(img: Image.Image, text: str, y: int | None, size: int = 150, fill=WHITE, box=None, lines: int = 2,
             bottom: int = H - 250) -> int:
    """Big words centred at `y` (white with a black stroke, or black on a `box` colour), or ending at `bottom`
    when `y` is None; returns the bottom."""
    from modules import bw_render

    while size > 70 and (len(bw_render.wrap(text, size, W - 120)) > lines
                         or max(font(size).getlength(w) for w in text.split()) > W - 120):
        size -= 6
    if y is None:
        y = bottom - int(size * 1.04) * len(bw_render.wrap(text, size, W - 120))
    f = font(size)
    d = ImageDraw.Draw(img)
    for line in bw_render.wrap(text, size, W - 120):
        if box:
            bb = d.textbbox((W / 2, y), line, font=f, anchor="ma")
            d.rectangle((bb[0] - 34, bb[1] - 22, bb[2] + 34, bb[3] + 22), fill=box)
            d.text((W / 2, y), line, font=f, fill=BLACK, anchor="ma")
            y = bb[3] + 38
        else:
            d.text((W / 2, y), line, font=f, fill=fill, stroke_width=max(8, size // 12), stroke_fill=BLACK, anchor="ma")
            y += int(size * 1.04)
    return y


def boss_bar(img: Image.Image, name: str, y: int, colour=(232, 58, 214), left: float = 0.72) -> None:
    """Minecraft's boss bar: the name over a notched bar."""
    d = ImageDraw.Draw(img)
    f = font(64)
    d.text((W / 2, y), name.upper(), font=f, fill=WHITE, stroke_width=6, stroke_fill=BLACK, anchor="ma")
    bw, bh = 840, 34
    x0, y0 = (W - bw) // 2, y + 84
    d.rectangle([x0 - 6, y0 - 6, x0 + bw + 6, y0 + bh + 6], fill=BLACK)
    d.rectangle([x0, y0, x0 + bw, y0 + bh], fill=tuple(c // 3 for c in colour))
    d.rectangle([x0, y0, x0 + int(bw * left), y0 + bh], fill=colour)
    d.rectangle([x0, y0, x0 + int(bw * left), y0 + 8], fill=tuple(min(255, c + 70) for c in colour))
    for k in range(1, 20):
        x = x0 + bw * k // 20
        d.line([(x, y0 + bh - 12), (x, y0 + bh)], fill=BLACK, width=4)


def hearts_row(img: Image.Image, y: int, count: int = 10) -> None:
    """Ten pixel hearts, the quest's HUD."""
    from trendclip import quest

    d = ImageDraw.Draw(img)
    cell = 11
    step = 7 * cell + 14
    x = (W - step * count + 14) // 2
    for k in range(count):
        quest._heart(d, x + k * step, y, cell, (230, 30, 40))


def badge(img: Image.Image, y: int = H - 210) -> None:
    d = ImageDraw.Draw(img)
    f = font(54)
    w = f.getlength("GAMER IQ")
    d.rounded_rectangle(((W - w) / 2 - 34, y, (W + w) / 2 + 34, y + 84), radius=42, fill=(12, 12, 14, 235))
    d.text((W / 2, y + 42), "GAMER IQ", font=f, fill=YELLOW, anchor="mm")


def texture(kind: str, seed: int) -> Image.Image:
    """A 16 px block face like the game's, for the wall and the lava."""
    if kind != "obsidian":  # the quest's own tiles (trendclip.quest), so the thumbnail matches the video
        from trendclip import quest

        return quest._tile(kind, 41 + seed % 9).convert("RGB")
    rng = random.Random(seed)
    tex = Image.new("RGB", (16, 16))
    px = tex.load()
    for y in range(16):
        for x in range(16):
            px[x, y] = rng.choices([(20, 16, 32), (30, 22, 48), (14, 10, 22), (70, 46, 110), (44, 30, 70)],
                                   (30, 26, 22, 8, 14))[0]
    return tex


def sky() -> Image.Image:
    """A Minecraft night sky with square stars, for a Short with no gameplay behind it."""
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(0, H, 8):
        t = y / H
        d.rectangle([0, y, W, y + 8], fill=(int(30 + 30 * t), int(36 + 10 * t), int(90 - 60 * t)))
    rng = random.Random(1)
    for _ in range(90):
        x, y, s = rng.randrange(W), rng.randrange(H), rng.choice((6, 8, 12))
        d.rectangle([x, y, x + s, y + s], fill=(230, 230, 255))
    return img


# --------------------------------------------------------------------------- the scenes


def draw(scene: dict, base: Image.Image) -> Image.Image:
    return {"fight": draw_fight, "wall": draw_wall, "chest": draw_chest, "blocks": draw_blocks,
            "lava": draw_lava, "duel": draw_duel}[scene["kind"]](scene, base)


def draw_fight(scene: dict, base: Image.Image) -> Image.Image:
    mob = picture(scene["mob"])
    weapon = picture(scene["weapon"])
    wide = mob.width > mob.height * 1.2
    sprite = scaled(mob, 1080 if wide else 780, 700 if wide else 860)
    top = 500 if not wide else 600
    at = ((W - sprite.width) // 2 + (0 if wide else 130), top)
    hit = (at[0] + int(sprite.width * (0.3 if wide else 0.22)), at[1] + int(sprite.height * 0.55))
    img = backdrop(base, (255, 40, 30), hit, scene.get("night", False))
    paste_glowing(img, outline(hurt(sprite), (40, 0, 0), 6), (at[0] - 6, at[1] - 6), (255, 40, 30), 30, 1.6)
    blade = scaled(weapon, 480, 480)
    if scene.get("ally"):  # a mob, not a weapon: it stands low on the left and looks at the boss
        blade = outline(blade.transpose(Image.FLIP_LEFT_RIGHT), BLACK, 8)
    else:
        blade = outline(blade, BLACK, 10).rotate(8, expand=True, resample=Image.NEAREST)
        swing_trail(img, (hit[0] - 60, hit[1] + 360), 520, 200, 300)
    burst(img, hit, 190)
    bx, by = hit[0] - int(blade.width * 0.84), hit[1] - int(blade.height * 0.2)
    paste_glowing(img, blade, (max(-60, bx), by), (255, 255, 255), 22, 1.2)
    d = ImageDraw.Draw(img)
    rng = random.Random(11)
    for _ in range(26):  # damage sparks
        a, r = rng.uniform(0, 2 * math.pi), rng.uniform(200, 380)
        x, y, s = hit[0] + r * math.cos(a), hit[1] + r * math.sin(a), rng.randint(14, 30)
        d.rectangle([x, y, x + s, y + s], fill=rng.choice(((255, 60, 40), (255, 200, 40), (255, 255, 255))),
                    outline=BLACK, width=3)
    headline(img, scene["top"], 110, 120, box=YELLOW, lines=1)
    boss_bar(img, scene["mob"], 330)
    if scene.get("hearts"):
        hearts_row(img, 1320, scene["hearts"])
    headline(img, scene["text"], None, 150)
    badge(img)
    return img


def draw_duel(scene: dict, base: Image.Image) -> Image.Image:
    """You against your friend: two weapons clash in the middle, YOU on the left, YOUR FRIEND on the right."""
    centre = (540, 930)
    img = backdrop(base, (255, 60, 40), centre)
    half = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(half)
    d.polygon([(0, 300), (600, 300), (480, 1560), (0, 1560)], fill=(40, 110, 255, 90))  # blue corner, red corner
    d.polygon([(600, 300), (W, 300), (W, 1560), (480, 1560)], fill=(255, 50, 40, 90))
    img.alpha_composite(half)
    mine = outline(scaled(picture("Diamond Sword"), 520, 520), BLACK, 10)
    theirs = outline(scaled(picture("Netherite Axe"), 520, 520), BLACK, 10).transpose(Image.FLIP_LEFT_RIGHT)
    burst(img, (centre[0], centre[1] - 150), 210)
    paste_glowing(img, mine, (centre[0] - mine.width + 110, centre[1] - 190), (90, 160, 255), 24, 1.4)
    paste_glowing(img, theirs, (centre[0] - 110, centre[1] - 190), (255, 80, 60), 24, 1.4)
    d = ImageDraw.Draw(img)
    for words, x, colour in (("YOU", 240, (90, 170, 255)), ("FRIEND", 820, (255, 90, 70))):
        d.text((x, 440), words, font=font(120), fill=colour, stroke_width=10, stroke_fill=BLACK, anchor="mm")
    d.text((centre[0] - 30, 440), "VS", font=font(70), fill=WHITE, stroke_width=8, stroke_fill=BLACK, anchor="mm")
    headline(img, "PAUSE NOW!", 110, 120, box=YELLOW, lines=1)
    headline(img, scene["text"], None, 190)
    badge(img)
    return img


def draw_wall(scene: dict, base: Image.Image) -> Image.Image:
    centre = (560, 960)
    img = backdrop(base, (255, 170, 40), centre)
    cell = 216  # 16 px x 13.5: whole blocks across
    wall = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hole = []
    for r in range(2, 7):
        for c in range(0, 5):
            x, y = c * cell, r * cell + 20
            dist = math.hypot(x + cell / 2 - centre[0], y + cell / 2 - centre[1])
            if dist < cell * 0.95:
                hole.append((x, y))
                continue
            tex = texture(scene["block"], r * 10 + c).resize((cell, cell), Image.NEAREST).convert("RGBA")
            dark = max(0.55, min(1.0, dist / 700))
            tex = ImageEnhance.Brightness(tex).enhance(dark)
            wall.alpha_composite(tex, (x, y))
            ImageDraw.Draw(wall).rectangle([x, y, x + cell - 1, y + cell - 1], outline=(0, 0, 0, 120), width=3)
    # light through the hole
    light = Image.new("L", (W, H), 0)
    ImageDraw.Draw(light).ellipse([centre[0] - 260, centre[1] - 260, centre[0] + 260, centre[1] + 260], fill=255)
    light = light.filter(ImageFilter.GaussianBlur(70))
    shine = Image.new("RGBA", (W, H), (255, 236, 170, 0))
    shine.putalpha(light)
    img.alpha_composite(wall)
    img.alpha_composite(shine)
    # cracks out of the hole
    d = ImageDraw.Draw(img)
    rng = random.Random(5)
    for k in range(11):
        a = k / 11 * 2 * math.pi + rng.uniform(-0.2, 0.2)
        x, y = centre[0] + 150 * math.cos(a), centre[1] + 150 * math.sin(a)
        pts = [(x, y)]
        for _ in range(rng.randint(4, 7)):
            a += rng.uniform(-0.5, 0.5)
            step = rng.uniform(50, 90)
            x, y = x + step * math.cos(a), y + step * math.sin(a)
            pts.append((x, y))
        d.line(pts, fill=(8, 6, 10), width=rng.choice((8, 10, 13)), joint="curve")
    # chunks flying at the viewer
    for k in range(16):
        a, r = rng.uniform(0, 2 * math.pi), rng.uniform(170, 470)
        s = rng.randint(50, 120)
        chunk = texture(scene["block"], 100 + k).resize((s, s), Image.NEAREST).convert("RGBA")
        chunk = outline(chunk, BLACK, 4).rotate(rng.uniform(0, 90), expand=True, resample=Image.NEAREST)
        img.alpha_composite(chunk, (int(centre[0] + r * math.cos(a) - chunk.width / 2),
                                    int(centre[1] + r * math.sin(a) - chunk.height / 2)))
    burst(img, centre, 170, inner=WHITE, outer=(255, 230, 140), spikes=12)
    tool = picture(scene["tool"])
    tool = outline(scaled(tool, 560, 560), BLACK, 10).transpose(Image.FLIP_LEFT_RIGHT)  # head top left, on the hole
    at = (centre[0] - int(tool.width * 0.28), centre[1] - int(tool.height * 0.24))
    swing_trail(img, (at[0] + tool.width, at[1] + tool.height), 640, 205, 265)
    paste_glowing(img, tool, at, (255, 255, 255), 22, 1.2)
    headline(img, scene.get("top", "PAUSE NOW!"), 110, 120, box=YELLOW, lines=1)
    if scene.get("hearts"):
        hearts_row(img, 330, scene["hearts"])
    headline(img, scene["text"], None, 150)
    badge(img)
    return img


def rays(img: Image.Image, centre: tuple[int, int], colour: tuple, count: int = 18, alpha: int = 110) -> None:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for k in range(count):
        a0 = k / count * 2 * math.pi
        a1 = a0 + math.pi / count
        d.polygon([centre, (centre[0] + 2400 * math.cos(a0), centre[1] + 2400 * math.sin(a0)),
                   (centre[0] + 2400 * math.cos(a1), centre[1] + 2400 * math.sin(a1))], fill=colour + (alpha,))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(6)))


def draw_chest(scene: dict, base: Image.Image) -> Image.Image:
    centre = (540, 1040)
    img = backdrop(base, (255, 190, 40), centre)
    rays(img, centre, (255, 210, 60))
    chest = scaled(picture("Chest"), 640, 640)
    paste_glowing(img, outline(chest, BLACK, 8), (centre[0] - chest.width // 2 - 8, centre[1] - chest.height // 2 + 60),
                  (255, 200, 40), 40, 1.8)
    rng = random.Random(4)
    loot = ["Totem of Undying", "Enchanted Golden Apple", "Diamond", "Netherite Ingot", "Ender Pearl", "Golden Apple",
            "Rotten Flesh"]  # trendclip.dungeon.LOOT
    spots = [(-330, -330), (300, -380), (-410, -40), (400, -60), (-120, -470), (150, -250), (-250, -560)]
    for name, (dx, dy) in zip(loot, spots):
        pic = picture(name)
        if pic is None:
            continue
        item = outline(scaled(pic, 220, 220), BLACK, 6).rotate(rng.uniform(-35, 35), expand=True,
                                                               resample=Image.NEAREST)
        paste_glowing(img, item, (centre[0] + dx - item.width // 2, centre[1] + dy - item.height // 2),
                      (255, 230, 120), 16, 1.0)
    headline(img, "PAUSE NOW!", 110, 120, box=YELLOW, lines=1)
    headline(img, scene["text"], None, 150)
    badge(img)
    return img


def draw_blocks(scene: dict, base: Image.Image) -> Image.Image:
    from trendclip import spin

    centre = (540, 930)
    img = backdrop(base, (255, 196, 0), centre)
    rays(img, centre, (255, 220, 80), alpha=90)
    rng = random.Random(9)
    names = scene.get("around") or ["Dirt", "Obsidian", "Diamond Ore", "Gold Block"]
    around = list(zip(names, [(-360, -380), (370, -340), (-400, 120), (390, 170)]))
    for name, (dx, dy) in around:
        pic = picture(name)
        if pic is None:
            continue
        colour = spin.TIERS[spin.tier_of(name, scene.get("set", ""))][0]
        cube = scaled(pic, 250, 250).rotate(rng.uniform(-25, 25), expand=True, resample=Image.BICUBIC)
        paste_glowing(img, cube, (centre[0] + dx - cube.width // 2, centre[1] + dy - cube.height // 2), colour, 22, 1.5)
    hero = scaled(picture(scene.get("hero", "Netherite Block")), 520, 520)
    paste_glowing(img, hero, (centre[0] - hero.width // 2, centre[1] - hero.height // 2), (255, 196, 0), 50, 2.0)
    d = ImageDraw.Draw(img)
    f = font(70)
    d.rounded_rectangle([centre[0] - 230, centre[1] + 290, centre[0] + 230, centre[1] + 390], radius=20,
                        fill=(255, 196, 0))
    d.text((centre[0], centre[1] + 340), "LEGENDARY", font=f, fill=BLACK, anchor="mm")
    headline(img, "PAUSE NOW!", 110, 120, box=YELLOW, lines=1)
    headline(img, scene["text"], None, 150)
    badge(img)
    return img


def draw_lava(scene: dict, base: Image.Image) -> Image.Image:
    """The quest's lava stop: a lake and one block to land on (stone, magma or nothing): which one is a "?"."""
    img = backdrop(base, (255, 90, 20), (540, 1150))
    cell = 216
    for r in range(4, 8):
        for c in range(5):
            tex = texture("lava", r * 7 + c).resize((cell, cell), Image.NEAREST).convert("RGBA")
            img.alpha_composite(tex, (c * cell, r * cell + 20))
    heat = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(heat).rectangle([0, 780, W, 1000], fill=(255, 150, 30, 190))
    img.alpha_composite(heat.filter(ImageFilter.GaussianBlur(70)))
    block = outline(texture("stone", 3).resize((300, 300), Image.NEAREST).convert("RGBA"), BLACK, 8)
    at = ((W - block.width) // 2, 900)
    paste_glowing(img, block, at, (255, 210, 80), 30, 1.6)
    d = ImageDraw.Draw(img)
    d.text((W / 2, at[1] + block.height / 2), "?", font=font(250), fill=YELLOW, stroke_width=14, stroke_fill=BLACK,
           anchor="mm")
    rng = random.Random(2)
    for _ in range(30):  # embers
        x, y, sz = rng.uniform(0, W), rng.uniform(560, 1500), rng.randint(10, 24)
        d.rectangle([x, y, x + sz, y + sz], fill=rng.choice(((255, 120, 20), (255, 200, 60), (255, 70, 20))))
    headline(img, "PAUSE NOW!", 110, 120, box=YELLOW, lines=1)
    if scene.get("hearts"):
        hearts_row(img, 330)
    headline(img, scene["text"], None, 150)
    badge(img)
    return img
