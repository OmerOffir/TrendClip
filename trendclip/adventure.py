"""Pause Adventure: a three-minute SideQuestLogic video made of the channel's best pieces (the owner, 2026-11-14:
"a good adventure of 3-4 minutes, think with everything we have").

From your first tree to the final boss in eight chapters, each one a pause. Four chapters are races drawn like the
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
SCRIPT = (
    "{game} fans only. This is a pause adventure. Nine pauses, from your first tree to the final boss. "
    "Every race is real {game} speed. Finish in the top three, and you win a medal. Five medals is perfect. "
    "Grab a friend, and compare at the end. "
    "Chapter one. Where do you spawn? Pause the video right now. That is where you spawn. Grey is common. "
    "Gold is legendary. A cherry grove is lucky. The deep dark is the worst place to wake up. Remember your biome. "
    "Chapter two. Get wood. Pause the video now. The axe you stop on is yours. Ready? Go! Six oak logs, real speed. "
    "Gold is already done! Netherite is second, then diamond. Iron, copper, stone... and wood is still chopping. "
    "Top three? That is your first medal. But the golden axe breaks after only thirty two uses. "
    "Chapter three. Your home. Pause the video now. The base you stop on is yours. Gold is legendary. "
    "A woodland mansion is the dream. A desert well is... just a well. Is yours gold? "
    "Chapter four. Mine down. Pause the video now. The pickaxe you stop on is yours. Ready? Go! Eight blocks of "
    "stone. Gold wins again! Netherite is second, diamond third. Iron, copper, stone. And wood is still digging. "
    "Medal number two? Gold is the fastest tier in the whole game. It just breaks fast too. "
    "Chapter five. Diamonds. Pause this video on the diamond block. Only one in a hundred can land it. If you land "
    "it, that is a medal too. Keep trying. It flies by fast. Pro tip: in the real game, diamonds are most common "
    "at Y minus fifty nine. "
    "Chapter six. The portal. You need obsidian. Pause the video now. The pickaxe you stop on is yours. Ready? Go! "
    "Watch this. Wood, stone, copper, iron and gold can't break obsidian at all. Only diamond and netherite can, "
    "and it takes a long time. Netherite breaks it first! Diamond is right behind. A portal needs ten of these. "
    "Chapter seven. A friend for the fight. Pause the video now. The pet you stop on is yours. Gold is legendary. "
    "A strong pet helps in the boss fight. Is yours gold? "
    "Chapter eight. Heal up. Pause the video now. The food you stop on is yours. Gold is legendary. "
    "A golden apple would be perfect right now. "
    "Chapter nine. The final boss. A ravager with one hundred health. Pause the video now. The sword you stop on "
    "is yours. Ready? Go! Double speed. Every hit counts. Here it comes... Netherite kills it first! Diamond is "
    "second, iron third. Stone and copper tie. And gold? Gold is dead last, with wood. The fastest pickaxe is the "
    "worst sword. "
    "Count your medals. Wood, stone, diamonds, obsidian and the boss. Five is perfect. Comment your medals, and "
    "tell me which chapter was the hardest. And one frame in this video hides a secret code. If you caught it, "
    "subscribe and comment it.")
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
    """(intro end, [{start, end, go}] per chapter, outro start) read off the narration: a chapter starts on its
    "Chapter", a race on the "Go" after it, the outro on "Count"."""
    starts = [w.start for w in words if _clean(w) == "chapter"]
    outro = next((w.start for w in words if _clean(w) == "count" and starts and w.start > starts[-1]), total)
    out = []
    for i, start in enumerate(starts[:len(CHAPTERS)]):
        end = starts[i + 1] if i + 1 < len(starts) else outro
        go = next((w.start for w in words if start < w.start < end and _clean(w) == "go"), None)
        out.append({"start": start, "end": end, "go": go})
    return (starts[0] if starts else total), out, outro


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

    add(_intro(plains, intro_end + 0.1, 15, cache), 0.0, intro_end, "intro", race.CENTER, 1 / 15)
    code = spin.codes(seed or KIND, "MEDAL")
    secret_at = None
    for number, ((kind, name, text), part) in enumerate(zip(CHAPTERS, parts), start=1):
        start, end, go = part["start"], part["end"], part["go"]
        cards_end = go if kind == "race" and go else end
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
    add(_outro(plains, total + 0.5 - outro, 15, cache, code["secret"]), outro, total + 0.5, "outro", race.CENTER,
        1 / 15)
    if seed and re.fullmatch(r"[\w.-]+", seed):  # the secret code beside the video, for reading the comments
        settings.shorts_dir.mkdir(parents=True, exist_ok=True)
        (settings.shorts_dir / f"{seed}.codes.json").write_text(
            json.dumps({"secret": code["secret"], "secret_at": secret_at}, indent=1) + "\n", encoding="utf-8")
    return overlays
