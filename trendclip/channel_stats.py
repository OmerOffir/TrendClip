"""Your own channels' numbers for the morning report: followers, and views / likes / comments of the videos
posted in the last 24 hours, on YouTube, TikTok and Instagram.

- YouTube: the Data API (the upload login if connected, so private / scheduled videos count too; else the key).
- TikTok: the public profile page (followers, total likes) and its public video list (per-video stats).
- Instagram: the Instagram API with an access token (INSTAGRAM_ACCESS_TOKEN). Without one, the public
  profile data is tried, which Instagram often refuses for logged-out requests.

Each run saves a snapshot, so the report shows how followers and total views changed since yesterday.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from .config import Settings, channel_file

logger = logging.getLogger(__name__)

Platform = Literal["youtube", "tiktok", "instagram"]
WINDOW_HOURS = 24
SNAPSHOTS_KEPT = 60
TIKTOK_PAGES = 10
INSTAGRAM_GRAPH = "https://graph.instagram.com/v23.0"
INSTAGRAM_WEB_APP_ID = "936619743392459"  # the public instagram.com web app
INSTAGRAM_TOKEN_FILE = channel_file("token_instagram.json")  # refreshed token; gitignored (token*.json)
INSTAGRAM_REFRESH_DAYS = 7  # long-lived tokens last 60 days; refresh well before that
LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}
FOLLOWER_WORD = {"youtube": "subscribers", "tiktok": "followers", "instagram": "followers"}
ICONS = {"youtube": "▶️", "tiktok": "🎵", "instagram": "📸"}


class StatsError(RuntimeError):
    pass


class VideoStats(BaseModel):
    id: str
    title: str = ""
    url: str = ""
    published: datetime
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None


class PlatformStats(BaseModel):
    platform: Platform
    handle: str
    url: str = ""
    followers: int | None = None
    total_views: int | None = None  # all videos, for the change since yesterday
    total_likes: int | None = None
    video_count: int | None = None
    videos: list[VideoStats] = Field(default_factory=list)  # posted in the last 24 hours, newest first
    followers_change: int | None = None
    views_change: int | None = None
    since: datetime | None = None  # the snapshot the changes are measured against
    note: str = ""  # e.g. "public data only: views are missing"
    error: str = ""


class Report(BaseModel):
    at: datetime
    hours: int = WINDOW_HOURS
    platforms: list[PlatformStats]


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _browser():
    """A session that looks like Chrome; TikTok and Instagram send empty answers to plain clients."""
    try:
        from curl_cffi import requests as curl
    except ImportError as err:
        raise StatsError("pip install curl_cffi (needed to read TikTok / Instagram)") from err
    return curl.Session(impersonate="chrome", timeout=20)


# --------------------------------------------------------------------------- YouTube


def _youtube_api(settings: Settings):
    from googleapiclient.discovery import build

    from . import publish

    if publish.TOKEN_FILE.is_file():
        try:
            return build("youtube", "v3", credentials=publish._credentials(), cache_discovery=False), True
        except Exception as err:  # noqa: BLE001 - expired login: public numbers still work
            logger.warning("YouTube login unusable for stats (%s); using the API key", err)
    return build("youtube", "v3", developerKey=settings.youtube_api_key.get_secret_value(),
                 cache_discovery=False), False


def youtube_stats(settings: Settings, since: datetime, api=None) -> PlatformStats:
    ref = settings.stats_youtube
    login = False
    if api is None:
        api, login = _youtube_api(settings)
    params = {"id": ref} if ref.startswith("UC") else {"forHandle": ref}
    items = api.channels().list(part="snippet,statistics,contentDetails", **params).execute().get("items", [])
    if not items:
        raise StatsError(f"YouTube channel {ref} not found")
    channel = items[0]
    stats = channel.get("statistics", {})
    out = PlatformStats(
        platform="youtube", handle=ref, url=f"https://www.youtube.com/{ref if ref.startswith('@') else 'channel/' + ref}",
        followers=None if stats.get("hiddenSubscriberCount") else _int(stats.get("subscriberCount")),
        total_views=_int(stats.get("viewCount")), video_count=_int(stats.get("videoCount")),
    )
    uploads = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: list[str] = []
    page: str | None = None
    while True:  # uploads are newest first; stop at the first one older than the window
        resp = api.playlistItems().list(part="snippet,contentDetails", playlistId=uploads, maxResults=50,
                                        **({"pageToken": page} if page else {})).execute()
        older = False
        for item in resp.get("items", []):
            published = item["contentDetails"].get("videoPublishedAt") or item["snippet"].get("publishedAt")
            if published and _ts(published) >= since:
                ids.append(item["contentDetails"]["videoId"])
            else:
                older = True
        page = resp.get("nextPageToken")
        if older or not page:
            break
    for start in range(0, len(ids), 50):
        resp = api.videos().list(part="snippet,statistics,status", id=",".join(ids[start:start + 50])).execute()
        for item in resp.get("items", []):
            s = item.get("statistics", {})
            out.videos.append(VideoStats(
                id=item["id"], title=item["snippet"].get("title", ""), url=f"https://youtu.be/{item['id']}",
                published=_ts(item["snippet"]["publishedAt"]), views=_int(s.get("viewCount")),
                likes=_int(s.get("likeCount")), comments=_int(s.get("commentCount")),
            ))
    if not login:
        out.note = "public videos only (connect YouTube in the dashboard to include private / scheduled ones)"
    return out


# --------------------------------------------------------------------------- TikTok


def _tiktok_profile(session, handle: str) -> dict:
    page = session.get(f"https://www.tiktok.com/@{handle}")
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', page.text, re.S)
    if page.status_code != 200 or not m:
        raise StatsError(f"TikTok profile page didn't load (HTTP {page.status_code})")
    detail = json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
    if detail.get("statusCode") or "userInfo" not in detail:
        raise StatsError(f"TikTok account @{handle} not found")
    return detail["userInfo"]


def tiktok_stats(settings: Settings, since: datetime, session=None) -> PlatformStats:
    handle = settings.stats_tiktok
    session = session or _browser()
    info = _tiktok_profile(session, handle)
    s = info.get("stats", {})
    out = PlatformStats(platform="tiktok", handle=handle, url=f"https://www.tiktok.com/@{handle}",
                        followers=_int(s.get("followerCount")), total_likes=_int(s.get("heartCount")),
                        video_count=_int(s.get("videoCount")))
    total_views, cursor = 0, int(time.time() * 1000)
    for _ in range(TIKTOK_PAGES):
        resp = session.get("https://www.tiktok.com/api/creator/item_list/", params={
            "aid": "1988", "secUid": info["user"]["secUid"], "count": "15", "cursor": str(cursor), "type": "1"})
        try:
            data = resp.json()
        except ValueError:
            data = {}
        items = data.get("itemList") or []
        if resp.status_code != 200 or (not items and data.get("statusCode")):
            out.note = f"TikTok didn't list the videos (HTTP {resp.status_code}); followers only"
            return out
        for item in items:
            st = item.get("stats", {})
            total_views += _int(st.get("playCount")) or 0
            published = datetime.fromtimestamp(int(item["createTime"]), timezone.utc)
            if published >= since:
                out.videos.append(VideoStats(
                    id=item["id"], title=(item.get("desc") or "").strip(), published=published,
                    url=f"https://www.tiktok.com/@{handle}/video/{item['id']}",
                    views=_int(st.get("playCount")), likes=_int(st.get("diggCount")),
                    comments=_int(st.get("commentCount")), shares=_int(st.get("shareCount")),
                ))
        if not data.get("hasMorePrevious") and not data.get("hasMore") or not items:
            break
        cursor = min(int(i["createTime"]) for i in items) * 1000 - 1
    out.total_views = total_views
    out.videos.sort(key=lambda v: v.published, reverse=True)
    return out


# --------------------------------------------------------------------------- Instagram


def instagram_token(settings: Settings, session=None) -> str | None:
    """The refreshed token from token_instagram.json, else INSTAGRAM_ACCESS_TOKEN. Refreshed weekly."""
    env = settings.instagram_access_token.get_secret_value() if settings.instagram_access_token else ""
    saved: dict = {}
    if INSTAGRAM_TOKEN_FILE.is_file():
        try:
            saved = json.loads(INSTAGRAM_TOKEN_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
    if env and saved.get("from_env") != env[-12:]:
        saved = {}  # a new token in channel.env replaces the refreshed one
    token = saved.get("access_token") or env
    if not token:
        return None
    refreshed = _ts(saved["refreshed"]) if saved.get("refreshed") else None
    if refreshed is None or datetime.now(timezone.utc) - refreshed > timedelta(days=INSTAGRAM_REFRESH_DAYS):
        try:
            resp = (session or _browser()).get(f"{INSTAGRAM_GRAPH.rsplit('/', 1)[0]}/refresh_access_token",
                                               params={"grant_type": "ig_refresh_token", "access_token": token})
            if resp.status_code == 200 and resp.json().get("access_token"):
                token = resp.json()["access_token"]
                INSTAGRAM_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
                INSTAGRAM_TOKEN_FILE.write_text(json.dumps({
                    "access_token": token, "refreshed": datetime.now(timezone.utc).isoformat(),
                    "from_env": env[-12:]}), encoding="utf-8")
        except Exception as err:  # noqa: BLE001 - the current token may still work
            logger.warning("Instagram token refresh failed: %s", err)
    return token


def _graph(session, path: str, token: str, **params: str) -> dict:
    resp = session.get(f"{INSTAGRAM_GRAPH}/{path}", params={**params, "access_token": token})
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code != 200:
        message = (data.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
        raise StatsError(f"Instagram API: {message}")
    return data


def instagram_stats(settings: Settings, since: datetime, session=None) -> PlatformStats:
    handle = settings.stats_instagram
    session = session or _browser()
    token = instagram_token(settings, session)
    if token:
        return _instagram_api(session, token, handle, since)
    return _instagram_public(session, handle, since)


def _instagram_api(session, token: str, handle: str, since: datetime) -> PlatformStats:
    me = _graph(session, "me", token, fields="username,followers_count,media_count")
    handle = me.get("username") or handle
    out = PlatformStats(platform="instagram", handle=handle, url=f"https://www.instagram.com/{handle}/",
                        followers=_int(me.get("followers_count")), video_count=_int(me.get("media_count")))
    media = _graph(session, "me/media", token, limit="50",
                   fields="id,caption,media_type,media_product_type,timestamp,like_count,comments_count,permalink")
    for item in media.get("data", []):
        published = _ts(item["timestamp"].replace("+0000", "+00:00"))
        if published < since:
            break
        views = None
        try:
            insights = _graph(session, f"{item['id']}/insights", token, metric="views")
            views = _int(insights["data"][0]["values"][0]["value"])
        except (StatsError, KeyError, IndexError) as err:
            logger.info("No Instagram views for %s: %s", item["id"], err)
        out.videos.append(VideoStats(
            id=item["id"], title=(item.get("caption") or "").strip(), url=item.get("permalink", ""),
            published=published, views=views, likes=_int(item.get("like_count")),
            comments=_int(item.get("comments_count")),
        ))
    return out


def _instagram_public(session, handle: str, since: datetime) -> PlatformStats:
    resp = session.get("https://www.instagram.com/api/v1/users/web_profile_info/", params={"username": handle},
                       headers={"x-ig-app-id": INSTAGRAM_WEB_APP_ID, "Referer": f"https://www.instagram.com/{handle}/"})
    try:
        user = resp.json()["data"]["user"]
    except (ValueError, KeyError, TypeError):
        raise StatsError("Instagram refuses logged-out requests right now. Add INSTAGRAM_ACCESS_TOKEN to "
                         "channels/<channel>/channel.env for reliable numbers (see README → Channel report)") from None
    timeline = user.get("edge_owner_to_timeline_media", {})
    out = PlatformStats(platform="instagram", handle=handle, url=f"https://www.instagram.com/{handle}/",
                        followers=_int(user.get("edge_followed_by", {}).get("count")),
                        video_count=_int(timeline.get("count")),
                        note="public data (latest 12 posts); add INSTAGRAM_ACCESS_TOKEN for full stats")
    for edge in timeline.get("edges", []):
        node = edge.get("node", {})
        published = datetime.fromtimestamp(int(node.get("taken_at_timestamp", 0)), timezone.utc)
        if published < since:
            continue
        caption = ((node.get("edge_media_to_caption", {}).get("edges") or [{}])[0].get("node", {}).get("text") or "")
        likes = node.get("edge_liked_by") or node.get("edge_media_preview_like") or {}
        out.videos.append(VideoStats(
            id=node.get("shortcode", ""), title=caption.strip(), published=published,
            url=f"https://www.instagram.com/reel/{node.get('shortcode', '')}/",
            views=_int(node.get("video_view_count")), likes=_int(likes.get("count")),
            comments=_int(node.get("edge_media_to_comment", {}).get("count")),
        ))
    out.videos.sort(key=lambda v: v.published, reverse=True)
    return out


# --------------------------------------------------------------------------- report


FETCHERS: dict[Platform, Callable[[Settings, datetime], PlatformStats]] = {
    "youtube": youtube_stats, "tiktok": tiktok_stats, "instagram": instagram_stats,
}
HANDLE_FIELD = {"youtube": "stats_youtube", "tiktok": "stats_tiktok", "instagram": "stats_instagram"}


def _snapshots_file(settings: Settings) -> Path:
    return settings.assets_dir / "cache" / "channel_stats.json"


def _load_snapshots(settings: Settings) -> dict[str, list[dict]]:
    try:
        return json.loads(_snapshots_file(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _apply_changes(stats: PlatformStats, history: list[dict], at: datetime) -> None:
    """Change since the latest snapshot that is at least 20 h old (yesterday's report)."""
    old = [h for h in history if h.get("handle") == stats.handle and at - _ts(h["at"]) >= timedelta(hours=20)]
    if not old:
        return
    prev = old[-1]
    stats.since = _ts(prev["at"])
    if stats.followers is not None and prev.get("followers") is not None:
        stats.followers_change = stats.followers - prev["followers"]
    if stats.total_views is not None and prev.get("total_views") is not None:
        stats.views_change = stats.total_views - prev["total_views"]


def build_report(settings: Settings, hours: int = WINDOW_HOURS, at: datetime | None = None,
                 save: bool = True) -> Report:
    at = at or datetime.now(timezone.utc)
    since = at - timedelta(hours=hours)
    history = _load_snapshots(settings)
    platforms: list[PlatformStats] = []
    for platform, fetch in FETCHERS.items():
        handle = getattr(settings, HANDLE_FIELD[platform])
        if not handle:
            continue
        try:
            stats = fetch(settings, since)
        except Exception as err:  # noqa: BLE001 - one platform failing must not hide the others
            logger.warning("%s stats failed: %s", LABELS[platform], err)
            stats = PlatformStats(platform=platform, handle=handle, error=str(err) or type(err).__name__)
        if not stats.error:
            _apply_changes(stats, history.get(platform, []), at)
            history.setdefault(platform, []).append({
                "at": at.isoformat(), "handle": stats.handle, "followers": stats.followers,
                "total_views": stats.total_views, "total_likes": stats.total_likes})
            history[platform] = history[platform][-SNAPSHOTS_KEPT:]
        platforms.append(stats)
    if save:
        path = _snapshots_file(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(history, indent=1), encoding="utf-8")
    return Report(at=at, hours=hours, platforms=platforms)


def _n(value: int | None) -> str:
    return "–" if value is None else f"{value:,}"


def _delta(value: int | None) -> str:
    return "" if value is None else f" ({value:+,})"


def _title(text: str, limit: int = 60) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"#\w+", "", text)).strip() or "(no caption)"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_report(report: Report, tz=None) -> list[str]:
    """Discord-ready text, one message per platform (each well under 2000 characters)."""
    local = report.at.astimezone(tz) if tz else report.at
    head = f"📊 **Channel report**: videos from the last {report.hours} h · {local:%a %b %-d, %H:%M}"
    messages = [head]
    for p in report.platforms:
        lines = [f"{ICONS[p.platform]} **{LABELS[p.platform]}** · [{p.handle}](<{p.url}>)" if p.url
                 else f"{ICONS[p.platform]} **{LABELS[p.platform]}** · {p.handle}"]
        if p.error:
            lines.append(f"⚠️ Couldn't read it: {p.error}")
            messages.append("\n".join(lines))
            continue
        word = FOLLOWER_WORD[p.platform]
        totals = [f"**{_n(p.followers)}** {word[:-1] if p.followers == 1 else word}{_delta(p.followers_change)}"]
        if p.total_views is not None:
            totals.append(f"{_n(p.total_views)} views overall{_delta(p.views_change)}")
        if p.total_likes is not None:
            totals.append(f"{_n(p.total_likes)} likes overall")
        lines.append(" · ".join(totals))
        if p.since is None:
            lines.append("(changes since yesterday show from tomorrow's report)")
        if p.videos:
            v_sum = sum(v.views or 0 for v in p.videos)
            l_sum = sum(v.likes or 0 for v in p.videos)
            c_sum = sum(v.comments or 0 for v in p.videos)
            lines.append(f"**{len(p.videos)} new video(s)**: {v_sum:,} views · {l_sum:,} likes · {c_sum:,} comments")
            best = max(p.videos, key=lambda v: v.views or 0)
            for v in p.videos[:8]:
                when = v.published.astimezone(tz) if tz else v.published
                day = "today" if when.date() == local.date() else "yesterday" if (
                    local.date() - when.date()).days == 1 else f"{when:%b %-d}"
                star = " ⭐" if len(p.videos) > 1 and v is best and (v.views or 0) > 0 else ""
                extra = f" · {_n(v.shares)} shares" if v.shares else ""
                lines.append(f"• [{_title(v.title)}](<{v.url}>) · {day} {when:%H:%M}{star}\n"
                             f"  👁 {_n(v.views)} · ❤️ {_n(v.likes)} · 💬 {_n(v.comments)}{extra}")
            if len(p.videos) > 8:
                lines.append(f"…and {len(p.videos) - 8} more")
        else:
            lines.append(f"No new videos in the last {report.hours} h.")
        if p.note:
            lines.append(f"_{p.note}_")
        messages.append("\n".join(lines))
    return messages
