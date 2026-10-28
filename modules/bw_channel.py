"""The Borderline Why channel (@BorderlineWhy): its folder under channels/borderlinewhy/, its YouTube login, its
Discord channel, and its day: one video per BW_SLOTS time (default 14:30 and 23:00 in Settings.timezone).

BW_LEAD hours before the slot (default 8) the bot makes the video: the next topic (a hand-checked script from
output/borderlinewhy/queue/ first, else the topic list), its research and checked script, the voice, the maps,
the render. The video is posted in the channel's Discord channel with Approve / Don't post / Upload now. At the
slot's time it goes to YouTube only when it was approved (BW_REQUIRE_APPROVAL, default on); approving later
uploads it at once. A video outside MIN_SECONDS-MAX_SECONDS never goes out.

Files: output/borderlinewhy/<date>_<HHMM>/ (script.json, voice.mp3, video.mp4, info.json); `key` below is that
folder's name.
"""

from __future__ import annotations

import json
import logging
import random
import re
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import date as Day, datetime, timedelta
from pathlib import Path
from typing import Any

from modules import bw_render, bw_script, bw_topics, channel_common
from modules.scene_images import setting
from trendclip import assistant, publish
from trendclip.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

CHANNEL = "borderlinewhy"
NAME = "Borderline Why"
DIR = channel_common.channel_dir(CHANNEL)
TOKEN = channel_common.token_path(CHANNEL)
OUTPUT_DIR = PROJECT_ROOT / "output" / CHANNEL
CATEGORY = "27"  # Education
DEFAULT_SLOTS = "14:30,23:00"  # two a day, like the other channels (BW_SLOTS, in Settings.timezone)
DEFAULT_LEAD = 8  # hours before the slot the video is made, so there is time to approve it (BW_LEAD)
POST_WINDOW = timedelta(hours=6)  # a slot's upload missed by more than this (bot was off) waits for a button
MIN_SECONDS, MAX_SECONDS = 30.0, 90.0
TOPIC_TRIES = 3  # topics tried in one job before giving up (a topic without two sources is skipped)
DEFAULT_VOICE = "am_michael"


class ChannelError(RuntimeError):
    pass


# --------------------------------------------------------------------------- settings


def slots() -> list[str]:
    found = sorted({s.strip() for s in setting("BW_SLOTS", DEFAULT_SLOTS).split(",") if re.fullmatch(r"\s*([01]\d|2[0-3]):[0-5]\d\s*", s)})
    return found or DEFAULT_SLOTS.split(",")


def lead_hours() -> float:
    try:
        value = float(setting("BW_LEAD", str(DEFAULT_LEAD)))
    except ValueError:
        return DEFAULT_LEAD
    return value if 0 <= value <= 20 else DEFAULT_LEAD


def _on(name: str, default: str = "1") -> bool:
    return setting(name, default).strip().lower() not in ("0", "false", "off", "no", "none")


def auto_make() -> bool:
    return _on("BW_AUTO_MAKE")


def auto_post() -> bool:
    return _on("BW_AUTO_POST")


def require_approval() -> bool:
    return _on("BW_REQUIRE_APPROVAL")


def upload_privacy() -> str:
    value = setting("BW_UPLOAD_PRIVACY", "public").strip().lower()
    return value if value in ("private", "unlisted", "public") else "public"


# --------------------------------------------------------------------------- the day's jobs


@dataclass
class Job:
    day: Day
    slot: str
    start: datetime  # when the bot makes it
    post_at: datetime  # when it goes to YouTube

    @property
    def key(self) -> str:
        return f"{self.day.isoformat()}_{self.slot.replace(':', '')}"

    @property
    def make_key(self) -> str:
        return f"{self.day.isoformat()} bw {self.slot} make"  # SentLog keys start with the date

    @property
    def post_key(self) -> str:
        return f"{self.day.isoformat()} bw {self.slot} post"

    def view(self) -> dict[str, Any]:
        return {**asdict(self), "day": self.day.isoformat(), "start": self.start.isoformat(timespec="minutes"),
                "post_at": self.post_at.isoformat(timespec="minutes"), "key": self.key}


def jobs_for(settings, day: Day) -> list[Job]:
    out = []
    for slot in slots():
        hour, minute = map(int, slot.split(":"))
        at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=settings.tz)
        out.append(Job(day, slot, at - timedelta(hours=lead_hours()), at))
    return out


def job_for(settings, day: Day, slot: str | None = None) -> Job:
    jobs = jobs_for(settings, day)
    return next((j for j in jobs if j.slot == slot), jobs[0])


def due_makes(settings, at: datetime, sent: set[str], out_dir: Path | None = None) -> list[Job]:
    """Jobs whose making time has come, before their slot, not started, with no video yet."""
    if not auto_make():
        return []
    at = at.astimezone(settings.tz)
    found = []
    for day in (at.date(), at.date() + timedelta(days=1)):
        for job in jobs_for(settings, day):
            if job.make_key not in sent and job.start <= at < job.post_at and load_info(job.key, out_dir) is None:
                found.append(job)
    return found


def due_posts(settings, at: datetime, sent: set[str]) -> list[Job]:
    """Jobs whose slot has come (within POST_WINDOW) and whose upload was not tried yet."""
    if not auto_post():
        return []
    at = at.astimezone(settings.tz)
    return [job for day in (at.date() - timedelta(days=1), at.date()) for job in jobs_for(settings, day)
            if job.post_key not in sent and job.post_at <= at < job.post_at + POST_WINDOW]


def next_job(settings, at: datetime, days: int = 3) -> Job | None:
    at = at.astimezone(settings.tz)
    for n in range(days):
        for job in jobs_for(settings, at.date() + timedelta(days=n)):
            if job.post_at > at:
                return job
    return None


def slot_state(job: Job, info: dict | None, now: datetime) -> str:
    """One word for the plan: posted | blocked | vetoed | approved | waiting (for Approve) | due (being made) |
    missed (no video and the slot has passed) | later (made at job.start)."""
    if info:
        if (info.get("uploads") or {}).get("youtube"):
            return "posted"
        if info.get("check"):
            return "blocked"
        if is_vetoed(info):
            return "vetoed"
        return "approved" if is_approved(info) or not require_approval() else "waiting"
    if now >= job.post_at:
        return "missed"
    return "due" if now >= job.start else "later"


def plan_days(settings, now: datetime, before: int = 1, after: int = 6, out_dir: Path | None = None) -> list[dict[str, Any]]:
    """The Plan view: every slot from `before` days back to `after` days ahead with its video and state; a
    video made for a time that is no longer a slot (BW_SLOTS changed) is listed under its day too."""
    now = now.astimezone(settings.tz)
    days = []
    for n in range(-before, after + 1):
        day = now.date() + timedelta(days=n)
        rows = []
        keys = set()
        for job in jobs_for(settings, day):
            info = load_info(job.key, out_dir)
            keys.add(job.key)
            rows.append({**job.view(), "state": slot_state(job, info, now), "video": _plan_video(info)})
        folder = out_root(out_dir)
        if folder.is_dir():
            for extra in sorted(folder.glob(f"{day.isoformat()}_[0-9][0-9][0-9][0-9]")):
                if extra.name in keys or (info := load_info(extra.name, out_dir)) is None:
                    continue
                slot = f"{extra.name[11:13]}:{extra.name[13:15]}"
                job = Job(day, slot, datetime.fromisoformat(info["job"]["post_at"]) - timedelta(hours=lead_hours())
                          if info.get("job") else now, datetime.fromisoformat(info["job"]["post_at"]) if info.get("job") else now)
                rows.append({**job.view(), "state": slot_state(job, info, now), "video": _plan_video(info), "extra": True})
        rows.sort(key=lambda r: r["slot"])
        days.append({"day": day.isoformat(), "weekday": day.strftime("%A"), "today": day == now.date(), "slots": rows})
    return days


def _plan_video(info: dict | None) -> dict | None:
    if info is None:
        return None
    return {"key": info["key"], "title": info.get("title", ""), "duration_seconds": info.get("duration_seconds"),
            "check": info.get("check") or [], "youtube": (info.get("uploads") or {}).get("youtube"), "question": info.get("question", ""),
            "sent": (info.get("sent") or {}).get("hub"), "created_at": info.get("created_at", "")}


# --------------------------------------------------------------------------- files


def out_root(out_dir: Path | None = None) -> Path:
    return out_dir or OUTPUT_DIR


def queue_dir(out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / "queue"


def info_path(key: str, out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / key / "info.json"


def video_path(key: str, out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / key / "video.mp4"


def load_info(key: str, out_dir: Path | None = None) -> dict | None:
    path = info_path(key, out_dir)
    if not path.is_file() or not video_path(key, out_dir).is_file():
        return None
    try:
        return {**json.loads(path.read_text(encoding="utf-8")), "key": key}
    except ValueError:
        return None


def save_info(info: dict, out_dir: Path | None = None) -> Path:
    path = info_path(info["key"], out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in info.items() if k != "key"}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def set_aside(key: str, out_dir: Path | None = None) -> Path | None:
    """Move a slot's folder to <key>.old-<time> so the slot can be made again; an uploaded video stays put."""
    folder = out_root(out_dir) / key
    if not folder.is_dir():
        return None
    info = load_info(key, out_dir)
    if info and (info.get("uploads") or {}).get("youtube"):
        raise ChannelError(f"{info['title']} is already on YouTube; it stays in {key}")
    target = folder.with_name(f"{key}.old-{time.strftime('%H%M%S')}")
    folder.rename(target)
    return target


def plan_view(settings, now: datetime | None = None, making: str | None = None) -> dict[str, Any]:
    """The Plan tab's data (GET /api/borderlinewhy/plan; the hub sends the same to the sending machine)."""
    from trendclip import assistant

    now = now or assistant.now(settings)
    days = plan_days(settings, now)
    return {"days": days, "slots": slots(), "lead_hours": lead_hours(), "timezone": settings.timezone,
            "require_approval": require_approval(), "privacy": upload_privacy(),
            "now": now.isoformat(timespec="minutes"), "making": making,
            "waiting": sum(1 for d in days for r in d["slots"] if r["state"] == "waiting")}


def videos(out_dir: Path | None = None, limit: int = 30) -> list[dict]:
    root = out_root(out_dir)
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(root.iterdir(), reverse=True):
        if folder.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}", folder.name) and (info := load_info(folder.name, out_dir)):
            out.append(info)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- the script


def queued_scripts(out_dir: Path | None = None) -> list[Path]:
    """Hand-checked scripts waiting in output/borderlinewhy/queue/ (oldest name first)."""
    folder = queue_dir(out_dir)
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def next_script(progress=lambda m: None, out_dir: Path | None = None, client=None, research=None, write=None) -> tuple[dict, str]:
    """(the script, where it came from): a queued script first, else the next topic with two readable sources
    whose script passes the checks. A topic that fails is marked rejected and the next is tried (TOPIC_TRIES);
    Wikipedia or Gemini being busy (TryLater) stops at once and leaves the topic on the list."""
    for path in queued_scripts(out_dir):
        try:
            script = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as err:
            logger.warning("Queued script %s is not JSON: %s", path.name, err)
            continue
        done = queue_dir(out_dir) / "used"
        done.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), done / path.name)
        return script, f"queue:{path.name}"
    research = research or bw_script.research
    write = write or bw_script.write
    root = out_root(out_dir)
    bw_topics.ensure(root, client)
    tried: set[str] = set()
    errors: list[str] = []
    for _ in range(TOPIC_TRIES):
        topic = bw_topics.next_topic(root, tried)
        if topic is None:
            break
        tried.add(topic.article)
        progress(f"Topic: {topic.question}")
        try:
            script = write(research(topic, progress), client, progress=progress)
        except bw_script.TryLater:
            raise
        except bw_script.ScriptError as err:
            logger.info("Borderline Why topic %s dropped: %s", topic.article, err)
            bw_topics.mark(root, topic.article, used=False, note=str(err)[:300])
            errors.append(f"{topic.article}: {err}")
            continue
        return script, f"topic:{topic.article}"
    raise ChannelError("No topic made a script: " + (" | ".join(errors) if errors else "the topic list is empty"))


# --------------------------------------------------------------------------- voice, music, render


def record_voice(text: str, out: Path, progress=lambda m: None) -> list[bw_render.Word]:
    """Kokoro (the hub's GPU voice) when it is installed, else the Edge voice."""
    from trendclip import kokoro_voice

    engine = setting("BW_TTS_ENGINE", "kokoro" if kokoro_voice.available() else "edge").lower()
    if engine == "kokoro":
        voice = setting("BW_TTS_VOICE", DEFAULT_VOICE)
        progress(f"Recording the narration (Kokoro {voice})")
        words = kokoro_voice.synthesize(text, out, voice, float(setting("BW_TTS_SPEED", "1.0")))
        kokoro_voice.unload()
    else:
        from trendclip import voiceover

        voice = setting("BW_EDGE_VOICE", "en-US-AndrewNeural")
        progress(f"Recording the narration ({voice})")
        words = voiceover.synthesize(text, out, voice, setting("BW_TTS_RATE", "+8%"))
    return [bw_render.Word(w.text, float(w.start), float(w.end)) for w in words]


def pick_music(settings, progress=lambda m: None):
    from trendclip import music

    source = setting("BW_MUSIC", "folder:dramatic")
    if source.lower() in ("none", "off", "0"):
        return None
    try:
        return music.pick(settings, source)
    except Exception as err:  # noqa: BLE001 - the voice alone is fine
        progress(f"No music: {err}")
        return None


def check_video(info: dict) -> list[str]:
    seconds = float(info.get("duration_seconds") or 0)
    problems = []
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        problems.append(f"{seconds:.0f} s long (wanted {MIN_SECONDS:.0f}-{MAX_SECONDS:.0f})")
    if len(info.get("sources") or []) < 2:
        problems.append("fewer than two sources")
    return problems


def handle() -> str:
    return channel_common.handle(DIR) or "@BorderlineWhy"


def make(settings, job: Job, progress=lambda f, m: None, *, out_dir: Path | None = None, client=None, script: dict | None = None,
         render=None, voice=None, announce: bool = False) -> dict:
    """The job's video: script, voice, maps, render, check. Raises ChannelError when the check fails (the video
    stays on disk, marked, and is not uploaded). `render` / `voice`: fakes for the tests."""
    work = out_root(out_dir) / job.key
    work.mkdir(parents=True, exist_ok=True)
    if script is None:
        progress(0.0, "Choosing the topic")
        script, origin = next_script(lambda m: progress(None, m), out_dir, client)
    else:
        origin = "given"
    script.setdefault("seed", random.randrange(1, 10_000))
    script.setdefault("look", setting("BW_LOOK", "satellite"))
    (work / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress(0.1, "Recording the narration")
    words = (voice or record_voice)(script["narration"], work / "voice.mp3", lambda m: progress(0.1, m))
    spec = bw_render.spec_from_dict(script, words)
    progress(0.15, "Placing the maps")
    if missing := bw_render.prepare(spec):
        raise ChannelError("Places not found on the map: " + ", ".join(missing))
    track = pick_music(settings, lambda m: progress(None, m))
    music_path = None
    if track is not None:
        from trendclip import music

        music_path = music.track_path(settings, track)
    (render or bw_render.render)(spec, work / "voice.mp3", work / "video.mp4", music_path,
                                 float(setting("BW_MUSIC_VOLUME", "0.10")), lambda f, m: progress(0.2 + 0.75 * (f or 0), m))
    info = {
        "key": job.key, "title": script["title"], "question": script.get("question", ""), "article": script.get("article", ""),
        "origin": origin, "description": bw_script.description(script, handle()),
        "hashtags": list(dict.fromkeys([*(script.get("hashtags") or []), "geography", "history", "maps"]))[:6],
        "sources": script.get("sources", []), "duration_seconds": round(spec.duration, 2),
        "words": bw_script.words(script["narration"]), "theme": spec.theme, "music": {"title": track.title} if track else None,
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        "job": {"day": job.day.isoformat(), "slot": job.slot, "post_at": job.post_at.isoformat(timespec="minutes")},
    }
    info["check"] = check_video(info)
    save_info(info, out_dir)
    article = origin.removeprefix("topic:") if origin.startswith("topic:") else script.get("article", "")
    if article:  # a queued script uses up its topic too, so the list never makes it twice
        bw_topics.mark(out_root(out_dir), article, used=True)
    if info["check"]:
        raise ChannelError(f"{info['title']} can't go out: " + "; ".join(info["check"]))
    if announce:
        announce_video(info, settings, out_dir)
    return info


# --------------------------------------------------------------------------- approve, veto, upload


def upload_request(info: dict, privacy: str | None = None) -> publish.YouTubeUploadRequest:
    return publish.YouTubeUploadRequest(
        title=info["title"][: publish.YT_TITLE_MAX], description=(info.get("description") or "")[:5000],
        tags=[t for t in info.get("hashtags", []) if t], privacy=privacy or upload_privacy(), category_id=CATEGORY, as_short=True)


def upload(key: str, privacy: str | None = None, progress=lambda f, m: None, youtube=None, out_dir: Path | None = None,
           settings=None) -> dict[str, Any]:
    info = load_info(key, out_dir)
    if info is None:
        raise publish.PublishError(f"No Borderline Why video {key}")
    if youtube is None and not connected():
        raise publish.PublishError("Connect the Borderline Why YouTube channel first (/bw connect)")
    record = publish.upload_file(video_path(key, out_dir), upload_request(info, privacy), True, progress,
                                 youtube or publish._youtube(TOKEN))
    info.setdefault("uploads", {})["youtube"] = record
    save_info(info, out_dir)
    progress(1.0, "Uploaded")
    announce_upload(info, record, settings)
    return record


def is_vetoed(info: dict) -> bool:
    return bool(info.get("veto"))


def is_approved(info: dict) -> bool:
    return bool(info.get("approved"))


def ready(info: dict) -> bool:
    """May go out: not vetoed, passed its check, and approved when approval is required."""
    return not is_vetoed(info) and not info.get("check") and (is_approved(info) or not require_approval())


def publish_if_allowed(key: str, progress=lambda f, m: None, out_dir: Path | None = None, youtube=None, settings=None) -> dict | None:
    """The upload at the slot's time: the record, or None when the video may not go out (yet)."""
    info = load_info(key, out_dir)
    if info is None or not ready(info) or not auto_post():
        return None
    if record := (info.get("uploads") or {}).get("youtube"):
        return record
    return upload(key, None, progress, youtube, out_dir, settings)


def approve(key: str, settings=None, out_dir: Path | None = None, now: datetime | None = None, youtube=None) -> dict[str, Any]:
    """Approve: it goes out at its slot, or at once when the slot has passed. {"title", "record", "post_at"}."""
    info = load_info(key, out_dir)
    if info is None:
        raise KeyError(key)
    info["approved"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    info.pop("veto", None)
    save_info(info, out_dir)
    post_at = datetime.fromisoformat(info["job"]["post_at"]) if info.get("job") else None
    record = None
    if post_at is not None and settings is not None and (now or assistant.now(settings)) >= post_at and auto_post():
        record = publish_if_allowed(key, out_dir=out_dir, youtube=youtube, settings=settings)
    return {"title": info["title"], "record": record, "post_at": post_at}


def veto(key: str, out_dir: Path | None = None, youtube=None) -> dict[str, Any]:
    """"Don't post this": before the upload it is cancelled; after it the video is made private."""
    info = load_info(key, out_dir)
    if info is None:
        raise KeyError(key)
    info["veto"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    info.pop("approved", None)
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


def video_line(info: dict) -> str:
    sources = ", ".join(s.get("title", "")[:40] for s in info.get("sources", [])[:3])
    return (f"🗺️ **{info.get('title', info.get('key', ''))}** · {info.get('duration_seconds', 0):.0f} s · {info.get('words', '?')} words"
            + (f" · 🎵 {info['music']['title']}" if info.get("music") else "") + (f"\nSources: {sources}" if sources else ""))


def video_note(info: dict) -> str:
    post_at = (info.get("job") or {}).get("post_at", "")[11:16]
    if info.get("check"):
        return "It failed its check and won't go out: " + "; ".join(info["check"])
    if require_approval():
        return f"Press **Approve** and it goes out at {post_at} as {upload_privacy()}; without it, nothing is posted."
    return f"It goes out by itself at {post_at} as {upload_privacy()}. Press **Don't post this** to stop it."


def announce_text(text: str, settings=None, components: list[dict[str, Any]] | None = None) -> str | None:
    try:
        rest = _rest(settings)
        return None if rest is None else rest.post(text, components=components)
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post in the Borderline Why channel failed: %s", err)
        return None


def announce_video(info: dict, settings=None, out_dir: Path | None = None) -> str | None:
    """Post the finished video (shrunk for Discord) with its buttons. Never raises."""
    try:
        rest = _rest(settings)
        if rest is None:
            return None
        path = video_path(info["key"], out_dir)
        small = assistant.fit_for_discord(path, path.parent / "discord" / path.name)
        b = channel_common.button
        components = channel_common.rows(b("bwok", info["key"], "✅ Approve", "success"), b("bwveto", info["key"], "🚫 Don't post this", "danger"),
                                         b("bwup", info["key"], "⬆️ Upload now", "primary"))
        return rest.post(video_line(info) + "\n" + video_note(info), (f"{info['key']}.mp4", small.read_bytes()), components)
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post for Borderline Why %s failed: %s", info.get("key"), err)
        return None


def announce_upload(info: dict, record: dict, settings=None) -> str | None:
    lb = channel_common.link_button
    return announce_text(f"⬆️ **{info.get('title', info.get('key', ''))}** is on YouTube ({record.get('privacy', '?')}): {record.get('url', '')}",
                         settings, channel_common.rows(lb("▶️ Watch", record.get("url", "")) if record.get("url") else None,
                                                       channel_common.button("bwveto", info["key"], "🚫 Make it private", "danger"),
                                                       lb("🎬 YouTube Studio", record.get("studio_url", "")) if record.get("studio_url") else None))


# --------------------------------------------------------------------------- YouTube login


def youtube_status() -> dict[str, Any]:
    return channel_common.youtube_status(DIR)


def start_login(redirect_uri: str) -> str:
    return channel_common.start_login(DIR, redirect_uri)


def logout() -> None:
    channel_common.logout(DIR)


def connected() -> bool:
    return channel_common.connected(DIR)
