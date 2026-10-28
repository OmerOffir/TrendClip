"""Borderline Why research and script: a topic's Wikipedia article and the outside pages it cites become the
sources; Gemini writes a 45-70 s narration split into map and card scenes, and lists its claims, each with a
quote from a source. The quotes are checked here word for word against the source text, a second Gemini pass
looks for anything in the narration the sources do not support, and every place a scene names must exist in
the map data. A draft that fails is sent back with the problems (ATTEMPTS times), then the topic is dropped.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from modules import bw_geo, bw_render
from modules.bw_topics import Topic

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = bw_geo.USER_AGENT
ARTICLE_CHARS = 14000
SOURCE_CHARS = 7000
MIN_SOURCE_CHARS = 1200
MAX_FETCH = 10  # cited pages tried per topic
MAX_SOURCES = 4  # outside pages kept besides Wikipedia
MIN_WORDS, MAX_WORDS = 110, 170  # about 45-70 s with the narrator
MAX_SENTENCE_WORDS = 24
MIN_SCENES, MAX_SCENES = 5, 14
MAX_CARDS = 3
ATTEMPTS = 3
SKIP_DOMAINS = ("wikipedia.org", "wikimedia.org", "wikidata.org", "books.google", "google.com", "archive.org/details",
                "jstor.org", "doi.org", "worldcat.org", "facebook.com", "twitter.com", "x.com", "youtube.com", "instagram.com",
                "amazon.", "ebay.", "geohack", "openstreetmap.org", "tools.wmflabs.org", "toolforge.org", "pdf")


class ScriptError(RuntimeError):
    pass


class TryLater(ScriptError):
    """Not the topic's fault (Wikipedia or Gemini busy): the topic stays on the list."""


# --------------------------------------------------------------------------- research


@dataclass
class Source:
    id: int
    url: str
    title: str
    text: str

    @property
    def domain(self) -> str:
        return urlparse(self.url).netloc.removeprefix("www.")

    @property
    def is_wikipedia(self) -> bool:
        return "wikipedia.org" in self.url


@dataclass
class Research:
    topic: Topic
    article: str  # the article's real title (after redirects)
    sources: list[Source] = field(default_factory=list)

    def source(self, sid: int) -> Source | None:
        return next((s for s in self.sources if s.id == sid), None)


def _get(url: str, params: dict | None = None, timeout: float = 20, tries: int = 4):
    """GET with Wikipedia's manners: a 429 or 503 waits (Retry-After, else 2, 4, 8 s) and tries again."""
    import time

    import requests

    for attempt in range(tries):
        r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if r.status_code in (429, 503) and attempt < tries - 1:
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(30.0, float(wait)) if wait.isdigit() else 2.0 * 2 ** attempt)
            continue
        r.raise_for_status()
        return r
    return r


# Wikipedia's Action API (api.php) is the first choice; when it fails (it had hours of random 503s on
# 2026-10-28 while the rest of the site worked), the same answers come from the REST API and the article page.
REST = "https://en.wikipedia.org/api/rest_v1/page/{kind}/{title}"
CUT_AT = ("See_also", "References", "Notes", "Further_reading", "External_links", "Citations", "Bibliography", "Sources",
          "Notes_and_references", "Footnotes")


def _rest(kind: str, title: str):
    """GET a REST endpoint for `title`; None on a 404 (no such page)."""
    import requests
    from urllib.parse import quote

    url = REST.format(kind=kind, title=quote(title.replace(" ", "_"), safe=""))
    try:
        return _get(url)
    except requests.HTTPError as err:
        if err.response is not None and err.response.status_code == 404:
            return None
        raise


def _fallback(what: str, first, second):
    """`first()`, else `second()` when the first raises; the second one's error is the one raised."""
    try:
        return first()
    except Exception as err:  # noqa: BLE001
        logger.warning("Wikipedia API failed for %s (%s); trying the REST API", what, err)
        return second()


def article_exists(title: str) -> str | None:
    """The article's canonical title, or None when English Wikipedia has no such page (a network error raises)."""
    def api():
        data = _get(WIKI_API, {"action": "query", "titles": title, "redirects": 1, "format": "json", "formatversion": 2}).json()
        pages = data.get("query", {}).get("pages", [])
        page = pages[0] if pages else {}
        return None if page.get("missing") or page.get("invalid") or not page.get("title") else page["title"]

    def rest():
        r = _rest("summary", title)
        return None if r is None else (r.json().get("title") or None)

    return _fallback(f"the title {title!r}", api, rest)


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("p", "br", "li", "h1", "h2", "h3", "div", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def page_text(raw: str) -> tuple[str, str]:
    """(title, readable text) of an HTML page: paragraphs of at least a few words, whitespace squeezed."""
    parser = _Text()
    try:
        parser.feed(raw)
    except Exception:  # noqa: BLE001 - broken HTML: keep what was read
        pass
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).split("\n")]
    text = "\n".join(line for line in lines if len(line.split()) >= 8)
    return html.unescape(parser.title.strip())[:200], text


def article_html(title: str) -> str:
    """The article as HTML from the REST API ("" when it has no such page)."""
    r = _rest("html", title)
    return "" if r is None else r.text


def html_text(raw: str) -> str:
    """The readable text of an article's HTML, up to its See also / References headings, footnote marks dropped."""
    cut = re.search(r'<h2[^>]*\bid="(?:%s)"' % "|".join(CUT_AT), raw)
    body = raw[: cut.start()] if cut else raw
    body = re.sub(r'<sup[^>]*class="[^"]*reference[^"]*"[^>]*>.*?</sup>', "", body, flags=re.S)
    return page_text(body)[1]


def wiki_text(title: str) -> tuple[str, str]:
    def api():
        data = _get(WIKI_API, {"action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "redirects": 1,
                               "format": "json", "formatversion": 2}).json()
        page = (data.get("query", {}).get("pages") or [{}])[0]
        text = page.get("extract") or ""
        text = re.split(r"\n== (See also|References|Notes|Further reading|External links|Citations|Bibliography) ==", text)[0]
        return page.get("title", title), text

    def rest():
        return title, html_text(article_html(title))

    real, text = _fallback(f"the text of {title!r}", api, rest)
    return real, text[:ARTICLE_CHARS]


def cited_links(title: str) -> list[str]:
    def api():
        data = _get(WIKI_API, {"action": "parse", "page": title, "prop": "externallinks", "redirects": 1, "format": "json",
                               "formatversion": 2}).json()
        return data.get("parse", {}).get("externallinks", [])

    def rest():
        found = re.findall(r'rel="mw:ExtLink[^"]*"[^>]*?href="(https?://[^"]+)"', article_html(title))
        return [html.unescape(u) for u in found]

    links = _fallback(f"the links of {title!r}", api, rest)
    out = []
    for url in links:
        low = url.lower()
        if not low.startswith("http") or any(d in low for d in SKIP_DOMAINS):
            continue
        out.append(url)
    official = [u for u in out if re.search(r"\.(gov|edu|mil|int)(\.[a-z]{2})?/|\.(gov|ac|edu)\.[a-z]{2}/|museum|historical|archives", u)]
    return list(dict.fromkeys(official + out))


def research(topic: Topic, progress=lambda m: None) -> Research:
    """The Wikipedia article and up to MAX_SOURCES of the pages it cites that can be read and talk about it."""
    try:
        title = article_exists(topic.article)
    except Exception as err:  # noqa: BLE001 - not the topic's fault: try it again another day
        raise TryLater(f"Wikipedia did not answer ({err})") from err
    if not title:
        raise ScriptError(f"No Wikipedia article called {topic.article!r}")
    progress(f"Reading Wikipedia: {title}")
    try:
        real, text = wiki_text(title)
    except Exception as err:  # noqa: BLE001 - both Wikipedia routes failed: try the topic again later
        raise TryLater(f"Wikipedia did not answer ({err})") from err
    if len(text) < MIN_SOURCE_CHARS:
        raise ScriptError(f"The Wikipedia article {real!r} is too short")
    out = Research(topic, real, [Source(1, f"https://en.wikipedia.org/wiki/{real.replace(' ', '_')}", f"{real} - Wikipedia", text)])
    keywords = {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", real)} or {real.lower()}
    try:
        links = cited_links(real)
    except Exception as err:  # noqa: BLE001
        logger.warning("Cited links of %s failed: %s", real, err)
        links = []
    domains: set[str] = set()
    for url in links[:MAX_FETCH * 2]:
        if len(out.sources) - 1 >= MAX_SOURCES or len(domains) >= MAX_FETCH:
            break
        domain = urlparse(url).netloc
        if domain in domains:
            continue
        domains.add(domain)
        progress(f"Reading a cited source: {domain}")
        try:
            r = _get(url, timeout=15)
            if "html" not in r.headers.get("content-type", "html"):
                continue
            page_title, body = page_text(r.text[:2_000_000])
        except Exception as err:  # noqa: BLE001 - paywalls, 403s, timeouts: try the next one
            logger.info("Source %s skipped: %s", url, err)
            continue
        if len(body) < MIN_SOURCE_CHARS or not any(k in body.lower() for k in keywords):
            continue
        out.sources.append(Source(len(out.sources) + 1, url, page_title or domain, body[:SOURCE_CHARS]))
    return out


# --------------------------------------------------------------------------- the draft


class ElementDraft(BaseModel):
    kind: str = Field(description="highlight | label | pin | ring | line | arrow | parallel | meridian | stamp")
    place: str = Field("", description="The place it is about, named as on a map (see the rules).")
    to: str = Field("", description="line / arrow: where it ends.")
    value: float = Field(0.0, description="parallel: the latitude; meridian: the longitude (negative = S / W).")
    text: str = Field("", description="A short label, 1-4 words (stamp: 1-2 words).")
    color: str = Field("a", description="a | b | c | gold | red | white")
    cue: str = Field("", description="The word of this scene's narration it appears on.")


class SceneDraft(BaseModel):
    kind: str = Field(description="map | card")
    narration: str = Field(description="1-2 spoken sentences.")
    focus: list[str] = Field(default_factory=list, description="map: the places the camera frames.")
    headline: str = Field("", description="2-6 words shown at the top (optional).")
    kicker: str = Field("", description="card: a small line above the title (e.g. 'Treaty of Paris').")
    title: str = Field("", description="card: 1-5 words, often a year or a name.")
    subtitle: str = Field("", description="card: one short line under the title.")
    elements: list[ElementDraft] = Field(default_factory=list)


class ClaimDraft(BaseModel):
    claim: str = Field(description="One fact the narration states.")
    source_id: int = Field(description="The id of the source that supports it.")
    quote: str = Field(description="An exact sentence or phrase copied from that source that supports it (8-40 words).")


class Draft(BaseModel):
    usable: bool = Field(description="False when the sources cannot support a clear, true answer.")
    reject_reason: str = ""
    title: str = Field("", description="The YouTube title: the question, under 70 characters.")
    cover_text: str = Field("", description="3-7 words for the first frame (the thumbnail), not a copy of the title.")
    scenes: list[SceneDraft] = Field(default_factory=list)
    claims: list[ClaimDraft] = Field(default_factory=list)
    description: str = Field("", description="2-3 sentences for the YouTube description: the answer in short.")
    hashtags: list[str] = Field(default_factory=list, description="3-5 hashtags without #.")


class Check(BaseModel):
    unsupported: list[str] = Field(default_factory=list, description="Statements in the narration the sources do not support.")


PROMPT = """You write a YouTube Short for Borderline Why: surprising geography and history questions answered with
animated maps. Today's question: "{question}"

Use ONLY the sources below. Do not add facts, numbers, names, dates or quotes that are not in them. If they cannot
support a clear, true answer, set usable to false and say why.

THE NARRATION ({min_words}-{max_words} words, about 45-70 seconds read aloud)
- Start straight away with the specific question or the surprising fact. No greeting, no "Did you know", no "You won't
  believe", no "Let's dive in".
- Build curiosity: what makes the situation odd, why the obvious answer is wrong; then a clear, satisfying answer
  near the end. No filler, no forced suspense, no exaggeration, no call to action, no "subscribe" or "follow".
- Simple, natural, spoken English. Short connected sentences (at most {max_sentence} words). Every sentence adds
  information that helps answer the question. Never add a side fact (an older history, a famous name, a trivia
  detail) just because a source mentions it or to cite another source; if the only other source covers side
  details, cite it for a fact that matters or leave it out. Keep uncertainty where the sources are uncertain ("about", "historians think").
- Numbers as digits (1783, 49th, 123).

THE SCENES ({min_scenes}-{max_scenes})
Split the narration, in order and word for word, into scenes of 1-2 sentences. Every scene's narration together
must be the whole narration.
- map scenes show places. "focus" = the 1-3 places the camera frames. Elements (0-4 per scene):
  highlight (fill a country, state/province or lake, draw a river, or ring a small place), label (a name on the map),
  pin (a marker with a short text), ring (a pulsing circle round a small place), line (a dashed line from place to
  "to"), arrow (a movement from place to "to"), parallel / meridian (a line of latitude / longitude: "value"),
  stamp (a big rubber stamp word like REJECTED, SOLD, SWAPPED, at most one in the video).
- Name places exactly as a map or Wikipedia would: countries ("Spain", "United States of America"), states and
  provinces ("Minnesota", "Manitoba"), lakes ("Lake of the Woods"), rivers ("Mississippi River"), cities ("Paris"),
  or a small place's Wikipedia title ("Llívia", "Northwest Angle"). Never coordinates, never regions like "Europe"
  or "the Midwest", never historical states that are not on today's map.
- color: "a", "b", "c" for the sides of a story (the same country keeps the same letter all video), "gold" for the
  place the video is about, "red" for a mistake or a "no".
- card scenes (at most {max_cards}) are old-paper title cards for a year, a treaty or a person: kicker, title, subtitle.
- cue: a word from that scene's narration, so the element appears as it is said.
- Vary the scenes: not every scene needs a headline; mix wide and close views.

THE CLAIMS
List every fact the narration states (5-12 claims). For each, the id of the source that supports it and an EXACT
quote copied from that source (no changes, no "..."). At least two different sources must back facts that matter
to the answer. If no second source supports anything that matters, set usable to false.

Today is {today}. Write in English.

SOURCES
{sources}
"""

FIX_PROMPT = """Your draft has problems: {problems}

Fix every one and return the whole draft again. Your previous draft:
{draft}
"""

VERIFY_PROMPT = """Check a YouTube narration that answers the question "{question}" against its sources. List:
- every statement the sources do not clearly support (a wrong number, a date, a cause, a name, an exaggeration);
- every sentence that does not help answer the question (a side fact or tangent), marked "OFF TOPIC: ...".
Simple framing like "here's why" needs no support. Return an empty list when everything is supported and on topic.

NARRATION
{narration}

SOURCES
{sources}
"""


def gemini(client=None):
    from google import genai

    from trendclip import script_writer
    from trendclip.config import get_settings

    settings = get_settings()
    key = script_writer.gemini_api_key(settings)
    if client is None and not key:
        raise ScriptError("GEMINI_API_KEY is missing in .env")
    return client or genai.Client(api_key=key), script_writer.gemini_model(settings)


def _ask(client, model: str, prompt: str, schema: type[BaseModel], temperature: float = 0.7) -> Any:
    from google.genai import errors, types

    from trendclip import script_writer

    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=temperature)
    try:
        response, _ = script_writer._generate_with_fallback(client, model, [prompt], config, lambda f, m: None, errors.APIError)
    except errors.APIError as err:
        raise TryLater(str(script_writer._explain(err, model))) from err
    return response.parsed if isinstance(response.parsed, schema) else schema.model_validate_json(response.text)


def sources_block(res: Research) -> str:
    return "\n\n".join(f"[source {s.id}] {s.title} ({s.url})\n{s.text}" for s in res.sources)


def narration(draft: Draft) -> str:
    return " ".join(s.narration.strip() for s in draft.scenes if s.narration.strip())


def words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.,%-]*", text))


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def quote_found(quote: str, source: Source) -> bool:
    """The quote is in the source text (case, punctuation and spacing ignored); a long quote may differ at its
    ends (a sentence cut mid-way), so its middle 80% is enough."""
    q, body = squash(quote), squash(source.text)
    if len(q.split()) < 4:
        return False
    if q in body:
        return True
    w = q.split()
    if len(w) >= 10:
        cut = max(1, len(w) // 10)
        return " ".join(w[cut:-cut]) in body
    return False


BANNED = ("did you know", "you won't believe", "you wont believe", "blow your mind", "let's dive", "subscribe", "follow for",
          "smash that", "like and")


def to_spec_dict(draft: Draft) -> dict[str, Any]:
    scenes = []
    for s in draft.scenes:
        els = []
        for e in s.elements:
            el = e.model_dump()
            if el["kind"] not in ("parallel", "meridian"):
                el["value"] = None
            els.append(el)
        scenes.append({**s.model_dump(exclude={"elements"}), "kind": s.kind.strip().lower(), "elements": els})
    return {"title": draft.title.strip(), "cover_text": draft.cover_text.strip(), "scenes": scenes}


def check(draft: Draft, res: Research, find=bw_geo.resolve) -> list[str]:
    """Everything wrong with a draft (empty = good to record)."""
    problems: list[str] = []
    text = narration(draft)
    n = words(text)
    if not MIN_WORDS <= n <= MAX_WORDS:
        problems.append(f"the narration is {n} words (wanted {MIN_WORDS}-{MAX_WORDS})")
    long = [s for s in sentences(text) if words(s) > MAX_SENTENCE_WORDS]
    if long:
        problems.append(f"{len(long)} sentence(s) over {MAX_SENTENCE_WORDS} words: " + " | ".join(s[:60] for s in long[:2]))
    low = text.lower()
    if bad := [b for b in BANNED if b in low]:
        problems.append("banned phrases: " + ", ".join(bad))
    if not MIN_SCENES <= len(draft.scenes) <= MAX_SCENES:
        problems.append(f"{len(draft.scenes)} scenes (wanted {MIN_SCENES}-{MAX_SCENES})")
    kinds = [s.kind.strip().lower() for s in draft.scenes]
    if any(k not in ("map", "card") for k in kinds):
        problems.append("a scene kind is not map or card")
    if kinds.count("card") > MAX_CARDS:
        problems.append(f"{kinds.count('card')} card scenes (at most {MAX_CARDS})")
    if kinds and kinds[0] != "map":
        problems.append("the first scene must be a map (it is the thumbnail)")
    stamps = 0
    for i, s in enumerate(draft.scenes, 1):
        if s.kind.strip().lower() == "card" and not s.title.strip():
            problems.append(f"card scene {i} has no title")
        if s.kind.strip().lower() == "map" and not s.focus and not s.elements:
            problems.append(f"map scene {i} names no place")
        for e in s.elements:
            if e.kind not in bw_render.ELEMENT_KINDS:
                problems.append(f"scene {i}: unknown element kind {e.kind!r}")
            if e.color not in bw_render.COLOR_ROLES:
                problems.append(f"scene {i}: unknown color {e.color!r}")
            if e.kind in ("parallel", "meridian") and not -85 <= e.value <= 85 and e.kind == "parallel":
                problems.append(f"scene {i}: parallel {e.value} is not a latitude")
            stamps += e.kind == "stamp"
    if stamps > 1:
        problems.append(f"{stamps} stamps (at most one)")
    spec = bw_render.spec_from_dict(to_spec_dict(draft), [])
    if missing := bw_render.resolve(spec, find):
        problems.append("places not on the map (use a country, state/province, lake, river, city or a small place's exact "
                        "Wikipedia title): " + ", ".join(missing[:8]))
    if len(draft.claims) < 4:
        problems.append(f"only {len(draft.claims)} claims listed (list every fact, 5-12)")
    bad_quotes = []
    for c in draft.claims:
        source = res.source(c.source_id)
        if source is None or not quote_found(c.quote, source):
            bad_quotes.append(f"source {c.source_id}: {c.quote[:70]!r}")
    if bad_quotes:
        problems.append("these quotes are not word for word in the source they name: " + "; ".join(bad_quotes[:5]))
    cited = {c.source_id for c in draft.claims if res.source(c.source_id)}
    if len(res.sources) > 1 and len(cited) < 2:
        problems.append("only one source is cited; support some claims with another source")
    if not draft.title.strip() or len(draft.title) > 95:
        problems.append("the title is missing or over 95 characters")
    return problems


def verify(client, model: str, draft: Draft, res: Research) -> list[str]:
    found = _ask(client, model, VERIFY_PROMPT.format(question=res.topic.question, narration=narration(draft), sources=sources_block(res)),
                  Check, temperature=0.0)
    return [u for u in found.unsupported if u.strip()]


def write(res: Research, client=None, today: str = "", progress=lambda m: None) -> dict[str, Any]:
    """The checked script for a researched topic (a dict: modules.bw_render.spec_from_dict reads it). Raises
    ScriptError when Gemini rejects the topic or misses the rules ATTEMPTS times."""
    import datetime as dt

    if len(res.sources) < 2:
        raise ScriptError(f"Only Wikipedia could be read for {res.article!r}: the channel wants two sources")
    client, model = gemini(client)
    prompt = PROMPT.format(question=res.topic.question, min_words=MIN_WORDS, max_words=MAX_WORDS, max_sentence=MAX_SENTENCE_WORDS,
                           min_scenes=MIN_SCENES, max_scenes=MAX_SCENES, max_cards=MAX_CARDS,
                           today=today or dt.date.today().isoformat(), sources=sources_block(res))
    progress("Writing the script")
    draft = _ask(client, model, prompt, Draft)
    problems: list[str] = []
    for attempt in range(1, ATTEMPTS + 1):
        if not draft.usable:
            raise ScriptError(f"Gemini: the sources can't support it ({draft.reject_reason.strip() or 'no reason given'})")
        problems = check(draft, res)
        if not problems:
            progress("Checking the narration against the sources")
            unsupported = verify(client, model, draft, res)
            if not unsupported:
                break
            problems = ["the checker found statements the sources do not support or that are off topic (remove or correct "
                        "them): " + " | ".join(unsupported[:5])]
        logger.info("Borderline Why draft, attempt %d: %s", attempt, "; ".join(problems))
        if attempt == ATTEMPTS:
            raise ScriptError(f"The script missed the rules {ATTEMPTS} times: " + "; ".join(problems))
        progress(f"Fixing the script (attempt {attempt + 1}): {problems[0][:80]}")
        draft = _ask(client, model, prompt + "\n\n" + FIX_PROMPT.format(problems="; ".join(problems),
                                                                          draft=draft.model_dump_json()), Draft)
    used = sorted({c.source_id for c in draft.claims})
    return {
        **to_spec_dict(draft),
        "question": res.topic.question, "article": res.article, "narration": narration(draft),
        "claims": [c.model_dump() for c in draft.claims],
        "sources": [{"id": s.id, "url": s.url, "title": s.title} for s in res.sources if s.id in used],
        "description": draft.description.strip(),
        "hashtags": [h.strip().lstrip("#").replace(" ", "") for h in draft.hashtags if h.strip()][:5],
    }


def description(script: dict[str, Any], handle: str = "") -> str:
    """The YouTube description: the question, the short answer, the sources, the map credit, hashtags."""
    tags = list(dict.fromkeys([*(script.get("hashtags") or []), "geography", "history", "maps", "shorts"]))[:7]
    lines = [script.get("question", ""), "", script.get("description", ""), "", "Sources:"]
    lines += [f"- {s['title']}: {s['url']}" for s in script.get("sources", [])]
    lines += ["", "Maps drawn from Natural Earth (public domain). No AI-generated images."]
    if handle:
        lines += ["", f"More map mysteries: {handle}"]
    lines += ["", " ".join(f"#{t}" for t in tags)]
    return "\n".join(lines).strip()
