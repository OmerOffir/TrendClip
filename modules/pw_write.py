"""Price Tag Why scripts written with any LLM by hand (the dashboard's "Write it myself" and "Write a week"), plus the
topic list they draw from. No Gemini: the free tier is too small to spend on a new channel.

  1. start    picks the next topic(s) from the list (or the one given) and gives the writing prompt
  2. answer   you paste the LLM's JSON; every script goes through pw_channel.check_script and the format rules here.
              Problems give a fix prompt (the prompt, the problems, the drafts) to paste back
  3. facts    passing drafts give the fact-check prompt (better in a second LLM that can open the links); you paste its
              JSON. A flagged claim gives a fix prompt again
  4. queue    both checks passed: the scripts go to output/pricetagwhy/queue/, where the daily slots take them

A session is one file, output/pricetagwhy/write/<one|week>.json, so it survives a restart.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from modules import pw_channel
from trendclip import llm_json

KINDS = ("one", "week")
DEFAULT_COUNT = 7
MIN_WORDS, MAX_WORDS = 90, 135  # about 35-50 s with the channel's voice
SCENE_KINDS = ("tag", "board", "number", "compare", "chart", "map", "photo", "follow")
MIDDLE = ("board", "number", "compare", "chart", "map", "photo")  # what goes between the tag and the follow line
PILLARS = ("everyday prices", "countries and money", "business tricks", "money history")
# icons a script may put on a board (Noto emoji names, modules.bw_fx.ICONS and modules.pw_render.ICONS)
ICONS = ("cup_straw", "tea", "tv", "moon", "receipt", "label", "store", "money_wings", "bank", "handshake", "megaphone",
         "family", "dollar", "credit_card", "shopping_cart", "slot_machine", "dice", "tulip", "printer", "gym", "mobile",
         "party", "sparkles", "eyes", "money_bag", "cash", "coin", "gem", "oil", "chart_up", "chart_down", "crown", "house",
         "factory", "government", "clock", "calendar", "hourglass", "question", "thinking", "surprised", "people", "truck",
         "ship", "plane", "globe", "lock", "key", "check", "cross", "forbidden", "fire", "explosion", "scroll", "castle")
SEED_TOPICS = (  # the brief's first Shorts first, then the pillars' examples
    ("Why is AriZona Iced Tea still 99 cents?", "everyday prices"),
    ("Why can't a country just print money to get rich?", "countries and money"),
    ("Why do casinos have no clocks or windows?", "business tricks"),
    ("Why do prices end in .99?", "everyday prices"),
    ("Why is Monaco so rich with no natural resources?", "countries and money"),
    ("Why is printer ink so expensive?", "everyday prices"),
    ("Why do gyms want you to sign up in January?", "business tricks"),
    ("Why is it so hard to cancel a subscription?", "business tricks"),
    ("Why do stores give you loyalty cards?", "business tricks"),
    ("Why is the Swiss franc so strong?", "countries and money"),
    ("Why did one tulip bulb cost as much as a house?", "money history"),
    ("Why did Zimbabwe print a 100-trillion-dollar note?", "money history"),
)


class WriteError(ValueError):
    pass


# --------------------------------------------------------------------------- topics


def root(out_dir: Path | None = None) -> Path:
    return pw_channel.out_root(out_dir)


def _topics_path(out_dir: Path | None = None) -> Path:
    return root(out_dir) / "topics.json"


def _norm(question: str) -> str:
    return re.sub(r"[^a-z0-9]", "", question.lower())


def topics(out_dir: Path | None = None) -> list[dict[str, Any]]:
    """The topic list ({question, pillar, used, added}), seeded from the brief the first time; a topic a queued or
    made script already answers counts as used."""
    path = _topics_path(out_dir)
    if path.is_file():
        found = json.loads(path.read_text(encoding="utf-8"))
    else:
        found = [{"question": q, "pillar": p, "used": False, "added": "brief"} for q, p in SEED_TOPICS]
    answered = {_norm(s.get("title", "")) for s in pw_channel.scripts(out_dir)} | {_norm(s.get("question", "")) for s in pw_channel.scripts(out_dir)}
    for t in found:
        t["used"] = bool(t.get("used")) or _norm(t["question"]) in answered
    return found


def save_topics(found: list[dict[str, Any]], out_dir: Path | None = None) -> None:
    path = _topics_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def add_topic(question: str, pillar: str = "", out_dir: Path | None = None) -> dict[str, Any]:
    question = " ".join(question.split())
    if len(question) < 8 or not question.endswith("?"):
        raise WriteError("A topic is a question ending in ?")
    if pillar and pillar not in PILLARS:
        raise WriteError(f"The pillar is one of: {', '.join(PILLARS)}")
    found = topics(out_dir)
    if any(_norm(t["question"]) == _norm(question) for t in found):
        raise WriteError("That question is already on the list")
    item = {"question": question, "pillar": pillar, "used": False, "added": datetime.now().strftime("%Y-%m-%d")}
    save_topics([*found, item], out_dir)
    return item


def remove_topic(question: str, out_dir: Path | None = None) -> None:
    save_topics([t for t in topics(out_dir) if _norm(t["question"]) != _norm(question)], out_dir)


def next_topics(count: int, out_dir: Path | None = None) -> list[dict[str, Any]]:
    """The next unused topics, taking the pillars in turn so the channel never looks mass-produced."""
    left = [t for t in topics(out_dir) if not t["used"]]
    picked: list[dict[str, Any]] = []
    while left and len(picked) < count:
        last = picked[-1]["pillar"] if picked else ""
        nxt = next((t for t in left if t.get("pillar") != last), left[0])
        picked.append(nxt)
        left.remove(nxt)
    return picked


# --------------------------------------------------------------------------- the format rules


def format_problems(script: dict) -> list[str]:
    """What the renderer and the brief need beyond pw_channel.check_script."""
    problems = []
    scenes = script.get("scenes") or []
    n = pw_channel.words(pw_channel.narration(script))
    if not MIN_WORDS <= n <= MAX_WORDS:
        problems.append(f"{n} words of narration (wanted {MIN_WORDS}-{MAX_WORDS}, about 35-50 s)")
    for k, s in enumerate(scenes, 1):
        if s.get("kind") not in SCENE_KINDS:
            problems.append(f"scene {k}: kind must be one of {', '.join(SCENE_KINDS)}")
        if s.get("kind") != "follow" and not str(s.get("narration") or "").strip():
            problems.append(f"scene {k}: no narration")
        if s.get("kind") == "tag":
            tag = s.get("tag") or {}
            if not tag.get("question") or not tag.get("price"):
                problems.append(f"scene {k}: the tag needs question (1-3 short lines) and price")
        if s.get("kind") == "board":
            lines = (s.get("board") or {}).get("write") or []
            if not 2 <= len(lines) <= 4:
                problems.append(f"scene {k}: a board writes 2-4 lines (a title first)")
            for line in lines:
                text = line.get("text", "") if isinstance(line, dict) else str(line)
                if len(text) > 26:
                    problems.append(f"scene {k}: board line too long for the board (26 characters at most): {text!r}")
        problems += [f"scene {k}: {p}" for p in scene_problems(s)]
        for el in s.get("elements") or []:
            if el.get("kind") == "icon" and el.get("icon") not in ICONS:
                problems.append(f"scene {k}: unknown icon {el.get('icon')!r}")
    if scenes and scenes[-1].get("kind") == "follow" and "follow" not in str(scenes[-1].get("narration", "")).lower():
        problems.append("the last scene says the follow line (\"Follow for tomorrow's why.\")")
    middle = [s.get("kind") for s in scenes[1:-1]]
    if len(middle) < 3:
        problems.append("2-3 reasons and a twist need at least 3 scenes between the tag and the follow line")
    if middle and all(k == "board" for k in middle):
        problems.append("vary the pictures: at least one number, compare, chart, map or photo scene, not only boards")
    for src in script.get("sources") or []:
        if not str(src.get("url", "")).startswith("http"):
            problems.append(f"source without a link: {src.get('title', '')[:50]}")
    return problems


def _short(text: Any, most: int) -> bool:
    return 0 < len(str(text or "")) <= most


def scene_problems(s: dict) -> list[str]:
    """What one of the drawn, map or photo scenes needs to be drawn well."""
    kind, o = s.get("kind"), s.get(s.get("kind") or "") or {}
    out = []
    if kind == "number":
        if not _short(o.get("to"), 10):
            out.append("number.to is the big figure, 10 characters at most (\"$2.27\", \"100 trillion\" won't fit: \"$100T\")")
        if o.get("from") and not _short(o.get("from"), 10):
            out.append("number.from is 10 characters at most")
    elif kind == "compare":
        for side in ("left", "right"):
            x = o.get(side) or {}
            if not _short(x.get("label"), 14) or not _short(x.get("price"), 8):
                out.append(f"compare.{side} needs label (14 characters at most) and price (8 at most)")
        if o.get("winner", "left") not in ("left", "right"):
            out.append("compare.winner is left or right")
    elif kind == "chart":
        lines = o.get("lines") or []
        if not 1 <= len(lines) <= 2:
            out.append("chart.lines has 1 or 2 lines")
        for ln in lines:
            pts = ln.get("points") or []
            if not 2 <= len(pts) <= 8 or not all(isinstance(p, list) and len(p) >= 2 and isinstance(p[1], (int, float)) for p in pts):
                out.append("each chart line has 2-8 points [label, number]")
    elif kind == "map":
        focus = s.get("focus") or []
        if not 1 <= len(focus) <= 4:
            out.append("a map scene names 1-4 places in focus")
        else:
            try:
                from modules import bw_geo

                out += [f"no such place on the map: {name}" for name in focus if bw_geo.resolve(str(name)) is None]
            except Exception:  # noqa: BLE001 - no map data here: the hub checks it when it draws
                pass
    elif kind == "photo":
        if not str(s.get("photo") or "").strip():
            out.append("a photo scene names the English Wikipedia article whose picture it shows (photo)")
    return out


def problems_of(script: dict) -> list[str]:
    return pw_channel.check_script(script) + format_problems(script)


# --------------------------------------------------------------------------- prompts


EXAMPLE = pw_channel.SCRIPTS_DIR / "001-arizona-99c.json"  # the first, hand-checked script


def _example() -> str:
    return EXAMPLE.read_text(encoding="utf-8").strip() if EXAMPLE.is_file() else "{}"


def rules() -> str:
    return f"""Price Tag Why is a YouTube Shorts channel: whiteboard-animated answers to the money questions everyone
wonders about, for curious adults (18-45, US/UK/CA/AU) who are not finance experts. Promise: "In under a minute,
you'll finally understand why money works the way it does."

Each script is ONE JSON object shaped exactly like the example below:
- {MIN_WORDS}-{MAX_WORDS} words of narration in all (35-50 seconds), short spoken sentences, numbers written as a
  narrator says them ("99 cents", "1992").
- scene 1, kind "tag": the question plus a shocking fact in the first 2 seconds. tag.question is 1-3 short lines
  written on a hand-drawn price tag; tag.price is the big red number above it (a price, an amount, a year); tag.note
  a few words under it.
- then 3-5 scenes: 2-3 reasons, each drawn on the whiteboard, then the twist. Mix the kinds so no two videos look
  alike, and use at least one that isn't a board:
  * "board": board.write is 2-4 lines, the first a short title ("Reason 1: ..."), each at most 26 characters. A line
    may be {{"text": ..., "color": "green" | "red", "strike": true}}: green for money in, red for cost, strike crosses
    it out. Up to 2 elements of kind "icon" per board, at x 300 and x 780, y 1230, each with a "cue" word from the
    narration. Icons: {", ".join(ICONS)}.
  * "number": a big figure that counts up as it is said. {{"kind": "number", "narration": ..., "number": {{"label":
    "If it rose with inflation", "from": "99¢", "to": "$2.27", "note": "BLS inflation calculator"}}}} ("from" may be
    left out; figures at most 10 characters, so "$100T" not "100 trillion dollars").
  * "compare": two price tags side by side, the winner circled. {{"kind": "compare", "narration": ..., "compare":
    {{"title": "Same tea, two prices", "left": {{"label": "On the can", "price": "99¢"}}, "right": {{"label":
    "Some stores", "price": "$1.59"}}, "winner": "left"}}}} (labels at most 14 characters, prices at most 8).
  * "chart": a hand-drawn line chart. {{"kind": "chart", "narration": ..., "chart": {{"title": "...", "unit": "$",
    "lines": [{{"label": "Bread", "color": "red", "points": [["1990", 0.7], ["2020", 2.5]], "end": "$2.50"}}]}}}}
    (1-2 lines, 2-8 points each, real numbers from the sources).
  * "map": a satellite map of real places. {{"kind": "map", "narration": ..., "focus": ["Monaco"], "headline":
    "Smaller than Central Park", "elements": [{{"kind": "highlight", "place": "Monaco", "cue": "Monaco"}}]}}.
  * "photo": the lead picture of an English Wikipedia article (free licence, credited automatically).
    {{"kind": "photo", "narration": ..., "photo": "Monte Carlo Casino", "caption": "Monte Carlo Casino"}}.
- last scene, kind "follow", narration exactly "Follow for tomorrow's why."
- "sources": 2 or more real pages with working links (news, official, academic; Wikipedia only to find others).
  "claims": every fact the narration states, each with the numbers of 2 or more sources that support it.
- "description": 2-3 sentences with the short answer. "hashtags": 2-3 without #. "pillar": one of
  {", ".join(PILLARS)}.
Rules: explain, never advise (no "buy", "invest", "you should"); nothing a source does not say; an original script
(no copying sentences from the sources); a different structure and tone from one video to the next.

The example (a finished script):
{_example()}"""


def write_prompt(session: dict[str, Any]) -> str:
    qs = "\n".join(f"{k}. {t['question']}" + (f" (pillar: {t['pillar']})" if t.get("pillar") else "")
                   for k, t in enumerate(session["topics"], 1))
    many = len(session["topics"]) > 1
    return (f"{rules()}\n\nWrite {'one script for each of these questions' if many else 'the script for this question'}:\n{qs}\n\n"
            f"Answer with JSON only: {{\"scripts\": [ ... ]}}, {'one object per question, in this order' if many else 'one object'}.")


def fix_prompt(session: dict[str, Any]) -> str:
    lines = [f"Script {k}: " + "; ".join(p) for k, p in sorted(session["problems"].items(), key=lambda kv: int(kv[0])) if p]
    return (f"{write_prompt(session)}\n\nYour last answer broke these rules:\n" + "\n".join(lines)
            + "\n\nFix only that and answer with the whole JSON again ({\"scripts\": [...]}, every script, in order). Your last answer:\n"
            + json.dumps({"scripts": session["drafts"]}, ensure_ascii=False))


def facts_prompt(session: dict[str, Any]) -> str:
    blocks = []
    for k, d in enumerate(session["drafts"], 1):
        src = "\n".join(f"  [{n}] {s.get('title', '')}: {s.get('url', '')}" for n, s in enumerate(d.get("sources") or [], 1))
        claims = "\n".join(f"  - {c.get('claim', '')} (sources {', '.join(map(str, c.get('sources') or []))})" for c in d.get("claims") or [])
        blocks.append(f"Script {k}: {d.get('title', '')}\nNarration: {pw_channel.narration(d)}\nSources:\n{src}\nClaims:\n{claims}")
    return ("You are a fact checker for a money-explainer YouTube channel. Open every source link. For each script, list "
            "every statement in the narration that its listed sources do NOT clearly support (a wrong number, a year, a "
            "cause the sources don't give, a dead link, a claim with fewer than two supporting sources), and any advice "
            "to buy or invest. Be strict: if you cannot open a link, say so.\n\n" + "\n\n".join(blocks)
            + '\n\nAnswer with JSON only: {"checks": [{"script": 1, "unsupported": ["..."]}, ...]}, one entry per script '
              "(an empty list when everything is supported).")


# --------------------------------------------------------------------------- the session


class Answer(BaseModel):
    scripts: list[dict[str, Any]] = Field(min_length=1)


class Check(BaseModel):
    script: int
    unsupported: list[str] = Field(default_factory=list)


class Checks(BaseModel):
    checks: list[Check]


def _path(kind: str, out_dir: Path | None = None) -> Path:
    if kind not in KINDS:
        raise WriteError(f"No such writing session: {kind}")
    return root(out_dir) / "write" / f"{kind}.json"


def load(kind: str, out_dir: Path | None = None) -> dict[str, Any] | None:
    path = _path(kind, out_dir)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _save(session: dict[str, Any], out_dir: Path | None = None) -> dict[str, Any]:
    path = _path(session["kind"], out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return view(session["kind"], out_dir)


def start(kind: str, question: str = "", count: int = DEFAULT_COUNT, out_dir: Path | None = None) -> dict[str, Any]:
    """A new session: `question` (or the next topic) for "one", the next `count` topics for "week"."""
    if kind == "one":
        picked = [{"question": " ".join(question.split()), "pillar": ""}] if question.strip() else next_topics(1, out_dir)
    else:
        picked = next_topics(max(1, min(14, count)), out_dir)
    if not picked:
        raise WriteError("No topics left: add questions in the Topics tab")
    session = {"kind": kind, "topics": picked, "step": "write", "drafts": [], "problems": {}, "flagged": {},
               "started": datetime.now().isoformat(timespec="minutes")}
    return _save(session, out_dir)


def answer(kind: str, text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The LLM's scripts: the rules check; all passing moves to the fact check."""
    session = load(kind, out_dir)
    if session is None:
        raise WriteError("Start a session first")
    stripped = text.strip()
    if stripped.startswith("{") and '"scripts"' not in stripped[:200]:
        stripped = '{"scripts": [' + stripped + "]}"  # one script pasted on its own
    parsed = llm_json.parse(stripped, Answer, WriteError)
    session["drafts"] = parsed.scripts
    session["problems"] = {str(k): problems_of(d) for k, d in enumerate(parsed.scripts, 1)}
    if len(parsed.scripts) != len(session["topics"]):
        session["problems"]["0"] = [f"{len(parsed.scripts)} scripts for {len(session['topics'])} questions"]
    session["flagged"] = {}
    session["step"] = "facts" if not any(session["problems"].values()) else "fix"
    return _save(session, out_dir)


def facts(kind: str, text: str, out_dir: Path | None = None) -> dict[str, Any]:
    session = load(kind, out_dir)
    if session is None or session["step"] != "facts":
        raise WriteError("The scripts must pass the rules check first")
    parsed = llm_json.parse(text, Checks, WriteError)
    flagged = {str(c.script): c.unsupported for c in parsed.checks if c.unsupported}
    session["flagged"] = flagged
    if flagged:
        session["problems"] = {k: [f"the fact checker found unsupported: {u}" for u in v] for k, v in flagged.items()}
        session["step"] = "fix"
    else:
        session["step"] = "ready"
    return _save(session, out_dir)


def drop(kind: str, i: int, out_dir: Path | None = None) -> dict[str, Any]:
    """Leave script i (1-based) out of the session, its topic too."""
    session = load(kind, out_dir)
    if session is None or not 1 <= i <= len(session["drafts"]):
        raise WriteError("No such script")
    session["drafts"].pop(i - 1)
    if i <= len(session["topics"]):
        session["topics"].pop(i - 1)
    session["problems"] = {str(k): problems_of(d) for k, d in enumerate(session["drafts"], 1)}
    session["flagged"] = {}
    session["step"] = "facts" if session["drafts"] and not any(session["problems"].values()) else "fix"
    return _save(session, out_dir)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "script"


def queue(kind: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The checked scripts into output/pricetagwhy/queue/ (the slots take them by name, so in this order)."""
    session = load(kind, out_dir)
    if session is None or session["step"] != "ready":
        raise WriteError("Both checks must pass first")
    folder = pw_channel.queue_dir(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    saved = []
    for k, d in enumerate(session["drafts"], 1):
        d = {**d, "checked": {"rules": True, "facts": "pasted fact check", "at": stamp}}
        path = folder / f"{stamp}-{k:02d}-{slug(d.get('title', ''))}.json"
        path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        saved.append(path.name)
    found = topics(out_dir)
    done = {_norm(t["question"]) for t in session["topics"]}
    for t in found:
        if _norm(t["question"]) in done:
            t["used"] = True
    listed = {_norm(t["question"]) for t in found}
    found += [{**t, "used": True, "added": stamp[:8]} for t in session["topics"] if _norm(t["question"]) not in listed]
    save_topics(found, out_dir)
    _path(kind, out_dir).unlink(missing_ok=True)
    return {"queued": saved, "waiting": len(pw_channel.queued(out_dir))}


def view(kind: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The tab's state: the step, the prompt to copy, the drafts with their problems."""
    session = load(kind, out_dir)
    if session is None:
        return {"kind": kind, "step": "none", "topics": next_topics(1 if kind == "one" else DEFAULT_COUNT, out_dir)}
    prompt = {"write": write_prompt, "fix": fix_prompt, "facts": facts_prompt}.get(session["step"])
    return {"kind": kind, "step": session["step"], "topics": session["topics"], "prompt": prompt(session) if prompt else "",
            "drafts": [{"title": d.get("title", ""), "words": pw_channel.words(pw_channel.narration(d)),
                        "sources": len(d.get("sources") or []), "problems": session["problems"].get(str(k), []),
                        "flagged": session["flagged"].get(str(k), [])} for k, d in enumerate(session["drafts"], 1)],
            "general": session["problems"].get("0", []), "started": session.get("started", "")}
