"""Insights: how the channel's own videos perform, so the next ones can be made like the winners.

Every video the channel published is kept in output/analytics.db with what it was made of (from the
Short's sidecar: format, kind of script, game, hook sentence and its style, length, upload slot, music
mood) and a daily snapshot of its numbers: views, likes and comments from the Data API, and, with the
YouTube login's analytics scope, watch time, average view duration / percentage, shares and subscribers
gained from the YouTube Analytics API (its numbers run about two days behind).

build_report() groups the videos of the last N days by each of those attributes and ranks the groups by
median views (robust to one viral hit), with the views per day since upload, the retention and the
engagement per 1,000 views next to it, so "which formats, games and hook styles perform best" has an
answer in the dashboard (Insights tab), in Discord (/insights, the morning summary) and on the CLI:

    python -m trendclip.analytics --refresh        # read YouTube, then print the report
    python -m trendclip.analytics --days 30
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import statistics
import sys
from contextlib import closing
from datetime import date as Day, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import planner, shorts
from .config import Settings

logger = logging.getLogger(__name__)

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
ANALYTICS_METRICS = ["views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
                     "likes", "comments", "shares", "subscribersGained"]
ANALYTICS_LAG_DAYS = 2  # YouTube Analytics finishes a day's numbers about two days later
MAX_VIDEOS_PER_QUERY = 200  # the video dimension returns at most 200 rows per query
TRACK_DAYS = 365  # channel uploads older than this are not tracked
MIN_AGE_DAYS = 2  # a video younger than this has no fair numbers yet: listed, but not in the group ranks
DIMENSIONS = {"format": "Format", "mode": "Kind of video", "game": "Game", "hook_style": "Hook style",
              "length_bucket": "Length", "slot": "Upload time", "music_mood": "Music"}
MODES = ("story", "math", "riddle", "trivia", "paradox", "movie")
MODE_LABELS = {"story": "Story", "math": "Math challenge", "riddle": "Riddle", "trivia": "Trivia trap",
               "paradox": "Paradox trap", "movie": "Movie quiz", "other": "Other / made elsewhere"}
FORMAT_LABELS = {"short": "Short", "multi": "Multi-part", "long": "Long 16:9", "other": "Unknown"}
HOOK_STYLES = {
    "dare": "Dare (\"Only 5% get this\")",
    "question": "Question",
    "number": "Starts with a number",
    "quote": "Quote",
    "command": "Command (\"Name this movie\")",
    "first_person": "First person (\"I …\", \"My …\")",
    "claim": "Bold claim",
}
# No trailing \b: "%" is not a word character, so "only 3% can" would never end on a word boundary.
_DARE = re.compile(r"\b(only \d+ ?%|\d+ ?% (of people|get|fail)|most people|can you|bet you|nobody|no one can|"
                   r"impossible|if you|are you smart)(?!\w)", re.I)
_COMMAND_WORDS = {"name", "guess", "watch", "start", "imagine", "stop", "listen", "think", "picture", "try",
                  "look", "wait", "remember", "tell", "answer", "finish", "solve", "spot", "find"}
_FIRST_PERSON = re.compile(r"^(i|i'm|i've|i'd|i'll|my|we|we're|our|me)\b", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_TITLE_MODE = [("math", re.compile(r"\b(math|calculat|number|count|add|subtract|divide|multiply)\w*", re.I)),
               ("riddle", re.compile(r"\briddle", re.I)),
               ("trivia", re.compile(r"\btrivia|\bwhich (country|planet|animal)|true or false", re.I)),
               ("movie", re.compile(r"\b(movie|film|actually sa(y|id)|quote|scene)\b", re.I)),
               ("paradox", re.compile(r"\b(paradox|keep or|would you|team [a-z]+ or)\b", re.I))]


class AnalyticsError(RuntimeError):
    pass


# --------------------------------------------------------------------------- what a video is made of


class VideoFacts(BaseModel):
    video_id: str
    filename: str = ""  # the Short's file here (empty for a video made elsewhere)
    title: str = ""
    published_at: datetime | None = None
    format: str = "other"  # short / multi / long / other
    mode: str = "other"  # story / math / riddle / trivia / paradox / movie / other
    game: str = ""
    hook: str = ""  # the first spoken sentence
    hook_style: str = ""
    length_seconds: float | None = None
    length_bucket: str = ""
    slot: str = ""  # "14:30" / "23:00" (the plan's slots) or the hour it went out
    music_mood: str = ""
    part: int | None = None
    made_here: bool = True


def hook_of(script: str) -> str:
    """The first sentence of the voiceover (what the viewer hears in the first 3 seconds)."""
    text = " ".join(script.split())
    if not text:
        return ""
    first = _SENTENCE_END.split(text, maxsplit=1)[0].strip()
    return first[:160]


def hook_style(hook: str) -> str:
    text = hook.strip()
    if not text:
        return ""
    if _DARE.search(text):
        return "dare"
    if text.endswith("?"):
        return "question"
    if text[0] in "\"'“‘":
        return "quote"
    if text[0].isdigit():
        return "number"
    if _FIRST_PERSON.match(text):
        return "first_person"
    first_word = re.sub(r"[^a-z']", "", text.split()[0].lower())
    if first_word in _COMMAND_WORDS:
        return "command"
    return "claim"


def length_bucket(seconds: float | None) -> str:
    if seconds is None:
        return ""
    for limit, label in ((15, "≤15 s"), (22, "16-22 s"), (30, "23-30 s"), (45, "31-45 s"), (60, "46-60 s"),
                         (120, "1-2 min")):
        if seconds <= limit:
            return label
    return ">2 min"


def mode_of(short: shorts.ShortVideo) -> str:
    """The kind of script: the recorded mode when the Short has one, else read from what was rendered."""
    if short.mode in MODES:
        return short.mode
    flashes = short.render_settings.get("flashes") or []
    texts = " ".join(str(f.get("text", "")) for f in flashes if isinstance(f, dict))
    if flashes or short.answer:
        if re.search(r"[+\-−×÷]|HALF|THIRD|SQUARE|DOUBLE", texts):
            return "math"
        if "CLUE" in texts:
            return "riddle"
        if re.search(r"\b[ABC]\)", texts) or re.search(r"\b[ABC]\b", texts):
            # Options A / B / C: a movie misquote or a trivia trap; the title / comment tell them apart.
            return "movie" if re.search(r"movie|film|actually", f"{short.title} {short.pinned_comment}", re.I) \
                else "trivia"
        return mode_from_title(f"{short.title} {short.pinned_comment}") or "trivia"
    guessed = mode_from_title(short.title)
    return guessed if guessed == "paradox" else "story"


def mode_from_title(title: str) -> str | None:
    for mode, pattern in _TITLE_MODE:
        if pattern.search(title):
            return mode
    return None


def format_of(short: shorts.ShortVideo) -> str:
    if short.aspect == "landscape":
        return "long"
    if short.series_id or short.part:
        return "multi"
    return "short"


def slot_label(settings: Settings, published: datetime | None) -> str:
    if published is None:
        return ""
    local = published.astimezone(settings.tz)
    minutes = local.hour * 60 + local.minute
    for slot in planner.SLOTS:
        h, m = map(int, slot.split(":"))
        if abs(minutes - (h * 60 + m)) <= 20:
            return slot
    return f"{local.hour:02d}:00"


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def facts_from_short(settings: Settings, short: shorts.ShortVideo) -> VideoFacts | None:
    """The attributes of a Short that was uploaded to YouTube from here (None when it wasn't)."""
    upload = short.uploads.get("youtube") or {}
    video_id = upload.get("video_id")
    if not video_id:
        return None
    published = _parse_ts(upload.get("scheduled_for")) or _parse_ts(upload.get("uploaded_at")) or short.created_at
    hook = hook_of(short.script)
    return VideoFacts(
        video_id=video_id, filename=short.filename, title=short.title, published_at=published,
        format=format_of(short), mode=mode_of(short), game=short.game, hook=hook, hook_style=hook_style(hook),
        length_seconds=short.duration_seconds, length_bucket=length_bucket(short.duration_seconds),
        slot=slot_label(settings, published), music_mood=short.music_mood or "", part=short.part, made_here=True,
    )


def facts_from_channel(settings: Settings, video_id: str, title: str, published: datetime | None) -> VideoFacts:
    """A channel upload with no sidecar here (made by hand or on the other machine): title and time only."""
    return VideoFacts(video_id=video_id, title=title, published_at=published, format="other",
                      mode=mode_from_title(title) or "other", slot=slot_label(settings, published), made_here=False)


# --------------------------------------------------------------------------- storage


class Snapshot(BaseModel):
    video_id: str
    day: Day
    views: int = 0
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    minutes_watched: float | None = None
    avg_view_seconds: float | None = None
    avg_view_pct: float | None = None
    subs_gained: int | None = None
    source: str = "data"  # "data": the Data API only; "analytics": the Analytics API too


SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    filename TEXT DEFAULT '',
    title TEXT DEFAULT '',
    published_at TEXT,
    format TEXT DEFAULT 'other',
    mode TEXT DEFAULT 'other',
    game TEXT DEFAULT '',
    hook TEXT DEFAULT '',
    hook_style TEXT DEFAULT '',
    length_seconds REAL,
    length_bucket TEXT DEFAULT '',
    slot TEXT DEFAULT '',
    music_mood TEXT DEFAULT '',
    part INTEGER,
    made_here INTEGER DEFAULT 1,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    video_id TEXT NOT NULL,
    day TEXT NOT NULL,
    views INTEGER DEFAULT 0,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    minutes_watched REAL,
    avg_view_seconds REAL,
    avg_view_pct REAL,
    subs_gained INTEGER,
    source TEXT DEFAULT 'data',
    PRIMARY KEY (video_id, day)
);
"""
_VIDEO_COLUMNS = ("video_id", "filename", "title", "published_at", "format", "mode", "game", "hook", "hook_style",
                  "length_seconds", "length_bucket", "slot", "music_mood", "part", "made_here")
_SNAPSHOT_COLUMNS = ("video_id", "day", "views", "likes", "comments", "shares", "minutes_watched",
                     "avg_view_seconds", "avg_view_pct", "subs_gained", "source")


def db_path(settings: Settings) -> Path:
    return settings.output_dir / "analytics.db"


def _connect(settings: Settings) -> sqlite3.Connection:
    path = db_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _video_row(facts: VideoFacts) -> tuple:
    data = facts.model_dump()
    data["published_at"] = facts.published_at.isoformat() if facts.published_at else None
    data["made_here"] = int(facts.made_here)
    return tuple(data[c] for c in _VIDEO_COLUMNS)


def save_facts(settings: Settings, facts: list[VideoFacts], keep_existing: bool = False) -> None:
    """Insert or update the videos. `keep_existing`: only fill in videos not stored yet (channel uploads never
    overwrite the richer sidecar facts)."""
    if not facts:
        return
    cols = ", ".join(_VIDEO_COLUMNS)
    marks = ", ".join("?" * len(_VIDEO_COLUMNS))
    sql = (f"INSERT OR IGNORE INTO videos ({cols}, updated_at) VALUES ({marks}, ?)" if keep_existing else
           f"INSERT OR REPLACE INTO videos ({cols}, updated_at) VALUES ({marks}, ?)")
    now = datetime.now(timezone.utc).isoformat()
    with closing(_connect(settings)) as conn, conn:
        conn.executemany(sql, [(*_video_row(f), now) for f in facts])


def refresh_published(settings: Settings, published: dict[str, tuple[str, datetime | None]]) -> None:
    """The channel's own publish times (and titles for videos made elsewhere) win over the upload record."""
    with closing(_connect(settings)) as conn, conn:
        for video_id, (title, when) in published.items():
            if when is None:
                continue
            conn.execute("UPDATE videos SET published_at = ?, slot = ?, "
                         "title = CASE WHEN made_here = 1 AND title != '' THEN title ELSE ? END WHERE video_id = ?",
                         (when.isoformat(), slot_label(settings, when), title, video_id))


def save_snapshots(settings: Settings, snaps: list[Snapshot]) -> None:
    if not snaps:
        return
    cols = ", ".join(_SNAPSHOT_COLUMNS)
    marks = ", ".join("?" * len(_SNAPSHOT_COLUMNS))
    with closing(_connect(settings)) as conn, conn:
        conn.executemany(f"INSERT OR REPLACE INTO snapshots ({cols}) VALUES ({marks})",
                         [tuple(s.day.isoformat() if c == "day" else getattr(s, c) for c in _SNAPSHOT_COLUMNS)
                          for s in snaps])


def load_videos(settings: Settings) -> list[VideoFacts]:
    with closing(_connect(settings)) as conn:
        rows = conn.execute("SELECT * FROM videos").fetchall()
    out = []
    for r in rows:
        data = {c: r[c] for c in _VIDEO_COLUMNS}
        data["published_at"] = _parse_ts(data["published_at"])
        data["made_here"] = bool(data["made_here"])
        out.append(VideoFacts(**data))
    return out


def load_snapshots(settings: Settings) -> dict[str, list[Snapshot]]:
    """Every video's snapshots, oldest first."""
    with closing(_connect(settings)) as conn:
        rows = conn.execute("SELECT * FROM snapshots ORDER BY video_id, day").fetchall()
    out: dict[str, list[Snapshot]] = {}
    for r in rows:
        data = {c: r[c] for c in _SNAPSHOT_COLUMNS}
        data["day"] = Day.fromisoformat(data["day"])
        out.setdefault(data["video_id"], []).append(Snapshot(**data))
    return out


# --------------------------------------------------------------------------- reading YouTube


class CollectResult(BaseModel):
    day: Day
    videos: int = 0  # videos tracked
    from_sidecars: int = 0
    from_channel: int = 0
    snapshots: int = 0
    with_retention: int = 0
    warnings: list[str] = Field(default_factory=list)


def _youtube(settings: Settings):
    from . import publish

    return publish._youtube()


def _analytics_client():
    from googleapiclient.discovery import build

    from . import publish

    return build("youtubeAnalytics", "v2", credentials=publish._credentials(), cache_discovery=False)


def _analytics_error(err: Exception) -> str:
    text = str(getattr(err, "content", b"") or err)
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if "accessNotConfigured" in text or "has not been used" in text:
        return ("the YouTube Analytics API is not enabled: Google Cloud console → APIs & Services → Library → "
                "YouTube Analytics API → Enable")
    if "insufficient" in text.lower() or "forbidden" in text.lower():
        return "the YouTube login lacks the analytics permission: Upload tab → Disconnect → Connect YouTube again"
    return text[:200]


def _analytics_rows(analytics, video_ids: list[str], start: Day, end: Day) -> dict[str, dict[str, float]]:
    """Lifetime totals per video (start..end) from the Analytics API, in chunks of MAX_VIDEOS_PER_QUERY."""
    out: dict[str, dict[str, float]] = {}
    for i in range(0, len(video_ids), MAX_VIDEOS_PER_QUERY):
        chunk = video_ids[i:i + MAX_VIDEOS_PER_QUERY]
        resp = analytics.reports().query(
            ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
            metrics=",".join(ANALYTICS_METRICS), dimensions="video", filters="video==" + ",".join(chunk),
            maxResults=MAX_VIDEOS_PER_QUERY, sort="-views",
        ).execute()
        names = [h["name"] for h in resp.get("columnHeaders", [])]
        for row in resp.get("rows", []) or []:
            record = dict(zip(names, row))
            video_id = str(record.pop("video", ""))
            out[video_id] = {k: float(v or 0) for k, v in record.items()}

    return out


def _data_stats(youtube, video_ids: list[str]) -> dict[str, dict[str, int | None]]:
    out: dict[str, dict[str, int | None]] = {}
    for i in range(0, len(video_ids), 50):
        resp = youtube.videos().list(part="statistics", id=",".join(video_ids[i:i + 50])).execute()
        for item in resp.get("items", []):
            s = item.get("statistics", {})
            out[item["id"]] = {k: (int(s[key]) if s.get(key) is not None else None)
                               for k, key in (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount"))}
    return out


def collect(settings: Settings, youtube=None, analytics=None, at: datetime | None = None) -> CollectResult:
    """Read every tracked video's numbers from YouTube into today's snapshot. The YouTube login is needed; the
    Analytics API numbers (retention…) are added when the login has the analytics scope."""
    from . import leaderboard, publish

    now = at or datetime.now(settings.tz)
    today = now.date()
    result = CollectResult(day=today)

    # 1. Shorts uploaded from here: everything the sidecar knows.
    facts = []
    for short in shorts.list_shorts(settings):
        f = facts_from_short(settings, short)
        if f:
            facts.append(f)
    save_facts(settings, facts)
    result.from_sidecars = len(facts)

    if youtube is None:
        try:
            youtube = _youtube(settings)
        except publish.PublishError as err:
            raise AnalyticsError(f"{err} (the insights need the channel's YouTube login)") from err

    # 2. The channel's uploads list: real publish times, and the videos not made here.
    try:
        _, channel_videos = leaderboard.channel_videos(youtube)
    except Exception as err:  # noqa: BLE001 - the sidecar videos still get their numbers
        logger.warning("Could not list the channel's uploads: %s", err)
        result.warnings.append(f"Could not list the channel's uploads: {err}")
        channel_videos = []
    known = {f.video_id for f in load_videos(settings)}
    since = now - timedelta(days=TRACK_DAYS)
    published: dict[str, tuple[str, datetime | None]] = {}
    extra = []
    for video in channel_videos:
        when = _parse_ts(video.published)
        if when and when < since:
            continue
        published[video.id] = (video.title, when)
        if video.id not in known:
            extra.append(facts_from_channel(settings, video.id, video.title, when))
    save_facts(settings, extra, keep_existing=True)
    refresh_published(settings, published)
    result.from_channel = len(extra)

    videos = load_videos(settings)
    ids = [v.video_id for v in videos]
    result.videos = len(ids)
    if not ids:
        return result

    # 3. Today's numbers: Data API (views / likes / comments) for every video...
    stats = _data_stats(youtube, ids)
    snaps = {vid: Snapshot(video_id=vid, day=today, **stats[vid]) for vid in ids if vid in stats}

    # 4. ...plus the Analytics API (watch time, retention, shares, subscribers) when allowed.
    if analytics is None and publish.has_scope(ANALYTICS_SCOPE):
        try:
            analytics = _analytics_client()
        except Exception as err:  # noqa: BLE001 - discovery / credentials
            result.warnings.append(f"YouTube Analytics unavailable: {err}")
    if analytics is not None:
        first = min((v.published_at for v in videos if v.published_at), default=None)
        start = (first.date() if first else today - timedelta(days=TRACK_DAYS)) - timedelta(days=1)
        try:
            rows = _analytics_rows(analytics, ids, start, today)
        except Exception as err:  # noqa: BLE001 - HttpError and friends; the Data API numbers are kept
            logger.warning("YouTube Analytics query failed: %s", err)
            result.warnings.append(f"YouTube Analytics: {_analytics_error(err)}")
            rows = {}
        for vid, m in rows.items():
            snap = snaps.get(vid) or Snapshot(video_id=vid, day=today, views=int(m.get("views", 0)))
            snap.shares = int(m.get("shares", 0))
            snap.minutes_watched = m.get("estimatedMinutesWatched")
            snap.avg_view_seconds = m.get("averageViewDuration")
            snap.avg_view_pct = m.get("averageViewPercentage")
            snap.subs_gained = int(m.get("subscribersGained", 0))
            snap.source = "analytics"
            snaps[vid] = snap
        result.with_retention = sum(s.source == "analytics" for s in snaps.values())
    elif not publish.has_scope(ANALYTICS_SCOPE):
        result.warnings.append("No retention numbers: the YouTube login was made before insights existed. "
                               "Upload tab → Disconnect → Connect YouTube again to allow YouTube Analytics.")

    save_snapshots(settings, list(snaps.values()))
    result.snapshots = len(snaps)
    return result


# --------------------------------------------------------------------------- the report


class VideoRow(BaseModel):
    video_id: str
    url: str
    title: str
    filename: str = ""
    published_at: datetime | None
    age_days: float
    format: str
    mode: str
    game: str
    hook: str
    hook_style: str
    length_bucket: str
    slot: str
    music_mood: str
    views: int
    views_per_day: float
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    avg_view_pct: float | None = None
    avg_view_seconds: float | None = None
    subs_gained: int | None = None
    day1_views: int | None = None  # views in the first day (needs a snapshot taken then)
    day7_views: int | None = None
    ranked: bool = True  # old enough to count in the group ranks


class GroupStat(BaseModel):
    key: str
    label: str
    n: int
    median_views: float
    mean_views: float
    views_per_day: float  # median
    vs_channel: float | None  # median views / the channel's median views (1.0 = average)
    avg_view_pct: float | None  # mean over the videos that have it
    likes_per_k: float | None
    comments_per_k: float | None
    total_views: int
    best_title: str = ""
    best_video_id: str = ""


class Dimension(BaseModel):
    key: str
    label: str
    groups: list[GroupStat]


class Report(BaseModel):
    generated_at: datetime
    days: int
    min_age_days: int = MIN_AGE_DAYS
    videos: int = 0  # in the window
    ranked: int = 0  # old enough to be ranked
    total_views: int = 0
    median_views: float = 0
    with_retention: int = 0
    latest_snapshot: Day | None = None
    dimensions: list[Dimension] = Field(default_factory=list)
    top_views: list[VideoRow] = Field(default_factory=list)
    top_retention: list[VideoRow] = Field(default_factory=list)
    top_hooks: list[VideoRow] = Field(default_factory=list)
    rows: list[VideoRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _label(dimension: str, key: str) -> str:
    if dimension == "format":
        return FORMAT_LABELS.get(key, key)
    if dimension == "mode":
        return MODE_LABELS.get(key, key)
    if dimension == "hook_style":
        return HOOK_STYLES.get(key, key or "No script here")
    if dimension == "music_mood":
        return {"funny_quirky": "Funny / quirky", "chill_lofi": "Chill lo-fi", "dramatic_suspense": "Dramatic",
                "": "No music / unknown"}.get(key, key)
    return key or "Unknown"


def _rows(settings: Settings, at: datetime, days: int, min_age_days: int) -> tuple[list[VideoRow], Day | None]:
    videos = load_videos(settings)
    snapshots = load_snapshots(settings)
    rows: list[VideoRow] = []
    latest: Day | None = None
    for v in videos:
        snaps = snapshots.get(v.video_id)
        if not snaps or v.published_at is None:
            continue
        last = snaps[-1]
        latest = max(latest, last.day) if latest else last.day
        if v.published_at < at - timedelta(days=days) or v.published_at > at:
            continue
        age = max((at - v.published_at).total_seconds() / 86400, 0.0)
        pub_day = v.published_at.astimezone(settings.tz).date()
        by_offset = {(s.day - pub_day).days: s for s in snaps}
        rows.append(VideoRow(
            video_id=v.video_id, url=f"https://youtu.be/{v.video_id}", title=v.title, filename=v.filename,
            published_at=v.published_at, age_days=round(age, 1), format=v.format, mode=v.mode, game=v.game,
            hook=v.hook, hook_style=v.hook_style, length_bucket=v.length_bucket, slot=v.slot,
            music_mood=v.music_mood, views=last.views, views_per_day=round(last.views / max(age, 1.0), 1),
            likes=last.likes, comments=last.comments, shares=last.shares, avg_view_pct=last.avg_view_pct,
            avg_view_seconds=last.avg_view_seconds, subs_gained=last.subs_gained,
            day1_views=by_offset[1].views if 1 in by_offset else None,
            day7_views=by_offset[7].views if 7 in by_offset else None,
            ranked=age >= min_age_days,
        ))
    return rows, latest


def _per_k(total: int | None, views: int) -> float | None:
    return round(total / views * 1000, 1) if total is not None and views else None


def _group(dimension: str, key: str, rows: list[VideoRow], channel_median: float) -> GroupStat:
    views = [r.views for r in rows]
    median = statistics.median(views)
    with_pct = [r.avg_view_pct for r in rows if r.avg_view_pct is not None]
    likes = [r.likes for r in rows if r.likes is not None]
    comments = [r.comments for r in rows if r.comments is not None]
    total = sum(views)
    best = max(rows, key=lambda r: r.views)
    return GroupStat(
        key=key, label=_label(dimension, key), n=len(rows), median_views=median,
        mean_views=round(statistics.fmean(views), 1), views_per_day=statistics.median(r.views_per_day for r in rows),
        vs_channel=round(median / channel_median, 2) if channel_median else None,
        avg_view_pct=round(statistics.fmean(with_pct), 1) if with_pct else None,
        likes_per_k=_per_k(sum(likes), total) if likes else None,
        comments_per_k=_per_k(sum(comments), total) if comments else None,
        total_views=total, best_title=best.title, best_video_id=best.video_id,
    )


def build_report(settings: Settings, days: int = 90, min_age_days: int = MIN_AGE_DAYS,
                 at: datetime | None = None) -> Report:
    now = at or datetime.now(settings.tz)
    rows, latest = _rows(settings, now, days, min_age_days)
    report = Report(generated_at=now, days=days, min_age_days=min_age_days, videos=len(rows), latest_snapshot=latest,
                    rows=sorted(rows, key=lambda r: r.published_at or now, reverse=True))
    ranked = [r for r in rows if r.ranked]
    report.ranked = len(ranked)
    report.total_views = sum(r.views for r in rows)
    report.with_retention = sum(r.avg_view_pct is not None for r in rows)
    if not rows:
        report.notes.append("No videos yet: press Refresh (or wait for the morning summary) after uploading.")
        return report
    if latest and (now.date() - latest).days > 1:
        report.notes.append(f"Numbers last read on {latest.isoformat()}; press Refresh for today's.")
    if not report.with_retention:
        from . import publish

        if publish.has_scope(ANALYTICS_SCOPE):
            report.notes.append("No retention (average view %) yet: the YouTube Analytics API must be enabled in the "
                                "Google Cloud project (APIs & Services → Library → YouTube Analytics API → Enable), "
                                "then Refresh. Views, likes and comments are counted meanwhile.")
        else:
            report.notes.append("Retention (average view %) needs the YouTube Analytics permission: Upload tab → "
                                "Disconnect → Connect YouTube again. Views, likes and comments are counted meanwhile.")

    if not ranked:
        report.notes.append(f"Every video is younger than {min_age_days} days; the group ranks start once they age.")
        return report
    channel_median = statistics.median(r.views for r in ranked)
    report.median_views = channel_median
    for key, label in DIMENSIONS.items():
        buckets: dict[str, list[VideoRow]] = {}
        for r in ranked:
            buckets.setdefault(getattr(r, key), []).append(r)
        if len(buckets) < 1:
            continue
        groups = sorted((_group(key, k, v, channel_median) for k, v in buckets.items()),
                        key=lambda g: (g.median_views, g.n), reverse=True)
        report.dimensions.append(Dimension(key=key, label=label, groups=groups))
    report.top_views = sorted(ranked, key=lambda r: r.views, reverse=True)[:10]
    report.top_retention = sorted((r for r in ranked if r.avg_view_pct is not None),
                                  key=lambda r: r.avg_view_pct or 0, reverse=True)[:5]
    report.top_hooks = sorted((r for r in ranked if r.hook), key=lambda r: r.views, reverse=True)[:8]
    return report


# --------------------------------------------------------------------------- text for Discord / the CLI


def _n(value: float | int | None) -> str:
    if value is None:
        return "–"
    value = float(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{value / 1000:.0f}k"
    if value >= 1_000:
        return f"{value / 1000:.1f}k"
    return f"{value:.0f}"


def _vs(ratio: float | None) -> str:
    if ratio is None:
        return ""
    return f" ({ratio:.1f}× the channel median)" if abs(ratio - 1) >= 0.15 else " (about average)"


def _group_line(g: GroupStat) -> str:
    extra = f", {g.avg_view_pct:.0f}% watched" if g.avg_view_pct is not None else ""
    return f"**{g.label}**: {_n(g.median_views)} views median over {g.n} video{'s' * (g.n != 1)}{extra}{_vs(g.vs_channel)}"


def insight_lines(report: Report, per_dimension: int = 1, dimensions: tuple[str, ...] = ("mode", "game", "hook_style")) -> list[str]:
    """A few lines for the morning summary: the best group of each dimension, with enough videos to mean something."""
    lines = []
    for dim in report.dimensions:
        if dim.key not in dimensions:
            continue
        solid = [g for g in dim.groups if g.n >= 2] or dim.groups
        for g in solid[:per_dimension]:
            lines.append(f"• {dim.label} → {_group_line(g)}")
    return lines


def format_report(report: Report, per_dimension: int = 3) -> list[str]:
    """The whole report as Discord messages."""
    head = (f"📈 **Insights: the last {report.days} days** · {report.videos} video{'s' * (report.videos != 1)}, "
            f"{_n(report.total_views)} views, median {_n(report.median_views)} per video")
    if report.with_retention:
        head += f" · retention known for {report.with_retention}"
    lines = [head]
    for note in report.notes:
        lines.append(f"ℹ️ {note}")
    for dim in report.dimensions:
        lines += ["", f"__{dim.label}__"]
        # Groups of one video say little: show them only when there is nothing better.
        solid = [g for g in dim.groups if g.n >= 2] or dim.groups
        for g in solid[:per_dimension]:
            lines.append(f"• {_group_line(g)}")
        if len(solid) > per_dimension:
            worst = solid[-1]
            lines.append(f"• … weakest: {worst.label} ({_n(worst.median_views)} median, {worst.n})")

    if report.top_views:
        lines += ["", "__Top videos by views__"]
        for r in report.top_views[:5]:
            pct = f" · {r.avg_view_pct:.0f}% watched" if r.avg_view_pct is not None else ""
            lines.append(f"• {_n(r.views)} · {r.title[:60]} · {MODE_LABELS.get(r.mode, r.mode)}{pct} <{r.url}>")
    if report.top_hooks:
        lines += ["", "__Hooks that pulled the most views__"]
        for r in report.top_hooks[:5]:
            lines.append(f"• {_n(r.views)} · “{r.hook[:90]}” ({HOOK_STYLES.get(r.hook_style, r.hook_style)})")
    out, chunk = [], ""
    for line in lines:
        if len(chunk) + len(line) + 1 > 1900:
            out.append(chunk)
            chunk = ""
        chunk = f"{chunk}\n{line}" if chunk else line
    if chunk:
        out.append(chunk)
    return out


def main(argv: list[str] | None = None) -> int:
    from .config import ConfigError, get_settings

    parser = argparse.ArgumentParser(description="TrendClip insights: which videos perform best")
    parser.add_argument("--refresh", action="store_true", help="Read today's numbers from YouTube first")
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s", stream=sys.stderr)
    try:
        settings = get_settings()
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 2
    if args.refresh:
        try:
            result = collect(settings)
        except AnalyticsError as err:
            print(err, file=sys.stderr)
            return 3
        print(f"Tracked {result.videos} videos ({result.from_sidecars} made here); {result.snapshots} snapshots, "
              f"{result.with_retention} with retention", file=sys.stderr)
        for warning in result.warnings:
            print(f"! {warning}", file=sys.stderr)
    for message in format_report(build_report(settings, days=args.days)):
        print(message.replace("**", "").replace("__", ""))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
