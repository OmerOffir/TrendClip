"""Pause Adventure: a three-minute SideQuestLogic video made of the channel's best pieces (the owner, 2026-11-14:
"a good adventure of 3-4 minutes, think with everything we have").

From your first tree to the final boss in nine chapters on an adventure map (the dungeon's idea: every stop
still ahead a "?", the player walking to the next), each one a pause. Four chapters are races drawn like the
game (race.py): you pause on a tool, then all seven tiers do the job at Java's real speed, and a top-three finish is a
medal. Between them the Block winner's loot cards (where you spawn, your base, your pet) and one Land This chapter
(land the diamond block: a medal too). The twists carry it: gold chops and mines fastest, can't even touch obsidian,
and is dead last with a sword. The end counts the medals (five is perfect) and asks for a comment; one card in the
diamond chapter is a secret frame with a code, as in the Shorts.

Rendered by the Shorts pipeline (spin.plan sends it here): narration, captions and the parkour behind, with the
chapter banner, the cards and the race panels on top, each cut to its chapter by the narration's own words.
"""

from __future__ import annotations

import math
import random
import re
from types import SimpleNamespace

from .config import Settings
from .video_assembler import WIDTH, Overlay, Word

KIND = "adventure"
MARK = "This is a pause adventure."
# (kind, set, banner) per chapter, in the order the narration says "Chapter ..."
CHAPTERS = [
    ("loot", "spawn", "WHERE YOU SPAWN"),
    ("race", "axe", "GET WOOD"),
    ("loot", "base", "YOUR HOME"),
    ("race", "pickaxe", "MINE DOWN"),
    ("land", "diamond", "DIAMONDS"),
    ("race", "obsidian", "THE PORTAL"),
    ("loot", "pet", "A FRIEND"),
    ("loot", "food", "HEAL UP"),
    ("race", "sword", "THE FINAL BOSS"),
]
MEDALS = ["WOOD", "STONE", "DIAMONDS", "OBSIDIAN", "BOSS"]
SCRIPT = (  # each chapter opens on the map: a line of story, then the pause
    "{game} fans only. This is a pause adventure. Nine stops on one map, from your first tree to the final boss. "
    "At every stop, you pause. Top three in a race wins a medal. "
    "Chapter one. You wake up in a brand new world. But where? Pause the video right now. That is where you spawn. "
    "Grey is common. Gold is legendary. A cherry grove is lucky. The deep dark is the worst place to wake up. "
    "Chapter two. The forest. You need wood, and fast. Pause the video now. The axe you stop on is yours. "
    "Ready? Go! Six oak logs, real speed. Gold is already done! Netherite is second, then diamond. Iron, copper, "
    "stone... and wood is still chopping. Top three? That is your first medal. But a golden axe breaks after only "
    "thirty two uses. "
    "Chapter three. The sun is going down. You need a home before the mobs come out. Pause the video now. The base "
    "you stop on is yours. Gold is legendary. A woodland mansion is the dream. A desert well is... just a well. "
    "Chapter four. The mine. Time to dig straight down. Pause the video now. The pickaxe you stop on is yours. "
    "Ready? Go! Eight blocks of stone. Gold wins again! Netherite is second, diamond third. And wood is still "
    "digging. Medal number two? Gold is the fastest tier in the whole game. It just breaks fast too. "
    "Chapter five. Deep underground, something shines. Diamonds! Pause this video on the diamond block. Only one in "
    "a hundred can land it. If you land it, that is a medal too. Keep trying. It flies by fast. Pro tip: in the "
    "real game, diamonds are most common at Y minus fifty nine. "
    "Chapter six. The boss waits on the other side of a portal, and a portal needs obsidian. Pause the video now. "
    "The pickaxe you stop on is yours. Ready? Go! Watch this. Wood, stone, copper, iron and gold can't break "
    "obsidian at all. Only diamond and netherite can, and it takes a long time. Netherite breaks it first! Diamond "
    "is right behind. "
    "Chapter seven. You should not fight alone. Pause the video now. The pet you stop on is yours. Gold is "
    "legendary. A strong pet helps in the boss fight. "
    "Chapter eight. The last camp before the boss. Heal up. Pause the video now. The food you stop on is yours. "
    "Gold is legendary. A golden apple would be perfect right now. "
    "Chapter nine. The final boss. A ravager with one hundred health. Pause the video now. The sword you stop on "
    "is yours. Ready? Go! Double speed. Every hit counts. Here it comes... Netherite kills it first! Diamond is "
    "second, iron third. Stone and copper tie. And gold? Gold is dead last, with wood. The fastest pickaxe is the "
    "worst sword. "
    "You made it to the end of the map. Count your medals. Wood, stone, diamonds, obsidian and the boss. Five is "
    "perfect. Comment your medals, and tell me which stop was the hardest. And one frame in this video hides a "
    "secret code. If you caught it, subscribe and comment it.")
TITLE = "Minecraft Pause Adventure: Can You Get All 5 Medals? 🏅⛏️"
DESCRIPTION = ("Pause the video nine times, from your first tree to the final boss. Every race runs at real Java "
               "speed: finish top three for a medal. How many medals did you get? One frame hides a secret code.")
PINNED = ("How many medals did you get? 🏅 Comment your medals (5 = perfect)\n"
          "🔎 One frame hides a SECRET CODE. Caught it? Comment it.")
HASHTAGS = ["#minecraft", "#gaming", "#pausechallenge", "#minecraftchallenge"]
GOLD, OUTLINE = (255, 196, 0), (16, 16, 22)
BANNER = (1000, 330)  # chapter pill and the PAUSE NOW! under it, over the top of the screen
BANNER_CENTER = (WIDTH // 2, 210)


def wanted(notes: str = "") -> bool:
    return "pause adventure" in (notes or "").lower()


def ready(settings: Settings) -> bool:
    from . import race, spin

    return (all(race.catalog(settings, n) for k, n, _ in CHAPTERS if k == "race")
            and all(spin.prize_catalog(settings, n) for k, n, _ in CHAPTERS if k == "loot")
            and bool(spin.land_catalog(settings, "diamond")))


def script(settings: Settings, game: str, *, format: str = "short", rng=None):
    from . import quiz, spin

    if not ready(settings):
        raise ValueError("The adventure's pictures are missing (race textures, loot sets or the diamond set)")
    game_name = quiz.game_name(game)
    pack = SimpleNamespace(title=TITLE, description=DESCRIPTION, hashtags=HASHTAGS, title_card="")
    out = quiz._script(settings, game, "builtin", "gamer", format, SCRIPT.format(game=game_name),
                       spin.HOOK.format(game=game_name), [], "", PINNED, pack, on_screen="Pause adventure",
                       card="PAUSE ADVENTURE", end_card="Comment your medals")
    return out.model_copy(update={"spin": True, "reactions": []})


# --------------------------------------------------------------------------- where each piece goes


def _clean(w: Word) -> str:
    return re.sub(r"[^a-z]", "", w.text.lower())


def chapters(words: list[Word], total: float) -> tuple[float, list[dict], float]:
    """(intro end, [{start, pause, go, end}] per chapter, outro start) read off the narration: a chapter starts on
    its "Chapter" (the map shows until its first "Pause"), a race on the "Go" after it, the outro on "made" (You made
    it to the end of the map)."""
    starts = [w.start for w in words if _clean(w) == "chapter"]
    outro = next((w.start for w in words if _clean(w) in ("made", "count") and starts and w.start > starts[-1]),
                 total)
    out = []
    for i, start in enumerate(starts[:len(CHAPTERS)]):
        end = starts[i + 1] if i + 1 < len(starts) else outro
        go = next((w.start for w in words if start < w.start < end and _clean(w) == "go"), None)
        pause = next((w.start for w in words if start < w.start < end and _clean(w) == "pause"), start)
        out.append({"start": start, "pause": pause, "end": end, "go": go})
    return (starts[0] if starts else total), out, outro


def cue(words: list[Word], word: str, after: float = 0.0, default: float | None = None) -> float | None:
    return next((w.start for w in words if w.start >= after and _clean(w) == word), default)


# --------------------------------------------------------------------------- drawing


def _medal(size: int, lit: bool = True):
    """A gold medal on a red-and-blue ribbon, drawn in big pixels; grey when not won."""
    from PIL import Image, ImageDraw

    cell = max(2, size // 16)
    im = Image.new("RGBA", (16 * cell, 20 * cell))
    d = ImageDraw.Draw(im)
    ribbon = [(220, 50, 50), (50, 90, 220)] if lit else [(110, 110, 116), (90, 90, 96)]
    for x in range(4, 12):
        for y in range(0, 7):
            if abs((x - 8) + 0.5) <= 4 - y * 0.35:
                d.rectangle([x * cell, y * cell, (x + 1) * cell - 1, (y + 1) * cell - 1],
                            fill=ribbon[0] if x < 8 else ribbon[1])
    face, rim, shine = ((255, 200, 30), (190, 120, 10), (255, 245, 170)) if lit else \
        ((150, 150, 156), (100, 100, 108), (190, 190, 196))
    for x in range(16):
        for y in range(16):
            r = math.hypot(x - 7.5, y - 7.5)
            if r <= 7.6:
                c = rim if r > 6.0 else shine if (x - y) in (-3, -2) and r < 5 else face
                d.rectangle([x * cell, (y + 4) * cell, (x + 1) * cell - 1, (y + 5) * cell - 1], fill=c)
    star = ["0001000", "0011100", "1111111", "0111110", "0110110"]
    for y, row in enumerate(star):
        for x, ch in enumerate(row):
            if ch == "1":
                d.rectangle([(x + 4.5) * cell, (y + 9) * cell, (x + 5.5) * cell - 1, (y + 10) * cell - 1],
                            fill=rim)
    return im


def _banner(number: int, text: str, pause: bool):
    """CHAPTER 2 · GET WOOD in a dark pill, and PAUSE NOW! in yellow under it while the cards run."""
    from PIL import Image, ImageDraw

    from .reveal import _font

    im = Image.new("RGBA", BANNER)
    d = ImageDraw.Draw(im)
    head = f"CHAPTER {number}"
    big = _font(64)
    small = _font(40)
    width = d.textlength(head, font=small) + d.textlength(text, font=big) + 110
    x0 = (BANNER[0] - width) / 2
    d.rounded_rectangle([x0, 20, x0 + width, 130], radius=40, fill=(18, 20, 30, 235), outline=GOLD, width=6)
    d.text((x0 + 40, 75), head, font=small, fill=GOLD, anchor="lm")
    d.text((x0 + 70 + d.textlength(head, font=small), 75), text, font=big, fill=(255, 255, 255), anchor="lm")
    if pause:
        d.text((BANNER[0] / 2, 225), "PAUSE NOW!", font=_font(110), fill=(255, 214, 0), anchor="mm",
               stroke_width=10, stroke_fill=OUTLINE)
    return im


def _intro(found: dict, length: float, fps: int, cache: dict) -> list:
    """The hook over the Plains: CAN YOU GET ALL 5 MEDALS?, the medals popping in one by one, then 8 PAUSES."""
    from PIL import Image, ImageDraw

    from . import race
    from .reveal import _font

    world = race._world(found, cache)
    frames = []
    medal = _medal(150)
    for i in range(max(1, int(length * fps))):
        t = i / fps
        img = world.copy()
        d = ImageDraw.Draw(img)
        d.text((race.SIZE[0] / 2, 150), "CAN YOU GET", font=_font(96), fill=(255, 255, 255), anchor="mm",
               stroke_width=9, stroke_fill=OUTLINE)
        d.text((race.SIZE[0] / 2, 270), "ALL 5 MEDALS?", font=_font(110), fill=GOLD, anchor="mm", stroke_width=10,
               stroke_fill=OUTLINE)
        for k in range(5):
            at = 0.5 + k * 0.35
            if t < at:
                continue
            pop = min(1.0, (t - at) / 0.18)
            size = max(1, int(medal.width * (0.6 + 0.4 * pop + 0.15 * math.sin(pop * math.pi))))
            m = medal.resize((size, int(size * medal.height / medal.width)), Image.NEAREST)
            cx = 120 + k * 200
            img.alpha_composite(m, (int(cx - m.width / 2), int(560 - m.height / 2 + 6 * math.sin(t * 5 + k))))
        if t > 2.6:
            d.text((race.SIZE[0] / 2, 800), "8 PAUSES  ·  REAL SPEED", font=_font(64), fill=(255, 255, 255),
                   anchor="mm", stroke_width=7, stroke_fill=OUTLINE)
        frames.append(race._framed(img))
    return frames


def _outro(found: dict, length: float, fps: int, cache: dict, secret: str) -> list:
    """COUNT YOUR MEDALS: the five medal slots light up as the narrator names them, then the comment ask."""
    from PIL import ImageDraw

    from . import race
    from .reveal import _font

    world = race._world(found, cache)
    lit, dim = _medal(120), _medal(120, lit=False)
    frames = []
    for i in range(max(1, int(length * fps))):
        t = i / fps
        img = world.copy()
        d = ImageDraw.Draw(img, "RGBA")
        d.rounded_rectangle([40, 40, race.SIZE[0] - 40, race.SIZE[1] - 40], radius=40, fill=(10, 12, 20, 150))
        d.text((race.SIZE[0] / 2, 130), "COUNT YOUR MEDALS", font=_font(82), fill=GOLD, anchor="mm",
               stroke_width=8, stroke_fill=OUTLINE)
        for k, name in enumerate(MEDALS):
            on = t > 1.0 + k * 0.45
            m = lit if on else dim
            cx = 140 + k * 190
            img.alpha_composite(m, (int(cx - m.width / 2), 230))
            d.text((cx, 420), name, font=_font(30), fill=(255, 255, 255) if on else (150, 150, 156), anchor="mm",
                   stroke_width=4, stroke_fill=OUTLINE)
        if t > 3.4:
            d.text((race.SIZE[0] / 2, 540), "5 = PERFECT", font=_font(76), fill=(255, 255, 255), anchor="mm",
                   stroke_width=7, stroke_fill=OUTLINE)
            d.text((race.SIZE[0] / 2, 650), "COMMENT YOUR MEDALS", font=_font(64), fill=GOLD, anchor="mm",
                   stroke_width=7, stroke_fill=OUTLINE)
        if t > 6.0:
            d.text((race.SIZE[0] / 2, 800), "CAUGHT THE SECRET CODE?", font=_font(52), fill=(0, 230, 200),
                   anchor="mm", stroke_width=6, stroke_fill=OUTLINE)
            d.text((race.SIZE[0] / 2, 880), "SUBSCRIBE AND COMMENT IT", font=_font(44), fill=(255, 255, 255),
                   anchor="mm", stroke_width=5, stroke_fill=OUTLINE)
        frames.append(race._framed(img))
    return frames


def _save(frames: list, path, frame_seconds: float, fps: int = 30):
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=round(1000 * frame_seconds), loop=1,
                   lossless=False, quality=86)
    return path




# --------------------------------------------------------------------------- the adventure map
# Like the dungeon's map (dungeon.py), the glue between chapters: a Minecraft-style map with the nine stops on one
# path, a "?" on every stop still ahead, what was there on the stops passed, and the player walking to the next one
# with YOU ARE HERE (the owner, 2026-11-14: "it has to be an adventure, like the dungeon").

STOPS = [  # (label on the map, folder, picture, biome colour around it)
    ("SPAWN", "spin", "grass-block.png", (106, 170, 64)),
    ("FOREST", "spin", "oak-log.png", (52, 120, 46)),
    ("HOME", "spin", "crafting-table.png", (120, 176, 72)),
    ("THE MINE", "spin", "stone.png", (124, 124, 128)),
    ("DIAMONDS", "spin", "diamond-block.png", (84, 92, 104)),
    ("PORTAL", "spin", "obsidian.png", (188, 170, 92)),
    ("FRIEND", "reveal", "wolf.png", (140, 186, 88)),
    ("CAMP", "prize-food", "golden-apple.png", (176, 150, 96)),
    ("BOSS", "reveal", "ravager.png", (150, 40, 36)),
]
NODES = [(200, 800), (520, 800), (840, 800), (840, 545), (520, 545), (200, 545), (200, 290), (520, 290), (840, 290)]
NODE_R = 64
WALK = 1.2  # seconds the player takes from one stop to the next


def _terrain(cache: dict):
    """The map's ground: each patch takes the biome of the nearest stop, in 10 px cells with a little noise, on
    parchment with a darker frame (the game's own map item)."""
    from PIL import Image, ImageDraw

    from . import race

    if "terrain" not in cache:
        w, h = race.SIZE
        im = Image.new("RGBA", (w, h), (214, 190, 140, 255))
        d = ImageDraw.Draw(im)
        cell = 10
        for y in range(150, h - 50, cell):
            for x in range(50, w - 50, cell):
                j = min(range(len(NODES)), key=lambda k: (NODES[k][0] - x) ** 2 + (NODES[k][1] - y) ** 2 * 1.6)
                base = STOPS[j][3]
                n = ((x // cell) * 73856093 ^ (y // cell) * 19349663) % 100 / 100
                k = 0.86 + 0.22 * n
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=tuple(min(255, int(v * k)) for v in base))
        d.rectangle([40, 140, w - 40, h - 40], outline=(120, 92, 56), width=10)
        d.rectangle([52, 152, w - 52, h - 52], outline=(160, 128, 84), width=4)
        cache["terrain"] = im
    return cache["terrain"]


def _along(here: int, k: float) -> tuple[float, float]:
    """A point k of the way from stop here-1 to stop here."""
    a, b = NODES[max(0, here - 1)], NODES[here]
    return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k


def map_frame(settings: Settings, found: dict, here: int, t: float, cache: dict, done: bool = False,
              shown: int = 99):
    """The map with stop `here` (0-based) next: the player walks there in WALK seconds; `done`: every stop
    passed (the end); `shown`: only the first stops and the path between them (the map drawing itself)."""
    from PIL import Image, ImageDraw

    from . import race
    from .reveal import _font

    img = _terrain(cache).copy()
    d = ImageDraw.Draw(img, "RGBA")
    d.text((race.SIZE[0] / 2, 72), "THE ADVENTURE MAP", font=_font(70), fill=GOLD, anchor="mm", stroke_width=7,
           stroke_fill=OUTLINE)
    k = min(1.0, t / WALK)
    for i in range(min(len(NODES), shown) - 1):  # the path: gold where you have been, dashes ahead
        a, b = NODES[i], NODES[i + 1]
        if i + 1 < here or (i + 1 == here and k >= 1):
            d.line([a, b], fill=(255, 210, 60), width=14)
        else:
            steps = 9
            for s in range(0, steps, 2):
                p = (a[0] + (b[0] - a[0]) * s / steps, a[1] + (b[1] - a[1]) * s / steps)
                q = (a[0] + (b[0] - a[0]) * (s + 1) / steps, a[1] + (b[1] - a[1]) * (s + 1) / steps)
                d.line([p, q], fill=(250, 244, 226), width=10)
    if here and k < 1:  # the walked part so far
        d.line([NODES[here - 1], _along(here, k)], fill=(255, 210, 60), width=14)
    for i, ((label, folder, file, _), (x, y)) in enumerate(zip(STOPS, NODES)):
        if i >= shown:
            continue
        ring = (60, 200, 90) if i < here or done else (255, 210, 60) if i == here else (70, 60, 50)
        pulse = 6 * math.sin(t * 8) if i == here else 0
        r = NODE_R + pulse
        d.ellipse([x - r, y - r, x + r, y + r], fill=(24, 22, 30, 235), outline=ring, width=9)
        if i <= here:
            key = ("stop", i)
            if key not in cache:
                pic = Image.open(settings.assets_dir / folder / file).convert("RGBA")
                s = 92 / max(pic.size)
                cache[key] = pic.resize((max(1, round(pic.width * s)), max(1, round(pic.height * s))),
                                        Image.NEAREST if max(pic.size) < 160 else Image.LANCZOS)
            pic = cache[key]
            img.alpha_composite(pic, (int(x - pic.width / 2), int(y - pic.height / 2)))
        else:
            d.text((x, y + 4), "?", font=_font(80), fill=(250, 244, 226), anchor="mm")
        if i < here or done:  # passed: a green tick
            d.ellipse([x + 30, y - 66, x + 70, y - 26], fill=(60, 200, 90), outline=OUTLINE, width=4)
            d.line([(x + 40, y - 46), (x + 48, y - 37), (x + 61, y - 55)], fill=(255, 255, 255), width=6)
        d.text((x, y + NODE_R + 30), label, font=_font(30), fill=(255, 255, 255), anchor="mm", stroke_width=5,
               stroke_fill=OUTLINE)
    px, py = _along(here, k) if here else NODES[0]
    hop = 8 * abs(math.sin(t * 12)) if k < 1 else 0
    figure = race._player_parts(cache.setdefault("player", {}))[0]
    small = figure.resize((figure.width * 3 // 4, figure.height * 3 // 4), Image.NEAREST)
    img.alpha_composite(small, (int(px - small.width / 2 - 40), int(py - small.height - 4 - hop)))
    if k >= 1 and not done:
        x, y = NODES[here]
        d.rounded_rectangle([x - 130, y - NODE_R - 74, x + 130, y - NODE_R - 26], radius=20, fill=(255, 210, 60),
                            outline=OUTLINE, width=4)
        d.text((x, y - NODE_R - 50), "YOU ARE HERE", font=_font(30), fill=OUTLINE, anchor="mm")
    return race._framed(img)


# --------------------------------------------------------------------------- the plan


def plan(settings: Settings, words: list[Word], seed: str = "", tail: float = 2.0, fps: int = 30) -> list[Overlay]:
    import json

    from . import race, spin

    if not words:
        return []
    rng = random.Random(seed or KIND)
    total = words[-1].end + tail
    intro_end, parts, outro = chapters(words, total)
    out_dir = settings.assets_dir / "cache" / "adventure"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-z0-9]+", "-", (seed or KIND).lower())[:40].strip("-")
    cache: dict = {}
    plains = race.catalog(settings, "pickaxe")
    overlays: list[Overlay] = []
    card_seconds = spin.HOLD / fps

    def add(frames, start, end, name, center, frame_seconds=1 / race.FPS):
        path = _save(frames, out_dir / f"{stem}-{name}.webp", frame_seconds)
        overlays.append(Overlay(path, round(start, 3), round(end, 3), center=center, box=max(frames[0].size),
                                style="asis", cut=True, animated=True))

    def still(img, start, end, name, center):
        path = out_dir / f"{stem}-{name}.png"
        img.save(path)
        overlays.append(Overlay(path, round(start, 3), round(end, 3), center=center, box=max(img.size),
                                style="asis", cut=True))

    map_from = cue(words, "nine", default=intro_end)
    map_from = map_from if map_from < intro_end else intro_end
    add(_intro(plains, map_from + 0.1, 15, cache), 0.0, map_from, "intro", race.CENTER, 1 / 15)
    if map_from < intro_end:  # the whole map, every stop a "?", the player at the start
        add([map_frame(settings, plains, 0, 5 + i / 15, cache, shown=1 + int(i / 15 / 0.45))
             for i in range(int((intro_end - map_from) * 15) + 2)], map_from, intro_end, "map0", race.CENTER, 1 / 15)
    code = spin.codes(seed or KIND, "MEDAL")
    secret_at = None
    for number, ((kind, name, text), part) in enumerate(zip(CHAPTERS, parts), start=1):
        start, end, go, pause = part["start"], part["end"], part["go"], part["pause"]
        cards_end = go if kind == "race" and go else end
        if pause > start:  # the map: the player walks to this stop while the story line is said
            add([map_frame(settings, plains, number - 1, i / 15, cache) for i in range(int((pause - start) * 15) + 2)],
                start, pause, f"map{number}", race.CENTER, 1 / 15)
            still(_banner(number, text, pause=False), start, pause, f"banner{number}m", BANNER_CENTER)
        start = pause
        still(_banner(number, text, pause=True), start, cards_end, f"banner{number}p", BANNER_CENTER)
        if kind == "race" and go:
            still(_banner(number, text, pause=False), go, end, f"banner{number}", BANNER_CENTER)
        count = max(2, int((cards_end - start) / card_seconds) + 2)
        if kind == "loot":
            picked = spin.deck(spin.prize_catalog(settings, name), count, rng)
            cards = [spin.card(settings, e, cache) for e in picked]
        elif kind == "land":
            land = spin.LANDS[name]
            dealt = spin.land_deck(spin.land_catalog(settings, name), land["target"], count, rng)
            cards = [spin.card(settings, e, cache, label, colour, ("THAT'S A MEDAL!",) if e["name"] ==
                               land["target"] else ()) for e, label, colour in dealt]
            at = spin.add_secret(cards, code["secret"], seed or KIND, fps=fps)
            secret_at = round(start + at, 2) if at is not None else None
        else:
            found = race.catalog(settings, name)
            cards = [spin.card(settings, e, cache) for e in spin.deck(found["items"], count, rng)]
        add(cards, start, cards_end, f"cards{number}", spin.CENTER, card_seconds)
        if kind == "race" and go:
            found = race.catalog(settings, name)
            drawn: dict = {}
            length = end - go + 0.2
            add([race.frame(found, name, i / race.FPS, drawn) for i in range(int(length * race.FPS) + 1)],
                go, end, f"race{number}", race.CENTER)
    medals = cue(words, "count", after=outro, default=outro)
    if medals > outro:  # the map once more, every stop ticked, the player at the boss
        add([map_frame(settings, plains, len(STOPS) - 1, 5 + i / 15, cache, done=True)
             for i in range(int((medals - outro) * 15) + 2)], outro, medals, "map-end", race.CENTER, 1 / 15)
    add(_outro(plains, total + 0.5 - medals, 15, cache, code["secret"]), medals, total + 0.5, "outro",
        race.CENTER, 1 / 15)
    if seed and re.fullmatch(r"[\w.-]+", seed):  # the secret code beside the video, for reading the comments
        settings.shorts_dir.mkdir(parents=True, exist_ok=True)
        (settings.shorts_dir / f"{seed}.codes.json").write_text(
            json.dumps({"secret": code["secret"], "secret_at": secret_at}, indent=1) + "\n", encoding="utf-8")
    return overlays
