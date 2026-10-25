"""DarkArchives script generator: turns the top NEW raw story into a 35-50 s Shorts script (strict JSON).

    python -m modules.script_gen            # the top-rated NEW story
    python -m modules.script_gen --id 12    # a specific story (any status but USED)

Structure (the DarkArchives guidelines): a suspense / paradox hook (0-3 s), tension escalating through
scenes 1-5, and a conclusion (40-50 s) that is an open question, an eerie twist or a provoking thought
for the comments. Documentary tone. The spoken script is hook_text followed by every scene's narration;
110-140 words in total and no sentence over MAX_SENTENCE_WORDS, both checked here (not trusted from
Gemini) and sent back to Gemini to fix when missed. Output: assets/generated_scripts/story_{id}.json,
and the story becomes SCRIPT_READY. A source Gemini judges unusable (not a real dark story, gore with
no story, fiction, recent politics or ongoing news…) is marked REJECTED with the reason in its note.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from db import repository

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "assets" / "generated_scripts"
MIN_WORDS, MAX_WORDS = 110, 140
MAX_SENTENCE_WORDS = 12  # short, rhythmic sentences for text-to-speech
WORDS_PER_SECOND = 2.8  # a calm, dark narrator: 125 words ~ 45 s
ATTEMPTS = 3
SOURCE_CHARS = 12000  # long Wikipedia articles are cut to this before going to Gemini


class SceneDraft(BaseModel):
    narration: str = Field(description="1-2 short spoken sentences, 8-12 words each.")
    visual_keywords: list[str] = Field(description="2-3 concrete stock-footage search terms for Pexels.")
    image_prompt: str = Field(description="A descriptive prompt for an AI image generator.")
    overlay_text: str = Field(description="2-5 words of on-screen text in CAPITALS.")


class ScriptDraft(BaseModel):
    usable: bool = Field(description="False when the source is not a real dark story that can be told.")
    reject_reason: str = Field("", description="Why it is unusable (empty when usable).")
    title: str = ""
    hook_text: str = ""
    scenes: list[SceneDraft] = Field(default_factory=list)


class Scene(BaseModel):
    scene_id: int
    narration: str
    visual_keywords: list[str]
    image_prompt: str
    overlay_text: str


class Script(BaseModel):
    story_id: int
    title: str
    hook_text: str
    estimated_duration_seconds: int
    total_word_count: int
    scenes: list[Scene]


class ScriptGenError(RuntimeError):
    pass


PROMPT = """You write scripts for Dark Archives, a YouTube Shorts channel (9:16) about obscure history,
eerie true events, unsolved mysteries and disturbing historical anomalies, narrated over moody visuals.

TONE: documentary-style, atmospheric, suspenseful, authoritative and eerie. A calm narrator who knows
the files, never a hype voice. No slang, no "guys", no exclamation marks.

Turn the SOURCE below into one Short of 35-50 seconds. Content pillar: {pillar}.

STRUCTURE (high retention):
- hook_text (0-3 s): ONE high-suspense statement or paradox, at most 12 words, that stops the scroll
  ("The ship was found sailing perfectly. Its crew was never seen again." is two sentences: too long;
  "A ship sailed perfectly for days with no one aboard." is right). Not a question like "Have you
  ever...", no spoiler of the ending. It is spoken first.
- SENTENCES: short and rhythmic for text-to-speech: 8-12 words each, never more than 12. One new
  concrete fact per sentence: dates, places, numbers, what was found.
- scenes: 6 to 8 scenes in story order, each narration 1-2 sentences.
  Scenes 1-5 escalate the tension: every scene more unsettling than the one before.
- The LAST scene (40-50 s) is the conclusion: an open-ended question, an eerie twist, or a provoking
  thought that makes viewers argue in the comments (e.g. "So what really happened on that mountain?").

LENGTH: hook_text + every narration together = {min_words} to {max_words} words. Aim for 125.

TRUTH: use only facts that are in the SOURCE. Never invent names, dates, numbers or quotes. If the
source is uncertain, say "reportedly". Never state as fact that a living person committed a crime
they were not convicted of: say "suspected", "reportedly" or "alleged", exactly as cautious as the
SOURCE. Keep real victims respectful: no gore, no mocking.

EACH SCENE ALSO HAS:
- visual_keywords: 2-3 generic, concrete stock-footage searches that exist on Pexels
  ("foggy forest at night", "old police car", "empty highway"). No names of people or cases.
- image_prompt: a vivid prompt for an AI image generator: subject, setting, era, lighting, mood,
  camera ("cinematic dark moody photo of ..., 35mm, film grain, photorealistic"). Never a real
  person's face or likeness; show places, objects, silhouettes.
- overlay_text: 2-5 words in CAPITALS that sum up the beat ("NO WRECKAGE FOUND", "MARCH 3, 1994").

title: a curiosity-gap YouTube title, at most 60 characters, no clickbait lies.

Set usable=false (with reject_reason, nothing else) if the SOURCE is any of these (today is {today}):
- not a real dark story you can tell: an announcement, a podcast or show promo, a request for help, a
  list of links, a general topic article, or a story with no events;
- explicit gore or graphic violence with no narrative depth (no mystery, history or meaning beyond it);
- purely fictional (a creepypasta, a made-up story), unless it is an internet urban legend and the
  source frames it as one;
- a recent political controversy or an ongoing, active news event (an open trial, a fresh arrest, a
  case from the last two years still developing).

SOURCE ({source_name}) — {title}:
{text}"""

FIX_PROMPT = """Your script breaks the rules: {problems}.
{direction} Keep the same facts, structure, scenes' visuals and the conclusion at the end. Return the
full script again in the same JSON format.

Your script:
{draft}"""


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'’]+(?:[-.][A-Za-z0-9'’]+)*", text))


def spoken(draft: ScriptDraft) -> str:
    return " ".join([draft.hook_text, *(s.narration for s in draft.scenes)])


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if words(s)]


def long_sentences(draft: ScriptDraft) -> list[str]:
    parts = [draft.hook_text, *(s.narration for s in draft.scenes)]
    return [s for part in parts for s in sentences(part) if words(s) > MAX_SENTENCE_WORDS]


def check(draft: ScriptDraft) -> list[str]:
    """Problems that make Gemini try again (empty = good)."""
    problems = []
    n = words(spoken(draft))
    if not MIN_WORDS <= n <= MAX_WORDS:
        problems.append(f"{n} words (hook_text + all narrations), it must be {MIN_WORDS}-{MAX_WORDS}")
    long = long_sentences(draft)
    if long:
        problems.append(f"{len(long)} sentence(s) over {MAX_SENTENCE_WORDS} words: "
                        + " | ".join(f'"{s}"' for s in long[:4]))
    if not draft.hook_text.strip() or not draft.title.strip():
        problems.append("missing hook or title")
    if len(draft.scenes) < 3:
        problems.append("fewer than 3 scenes")
    for i, s in enumerate(draft.scenes, 1):
        if not s.narration.strip() or not s.image_prompt.strip() or not s.overlay_text.strip():
            problems.append(f"scene {i} is incomplete")
        if len([k for k in s.visual_keywords if k.strip()]) < 2:
            problems.append(f"scene {i} needs 2-3 visual keywords")
    return problems


def finalize(story_id: int, draft: ScriptDraft) -> Script:
    n = words(spoken(draft))
    return Script(
        story_id=story_id,
        title=draft.title.strip()[:100],
        hook_text=draft.hook_text.strip(),
        estimated_duration_seconds=round(n / WORDS_PER_SECOND),
        total_word_count=n,
        scenes=[Scene(scene_id=i, narration=s.narration.strip(),
                      visual_keywords=[k.strip() for k in s.visual_keywords if k.strip()][:3],
                      image_prompt=s.image_prompt.strip(), overlay_text=s.overlay_text.strip().upper())
                for i, s in enumerate(draft.scenes, 1)],
    )


def _gemini(client=None):
    from google import genai

    from trendclip import script_writer
    from trendclip.config import get_settings

    settings = get_settings()
    key = script_writer.gemini_api_key(settings)
    if client is None and not key:
        raise ScriptGenError("GEMINI_API_KEY is missing in .env")
    return client or genai.Client(api_key=key), script_writer.gemini_model(settings)


def _ask(client, model: str, prompt: str) -> ScriptDraft:
    from google.genai import errors, types

    from trendclip import script_writer

    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=ScriptDraft,
                                         temperature=0.8)
    try:
        response, _ = script_writer._generate_with_fallback(client, model, [prompt], config, lambda f, m: None,
                                                            errors.APIError)
    except errors.APIError as err:
        raise ScriptGenError(str(script_writer._explain(err, model))) from err
    if isinstance(response.parsed, ScriptDraft):
        return response.parsed
    return ScriptDraft.model_validate_json(response.text)


def build_prompt(story: dict, today: dt.date | None = None) -> str:
    pillar = story.get("pillar")
    return PROMPT.format(
        min_words=MIN_WORDS, max_words=MAX_WORDS, source_name=story["source_name"], title=story["title"],
        text=story["raw_text"][:SOURCE_CHARS], today=(today or dt.date.today()).isoformat(),
        pillar=repository.PILLAR_LABELS.get(pillar, "dark history") if pillar else "dark history")


def write_script(story: dict, client=None) -> tuple[Script | None, str]:
    """(the Shorts script for one raw story, ""), or (None, why) when Gemini says the story is unusable."""
    client, model = _gemini(client)
    prompt = build_prompt(story)
    draft = _ask(client, model, prompt)
    for attempt in range(ATTEMPTS):
        if not draft.usable:
            reason = draft.reject_reason.strip() or "Gemini: not a usable story"
            logger.info("Story %s rejected: %s", story["id"], reason)
            return None, reason
        problems = check(draft)
        if not problems:
            return finalize(story["id"], draft), ""
        logger.info("Attempt %d: %s", attempt + 1, "; ".join(problems))
        if attempt == ATTEMPTS - 1:
            break
        n = words(spoken(draft))
        direction = " ".join(filter(None, [
            "Cut words: drop the least important detail." if n > MAX_WORDS else
            "Add words: one more concrete detail or scene from the source." if n < MIN_WORDS else "",
            f"Split or shorten every sentence over {MAX_SENTENCE_WORDS} words." if long_sentences(draft) else "",
        ])) or "Fix those problems."
        draft = _ask(client, model, prompt + "\n\n" + FIX_PROMPT.format(
            problems="; ".join(problems), direction=direction,
            draft=draft.model_dump_json(include={"title", "hook_text", "scenes"})))
    raise ScriptGenError(f"Gemini missed the rules {ATTEMPTS} times ({'; '.join(problems)})")


def script_path(story_id: int, out_dir: Path | None = None) -> Path:
    return (out_dir or OUTPUT_DIR) / f"story_{story_id}.json"


def process(story_id: int | None = None, client=None, out_dir: Path | None = None, db_path=None) -> dict:
    """Script the top NEW story (or `story_id`): {"story": ..., "script": Script | None, "path", "status"}.
    The story is PROCESSING while Gemini works and goes back to its old status if anything fails."""
    repository.init_db(db_path)
    story = repository.get_story(story_id, db_path) if story_id else repository.get_next_story(db_path)
    if not story:
        raise ScriptGenError(f"No story {story_id}" if story_id else "No NEW stories: run python -m modules.fetcher")
    if story["status"] == "USED":
        raise ScriptGenError(f"Story {story['id']} was already used in a video")
    before = story["status"]
    repository.set_status(story["id"], "PROCESSING", db_path)
    try:
        script, reason = write_script(story, client)
        if script is None:
            repository.set_status(story["id"], "REJECTED", db_path, note=reason[:300])
            return {"story": story, "script": None, "path": None, "status": "REJECTED", "reason": reason}
        path = script_path(story["id"], out_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(script.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        repository.set_status(story["id"], "SCRIPT_READY", db_path, note="")
        return {"story": story, "script": script, "path": path, "status": "SCRIPT_READY", "reason": ""}
    except BaseException:
        repository.set_status(story["id"], before, db_path)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--id", type=int, help="script this story instead of the top NEW one")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        result = process(args.id)
    except ScriptGenError as err:
        raise SystemExit(f"error: {err}") from None
    story = result["story"]
    print(f"story {story['id']}: {story['title'][:80]} ({story['source_name']}, {story.get('pillar') or 'no pillar'})")
    if result["script"] is None:
        print(f"REJECTED: {result['reason']}")
        return
    s = result["script"]
    print(f"{s.total_word_count} words · ~{s.estimated_duration_seconds}s · {len(s.scenes)} scenes -> {result['path']}")
    print(f"TITLE: {s.title}\nHOOK:  {s.hook_text}")
    for scene in s.scenes:
        print(f"  {scene.scene_id}. [{scene.overlay_text}] {scene.narration}  {scene.visual_keywords}")


if __name__ == "__main__":
    main()
