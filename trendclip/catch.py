"""Catch the Zombie: a Gamer IQ level with no question. A pixel zombie (drawn here, not Mojang's) pops up a few
times in the gameplay (three to six, and the video never says how many, so viewers watch to the end), each time
holding a different random emoji, for less and less time: 0.4 s, four frames, then two. The video ends by asking
which emojis it held; all of them right scores on the leaderboard. Viewers pause and replay to catch them, which
is the point.

The level is picked in quiz._write_gamer: on CATCH_GAME's videos, once no catch level sits in the last
CATCH_EVERY - 1 levels (or "catch" in the Create tab's notes). It needs no Gemini.
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path
from types import SimpleNamespace

from .config import Settings
from .video_assembler import HEIGHT, WIDTH, Overlay, Word

logger = logging.getLogger(__name__)

SHAPE = "catch"
# Single-code-point emoji that read at a glance and that the Noto set has (the leaderboard matches them 1:1).
EMOJI = list("👺🍕🚀🐸🎃👑🦄🍩🔥⚡🎸🍌🐙🎯👽🤖🦖🧀🎈💎🌵🍄🐧🦋🍉⚽🎲🐔🌮🍔")
COUNT = (3, 6)  # how many times it shows up, random; the script never says (the owner's call, 2026-11-03)
POPS = (0.4, 0.13, 0.067)  # how long each appearance lasts: quick, four frames, then two frames for all the rest
BOX = 640  # the sprite's usual height on the 1080 x 1920 canvas: the emoji in its hands must read in 4 frames
SIZES = (440, 540, 640, 760, 880)  # each pop picks one, so the eye can't settle on a shape (the owner, 2026-11-03)
MARGIN = 2.0  # never in the first seconds (the banner) nor right before the question
# Where the zombie may stand: the upper half, clear of the captions (lower middle) and the Shorts buttons (right).
SPOTS = ((340, 520), (640, 520), (340, 700), (640, 700), (340, 880), (640, 880))
JITTER = 60
# The opening (the Shorts thumbnail is the first frame): the zombie stands below the captions holding a "?"
# while the banner shows, so the grid already says what the game is.
CARD_EMOJI = "\u2753"
CARD_AT, CARD_UNTIL, CARD_CENTER, CARD_BOX = 0.2, 3.0, (WIDTH // 2, 1580), 640

HOOK = "{game} fans only."
SCRIPT = ("{game} fans only. A zombie is hiding in this video, more than once, each time holding a different emoji. "
          "How many times? I won't tell. Ready? Here comes the first. Keep your eyes open. Don't blink. "
          "Still watching? Pros only. Catch them all? What emojis did the zombie hold? Comment every one below! "
          "Level {next} drops next. Subscribe so you don't miss it!")
WINDOW = ("Ready", "Catch")  # the pops happen between these two spoken words
TITLE = "Can You Catch The Zombie? | Level {level} 🎮"
CARD = "LEVEL {level} - CATCH THE ZOMBIE"
PINNED = ("🧟 The zombie showed up more than once. Which emojis did it hold? Comment every one 👇\n"
          "Hint: pause and step frames, the last ones last 2 frames. How many? That's the game.\n"
          "🏆 All of them = 10 leaderboard points. Level {next} drops next!")
DESCRIPTION = "A zombie hides a few times in this video, each time with a different emoji. Catch them all and comment them."
HASHTAGS = ["#shorts", "#minecraft", "#gaming", "#catchthezombie", "#quiz"]

# The sprite, Minecraft style: 16 x 32 cells (head 8 x 8, body 8 x 12, legs 4 x 12), the arms raised forward so
# only the hands show, holding the emoji in front of the chest.
CELL = 14
SKIN, SKIN_DARK, EYE, MOUTH = (82, 140, 62), (62, 110, 46), (28, 28, 28), (48, 80, 36)
SHIRT, SHIRT_DARK, PANTS, PANTS_DARK = (0, 150, 150), (0, 112, 112), (72, 62, 122), (54, 46, 94)


def wanted(settings: Settings, game: str, notes: str = "") -> bool:
    """A catch level now: asked for in the notes, or CATCH_GAME's video with no catch level in the last
    CATCH_EVERY - 1 levels."""
    from . import quiz

    if "catch" in (notes or "").lower():
        return True
    every = settings.catch_every
    if every <= 0 or not re.search(rf"\b{re.escape(settings.catch_game)}\b", game, re.I):
        return False
    recent = quiz.history(settings, "gamer")[-(every - 1):] if every > 1 else []
    return all(d.get("shape") != SHAPE for d in recent)


def pick_emoji(rng: random.Random, count: int | None = None) -> list[str]:
    return rng.sample(EMOJI, count or rng.randint(*COUNT))


def script(settings: Settings, game: str, *, format: str = "short", rng: random.Random | None = None,
           level: int | None = None, record: bool = True):
    """The level's ShortScript: fixed narration, the emoji in `catch` (one per appearance), the answer for the
    leaderboard."""
    from . import quiz

    rng = rng or random.Random()
    level = level or quiz.gamer_level(settings)
    emojis = pick_emoji(rng)
    answer = " ".join(emojis)
    name = quiz.game_name(game)
    text = SCRIPT.format(game=name, next=level + 1)
    if record:
        quiz._remember(settings, "gamer", answer=answer, question="Catch the zombie", game=game, shape=SHAPE, level=str(level))
    # No flash while the zombie can appear (a flash would cover it): one before the window, one right after.
    flashes = [quiz.Flash(word="once", text="x?"), quiz.Flash(word="Catch", text="CATCH THEM ALL?!", color="green")]
    pack = SimpleNamespace(title=TITLE.format(level=level), description=DESCRIPTION, hashtags=HASHTAGS,
                           title_card=CARD.format(level=level))
    out = quiz._script(settings, game, "builtin", "gamer", format, text, HOOK.format(game=name), flashes, answer,
                       PINNED.format(next=level + 1), pack, on_screen=f"The zombie holds: {answer}",
                       card=CARD.format(level=level), end_card=quiz.LOOP_CARD)
    return out.model_copy(update={"catch": emojis, "reactions": []})


# --------------------------------------------------------------------------- the sprite


def _grid() -> list[tuple[int, int, int, int, tuple[int, int, int]]]:
    """(x, y, w, h, colour) rectangles in cells."""
    r = []
    r += [(4, 0, 8, 8, SKIN), (4, 0, 8, 1, SKIN_DARK), (4, 7, 8, 1, SKIN_DARK)]  # head
    r += [(5, 3, 2, 1, EYE), (9, 3, 2, 1, EYE), (7, 5, 2, 1, MOUTH), (6, 6, 1, 1, MOUTH), (9, 6, 1, 1, MOUTH)]
    r += [(4, 8, 8, 12, SHIRT), (4, 8, 8, 1, SHIRT_DARK), (4, 14, 8, 1, SHIRT_DARK)]  # body
    r += [(0, 8, 4, 2, SHIRT), (12, 8, 4, 2, SHIRT)]  # shoulders (the arms go forward)
    r += [(4, 20, 4, 12, PANTS), (8, 20, 4, 12, PANTS_DARK), (4, 31, 8, 1, (40, 34, 70))]  # legs
    return r


def draw_zombie(emoji_png: Path | None, out: Path) -> Path:
    """The zombie PNG, hands forward holding `emoji_png` (a transparent sticker) in front of the chest."""
    from PIL import Image, ImageDraw

    w, h = 16 * CELL, 32 * CELL
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for x, y, cw, ch, colour in _grid():
        d.rectangle([x * CELL, y * CELL, (x + cw) * CELL - 1, (y + ch) * CELL - 1], fill=colour + (255,))
    # The hands: two green squares at chest height, in front of the body, the emoji resting on them.
    for hx in (2, 10):
        d.rectangle([hx * CELL, 11 * CELL, (hx + 4) * CELL - 1, 15 * CELL - 1], fill=SKIN + (255,))
        d.rectangle([hx * CELL, 11 * CELL, (hx + 4) * CELL - 1, 12 * CELL - 1], fill=SKIN_DARK + (255,))
    if emoji_png is not None and emoji_png.is_file():
        em = Image.open(emoji_png).convert("RGBA")
        side = 10 * CELL
        em.thumbnail((side, side))
        img.alpha_composite(em, ((w - em.width) // 2, 9 * CELL))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def sprites(settings: Settings, emojis: list[str]) -> list[Path]:
    """One zombie PNG per emoji (the emoji sticker comes from the pop-up cache, Noto / Fluent)."""
    from . import popups

    out = []
    for e in emojis:
        asset = popups.fetch_asset(settings, popups.Popup(word="zombie", emoji=e))
        png = settings.assets_dir / "popups" / asset.filename if asset else None
        if png is None:
            logger.warning("No sticker for %s; the zombie holds nothing", e)
        out.append(draw_zombie(png, settings.assets_dir / "catch" / f"zombie-{ord(e):x}.png"))
    return out


# --------------------------------------------------------------------------- placement


def _word_time(words: list[Word], text: str, start: bool, default: float) -> float:
    low = text.lower()
    hits = [w for w in words if re.sub(r"[^a-z]", "", w.text.lower()) == low]
    if not hits:
        return default
    return hits[0].start if start else hits[-1].end


def card_overlay(settings: Settings) -> Overlay:
    """The zombie holding "?" under the opening banner (pops in like a sticker, no catching needed)."""
    image = sprites(settings, [CARD_EMOJI])[0]
    return Overlay(image, CARD_AT, CARD_UNTIL, center=CARD_CENTER, box=CARD_BOX, style="asis")


def plan(settings: Settings, emojis: list[str], words: list[Word], seed: str = "") -> list[Overlay]:
    """The opening zombie with its "?", then the appearances: random moments between the spoken "Ready" and
    the closing "Catch", one in each equal share of that window so they stay apart, each in a different spot
    and a random size, each shorter than the last."""
    if not emojis or not words:
        return []
    rng = random.Random(seed or " ".join(emojis))
    total = words[-1].end
    begin = max(_word_time(words, WINDOW[0], True, MARGIN), MARGIN)
    close = min(_word_time(words, WINDOW[1], True, total - MARGIN), total - MARGIN)
    if close - begin < 3.0:  # the voice is faster than expected: use the middle of the video
        begin, close = MARGIN, max(total - MARGIN, MARGIN + 3.0)
    images = sprites(settings, emojis)
    out = [card_overlay(settings)]
    span = (close - begin) / len(emojis)
    spots = rng.sample(SPOTS, min(len(emojis), len(SPOTS)))  # different places
    for i, (image, (x, y)) in enumerate(zip(images, spots)):
        hold = POPS[min(i, len(POPS) - 1)]
        box = rng.choice(SIZES)
        at = begin + span * i + rng.uniform(0.2, max(0.3, span - hold - 0.2))
        x = min(max(x + rng.randint(-JITTER, JITTER), box // 2), WIDTH - box // 2 - 120)
        y = min(max(y + rng.randint(-JITTER, JITTER), box // 2 + 120), HEIGHT // 2 + 260)
        at = round(at, 3)
        out.append(Overlay(image, at, round(at + hold, 3), center=(x, y), box=box, style="asis", cut=True))
    return out
