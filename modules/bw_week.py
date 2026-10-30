"""Write a week of Borderline Why scripts with any LLM at once (the dashboard's "Write a week"), then send them to
the Linux hub, which makes one video per slot from them without asking Gemini.

The steps are the manual flow's (modules.bw_manual), for several topics in one prompt:

  1. start    the code reads Wikipedia and the cited sources of the next N topics (no Gemini): the session
  2. answer   you paste the LLM's JSON {"scripts": [{"topic": 1, ...}, ...]}; each draft gets the rules check.
              Failed drafts go back in the next write prompt with their problems (a fix); passed ones wait for
              the fact check
  3. facts    one fact-check prompt for every passing draft (better in a second LLM); you paste
              {"checks": [{"topic": 1, "unsupported": [...]}]}. Flags send that draft back for a fix
  4. send     the ready scripts go to the hub's queue (output/borderlinewhy/queue): its daily job takes the oldest
              first, one per slot, so 7 scripts are the coming week

One session at a time: output/borderlinewhy/week/session.json (starting a new week replaces it).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from modules import bw_channel, bw_manual, bw_script, bw_topics
from modules.bw_script import Draft, Research, ScriptError, Source
from trendclip import llm_json

DEFAULT_COUNT = 7
MAX_COUNT = 14
WRITING = ("write", "fix", "facts_fix")  # steps whose topic is in the write prompt
DONE = ("ready", "sent", "rejected", "dropped")


class WeekError(ValueError):
    pass


class TopicDraft(Draft):
    topic: int = Field(description="The topic's number from the prompt")


class WeekAnswer(BaseModel):
    scripts: list[TopicDraft] = Field(default_factory=list)


class TopicCheck(BaseModel):
    topic: int
    unsupported: list[str] = Field(default_factory=list)


class WeekChecks(BaseModel):
    checks: list[TopicCheck] = Field(default_factory=list)


def root(out_dir: Path | None = None) -> Path:
    return bw_channel.out_root(out_dir) / "week"


def _path(out_dir: Path | None = None) -> Path:
    return root(out_dir) / "session.json"


def _load(out_dir: Path | None = None) -> dict[str, Any]:
    path = _path(out_dir)
    if not path.is_file():
        raise WeekError("No week started yet: read the sources of the next topics first")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(session: dict[str, Any], out_dir: Path | None = None) -> None:
    path = _path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _research(entry: dict[str, Any]) -> Research:
    return Research(bw_topics.Topic(**entry["topic"]), entry["article"], [Source(**s) for s in entry["sources"]])


def _draft(entry: dict[str, Any]) -> Draft | None:
    return Draft.model_validate(entry["draft"]) if entry.get("draft") else None


# --------------------------------------------------------------------------- 1. start


def start(count: int = DEFAULT_COUNT, progress=lambda m: None, out_dir: Path | None = None) -> dict[str, Any]:
    """Research the next `count` topics on the list (a topic without two sources is marked rejected and the
    next is taken) and open the session."""
    count = max(1, min(MAX_COUNT, int(count)))
    topics_dir = bw_channel.out_root(out_dir)
    entries: list[dict[str, Any]] = []
    tried: set[str] = set()
    while len(entries) < count:
        topic = bw_topics.next_topic(topics_dir, tried)
        if topic is None:
            break
        tried.add(topic.article)
        progress(f"Reading the sources of topic {len(entries) + 1}: {topic.question}")
        try:
            res = bw_script.research(topic, lambda m: None)
        except bw_script.TryLater:
            raise
        except ScriptError as err:
            bw_topics.mark(topics_dir, topic.article, used=False, note=str(err)[:300])
            continue
        if len(res.sources) < 2:
            bw_topics.mark(topics_dir, topic.article, used=False, note="only Wikipedia could be read: the channel wants two sources")
            continue
        entries.append({"i": len(entries) + 1, "topic": topic.model_dump(), "article": res.article,
                        "sources": [asdict(s) for s in res.sources], "step": "write", "draft": None, "problems": [], "flagged": []})
    if not entries:
        raise WeekError("No topic with two readable sources is left on the list: add topics in Topics first")
    session = {"created_at": datetime.now().replace(microsecond=0).isoformat(), "count": count, "topics": entries}
    _save(session, out_dir)
    return view(out_dir)


# --------------------------------------------------------------------------- the prompts


def _rules() -> str:
    """The writing rules once, for several topics: PROMPT without its question line and SOURCES block."""
    text = bw_script.PROMPT
    head, _, rest = text.partition('Today\'s question: "{question}"')
    body, _, _ = rest.partition("SOURCES\n{sources}")
    rules = (head + "You write one Short for EACH topic below, each from ONLY its own sources. The same rules apply to every one."
             + body)
    return rules.format(min_words=bw_script.MIN_WORDS, max_words=bw_script.MAX_WORDS, max_sentence=bw_script.MAX_SENTENCE_WORDS,
                        min_scenes=bw_script.MIN_SCENES, max_scenes=bw_script.MAX_SCENES, max_cards=bw_script.MAX_CARDS,
                        icons=", ".join(bw_script.bw_fx.ICONS), icon_count="6-10 in the video", stamp_count="one in the video",
                        mascot_lines=bw_script.MASCOT_LINES, today=datetime.now().date().isoformat()).rstrip()


def _topic_block(entry: dict[str, Any]) -> str:
    res = _research(entry)
    lines = [f'=== TOPIC {entry["i"]}: "{res.topic.question}" ===', "SOURCES", bw_script.sources_block(res)]
    draft = _draft(entry)
    if entry["step"] == "fix" and draft is not None:
        lines += ["", bw_script.fix_prompt(entry.get("problems") or [], draft).rstrip()]
    elif entry["step"] == "facts_fix" and draft is not None:
        lines += ["", bw_script.check_fix_prompt(entry.get("flagged") or [], draft).rstrip()]
    return "\n".join(lines)


WEEK_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence):
{"scripts": [ {"topic": 1, ...}, {"topic": 2, ...} ]}
with one entry per topic, "topic" being the topic's number above, and the rest of each entry shaped like this example:
"""


def write_prompt(session: dict[str, Any]) -> str:
    """One prompt for every topic still to write or to fix (empty when there is none)."""
    todo = [t for t in session["topics"] if t["step"] in WRITING]
    if not todo:
        return ""
    example = bw_manual.JSON_FORMAT.split("\n", 2)[2]  # the example object and the element fields
    fixes = [t for t in todo if t["step"] != "write"]
    note = ""
    if fixes:
        note = ("\n\nTopics " + ", ".join(str(t["i"]) for t in fixes)
                + " come with your previous draft and what was wrong with it: fix every problem and return the whole draft again.")
    return (_rules() + f"\n\nThere are {len(todo)} topics." + note + "\n\n" + "\n\n".join(_topic_block(t) for t in todo)
            + "\n\n" + WEEK_FORMAT + example)


FACTS_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence):
{"checks": [ {"topic": 1, "unsupported": ["the statement - what is wrong", "OFF TOPIC: the sentence - why"]}, {"topic": 2, "unsupported": []} ]}
with one entry per topic; an empty list when everything in that narration is supported and on topic."""


def facts_prompt(session: dict[str, Any]) -> str:
    """One fact-check prompt for every draft that passed the rules (empty when there is none)."""
    todo = [t for t in session["topics"] if t["step"] == "facts"]
    if not todo:
        return ""
    head = bw_script.VERIFY_PROMPT.split("\n\nNARRATION\n", 1)[0]
    head = head.replace('Check a YouTube narration that answers the question "{question}" against its sources. List:',
                        f"Check {len(todo)} YouTube narrations, each answering its own question, against its own sources. For each, list:")
    blocks = []
    for t in todo:
        res, draft = _research(t), _draft(t)
        claims = "\n".join(f"- {c.claim} [source {c.source_id}: \"{c.quote}\"]" for c in draft.claims) or "(none)"
        blocks.append(f'=== TOPIC {t["i"]}: "{res.topic.question}" ===\nNARRATION\n{bw_script.narration(draft)}\n\n'
                      f"CLAIMS (quote checked word for word in the source named)\n{claims}\n\nSOURCES\n{bw_script.sources_block(res)}")
    return head + "\n\n" + "\n\n".join(blocks) + "\n\n" + FACTS_FORMAT


# --------------------------------------------------------------------------- 2. answer, 3. facts


def _entry(session: dict[str, Any], i: int) -> dict[str, Any] | None:
    return next((t for t in session["topics"] if t["i"] == i), None)


def answer(text: str, out_dir: Path | None = None, find=None) -> dict[str, Any]:
    """The LLM's drafts: the rules check for each; a pass waits for the fact check, a fail goes back for a fix."""
    session = _load(out_dir)
    found = llm_json.parse(text, WeekAnswer, WeekError)
    if not found.scripts:
        raise WeekError('The answer has no "scripts": ask the LLM for {"scripts": [...]} with one entry per topic')
    seen = 0
    for item in found.scripts:
        entry = _entry(session, item.topic)
        if entry is None or entry["step"] not in WRITING:
            continue
        seen += 1
        draft = Draft.model_validate(item.model_dump(exclude={"topic"}))
        if not draft.usable:
            reason = draft.reject_reason.strip() or "no reason given"
            entry.update(step="rejected", draft=draft.model_dump(), problems=[reason], flagged=[])
            bw_topics.mark(bw_channel.out_root(out_dir), entry["topic"]["article"], used=False,
                           note=f"the LLM said the sources can't support it: {reason}"[:300])
            continue
        problems = bw_script.check(draft, _research(entry), **({"find": find} if find else {}))
        entry.update(step="fix" if problems else "facts", draft=draft.model_dump(), problems=problems, flagged=[])
    if not seen:
        raise WeekError("None of the pasted scripts belongs to a topic still to write (check the topic numbers)")
    _save(session, out_dir)
    return view(out_dir)


def facts(text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The fact checker's answer: nothing flagged makes a draft ready, flags send it back for a fix."""
    session = _load(out_dir)
    found = llm_json.parse(text, WeekChecks, WeekError)
    seen = 0
    for item in found.checks:
        entry = _entry(session, item.topic)
        if entry is None or entry["step"] != "facts":
            continue
        seen += 1
        flagged = [u.strip() for u in item.unsupported if u.strip()]
        entry.update(step="facts_fix" if flagged else "ready", flagged=flagged)
    if not seen:
        raise WeekError("None of the pasted checks belongs to a draft waiting for its fact check (check the topic numbers)")
    _save(session, out_dir)
    return view(out_dir)


def drop(i: int, out_dir: Path | None = None) -> dict[str, Any]:
    """Leave a topic out of this week (it stays on the list for another time)."""
    session = _load(out_dir)
    entry = _entry(session, i)
    if entry is None:
        raise WeekError(f"No topic {i} in this week")
    if entry["step"] == "sent":
        raise WeekError("That script is already on Linux")
    entry.update(step="dropped")
    _save(session, out_dir)
    return view(out_dir)


# --------------------------------------------------------------------------- 4. send


def scripts(out_dir: Path | None = None) -> list[dict[str, Any]]:
    """The ready scripts (both checks passed), in topic order: what goes to the hub's queue."""
    session = _load(out_dir)
    out = []
    for t in session["topics"]:
        if t["step"] == "ready":
            out.append({**bw_script.finish(_draft(t), _research(t)), "origin": "week", "week_topic": t["i"]})
    return out


def send(settings, out_dir: Path | None = None, deliver=None) -> dict[str, Any]:
    """The ready scripts go to the hub's queue (through Discord on the sending machine, straight into the queue
    on the hub itself). Marks each sent topic; returns the hub's answer with the view."""
    from modules import channel_hub

    ready = scripts(out_dir)
    if not ready:
        raise WeekError("No script has passed both checks yet")
    deliver = deliver or channel_hub.queue_scripts
    reply = deliver(settings, "borderlinewhy", {"scripts": ready})
    session = _load(out_dir)
    queued = {q.get("week_topic") for q in reply.get("queued", [])}
    skipped = {s.get("week_topic"): s.get("why", "") for s in reply.get("skipped", [])}
    for t in session["topics"]:
        if t["step"] != "ready":
            continue
        if t["i"] in queued:
            t.update(step="sent", sent={"at": datetime.now().replace(microsecond=0).isoformat(), "name": next(
                (q.get("name") for q in reply["queued"] if q.get("week_topic") == t["i"]), "")})
            bw_topics.mark(bw_channel.out_root(out_dir), t["topic"]["article"], used=True)
        elif t["i"] in skipped:
            t["problems"] = [f"Linux did not take it: {skipped[t['i']]}"]
    session["last_send"] = {"at": datetime.now().replace(microsecond=0).isoformat(), **reply}
    _save(session, out_dir)
    return {**view(out_dir), "reply": reply}


# --------------------------------------------------------------------------- what the panel shows


def _preview(entry: dict[str, Any]) -> dict[str, Any] | None:
    draft = _draft(entry)
    if draft is None:
        return None
    text = bw_script.narration(draft)
    return {"title": draft.title, "cover_text": draft.cover_text, "words": bw_script.words(text), "scenes": len(draft.scenes),
            "claims": len(draft.claims), "narration": text}


def view(out_dir: Path | None = None) -> dict[str, Any]:
    session = _load(out_dir)
    topics = [{"i": t["i"], "question": t["topic"]["question"], "article": t["article"], "step": t["step"],
               "sources": [{"id": s["id"], "title": s["title"], "url": s["url"]} for s in t["sources"]],
               "problems": t.get("problems") or [], "flagged": t.get("flagged") or [], "preview": _preview(t),
               "sent": t.get("sent")} for t in session["topics"]]
    steps = [t["step"] for t in topics]
    write = write_prompt(session)
    check = facts_prompt(session)
    return {"created_at": session["created_at"], "count": session["count"], "topics": topics,
            "to_write": sum(1 for s in steps if s in WRITING), "to_check": steps.count("facts"), "ready": steps.count("ready"),
            "sent": steps.count("sent"), "write_prompt": write, "facts_prompt": check,
            "write_chars": len(write), "facts_chars": len(check), "last_send": session.get("last_send")}


def exists(out_dir: Path | None = None) -> bool:
    return _path(out_dir).is_file()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "topic"
