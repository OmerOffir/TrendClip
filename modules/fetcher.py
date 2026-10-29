"""Dark Outliers story fetcher: pulls raw dark / mystery stories from Reddit, Wikipedia, Gemini and RSS
feeds (Atlas Obscura, Listverse) into the content bank (db/content_bank.db). Already-fetched stories are
skipped by source_id.

    python -m modules.fetcher                       # every source
    python -m modules.fetcher --sources wikipedia   # one source
    python -m modules.fetcher --reddit 25 --wikipedia 10 --gemini 5 --rss 10
    python -m modules.fetcher --recheck             # apply the current filters to the NEW stories in the bank

Curation (the Dark Outliers content guidelines):
  pillars     mystery 40% (r/UnresolvedMysteries, r/ColdCases), history 40% (r/CreepyWikipedia,
              r/DarkHistory, r/todayilearned), outrage 20% (r/TrueCrime, Atlas Obscura, Listverse);
              see repository.PILLARS
  length      raw text MIN_CHARS-MAX_CHARS (Wikipedia articles are cut to their opening paragraphs)
  engagement  MIN_UPVOTES on Reddit posts. Reddit only shows votes to a logged-in app: set
              REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET in .env (a "script" app from reddit.com/prefs/apps).
              Without them the public RSS feed is used, where the top-of-the-month rank stands in for votes
  keywords    the mixed-topic sources (r/todayilearned, the RSS feeds) need one of KEYWORDS
  exclusions  promos, meta posts and fiction by title here; gore without depth, fiction and recent
              politics / ongoing news are judged by Gemini when the script is written (REJECTED)

Scores are 0-100 on every source so get_next_story() compares them fairly. Famous cases come first: a
story's fame is its Wikipedia article's page views (a topic people already search for), and a story
without an article never scores above UNKNOWN_MAX_SCORE (the famous Devil's Triangle Short got 458 views,
the obscure ones 18-86):
  wikipedia  page views over the last 30 days, on a log scale (300 -> 50, 3,000 -> 70, 30,000 -> 90)
  reddit     upvotes on a log scale (100 -> 50, 1,000 -> 75) or, from RSS, the rank in the month's top
             posts, capped at UNKNOWN_MAX_SCORE; a link post to Wikipedia scores as that article
  gemini     the page views of the case's Wikipedia article (it becomes the story's source, with its
             photos); Gemini's own 1-100 rating, capped at GEMINI_MAX_SCORE, when it has none
  rss        RSS_SCORE (the feeds have no engagement numbers)
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import logging
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, NamedTuple
from urllib.parse import unquote, urlparse

import requests
from pydantic import BaseModel, Field

from db import repository

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Wikimedia throttles user agents without contact details (HTTP 429)
USER_AGENT = "DarkArchives/0.1 (YouTube story research; sidequestlogic@gmail.com) python-requests"
TIMEOUT = 20
RETRIES = 3
MAX_WAIT = 60.0  # longest Retry-After we honour

MIN_CHARS, MAX_CHARS = 200, 3500
MIN_UPVOTES = 100
KEYWORDS = re.compile(r"myster|unexplain|vanish|strange|bizarre|creep|classified|incident|cold case", re.I)
EXCLUDE = re.compile(r"podcast|megathread|announcement|giveaway|creepypasta|nosleep|\bAMA\b|\[meta\]|"
                     r"[\[(](fiction|oc|story)[\])]", re.I)


class Sub(NamedTuple):
    pillar: str
    period: str = "month"
    keywords: bool = False  # a mixed-topic subreddit: only posts with one of KEYWORDS
    limit_scale: int = 1    # read more posts when most of them are filtered out


SUBREDDITS = {
    "UnresolvedMysteries": Sub("mystery"),
    "ColdCases": Sub("mystery"),
    "CreepyWikipedia": Sub("history"),
    "DarkHistory": Sub("history", period="year"),  # too small for a month of top posts
    "todayilearned": Sub("history", keywords=True, limit_scale=4),
    "TrueCrime": Sub("outrage"),
}
OLD_SUBREDDITS = {"UnsolvedMysteries": "mystery", "TrueCrimeDiscussion": "outrage"}  # still in the bank
WIKI_CATEGORIES = {
    "Unsolved murders in the United States": "mystery", "Unexplained disappearances": "mystery",
    "Unidentified decedents": "mystery", "Ghost ships": "mystery", "Unexplained phenomena": "mystery",
    "Mass disappearances": "mystery", "Unsolved deaths": "mystery",
    "Urban legends": "history", "Mass psychogenic illness": "history",
    "Human subject research in the United States": "history",
    "Medical scandals": "outrage",
}
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_LINK = re.compile(r"https?://en\.(?:m\.)?wikipedia\.org/wiki/([^?#]+)")
WIKI_MIN_CHARS = 1500  # an article shorter than this has no story to tell
WIKI_END_SECTIONS = ("See also", "References", "Notes", "Further reading", "External links", "Sources",
                     "Bibliography", "Citations", "In popular culture")
FEEDS = {"atlasobscura": "https://www.atlasobscura.com/feeds/latest", "listverse": "https://listverse.com/feed/"}
RSS_PILLAR = "outrage"
RSS_SCORE = 55
GEMINI_MAX_SCORE = 70
UNKNOWN_MAX_SCORE = 75  # a story with no Wikipedia article: below every article with 5,600+ views a month
REDDIT_PAUSE = 8.0  # seconds between Reddit feeds: the logged-out RSS limit is a few per minute
WIKI_PAUSE = 1.0
FEED_PAUSE = 2.0

Story = dict  # source_id, source_name, title, raw_text, url, score, pillar


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def get(session: requests.Session, url: str, sleep=time.sleep, **kw) -> requests.Response:
    """GET that waits and retries on 429 / 503, honouring Retry-After."""
    for attempt in range(RETRIES):
        r = session.get(url, timeout=TIMEOUT, **kw)
        if r.status_code not in (429, 503) or attempt == RETRIES - 1:
            return r
        try:
            wait = float(r.headers.get("Retry-After", ""))
        except ValueError:
            wait = 10.0 * (attempt + 1)
        sleep(min(max(wait, 1.0), MAX_WAIT))
    return r


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:80]


def on_topic(title: str, text: str) -> bool:
    """A keyword in the title, or two different keywords in the text (one passing "strange" in a long
    article is not enough)."""
    return bool(KEYWORDS.search(title)) or len({m.lower() for m in KEYWORDS.findall(text)}) >= 2


def screen(title: str, text: str, keywords: bool = False) -> str:
    """Why a story fails the fetch filters ("" = keep it)."""
    if len(text) < MIN_CHARS:
        return f"too short ({len(text)} chars)"
    if len(text) > MAX_CHARS:
        return f"too long ({len(text):,} chars)"
    if EXCLUDE.search(title):
        return "promo, meta post or fiction"
    if title.startswith(("List of", "Lists of")):
        return "a list, not a story"
    if keywords and not on_topic(title, text):
        return "no Dark Outliers keyword"
    return ""


def fit(text: str, limit: int = MAX_CHARS) -> str:
    """The opening paragraphs of `text` that fit in `limit` characters."""
    if len(text) <= limit:
        return text
    out = ""
    for para in text.split("\n\n"):
        if len(out) + len(para) + 2 > limit:
            break
        out = f"{out}\n\n{para}" if out else para
    if not out:  # one huge paragraph: cut at the last full sentence
        cut = text[:limit]
        out = cut[:cut.rfind(". ") + 1] or cut
    return out


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "li", "h1", "h2", "h3", "blockquote", "div"):
            self.parts.append("\n\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(markup: str) -> str:
    markup = markup.split("<!-- SC_ON -->")[0]  # drop the "submitted by … [link] [comments]" footer
    parser = _TextOnly()
    parser.feed(markup)
    text = html.unescape("".join(parser.parts)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# --------------------------------------------------------------------------- Wikipedia


def wiki_score(views: int) -> int:
    return min(100, round(20 * math.log10(max(views, 0) + 1)))


def wiki_fame(titles: list[str], session: requests.Session | None = None) -> dict[str, dict]:
    """{asked title: {"views", "url", "pageid"}} for the titles that have an English Wikipedia article
    (redirects followed), with its views over the last 30 days. Never raises: {} when Wikipedia does not answer."""
    session = session or _session()
    out: dict[str, dict] = {}
    for start in range(0, len(titles), 20):
        batch = [t for t in titles[start:start + 20] if t.strip()]
        if not batch:
            continue
        try:
            r = get(session, WIKI_API, params={"action": "query", "titles": "|".join(batch), "redirects": 1,
                                               "prop": "pageviews|info", "pvipdays": 30, "inprop": "url",
                                               "format": "json", "formatversion": 2})
            r.raise_for_status()
            data = r.json().get("query", {})
        except Exception as err:  # noqa: BLE001 - fame is a bonus
            logger.warning("Wikipedia page views: %s", err)
            continue
        names = {t: t for t in batch}
        for step in ("normalized", "redirects"):
            for m in data.get(step, []):
                for asked, now in names.items():
                    if now == m.get("from"):
                        names[asked] = m.get("to", now)
        pages = {p.get("title"): p for p in data.get("pages", []) if not p.get("missing") and "pageid" in p}
        for asked, now in names.items():
            page = pages.get(now)
            if page:
                out[asked] = {"views": sum(v or 0 for v in (page.get("pageviews") or {}).values()),
                              "url": page.get("fullurl", ""), "pageid": page["pageid"]}
    return out


def clean_extract(text: str) -> str:
    """Plain-text article without the reference sections at the end, cut to MAX_CHARS."""
    for name in WIKI_END_SECTIONS:
        text = re.split(rf"\n=+ {re.escape(name)} =+\n", text)[0]
    text = re.sub(r"\n=+ (.+?) =+\n", r"\n\n\1\n", text)
    return fit(re.sub(r"\n{3,}", "\n\n", text).strip())


def wiki_article(session: requests.Session, pageid: int | None = None, title: str | None = None) -> dict | None:
    """{pageid, title, text, url} of one article (by id or title), None when missing; raises on HTTP errors."""
    params = {"action": "query", "prop": "extracts|info", "inprop": "url", "explaintext": 1, "redirects": 1,
              "exsectionformat": "wiki", "format": "json", "formatversion": 2}
    params.update({"pageids": pageid} if pageid else {"titles": title})
    r = get(session, WIKI_API, params=params)
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or "pageid" not in pages[0]:
        return None
    info = pages[0]
    return {"pageid": info["pageid"], "title": info.get("title", ""), "text": clean_extract(info.get("extract", "")),
            "url": info.get("fullurl", "")}


def wiki_candidates(categories=WIKI_CATEGORIES, session: requests.Session | None = None) -> list[dict]:
    """Articles in the categories with their 30-day page views: [{pageid, title, views, category}]."""
    session = session or _session()
    found: dict[int, dict] = {}
    for cat in categories:
        r = get(session, WIKI_API, params={
            "action": "query", "generator": "categorymembers", "gcmtitle": f"Category:{cat}", "gcmtype": "page",
            "gcmlimit": 50, "prop": "pageviews", "pvipdays": 30, "format": "json", "formatversion": 2,
        })
        r.raise_for_status()
        for page in r.json().get("query", {}).get("pages", []):
            title = page.get("title", "")
            if title.startswith(("List of", "Lists of")) or title.lower() in cat.lower():
                continue  # lists and the article about the category's topic itself
            views = sum(v or 0 for v in (page.get("pageviews") or {}).values())
            found[page["pageid"]] = {"pageid": page["pageid"], "title": title, "views": views, "category": cat}
    return sorted(found.values(), key=lambda p: -p["views"])


def fetch_wikipedia(limit: int = 10, skip: set[str] = frozenset(), categories=WIKI_CATEGORIES,
                    session: requests.Session | None = None) -> list[Story]:
    """The `limit` most-viewed articles not yet in the bank, cut to their opening MAX_CHARS. Stops early
    (keeping what it has) if Wikipedia keeps refusing."""
    session = session or _session()
    out: list[Story] = []
    for page in wiki_candidates(categories, session):
        if len(out) >= limit:
            break
        source_id = f"wikipedia_{page['pageid']}"
        if source_id in skip:
            continue
        time.sleep(WIKI_PAUSE)
        try:
            art = wiki_article(session, pageid=page["pageid"])
        except requests.HTTPError as err:
            logger.warning("Wikipedia stopped answering (%s) after %d articles", err, len(out))
            break
        if not art or len(art["text"]) < WIKI_MIN_CHARS:
            continue
        out.append({
            "source_id": source_id, "source_name": f"wikipedia/{page['category']}", "title": page["title"],
            "raw_text": art["text"], "url": art["url"], "score": wiki_score(page["views"]),
            "pillar": categories.get(page["category"], "mystery") if isinstance(categories, dict) else "mystery",
        })
    return out


# --------------------------------------------------------------------------- Reddit


def upvote_score(ups: int) -> int:
    return min(100, round(25 * math.log10(max(ups, 1))))


def parse_reddit_feed(xml_text: str) -> list[dict]:
    """Posts of a subreddit's RSS feed, best first: [{id, title, text, link, url, rank, ups: None}].
    `link` is the external URL of a link post ("" for a text post)."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for rank, e in enumerate(ET.fromstring(xml_text).findall("a:entry", ns)):
        post_id = (e.findtext("a:id", "", ns) or "").removeprefix("t3_")
        if not post_id:
            continue
        content = e.findtext("a:content", "", ns) or ""
        link_el = e.find("a:link", ns)
        url = link_el.get("href", "") if link_el is not None else ""
        found = re.search(r'<a href="([^"]+)">\[link\]</a>', content)
        link = html.unescape(found.group(1)) if found else ""
        out.append({"id": post_id, "title": (e.findtext("a:title", "", ns) or "").strip(),
                    "text": html_to_text(content), "link": "" if "reddit.com" in link else link,
                    "url": url, "rank": rank, "ups": None})
    return out


def reddit_credentials() -> tuple[str, str] | None:
    from dotenv import dotenv_values

    env = dotenv_values(PROJECT_ROOT / ".env")
    cid = os.getenv("REDDIT_CLIENT_ID") or env.get("REDDIT_CLIENT_ID")
    secret = os.getenv("REDDIT_CLIENT_SECRET") or env.get("REDDIT_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


def reddit_token(session: requests.Session, credentials: tuple[str, str]) -> str:
    r = session.post("https://www.reddit.com/api/v1/access_token", auth=credentials,
                     data={"grant_type": "client_credentials"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["access_token"]


def parse_reddit_listing(data: dict) -> list[dict]:
    """Posts of an OAuth /top listing, in the same shape as parse_reddit_feed (with real upvotes)."""
    out = []
    for rank, child in enumerate(data.get("data", {}).get("children", [])):
        p = child.get("data", {})
        if p.get("over_18") or p.get("stickied"):
            continue
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", html.unescape(p.get("selftext") or "")).strip()
        out.append({"id": p.get("id", ""), "title": html.unescape(p.get("title", "")).strip(), "text": text,
                    "link": "" if p.get("is_self") else p.get("url", ""),
                    "url": "https://www.reddit.com" + p.get("permalink", ""), "rank": rank, "ups": p.get("score", 0)})
    return out


def reddit_posts(session: requests.Session, sub: str, cfg: Sub, limit: int, token: str | None) -> list[dict]:
    n = min(100, limit * cfg.limit_scale)
    if token:
        r = get(session, f"https://oauth.reddit.com/r/{sub}/top", params={"t": cfg.period, "limit": n},
                headers={"Authorization": f"bearer {token}"})
        r.raise_for_status()
        return parse_reddit_listing(r.json())
    r = get(session, f"https://www.reddit.com/r/{sub}/top/.rss", params={"t": cfg.period, "limit": n})
    if r.status_code != 200:
        logger.warning("Reddit r/%s: HTTP %s", sub, r.status_code)
        return []
    return parse_reddit_feed(r.text)


def reddit_story(post: dict, sub: str, cfg: Sub, session: requests.Session | None = None,
                 skip: set[str] = frozenset()) -> Story | None:
    """The story of one post: its own text, or the Wikipedia article a link post points to."""
    if post["ups"] is not None and post["ups"] < MIN_UPVOTES:
        return None
    score = min(upvote_score(post["ups"]) if post["ups"] is not None else max(40, 100 - 2 * post["rank"]), UNKNOWN_MAX_SCORE)
    story = {"source_id": f"reddit_{post['id']}", "source_name": f"reddit/{sub}", "title": post["title"],
             "raw_text": post["text"], "url": post["url"], "score": score, "pillar": cfg.pillar}
    wiki = WIKI_LINK.match(post["link"])
    if post["link"] and not wiki:
        return None  # a link to some other site: nothing to read here
    if wiki:
        if session is None:
            return None
        art = wiki_article(session, title=unquote(wiki.group(1)).replace("_", " "))
        if not art or f"wikipedia_{art['pageid']}" in skip:
            return None
        # the same article from a Wikipedia category would be the same story: one source_id for both
        fame = wiki_fame([art["title"]], session).get(art["title"])
        if fame:
            story["score"] = wiki_score(fame["views"])
        story.update(source_id=f"wikipedia_{art['pageid']}", title=art["title"], url=art["url"],
                     raw_text=f"{post['title']}\n\n{fit(art['text'], MAX_CHARS - len(post['title']) - 2)}")
    if screen(post["title"], story["raw_text"], cfg.keywords) or screen(story["title"], story["raw_text"]):
        return None
    return story


def fetch_reddit(limit: int = 25, subreddits: dict[str, Sub] | None = None, session: requests.Session | None = None,
                 skip: set[str] = frozenset()) -> list[Story]:
    session = session or _session()
    subreddits = subreddits or SUBREDDITS
    credentials = reddit_credentials()
    token = reddit_token(session, credentials) if credentials else None
    out: list[Story] = []
    for i, (sub, cfg) in enumerate(subreddits.items()):
        if i and not token:
            time.sleep(REDDIT_PAUSE)
        for post in reddit_posts(session, sub, cfg, limit, token):
            try:
                story = reddit_story(post, sub, cfg, session, skip)
            except requests.HTTPError as err:
                logger.warning("r/%s: Wikipedia link failed (%s)", sub, err)
                continue
            if story:
                out.append(story)
    return out


# --------------------------------------------------------------------------- RSS feeds


def parse_rss(xml_text: str) -> list[dict]:
    """[{title, link, text, categories}] of an RSS 2.0 feed."""
    out = []
    for item in ET.fromstring(xml_text).iter("item"):
        out.append({"title": html.unescape(item.findtext("title", "") or "").strip(),
                    "link": (item.findtext("link", "") or "").strip(),
                    "text": html_to_text(item.findtext("description", "") or ""),
                    "categories": [c.text or "" for c in item.findall("category")]})
    return out


def listverse_items(page_html: str) -> list[tuple[int, str, str]]:
    """The numbered entries of a Listverse list article: [(number, title, plain text)]."""
    out = []
    for m in re.finditer(r"<h2><span>(\d+)</span>\s*(.*?)</h2>(.*?)(?=<h2|<h3|<footer|$)", page_html, re.S):
        paras = [p for p in re.findall(r"<p>(.*?)</p>", m.group(3), re.S)
                 if "<noscript" not in p and not re.match(r"\s*Related:", html_to_text(p))]
        text = "\n\n".join(t for t in (html_to_text(p) for p in paras) if t)
        out.append((int(m.group(1)), html_to_text(m.group(2)), text))
    return out


def _path_slug(url: str) -> str:
    return slug(urlparse(url).path.strip("/").split("/")[-1])


def fetch_rss(limit: int = 10, feeds: dict[str, str] | None = None, session: requests.Session | None = None,
              skip: set[str] = frozenset()) -> list[Story]:
    """Up to `limit` new stories per feed. Atlas Obscura: one place per item. Listverse: each entry of a
    matching list article is its own story."""
    session = session or _session()
    out: list[Story] = []
    for name, url in (feeds or FEEDS).items():
        r = get(session, url)
        if r.status_code != 200:
            logger.warning("%s feed: HTTP %s", name, r.status_code)
            continue
        found = 0
        for item in parse_rss(r.text):
            if found >= limit:
                break
            topic = f"{item['title']} {' '.join(item['categories'])}"
            base = {"url": item["link"], "score": RSS_SCORE, "pillar": RSS_PILLAR}
            if name != "listverse":
                source_id = f"rss_{name}_{_path_slug(item['link'])}"
                if source_id not in skip and not screen(item["title"], item["text"], keywords=True):
                    out.append({**base, "source_id": source_id, "source_name": name, "title": item["title"],
                                "raw_text": item["text"]})
                    found += 1
                continue
            if not on_topic(topic, item["text"]):
                continue  # the article's theme decides; its entries need not repeat the keyword
            time.sleep(FEED_PAUSE)
            page = get(session, item["link"])
            if page.status_code != 200:
                continue
            for n, title, text in listverse_items(page.text):
                source_id = f"rss_listverse_{_path_slug(item['link'])}_{n}"
                if found >= limit or source_id in skip or screen(title, text):
                    continue
                out.append({**base, "source_id": source_id, "source_name": f"listverse/{item['title']}",
                            "title": title, "raw_text": text})
                found += 1
    return out


# --------------------------------------------------------------------------- Gemini

GEMINI_PROMPT = """You research stories for Dark Outliers, a documentary-style YouTube channel about obscure
history, eerie true events, unsolved mysteries and disturbing historical anomalies. Give {count}
different REAL, well-documented cases:
{mix}

For each case:
- title: the name the case is known by (as on Wikipedia), e.g. "Dyatlov Pass incident".
- pillar: mystery (unresolved mysteries, disappearances, unexplained phenomena, cold cases), history
  (forgotten events, strange psychological occurrences, disturbing medical or scientific experiments)
  or outrage (true crime, severe malpractice, bizarre documented events that provoke a strong reaction).
- story: 250-450 words of plain, factual text in chronological order: who, where, when (dates), what
  happened, what investigators found, the theories, and what is still unexplained. Only documented
  facts; write "reportedly" where sources disagree. Never invent names, quotes or details.
- hook_score: 1-100, how gripping this case is for a 30-second Short.

Today is {today}. At least {anniversaries} of the cases must be events whose anniversary (same month
and day, any year) falls within the next {window} days: for those, begin the story with
"Anniversary: <Month day, year>" (the date of the event), then the facts. Timely stories get shown more.

Prefer FAMOUS cases, the ones people already know by name and search for (Dyatlov Pass incident,
Mary Celeste, Flight 19, Roanoke Colony, D. B. Cooper), told through their most unsettling detail:
a known name stops the scroll, an unknown one does not. Every case must have its own English
Wikipedia article under the title you give. Never: fiction or creepypastas (unless it is
an internet urban legend, framed as one), gore without a story, recent political controversies or
ongoing news (open trials, cases from the last two years). Do NOT use any of these cases:
{avoid}"""


class GeminiCase(BaseModel):
    title: str
    pillar: Literal["mystery", "history", "outrage"] = "mystery"
    story: str
    hook_score: int = Field(50, ge=1, le=100)


class GeminiCases(BaseModel):
    cases: list[GeminiCase]


def pillar_split(count: int) -> dict[str, int]:
    """`count` cases split by the target mix (largest remainders first): 5 -> 2 / 2 / 1."""
    raw = {p: count * share for p, share in repository.PILLARS.items()}
    out = {p: int(v) for p, v in raw.items()}
    for p in sorted(raw, key=lambda p: out[p] - raw[p])[:count - sum(out.values())]:
        out[p] += 1
    return out


ANNIVERSARY_WINDOW = 10  # days: an "Anniversary:" story is the top pick while its date is this close
ANNIVERSARY_SCORE = 100
ANNIVERSARY = re.compile(r"^\s*Anniversary:\s*([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", re.I)


def anniversary_date(text: str, today: dt.date | None = None) -> dt.date | None:
    """The next occurrence (from today) of the "Anniversary: Month day, year" a story starts with, or None."""
    m = ANNIVERSARY.match(text or "")
    if not m:
        return None
    try:
        month = dt.datetime.strptime(m.group(1)[:3], "%b").month
        day = int(m.group(2))
    except ValueError:
        return None
    today = today or dt.date.today()
    for year in (today.year, today.year + 1):
        try:
            when = dt.date(year, month, day)
        except ValueError:
            continue
        if when >= today - dt.timedelta(days=1):
            return when
    return None


def anniversary_soon(text: str, today: dt.date | None = None, window: int = ANNIVERSARY_WINDOW) -> bool:
    when = anniversary_date(text, today)
    return when is not None and (when - (today or dt.date.today())).days <= window


def fetch_gemini(count: int = 5, avoid: list[str] | None = None, client=None, today: dt.date | None = None,
                 fame=None) -> list[Story]:
    """`count` cases from Gemini, each scored by its Wikipedia article's page views (`fame`: wiki_fame,
    replaceable in tests), which also becomes its source URL; an anniversary near today scores ANNIVERSARY_SCORE."""
    from google import genai
    from google.genai import errors, types

    from trendclip import script_writer
    from trendclip.config import get_settings

    settings = get_settings()
    api_key = script_writer.gemini_api_key(settings)
    if client is None and not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing in .env")
    client = client or genai.Client(api_key=api_key)
    model = script_writer.gemini_model(settings)
    mix = "\n".join(f"- {n} {p} ({repository.PILLAR_LABELS[p].lower()})" for p, n in pillar_split(count).items() if n)
    today = today or dt.date.today()
    prompt = GEMINI_PROMPT.format(count=count, mix=mix, today=today.isoformat(), anniversaries=max(1, count // 2),
                                  window=ANNIVERSARY_WINDOW,
                                  avoid="\n".join(f"- {t}" for t in (avoid or [])[:200]) or "- (none yet)")
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=GeminiCases,
                                         temperature=0.9)
    try:
        response, _ = script_writer._generate_with_fallback(client, model, [prompt], config, lambda f, m: None,
                                                            errors.APIError)
    except errors.APIError as err:
        raise script_writer._explain(err, model) from err
    parsed = response.parsed if isinstance(response.parsed, GeminiCases) else GeminiCases.model_validate_json(response.text)
    cases = [c for c in parsed.cases if c.title.strip() and c.story.strip()]
    known = (fame or wiki_fame)([c.title.strip() for c in cases])
    out = []
    for c in cases:
        page = known.get(c.title.strip())
        score = max(min(c.hook_score, GEMINI_MAX_SCORE), wiki_score(page["views"])) if page else min(c.hook_score, GEMINI_MAX_SCORE)
        out.append({
            "source_id": f"gemini_{slug(c.title)}", "source_name": "gemini", "title": c.title.strip(),
            "raw_text": c.story.strip(), "url": page["url"] if page else "",
            "score": ANNIVERSARY_SCORE if anniversary_soon(c.story, today) else score,
            "pillar": c.pillar,
        })
    return out


# --------------------------------------------------------------------------- run


def save_all(stories: list[Story], db_path=None) -> tuple[int, int]:
    """(saved, duplicates)."""
    saved = sum(repository.save_story(**s, db_path=db_path) for s in stories)
    return saved, len(stories) - saved


def pillar_for(source_name: str) -> str:
    """The pillar of a story from where it came (for stories saved before pillars existed)."""
    family, _, rest = source_name.partition("/")
    if family == "reddit":
        return SUBREDDITS[rest].pillar if rest in SUBREDDITS else OLD_SUBREDDITS.get(rest, "mystery")
    if family == "wikipedia":
        return WIKI_CATEGORIES.get(rest, "mystery")
    if family in FEEDS:
        return RSS_PILLAR
    return "mystery"


def recheck(db_path=None) -> dict[str, int]:
    """Bring the bank up to the current rules: give every story a pillar, cut NEW Wikipedia articles to
    MAX_CHARS, cap NEW stories with no Wikipedia article at UNKNOWN_MAX_SCORE (anniversaries keep their
    place), and reject NEW stories that fail the fetch filters (the reason goes in the note)."""
    repository.init_db(db_path)
    report = {"pillars": 0, "trimmed": 0, "capped": 0, "rejected": 0}
    for s in repository.all_stories(db_path=db_path):
        if not s["pillar"]:
            repository.update_story(s["id"], db_path, pillar=pillar_for(s["source_name"] or ""))
            report["pillars"] += 1
        if s["status"] != "NEW":
            continue
        text = s["raw_text"] or ""
        unknown = not WIKI_LINK.match(s["url"] or "") and not s["source_id"].startswith("wikipedia_")
        if unknown and (s["score"] or 0) > UNKNOWN_MAX_SCORE and not anniversary_soon(text):
            repository.update_story(s["id"], db_path, score=UNKNOWN_MAX_SCORE)
            report["capped"] += 1
        if s["source_id"].startswith("wikipedia_") and len(text) > MAX_CHARS:
            repository.update_story(s["id"], db_path, raw_text=fit(text))
            report["trimmed"] += 1
            continue
        family, _, rest = (s["source_name"] or "").partition("/")
        mixed = family in FEEDS or (family == "reddit" and SUBREDDITS.get(rest, Sub("")).keywords)
        # a Listverse entry is on topic when its list article is (as when it was fetched)
        topic = f"{s['title'] or ''} {rest}" if family == "listverse" else s["title"] or ""
        reason = screen(topic, text, keywords=mixed)
        if family == "wikipedia" and (s["title"] or "").lower() in rest.lower():
            reason = "the article about the topic itself, not a story"
        if reason:
            repository.set_status(s["id"], "REJECTED", db_path, note=f"fetch filter: {reason}")
            report["rejected"] += 1
    return report


SOURCES = ("reddit", "wikipedia", "gemini", "rss")


def run(sources=SOURCES, reddit: int = 25, wikipedia: int = 10, gemini: int = 5, rss: int = 10,
        db_path=None, progress=lambda name, report: None) -> dict[str, dict]:
    """Fetch from each source and save. One failing source never stops the others. `progress(name,
    report)` is called as each source starts (and once more with name None at the end)."""
    repository.init_db(db_path)
    report: dict[str, dict] = {}
    for name in sources:
        progress(name, report)
        try:
            if name == "reddit":
                stories = fetch_reddit(reddit, skip=repository.known_ids("wikipedia_", db_path))
            elif name == "wikipedia":
                stories = fetch_wikipedia(wikipedia, skip=repository.known_ids("wikipedia_", db_path))
            elif name == "gemini":
                from trendclip import gemini_quota

                if gemini_quota.low():
                    report[name] = {"skipped": "Gemini's free budget is low today; the videos come first"}
                    continue
                stories = fetch_gemini(gemini, avoid=repository.known_titles(db_path))
            elif name == "rss":
                stories = fetch_rss(rss, skip=repository.known_ids("rss_", db_path))
            else:
                raise ValueError(f"unknown source '{name}'")
            saved, dupes = save_all(stories, db_path)
            report[name] = {"fetched": len(stories), "saved": saved, "duplicates": dupes}
        except Exception as err:  # noqa: BLE001 - report it and go on with the next source
            logger.exception("%s failed", name)
            report[name] = {"error": str(err)[:300]}
    progress(None, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sources", default=",".join(SOURCES))
    parser.add_argument("--reddit", type=int, default=25, help="posts per subreddit feed")
    parser.add_argument("--wikipedia", type=int, default=10, help="new articles")
    parser.add_argument("--gemini", type=int, default=5, help="new cases")
    parser.add_argument("--rss", type=int, default=10, help="new stories per RSS feed")
    parser.add_argument("--recheck", action="store_true", help="only apply the filters to the bank")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if args.recheck:
        print("recheck:", recheck())
    else:
        report = run([s.strip() for s in args.sources.split(",") if s.strip()], args.reddit, args.wikipedia,
                     args.gemini, args.rss)
        for name, r in report.items():
            print(f"{name:10} " + (f"ERROR {r['error']}" if "error" in r else
                                   f"fetched {r['fetched']}, saved {r['saved']}, duplicates {r['duplicates']}"))
    print("bank:", repository.counts())
    print("mix: ", {p: f"{m['produced']} made, {m['new']} new" for p, m in repository.pillar_mix().items()})
    nxt = repository.get_next_story()
    if nxt:
        print(f"next story: [{nxt['pillar']} {nxt['score']}] {nxt['title']} ({nxt['source_name']}, "
              f"{len(nxt['raw_text'])} chars)")


if __name__ == "__main__":
    main()
