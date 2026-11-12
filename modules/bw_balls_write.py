"""Borderline Why's balls Shorts written by the owner's own LLM (the dashboard's "Talking flag balls" card).

One session at a time, three steps, the owner carrying the prompts:
  1. start    the writing prompt for N scripts (the questions already made are listed, so none is repeated)
  2. answer   the LLM's JSON; the rules check (modules.bw_balls.problems and what a script needs to go up). Broken
              scripts give a fix prompt; all passing gives the fact-check prompt (better in a second LLM)
  3. facts    the checker's JSON; flagged scripts give a fix prompt, none flagged makes the scripts ready
  4. queue    the scripts go to the channel's queue, where the balls slot takes one a day

The session is output/borderlinewhy/balls/session.json, so it survives a restart.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from modules import bw_balls, bw_channel
from trendclip import llm_json

EXAMPLE = bw_channel.PROJECT_ROOT / "content" / "borderlinewhy" / "balls" / "01-most-neighbors.json"
OWN = bw_channel.PROJECT_ROOT / "content" / "borderlinewhy" / "balls"  # the first scripts, written by hand
MOST, DEFAULT = 10, 7
CAST = (2, 5)  # countries that speak in one Short


class WriteError(RuntimeError):
    pass


class Answer(BaseModel):
    scripts: list[dict[str, Any]]


class Flag(BaseModel):
    script: int
    claim: str = ""
    why: str = ""


class Facts(BaseModel):
    flags: list[Flag] = []


def root(out_dir: Path | None = None) -> Path:
    return bw_channel.out_root(out_dir) / "balls"


def _path(out_dir: Path | None = None) -> Path:
    return root(out_dir) / "session.json"


def load(out_dir: Path | None = None) -> dict[str, Any] | None:
    try:
        return json.loads(_path(out_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(session: dict[str, Any], out_dir: Path | None = None) -> dict[str, Any]:
    root(out_dir).mkdir(parents=True, exist_ok=True)
    _path(out_dir).write_text(json.dumps(session, indent=1, ensure_ascii=False), encoding="utf-8")
    return view(out_dir)


def made(out_dir: Path | None = None) -> list[str]:
    """The questions already written (in the repo, waiting in the queue or made), so the LLM brings new ones."""
    queue = bw_channel.queue_dir(out_dir)
    found: list[str] = []
    for path in [*sorted(OWN.glob("*.json")), *sorted(queue.glob("*.json")), *sorted((queue / "used").glob("*.json"))]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("format") == "balls" and data.get("title") and data["title"] not in found:
            found.append(str(data["title"]))
    return found


def waiting(out_dir: Path | None = None) -> int:
    return sum(1 for p in bw_channel.queued_scripts(out_dir) if bw_channel._is_balls_file(p))


def _example() -> str:
    try:
        return json.dumps(json.loads(EXAMPLE.read_text(encoding="utf-8")), indent=1, ensure_ascii=False)
    except (OSError, ValueError):
        return "{}"


def rules() -> str:
    lo, hi = bw_balls.WORDS
    return f"""You write YouTube Shorts for Borderline Why, a geography channel. In these Shorts the countries are balls with
their flag and two eyes, and they TALK: the host (a red panda ball) asks one geography question, three or four countries
answer it about themselves, each one beating the last, and the surprising winner comes last. Everything a country says
is shown on the map behind it. 12-25 seconds, fast and funny, for viewers of every country (simple English).

Each script is ONE JSON object shaped exactly like the example below, with "format": "balls":
- "cover_text": the question on the banner, 3-7 words, starting with Who or Which ("Who has the most islands?").
  "title": the same question as a YouTube title.
- "scenes": {bw_balls.SCENES[0]}-10 lines, each {{"kind": "balls", "who", "narration", ...}}. ONE speaker says ONE short line:
  at most 12 words, {lo}-60 words in the whole script. Numbers in the narration are written as words
  ("fourteen", "two hundred thousand"), never digits.
- "who": "host" or a country's common English name ("Russia", "United States", "South Korea"). Only real, sovereign
  countries that can be seen on a world map: no Vatican, Monaco, San Marino, Singapore or other dots, no regions,
  no disputed territories. {CAST[0]}-{CAST[1]} countries speak in one script.
- line 1 is the host asking the question, with "with": the countries that will answer (in their order).
- then each country answers about ITSELF, from the smallest answer to the biggest; the last country is the winner and
  should surprise (Sweden has more islands than Indonesia). A country may tease the one before it in two words
  ("Cute.", "Amateurs.", "Sorry."), never insult it.
- "expr": the speaker's eyes: {' | '.join(bw_balls.EXPRS)}.
- "badge": {{"text", "sub", "cue"}} on a country's line: its number as it is shown ("14", "267K", "9.98M", "1.45B": at
  most 6 characters) and what it counts in one or two words ("neighbors", "islands", "km"). "cue": the word of the line
  the badge pops on.
- "show": only when the answer IS a list of countries (its neighbors): every one of them by name, and they light up on
  the map one by one while the badge counts up to the same number. The list's length must equal the badge's number.
- the last one or two lines are the host's: it names the winner ("with": [the winner]) and ends on ONE question to the
  viewer that anyone can answer in a word or a number about their own country. The last line ends with "?".
- "comment": that same question with 👇. "description": 2 sentences with every number of the script and one emoji.
  "hashtags": 3-4 without #, "countryballs" first.
- FACTS: every number must be true and current, and each country's line true about that country. "sources": 2 or
  more real pages (CIA World Factbook, UN, national statistics offices, Britannica, Guinness: never Wikipedia).
  "claims": every fact of the script as a short sentence with its number. If two good sources disagree, pick another
  question.
- Never: wars, deaths, religion, politics, disputed borders, stereotypes about a people, anything a country's
  viewers would feel insulted by. The fun is in the numbers and the surprise.

The example (a finished script):
{_example()}"""


def write_prompt(session: dict[str, Any], out_dir: Path | None = None) -> str:
    n = int(session["count"])
    ideas = [i for i in session.get("ideas") or [] if i]
    have = made(out_dir)
    parts = [rules(), ""]
    if ideas:
        parts.append(f"Write {n} script{'s' if n > 1 else ''}. Use these ideas first, then your own:\n" + "\n".join(f"- {i}" for i in ideas))
    else:
        parts.append(f"Write {n} script{'s' if n > 1 else ''}, each on a different question (sizes, borders, coasts, people, records, "
                     "flags, cities, mountains, rivers: anything a map can show).")
    if have:
        parts.append("Already made, do not repeat them:\n" + "\n".join(f"- {t}" for t in have))
    parts.append(f'Answer with JSON only: {{"scripts": [ ... ]}}, {n} object{"s" if n > 1 else ""}.')
    return "\n\n".join(parts)


def write_problems(script: dict[str, Any]) -> list[str]:
    """bw_balls.problems, and what an LLM's script needs before it may go up."""
    found = list(bw_balls.problems(script))
    scenes = script.get("scenes") or []
    cast = list(dict.fromkeys(str(s.get("who") or bw_balls.HOST) for s in scenes if str(s.get("who") or bw_balls.HOST) != bw_balls.HOST))
    if not CAST[0] <= len(cast) <= CAST[1]:
        found.append(f"{len(cast)} countries speak (wanted {CAST[0]}-{CAST[1]})")
    if not str(script.get("title") or "").strip():
        found.append("no title")
    if len(script.get("sources") or []) < 2:
        found.append("fewer than two sources")
    if any("wikipedia.org" in str((s or {}).get("url", "")).lower() for s in script.get("sources") or [] if isinstance(s, dict)):
        found.append("a source is Wikipedia (use the page it cites)")
    if not script.get("claims"):
        found.append("no claims (every fact as a short sentence)")
    if not str(script.get("comment") or "").strip():
        found.append("no comment (the last line's question with 👇)")
    for k, s in enumerate(scenes, 1):
        if re.search(r"\d", str(s.get("narration") or "")):
            found.append(f"line {k}: a number in digits (write it as words, the voice reads it)")
        bd, show = s.get("badge") or {}, s.get("show") or []
        digits = re.fullmatch(r"\d+", str(bd.get("text", "")).strip())
        if show and digits and int(digits.group()) != len(show):
            found.append(f"line {k}: the badge says {bd['text']} but {len(show)} countries light up")
    return found


def _clean(script: dict[str, Any]) -> dict[str, Any]:
    out = {**script, "format": "balls"}
    out["scenes"] = [{**s, "kind": "balls"} for s in script.get("scenes") or [] if isinstance(s, dict)]
    out.pop("narration", None)
    return out


def fix_prompt(session: dict[str, Any], out_dir: Path | None = None) -> str:
    lines = [f"Script {k}: " + "; ".join(p) for k, p in sorted(session["problems"].items(), key=lambda kv: int(kv[0])) if p]
    why = ("A fact checker could not confirm these statements in your last answer" if session.get("flagged")
           else "Your last answer broke these rules")
    how = ("For each one: fix the number to what the listed sources say, or replace the country or the question. "
           if session.get("flagged") else "")
    return (f"{write_prompt(session, out_dir)}\n\n{why}:\n" + "\n".join(lines)
            + f"\n\n{how}Fix only that and answer with the whole JSON again ({{\"scripts\": [...]}}, every script, in order). "
              "Your last answer:\n" + json.dumps({"scripts": session["drafts"]}, ensure_ascii=False))


def facts_prompt(session: dict[str, Any]) -> str:
    blocks = []
    for k, d in enumerate(session["drafts"], 1):
        said = "\n".join(f"  {s.get('who', 'host')}: {s.get('narration', '')}" + (f"  [shown: {s['badge'].get('text')} {s['badge'].get('sub', '')}]" if s.get("badge") else "")
                         + (f"  [lights up: {', '.join(map(str, s['show']))}]" if s.get("show") else "") for s in d["scenes"])
        claims = "\n".join(f"  - {c}" for c in d.get("claims") or [])
        sources = "\n".join(f"  - {(s or {}).get('title', '')}: {(s or {}).get('url', '')}" for s in d.get("sources") or [] if isinstance(s, dict))
        blocks.append(f"SCRIPT {k}: {d.get('title', '')}\nLines:\n{said}\nClaims:\n{claims}\nSources:\n{sources}")
    return ("You are a strict fact checker for short geography videos. Below are scripts in which countries state facts "
            "about themselves. Check every number, every ranking (is the 'winner' really first in the world, and is each "
            "country's own figure right?) and every list of countries against reliable, current sources. Flag anything "
            "wrong, outdated, disputed between good sources, or true only under an unusual definition.\n\n"
            + "\n\n".join(blocks)
            + '\n\nAnswer with JSON only: {"flags": [{"script": 1, "claim": "what is wrong", "why": "the right figure and where it is from"}]}. '
              'No problems at all: {"flags": []}.')


def view(out_dir: Path | None = None) -> dict[str, Any]:
    s = load(out_dir)
    base = {"waiting": waiting(out_dir), "made": len(made(out_dir)), "slot": bw_channel.balls_slot()}
    if s is None:
        return {**base, "step": "none", "drafts": [], "prompt": ""}
    step = s["step"]
    prompt = {"write": write_prompt, "fix": fix_prompt}.get(step)
    text = prompt(s, out_dir) if prompt else facts_prompt(s) if step == "facts" else ""
    drafts = [{"title": str(d.get("title") or d.get("cover_text") or "?"), "words": sum(len(str(x.get("narration") or "").split()) for x in d.get("scenes") or []),
               "lines": len(d.get("scenes") or []), "problems": s["problems"].get(str(k), [])} for k, d in enumerate(s.get("drafts") or [], 1)]
    return {**base, "step": step, "count": s["count"], "ideas": s.get("ideas") or [], "drafts": drafts, "prompt": text,
            "flagged": bool(s.get("flagged")), "general": s["problems"].get("0", []), "skipped_facts": bool(s.get("skipped_facts"))}


def start(count: int = DEFAULT, ideas: str = "", out_dir: Path | None = None) -> dict[str, Any]:
    session = {"step": "write", "count": max(1, min(MOST, int(count))), "drafts": [], "problems": {},
               "ideas": [" ".join(i.split())[:160] for i in str(ideas or "").splitlines() if i.strip()][:MOST],
               "started": datetime.now().isoformat(timespec="seconds")}
    return _save(session, out_dir)


def _session(out_dir: Path | None) -> dict[str, Any]:
    session = load(out_dir)
    if session is None:
        raise WriteError("Start first: get the prompt")
    return session


def answer(text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The LLM's scripts: the rules check; all passing moves on to the fact check."""
    session = _session(out_dir)
    stripped = (text or "").strip()
    if stripped.startswith("{") and '"scripts"' not in stripped[:200]:
        stripped = '{"scripts": [' + stripped + "]}"  # one script pasted on its own
    parsed = llm_json.parse(stripped, Answer, WriteError)
    session["drafts"] = [_clean(d) for d in parsed.scripts]
    session["problems"] = {str(k): write_problems(d) for k, d in enumerate(session["drafts"], 1)}
    titles = [str(d.get("title", "")).strip().lower() for d in session["drafts"]]
    have = {t.lower() for t in made(out_dir)}
    for k, t in enumerate(titles, 1):
        if t and (t in have or titles.index(t) != k - 1):
            session["problems"][str(k)].append("this question was already made (or is twice in the answer)")
    if len(session["drafts"]) != session["count"]:
        session["problems"]["0"] = [f"{len(session['drafts'])} scripts came back, {session['count']} were asked for"]
    session["flagged"] = False
    session["skipped_facts"] = False
    session["step"] = "fix" if any(session["problems"].values()) else "facts"
    return _save(session, out_dir)


def facts(text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The fact checker's flags: none makes the scripts ready, any sends the flagged scripts back to be fixed."""
    session = _session(out_dir)
    if session["step"] != "facts":
        raise WriteError("The scripts are not at the fact check")
    parsed = llm_json.parse(text, Facts, WriteError)
    n = len(session["drafts"])
    session["problems"] = {str(k): [] for k in range(1, n + 1)}
    for f in parsed.flags:
        if 1 <= f.script <= n:
            session["problems"][str(f.script)].append(" - ".join(x for x in (f.claim.strip(), f.why.strip()) if x) or "flagged")
    session["flagged"] = any(session["problems"].values())
    session["step"] = "fix" if session["flagged"] else "ready"
    return _save(session, out_dir)


def skip_facts(out_dir: Path | None = None) -> dict[str, Any]:
    session = _session(out_dir)
    if session["step"] != "facts":
        raise WriteError("The scripts are not at the fact check")
    session["step"], session["skipped_facts"] = "ready", True
    return _save(session, out_dir)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "balls"


def queue(out_dir: Path | None = None) -> dict[str, Any]:
    """The ready scripts into the channel's queue (the balls slot takes one a day); the session ends."""
    session = _session(out_dir)
    if session["step"] != "ready":
        raise WriteError("The scripts have not passed both checks yet")
    folder = bw_channel.queue_dir(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    names = []
    for k, d in enumerate(session["drafts"], 1):
        if session.get("skipped_facts"):
            d = {**d, "facts_skipped": True}
        name = f"{stamp}-balls-{k:02d}-{slug(str(d.get('title') or d.get('cover_text') or ''))}.json"
        (folder / name).write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        names.append(name)
    _path(out_dir).unlink(missing_ok=True)
    return {**view(out_dir), "queued": names}


def drop(out_dir: Path | None = None) -> dict[str, Any]:
    _path(out_dir).unlink(missing_ok=True)
    return view(out_dir)
