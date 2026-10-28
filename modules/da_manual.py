"""Write a Dark Outliers script with any LLM, by hand (the dashboard's "Write it myself"), for when Gemini is out
of quota. The steps are Gemini's own, with you carrying the prompts:

  1. start      a story from the bank (the next one get_next_story() gives when none is picked) and the writing
                prompt: script_gen's own, with the story's text in it (no Gemini)
  2. answer     you paste the LLM's JSON; the same rules check as on Gemini's drafts (script_gen.check). A failed
                check gives a fix prompt (the writing prompt, the problems and the draft) to paste back. An LLM
                that calls the story unusable rejects it in the bank, as Gemini does
  3. facts      a passing draft gives the fact-check prompt (better in a second LLM): every statement must be in
                the story's text. Flags give a fix prompt, and the fixed draft goes through steps 2 and 3 again
  4. script     both checks passed: story_<id>.json is written and the story is SCRIPT_READY, so the video can
                be made for a slot (the dashboard) like any other

Each session is a folder output/darkarchives/manual/story-<id>/ (state.json), so it survives a restart and can
be finished later. The story stays NEW until step 4: the morning run may still take it meanwhile.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from db import repository
from modules import script_gen, video_gen
from modules.script_gen import ScriptDraft
from trendclip import llm_json

SESSIONS_SHOWN = 10
STORIES_SHOWN = 15

JSON_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence), shaped like this example:
{
  "usable": true,
  "reject_reason": "",
  "title": "The Ship That Sailed With No One Aboard",
  "hook_text": "A ship sailed perfectly for days with no one aboard.",
  "thumbnail_text": "NO ONE ABOARD",
  "hook_image_prompt": "cinematic dark moody photo of an empty 19th-century sailing ship on a grey sea, close-up of its deserted deck, 35mm, film grain, photorealistic",
  "scenes": [
    {"narration": "One or two short sentences.", "visual_keywords": ["foggy sea", "old ship"],
     "image_prompt": "cinematic dark moody photo of ..., 35mm, film grain, photorealistic", "overlay_text": "DECEMBER 1872"}
  ]
}
3 to 4 scenes. When the source can't be told (see the rules above): {"usable": false, "reject_reason": "why"}."""

FACTS_PROMPT = """You fact-check a script for Dark Outliers, a YouTube Shorts channel about true dark history and
unsolved mysteries. Every statement must be supported by the SOURCE below: the SOURCE is all the writer had.

List every statement of the SCRIPT (title, hook_text, thumbnail_text, each narration and overlay_text) that:
- names a person, place, date, number or event the SOURCE does not have, or gets one wrong;
- states as certain what the SOURCE calls uncertain, alleged or reported (unless it says "reportedly");
- calls a living person guilty of a crime they were not convicted of.
A shorter wording of what the SOURCE says is fine, and so is a question that invents no facts.

SOURCE ({source_name}) — {title}:
{text}

SCRIPT:
{draft}"""

FACTS_FIX = """A fact checker found statements the SOURCE does not support:
{flagged}
Rewrite only those so every fact is in the SOURCE (or drop them), keeping every rule above. Return the full
script again in the same JSON format.

Your script:
{draft}"""

FACTS_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence):
{"unsupported": ["the statement - what is wrong"]}
and {"unsupported": []} when everything is supported."""


class ManualError(ValueError):
    pass


class Check(BaseModel):
    unsupported: list[str] = Field(default_factory=list)


def root(out_dir: Path | None = None) -> Path:
    return (out_dir or video_gen.OUTPUT_DIR) / "manual"


def session_id(story_id: int) -> str:
    return f"story-{story_id}"


def _folder(sid: str, out_dir: Path | None = None) -> Path:
    if not re.fullmatch(r"story-\d{1,9}", sid or ""):
        raise ManualError(f"No manual script {sid!r}")
    folder = root(out_dir) / sid
    if not (folder / "state.json").is_file():
        raise ManualError(f"No manual script {sid!r}")
    return folder


def _state(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "state.json").read_text(encoding="utf-8"))


def _save(folder: Path, state: dict[str, Any]) -> None:
    (folder / "state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _story(state: dict[str, Any], db_path=None) -> dict:
    story = repository.get_story(state["story_id"], db_path)
    if not story:
        raise ManualError(f"Story {state['story_id']} is no longer in the bank")
    return story


# --------------------------------------------------------------------------- the steps


def start(story_id: int | None = None, out_dir: Path | None = None, db_path=None) -> dict[str, Any]:
    """Open a session for a story (the next one get_next_story() gives when none is picked); an open session
    of the same story is picked up where it was."""
    repository.init_db(db_path)
    story = repository.get_story(story_id, db_path) if story_id else repository.get_next_story(db_path)
    if not story:
        raise ManualError(f"No story {story_id}" if story_id else "No NEW stories left: fetch more in Stories")
    if story["status"] in ("USED", "PROCESSING"):
        raise ManualError("This story is already in a video" if story["status"] == "USED" else "Gemini is writing this story right now")
    folder = root(out_dir) / session_id(story["id"])
    if not (folder / "state.json").is_file():
        folder.mkdir(parents=True, exist_ok=True)
        _save(folder, {"story_id": story["id"], "step": "write", "draft": None, "problems": [], "flagged": [],
                       "created_at": datetime.now().replace(microsecond=0).isoformat()})
    return view(folder.name, out_dir, db_path)


def answer(sid: str, text: str, out_dir: Path | None = None, db_path=None) -> dict[str, Any]:
    """Step 2: the LLM's draft. The rules check runs; a pass moves on to the fact check."""
    folder = _folder(sid, out_dir)
    state = _state(folder)
    if state["step"] == "made":
        raise ManualError("This story is already made into a video")
    draft = llm_json.parse(text, ScriptDraft, ManualError)
    if not draft.usable:
        reason = draft.reject_reason.strip() or "no reason given"
        state.update(step="rejected", draft=draft.model_dump(), problems=[reason], flagged=[])
        repository.set_status(state["story_id"], "REJECTED", db_path, note=f"the LLM (by hand): {reason}"[:300])
    else:
        problems = script_gen.check(draft)
        state.update(step="fix" if problems else "facts", draft=draft.model_dump(), problems=problems, flagged=[])
    _save(folder, state)
    return view(sid, out_dir, db_path)


def facts(sid: str, text: str, out_dir: Path | None = None, db_path=None) -> dict[str, Any]:
    """Step 3: the fact checker's answer. Nothing flagged: the script is saved and the story SCRIPT_READY."""
    folder = _folder(sid, out_dir)
    state = _state(folder)
    if state["step"] not in ("facts", "facts_fix"):
        raise ManualError("Paste a draft that passes the rules first")
    flagged = [u.strip() for u in llm_json.parse(text, Check, ManualError).unsupported if u.strip()]
    if flagged:
        state.update(step="facts_fix", flagged=flagged)
    else:
        story = _story(state, db_path)
        if story["status"] in ("USED", "PROCESSING"):
            raise ManualError("Gemini wrote or used this story meanwhile: start another one")
        script_gen.save(script_gen.finalize(story["id"], ScriptDraft.model_validate(state["draft"])),
                        db_path=db_path, note="written by hand (Write it myself)")
        state.update(step="ready", flagged=[])
    _save(folder, state)
    return view(sid, out_dir, db_path)


def mark_made(sid: str, item: dict | None = None, out_dir: Path | None = None) -> None:
    folder = _folder(sid, out_dir)
    state = _state(folder)
    state.update(step="made", planned=f"{item['date']} {item['slot']}" if item else "")
    _save(folder, state)


def story_of(sid: str, out_dir: Path | None = None) -> int:
    """The story id of a session whose script is ready (both checks passed)."""
    state = _state(_folder(sid, out_dir))
    if state["step"] != "ready":
        raise ManualError("The script hasn't passed both checks yet")
    return state["story_id"]


# --------------------------------------------------------------------------- what the panel shows


def _prompt(step: str, story: dict, draft: ScriptDraft | None, problems: list[str], flagged: list[str]) -> str:
    """The prompt to copy for this step (each is whole, so a fresh chat or another LLM works too)."""
    write = script_gen.build_prompt(story)
    if step in ("write", "rejected") or draft is None:
        return write + "\n\n" + JSON_FORMAT
    if step == "fix":
        return write + "\n\n" + script_gen.fix_prompt(draft, problems) + "\n" + JSON_FORMAT
    if step == "facts_fix":
        return write + "\n\n" + FACTS_FIX.format(flagged="\n".join(f"- {f}" for f in flagged),
                                                 draft=script_gen.draft_json(draft)) + "\n" + JSON_FORMAT
    if step == "facts":
        return FACTS_PROMPT.format(source_name=story["source_name"], title=story["title"],
                                   text=story["raw_text"][:script_gen.SOURCE_CHARS], draft=script_gen.draft_json(draft)) + "\n\n" + FACTS_FORMAT
    return ""


def view(sid: str, out_dir: Path | None = None, db_path=None) -> dict[str, Any]:
    folder = _folder(sid, out_dir)
    state = _state(folder)
    story = _story(state, db_path)
    draft = ScriptDraft.model_validate(state["draft"]) if state.get("draft") else None
    preview = None
    if draft is not None:
        preview = {"title": draft.title, "hook": draft.hook_text, "cover_text": draft.thumbnail_text,
                   "words": script_gen.words(script_gen.spoken(draft)),
                   "scenes": [{"narration": s.narration, "overlay": s.overlay_text} for s in draft.scenes]}
    step = state["step"]
    return {"id": sid, "story_id": story["id"], "title": story["title"], "source": story["source_name"],
            "url": story.get("url") or "", "pillar": story.get("pillar") or "", "status": story["status"], "step": step,
            "problems": state.get("problems") or [], "flagged": state.get("flagged") or [],
            "prompt": _prompt(step, story, draft, state.get("problems") or [], state.get("flagged") or []),
            "preview": preview, "planned": state.get("planned", ""), "created_at": state.get("created_at", "")}


def sessions(out_dir: Path | None = None, db_path=None) -> list[dict[str, Any]]:
    """The newest sessions, for picking one up again."""
    out = []
    folder = root(out_dir)
    if not folder.is_dir():
        return out
    for f in folder.iterdir():
        if (f / "state.json").is_file():
            state = _state(f)
            story = repository.get_story(state["story_id"], db_path)
            out.append({"id": f.name, "title": story["title"] if story else f"story {state['story_id']}",
                        "step": state["step"], "created_at": state.get("created_at", "")})
    return sorted(out, key=lambda s: s["created_at"], reverse=True)[:SESSIONS_SHOWN]


def stories(db_path=None) -> list[dict[str, Any]]:
    """The best NEW stories to pick from (the first is what the morning run would take next)."""
    nxt = repository.get_next_story(db_path)
    rows = [s for s in repository.all_stories(db_path=db_path) if s["status"] == "NEW"]
    rows.sort(key=lambda s: -(s["score"] or 0))
    if nxt:
        rows = [nxt] + [s for s in rows if s["id"] != nxt["id"]]
    return [{"id": s["id"], "title": s["title"], "score": s["score"], "pillar": s.get("pillar") or "",
             "next": bool(nxt) and s["id"] == nxt["id"]} for s in rows[:STORIES_SHOWN]]
