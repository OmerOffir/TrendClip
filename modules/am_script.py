"""After Market script: Gemini turns the session's facts (modules.am_data) into a 45-60 s Shorts script whose
every number is checked against the facts, so the voice never says a figure the data did not give.

    python -m modules.am_script output/aftermarket/2026-10-27_close/facts.json          # -> script.json next to it
    python -m modules.am_script facts.json --template                                   # no Gemini: the built-in script

A scene names its visual: "board" (the indices card), "ticker:TSLA" (one mover's card), "crypto",
"calendar" (earnings and macro due), or "broll" with an image_prompt for the GPU. The spoken script is
hook_text + every narration: MIN_WORDS-MAX_WORDS words, sentences of at most MAX_SENTENCE_WORDS.
Numbers: every number in the script must be one from the facts, rounded as it would be spoken (394.63
may be said as 395 or 394.63; 6.021 % as 6 or 6.0 %). A script that breaks a rule goes back to Gemini
with the problems, ATTEMPTS times; `template_script` is the fallback that needs no Gemini at all.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MIN_WORDS, MAX_WORDS = 95, 135
MAX_SENTENCE_WORDS = 14
WORDS_PER_SECOND = 2.5
ATTEMPTS = 3
MIN_SCENES, MAX_SCENES = 5, 8
VISUALS = ("board", "crypto", "calendar", "broll")
FREE_NUMBERS = set(range(0, 32)) | {40, 50, 60, 70, 80, 90, 100, 200, 500, 1000} | set(range(1990, 2031))  # counts, days, years
MAX_HOOK_WORDS = 14
# A hook that opens like a newsreader loses the scroll: these never appear in it.
WEAK_HOOK = re.compile(r"\b(here is|here's|welcome|let's|lets|in this video|today's|report|update|recap|wrap|brief|"
                       r"good morning|good evening|hey|stay tuned|the picture)\b", re.I)
SLATE_WHAT = {"premarket": "Your pre-market report.", "close": "Your closing report.", "weekend": "Your weekend review."}
CHANNEL_NAME = "After Market"


class SceneDraft(BaseModel):
    narration: str = Field(description="1-2 short spoken sentences, at most 14 words each.")
    visual: str = Field(description="board | ticker:<SYMBOL> | crypto | calendar | broll")
    overlay_text: str = Field(description="2-5 words of on-screen text in CAPITALS, e.g. 'TSLA +6%'.")
    image_prompt: str = Field("", description="For broll only: a prompt for an AI image generator (no text, no faces).")


class ScriptDraft(BaseModel):
    title: str = ""
    hook_text: str = ""
    thumbnail_text: str = Field("", description="2-4 words in CAPITALS for the first frame.")
    scenes: list[SceneDraft] = Field(default_factory=list)


class Scene(BaseModel):
    scene_id: int
    narration: str
    visual: str
    overlay_text: str
    image_prompt: str = ""


class Script(BaseModel):
    slot: str
    date: str
    title: str
    hook_text: str
    thumbnail_text: str
    estimated_duration_seconds: int
    total_word_count: int
    scenes: list[Scene]
    writer: str = "gemini"  # or "template"


class ScriptError(RuntimeError):
    pass


# --------------------------------------------------------------------------- the facts, compact


def compact_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """The facts without the candle series and links: what Gemini reads."""
    def q(row: dict) -> dict:
        keep = ("symbol", "name", "price", "previous_close", "change_pct", "extended_price", "extended_change_pct",
                "session", "day_high", "day_low", "reported")
        return {k: row[k] for k in keep if row.get(k) is not None}

    out: dict[str, Any] = {
        "slot": facts.get("slot"), "date": facts.get("date"), "ny_time": facts.get("ny_time"),
        "next_session": facts.get("next_session"),
        "indices": [q(r) for r in facts.get("indices") or []],
        "futures": [q(r) for r in facts.get("futures") or []],
        "vix": q(facts["vix"]) if facts.get("vix") else None,
        "movers": [{**q(m), "why": [h["title"] for h in (m.get("why") or [])]} for m in facts.get("movers") or []],
        "earnings": {k: [{kk: e[kk] for kk in ("symbol", "name", "time", "eps_forecast") if e.get(kk)} for e in v[:6]]
                     for k, v in (facts.get("earnings") or {}).items()},
        "macro": [{k: ev[k] for k in ("title", "time_et", "impact", "forecast", "previous") if ev.get(k)} for ev in facts.get("macro") or []],
        "crypto": [{k: c[k] for k in ("symbol", "name", "price", "change_24h_pct") if c.get(k) is not None} for c in facts.get("crypto") or []],
        "crypto_fear_greed": facts.get("crypto_fear_greed"),
        "news": [f"{h['title']} ({h['source']})" if h.get("source") else h["title"] for h in (facts.get("news") or [])[:10]],
    }
    return out


# --------------------------------------------------------------------------- checks


NUMBER = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?(?![\w])")


def numbers_in(text: str) -> list[float]:
    out = []
    for m in NUMBER.finditer(text):
        raw = m.group().strip("$%+").replace(",", "")
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


def _walk(value: Any):
    if isinstance(value, dict):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk(v)
    elif isinstance(value, bool):
        return
    elif isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, str):
        yield from numbers_in(value)


def fact_numbers(facts: dict[str, Any]) -> set[float]:
    """Every number in the facts, with the roundings a narrator uses: 394.63 -> 394.6, 395, 390, 400;
    6.021 -> 6.02, 6.0, 6; 80,064 -> 80,060, 80,100, 80,000."""
    out: set[float] = set()
    for f in _walk(compact_facts(facts)):
        out.add(round(f, 2))
        out.add(abs(round(f, 2)))
        for d in (1, 0):
            out.add(round(f, d))
            out.add(abs(round(f, d)))
        if abs(f) >= 100:
            for k in (-1, -2, -3):
                out.add(float(round(f, k)))
                out.add(abs(float(round(f, k))))
    return out


def unknown_numbers(text: str, allowed: set[float]) -> list[str]:
    bad = []
    for n in numbers_in(text):
        if n in allowed or abs(n) in allowed or n in FREE_NUMBERS:
            continue
        bad.append(f"{n:g}")
    return list(dict.fromkeys(bad))


def words(text: str) -> int:
    return len(text.split())


def spoken(draft: ScriptDraft | Script) -> str:
    return " ".join(p.strip() for p in [draft.hook_text, *(s.narration for s in draft.scenes)] if p.strip())


ABBREVIATION = re.compile(r"\b(Inc|Corp|Ltd|Co|Mr|Mrs|Dr|vs|St|Jr|Sr|S|P|U|A|E|F)\.", re.I)


def sentences(text: str) -> list[str]:
    """Split at sentence ends; a company suffix ("XP Inc.") is not one."""
    text = ABBREVIATION.sub(lambda m: m.group(1), text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def long_sentences(draft: ScriptDraft | Script) -> list[str]:
    return [s for s in sentences(spoken(draft)) if words(s) > MAX_SENTENCE_WORDS]


def slate(facts: dict[str, Any]) -> str:
    """The spoken slate right after the hook: the weekday and date, and which report this is
    ("Monday, October 26th. Your closing report.")."""
    try:
        day = dt.date.fromisoformat(facts.get("date", ""))
    except ValueError:
        return SLATE_WHAT.get(facts.get("slot", ""), "")
    n = day.day
    suffix = "th" if 11 <= n <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{day.strftime('%A, %B')} {n}{suffix}. {SLATE_WHAT.get(facts.get('slot', ''), '')}".strip()


def hook_problems(hook: str) -> list[str]:
    """What makes a hook weak: no number, a newsreader opener, too long, a question."""
    hook = hook.strip()
    problems = []
    if not hook:
        return ["no hook"]
    if not re.search(r"\d", hook) and not re.search(r"\b(half|double|triple|record|all-time)\b", hook, re.I):
        problems.append("the hook has no number")
    weak = WEAK_HOOK.search(hook)
    if weak:
        problems.append(f"the hook opens like a newsreader (\"{weak.group()}\"): lead with the shock, no framing")
    if words(hook) > MAX_HOOK_WORDS:
        problems.append(f"the hook is {words(hook)} words (at most {MAX_HOOK_WORDS})")
    if len(sentences(hook)) > 2:
        problems.append("the hook is more than two sentences")
    return problems


def visuals_for(facts: dict[str, Any]) -> list[str]:
    return list(VISUALS) + [f"ticker:{m['symbol']}" for m in facts.get("movers") or []]


def check(draft: ScriptDraft, facts: dict[str, Any]) -> list[str]:
    """The rules, counted with the slate that finalize() adds after the hook."""
    problems = []
    n = words(spoken(draft)) + words(slate(facts))
    if n < MIN_WORDS:
        problems.append(f"only {n} words (at least {MIN_WORDS})")
    if n > MAX_WORDS:
        problems.append(f"{n} words (at most {MAX_WORDS})")
    problems += hook_problems(draft.hook_text)
    if not (MIN_SCENES <= len(draft.scenes) <= MAX_SCENES):
        problems.append(f"{len(draft.scenes)} scenes ({MIN_SCENES}-{MAX_SCENES})")
    long = long_sentences(draft)
    if long:
        problems.append(f"{len(long)} sentence(s) over {MAX_SENTENCE_WORDS} words: \"{long[0][:60]}\"")
    allowed = fact_numbers(facts)
    bad = unknown_numbers(spoken(draft) + " " + " ".join(s.overlay_text for s in draft.scenes), allowed)
    if bad:
        problems.append("numbers not in the facts: " + ", ".join(bad[:6]))
    known = visuals_for(facts)
    for i, s in enumerate(draft.scenes, 1):
        if s.visual.strip() not in known:
            problems.append(f"scene {i} visual '{s.visual}' is not one of {', '.join(known)}")
        if s.visual.strip() == "broll" and not s.image_prompt.strip():
            problems.append(f"scene {i} is broll without an image_prompt")
    return problems


def finalize(draft: ScriptDraft, facts: dict[str, Any], writer: str = "gemini") -> Script:
    """The Script, with the slate (date, which report) spoken first thing after the hook."""
    opening = slate(facts)
    if opening and draft.scenes:
        first = draft.scenes[0]
        draft = draft.model_copy(update={"scenes": [first.model_copy(update={"narration": f"{opening} {first.narration.strip()}"}),
                                                    *draft.scenes[1:]]})
    n = words(spoken(draft))
    return Script(
        slot=facts.get("slot", ""), date=facts.get("date", ""), title=draft.title.strip()[:100],
        hook_text=draft.hook_text.strip(), thumbnail_text=" ".join(draft.thumbnail_text.split()).upper()[:40],
        estimated_duration_seconds=round(n / WORDS_PER_SECOND), total_word_count=n, writer=writer,
        scenes=[Scene(scene_id=i, narration=s.narration.strip(), visual=s.visual.strip(),
                      overlay_text=s.overlay_text.strip().upper(), image_prompt=s.image_prompt.strip())
                for i, s in enumerate(draft.scenes, 1)])


# --------------------------------------------------------------------------- Gemini


PROMPT = """You write scripts for After Market, a YouTube Shorts channel (9:16, 45-60 seconds) for traders:
a sharp daily brief before the New York open and at the close, over charts.

TONE: a fast, confident market desk voice. Concrete, no hype, no advice ("buy", "sell", "you should" are
banned), no "guys", no exclamation marks. Short sentences for text-to-speech, 8-14 words each, never more
than {max_sentence} words. Numbers are said the way a trader says them: prices rounded to the dollar
unless under 20 dollars, percentages with at most one decimal ("up 6 percent", "down 2.4 percent").
Write "percent", not "%". Write index levels with the thousands ("7,679", "26,198").

THIS VIDEO: slot = {slot}. {slot_brief}

STRUCTURE:
- hook_text (0-3 s): the line that stops the scroll, spoken first. At most {max_hook} words, one or two
  sentences, ALWAYS with a number, and it must carry a stake or a surprise, not just a quote:
    * the shock and its consequence: "XP lost half its value in one session."
    * the contrast: "The S&P closed at a record. Three stocks lost 30 percent."
    * the clock and the stake: "Visa reports in 3 hours. 728 billion dollars on the line."
    * what it means for tomorrow: "Nvidia fell 2.4 percent. Its earnings are 5 days away."
  Pick the most surprising fact in FACTS (the biggest move, a record, a huge name reporting), not the
  index tally. NEVER open with framing or a greeting: no "here is", "welcome", "let's", "today's",
  "report", "update", "recap", "the picture". No questions.
- Right after the hook the video says the date and which report it is by itself ("{slate}"): do not
  repeat the date or the words "pre-market report" / "closing report" anywhere.
- scenes: {min_scenes} to {max_scenes} scenes, each 1-2 sentences of narration, in this order of priority:
  1. the big picture (visual "board"): indices or futures, with their moves;
  2. two or three movers (visual "ticker:<SYMBOL>", one scene each, only symbols from FACTS.movers): the
     move, and why if the "why" headlines say so, else say what the stock is reacting to is unclear;
  3. what is due (visual "calendar"): the earnings and macro events that matter next;
  4. crypto (visual "crypto") in one scene: Bitcoin's move, the fear & greed reading;
  5. the last scene is a one-line takeaway on what traders watch next (visual "broll" or "board").
  A "broll" scene also has image_prompt: a photoreal prompt with no text, no logos, no faces
  ("a trading floor at dawn, screens glowing, cinematic").
- overlay_text per scene: 2-5 words in CAPITALS, the number of the scene ("S&P +0.7%", "VISA REPORTS 4PM").

LENGTH: hook_text + every narration = {min_words} to {max_words} words. Every second counts.

TRUTH: use ONLY numbers that appear in FACTS (rounded as above). Never invent a price, a move, a time
or a reason. A headline in "why" is a reason only if it clearly explains the move; otherwise do not
guess. Earnings: "reports before the bell" / "after the bell"; never state results that are not in FACTS.

title: a YouTube title under 60 characters with the strongest number ("Tesla +6% Before The Bell").
thumbnail_text: 2-4 words in CAPITALS for the first frame, the strongest number ("TSLA +6%").

FACTS (JSON):
{facts}"""

SLOT_BRIEF = {
    "premarket": "The market opens in about 90 minutes. Lead with the futures and the pre-market movers "
                 "(extended_price / extended_change_pct, session 'pre'); last night's earnings reactions are "
                 "the story when a mover has reported=true. End with what is due today (earnings before the "
                 "bell, macro events).",
    "close": "The closing bell just rang. Lead with how the indices finished and the session's biggest movers "
             "(change_pct). Then what reports after the bell today and tomorrow's calendar.",
    "weekend": "No session today. The week's moves (the indices' 'week' values), the biggest stories, "
               "crypto over the weekend, and what is due in the week ahead.",
}

FIX_PROMPT = """Your script breaks the rules: {problems}.
Fix those and keep everything else (the facts, the scenes' visuals, the order). Return the full script
again in the same JSON format.

Your script:
{draft}"""


def build_prompt(facts: dict[str, Any]) -> str:
    slot = facts.get("slot", "close")
    return PROMPT.format(slot=slot, slot_brief=SLOT_BRIEF.get(slot, SLOT_BRIEF["close"]), min_words=MIN_WORDS, slate=slate(facts),
                         max_hook=MAX_HOOK_WORDS,
                         max_words=MAX_WORDS, max_sentence=MAX_SENTENCE_WORDS, min_scenes=MIN_SCENES,
                         max_scenes=MAX_SCENES, facts=json.dumps(compact_facts(facts), ensure_ascii=False))


def _gemini(client=None):
    from google import genai

    from trendclip import script_writer
    from trendclip.config import get_settings

    settings = get_settings()
    key = script_writer.gemini_api_key(settings)
    if client is None and not key:
        raise ScriptError("GEMINI_API_KEY is missing in .env")
    return client or genai.Client(api_key=key), script_writer.gemini_model(settings)


def _ask(client, model: str, prompt: str) -> ScriptDraft:
    from google.genai import errors, types

    from trendclip import script_writer

    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=ScriptDraft, temperature=0.7)
    try:
        response, _ = script_writer._generate_with_fallback(client, model, [prompt], config, lambda f, m: None, errors.APIError)
    except errors.APIError as err:
        raise ScriptError(str(script_writer._explain(err, model))) from err
    if isinstance(response.parsed, ScriptDraft):
        return response.parsed
    return ScriptDraft.model_validate_json(response.text)


def write_script(facts: dict[str, Any], client=None) -> Script:
    client, model = _gemini(client)
    prompt = build_prompt(facts)
    draft = _ask(client, model, prompt)
    problems: list[str] = []
    for attempt in range(ATTEMPTS):
        problems = check(draft, facts)
        if not problems:
            return finalize(draft, facts)
        logger.info("Attempt %d: %s", attempt + 1, "; ".join(problems))
        if attempt == ATTEMPTS - 1:
            break
        draft = _ask(client, model, prompt + "\n\n" + FIX_PROMPT.format(problems="; ".join(problems), draft=draft.model_dump_json()))
    raise ScriptError(f"Gemini missed the rules {ATTEMPTS} times ({'; '.join(problems)})")


# --------------------------------------------------------------------------- the template (no Gemini)


def _pct_words(change: float | None, decimals: int = 1) -> str:
    if change is None:
        return "flat"
    value = round(abs(change), decimals)
    value_s = f"{value:g}"
    return f"{'up' if change >= 0 else 'down'} {value_s} percent"


def _price_words(price: float | None) -> str:
    if price is None:
        return ""
    return f"{price:,.2f} dollars" if price < 20 else f"{price:,.0f} dollars"


def due_earnings(facts: dict[str, Any]) -> list[dict]:
    """The earnings still ahead: today's before the open; at the close, tonight's (today, after the bell,
    marked when="tonight") then the next session's."""
    earnings = facts.get("earnings") or {}
    if facts.get("slot") == "close":
        tonight = [{**e, "when": "tonight"} for e in earnings.get("today") or [] if e.get("time") == "post"]
        return tonight + list(earnings.get("next") or [])
    return list(earnings.get("today" if facts.get("slot") == "premarket" else "next") or [])


def template_hook(lead: dict, move: float | None, spx: dict | None, slot: str) -> str:
    """The template's hook: the lead mover's shock with a stake, in a trader's words."""
    sym = lead["symbol"]
    when = {"premarket": "before the open", "close": "in one session", "weekend": "this week"}[slot]
    if move is None:
        return f"{sym} is the name every desk is watching {when}."
    size = abs(move)
    if size >= 40:
        return f"{sym} lost half its value {when}." if move < 0 else f"{sym} gained {size:.0f} percent {when}. Half the float changed hands."
    if size >= 15:
        return f"{sym} {'crashed' if move < 0 else 'exploded'} {size:.0f} percent {when}. Someone was very wrong."
    if spx and spx.get("change_pct") is not None and (move < 0) != (spx["change_pct"] < 0):
        return (f"The S&P {_pct_words(spx['change_pct'], 1)}. {sym} went the other way, {_pct_words(move, 1)}.")
    return f"{sym} moved {size:.1f} percent {when}. That decides the next session."


def template_script(facts: dict[str, Any]) -> Script:
    """A plain script from the facts alone: the fallback when Gemini is out, and the tests' script."""
    slot = facts.get("slot", "close")
    indices = facts.get("indices") or []
    futures = facts.get("futures") or []
    movers = facts.get("movers") or []
    crypto = facts.get("crypto") or []
    earnings = facts.get("earnings") or {}
    lead = movers[0] if movers else None
    label = {"premarket": "before the bell", "close": "at the close", "weekend": "this week"}[slot]
    scenes: list[SceneDraft] = []
    spx = indices[0] if indices else None
    if lead:
        move = lead.get("extended_change_pct") if slot == "premarket" and lead.get("extended_change_pct") is not None else lead.get("change_pct")
        hook = template_hook(lead, move, spx, slot)
        thumb = f"{lead['symbol']} {move:+.1f}%" if move is not None else lead["symbol"]
    else:
        hook = f"The S and P 500 {_pct_words(spx['change_pct'] if spx else None)} {label}. One number decides tomorrow."
        thumb = f"S&P {spx['change_pct']:+.1f}%" if spx and spx.get("change_pct") is not None else "MARKETS"
    board_rows = (futures[:2] + indices[:2]) if slot == "premarket" and futures else indices[:3]
    if board_rows:
        parts = [f"{'The ' if 'futures' in r['name'] else ''}{r['name']} {_pct_words(r['change_pct'])}" for r in board_rows]
        vix = facts.get("vix")
        if vix and vix.get("price") is not None:
            parts.append(f"The VIX at {vix['price']:.1f}, {_pct_words(vix.get('change_pct'))}")
        scenes.append(SceneDraft(narration="The big picture. " + ". ".join(parts) + ".", visual="board",
                                 overlay_text=f"{board_rows[0]['name'].upper()} {board_rows[0]['change_pct']:+.1f}%"))
    for m in movers[:3]:
        move = m.get("extended_change_pct") if slot == "premarket" and m.get("extended_change_pct") is not None else m.get("change_pct")
        price = m.get("extended_price") if slot == "premarket" and m.get("extended_price") is not None else m.get("price")
        headline = (m["why"][0]["title"].split(" - ")[0].strip() if m.get("why") else "")
        why = f" {headline.rstrip('.')}." if headline and len(headline) < 70 and not headline.endswith("?") else ""
        if not why and m.get("day_low") is not None and m.get("day_high") is not None and slot != "premarket":
            why = f" Its day ran from {_price_words(m['day_low'])} to {_price_words(m['day_high'])}."
        scenes.append(SceneDraft(narration=f"{m['symbol']} {_pct_words(move)} to {_price_words(price)}.{why}",
                                 visual=f"ticker:{m['symbol']}", overlay_text=f"{m['symbol']} {move:+.1f}%" if move is not None else m["symbol"]))
    due = due_earnings(facts)
    macro = facts.get("macro") or []
    if due or macro:
        bits = []
        if due:
            tonight = [e["symbol"] for e in due if e["time"] == "post" and e.get("when") == "tonight"][:3]
            pre = [e["symbol"] for e in due if e["time"] == "pre"][:3]
            post = [e["symbol"] for e in due if e["time"] == "post" and e.get("when") != "tonight"][:3]
            if tonight:
                bits.append(f"{', '.join(tonight)} report after the bell tonight")
            if pre:
                bits.append(f"{', '.join(pre)} report before the bell{' tomorrow' if slot == 'close' else ''}")
            if post:
                bits.append(f"{', '.join(post)} report after the bell{' tomorrow' if slot == 'close' else ''}")
        if macro:
            bits.append(f"{macro[0]['title']} lands at {macro[0]['time_et']} Eastern")
        scenes.append(SceneDraft(narration="On deck. " + ". ".join(bits) + ".", visual="calendar",
                                 overlay_text=(due[0]["symbol"] + " REPORTS") if due else "MACRO DUE"))
    btc = next((c for c in crypto if c["symbol"] == "BTC"), None)
    if btc:
        fg = facts.get("crypto_fear_greed")
        eth = next((c for c in crypto if c["symbol"] == "ETH"), None)
        extra = f" Ethereum {_pct_words(eth['change_24h_pct'])}." if eth else ""
        extra += f" Crypto fear and greed reads {fg['value']}, {fg['label'].lower()}." if fg else ""
        scenes.append(SceneDraft(narration=f"Bitcoin is {_pct_words(btc['change_24h_pct'])} at {_price_words(btc['price'])}.{extra}",
                                 visual="crypto", overlay_text=f"BTC {btc['change_24h_pct']:+.1f}%"))
    scenes.append(SceneDraft(narration="That is the picture. Watch the open, and the names that reported." if slot == "premarket"
                             else "That is the close. Tomorrow's open starts with the names reporting tonight.",
                             visual="broll", overlay_text="WATCH THE OPEN" if slot == "premarket" else "SEE YOU AT THE OPEN",
                             image_prompt="a trading floor at dusk, rows of glowing monitors with charts, cinematic, photoreal, no text"))
    title = f"{lead['symbol']} {_pct_words(lead.get('change_pct')).replace(' percent', '%')} {label}".strip() if lead else f"Markets {label}"
    draft = ScriptDraft(title=title[:60].title(), hook_text=hook, thumbnail_text=thumb.upper(), scenes=scenes[:MAX_SCENES])
    return finalize(draft, facts, writer="template")


# --------------------------------------------------------------------------- files


def script_path(facts_file: Path) -> Path:
    return facts_file.with_name("script.json")


def save(script: Script, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(script.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("facts", type=Path)
    parser.add_argument("--template", action="store_true", help="no Gemini: the built-in script from the facts")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    facts = json.loads(args.facts.read_text(encoding="utf-8"))
    script = template_script(facts) if args.template else write_script(facts)
    path = save(script, args.out or script_path(args.facts))
    print(f"{script.title}\n  {script.hook_text}")
    for s in script.scenes:
        print(f"  [{s.visual}] {s.narration}  ({s.overlay_text})")
    print(f"{script.total_word_count} words, ~{script.estimated_duration_seconds}s, by {script.writer} -> {path}")


if __name__ == "__main__":
    main()
