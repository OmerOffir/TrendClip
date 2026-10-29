"""Borderline Why long videos: 8-15 minutes, landscape (1920x1080), in chapters, written with any LLM by hand like
"Write it myself" (modules.bw_manual), since the free Gemini quota can't write one. The dashboard's Long video tab
carries the prompts; the code reads the sources and holds every chapter to the Short's rules.

  1. start      the code reads the Wikipedia article (and any more articles named) and the pages they cite
  2. research   optional: a research prompt for the LLM (Gemini's Deep Research is ideal); every link in its answer
                is read by the code, and only pages it could read become sources
  3. outline    the outline prompt; its answer (the chapters, each with its question and beats) is checked
  4. chapters   one prompt per chapter, with the outline and the story so far; each chapter gets the rules check
                (quotes word for word in a source), then a fact-check prompt (a second LLM is best)
  5. make       in a process of its own (it switches the renderer to landscape and runs for about an hour):
                voice, maps, the chapter banners, the thumbnail, chapter times in the description
  6. upload     a regular YouTube video (not a Short), with its thumbnail

Each long video is a folder output/borderlinewhy/long/<id>/: research.json, state.json, then script.json,
voice.mp3, video.mp4, thumbnail.jpg, info.json, progress.json and make.log.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import sys
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError

from modules import bw_channel, bw_fx, bw_render, bw_script, bw_topics
from modules.bw_script import ClaimDraft, Draft, Limits, Research, SceneDraft, ScriptError, Source

logger = logging.getLogger(__name__)

CHAPTERS = (5, 8)
CHAPTER = Limits(min_words=150, max_words=400, min_scenes=5, max_scenes=24, max_cards=3, first_map=False, min_claims=3, min_cited=1)
TOTAL_WORDS = (1100, 2400)  # about 8-16 minutes with the narrator
WORDS_PER_SECOND = 2.6  # the preview's guess of the voice's pace
MORE_ARTICLES = 4  # more Wikipedia articles read at the start
PACK_PAGES = 14  # pages read from the research answer's links
PAGE_CHARS = 9000
SESSIONS_SHOWN = 10
BANNER = 3.2  # seconds a chapter's banner stays up

JSON_TAIL = """
Answer with ONLY one JSON object (no text before or after it, no code fence)."""


class LongError(ValueError):
    pass


# --------------------------------------------------------------------------- what the LLM answers


class ChapterPlan(BaseModel):
    title: str = Field(description="2-5 words, shown as the chapter's banner and in YouTube's chapter list.")
    question: str = Field(description="The small question this chapter answers.")
    beats: list[str] = Field(default_factory=list, description="3-6 points the chapter covers, each with its source ids.")


class Outline(BaseModel):
    usable: bool
    reject_reason: str = ""
    title: str = ""
    cover_text: str = ""
    chapters: list[ChapterPlan] = Field(default_factory=list)
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)


class Chapter(BaseModel):
    scenes: list[SceneDraft] = Field(default_factory=list)
    claims: list[ClaimDraft] = Field(default_factory=list)


# --------------------------------------------------------------------------- the prompts


RESEARCH_PROMPT = """Research this question for a 10-minute YouTube documentary with animated maps: "{question}"
(Wikipedia's article: {article}).

Find the whole story: how the situation began, the people and decisions behind it, the turning points with their
dates, what it is like today, and the surprising details most people don't know. Prefer official, government,
academic, museum, archive and news pages; never YouTube, Reddit, Quora, forums or AI-written pages.

Answer with 25-50 facts, one per line, each followed by the full link (https://...) of a page that states it:
fact - link
Only facts a page really states; no link, no fact."""

OUTLINE_PROMPT = """You plan a long YouTube video ({minutes} minutes) for Borderline Why, a channel that answers surprising
geography and history questions with animated maps. The question: "{question}"

Use ONLY the sources below. If they cannot carry a story of {minutes} minutes with a clear, true answer, set usable to
false and say why.

THE STORY
- {min_ch}-{max_ch} chapters. Chapter 1 is the hook (30-60 seconds): the most surprising fact and the big question,
  and why it is stranger than it looks. Each middle chapter is one step of the story in time order; it opens with its
  own small question and ends on what makes the viewer want the next chapter. The last chapter gives the full
  answer and what the place is like today.
- Never pad: every chapter must have real facts from the sources. Fewer, fuller chapters beat thin ones.
- title: the YouTube title, the question in under 70 characters. cover_text: 2-5 words for the thumbnail (not the title).
- Each chapter: title (2-5 words), question, and beats (3-6 points it covers, each ending with its source ids, e.g. [1][3]).
- description: 2-3 sentences for YouTube. hashtags: 3-5 without #.

ANSWER FORMAT
{{"usable": true, "reject_reason": "", "title": "Why ... ?", "cover_text": "...",
  "chapters": [{{"title": "...", "question": "...", "beats": ["... [1]", "... [2][4]"]}}],
  "description": "...", "hashtags": ["geography", "borders", "history"]}}""" + JSON_TAIL + """

Today is {today}.

SOURCES
{sources}
"""

CHAPTER_PROMPT = """You write chapter {n} of {total} of a long Borderline Why video (animated maps, a calm narrator):
"{title}". The whole video answers: "{question}"

Use ONLY the sources below. Do not add facts, numbers, names, dates or quotes that are not in them.

THE OUTLINE (so your chapter fits; write only chapter {n})
{outline}

{so_far}YOUR CHAPTER: {n}. {chapter_title}
Its question: {chapter_question}
Cover: {beats}

THE NARRATION ({min_words}-{max_words} words)
- {opening}
- Simple, natural, spoken English. Short connected sentences (at most {max_sentence} words). Every sentence adds
  something to the story; no filler, no exaggeration, no "subscribe", no "Did you know", no "Let's dive in".
- Don't repeat what earlier chapters said. Keep uncertainty where the sources are uncertain. Numbers as digits.

""" + bw_script.SCENE_RULES + """THE CLAIMS
List every fact the narration states. For each, the id of the source that supports it and an EXACT quote copied
from that source (no changes, no "...").

ANSWER FORMAT
{{"scenes": [{{"kind": "map", "narration": "...", "focus": ["..."], "headline": "", "elements": [
    {{"kind": "highlight", "place": "...", "color": "a", "cue": "..."}}]}},
  {{"kind": "card", "narration": "...", "kicker": "...", "title": "...", "subtitle": "...", "elements": []}}],
 "claims": [{{"claim": "...", "source_id": 1, "quote": "..."}}]}}
Element fields: kind, place, to, value, text, color, cue, flag, icon, points (leave out what an element doesn't use).""" + JSON_TAIL + """

Today is {today}.

SOURCES
{sources}
"""

FIX_PROMPT = """Your chapter has problems: {problems}

Fix every one and return the whole chapter again (the same JSON). Your previous chapter:
{draft}
"""

CHECK_FIX_PROMPT = """A fact checker compared your chapter's narration with the sources and flagged these statements:
{flagged}

For each one: delete it, or rewrite it so it says only what a source says. An OFF TOPIC sentence goes. Keep every
other sentence as it is; update the claims so each quote still backs the narration; keep it {min_words}-{max_words}
words. Return the whole chapter again (the same JSON). Your previous chapter:
{draft}
"""

FACTS_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence):
{"unsupported": ["the statement - what is wrong", "OFF TOPIC: the sentence - why"]}
and {"unsupported": []} when everything is supported and on topic."""


# --------------------------------------------------------------------------- the folder


def root(out_dir: Path | None = None) -> Path:
    return bw_channel.out_root(out_dir) / "long"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "topic"


def folder(sid: str, out_dir: Path | None = None) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", sid or ""):
        raise LongError(f"No long video {sid!r}")
    f = root(out_dir) / sid
    if not (f / "research.json").is_file():
        raise LongError(f"No long video {sid!r}")
    return f


def _save(f: Path, name: str, data: dict[str, Any]) -> None:
    (f / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load(f: Path, name: str) -> dict[str, Any]:
    return json.loads((f / name).read_text(encoding="utf-8"))


def _research(f: Path) -> Research:
    data = _load(f, "research.json")
    return Research(bw_topics.Topic(**data["topic"]), data["article"], [Source(**s) for s in data["sources"]])


def _save_research(f: Path, res: Research) -> None:
    _save(f, "research.json", {"topic": res.topic.model_dump(), "article": res.article, "sources": [asdict(s) for s in res.sources]})


def _parse(text: str, schema):
    from modules import bw_manual

    try:
        return bw_manual._parse(text, schema)
    except bw_manual.ManualError as err:
        raise LongError(str(err)) from None


# --------------------------------------------------------------------------- 1-2: research


def start(question: str, article: str, more: list[str] | None = None, progress=lambda m: None,
          out_dir: Path | None = None) -> dict[str, Any]:
    """Read the article, the pages it cites and up to MORE_ARTICLES more articles; open the long video's folder."""
    if not article.strip():
        raise LongError("Name the Wikipedia article the video is about")
    topic = bw_topics.Topic(question=question.strip() or f"Why {article.strip()}?", article=article.strip(), source="long")
    res = bw_script.research(topic, progress)
    for title in [m.strip() for m in (more or []) if m.strip()][:MORE_ARTICLES]:
        progress(f"Reading Wikipedia: {title}")
        try:
            real, text = bw_script.wiki_text(title)
        except Exception as err:  # noqa: BLE001 - a missing extra article doesn't stop the video
            logger.info("Extra article %s skipped: %s", title, err)
            continue
        if len(text) >= bw_script.MIN_SOURCE_CHARS and all(s.title != f"{real} - Wikipedia" for s in res.sources):
            res.sources.append(Source(len(res.sources) + 1, f"https://en.wikipedia.org/wiki/{real.replace(' ', '_')}",
                                      f"{real} - Wikipedia", text))
    f = root(out_dir) / _slug(res.article)
    f.mkdir(parents=True, exist_ok=True)
    _save_research(f, res)
    _save(f, "state.json", {"step": "research", "created_at": datetime.now().replace(microsecond=0).isoformat(),
                            "outline": None, "chapters": [], "problems": []})
    return view(f.name, out_dir)


def links(text: str) -> list[str]:
    """The links in a research answer, in order, without the ones the channel never uses as sources."""
    found = [u.rstrip(".,;:)]}>'\"") for u in re.findall(r"https?://[^\s<>()\"'\]]+", text or "")]
    skip = tuple(d for d in bw_script.SKIP_DOMAINS if d != "pdf") + ("reddit.com", "quora.com", "tiktok.com", "chatgpt.com",
                                                                     "gemini.google", "perplexity.ai")
    return list(dict.fromkeys(u for u in found if not any(d in u.lower() for d in skip)))


def _read(url: str) -> Source | None:
    try:
        r = bw_script._get(url, timeout=15, tries=2)
        if "html" not in r.headers.get("content-type", "html"):
            return None
        title, body = bw_script.page_text(r.text[:2_000_000])
    except Exception as err:  # noqa: BLE001 - paywalls, 403s, timeouts
        logger.info("Research link %s skipped: %s", url, err)
        return None
    if len(body) < bw_script.MIN_SOURCE_CHARS:
        return None
    return Source(0, url, title or urlparse(url).netloc, body[:PAGE_CHARS])


def add_research(sid: str, text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """Read every link of the research answer (in parallel); the pages read become sources."""
    f = folder(sid, out_dir)
    res = _research(f)
    known = {s.url for s in res.sources}
    urls = [u for u in links(text) if u not in known][:PACK_PAGES]
    if not urls:
        raise LongError("There are no links in the answer: ask the LLM to put the link of a page after every fact")
    with ThreadPoolExecutor(6) as pool:
        pages = [p for p in pool.map(_read, urls) if p is not None]
    for p in pages:
        p.id = len(res.sources) + 1
        res.sources.append(p)
    _save_research(f, res)
    state = _load(f, "state.json")
    state.update(step="outline", research={"links": len(urls), "read": len(pages)})
    _save(f, "state.json", state)
    return view(sid, out_dir)


def skip_research(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    f = folder(sid, out_dir)
    state = _load(f, "state.json")
    if state["step"] == "research":
        state["step"] = "outline"
        _save(f, "state.json", state)
    return view(sid, out_dir)


# --------------------------------------------------------------------------- 3: the outline


def check_outline(o: Outline) -> list[str]:
    problems = []
    if not CHAPTERS[0] <= len(o.chapters) <= CHAPTERS[1]:
        problems.append(f"{len(o.chapters)} chapters (wanted {CHAPTERS[0]}-{CHAPTERS[1]})")
    if not o.title.strip() or len(o.title) > 95:
        problems.append("the title is missing or over 95 characters")
    if not o.cover_text.strip():
        problems.append("there is no cover_text")
    for i, c in enumerate(o.chapters, 1):
        if not c.title.strip() or len(c.title) > 40:
            problems.append(f"chapter {i}'s title is missing or over 40 characters")
        if len(c.beats) < 2:
            problems.append(f"chapter {i} has fewer than 2 beats")
    return problems


def answer_outline(sid: str, text: str, out_dir: Path | None = None) -> dict[str, Any]:
    f = folder(sid, out_dir)
    o = _parse(text, Outline)
    state = _load(f, "state.json")
    if not o.usable:
        state.update(step="rejected", problems=[o.reject_reason.strip() or "no reason given"])
    elif problems := check_outline(o):
        state.update(step="outline", problems=problems, outline=None)
    else:
        state.update(step="chapter", problems=[], outline=o.model_dump(),
                     chapters=[{"step": "write", "draft": None, "problems": [], "flagged": []} for _ in o.chapters])
    _save(f, "state.json", state)
    return view(sid, out_dir)


# --------------------------------------------------------------------------- 4: the chapters


def _current(state: dict[str, Any]) -> int:
    return next((i for i, c in enumerate(state["chapters"]) if c["step"] != "done"), len(state["chapters"]))


def as_draft(ch: Chapter, title: str) -> Draft:
    return Draft(usable=True, title=title, cover_text=title, scenes=ch.scenes, claims=ch.claims)


def answer_chapter(sid: str, i: int, text: str, out_dir: Path | None = None, find=None) -> dict[str, Any]:
    """A chapter's draft: the rules check; a pass moves on to its fact check."""
    f = folder(sid, out_dir)
    state = _load(f, "state.json")
    if state["step"] != "chapter" or not 0 <= i < len(state["chapters"]):
        raise LongError("Paste the outline first")
    ch = _parse(text, Chapter)
    title = state["outline"]["chapters"][i]["title"]
    problems = bw_script.check(as_draft(ch, title), _research(f), **({"find": find} if find else {}), limits=CHAPTER)
    state["chapters"][i].update(step="fix" if problems else "facts", draft=ch.model_dump(), problems=problems, flagged=[])
    _save(f, "state.json", state)
    return view(sid, out_dir)


def answer_facts(sid: str, i: int, text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """A chapter's fact check: nothing flagged = the chapter is done."""
    f = folder(sid, out_dir)
    state = _load(f, "state.json")
    c = state["chapters"][i] if 0 <= i < len(state["chapters"]) else None
    if c is None or c["step"] not in ("facts", "facts_fix", "done"):
        raise LongError("Paste a chapter that passes the rules first")
    found = _parse(text, bw_script.Check)
    flagged = [u.strip() for u in found.unsupported if u.strip()]
    c.update(step="facts_fix" if flagged else "done", flagged=flagged)
    if all(ch["step"] == "done" for ch in state["chapters"]):
        state["step"] = "ready"
    _save(f, "state.json", state)
    return view(sid, out_dir)


def reopen(sid: str, i: int, out_dir: Path | None = None) -> dict[str, Any]:
    """Write a chapter again (the others stay as they are)."""
    f = folder(sid, out_dir)
    state = _load(f, "state.json")
    if not state.get("chapters") or not 0 <= i < len(state["chapters"]):
        raise LongError("No such chapter")
    state["chapters"][i].update(step="write", problems=[], flagged=[])
    state["step"] = "chapter"
    _save(f, "state.json", state)
    return view(sid, out_dir)


def _outline_text(outline: dict[str, Any]) -> str:
    return "\n".join(f"{n}. {c['title']} - {c['question']}\n" + "\n".join(f"   - {b}" for b in c["beats"])
                     for n, c in enumerate(outline["chapters"], 1))


def chapter_prompt(res: Research, outline: dict[str, Any], chapters: list[dict[str, Any]], i: int, today: str = "") -> str:
    plan = outline["chapters"][i]
    done = [Chapter.model_validate(c["draft"]) for c in chapters[:i] if c.get("draft")]
    so_far = ""
    if done:
        so_far = "THE STORY SO FAR (the narration of the chapters before yours)\n" + "\n\n".join(
            bw_script.narration(as_draft(c, "")) for c in done) + "\n\n"
    first, last = i == 0, i == len(outline["chapters"]) - 1
    opening = ("This is the video's hook: start straight away with the most surprising fact or the question itself, then "
               "say why it is stranger than it looks; end on the promise of the story." if first else
               "This is the last chapter: give the clear, full answer, then what the place is like today; end on one "
               "memorable last line." if last else
               "Open on this chapter's own question; tell its step of the story in time order; end on what makes the "
               "viewer want the next chapter.")
    return CHAPTER_PROMPT.format(
        n=i + 1, total=len(outline["chapters"]), title=outline["title"], question=res.topic.question,
        outline=_outline_text(outline), so_far=so_far, chapter_title=plan["title"], chapter_question=plan["question"],
        beats="; ".join(plan["beats"]), opening=opening, min_words=CHAPTER.min_words, max_words=CHAPTER.max_words,
        max_sentence=bw_script.MAX_SENTENCE_WORDS, min_scenes=CHAPTER.min_scenes, max_scenes=CHAPTER.max_scenes,
        max_cards=CHAPTER.max_cards, icons=", ".join(bw_fx.icon_names()), icon_count="3-6 in this chapter",
        stamp_count="one in the whole video", today=today or datetime.now().date().isoformat(), sources=bw_script.sources_block(res))


def prompt(state: dict[str, Any], res: Research) -> tuple[str, str]:
    """(what the prompt is for, the prompt) for the session's current step."""
    step = state["step"]
    if step == "research":
        return "research", RESEARCH_PROMPT.format(question=res.topic.question, article=res.article)
    if step in ("outline", "rejected"):
        p = OUTLINE_PROMPT.format(minutes="8-12", question=res.topic.question, min_ch=CHAPTERS[0], max_ch=CHAPTERS[1],
                                  today=datetime.now().date().isoformat(), sources=bw_script.sources_block(res))
        if state.get("problems") and step == "outline":
            p += f"\n\nYour last outline had problems, fix them: {'; '.join(state['problems'])}"
        return "outline", p
    if step != "chapter":
        return "", ""
    i = _current(state)
    c = state["chapters"][i]
    write = chapter_prompt(res, state["outline"], state["chapters"], i)
    if c["step"] == "fix":
        return "chapter", write + "\n\n" + FIX_PROMPT.format(problems="; ".join(c["problems"]), draft=json.dumps(c["draft"], ensure_ascii=False))
    if c["step"] == "facts_fix":
        return "chapter", write + "\n\n" + CHECK_FIX_PROMPT.format(
            flagged="\n".join(f"- {p}" for p in c["flagged"][:8]), min_words=CHAPTER.min_words, max_words=CHAPTER.max_words,
            draft=json.dumps(c["draft"], ensure_ascii=False))
    if c["step"] == "facts":
        draft = as_draft(Chapter.model_validate(c["draft"]), "")
        return "facts", bw_script.verify_prompt(draft, res) + "\n\n" + FACTS_FORMAT
    return "chapter", write


# --------------------------------------------------------------------------- the whole script


def full_script(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    """Every chapter joined into one script (modules.bw_render.spec_from_dict reads it); a chapter's first scene
    carries its banner."""
    f = folder(sid, out_dir)
    state = _load(f, "state.json")
    if state["step"] not in ("ready", "made"):
        raise LongError("Not every chapter has passed both checks yet")
    res, outline = _research(f), Outline.model_validate(state["outline"])
    chapters = [Chapter.model_validate(c["draft"]) for c in state["chapters"]]
    draft = Draft(usable=True, title=outline.title, cover_text=outline.cover_text, description=outline.description,
                  hashtags=outline.hashtags, scenes=[s for c in chapters for s in c.scenes], claims=[x for c in chapters for x in c.claims])
    script = bw_script.finish(draft, res)
    k = 0
    for n, (plan, ch) in enumerate(zip(outline.chapters, chapters), 1):
        script["scenes"][k]["chapter"] = {"n": n, "title": plan.title}
        k += len(ch.scenes)
    script.update(layout="landscape", look="satellite", origin="long", seed=zlib.crc32(sid.encode()) % 9000 + 1)
    return script


def chapter_times(spec: bw_render.Spec) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for s in spec.scenes:
        if isinstance(s.opts.get("chapter"), dict):
            out.append((0.0 if not out else s.start, s.opts["chapter"]["title"]))
    return out


def stamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def description(script: dict[str, Any], times: list[tuple[float, str]]) -> str:
    """The Short's description with the chapters (YouTube makes them clickable: from 0:00, 3+ of 10+ seconds)."""
    text = re.sub(r" ?#shorts\b", "", bw_script.description(script, bw_channel.handle()))
    if len(times) >= 3:
        block = "Chapters:\n" + "\n".join(f"{stamp(t)} {title}" for t, title in times)
        text = text.replace("Sources:", block + "\n\nSources:", 1)
    return text


# --------------------------------------------------------------------------- 5: make (its own process)


def _progress(f: Path, **kw) -> None:
    data = {}
    if (f / "progress.json").is_file():
        try:
            data = _load(f, "progress.json")
        except ValueError:
            data = {}
    data.update(kw)
    _save(f, "progress.json", data)


def job(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The make process's state: running (its process is alive), message, fraction, error."""
    f = folder(sid, out_dir)
    if not (f / "progress.json").is_file():
        return {"running": False}
    data = _load(f, "progress.json")
    if data.get("running"):
        try:
            os.kill(int(data.get("pid", 0)), 0)
        except (OSError, ValueError):
            data.update(running=False, error=data.get("error") or "The make process stopped (was the bot restarted?)")
    return data


def start_make(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    """Start the make in a process of its own (the renderer switches to landscape there, and it runs for an hour)."""
    f = folder(sid, out_dir)
    if job(sid, out_dir).get("running"):
        raise LongError("This video is already being made")
    full_script(sid, out_dir)  # the checks, before a process starts
    log = open(f / "make.log", "ab")  # noqa: SIM115 - the child process owns it
    cmd = [sys.executable, "-m", "modules.bw_long", "make", sid] + (["--out", str(out_dir)] if out_dir else [])
    proc = subprocess.Popen(cmd, cwd=str(bw_render.bw_geo.PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    _progress(f, running=True, pid=proc.pid, message="Starting", fraction=0.0, error=None, started=datetime.now().isoformat(timespec="seconds"))
    return job(sid, out_dir)


def spec_for(script: dict[str, Any], words: list[bw_render.Word]) -> bw_render.Spec:
    bw_render.use_layout("landscape")
    return bw_render.spec_from_dict(script, words)


def make(sid: str, out_dir: Path | None = None, settings=None) -> dict[str, Any]:
    """Voice, maps, render, thumbnail, info.json. Runs in the make process (python -m modules.bw_long make <id>)."""
    f = folder(sid, out_dir)

    def progress(fraction, message):
        _progress(f, message=message, **({"fraction": round(fraction, 3)} if fraction is not None else {}))

    try:
        script = full_script(sid, out_dir)
        _save(f, "script.json", script)
        progress(0.02, "Recording the narration")
        words = bw_channel.record_voice(script["narration"], f / "voice.mp3", lambda m: progress(None, m))
        spec = spec_for(script, words)
        progress(0.08, "Placing the maps")
        if missing := bw_render.prepare(spec):
            raise LongError("Places not found on the map: " + ", ".join(missing))
        if settings is None:
            from trendclip.config import get_settings

            settings = get_settings()
        track = bw_channel.pick_music(settings, lambda m: progress(None, m))
        music_path = None
        if track is not None:
            from trendclip import music

            music_path = music.track_path(settings, track)
        bw_render.render(spec, f / "voice.mp3", f / "video.mp4", music_path, float(bw_channel.setting("BW_MUSIC_VOLUME", "0.10")),
                         lambda fr, m: progress(0.1 + 0.85 * (fr or 0), m))
        progress(0.96, "Drawing the thumbnail")
        thumbnail(spec, f / "thumbnail.jpg")
        times = chapter_times(spec)
        info = {"id": sid, "title": script["title"], "question": script.get("question", ""), "article": script.get("article", ""),
                "description": description(script, times), "chapters": [[round(t, 2), title] for t, title in times],
                "hashtags": list(dict.fromkeys([*(script.get("hashtags") or []), "geography", "history", "maps"]))[:8],
                "sources": script.get("sources", []), "duration_seconds": round(spec.duration, 2),
                "words": bw_script.words(script["narration"]), "music": {"title": track.title} if track else None,
                "created_at": datetime.now().replace(microsecond=0).isoformat()}
        _save(f, "info.json", info)
        state = _load(f, "state.json")
        state["step"] = "made"
        _save(f, "state.json", state)
        _progress(f, running=False, message="Done", fraction=1.0)
        return info
    except Exception as err:  # noqa: BLE001 - shown in the tab
        logger.exception("Long video %s failed", sid)
        _progress(f, running=False, error=str(err)[:500], message="Failed")
        raise


def thumbnail(spec: bw_render.Spec, out: Path) -> Path:
    """1280x720: the first map scene with a place on it, darkened on the left under the cover text in big letters."""
    from PIL import Image, ImageDraw

    r = bw_fx.FxRenderer(spec)
    maps = [i for i, s in enumerate(spec.scenes) if s.kind == "map" and not s.opts.get("globe")]
    i = maps[0] if maps else 0
    s = spec.scenes[i]
    if r.views[i] is not None:  # the place on the right half, clear of the text
        lon, lat, ppd = r.views[i]
        r.views[i] = (lon - 0.22 * bw_render.W / (ppd * max(0.2, math.cos(math.radians(lat)))), lat, ppd)
    img = r.scene_frame(i, min(s.end - 0.05, s.start + 2.5)).convert("RGBA")
    w, h = img.size
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(shade)
    for x in range(0, w, 8):
        a = int(190 * max(0.0, 1 - x / (w * 0.62)))
        d.rectangle([x, 0, x + 8, h], fill=(0, 0, 0, a))
    img.alpha_composite(shade)
    words = (spec.cover or "").upper()
    size = 170
    lines = bw_render.wrap(words, size, int(w * 0.55))
    while size > 90 and len(lines) > 3:
        size -= 12
        lines = bw_render.wrap(words, size, int(w * 0.55))
    y = h / 2 - (len(lines) - 1) * size * 0.55
    d = ImageDraw.Draw(img)
    for k, line in enumerate(lines[:3]):
        d.text((70, y + k * size * 1.1), line, font=bw_render.font(size), fill=bw_render.FIXED["gold"] if k == 0 else (255, 255, 255),
               stroke_width=12, stroke_fill=(0, 0, 0), anchor="lm")
    img.convert("RGB").resize((1280, 720)).save(out, quality=92)
    return out


# --------------------------------------------------------------------------- preview (its own process too)


def preview(sid: str, i: int, out_dir: Path | None = None) -> Path:
    """A storyboard of chapter i: one landscape frame from the middle of each scene, the voice's pace guessed."""
    from PIL import Image

    f = folder(sid, out_dir)
    state = _load(f, "state.json")
    c = state["chapters"][i]
    if not c.get("draft"):
        raise LongError("This chapter has no draft yet")
    script = bw_script.to_spec_dict(as_draft(Chapter.model_validate(c["draft"]), state["outline"]["chapters"][i]["title"]))
    script["scenes"][0]["chapter"] = {"n": i + 1, "title": state["outline"]["chapters"][i]["title"]}
    script.update(look="satellite", seed=7, cover_text="" if i else state["outline"]["cover_text"])
    t, words = 0.0, []
    for w in " ".join(s["narration"] for s in script["scenes"]).split():
        words.append(bw_render.Word(w, t, t + 0.3))
        t += 1 / WORDS_PER_SECOND
    spec = spec_for(script, words)
    if missing := bw_render.prepare(spec):
        raise LongError("Places not found on the map: " + ", ".join(missing))
    r = bw_fx.FxRenderer(spec)
    thumbs = [r.frame(min(s.end - 0.05, s.start + max(1.2, (s.end - s.start) * 0.6))).resize((480, 270)) for s in spec.scenes]
    cols = 4
    sheet = Image.new("RGB", (480 * cols, 270 * ((len(thumbs) + cols - 1) // cols)), (12, 12, 16))
    for k, im in enumerate(thumbs):
        sheet.paste(im, ((k % cols) * 480, (k // cols) * 270))
    out = f / f"preview_{i + 1}.jpg"
    sheet.save(out, quality=85)
    return out


def run_preview(sid: str, i: int, out_dir: Path | None = None) -> Path:
    """preview() in a process of its own, so the bot's renderer stays portrait."""
    f = folder(sid, out_dir)
    cmd = [sys.executable, "-m", "modules.bw_long", "preview", sid, str(i)] + (["--out", str(out_dir)] if out_dir else [])
    r = subprocess.run(cmd, cwd=str(bw_render.bw_geo.PROJECT_ROOT), capture_output=True, text=True, timeout=600)
    if r.returncode:
        lines = [l for l in (r.stderr or r.stdout).strip().splitlines() if l.strip()]
        raise LongError("The preview failed: " + (lines[-1] if lines else f"exit {r.returncode}"))
    return f / f"preview_{i + 1}.jpg"


# --------------------------------------------------------------------------- 6: upload


def upload(sid: str, privacy: str = "private", progress=lambda f, m: None, out_dir: Path | None = None, youtube=None) -> dict[str, Any]:
    """Upload as a regular video with its thumbnail (a thumbnail YouTube refuses, e.g. an unverified channel, is noted)."""
    from trendclip import publish

    f = folder(sid, out_dir)
    if not (f / "info.json").is_file():
        raise LongError("Make the video first")
    info = _load(f, "info.json")
    if youtube is None and not bw_channel.connected():
        raise publish.PublishError("Connect the Borderline Why YouTube channel first (/bw connect)")
    youtube = youtube or publish._youtube(bw_channel.TOKEN)
    req = publish.YouTubeUploadRequest(title=info["title"][: publish.YT_TITLE_MAX], description=info["description"][:5000],
                                       tags=[t for t in info.get("hashtags", []) if t], privacy=privacy,
                                       category_id=bw_channel.CATEGORY, as_short=False)
    record = publish.upload_file(f / "video.mp4", req, False, progress, youtube)
    if (f / "thumbnail.jpg").is_file():
        try:
            from googleapiclient.http import MediaFileUpload

            youtube.thumbnails().set(videoId=record["video_id"], media_body=MediaFileUpload(str(f / "thumbnail.jpg"),
                                                                                          mimetype="image/jpeg")).execute()
            record["thumbnail"] = True
        except Exception as err:  # noqa: BLE001 - the video is up; the thumbnail can be set in Studio
            record["thumbnail_error"] = publish._api_error_text(err)
    info["upload"] = record
    _save(f, "info.json", info)
    progress(1.0, "Uploaded")
    return record


# --------------------------------------------------------------------------- what the tab shows


def view(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    f = folder(sid, out_dir)
    res, state = _research(f), _load(f, "state.json")
    purpose, text = prompt(state, res)
    chapters = []
    plans = (state.get("outline") or {}).get("chapters") or []
    for n, (plan, c) in enumerate(zip(plans, state.get("chapters") or []), 1):
        item = {"n": n, "title": plan["title"], "question": plan["question"], "step": c["step"], "problems": c["problems"],
                "flagged": c["flagged"], "preview": (f / f"preview_{n}.jpg").is_file()}
        if c.get("draft"):
            ch = Chapter.model_validate(c["draft"])
            item.update(words=bw_script.words(bw_script.narration(as_draft(ch, ""))), scenes=len(ch.scenes), claims=len(ch.claims))
        chapters.append(item)
    total = sum(c.get("words", 0) for c in chapters)
    info = _load(f, "info.json") if (f / "info.json").is_file() else None
    return {"id": sid, "question": res.topic.question, "article": res.article, "step": state["step"],
            "sources": [{"id": s.id, "title": s.title, "url": s.url} for s in res.sources],
            "research": state.get("research"), "problems": state.get("problems") or [],
            "outline": state.get("outline"), "chapters": chapters, "current": _current(state) if state["step"] == "chapter" else None,
            "words": total, "minutes": round(total / WORDS_PER_SECOND / 60, 1), "purpose": purpose, "prompt": text,
            "job": job(sid, out_dir), "info": info, "thumbnail": (f / "thumbnail.jpg").is_file(),
            "video": (f / "video.mp4").is_file() and state["step"] == "made", "created_at": state.get("created_at", "")}


def sessions(out_dir: Path | None = None) -> list[dict[str, Any]]:
    out = []
    r = root(out_dir)
    if not r.is_dir():
        return out
    for f in r.iterdir():
        if (f / "state.json").is_file() and (f / "research.json").is_file():
            state = _load(f, "state.json")
            topic = _load(f, "research.json")["topic"]
            out.append({"id": f.name, "question": topic["question"], "step": state["step"], "created_at": state.get("created_at", "")})
    return sorted(out, key=lambda s: s["created_at"], reverse=True)[:SESSIONS_SHOWN]


def main(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m modules.bw_long")
    p.add_argument("what", choices=("make", "preview"))
    p.add_argument("sid")
    p.add_argument("chapter", nargs="?", type=int, default=0, help="preview: the chapter's index (0 = the first)")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.what == "make":
        make(a.sid, a.out)
    else:
        print(preview(a.sid, a.chapter, a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
