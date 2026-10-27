"""Borderline Why topics: the questions the channel answers, each with the Wikipedia article its research starts
from. A hand-picked seed list comes first; when fewer than REFILL_AT are left, Gemini proposes more (each one
kept only when its Wikipedia article exists). Used and rejected topics are remembered in
output/borderlinewhy/topics.json, so no question is made twice.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

REFILL_AT = 8
_lock = threading.Lock()

# (question, Wikipedia article). Map-shaped "why" questions: an odd border, an enclave, a capital, a line.
SEEDS: list[tuple[str, str]] = [
    ("Why is there a piece of the USA you can only drive to through Canada?", "Northwest Angle"),
    ("Why does a Spanish town sit completely inside France?", "Llívia"),
    ("Why do some houses in Baarle have a border running through them?", "Baarle-Hertog"),
    ("Why does an island switch countries every six months?", "Pheasant Island"),
    ("Why is a tip of Washington State cut off from the rest of the USA?", "Point Roberts, Washington"),
    ("Why does Russia own a piece of land between Poland and Lithuania?", "Kaliningrad Oblast"),
    ("Why is Gambia a thin strip inside Senegal?", "The Gambia"),
    ("Why is Lesotho completely surrounded by South Africa?", "Lesotho"),
    ("Why does Namibia have a long thin arm reaching east?", "Caprivi Strip"),
    ("Why does Kentucky own a piece of land you can't reach from Kentucky?", "Kentucky Bend"),
    ("Why does the border between Sweden and Finland zigzag across a tiny island?", "Märket"),
    ("Why does no country want a piece of land between Egypt and Sudan?", "Bir Tawil"),
    ("Why does Spain own two cities in Africa?", "Ceuta"),
    ("Why does a German town use the Swiss franc?", "Büsingen am Hochrhein"),
    ("Why is an Italian village surrounded by Switzerland?", "Campione d'Italia"),
    ("Why does Lake Constance have no agreed border?", "Lake Constance"),
    ("Why did India and Bangladesh swap 162 pieces of land in 2015?", "India–Bangladesh enclaves"),
    ("Why does Brazil speak Portuguese when the rest of South America speaks Spanish?", "Treaty of Tordesillas"),
    ("Why is Bolivia landlocked?", "War of the Pacific"),
    ("Why did the USA buy Alaska from Russia?", "Alaska purchase"),
    ("Why does Oklahoma have a panhandle?", "Oklahoma Panhandle"),
    ("Why did Ohio and Michigan almost go to war over Toledo?", "Toledo War"),
    ("Why is most of Ellis Island in New Jersey?", "Ellis Island"),
    ("Why is Washington, D.C. not part of any state?", "Washington, D.C."),
    ("Why is Canberra the capital of Australia, not Sydney?", "Canberra"),
    ("Why did Brazil build a new capital in the middle of nowhere?", "Brasília"),
    ("Why is Ankara Turkey's capital, not Istanbul?", "Ankara"),
    ("Why does all of China use a single time zone?", "Time in China"),
    ("Why is Nepal 15 minutes off from everyone else's time?", "Nepal Standard Time"),
    ("Why does the International Date Line zigzag?", "International Date Line"),
    ("Why is San Marino a country inside Italy?", "San Marino"),
    ("Why does Andorra have two princes, and neither is from Andorra?", "Co-princes of Andorra"),
    ("Why is Vatican City its own country?", "Lateran Treaty"),
    ("Why did Panama split from Colombia?", "Separation of Panama from Colombia"),
    ("Why do Russia and the USA sit less than 4 kilometres apart?", "Diomede Islands"),
    ("Why did Denmark and Canada fight a 'whisky war' over a rock?", "Hans Island"),
    ("Why does anyone get to live in Svalbard without a visa?", "Svalbard Treaty"),
    ("Why is Antarctica sliced up like a pie?", "Territorial claims in Antarctica"),
    ("Why is there a line between Maryland and Pennsylvania everyone knows by name?", "Mason–Dixon line"),
    ("Why is Gibraltar British?", "Gibraltar"),
    ("Why did the USA and Britain almost go to war over a pig?", "Pig War"),
    ("Why is there a gap in the Pan-American Highway?", "Darién Gap"),
    ("Why does Greenland belong to Denmark?", "History of Greenland"),
    ("Why does Belgium have a German-speaking region?", "German-speaking Community of Belgium"),
    ("Why does the Suez Canal belong to Egypt?", "Suez Crisis"),
    ("Why is Liechtenstein one of the last countries ruled by a prince?", "Liechtenstein"),
    ("Why does the Dominican Republic share an island with Haiti?", "Treaty of Aranjuez (1777)"),
]


class Topic(BaseModel):
    question: str
    article: str
    source: str = "seed"  # seed | gemini | manual (added on the dashboard: goes first)


class TopicState(BaseModel):
    used: dict[str, str] = Field(default_factory=dict)  # article -> when it got a video
    rejected: dict[str, str] = Field(default_factory=dict)  # article -> why the research or the script failed
    proposed: list[Topic] = Field(default_factory=list)  # Gemini's extra topics


def state_path(out_dir: Path) -> Path:
    return out_dir / "topics.json"


def load(out_dir: Path) -> TopicState:
    try:
        return TopicState.model_validate_json(state_path(out_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TopicState()


def save(state: TopicState, out_dir: Path) -> None:
    path = state_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def all_topics(state: TopicState) -> list[Topic]:
    return [Topic(question=q, article=a) for q, a in SEEDS] + list(state.proposed)


def remaining(out_dir: Path) -> list[Topic]:
    """The topics still to make: the ones added by hand first, then the seeds, then Gemini's."""
    state = load(out_dir)
    done = set(state.used) | set(state.rejected)
    left = [t for t in all_topics(state) if t.article not in done]
    return [t for t in left if t.source == "manual"] + [t for t in left if t.source != "manual"]


def add(out_dir: Path, question: str, article: str) -> Topic:
    """A question added by hand (its Wikipedia article checked by the caller); it is made next."""
    topic = Topic(question=question.strip(), article=article.strip(), source="manual")
    with _lock:
        state = load(out_dir)
        state.proposed = [t for t in state.proposed if t.article != topic.article] + [topic]
        state.rejected.pop(topic.article, None)
        save(state, out_dir)
    return topic


def listing(out_dir: Path) -> list[dict[str, Any]]:
    """Every topic with its state, in the order they will be made (done ones last)."""
    state = load(out_dir)
    left = remaining(out_dir)
    order = {t.article: i for i, t in enumerate(left)}
    out = []
    for t in all_topics(state):
        if t.article in state.used:
            status, note = "used", state.used[t.article]
        elif t.article in state.rejected:
            status, note = "dropped", state.rejected[t.article]
        else:
            status, note = "next" if order.get(t.article) == 0 else "waiting", ""
        out.append({**t.model_dump(), "status": status, "note": note, "order": order.get(t.article)})
    return sorted(out, key=lambda r: (r["order"] is None, r["order"] if r["order"] is not None else 0))


def next_topic(out_dir: Path, skip: set[str] | None = None) -> Topic | None:
    """The first unused topic (seeds in order, then Gemini's), not in `skip`."""
    return next((t for t in remaining(out_dir) if t.article not in (skip or set())), None)


def mark(out_dir: Path, article: str, used: bool, note: str = "") -> None:
    with _lock:
        state = load(out_dir)
        stamp = datetime.now().replace(microsecond=0).isoformat()
        if used:
            state.used[article] = stamp
            state.rejected.pop(article, None)
        else:
            state.rejected[article] = f"{stamp} {note}".strip()
        save(state, out_dir)


class Proposal(BaseModel):
    question: str = Field(description="A 'Why...?' question a curious viewer would ask, under 90 characters.")
    article: str = Field(description="The exact title of the English Wikipedia article that answers it.")


class Proposals(BaseModel):
    topics: list[Proposal] = Field(default_factory=list)


PROPOSE_PROMPT = """You pick topics for Borderline Why, a YouTube Shorts channel that answers surprising geography and
history questions with animated maps (borders, enclaves, capitals, time zones, treaties, odd lines on the map).

Propose 20 new questions. Each must:
- be a real, well-documented case with an English Wikipedia article (give its exact title);
- be shown well on a map (places, borders, lines, movements);
- have a surprising but true answer that fits in 60 seconds;
- avoid active wars, ongoing ethnic conflicts and current elections.

Do not repeat any of these articles: {used}
"""


def propose(out_dir: Path, client=None, check=None) -> list[Topic]:
    """Ask Gemini for 20 more topics; keep the ones whose Wikipedia article exists. `check(article)`: the
    existence test (tests pass a fake)."""
    from google.genai import errors, types

    from modules import bw_script
    from trendclip import script_writer

    client, model = bw_script.gemini(client)
    state = load(out_dir)
    used = sorted({t.article for t in all_topics(state)} | set(state.used) | set(state.rejected))
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=Proposals, temperature=0.9)
    try:
        response, _ = script_writer._generate_with_fallback(client, model, [PROPOSE_PROMPT.format(used=", ".join(used))], config,
                                                            lambda f, m: None, errors.APIError)
    except errors.APIError as err:
        raise bw_script.ScriptError(str(script_writer._explain(err, model))) from err
    found = response.parsed if isinstance(response.parsed, Proposals) else Proposals.model_validate_json(response.text)
    check = check or bw_script.article_exists
    new = []
    for p in found.topics:
        article = p.article.strip()
        if not article or article in used or not p.question.strip():
            continue
        try:
            title = check(article)
        except Exception as err:  # noqa: BLE001 - Wikipedia busy: skip this one
            logger.info("Topic %r not checked: %s", article, err)
            continue
        if title and title not in used:
            new.append(Topic(question=p.question.strip(), article=title, source="gemini"))
            used.append(title)
    with _lock:
        state = load(out_dir)
        state.proposed.extend(new)
        save(state, out_dir)
    return new


def ensure(out_dir: Path, client=None) -> int:
    """Refill the list when it runs low; the number of topics left. Never raises (the seeds may still do)."""
    left = len(remaining(out_dir))
    if left >= REFILL_AT:
        return left
    try:
        propose(out_dir, client)
    except Exception as err:  # noqa: BLE001
        logger.warning("Borderline Why: could not get new topics from Gemini: %s", err)
    return len(remaining(out_dir))


def summary(out_dir: Path) -> dict[str, Any]:
    state = load(out_dir)
    return {"used": len(state.used), "rejected": len(state.rejected), "left": len(remaining(out_dir)),
            "next": (next_topic(out_dir) or Topic(question="", article="")).question}
