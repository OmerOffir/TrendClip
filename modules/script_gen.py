"""Dark Outliers script generator: turns the top NEW raw story into a 20-28 s Shorts script (strict JSON).

    python -m modules.script_gen            # the top-rated NEW story
    python -m modules.script_gen --id 12    # a specific story (any status but USED)

Structure (the Dark Outliers guidelines): a suspense / paradox hook (0-3 s), tension escalating through
scenes 1-3, and a conclusion (at 20-28 s) that is an open question, an eerie twist or a provoking thought
for the comments. Documentary tone. The spoken script is hook_text followed by every scene's narration;
48-60 words in total and no sentence over MAX_SENTENCE_WORDS, both checked here (not trusted from
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
MIN_WORDS, MAX_WORDS = 48, 60  # the owner wants the channel under 30 s (2026-10-27): about 22-27 s with the voice
MAX_SENTENCE_WORDS = 12  # short, rhythmic sentences for text-to-speech
HOOK_WORDS = 12  # the hook is spoken in under 2-3 s: the shock first, no background
MIN_SCENES, MAX_SCENES = 4, 6  # one sentence each, so the picture changes every few seconds
HEADLINE_WORDS = (4, 9)  # thumbnail_text, the headline box on frame 0: the whole hook, not a fragment
# A title of mood words instead of the story ("Unsolved Horror", "The Dark Secret Beneath ...").
VAGUE_TITLE = re.compile(r"\b(dark secrets?|secrets?|mystery of|the truth about|horror|chilling|terrifying|creepy|disturbing|"
                         r"unsolved|unexplained|eerie|sinister)\b", re.I)
# A picture YouTube and viewers flinch from: the image prompts must show what was left behind, not the dead.
GORE = re.compile(r"\b(corpses?|dead bod(y|ies)|bod(y|ies)|torsos?|wounds?|wounded|injur(y|ies|ed)|blood(y|ied)?|bones?|"
                  r"skeletons?|skeletal|skulls?|fractures?|gore|gory|organs?|severed|mutilated|x-ray)\b", re.I)
# A hook that opens with the background instead of the shock: a year, a date, "Years ago", "On November 5".
BACKGROUND_OPENING = re.compile(r"^\W*(in|on|back in|during|by)?\s*(\d{3,4}\b|(the\s+)?(year|years|decades|centuries)\b|"
                                r"\w+\s+(hundred|years|decades|centuries)\b.*\bago\b|"
                                r"(january|february|march|april|may|june|july|august|september|october|november|december)\b)", re.I)
WORDS_PER_SECOND = 2.3  # the channel's narrator (F5-TTS at +10%): 105 words ~ 46 s
ATTEMPTS = 3
SOURCE_CHARS = 12000  # long Wikipedia articles are cut to this before going to Gemini


class SceneDraft(BaseModel):
    narration: str = Field(description="One short spoken sentence, 8-12 words.")
    visual_keywords: list[str] = Field(description="2-3 concrete stock-footage search terms for Pexels.")
    image_prompt: str = Field(description="A descriptive prompt for an AI image generator.")
    overlay_text: str = Field(description="2-5 words of on-screen text in CAPITALS.")


class ScriptDraft(BaseModel):
    usable: bool = Field(description="False when the source is not a real dark story that can be told.")
    reject_reason: str = Field("", description="Why it is unusable (empty when usable).")
    title: str = ""
    hook_text: str = ""
    thumbnail_text: str = Field("", description="The headline at the top of the first frame: the whole hook in 4-8 words.")
    hook_image_prompt: str = Field("", description="The picture under the hook: the subject itself, close up.")
    scenes: list[SceneDraft] = Field(default_factory=list)


class Scene(BaseModel):
    scene_id: int
    narration: str
    visual_keywords: list[str]
    image_prompt: str
    overlay_text: str


class RankEntry(BaseModel):
    """One place of a ranking video (modules.da_ranking): the list on screen shows `label` at `rank`."""
    rank: int
    label: str
    story_id: int  # the bank story it came from
    title: str = ""


class Script(BaseModel):
    story_id: int
    title: str
    hook_text: str
    estimated_duration_seconds: int
    total_word_count: int
    scenes: list[Scene]
    thumbnail_text: str = ""  # the headline box on frame 0 (since 2026-10-29 the whole hook in 4-8 words; before, 2-4)
    hook_image_prompt: str = ""  # the picture on frame 0 (scripts from before it existed: scene 1's, close up)
    ranking: list[RankEntry] = []  # a ranking video: the scenes count down from the last place to rank 1


class ScriptGenError(RuntimeError):
    pass


PROMPT = """You write scripts for Dark Outliers, a YouTube Shorts channel (9:16) about obscure history,
eerie true events, unsolved mysteries and disturbing historical anomalies, narrated over moody visuals.

TONE: documentary-style, atmospheric, suspenseful, authoritative and eerie. A calm narrator who knows
the files, never a hype voice. No slang, no "guys", no exclamation marks.

Turn the SOURCE below into one Short of 20-28 seconds. Content pillar: {pillar}.
If the SOURCE starts with "Anniversary:", the video goes out around that date: say how many years ago this
week it happened in scene 1 ("It happened eighty years ago this week."), never in the hook.

STRUCTURE (high retention):
- hook_text (0-2 s): the single most SHOCKING fact of the story, at most {hook_words} words, spoken first.
  Start with the shock, not the background: no date, year, place or "In 1872..." / "Years ago..." in it
  ("A ship sailed for days with no one aboard." / "He poisoned his own son for the insurance money.").
  Not a question like "Have you ever...", no spoiler of the ending.
- The background (date, place, who) comes AFTER the hook, in scene 1.
- SENTENCES: short and rhythmic for text-to-speech: 8-12 words each, never more than 12. One new
  concrete fact per sentence: dates, places, numbers, what was found. Cut every sentence that doesn't
  move the story forward.
- scenes: 4 to 6 scenes in story order, each narration ONE sentence (each scene is a new picture, so the
  picture changes every 2-3 seconds). Every scene more unsettling than the one before.
- The LAST scene ends on a twist or an open question that makes people rewatch or argue in the comments
  ("...and he was never caught." / "So who really sent that letter?").

LENGTH: hook_text + every narration together = {min_words} to {max_words} words. Aim for 55: the
whole Short is under 30 seconds, so only the facts that make the story unbelievable, and the twist.

TRUTH: use only facts that are in the SOURCE. Never invent names, dates, numbers or quotes. If the
source is uncertain, say "reportedly". Never state as fact that a living person committed a crime
they were not convicted of: say "suspected", "reportedly" or "alleged", exactly as cautious as the
SOURCE. Keep real victims respectful: no gore, no mocking.

EACH SCENE ALSO HAS:
- visual_keywords: 2-3 generic, concrete stock-footage searches that exist on Pexels
  ("foggy forest at night", "old police car", "empty highway"). No names of people or cases.
- image_prompt: a vivid prompt for an AI image generator: subject, setting, era, lighting, mood,
  camera ("cinematic dark moody photo of ..., 35mm, film grain, photorealistic"). Dark mood, but ONE
  bright focal point the eye goes to: the subject lit by a warm light, a lit face in silhouette, a glowing
  object, a red detail ("..., the subject lit by a single warm lamp, the rest in shadow"). Never a dark
  blur. Never a real person's face or likeness; show places, objects, silhouettes. Never gore: no bodies,
  corpses, wounds, injuries, bones, blood or skulls; show what was left behind instead (the slashed tent,
  the empty boots, the letter, the locked door).
- overlay_text: 2-5 words in CAPITALS that sum up the beat ("NO WRECKAGE FOUND", "MARCH 3, 1994").

title: a curiosity title that makes people NEED the answer, at most 60 characters: who + the shocking
act or the impossible detail ("The Father Who Poisoned Halloween Candy", "The Ship That Sailed With No
One Aboard"). Never vague words like "Dark Secret", "Mystery Of", "The Truth About". No clickbait lies.

thumbnail_text: the HEADLINE in a box at the top of the first frame, the first thing people read: the
whole hook as one complete statement in 4-8 words, understandable on its own ("HE POISONED HIS OWN SON
FOR MONEY", "FIVE BOMBERS VANISHED IN CLEAR SKIES"). Never a caption fragment ("FIVE GIANT TUBES",
"36 BARRELS UNDERGROUND"), never the title again.

hook_image_prompt: the picture under the hook and the thumbnail, the first thing a scrolling viewer
sees. It shows the exact thing the hook names, close up and filling the frame: the ghost ship with
empty decks, the slashed tent in the snow, five bombers over a dark sea, a courtroom full of
accused. Never a scene-setter (a desk, a lamp, an old book, files, an archive, an empty room), never
gore or a body, never a real person's face; same style as the scene prompts.

Set usable=false (with reject_reason, nothing else) if the SOURCE is any of these (today is {today}):
- not a real dark story you can tell: an announcement, a podcast or show promo, a request for help, a
  list of links, a general topic article, or a story with no events;
- explicit gore or graphic violence with no narrative depth (no mystery, history or meaning beyond it);
- purely fictional (a creepypasta, a made-up story), unless it is an internet urban legend and the
  source frames it as one;
- a recent political controversy or an ongoing, active news event (an open trial, a fresh arrest, a
  case from the last two years still developing);
- a story centred on child abuse (YouTube pushes these less).

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
    hook = words(draft.hook_text)
    if hook > HOOK_WORDS:
        problems.append(f"the hook has {hook} words, at most {HOOK_WORDS}: only the shock")
    if BACKGROUND_OPENING.match(draft.hook_text.strip()):
        problems.append("the hook opens with the background (a date, a year, 'years ago'): start with the shocking fact, "
                        "the date goes in scene 1")
    gory = [f"scene {i}" for i, sc in enumerate(draft.scenes, 1) if GORE.search(sc.image_prompt)]
    if GORE.search(draft.hook_image_prompt):
        gory.insert(0, "hook_image_prompt")
    if gory:
        problems.append(f"gore in the picture of {', '.join(gory)} (a body, wound, bone, blood or skull): show what was "
                        f"left behind instead (the slashed tent, the empty boots, the letter)")
    vague = VAGUE_TITLE.search(draft.title)
    if vague:
        problems.append(f'the title says "{vague.group(0)}": name who did what, or the impossible detail, so people need '
                        f'the answer ("The Father Who Poisoned Halloween Candy")')
    headline = words(draft.thumbnail_text)
    if not HEADLINE_WORDS[0] <= headline <= HEADLINE_WORDS[1]:
        problems.append(f"thumbnail_text has {headline} words: it is the headline, the whole hook in "
                        f"{HEADLINE_WORDS[0]}-{HEADLINE_WORDS[1] - 1} words")
    if not MIN_SCENES <= len(draft.scenes) <= MAX_SCENES:
        problems.append(f"{len(draft.scenes)} scenes, it must be {MIN_SCENES}-{MAX_SCENES} (one sentence each)")
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
        thumbnail_text=" ".join(draft.thumbnail_text.split()).upper()[:70],
        hook_image_prompt=draft.hook_image_prompt.strip(),
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


def build_prompt(story: dict, today: dt.date | None = None, brief: str | None = None) -> str:
    """`brief`: what worked on the channel lately, appended after the source (modules.da_insights.learned():
    the lessons once the channel has enough measured videos, "" before; None = ask it)."""
    pillar = story.get("pillar")
    text = PROMPT.format(
        min_words=MIN_WORDS, max_words=MAX_WORDS, source_name=story["source_name"], title=story["title"],
        text=story["raw_text"][:SOURCE_CHARS], today=(today or dt.date.today()).isoformat(), hook_words=HOOK_WORDS,
        pillar=repository.PILLAR_LABELS.get(pillar, "dark history") if pillar else "dark history")
    if brief is None:
        from modules import da_insights

        brief = da_insights.learned()
    return text + brief if brief else text


def draft_json(draft: ScriptDraft) -> str:
    return draft.model_dump_json(include={"title", "hook_text", "thumbnail_text", "hook_image_prompt", "scenes"})


def fix_prompt(draft: ScriptDraft, problems: list[str]) -> str:
    """What goes after the writing prompt when a draft missed the rules: the problems, how to fix them, the draft."""
    n = words(spoken(draft))
    direction = " ".join(filter(None, [
        "Cut words: drop the least important detail." if n > MAX_WORDS else
        "Add words: one more concrete detail or scene from the source." if n < MIN_WORDS else "",
        f"Split or shorten every sentence over {MAX_SENTENCE_WORDS} words." if long_sentences(draft) else "",
    ])) or "Fix those problems."
    return FIX_PROMPT.format(problems="; ".join(problems), direction=direction, draft=draft_json(draft))


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
        draft = _ask(client, model, prompt + "\n\n" + fix_prompt(draft, problems))
    raise ScriptGenError(f"Gemini missed the rules {ATTEMPTS} times ({'; '.join(problems)})")


def script_path(story_id: int, out_dir: Path | None = None) -> Path:
    return (out_dir or OUTPUT_DIR) / f"story_{story_id}.json"


def save(script: Script, out_dir: Path | None = None, db_path=None, note: str = "") -> Path:
    """Write story_<id>.json; the story becomes SCRIPT_READY (the video can be made)."""
    path = script_path(script.story_id, out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(script.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    repository.set_status(script.story_id, "SCRIPT_READY", db_path, note=note)
    return path


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
        path = save(script, out_dir, db_path)
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
    print(f"TITLE: {s.title}\nHOOK:  {s.hook_text}\nCOVER: {s.thumbnail_text or '(from the hook)'}")
    for scene in s.scenes:
        print(f"  {scene.scene_id}. [{scene.overlay_text}] {scene.narration}  {scene.visual_keywords}")


if __name__ == "__main__":
    main()
