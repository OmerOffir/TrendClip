"""Dark Outliers insights: how the channel's own videos perform, so the next ones are made like the winners.

The channel's learning loop, apart from the gameplay channel's (trendclip.analytics is bound to the active
channel's login and the Shorts sidecars). Every video in output/darkarchives/story_<id>.json that is on
YouTube (uploads.youtube.video_id) gets its numbers with the channel's own token (channels/darkarchives/
token_youtube.json): views, likes and comments from the Data API, and, with the analytics scope, the
average view duration / percentage and the views of its first day and first week from YouTube Analytics
(whose numbers run about two days behind). Next to the numbers sits what the video was made of: its
pillar (the bank), its hook and the hook's style (question / number-led / statement), the thumbnail
headline and its length, the video's length and the slot it went out in.

Everything is cached in output/darkarchives/insights.json (DA_INSIGHTS_HOURS, default 4, between reads):

    refresh()      the cache, read again from YouTube when it is older than that (or force=True)
    report()       the dict the dashboard card, /da insights and the endpoint show
    lessons()      a plain-text block (about 12 lines) appended to every script prompt once the channel has
                   MIN_VIDEOS measured videos (DA_SCRIPT_LEARNING, default on)
    best_lesson()  one line for the morning production run
    pillar_bias()  how far the mix may lean towards a pillar that over-performs (DA_PILLAR_BIAS caps it)

    python -m modules.da_insights [--refresh]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
from datetime import date as Day, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from db import repository
from modules import da_channel, da_plan, script_gen, video_gen
from modules.scene_images import setting
from trendclip import publish

logger = logging.getLogger(__name__)

CACHE_FILE = "insights.json"
DEFAULT_CACHE_HOURS = 4.0
MIN_VIDEOS = 5  # measured videos before the lessons are trusted (and appended to the prompts)
MIN_GROUP = 2  # videos a group (pillar, hook style...) needs before it is compared
ANALYTICS_LAG_DAYS = 2
ANALYTICS_METRICS = ["views", "averageViewDuration", "averageViewPercentage", "likes", "comments", "shares",
                     "subscribersGained"]
DAY_QUERIES_MAX = 40  # videos whose first days are asked for (one query each), newest first
DEFAULT_BIAS_CAP = 0.15  # DA_PILLAR_BIAS: the most a pillar's target share may move
HOOK_STYLES = {"question": "Question", "number": "Number-led", "statement": "Statement"}
DIMENSIONS = {"pillar": "Pillar", "hook_style": "Hook style", "thumbnail_bucket": "Thumbnail text",
              "length_bucket": "Length", "slot": "Upload slot"}
_NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
                 "thirteen", "fourteen", "fifteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty",
                 "ninety", "hundred", "hundreds", "thousand", "thousands", "million", "millions", "dozen", "dozens",
                 "half", "twice", "both"}


class InsightsError(RuntimeError):
    pass


# --------------------------------------------------------------------------- what a video is made of


class VideoInsight(BaseModel):
    story_id: int
    video_id: str
    title: str = ""
    url: str = ""
    privacy: str = ""
    published_at: datetime | None = None  # the upload time (UTC)
    pillar: str = ""
    hook: str = ""
    hook_style: str = ""
    thumbnail_text: str = ""
    thumbnail_bucket: str = ""
    length_seconds: float | None = None
    length_bucket: str = ""
    slot: str = ""
    age_days: float | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    subs_gained: int | None = None
    avg_view_seconds: float | None = None
    avg_view_pct: float | None = None
    views_day1: int | None = None  # views on the upload day and the day after (its first 24-48 h)
    views_day7: int | None = None  # views in its first 7 days (None while it is younger)

    @property
    def measured(self) -> bool:
        return self.views is not None


def hook_style(hook: str) -> str:
    """question (ends with ?), number (a number in the first three words: "Five bombers vanished",
    "For two decades...") or statement."""
    text = " ".join(hook.split())
    if not text:
        return ""
    if text.rstrip("\"'”’").endswith("?"):
        return "question"
    first = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in text.split()[:3]]
    if any(w and (w[0].isdigit() or w in _NUMBER_WORDS) for w in first):
        return "number"
    return "statement"


def thumbnail_bucket(text: str) -> str:
    n = len(text.split())
    if n == 0:
        return ""
    return "1-2 words" if n <= 2 else "3 words" if n == 3 else "4+ words"


def length_bucket(seconds: float | None) -> str:
    if seconds is None:
        return ""
    for limit, label in ((45, "<45 s"), (60, "45-60 s"), (75, "61-75 s")):
        if seconds <= limit:
            return label
    return ">75 s"


def slot_label(settings, published: datetime | None) -> str:
    """The plan slot (DA_SLOTS) the upload is nearest to (within 20 min), else its local hour."""
    if published is None:
        return ""
    local = published.astimezone(settings.tz)
    minutes = local.hour * 60 + local.minute
    for slot in da_plan.slots():
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


def _script(story_id: int, scripts_dir: Path | None) -> script_gen.Script | None:
    path = script_gen.script_path(story_id, scripts_dir)
    if not path.is_file():
        return None
    try:
        return script_gen.Script.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def facts(settings, out_dir: Path | None = None, db_path=None, scripts_dir: Path | None = None,
          at: datetime | None = None) -> list[VideoInsight]:
    """Every video on YouTube, with what it was made of (no numbers yet), newest upload first."""
    now = at or datetime.now(timezone.utc)
    out = []
    for info in da_plan.videos(out_dir):
        upload = (info.get("uploads") or {}).get("youtube") or {}
        video_id = upload.get("video_id")
        if not video_id:
            continue
        story_id = int(info["story_id"])
        try:
            story = repository.get_story(story_id, db_path) if (db_path or repository.DB_PATH.is_file()) else None
        except Exception:  # noqa: BLE001 - a bank without the table: the pillar is just unknown
            story = None
        script = _script(story_id, scripts_dir)
        beats = info.get("beats") or []
        hook = next((b.get("text", "") for b in beats if b.get("label") == "hook"), "") or (script.hook_text if script else "")
        thumb = video_gen.cover_text(script) if script else " ".join(hook.split(",")[0].split()[:4]).upper()
        published = _parse_ts(upload.get("uploaded_at")) or _parse_ts(info.get("created_at"))
        seconds = info.get("duration_seconds")
        out.append(VideoInsight(
            story_id=story_id, video_id=str(video_id), title=info.get("title", ""), url=upload.get("url", ""),
            privacy=upload.get("privacy", ""), published_at=published, pillar=(story or {}).get("pillar") or "",
            hook=hook, hook_style=hook_style(hook), thumbnail_text=thumb, thumbnail_bucket=thumbnail_bucket(thumb),
            length_seconds=float(seconds) if seconds is not None else None, length_bucket=length_bucket(seconds),
            slot=slot_label(settings, published),
            age_days=round((now - published).total_seconds() / 86400, 2) if published else None))
    return sorted(out, key=lambda v: v.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


# --------------------------------------------------------------------------- the numbers


def _youtube():
    try:
        return publish._youtube(da_channel.TOKEN)
    except publish.PublishError as err:
        raise InsightsError(f"{err} (the insights need the Dark Outliers YouTube login: tab → Videos → Connect YouTube)") from err


def _analytics():
    from googleapiclient.discovery import build

    return build("youtubeAnalytics", "v2", credentials=publish._credentials(da_channel.TOKEN), cache_discovery=False)


def _data_stats(youtube, video_ids: list[str]) -> dict[str, dict[str, int | None]]:
    out: dict[str, dict[str, int | None]] = {}
    for i in range(0, len(video_ids), 50):
        resp = youtube.videos().list(part="statistics", id=",".join(video_ids[i:i + 50])).execute()
        for item in resp.get("items", []):
            s = item.get("statistics", {})
            out[item["id"]] = {k: (int(s[key]) if s.get(key) is not None else None)
                               for k, key in (("views", "viewCount"), ("likes", "likeCount"), ("comments", "commentCount"))}
    return out


def _rows(resp: dict) -> list[dict[str, Any]]:
    names = [h["name"] for h in resp.get("columnHeaders", [])]
    return [dict(zip(names, row)) for row in resp.get("rows", []) or []]


def _lifetime(analytics, video_ids: list[str], start: Day, end: Day) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for i in range(0, len(video_ids), 200):
        chunk = video_ids[i:i + 200]
        resp = analytics.reports().query(
            ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
            metrics=",".join(ANALYTICS_METRICS), dimensions="video", filters="video==" + ",".join(chunk),
            maxResults=200, sort="-views").execute()
        for record in _rows(resp):
            video_id = str(record.pop("video", ""))
            out[video_id] = {k: float(v or 0) for k, v in record.items()}
    return out


def _first_days(analytics, video_id: str, start: Day, end: Day) -> dict[str, int]:
    """{day: views} for one video between start and end."""
    resp = analytics.reports().query(
        ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(), metrics="views",
        dimensions="day", filters=f"video=={video_id}", sort="day").execute()
    return {str(r.get("day")): int(r.get("views") or 0) for r in _rows(resp)}


def _analytics_error(err: Exception) -> str:
    text = str(getattr(err, "content", b"") or err)
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    if "accessNotConfigured" in text or "has not been used" in text:
        return ("the YouTube Analytics API is not enabled: Google Cloud console → APIs & Services → Library → "
                "YouTube Analytics API → Enable")
    if "insufficient" in text.lower() or "forbidden" in text.lower():
        return "the Dark Outliers login lacks the analytics permission: Videos → Disconnect → Connect YouTube again"
    return text[:200]


def cache_path(out_dir: Path | None = None) -> Path:
    return (out_dir or video_gen.OUTPUT_DIR) / CACHE_FILE


def cache_hours() -> float:
    try:
        return max(0.0, float(setting("DA_INSIGHTS_HOURS", str(DEFAULT_CACHE_HOURS))))
    except ValueError:
        return DEFAULT_CACHE_HOURS


def load(out_dir: Path | None = None) -> dict[str, Any] | None:
    """The cached insights ({"fetched_at", "videos": [...], "warnings"}), None when there are none."""
    try:
        data = json.loads(cache_path(out_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("videos"), list) else None


def _save(data: dict[str, Any], out_dir: Path | None = None) -> None:
    path = cache_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def fetch(settings, out_dir: Path | None = None, db_path=None, scripts_dir: Path | None = None, youtube=None,
          analytics=None, at: datetime | None = None, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read every uploaded video's numbers from YouTube and write the cache. `youtube` / `analytics`: clients
    for the channel's token (tests pass fakes). First-day numbers already final in `previous` are kept."""
    now = at or datetime.now(timezone.utc)
    today = now.astimezone(settings.tz).date()
    videos = facts(settings, out_dir, db_path, scripts_dir, now)
    data: dict[str, Any] = {"fetched_at": now.isoformat(), "videos": [], "warnings": []}
    if not videos:
        data["warnings"].append("No Dark Outliers video is on YouTube yet")
        _save(data, out_dir)
        return data
    youtube = youtube or _youtube()
    ids = [v.video_id for v in videos]
    stats = _data_stats(youtube, ids)
    for v in videos:
        if v.video_id in stats:
            v.views, v.likes, v.comments = stats[v.video_id]["views"], stats[v.video_id]["likes"], stats[v.video_id]["comments"]
    if analytics is None and publish.has_scope(publish.ANALYTICS_SCOPE, da_channel.TOKEN):
        try:
            analytics = _analytics()
        except Exception as err:  # noqa: BLE001 - discovery / credentials
            data["warnings"].append(f"YouTube Analytics unavailable: {err}")
    if analytics is None:
        if not publish.has_scope(publish.ANALYTICS_SCOPE, da_channel.TOKEN):
            data["warnings"].append("No retention numbers: the Dark Outliers login lacks the analytics permission "
                                    "(Videos → Disconnect → Connect YouTube again)")
    else:
        first = min((v.published_at for v in videos if v.published_at), default=None)
        start = (first.astimezone(settings.tz).date() if first else today - timedelta(days=365)) - timedelta(days=1)
        try:
            rows = _lifetime(analytics, ids, start, today)
        except Exception as err:  # noqa: BLE001 - the Data API numbers are kept
            logger.warning("YouTube Analytics query failed: %s", err)
            data["warnings"].append(f"YouTube Analytics: {_analytics_error(err)}")
            rows = None
        if rows is not None:
            for v in videos:
                m = rows.get(v.video_id)
                if m is None:
                    continue
                v.avg_view_seconds, v.avg_view_pct = m.get("averageViewDuration"), m.get("averageViewPercentage")
                v.shares, v.subs_gained = int(m.get("shares", 0)), int(m.get("subscribersGained", 0))
                if v.views is None:
                    v.views = int(m.get("views", 0))
            known = {str(x.get("video_id")): x for x in (previous or {}).get("videos", [])}
            for v in videos[:DAY_QUERIES_MAX]:
                if not v.published_at:
                    continue
                old = known.get(v.video_id) or {}
                if old.get("views_day7") is not None and old.get("views_day1") is not None:
                    v.views_day1, v.views_day7 = old["views_day1"], old["views_day7"]
                    continue
                day0 = v.published_at.astimezone(settings.tz).date()
                try:
                    per_day = _first_days(analytics, v.video_id, day0, day0 + timedelta(days=6))
                except Exception as err:  # noqa: BLE001
                    data["warnings"].append(f"YouTube Analytics (first days of {v.title[:30]}): {_analytics_error(err)}")
                    break
                v.views_day1 = sum(per_day.get((day0 + timedelta(days=n)).isoformat(), 0) for n in range(2))
                if (today - day0).days >= 7 + ANALYTICS_LAG_DAYS:
                    v.views_day7 = sum(per_day.values())
    data["videos"] = [v.model_dump(mode="json") for v in videos]
    _save(data, out_dir)
    return data


def refresh(settings=None, out_dir: Path | None = None, max_age_hours: float | None = None, force: bool = False,
            at: datetime | None = None, **kw) -> dict[str, Any]:
    """The cache, read again from YouTube when it is older than `max_age_hours` (DA_INSIGHTS_HOURS) or `force`.
    A read that fails leaves the old cache (with the error in "warnings"); without any cache it raises."""
    cached = load(out_dir)
    now = at or datetime.now(timezone.utc)
    if cached and not force:
        age = max_age_hours if max_age_hours is not None else cache_hours()
        fetched = _parse_ts(cached.get("fetched_at"))
        if fetched and now - fetched < timedelta(hours=age):
            return cached
    if settings is None:
        from trendclip.config import get_settings

        settings = get_settings()
    try:
        return fetch(settings, out_dir, at=now, previous=cached, **kw)
    except Exception as err:  # noqa: BLE001
        if cached is None:
            raise InsightsError(str(err)) from err
        logger.warning("Dark Outliers insights not refreshed: %s", err)
        return {**cached, "warnings": [*cached.get("warnings", []), f"Not refreshed: {str(err)[:200]}"]}


# --------------------------------------------------------------------------- the report


def _videos(data: dict[str, Any] | None) -> list[VideoInsight]:
    out = []
    for raw in (data or {}).get("videos", []):
        try:
            out.append(VideoInsight.model_validate(raw))
        except ValueError:
            continue
    return out


def _median(values: list[float | int]) -> float:
    return float(statistics.median(values)) if values else 0.0


def _label(dimension: str, key: str) -> str:
    if dimension == "pillar":
        return repository.PILLAR_LABELS.get(key, key or "no pillar").split(" &")[0]
    if dimension == "hook_style":
        return HOOK_STYLES.get(key, key or "?") + " hooks"
    if dimension == "thumbnail_bucket":
        return f"{key} headlines"
    if dimension == "length_bucket":
        return f"{key} videos"
    if dimension == "slot":
        return f"the {key} slot"
    return key


def _groups(videos: list[VideoInsight], dimension: str) -> list[dict[str, Any]]:
    """The groups of one dimension ranked by median views; retention and first-day views next to it."""
    by_key: dict[str, list[VideoInsight]] = {}
    for v in videos:
        key = getattr(v, dimension) or ""
        if key:
            by_key.setdefault(key, []).append(v)
    channel = _median([v.views or 0 for v in videos])
    out = []
    for key, rows in by_key.items():
        pct = [v.avg_view_pct for v in rows if v.avg_view_pct is not None]
        day1 = [v.views_day1 for v in rows if v.views_day1 is not None]
        median = _median([v.views or 0 for v in rows])
        out.append({"key": key, "label": _label(dimension, key), "n": len(rows), "views_median": round(median),
                    "views_total": sum(v.views or 0 for v in rows),
                    "avg_view_pct": round(statistics.fmean(pct), 1) if pct else None,
                    "views_day1_median": round(_median(day1)) if day1 else None,
                    "vs_channel": round(median / channel, 2) if channel else None,
                    "titles": [v.title for v in rows][:5]})
    return sorted(out, key=lambda g: (g["views_median"], g["n"]), reverse=True)


def _video_row(v: VideoInsight) -> dict[str, Any]:
    row = v.model_dump(mode="json")
    row["measured"] = v.measured
    return row


def report(data: dict[str, Any] | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    """What the dashboard card, /da insights and GET /api/darkarchives/insights show."""
    data = data if data is not None else (load(out_dir) or {})
    videos = _videos(data)
    measured = [v for v in videos if v.measured]
    ranked = sorted(measured, key=lambda v: (v.views or 0, v.avg_view_pct or 0), reverse=True)
    out: dict[str, Any] = {
        "fetched_at": data.get("fetched_at"), "warnings": list(data.get("warnings", [])),
        "videos": len(videos), "measured": len(measured),
        "with_retention": sum(v.avg_view_pct is not None for v in measured),
        "min_videos": MIN_VIDEOS, "learning": len(measured) >= MIN_VIDEOS,
        "totals": {"views": sum(v.views or 0 for v in measured), "likes": sum(v.likes or 0 for v in measured),
                   "comments": sum(v.comments or 0 for v in measured)},
        "views_median": round(_median([v.views or 0 for v in measured])) if measured else 0,
        "avg_view_pct": (round(statistics.fmean([v.avg_view_pct for v in measured if v.avg_view_pct is not None]), 1)
                         if any(v.avg_view_pct is not None for v in measured) else None),
        "top": [_video_row(v) for v in ranked[:5]],
        "worst": [_video_row(v) for v in reversed(ranked[-3:])] if len(ranked) >= 2 else [],
        "retention_best": [_video_row(v) for v in sorted((v for v in measured if v.avg_view_pct is not None),
                                                         key=lambda v: v.avg_view_pct or 0, reverse=True)[:3]],
        "all": [_video_row(v) for v in videos],
        "dimensions": {key: {"label": label, "groups": _groups(measured, key)} for key, label in DIMENSIONS.items()},
        "pillar_bias": pillar_bias(data) if measured else {},
    }
    out["lessons"] = lessons(data)
    out["best_lesson"] = best_lesson(data)
    return out


# --------------------------------------------------------------------------- lessons


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


def _pct(value: float | None) -> str:
    return "" if value is None else f"{value:.0f}% watched"


def _group_line(name: str, groups: list[dict[str, Any]]) -> str | None:
    """"Pillar: Creepy history gets 1.2k views median over 3 videos (78% watched); Unresolved mysteries only 400"."""
    solid = [g for g in groups if g["n"] >= MIN_GROUP]
    if len(solid) < 2:
        return None
    best, worst = solid[0], solid[-1]
    if best["views_median"] <= 0 or best["key"] == worst["key"]:
        return None
    verb = "gets" if name in ("Pillar", "Upload slot") else "get"  # "Creepy history gets", "Number-led hooks get"
    line = f"{name}: {best['label']} {verb} {_n(best['views_median'])} views median over {best['n']} videos"
    if best["avg_view_pct"] is not None:
        line += f" ({_pct(best['avg_view_pct'])})"
    line += f"; {worst['label']} only {_n(worst['views_median'])}"
    held = max(solid, key=lambda g: g["avg_view_pct"] or 0)
    if held["key"] != best["key"] and held["avg_view_pct"] is not None and (best["avg_view_pct"] or 0) < held["avg_view_pct"]:
        line += f". Best retention: {held['label']} ({_pct(held['avg_view_pct'])})"
    return line


def lessons(data: dict[str, Any] | None = None, out_dir: Path | None = None, min_videos: int = MIN_VIDEOS) -> str:
    """The brief for the script prompt: which pillar, hook style, headline length, video length and slot got
    the most views and held viewers, the best and the weakest titles. "" with fewer than `min_videos`
    measured videos (there is nothing to learn from two)."""
    data = data if data is not None else (load(out_dir) or {})
    measured = [v for v in _videos(data) if v.measured]
    if len(measured) < min_videos:
        return ""
    with_ret = [v for v in measured if v.avg_view_pct is not None]
    lines = [f"WHAT WORKED ON DARK OUTLIERS (YouTube, {len(measured)} videos measured"
             + (f", {len(with_ret)} with retention" if with_ret else "") + "):"]
    for key, name in (("pillar", "Pillar"), ("hook_style", "Hooks"), ("thumbnail_bucket", "Thumbnail text"),
                      ("length_bucket", "Length"), ("slot", "Upload slot")):
        line = _group_line(name, _groups(measured, key))
        if line:
            lines.append(f"- {line}.")
    ranked = sorted(measured, key=lambda v: (v.views or 0, v.avg_view_pct or 0), reverse=True)

    def title_of(v: VideoInsight) -> str:
        extra = ", ".join(filter(None, [f"{_n(v.views)} views", _pct(v.avg_view_pct)]))
        return f"“{v.title}” ({extra})"

    lines.append("- Best titles: " + "; ".join(title_of(v) for v in ranked[:3]))
    weak = [v for v in reversed(ranked[-2:]) if v not in ranked[:3]]
    if weak:
        lines.append("- Weakest titles: " + "; ".join(title_of(v) for v in weak))
    if with_ret:
        best_hook = max(with_ret, key=lambda v: (v.avg_view_pct or 0, v.views or 0))
        if best_hook.hook:
            lines.append(f"- The hook that held viewers best: “{best_hook.hook}” ({_pct(best_hook.avg_view_pct)})")
    recent = [v.title for v in measured if v.title][:8]
    if recent:
        lines.append("- Already published (tell something new, not a variation): " + "; ".join(f"“{t}”" for t in recent))
    lines.append("Use these as evidence of what this audience responds to, not as text to copy: open with the "
                 "same kind of stakes as the winners, keep the pillar's tone, avoid what lost viewers.")
    return "\n".join(lines[:12])


def best_lesson(data: dict[str, Any] | None = None, out_dir: Path | None = None) -> str:
    """One line for the morning run: the strongest comparison, or how far the channel is from learning."""
    data = data if data is not None else (load(out_dir) or {})
    measured = [v for v in _videos(data) if v.measured]
    if len(measured) >= MIN_VIDEOS:
        for key, name in (("pillar", "Pillar"), ("hook_style", "Hooks"), ("length_bucket", "Length"),
                          ("thumbnail_bucket", "Thumbnail text"), ("slot", "Upload slot")):
            line = _group_line(name, _groups(measured, key))
            if line:
                return line
    if not measured:
        return ""
    top = max(measured, key=lambda v: (v.views or 0))
    return (f"{len(measured)} video{'s' if len(measured) != 1 else ''} measured (lessons start at {MIN_VIDEOS}); "
            f"best so far: “{top.title}” with {_n(top.views)} views" + (f", {_pct(top.avg_view_pct)}" if top.avg_view_pct is not None else ""))


def learning_on() -> bool:
    return setting("DA_SCRIPT_LEARNING", "1").strip().lower() not in ("0", "false", "off", "none")


def learned(out_dir: Path | None = None) -> str:
    """The lessons as a prompt block starting with a newline; "" when DA_SCRIPT_LEARNING is off, there are too
    few videos, or the numbers cannot be read. Never fails a script."""
    if not learning_on():
        return ""
    try:
        data = load(out_dir)
        if data is None and not any((i.get("uploads") or {}).get("youtube") for i in da_plan.videos(out_dir)):
            return ""  # nothing on YouTube yet: no login needed, nothing to learn
        text = lessons(refresh(out_dir=out_dir), out_dir)
    except Exception as err:  # noqa: BLE001
        logger.warning("Could not build the Dark Outliers lessons (%s); writing without them", err)
        return ""
    return f"\n\n{text}" if text else ""


# --------------------------------------------------------------------------- the mix


def bias_cap() -> float:
    try:
        return max(0.0, min(0.5, float(setting("DA_PILLAR_BIAS", str(DEFAULT_BIAS_CAP)))))
    except ValueError:
        return DEFAULT_BIAS_CAP


def pillar_bias(data: dict[str, Any] | None = None, out_dir: Path | None = None, cap: float | None = None) -> dict[str, float]:
    """{pillar: shift of its target share}: a pillar whose median views are above the channel's gets more,
    one below gets less, each capped at ±cap (DA_PILLAR_BIAS, 0 turns it off). {} until MIN_VIDEOS are
    measured; a pillar needs MIN_GROUP videos to move."""
    cap = bias_cap() if cap is None else cap
    data = data if data is not None else (load(out_dir) or {})
    measured = [v for v in _videos(data) if v.measured]
    if cap <= 0 or len(measured) < MIN_VIDEOS:
        return {}
    channel = _median([v.views or 0 for v in measured])
    if channel <= 0:
        return {}
    out = {}
    for g in _groups(measured, "pillar"):
        if g["key"] in repository.PILLARS and g["n"] >= MIN_GROUP:
            shift = (g["views_median"] / channel - 1) * 0.2  # 1.5× the channel median -> +0.10
            out[g["key"]] = round(max(-cap, min(cap, shift)), 3)
    return {k: v for k, v in out.items() if v}


def biased_targets(bias: dict[str, float]) -> dict[str, float]:
    """repository.PILLARS with the bias added, every pillar kept at 5% at least, summing to 1."""
    raw = {p: max(0.05, t + bias.get(p, 0.0)) for p, t in repository.PILLARS.items()}
    total = sum(raw.values())
    return {p: round(v / total, 3) for p, v in raw.items()}


# --------------------------------------------------------------------------- text for Discord / the CLI


def format_report(rep: dict[str, Any], top: int = 5) -> list[str]:
    if not rep.get("videos"):
        return ["📈 No Dark Outliers video is on YouTube yet; the insights start with the first upload."]
    lines = [f"📈 **Dark Outliers insights** · {rep['measured']} of {rep['videos']} videos measured"
             + (f" · {rep['with_retention']} with retention" if rep.get("with_retention") else "")
             + f" · {_n(rep['totals']['views'])} views in all"]
    for v in rep.get("top", [])[:top]:
        parts = [f"{_n(v.get('views'))} views"]
        if v.get("avg_view_pct") is not None:
            parts.append(_pct(v["avg_view_pct"]))
        if v.get("views_day1"):
            parts.append(f"{_n(v['views_day1'])} on day 1")
        if v.get("pillar"):
            parts.append(v["pillar"])
        lines.append(f"• **{v.get('title', '')}** · " + " · ".join(parts))
    if rep.get("lessons"):
        lines.append("")
        lines.extend(rep["lessons"].splitlines()[1:-1])
    elif rep.get("best_lesson"):
        lines.append(rep["best_lesson"])
    for w in rep.get("warnings", [])[:2]:
        lines.append(f"⚠️ {w}")
    return lines


def main(argv: list[str] | None = None) -> int:
    from trendclip.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="read YouTube now, whatever the cache's age")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        data = refresh(get_settings(), force=args.refresh)
    except InsightsError as err:
        print(f"error: {err}")
        return 1
    rep = report(data)
    print("\n".join(format_report(rep)).replace("**", ""))
    if rep["pillar_bias"]:
        print("mix bias:", rep["pillar_bias"], "->", biased_targets(rep["pillar_bias"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
