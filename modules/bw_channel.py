"""The Borderline Why channel (@BorderlineWhy): its folder under channels/borderlinewhy/, its YouTube login, its
Discord channel, and its day: one video per BW_SLOTS time (default 14:30 and 23:00 in Settings.timezone).

BW_LEAD hours before the slot (default 8) the bot makes the video: the next topic (a hand-checked script from
output/borderlinewhy/queue/ first, else the topic list), its research and checked script, the voice, the maps,
the render. The video is posted in the channel's Discord channel with Don't post / Upload now and goes to YouTube
at the slot's time by itself, like the other channels. With BW_REQUIRE_APPROVAL=1 (default off since 2026-10-29)
it goes out only when approved (an Approve button too); approving later uploads it at once. A video outside MIN_SECONDS-MAX_SECONDS never goes out.

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

from modules import bw_render, bw_script, bw_series, bw_topics, channel_common
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
PANDA_VOICE = "af_bella"  # the mascot (BW_PANDA_VOICE), pitched up by BW_PANDA_PITCH
VOICE_RATE = 24000


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
    return _on("BW_REQUIRE_APPROVAL", "0")


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
    numbers = expected_numbers(videos(out_dir, limit=60), out_dir)
    days = []
    for n in range(-before, after + 1):
        day = now.date() + timedelta(days=n)
        rows = []
        keys = set()
        for job in jobs_for(settings, day):
            info = load_info(job.key, out_dir)
            keys.add(job.key)
            rows.append({**job.view(), "state": slot_state(job, info, now), "video": _plan_video(info, numbers, out_dir)})
        folder = out_root(out_dir)
        if folder.is_dir():
            for extra in sorted(folder.glob(f"{day.isoformat()}_[0-9][0-9][0-9][0-9]")):
                if extra.name in keys or (info := load_info(extra.name, out_dir)) is None:
                    continue
                slot = f"{extra.name[11:13]}:{extra.name[13:15]}"
                job = Job(day, slot, datetime.fromisoformat(info["job"]["post_at"]) - timedelta(hours=lead_hours())
                          if info.get("job") else now, datetime.fromisoformat(info["job"]["post_at"]) if info.get("job") else now)
                rows.append({**job.view(), "state": slot_state(job, info, now), "video": _plan_video(info, numbers, out_dir), "extra": True})
        rows.sort(key=lambda r: r["slot"])
        days.append({"day": day.isoformat(), "weekday": day.strftime("%A"), "today": day == now.date(), "slots": rows})
    return days


def _plan_video(info: dict | None, numbers: dict[str, int] | None = None, out_dir: Path | None = None) -> dict | None:
    if info is None:
        return None
    return {"key": info["key"], "title": display_title(info, out_dir, (numbers or {}).get(info["key"])), "duration_seconds": info.get("duration_seconds"),
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


def recent_series(out_dir: Path | None = None, limit: int = 10) -> list[str]:
    """The series of the last Shorts made, newest first ("" for one outside any series)."""
    return [series_of(v) for v in videos(out_dir, limit)]


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
    prefer = bw_series.due(recent_series(out_dir))
    for _ in range(TOPIC_TRIES):
        topic = bw_topics.next_topic(root, tried, prefer=prefer)
        if topic is None:
            break
        tried.add(topic.article)
        progress(f"Topic: {topic.question}")
        try:
            kwargs = {"drafts_dir": root / "drafts"} if write is bw_script.write else {}
            script = write(research(topic, progress), client, progress=progress, **kwargs)
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


def with_credits(description: str, spec: bw_render.Spec) -> str:
    """The description with the credits of the Wikimedia pictures the video shows (modules.bw_photos)."""
    from modules import bw_photos

    credits = bw_photos.credits([s.opts["_photo"] for s in spec.scenes if s.kind == "photo" and s.opts.get("_photo")])
    return f"{description}\n\n{credits}" if credits else description


def brand_on() -> bool:
    """The signature look (modules.bw_brand: the talking mascot, the marker hand, the whiteboard); BW_BRAND=off drops it."""
    return setting("BW_BRAND", "on").lower() not in ("off", "0", "no", "false")


def record_voice(text: str, out: Path, progress=lambda m: None, who: str = "", unload: bool = True) -> list[bw_render.Word]:
    """Kokoro (the hub's GPU voice) when it is installed, else the Edge voice. `who`: "" the narrator, "panda" the
    mascot (its own voice, made higher and quicker by record_script)."""
    from trendclip import kokoro_voice

    engine = setting("BW_TTS_ENGINE", "kokoro" if kokoro_voice.available() else "edge").lower()
    if engine == "kokoro":
        voice = setting("BW_PANDA_VOICE", PANDA_VOICE) if who else setting("BW_TTS_VOICE", DEFAULT_VOICE)
        progress(f"Recording the {'mascot' if who else 'narration'} (Kokoro {voice})")
        words = kokoro_voice.synthesize(text, out, voice, float(setting("BW_TTS_SPEED", "1.0")))
        if unload:
            kokoro_voice.unload()
    else:
        from trendclip import voiceover

        voice = setting("BW_PANDA_EDGE_VOICE", "en-US-AnaNeural") if who else setting("BW_EDGE_VOICE", "en-US-AndrewNeural")
        progress(f"Recording the {'mascot' if who else 'narration'} ({voice})")
        words = voiceover.synthesize(text, out, voice, setting("BW_TTS_RATE", "+8%"))
    return [bw_render.Word(w.text, float(w.start), float(w.end), who) for w in words]


def _pcm(path: Path, filters: str = "") -> bytes:
    """An audio file as mono 16-bit samples at VOICE_RATE, through ffmpeg `filters` when given."""
    import subprocess

    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path), *(["-af", filters] if filters else []),
           "-f", "s16le", "-ac", "1", "-ar", str(VOICE_RATE), "-"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def record_script(script: dict, out: Path, progress=lambda m: None) -> list[bw_render.Word]:
    """The narration, with the mascot's lines (a scene's ask / react, modules.bw_brand) in its own voice between the
    narrator's parts: one MP3 and its timed words, the mascot's marked who="panda"."""
    import subprocess

    from modules import bw_brand

    parts = bw_brand.dialogue(script)
    if all(not who for who, _ in parts):
        return record_voice(script["narration"], out, progress)
    pitch = float(setting("BW_PANDA_PITCH", "1.22"))
    speed = float(setting("BW_PANDA_SPEED", "1.08"))
    gap = b"\0\0" * int(VOICE_RATE * bw_brand.PANDA_GAP)
    pcm = bytearray()
    words: list[bw_render.Word] = []
    try:
        for k, (who, text) in enumerate(parts):
            tmp = out.with_name(f"{out.stem}.part{k}.mp3")
            got = record_voice(text, tmp, progress, who=who, unload=False)
            # the mascot's voice: pitched up by `pitch` (which also quickens it by `pitch`), then retimed to `speed`
            raw = _pcm(tmp, f"asetrate={VOICE_RATE * pitch:.0f},aresample={VOICE_RATE},atempo={speed / pitch:.4f}" if who else "")
            if who and pcm:
                pcm += gap
            scale = 1 / speed if who else 1.0
            offset = len(pcm) / 2 / VOICE_RATE
            words += [bw_render.Word(w.text, round(offset + w.start * scale, 3), round(offset + w.end * scale, 3), who) for w in got]
            pcm += raw
            if who:
                pcm += gap
            tmp.unlink(missing_ok=True)
            tmp.with_suffix(".words.json").unlink(missing_ok=True)
    finally:
        from trendclip import kokoro_voice

        kokoro_voice.unload()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ac", "1", "-ar", str(VOICE_RATE), "-i", "-",
                    "-c:a", "libmp3lame", "-b:a", "160k", str(out)], input=bytes(pcm), check=True)
    out.with_suffix(".words.json").write_text(json.dumps(
        [{"word": w.text, "start": w.start, "end": w.end, **({"who": w.who} if w.who else {})} for w in words], indent=1), encoding="utf-8")
    return words


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


def script_series(script: dict) -> str:
    """A script's series: what bw_script.finish wrote, else (an older queued script) judged from its topic."""
    if "series" in script:
        return bw_series.normalize(script["series"])
    return bw_series.classify(script.get("question") or script.get("title", ""), script.get("article", ""))


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
        script = dict(script)
        origin = script.pop("origin", "given")
    script.setdefault("seed", random.randrange(1, 10_000))
    script.setdefault("look", setting("BW_LOOK", "satellite"))
    script.setdefault("brand", brand_on())
    from modules import bw_brand

    script = bw_brand.add_outro(script)
    (work / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress(0.1, "Recording the narration")
    if voice:
        words = voice(script["narration"], work / "voice.mp3", lambda m: progress(0.1, m))
    else:
        words = record_script(script, work / "voice.mp3", lambda m: progress(0.1, m))
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
        "key": job.key, "title": bw_series.strip(script["title"]), "series": script_series(script), "question": script.get("question", ""), "article": script.get("article", ""),
        "origin": origin, "description": with_credits(bw_script.description(script, handle()), spec),
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


def series_of(info: dict) -> str:
    """A video's series (a video made before the series existed is judged from its topic)."""
    return script_series(info)


def episode(info: dict, out_dir: Path | None = None) -> dict | None:
    """The video's place in its series: {"series", "number", "next"}; "next" is True while the number is only the
    one it would get if it went out now (the number is given at the upload). None outside any series."""
    if done := info.get("episode"):
        return {**done, "next": False}
    series = series_of(info)
    if not series or (info.get("uploads") or {}).get("youtube"):
        return None
    held = bw_series.number_of(out_root(out_dir), series, info["key"])
    return {"series": series, "number": held or bw_series.peek(out_root(out_dir), series), "next": True}


def youtube_title(info: dict, out_dir: Path | None = None, number: int | None = None) -> str:
    """The title on YouTube: "<question> | Weird Borders #7" for a series episode, else the title."""
    if info.get("episode"):
        return info["title"]  # numbered at its upload
    ep = episode(info, out_dir)
    if ep is None:
        return info["title"]
    return bw_series.titled(info.get("base_title") or info["title"], ep["series"], number or ep["number"])


def expected_numbers(infos: list[dict], out_dir: Path | None = None) -> dict[str, int]:
    """key -> the number each series video not yet on YouTube should get, in the order of their slots (a video
    stopped or failing its check gets none): so the Plan shows #8, #9, #10 rather than #8 three times."""
    root = out_root(out_dir)
    pending = sorted((i for i in infos if series_of(i) and not i.get("episode") and not (i.get("uploads") or {}).get("youtube")
                      and not is_vetoed(i) and not i.get("check")),
                     key=lambda i: ((i.get("job") or {}).get("post_at", ""), i["key"]))
    out: dict[str, int] = {}
    after: dict[str, int] = {}
    for info in pending:
        series = series_of(info)
        held = bw_series.number_of(root, series, info["key"])
        if held is None:
            held = after.get(series) or bw_series.peek(root, series)
        out[info["key"]] = held
        after[series] = max(after.get(series, 0), held + 1)
    return out


def display_title(info: dict, out_dir: Path | None = None, number: int | None = None) -> str:
    """The title for Discord and the dashboard: numbered once uploaded, "(Weird Borders #7 next)" before
    (`number`: the expected one, from expected_numbers)."""
    title = info.get("title", info.get("key", ""))
    if info.get("episode") or (info.get("uploads") or {}).get("youtube") or not series_of(info):
        return title
    if number is None:
        number = expected_numbers([info], out_dir).get(info["key"])
    return f"{title} ({bw_series.label(series_of(info), number)} next)" if number else title


def upload_request(info: dict, privacy: str | None = None, out_dir: Path | None = None, number: int | None = None) -> publish.YouTubeUploadRequest:
    return publish.YouTubeUploadRequest(
        title=youtube_title(info, out_dir, number)[: publish.YT_TITLE_MAX], description=(info.get("description") or "")[:5000],
        tags=[t for t in info.get("hashtags", []) if t], privacy=privacy or upload_privacy(), category_id=CATEGORY, as_short=True)


def upload(key: str, privacy: str | None = None, progress=lambda f, m: None, youtube=None, out_dir: Path | None = None,
           settings=None) -> dict[str, Any]:
    """Upload the Short. A series episode takes its number now (bw_series: it never skips a number for a video that
    didn't go out), then the channel posts the "what next" comment for the owner to pin."""
    info = load_info(key, out_dir)
    if info is None:
        raise publish.PublishError(f"No Borderline Why video {key}")
    if youtube is None and not connected():
        raise publish.PublishError("Connect the Borderline Why YouTube channel first (/bw connect)")
    client = youtube or publish._youtube(TOKEN)
    series = "" if info.get("episode") else series_of(info)
    number = bw_series.reserve(out_root(out_dir), series, key) if series else None
    try:
        record = publish.upload_file(video_path(key, out_dir), upload_request(info, privacy, out_dir, number), True, progress, client)
    except BaseException:
        if number:
            bw_series.release(out_root(out_dir), series, key)
        raise
    if number:
        bw_series.confirm(out_root(out_dir), series, key, record.get("video_id", ""))
        info.update(base_title=info["title"], title=bw_series.titled(info["title"], series, number),
                    episode={"series": series, "number": number})
    info.setdefault("uploads", {})["youtube"] = record
    save_info(info, out_dir)
    progress(1.0, "Uploaded")
    comment(info, youtube, out_dir)  # its own client: it checks the login may comment first
    announce_upload(info, record, settings)
    return record


def can_comment() -> bool:
    return publish.has_scope(publish.COMMENT_SCOPE, TOKEN)


def comment(info: dict, youtube=None, out_dir: Path | None = None) -> dict | None:
    """Post bw_series.COMMENT under the uploaded video (once). The result goes in the upload record ("comment", or
    "comment_error" when it failed: the Discord message offers a retry). Never raises."""
    record = (info.get("uploads") or {}).get("youtube") or {}
    if not record.get("video_id") or record.get("comment"):
        return record.get("comment")
    error = ""
    if record.get("privacy") == "private":
        error = "comments only work once the video is public or unlisted"
    elif youtube is None and not can_comment():
        error = "reconnect the Borderline Why YouTube channel once to allow comments (/bw connect)"
    else:
        try:
            record["comment"] = bw_series.post_comment(youtube or publish._youtube(TOKEN), record["video_id"], bw_series.comment_text())
        except Exception as err:  # noqa: BLE001 - the video is up; only the comment failed
            error = publish._api_error_text(err).replace("YouTube upload failed", "YouTube refused the comment") \
                if err.__class__.__name__ == "HttpError" else str(err) or err.__class__.__name__
    if error:
        record["comment_error"] = error
    else:
        record.pop("comment_error", None)
    save_info(info, out_dir)
    return record.get("comment")


def comment_again(key: str, out_dir: Path | None = None, youtube=None) -> dict:
    """The retry from Discord: the comment record. Raises PublishError with the reason when it fails again."""
    info = load_info(key, out_dir)
    if info is None or not (info.get("uploads") or {}).get("youtube"):
        raise publish.PublishError("That Borderline Why video is not on YouTube")
    found = comment(info, youtube, out_dir)
    if found is None:
        raise publish.PublishError(info["uploads"]["youtube"].get("comment_error") or "the comment didn't go through")
    return found


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
    title = (load_info(key, out_dir) or info)["title"] if record else info["title"]  # numbered by the upload
    return {"title": title, "record": record, "post_at": post_at}


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
    return (f"🗺️ **{display_title(info)}** · {info.get('duration_seconds', 0):.0f} s · {info.get('words', '?')} words"
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
        buttons = [b("bwveto", info["key"], "🚫 Don't post this", "danger"), b("bwup", info["key"], "⬆️ Upload now", "primary")]
        if require_approval():
            buttons.insert(0, b("bwok", info["key"], "✅ Approve", "success"))
        components = channel_common.rows(*buttons)
        return rest.post(video_line(info) + "\n" + video_note(info), (f"{info['key']}.mp4", small.read_bytes()), components)
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post for Borderline Why %s failed: %s", info.get("key"), err)
        return None


def comment_line(record: dict) -> str:
    """The upload message's comment line: the pin reminder (the API can't pin), or why it didn't go through."""
    if found := record.get("comment"):
        return f"💬 Comment posted: “{found['text']}”. **Pin it** on YouTube (the comment's ⋮ → Pin); the API can't: <{found['url']}>"
    if error := record.get("comment_error"):
        return f"💬 The comment didn't go through: {error}"
    return ""


def announce_upload(info: dict, record: dict, settings=None) -> str | None:
    lb = channel_common.link_button
    b = channel_common.button
    text = f"⬆️ **{info.get('title', info.get('key', ''))}** is on YouTube ({record.get('privacy', '?')}): {record.get('url', '')}"
    if line := comment_line(record):
        text += "\n" + line
    return announce_text(text, settings, channel_common.rows(
        lb("▶️ Watch", record.get("url", "")) if record.get("url") else None,
        b("bwcm", info["key"], "💬 Post the comment", "primary") if record.get("comment_error") else None,
        b("bwveto", info["key"], "🚫 Make it private", "danger"),
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
