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

from modules import pw_channel, pw_draw
from trendclip import llm_json

KINDS = ("one", "week")
FORMATS = ("why", "story")  # a 35 s "why" explainer; a 50-75 s money story (a cast, their moves with the math, the bars, the ask)
DEFAULT_COUNT = 7
MIN_WORDS, MAX_WORDS = 70, 100  # about 30-38 s with the channel's voice (1.1x): 45-50 s ones lost most viewers
STORY_WORDS = (130, 190)  # 50-75 s: long enough for the next number to pull, short enough for the algorithm (a 300-word one ran 123 s)
STEP_WORDS = 42  # one move: what they did, the math, the idea's name
HOOK_WORDS = 10  # the first sentence, said before a scroller decides
STORY = ("cast", "step", "bars", "ask")
STORY_SCENE_KINDS = (*STORY, "board", "number", "compare", "chart", "map", "photo", "follow")
STORY_HOOK_WORDS = 14
HOOK_MOST = 26  # the whole hook scene: the clash and "here's what each did", about 10 s
SLOW_START = re.compile(r"^(when|in \d|back in|once|since|most people|have you|did you|ever|today|so|imagine)\b", re.I)
SCENE_KINDS = ("product", "tag", "board", "number", "compare", "chart", "map", "photo", "follow")
MIDDLE = ("product", "board", "number", "compare", "chart", "map", "photo")  # what goes between the opening and the follow line
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
    answered = {_norm(s.get(k) or "") for s in pw_channel.scripts(out_dir) for k in ("title", "question", "topic")} - {""}
    for t in found:
        t["used"] = bool(t.get("used")) or _norm(t["question"]) in answered
    return found


def save_topics(found: list[dict[str, Any]], out_dir: Path | None = None) -> None:
    path = _topics_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(found, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def add_topic(question: str, pillar: str = "", out_dir: Path | None = None, format: str = "") -> dict[str, Any]:
    question = " ".join(question.split())
    if len(question) < 8 or not question.endswith("?"):
        raise WriteError("A topic is a question ending in ?")
    if pillar and pillar not in PILLARS:
        raise WriteError(f"The pillar is one of: {', '.join(PILLARS)}")
    found = topics(out_dir)
    if any(_norm(t["question"]) == _norm(question) for t in found):
        raise WriteError("That question is already on the list")
    item = {"question": question, "pillar": pillar, "used": False, "added": datetime.now().strftime("%Y-%m-%d"),
            **({"format": "story"} if format == "story" else {})}
    save_topics([*found, item], out_dir)
    return item


def remove_topic(question: str, out_dir: Path | None = None) -> None:
    save_topics([t for t in topics(out_dir) if _norm(t["question"]) != _norm(question)], out_dir)


def move_first(question: str, out_dir: Path | None = None) -> None:
    """Put a topic at the top of the list, so the next script written takes it (the Topics tab's "Write next")."""
    found = topics(out_dir)
    item = next((t for t in found if _norm(t["question"]) == _norm(question)), None)
    if item is None:
        raise WriteError("That question is not on the list")
    save_topics([item, *(t for t in found if t is not item)], out_dir)


def next_topics(count: int, out_dir: Path | None = None, found: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The next unused topics, the top one first, then taking the pillars in turn so the channel never looks
    mass-produced."""
    left = [t for t in (topics(out_dir) if found is None else found) if not t["used"]]
    picked: list[dict[str, Any]] = left[:1]
    left = left[1:]
    while left and len(picked) < count:
        last = picked[-1]["pillar"] if picked else ""
        nxt = next((t for t in left if t.get("pillar") != last), left[0])
        picked.append(nxt)
        left.remove(nxt)
    return picked


# --------------------------------------------------------------------------- the format rules


def format_problems(script: dict) -> list[str]:
    """What the renderer and the brief need beyond pw_channel.check_script."""
    if pw_channel.is_story(script):
        return story_problems(script)
    problems = []
    scenes = script.get("scenes") or []
    n = pw_channel.words(pw_channel.narration(script))
    if not MIN_WORDS <= n <= MAX_WORDS:
        problems.append(f"{n} words of narration (wanted {MIN_WORDS}-{MAX_WORDS}, about 30-38 s)")
    problems += hook_problems(script)
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
            board = s.get("board") or {}
            lines = board.get("write") or []
            if not 2 <= len(lines) <= 3:
                problems.append(f"scene {k}: a board writes 2-3 lines (a title first), its picture does the explaining")
            if board.get("picture") not in pw_draw.DRAWINGS:
                problems.append(f"scene {k}: board.picture is one of {', '.join(pw_draw.DRAWINGS)}")
            for line in lines:
                text = line.get("text", "") if isinstance(line, dict) else str(line)
                if len(text) > 26:
                    problems.append(f"scene {k}: board line too long for the board (26 characters at most): {text!r}")
        problems += [f"scene {k}: {p}" for p in scene_problems(s)]
        for el in s.get("elements") or []:
            if el.get("kind") == "icon" and el.get("icon") not in ICONS:
                problems.append(f"scene {k}: unknown icon {el.get('icon')!r}")
    if not any(s.get("kind") == "product" for s in scenes):
        problems.append("show the product: a scene of kind product (drawn, or a free photo when it can't be drawn)")
    middle = [s.get("kind") for s in scenes[1:] if s.get("kind") != "follow"]
    if not 3 <= len(middle) <= 4:
        problems.append("2 reasons and a twist: 3-4 scenes after the opening")
    if middle and all(k == "board" for k in middle):
        problems.append("vary the pictures: at least one number, compare, chart, map or photo scene, not only boards")
    for src in script.get("sources") or []:
        if not str(src.get("url", "")).startswith("http"):
            problems.append(f"source without a link: {src.get('title', '')[:50]}")
    return problems


MATH_NUMBER = re.compile(r"[$€£]?\s*([\d][\d,]*(?:\.\d+)?)")


def math_value(text: str) -> float | None:
    """"5 x 365 x $10 = $18,250" -> 18250.0 when the working really gives the result, else None (a wrong line,
    or one that isn't a product of numbers)."""
    left, eq, right = text.partition("=")
    if not eq:
        return None
    nums = [float(m.group(1).replace(",", "")) for m in MATH_NUMBER.finditer(left)]
    want = [float(m.group(1).replace(",", "")) for m in MATH_NUMBER.finditer(right)]
    if not nums or len(want) != 1 or not re.fullmatch(r"[\s\d.,$€£x×*]+", left.strip()):
        return None
    got = 1.0
    for v in nums:
        got *= v
    return want[0] if abs(got - want[0]) <= max(1.0, 0.005 * abs(want[0])) else None


def story_problems(script: dict) -> list[str]:
    """A money story: the cast, 3-4 steps whose math adds up, the bars, the ask (the last scene)."""
    problems = []
    scenes = script.get("scenes") or []
    n = pw_channel.words(pw_channel.narration(script))
    lo, hi = STORY_WORDS
    if not lo <= n <= hi:
        problems.append(f"{n} words of narration (a story wants {lo}-{hi}, about 50-75 s)")
    said = str(scenes[0].get("narration") or "").strip() if scenes else ""
    first = re.split(r"(?<=[.!?])\s", said, maxsplit=1)[0]
    if scenes and len(first.split()) > STORY_HOOK_WORDS:
        problems.append(f"the hook's first sentence is {len(first.split())} words; {STORY_HOOK_WORDS} at most, and it holds the clash "
                        f"(\"Same $500. Four friends. One made $3,650, one made $511,000.\"): {first!r}")
    if scenes and not re.search(r"\d", said):
        problems.append("the hook says the numbers that clash (the smallest and the biggest result)")
    if scenes and pw_channel.words(said) > HOOK_MOST:
        problems.append(f"the hook is {pw_channel.words(said)} words; {HOOK_MOST} at most (the clash, then \"Here's what each one did.\")")
    if scenes and scenes[0].get("kind") == "cast":
        results = [str(p.get("result") or "") for p in (scenes[0].get("cast") or {}).get("people") or [] if isinstance(p, dict)]
        said_digits = re.sub(r"[^\d ]", "", said).split()
        if len([r for r in results if r]) != 2 or any(r and re.sub(r"\D", "", r) not in said_digits for r in results):
            problems.append("cast.people: the smallest and the biggest result go in their person's \"result\" (\"$3,650\", \"$511,000\"), "
                            "each said in the hook in digits exactly so: they are written under them as they are said")
    kinds = [s.get("kind") for s in scenes]
    for k, s in enumerate(scenes, 1):
        if s.get("kind") not in STORY_SCENE_KINDS:
            problems.append(f"scene {k}: a story's kinds are {', '.join(STORY_SCENE_KINDS)}")
        if s.get("kind") != "follow" and not str(s.get("narration") or "").strip():
            problems.append(f"scene {k}: no narration")
        if s.get("kind") == "step" and pw_channel.words(str(s.get("narration") or "")) > STEP_WORDS:
            problems.append(f"scene {k}: a step is {STEP_WORDS} words at most (what they did, the math, the idea's name); "
                            f"this one is {pw_channel.words(str(s.get('narration') or ''))}")
        o = s.get(s.get("kind") or "") or {}
        if s.get("kind") == "cast":
            people = o.get("people") or []
            if not 2 <= len(people) <= 4 or not all(isinstance(p, dict) and _short(p.get("name"), 12) for p in people):
                problems.append(f"scene {k}: cast.people is 2-4 of {{name (12 characters at most), money}}")
            if not _short(o.get("title"), 24):
                problems.append(f"scene {k}: cast.title is at most 24 characters (\"Same $500 each\")")
        elif s.get("kind") == "step":
            people = next((len((x.get("cast") or {}).get("people") or []) for x in scenes if x.get("kind") == "cast"), 4)
            if not isinstance(o.get("n"), int) or not 1 <= o.get("n") <= people:
                problems.append(f"scene {k}: step.n is the person's number in the cast (1-{people})")
            if not _short(o.get("did"), 24):
                problems.append(f"scene {k}: step.did is what they did, at most 24 characters (\"Rented five carts\")")
            if o.get("draw") not in pw_draw.DRAWINGS:
                problems.append(f"scene {k}: step.draw is the thing they got, one of {', '.join(pw_draw.DRAWINGS)}")
            if not isinstance(o.get("count"), int) or o.get("count") < 1:
                problems.append(f"scene {k}: step.count is how many of them (a whole number, 1 or more)")
            math = str(o.get("math") or "")
            value = math_value(math)
            if value is None or len(math) > 34:
                problems.append(f"scene {k}: step.math is a product that adds up, at most 34 characters (\"5 x 365 x $10 = $18,250\"): {math!r}")
            elif re.sub(r"\D", "", math.rpartition("=")[2]) not in re.sub(r"[^\d ]", "", str(s.get("narration") or "")).split():
                problems.append(f"scene {k}: the narration says the result in digits exactly as step.math writes it ({math.rpartition('=')[2].strip()}), so it lands on screen as it is said")
            if not _short(o.get("lesson"), 18):
                problems.append(f"scene {k}: step.lesson names the idea in 1-3 words (18 characters at most): \"Leasing\"")
        elif s.get("kind") == "bars":
            bars = o.get("bars") or []
            if not 2 <= len(bars) <= 4 or not all(isinstance(b, dict) and _short(b.get("label"), 12) and isinstance(b.get("value"), (int, float)) and _short(b.get("text"), 10) for b in bars):
                problems.append(f"scene {k}: bars.bars is 2-4 of {{label (12 characters), value (a number), text (10 characters)}}")
            totals = [math_value(str((x.get("step") or {}).get("math") or "")) for x in scenes if x.get("kind") == "step"]
            if all(t is not None for t in totals) and len(totals) == len(bars) and any(float(b.get("value") or 0) != t for b, t in zip(bars, totals) if isinstance(b, dict)):
                problems.append(f"scene {k}: the bars' values are the steps' results, in order")
        elif s.get("kind") == "ask":
            if not _short(o.get("question"), 26) or not 2 <= len(o.get("options") or []) <= 5:
                problems.append(f"scene {k}: ask needs question (26 characters at most) and options (2-5 short choices)")
        else:
            problems += [f"scene {k}: {p}" for p in scene_problems(s)]
            if s.get("kind") == "board":
                board = s.get("board") or {}
                if board.get("picture") not in pw_draw.DRAWINGS:
                    problems.append(f"scene {k}: board.picture is one of {', '.join(pw_draw.DRAWINGS)}")
    if kinds[:1] != ["cast"]:
        problems.append("a story opens on its cast (kind cast): the people and what they start with")
    if not 3 <= kinds.count("step") <= 4:
        problems.append("3-4 step scenes: one move per person, each with its math and the idea's name")
    if "bars" not in kinds:
        problems.append("a bars scene near the end: every result side by side")
    if "ask" not in kinds:
        problems.append("an ask scene at the end: \"Which one are you?\" and the choices")
    if [k for k in kinds if k != "follow"][-1:] != ["ask"]:  # an older script's follow line is skipped when made
        problems.append("the ask is the last scene: no follow line after it (the video ends on the question)")
    if not _short(script.get("comment"), 120):
        problems.append("comment: the question pinned under the video (\"Which friend are you: 1, 2, 3 or 4? 👇\")")
    for src in script.get("sources") or []:
        if not str(src.get("url", "")).startswith("http"):
            problems.append(f"source without a link: {src.get('title', '')[:50]}")
    return problems


def hook_problems(script: dict) -> list[str]:
    """The first sentence is said before a scroller decides: a short claim that clashes, drawn on the first frame."""
    scenes = script.get("scenes") or []
    if not scenes:
        return []
    out = []
    said = str(scenes[0].get("narration") or "").strip()
    first = re.split(r"(?<=[.!?])\s", said, maxsplit=1)[0]
    if len(first.split()) > HOOK_WORDS:
        out.append(f"the hook's first sentence is {len(first.split())} words; make it {HOOK_WORDS} at most, a claim that "
                   f"clashes (\"This can should cost $2.38.\"), not background: {first!r}")
    elif SLOW_START.match(first):
        out.append(f"the hook opens on background ({first!r}); open on the surprising claim itself, the background after")
    if scenes[0].get("kind") == "product" and (scenes[0].get("product") or {}).get("draw") not in pw_draw.DRAWINGS:
        out.append(f"the opening product is drawn (product.draw, one of {', '.join(pw_draw.DRAWINGS)}): a photo can't be the first frame")
    return out


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
    elif kind == "product":
        if o.get("draw") not in pw_draw.DRAWINGS and not str(o.get("photo") or "").strip():
            out.append(f"product.draw is one of {', '.join(pw_draw.DRAWINGS)}, or product.photo names a Wikimedia Commons picture of it")
        if not all(_short(x, 10) for x in o.get("label") or []) or len(o.get("label") or []) > 3:
            out.append("product.label is 1-3 words of 10 characters at most (our own words, never the brand's logo)")
        if o.get("label") and o.get("draw") not in pw_draw.PACKAGED:
            out.append(f"product.label only goes on a package, card or note ({', '.join(pw_draw.PACKAGED)}): a {o.get('draw')} is drawn as itself, "
                       "with no word on it; never write one thing on a drawing of another")
        lines = o.get("lines") or []
        if len(lines) > 4 or not all(_short(x.get("text") if isinstance(x, dict) else x, 12) for x in lines):
            out.append("product.lines is up to 4 lines of 12 characters at most")
    elif kind == "photo":
        if not str(s.get("photo") or "").strip():
            out.append("a photo scene names the English Wikipedia article whose picture it shows (photo)")
        mark = s.get("mark") or {}
        if mark.get("draw") not in pw_draw.DRAWINGS or not _short(mark.get("note"), 14):
            out.append(f"a photo scene has mark: {{\"draw\": one of {', '.join(pw_draw.DRAWINGS)}, \"note\": 1-3 words}}, "
                       "what the hand draws on the picture: it must show what the narration says (glare -> sun)")
    return out


def problems_of(script: dict) -> list[str]:
    return pw_channel.check_script(script) + format_problems(script)


# --------------------------------------------------------------------------- prompts


EXAMPLE = pw_channel.SCRIPTS_DIR / "001-arizona-99c.json"  # the first, hand-checked script
STORY_EXAMPLE = pw_channel.SCRIPTS_DIR.parent / "examples" / "002-coffee-cart-story.json"  # the prompt's example only: not in the queue


def _example(path: Path = EXAMPLE) -> str:
    """The example script for the prompt, without its follow line (videos end on their last real scene now)."""
    if not path.is_file():
        return "{}"
    return json.dumps(pw_channel.without_follow(json.loads(path.read_text(encoding="utf-8"))), indent=2, ensure_ascii=False)


def story_rules() -> str:
    lo, hi = STORY_WORDS
    return f"""Price Tag Why is a YouTube Shorts channel: whiteboard-animated money stories for curious adults (18-45,
US/UK/CA/AU) who are not finance experts. A MONEY STORY is a parable (made up, and it says so) or a true story
(sourced) in which 3-4 people start with the same money and each does something different with it, the numbers
growing with every move, so the viewer stays to see the next number. It explains, it never advises.

Each script is ONE JSON object shaped exactly like the example below, with "format": "story":
- {lo}-{hi} words of narration in all (50-75 seconds: SHORT wins, cut every word that isn't the story), short
  spoken sentences. Every figure the math shows is
  said in digits exactly as written ("that's $18,250"), so it lands on screen as it is said.
- scene 1 is kind "cast": the hook, at most {HOOK_MOST} words (about 10 s). Its first sentence (at most {STORY_HOOK_WORDS} words) holds
  the clash: the same start, the smallest and the biggest result ("Same $500. Four friends. One made $3,650. One made
  $511,000."). Then a few words of setting and "Here's what each one did." cast: {{"title": at most 24 characters,
  "people": 2-4 of {{"name": "Friend 1" (12 characters at most), "money": "$500", "result": "$3,650" only on the
  smallest and the biggest}}, "note": "One year later"}}: the hand writes each figure under its person as it is said.
- then 3-4 scenes of kind "step", one per person, in order, each 20-{STEP_WORDS} words: what they did, the math said out
  loud with its result, then the idea's name ("This is called leasing."). No extra lines.
  step: {{"who": the person's name, "n": their number (1-4), "did": at most 24 characters ("Rented five carts"),
  "draw": the thing they got, one of the drawings below, "count": how many (a whole number; 6 are drawn, more is
  written "x151"), "math": a product that adds up, at most 34 characters ("5 x 365 x $10 = $18,250": the numbers
  multiplied, = , the result; it is checked), "lesson": the idea's name, 1-3 words}}. Each move must beat the one
  before it, and the last is the biggest.
- then kind "bars": every result side by side. bars: {{"title": ..., "bars": [{{"label": the name, "value": the
  result as a number, "text": "$18,250"}}, ...]}} in the cast's order; the narration says them one by one and the
  moral in one line ("The money was the same. The idea was different.").
- then kind "ask": {{"question": "Which friend are you?" (26 characters at most), "options": ["1", "2", "3", "4"],
  "line": "Send this to a friend"}}; the narration asks it, then asks for a comment and a share.
- the ask is the LAST scene: no follow line, no "Follow for…" after it (the video ends on the question).
- THE DRAWING IS THE THING THE NARRATION NAMES. step.draw (and any board.picture, photo mark) is one of these, used
  only for what it shows: {"; ".join(f"{k} = {v}" for k, v in pw_draw.LOOKS.items())}. Write the story around
  these things (carts, stands, machines, cups, trucks, contracts, coins, shops): never name a thing there is no
  drawing of.
- a parable has "parable": true and says it is one, in the ask scene or the description ("It's a parable, not a
  plan."); it needs no sources. A true story (a founder, a town, a country) has "parable": false, "sources" (2 or
  more real pages, never Wikipedia) and "claims" (every figure, each with 2 or more source numbers); its steps may
  be years instead of people (n still 1-4, who the year or the name).
- "comment": the question pinned under the video, the one the ask scene asks ("Which friend are you: 1, 2, 3 or 4? 👇").
- "title": the YouTube title with the numbers in it ("Same $500, four friends: one made $3,650, one made $511,000").
  "question": the words on the first frame, at most 5 ("Same $500. Four friends."). "description": 2-3 sentences,
  starting with "A money parable" when it is one. "hashtags": 2-3 without #. "pillar": one of {", ".join(PILLARS)}.
  "thumbnail": {{"hook": "WHICH ONE ARE YOU?"}}.
Rules: explain, never advise (no "buy", "invest", "you should"); the math must be right; an original story (never
the chicken-and-eggs one, never a story already on YouTube); a different trade and a different set of ideas from
one story to the next (own / rent / pre-sell / a system; stock turnover; sales vs profit; fixed vs variable
cost; price vs value; one customer vs many; cash now vs cash later...).

The example (a finished script):
{_example(STORY_EXAMPLE)}"""


def rules() -> str:
    return f"""Price Tag Why is a YouTube Shorts channel: whiteboard-animated answers to the money questions everyone
wonders about, for curious adults (18-45, US/UK/CA/AU) who are not finance experts. Promise: "In under a minute,
you'll finally understand why money works the way it does."

Each script is ONE JSON object shaped exactly like the example below:
- {MIN_WORDS}-{MAX_WORDS} words of narration in all (30-38 seconds), short spoken sentences, numbers written as a
  narrator says them ("99 cents", "1992"). Shorter wins: most viewers decide in the first second and leave at 20 s.
- scene 1 is the HOOK, and it must SHOCK, not just ask. Its FIRST sentence is at most {HOOK_WORDS} words and is the
  surprising claim itself ("This can should cost $2.38.", "Casinos removed every clock. Gamblers asked."), never
  background ("When Nevada made gambling legal in 1931...", "Monaco has no natural resources..."): the background
  comes after, if at all. Then the clash and "Why?". Then tease the twist in a few words ("And the real reason isn't
  the one you think."), so they stay for it. Scene 1 is kind "product", drawn (never a photo): the drawing and the
  question in big letters are on the first frame, so a scroller recognises it at once. {{"kind": "product", "narration": ..., "product": {{"draw":
  "can", "label": ["ICED", "TEA"], "lines": ["Should cost", {{"text": "$2.38", "color": "red"}}, "Costs",
  {{"text": "99¢", "color": "green"}}]}}}}.
- THE DRAWING IS THE THING THE NARRATION NAMES. Every draw, picture and mark is one of these, used only for what it
  shows: {"; ".join(f"{k} = {v}" for k, v in pw_draw.LOOKS.items())}. Never write one thing on a drawing of another (a
  "DRESS" label on a chip bag is wrong: there is a dress drawing). If nothing here is the thing, open with a drawing of
  what the hook is about instead (a receipt for a price, a card for a payment), or ask for a new drawing. label is
  only for a package, card or note ({", ".join(pw_draw.PACKAGED)}): what is printed on it, never the brand's name or logo.
- then 3-4 scenes: 2 reasons, each drawn on the whiteboard, then the twist by about 20 seconds in (it is what they
  stayed for). Mix the kinds so no two videos look
  alike, and use at least one that isn't a board:
  * "board": a big drawing that explains, with a few words over it. board.write is 2-3 lines, the first a short
    title ("Reason 1: ..."), each at most 26 characters. A line may be {{"text": ..., "color": "green" | "red",
    "strike": true}}: green for money in, red for cost, strike crosses it out. board.picture is the drawing, one of
    the drawings above, the one that shows what the board says; board.color "red" washes it red (a cost).
  * "number": a big figure that counts up as it is said. {{"kind": "number", "narration": ..., "number": {{"label":
    "If it rose with inflation", "from": "99¢", "to": "$2.38", "note": "BLS inflation calculator"}}}} ("from" may be
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
    {{"kind": "photo", "narration": ..., "photo": "Slot machine", "caption": "No sun, no glare", "mark": {{"draw":
    "sun", "note": "Glare!"}}}}. mark is what the hand draws on a note stuck on the picture, with an arrow into it:
    a drawing that says what the narration says (glare -> sun, attention -> eye, a virus -> virus, a book's name
    -> book), never a random one; note is 1-3 words (14 characters at most).
- the thumbnail is made from the script, so write for it: "question" is the video's question in at most 6 words
  ("Why is AriZona still 99¢?"), shown big at the top so the thumbnail says what the video is about; the product
  scene's lines go in pairs, a label then its figure ("Cost in 1922", "160 marks", "Cost in 1923", "200B marks"):
  the first figure is crossed out and the last one goes in a red starburst, so they must clash.
  "thumbnail": {{"hook": "WAIT, WHAT?"}} adds 1-3 punch words at the bottom ("HOW?!", "WHO PAYS?").
- "comment": one short question to viewers about the video, posted under it and pinned, that they want to answer
  ("Would you pay $2.38 for it? 👇", "Did you ever notice there's no clock? 👇"); never advice, never a poll on money moves.
- the twist must surprise (a number that flips the story, what the owner said), never a letdown.
- the twist is the LAST scene: no follow line, no "Follow for…" after it (the video ends on the twist).
- every reason needs its 2 sources: leave out anything only one source says.
- "sources": 2 or more real pages with working links (news, official, academic). Never Wikipedia, Wikiwand, Kiddle or
  a fandom wiki: use them to find the real source and cite that.
  "claims": every fact the narration states, each with the numbers of 2 or more sources that support it.
- "description": 2-3 sentences with the short answer. "hashtags": 2-3 without #. "pillar": one of
  {", ".join(PILLARS)}.
Rules: explain, never advise (no "buy", "invest", "you should"); nothing a source does not say; an original script
(no copying sentences from the sources); a different structure and tone from one video to the next.

The example (a finished script):
{_example()}"""


def session_format(session: dict[str, Any]) -> str:
    return session.get("format") if session.get("format") in FORMATS else "why"


def write_prompt(session: dict[str, Any]) -> str:
    qs = "\n".join(f"{k}. {t['question']}" + (f" (pillar: {t['pillar']})" if t.get("pillar") else "")
                   for k, t in enumerate(session["topics"], 1))
    many = len(session["topics"]) > 1
    story = session_format(session) == "story"
    what = ("one money story for each of these ideas" if many else "the money story for this idea") if story else \
           ("one script for each of these questions" if many else "the script for this question")
    return (f"{story_rules() if story else rules()}\n\nWrite {what}:\n{qs}\n\n"
            f"Answer with JSON only: {{\"scripts\": [ ... ]}}, {'one object per idea, in this order' if many else 'one object'}.")


def broken(session: dict[str, Any]) -> list[int]:
    """The scripts (1-based) that still have problems; the others already passed and are kept."""
    n = len(session.get("drafts") or [])
    return [int(k) for k, p in sorted(session["problems"].items(), key=lambda kv: int(kv[0])) if p and 0 < int(k) <= n]


def fix_prompt(session: dict[str, Any]) -> str:
    """Only the broken scripts go back to the LLM, and only they come back: the passing ones stay in the session."""
    lines = [f"Script {k}: " + "; ".join(p) for k, p in sorted(session["problems"].items(), key=lambda kv: int(kv[0])) if p]
    bad = broken(session)
    whole = bool(session["problems"].get("0")) or not bad  # a wrong number of scripts: everything again
    sent = session["drafts"] if whole else [session["drafts"][k - 1] for k in bad]
    back = ("the whole JSON again ({\"scripts\": [...]}, every script, in order)" if whole else
            f"ONLY the {'script' if len(bad) == 1 else f'{len(bad)} scripts'} below, fixed ({{\"scripts\": [...]}}, "
            f"{'one object' if len(bad) == 1 else 'in this order'}); the others already passed, leave them out")
    which = "" if whole else f" (script{'s' if len(bad) > 1 else ''} {', '.join(map(str, bad))} of your last answer)"
    if session.get("flagged"):
        return (f"{write_prompt(session)}\n\nA fact checker could not confirm these statements in your last answer:\n" + "\n".join(lines)
                + "\n\nFor each one: cut it, or say only what the listed sources say (fix the number, the year, the cause). "
                  f"Don't add any new facts, keep everything else as it is, and answer with {back}. The script{'s' if len(sent) > 1 else ''} to fix{which}:\n"
                + json.dumps({"scripts": sent}, ensure_ascii=False))
    return (f"{write_prompt(session)}\n\nYour last answer broke these rules:\n" + "\n".join(lines)
            + f"\n\nFix only that and answer with {back}. The script{'s' if len(sent) > 1 else ''} to fix{which}:\n"
            + json.dumps({"scripts": sent}, ensure_ascii=False))


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


class Proposal(BaseModel):
    question: str
    pillar: str = ""
    format: str = ""


class Proposals(BaseModel):
    topics: list[Proposal] = Field(default_factory=list)


TOPICS_PROMPT = """I run "Price Tag Why", a YouTube Shorts channel (30-38 seconds) that explains why something costs what it
costs, or how money works, in plain words with simple drawings. The tone is curious and friendly: it explains, it
never gives financial advice.

Give me {count} new video questions. Each one must:
- be a short question starting with "Why" and ending with "?" (under 80 characters)
- have a surprising but true answer, backed by at least two solid sources (news, official, academic; not Wikipedia)
- fit in 35 seconds: one clear answer with 2 reasons and a twist
- be about something the viewer buys, pays or sees every week (a snack, a phone, a ticket, a subscription, a price
  ending in .99, a store trick): most of them; at most 1 in 4 about a country or money history. A scroller must feel
  "I've wondered that" in one second ("Why is the Costco hot dog still $1.50?" beats "Why is Monaco rich?")
- avoid investing or crypto tips, politics, elections and wars

Spread them evenly across these pillars:
{pillars}

Don't repeat or rephrase any of these, already made or planned:
{taken}

Answer with JSON only, no other text:
{{"topics": [{{"question": "Why are movie theater snacks so expensive?", "pillar": "business tricks"}}]}}
The pillar is written exactly as above."""


STORY_TOPICS_PROMPT = """I run "Price Tag Why", a YouTube Shorts channel of whiteboard money stories (50-75 seconds): 3-4 people
start with the same money, each does something different with it, the numbers grow with every move, and each move
teaches one money idea with a name (leasing, pre-selling, stock turnover, a system, fixed vs variable cost, sales vs
profit, price vs value, cash now vs cash later...). It explains, it never gives financial advice.

Give me {count} new story ideas. Each one is a short line that names the trade, the start money and the clash of
results, ending with "?" ("Same $500, four friends with a coffee cart: how did one make $3,650 and one $511,000?").
Each must:
- use everyday things that can be drawn: a cart, a stand, a vending machine, a shop, a truck, a cup, a contract, coins
- teach 3-4 ideas with names, a different set and a different trade from every idea below
- be a parable that says so, or a true story (a founder, a town, a country) with real sourced numbers
- avoid investing or crypto tips, politics, elections and wars, and never the chicken-and-eggs parable

Don't repeat or rephrase any of these, already made or planned:
{taken}

Answer with JSON only, no other text:
{{"topics": [{{"question": "Same $300, three cousins with a lemonade stand: how did one make $900 and one $90,000?", "pillar": "business tricks", "format": "story"}}]}}
The pillar is one of: {pillars}, written exactly so."""


def llm_prompt(count: int = 20, out_dir: Path | None = None, found: list[dict[str, Any]] | None = None, format: str = "why") -> str:
    """The prompt for new topics from any LLM (the Topics tab's "New topics from your own LLM"); `found`: the list
    to avoid (the Linux hub's, on the sending machine); format "story" asks for money-story ideas."""
    taken = "\n".join(t["question"] for t in (topics(out_dir) if found is None else found)) or "(none yet)"
    if format == "story":
        return STORY_TOPICS_PROMPT.format(count=count, pillars=", ".join(PILLARS), taken=taken)
    return TOPICS_PROMPT.format(count=count, pillars="\n".join(f"- {p}" for p in PILLARS), taken=taken)


def llm_add(text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The LLM's pasted topics: each new question ending in ? joins the end of the list (an unknown pillar is
    left empty). {"added": [questions], "skipped": n}."""
    found = llm_json.parse(text, Proposals, WriteError).topics
    if not found:
        raise WriteError("The answer has no topics")
    added = []
    for p in found:
        try:
            added.append(add_topic(p.question, p.pillar if p.pillar in PILLARS else "", out_dir, p.format)["question"])
        except WriteError:
            continue
    return {"added": added, "skipped": len(found) - len(added)}


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


def of_format(found: list[dict[str, Any]], format: str) -> list[dict[str, Any]]:
    """The topics written in this format: story ideas, or the "why" questions."""
    return [t for t in found if (t.get("format") == "story") == (format == "story")]


def start(kind: str, question: str = "", count: int = DEFAULT_COUNT, out_dir: Path | None = None,
          found: list[dict[str, Any]] | None = None, format: str = "why") -> dict[str, Any]:
    """A new session: `question` (or the next topic) for "one", the next `count` topics for "week", all in one
    `format` (why | story). `found`: the topic list to take them from (the Linux hub's, on the sending machine),
    else this machine's."""
    if format not in FORMATS:
        raise WriteError(f"The format is one of: {', '.join(FORMATS)}")
    found = of_format(topics(out_dir) if found is None else found, format)
    if kind == "one":
        asked = " ".join(question.split())
        known = next((t for t in found if _norm(t["question"]) == _norm(asked)), None) if asked else None
        picked = [{"question": asked, "pillar": (known or {}).get("pillar", "")}] if asked else next_topics(1, out_dir, found)
    else:
        picked = next_topics(max(1, min(14, count)), out_dir, found)
    if not picked:
        raise WriteError("No topics left: add questions in the Topics tab" if format == "why" else
                         "No story ideas left: add some in the Topics tab (New topics from your own LLM, Stories)")
    session = {"kind": kind, "format": format, "topics": picked, "step": "write", "drafts": [], "problems": {}, "flagged": {},
               "started": datetime.now().isoformat(timespec="minutes")}
    return _save(session, out_dir)


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    keys = lambda d: {_norm(str(d.get(k) or "")) for k in ("title", "question", "topic")} - {""}
    return bool(keys(a) & keys(b))


def merge(session: dict[str, Any], scripts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A fix answer with fewer scripts than the session holds: each fixed script takes the place of the draft it
    answers (the same title or question, else the next broken one in order), and the scripts that passed stay. An
    answer with every script replaces them all, as before."""
    old = session.get("drafts") or []
    if session.get("step") != "fix" or not old or len(scripts) >= len(old):
        return scripts
    merged, free = list(old), [k - 1 for k in broken(session)]
    for d in scripts:
        at = next((i for i, o in enumerate(old) if _same(d, o)), None)
        if at is None:
            if not free:
                raise WriteError(f"{len(scripts)} scripts pasted but only {len(broken(session))} needed fixing: "
                                 "paste the fixed ones only, or every script")
            at = free[0]
        merged[at] = d
        if at in free:
            free.remove(at)
    return merged


def answer(kind: str, text: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The LLM's scripts: the rules check; all passing moves to the fact check."""
    session = load(kind, out_dir)
    if session is None:
        raise WriteError("Start a session first")
    stripped = text.strip()
    if stripped.startswith("{") and '"scripts"' not in stripped[:200]:
        stripped = '{"scripts": [' + stripped + "]}"  # one script pasted on its own
    parsed = llm_json.parse(stripped, Answer, WriteError)
    drafts = merge(session, parsed.scripts)
    session["drafts"] = drafts
    if session_format(session) == "story":
        for d in drafts:
            d["format"] = "story"
    session["problems"] = {str(k): problems_of(d) for k, d in enumerate(drafts, 1)}
    if len(drafts) != len(session["topics"]):
        session["problems"]["0"] = [f"{len(drafts)} scripts for {len(session['topics'])} questions"]
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


def can_accept(session: dict[str, Any]) -> bool:
    """The fact checker flagged something, and the scripts themselves pass the rules: the owner may decide."""
    return session["step"] == "fix" and bool(session.get("flagged")) and all(not problems_of(d) for d in session["drafts"])


def accept(kind: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The owner read what the fact checker flagged and keeps the scripts as they are (a checker always finds
    something new; this ends the loop). What was flagged is kept in the queued script."""
    session = load(kind, out_dir)
    if session is None or not can_accept(session):
        raise WriteError("Nothing to accept: paste the fact checker's answer first")
    session["accepted"] = session["flagged"]
    session["flagged"], session["problems"] = {}, {str(k): [] for k in range(1, len(session["drafts"]) + 1)}
    session["step"] = "ready"
    return _save(session, out_dir)


def skip_rules(kind: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The owner keeps the scripts though they break the rules (the fix prompt went round enough); the fact check
    comes next, and can be skipped too. The script is marked, so the slots still make it; a broken one fails when
    it is drawn, with the reason on the Plan."""
    session = load(kind, out_dir)
    if session is None or session["step"] != "fix" or not session["drafts"]:
        raise WriteError("Nothing to skip: paste the LLM's answer first")
    session["skipped_rules"] = True
    session["flagged"], session["problems"] = {}, {str(k): [] for k in range(1, len(session["drafts"]) + 1)}
    session["step"] = "facts"
    return _save(session, out_dir)


def skip_facts(kind: str, out_dir: Path | None = None) -> dict[str, Any]:
    """The owner skips the second LLM's fact check (the rules check already wants 2 sources for every claim)."""
    session = load(kind, out_dir)
    if session is None or session["step"] != "facts":
        raise WriteError("The scripts must pass the rules check first")
    session["skipped_facts"] = True
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


def _mark_used(asked: list[dict[str, Any]], stamp: str, out_dir: Path | None = None) -> None:
    """The questions these scripts answer count as used on the list (added to it when they came from elsewhere)."""
    found = topics(out_dir)
    done = {_norm(t["question"]) for t in asked}
    for t in found:
        if _norm(t["question"]) in done:
            t["used"] = True
    listed = {_norm(t["question"]) for t in found}
    found += [{**t, "used": True, "added": stamp[:8]} for t in asked if _norm(t["question"]) not in listed]
    save_topics(found, out_dir)


def save_scripts(drafts: list[dict[str, Any]], asked: list[dict[str, Any]], out_dir: Path | None = None) -> dict[str, Any]:
    """Checked scripts into output/pricetagwhy/queue/ (the slots take them by name, so in this order), on this
    machine or, through Discord, on the hub (channel_hub.PriceTagWhy.queue). A script whose title or question
    is already in the queue folder (waiting or made) is skipped, so one topic never makes two videos. {"queued": [names], "skipped": [{"title", "why"}], "waiting": n}."""
    folder = pw_channel.queue_dir(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    have = set()  # titles and questions already in the queue folder (made or waiting): one video per topic
    for path in folder.glob("*.json"):
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        have |= {_norm(old.get(k, "")) for k in ("title", "question", "topic")} - {""}
    saved, skipped = [], []
    for k, d in enumerate(drafts, 1):
        mine = {_norm(d.get(k, "")) for k in ("title", "question", "topic")} - {""}
        if mine & have:
            skipped.append({"title": d.get("title", ""), "why": "a script for this question is already queued or made"})
            continue
        have |= mine
        path = folder / f"{stamp}-{k:02d}-{slug(d.get('title', ''))}.json"
        path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        saved.append(path.name)
    _mark_used(asked, stamp, out_dir)
    return {"queued": saved, "skipped": skipped, "waiting": len(pw_channel.queued(out_dir))}


def queue(kind: str, out_dir: Path | None = None, deliver=None) -> dict[str, Any]:
    """The checked scripts into the queue: this machine's, or the Linux hub's through `deliver(bundle)` on the
    sending machine (the questions are marked used here too, so this list never offers them again)."""
    session = load(kind, out_dir)
    if session is None or session["step"] != "ready":
        raise WriteError("Both checks must pass first")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    drafts = []
    for k, d in enumerate(session["drafts"], 1):
        flags = (session.get("accepted") or {}).get(str(k), [])
        how = ("skipped by the owner" if session.get("skipped_facts") else
               "accepted by the owner with flags" if flags else "pasted fact check")
        asked = session["topics"][k - 1]["question"] if k <= len(session["topics"]) else ""  # the topic it answers
        drafts.append({**d, **({"topic": asked} if asked else {}), "checked": {"rules": "skipped by the owner" if session.get("skipped_rules") else True, "facts": how, **({"flags": flags} if flags else {}), "at": stamp}})
    if deliver is None:
        done = save_scripts(drafts, session["topics"], out_dir)
    else:
        done = {**deliver({"scripts": drafts, "topics": session["topics"]}), "where": "linux"}
        _mark_used(session["topics"], stamp, out_dir)
    _path(kind, out_dir).unlink(missing_ok=True)
    return done


def view(kind: str, out_dir: Path | None = None, found: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The tab's state: the step, the prompt to copy, the drafts with their problems; with no session, the next
    topics of each format."""
    session = load(kind, out_dir)
    if session is None:
        all_ = topics(out_dir) if found is None else found
        n = 1 if kind == "one" else DEFAULT_COUNT
        return {"kind": kind, "step": "none", "topics": next_topics(n, out_dir, of_format(all_, "why")),
                "stories": next_topics(n, out_dir, of_format(all_, "story")), "formats": list(FORMATS)}
    prompt = {"write": write_prompt, "fix": fix_prompt, "facts": facts_prompt}.get(session["step"])
    return {"kind": kind, "format": session_format(session), "step": session["step"], "topics": session["topics"], "prompt": prompt(session) if prompt else "",
            "drafts": [{"title": d.get("title", ""), "words": pw_channel.words(pw_channel.narration(d)),
                        "sources": len(d.get("sources") or []), "problems": session["problems"].get(str(k), []),
                        "flagged": session["flagged"].get(str(k), [])} for k, d in enumerate(session["drafts"], 1)],
            "general": session["problems"].get("0", []), "started": session.get("started", ""),
            "can_accept": can_accept(session), "after_facts": bool(session.get("flagged")),
            "skipped_rules": bool(session.get("skipped_rules"))}
