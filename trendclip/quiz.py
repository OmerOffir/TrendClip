"""Quiz Shorts ("brain workout"): viewers play along and comment their answer.

- math: a fast mental-math chain. The numbers are picked here, so the answer is always right; Gemini only
  writes the hook, title and captions (with a built-in fallback when Gemini is busy).
- riddle: Gemini writes a riddle told in 2-3 clues plus a final question.
- trivia: a 3-option speed question built on a surprising true fact.
- paradox: a mini story that traps the viewer between two answers that both seem right; viewers pick a
  side in one word ("Comment KEEP or RETURN"), which starts arguments in the replies.
- movie: a famous movie, in one of three shapes picked in turn: a misquote trap (what did the character
  ACTUALLY say? A / B / C, fact-checked on Google), guess the movie from 3 emojis (pop-up stickers), or a
  movie-logic debate (two sides, like a paradox).
- gamer: "Gamer IQ", a 3-option question about the game in the background (a number players meet, the fake
  one, which came first, a fact from its makers), fact-checked on Google and numbered as a level of a series.

Each number / clue flashes big in the middle of the screen the moment it is spoken (`Flash`), the video
ends on a big "?" and "COMMENT YOUR ANSWER" (trivia and emoji movie: 12-15 s loops ending on a green
"GOT IT?" and "DROP YOUR ANSWER BELOW!"), and the answer goes in the pinned comment, never on screen.
"""

from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .popups import _matches, _norm

if TYPE_CHECKING:
    from .config import Settings
    from .script_writer import ProgressFn, ShortScript

logger = logging.getLogger(__name__)

QuizKind = Literal["math", "riddle", "trivia", "paradox", "movie", "gamer"]
QUIZ_MODES = ("math", "riddle", "trivia", "paradox", "movie", "gamer")
FLASH_SECONDS = 1.4  # each step stays up 1.2-1.5 s (or until the next one)
LAST_FLASH_SECONDS = 3.0
END_CARD = "COMMENT YOUR ANSWER"
MUSIC_MOOD = "funny_quirky"  # the upbeat folder


class Flash(BaseModel):
    """Big text that flashes in the middle of the screen when `word` is spoken."""

    word: str = Field(min_length=1, max_length=40, description="The spoken word that triggers it.")
    text: str = Field(min_length=1, max_length=16, description="What is shown, e.g. '+18' or 'CLUE 1'.")
    color: str = Field("", max_length=12, description="'' (the caption highlight colour) or e.g. 'green'.")
    y: float | None = Field(None, ge=0.05, le=0.95, description="Height on screen (0-1); None: the usual spot.")


@dataclass
class TimedFlash:
    text: str
    start: float
    end: float
    color: str = ""
    y: float | None = None


def schedule_flashes(flashes: list[Flash], words: list, total: float | None = None) -> list[TimedFlash]:
    """Each flash starts on the first matching spoken word after the previous flash (numbers repeat).
    A several-word `word` ("it Venus") must be spoken in a row and starts on its first word."""
    if not words:
        return []
    total = total if total is not None else words[-1].end + 0.6
    spoken = [_norm(w.text) for w in words]
    starts: list[tuple[Flash, float]] = []
    cursor = 0
    for flash in flashes:
        tokens = [t for t in (_norm(w) for w in flash.word.split()) if t]
        hit = next((i for i in range(cursor, len(words) - len(tokens) + 1) if tokens
                    and all(_matches(spoken[i + k], t) for k, t in enumerate(tokens))), None)
        if hit is None:
            continue
        starts.append((flash, max(words[hit].start - 0.05, 0.0)))
        cursor = hit + len(tokens)
    out = []
    for i, (flash, start) in enumerate(starts):
        last = i + 1 == len(starts)
        limit = total if last else starts[i + 1][1] - 0.05
        end = min(start + (LAST_FLASH_SECONDS if last else FLASH_SECONDS), limit)
        if end - start >= 0.3:
            out.append(TimedFlash(flash.text, round(start, 3), round(end, 3), flash.color, flash.y))
    return out


# --------------------------------------------------------------------------- math chain


class Step(BaseModel):
    op: Literal["+", "-", "×", "÷"]
    value: int
    trick: str = ""  # a key of TRICKS: worded to trip people up, its value follows the running total


# Steps worded to trip people up: (spoken, flashed, spoken trigger word, applies to the running total v,
# usable on v). "Divide by half" is ×2: most people halve.
TRICKS = {
    "div_half": ("Divide by half.", "÷ HALF", "Divide by half", lambda v: v * 2, lambda v: v <= 120),
    "add_half": ("Add half of it.", "+ HALF", "half of", lambda v: v + v // 2, lambda v: v % 2 == 0 and 20 <= v <= 200),
    "sub_third": ("Take away a third of it.", "- A THIRD", "third", lambda v: v - v // 3,
                  lambda v: v % 3 == 0 and 18 <= v <= 300),
    "square": ("Square it.", "SQUARED", "Square", lambda v: v * v, lambda v: 4 <= v <= 12),
    "double": ("Double it.", "×2", "Double", lambda v: v * 2, lambda v: 15 <= v <= 150),
}
TRICK_OP = {"div_half": "×", "add_half": "+", "sub_third": "-", "square": "×", "double": "×"}
MATH_TRICKS = 2  # per chain; "Divide by half" in most of them


# No "Plus" / "Minus": they are French words too, and multilingual voices read "Plus 27." as French.
SAY = {
    "+": ("Add {n}.",),
    "-": ("Subtract {n}.", "Take away {n}."),
    "×": ("Multiply by {n}.", "Times {n}."),
    "÷": ("Divide by {n}.",),
}
QUIZ_RATE = 5  # % faster speech at least: en-US-AndrewNeural at +5% lands a step every ~1.2 s
CAPTION_MARGIN = 620  # vertical: captions end at 68% of the height...
FLASH_Y = 0.39  # ...and the big flash sits above them; a 3-line caption still clears it (and the title card)
TOP_FLASH_Y = 0.2  # above a row of pop-ups, where the title card was (it is gone after 3 s)


def english_voice(voice: str) -> str:
    """Multilingual voices guess the language of short lines ("Plus six" came out French); quizzes use
    the English-only version of the same voice, e.g. en-US-AndrewMultilingualNeural → en-US-AndrewNeural."""
    return voice.replace("MultilingualNeural", "Neural")


def quiz_rate(rate: str) -> str:
    m = re.fullmatch(r"([+-]\d{1,3})%", rate or "")
    return f"+{max(int(m.group(1)) if m else 0, QUIZ_RATE)}%"


def render_style(landscape: bool) -> dict:
    """assemble_video options for a quiz: a large pulsing dare on frame 0, numbers + captions in the middle,
    music at full level from the first frame (a slow fade-in reads as a slow start and viewers swipe)."""
    style: dict = {"title_pulse": True, "title_chars": 16, "title_size": 148, "music_fade_in": 0.0}
    if not landscape:
        style |= {"margin_v": CAPTION_MARGIN, "flash_y": FLASH_Y}
    return style


def digit_hint(answer: int) -> str:
    word = {1: "one digit", 2: "two digits", 3: "three digits"}.get(len(str(abs(int(answer)))), f"{len(str(abs(answer)))} digits")
    return f"it's {word}"


def safe_hint(hint: str, *forbidden: str) -> str:
    """A hint viewers can use, with the answer itself removed."""
    text = " ".join((hint or "").split()).strip(" .!")
    low = text.lower()
    if not text or any(len(word) > 2 and word.lower() in low for word in forbidden):
        return ""
    return text[:80]


def hint_from_fact(fact: str, *banned: str) -> str:
    text = fact or ""
    for word in banned:
        if len(word) > 2:
            text = re.sub(re.escape(word), "", text, flags=re.I)
    match = re.search(r"\d[\d,.]*(?:\s*°\s*[CF])?(?:\s+[A-Za-z]+)?", text)
    return match.group(0).strip(" .,;") if match else "the obvious guess is wrong"


def public_pin(ask: str, hint: str) -> str:
    """The comment to pin on publish: a question, then a hint. Never the answer."""
    hint = hint.strip().rstrip(".!")
    hint = re.sub(r"(?i)^hint:\s*", "", hint)
    return f"{ask.strip()} 👇\nHint: {hint}."


_DARE = re.compile(r"\d+\s*%|don't pause|do not pause|\bstop\b", re.I)


def frame_zero(card: str, hook: str) -> str:
    """The frame-0 banner: a rarity dare or a hard stop, taken from the card or the spoken hook."""
    card = " ".join((card or "").split())
    if _DARE.search(card) and len(card.split()) >= 3:
        return card
    words = re.sub(r"[\"']", "", hook or "").split()[:6]
    return " ".join(words).rstrip(".!?") or "ONLY 3% CAN SOLVE THIS"


def with_emoji(title: str, emoji: str = "🧠") -> str:
    title = title.strip()
    if any(ord(ch) > 1000 for ch in title):
        return title
    return f"{title} {emoji}".strip()


def apply(value: int, step: Step) -> int:
    if step.trick:
        return TRICKS[step.trick][3](value)
    if step.op == "+":
        return value + step.value
    if step.op == "-":
        return value - step.value
    if step.op == "×":
        return value * step.value
    return value // step.value


def _awkward(rng: random.Random, low: int, high: int) -> int:
    """A number that is hard to add in your head: no multiples of 5 when it can be helped."""
    picks = [n for n in range(low, high + 1) if n % 5]
    return rng.choice(picks or [low])


def trick_step(trick: str, value: int) -> Step:
    after = TRICKS[trick][3](value)
    op = TRICK_OP[trick]
    amount = after // value if op == "×" else abs(after - value)
    return Step(op=op, value=amount, trick=trick)


def _next_step(rng: random.Random, value: int, last_op: str | None, trick: str | None = None,
               last_value: int | None = None) -> Step | None:
    if trick:
        step = trick_step(trick, value) if TRICKS[trick][4](value) and TRICK_OP[trick] != last_op else None
        return None if step and (last_op, step.value) == (UNDO.get(step.op), last_value) else step
    options = []
    divisors = [d for d in range(3, 10) if value % d == 0 and value // d >= 3]
    if divisors:
        options.append(Step(op="÷", value=rng.choice(divisors)))
    if value <= 30:
        options.append(Step(op="×", value=rng.randint(3, 9 if value <= 12 else 6 if value <= 20 else 4)))
    if value <= 300:
        options.append(Step(op="+", value=_awkward(rng, 13, 49)))
    if value > 25:
        options.append(Step(op="-", value=_awkward(rng, 13, min(49, value - 8))))
    options = [o for o in options if o.op != last_op and (last_op, o.op, o.value) != (UNDO.get(o.op), o.op, last_value)]
    return rng.choice(options) if options else None


UNDO = {"÷": "×", "×": "÷", "+": "-", "-": "+"}  # "Times 3. Divide by 3." cancels out: too easy


def make_chain(rng: random.Random | None = None, steps: int = 7) -> tuple[int, list[Step]]:
    """A start number and `steps` operations, built to be hard: awkward two-digit numbers, ×3-×9 and
    ÷3-÷9, and MATH_TRICKS steps worded to trip people up ("Divide by half." is ×2). Whole numbers only,
    at least one × and one ÷, never the same operation twice in a row, answer 21-399."""
    rng = rng or random.Random()
    for _ in range(2000):
        value = start = _awkward(rng, 14, 64)
        tricks = ["div_half"] if rng.random() < 0.75 else []
        tricks += rng.sample([t for t in TRICKS if t != "div_half"], MATH_TRICKS - len(tricks))
        rng.shuffle(tricks)
        at = sorted(rng.sample(range(1, steps), MATH_TRICKS))  # never the first step
        plan = dict(zip(at, tricks))
        chain: list[Step] = []
        for i in range(steps):
            step = _next_step(rng, value, chain[-1].op if chain else None, plan.get(i),
                              chain[-1].value if chain else None)
            if step is None:
                break
            chain.append(step)
            value = apply(value, step)
        ops = {s.op for s in chain if not s.trick}
        if len(chain) == steps and {"×", "÷"} <= ops and 21 <= value <= 399 and value % 10:
            return start, chain
    return 34, [Step(op="-", value=13), Step(op="×", value=4), Step(op="÷", value=7), trick_step("div_half", 12),
                Step(op="+", value=27), trick_step("sub_third", 51), Step(op="+", value=29)]


def solve(start: int, chain: list[Step]) -> list[int]:
    """The running total after the start and after each step."""
    values = [start]
    for step in chain:
        values.append(apply(values[-1], step))
    return values


def trap_answer(start: int, chain: list[Step]) -> int:
    """The number most people get: "Divide by half" read as ÷2, else one step in the middle missed."""
    answer = solve(start, chain)[-1]
    if (wrong := misread(start, chain)) is not None and wrong != answer and wrong > 0:
        return wrong
    for skip in (2, 3, 1, 4):
        if skip < len(chain):
            rest = chain[:skip] + chain[skip + 1:]
            wrong = solve(start, rest)[-1]
            if wrong != answer and wrong > 0:
                return wrong
    return answer + 10


def misread(start: int, chain: list[Step]) -> int | None:
    """The answer when "Divide by half" is done as ÷2 (None without one, or when it isn't whole)."""
    if not any(s.trick == "div_half" for s in chain):
        return None
    value = start
    for step in chain:
        if step.trick == "div_half":
            if value % 2:
                return None
            value //= 2
        elif step.trick and not TRICKS[step.trick][4](value):
            return None
        elif step.op == "÷" and not step.trick and value % step.value:
            return None
        else:
            value = apply(value, step)
    return value


def _shown(step: Step) -> str:
    if step.trick:
        return f'"{TRICKS[step.trick][0].rstrip(".")}" ({step.op} {step.value})'
    return f"{step.op} {step.value}"


def working(start: int, chain: list[Step]) -> str:
    """'24 + 18 = 42 → ÷ 2 = 21 → "Divide by half" (× 2) = 42 → …' for the pinned comment."""
    values = solve(start, chain)
    parts = [f"{start} {_shown(chain[0])} = {values[1]}"]
    parts += [f"{_shown(s)} = {v}" for s, v in zip(chain[1:], values[2:])]
    return " → ".join(parts).replace(" - ", " − ")


def math_lines(start: int, chain: list[Step], rng: random.Random) -> tuple[list[str], list[Flash]]:
    lines = [f"Start with {start}."]
    flashes = [Flash(word=str(start), text=str(start))]
    for step in chain:
        if step.trick:
            said, shown, trigger = TRICKS[step.trick][:3]
            lines.append(said)
            flashes.append(Flash(word=trigger, text=shown))
            continue
        lines.append(rng.choice(SAY[step.op]).format(n=step.value))
        flashes.append(Flash(word=str(step.value), text=f"{step.op}{step.value}"))
    return lines, flashes


# --------------------------------------------------------------------------- Gemini packaging


QUIZ_INSTRUCTION = """You package interactive "brain workout" quiz Shorts (YouTube Shorts, TikTok, Reels). The
viewer plays along in their head, then comments their answer: that is the whole point. Style: bold, fast,
competitive, a little cheeky. The voiceover starts straight on the puzzle (the question, the first number,
the story), because viewers decide in the first second; the dare lives on the frame-0 banner and in the
title. Vary the dare's kind from video to video: a rarity claim ("Only 3% ..."), a hard stop ("Stop. Do
not pause."), a bet ("Bet you can't ..."), a callout ("Gamers, this one's for you."), a time limit ("You
have five seconds."). Every quiz is at most 20 seconds: short sentences, no filler. Titles end with one
emoji (🧠 or 🛑).
Never reveal the answer in the title, description, title card, hint or hashtags. Plain English."""


class GeminiMathPack(BaseModel):
    hook: str = Field(description=(
        "The opening sentence, at most 8 words (it must be spoken in under 3 seconds), a dare with a rarity "
        "claim, e.g. 'Only 5% finish this without pausing.' or 'Only geniuses get this one right.' "
        "No numbers from the chain."))
    title: str = Field(description="YouTube title, at most 60 characters, no hashtags, no answer, e.g. 'Only 5% Get This Right 🧠'.")
    description: str = Field(description="One or two short sentences daring viewers to comment their answer, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 hashtags, e.g. #shorts #math #brainteaser #mathchallenge #quiz.")
    title_card: str = Field(description=(
        "3 to 7 word ALL-CAPS banner on the first frame, a rarity dare or a hard stop, e.g. 'ONLY 3% CAN SOLVE THIS' "
        "or 'STOP DO NOT PAUSE'. No emoji."))


class GeminiRiddle(BaseModel):
    hook: str = Field(description=(
        "The opening line, spoken from the first word: the riddle's most paradoxical idea asked as a question, "
        "at most 12 words, e.g. 'What weighs nothing, but you can't hold it for ten minutes?' (the form only: "
        "never reuse an example or a famous riddle). Never a percentage or an 'only X% solve this' dare, never the answer."))
    clues: list[str] = Field(description=(
        "2 or 3 more short spoken clues after the opening question, one sentence each (at most 14 words), each "
        "adding something new that narrows it down, e.g. 'You can see me on freezing days.' Together with the "
        "opening they must point to exactly one fair answer."))
    question: str = Field(description="The final question, 2 to 6 words, e.g. 'What am I?' or 'Who is the thief?'")
    answer: str = Field(description="The one correct answer, 1 to 5 words, e.g. 'A piano'.")
    explanation: str = Field(description="One short sentence on why every clue fits the answer.")
    title: str = Field(description="YouTube title, at most 60 characters, no hashtags, no answer, e.g. 'Only 5% Solve This Riddle 🧩'.")
    description: str = Field(description="One or two short sentences daring viewers to comment their answer, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 hashtags, e.g. #shorts #riddle #brainteaser #puzzle #quiz.")
    hint: str = Field("", description=(
        "One spoiler-free hint that makes people want to comment, with a number or a comparison when you can, "
        "e.g. 'over 33,000 miles'. Never the answer itself."))
    title_card: str = Field(description=(
        "The opening paradox as a 3 to 6 word ALL-CAPS banner on the first frame, ending in '?', at most 36 "
        "characters, e.g. 'WEIGHS NOTHING. CAN'T HOLD IT?'. No emoji, no percentages."))
    bonus: str = Field("", description=(
        "A follow-up challenge for the pinned comment: a different, easier one-line riddle, e.g. 'What can you "
        "catch but never throw?' (the form only: invent a new one). Not its answer."))


MATH_PROMPT = """Package this RAPID MATH CHAIN Short. The voiceover reads the chain fast, one step every
1.2-1.5 seconds, then asks viewers to comment their final number. It is built to be hard: awkward numbers
and steps worded to trip people up (e.g. "Divide by half" is ×2, most people halve), so most viewers get
it wrong and argue in the comments. The chain (for you only, don't repeat it or hint at the trick):
{chain} (answer {answer}).
Write the hook, title, description, hashtags and title card. Topic flavour for the game background: {game}.{notes}"""

RIDDLE_PROMPT = """Write a MULTI-PART RIDDLE Short of at most 18 seconds (about 45 spoken words in all, clues of
at most 12 words). It opens, from the very first word, on
the riddle's most paradoxical idea asked as a question (no "only 3% solve this" stat: viewers swipe past
those), then 2-3 more clues (spoken one at a time, each one making the viewer rethink), then the question.
Viewers comment their guess.
Make it clever but fair: one clear answer that clicks when revealed, not a pun only you would get, and not
an overused internet classic (no "What has keys but can't open locks", "What gets wetter the more it dries",
"What do you throw away only when you need it", "The more you take, the more you leave behind"): invent a
fresh one.
This one's answer or setting: {topic}. Its style: {style}.
Already used on the channel, don't reuse these answers: {used}.
Double-check that every clue fits the answer. Background gameplay: {game}.{notes}"""

RIDDLE_TOPICS = (
    "something in the kitchen", "a part of the human body", "the weather", "an animal", "a sea creature",
    "an insect", "a plant or tree", "an item, mechanic or place in video games", "a game controller or console",
    "something at school", "a letter of the alphabet or a word", "a number", "time, a clock or a calendar",
    "a household object", "a vehicle", "something in space", "a musical instrument or sound", "money",
    "a feeling", "clothing", "a food", "light, fire or a shadow", "a phone, a computer or the internet",
    "a sport or a ball", "a tool", "a building or a room", "a map, a road or directions", "a family relation",
    "a body of water", "a mirror or a reflection", "a board game, cards or dice", "a job", "a toy",
    "a holiday or a party", "a famous landmark", "the human senses", "an everyday habit", "sleep or dreams",
)
RIDDLE_STYLES = (
    "'What am I?' told in the first person", "'What am I?' told in the first person",
    "a short who-did-it with a hidden clue (ask 'Who did it?')",
    "a lateral-thinking situation that sounds impossible (ask 'How?')",
    "a word or letter riddle (ask 'What word is it?')",
)


class GeminiTrivia(BaseModel):
    category: str = Field(description="video games, science, geography, history, animals, space, food or the human body.")
    hook: str = Field("", description=(
        "A spoken dare before the question, at most 7 words, e.g. 'Only 5% get this right.' or '90% fall for "
        "this one.'"))
    question: str = Field(description=(
        "The trap question, at most 10 words, 'Which [category item] is [surprising condition]?', e.g. 'Which "
        "country has the most pyramids?' or 'Which planet is the hottest?'"))
    trap: str = Field(description=(
        "The obvious answer about 90% of people pick instinctively, but WRONG, 1 to 4 words, as spoken after "
        "'Is it', with 'the'/'a' where English needs it, e.g. 'Egypt', 'Mercury', 'the heart', 'the Six-Day War'."))
    distractor: str = Field(description="A plausible but wrong option, 1 to 4 words, same style, e.g. 'Mexico' or 'the brain'.")
    correct: str = Field(description=(
        "The mind-bending CORRECT answer, 1 to 4 words, same style, e.g. 'Sudan', 'Venus' or 'the liver'. Must be "
        "true, well documented and easy to verify on Google."))
    fact: str = Field(description=(
        "One or two short sentences proving the correct answer with the real numbers, e.g. 'Sudan has about 255 "
        "pyramids, Egypt about 120.' Used to build the hint; the answer itself stays off the video."))
    title: str = Field(description="YouTube title, at most 60 characters, no hashtags, never the answer, e.g. '90% Get This Wrong 🌍'.")
    description: str = Field(description="One or two short sentences daring viewers to comment the real answer, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 hashtags, e.g. #shorts #trivia #quiz #geography #funfacts.")
    hint: str = Field("", description=(
        "One spoiler-free hint with the surprising number or comparison, never naming the correct option, "
        "e.g. 'over 33,000 miles' or 'hotter than 400 degrees'. Shown at the end so people comment."))
    title_card: str = Field(description=(
        "3 to 7 word ALL-CAPS challenge banner with a low pass rate, e.g. '90% GET THIS TRIVIA TRAP WRONG!' or "
        "'ONLY 5% SOLVE THIS GEOGRAPHY TRAP!'. No emoji."))


TRIVIA_PROMPT = """Write {count} different GAMIFIED FACT TRAP candidates for a Short (12-15 seconds), one per
category, in this order: {categories}. Each is a speed trivia question built on a counter-intuitive but
TRUE fact, with three options: the trap most people pick instinctively, a plausible distractor, and the
surprising correct answer. The viewer must feel sure of the trap, then doubt it, and comment to argue.

What makes a candidate good:
- ONE answer is right under the exact wording, by every reputable source, with no "it depends": no
  "deadliest" (by toxicity or by deaths?), "biggest" (by area, mass or height?), "most" of things counted in
  different ways, unless the question says how it's measured ("by land area", "by venom toxicity").
- SURPRISING: most adults would confidently pick the trap. The trap is the popular belief (the famous
  one, the bigger-sounding one), clearly wrong once checked; the distractor is believable. If the correct
  answer is common knowledge (Ganymede biggest moon, the liver regrows), it's not a trap: pick another fact.
- Video games: a well-documented fact about one famous game or character, told by its makers (origins,
  original names, what inspired a design, cut content), e.g. "What was Mario first called? Jumpman".
  Never "the first game ever to..." (historians argue), current sales, player counts or "most popular".
- Not outdated, not a myth, not a trick question. A 12-year-old understands it at once.
- Every option short and easy to say, written as spoken after "Is it ...?" ("the Philippines", "the
  Pacific", "a dolphin").
- Not one of these, already used on the channel: {used}. Questions already asked: {questions}.
  And not the overused ones (Sudan pyramids,
  Venus hottest planet, Canada lakes, Alaska coastline) unless the creator's notes ask for them.
Background gameplay: {game}.{notes}"""

TRIVIA_CATEGORIES = ("geography", "animals", "space", "science", "history", "food", "the human body")
TRIVIA_HOOKS = ("90% fall for this one.", "Your gut will lie to you here.", "Almost everyone picks wrong.",
                "Bet you pick the trap.", "Gamers always miss this one.", "Trust your gut? Big mistake.",
                "This one fools almost everyone.", "Only 5% get this right.")

FACT_CHECK = """Fact-check these trivia questions with Google Search. For each candidate, PASS only if ALL hold:
1. The claimed correct answer is true today, by reputable sources.
2. Under the exact wording, it is the ONLY right option: the other two are clearly wrong, and no common
   reading of the question (other measure, other definition) makes another option right.
3. The fact is not disputed, outdated or a popular myth.
4. It's a real trap: most adults would pick the wrong "trap" option, and the correct answer isn't common
   knowledge.
Reply with exactly one line per candidate, for every candidate, and nothing else:
"<number>: PASS" or "<number>: FAIL: <reason in one sentence>".

{candidates}"""


class GeminiTriviaSet(BaseModel):
    candidates: list[GeminiTrivia] = Field(description="The trivia traps, one per requested category, in order.")


class GeminiGamer(BaseModel):
    question: str = Field(description=(
        "The question, at most 12 words, naming the game, e.g. 'How many obsidian blocks make the smallest Minecraft "
        "Nether portal?' (the form only: never reuse an example)."))
    trap: str = Field(description=(
        "The answer most casual players pick instinctively, but WRONG, 1 to 4 words, as spoken after 'Is it'."))
    distractor: str = Field(description="A plausible but wrong option, 1 to 4 words, same style.")
    correct: str = Field(description=(
        "The CORRECT answer, 1 to 4 words, same style. True in the current version of the game, well documented "
        "on the game's wiki or by its makers, easy to verify on Google."))
    fact: str = Field(description=(
        "One or two short sentences proving the correct answer with the real numbers or names. Used to build the "
        "hint; the answer itself stays off the video."))
    short_name: str = Field(description="The game's short name in capitals, at most 12 characters, e.g. 'MINECRAFT', 'GTA', 'CS2'.")
    title: str = Field(description=(
        "YouTube title, at most 45 characters, no hashtags, never the answer, aimed at the game's fans, e.g. "
        "'Real Minecraft Players Know This' or 'GTA Fans Always Miss This One'. No emoji."))
    description: str = Field(description="One or two short sentences daring the game's fans to comment the answer, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 hashtags: #shorts, the game's own tags (e.g. #minecraft), #gaming, #quiz.")
    hint: str = Field("", description=(
        "One spoiler-free hint with a number or a comparison, never naming the correct option, e.g. 'fewer than "
        "you need for a full frame'."))


class GeminiGamerSet(BaseModel):
    candidates: list[GeminiGamer] = Field(description="The questions, all about the same game.")


# Every shape is about the game in the background: its fans recognise it in the first frame and argue in the
# comments; anyone else swipes, which is fine.
GAMER_SHAPES = {
    "number": ("a number every real player has met: a crafting recipe, a price, a stack or magazine size, a "
               "health or level cap, a map count. Only numbers that patches don't change"),
    "fake": ("'Which one is NOT in {game}?': two real items, characters, places or weapons and one convincing "
             "fake. The correct answer is the fake; the trap is a real but obscure one that sounds made up"),
    "first": ("'Which came first?': three things added to the game at documented, different times (updates, "
              "maps, characters, mechanics, games of the series). The correct answer is the oldest"),
    "lore": ("a fact about the game's world, characters or making, told by its makers: original names, what "
             "inspired a design, cut content, hidden details most players walk past"),
}

GAMER_PROMPT = """Write {count} different GAMER IQ questions for a Short (12-15 seconds) about {game}, the
gameplay playing in the background. Shape: {shape}.
Each is a speed question with three options: the trap most casual players pick instinctively, a plausible
distractor, and the correct answer. A real {game} fan should be able to get it, and feel proud; a casual
player should feel sure of the trap, then doubt it, and comment to argue.

What makes a candidate good:
- ONE answer is right under the exact wording, on the game's wiki and by every reputable source, with no
  "it depends" (platform, edition, game mode or patch): say which when it matters ("in Java Edition").
- About {game} itself (or its series), never a generic gaming question.
- Never current player counts, sales, prices of real money, esports results or "most popular".
- Every option short and easy to say, written as spoken after "Is it ...?"; numbers as digits ("16").
- The question alone, at most 14 words: never the options in it (the video reads "Is it A? B? Or C?"),
  and no dare in it ("Only 3% ...", "Don't pause", "You have five seconds").
- The title names the game and speaks to its fans ("Real Minecraft Players Know This"); no dares
  ("Stop", "Don't pause", "Only 3%"). short_name is just the game's short name (MINECRAFT, GTA), not the topic.
- Not one of these, already asked on the channel: {used}.
{notes}"""

GAMER_INTRO = """You write questions for "Gamer IQ", a YouTube Shorts series: each video asks the fans of one game a
question about that game while its gameplay plays in the background, reads three options, counts down from 3
and asks viewers to comment their answer. Every video is the next numbered level. Plain English."""

GAMER_CHECK = """Fact-check these video game questions with Google Search. For each candidate, PASS only if ALL hold:
1. The claimed correct answer is true in the current version of the game, by the game's wiki or its makers.
2. Under the exact wording, it is the ONLY right option: the other two are clearly wrong, and no edition,
   platform, game mode or older patch makes another option right.
3. It is not a rumour, a fan theory or a myth.
4. A dedicated fan can know it, but it is not something every casual player knows.
Reply with exactly one line per candidate, for every candidate, and nothing else:
"<number>: PASS" or "<number>: FAIL: <reason in one sentence>".

{candidates}"""

class GeminiParadox(BaseModel):
    name: str = Field(description="The paradox or dilemma it is based on, e.g. 'the crocodile dilemma' (for the creator only).")
    hook: str = Field(description=(
        "The opening dare, at most 8 words, spoken from the first millisecond, e.g. 'Only 2% can escape this "
        "paradox.' or 'This breaks 97% of brains.'"))
    setup: list[str] = Field(description=(
        "The mini story in 1 spoken sentence (2 only if it can't work otherwise; at most 18 words each), concrete characters and stakes, "
        "e.g. 'A crocodile steals a child and promises to return him if the father can guess what it will do.'"))
    twist: str = Field(description=(
        "The move that springs the trap, one sentence of at most 12 words, e.g. \"The father guesses: 'You will "
        "NOT return my child!'\""))
    dilemma: str = Field(description="The question the viewer must decide, at most 10 words, e.g. 'What should the crocodile do?'")
    side_a: str = Field(description=(
        "The first branch as one sentence starting with 'If', at most 14 words, showing it fails, e.g. 'If he "
        "keeps the child, the father was right, so he must return him.'"))
    side_b: str = Field(description=(
        "The second branch as one sentence starting with 'But if', at most 12 words, showing it fails too, e.g. "
        "'But if he returns the child, the father was wrong!'"))
    vote_a: str = Field(description="One word for picking the first side, e.g. 'KEEP'. Short, plain, easy to type.")
    vote_b: str = Field(description="One word for picking the second side, e.g. 'RETURN'. Different from vote_a.")
    debate: str = Field(description=(
        "The closing either/or question that splits viewers into two camps, at most 16 words, e.g. 'Did the "
        "father just outsmart the crocodile, or is the child trapped forever?'"))
    resolution: str = Field(description=(
        "Two short sentences for the creator: why it is a paradox and how philosophers usually resolve or "
        "classify it. Never spoken."))
    title: str = Field(description="YouTube title, at most 60 characters, no hashtags, e.g. 'This Paradox Breaks 98% of Brains 🤯'.")
    description: str = Field(description="One or two short sentences daring viewers to pick a side in the comments, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 hashtags, e.g. #shorts #paradox #logic #brainteaser #mindblown.")
    title_card: str = Field(description=(
        "3 to 7 word ALL-CAPS banner on the first frame with a low pass rate, e.g. 'ONLY 2% CAN SOLVE THIS PARADOX'. No emoji."))


PARADOX_PROMPT = """Write a PARADOX TRAP Short (at most 20 seconds, about 45 spoken words): a tiny story that
traps the viewer between two answers that both seem right, so they can't scroll away until they pick a side,
then argue about it in the comments. Shape: the story in ONE short sentence, spoken first (the dare hook is
only shown on the banner), the twist that springs the trap, "Think fast!" plus the
dilemma, both branches failing ("If ... / But if ..."), then an either/or question that splits viewers into
two camps, and they comment one word for their side.
Build it on this paradox or dilemma: {idea}.
Retell it as a fresh, vivid situation with concrete characters (it may happen in the game's world when that
fits naturally), not the textbook version. If the creator's notes ask for a different paradox, use theirs.
Already used on the channel, don't retell these: {used}.
Both sides must sound convincing; keep every sentence short and easy to say aloud; no jargon, no names of
philosophers in the spoken lines. Background gameplay: {game}.{notes}"""

# One is picked per paradox Short, skipping the recently used ones (output/paradox_history.json).
PARADOX_IDEAS = (
    "the liar: someone says 'I am lying right now'",
    "Pinocchio says 'My nose will grow now'",
    "the barber who shaves everyone who doesn't shave himself",
    "the crocodile who returns the child only if the parent guesses what it will do",
    "the lawyer who is paid only after winning his first case, then gets sued by his teacher",
    "the bridge guard who hangs liars and frees truth-tellers; a man says 'I'm here to be hanged'",
    "the genie and the wish 'I wish this wish won't come true'",
    "the card that says 'the other side is true' while the other side says 'the other side is false'",
    "the sign that says 'ignore this sign'",
    "the all-powerful wizard asked to create a rock too heavy for him to lift",
    "the surprise exam (or surprise boss fight) announced for next week that can't be a surprise",
    "the grandfather paradox: going back in time and stopping your grandparents from meeting",
    "the bootstrap paradox: a song (or map, or invention) handed to your past self, so nobody wrote it",
    "the self-fulfilling prophecy: trying to stop a prediction is exactly what makes it happen",
    "the billiard ball that goes back in time and knocks its past self away from the time machine",
    "the Ship of Theseus: replace every plank, is it still the same ship (or car, or game character)?",
    "the teleporter that copies you and destroys the original: did you travel or die?",
    "the teleporter glitch that leaves the original alive next to the copy: which one is you?",
    "the brain swap: two people swap brains, one gets a prize and one gets punished",
    "the heap of sand: remove one grain at a time, when does it stop being a heap?",
    "the same river twice: can you ever step into the same river?",
    "Monty Hall: three doors, the host opens a losing one, switch or stay?",
    "the two envelopes: one holds double the other, should you always switch?",
    "Newcomb's boxes: a perfect predictor already filled the boxes, take one or both?",
    "the birthday paradox: only 23 people and two probably share a birthday",
    "the boy or girl paradox: 'I have two kids, at least one is a boy'",
    "Sleeping Beauty and the coin flip: woken once on heads, twice on tails, what are the odds of heads?",
    "the St. Petersburg bet: a coin game with infinite expected winnings, how much would you pay?",
    "the gambler's coin: ten heads in a row, is tails now 'due'?",
    "Bertrand's box: three boxes of coins, you draw gold, odds the other coin is gold?",
    "the prisoner's dilemma: two friends, stay silent or betray?",
    "the trolley problem: pull the lever to save five and doom one?",
    "the footbridge: push one person to stop the trolley and save five?",
    "the lifeboat with room for one fewer person than are aboard",
    "Braess's paradox: a brand-new road makes everyone's trip slower",
    "the voting circle: the group prefers A to B, B to C and C to A",
    "the bus waiting paradox: buses every 10 minutes but you wait longer on average",
    "the potato paradox: 100 kg of potatoes, 99% water, dry them to 98% water and they weigh 50 kg",
    "the missing hotel dollar: three friends pay $30, get $3 back, where did the last dollar go?",
    "the last plane passenger: the first sits randomly, odds the last one gets their own seat?",
    "Achilles and the tortoise: the fastest runner can never pass a head start",
    "the halfway door: to reach the door you go half the distance first, then half again, forever",
    "the flying arrow that is still at every single instant, so when does it move?",
    "Hilbert's hotel: a full hotel with infinite rooms still fits a new guest",
    "Gabriel's horn: you can fill it with paint but never paint its surface",
    "the lamp switched on and off infinitely fast: is it on or off at the end?",
    "the unstoppable force meets the immovable object",
    "Buridan's donkey: perfectly between two equal haystacks, it starves",
    "the coastline paradox: the more carefully you measure a coast, the longer it gets",
    "the twin paradox: one twin flies near light speed and comes back younger",
    "Schrödinger's cat: alive and dead until someone looks",
    "the tree falling in a forest with nobody to hear it",
    "the Fermi paradox: billions of planets, so where is everybody?",
    "the dark night sky: an endless universe full of stars should be bright at night",
    "the experience machine: a perfect simulated happy life, plug in forever or stay real?",
    "the infinite monkeys: typing forever, they eventually write any book",
    "the raven paradox: a green apple is evidence that all ravens are black",
    "the lottery paradox: you're sure each ticket loses, yet one ticket wins",
    "the paradox of choice: more options make people less happy with what they pick",
    "the friendship paradox: most people's friends have more friends than they do",
    "the hedonism paradox: chasing happiness directly makes it harder to find",
    "the oracle who says 'you'll do the opposite of what I predict'",
    "the robot ordered 'disobey this order'",
    "the village where the librarian lists every catalog that doesn't list itself",
    "the Mpemba effect: hot water can freeze faster than cold water",
    "the Simpson's paradox: one team wins every month but loses the whole year",
)


def _history_path(settings: Settings, kind: str) -> Path:
    return settings.output_dir / f"{kind}_history.json"


def history(settings: Settings, kind: str) -> list[dict[str, str]]:
    """What was asked of / written by Gemini for each quiz of this kind, even when the Short was deleted."""
    try:
        data = json.loads(_history_path(settings, kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _remember(settings: Settings, kind: str, **entry: str) -> None:
    items = history(settings, kind) + [{**entry, "at": datetime.now().isoformat()}]
    path = _history_path(settings, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items[-300:], ensure_ascii=False, indent=1), encoding="utf-8")


def _unique(names: list[str]) -> list[str]:
    seen, out = set(), []
    for n in (n.strip() for n in names):
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def paradox_history(settings: Settings) -> list[dict[str, str]]:
    return history(settings, "paradox")


def used_paradoxes(settings: Settings) -> list[str]:
    """Paradox names already made: the history plus the paradox Shorts on this machine, oldest first."""
    from . import shorts

    names = [d.get("name", "") for d in paradox_history(settings)]
    for short in sorted(shorts.list_shorts(settings), key=lambda s: s.created_at):
        m = re.match(r"No single answer: it's (.+?)\.?$", short.answer or "")
        if m:
            names.append(m.group(1))
    return _unique(names)


def pick_paradox(settings: Settings, rng: random.Random) -> str:
    """A random idea not used in the last len(PARADOX_IDEAS) - 15 paradoxes (the oldest come back first)."""
    recent = {d.get("idea") for d in paradox_history(settings)[-(len(PARADOX_IDEAS) - 15):]}
    return rng.choice([i for i in PARADOX_IDEAS if i not in recent] or list(PARADOX_IDEAS))


def used_riddles(settings: Settings) -> list[str]:
    """Riddle answers already made: the history plus the riddle Shorts on this machine, oldest first."""
    from . import shorts

    names = [d.get("answer", "") for d in history(settings, "riddle")]
    for s in sorted(shorts.list_shorts(settings), key=lambda s: s.created_at):
        flashes = [f.get("text", "") for f in (s.render_settings.get("flashes") or []) if isinstance(f, dict)]
        if s.answer and any(t.startswith("CLUE") for t in flashes):
            names.append(s.answer)
    return _unique(names)


def _answer_key(answer: str) -> str:
    return re.sub(r"^(a|an|the|your|my)\s+", "", re.sub(r"[^a-z0-9 ]", "", answer.lower()).strip())

PARADOX_HOOKS = ("Only 2% can escape this paradox.", "This breaks 97% of brains.", "Nobody has solved this one.")


def _vote(word: str, fallback: str) -> str:
    word = re.sub(r"[^A-Za-z0-9' -]", "", word or "").strip().split(" ")[0].upper()
    return word[:12] or fallback


def _write_paradox(settings: Settings, game: str, *, notes: str, format: str, progress: ProgressFn, client,
                   rng: random.Random, movie: dict[str, str] | None = None) -> ShortScript:
    """A paradox trap; with `movie` ({"prompt", "seed"}) a movie-logic debate in the same shape."""
    from . import script_writer as sw
    from .stickers import Reaction

    client, model = sw._gemini(settings, client)
    if movie:
        idea = movie["seed"]
        prompt = movie["prompt"]
    else:
        idea = pick_paradox(settings, rng)
        used = used_paradoxes(settings)[-40:]
        prompt = PARADOX_PROMPT.format(idea=idea, used="; ".join(used) or "none yet", game=game,
                                       notes=_notes(notes) + _variety(settings) + _learned(settings, "paradox"))
    result, model = _ask(client, model, GeminiParadox, prompt, progress)
    setup = [_one_line(s, 22) for s in result.setup if s.strip()][:2]
    twist, side_a, side_b = _one_line(result.twist, 18), _one_line(result.side_a, 18), _one_line(result.side_b, 16)
    twist = re.sub(r"(?i)\s*think fast\W*$", "", twist) or twist
    if not setup or not all((twist, side_a, side_b)):
        raise sw.ScriptError("Gemini wrote a paradox without its story or both sides; try again")
    vote_a, vote_b = _vote(result.vote_a, "A"), _vote(result.vote_b, "B")
    if vote_a == vote_b:
        vote_a, vote_b = "A", "B"
    hook = _one_line(result.hook, 9) or rng.choice(PARADOX_HOOKS)
    dilemma = re.sub(r"(?i)^think fast\W*", "", _one_line(result.dilemma, 13))
    dilemma = (dilemma.rstrip(".!?") or "What should happen") + "?"
    debate = (_one_line(result.debate, 18).rstrip(".!?") or f"So, {vote_a.lower()} or {vote_b.lower()}") + "?"
    closing = f"Comment {vote_a.lower()} or {vote_b.lower()} right now!"
    # The story is the first thing spoken; the dare is the banner. The debate stays in the pinned comment.
    lines = fit_quiz([*setup, twist, f"Think fast! {dilemma}", side_a, side_b, debate, closing], *setup[1:], debate)
    setup = [s for s in setup if s in lines]
    picks = f"{vote_a} OR {vote_b}?"
    flashes = [Flash(word=" ".join(setup[0].split()[:2]), text="MOVIE LOGIC" if movie else "PARADOX"),
               Flash(word="Think fast", text="THINK FAST!"),
               Flash(word=" ".join(side_a.split()[:2]), text=f"A) {vote_a}"),
               Flash(word=" ".join(side_b.split()[:2]), text=f"B) {vote_b}"),
               *([Flash(word=debate.split()[0], text="?")] if debate in lines else []),
               Flash(word="Comment", text=picks if len(picks) <= 16 else "A OR B?")]
    pinned = f"{debate} 👇\nTeam {vote_a} or Team {vote_b}? Defend your side!"
    resolution = " ".join(result.resolution.split())
    if movie:
        title = re.split(r"\s+[-–—:]\s+", result.name.strip())[0][:40] or "a movie"  # "Batman Begins (2005)"
        _remember(settings, "movie", shape="logic", movie=title, seed=idea)
        answer = f"No single answer: it's movie logic in {title}."
        on_screen = f"Movie logic: {result.name.strip()}. {resolution}"
    else:
        _remember(settings, "paradox", idea=idea, name=result.name.strip() or idea)
        answer = f"No single answer: it's {result.name.strip() or 'a paradox'}."
        on_screen = f"Paradox: {result.name.strip()}. {resolution}"
    script = _script(settings, game, model, "movie" if movie else "paradox", format, " ".join(lines), hook,
                     flashes, answer, pinned, result, on_screen=on_screen)
    twist_word = re.sub(r"[^\w']", "", side_b.split()[-1]) or "wrong"
    return script.model_copy(update={"reactions": [Reaction(word=twist_word, mood="shocked")]})


MATH_HOOKS = ("Only 5% finish this without pausing.", "Only 3% get this right the first time.",
              "Most people fail this by step four.", "Only geniuses get this without pausing.")


def _ask(client, model: str, schema: type[BaseModel], prompt: str, progress: ProgressFn):
    from google.genai import errors, types

    from . import script_writer as sw

    config = types.GenerateContentConfig(system_instruction=QUIZ_INSTRUCTION, response_mime_type="application/json",
                                         response_schema=schema, temperature=1.0)
    try:
        response, model = sw._generate_with_fallback(client, model, [prompt], config, progress, errors.APIError)
    except errors.APIError as err:
        raise sw._explain(err, model) from err
    result = response.parsed
    if not isinstance(result, schema):
        if not response.text:
            raise sw.ScriptError("Gemini returned an empty answer (possibly blocked); try again")
        result = schema.model_validate_json(response.text)
    return result, model


# Spoken words in a quiz: about 18 s of voice (quizzes run ~2.6 words/s with the countdown pauses), so the
# Short with its ending stays at about 20 s. Shorts over ~21 s were swiped away.
QUIZ_MAX_WORDS = 46
# Loop quizzes (emoji movie, trivia): 12-15 s in all, the 2 s tail included, and the last line flows back
# into the first one when YouTube replays the Short.
LOOP_MAX_WORDS = 32
LOOP_END = "Did you guess it right? Drop your answer below!"
LOOP_END_SHORT = "Got it? Drop your answer below!"
LOOP_CARD = "DROP YOUR ANSWER BELOW!"

def got_it(end_line: str) -> Flash:
    """The green payoff on the closing line. Never the answer: viewers still comment it (the leaderboard)."""
    return Flash(word=end_line.split()[0], text="GOT IT?", color="green")


def _words(lines: list[str]) -> int:
    return sum(len(line.split()) for line in lines)


def loop_end(lines: list[str]) -> str:
    """The long closing line when it still fits LOOP_MAX_WORDS, else the short one."""
    return LOOP_END if _words([*lines, LOOP_END]) <= LOOP_MAX_WORDS else LOOP_END_SHORT


def fit_quiz(lines: list[str], *optional: str, limit: int = QUIZ_MAX_WORDS) -> list[str]:
    """`lines` without the `optional` ones (dropped in that order) until it fits `limit` words."""
    lines = list(lines)
    for line in optional:
        if _words(lines) <= limit:
            break
        if line in lines:
            lines.remove(line)
    return lines


def _one_line(text: str, max_words: int) -> str:
    text = re.sub(r"[\"“”`*#]|\s+", " ", text or "").strip()
    words = text.split()
    text = " ".join(words[:max_words])
    return text if not text or text.rstrip("'’)")[-1:] in (".", "!", "?") else text + "."


def _notes(notes: str) -> str:
    return f"\nCreator's notes: {notes.strip()}" if notes.strip() else ""


def _learned(settings: Settings, kind: str) -> str:
    """What worked on the channel for this kind of quiz (script_writer.learned)."""
    from . import script_writer as sw

    return sw.learned(settings, kind)


def _quiz_shorts(settings: Settings, limit: int = 15) -> list:
    from . import shorts

    return [s for s in sorted(shorts.list_shorts(settings), key=lambda s: s.created_at) if s.answer][-limit:]


def recent_openings(settings: Settings) -> list[str]:
    return [re.split(r"(?<=[.!?])\s", s.script.strip(), maxsplit=1)[0] for s in _quiz_shorts(settings)]


def _variety(settings: Settings) -> str:
    """The latest quiz titles and opening lines, so Gemini doesn't write the same packaging every time."""
    items = _quiz_shorts(settings)
    if not items:
        return ""
    lines = "; ".join(f'"{s.title}" / "{o}"' for s, o in zip(items, recent_openings(settings)))
    return ("\nThe channel's latest titles / opening lines: " + lines + ". Write a new title, opening line and "
            "title card: don't reuse their wording, and if several already start with 'Only X%', use another "
            "kind of dare.")


def write_quiz(settings: Settings, game: str, kind: QuizKind, *, notes: str = "", format: str = "short",
               progress: ProgressFn = lambda f, m: None, client=None, rng: random.Random | None = None) -> ShortScript:
    from . import script_writer as sw

    rng = rng or random.Random()
    if kind == "math":
        return _write_math(settings, game, notes=notes, format=format, progress=progress, client=client, rng=rng)
    if kind == "trivia":
        return _write_trivia(settings, game, notes=notes, format=format, progress=progress, client=client, rng=rng)
    if kind == "paradox":
        return _write_paradox(settings, game, notes=notes, format=format, progress=progress, client=client, rng=rng)
    if kind == "gamer":
        return _write_gamer(settings, game, notes=notes, format=format, progress=progress, client=client, rng=rng)
    if kind == "movie":
        return _write_movie(settings, game, notes=notes, format=format, progress=progress, client=client, rng=rng)
    client, model = sw._gemini(settings, client)
    used = used_riddles(settings)
    recent = {d.get("topic") for d in history(settings, "riddle")[-(len(RIDDLE_TOPICS) // 2):]}
    topic = rng.choice([t for t in RIDDLE_TOPICS if t not in recent] or list(RIDDLE_TOPICS))
    style = rng.choice(RIDDLE_STYLES)
    for attempt in range(2):
        prompt = RIDDLE_PROMPT.format(topic=topic, style=style, used="; ".join(used[-60:]) or "none yet", game=game,
                                      notes=_notes(notes) + _variety(settings) + _learned(settings, "riddle"))
        result, model = _ask(client, model, GeminiRiddle, prompt, progress)
        if _answer_key(result.answer) not in {_answer_key(u) for u in used} or attempt:
            break
        logger.info("Gemini reused the riddle answer %r; asking again", result.answer)
        used.append(result.answer)
    _remember(settings, "riddle", topic=topic, answer=result.answer.strip(), hook=result.hook.strip())
    clues = [_one_line(c, 18) for c in result.clues if c.strip()][:3]
    if len(clues) < 2:
        raise sw.ScriptError("Gemini wrote a riddle without clues; try again")
    question = (_one_line(result.question, 8).rstrip(".!?") or "What am I") + "?"
    answer = _one_line(result.answer, 6).rstrip(".")
    hook = riddle_hook(result.hook, answer) or clues.pop(0)
    hint = safe_hint(result.hint, answer) or "every clue has to fit one answer"
    hint_line = f"Hint: {hint}."
    # Clues are never dropped (a whodunit needs all of them); the hint is also in the pinned comment.
    lines = fit_quiz([hook, *clues, question, COUNTDOWN, hint_line, CLOSING], hint_line)
    flashes = [Flash(word=" ".join(hook.split()[:2]), text="RIDDLE")]  # moves on the first word
    flashes += [Flash(word=" ".join(c.split()[:2]), text=f"CLUE {i}") for i, c in enumerate(clues, 1)]
    flashes += [Flash(word=question.split()[0], text="?"), *COUNTDOWN_FLASHES]
    if hint_line in lines:
        flashes.append(Flash(word="Hint", text="HINT"))
    pinned = public_pin(question, hint)
    bonus = (_one_line(result.bonus, 14).rstrip(".!?") + "?") if result.bonus.strip() else ""
    if bonus and answer.lower() not in bonus.lower():
        pinned += f"\n🔥 Bonus round: {bonus} Reply with both answers!"
    card = riddle_card(result.title_card, hook)
    return _script(settings, game, model, "riddle", format, " ".join(lines), hook, flashes, answer, pinned, result,
                   on_screen=f"Riddle answer: {answer}. {result.explanation.strip()}", card=card)


COUNTDOWN = "Three. Two. One."
COUNTDOWN_FLASHES = [Flash(word="Three", text="3"), Flash(word="Two", text="2"), Flash(word="One", text="1")]


def riddle_hook(hook: str, answer: str) -> str:
    """The opening question, or "" when Gemini still wrote a stat dare or gave the answer away."""
    hook = _one_line(hook, 13)
    words = {w for w in re.findall(r"[a-z']+", answer.lower()) if len(w) > 2 and w not in ("the", "and")}
    if not hook or _DARE.search(hook) or words & set(re.findall(r"[a-z']+", hook.lower())):
        return ""
    return hook


def riddle_card(card: str, hook: str) -> str:
    """The frame-0 banner: the paradox itself (up to 3 lines of 16 characters), not a rarity stat."""
    card = " ".join((card or "").split())
    if card and not _DARE.search(card) and len(card) <= 40:
        return card if card.endswith("?") else card.rstrip(".!") + "?"
    words, out = hook.rstrip(".!?").split(), ""
    for word in words:
        if len(f"{out} {word}".strip()) > 36:
            break
        out = f"{out} {word}".strip()
    return out + "?"


def _option(text: str) -> str:
    return " ".join(re.sub(r"[\"“”`*#?!.,;:]", " ", text or "").split()[:4])


TRIVIA_CANDIDATES = 4
TRIVIA_ROUNDS = 3


def _trivia_options(trivia: GeminiTrivia | GeminiGamer) -> tuple[str, str, str] | None:
    """(trap, distractor, correct), or None unless all three are there and different."""
    options = tuple(_option(t) for t in (trivia.trap, trivia.distractor, trivia.correct))
    return options if all(options) and len({o.lower() for o in options}) == 3 else None


def used_trivia(settings: Settings) -> list[str]:
    """Correct answers of the trivia Shorts already made (the history, then the A/B/C Shorts here), oldest first."""
    from . import shorts

    out = [d.get("answer", "") for d in history(settings, "trivia")]
    for short in sorted(shorts.list_shorts(settings), key=lambda s: s.created_at):
        flashes = [f.get("text", "") for f in (short.render_settings.get("flashes") or []) if isinstance(f, dict)]
        if (short.answer and not short.answer.startswith("No single answer")
                and any(t.startswith(("A)", "B)")) or t in ("A", "B") for t in flashes)):
            out.append(short.answer)
    return _unique(out)


def used_trivia_questions(settings: Settings) -> list[str]:
    return _unique([f"{d.get('question', '')} ({d.get('answer', '')})" for d in history(settings, "trivia")
                    if d.get("question")])


_VERDICT = re.compile(r"^\W*(\d+)\W*(PASS|FAIL)\b\W*(.*)$", re.I)


def fact_check(client, model: str, candidates: list[GeminiTrivia] | list[GeminiGamer],
               progress: ProgressFn, check: str = FACT_CHECK) -> list[tuple[bool, str]]:
    """(passed, reason) per candidate, checked by Gemini with Google Search. Unanswered ones fail."""
    from google.genai import errors, types

    from . import script_writer as sw

    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0)
    try:
        response, _ = sw._generate_with_fallback(client, model, [check.format(candidates=check_listing(candidates))],
                                                 config, progress, errors.APIError)
    except errors.APIError as err:
        raise sw._explain(err, model) from err
    return read_verdicts(response.text or "", len(candidates))


def check_listing(candidates: list[GeminiTrivia] | list[GeminiGamer]) -> str:
    """The candidates as the fact-check prompt lists them, numbered from 1."""
    return "\n\n".join(
        f"{i}. Q: {c.question.strip().rstrip('?')}?\n   Claimed correct: {c.correct}\n   Wrong options: {c.trap}; "
        f"{c.distractor}\n   Claimed proof: {c.fact}" for i, c in enumerate(candidates, 1))


def read_verdicts(text: str, count: int) -> list[tuple[bool, str]]:
    """(passed, reason) per candidate from "<n>: PASS" / "<n>: FAIL: why" lines; unanswered ones fail."""
    found: dict[int, tuple[bool, str]] = {}
    for line in text.splitlines():
        if (m := _VERDICT.match(line.strip())) and int(m.group(1)) not in found:
            found[int(m.group(1))] = (m.group(2).upper() == "PASS", m.group(3).strip(" :-"))
    return [found.get(i, (False, "not checked")) for i in range(1, count + 1)]


def _write_trivia(settings: Settings, game: str, *, notes: str, format: str, progress: ProgressFn, client,
                  rng: random.Random) -> ShortScript:
    from . import script_writer as sw
    from .stickers import Reaction

    client, model = sw._gemini(settings, client)
    used = used_trivia(settings)
    result = None
    for _ in range(TRIVIA_ROUNDS):
        categories = ["video games", *rng.sample(TRIVIA_CATEGORIES, TRIVIA_CANDIDATES - 1)]
        prompt = TRIVIA_PROMPT.format(count=len(categories), categories=", ".join(categories),
                                      used=", ".join(used[-40:]) or "none yet",
                                      questions="; ".join(used_trivia_questions(settings)[-30:]) or "none yet",
                                      game=game, notes=_notes(notes) + _variety(settings) + _learned(settings, "trivia"))
        batch, model = _ask(client, model, GeminiTriviaSet, prompt, progress)
        usable = [c for c in batch.candidates if _trivia_options(c) and _option(c.correct).lower() not in
                  {u.lower() for u in used}]
        if not usable:
            continue
        progress(None, "Fact-checking the trivia on Google")
        verdicts = fact_check(client, model, usable, progress)
        passed = [c for c, (ok, _) in zip(usable, verdicts) if ok]
        for c, (ok, why) in zip(usable, verdicts):
            if not ok:
                logger.info("Trivia rejected: %s -> %s (%s)", c.question, c.correct, why)
        if passed:
            result = rng.choice(passed)
            break
        used += [_option(c.correct) for c in usable]
    if result is None:
        raise sw.ScriptError("No trivia question passed the Google fact-check; try again")
    trap, distractor, correct = _trivia_options(result)
    question = (_one_line(result.question, 12).rstrip(".!?") or "Which one is it") + "?"
    options = [trap, distractor, correct]
    rng.shuffle(options)
    hooks = [h for h in TRIVIA_HOOKS if "game" in result.category.lower() or "Gamers" not in h] or TRIVIA_HOOKS
    opened = {o.lower() for o in recent_openings(settings)}
    hooks = [h for h in hooks if h.lower() not in opened] or hooks
    hook = _one_line(result.hook, 8) if result.hook.strip() and rng.random() < 0.4 else rng.choice(hooks)
    _remember(settings, "trivia", answer=correct, question=question)
    hint = safe_hint(result.hint, correct, trap, distractor) or hint_from_fact(result.fact, correct, trap, distractor)
    asks = [f"Is it {options[0]}?", f"{options[1][:1].upper()}{options[1][1:]}?", f"Or {options[2]}?"]
    # The question is the first word spoken (the dare is only on the banner); the hint is in the pinned
    # comment, so the Short stays at 12-15 s and ends on a line that loops into the question.
    lines = [question, *asks, COUNTDOWN]
    end = loop_end(lines)
    lines.append(end)
    # The question first, so an option named in the question can't trigger its flash early.
    flashes = [Flash(word=question.split()[0], text="?")]
    for letter, option, said in zip("ABC", options, ("Is it ", "", "Or ")):
        name = re.sub(r"^(the|an?) ", "", option, flags=re.I)
        label = f"{letter}) {name[:1].upper()}{name[1:]}"
        word = (said + " ".join(option.split()[:3])).strip()[:40]
        flashes.append(Flash(word=word, text=label if len(label) <= 16 else letter))
    flashes += [*COUNTDOWN_FLASHES, got_it(end)]
    fact = result.fact.strip()
    pinned = public_pin(question, hint)
    script = _script(settings, game, model, "trivia", format, " ".join(lines), hook, flashes, correct, pinned,
                     result, on_screen=f"Answer: {correct} (trap: {trap}). {fact}", end_card=LOOP_CARD)
    return script.model_copy(update={"reactions": [Reaction(word="Three", mood="suspicious")]})


GAMER_CANDIDATES = 4
GAMER_HOOK = "{game} fans only."


def gamer_level(settings: Settings) -> int:
    """The next Gamer IQ level: one more than the questions written so far (the series' episode number)."""
    return len(history(settings, "gamer")) + 1


def pick_gamer_shape(settings: Settings, rng: random.Random) -> str:
    """A shape the last two Gamer IQ questions didn't use."""
    recent = {d.get("shape") for d in history(settings, "gamer")[-2:]}
    return rng.choice([s for s in GAMER_SHAPES if s not in recent] or list(GAMER_SHAPES))


GAMER_MAX_WORDS = 16
_OPTION_ASK = re.compile(r"^(is it|or|was it|are they)\b", re.I)
_PURE_DARE = re.compile(r"^(stop|don'?t pause|do not pause|no pausing|quick|think fast|be honest)\b", re.I)
_GAMER_DARE = re.compile(r"\d+\s*%|\b(stop|don'?t pause|do not pause|five seconds|bet you|nobody|instincts?)\b", re.I)


def game_name(game: str) -> str:
    """The game as fans say it: "GTA V / Online" -> "GTA V"."""
    return re.split(r"[/:(]", game)[0].strip() or game.strip()


def _game_words(game: str) -> set[str]:
    words = [w for w in re.split(r"[\s/:()-]+", game.lower()) if w]
    initials = "".join(w[0] for w in words if w[0].isalnum())
    return {w for w in words if len(w) > 1 or w.isdigit()} | {initials}


def gamer_question(text: str, options: tuple[str, str, str]) -> str:
    """The question as spoken before the options: without the options an LLM wrote into it ("Is it A, B or C?")
    or a bare dare ("Don't pause."); "" when what is left is no usable question or too long to speak."""
    sentences = re.findall(r"[^.!?]+[.!?]*", " ".join((text or "").split()))
    keep = []
    for sentence in (x.strip() for x in sentences):
        named = sum(bool(re.search(rf"\b{re.escape(o)}\b", sentence, re.I)) for o in options)
        if not sentence or _OPTION_ASK.search(sentence) or named >= 2 or (
                _PURE_DARE.search(sentence) and len(sentence.split()) <= 4):
            continue
        keep.append(sentence)
    question = " ".join(keep).strip().rstrip(",;:")
    if not question or len(question.split()) > GAMER_MAX_WORDS:
        return ""
    return question if question[-1] in ".!?" else question + "?"


def gamer_title(title: str, game: str) -> str:
    """The LLM's title when it names the game without a dare (not shouted in capitals), else a fans' line."""
    title = re.sub(r"\b(level|lvl|lv)\.?\s*\d+\b", "", title or "", flags=re.I)  # the video adds its own level
    title = " ".join(re.sub(r"[^\w\s'’!?,.&%:-]", "", title).split()).strip(" -:,|")
    if title.isupper():
        title = title.title()
    words = set(re.findall(r"[a-z0-9]+", title.lower()))
    if not title or len(title) > 50 or _GAMER_DARE.search(title) or not words & _game_words(game):
        return f"Real {game_name(game)} Fans Know This"
    return title


def gamer_card(level: int, short_name: str, game: str) -> str:
    """The frame-0 banner: the level (a series to come back to) and who it is for (the game, never the topic)."""
    name = " ".join(re.sub(r"[^\w\s&-]", " ", short_name or "").split()).upper()
    if not name or len(name) > 12 or not set(name.lower().split()) & _game_words(game):
        name = game_name(game).upper()[:12] or "GAMERS"
    return f"LEVEL {level} - {name} FANS ONLY"


def gamer_asked(settings: Settings) -> list[str]:
    """The Gamer IQ questions already written, "question (answer)", oldest first."""
    return _unique([f"{d.get('question', '')} ({d.get('answer', '')})" for d in history(settings, "gamer")
                    if d.get("question")])


def gamer_prompt(settings: Settings, game: str, shape: str, asked: list[str], notes: str = "") -> str:
    """The writing prompt for GAMER_CANDIDATES questions of `shape` about `game` (Gemini's, or any LLM's by hand)."""
    return GAMER_PROMPT.format(count=GAMER_CANDIDATES, game=game, shape=GAMER_SHAPES[shape].format(game=game),
                               used="; ".join(asked[-40:]) or "none yet",
                               notes=_notes(notes) + _variety(settings) + _learned(settings, "gamer"))


def _write_gamer(settings: Settings, game: str, *, notes: str, format: str, progress: ProgressFn, client,
                 rng: random.Random) -> ShortScript:
    """Gamer IQ: a 3-option question about the game in the background, fact-checked on Google, numbered as a
    level of an ongoing series. The game is the puzzle, not wallpaper: its fans recognise it and argue."""
    from . import script_writer as sw

    client, model = sw._gemini(settings, client)
    shape = pick_gamer_shape(settings, rng)
    asked = gamer_asked(settings)
    result = None
    for _ in range(TRIVIA_ROUNDS):
        batch, model = _ask(client, model, GeminiGamerSet, gamer_prompt(settings, game, shape, asked, notes), progress)
        usable = [c for c in batch.candidates if _trivia_options(c) and gamer_question(c.question, _trivia_options(c))]
        if not usable:
            continue
        progress(None, f"Fact-checking the {game} questions on Google")
        verdicts = fact_check(client, model, usable, progress, GAMER_CHECK)
        passed = [c for c, (ok, _) in zip(usable, verdicts) if ok]
        for c, (ok, why) in zip(usable, verdicts):
            if not ok:
                logger.info("Gamer question rejected: %s -> %s (%s)", c.question, c.correct, why)
        if passed:
            result = rng.choice(passed)
            break
        asked += [f"{c.question} ({c.correct})" for c in usable]
    if result is None:
        raise sw.ScriptError(f"No {game} question passed the Google fact-check; try again")
    return gamer_script(settings, game, result, shape=shape, model=model, format=format, rng=rng)


def gamer_script(settings: Settings, game: str, result: GeminiGamer, *, shape: str, model: str,
                 format: str = "short", rng: random.Random | None = None, level: int | None = None,
                 record: bool = True) -> ShortScript:
    """The Short of one checked Gamer IQ question, as the next level (recorded in the history), or as `level`
    without recording it (a level made again)."""
    from . import script_writer as sw
    from .stickers import Reaction

    rng = rng or random.Random()
    level = level or gamer_level(settings)
    trap, distractor, correct = _trivia_options(result)
    question = gamer_question(result.question, (trap, distractor, correct))
    if not question:
        raise sw.ScriptError(f"The question is too long to speak or only lists the options: {result.question!r}")
    options = [trap, distractor, correct]
    rng.shuffle(options)
    if record:
        _remember(settings, "gamer", answer=correct, question=question, game=game, shape=shape, level=str(level))
    hint = safe_hint(result.hint, correct, trap, distractor) or hint_from_fact(result.fact, correct, trap, distractor)
    asks = [f"Is it {options[0]}?", f"{options[1][:1].upper()}{options[1][1:]}?", f"Or {options[2]}?"]
    # Like the trivia trap: the question is the first word spoken and the Short loops back into it.
    lines = [question, *asks, COUNTDOWN]
    end = loop_end(lines)
    lines.append(end)
    flashes = [Flash(word=question.split()[0], text="?")]
    for letter, option, said in zip("ABC", options, ("Is it ", "", "Or ")):
        name = re.sub(r"^(the|an?) ", "", option, flags=re.I)
        label = f"{letter}) {name[:1].upper()}{name[1:]}"
        word = (said + " ".join(option.split()[:3])).strip()[:40]
        flashes.append(Flash(word=word, text=label if len(label) <= 16 else letter))
    flashes += [*COUNTDOWN_FLASHES, got_it(end)]
    pinned = (public_pin(question, hint) + f"\n🏆 First correct answer gets 10 leaderboard points. "
              f"Level {level + 1} drops next!")
    pack = result.model_copy(update={"title": f"{gamer_title(result.title, game)} | Level {level} 🎮"})
    script = _script(settings, game, model, "gamer", format, " ".join(lines), GAMER_HOOK.format(game=game), flashes,
                     correct, pinned, pack, on_screen=f"Answer: {correct} (trap: {trap}). {result.fact.strip()}",
                     card=gamer_card(level, result.short_name, game), end_card=LOOP_CARD)
    return script.model_copy(update={"reactions": [Reaction(word="Three", mood="suspicious")]})


def _write_math(settings: Settings, game: str, *, notes: str, format: str, progress: ProgressFn, client,
                rng: random.Random) -> ShortScript:
    from . import script_writer as sw

    start, chain = make_chain(rng)
    answer = solve(start, chain)[-1]
    trap = trap_answer(start, chain)
    steps_text = working(start, chain)
    try:
        client, model = sw._gemini(settings, client)
        pack, model = _ask(client, model, GeminiMathPack,
                           MATH_PROMPT.format(chain=steps_text, answer=answer, game=game,
                                              notes=_notes(notes) + _variety(settings) + _learned(settings, "math")), progress)
    except sw.ScriptError as err:
        if "GEMINI_API_KEY" in str(err):
            raise
        logger.warning("Gemini unavailable for the math quiz, using the built-in texts: %s", err)
        model = "built-in"
        pack = GeminiMathPack(hook=rng.choice(MATH_HOOKS), title="Only 5% Get This Right 🧠",
                              description="Do it in your head, no pausing. Comment your final number!",
                              hashtags=["#shorts", "#math", "#brainteaser", "#mathchallenge", "#quiz"],
                              title_card="ONLY 3% CAN SOLVE THIS")
    hook = _one_line(pack.hook, 10)
    if str(answer) in {t.strip(".,!?") for t in hook.split()}:
        hook = rng.choice(MATH_HOOKS)
    lines, flashes = math_lines(start, chain, rng)
    hint = digit_hint(answer)
    # "Start with 24." is the first thing spoken (its number slams in at once); the dare is the banner. No
    # spoken hint: it made the chain too easy (the digit count is in the pinned comment).
    spoken = [*lines, "Got your number?", COUNTDOWN, MATH_CLOSING]
    flashes += [Flash(word="number", text="?"), *COUNTDOWN_FLASHES]
    pinned = public_pin("What number did you get?", hint)
    return _script(settings, game, model, "math", format, " ".join(spoken), hook, flashes, str(answer), pinned,
                   pack, on_screen=f"Math chain: {steps_text} (answer {answer}; most people get {trap})")


# --------------------------------------------------------------------------- your own quiz text


CLOSING = "Comment your answer right now!"
MATH_CLOSING = "Most people get this wrong. Comment yours!"
FOLLOW_LINE = re.compile(r"\b(subscribe|sub to|follow|stories|part (2|two))\b", re.I)
_NUM = r"(\d+)\s*(%|percent)?"
STEP_PATTERNS = [  # (op, regex); the first match in each sentence wins
    ("start", re.compile(rf"\b(?:start|begin) with\s+{_NUM}|\bthink of\s+{_NUM}", re.I)),
    ("+", re.compile(rf"\b(?:add|plus)\s+{_NUM}", re.I)),
    ("-", re.compile(rf"\b(?:subtract|minus|take away|remove)\s+{_NUM}", re.I)),
    ("×", re.compile(rf"\b(?:multiply(?: it| that)? by|times)\s+{_NUM}", re.I)),
    ("÷", re.compile(rf"\b(?:divide(?: it| that)? by)\s+{_NUM}", re.I)),
    ("×2", re.compile(r"\b(double)\b", re.I)),
    ("÷2", re.compile(r"\b(halve|half)\b", re.I)),
]


def read_steps(script: str) -> list[tuple[str, int | None, bool, str]]:
    """(op, number, is_percent, spoken trigger word) for each math step in a written script, in order."""
    from .script_writer import _sentences

    steps = []
    for sentence in _sentences(script):
        hits = [(m.start(), op, m) for op, rx in STEP_PATTERNS if (m := rx.search(sentence))]
        if not hits:
            continue
        _, op, m = min(hits, key=lambda h: h[0])
        if op in ("×2", "÷2"):
            steps.append((op[0], 2, False, m.group(1)))
            continue
        groups = [g for g in m.groups()]
        n = next(g for g in groups if g and g.isdigit())
        pct = any(g and g.lower() in ("%", "percent") for g in groups)
        steps.append((op, int(n), pct, n))
    return steps


def solve_steps(steps: list[tuple[str, int | None, bool, str]]) -> tuple[int, str] | None:
    """(answer, working) when the steps start with a number and stay whole; else None."""
    if not steps or steps[0][0] != "start":
        return None
    value = Fraction(steps[0][1])
    parts = [str(steps[0][1])]
    for op, n, pct, _ in steps[1:]:
        if op == "start":
            return None
        amount = value * n / 100 if pct else Fraction(n)
        if op == "+":
            value += amount
        elif op == "-":
            value -= amount
        elif op == "×":
            value *= amount
        elif n == 0:
            return None
        else:
            value /= amount
        if value.denominator != 1:
            return None
        sign = {"-": "−"}.get(op, op)
        parts.append(f"{sign} {n}{'%' if pct else ''} = {value}")
    if len(parts) < 3:
        return None
    return int(value), " → ".join(parts)


def own_quiz(settings: Settings, game: str, script: str, kind: QuizKind, *, notes: str = "", format: str = "short",
             progress: ProgressFn = lambda f, m: None, client=None) -> ShortScript:
    """Your own math / riddle text: kept word for word (no subscribe line), numbers flashed on screen,
    the answer worked out when the steps can be followed; Gemini writes the title, hashtags and banner."""
    from . import script_writer as sw

    sentences = sw._sentences(sw.clean_own_script(script))
    while len(sentences) > 1 and FOLLOW_LINE.search(sentences[-1]):
        sentences.pop()
    text = " ".join(sentences)
    if len(text.split()) < 3:
        raise sw.ScriptError("Write or paste your quiz in the voiceover box first")
    if not re.search(r"\bcomment", " ".join(sw._sentences(text)[-2:]), re.I):
        text = f"{text} {CLOSING}"

    flashes: list[Flash] = []
    solved = None
    if kind == "math":
        steps = read_steps(text)
        flashes = [Flash(word=word, text=(f"{n}" if op == "start" else f"{op}{n}") + ("%" if pct else ""))
                   for op, n, pct, word in steps]
        solved = solve_steps(steps)
    question = next((s for s in sw._sentences(text)[::-1] if s.rstrip().endswith("?")), "")
    if question:
        flashes.append(Flash(word=question.split()[0], text="?"))

    client, model = sw._gemini(settings, client)
    label = {"math": "RAPID MATH", "riddle": "RIDDLE", "trivia": "TRIVIA TRAP", "paradox": "PARADOX TRAP",
             "movie": "MOVIE QUIZ", "gamer": "GAMER IQ"}[kind]
    prompt = "\n".join([f"Package this {label} quiz Short the creator wrote.",
                        f"Background gameplay: {game}.{_notes(notes)}",
                        *([f"The correct answer is {solved[0]} ({solved[1]})."] if solved else []),
                        "VOICEOVER (do not change it):", text])
    pack, model = _ask(client, model, GeminiOwnQuiz, prompt, progress)
    answer = str(solved[0]) if solved else _one_line(pack.answer, 8).rstrip(".")
    working = solved[1] if solved else pack.working.strip()
    if kind == "math" and answer.isdigit():
        hint, ask = digit_hint(int(answer)), "What number did you get?"
    else:
        hint, ask = (safe_hint(working, answer) or "the obvious guess is wrong"), "What do you think?"
    pinned = public_pin(ask, hint)
    hook = (sw._sentences(text) or [text])[0]
    return _script(settings, game, model, kind, format, text, hook, flashes, answer, pinned, pack,
                   on_screen=f"Answer: {answer}" + (f" ({working})" if working else ""))


class GeminiOwnQuiz(BaseModel):
    answer: str = Field(description="The correct final answer to the quiz, 1 to 5 words (work it out carefully).")
    working: str = Field(description="One short line showing how to get the answer, e.g. '100 − 50% = 50 → + 50% = 75'.")
    title: str = GeminiMathPack.model_fields["title"]
    description: str = GeminiMathPack.model_fields["description"]
    hashtags: list[str] = GeminiMathPack.model_fields["hashtags"]
    title_card: str = GeminiMathPack.model_fields["title_card"]


def _script(settings: Settings, game: str, model: str, mode: str, format: str, script: str, hook: str,
            flashes: list[Flash], answer: str, pinned: str, pack, *, on_screen: str, card: str = "",
            choices: list[str] | None = None, popups: list | None = None, end_card: str = END_CARD) -> ShortScript:
    from . import script_writer as sw

    card = card or frame_zero(pack.title_card, hook)
    return sw.ShortScript(
        on_screen=on_screen,
        hook=hook,
        script=script,
        question="",
        pinned_comment=pinned,
        title=with_emoji(pack.title.strip().strip('"')[:100]),
        description=pack.description.strip(),
        hashtags=sw._clean_hashtags(pack.hashtags),
        title_card=" ".join(re.sub(r"[^\w\s%!?.,'&-]", "", card).split()).upper()[:48],
        music_mood=MUSIC_MOOD,
        ending="question",
        end_card=end_card,
        flashes=flashes,
        answer=answer[:80],
        choices=[c[:100] for c in choices or []],
        popups=popups or [],
        game=game,
        model=model,
        mode=mode,
        format=format,
        watched_clip=False,
        word_count=len(script.split()),
        estimated_seconds=sw.estimate_seconds(script),
    )


# --------------------------------------------------------------------------- background that fits


THEME_PROMPT = """Pick the background gameplay for a quiz Short. The quiz: {topic}
Gameplay available: {games}.
Pick the one whose world or mood matches the quiz (a war movie over a war or shooter game, space trivia
over a space game, a jungle movie over a jungle level). If none clearly matches, pick the most neutral,
high-contrast, fast-moving one (racing, parkour, an endless runner). Answer with one name from the list,
exactly as written, and nothing else."""


def theme_game(settings: Settings, script: ShortScript, games: list[str], *, progress: ProgressFn = lambda f, m: None,
               client=None) -> str | None:
    """The available game whose look fits the quiz (Gemini picks), or None when it can't tell. A quiz
    about the gameplay's own game keeps it."""
    from . import script_writer as sw

    if not games:
        return None
    if script.game and script.game in games and script.game.lower() in script.script.lower():
        return script.game
    try:
        from google.genai import errors, types

        client, model = sw._gemini(settings, client)
        topic = f"{script.title}. {script.on_screen}"[:400]
        response, _ = sw._generate_with_fallback(
            client, model, [THEME_PROMPT.format(topic=topic, games="; ".join(games))],
            types.GenerateContentConfig(temperature=0.2), progress, errors.APIError)
    except Exception as err:  # noqa: BLE001 - a background that fits is a bonus, never a failure
        logger.warning("Could not pick a background that fits the quiz: %s", err)
        return None
    answer = (response.text or "").strip().strip("\"'.*").lower()
    return next((g for g in games if g.lower() == answer), None) or next(
        (g for g in sorted(games, key=len, reverse=True) if g.lower() in answer), None)


# --------------------------------------------------------------------------- movie quizzes


MOVIE_SHAPES = ("quote", "emoji", "logic")
MOVIE_GENRES = ("animated", "sci-fi", "action", "comedy", "superhero", "fantasy", "horror", "family adventure",
                "thriller", "romance", "crime", "space", "video game adaptation", "musical", "war", "sports")
MOVIE_ERAS = ("the 1970s or 1980s", "the 1990s", "the 1990s", "the 2000s", "the 2000s", "the 2010s", "the 2010s",
              "the 2020s")
MOVIE_CANDIDATES = 3
MOVIE_HOOKS = ("Bet you get this one wrong.", "Movie fans, prove it.", "Think you know movies? Prove it.",
               "Your memory is lying to you.", "Almost everyone misquotes this.")
EMOJI_HOOKS = ("Guess the movie in five seconds!", "Name this movie in five seconds!", "Five seconds. One movie. Go!")


def movie_history(settings: Settings) -> list[dict[str, str]]:
    return history(settings, "movie")


def used_movies(settings: Settings) -> list[str]:
    return _unique([d.get("movie", "") for d in movie_history(settings)])


def pick_movie_shape(settings: Settings, rng: random.Random, notes: str = "") -> str:
    """The shape the notes ask for, else one not used in the last two movie quizzes."""
    low = notes.lower()
    for shape, words in (("emoji", ("emoji",)), ("quote", ("quote", "line", "said", "say")),
                         ("logic", ("logic", "debate", "paradox", "what if"))):
        if any(w in low for w in words):
            return shape
    recent = [d.get("shape") for d in movie_history(settings)[-2:]]
    return rng.choice([s for s in MOVIE_SHAPES if s not in recent] or list(MOVIE_SHAPES))


def _movie_seed(rng: random.Random) -> str:
    return f"a famous {rng.choice(MOVIE_GENRES)} movie from {rng.choice(MOVIE_ERAS)}"


class GeminiMovieQuote(BaseModel):
    movie: str = Field(description="The movie and year, e.g. 'The Empire Strikes Back (1980)'.")
    hook: str = Field("", description="A spoken dare, at most 7 words, e.g. 'Almost everyone misquotes this.'")
    question: str = Field(description=(
        "At most 14 words: 'What did [character] ACTUALLY say ...?' or 'What is the REAL line ...?', e.g. 'What "
        "did Darth Vader actually say to Luke in Star Wars?'"))
    trap: str = Field(description=(
        "The famous MISQUOTE most people remember, WRONG, at most 7 words, e.g. 'Luke, I am your father.'"))
    distractor: str = Field(description="A believable wrong version, at most 7 words, e.g. 'I am your father, Luke.'")
    correct: str = Field(description=(
        "The line as actually said in the movie, word for word, at most 7 words, e.g. 'No, I am your father.' "
        "Must be easy to verify on Google."))
    fact: str = Field(description="One short sentence proving it, e.g. 'In the film Vader says \"No, I am your father\"; he never says \"Luke\".'")
    title: str = Field(description="YouTube title, at most 60 characters, no hashtags, never the answer, e.g. '90% Misquote This Movie Line 🎬'.")
    description: str = Field(description="One or two short sentences daring viewers to comment A, B or C, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 hashtags, e.g. #shorts #movies #moviequiz #starwars #quiz.")
    title_card: str = Field(description="3 to 7 word ALL-CAPS banner, e.g. 'YOU'VE BEEN SAYING IT WRONG'. No emoji.")


class GeminiMovieQuoteSet(BaseModel):
    candidates: list[GeminiMovieQuote] = Field(description="The misquote traps, each from a different movie.")


class GeminiMovieEmoji(BaseModel):
    class Pic(BaseModel):
        emoji: str = Field(description="One emoji, e.g. 🎈.")
        line: str = Field(description=(
            "What the emoji shows, said in 1 to 3 words, e.g. 'Balloons.', 'A flying house.' or 'A grumpy old "
            "man.' Never the movie's title or a character's name."))
        word: str = Field(description="The thing's word as written in the line, e.g. 'Balloons'.")

    movie: str = Field(description="The movie's title as people would type it, e.g. 'Up'. No year.")
    year: str = Field(description="Its release year, e.g. '2009'.")
    emojis: list[Pic] = Field(description="Exactly 3 emojis that together point to this movie and no other.")
    hint: str = Field(description=(
        "One spoken hint, at most 7 words, never a word of the title, e.g. 'It won two Oscars!'"))
    title: str = Field(description="YouTube title, at most 60 characters, no hashtags, never the movie, e.g. 'Guess The Movie From 3 Emojis 🎬'.")
    description: str = Field(description="One or two short sentences daring viewers to comment the movie, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 hashtags, e.g. #shorts #guessthemovie #emojichallenge #movies #quiz.")
    title_card: str = Field(description="3 to 7 word ALL-CAPS banner, e.g. 'GUESS THE MOVIE IN 5 SECONDS'. No emoji.")


MOVIE_QUOTE_PROMPT = """Write {count} different MOVIE MISQUOTE TRAP candidates for a Short (15-20 seconds), each
from a different famous movie, one per idea in this order: {seeds}. Each is a line almost everyone quotes
wrong (the "Mandela effect"), with three options: the famous misquote (the trap), a believable wrong
version, and the line as actually said in the movie, word for word.
What makes a candidate good: the movie is famous worldwide, the misquote is how most people really say it,
and the real line is documented (the film's script, reputable sources). No lines that differ only by "Oh"
or a comma: the difference must be clear when read aloud. Each option at most 7 words, easy to say.
Movies already used on the channel, pick others: {used}.
Background gameplay: {game}.{notes}"""

MOVIE_EMOJI_PROMPT = """Write a GUESS THE MOVIE FROM 3 EMOJIS Short (12-15 seconds; the 3 emojis pop up back to
back in the first 4 seconds). The movie: {seed}, famous enough that most people have seen it or know it.
Pick 3 emojis that together point to this movie and no other (its most iconic image, character or
moment), in the order that makes it click at the last one, each said in 1 to 3 words, plus one short
spoken hint that helps without giving the title away. Never say or write the title, a word of it, or a
character's name anywhere except "movie".
Movies already used on the channel, pick another: {used}.
Background gameplay: {game}.{notes}"""

MOVIE_LOGIC_PROMPT = """Write a MOVIE LOGIC DEBATE Short (at most 20 seconds, about 45 spoken words) in the shape of a paradox trap: a famous
movie's plot or rules, pushed to a question with two answers that both seem right, so viewers pick a side
and argue in the comments, e.g. "If Marty McFly stops his parents from meeting, does he vanish at once or
does he have time to fix it?" The movie: {seed}. Say the movie or character in the story so viewers know it.
Shape: the situation in ONE short sentence, spoken first (the dare hook is only shown on the banner), the twist that springs the trap, "Think fast!" plus the
dilemma, both branches ("If ... / But if ..."), then an either/or question that splits viewers into two
camps, and they comment one word for their side. Put the movie's title and year in `name`.
Movies already used on the channel, pick another: {used}.
Background gameplay: {game}.{notes}"""


def _write_movie(settings: Settings, game: str, *, notes: str, format: str, progress: ProgressFn, client,
                 rng: random.Random) -> ShortScript:
    shape = pick_movie_shape(settings, rng, notes)
    used = "; ".join(used_movies(settings)[-50:]) or "none yet"
    extra = _notes(notes) + _variety(settings) + _learned(settings, "movie")

    if shape == "logic":
        seed = _movie_seed(rng)
        prompt = MOVIE_LOGIC_PROMPT.format(seed=seed, used=used, game=game, notes=extra)
        return _write_paradox(settings, game, notes=notes, format=format, progress=progress, client=client, rng=rng,
                              movie={"prompt": prompt, "seed": seed})
    if shape == "emoji":
        return _write_movie_emoji(settings, game, used=used, extra=extra, format=format, progress=progress,
                                  client=client, rng=rng)
    return _write_movie_quote(settings, game, used=used, extra=extra, format=format, progress=progress,
                              client=client, rng=rng)


def _quote(text: str) -> str:
    return " ".join(re.sub(r"[\"“”`*#]", "", text or "").split()[:10]).strip(" '‘’")


def _write_movie_quote(settings: Settings, game: str, *, used: str, extra: str, format: str, progress: ProgressFn,
                       client, rng: random.Random) -> ShortScript:
    from . import script_writer as sw
    from .stickers import Reaction

    client, model = sw._gemini(settings, client)
    result = None
    for _ in range(TRIVIA_ROUNDS):
        seeds = [_movie_seed(rng) for _ in range(MOVIE_CANDIDATES)]
        prompt = MOVIE_QUOTE_PROMPT.format(count=len(seeds), seeds="; ".join(seeds), used=used, game=game, notes=extra)
        batch, model = _ask(client, model, GeminiMovieQuoteSet, prompt, progress)
        usable = [c for c in batch.candidates
                  if all(map(_quote, (c.trap, c.distractor, c.correct)))
                  and len({_quote(o).lower() for o in (c.trap, c.distractor, c.correct)}) == 3]
        if not usable:
            continue
        progress(None, "Checking the movie lines on Google")
        verdicts = fact_check(client, model, usable, progress)
        passed = [c for c, (ok, _) in zip(usable, verdicts) if ok]
        for c, (ok, why) in zip(usable, verdicts):
            if not ok:
                logger.info("Movie quote rejected: %s -> %s (%s)", c.question, c.correct, why)
        if passed:
            result = rng.choice(passed)
            break
        used += "; " + "; ".join(c.movie for c in usable)
    if result is None:
        raise sw.ScriptError("No movie line passed the Google check; try again")
    options = [_quote(result.trap), _quote(result.distractor), _quote(result.correct)]
    correct = options[2]
    order = options[:]
    rng.shuffle(order)
    letter = "ABC"[order.index(correct)]
    question = (_one_line(result.question, 15).rstrip(".!?") or "What is the real line") + "?"
    hook = _one_line(result.hook, 8) if result.hook.strip() and rng.random() < 0.5 else rng.choice(MOVIE_HOOKS)
    asks = [f"Option {l}: {o.rstrip('.!?')}." for l, o in zip("ABC", order)]
    only = "Only one is the real line."
    # The question is the first word spoken; the dare is only on the banner.
    lines = fit_quiz([question, *asks, "Think fast! Three. Two. One.", only, "Comment A, B or C right now!"], only)
    flashes = [Flash(word=question.split()[0], text="?"), *[Flash(word=f"Option {l}", text=l) for l in "ABC"],
               Flash(word="Think fast", text="THINK FAST!"), Flash(word="Three", text="3"), Flash(word="two", text="2"),
               Flash(word="one", text="1"), Flash(word="Comment", text="A, B OR C?")]
    choices = [f"{l}) {o}" for l, o in zip("ABC", order)]
    _remember(settings, "movie", shape="quote", movie=result.movie.strip(), answer=correct)
    pinned = public_pin(question, "only one of them is word for word")
    script = _script(settings, game, model, "movie", format, " ".join(lines), hook, flashes, f"{letter}) {correct}",
                     pinned, result, choices=choices,
                     on_screen=f"Answer: {letter}) {correct} ({result.movie.strip()}). {result.fact.strip()}")
    return script.model_copy(update={"reactions": [Reaction(word="real", mood="suspicious")]})


def _write_movie_emoji(settings: Settings, game: str, *, used: str, extra: str, format: str, progress: ProgressFn,
                       client, rng: random.Random) -> ShortScript:
    from . import script_writer as sw
    from .popups import Popup

    client, model = sw._gemini(settings, client)
    seed = _movie_seed(rng)
    result, model = _ask(client, model, GeminiMovieEmoji,
                         MOVIE_EMOJI_PROMPT.format(seed=seed, used=used, game=game, notes=extra), progress)
    movie = _one_line(result.movie, 8).rstrip(".")
    title_words = {w for w in re.findall(r"[a-z0-9']+", movie.lower()) if w not in ("the", "a", "an", "of")}
    pics = [p for p in result.emojis if p.emoji.strip() and p.line.strip()][:3]
    if len(pics) < 3 or not movie:
        raise sw.ScriptError("Gemini wrote an emoji quiz without 3 emojis; try again")
    lines, popups = [], []
    for pic in pics:
        line = _one_line(pic.line, 4)
        if title_words & set(re.findall(r"[a-z0-9']+", line.lower())):
            raise sw.ScriptError("Gemini gave the movie away in an emoji line; try again")
        # Rapid fire ("Balloons. Flying house. Grumpy old man.") and each emoji pops up as its line starts,
        # so all three are up by ~4 s.
        said = re.sub(r"^(an?|the)\s+", "", line, flags=re.I).strip(" .,!?")
        said = said[:1].upper() + said[1:]
        lines.append(f"{said}.")
        noun = pic.word.strip() if _norm(pic.word) and _norm(pic.word) in _norm(line) else line.split()[-1]
        popups.append(Popup(word=said[:40], emoji=pic.emoji.strip()[:16], query=noun.strip(".,!?")[:60]))
    hint = safe_hint(result.hint, movie, *title_words) or "it's a famous one"
    hook = rng.choice([h for h in EMOJI_HOOKS if h.lower() not in {o.lower() for o in recent_openings(settings)}]
                      or EMOJI_HOOKS)
    hint_line = f"Hint: {hint.rstrip('.!')}!"
    # The emojis right after the hook (all on screen by ~4 s), the hint only while it fits 12-15 s, and a
    # closing line that flows back into the hook on the replay.
    spoken = fit_quiz([hook, *lines, hint_line, COUNTDOWN], hint_line, limit=LOOP_MAX_WORDS - 6)
    end = loop_end(spoken)
    spoken.append(end)
    script = " ".join(spoken)
    # The emojis stay up side by side in the middle, so the steps flash above them.
    flashes = [Flash(word="Hint", text="HINT", y=TOP_FLASH_Y)] if hint_line in spoken else []
    flashes += [f.model_copy(update={"y": TOP_FLASH_Y}) for f in COUNTDOWN_FLASHES] + [got_it(end)]
    _remember(settings, "movie", shape="emoji", movie=f"{movie} ({result.year.strip()})", answer=movie)
    pinned = public_pin("Which movie is it?", hint)
    return _script(settings, game, model, "movie", format, script, hook, flashes, movie, pinned, result,
                   popups=popups, card=result.title_card or "GUESS THE MOVIE IN 5 SECONDS", end_card=LOOP_CARD,
                   on_screen=f"Answer: {movie} ({result.year.strip()}). Emojis: {' '.join(p.emoji for p in pics)}")
