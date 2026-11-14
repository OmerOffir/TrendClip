"""Debugging from the phone, through the Discord bot.

  /check   one upload read live from YouTube (privacy, processing, views, the comment) and its thumbnail: the picture
           YouTube serves on the Shorts shelf next to ours, compared pixel by pixel, with a "Set it again" button.
  alerts   after every upload the thumbnail's second setting (publish.keep_thumbnail) is verified against what
           YouTube serves; a miss is posted in the channel's Discord channel with the button, so nothing has to be asked.
  /log     the bot's log (output/discord_bot.log): the last lines, the errors, or one channel's lines.

The three channels differ in where their uploads live; `uploads`, `client`, `thumbnail` and `note` hide that.
"""

from __future__ import annotations

import io
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from trendclip import publish

logger = logging.getLogger(__name__)

CHANNELS = {"sq": "SideQuestLogic", "pw": "Vice Pause", "bw": "Borderline Why"}
SERVED = "https://i.ytimg.com/vi/{id}/sardefault.jpg"  # the vertical thumbnail the Shorts shelf shows: ours when it took
FRAME = "https://i.ytimg.com/vi/{id}/oar2.jpg"  # the frame YouTube picked itself
VERIFY_WAIT = 90.0  # seconds after the second setting before YouTube serves what it has (THUMB_VERIFY_WAIT)
MATCH_LIMIT = 18.0  # mean pixel distance (0-255) under which two thumbnails are the same picture
TIMEOUT = 20
LOG_KEYWORDS = {
    "sq": ("planner", "assistant", "shorts", "publish", "gamer", "catch", "SideQuest", "emergency"),
    "pw": ("pw_channel", "pw_", "Price Tag", "pricetag", "Encore", "band_"),
    "bw": ("bw_channel", "bw_", "Borderline", "borderline"),
}
LOG_LEVELS = ("WARNING", "ERROR", "CRITICAL", "Traceback")
LOG_NOISE = ("PyNaCl is not installed", "davey is not installed")  # discord.py at every start: not a problem


class DoctorError(RuntimeError):
    pass


@dataclass
class Upload:
    channel: str  # sq | pw | bw
    key: str  # the Short's filename (sq) or the video folder (pw, bw)
    title: str
    record: dict[str, Any]  # the upload record: video_id, url, privacy, thumbnail, comment…
    thumbnail: Path | None = None  # our picture
    created_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def video_id(self) -> str:
        return str(self.record.get("video_id") or "")

    @property
    def url(self) -> str:
        return str(self.record.get("url") or (f"https://youtube.com/shorts/{self.video_id}" if self.video_id else ""))


# --------------------------------------------------------------------------- the channels


def _settings(settings=None):
    if settings is not None:
        return settings
    from trendclip.config import get_settings

    return get_settings()


def uploads(channel: str, settings=None, limit: int = 10) -> list[Upload]:
    """The channel's last videos on YouTube, newest first."""
    if channel == "sq":
        from trendclip import shorts

        s = _settings(settings)
        out = []
        for short in shorts.list_shorts(s):
            record = short.uploads.get("youtube")
            if record:
                out.append(Upload("sq", short.filename, short.title or short.filename, record, created_at=short.created_at.isoformat()[:16]))
            if len(out) >= limit:
                break
        return out
    if channel == "pw":
        from modules import pw_channel

        return [Upload("pw", v["key"], v.get("title", v["key"]), v["uploads"]["youtube"], pw_channel.thumb_path(v["key"]),
                       str(v.get("created_at", ""))[:16]) for v in pw_channel.videos() if (v.get("uploads") or {}).get("youtube")][:limit]
    if channel == "bw":
        from modules import bw_channel

        return [Upload("bw", v["key"], v.get("title", v["key"]), v["uploads"]["youtube"], None, str(v.get("created_at", ""))[:16])
                for v in bw_channel.videos(None, 60) if (v.get("uploads") or {}).get("youtube")][:limit]
    raise DoctorError(f"No channel {channel!r} (sq, pw or bw)")


def find(channel: str, key: str | None = None, settings=None) -> Upload:
    """One upload by its key (the last one when None)."""
    found = uploads(channel, settings, limit=60 if key else 1)
    if not found:
        raise DoctorError(f"{CHANNELS.get(channel, channel)} has no upload yet")
    if key is None:
        return found[0]
    for up in found:
        if up.key == key or up.video_id == key:
            return up
    raise DoctorError(f"No {CHANNELS.get(channel, channel)} upload {key}")


def thumbnail(up: Upload, settings=None) -> Path | None:
    """Our thumbnail for the upload (drawn now when the channel can)."""
    if up.thumbnail is not None:
        return up.thumbnail
    if up.channel == "sq":
        return publish.short_thumbnail(_settings(settings), up.key)
    if up.channel == "bw":
        from modules import bw_channel

        return bw_channel.thumbnail(up.key)
    if up.channel == "pw":
        from modules import pw_channel

        return pw_channel.thumb_path(up.key)
    return None


def client(channel: str):
    """A YouTube client with the channel's login."""
    if channel == "sq":
        return publish._youtube()
    if channel == "pw":
        from modules import pw_channel

        return publish._youtube(pw_channel.TOKEN)
    if channel == "bw":
        from modules import bw_channel

        return publish._youtube(bw_channel.TOKEN)
    raise DoctorError(f"No channel {channel!r}")


def note(channel: str, key: str, status: str, settings=None) -> None:
    """The thumbnail's status into the upload record."""
    if channel == "sq":
        from trendclip import shorts

        shorts.update_short(_settings(settings), key, lambda s: s.uploads["youtube"].__setitem__("thumbnail", status))
    elif channel == "pw":
        from modules import pw_channel

        pw_channel.note_thumbnail(key, status)
    elif channel == "bw":
        from modules import bw_channel

        bw_channel.note_thumbnail(key, status)


def rest(channel: str, settings=None):
    """Discord's REST API aimed at the channel's Discord channel (the gameplay channel: DISCORD_CHANNEL_ID in .env)."""
    from modules import channel_common
    from trendclip import hub

    s = _settings(settings)
    if channel == "sq":
        if not s.discord_channel_id or not s.discord_bot_token:
            return None
        api = hub.Rest(s)
        api.channel = s.discord_channel_id
        return api
    if channel == "pw":
        from modules import pw_channel

        return channel_common.rest(pw_channel.DIR, s)
    if channel == "bw":
        from modules import bw_channel

        return channel_common.rest(bw_channel.DIR, s)
    return None


# --------------------------------------------------------------------------- YouTube, live


def live(youtube, video_id: str) -> dict[str, Any]:
    """What YouTube says about the video now: privacy, processing, the counts, its thumbnails."""
    items = youtube.videos().list(part="snippet,status,statistics,processingDetails", id=video_id).execute().get("items") or []
    if not items:
        return {"found": False}
    item = items[0]
    snip, status, stats = item.get("snippet") or {}, item.get("status") or {}, item.get("statistics") or {}
    proc = item.get("processingDetails") or {}
    thumbs = snip.get("thumbnails") or {}
    return {
        "found": True, "title": snip.get("title", ""), "published_at": str(snip.get("publishedAt", ""))[:16].replace("T", " "),
        "privacy": status.get("privacyStatus", "?"), "upload_status": status.get("uploadStatus", "?"),
        "processing": proc.get("processingStatus", "?"), "rejection": status.get("rejectionReason", "") or status.get("failureReason", ""),
        "views": int(stats.get("viewCount") or 0), "likes": int(stats.get("likeCount") or 0), "comments": int(stats.get("commentCount") or 0),
        "thumbnail_url": ((thumbs.get("maxres") or thumbs.get("high") or thumbs.get("default") or {}).get("url", "")),
    }


def fetch(url: str) -> bytes | None:
    """The picture at `url`; None when YouTube has none there (404) or can't be reached."""
    import requests

    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0 TrendClip-doctor/1.0"})
    except requests.RequestException as err:
        logger.warning("Couldn't fetch %s: %s", url, err)
        return None
    if r.status_code != 200 or not r.content:
        return None
    return r.content


def distance(a: bytes | Path, b: bytes | Path) -> float:
    """How far apart two pictures are (0 = the same), on a 36x64 grey thumbnail of each, whatever their sizes."""
    from PIL import Image, ImageOps

    def small(src):
        img = Image.open(io.BytesIO(src) if isinstance(src, bytes) else src).convert("L")
        return ImageOps.fit(img, (36, 64))

    x, y = small(a), small(b)
    return sum(abs(p - q) for p, q in zip(x.tobytes(), y.tobytes())) / (36 * 64)


def served(video_id: str, ours: Path | None) -> dict[str, Any]:
    """What YouTube serves as the Short's thumbnail next to ours: {"served": bytes|None, "frame": bytes|None,
    "match": True|False|None, "distance": float|None}."""
    picture = fetch(SERVED.format(id=video_id))
    frame = fetch(FRAME.format(id=video_id))
    out: dict[str, Any] = {"served": picture, "frame": frame, "match": None, "distance": None}
    if picture and ours and ours.is_file():
        try:
            out["distance"] = round(distance(ours, picture), 1)
            out["match"] = out["distance"] <= MATCH_LIMIT
        except Exception as err:  # noqa: BLE001 - a picture Pillow can't read
            logger.warning("Couldn't compare the thumbnails of %s: %s", video_id, err)
    return out


def verify_thumbnail(video_id: str, ours: Path, wait: float | None = None) -> str:
    """After the second setting: does YouTube serve our picture? "set", or why it seems not to."""
    from modules.scene_images import setting

    seconds = VERIFY_WAIT if wait is None else wait
    try:
        seconds = float(setting("THUMB_VERIFY_WAIT", str(seconds)))
    except ValueError:
        pass
    if seconds < 0:
        return "set"
    if seconds:
        time.sleep(seconds)
    check = served(video_id, ours)
    if check["match"] is True:
        return "set"
    if check["served"] is None:
        return "set, but the Shorts shelf has no custom thumbnail yet"
    if check["match"] is False:
        return f"set, but YouTube shows another picture (distance {check['distance']:.0f})"
    return "set"


# --------------------------------------------------------------------------- /check


def check(channel: str, key: str | None = None, settings=None, youtube=None, with_pictures: bool = True) -> dict[str, Any]:
    """The report for /check: the record, YouTube live, the thumbnail comparison. Never raises for YouTube: the
    report carries the error (an expired login is the usual one)."""
    up = find(channel, key, settings)
    report: dict[str, Any] = {"channel": channel, "label": CHANNELS.get(channel, channel), "key": up.key, "title": up.title,
                              "url": up.url, "video_id": up.video_id, "record": up.record, "created_at": up.created_at,
                              "ours": None, "live": None, "error": "", "thumbnail": None}
    try:
        report["ours"] = thumbnail(up, settings)
    except Exception as err:  # noqa: BLE001
        logger.warning("Our thumbnail for %s %s: %s", channel, up.key, err)
    if not up.video_id:
        report["error"] = "the upload record has no video id"
        return report
    try:
        report["live"] = live(youtube or client(channel), up.video_id)
    except Exception as err:  # noqa: BLE001
        report["error"] = publish._api_error_text(err) if hasattr(publish, "_api_error_text") else str(err)
    if with_pictures:
        report["thumbnail"] = served(up.video_id, report["ours"])
    return report


def format_report(r: dict[str, Any]) -> list[str]:
    rec = r["record"]
    lines = [f"🔎 **{r['title']}** · {r['label']} · `{r['key']}`"]
    live_ = r.get("live")
    if r.get("error"):
        lines.append(f"❌ YouTube didn't answer: {r['error'][:300]}" + (" · reconnect the channel's YouTube login" if "invalid_grant" in r["error"] or "expired" in r["error"].lower() else ""))
    elif live_ is not None and not live_.get("found"):
        lines.append(f"❌ YouTube has no video {r['video_id']}: it was deleted, or the login is another channel's")
    elif live_:
        state = f"processing {live_['processing']}" if live_["processing"] != "succeeded" else "processed"
        lines.append(f"▶️ <{r['url']}> · **{live_['privacy']}** · {state}" + (f" · ⚠️ {live_['rejection']}" if live_.get("rejection") else "")
                     + (f" · published {live_['published_at']}" if live_.get("published_at") else ""))
        lines.append(f"👀 {live_['views']} views · {live_['likes']} likes · {live_['comments']} comments")
        if live_.get("title") and live_["title"] != r["title"] and not live_["title"].startswith(r["title"][:40]):
            lines.append(f"✏️ Title on YouTube: {live_['title']}")
    ours = r.get("ours")
    noted = str(rec.get("thumbnail") or "never set")
    t = r.get("thumbnail") or {}
    if t.get("match") is True:
        serves = "✅ YouTube serves our picture"
    elif t.get("match") is False:
        serves = f"❌ YouTube serves another picture (distance {t['distance']:.0f}): press **Set it again**"
    elif t.get("served") is None and t:
        serves = "❌ the Shorts shelf has no custom thumbnail" + (" (YouTube's own frame attached)" if t.get("frame") else "")
    else:
        serves = "not compared"
    lines.append(f"🖼️ Thumbnail: ours {'drawn' if ours else 'not drawn'} · record says: {noted} · {serves}")
    comment = rec.get("comment") or {}
    if comment.get("url"):
        lines.append(f"💬 Comment posted: <{comment['url']}>")
    elif rec.get("comment_error"):
        lines.append(f"💬 Comment failed: {str(rec['comment_error'])[:160]}")
    return lines


def set_again(channel: str, key: str, settings=None, youtube=None) -> str:
    """The "Set it again" button: our thumbnail set on the video now, verified at once, noted in the record."""
    up = find(channel, key, settings)
    ours = thumbnail(up, settings)
    if ours is None or not ours.is_file():
        raise DoctorError(f"{CHANNELS.get(channel, channel)} has no thumbnail drawn for {key}")
    if not up.video_id:
        raise DoctorError("The upload record has no video id")
    status = publish.set_thumbnail(youtube or client(channel), up.video_id, ours)
    if status == "set":
        status = verify_thumbnail(up.video_id, ours, wait=min(VERIFY_WAIT, 30.0))
    note(channel, key, f"{status} by hand", settings)
    return status


# --------------------------------------------------------------------------- alerts


def thumbnail_ok(status: str) -> bool:
    return status.startswith("set") and ", but" not in status


def alert_thumbnail(channel: str, key: str, status: str, settings=None, title: str = "") -> str | None:
    """After the upload's thumbnail work: a message in the channel's Discord channel only when something is wrong,
    with "Set it again" and the links. Never raises."""
    if thumbnail_ok(status) or status == "unknown":
        return None
    try:
        from modules import channel_common

        api = rest(channel, settings)
        if api is None:
            return None
        up = find(channel, key, settings)
        text = (f"🖼️ **{title or up.title}**: the thumbnail is not what we set ({status[:200]}). "
                f"Until it is, the Shorts shelf shows YouTube's own frame.")
        return api.post(text, components=channel_common.rows(
            channel_common.button("thumb", f"{channel} {key}", "🖼️ Set it again", "primary"),
            channel_common.button("check", f"{channel} {key}", "🔎 Check", "secondary"),
            channel_common.link_button("▶️ Watch", up.url) if up.url else None,
            channel_common.link_button("🎬 YouTube Studio", up.record.get("studio_url", "")) if up.record.get("studio_url") else None))
    except Exception as err:  # noqa: BLE001
        logger.warning("Thumbnail alert for %s %s failed: %s", channel, key, err)
        return None


# --------------------------------------------------------------------------- /log


def log_path(settings=None) -> Path:
    return _settings(settings).output_dir / "discord_bot.log"


def log_lines(path: Path, lines: int = 40, only: str = "all", width: int = 160) -> list[str]:
    """The log's last `lines` lines: all, the errors (warnings and tracebacks with their continuation lines), or one
    channel's (LOG_KEYWORDS). The date is dropped (the time stays) and long lines are clipped."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    keep: list[str] = []
    if only == "errors":
        in_block = False
        for line in raw:
            starts = bool(re.match(r"^\d{4}-\d{2}-\d{2} ", line))
            if starts:
                in_block = any(level in line for level in LOG_LEVELS) and not any(n in line for n in LOG_NOISE)
            if in_block:  # a warning or error line, and the traceback lines under it
                keep.append(line)
    elif only in LOG_KEYWORDS:
        words = LOG_KEYWORDS[only]
        keep = [l for l in raw if any(w in l for w in words)]
    else:
        keep = raw
    out = []
    for line in keep[-lines:]:
        line = re.sub(r"^\d{4}-\d{2}-\d{2} ", "", line).replace("trendclip.", "").replace("modules.", "")
        out.append(line if len(line) <= width else line[: width - 1] + "…")
    return out
