"""Borderline Why research and script: a topic's Wikipedia article and the outside pages it cites become the
sources; Gemini writes a 45-70 s narration split into map and card scenes, and lists its claims, each with a
quote from a source. The quotes are checked here word for word against the source text, a second Gemini pass
looks for anything in the narration the sources do not support, and every place a scene names must exist in
the map data. A draft that fails is sent back with the problems (ATTEMPTS drafts for the rules, CHECK_FIXES more
for what the checker flags), then the topic is dropped.
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

from modules import bw_fx, bw_geo, bw_render
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
PHOTO_COUNT = "3-4 in the video, at most 5"
BOARD_COUNT = "1-2 in the video"
MASCOT_LINES = "2-3 asks in the video and at most one react"  # the mascot's lines in a Short (modules.bw_brand)
ATTEMPTS = 3  # drafts against the rules (the first one and 2 fixes)
CHECK_FIXES = 3  # fixes of what the fact checker flags, on top of those
SKIP_DOMAINS = ("wikipedia.org", "wikimedia.org", "wikidata.org", "books.google", "google.com", "archive.org/details",
                "jstor.org", "doi.org", "worldcat.org", "facebook.com", "twitter.com", "x.com", "youtube.com", "instagram.com",
                "amazon.", "ebay.", "geohack", "openstreetmap.org", "tools.wmflabs.org", "toolforge.org", "pdf")


class ScriptError(RuntimeError):
    pass


class TryLater(ScriptError):
    """Not the topic's fault (Wikipedia or Gemini busy): the topic stays on the list. `retry_at` (aware
    datetime) is when Gemini's quota opens again, when that is what stopped it."""

    retry_at = None


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
            self._in_title = not self.title  # only the page's first <title>; SVG logos carry their own
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
    kind: str = Field(description="highlight | label | pin | ring | line | arrow | parallel | meridian | stamp | icon | pole | route")
    place: str = Field("", description="The place it is about, named as on a map (see the rules).")
    to: str = Field("", description="line / arrow: where it ends.")
    value: float = Field(0.0, description="parallel: the latitude; meridian: the longitude (negative = S / W).")
    text: str = Field("", description="A short label, 1-4 words (stamp: 1-2 words).")
    color: str = Field("a", description="a | b | c | gold | red | white")
    cue: str = Field("", description="The word of this scene's narration it appears on.")
    flag: str = Field("", description="highlight / pole: the flag's Wikimedia Commons file, e.g. 'Flag of Canada.svg'.")
    icon: str = Field("", description="icon / route: one name from the icon list.")
    points: list[str] = Field(default_factory=list, description="route: 2-5 places in the order the path goes.")

    @model_validator(mode="after")
    def _plain_kind(self):
        # Gemini sometimes writes kind "flag": a flag on a place is a highlight with that flag, otherwise a pole
        self.kind = self.kind.strip().lower()
        if self.kind == "flag":
            self.kind = "highlight" if self.place.strip() else "pole"
        return self


class SceneDraft(BaseModel):
    kind: str = Field(description="map | card | board | photo")
    narration: str = Field(description="1-2 spoken sentences.")
    focus: list[str] = Field(default_factory=list, description="map: the places the camera frames.")
    headline: str = Field("", description="2-6 words shown at the top (optional).")
    kicker: str = Field("", description="card: a small line above the title (e.g. 'Treaty of Paris').")
    title: str = Field("", description="card: 1-5 words, often a year or a name.")
    subtitle: str = Field("", description="card: one short line under the title.")
    elements: list[ElementDraft] = Field(default_factory=list)
    board_write: list[str] = Field(default_factory=list, description="board: a 2-5 word title, then up to 3 short lines the hand writes.")
    board_draw: list[str] = Field(default_factory=list, description="board: 0-2 places whose outline the hand sketches; the last is circled.")
    photo: str = Field("", description="photo: the English Wikipedia article whose main picture to show.")
    caption: str = Field("", description="photo: 1-5 words written under the picture.")
    ask: str = Field("", description="The mascot's short question, said just before this scene (optional).")
    react: str = Field("", description="The mascot's short reaction, said just after this scene (optional).")


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


# The scene rules a Short and a long video's chapter share (modules.bw_long)
SCENE_RULES = """THE SCENES ({min_scenes}-{max_scenes})
Split the narration, in order and word for word, into scenes of 1-2 sentences. Every scene's narration together
must be the whole narration.
- map scenes show places. "focus" = the 1-3 places the camera frames. Elements (1-5 per scene; something new
  should appear in every scene so the screen is never still):
  highlight (fill a country, state/province or lake, draw a river, or ring a small place), label (a name on the map),
  pin (a marker with a short text), ring (a pulsing circle round a small place), line (a dashed line from place to
  "to"), arrow (a movement from place to "to"), parallel / meridian (a line of latitude / longitude: "value"),
  stamp (a big rubber stamp word like REJECTED, SOLD, SWAPPED, at most {stamp_count}),
  icon (a 3D picture that pops in: "icon" from the list below; with a place it stands on that place, else the
  layout places it), pole (a waving flag on a pole: "flag"; good for who claimed or owned something, and on cards),
  route (a path drawn place by place through "points", e.g. a road, a voyage, an army's march; "icon" can ride
  along it, like car, ship, canoe, horse, train).
- Flags: a highlight with "flag" fills a country or state/province with its waving flag instead of a color. Use
  flags in 2-4 scenes for the countries the story is about. On a place too small to fill (an island, a town) the
  flag shows as a round badge over it, which is a good way to say who owns it. "flag" is the Wikimedia Commons file name of
  the flag viewers know for the name the narration uses: "Flag of France.svg", "Flag of the United States.svg",
  "Flag of the United Kingdom.svg", "Flag of Germany.svg". Today's flag even in a scene set in the past: when the
  narration says "Germany in 1890" or "Britain in 1780", fly today's Germany and today's United Kingdom, never a
  period flag of a country that still exists (no "Flag of the German Empire.svg", no "(1707–1800)" versions).
  Only a state that no longer exists and is named as such in the narration gets its own flag ("Flag of the
  Ottoman Empire.svg", "Flag of the Soviet Union.svg", "Flag of Prussia (1892–1918).svg").
- Icons ({icons}): use {icon_count} where they make a sentence easier to picture (money for a sale, a
  ship for a voyage, a cross for a mistake). Never decoration only.
- Name places exactly as a map or Wikipedia would: countries ("Spain", "United States of America"), states and
  provinces ("Minnesota", "Manitoba"), lakes ("Lake of the Woods"), rivers ("Mississippi River"), cities ("Paris"),
  or a small place's Wikipedia title ("Llívia", "Northwest Angle"). Never coordinates, never regions like "Europe"
  or "the Midwest", never historical states that are not on today's map.
- color: "a", "b", "c" for the sides of a story (the same country keeps the same letter all video), "gold" for the
  place the video is about, "red" for a mistake or a "no".
- card scenes (at most {max_cards}) are old-paper title cards for a year, a treaty or a person: kicker, title, subtitle;
  they can hold up to 2 icons and 2 poles (e.g. the two sides of a treaty).
- board scenes ({board_count}) are the channel's whiteboard: a hand writes in red and black marker. Use them for the
  "why" moment: the deal, the rule, the mistake, the trade. "board_write": a title of 2-5 words, then 0-3 short lines
  (2-6 words each) that sum up what the narration says ("The 1890 deal", "Britain gets Zanzibar", "Germany gets
  Heligoland"). "board_draw" (optional): 1-2 places whose real outline the hand sketches, a country or state first,
  then a small place inside it that gets circled (["Namibia", "Caprivi Strip"]). No focus, no elements. Never the
  first scene.
- photo scenes ({photo_count}) show a real picture: "photo" is the exact title of the English Wikipedia article
  whose main picture shows what the narration names: a person ("Benjamin Franklin"), a landmark or place
  ("Victoria Falls", "Angle Inlet"), an old map or object ("Mitchell Map"), an animal ("Walleye"). "caption": 1-5
  words written under it; "headline" optional; "focus" optional (a map of it is shown if the picture can't be found).
  People hold on to a face or a real place: use photos for the people, landmarks and objects the story names, and
  keep the pictures changing: never more than 3 map scenes in a row without a photo, board or card between them.
- The mascot: the channel's red-panda explorer stands in the corner and talks with the narrator in its own voice.
  Give it {mascot_lines}. "ask" on a scene is a question it asks just before that scene, which that scene's
  narration then answers ("Wait. Why would Germany want a skinny strip like that?", "Okay, so how did they get
  it?", "Ruined? How?"); put asks where a viewer would wonder, never on the first scene. "react" is a one-line
  reaction just after a scene, best after the last one ("So Germany traded Zanzibar... for a waterfall?").
  3-12 words, casual and curious, no new facts, no greeting, no "subscribe". Its lines are not part of the narration:
  the narration must read as a whole without them.
- cue: a word from that scene's narration, so the element appears as it is said.
- Vary the scenes: not every scene needs a headline; mix wide and close views. Don't keep the same focus for more
  than two scenes in a row: pull out to the countries or the region around a small place, then go back in.

"""

PROMPT = """You write a YouTube Short for Borderline Why: surprising geography and history questions answered with
animated maps. Today's question: "{question}"

Use ONLY the sources below. Do not add facts, numbers, names, dates or quotes that are not in them. If they cannot
support a clear, true answer, set usable to false and say why.

THE NARRATION ({min_words}-{max_words} words, about 45-70 seconds read aloud)
- Start straight away with the specific question or the surprising fact. No greeting, no "Did you know", no "You won't
  believe", no "Let's dive in".
- Build curiosity: what makes the situation odd, why the obvious answer is wrong; then a clear, satisfying answer
  near the end. The last sentence closes the story: it ties back to the opening question in a few memorable words
  ("And that's how one bad map left a piece of Minnesota stuck in Canada."), never a new fact. No filler, no forced suspense, no exaggeration, no call to action, no "subscribe" or "follow".
- Simple, natural, spoken English. Short connected sentences (at most {max_sentence} words). Every sentence adds
  information that helps answer the question. Never add a side fact (an older history, a famous name, a trivia
  detail) just because a source mentions it or to cite another source; if the only other source covers side
  details, cite it for a fact that matters or leave it out. Keep uncertainty where the sources are uncertain ("about", "historians think").
- Numbers as digits (1783, 49th, 123).

""" + SCENE_RULES + """THE CLAIMS
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

CHECK_FIX_PROMPT = """A fact checker compared your narration with the sources and flagged these statements:
{flagged}

For each one: delete it, or rewrite it so it says only what a source says (stay close to the source's wording).
An OFF TOPIC sentence goes. Keep every other sentence exactly as it is and the scenes split the same way where you
can; update the claims so each quote still backs what the narration now says; keep the narration {min_words}-{max_words}
words. Return the whole draft. Your previous draft:
{draft}
"""

VERIFY_PROMPT = """Check a YouTube narration that answers the question "{question}" against its sources. List:
- every statement the sources do not support (a wrong number, a date, a cause, a name, an exaggeration);
- every sentence that does not help answer the question (a side fact or tangent), marked "OFF TOPIC: ...".
Write each as the statement, a dash, and what is wrong. Only flag real problems: a statement a source supports in
other words, a fair simplification or simple framing like "here's why" is fine. The claims below come with quotes
already checked word for word against their source. Return an empty list when everything is supported and on topic.

NARRATION
{narration}

CLAIMS (quote checked word for word in the source named)
{claims}

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
        explained = script_writer._explain(err, model)
        later = TryLater(str(explained))
        later.retry_at = getattr(explained, "retry_at", None)
        raise later from err
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


def _piece_found(q: str, body: str) -> bool:
    if q in body:
        return True
    w = q.split()
    if len(w) >= 10:
        cut = max(1, len(w) // 10)
        return " ".join(w[cut:-cut]) in body
    return False


def quote_found(quote: str, source: Source) -> bool:
    """The quote is in the source text (case, punctuation and spacing ignored); a long quote may differ at its
    ends (a sentence cut mid-way), so its middle 80% is enough. A quote shortened with "..." passes when every
    piece of 4+ words is in the source (and the pieces hold 4+ words in all)."""
    body = squash(source.text)
    pieces = [squash(p) for p in re.split(r"\.{3,}|…", quote)]
    pieces = [p for p in pieces if p]
    if sum(len(p.split()) for p in pieces) < 4:
        return False
    if len(pieces) == 1:
        return _piece_found(pieces[0], body)
    long = [p for p in pieces if len(p.split()) >= 4]
    return bool(long) and all(_piece_found(p, body) for p in long)


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
        scene = {**s.model_dump(exclude={"elements", "board_write", "board_draw"}), "kind": s.kind.strip().lower(), "elements": els}
        if scene["kind"] == "board":
            scene["board"] = {"write": [w.strip() for w in s.board_write if w.strip()], "draw": [n for n in s.board_draw if n.strip()][:2]}
        scenes.append(scene)
    return {"title": draft.title.strip(), "cover_text": draft.cover_text.strip(), "scenes": scenes}


@dataclass(frozen=True)
class Limits:
    """What check() holds a draft to: a Short's whole script, or one chapter of a long video (modules.bw_long)."""
    min_words: int = MIN_WORDS
    max_words: int = MAX_WORDS
    min_scenes: int = MIN_SCENES
    max_scenes: int = MAX_SCENES
    max_cards: int = MAX_CARDS
    first_map: bool = True  # the first scene is the thumbnail
    max_boards: int = 2
    max_lines: int = 4  # the mascot's asks and reacts
    max_photos: int = 5
    min_claims: int = 4
    min_cited: int = 2


SHORT = Limits()


def _find_photo(query: str):
    from modules import bw_photos

    return bw_photos.find(query)


def check(draft: Draft, res: Research, find=bw_geo.resolve, limits: Limits = SHORT, photo_find=_find_photo) -> list[str]:
    """Everything wrong with a draft (empty = good to record)."""
    problems: list[str] = []
    text = narration(draft)
    n = words(text)
    if not limits.min_words <= n <= limits.max_words:
        problems.append(f"the narration is {n} words (wanted {limits.min_words}-{limits.max_words})")
    long = [s for s in sentences(text) if words(s) > MAX_SENTENCE_WORDS]
    if long:
        problems.append(f"{len(long)} sentence(s) over {MAX_SENTENCE_WORDS} words: " + " | ".join(s[:60] for s in long[:2]))
    low = text.lower()
    if bad := [b for b in BANNED if b in low]:
        problems.append("banned phrases: " + ", ".join(bad))
    if not limits.min_scenes <= len(draft.scenes) <= limits.max_scenes:
        problems.append(f"{len(draft.scenes)} scenes (wanted {limits.min_scenes}-{limits.max_scenes})")
    kinds = [s.kind.strip().lower() for s in draft.scenes]
    if any(k not in ("map", "card", "board", "photo") for k in kinds):
        problems.append("a scene kind is not map, card, board or photo")
    if kinds.count("photo") > limits.max_photos:
        problems.append(f"{kinds.count('photo')} photo scenes (at most {limits.max_photos})")
    if kinds.count("board") > limits.max_boards:
        problems.append(f"{kinds.count('board')} board scenes (at most {limits.max_boards})")
    if kinds and kinds[0] == "board":
        problems.append("the first scene can't be a board")
    lines = [(i, k, getattr(s, k).strip()) for i, s in enumerate(draft.scenes, 1) for k in ("ask", "react") if getattr(s, k).strip()]
    if len(lines) > limits.max_lines:
        problems.append(f"the mascot has {len(lines)} lines (at most {limits.max_lines})")
    for i, k, line in lines:
        if not 2 <= words(line) <= 12:
            problems.append(f"scene {i}: the mascot's {k} is {words(line)} words (wanted 3-12)")
        if k == "ask" and not line.endswith("?"):
            problems.append(f"scene {i}: the mascot's ask must be a question")
        if k == "ask" and i == 1:
            problems.append("the first scene can't have an ask (the narrator opens the video)")
        if bad := [b for b in BANNED if b in line.lower()]:
            problems.append(f"scene {i}: banned phrases in the mascot's {k}: " + ", ".join(bad))
    if kinds.count("card") > limits.max_cards:
        problems.append(f"{kinds.count('card')} card scenes (at most {limits.max_cards})")
    if limits.first_map and kinds and kinds[0] != "map":
        problems.append("the first scene must be a map (it is the thumbnail)")
    stamps = 0
    for i, s in enumerate(draft.scenes, 1):
        if s.kind.strip().lower() == "card" and not s.title.strip():
            problems.append(f"card scene {i} has no title")
        if s.kind.strip().lower() == "map" and not s.focus and not s.elements:
            problems.append(f"map scene {i} names no place")
        if s.kind.strip().lower() == "photo" and photo_find is not None:
            if not s.photo.strip():
                problems.append(f"photo scene {i} names no Wikipedia article")
            elif photo_find(s.photo) is None:
                problems.append(f"photo scene {i}: no picture found for {s.photo!r} (use the exact title of an English "
                                "Wikipedia article that has a photo or painting)")
        if s.kind.strip().lower() == "board":
            if not [w for w in s.board_write if w.strip()]:
                problems.append(f"board scene {i} has no board_write")
            elif len(s.board_write) > 4 or any(words(w) > 7 for w in s.board_write):
                problems.append(f"board scene {i}: board_write is at most 4 short lines (7 words each)")
            if far := [n for n in s.board_draw[:2] if not find(n)]:
                problems.append(f"board scene {i}: places not on the map: " + ", ".join(far))
        for e in s.elements:
            if e.kind not in bw_render.ELEMENT_KINDS:
                problems.append(f"scene {i}: unknown element kind {e.kind!r}")
            if e.color not in bw_render.COLOR_ROLES:
                problems.append(f"scene {i}: unknown color {e.color!r}")
            if e.kind in ("parallel", "meridian") and not -85 <= e.value <= 85 and e.kind == "parallel":
                problems.append(f"scene {i}: parallel {e.value} is not a latitude")
            stamps += e.kind == "stamp"
            if e.kind == "icon" and not bw_fx.icon_code(e.icon):
                problems.append(f"scene {i}: unknown icon {e.icon!r} (use a name from the icon list)")
            if e.kind == "pole" and not e.flag.strip():
                problems.append(f"scene {i}: a pole needs a flag")
    if stamps > 1:
        problems.append(f"{stamps} stamps (at most one)")
    spec = bw_render.spec_from_dict(to_spec_dict(draft), [])
    if missing := bw_render.resolve(spec, find):
        problems.append("places not on the map (use a country, state/province, lake, river, city or a small place's exact "
                        "Wikipedia title): " + ", ".join(missing[:8]))
    if len(draft.claims) < limits.min_claims:
        problems.append(f"only {len(draft.claims)} claims listed (list every fact the narration states)")
    bad_quotes = []
    for c in draft.claims:
        source = res.source(c.source_id)
        if source is None or not quote_found(c.quote, source):
            bad_quotes.append(f"source {c.source_id}: {c.quote[:70]!r}")
    if bad_quotes:
        problems.append("these quotes are not word for word in the source they name: " + "; ".join(bad_quotes[:5]))
    cited = {c.source_id for c in draft.claims if res.source(c.source_id)}
    if len(res.sources) > 1 and len(cited) < limits.min_cited:
        problems.append("only one source is cited; support some claims with another source")
    if not draft.title.strip() or len(draft.title) > 95:
        problems.append("the title is missing or over 95 characters")
    return problems


def verify_prompt(draft: Draft, res: Research) -> str:
    claims = "\n".join(f"- {c.claim} [source {c.source_id}: \"{c.quote}\"]" for c in draft.claims) or "(none)"
    return VERIFY_PROMPT.format(question=res.topic.question, narration=narration(draft), claims=claims, sources=sources_block(res))


def verify(client, model: str, draft: Draft, res: Research) -> list[str]:
    found = _ask(client, model, verify_prompt(draft, res), Check, temperature=0.0)
    return [u.strip() for u in found.unsupported if u.strip()]


def write_prompt(res: Research, today: str = "") -> str:
    import datetime as dt

    return PROMPT.format(question=res.topic.question, icon_count="6-10 in the video", stamp_count="one in the video", mascot_lines=MASCOT_LINES,
                         photo_count=PHOTO_COUNT, board_count=BOARD_COUNT,
                         min_words=MIN_WORDS, max_words=MAX_WORDS, max_sentence=MAX_SENTENCE_WORDS,
                         min_scenes=MIN_SCENES, max_scenes=MAX_SCENES, max_cards=MAX_CARDS, icons=", ".join(bw_fx.icon_names()),
                         today=today or dt.date.today().isoformat(), sources=sources_block(res))


def fix_prompt(problems: list[str], draft: Draft) -> str:
    return FIX_PROMPT.format(problems="; ".join(problems), draft=draft.model_dump_json())


def check_fix_prompt(flagged: list[str], draft: Draft) -> str:
    return CHECK_FIX_PROMPT.format(flagged="\n".join(f"- {p}" for p in flagged[:8]), min_words=MIN_WORDS, max_words=MAX_WORDS,
                                   draft=draft.model_dump_json())


def finish(draft: Draft, res: Research) -> dict[str, Any]:
    """The script dict of a draft that passed (modules.bw_render.spec_from_dict reads it)."""
    used = sorted({c.source_id for c in draft.claims})
    return {
        **to_spec_dict(draft),
        "question": res.topic.question, "article": res.article, "narration": narration(draft),
        "claims": [c.model_dump() for c in draft.claims],
        "sources": [{"id": s.id, "url": s.url, "title": s.title} for s in res.sources if s.id in used],
        "description": draft.description.strip(),
        "hashtags": [h.strip().lstrip("#").replace(" ", "") for h in draft.hashtags if h.strip()][:5],
    }


def draft_path(drafts_dir: Path | None, article: str) -> Path | None:
    """Where a half-written script waits for Gemini's quota (drafts_dir/<article>.json), if drafts are kept."""
    if drafts_dir is None:
        return None
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", article).strip("_") or "topic"
    return drafts_dir / f"{name[:80]}.json"


def _save_draft(path: Path | None, draft: "Draft", fixes: dict[str, int]) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"draft": draft.model_dump(), "fixes": fixes}, indent=1), encoding="utf-8")
    except OSError as err:
        logger.warning("Could not keep the draft at %s: %s", path, err)


def _load_draft(path: Path | None) -> tuple["Draft", dict[str, int]] | None:
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Draft.model_validate(data["draft"]), {"rules": int(data["fixes"].get("rules", 0)),
                                                     "facts": int(data["fixes"].get("facts", 0))}
    except (OSError, ValueError, KeyError, TypeError) as err:
        logger.warning("The kept draft at %s is unreadable (%s); starting over", path, err)
        return None


def write(res: Research, client=None, today: str = "", progress=lambda m: None, drafts_dir: Path | None = None) -> dict[str, Any]:
    """The checked script for a researched topic (a dict: modules.bw_render.spec_from_dict reads it). Raises
    ScriptError when Gemini rejects the topic or misses the rules ATTEMPTS times.

    With `drafts_dir`, the draft is kept on disk after every Gemini answer, so when the quota runs out
    halfway (TryLater) the next try carries on from the kept draft instead of spending the calls again;
    the kept draft is removed once the script passes or the topic is dropped."""
    if len(res.sources) < 2:
        raise ScriptError(f"Only Wikipedia could be read for {res.article!r}: the channel wants two sources")
    client, model = gemini(client)
    prompt = write_prompt(res, today)
    kept = draft_path(drafts_dir, res.article)
    resumed = _load_draft(kept)
    # The rules (length, quotes, places) and the fact checker each get their own fixes, so a draft that needed
    # its rule fixes still gets a chance to correct what the checker flags.
    fixes = {"rules": 0, "facts": 0}
    limits = {"rules": ATTEMPTS - 1, "facts": CHECK_FIXES}
    if resumed:
        draft, fixes = resumed
        progress("Carrying on with the draft kept from the last try")
    else:
        progress("Writing the script")
        draft = _ask(client, model, prompt, Draft)
        _save_draft(kept, draft, fixes)
    try:
        draft = _finish_draft(client, model, prompt, draft, fixes, limits, res, progress, kept)
    except TryLater:
        raise  # the kept draft stays for the next try
    except ScriptError:
        if kept is not None:
            kept.unlink(missing_ok=True)
        raise
    if kept is not None:
        kept.unlink(missing_ok=True)
    return finish(draft, res)


def _finish_draft(client, model: str, prompt: str, draft: "Draft", fixes: dict[str, int], limits: dict[str, int],
                  res: Research, progress, kept: Path | None) -> "Draft":
    while True:
        if not draft.usable:
            raise ScriptError(f"Gemini: the sources can't support it ({draft.reject_reason.strip() or 'no reason given'})")
        kind, problems = "rules", check(draft, res)
        if not problems:
            progress("Checking the narration against the sources")
            problems = verify(client, model, draft, res)
            if not problems:
                return draft
            kind = "facts"
        logger.info("Borderline Why draft (%s fix %d): %s", kind, fixes[kind], " | ".join(problems))
        if fixes[kind] >= limits[kind]:
            what = "the fact checker still flagged" if kind == "facts" else f"the script missed the rules {ATTEMPTS} times"
            raise ScriptError(f"{what[0].upper()}{what[1:]}: " + " | ".join(problems[:5]))
        fixes[kind] += 1
        if kind == "facts":
            progress(f"Fixing the facts ({fixes[kind]} of {limits[kind]}): {problems[0][:110]}")
            fix = check_fix_prompt(problems, draft)
        else:
            progress(f"Fixing the script ({fixes[kind]} of {limits[kind]}): {problems[0][:110]}")
            fix = fix_prompt(problems, draft)
        draft = _ask(client, model, prompt + "\n\n" + fix, Draft)
        _save_draft(kept, draft, fixes)


def description(script: dict[str, Any], handle: str = "") -> str:
    """The YouTube description: the question, the short answer, the sources, the map credit, hashtags."""
    tags = list(dict.fromkeys([*(script.get("hashtags") or []), "geography", "history", "maps", "shorts"]))[:7]
    lines = [script.get("question", ""), "", script.get("description", ""), "", "Sources:"]
    lines += [f"- {s['title']}: {s['url']}" for s in script.get("sources", [])]
    if script.get("look", "satellite") == "satellite":
        lines += ["", "Maps: NASA Blue Marble and Natural Earth (public domain). Flags: Wikimedia Commons. Icons: Noto Emoji "
                  "by Google (Apache 2.0; animated Noto Emoji, CC BY 4.0). No AI-generated images."]
    else:
        lines += ["", "Maps drawn from Natural Earth (public domain). No AI-generated images."]
    if handle:
        lines += ["", f"More map mysteries: {handle}"]
    lines += ["", " ".join(f"#{t}" for t in tags)]
    return "\n".join(lines).strip()
