"""Quiz Shorts ("brain workout"): viewers play along and comment their answer.

- math: a fast mental-math chain. The numbers are picked here, so the answer is always right; Gemini only
  writes the hook, title and captions (with a built-in fallback when Gemini is busy).
- riddle: Gemini writes a riddle told in 2-3 clues plus a final question.

Each number / clue flashes big in the middle of the screen the moment it is spoken (`Flash`), the video
ends on a big "?" and "COMMENT YOUR ANSWER", and the answer goes in the pinned comment.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .popups import _matches, _norm

if TYPE_CHECKING:
    from .config import Settings
    from .script_writer import ProgressFn, ShortScript

logger = logging.getLogger(__name__)

QuizKind = Literal["math", "riddle"]
QUIZ_MODES = ("math", "riddle")
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
    """Each flash starts on the first matching spoken word after the previous flash (numbers repeat)."""
    if not words:
        return []
    total = total if total is not None else words[-1].end + 0.6
    spoken = [_norm(w.text) for w in words]
    starts: list[tuple[str, float]] = []
    cursor = 0
    for flash in flashes:
        token = _norm(flash.word.split()[0]) if flash.word.split() else ""
        hit = next((i for i in range(cursor, len(words)) if token and _matches(spoken[i], token)), None)
        if hit is None:
            continue
        starts.append((flash.text, max(words[hit].start - 0.05, 0.0)))
        cursor = hit + 1
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
CAPTION_MARGIN = 800  # vertical: captions end at 58% of the height...
FLASH_Y = 0.43  # ...and the big number sits above them, both in the 36-60% band


def english_voice(voice: str) -> str:
    """Multilingual voices guess the language of short lines ("Plus six" came out French); quizzes use
    the English-only version of the same voice, e.g. en-US-AndrewMultilingualNeural → en-US-AndrewNeural."""
    return voice.replace("MultilingualNeural", "Neural")


def quiz_rate(rate: str) -> str:
    m = re.fullmatch(r"([+-]\d{1,3})%", rate or "")
    return f"+{max(int(m.group(1)) if m else 0, QUIZ_RATE)}%"


def render_style(landscape: bool) -> dict:
    """assemble_video options for a quiz: pulsing hook banner, numbers + captions in the middle band."""
    style: dict = {"title_pulse": True}
    if not landscape:
        style |= {"margin_v": CAPTION_MARGIN, "flash_y": FLASH_Y}
    return style


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
competitive, a little cheeky. Hooks dare the viewer (rarity claims like "Only 5% ...", "Don't pause this
video"). Never reveal the answer in the title, description, title card or hashtags. Plain English."""


class GeminiMathPack(BaseModel):
    hook: str = Field(description=(
        "The opening sentence, at most 8 words (it must be spoken in under 3 seconds), a dare with a rarity "
        "claim, e.g. 'Only 5% finish this without pausing.' or 'Only geniuses get this one right.' "
        "No numbers from the chain."))
    title: str = Field(description="YouTube title, at most 60 characters, no hashtags, no answer, e.g. 'Only 5% Get This Right 🧠'.")
    description: str = Field(description="One or two short sentences daring viewers to comment their answer, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 hashtags, e.g. #shorts #math #brainteaser #mathchallenge #quiz.")
    title_card: str = Field(description="2 to 4 word ALL-CAPS banner for the first 3 seconds, e.g. 'DON'T PAUSE'.")


class GeminiRiddle(BaseModel):
    hook: str = Field(description=(
        "The opening sentence, at most 10 words, a dare, e.g. 'Only 5% solve this riddle on the first try.'"))
    clues: list[str] = Field(description=(
        "The riddle told in 2 or 3 short spoken clues, one sentence each (at most 14 words), each clue narrowing "
        "it down, e.g. 'I have keys, but I open no locks.' The clues must point to exactly one fair answer."))
    question: str = Field(description="The final question, 2 to 6 words, e.g. 'What am I?' or 'Who is the thief?'")
    answer: str = Field(description="The one correct answer, 1 to 5 words, e.g. 'A piano'.")
    explanation: str = Field(description="One short sentence on why every clue fits the answer.")
    title: str = Field(description="YouTube title, at most 60 characters, no hashtags, no answer, e.g. 'Only 5% Solve This Riddle 🧩'.")
    description: str = Field(description="One or two short sentences daring viewers to comment their answer, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 hashtags, e.g. #shorts #riddle #brainteaser #puzzle #quiz.")
    title_card: str = Field(description="2 to 4 word ALL-CAPS banner for the first 3 seconds, e.g. 'RIDDLE TIME'.")


MATH_PROMPT = """Package this RAPID MATH CHAIN Short. The voiceover reads the chain fast, one step every
1.2-1.5 seconds, then asks viewers to comment their final number. The chain (for you only, don't repeat it):
{chain} (answer {answer}).
Write the hook, title, description, hashtags and title card. Topic flavour for the game background: {game}.{notes}"""

RIDDLE_PROMPT = """Write a MULTI-PART RIDDLE Short of about 20 seconds: hook, then the riddle in 2-3 clues
(spoken one at a time, each one making the viewer rethink), then the question. Viewers comment their guess.
Make it clever but fair: one clear answer that clicks when revealed, not a pun only you would get, and not
an overused one (no "What has keys but can't open locks", "What gets wetter the more it dries"). Riddle
types that work: "What am I?", a short who-did-it with a hidden clue, a lateral-thinking situation.
Double-check that every clue fits the answer. Background gameplay: {game}.{notes}"""

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
    return text if not text or text[-1] in ".!?" else text + "."


def _notes(notes: str) -> str:
    return f"\nCreator's notes: {notes.strip()}" if notes.strip() else ""


def write_quiz(settings: Settings, game: str, kind: QuizKind, *, notes: str = "", format: str = "short",
               progress: ProgressFn = lambda f, m: None, client=None, rng: random.Random | None = None) -> ShortScript:
    from . import script_writer as sw

    rng = rng or random.Random()
    if kind == "math":
        return _write_math(settings, game, notes=notes, format=format, progress=progress, client=client, rng=rng)
    client, model = sw._gemini(settings, client)
    result, model = _ask(client, model, GeminiRiddle, RIDDLE_PROMPT.format(game=game, notes=_notes(notes)), progress)
    clues = [_one_line(c, 18) for c in result.clues if c.strip()][:3]
    if len(clues) < 2:
        raise sw.ScriptError("Gemini wrote a riddle without clues; try again")
    question = (_one_line(result.question, 8).rstrip(".!?") or "What am I") + "?"
    hook = _one_line(result.hook, 12) or "Only 5% solve this riddle."
    lines = [hook, "Listen carefully."] + clues + [question, "Got it? Comment your answer right now!"]
    # "carefully" first, so a clue's first word ("I ...") can't match a word in the hook
    flashes = [Flash(word="carefully", text="RIDDLE")]
    flashes += [Flash(word=c.split()[0], text=f"CLUE {i}") for i, c in enumerate(clues, 1)]
    flashes.append(Flash(word=question.split()[0], text="?"))
    answer = _one_line(result.answer, 6).rstrip(".")
    pinned = f"🧩 Answer: {answer}. {result.explanation.strip()} Did you get it before the end?"
    return _script(settings, game, model, "riddle", format, " ".join(lines), hook, flashes, answer, pinned, result,
                   on_screen=f"Riddle answer: {answer}. {result.explanation.strip()}")


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
                           MATH_PROMPT.format(chain=steps_text, answer=answer, game=game, notes=_notes(notes)), progress)
    except sw.ScriptError as err:
        if "GEMINI_API_KEY" in str(err):
            raise
        logger.warning("Gemini unavailable for the math quiz, using the built-in texts: %s", err)
        model = "built-in"
        pack = GeminiMathPack(hook=rng.choice(MATH_HOOKS), title="Only 5% Get This Right 🧠",
                              description="Do it in your head, no pausing. Comment your final number!",
                              hashtags=["#shorts", "#math", "#brainteaser", "#mathchallenge", "#quiz"],
                              title_card="DON'T PAUSE")
    hook = _one_line(pack.hook, 10)
    if {str(answer), str(start)} & {_norm(t) for t in hook.split()}:
        hook = rng.choice(MATH_HOOKS)
    lines, flashes = math_lines(start, chain, rng)
    flashes.append(Flash(word="number", text="?"))
    closing = ["Got your number?", f"If you got {trap}, you made a mistake.", "Comment your answer right now!"]
    script = " ".join([hook, "Don't pause.", *lines, *closing])
    pinned = (f"✅ Answer: {answer}\n{steps_text}\nDid you beat the clock, or did you have to rewatch?")
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
    prompt = "\n".join([f"Package this {'RAPID MATH' if kind == 'math' else 'RIDDLE'} quiz Short the creator wrote.",
                        f"Background gameplay: {game}.{_notes(notes)}",
                        *([f"The correct answer is {solved[0]} ({solved[1]})."] if solved else []),
                        "VOICEOVER (do not change it):", text])
    pack, model = _ask(client, model, GeminiOwnQuiz, prompt, progress)
    answer = str(solved[0]) if solved else _one_line(pack.answer, 8).rstrip(".")
    working = solved[1] if solved else pack.working.strip()
    pinned = (f"✅ Answer: {answer}\n{working}\nDid you beat the clock, or did you have to rewatch?" if kind == "math"
              else f"🧩 Answer: {answer}. {working} Did you get it before the end?")
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
            flashes: list[Flash], answer: str, pinned: str, pack, *, on_screen: str) -> ShortScript:
    from . import script_writer as sw

    return sw.ShortScript(
        on_screen=on_screen,
        hook=hook,
        script=script,
        question="",
        pinned_comment=pinned,
        title=pack.title.strip().strip('"')[:100],
        description=pack.description.strip(),
        hashtags=sw._clean_hashtags(pack.hashtags),
        title_card=re.sub(r"[#*\"]", "", pack.title_card).strip().upper()[:40],
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
