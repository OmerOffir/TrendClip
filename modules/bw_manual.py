"""Write a Borderline Why script with any LLM, by hand (the dashboard's "Write it myself"), for when Gemini is out
of quota. The steps are Gemini's own, with you carrying the prompts:

  1. start      the code reads Wikipedia and the cited sources (no Gemini) and gives the writing prompt
  2. answer     you paste the LLM's JSON; the code runs the same rules check as on Gemini's drafts. A failed
                check gives a fix prompt (the writing prompt, the problems and the draft) to paste back
  3. facts      a passing draft gives the fact-check prompt (better in a second LLM); you paste its JSON. Flags
                give a fix prompt, and the fixed draft goes through steps 2 and 3 again
  4. script     both checks passed: the script the bot makes the video from (modules.bw_channel.make)

Each topic is a folder output/borderlinewhy/manual/<id>/ (research.json, state.json), so a session survives a
restart and can be finished later.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any


from modules import bw_channel, bw_script, bw_topics
from modules.bw_script import Check, Draft, Research, ScriptError, Source
from trendclip import llm_json
from trendclip.llm_json import repair_json  # noqa: F401 - kept importable from here

SESSIONS_SHOWN = 10

JSON_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence), shaped like this example:
{
  "usable": true,
  "reject_reason": "",
  "title": "Why does ... ?",
  "cover_text": "3-7 words",
  "scenes": [
    {"kind": "map", "narration": "First sentence of the narration.", "focus": ["Minnesota", "Manitoba"],
     "headline": "2-6 words", "elements": [
       {"kind": "highlight", "place": "Canada", "flag": "Flag of Canada.svg", "color": "b", "cue": "Canada"},
       {"kind": "route", "points": ["Warroad", "Northwest Angle"], "icon": "car", "color": "gold", "cue": "drive"},
       {"kind": "icon", "icon": "money_bag", "cue": "paid"}]},
    {"kind": "card", "narration": "Next sentence.", "kicker": "Treaty of Paris", "title": "1783",
     "subtitle": "one short line", "elements": [
       {"kind": "pole", "flag": "Flag of the United Kingdom.svg", "cue": "British"}]},
    {"kind": "board", "narration": "A sentence that answers the mascot.", "ask": "Wait, so how did they get it?",
     "board_write": ["The 1783 deal", "Britain keeps Canada"], "board_draw": ["Minnesota", "Northwest Angle"]},
    {"kind": "map", "narration": "The last sentence.", "focus": ["Northwest Angle"], "react": "So it's American... but you drive through Canada?",
     "elements": [{"kind": "highlight", "place": "Northwest Angle", "color": "gold", "cue": "Angle"}]}
  ],
  "claims": [{"claim": "One fact the narration states.", "source_id": 1, "quote": "the exact words copied from source 1"}],
  "description": "2-3 sentences for the YouTube description.",
  "hashtags": ["geography", "borders", "history"]
}
Scene kinds: map | card | board. Board fields: board_write, board_draw. The mascot: ask, react (any scene).
Element fields: kind (highlight | label | pin | ring | line | arrow | parallel | meridian | stamp | icon | pole |
route), place, to (line / arrow: where it ends), value (parallel / meridian: the degrees), text (a short label),
color (a | b | c | gold | red | white), cue, flag, icon, points. Leave out the fields an element doesn't use."""

FACTS_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence):
{"unsupported": ["the statement - what is wrong", "OFF TOPIC: the sentence - why"]}
and {"unsupported": []} when everything is supported and on topic."""


class ManualError(ValueError):
    pass


def root(out_dir: Path | None = None) -> Path:
    return bw_channel.out_root(out_dir) / "manual"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "topic"


def _folder(sid: str, out_dir: Path | None = None) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", sid or ""):
        raise ManualError(f"No manual script {sid!r}")
    folder = root(out_dir) / sid
    if not (folder / "research.json").is_file():
        raise ManualError(f"No manual script {sid!r}")
    return folder


def _save(folder: Path, name: str, data: dict[str, Any]) -> None:
    (folder / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _research(folder: Path) -> Research:
    data = json.loads((folder / "research.json").read_text(encoding="utf-8"))
    return Research(bw_topics.Topic(**data["topic"]), data["article"], [Source(**s) for s in data["sources"]])


def _state(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "state.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- the steps


def start(question: str = "", article: str = "", progress=lambda m: None, out_dir: Path | None = None) -> dict[str, Any]:
    """Research a topic (the next on the list when none is given) and open its session."""
    topics_dir = bw_channel.out_root(out_dir)
    if article.strip():
        topic = bw_topics.Topic(question=question.strip() or f"Why {article.strip()}?", article=article.strip(), source="manual")
    else:
        topic = bw_topics.next_topic(topics_dir)
        if topic is None:
            raise ManualError("The topic list is empty: add a topic in Topics first")
    res = bw_script.research(topic, progress)
    if len(res.sources) < 2:
        raise ScriptError(f"Only Wikipedia could be read for {res.article!r}: the channel wants two sources. Pick another topic.")
    folder = root(out_dir) / _slug(res.article)
    folder.mkdir(parents=True, exist_ok=True)
    _save(folder, "research.json", {"topic": topic.model_dump(), "article": res.article,
                                    "sources": [asdict(s) for s in res.sources]})
    _save(folder, "state.json", {"step": "write", "created_at": datetime.now().replace(microsecond=0).isoformat(),
                                 "draft": None, "problems": [], "flagged": []})
    return view(folder.name, out_dir)


def _parse(text: str, schema):
    return llm_json.parse(text, schema, ManualError)


def answer(sid: str, text: str, out_dir: Path | None = None, find=None) -> dict[str, Any]:
    """Step 2: the LLM's draft. The rules check runs; a pass moves on to the fact check."""
    folder = _folder(sid, out_dir)
    draft = _parse(text, Draft)
    state = _state(folder)
    if not draft.usable:
        reason = draft.reject_reason.strip() or "no reason given"
        state.update(step="rejected", draft=draft.model_dump(), problems=[reason])
        bw_topics.mark(bw_channel.out_root(out_dir), _research(folder).topic.article, used=False,
                       note=f"the LLM said the sources can't support it: {reason}"[:300])
    else:
        problems = bw_script.check(draft, _research(folder), **({"find": find} if find else {}))
        state.update(step="fix" if problems else "facts", draft=draft.model_dump(), problems=problems, flagged=[])
    _save(folder, "state.json", state)
    return view(sid, out_dir)


def facts(sid: str, text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """Step 3: the fact checker's answer. Nothing flagged: ready; else a fix prompt."""
    folder = _folder(sid, out_dir)
    state = _state(folder)
    if state["step"] not in ("facts", "facts_fix", "ready"):
        raise ManualError("Paste a draft that passes the rules first")
    found = _parse(text, Check)
    flagged = [u.strip() for u in found.unsupported if u.strip()]
    state.update(step="facts_fix" if flagged else "ready", flagged=flagged)
    _save(folder, "state.json", state)
    return view(sid, out_dir)


def script(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    """Step 4: the script to make the video from (both checks passed)."""
    folder = _folder(sid, out_dir)
    state = _state(folder)
    if state["step"] != "ready":
        raise ManualError("The script hasn't passed both checks yet")
    return {**bw_script.finish(Draft.model_validate(state["draft"]), _research(folder)), "origin": "manual"}


def mark_made(sid: str, key: str, out_dir: Path | None = None) -> None:
    folder = _folder(sid, out_dir)
    state = _state(folder)
    state.update(step="made", video=key)
    _save(folder, "state.json", state)


# --------------------------------------------------------------------------- what the panel shows


def _prompt(step: str, res: Research, draft: Draft | None, problems: list[str], flagged: list[str]) -> str:
    """The prompt to copy for this step (each is whole, so a fresh chat or another LLM works too)."""
    write = bw_script.write_prompt(res)
    if step in ("write", "rejected") or draft is None:
        return write + "\n\n" + JSON_FORMAT
    if step == "fix":
        return write + "\n\n" + bw_script.fix_prompt(problems, draft) + "\n" + JSON_FORMAT
    if step == "facts_fix":
        return write + "\n\n" + bw_script.check_fix_prompt(flagged, draft) + "\n" + JSON_FORMAT
    if step == "facts":
        return bw_script.verify_prompt(draft, res) + "\n\n" + FACTS_FORMAT
    return ""


def view(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    folder = _folder(sid, out_dir)
    res, state = _research(folder), _state(folder)
    draft = Draft.model_validate(state["draft"]) if state.get("draft") else None
    preview = None
    if draft is not None:
        text = bw_script.narration(draft)
        preview = {"title": draft.title, "cover_text": draft.cover_text, "words": bw_script.words(text),
                   "scenes": [{"kind": s.kind, "narration": s.narration, "elements": [e.kind for e in s.elements]} for s in draft.scenes],
                   "claims": len(draft.claims)}
    return {"id": sid, "question": res.topic.question, "article": res.article, "step": state["step"],
            "sources": [{"id": s.id, "title": s.title, "url": s.url} for s in res.sources],
            "problems": state.get("problems") or [], "flagged": state.get("flagged") or [],
            "prompt": _prompt(state["step"], res, draft, state.get("problems") or [], state.get("flagged") or []),
            "preview": preview, "video": state.get("video"), "created_at": state.get("created_at", "")}


def sessions(out_dir: Path | None = None) -> list[dict[str, Any]]:
    """The newest sessions, for picking one up again."""
    out = []
    folder = root(out_dir)
    if not folder.is_dir():
        return out
    for f in folder.iterdir():
        if (f / "state.json").is_file() and (f / "research.json").is_file():
            state = _state(f)
            topic = json.loads((f / "research.json").read_text(encoding="utf-8"))["topic"]
            out.append({"id": f.name, "question": topic["question"], "step": state["step"], "created_at": state.get("created_at", "")})
    return sorted(out, key=lambda s: s["created_at"], reverse=True)[:SESSIONS_SHOWN]
