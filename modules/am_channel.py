"""The After Market channel: its folder under channels/aftermarket/, its YouTube login, its Discord channel,
and the two jobs a trading day has (AM_SLOTS, default 15:00 and 23:00 in Settings.timezone: before the
New York open and at the close).

Unlike Dark Outliers, nothing is made the day before: the numbers go stale. At each slot the bot fetches
the facts, writes the script, renders and uploads in one job. The pre-market job starts AM_LEAD minutes
early (default 20) so the video is up when the slot comes; the close job starts at its time so it has
the closing prints, and uploads as soon as the render is done. A day with no session (weekends, the
NYSE holidays in am_data.HOLIDAYS) gets one video, at the first slot: Saturday looks back at the week,
Sunday (and a holiday) at the week ahead. Every finished video is posted in the channel's Discord
channel with a "Don't post this" button; a veto before the upload cancels it, after the upload it
makes the video private. A video over 60 s (not a Short) never goes out.

Files: output/aftermarket/<date>_<kind>/ (facts.json, script.json, video.mp4, info.json); `key` below
is that folder's name.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import date as Day, datetime, timedelta
from pathlib import Path
from typing import Any

from modules import am_data, am_video, channel_common
from modules.scene_images import setting
from trendclip import assistant, publish

logger = logging.getLogger(__name__)

CHANNEL = "aftermarket"
NAME = "After Market"
DIR = channel_common.channel_dir(CHANNEL)
TOKEN = channel_common.token_path(CHANNEL)
CATEGORY = "25"  # News & Politics (AM_CATEGORY=27 for Education)
DEFAULT_SLOTS = "15:00,23:00"  # 08:00 and 16:00 New York, in Asia/Jerusalem (AM_SLOTS)
DEFAULT_LEAD = 20  # minutes before the pre-market slot the job starts (AM_LEAD)
START_WINDOW = timedelta(hours=2)  # a job missed by more than this (bot was off) is skipped: the brief would be stale
MIN_SECONDS, MAX_SECONDS = 20.0, 60.0  # a Short
MIN_WORDS = 60
WEEKEND_FOCUS = {5: "the week in review", 6: "the week ahead"}  # Saturday, Sunday; a holiday looks ahead too


class ChannelError(RuntimeError):
    pass


# --------------------------------------------------------------------------- settings


def slots() -> list[str]:
    """AM_SLOTS: "HH:MM,HH:MM" in Settings.timezone, sorted (default 15:00 and 23:00)."""
    found = sorted({s.strip() for s in setting("AM_SLOTS", DEFAULT_SLOTS).split(",") if re.fullmatch(r"\s*([01]\d|2[0-3]):[0-5]\d\s*", s)})
    return found or DEFAULT_SLOTS.split(",")


def lead_minutes() -> int:
    value = setting("AM_LEAD", str(DEFAULT_LEAD)).strip()
    return int(value) if value.isdigit() and 0 <= int(value) <= 120 else DEFAULT_LEAD


def auto_post() -> bool:
    return setting("AM_AUTO_POST", "1").strip().lower() not in ("0", "false", "off", "none")


def upload_privacy() -> str:
    value = setting("AM_UPLOAD_PRIVACY", "public").strip().lower()
    return value if value in ("private", "unlisted", "public") else "public"


def category() -> str:
    value = setting("AM_CATEGORY", CATEGORY).strip()
    return value if value.isdigit() else CATEGORY


# --------------------------------------------------------------------------- the day's jobs


@dataclass
class Job:
    day: Day
    slot: str  # "HH:MM" from AM_SLOTS
    kind: str  # premarket | close | weekend (am_data.SLOTS)
    start: datetime  # when the bot starts making it
    post_at: datetime  # when it should be on YouTube (the slot's time)
    focus: str = ""  # the weekend videos: "the week in review" / "the week ahead"

    @property
    def key(self) -> str:
        return job_key(self.day, self.slot)

    @property
    def video_key(self) -> str:
        return f"{self.day.isoformat()}_{self.kind}"

    def view(self) -> dict[str, Any]:
        return {**asdict(self), "day": self.day.isoformat(), "start": self.start.isoformat(timespec="minutes"),
                "post_at": self.post_at.isoformat(timespec="minutes"), "key": self.key, "video_key": self.video_key}


def job_key(day: Day, slot: str) -> str:
    return f"am {day.isoformat()} {slot} make"


def slot_time(settings, day: Day, slot: str) -> datetime:
    hour, minute = map(int, slot.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=settings.tz)


def focus_for(day: Day) -> str:
    return WEEKEND_FOCUS.get(day.weekday(), WEEKEND_FOCUS[6])


def jobs_for(settings, day: Day) -> list[Job]:
    """The day's jobs: one per slot on a trading day (the clock in New York decides whether a slot is the
    pre-market or the close brief); one weekend video at the first slot on any other day."""
    out = []
    for slot in slots():
        at = slot_time(settings, day, slot)
        kind = am_data.slot_for(at)
        if kind == "weekend":
            if out:
                break
            out.append(Job(day, slot, kind, at, at, focus_for(day)))
            continue
        start = at - timedelta(minutes=lead_minutes()) if kind == "premarket" else at
        out.append(Job(day, slot, kind, start, at))
    return out


def due_jobs(settings, at: datetime, sent: set[str]) -> list[Job]:
    """The jobs whose start has come (within START_WINDOW) and that have not started, when AM_AUTO_POST is on."""
    if not auto_post():
        return []
    at = at.astimezone(settings.tz)
    found = []
    for day in (at.date() - timedelta(days=1), at.date()):  # a job that started late yesterday is still yesterday's
        for job in jobs_for(settings, day):
            if job.key not in sent and job.start <= at < job.start + START_WINDOW:
                found.append(job)
    return found


def next_job(settings, at: datetime, days: int = 10) -> Job | None:
    at = at.astimezone(settings.tz)
    for n in range(days):
        for job in jobs_for(settings, at.date() + timedelta(days=n)):
            if job.start > at:
                return job
    return None


def job_for(settings, day: Day, kind: str) -> Job:
    """The day's job of that kind, or an ad-hoc one (the dashboard's "run now" on a kind the day has not)."""
    for job in jobs_for(settings, day):
        if job.kind == kind:
            return job
    now = datetime.now(settings.tz).replace(second=0, microsecond=0)
    return Job(day, now.strftime("%H:%M"), kind, now, now, focus_for(day) if kind == "weekend" else "")


# --------------------------------------------------------------------------- files


def out_root(out_dir: Path | None = None) -> Path:
    return out_dir or am_video.OUTPUT_DIR


def info_path(key: str, out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / key / "info.json"


def video_path(key: str, out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / key / "video.mp4"


def key_of(info: dict) -> str:
    return f"{info.get('date', '')}_{info.get('slot', '')}"


def load_info(key: str, out_dir: Path | None = None) -> dict | None:
    path = info_path(key, out_dir)
    if not path.is_file() or not video_path(key, out_dir).is_file():
        return None
    try:
        return {**json.loads(path.read_text(encoding="utf-8")), "key": key}
    except ValueError:
        return None


def save_info(info: dict, out_dir: Path | None = None) -> Path:
    key = info.get("key") or key_of(info)
    path = info_path(key, out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in info.items() if k != "key"}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def videos(out_dir: Path | None = None, limit: int = 40) -> list[dict]:
    """Every finished video (output/aftermarket/*/info.json with its video.mp4), newest first."""
    root = out_root(out_dir)
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(root.iterdir(), reverse=True):
        if folder.is_dir() and (info := load_info(folder.name, out_dir)):
            out.append(info)
        if len(out) >= limit:
            break
    return out


def latest_facts(out_dir: Path | None = None) -> dict | None:
    """The newest facts.json (the last session read), by its folder name (<date>_<kind>) then its time."""
    root = out_root(out_dir)
    if not root.is_dir():
        return None
    files = sorted(root.glob("*/facts.json"), key=lambda p: (p.parent.name[:10], p.stat().st_mtime), reverse=True)
    for path in files:
        try:
            return {**json.loads(path.read_text(encoding="utf-8")), "key": path.parent.name}
        except ValueError:
            continue
    return None


def facts_lines(facts: dict | None) -> str:
    return am_data.summary(facts) if facts else "No session read yet."


# --------------------------------------------------------------------------- make, check, upload


def check_video(info: dict) -> list[str]:
    """Why a finished video should not go out (empty = fine): over 60 s it is not a Short."""
    problems = []
    seconds = float(info.get("duration_seconds") or 0)
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        problems.append(f"{seconds:.0f} s long (wanted {MIN_SECONDS:.0f}-{MAX_SECONDS:.0f})")
    pictures = str(info.get("pictures") or "")
    if "placeholder" in pictures and setting("AM_ALLOW_PLACEHOLDER", "0") not in ("1", "true", "on"):
        problems.append(f"placeholder pictures ({pictures})")
    if int(info.get("words") or 0) < MIN_WORDS:
        problems.append(f"only {info.get('words')} words")
    return problems


def make(settings, job: Job, progress=lambda f, m: None, *, now: datetime | None = None, out_dir: Path | None = None,
         produce=None, template: bool = False, announce: bool = False) -> dict:
    """The job's video: the facts now, the script, the render, the check. Raises ChannelError when the
    check fails (the video stays on disk, marked, and is not uploaded). `produce`: am_video.produce
    (tests pass a fake)."""
    progress(0.0, "Reading the market")
    facts = am_data.build(job.kind, now=now, progress=lambda m: progress(0.0, m))
    if job.focus:
        facts["focus"] = job.focus
    am_data.save(facts, out_dir)
    info = (produce or am_video.produce)(job.kind, progress, facts=facts, out_dir=out_dir, settings=settings, template=template)
    info["key"] = key_of(info)
    info["job"] = {"day": job.day.isoformat(), "slot": job.slot, "post_at": job.post_at.isoformat(timespec="minutes")}
    problems = check_video(info)
    info["check"] = problems
    save_info(info, out_dir)
    if problems:
        raise ChannelError(f"{info.get('title', info['key'])} can't go out: " + "; ".join(problems))
    if announce:
        announce_video(info, settings, out_dir)
    return info


def upload_request(info: dict, privacy: str = "public") -> publish.YouTubeUploadRequest:
    """Title, description and tags from info.json (the description already carries the sources, the
    disclaimer and the hashtags)."""
    return publish.YouTubeUploadRequest(
        title=info["title"][: publish.YT_TITLE_MAX], description=(info.get("description") or "")[:5000],
        tags=[t for t in info.get("hashtags", []) if t], privacy=privacy, category_id=category(), as_short=True)


def upload(key: str, privacy: str | None = None, progress=lambda f, m: None, youtube=None, out_dir: Path | None = None,
           settings=None) -> dict[str, Any]:
    """Upload output/aftermarket/<key>/video.mp4 to the After Market channel and record it in its info.json."""
    info = load_info(key, out_dir)
    if info is None:
        raise publish.PublishError(f"No After Market video {key}")
    if youtube is None and not connected():
        raise publish.PublishError("Connect the After Market YouTube channel first (After Market tab → Connect YouTube)")
    req = upload_request(info, privacy or upload_privacy())
    record = publish.upload_file(video_path(key, out_dir), req, True, progress, youtube or publish._youtube(TOKEN))
    info.setdefault("uploads", {})["youtube"] = record
    save_info(info, out_dir)
    progress(1.0, "Uploaded")
    announce_upload(info, record, settings)
    return record


def is_vetoed(info: dict) -> bool:
    return bool(info.get("veto"))


def publish_if_allowed(key: str, progress=lambda f, m: None, out_dir: Path | None = None, youtube=None,
                       settings=None) -> dict[str, Any] | None:
    """The upload at the slot's time: None when the video was vetoed, failed its check, is already up,
    or AM_AUTO_POST is off; else the upload record."""
    info = load_info(key, out_dir)
    if info is None or is_vetoed(info) or info.get("check") or not auto_post():
        return None
    if record := (info.get("uploads") or {}).get("youtube"):
        return record
    return upload(key, None, progress, youtube, out_dir, settings)


def veto(key: str, out_dir: Path | None = None, youtube=None) -> dict[str, Any]:
    """"Don't post this": before the upload it is cancelled; after it the video is made private on YouTube."""
    info = load_info(key, out_dir)
    if info is None:
        raise KeyError(key)
    info["veto"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    record = (info.get("uploads") or {}).get("youtube") or {}
    hidden = False
    if record.get("video_id") and record.get("privacy") != "private":
        from googleapiclient.errors import HttpError

        youtube = youtube or publish._youtube(TOKEN)
        try:
            youtube.videos().update(part="status", body={"id": record["video_id"], "status": {"privacyStatus": "private"}}).execute()
        except HttpError as err:
            save_info(info, out_dir)
            raise publish.PublishError(publish._api_error_text(err).replace("YouTube upload failed", "YouTube refused the change")) from err
        record["privacy"] = "private"
        hidden = True
    save_info(info, out_dir)
    return {"key": key, "title": info.get("title", key), "uploaded": bool(record), "hidden": hidden}


# --------------------------------------------------------------------------- Discord


def discord_channel_id() -> int | None:
    return channel_common.discord_channel_id(DIR)


def _rest(settings=None):
    return channel_common.rest(DIR, settings)


button, link_button, rows = channel_common.button, channel_common.link_button, channel_common.rows


def video_buttons(key: str, info: dict) -> list[dict[str, Any]]:
    """Under a video post: Don't post this (cancels the upload, or hides it), Watch / Upload now."""
    uploaded = (info.get("uploads") or {}).get("youtube") or {}
    out = []
    if not is_vetoed(info):
        out.append(button("amveto", key, "🚫 Don't post this" if not uploaded else "🚫 Make it private", "danger"))
    if uploaded.get("url"):
        out.append(link_button("▶️ Watch on YouTube", uploaded["url"]))
    elif not info.get("check") and not is_vetoed(info):
        out.append(button("amup", key, "⬆️ Upload to YouTube now", "primary"))
    return rows(*out)


def video_line(info: dict) -> str:
    return (f"🎬 **{info.get('title', info.get('key', ''))}** · {info.get('duration_seconds', 0):.0f} s · {info.get('words', '?')} words · "
            f"script by {info.get('writer', '?')} · pictures: {info.get('pictures', '?')}"
            + (f" · 🎵 {info['music']['title']}" if info.get("music") else ""))


def announce_video(info: dict, settings=None, out_dir: Path | None = None, note: str | None = None,
                   components: list[dict[str, Any]] | None = None) -> str | None:
    """Post the finished video (shrunk to Discord's 10 MB when needed) in the After Market channel. Never raises."""
    try:
        rest = _rest(settings)
        if rest is None:
            return None
        key = info.get("key") or key_of(info)
        path = video_path(key, out_dir)
        small = assistant.fit_for_discord(path, path.parent / "discord" / path.name)
        post_at = (info.get("job") or {}).get("post_at", "")[11:16]
        text = video_line(info) + "\n" + (note or (
            f"It goes out by itself at {post_at} as {upload_privacy()}." if post_at and auto_post() and not info.get("check")
            else "Upload it from the After Market tab."))
        if components is None:
            components = video_buttons(key, info)
        return rest.post(text, (f"{key}.mp4", small.read_bytes()), components)
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post for After Market %s failed: %s", info.get("key"), err)
        return None


def announce_upload(info: dict, record: dict, settings=None) -> str | None:
    try:
        rest = _rest(settings)
        if rest is None:
            return None
        return rest.post(f"⬆️ **{info.get('title', info.get('key', ''))}** is on YouTube ({record.get('privacy', '?')}): {record.get('url', '')}",
                         components=rows(link_button("▶️ Watch", record.get("url", "")) if record.get("url") else None,
                                         button("amveto", info.get("key") or key_of(info), "🚫 Make it private", "danger"),
                                         link_button("🎬 YouTube Studio", record.get("studio_url", "")) if record.get("studio_url") else None))
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post for the After Market upload %s failed: %s", info.get("key"), err)
        return None


def announce_text(text: str, settings=None, components: list[dict[str, Any]] | None = None) -> str | None:
    try:
        rest = _rest(settings)
        return None if rest is None else rest.post(text, components=components)
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post in the After Market channel failed: %s", err)
        return None


# --------------------------------------------------------------------------- YouTube login


def youtube_status() -> dict[str, Any]:
    return channel_common.youtube_status(DIR)


def start_login(redirect_uri: str) -> str:
    return channel_common.start_login(DIR, redirect_uri)


def logout() -> None:
    channel_common.logout(DIR)


def connected() -> bool:
    return channel_common.connected(DIR)


def handle() -> str:
    return channel_common.handle(DIR)


# --------------------------------------------------------------------------- the whole job, synchronously


def run(settings, job: Job, progress=lambda f, m: None, *, out_dir: Path | None = None, produce=None, upload_now: bool = True,
        template: bool = False, announce: bool = True, youtube=None) -> dict[str, Any]:
    """The dashboard's "run this slot now": make (and announce with the veto button), then upload at once
    when `upload_now`. {"info", "record", "error"}: a failed check is reported, not raised."""
    try:
        info = make(settings, job, progress, out_dir=out_dir, produce=produce, template=template, announce=announce)
    except ChannelError as err:
        if announce:
            announce_text(f"❌ {err}", settings)
        return {"info": load_info(job.video_key, out_dir), "record": None, "error": str(err)}
    record = None
    if upload_now:
        try:
            record = publish_if_allowed(info["key"], progress, out_dir, youtube, settings) if auto_post() else \
                upload(info["key"], None, progress, youtube, out_dir, settings)
        except Exception as err:  # noqa: BLE001 - the video is made; the upload can be done from the tab
            logger.exception("After Market upload of %s failed", info["key"])
            if announce:
                announce_text(f"❌ **{info.get('title', info['key'])}** did not go out: {err}\nUpload it from the After Market tab.", settings)
            return {"info": info, "record": None, "error": str(err)}
    return {"info": info, "record": record, "error": None}
