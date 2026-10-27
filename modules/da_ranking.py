"""Dark Outliers ranking videos: "5 cases that still make no sense", counted down from 5 to 1.

    python -m modules.da_ranking                 # Gemini picks and ranks COUNT NEW stories -> a script
    python -m modules.da_ranking --count 4

Gemini gets the best NEW stories of the bank and returns a theme, a hook and one entry per rank: a short
label for the list on screen, 18-26 words of narration ("Number five. ..."), a picture prompt. The
ranking is saved as a story of its own in the bank (source "ranking", so it has an id like any story:
the video, the plan and the uploads work unchanged) with its script in output/darkarchives/scripts; the
ranked stories become USED ("ranking <id>"). The video (modules.video_gen) shows the title all along and
a numbered list down the left that fills in from the last place to number one as the countdown goes.

Production (modules.da_produce) makes a ranking every DA_RANKING_EVERY-th video (default 4; 0 = never);
the dashboard's "Rank the best stories" button and /da rank make one on demand.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from db import repository
from modules import script_gen
from modules.scene_images import setting

logger = logging.getLogger(__name__)

COUNT = 5
POOL = 24  # NEW stories Gemini chooses from
SOURCE_CHARS = 700  # of each story
ENTRY_WORDS = (18, 26)  # about 8-11 s each with the channel's voice
HOOK_WORDS = 12
MAX_WORDS = 125  # hook + every entry: the production check wants the video under 60 s
ATTEMPTS = 3
LABEL_WORDS = 4

PROMPT = """You write scripts for Dark Outliers, a YouTube Shorts channel (9:16) about obscure history,
eerie true events, unsolved mysteries and disturbing historical anomalies, narrated over moody visuals.

TONE: documentary-style, atmospheric, suspenseful, authoritative and eerie. A calm narrator who knows
the files, never a hype voice. No slang, no "guys", no exclamation marks.

Make a RANKING Short: pick the {count} strongest stories from the CANDIDATES below, rank them (1 = the
one that will shock people most) and count them down from {count} to 1. Every entry gets about 10
seconds. Pick stories that belong to one theme when you can (disappearances, experiments, hoaxes,
cold cases, cursed places...) and give the ranking a title that names the theme and the number:
"5 Disappearances That Still Make No Sense", "5 Experiments Nobody Should Have Run". At most 45
characters, the number first.

- hook_text: ONE statement of at most {hook_words} words, spoken first, that says what is ranked and
  why it is worth staying for ("Five cases the files never closed."). No question, and it never says
  "Number five": the first entry's narration says that.
- entries: exactly {count}, one per rank, each with
  - story_id: the id of a CANDIDATE (every id different);
  - rank: 1 to {count};
  - label: 2-{label_words} words for the list on screen, the name of the case or place ("Dyatlov Pass",
    "The Dancing Plague"), no number;
  - narration: {min_words}-{max_words} words that start with "Number <rank>." (in words: "Number five.")
    then the one fact that makes the case unbelievable: concrete dates, places, numbers, what was found.
    Short sentences of at most 12 words each. Rank 1's narration ends with the twist or the open
    question that makes viewers argue in the comments;
  - image_prompt: a vivid prompt for an AI image generator: subject, setting, era, lighting, mood,
    camera ("cinematic dark moody photo of ..., 35mm, film grain, photorealistic"). Never a real
    person's face or likeness; show places, objects, silhouettes.
- thumbnail_text: 2-4 words in CAPITALS over the first frame, the thumbnail: the theme as a curiosity
  gap ("5 CASES. NO ANSWERS").

TRUTH: use only facts that are in the CANDIDATES. Never invent names, dates, numbers or quotes. Say
"reportedly" where the source is unsure. Never state as fact that a living person committed a crime
they were not convicted of. Keep real victims respectful: no gore, no mocking.

Set usable=false (with reject_reason) only if fewer than {count} candidates are real dark stories
(no promos, requests, link lists, fiction, or active news events from the last two years). Today is {today}.

CANDIDATES:
{candidates}"""

FIX_PROMPT = """Your ranking breaks the rules: {problems}. Fix them and return the full ranking again
in the same JSON format, keeping the same stories and order where they were right.

Your ranking:
{draft}"""


class EntryDraft(BaseModel):
    story_id: int
    rank: int
    label: str = Field(description="2-4 words for the list on screen")
    narration: str = Field(description="18-26 words starting with 'Number <rank>.'")
    image_prompt: str


class RankingDraft(BaseModel):
    usable: bool = True
    reject_reason: str = ""
    title: str = ""
    hook_text: str = ""
    thumbnail_text: str = ""
    entries: list[EntryDraft] = Field(default_factory=list)


class RankingError(RuntimeError):
    pass


def count() -> int:
    try:
        return max(3, min(int(setting("DA_RANK_COUNT", str(COUNT))), 8))
    except ValueError:
        return COUNT


def every() -> int:
    """Every n-th produced video is a ranking (DA_RANKING_EVERY, 0 = never)."""
    try:
        return max(int(setting("DA_RANKING_EVERY", "4")), 0)
    except ValueError:
        return 4


def candidates(db_path=None, pool: int = POOL) -> list[dict]:
    """The best NEW stories (never a ranking itself)."""
    rows = [s for s in repository.all_stories("NEW", db_path) if not s["source_id"].startswith("ranking_")]
    rows.sort(key=lambda s: (-s["score"], s["id"]))
    return rows[:pool]


def build_prompt(stories: list[dict], n: int, today: dt.date | None = None) -> str:
    lines = []
    for s in stories:
        text = re.sub(r"\s+", " ", s["raw_text"])[:SOURCE_CHARS]
        lines.append(f"[id {s['id']}] {s['title']} ({s['source_name']}, {s.get('pillar') or 'dark history'}): {text}")
    return PROMPT.format(count=n, hook_words=HOOK_WORDS, label_words=LABEL_WORDS, min_words=ENTRY_WORDS[0],
                         max_words=ENTRY_WORDS[1], today=(today or dt.date.today()).isoformat(), candidates="\n\n".join(lines))


NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}
TRAILING_NUMBER = re.compile(r"\s*\bnumber\s+(\w+|\d+)\s*[.!]?\s*$", re.I)


def clean_hook(hook: str) -> str:
    """The hook without a "Number five." at its end: the first entry says that, so the voice said it twice."""
    hook = hook.strip()
    cut = TRAILING_NUMBER.sub("", hook).strip()
    if not cut or cut == hook:
        return hook
    cut = cut.rstrip(",;:- ")
    return cut if cut.endswith((".", "!", "?")) else cut + "."


def check(draft: RankingDraft, ids: set[int], n: int) -> list[str]:
    """Problems that make Gemini try again (empty = good)."""
    problems = []
    if not draft.title.strip() or not draft.hook_text.strip():
        problems.append("missing title or hook_text")
    if len(draft.title) > 48:
        problems.append("the title is over 45 characters")
    if script_gen.words(draft.hook_text) > HOOK_WORDS + 2:
        problems.append(f"hook_text over {HOOK_WORDS} words")
    if len(draft.entries) != n:
        problems.append(f"{len(draft.entries)} entries instead of {n}")
    ranks = sorted(e.rank for e in draft.entries)
    if ranks != list(range(1, len(draft.entries) + 1)):
        problems.append(f"ranks must be 1..{n} once each (got {ranks})")
    used = [e.story_id for e in draft.entries]
    if len(set(used)) != len(used) or any(i not in ids for i in used):
        problems.append("every story_id must be a different CANDIDATE id")
    for e in draft.entries:
        w = script_gen.words(e.narration)
        if not ENTRY_WORDS[0] - 2 <= w <= ENTRY_WORDS[1] + 2:
            problems.append(f"rank {e.rank}: {w} words of narration (wanted {ENTRY_WORDS[0]}-{ENTRY_WORDS[1]})")
        if not re.match(r"\s*number\s+\w+\s*[.,:]", e.narration, re.I):
            problems.append(f"rank {e.rank}: the narration must start with \"Number {NUMBER_WORDS.get(e.rank, e.rank)}.\"")
        if not 1 <= script_gen.words(e.label) <= LABEL_WORDS + 1:
            problems.append(f"rank {e.rank}: the label must be 2-{LABEL_WORDS} words")
        if not e.image_prompt.strip():
            problems.append(f"rank {e.rank}: no image_prompt")
    total = script_gen.words(draft.hook_text) + sum(script_gen.words(e.narration) for e in draft.entries)
    if total > MAX_WORDS:
        problems.append(f"{total} words in all; at most {MAX_WORDS} (cut the least important facts)")
    return problems


def _ask(client, model: str, prompt: str) -> RankingDraft:
    from google.genai import errors, types

    from trendclip import script_writer

    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=RankingDraft,
                                         temperature=0.8)
    try:
        response, _ = script_writer._generate_with_fallback(client, model, [prompt], config, lambda f, m: None,
                                                            errors.APIError)
    except errors.APIError as err:
        raise RankingError(str(script_writer._explain(err, model))) from err
    if isinstance(response.parsed, RankingDraft):
        return response.parsed
    return RankingDraft.model_validate_json(response.text)


def write_ranking(stories: list[dict], client=None, n: int | None = None, today: dt.date | None = None) -> RankingDraft:
    """Gemini's ranking of `n` of the stories, checked; raises RankingError when it will not come right."""
    n = n or count()
    if len(stories) < n:
        raise RankingError(f"Only {len(stories)} NEW stories in the bank; a ranking needs {n} (fetch more)")
    client, model = script_gen._gemini(client)
    prompt = build_prompt(stories, n, today)
    ids = {s["id"] for s in stories}
    draft = _ask(client, model, prompt)
    problems: list[str] = []
    for attempt in range(ATTEMPTS):
        if not draft.usable:
            raise RankingError(draft.reject_reason.strip() or "Gemini: not enough usable stories")
        problems = check(draft, ids, n)
        if not problems:
            return draft
        logger.info("Ranking attempt %d: %s", attempt + 1, "; ".join(problems))
        if attempt == ATTEMPTS - 1:
            break
        draft = _ask(client, model, prompt + "\n\n" + FIX_PROMPT.format(
            problems="; ".join(problems), draft=draft.model_dump_json(exclude={"usable", "reject_reason"})))
    raise RankingError(f"Gemini missed the rules {ATTEMPTS} times ({'; '.join(problems)})")


def to_script(story_id: int, draft: RankingDraft, stories: dict[int, dict]) -> script_gen.Script:
    """The ranking as a Script: the scenes count down (rank n first), the ranking lists the labels 1..n."""
    ordered = sorted(draft.entries, key=lambda e: -e.rank)
    scenes = [script_gen.Scene(scene_id=i, narration=e.narration.strip(), visual_keywords=["dark archive", "old documents"],
                               image_prompt=e.image_prompt.strip(), overlay_text=e.label.strip().upper())
              for i, e in enumerate(ordered, 1)]
    total = script_gen.words(clean_hook(draft.hook_text)) + sum(script_gen.words(s.narration) for s in scenes)
    return script_gen.Script(
        story_id=story_id, title=draft.title.strip(), hook_text=clean_hook(draft.hook_text),
        estimated_duration_seconds=round(total / script_gen.WORDS_PER_SECOND), total_word_count=total, scenes=scenes,
        thumbnail_text=draft.thumbnail_text.strip().upper(),
        ranking=[script_gen.RankEntry(rank=e.rank, label=e.label.strip(), story_id=e.story_id,
                                      title=stories[e.story_id]["title"]) for e in sorted(draft.entries, key=lambda e: e.rank)])


def process(client=None, db_path=None, out_dir: Path | None = None, n: int | None = None) -> dict:
    """A ranking script from the bank: {"story", "script", "path", "status": "SCRIPT_READY", "reason": ""}
    like script_gen.process. The ranking is a new bank story (source "ranking"), its stories become USED."""
    repository.init_db(db_path)
    n = n or count()
    pool = candidates(db_path)
    draft = write_ranking(pool, client, n)
    by_id = {s["id"]: s for s in pool}
    pillar = Counter(by_id[e.story_id].get("pillar") or "mystery" for e in draft.entries).most_common(1)[0][0]
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    raw = "\n".join(f"{e.rank}. {e.label} (story {e.story_id}: {by_id[e.story_id]['title']})"
                    for e in sorted(draft.entries, key=lambda e: e.rank))
    source_id = f"ranking_{stamp}"
    if not repository.save_story(source_id, "ranking", draft.title.strip(), raw, "", 0, pillar=pillar, db_path=db_path):
        raise RankingError("A ranking with this timestamp already exists; try again")
    story = next(s for s in repository.all_stories(None, db_path) if s["source_id"] == source_id)
    script = to_script(story["id"], draft, by_id)
    path = script_gen.script_path(story["id"], out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(script.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    repository.set_status(story["id"], "SCRIPT_READY", db_path, note="")
    for e in draft.entries:
        repository.set_status(e.story_id, "USED", db_path, note=f"ranking {story['id']} (#{e.rank})")
    story = repository.get_story(story["id"], db_path)
    return {"story": story, "script": script, "path": path, "status": "SCRIPT_READY", "reason": ""}


def due(out_dir: Path | None = None) -> bool:
    """True when the last `every()` videos hold no ranking (0 = never)."""
    from modules import video_gen

    n = every()
    if n == 0:
        return False
    folder = out_dir or video_gen.OUTPUT_DIR
    infos = []
    for path in folder.glob("story_*.json"):
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        infos.append((info.get("created_at", ""), bool(info.get("ranking"))))
    if len(infos) < n - 1:
        return False  # a young channel tells single stories first
    infos.sort()
    return not any(is_ranking for _, is_ranking in infos[-(n - 1):])


# --------------------------------------------------------------------------- the list on screen

PANEL_X = 56
PANEL_TOP = 500  # under the title card (the title stays all along)
PANEL_STEP = 96
PANEL_SIZE = 60


def panel(script: script_gen.Script, beats: list, end: float, font_size: int = PANEL_SIZE, highlight: str = "orange",
          size: tuple[int, int] = (1080, 1920)) -> tuple[list[str], list[str]]:
    """(.ass styles, events) for the list down the left: every rank's number from frame 0, its label
    from the moment it is spoken (its beat), the entry being spoken big and coloured, the earlier ones
    white. `beats`: video_gen's, in spoken order (the hook, then rank n .. 1)."""
    from trendclip.video_assembler import HIGHLIGHT_COLORS, _ass_time, _resolve_color

    if not script.ranking:
        return [], []
    try:
        hl = _resolve_color(highlight)
    except Exception:  # noqa: BLE001
        hl = HIGHLIGHT_COLORS["orange"]
    dim = "&H00888888"
    styles = [f"Style: Panel,{{font}},{font_size},&H00FFFFFF,{hl},&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,5,2,7,0,0,0,1"]
    n = len(script.ranking)
    reveal: dict[int, float] = {}
    for b in beats:
        if b.label != "hook" and 1 <= (rank := n - (b.index - 1)) <= n:  # scene 1 is rank n
            reveal[rank] = b.start
    events = []
    ranks = sorted(reveal)
    for rank in range(1, n + 1):
        y = PANEL_TOP + (rank - 1) * PANEL_STEP
        pos = f"\\pos({PANEL_X},{y})"
        if rank not in reveal:  # a beat the voice never reached: its number stays dim
            events.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(end)},Panel,,0,0,0,,{{{pos}\\c{dim}}}{rank}.")
            continue
        label = next((e.label for e in script.ranking if e.rank == rank), "")
        at = reveal[rank]
        later = min((reveal[r] for r in ranks if reveal[r] > at), default=end)
        if at > 0:
            events.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(at)},Panel,,0,0,0,,{{{pos}\\c{dim}}}{rank}.")
        text = f"{{\\c{hl}}}{rank}.{{\\c&H00FFFFFF&}} {label.upper()}"
        events.append(f"Dialogue: 2,{_ass_time(at)},{_ass_time(later)},Panel,,0,0,0,,"
                      f"{{{pos}\\fscx118\\fscy118\\fad(120,0)}}{{\\c{hl}}}{rank}. {label.upper()}")
        if later < end:
            events.append(f"Dialogue: 2,{_ass_time(later)},{_ass_time(end)},Panel,,0,0,0,,{{{pos}}}{text}")
    return styles, events


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, help=f"stories in the ranking (DA_RANK_COUNT, default {COUNT})")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        result = process(n=args.count)
    except (RankingError, script_gen.ScriptGenError) as err:
        raise SystemExit(f"error: {err}") from None
    script = result["script"]
    print(f"story {script.story_id}: {script.title} ({script.total_word_count} words, ~{script.estimated_duration_seconds}s)")
    for e in script.ranking:
        print(f"  {e.rank}. {e.label} — {e.title}")
    print(f"next: python -m modules.video_gen --id {script.story_id}")


if __name__ == "__main__":
    main()
