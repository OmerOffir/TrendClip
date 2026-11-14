"""Guess the Mob, pause edition: you pause to get your mob, the video has its own secret one, yes/no questions
knock the rest out, and if the last mob standing is yours, you win (the owner, 2026-11-14, after Wished's "Minecraft
Guess the Mob" at 3.4M views: "like Battleship: the viewer picks one, the video picks one and doesn't say").

A board of the sixteen mobs that move (mobs.py) over a real Plains screenshot. From frame 0 a gold frame jumps from
mob to mob, a new one every two video frames: where you pause is your mob. The video's own mob hangs as a "?" over
the board. Each question is a true fact from the Minecraft Wiki about the secret mob; every mob with the other
answer dies the game's death (tips over red, puffs into smoke), until one is left: the "?" turns into it. The
questions are picked so each knocks out about half of what is left, so every video plays out differently.
"""

from __future__ import annotations

import math
import random
import re
from pathlib import Path
from types import SimpleNamespace

from .config import Settings
from .video_assembler import WIDTH, Overlay, Word

KIND = "guessmob"
MARK = "I picked a secret mob too."
MOBS = ["Zombie", "Husk", "Drowned", "Skeleton", "Stray", "Wither Skeleton", "Creeper", "Pig", "Cow", "Enderman",
        "Spider", "Chicken", "Iron Golem", "Villager", "Piglin", "Zombified Piglin"]
# question -> the mobs whose answer is yes (Java Edition, minecraft.wiki)
QUESTIONS = {
    "Is it undead?": {"Zombie", "Husk", "Drowned", "Skeleton", "Stray", "Wither Skeleton", "Zombified Piglin"},
    "Does it have more than two legs?": {"Creeper", "Pig", "Cow", "Spider"},
    "Can it be a baby?": {"Zombie", "Husk", "Drowned", "Pig", "Cow", "Chicken", "Villager", "Piglin",
                          "Zombified Piglin"},
    "Is it taller than two blocks?": {"Enderman", "Iron Golem", "Wither Skeleton"},
    "Does it burn in sunlight?": {"Zombie", "Drowned", "Skeleton", "Stray"},
    "Can it shoot arrows?": {"Skeleton", "Stray", "Piglin"},
    "Is it from the Nether?": {"Wither Skeleton", "Piglin", "Zombified Piglin"},
    "Does it live underwater?": {"Drowned"},
    "Does it live in the snow?": {"Stray"},
    "Can you ride it?": {"Pig"},
    "Can you milk it?": {"Cow"},
    "Does it lay eggs?": {"Chicken"},
    "Can it climb walls?": {"Spider"},
    "Does it explode?": {"Creeper"},
    "Does it teleport?": {"Enderman"},
    "Is it made of iron?": {"Iron Golem"},
    "Does it trade for emeralds?": {"Villager"},
}
ORDINAL = ["one", "two", "three", "four", "five", "six", "seven", "eight"]
SCRIPT_HEAD = ("{game} fans only. Pause the video now. The mob you stop on is yours. " + MARK + " If yours is the "
               "last one standing, you win. ")
SCRIPT_TAIL = ("Only one left. My mob was the {secret}! Did you pick the {secret}? Then you win. Comment your mob, "
               "and the question that knocked it out.")
TITLES = ["Pause To Pick Your Mob... Is It My Secret One? 🤔", "Guess My Secret Minecraft Mob (Pause Edition) 🤔",
          "Pause On A Mob: If It's The Last One Standing, You Win 🏆"]
PINNED = "Which mob did you get? 👇 And which question knocked it out?\nSend this to a friend: who lasts longer?"
DESCRIPTION = ("Pause the video: the Minecraft mob you stop on is yours. I picked a secret mob too. Yes or no "
               "questions knock the rest out. If yours is the last one standing, you win.")
HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#pausechallenge", "#guessthemob"]


def of_title(title: str) -> bool:
    low = (title or "").lower()
    return "secret" in low and "mob" in low or "pick your mob" in low or "last one standing" in low


def wanted(notes: str = "") -> bool:
    return "guess the mob" in (notes or "").lower()


def ready(settings: Settings) -> bool:
    from . import mobs, race

    return all(mobs.skin_path(settings, m).is_file() for m in MOBS) and bool(race.catalog(settings, "pickaxe"))


def chain(secret: str, rng: random.Random) -> list[tuple[str, bool, list[str]]]:
    """[(question, the secret's answer, the mobs it knocks out)] until only the secret is left: each time the
    question that splits what is left most evenly (ties at random), never one that knocks out nobody."""
    left, out, used = list(MOBS), [], set()
    while len(left) > 1:
        best, best_score = None, None
        for q in sorted(QUESTIONS):
            if q in used:
                continue
            yes = [m for m in left if m in QUESTIONS[q]]
            if not yes or len(yes) == len(left):
                continue
            answer = secret in QUESTIONS[q]
            gone = len(left) - len(yes) if answer else len(yes)
            score = abs(gone - len(left) / 2) + rng.random() * 0.6
            if best_score is None or score < best_score:
                best, best_score = q, score
        if best is None:
            break
        used.add(best)
        answer = secret in QUESTIONS[best]
        gone = [m for m in left if (m in QUESTIONS[best]) != answer]
        left = [m for m in left if m not in gone]
        out.append((best, answer, gone))
    return out


def script_text(secret: str, rng: random.Random, game: str = "Minecraft") -> str:
    parts = [SCRIPT_HEAD.format(game=game)]
    for i, (q, answer, _) in enumerate(chain(secret, rng)):
        parts.append(f"Question {ORDINAL[i]}. {q} {'Yes.' if answer else 'No.'} ")
    parts.append(SCRIPT_TAIL.format(secret=secret.lower()))
    return "".join(parts)


def read(text: str) -> tuple[str, list[tuple[str, bool, list[str]]]]:
    """(the secret, the chain) back from a script, so the board shows exactly what is said."""
    m = re.search(r"My mob was the ([a-z ]+)!", text or "")
    secret = next((x for x in MOBS if m and x.lower() == m.group(1).strip()), "")
    left, out = list(MOBS), []
    for q, ans in re.findall(r"Question \w+\. ([^?]+\?) (Yes|No)\.", text or ""):
        if q not in QUESTIONS:
            continue
        answer = ans == "Yes"
        gone = [x for x in left if (x in QUESTIONS[q]) != answer]
        left = [x for x in left if x not in gone]
        out.append((q, answer, gone))
    return secret, out


def script(settings: Settings, game: str, *, format: str = "short", rng: random.Random | None = None):
    from . import quiz, spin

    if not ready(settings):
        raise ValueError("No mob skins yet: run mobs.fetch_skins(settings) first")
    rng = rng or random.Random()
    secret = rng.choice(MOBS)
    game_name = quiz.game_name(game)
    pack = SimpleNamespace(title=rng.choice(TITLES), description=DESCRIPTION, hashtags=HASHTAGS,
                           title_card=spin.CARD)
    out = quiz._script(settings, game, "builtin", "gamer", format, script_text(secret, rng, game_name),
                       spin.HOOK.format(game=game_name), [], "", PINNED, pack, on_screen="Pick your mob",
                       card=spin.CARD, end_card="Comment your mob")
    return out.model_copy(update={"spin": True, "reactions": []})


# --------------------------------------------------------------------------- the board

FPS = 15  # board pictures a second: each shows for two video frames, as the pause cards do
COLS, ROWS = 4, 4
CELL = (250, 200)
GRID_TOP = 185
PLAQUE = (868, 14, 1024, 170)
GOLD, OUTLINE, GREEN, RED = (255, 196, 0), (16, 16, 22), (60, 200, 90), (230, 50, 50)
STAGGER = 0.07  # between two mobs dying on the same answer


def _clean(w: Word) -> str:
    return re.sub(r"[^a-z]", "", w.text.lower())


def timeline(words: list[Word], text: str) -> dict:
    """When each thing happens, read off the narration: the pause ends at the first "Question"; each answer lands
    on its Yes/No; the "?" turns at "was" (My mob was the ...)."""
    secret, steps = read(text)
    starts = [w.start for w in words if _clean(w) == "question"]
    answers = []
    for i, at in enumerate(starts[:len(steps)]):
        end = starts[i + 1] if i + 1 < len(starts) else math.inf
        answers.append(next((w.start for w in words if at < w.start < end and _clean(w) in ("yes", "no")), at + 2))
    reveal = next((w.start for w in words if _clean(w) == "was" and w.start > (answers[-1] if answers else 0)),
                  words[-1].end if words else 0)
    dies = {}
    for (q, _, gone), at in zip(steps, answers):
        for k, mob in enumerate(gone):
            dies[mob] = at + 0.15 + k * STAGGER
    return {"secret": secret, "steps": steps, "starts": starts[:len(steps)], "answers": answers,
            "pause_end": starts[0] if starts else 3.0, "reveal": reveal, "dies": dies}


def _scale(mob: str) -> float:
    from . import mobs

    model = mobs.MODELS[mob]
    return min(9.0, 148 / (model.height * model.grow))


def _cell_xy(i: int) -> tuple[int, int]:
    return 20 + (i % COLS) * CELL[0], GRID_TOP + (i // COLS) * CELL[1]


def board(settings: Settings, plan: dict, t: float, cache: dict, pick: int | None = None):
    from PIL import Image, ImageDraw

    from . import mobs, race
    from .reveal import _font

    found = cache.setdefault("found", race.catalog(settings, "pickaxe"))
    img = race._world(found, cache).copy()
    d = race.blend_draw(img)
    d.rectangle([0, 0, race.SIZE[0], 176], fill=(10, 12, 20, 150))
    steps, starts, answers = plan["steps"], plan["starts"], plan["answers"]
    current = max([i for i, at in enumerate(starts) if at <= t], default=-1)
    left = [m for m in MOBS if plan["dies"].get(m, math.inf) > t]
    revealed = t >= plan["reveal"]

    def fit(text: str, size: int, width: int = 830) -> object:
        while size > 30 and d.textlength(text, font=_font(size)) > width:
            size -= 2
        return _font(size)

    if pick is not None:  # the pause: the gold frame on a mob and its name
        d.text((440, 52), "PAUSE NOW!", font=_font(76), fill=(255, 214, 0), anchor="mm", stroke_width=7,
               stroke_fill=OUTLINE)
        name = MOBS[pick].upper()
        d.text((440, 130), name, font=fit(name, 58), fill=(255, 255, 255), anchor="mm", stroke_width=5,
               stroke_fill=OUTLINE)
    elif revealed:
        line = f"MY MOB WAS THE {plan['secret'].upper()}!"
        d.text((440, 52), line, font=fit(line, 58), fill=GOLD, anchor="mm", stroke_width=6, stroke_fill=OUTLINE)
        d.text((440, 130), "IS IT YOURS? YOU WIN!", font=_font(48), fill=(255, 255, 255), anchor="mm",
               stroke_width=5, stroke_fill=OUTLINE)
    elif current >= 0:
        q = steps[current][0].upper()
        d.text((440, 52), q, font=fit(q, 60), fill=(255, 255, 255), anchor="mm", stroke_width=6, stroke_fill=OUTLINE)
        if t >= answers[current]:
            yes = steps[current][1]
            d.text((440, 130), "YES!" if yes else "NO!", font=_font(70), fill=GREEN if yes else RED, anchor="mm",
                   stroke_width=6, stroke_fill=OUTLINE)
        else:
            d.text((440, 130), f"QUESTION {current + 1}", font=_font(40), fill=(200, 200, 210), anchor="mm",
                   stroke_width=4, stroke_fill=OUTLINE)
    else:
        d.text((440, 52), "WHICH MOB DID I PICK?", font=fit("WHICH MOB DID I PICK?", 58), fill=(255, 255, 255),
               anchor="mm", stroke_width=6, stroke_fill=OUTLINE)
        d.text((440, 130), f"{len(left)} LEFT", font=_font(48), fill=GOLD, anchor="mm", stroke_width=5,
               stroke_fill=OUTLINE)
    x0, y0, x1, y1 = PLAQUE  # the video's own mob: a "?" until the end
    d.rounded_rectangle([x0, y0, x1, y1], radius=18, fill=(24, 22, 30, 240), outline=GOLD, width=6)
    d.text(((x0 + x1) / 2, y0 + 20), "MY MOB", font=_font(24), fill=GOLD, anchor="mm")
    if revealed:
        key = ("plaque", int(t * FPS) % 39)
        if key not in cache:
            cache[key] = mobs.frame(settings, plan["secret"], "idle", key[1] / FPS, size=(150, 140),
                                    scale=_scale(plan["secret"]) * 0.62, cache=cache.setdefault("mobs", {}))
        img.alpha_composite(cache[key], (x0 + 3, y0 + 28))
    else:
        d.text(((x0 + x1) / 2, y0 + 96), "?", font=_font(110), fill=(255, 255, 255), anchor="mm", stroke_width=6,
               stroke_fill=OUTLINE)
    for i, mob in enumerate(MOBS):
        cx, cy = _cell_xy(i)
        box = [cx + 6, cy + 6, cx + CELL[0] - 6, cy + CELL[1] - 6]
        died = plan["dies"].get(mob)
        gone_for = t - died if died is not None and t >= died else None
        winner = revealed and mob == plan["secret"]
        d.rounded_rectangle(box, radius=20, fill=(235, 242, 255, 105) if gone_for is None else (14, 16, 24, 90))
        # light cells: the dark mobs (enderman, wither skeleton) stand out
        if gone_for is None:
            key = ("idle", mob, int(t * FPS) % 39)
            pic = cache.get(key)
            if pic is None:
                pic = cache[key] = mobs.frame(settings, mob, "idle", key[2] / FPS, size=(CELL[0] - 12, 168),
                                              scale=_scale(mob), cache=cache.setdefault("mobs", {}))
            img.alpha_composite(pic, (cx + 6, cy + 6))
        elif gone_for < mobs.DEATH + mobs.SMOKE:
            key = ("death", mob, int(gone_for * FPS))
            pic = cache.get(key)
            if pic is None:
                pic = cache[key] = mobs.frame(settings, mob, "death", key[2] / FPS, size=(CELL[0] - 12, 168),
                                              scale=_scale(mob), cache=cache.setdefault("mobs", {}))
            img.alpha_composite(pic, (cx + 6, cy + 6))
        else:  # out: a red cross where it stood
            d.line([(cx + 95, cy + 55), (cx + 155, cy + 115)], fill=(230, 50, 50, 200), width=12)
            d.line([(cx + 155, cy + 55), (cx + 95, cy + 115)], fill=(230, 50, 50, 200), width=12)
        name = mob.upper()
        d.text((cx + CELL[0] / 2, cy + CELL[1] - 20), name, font=_font(22 if len(name) < 13 else 18),
               fill=(255, 255, 255) if gone_for is None else (150, 150, 156), anchor="mm", stroke_width=3,
               stroke_fill=OUTLINE)
        if pick == i:
            d.rounded_rectangle(box, radius=20, outline=GOLD, width=10)
        if winner:
            pulse = int(4 + 3 * math.sin(t * 10))
            d.rounded_rectangle([box[0] - pulse, box[1] - pulse, box[2] + pulse, box[3] + pulse], radius=24,
                                outline=GOLD, width=10)
    return race._framed(img)


def plan(settings: Settings, words: list[Word], script_text: str, seed: str = "", tail: float = 2.0,
         fps: int = 30) -> list[Overlay]:
    """One overlay, the board, from frame 0 to the end: the pause frame jumping until the first question, then the
    knock-outs, then the reveal."""
    from . import race

    if not words:
        return []
    timing = timeline(words, script_text)
    total = words[-1].end + tail
    rng = random.Random(seed or KIND)
    cache: dict = {}
    frames, last = [], None
    for i in range(int((total + 0.5) * FPS) + 1):
        t = i / FPS
        pick = None
        if t < timing["pause_end"]:
            while True:  # never the same mob twice in a row, so every pause lands on a clear one
                pick = rng.randrange(len(MOBS))
                if pick != last:
                    break
            last = pick
        frames.append(board(settings, timing, t, cache, pick))
    out_dir = settings.assets_dir / "cache" / "guessmob"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (re.sub(r"[^a-z0-9]+", "-", (seed or KIND).lower())[:40].strip("-") + ".webp")
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=round(1000 / FPS), loop=1,
                   lossless=False, quality=86)
    return [Overlay(path, 0.0, round(total + 0.5, 3), center=race.CENTER, box=max(race.SIZE), style="asis",
                    cut=True, animated=True)]
