"""Borderline Why research and script: a topic's Wikipedia article and the outside pages it cites become the
sources; Gemini writes a 40-55 s narration split into map and card scenes, and lists its claims, each with a
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
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field, model_validator

from modules import bw_fx, bw_geo, bw_local, bw_render, bw_series
from modules.bw_topics import Topic

logger = logging.getLogger(__name__)

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = bw_geo.USER_AGENT
ARTICLE_CHARS = 14000
SOURCE_CHARS = 7000
MIN_SOURCE_CHARS = 1200
MAX_FETCH = 10  # cited pages tried per topic
MAX_SOURCES = 4  # outside pages kept besides Wikipedia
MIN_WORDS, MAX_WORDS = 60, 80  # the narrator's part; with the mascot a Short is 30-40 s (never over 60):
# the 50-54 s Shorts were watched past their end, 59 s+ lost half (2026-10-31); a 50 s what-if lost half its viewers
# between seconds 3 and 15 and a third at its subscribe outro (2026-11-05): shorter, faster (bw_channel.SHORT_PACE), looping
SPOKEN_WORDS = 100  # the narration and the mascot's lines together (about 37 s spoken at the Short's pace)
MAX_SENTENCE_WORDS = 24
MIN_SCENES, MAX_SCENES = 5, 14
MAX_CARDS = 1  # a Short's static title cards: the first card lost viewers in 4 of the first 5 Shorts (2026-11-01)
FIRST_WORDS = 12  # scene 1, about 3-4 s: viewers left where a long opening scene ended
CARD_WORDS = 10  # a card's narration, about 4 s
# Scene 2 opening on a setup line ("This is Point Roberts") lost 30-40 points of retention between 3 s and 10 s
SETUP = re.compile(r"^\W*(this is|these are|this place is|it is called|it's called|they are called|meet|here is|here's|"
                   r"welcome to|say hello)\b", re.I)
PHOTO_COUNT = "3-4 in the video, at most 5"
BOARD_COUNT = "1-2 in the video"
MASCOT_LINES = "1-2 lines in the video (asks or a react), short ones"  # the mascot's lines in a Short (modules.bw_brand)
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


def own_source(entry: str, sid: int) -> Source:
    """A source you named: a link (any readable web page) or another Wikipedia article's title. Unreadable: ScriptError."""
    entry = entry.strip()
    if re.match(r"https?://", entry):
        wiki = re.match(r"https?://[a-z]+\.wikipedia\.org/wiki/([^?#]+)", entry)
        if wiki:
            return own_source(unquote(wiki.group(1)).replace("_", " "), sid)
        try:
            r = _get(entry, timeout=20, tries=2)
            page_title, body = page_text(r.text[:2_000_000])
        except Exception as err:  # noqa: BLE001 - paywalls, 403s, timeouts
            raise ScriptError(f"{urlparse(entry).netloc} couldn't be read ({err})") from err
        if len(body) < MIN_SOURCE_CHARS:
            raise ScriptError(f"{urlparse(entry).netloc} has almost no readable text (a page drawn by JavaScript can't be read)")
        return Source(sid, entry, page_title or urlparse(entry).netloc, body[:SOURCE_CHARS])
    title = article_exists(entry)
    if not title:
        raise ScriptError(f"No Wikipedia article called {entry!r}")
    real, text = wiki_text(title)
    return Source(sid, f"https://en.wikipedia.org/wiki/{real.replace(' ', '_')}", f"{real} - Wikipedia", text)


# --------------------------------------------------------------------------- the draft


LOCAL_MOODS = ("shocked", "sad", "grin", "smug", "think", "smile")  # a local's face on the react word (modules.bw_fx.local)


class ElementDraft(BaseModel):
    kind: str = Field(description="highlight | label | pin | ring | line | arrow | parallel | meridian | stamp | icon | pole | route | transfer | counter | army | battle | local")
    place: str = Field("", description="The place it is about, named as on a map (see the rules).")
    to: str = Field("", description="line / arrow / army: where it ends.")
    value: float = Field(0.0, description="parallel: the latitude; meridian: the longitude (negative = S / W); counter: the number.")
    text: str = Field("", description="A short label, 1-4 words (stamp: 1-2 words).")
    color: str = Field("a", description="a | b | c | gold | red | white")
    cue: str = Field("", description="The word of this scene's narration it appears on.")
    flag: str = Field("", description="highlight / pole: the flag's Wikimedia Commons file, e.g. 'Flag of Canada.svg'; "
                                       "transfer: the new owner's flag; army: the flag its units carry; battle: the attacker's flag.")
    from_flag: str = Field("", description="transfer: the old owner's flag (give both flags or neither); battle: the defender's flag.")
    count: int = Field(0, description="army: how many units march (3-9; 0: 5).")
    icon: str = Field("", description="icon / route / local: one name from the icon list (local: a person).")
    points: list[str] = Field(default_factory=list, description="route: 2-5 places in the order the path goes.")
    mood: str = Field("", description="local: how they feel once the react word is said: shocked | sad | grin | smug | think | smile.")
    react: str = Field("", description="local: the word of this scene's narration their mood changes on.")

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
    year: str = Field("", description="map: a year or span (\"1812\", \"1838-39\") that slams in big as the scene starts, "
                                      "then stays as a small tag (a new step of the story; the war-map format).")
    ask: str = Field("", description="The mascot's short question, said just before this scene (optional).")
    react: str = Field("", description="The mascot's short reaction, said just after this scene (optional).")


class ClaimDraft(BaseModel):
    claim: str = Field(description="One fact the narration states.")
    source_id: int = Field(description="The id of the source that supports it.")
    quote: str = Field(description="An exact sentence or phrase copied from that source that supports it (8-40 words).")


class Draft(BaseModel):
    usable: bool = Field(description="False when the sources cannot support a clear, true answer.")
    reject_reason: str = ""
    title: str = Field("", description="The YouTube title: a short question under 45 characters that names the place "
                                       "(\"Why Does Kentucky Own This Island?\"); the feed cuts longer ones.")
    cover_text: str = Field("", description="3-7 words for the first frame (the thumbnail), not a copy of the title.")
    hook_scene: int = Field(0, description="The number of the later map scene whose finished map is flashed in the first "
                                           "2 seconds: the outcome (0: no flash-forward).")
    hook_before: str = Field("", description="1-3 words stamped on the first map during the flash-forward (\"Day 0\").")
    hook_after: str = Field("", description="1-3 words stamped on the flashed outcome (\"Day 85\").")
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
  along it, like car, ship, canoe, horse, train), transfer (a place changing hands: it shows in the old owner's
  "from_flag" from the scene's start, then on the cue word the new owner's "flag" wipes over it; "color" is the new
  owner's letter, "text" an optional 1-3 word label like "SOLD 1867". Use it whenever the narration says a place was
  sold, swapped, ceded, conquered or handed over; give both flags or neither), counter (a number that counts up big
  under the headline as it is said: "value" is the number, "text" its label ("pirate ships", "km", "soldiers"), or
  the whole line with {{n}} where the number goes ("Day {{n}}", "{{n}}+ ships"); 2-3 counters in one scene stack into a
  tally, like "500 ships", "120,000 soldiers", "5,000 horsemen"). Use 1-4 counters in the video for the numbers the
  story turns on (sizes, distances, armies, prices, days); never for a year (a year goes in the headline).
  local (one of the channel's own characters standing on "place", for the people the narration names: "icon" is
  citizen, farmer, settler, king or soldier (these act out the mood with their own faces), else another person
  from the icon list (merchant, worker, scholar, sailor, queen...),
  "text" a 1-3 word caption like "a villager"; they pop in on "cue" and react on "react", a later word of the
  scene, with "mood": shocked (a border moved over them), sad (they lost the land), grin (they won), smug, think,
  smile. Use 1-3 in the video, one per scene at most, only where the narration names people.
- Wars and conflicts (an invasion, a battle, a border war, a siege, a rebellion) are told on the war map: give the
  story's armies and fights as elements and the video switches to it (each side lightly tinted, color "a" the
  attacker and "b" the defender, the same all video). army: troops marching from "place" to "to" (cities or
  states/provinces near the front), with "flag" for whose army it is (a block of little flags led by a flag-bearer)
  or no flag (marching soldiers, painted in the side's colour); "count" 3-9. A side that is not one of today's
  countries (Rome, Carthage, a kingdom or empire that is gone) gets "flag": "none" on its highlight, so no modern
  flag waves over it; a side that is a country of today gets its flag. battle: a clash at a place, the attacker's "flag" against the
  defender's "from_flag", with explosions and smoke; "text" can name it. A map scene can also have "year" ("1812")
  when the story jumps to a new date: it slams in big, then stays as a small tag. The marker hand draws a war scene's
  "line" or "arrow" (the front, the new border, the push), else it circles the battle just before it starts. Not for a peaceful story: a sale,
  a treaty or a survey keeps the usual flags, arrows and transfers.
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

# A Short's opening and its history scenes (the Short prompts: here, modules.bw_whatif; bw_week reuses PROMPT)
HOOK_RULES = """THE FIRST 5 SECONDS decide whether people stay
- Scene 1 is one short line (at most """ + str(FIRST_WORDS) + """ words, about 3 seconds) over the striking map: the
  question or the surprise.
- Scene 2 already pays something off: the strangest fact or the first part of the answer. Never a setup line like
  "This is Point Roberts." or "It is called Pheasant Island." (the map already shows its name).
- The title is a short question under 45 characters that names the place ("Why Does Kentucky Own This Island?"):
  the feed cuts longer ones. cover_text is the hook in 3-6 big words on the first frame, not the title again.
- THE PACE: the answer is moving on the map by second 8: at most 2 short sentences of background before it. Every
  scene is one sentence (6-14 words) and brings something new to the map (a line, a flag, a transfer, a label, a
  person), so the picture changes every 2-3 seconds. No sentence that only sets the scene.
- The flash-forward: in the first 2 seconds the video shows where the story ends, then goes back to the start.
  hook_scene is the number of a later map scene whose finished map shows the outcome (the pirates gone, the border
  drawn, the land in its new owner's flag, the what-if's new map); hook_after is 1-3 words stamped on it ("Day 85",
  "SOLD", "1867"); hook_before is 1-3 words stamped on the opening map ("Day 0", "1803"), or empty. Scene 1's line is
  the question that outcome answers ("How did Rome clear the sea in 3 months?").

HISTORY ON THE MAP, NOT ON CARDS
- Tell the history while the map moves: the treaty or border line drawing itself across the map (line, route,
  parallel), the land changing hands (transfer), the side lighting up (highlight), with the year as the headline.
- A card is at most one quick beat (at most """ + str(CARD_WORDS) + """ words, about 4 seconds); a longer history step goes
  on the map or the whiteboard instead.

"""

PROMPT = """You write a YouTube Short for Borderline Why: surprising geography and history questions answered with
animated maps. Today's question: "{question}"

Use ONLY the sources below. Do not add facts, numbers, names, dates or quotes that are not in them. If they cannot
support a clear, true answer, set usable to false and say why.

THE NARRATION ({min_words}-{max_words} words, about 25-30 seconds read aloud; with the mascot the Short is 30-40 seconds and never over 60:
short Shorts get watched to the end and replayed)
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

""" + HOOK_RULES + SCENE_RULES + """THE CLAIMS
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


def _ask(client, model: str, prompt: str, schema: type[BaseModel], temperature: float = 0.7, progress=lambda m: None) -> Any:
    """One Gemini call. `progress` hears only when Gemini is busy and the call waits to try again."""
    from google.genai import errors, types

    from trendclip import script_writer

    def busy(_fraction, message: str) -> None:
        if "busy" in message:
            progress("Gemini is busy: waiting to try again" + message[message.index(" (busy"):])

    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=temperature)
    try:
        response, _ = script_writer._generate_with_fallback(client, model, [prompt], config, busy, errors.APIError)
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
            if el["kind"] not in ("parallel", "meridian", "counter"):
                el["value"] = None
            els.append(el)
        scene = {**s.model_dump(exclude={"elements", "board_write", "board_draw"}), "kind": s.kind.strip().lower(), "elements": els}
        if scene["kind"] == "board":
            scene["board"] = {"write": [w.strip() for w in s.board_write if w.strip()], "draw": [n for n in s.board_draw if n.strip()][:2]}
        scenes.append(scene)
    out = {"title": draft.title.strip(), "cover_text": draft.cover_text.strip(), "scenes": scenes}
    if any(e["kind"] in ("army", "battle") for sc in scenes for e in sc["elements"]):  # a war or conflict: the war map
        out["style"] = "war"
    if 1 < draft.hook_scene <= len(scenes) and draft.hook_after.strip():
        out["hook"] = {"scene": draft.hook_scene - 1, "before": draft.hook_before.strip(), "after": draft.hook_after.strip()}
    return out


@dataclass(frozen=True)
class Limits:
    """What check() holds a draft to: a Short's whole script, or one chapter of a long video (modules.bw_long)."""
    min_words: int = MIN_WORDS
    max_words: int = MAX_WORDS
    min_scenes: int = MIN_SCENES
    max_scenes: int = MAX_SCENES
    max_cards: int = MAX_CARDS
    # scene 1's / a card's narration at most (0: no limit); 2 words more than the prompt asks, so a near miss
    # doesn't spend a Gemini fix call
    first_words: int = FIRST_WORDS + 2
    card_words: int = CARD_WORDS + 2
    no_setup: bool = True  # scene 2 must not open on "This is X"
    first_map: bool = True  # the first scene is the thumbnail
    max_boards: int = 2
    max_lines: int = 2  # the mascot's asks and reacts (the outro's line comes on top)
    spoken_words: int = SPOKEN_WORDS  # the narration and the mascot's lines together (0: no limit)
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
    spoken = n + sum(words(line) for _, _, line in lines)
    if limits.spoken_words and spoken > limits.spoken_words:
        problems.append(f"the narration and the mascot's lines are {spoken} words together (at most {limits.spoken_words}, "
                        "so the Short stays under 60 seconds): cut the least needed sentence")
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
        problems.append(f"{kinds.count('card')} card scenes (at most {limits.max_cards}): tell the history on the map "
                        "(a line or route drawing itself, a transfer, the year as the headline)")
    if limits.first_words and draft.scenes and words(draft.scenes[0].narration) > limits.first_words:
        problems.append(f"scene 1 is {words(draft.scenes[0].narration)} words (at most {limits.first_words}: one short line, "
                        "the question or the surprise)")
    if limits.no_setup and len(draft.scenes) > 1 and SETUP.match(draft.scenes[1].narration):
        problems.append("scene 2 opens on a setup line (\"This is ...\", \"It is called ...\"): open it on the strangest fact or "
                        "the first part of the answer")
    if limits.card_words:
        for i, s in enumerate(draft.scenes, 1):
            if s.kind.strip().lower() == "card" and words(s.narration) > limits.card_words:
                problems.append(f"card scene {i} is {words(s.narration)} words (at most {limits.card_words}): shorten it, or tell "
                                "that step on the map (a line drawing itself, a transfer) or the whiteboard")
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
            if e.kind == "local" and e.mood.strip() and e.mood.strip().lower() not in LOCAL_MOODS:
                problems.append(f"scene {i}: a local's mood is one of {', '.join(LOCAL_MOODS)}")
            if e.kind == "local" and e.icon.strip() and not bw_fx.icon_code(e.icon) and not bw_local.family(e.icon):
                problems.append(f"scene {i}: unknown local {e.icon!r} (use a person from the icon list)")
            if e.kind == "army" and not (e.place.strip() and e.to.strip()):
                problems.append(f"scene {i}: an army needs a place it starts from and a place it marches \"to\"")
            if e.kind == "battle" and not e.place.strip():
                problems.append(f"scene {i}: a battle needs its place")
            if e.kind == "transfer" and (not e.place.strip() or bool(e.flag.strip()) != bool(e.from_flag.strip())):
                problems.append(f"scene {i}: a transfer needs a place and both flags (from_flag and flag) or neither")
    if stamps > 1:
        problems.append(f"{stamps} stamps (at most one)")
    counters = [(i, s, e) for i, s in enumerate(draft.scenes, 1) for e in s.elements if e.kind == "counter"]
    for i, s, e in counters:
        if s.kind.strip().lower() != "map":
            problems.append(f"scene {i}: a counter goes on a map scene")
        if not e.value or e.value <= 0:
            problems.append(f"scene {i}: a counter needs its number in value")
        elif 1000 <= e.value <= 2100 and e.value == int(e.value) and not e.text.strip():
            problems.append(f"scene {i}: counter {e.value:g} looks like a year (a year goes in the headline)")
    if len(counters) > 4:
        problems.append(f"{len(counters)} counters (at most 4)")
    if any(sum(e.kind == "counter" for e in s.elements) > 3 for s in draft.scenes):
        problems.append("more than 3 counters in one scene")
    if draft.hook_scene:
        k = draft.hook_scene
        if not 1 < k <= len(draft.scenes) or draft.scenes[k - 1].kind.strip().lower() != "map":
            problems.append(f"hook_scene {k} must be the number of a later map scene (the outcome)")
        elif not draft.hook_after.strip():
            problems.append("the flash-forward needs hook_after (1-3 words stamped on the outcome)")
        if words(draft.hook_before) > 3 or words(draft.hook_after) > 3:
            problems.append("hook_before and hook_after are 1-3 words each")
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
    elif bw_series.strip(draft.title) != draft.title.strip() or re.search(r"#\d", draft.title):
        problems.append("leave the series name and episode number out of the title: the channel adds them at the upload")
    return problems


def verify_prompt(draft: Draft, res: Research) -> str:
    claims = "\n".join(f"- {c.claim} [source {c.source_id}: \"{c.quote}\"]" for c in draft.claims) or "(none)"
    return VERIFY_PROMPT.format(question=res.topic.question, narration=narration(draft), claims=claims, sources=sources_block(res))


def verify(client, model: str, draft: Draft, res: Research, progress=lambda m: None) -> list[str]:
    found = _ask(client, model, verify_prompt(draft, res), Check, temperature=0.0, progress=progress)
    return [u.strip() for u in found.unsupported if u.strip()]


def series_note(topic: Topic) -> str:
    """The line that tells the writer this Short is the next episode of a numbered series ("" when it isn't)."""
    series = topic.episode_of
    if not series:
        return ""
    return (f'This Short is the next episode of the channel\'s numbered series "{series}" ({bw_series.SERIES[series]}). '
            f"Viewers follow the series, so make the weirdness of the map the hook. {bw_series.HINTS.get(series, '')} "
            f"Keep the title the plain question: "
            f'never write "{series}" or an episode number in it (the channel adds "| {series} #N" at the upload).')


def write_prompt(res: Research, today: str = "") -> str:
    import datetime as dt

    note = series_note(res.topic)
    head = 'Today\'s question: "{question}"'
    return PROMPT.replace(head, head + ("\n{series_note}" if note else "")).format(series_note=note, question=res.topic.question, icon_count="6-10 in the video", stamp_count="one in the video", mascot_lines=MASCOT_LINES,
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
        "question": res.topic.question, "article": res.article, "narration": narration(draft), "series": res.topic.episode_of,
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
        draft = _ask(client, model, prompt, Draft, progress=progress)
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
            problems = verify(client, model, draft, res, progress)
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
        draft = _ask(client, model, prompt + "\n\n" + fix, Draft, progress=progress)
        _save_draft(kept, draft, fixes)


def description(script: dict[str, Any], handle: str = "") -> str:
    """The YouTube description: the question, the short answer, the sources, the map credit, hashtags."""
    tags = list(dict.fromkeys([*(script.get("hashtags") or []), "geography", "history", "maps", "shorts"]))[:7]
    lines = [script.get("question", ""), "", script.get("description", "")]
    if script.get("kind") == "whatif":  # modules.bw_whatif: no sources to list, and it says it is a guess
        lines += ["", "A what-if: the real history up to the turning point, then one possible guess at what might have "
                  "happened. Nobody knows for sure."]
    elif script.get("kind") == "attempts":  # modules.bw_whatif's attempts format: no source list
        pass
    else:
        lines += ["", "Sources:"] + [f"- {s['title']}: {s['url']}" for s in script.get("sources", [])]
    if script.get("look", "satellite") == "satellite":
        lines += ["", "Maps: NASA Blue Marble and Natural Earth (public domain). Flags: Wikimedia Commons. Icons: Noto Emoji "
                  "by Google (Apache 2.0; animated Noto Emoji, CC BY 4.0). No AI-generated images."]
    else:
        lines += ["", "Maps drawn from Natural Earth (public domain). No AI-generated images."]
    if handle:
        lines += ["", f"More map mysteries: {handle}"]
    lines += ["", " ".join(f"#{t}" for t in tags)]
    return "\n".join(lines).strip()
