"""Chat-bot backend (used by the Discord bot): the day's upload slots, one-call random Shorts and
uploads, Discord-sized video copies and reminder bookkeeping. No Discord code here."""

from __future__ import annotations

import json
import logging
import random
import re
import subprocess
import threading
from datetime import date as Day, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel

from . import music, planner, publish, script_writer, shorts, video_assembler, video_downloader
from .config import Settings
from .planner import PlanItem, Slot
from .video_downloader import BackgroundClip, DownloadError

logger = logging.getLogger(__name__)

ProgressFn = Callable[[float | None, str], None]
DISCORD_FILE_LIMIT = 10 * 1024 * 1024  # servers without boosts
MORNING_WINDOW = timedelta(hours=6)  # a summary missed by more than this (bot was off) is skipped


def _noop(_frac: float | None, _msg: str) -> None:
    pass


def now(settings: Settings) -> datetime:
    return datetime.now(settings.tz)


def today(settings: Settings) -> Day:
    return now(settings).date()


def parse_day(settings: Settings, text: str | None) -> Day:
    """'today', 'tomorrow', '+3' (days from today) or YYYY-MM-DD."""
    text = (text or "today").strip().lower()
    base = today(settings)
    if text in ("today", "0"):
        return base
    if text == "tomorrow":
        return base + timedelta(days=1)
    if m := re.fullmatch(r"\+(\d{1,3})", text):
        return base + timedelta(days=int(m.group(1)))
    try:
        return Day.fromisoformat(text)
    except ValueError:
        raise ValueError(f"Not a day: {text!r} (use today, tomorrow, +N or YYYY-MM-DD)") from None


# --------------------------------------------------------------------------- plan


class SlotEntry(BaseModel):
    day: Day
    slot: Slot
    item: PlanItem | None = None
    short: shorts.ShortVideo | None = None

    @property
    def youtube(self) -> dict[str, Any]:
        return (self.short.uploads.get("youtube") or {}) if self.short else {}


def _short_or_none(settings: Settings, filename: str | None) -> shorts.ShortVideo | None:
    if not filename:
        return None
    try:
        return shorts.get_short(settings, filename)
    except (ValueError, FileNotFoundError):
        return None


def day_slots(settings: Settings, day: Day) -> list[SlotEntry]:
    out = []
    for slot in planner.SLOTS:
        item = planner.slot_item(settings, day, slot)
        out.append(SlotEntry(day=day, slot=slot, item=item, short=_short_or_none(settings, item and item.short)))
    return out


def unslotted(settings: Settings, day: Day) -> list[PlanItem]:
    """Items planned for the day without a fixed time."""
    return [i for i in planner.load(settings).items if i.date == day and i.slot is None]


def planned_items(settings: Settings, start: Day, days: int) -> list[PlanItem]:
    end = start + timedelta(days=days)
    items = [i for i in planner.load(settings).items if start <= i.date < end]
    return sorted(items, key=lambda i: (i.date, i.slot or "99", i.created_at))


def backlog(settings: Settings) -> list[shorts.ShortVideo]:
    """Shorts that are not on the plan and not on YouTube yet, newest first."""
    planned = {i.short for i in planner.load(settings).items if i.short}
    return [s for s in shorts.list_shorts(settings) if s.filename not in planned and not s.uploads.get("youtube")]


def plan_short(settings: Settings, filename: str, day: Day | None = None, slot: Slot | None = None) -> PlanItem:
    """Put a Short on the plan. No day: the next free slot; a day without a slot: its first free slot."""
    if day is None and slot is None:
        found = planner.next_free_slot(settings, now(settings))
        if not found:
            raise ValueError("Every slot of the next 30 days is taken")
        day, slot = found
    day = day or today(settings)
    if slot is None:
        free = planner.free_slots(settings, day)
        if not free:
            raise ValueError(f"{day.isoformat()} is full (18:00 and 23:00 are taken)")
        slot = free[0]
    return planner.add_item(settings, planner.ItemRequest(kind="reel", date=day, slot=slot, short=filename))


def unplan(settings: Settings, item_id: str) -> PlanItem:
    item = planner.find_item(settings, item_id)
    planner.delete_item(settings, item_id)
    return item


def move(settings: Settings, item_id: str, day: Day, slot: Slot) -> PlanItem:
    return planner.update_item(settings, item_id, planner.ItemUpdate(date=day, slot=slot))


# --------------------------------------------------------------------------- random Short


def random_game(settings: Settings) -> str:
    """A random game that the No-Copyright channels have videos of."""
    names = [name for name, n in video_downloader.get_library(settings).counts().items() if n]
    if not names:
        raise DownloadError("The No-Copyright channels have no recognised games yet")
    return random.choice(names)


def fresh_clip(settings: Settings, game: str | None = None, progress: ProgressFn = _noop) -> BackgroundClip:
    """A newly downloaded random clip; one of the downloaded clips when downloading fails."""
    try:
        game = game or random_game(settings)
        progress(None, f"Downloading random {game} gameplay")
        return video_downloader.download_background(game, settings, sources=["youtube"], progress=progress)
    except Exception as err:  # noqa: BLE001 - any download problem falls back to the library
        logger.warning("Fresh clip download failed (%s); using a downloaded clip", err)
        clips = video_downloader.list_backgrounds(settings)
        same_game = [c for c in clips if game and c.game == game]
        if not clips:
            raise DownloadError(f"Could not download gameplay ({err}) and no clips are downloaded yet") from err
        progress(None, "Download failed; using one of the downloaded clips")
        return random.choice(same_game or clips)


def create_random_short(settings: Settings, game: str | None = None, progress: ProgressFn = _noop,
                        mode: Literal["story", "math", "riddle"] = "story") -> shorts.ShortVideo:
    """Random gameplay + a random Gemini storytime (or a math / riddle quiz) + voice, music by mood,
    pop-ups and stickers."""
    if script_writer.gemini_api_key(settings) is None:
        raise script_writer.ScriptError("GEMINI_API_KEY is not set in .env (needed to write the story)")
    clip = fresh_clip(settings, game, progress)
    path = Path(clip.path)
    clip_seconds = video_downloader.probe_video(path).get("duration_seconds")
    script = script_writer.write_script(settings, clip.game, path, mode=mode, watch_clip=False,
                                        max_seconds=clip_seconds, progress=progress)
    req = shorts.RenderRequest(
        clip=clip.filename, game=clip.game[:80] or "Gameplay", script=script.script, title=script.title,
        description=script.description, hashtags=script.hashtags, title_card=script.title_card,
        popups=script.popups[:20], reactions=script.reactions[:10], end_card=script.end_card,
        pinned_comment=script.pinned_comment, music_source="mood", music_mood=script.music_mood,
        channel_handle=settings.channel_handle, flashes=script.flashes[:30], answer=script.answer,
    )
    try:
        return shorts.render_short(settings, req, progress)
    except music.MusicError as err:
        logger.warning("No background music (%s); rendering without", err)
        progress(None, "No music found; rendering without music")
        return shorts.render_short(settings, req.model_copy(update={"music_source": "none"}), progress)


# --------------------------------------------------------------------------- YouTube


def upload_request(settings: Settings, filename: str, privacy: publish.Privacy = "public") -> publish.YouTubeUploadRequest:
    """What the Upload tab would send: the Short's saved texts (template the first time), series links."""
    short = publish.texts_for(settings, filename)
    texts = publish.PlatformTexts.model_validate(short.texts)
    series = publish.series_info(settings, short)
    return publish.YouTubeUploadRequest(
        title=texts.youtube.title,
        description=publish.youtube_description(texts.youtube, texts.credit, series["links"] if series else ""),
        tags=texts.youtube.tags,
        privacy=privacy,
    )


def upload_now(settings: Settings, filename: str, progress: ProgressFn = _noop) -> dict[str, Any]:
    return publish.upload_youtube(settings, filename, upload_request(settings, filename, "public"), progress)


def pinned_comment(settings: Settings, filename: str) -> str:
    short = shorts.get_short(settings, filename)
    series = publish.series_info(settings, short)
    return (series["pinned_comment"] if series else short.pinned_comment).strip()


# --------------------------------------------------------------------------- Discord-sized copies


def discord_copy(settings: Settings, filename: str, limit: int = DISCORD_FILE_LIMIT) -> Path:
    """The Short itself when it fits the upload limit, else a smaller re-encode (cached)."""
    path = shorts.short_path(settings, filename)
    if path.stat().st_size <= limit:
        return path
    out = settings.output_dir / "discord" / filename
    if out.is_file() and out.stat().st_mtime >= path.stat().st_mtime and out.stat().st_size <= limit:
        return out
    ffmpeg = video_downloader.ffmpeg_path()
    if not ffmpeg:
        raise ValueError("ffmpeg is needed to shrink the video for Discord")
    out.parent.mkdir(parents=True, exist_ok=True)
    seconds = max(video_assembler.media_duration(path), 1.0)
    audio_kbps = 96
    video_kbps = int(limit * 8 * 0.9 / seconds / 1000) - audio_kbps
    tmp = out.with_suffix(".tmp.mp4")
    for _ in range(3):
        if video_kbps < 150:
            raise ValueError(f"The video is too long to fit Discord's {limit // (1024 * 1024)} MB limit")
        subprocess.run([
            ffmpeg, "-hide_banner", "-y", "-i", str(path), "-vf", "scale=-2:'min(1280,ih)'",
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{video_kbps}k", "-maxrate", f"{video_kbps}k",
            "-bufsize", f"{video_kbps * 2}k", "-c:a", "aac", "-b:a", f"{audio_kbps}k",
            "-movflags", "+faststart", str(tmp),
        ], check=True, capture_output=True, timeout=900)
        if tmp.stat().st_size <= limit:
            tmp.replace(out)
            return out
        video_kbps = int(video_kbps * 0.75)
    tmp.unlink(missing_ok=True)
    raise ValueError("Could not shrink the video under Discord's upload limit")


# --------------------------------------------------------------------------- reminders


class Reminder(BaseModel):
    key: str
    kind: Literal["morning", "slot"]
    day: Day
    slot: Slot | None = None


def due_reminders(settings: Settings, at: datetime, sent: set[str]) -> list[Reminder]:
    """The morning plan summary, and each slot's video DISCORD_REMINDER_MINUTES before it."""
    at = at.astimezone(settings.tz)
    day = at.date()
    out = []
    hour, minute = map(int, settings.discord_morning_time.split(":"))
    morning = datetime(day.year, day.month, day.day, hour, minute, tzinfo=settings.tz)
    key = f"{day.isoformat()} morning"
    if key not in sent and morning <= at < morning + MORNING_WINDOW:
        out.append(Reminder(key=key, kind="morning", day=day))
    if settings.discord_reminder_minutes:
        lead = timedelta(minutes=settings.discord_reminder_minutes)
        for slot in planner.SLOTS:
            when = planner.slot_time(settings, day, slot)
            key = f"{day.isoformat()} {slot}"
            if key not in sent and when - lead <= at < when:
                out.append(Reminder(key=key, kind="slot", day=day, slot=slot))
    return out


class SentLog:
    """Which reminders went out (survives restarts): output/discord_state.json."""

    def __init__(self, settings: Settings, keep_days: int = 7):
        self.path = settings.output_dir / "discord_state.json"
        self.keep_days = keep_days
        self._lock = threading.Lock()

    def load(self) -> set[str]:
        try:
            return set(json.loads(self.path.read_text(encoding="utf-8")).get("sent", []))
        except (OSError, ValueError):
            return set()

    def add(self, key: str, today_: Day) -> None:
        with self._lock:
            cutoff = (today_ - timedelta(days=self.keep_days)).isoformat()
            sent = {k for k in self.load() if k[:10] >= cutoff} | {key}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"sent": sorted(sent)}, indent=2), encoding="utf-8")
            tmp.replace(self.path)
