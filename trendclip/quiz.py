"""Quiz Shorts ("brain workout"): viewers play along and comment their answer.

- math: a fast mental-math chain. The numbers are picked here, so the answer is always right; Gemini only
  writes the hook, title and captions (with a built-in fallback when Gemini is busy).
- riddle: Gemini writes a riddle told in 2-3 clues plus a final question.
- trivia: a 3-option speed question built on a surprising true fact.
- paradox: a mini story that traps the viewer between two answers that both seem right; viewers pick a
  side in one word ("Comment KEEP or RETURN"), which starts arguments in the replies.

Each number / clue flashes big in the middle of the screen the moment it is spoken (`Flash`), the video
ends on a big "?" and "COMMENT YOUR ANSWER", and the answer goes in the pinned comment.
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

QuizKind = Literal["math", "riddle", "trivia", "paradox"]
QUIZ_MODES = ("math", "riddle", "trivia", "paradox")
FLASH_SECONDS = 1.4  # each step stays up 1.2-1.5 s (or until the next one)
LAST_FLASH_SECONDS = 3.0
END_CARD = "COMMENT YOUR ANSWER"
MUSIC_MOOD = "funny_quirky"  # the upbeat folder


class Flash(BaseModel):
    """Big text that flashes in the middle of the screen when `word` is spoken."""

    word: str = Field(min_length=1, max_length=40, description="The spoken word that triggers it.")
    text: str = Field(min_length=1, max_length=16, description="What is shown, e.g. '+18' or 'CLUE 1'.")


@dataclass
class TimedFlash:
    text: str
    start: float
    end: float


def schedule_flashes(flashes: list[Flash], words: list, total: float | None = None) -> list[TimedFlash]:
    """Each flash starts on the first matching spoken word after the previous flash (numbers repeat).
    A several-word `word` ("it Venus") must be spoken in a row and starts on its first word."""
    if not words:
        return []
    total = total if total is not None else words[-1].end + 0.6
    spoken = [_norm(w.text) for w in words]
    starts: list[tuple[str, float]] = []
    cursor = 0
    for flash in flashes:
        tokens = [t for t in (_norm(w) for w in flash.word.split()) if t]
        hit = next((i for i in range(cursor, len(words) - len(tokens) + 1) if tokens
                    and all(_matches(spoken[i + k], t) for k, t in enumerate(tokens))), None)
        if hit is None:
            continue
        starts.append((flash.text, max(words[hit].start - 0.05, 0.0)))
        cursor = hit + len(tokens)
    out = []
    for i, (text, start) in enumerate(starts):
        last = i + 1 == len(starts)
        limit = total if last else starts[i + 1][1] - 0.05
        end = min(start + (LAST_FLASH_SECONDS if last else FLASH_SECONDS), limit)
        if end - start >= 0.3:
            out.append(TimedFlash(text, round(start, 3), round(end, 3)))
    return out


# --------------------------------------------------------------------------- math chain


class Step(BaseModel):
    op: Literal["+", "-", "×", "÷"]
    value: int


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
    if step.op == "+":
        return value + step.value
    if step.op == "-":
        return value - step.value
    if step.op == "×":
        return value * step.value
    return value // step.value


def _next_step(rng: random.Random, value: int, last_op: str | None) -> Step | None:
    options = []
    divisors = [d for d in range(2, 10) if value % d == 0 and value // d >= 2]
    if divisors and last_op != "÷":
        options.append(Step(op="÷", value=rng.choice(divisors)))
    if value <= 25 and last_op != "×":
        options.append(Step(op="×", value=rng.randint(2, 5 if value <= 12 else 3)))
    if value <= 150:
        options.append(Step(op="+", value=rng.randint(3, 29)))
    if value > 12:
        options.append(Step(op="-", value=rng.randint(2, min(29, value - 5))))
    options = [o for o in options if o.op != last_op]
    return rng.choice(options) if options else None


def make_chain(rng: random.Random | None = None, steps: int = 7) -> tuple[int, list[Step]]:
    """A start number and `steps` operations: whole numbers only, at least one × and one ÷, never
    the same operation twice in a row, answer 5-199."""
    rng = rng or random.Random()
    for _ in range(500):
        value = start = rng.randint(8, 40)
        chain: list[Step] = []
        for _ in range(steps):
            step = _next_step(rng, value, chain[-1].op if chain else None)
            if step is None:
                break
            chain.append(step)
            value = apply(value, step)
        ops = {s.op for s in chain}
        if len(chain) == steps and {"×", "÷"} <= ops and 5 <= value <= 199:
            return start, chain
    return 24, [Step(op="+", value=18), Step(op="÷", value=2), Step(op="×", value=3),
                Step(op="-", value=13), Step(op="+", value=7), Step(op="÷", value=3), Step(op="+", value=9)]


def solve(start: int, chain: list[Step]) -> list[int]:
    """The running total after the start and after each step."""
    values = [start]
    for step in chain:
        values.append(apply(values[-1], step))
    return values


def trap_answer(start: int, chain: list[Step]) -> int:
    """The number you get by missing one step in the middle: the 'If you got X, you made a mistake' bait."""
    answer = solve(start, chain)[-1]
    for skip in (2, 3, 1, 4):
        if skip < len(chain):
            rest = chain[:skip] + chain[skip + 1:]
            wrong = solve(start, rest)[-1]
            if wrong != answer and wrong > 0:
                return wrong
    return answer + 10


def working(start: int, chain: list[Step]) -> str:
    """'24 + 18 = 42 → ÷ 2 = 21 → …' for the pinned comment."""
    values = solve(start, chain)
    parts = [f"{start} {chain[0].op} {chain[0].value} = {values[1]}"]
    parts += [f"{s.op} {s.value} = {v}" for s, v in zip(chain[1:], values[2:])]
    return " → ".join(parts).replace(" - ", " − ")


def math_lines(start: int, chain: list[Step], rng: random.Random) -> tuple[list[str], list[Flash]]:
    lines = [f"Start with {start}."]
    flashes = [Flash(word=str(start), text=str(start))]
    for step in chain:
        lines.append(rng.choice(SAY[step.op]).format(n=step.value))
        flashes.append(Flash(word=str(step.value), text=f"{step.op}{step.value}"))
    return lines, flashes


# --------------------------------------------------------------------------- Gemini packaging


QUIZ_INSTRUCTION = """You package interactive "brain workout" quiz Shorts (YouTube Shorts, TikTok, Reels). The
viewer plays along in their head, then comments their answer: that is the whole point. Style: bold, fast,
competitive, a little cheeky. The first sentence is a dare spoken from the first millisecond, with no soft
intro; vary its kind from video to video: a rarity claim ("Only 3% ..."), a hard stop ("Stop. Do not
pause."), a bet ("Bet you can't ..."), a callout ("Gamers, this one's for you."), a time limit ("You have
five seconds."). Titles end with one emoji (🧠 or 🛑).
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
1.2-1.5 seconds, then asks viewers to comment their final number. The chain (for you only, don't repeat it):
{chain} (answer {answer}).
Write the hook, title, description, hashtags and title card. Topic flavour for the game background: {game}.{notes}"""

RIDDLE_PROMPT = """Write a MULTI-PART RIDDLE Short of about 20 seconds. It opens, from the very first word, on
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
        "The trap question, at most 12 words, 'Which [category item] is [surprising condition]?', e.g. 'Which "
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


TRIVIA_PROMPT = """Write {count} different GAMIFIED FACT TRAP candidates for a Short (15-20 seconds), one per
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

class GeminiParadox(BaseModel):
    name: str = Field(description="The paradox or dilemma it is based on, e.g. 'the crocodile dilemma' (for the creator only).")
    hook: str = Field(description=(
        "The opening dare, at most 8 words, spoken from the first millisecond, e.g. 'Only 2% can escape this "
        "paradox.' or 'This breaks 97% of brains.'"))
    setup: list[str] = Field(description=(
        "The mini story in 1 or 2 spoken sentences (at most 20 words each), concrete characters and stakes, "
        "e.g. 'A crocodile steals a child and promises to return him if the father can guess what it will do.'"))
    twist: str = Field(description=(
        "The move that springs the trap, one sentence of at most 16 words, e.g. \"The father guesses: 'You will "
        "NOT return my child!'\""))
    dilemma: str = Field(description="The question the viewer must decide, at most 10 words, e.g. 'What should the crocodile do?'")
    side_a: str = Field(description=(
        "The first branch as one sentence starting with 'If', at most 16 words, showing it fails, e.g. 'If he "
        "keeps the child, the father was right, so he must return him.'"))
    side_b: str = Field(description=(
        "The second branch as one sentence starting with 'But if', at most 14 words, showing it fails too, e.g. "
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


PARADOX_PROMPT = """Write a PARADOX TRAP Short (20-25 seconds): a tiny story that traps the viewer between two
answers that both seem right, so they can't scroll away until they pick a side, then argue about it in the
comments. Shape: dare hook, the story in 1-2 sentences, the twist that springs the trap, "Think fast!" plus the
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
                   rng: random.Random) -> ShortScript:
    from . import script_writer as sw
    from .stickers import Reaction

    client, model = sw._gemini(settings, client)
    idea = pick_paradox(settings, rng)
    used = used_paradoxes(settings)[-40:]
    prompt = PARADOX_PROMPT.format(idea=idea, used="; ".join(used) or "none yet", game=game,
                                   notes=_notes(notes) + _variety(settings))
    result, model = _ask(client, model, GeminiParadox, prompt, progress)
    setup = [_one_line(s, 22) for s in result.setup if s.strip()][:2]
    twist, side_a, side_b = _one_line(result.twist, 18), _one_line(result.side_a, 18), _one_line(result.side_b, 16)
    if not setup or not all((twist, side_a, side_b)):
        raise sw.ScriptError("Gemini wrote a paradox without its story or both sides; try again")
    vote_a, vote_b = _vote(result.vote_a, "A"), _vote(result.vote_b, "B")
    if vote_a == vote_b:
        vote_a, vote_b = "A", "B"
    hook = _one_line(result.hook, 9) or rng.choice(PARADOX_HOOKS)
    dilemma = (_one_line(result.dilemma, 11).rstrip(".!?") or "What should happen") + "?"
    debate = (_one_line(result.debate, 18).rstrip(".!?") or f"So, {vote_a.lower()} or {vote_b.lower()}") + "?"
    closing = f"Comment {vote_a.lower()} or {vote_b.lower()} right now!"
    lines = [hook, *setup, twist, f"Think fast! {dilemma}", side_a, side_b, debate, closing]
    picks = f"{vote_a} OR {vote_b}?"
    flashes = [Flash(word=" ".join(setup[0].split()[:2]), text="PARADOX"),
               Flash(word="Think fast", text="THINK FAST!"),
               Flash(word=" ".join(side_a.split()[:2]), text=f"A) {vote_a}"),
               Flash(word=" ".join(side_b.split()[:2]), text=f"B) {vote_b}"),
               Flash(word=debate.split()[0], text="?"),
               Flash(word="Comment", text=picks if len(picks) <= 16 else "A OR B?")]
    pinned = f"{debate} 👇\nTeam {vote_a} or Team {vote_b}? Defend your side!"
    resolution = " ".join(result.resolution.split())
    _remember(settings, "paradox", idea=idea, name=result.name.strip() or idea)
    script = _script(settings, game, model, "paradox", format, " ".join(lines), hook, flashes,
                     f"No single answer: it's {result.name.strip() or 'a paradox'}.", pinned, result,
                     on_screen=f"Paradox: {result.name.strip()}. {resolution}")
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


def _one_line(text: str, max_words: int) -> str:
    text = re.sub(r"[\"“”`*#]|\s+", " ", text or "").strip()
    words = text.split()
    text = " ".join(words[:max_words])
    return text if not text or text.rstrip("'’)")[-1:] in (".", "!", "?") else text + "."


def _notes(notes: str) -> str:
    return f"\nCreator's notes: {notes.strip()}" if notes.strip() else ""


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
    client, model = sw._gemini(settings, client)
    used = used_riddles(settings)
    recent = {d.get("topic") for d in history(settings, "riddle")[-(len(RIDDLE_TOPICS) // 2):]}
    topic = rng.choice([t for t in RIDDLE_TOPICS if t not in recent] or list(RIDDLE_TOPICS))
    style = rng.choice(RIDDLE_STYLES)
    for attempt in range(2):
        prompt = RIDDLE_PROMPT.format(topic=topic, style=style, used="; ".join(used[-60:]) or "none yet", game=game,
                                      notes=_notes(notes) + _variety(settings))
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
    lines = [hook, *clues, question, COUNTDOWN, f"Hint: {hint}.", CLOSING]
    flashes = [Flash(word=" ".join(c.split()[:2]), text=f"CLUE {i}") for i, c in enumerate(clues, 1)]
    flashes += [Flash(word=question.split()[0], text="?"), *COUNTDOWN_FLASHES, Flash(word="Hint", text="HINT")]
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


def _trivia_options(trivia: GeminiTrivia) -> tuple[str, str, str] | None:
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


def fact_check(client, model: str, candidates: list[GeminiTrivia],
               progress: ProgressFn) -> list[tuple[bool, str]]:
    """(passed, reason) per candidate, checked by Gemini with Google Search. Unanswered ones fail."""
    from google.genai import errors, types

    from . import script_writer as sw

    listing = "\n\n".join(
        f"{i}. Q: {c.question.strip().rstrip('?')}?\n   Claimed correct: {c.correct}\n   Wrong options: {c.trap}; "
        f"{c.distractor}\n   Claimed proof: {c.fact}" for i, c in enumerate(candidates, 1))
    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0)
    try:
        response, _ = sw._generate_with_fallback(client, model, [FACT_CHECK.format(candidates=listing)], config,
                                                 progress, errors.APIError)
    except errors.APIError as err:
        raise sw._explain(err, model) from err
    found: dict[int, tuple[bool, str]] = {}
    for line in (response.text or "").splitlines():
        if (m := _VERDICT.match(line.strip())) and int(m.group(1)) not in found:
            found[int(m.group(1))] = (m.group(2).upper() == "PASS", m.group(3).strip(" :-"))
    return [found.get(i, (False, "not checked")) for i in range(1, len(candidates) + 1)]


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
                                      game=game, notes=_notes(notes) + _variety(settings))
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
    question = (_one_line(result.question, 14).rstrip(".!?") or "Which one is it") + "?"
    options = [trap, distractor, correct]
    rng.shuffle(options)
    hooks = [h for h in TRIVIA_HOOKS if "game" in result.category.lower() or "Gamers" not in h] or TRIVIA_HOOKS
    opened = {o.lower() for o in recent_openings(settings)}
    hooks = [h for h in hooks if h.lower() not in opened] or hooks
    hook = _one_line(result.hook, 8) if result.hook.strip() and rng.random() < 0.4 else rng.choice(hooks)
    _remember(settings, "trivia", answer=correct, question=question)
    hint = safe_hint(result.hint, correct, trap, distractor) or hint_from_fact(result.fact, correct, trap, distractor)
    asks = [f"Is it {options[0]}?", f"Is it {options[1]}?", f"Or is it {options[2]}?"]
    lines = [hook, question, *asks, "Think fast! Three. Two. One.", f"Hint: {hint}.",
             "Comment the real answer right now!"]
    # The question first, so an option named in the hook or question can't trigger its flash early.
    flashes = [Flash(word=question.split()[0], text="?")]
    for letter, option in zip("ABC", options):
        name = re.sub(r"^(the|an?) ", "", option, flags=re.I)
        label = f"{letter}) {name[:1].upper()}{name[1:]}"
        flashes.append(Flash(word=f"it {option.split()[0]}", text=label if len(label) <= 16 else letter))
    flashes += [Flash(word="Think fast", text="THINK FAST!"), Flash(word="Three", text="3"), Flash(word="two", text="2"),
                Flash(word="one", text="1"), Flash(word="Hint", text="HINT"), Flash(word="real answer", text="?")]
    fact = result.fact.strip()
    pinned = public_pin(question, hint)
    script = _script(settings, game, model, "trivia", format, " ".join(lines), hook, flashes, correct, pinned,
                     result, on_screen=f"Answer: {correct} (trap: {trap}). {fact}")
    return script.model_copy(update={"reactions": [Reaction(word="Hint", mood="suspicious")]})


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
                           MATH_PROMPT.format(chain=steps_text, answer=answer, game=game, notes=_notes(notes) + _variety(settings)), progress)
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
    # The start number would fire its flash early ("Only 10%..."); the answer just mustn't be given away.
    if str(start) in {_norm(t) for t in hook.split()} or str(answer) in {t.strip(".,!?") for t in hook.split()}:
        hook = rng.choice(MATH_HOOKS)
    lines, flashes = math_lines(start, chain, rng)
    flashes += [Flash(word="number", text="?"), *COUNTDOWN_FLASHES, Flash(word="Hint", text="HINT")]
    hint = digit_hint(answer)
    closing = ["Got your number?", COUNTDOWN, f"Hint: {hint}.", CLOSING]
    script = " ".join([hook, *lines, *closing])
    pinned = public_pin("What number did you get?", hint)
    return _script(settings, game, model, "math", format, script, hook, flashes, str(answer), pinned, pack,
                   on_screen=f"Math chain: {steps_text} (answer {answer})")


# --------------------------------------------------------------------------- your own quiz text


CLOSING = "Comment your answer right now!"
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
    label = {"math": "RAPID MATH", "riddle": "RIDDLE", "trivia": "TRIVIA TRAP", "paradox": "PARADOX TRAP"}[kind]
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
            flashes: list[Flash], answer: str, pinned: str, pack, *, on_screen: str, card: str = "") -> ShortScript:
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
        end_card=END_CARD,
        flashes=flashes,
        answer=answer,
        game=game,
        model=model,
        mode=mode,
        format=format,
        watched_clip=False,
        word_count=len(script.split()),
        estimated_seconds=sw.estimate_seconds(script),
    )
