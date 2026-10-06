"""Gemini writes the Short: it watches the start of the background clip and returns a voiceover
script, title, description and hashtags as structured JSON."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, Field

from . import music
from .config import PROJECT_ROOT, Settings
from .popups import Popup, _matches, _norm, clean_popups
from .stickers import Reaction

logger = logging.getLogger(__name__)

WORDS_PER_SECOND = 3.3  # measured edge-tts at +5%: 3.1 (long words) to 3.8 (short words) words/s
UPLOAD_WAIT_SECONDS = 180
# Tried in order after GEMINI_MODEL when it is overloaded, rate limited or unavailable.
FALLBACK_MODELS = ["gemini-3.7-flash", "gemini-flash-latest", "gemini-2.5-flash"]
RETRYABLE_CODES = {500, 502, 503, 504}
RETRY_DELAYS = (4, 10, 0)  # seconds to wait after attempts 1 and 2; 3 attempts per model
ProgressFn = Callable[[float | None, str], None]


class ScriptError(RuntimeError):
    pass


MUSIC_MOOD_HELP = (
    "Background music mood for this voiceover, exactly one of: 'funny_quirky' (awkward, weird, embarrassing, "
    "silly, chaotic stories) or 'chill_lofi' (casual, cozy, relaxed storytelling, mysteries, facts, tips).")
QUESTION_MAX_WORDS = 5  # "under 6 words"
DEFAULT_QUESTION = "What would you do?"
PINNED_MAX = 200


class GeminiShort(BaseModel):
    """Response schema sent to Gemini (field descriptions become part of the instructions)."""

    on_screen: str = Field(description="One or two sentences on what actually happens in the clip.")
    hook: str = Field(description=(
        "The first sentence of the script, at most 12 words: a high-stakes, surprising or bold claim that opens "
        "a curiosity gap in the first 3 seconds, e.g. 'I accidentally committed a crime at my office.'"))
    script: str = Field(description=(
        "The full voiceover: starts with the hook word for word and ends with the question (no follow / "
        "subscribe line: the channel adds it). Plain spoken English only."))
    question: str = Field("", description=(
        f"The last sentence of the script: a punchy, conversational question of 2 to {QUESTION_MAX_WORDS} words "
        "that makes viewers comment their own story, e.g. 'Ever done this?' or 'What would you do?'"))
    pinned_comment: str = Field("", description=(
        f"A friendly comment the creator pins under the video right after upload, at most {PINNED_MAX} characters: "
        "a playful line about the story plus a question that invites viewers to share their own. No links, no hashtags."))
    title: str = Field(description="YouTube Shorts title, at most 70 characters, no hashtags.")
    description: str = Field(description="Two or three short sentences for the video description, no hashtags.")
    hashtags: list[str] = Field(description="5 to 8 relevant hashtags, each starting with #, including #shorts.")
    title_card: str = Field("", description=(
        "2 to 5 word ALL-CAPS banner shown at the top for the first 3 seconds, e.g. 'CAT LOGIC 101'. "
        "Punchy and curiosity-building; different from the title."))
    popups: list[Popup] = Field(default_factory=list, description=(
        "Pop-up images, one every 3 to 5 seconds of speech (about one per 12 words): concrete, easy to picture "
        "things the voiceover mentions (animals, objects, food, places), spread evenly from the first sentence "
        "to the end, in script order. Never abstract words."))
    reactions: list[Reaction] = Field(default_factory=list, description=(
        "2 to 5 reaction-sticker beats, in script order: the word where a funny, awkward or shocking "
        "moment lands (a meme reaction pops up there). Between the pop-ups, not in the first sentence, "
        "not in the closing question."))
    music_mood: str = Field("", description=MUSIC_MOOD_HELP)


class ShortScript(GeminiShort):
    game: str
    model: str
    mode: str = "clip"
    format: str = "short"
    watched_clip: bool
    word_count: int
    estimated_seconds: float
    part: int | None = None
    parts_total: int | None = None
    end_card: str = ""


class GeminiSeries(BaseModel):
    """Response schema for a two-part story."""

    story_name: str = Field(description="2 to 4 word Title Case name of the story, used for file names, e.g. 'The Wrong Uber'.")
    part1: GeminiShort = Field(description="Part 1: setup and escalation, ending on the cliffhanger.")
    part2: GeminiShort = Field(description="Part 2: picks up right after the cliffhanger and resolves it.")
    pinned_comment: str = Field(description=(
        "A short, friendly comment the creator pins under Part 1 that teases Part 2 without spoiling it and "
        f"asks viewers to guess what happens. No links, no hashtags, at most {PINNED_MAX} characters."))
    music_mood: str = Field("chill_lofi", description=MUSIC_MOOD_HELP + " One mood for both parts.")


class SeriesScript(BaseModel):
    series_id: str
    story_name: str
    model: str
    game: str
    handle: str
    pinned_comment: str
    parts: list[ShortScript]
    music_mood: str = "chill_lofi"


def gemini_api_key(settings: Settings | None = None) -> str | None:
    """Read fresh from .env each time, so adding the key works without restarting the dashboard."""
    from_file = dotenv_values(PROJECT_ROOT / ".env").get("GEMINI_API_KEY")
    key = (from_file or os.getenv("GEMINI_API_KEY") or "").strip()
    if not key and settings and settings.gemini_api_key:
        key = settings.gemini_api_key.get_secret_value().strip()
    return key or None


def gemini_model(settings: Settings) -> str:
    return (dotenv_values(PROJECT_ROOT / ".env").get("GEMINI_MODEL") or settings.gemini_model).strip()


def estimate_seconds(text: str) -> float:
    return round(len(text.split()) / WORDS_PER_SECOND, 1)


SYSTEM_INSTRUCTION = """You write voiceovers for vertical gaming Shorts (YouTube Shorts, TikTok, Reels).
The voiceover is read by a text-to-speech voice over gameplay footage, with karaoke subtitles.

Rules:
- The script is plain spoken English. No emojis, hashtags, stage directions, brackets, timestamps or speaker labels.
- HOOK (the first 3 seconds decide everything): the very first sentence is the hook. It drops the viewer
  straight into the most high-stakes, surprising or absurd moment with a bold claim or a curiosity gap
  that only the rest of the video answers.
  - Bad (slow): "I once stole someone's lunch at work." / "So this happened last week." / "Let me tell you
    about my neighbour."
  - Good: "I accidentally committed a crime at my office." / "My neighbour has been living in my attic." /
    "This one mistake got me banned from every server."
  - Never open with "So", "Hey guys", "One day", "I once", "Let me tell you", "This is the story of",
    a greeting, background or scene-setting; context comes after the hook.
- Short punchy sentences. Talk to the viewer ("you"). Keep energy high but natural.
- Only state facts you are confident are true about the game; prefer tips, reactions, questions and
  observations over specific numbers, dates or patch details you are unsure of.
- Never mention copyright, footage sources, AI, or that the clip is stock gameplay.
- ENDING: the last sentence of the script is `question`: a short, punchy, conversational question (2 to 5
  words) that makes viewers comment their own story, e.g. "Ever done this?", "What would you do?",
  "Worst coworker story?". Do NOT write a follow / subscribe call to action: the channel adds its own
  one-line call to action right after your question, as the very last line.
- pinned_comment: the comment the creator pins right after upload; it keeps the conversation going.
- Pop-ups: each `word` must appear exactly as written in your script; give the best matching emoji.
  Keep the screen busy: a new pop-up every 3 to 5 seconds of speech.
- Reactions: each `word` must appear exactly as written in your script, at the funniest, most awkward
  or most shocking beats.
- music_mood: classify the whole voiceover as funny_quirky or chill_lofi; this picks the background music.
- Never wrap words in backticks, quotes or markdown.
"""

ScriptMode = Literal["clip", "story"]

STORY_BRIEF = """Format: STORYTIME. The gameplay is only a background to keep eyes on screen; the voiceover is a
random, self-contained story that is NOT about the game or the footage.
- Pick a fresh, surprising everyday premise (school, work, family, neighbours, a date, a trip, a pet,
  a weird coincidence...). Vary it every time; avoid the most obvious clichés.
- First person, past tense, like a friend telling it. Clear setup, escalating middle, twist or punchline.
- Fictional and family friendly: no real people, brands' wrongdoing, violence, or anything hateful.
- The hook is the most dramatic or absurd line of the story, told first: a bold claim that teases the
  twist without giving it away. Then jump back to how it started.
- on_screen: one sentence summarising the story.
- Title and hashtags describe the story (#storytime is good); you may add one gaming hashtag.
- The creator's notes win over these defaults: if they ask for a genre (mystery, drama, adventure...) or a
  different format such as facts, write exactly that, still fast, hooky and not about the footage."""

ScriptFormat = Literal["short", "long", "multi"]

LONG_BRIEF = """Format: LONG-FORM. This one is longer, so it must earn every second:
- More detail and wit: vivid specific details, funny asides, a running joke or callback that pays off at the end.
- Two or three escalating beats; each one raises the stakes or the absurdity. No filler, no recap, no padding.
- Mix short punchy lines with a few longer ones so the rhythm feels like real storytelling.
- Drop a mini-hook every 15 seconds or so ("and that's when it got worse") so viewers keep watching.
- Spread the pop-ups across the whole script, one every 3 to 5 seconds."""

SERIES_BRIEF = """Format: TWO-PART SERIES. Write ONE story split into Part 1 and Part 2, released a day apart.
- Part 1 introduces the situation and the characters, builds tension and humour, and ends on a dramatic or
  funny CLIFFHANGER: stop right before the big reveal. The last sentence of Part 1 is the cliffhanger itself.
- Part 1's hook follows the HOOK rule. Part 2 opens with a high-stakes one-sentence hook that drops viewers
  right back into the cliffhanger, then resolves the story with a hilarious twist or a satisfying ending.
- Do NOT write "follow", "subscribe" or "part 2 tomorrow" in the script, and do NOT end the script with the
  question: put each part's closing question only in its `question` field. The channel adds that question
  and then its own call to action at the end of each part.
- Each part has its own hook, title, description, hashtags, title_card and popups. Do not put "Part 1"
  or "Part 2" in titles or title cards; they are added automatically.
- story_name names the whole story; pinned_comment teases Part 2."""

CTA_WORDS = 16  # room left in each part for the call to action + question appended after Gemini
POPUP_EVERY_SECONDS = 4


def popup_target(seconds: float) -> int:
    return max(3, min(15, round(seconds / POPUP_EVERY_SECONDS)))


def build_prompt(game: str, target_seconds: int, trend_titles: list[str], notes: str, watched: bool,
                 mode: ScriptMode = "clip", format: ScriptFormat = "short") -> str:
    words = int(target_seconds * WORDS_PER_SECOND)
    pace = (f"Visual pacing: about {popup_target(target_seconds)} pop-ups{' per part' if format == 'multi' else ''}, "
            "one every 3 to 5 seconds, so something new pops up on screen all the time.")
    if format == "multi":
        words -= CTA_WORDS
        length = (f"Target length: about {target_seconds} seconds of speech PER PART, so {words - 8} to "
                  f"{words + 5} words in EACH part.")
        lines = [STORY_BRIEF, SERIES_BRIEF, f"Background gameplay: {game}", length, pace]
        if notes.strip():
            lines.append(f"Creator's notes for the story: {notes.strip()}")
        return "\n".join(lines)

    length = f"Target length: about {target_seconds} seconds of speech, so {words - 8} to {words + 5} words in total."
    extra = [LONG_BRIEF] if format == "long" else []
    if mode == "story":
        lines = [STORY_BRIEF, *extra, f"Background gameplay: {game}", length, pace]
        if notes.strip():
            lines.append(f"Creator's notes for the story: {notes.strip()}")
        return "\n".join(lines)

    lines = [*extra, f"Game: {game}", length, pace]
    if watched:
        lines.append(
            f"The attached video is the exact footage that plays under the voiceover ({target_seconds}s). "
            "Tie the narration to what is visible in it."
        )
    else:
        lines.append("You have not seen the footage; write narration that fits generic gameplay of this game.")
    if trend_titles:
        lines.append("Videos about this game trending on YouTube right now (use them for the angle, don't copy them):")
        lines += [f"- {t}" for t in trend_titles[:10]]
    if notes.strip():
        lines.append(f"Creator's notes for this Short: {notes.strip()}")
    return "\n".join(lines)


def _preview_clip(clip: Path, seconds: int) -> Path:
    """Small 480p silent copy of the part of the clip the Short uses: quick to upload."""
    from .video_assembler import ffmpeg_with_libass

    out = Path(tempfile.mkstemp(prefix="trendclip-preview-", suffix=".mp4")[1])
    proc = subprocess.run(
        [ffmpeg_with_libass(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
         "-t", str(seconds + 2), "-vf", "scale=-2:480", "-r", "15", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", str(out)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        raise ScriptError(f"Could not prepare the clip for Gemini: {proc.stderr.strip()[-200:]}")
    return out


def _explain(err: Exception, model: str) -> ScriptError:
    code = getattr(err, "code", None)
    text = str(getattr(err, "message", "") or err)
    if code in (401, 403) or "API key" in text or "API_KEY" in text:
        return ScriptError("Gemini rejected the API key: check GEMINI_API_KEY in .env")
    if code == 429:
        return ScriptError("Gemini quota reached (free tier limit); wait a minute or try tomorrow")
    if code in RETRYABLE_CODES:
        return ScriptError("Gemini is overloaded right now (all models busy); try again in a minute")
    if code == 404:
        return ScriptError(f"Gemini model '{model}' not found: set GEMINI_MODEL in .env")
    return ScriptError(f"Gemini error{f' {code}' if code else ''}: {text[:300]}")


def _generate_with_fallback(client, model: str, contents: list, config, progress: ProgressFn, api_error: type):
    """Retry overloaded models (500/503) with backoff, then fall back to other Flash models.
    Returns (response, model actually used)."""
    models = [model] + [m for m in FALLBACK_MODELS if m != model]
    last: Exception | None = None
    for name in models:
        for attempt, delay in enumerate(RETRY_DELAYS):
            busy = f" (busy, retry {attempt})" if attempt else ""
            progress(None, f"Gemini ({name}) is writing the script{busy}")
            try:
                return client.models.generate_content(model=name, contents=contents, config=config), name
            except api_error as err:
                last = err
                code = getattr(err, "code", None)
                if code in RETRYABLE_CODES and attempt + 1 < len(RETRY_DELAYS):
                    time.sleep(delay)
                    continue
                if code in RETRYABLE_CODES or code in (404, 429):
                    logger.warning("Gemini model %s unavailable (%s); trying the next one", name, code)
                    break
                raise
    raise last  # every model failed; the caller explains the last error


class Shortened(BaseModel):
    script: str


SHORTEN_PROMPT = """This voiceover is {words} words, but it must be spoken in {seconds} seconds, so it may have
AT MOST {max_words} words. Rewrite it to {low} to {max_words} words. Keep the hook, the key beats and the
ending / punchline, and the same voice and language. Cut filler and side details; don't add anything new.
Plain spoken text only.{extra}

Voiceover:
{script}"""


def max_words(seconds: float) -> int:
    return int(seconds * WORDS_PER_SECOND)


def _cut_to(script: str, limit: int) -> str:
    """Whole sentences up to `limit` words (last resort when Gemini can't shorten)."""
    out: list[str] = []
    count = 0
    for sentence in re.split(r"(?<=[.!?…])\s+", script.strip()):
        n = len(sentence.split())
        if count + n > limit:
            break
        out.append(sentence)
        count += n
    return " ".join(out) if out else " ".join(script.split()[:limit])


OWN_STORY_SHORTEN = """
This is the creator's own story: keep their sentences and wording wherever possible. Mostly drop whole
sentences or clauses; only rephrase to join what is left. Keep every clue the ending depends on."""


def fit_length(client, model: str, script: str, limit: int, seconds: float, progress: ProgressFn,
               own: bool = False) -> str:
    """Gemini often overshoots the target; ask it once to tighten the script, then cut at a sentence end."""
    words = len(script.split())
    if words <= limit:
        return script
    from google.genai import types

    progress(None, f"Script is {words} words; tightening it to {limit} so it fits {seconds:.0f}s")
    try:
        config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=Shortened,
                                             temperature=0.4)
        prompt = SHORTEN_PROMPT.format(words=words, seconds=round(seconds), max_words=limit,
                                       low=max(limit - 25, limit * 3 // 4), script=script,
                                       extra=OWN_STORY_SHORTEN if own else "")
        response, _ = _generate_with_fallback(client, model, [prompt], config, progress, Exception)
        parsed = response.parsed if isinstance(response.parsed, Shortened) else Shortened.model_validate_json(response.text)
        shorter = _clean_script(parsed.script)
        if 0 < len(shorter.split()) < words:
            script = shorter
    except Exception as err:  # noqa: BLE001 - fall back to cutting whole sentences
        logger.warning("Could not shorten the script with Gemini: %s", err)
    return _cut_to(script, limit)


def _clean_hashtags(tags: list[str]) -> list[str]:
    out = []
    for tag in tags:
        tag = "#" + re.sub(r"[^\w]", "", tag.lstrip("#"))
        if len(tag) > 1 and tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    if "#shorts" not in {t.lower() for t in out}:
        out.append("#shorts")
    return out[:10]


def _clean_script(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)|\*+|`+", "", text)  # stage directions / markdown
    text = re.sub(r"#\w+", "", text)
    return re.sub(r"\s+", " ", text).strip()


_ABBREVIATION = re.compile(r"\b(mr|mrs|ms|dr|st|vs|jr|sr)\.$", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    """Sentences; 'a.m. she', 'Mr. Smith' and similar don't end one."""
    sentences: list[str] = []
    for piece in re.split(r"(?<=[.!?…])\s+", text.strip()):
        if sentences and (piece[:1].islower() or _ABBREVIATION.search(sentences[-1])):
            sentences[-1] += f" {piece}"
        elif piece:
            sentences.append(piece)
    return sentences


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def clean_question(text: str | None) -> str | None:
    """A closing question of 2 to 5 words ending in '?', or None."""
    text = re.sub(r"[\"“”`*#]|\s+", " ", text or "").strip().rstrip(".!")
    if not text:
        return None
    text = text[:1].upper() + text[1:]
    text = text if text.endswith("?") else f"{text}?"
    return text if 2 <= len(text.split()) <= QUESTION_MAX_WORDS else None


def split_question(script: str) -> tuple[str, str]:
    """(script without its closing question, that question or '')."""
    sentences = _sentences(script)
    if len(sentences) > 1 and sentences[-1].endswith("?"):
        return " ".join(sentences[:-1]), sentences[-1]
    return script.strip(), ""


def lead_with_hook(script: str, hook: str) -> str:
    """The hook is spoken first: drop a slow opener Gemini put before it, or put the hook in front."""
    hook = _clean_script(hook)
    key = _words(hook)[:5]
    if not key:
        return script
    sentences = _sentences(script)
    for i, sentence in enumerate(sentences[:3]):
        if _words(sentence)[:len(key)] == key:
            return " ".join(sentences[i:])
    return f"{hook if hook[-1] in '.!?…' else hook + '.'} {script}".strip()


def clean_pinned(text: str, question: str) -> str:
    text = re.sub(r"https?://\S+|#\w+", "", text or "")
    text = re.sub(r"\s+", " ", text).strip().strip('"')
    return (text or f"{question} Tell me your story below 👇")[:PINNED_MAX * 2]


def write_script(
    settings: Settings,
    game: str,
    clip: Path | None = None,
    *,
    trend_titles: list[str] | None = None,
    notes: str = "",
    target_seconds: int | None = None,
    watch_clip: bool = True,
    mode: ScriptMode = "clip",
    format: ScriptFormat = "short",
    max_seconds: float | None = None,
    progress: ProgressFn = lambda f, m: None,
    client=None,
) -> ShortScript:
    """`max_seconds` (the clip length) is a hard cap: the voiceover never outlasts the gameplay."""
    api_key = gemini_api_key(settings)
    if client is None and not api_key:
        raise ScriptError("GEMINI_API_KEY is not set in .env (create one at https://aistudio.google.com/apikey)")
    from google import genai
    from google.genai import errors, types

    model = gemini_model(settings)
    target = target_seconds or settings.short_target_seconds
    capped = bool(max_seconds and max_seconds < target)
    if capped:
        target = max(int(max_seconds), 10)
    limit = max_words(target) + (0 if capped else 5)
    client = client or genai.Client(api_key=api_key)
    watched = bool(mode == "clip" and watch_clip and clip and clip.is_file())

    contents: list = []
    uploaded = None
    preview = None
    try:
        if watched:
            progress(None, "Preparing the clip for Gemini")
            preview = _preview_clip(clip, target)
            progress(None, "Uploading the clip to Gemini")
            uploaded = client.files.upload(file=str(preview), config={"mime_type": "video/mp4"})
            deadline = time.monotonic() + UPLOAD_WAIT_SECONDS
            while uploaded.state and uploaded.state.name == "PROCESSING":
                if time.monotonic() > deadline:
                    raise ScriptError("Gemini took too long to process the clip; try again or untick 'watch the clip'")
                progress(None, "Gemini is watching the clip")
                time.sleep(3)
                uploaded = client.files.get(name=uploaded.name)
            if uploaded.state and uploaded.state.name == "FAILED":
                raise ScriptError("Gemini could not process the clip")
            contents.append(uploaded)

        contents.append(build_prompt(game, target, trend_titles or [], notes, watched, mode,
                                     "long" if format == "long" else "short"))
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=GeminiShort,
            temperature=1.1 if mode == "story" else 0.9,
        )
        response, model = _generate_with_fallback(client, model, contents, config, progress, errors.APIError)
        result = response.parsed
        if not isinstance(result, GeminiShort):
            if not response.text:
                raise ScriptError("Gemini returned an empty answer (possibly blocked); try different notes")
            result = GeminiShort.model_validate_json(response.text)
    except errors.APIError as err:
        raise _explain(err, model) from err
    finally:
        if preview:
            preview.unlink(missing_ok=True)
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:  # noqa: BLE001 - files expire after 48h anyway
                logger.debug("Could not delete uploaded file %s", uploaded.name)

    body, own_question = split_question(_strip_cta(_clean_script(result.script)))
    body = _strip_cta(body)  # "Follow for more. What would you do?": both are replaced by ours
    question = clean_question(result.question) or clean_question(own_question) or DEFAULT_QUESTION
    cta = follow_cta(settings.channel_handle, body)
    body = lead_with_hook(body, result.hook)
    body = fit_length(client, model, body, limit - len(question.split()) - len(cta.split()), target, progress)
    script = f"{body} {question} {cta}"
    return _finish(result, game=game, model=model, mode=mode, format=format, watched=watched, script=script,
                   question=question)


def _finish(result: GeminiShort, *, game: str, model: str, mode: str, format: str, watched: bool,
            script: str | None = None, question: str | None = None, **extra) -> ShortScript:
    script = script if script is not None else _clean_script(result.script)
    question = question or clean_question(result.question) or DEFAULT_QUESTION
    return ShortScript(
        question=question,
        pinned_comment=clean_pinned(result.pinned_comment, question),
        on_screen=result.on_screen.strip(),
        hook=result.hook.strip(),
        script=script,
        title=result.title.strip().strip('"')[:100],
        description=result.description.strip(),
        hashtags=_clean_hashtags(result.hashtags),
        title_card=re.sub(r"[#*\"]", "", result.title_card).strip().upper()[:40],
        popups=clean_popups(result.popups, script),
        reactions=clean_reactions(result.reactions, script),
        game=game,
        model=model,
        mode=mode,
        format=format,
        watched_clip=watched,
        word_count=len(script.split()),
        estimated_seconds=estimate_seconds(script),
        **{"music_mood": music.normalize_mood(result.music_mood) or music.guess_mood(script), **extra},
    )


def clean_reactions(reactions: list[Reaction], script: str, limit: int = 5) -> list[Reaction]:
    """Reactions whose word is in the script, in script order, one per word."""
    tokens = [_norm(t) for t in script.split()]
    out, seen = [], set()
    for r in reactions:
        first = _norm(r.word.split()[0]) if r.word.split() else ""
        pos = next((i for i, t in enumerate(tokens) if _matches(t, first)), None)
        if pos is None or first in seen:
            continue
        seen.add(first)
        out.append((pos, Reaction(word=r.word.strip(), mood=r.mood)))
    return [r for _, r in sorted(out, key=lambda x: x[0])][:limit]


def spoken_handle(handle: str) -> str:
    """'@SideQuestLogic' -> 'Side Quest Logic': TTS voices stumble over '@' and run-together words."""
    name = handle.lstrip("@").replace("_", " ").replace(".", " ")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).strip()


# The last line of every Short (about 3 s of speech), after the closing question.
FOLLOW_CTAS = (
    "Hit that subscribe button for daily side quest stories!",
    "Follow {name} so you never miss a single story!",
    "Subscribe to see if your story gets featured in the next one!",
    "Subscribe for a brand new side quest story every single day!",
)


def follow_cta(handle: str, seed: str = "") -> str:
    """One of FOLLOW_CTAS, the same one for the same script."""
    import hashlib

    pick = int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(FOLLOW_CTAS) if seed else 0
    return FOLLOW_CTAS[pick].format(name=spoken_handle(handle))


def series_ctas(handle: str, seed: str = "") -> tuple[str, str]:
    """Spoken after each part's closing question, as its last line."""
    return f"Sub to {spoken_handle(handle)} for Part 2 dropping tomorrow!", follow_cta(handle, seed)


SERIES_QUESTIONS = ("What would you do?", "Ever happened to you?")


def end_cards(handle: str) -> tuple[str, str]:
    return f"PART 2 TOMORROW · SUB {handle}", f"SUB {handle} · DROP YOUR STORY BELOW"


def _strip_cta(script: str) -> str:
    """Remove a trailing call to action Gemini may still add, so ours is not doubled."""
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())
    while len(sentences) > 1 and re.search(r"\b(subscribe|sub to|follow|part (2|two)|comment)", sentences[-1], re.I):
        sentences.pop()
    return " ".join(sentences)


def _part_title(title: str, n: int) -> str:
    title = re.sub(r"\s*[(\[]?\s*part\s*\d\s*[)\]]?\s*$", "", title.strip().strip('"'), flags=re.I)
    suffix = f" (Part {n})"
    return title[: 100 - len(suffix)].rstrip() + suffix


def _part_tags(tags: list[str], n: int) -> list[str]:
    tags = [t for t in _clean_hashtags(tags) if not re.fullmatch(r"#part\d", t, re.I)]
    keep = [t for t in tags if t.lower() not in ("#shorts", "#storytime")][:6]
    return [f"#part{n}", "#storytime", *keep, "#shorts"]


def _slug_name(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", re.sub(r"['’]", "", name))[:5]
    return "".join(w[:1].upper() + w[1:] for w in words) or "Story"


def write_series(
    settings: Settings,
    game: str,
    *,
    notes: str = "",
    target_seconds: int = 45,
    handle: str | None = None,
    max_seconds: float | None = None,
    progress: ProgressFn = lambda f, m: None,
    client=None,
) -> SeriesScript:
    """One Gemini call writes both parts, so Part 2 really continues Part 1.
    `max_seconds` caps each part (Part 2 continues the clip where Part 1 stopped)."""
    api_key = gemini_api_key(settings)
    if client is None and not api_key:
        raise ScriptError("GEMINI_API_KEY is not set in .env (create one at https://aistudio.google.com/apikey)")
    from google import genai
    from google.genai import errors, types

    handle = handle or settings.channel_handle
    model = gemini_model(settings)
    client = client or genai.Client(api_key=api_key)
    capped = bool(max_seconds and max_seconds < target_seconds)
    if capped:
        target_seconds = max(int(max_seconds), 15)
    limit = max_words(target_seconds) - CTA_WORDS + (0 if capped else 5)
    try:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=GeminiSeries,
            temperature=1.1,
        )
        prompt = build_prompt(game, target_seconds, [], notes, False, "story", "multi")
        response, model = _generate_with_fallback(client, model, [prompt], config, progress, errors.APIError)
        result = response.parsed
        if not isinstance(result, GeminiSeries):
            if not response.text:
                raise ScriptError("Gemini returned an empty answer (possibly blocked); try different notes")
            result = GeminiSeries.model_validate_json(response.text)
    except errors.APIError as err:
        raise _explain(err, model) from err
    return _assemble_series(result, game=game, model=model, handle=handle,
                            fit=lambda story: fit_length(client, model, story, limit, target_seconds, progress))


def _assemble_series(result: GeminiSeries, *, game: str, model: str, handle: str,
                     fit: Callable[[str], str] = lambda story: story) -> SeriesScript:
    """Both parts with their closing question + CTA, (Part n) titles, tags, cards and the pinned comment."""
    import uuid

    ctas = series_ctas(handle, result.part2.script)
    cards = end_cards(handle)
    notes_for = (f"Part 2 drops tomorrow! Subscribe {handle} so you don't miss it.",
                 f"This is Part 2. Missed Part 1? It's on {handle}.")
    parts = []
    raws = (result.part1, result.part2)
    stories, questions = [], []
    for n, p in enumerate(raws, start=1):
        story = _strip_cta(_clean_script(p.script))
        body, last = split_question(story)
        if last and _words(last) == _words(p.question):  # else that question is the cliffhanger: keep it
            story = body
        stories.append(lead_with_hook(story, p.hook))
        questions.append(clean_question(p.question) or SERIES_QUESTIONS[n - 1])
    series_mood = music.normalize_mood(result.music_mood) or music.guess_mood(" ".join(stories))
    for n, raw in enumerate(raws, start=1):
        story = fit(stories[n - 1])
        script = f"{story} {questions[n - 1]} {ctas[n - 1]}"
        part = _finish(raw, game=game, model=model, mode="story", format="multi", watched=False,
                       script=script, question=questions[n - 1], part=n, parts_total=2, end_card=cards[n - 1], music_mood=series_mood)
        card = part.title_card or "STORYTIME"
        part.title_card = f"PART {n}: {card}"[:40]
        part.title = _part_title(raw.title, n)
        part.description = f"{part.description}\n\n{notes_for[n - 1]}"
        part.hashtags = _part_tags(raw.hashtags, n)
        parts.append(part)

    comment = re.sub(r"https?://\S+|#\w+", "", result.pinned_comment).strip()
    comment = comment or "Part 2 drops tomorrow and it gets WORSE."
    if handle.lower() not in comment.lower():
        comment = f"{comment} Subscribe {handle} so you don't miss Part 2!"
    return SeriesScript(
        series_id=uuid.uuid4().hex[:10],
        story_name=_slug_name(result.story_name),
        model=model,
        game=game,
        handle=handle,
        pinned_comment=comment[:500],
        parts=parts,
        music_mood=series_mood,
    )


# --------------------------------------------------------------------------- your own story


class GeminiExtras(BaseModel):
    """Everything around a voiceover the creator wrote: Gemini must not touch the script itself."""

    on_screen: str = Field(description="One sentence summarising the story.")
    title: str = GeminiShort.model_fields["title"]
    description: str = GeminiShort.model_fields["description"]
    hashtags: list[str] = GeminiShort.model_fields["hashtags"]
    title_card: str = GeminiShort.model_fields["title_card"]
    popups: list[Popup] = GeminiShort.model_fields["popups"]
    reactions: list[Reaction] = GeminiShort.model_fields["reactions"]
    music_mood: str = GeminiShort.model_fields["music_mood"]
    question: str = GeminiShort.model_fields["question"]
    pinned_comment: str = GeminiShort.model_fields["pinned_comment"]


class GeminiSeriesExtras(BaseModel):
    story_name: str = GeminiSeries.model_fields["story_name"]
    part2_starts_with: str = Field("", description=(
        "Only when PART 2 is empty: the first 6 to 10 words of the sentence where Part 2 should begin, copied "
        "exactly from the story. Split right after the best cliffhanger, roughly in the middle."))
    part1: GeminiExtras
    part2: GeminiExtras
    pinned_comment: str = GeminiSeries.model_fields["pinned_comment"]
    music_mood: str = GeminiSeries.model_fields["music_mood"]


DESCRIBE_INSTRUCTION = """You package voiceovers for vertical Shorts (YouTube Shorts, TikTok, Reels). The creator
wrote the voiceover themselves: NEVER rewrite, shorten, translate or add to it. Write everything else for it.

Rules:
- title: a curiosity-building title (at most 70 characters, no hashtags) that makes people click.
- title_card: a 2 to 5 word ALL-CAPS banner for the first 3 seconds, punchy, different from the title.
- description: two or three short sentences, no hashtags, no spoilers of the ending.
- hashtags: 5 to 8 relevant ones, including #shorts (and #storytime for stories).
- Pop-ups: each `word` must appear exactly as written in the voiceover; concrete, easy to picture things,
  one every 3 to 5 seconds of speech (about one per 12 words), spread from start to end, in script order.
- Reactions: each `word` must appear exactly as written in the voiceover, at the funniest, most awkward,
  creepiest or most shocking beats; not in the first sentence.
- question: a punchy 2 to 5 word question that makes viewers comment their own story; it is spoken
  after the voiceover.
- pinned_comment: the comment the creator pins right after upload; it keeps the conversation going.
- music_mood: funny_quirky or chill_lofi.
- Never wrap words in backticks, quotes or markdown.
"""

_HEADING = re.compile(r"^\s*(part|chapter)\s+(one|two|three|[1-9])\b[^.!?\n]*$", re.IGNORECASE | re.MULTILINE)


def clean_own_script(text: str) -> str:
    """The creator's text as it will be spoken: 'Part One: The Call' headings and markdown removed."""
    return _clean_script(_HEADING.sub("", text or ""))


def split_story(story: str, starts_with: str) -> tuple[str, str]:
    """Split at the sentence that starts with `starts_with` (Gemini's pick), else at the middle sentence."""
    sentences = _sentences(story)
    if len(sentences) < 2:
        raise ScriptError("The story is too short to split into two parts")
    key = _words(starts_with)[:6]
    at = next((i for i, s in enumerate(sentences) if key and i > 0 and _words(s)[:len(key)] == key), None)
    if at is None:
        total, count, at = len(story.split()), 0, len(sentences) // 2
        for i, s in enumerate(sentences):
            count += len(s.split())
            if count >= total / 2:
                at = max(1, min(i + 1, len(sentences) - 1))
                break
    return " ".join(sentences[:at]), " ".join(sentences[at:])


def _gemini(settings: Settings, client=None):
    api_key = gemini_api_key(settings)
    if client is None and not api_key:
        raise ScriptError("GEMINI_API_KEY is not set in .env (create one at https://aistudio.google.com/apikey)")
    from google import genai

    return client or genai.Client(api_key=api_key), gemini_model(settings)


def _own_limit(target_seconds: float, max_seconds: float | None) -> tuple[int, float]:
    """Words your story may use (before the question + CTA), and the seconds that is."""
    seconds = min(target_seconds, max_seconds) if max_seconds else target_seconds
    seconds = max(seconds, 10)
    return max_words(seconds) - CTA_WORDS, seconds


def _ask_gemini(client, model: str, schema: type[BaseModel], prompt: str, progress: ProgressFn):
    from google.genai import errors, types

    config = types.GenerateContentConfig(system_instruction=DESCRIBE_INSTRUCTION, response_mime_type="application/json",
                                         response_schema=schema, temperature=0.8)
    try:
        response, model = _generate_with_fallback(client, model, [prompt], config, progress, errors.APIError)
    except errors.APIError as err:
        raise _explain(err, model) from err
    result = response.parsed
    if not isinstance(result, schema):
        if not response.text:
            raise ScriptError("Gemini returned an empty answer (possibly blocked); try again")
        result = schema.model_validate_json(response.text)
    return result, model


def _with_script(extras: GeminiExtras, script: str) -> GeminiShort:
    first = (_sentences(script) or [script])[0]
    return GeminiShort(hook=first, script=script, **extras.model_dump())


def describe_script(settings: Settings, game: str, script: str, *, notes: str = "",
                    mode: ScriptMode = "story", format: ScriptFormat = "short", target_seconds: float | None = None,
                    max_seconds: float | None = None, progress: ProgressFn = lambda f, m: None,
                    client=None) -> ShortScript:
    """Your own voiceover; Gemini writes the title, description, hashtags, title card, pop-ups, reaction
    beats, music mood, closing question and pinned comment. A story too long for the chosen length is
    tightened first (keeping your wording where possible); otherwise your words are kept as they are.
    The question and the follow CTA are added at the end (a short question you already end on is kept)."""
    story = _strip_cta(clean_own_script(script))
    if len(story.split()) < 3:
        raise ScriptError("Write or paste your story in the voiceover box first")
    client, model = _gemini(settings, client)
    limit, seconds = _own_limit(target_seconds or settings.short_target_seconds, max_seconds)
    story = fit_length(client, model, story, limit, seconds, progress, own=True)
    body, own = split_question(story)
    prompt = "\n".join([f"Background gameplay: {game}",
                        *( [f"Creator's notes: {notes.strip()}"] if notes.strip() else []),
                        "VOICEOVER (do not change it):", story])
    progress(None, "Gemini is reading your story")
    extras, model = _ask_gemini(client, model, GeminiExtras, prompt, progress)
    question = clean_question(own) or clean_question(extras.question) or DEFAULT_QUESTION
    body = body if clean_question(own) else story
    script = f"{body} {question} {follow_cta(settings.channel_handle, body)}"
    return _finish(_with_script(extras, script), game=game, model=model, mode=mode, format=format,
                   watched=False, script=script, question=question)


def describe_series(settings: Settings, game: str, part1: str, part2: str = "", *, notes: str = "",
                    handle: str | None = None, target_seconds: float = 45, max_seconds: float | None = None,
                    progress: ProgressFn = lambda f, m: None, client=None) -> SeriesScript:
    """Your own two-part story (or one story to split at the best cliffhanger); each part is tightened
    to the chosen length per part when it is too long."""
    handle = handle or settings.channel_handle
    one, two = _strip_cta(clean_own_script(part1)), _strip_cta(clean_own_script(part2))
    if len((one + " " + two).split()) < 6:
        raise ScriptError("Write or paste your story in the Part 1 box first")
    client, model = _gemini(settings, client)
    limit, seconds = _own_limit(target_seconds, max_seconds)
    if two:
        one = fit_length(client, model, one, limit, seconds, progress, own=True)
        two = fit_length(client, model, two, limit, seconds, progress, own=True)
    else:
        one = fit_length(client, model, one, 2 * limit, 2 * seconds, progress, own=True)
    lines = [f"Background gameplay: {game}",
             "A TWO-PART SERIES released a day apart: Part 1 ends on a cliffhanger, Part 2 resolves it.",
             "Do not put 'Part 1' or 'Part 2' in titles or title cards; they are added automatically.",
             "pinned_comment teases Part 2 without spoiling it."]
    if notes.strip():
        lines.append(f"Creator's notes: {notes.strip()}")
    if two:
        lines += ["PART 1 (do not change it):", one, "PART 2 (do not change it):", two]
    else:
        lines += ["PART 2 is empty: this is the whole story. Pick where Part 2 starts (part2_starts_with) and "
                  "describe each half.", "STORY (do not change it):", one]
    progress(None, "Gemini is reading your story")
    extras, model = _ask_gemini(client, model, GeminiSeriesExtras, "\n".join(lines), progress)
    if not two:
        one, two = split_story(one, extras.part2_starts_with)
    result = GeminiSeries(story_name=extras.story_name, part1=_with_script(extras.part1, one),
                          part2=_with_script(extras.part2, two), pinned_comment=extras.pinned_comment,
                          music_mood=extras.music_mood)
    return _assemble_series(result, game=game, model=model, handle=handle,
                            fit=lambda story: fit_length(client, model, story, limit, seconds, progress, own=True))
