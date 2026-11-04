"""Borderline Why "What if" Shorts ("What If Alaska Was Still Russian?"), written by your own LLM on the dashboard.

A what-if can't be checked against sources the way the channel's "why" Shorts are, so it has its own rules: the
real history up to the turning point told straight, then one plausible guess told as a guess, the territory
changing hands shown with "transfer" elements, and the description saying it is a what-if. The steps:

  1. start    you type the question; the panel gives the writing prompt (no Gemini, no research)
  2. answer   you paste the LLM's JSON; the code runs the channel's rules check (length, scenes, places on the
              map, flags, icons; no claims or sources) plus the what-if rules. A failed check gives a fix prompt
  3. queue    the passed script goes to output/borderlinewhy/queue/, so the next slot makes it (a queued script
              goes before the topic list)

Each session is output/borderlinewhy/whatif/<id>.json.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from modules import bw_channel, bw_fx, bw_manual, bw_script, bw_topics
from modules.bw_script import Draft, Limits, Research
from trendclip import llm_json

KIND = "whatif"
ATTEMPTS = "attempts"  # the "N times X tried Y" format on the war map (a 6M-view Short, 2026-11-02)
FORMATS = (KIND, ATTEMPTS)
SESSIONS_SHOWN = 10
LIMITS = Limits(min_claims=0, min_cited=0)
# an attempts Short runs about 75-90 s: each attempt is a fresh mini-story, so viewers stay for the next one
ATTEMPT_WORDS = (150, 195)
ATTEMPT_LIMITS = Limits(min_words=ATTEMPT_WORDS[0], max_words=ATTEMPT_WORDS[1], min_scenes=8, max_scenes=32, max_cards=0,
                        spoken_words=215, max_photos=4, max_boards=1, min_claims=0, min_cited=0)
FORMAT = bw_manual.JSON_FORMAT.replace(
    '  "claims": [{"claim": "One fact the narration states.", "source_id": 1, "quote": "the exact words copied from source 1"}],',
    '  "claims": [],')
HEDGES = re.compile(r"\b(might|could|would|maybe|perhaps|probably|possibly|imagine|guess|nobody knows|no one knows)\b", re.I)


class WhatIfError(ValueError):
    pass


PROMPT = """You write a YouTube Short for Borderline Why: geography and history told with animated maps. This one is a
WHAT IF episode: "{question}"

THE NARRATION ({min_words}-{max_words} words, about 35-45 seconds read aloud; with the mascot the Short is 45-55 seconds and never over 60:
short Shorts get watched to the end and replayed)
- Start straight away with the question or the real turning point. No greeting, no "Did you know", no "Let's dive in".
- First, the real history up to the turning point (about a third): who owned what, when, and why it changed. Only
  well-known facts you are sure of; never invent a number, a date, a name or a quote.
- Then the what-if: one clear, plausible chain of consequences, told as a guess, never as fact ("might", "would
  probably", "nobody can know, but..."). Keep it grounded in the real geography and the situation of the time.
- Show the map changing: a "transfer" element for every place that changes hands, the real handover and the
  imagined one.
- The last sentence comes back to the real world in a few memorable words ("Instead, Alaska became America's
  biggest state."). No call to action, no "subscribe".
- Respectful: no jokes about real peoples, no cheering for a side in today's disputes or wars. If the question
  can't be told that way, set usable to false and say why.
- Simple, natural, spoken English. Short connected sentences (at most {max_sentence} words). Numbers as digits.

THE TITLE: the what-if question, under 45 characters (the feed cuts longer ones), starting with "What if" ("What If
Alaska Was Still Russian?").
cover_text: 3-7 words for the first frame. description: 2-3 sentences saying what really happened and what the
video imagines. "claims": leave it an empty list (a what-if has no sources to quote).

""" + bw_script.HOOK_RULES + bw_script.SCENE_RULES + """Today is {today}. Write in English.
"""


ATTEMPTS_PROMPT = """You write a YouTube Short for Borderline Why: geography and history told with animated maps. This one is
an ATTEMPTS episode on a war map: "{question}"

The format: a list of real attempts (invasions, purchases, plots, failed plans), each one a fast mini-story on the
map, told in time order and counted ("Attempt 1...", "The second try...", "Then, in 1838..."). Each one ends on why it
failed, in a short dry punchline ("Winter did the rest.", "Nobody showed up."). Viewers stay for the next attempt.

THE NARRATION ({min_words}-{max_words} words, about 70-85 seconds; with the mascot never over 90 seconds)
- Scene 1 names the count and the surprise at once ("The US tried to take Canada 4 times."). No greeting, no "Did
  you know", no "Let's dive in".
- Then 3-5 attempts, about 15-20 seconds each: when, who, the plan, what happened on the ground, why it failed.
- Only well-known facts you are sure of: real dates, real battles, real people. Never invent a number, a name or a
  quote; when unsure, leave it out. The last attempt can be the strangest or the closest call.
- The last sentence closes the list in a few memorable words ("4 tries, and the border never moved."). No call to
  action, no "subscribe".
- Respectful: no jokes about real peoples, no cheering for a side in today's disputes or wars.
- Simple, natural, spoken English. Short connected sentences (at most {max_sentence} words). Numbers as digits.

THE WAR MAP (this Short is drawn on it: a pale paper map, each side in one solid colour)
- Color "a" is the attacking side, "b" the defending side, the same all video. Every map scene fills both sides with
  highlights (a whole country, or the states/provinces that side held then) so who owns what reads at a glance.
- Each attempt opens on a map scene with "year" ("1775", "1812", "1838-39"): it slams in big, then stays as a tag.
- The armies move: an "army" element marches from its "place" to "to" (cities or states/provinces near the front)
  with a block of little flags ("flag", e.g. "Flag of the United States.svg") or, with no flag, round tokens in the
  side's colour; "count" 3-9 units. Use 1-3 armies per attempt (two at once for a two-pronged attack).
- A "battle" element at a place (a city or the battle's town) is the clash: the attacker's "flag" and the defender's
  "from_flag" shove at each other while bursts go off; "text" can name it ("QUEENSTON HEIGHTS"). One per attempt.
- A "transfer" for land that really changed hands, a "label" or "pin" for a city the story names, an "icon" where it
  helps (fire for a burned city, ship, horse, cannon if listed).
- Keep the camera on the front: "focus" is the 1-3 states/provinces or cities where the fighting is, not whole
  continents.
- Fast: a map scene is ONE sentence (6-14 words), so the picture changes every 3-4 seconds, and every scene brings
  something new (an army, a battle, a transfer, a label). Two things can happen at once in a scene.
- No cards. Photos only for a famous face or place (0-3), a board only for a plan worth writing out (0-1).

THE TITLE: under 50 characters, the count and the surprise ("4 Times the US Tried to Take Canada").
cover_text: 3-6 words for the first frame. description: 2-3 sentences saying what the attempts were. "claims": leave
it an empty list.

""" + bw_script.HOOK_RULES + bw_script.SCENE_RULES + """Today is {today}. Write in English.
"""


def root(out_dir: Path | None = None) -> Path:
    return bw_channel.out_root(out_dir) / "whatif"


def _path(sid: str, out_dir: Path | None = None) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", sid or ""):
        raise WhatIfError(f"No what-if {sid!r}")
    return root(out_dir) / f"{sid}.json"


def _load(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    path = _path(sid, out_dir)
    if not path.is_file():
        raise WhatIfError(f"No what-if {sid!r}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(sid: str, state: dict[str, Any], out_dir: Path | None = None) -> None:
    path = _path(sid, out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _research(question: str) -> Research:
    return Research(bw_topics.Topic(question=question, article="", source="whatif", series=""), "", [])


def clean_question(text: str, fmt: str = KIND) -> str:
    q = re.sub(r"\s+", " ", text).strip()
    if not q:
        raise WhatIfError("Type the what-if question")
    if fmt == ATTEMPTS:
        return q.rstrip(".")
    if not re.match(r"what if\b", q, re.I):
        q = "What if " + q
    return q if q.endswith("?") else q.rstrip(".!") + "?"


def write_prompt(question: str, today: str = "", fmt: str = KIND) -> str:
    attempts = fmt == ATTEMPTS
    return (ATTEMPTS_PROMPT if attempts else PROMPT).format(
        question=question, min_words=ATTEMPT_WORDS[0] if attempts else bw_script.MIN_WORDS,
        max_words=ATTEMPT_WORDS[1] if attempts else bw_script.MAX_WORDS,
                         max_sentence=bw_script.MAX_SENTENCE_WORDS, min_scenes=ATTEMPT_LIMITS.min_scenes if attempts else bw_script.MIN_SCENES,
                         max_scenes=ATTEMPT_LIMITS.max_scenes if attempts else bw_script.MAX_SCENES,
                         max_cards=0 if attempts else bw_script.MAX_CARDS, icons=", ".join(bw_fx.icon_names()), icon_count="6-10 in the video",
                         stamp_count="one in the video", mascot_lines=bw_script.MASCOT_LINES, photo_count=bw_script.PHOTO_COUNT,
                         board_count=bw_script.BOARD_COUNT, today=today or datetime.now().date().isoformat())


def check_attempts(draft: Draft, question: str, find=None) -> list[str]:
    """The channel's rules (no claims or sources) and the attempts format's own: years, armies and battles."""
    problems = bw_script.check(draft, _research(question), **({"find": find} if find else {}), limits=ATTEMPT_LIMITS)
    if not draft.title.strip() or len(draft.title) > 55:
        problems.append("the title is missing or over 50 characters")
    maps = [s for s in draft.scenes if s.kind.strip().lower() == "map"]
    years = [s.year for s in maps if s.year.strip()]
    if len(years) < 3:
        problems.append(f"only {len(years)} map scenes have a \"year\" (each attempt opens on one: 3-5 in the video)")
    els = [e for s in maps for e in s.elements]
    if sum(e.kind == "army" for e in els) < 3:
        problems.append("fewer than 3 army elements (the armies must march on the map: 1-3 per attempt)")
    if not any(e.kind == "battle" for e in els):
        problems.append("no battle element (one clash per attempt)")
    sides = {e.color for e in els if e.kind == "highlight"}
    if not {"a", "b"} <= sides:
        problems.append('fill both sides with highlights: color "a" for the attacker, "b" for the defender')
    return problems


def check(draft: Draft, question: str, find=None) -> list[str]:
    """The channel's rules (no claims or sources) and the what-if's own."""
    problems = bw_script.check(draft, _research(question), **({"find": find} if find else {}), limits=LIMITS)
    if not re.match(r"\s*what if\b", draft.title, re.I):
        problems.append('the title must start with "What if"')
    text = bw_script.narration(draft)
    if not HEDGES.search(text):
        problems.append('the what-if part must be told as a guess ("might", "would probably", "nobody knows")')
    if not any(e.kind == "transfer" for s in draft.scenes for e in s.elements):
        problems.append("show the territory changing hands with at least one transfer element")
    return problems


# --------------------------------------------------------------------------- the steps


def start(question: str, out_dir: Path | None = None, fmt: str = KIND) -> dict[str, Any]:
    if fmt not in FORMATS:
        raise WhatIfError(f"Unknown format {fmt!r}")
    q = clean_question(question, fmt)
    sid = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")[:60] or "what-if"
    _save(sid, {"question": q, "format": fmt, "step": "write", "draft": None, "problems": [],
                "created_at": datetime.now().replace(microsecond=0).isoformat()}, out_dir)
    return view(sid, out_dir)


def answer(sid: str, text: str, out_dir: Path | None = None, find=None) -> dict[str, Any]:
    state = _load(sid, out_dir)
    draft = llm_json.parse(text, Draft, WhatIfError)
    if not draft.usable:
        state.update(step="rejected", draft=draft.model_dump(), problems=[draft.reject_reason.strip() or "no reason given"])
    else:
        problems = (check_attempts if state.get("format") == ATTEMPTS else check)(draft, state["question"], find)
        state.update(step="fix" if problems else "ready", draft=draft.model_dump(), problems=problems)
    _save(sid, state, out_dir)
    return view(sid, out_dir)


def script(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The script the bot makes the video from (its rules check passed)."""
    state = _load(sid, out_dir)
    if state["step"] not in ("ready", "queued"):
        raise WhatIfError("The script hasn't passed the check yet")
    draft = Draft.model_validate(state["draft"])
    out = bw_script.finish(draft, _research(state["question"]))
    # the description leads with the title, never with the owner's typed question (typos and all went out as the
    # first line of "What If Gaza Wasn't a Thin Strip?", 2026-11-04); the state keeps the typed question
    if state.get("format") == ATTEMPTS:  # the war map
        return {**out, "question": draft.title.strip(), "kind": ATTEMPTS, "style": "war", "series": "", "origin": "whatif",
                "claims": [], "sources": []}
    return {**out, "question": draft.title.strip(), "kind": KIND, "series": "", "origin": "whatif", "claims": [], "sources": []}


def queue(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    """Put the passed script in the queue (the next slot makes it)."""
    data = script(sid, out_dir)
    folder = bw_channel.queue_dir(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d-%H%M%S}_{data['kind']}-{sid[:40]}.json"
    (folder / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    state = _load(sid, out_dir)
    state.update(step="queued", queued={"at": datetime.now().replace(microsecond=0).isoformat(), "name": name})
    _save(sid, state, out_dir)
    return view(sid, out_dir)


# --------------------------------------------------------------------------- what the panel shows


def _prompt(state: dict[str, Any]) -> str:
    write = write_prompt(state["question"], fmt=state.get("format") or KIND)
    if state["step"] == "fix" and state.get("draft"):
        return write + "\n\n" + bw_script.fix_prompt(state["problems"], Draft.model_validate(state["draft"])) + "\n" + FORMAT
    if state["step"] in ("write", "rejected", "fix"):
        return write + "\n\n" + FORMAT
    return ""


def view(sid: str, out_dir: Path | None = None) -> dict[str, Any]:
    state = _load(sid, out_dir)
    preview = None
    if state.get("draft"):
        draft = Draft.model_validate(state["draft"])
        text = bw_script.narration(draft)
        preview = {"title": draft.title, "cover_text": draft.cover_text, "words": bw_script.words(text), "narration": text,
                   "scenes": len(draft.scenes), "transfers": sum(e.kind == "transfer" for s in draft.scenes for e in s.elements),
                   "armies": sum(e.kind == "army" for s in draft.scenes for e in s.elements),
                   "battles": sum(e.kind == "battle" for s in draft.scenes for e in s.elements)}
    return {"id": sid, "question": state["question"], "format": state.get("format") or KIND, "step": state["step"], "problems": state.get("problems") or [],
            "prompt": _prompt(state), "preview": preview, "queued": state.get("queued"), "created_at": state.get("created_at", "")}


def sessions(out_dir: Path | None = None) -> list[dict[str, Any]]:
    folder = root(out_dir)
    if not folder.is_dir():
        return []
    out = []
    for f in folder.glob("*.json"):
        try:
            state = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"id": f.stem, "question": state.get("question", ""), "step": state.get("step", ""), "created_at": state.get("created_at", "")})
    return sorted(out, key=lambda s: s["created_at"], reverse=True)[:SESSIONS_SHOWN]
