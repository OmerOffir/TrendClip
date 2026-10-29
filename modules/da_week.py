"""Write a week of Dark Outliers scripts with any LLM at once (the dashboard's "Write a week"), then send them to
the Linux hub, which makes the videos from them without asking Gemini.

The steps are the manual flow's (modules.da_manual), for several stories in one prompt, plus the rankings the
channel makes every DA_RANKING_EVERY-th video:

  1. start    the best NEW stories of the bank (no Gemini): S single stories and R rankings, each ranking with
              its own pool of candidate stories (the pools and the single stories never share a story)
  2. answer   you paste the LLM's JSON {"scripts": [{"story": <id>, ...}], "rankings": [{"ranking": 1, ...}]};
              every script gets the rules check (script_gen.check / da_ranking.check). Failed ones go back in
              the next write prompt with their problems; passed single stories wait for the fact check, passed
              rankings are ready (their facts are the candidates' own words, 700 characters each)
  3. facts    one fact-check prompt for every passing story draft (better in a second LLM); you paste
              {"checks": [{"story": <id>, "unsupported": [...]}]}. Flags send that draft back for a fix
  4. send     the ready scripts go to the hub (channel_hub.DarkOutliers.queue): it stores each story in its own
              bank with the script, SCRIPT_READY, and the production run takes them before asking Gemini

One session at a time: output/darkarchives/week/session.json (starting a new week replaces it).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from db import repository
from modules import da_manual, da_ranking, script_gen, video_gen
from modules.script_gen import ScriptDraft
from trendclip import llm_json

DEFAULT_COUNT = 10  # single stories
MAX_COUNT = 21
MAX_RANKINGS = 7
POOL = 8  # candidate stories per ranking
WRITING = ("write", "fix", "facts_fix")
DONE = ("ready", "sent", "rejected", "dropped")


class WeekError(ValueError):
    pass


class StoryDraft(ScriptDraft):
    story: int = Field(description="The story's id from the prompt")


class WeekRanking(da_ranking.RankingDraft):
    ranking: int = Field(description="The ranking's number from the prompt")


class WeekAnswer(BaseModel):
    scripts: list[StoryDraft] = Field(default_factory=list)
    rankings: list[WeekRanking] = Field(default_factory=list)


class StoryCheck(BaseModel):
    story: int
    unsupported: list[str] = Field(default_factory=list)


class WeekChecks(BaseModel):
    checks: list[StoryCheck] = Field(default_factory=list)


def root(out_dir: Path | None = None) -> Path:
    return (out_dir or video_gen.OUTPUT_DIR) / "week"


def _path(out_dir: Path | None = None) -> Path:
    return root(out_dir) / "session.json"


def _load(out_dir: Path | None = None) -> dict[str, Any]:
    path = _path(out_dir)
    if not path.is_file():
        raise WeekError("No week started yet: pick the next stories first")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(session: dict[str, Any], out_dir: Path | None = None) -> None:
    path = _path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _draft(entry: dict[str, Any]) -> ScriptDraft | None:
    return ScriptDraft.model_validate(entry["draft"]) if entry.get("draft") else None


def _rdraft(entry: dict[str, Any]) -> da_ranking.RankingDraft | None:
    return da_ranking.RankingDraft.model_validate(entry["draft"]) if entry.get("draft") else None


# --------------------------------------------------------------------------- 1. start


def default_rankings(count: int) -> int:
    """How many rankings go with `count` single stories: one per DA_RANKING_EVERY videos (0 when rankings are off)."""
    every = da_ranking.every()
    return min(MAX_RANKINGS, count // max(every - 1, 1)) if every else 0


def start(count: int = DEFAULT_COUNT, rankings: int | None = None, out_dir: Path | None = None, db_path=None) -> dict[str, Any]:
    """The best NEW stories of the bank: `count` single stories and `rankings` rankings with POOL candidates each,
    all different stories. Opens the session (replacing the old one)."""
    repository.init_db(db_path)
    count = max(0, min(MAX_COUNT, int(count)))
    rankings = default_rankings(count) if rankings is None else max(0, min(MAX_RANKINGS, int(rankings)))
    if not count and not rankings:
        raise WeekError("Pick at least one story or one ranking")
    n = da_ranking.count()
    pool = da_ranking.candidates(db_path, pool=count + rankings * POOL)
    if len(pool) < count + (n if rankings else 0):
        raise WeekError(f"Only {len(pool)} NEW stories in the bank; fetch more in Stories "
                        f"(a week of {count} stories{f' and {rankings} rankings' if rankings else ''} needs more)")
    stories = [{"story": s, "step": "write", "draft": None, "problems": [], "flagged": []} for s in pool[:count]]
    rest = pool[count:]
    groups: list[dict[str, Any]] = []
    for k in range(rankings):
        members = rest[k * POOL:(k + 1) * POOL]
        if len(members) < n:
            break  # fewer rankings than asked: the bank is short
        groups.append({"k": k + 1, "pool": members, "n": n, "step": "write", "draft": None, "problems": []})
    session = {"created_at": datetime.now().replace(microsecond=0).isoformat(), "count": count, "rankings": rankings,
               "stories": stories, "groups": groups}
    _save(session, out_dir)
    return view(out_dir)


# --------------------------------------------------------------------------- the prompts


def _rules(today: datetime | None = None) -> str:
    """script_gen.PROMPT's rules once, for several stories: without the one-story line and the SOURCE tail."""
    head, _, _ = script_gen.PROMPT.partition("\n\nSOURCE ({source_name})")
    head = head.replace("Turn the SOURCE below into one Short of 20-28 seconds. Content pillar: {pillar}.",
                        "Turn EACH STORY below into one Short of 20-28 seconds, from ONLY its own SOURCE (its content pillar "
                        "is named with it). The same rules apply to every one.")
    head = head.replace("If the SOURCE starts with", "If a SOURCE starts with")
    return head.format(min_words=script_gen.MIN_WORDS, max_words=script_gen.MAX_WORDS, hook_words=script_gen.HOOK_WORDS,
                       today=(today or datetime.now()).date().isoformat()).rstrip()


def _story_block(entry: dict[str, Any]) -> str:
    s = entry["story"]
    pillar = repository.PILLAR_LABELS.get(s.get("pillar"), "dark history") if s.get("pillar") else "dark history"
    lines = [f'=== STORY {s["id"]}: {s["title"]} ===', f"Content pillar: {pillar}",
             f"SOURCE ({s['source_name']}) — {s['title']}:", s["raw_text"][:script_gen.SOURCE_CHARS]]
    draft = _draft(entry)
    if entry["step"] == "fix" and draft is not None:
        lines += ["", script_gen.fix_prompt(draft, entry.get("problems") or []).rstrip()]
    elif entry["step"] == "facts_fix" and draft is not None:
        lines += ["", da_manual.FACTS_FIX.format(flagged="\n".join(f"- {f}" for f in entry.get("flagged") or []),
                                                 draft=script_gen.draft_json(draft)).rstrip()]
    return "\n".join(lines)


def _ranking_rules(n: int, today: datetime | None = None) -> str:
    """da_ranking.PROMPT's rules once (without its channel intro, said above, and its CANDIDATES tail)."""
    text = da_ranking.PROMPT
    _, _, text = text.partition("Make a RANKING Short:")
    text, _, _ = text.partition("\n\nCANDIDATES:")
    text = ("RANKINGS: for each RANKING below, make a RANKING Short from ONLY its own CANDIDATES: " + text.strip()
            .replace("pick the {count} strongest stories from the CANDIDATES below", "pick the {count} strongest stories from its CANDIDATES"))
    return text.format(count=n, hook_words=da_ranking.HOOK_WORDS, label_words=da_ranking.LABEL_WORDS,
                       min_words=da_ranking.ENTRY_WORDS[0], max_words=da_ranking.ENTRY_WORDS[1], max_total=da_ranking.MAX_WORDS,
                       today=(today or datetime.now()).date().isoformat()).rstrip()


def _ranking_block(group: dict[str, Any]) -> str:
    lines = [f'=== RANKING {group["k"]} ===', "CANDIDATES:"]
    for s in group["pool"]:
        text = re.sub(r"\s+", " ", s["raw_text"])[:da_ranking.SOURCE_CHARS]
        lines.append(f"[id {s['id']}] {s['title']} ({s['source_name']}, {s.get('pillar') or 'dark history'}): {text}")
    draft = _rdraft(group)
    if group["step"] == "fix" and draft is not None:
        lines += ["", da_ranking.FIX_PROMPT.format(problems="; ".join(group.get("problems") or []),
                                                   draft=draft.model_dump_json(exclude={"usable", "reject_reason"})).rstrip()]
    return "\n".join(lines)


WEEK_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence):
{"scripts": [ {"story": <id>, ...}, ... ], "rankings": [ {"ranking": 1, ...}, ... ]}
with one "scripts" entry per STORY ("story" is the id in its heading) and one "rankings" entry per RANKING ("ranking" is
its number). Leave out a list that has nothing to hold.

Each "scripts" entry is "story": <id> plus the fields of this example:
"""

RANKING_FORMAT = """Each "rankings" entry is shaped like this:
{"ranking": 1, "usable": true, "reject_reason": "", "title": "5 Disappearances That Still Make No Sense",
 "hook_text": "Five cases the files never closed.", "thumbnail_text": "5 CASES. NO ANSWERS",
 "entries": [{"story_id": 12, "rank": 5, "label": "Dyatlov Pass", "narration": "Number five. ...", "image_prompt": "cinematic dark moody photo of ..."}, ...]}
with exactly COUNT entries, ranks COUNT down to 1, every story_id a different CANDIDATE id of that ranking."""


def write_prompt(session: dict[str, Any], today: datetime | None = None) -> str:
    """One prompt for every story and ranking still to write or to fix (empty when there is none)."""
    stories = [t for t in session["stories"] if t["step"] in WRITING]
    groups = [g for g in session["groups"] if g["step"] in WRITING]
    if not stories and not groups:
        return ""
    parts = [_rules(today)]
    count = groups[0]["n"] if groups else da_ranking.count()
    if groups:
        parts.append(_ranking_rules(count, today))
    what = " and ".join(filter(None, [f"{len(stories)} STOR{'Y' if len(stories) == 1 else 'IES'}" if stories else "",
                                      f"{len(groups)} RANKING{'S' if len(groups) > 1 else ''}" if groups else ""]))
    note = f"There are {what} below."
    fixes = [f"story {t['story']['id']}" for t in stories if t["step"] != "write"] + [f"ranking {g['k']}" for g in groups if g["step"] != "write"]
    if fixes:
        note += (" " + ", ".join(fixes).capitalize()
                 + (" comes" if len(fixes) == 1 else " come") + " with your previous draft and what was wrong with it: fix every problem and return the whole draft again.")
    brief = ""
    try:
        from modules import da_insights

        brief = da_insights.learned().strip()
    except Exception:  # noqa: BLE001 - the lessons are a bonus
        brief = ""
    if brief:
        parts.append(brief)
    parts.append(note)
    parts += [_story_block(t) for t in stories] + [_ranking_block(g) for g in groups]
    example = da_manual.JSON_FORMAT.split("\n", 2)[2]  # the example object and the scenes note
    parts.append(WEEK_FORMAT + example)
    if groups:
        parts.append(RANKING_FORMAT.replace("COUNT", str(count)))
    return "\n\n".join(parts)


FACTS_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence):
{"checks": [ {"story": <id>, "unsupported": ["the statement - what is wrong"]}, {"story": <id>, "unsupported": []} ]}
with one entry per STORY ("story" is the id in its heading); an empty list when everything in that script is supported."""


def facts_prompt(session: dict[str, Any]) -> str:
    """One fact-check prompt for every story draft that passed the rules (empty when there is none)."""
    todo = [t for t in session["stories"] if t["step"] == "facts"]
    if not todo:
        return ""
    head, _, _ = da_manual.FACTS_PROMPT.partition("\n\nSOURCE ({source_name})")
    head = head.replace("You fact-check a script for Dark Outliers", f"You fact-check {len(todo)} scripts for Dark Outliers")
    head = head.replace("Every statement must be supported by the SOURCE below: the SOURCE is all the writer had.",
                        "Every statement of a SCRIPT must be supported by its own SOURCE: that SOURCE is all the writer had for it.")
    blocks = []
    for t in todo:
        s, draft = t["story"], _draft(t)
        blocks.append(f'=== STORY {s["id"]}: {s["title"]} ===\nSOURCE ({s["source_name"]}) — {s["title"]}:\n'
                      f'{s["raw_text"][:script_gen.SOURCE_CHARS]}\n\nSCRIPT:\n{script_gen.draft_json(draft)}')
    return head + "\n\n" + "\n\n".join(blocks) + "\n\n" + FACTS_FORMAT


# --------------------------------------------------------------------------- 2. answer, 3. facts


def _story_entry(session: dict[str, Any], story_id: int) -> dict[str, Any] | None:
    return next((t for t in session["stories"] if t["story"]["id"] == story_id), None)


def _group(session: dict[str, Any], k: int) -> dict[str, Any] | None:
    return next((g for g in session["groups"] if g["k"] == k), None)


def answer(text: str, out_dir: Path | None = None, db_path=None) -> dict[str, Any]:
    """The LLM's drafts: the rules check for each; a story that passes waits for the fact check, a ranking that
    passes is ready; a fail goes back for a fix. A story the LLM calls unusable is REJECTED in the bank."""
    session = _load(out_dir)
    found = llm_json.parse(text, WeekAnswer, WeekError)
    if not found.scripts and not found.rankings:
        raise WeekError('The answer has no "scripts" or "rankings": ask the LLM for the JSON described at the end of the prompt')
    seen = 0
    for item in found.scripts:
        entry = _story_entry(session, item.story)
        if entry is None or entry["step"] not in WRITING:
            continue
        seen += 1
        draft = ScriptDraft.model_validate(item.model_dump(exclude={"story"}))
        if not draft.usable:
            reason = draft.reject_reason.strip() or "no reason given"
            entry.update(step="rejected", draft=draft.model_dump(), problems=[reason], flagged=[])
            story = repository.get_story(entry["story"]["id"], db_path)
            if story and story["status"] == "NEW":
                repository.set_status(story["id"], "REJECTED", db_path, note=f"the LLM (week): {reason}"[:300])
            continue
        problems = script_gen.check(draft)
        entry.update(step="fix" if problems else "facts", draft=draft.model_dump(), problems=problems, flagged=[])
    for item in found.rankings:
        group = _group(session, item.ranking)
        if group is None or group["step"] not in WRITING:
            continue
        seen += 1
        draft = da_ranking.RankingDraft.model_validate(item.model_dump(exclude={"ranking"}))
        if not draft.usable:
            group.update(step="rejected", draft=draft.model_dump(), problems=[draft.reject_reason.strip() or "no reason given"])
            continue
        problems = da_ranking.check(draft, {s["id"] for s in group["pool"]}, group["n"])
        group.update(step="fix" if problems else "ready", draft=draft.model_dump(), problems=problems)
    if not seen:
        raise WeekError("None of the pasted scripts belongs to a story or ranking still to write (check the ids and numbers)")
    _save(session, out_dir)
    return view(out_dir)


def facts(text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The fact checker's answer: nothing flagged makes a draft ready, flags send it back for a fix."""
    session = _load(out_dir)
    found = llm_json.parse(text, WeekChecks, WeekError)
    seen = 0
    for item in found.checks:
        entry = _story_entry(session, item.story)
        if entry is None or entry["step"] != "facts":
            continue
        seen += 1
        flagged = [u.strip() for u in item.unsupported if u.strip()]
        entry.update(step="facts_fix" if flagged else "ready", flagged=flagged)
    if not seen:
        raise WeekError("None of the pasted checks belongs to a script waiting for its fact check (check the story ids)")
    _save(session, out_dir)
    return view(out_dir)


def drop(kind: str, i: int, out_dir: Path | None = None) -> dict[str, Any]:
    """Leave a story or a ranking out of this week (the stories stay NEW in the bank)."""
    session = _load(out_dir)
    entry = _story_entry(session, i) if kind == "story" else _group(session, i) if kind == "ranking" else None
    if entry is None:
        raise WeekError(f"No {kind} {i} in this week")
    if entry["step"] == "sent":
        raise WeekError("That script is already on Linux")
    entry.update(step="dropped")
    _save(session, out_dir)
    return view(out_dir)


# --------------------------------------------------------------------------- 4. send


def bundle(out_dir: Path | None = None) -> dict[str, Any]:
    """What goes to the hub: the ready stories with their drafts, the ready rankings with their candidate stories."""
    session = _load(out_dir)
    return {"stories": [{"story": t["story"], "draft": t["draft"], "week_i": t["story"]["id"]}
                        for t in session["stories"] if t["step"] == "ready"],
            "rankings": [{"draft": g["draft"], "pool": g["pool"], "n": g["n"], "week_k": g["k"]}
                         for g in session["groups"] if g["step"] == "ready"]}


def send(settings, out_dir: Path | None = None, db_path=None, deliver=None, local: bool | None = None) -> dict[str, Any]:
    """The ready scripts go to the hub (through Discord on the sending machine, straight into the bank on the hub
    itself). On the sending machine the stories the hub took become USED here, so this machine never tells them
    too; on the hub itself (`local`) the bank is the hub's and the scripts wait there as SCRIPT_READY."""
    from modules import channel_hub
    from trendclip import hub

    data = bundle(out_dir)
    if not data["stories"] and not data["rankings"]:
        raise WeekError("No script has passed its checks yet")
    deliver = deliver or channel_hub.queue_scripts
    if local is None:
        local = not hub.is_sender(settings)
    reply = deliver(settings, "darkarchives", data)
    session = _load(out_dir)
    at = datetime.now().replace(microsecond=0).isoformat()
    took = {(q.get("kind"), q.get("week_i")): q for q in reply.get("queued", [])}
    skipped = {(s.get("kind"), s.get("week_i")): s.get("why", "") for s in reply.get("skipped", [])}
    for t in session["stories"]:
        key = ("story", t["story"]["id"])
        if t["step"] != "ready":
            continue
        if key in took:
            t.update(step="sent", sent={"at": at, "hub_id": took[key].get("hub_id")})
            if not local:
                repository.set_status(t["story"]["id"], "USED", db_path, note=f"sent to Linux for later (week, hub story {took[key].get('hub_id')})")
        elif key in skipped:
            t["problems"] = [f"Linux did not take it: {skipped[key]}"]
    for g in session["groups"]:
        key = ("ranking", g["k"])
        if g["step"] != "ready":
            continue
        if key in took:
            g.update(step="sent", sent={"at": at, "hub_id": took[key].get("hub_id")})
            for e in g["draft"]["entries"] if not local else ():
                repository.set_status(e["story_id"], "USED", db_path, note=f"sent to Linux in a ranking for later (week {g['k']})")
        elif key in skipped:
            g["problems"] = [f"Linux did not take it: {skipped[key]}"]
    session["last_send"] = {"at": at, **reply}
    _save(session, out_dir)
    return {**view(out_dir), "reply": reply}


# --------------------------------------------------------------------------- what the panel shows


def _preview(entry: dict[str, Any]) -> dict[str, Any] | None:
    draft = _draft(entry)
    if draft is None:
        return None
    return {"title": draft.title, "hook": draft.hook_text, "cover_text": draft.thumbnail_text,
            "words": script_gen.words(script_gen.spoken(draft)), "scenes": len(draft.scenes),
            "narration": script_gen.spoken(draft)}


def _rpreview(group: dict[str, Any]) -> dict[str, Any] | None:
    draft = _rdraft(group)
    if draft is None:
        return None
    titles = {s["id"]: s["title"] for s in group["pool"]}
    return {"title": draft.title, "hook": draft.hook_text, "cover_text": draft.thumbnail_text,
            "words": script_gen.words(draft.hook_text) + sum(script_gen.words(e.narration) for e in draft.entries),
            "entries": [{"rank": e.rank, "label": e.label, "title": titles.get(e.story_id, f"story {e.story_id}")}
                        for e in sorted(draft.entries, key=lambda e: e.rank)]}


def view(out_dir: Path | None = None) -> dict[str, Any]:
    session = _load(out_dir)
    stories = [{"id": t["story"]["id"], "title": t["story"]["title"], "source": t["story"]["source_name"],
                "url": t["story"].get("url") or "", "pillar": t["story"].get("pillar") or "", "score": t["story"].get("score") or 0,
                "step": t["step"], "problems": t.get("problems") or [], "flagged": t.get("flagged") or [],
                "preview": _preview(t), "sent": t.get("sent")} for t in session["stories"]]
    groups = [{"k": g["k"], "n": g["n"], "pool": [{"id": s["id"], "title": s["title"]} for s in g["pool"]], "step": g["step"],
               "problems": g.get("problems") or [], "preview": _rpreview(g), "sent": g.get("sent")} for g in session["groups"]]
    steps = [t["step"] for t in stories] + [g["step"] for g in groups]
    write = write_prompt(session)
    check = facts_prompt(session)
    return {"created_at": session["created_at"], "count": session["count"], "rankings": session["rankings"],
            "stories": stories, "groups": groups,
            "to_write": sum(1 for s in steps if s in WRITING), "to_check": steps.count("facts"), "ready": steps.count("ready"),
            "sent": steps.count("sent"), "write_prompt": write, "facts_prompt": check,
            "write_chars": len(write), "facts_chars": len(check), "last_send": session.get("last_send")}


def exists(out_dir: Path | None = None) -> bool:
    return _path(out_dir).is_file()
