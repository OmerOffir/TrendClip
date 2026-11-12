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
MIN_SECONDS, MAX_SECONDS = 30.0, 60.0  # aimed at 45-55 s (bw_script.MAX_WORDS, SPOKEN_WORDS); longer ones lost half their viewers
SHORT_PACE = 1.12  # a Short's voice (narrator and mascot) this much quicker than a long video's (BW_SHORT_PACE; the owner asked for higher energy, 2026-11-05)
LOOP_HOLD = 1.4  # a Short without the outro runs on this long after its last word (a ding, the music fading out) and
# loops; 0.35 s felt cut off (the owner, 2026-11-05)
# an attempts Short (modules.bw_whatif, the war map) is a list of fast mini-stories: 70-90 s, like the 6M-view one
LONGER = {"attempts": (50.0, 95.0), "balls": (10.0, 60.0)}  # the talking flag balls answer one question fast (modules.bw_balls)
TOPIC_TRIES = 3  # topics tried in one job before giving up (a topic without two sources is skipped)
DEFAULT_VOICE = "am_michael"
PANDA_VOICE = "af_bella"  # the mascot (BW_PANDA_VOICE), pitched up by BW_PANDA_PITCH
VOICE_RATE = 24000


class ChannelError(RuntimeError):
    pass


class Stopped(ChannelError):
    """The owner pressed Stop while the video was being made (request_stop)."""


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
    """Jobs whose upload time has come (publish.EARLY_UPLOAD before the slot: private, scheduled for the slot; up to
    POST_WINDOW after it) and whose upload was not tried yet."""
    if not auto_post():
        return []
    at = at.astimezone(settings.tz)
    return [job for day in (at.date() - timedelta(days=1), at.date(), at.date() + timedelta(days=1)) for job in jobs_for(settings, day)
            if job.post_key not in sent and job.post_at - publish.EARLY_UPLOAD <= at < job.post_at + POST_WINDOW]


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
            rows.append({**job.view(), "state": slot_state(job, info, now), "video": _plan_video(info, numbers, out_dir),
                         "progress": None if info else progress_of(job.key, out_dir)})
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


WHATIF_EVERY = 3  # a what-if at least every 3rd Short: "What If Alaska Was Still Russian?" was watched 100% (2026-11-02)


def whatif_due(out_dir: Path | None = None) -> bool:
    """True when the last WHATIF_EVERY - 1 Shorts were not what-ifs and none waits in the queue. What-ifs come from
    the owner's own LLM (modules.bw_whatif), so the bot can only remind them."""
    if any("_whatif-" in f.name for f in queued_scripts(out_dir)):
        return False
    recent = videos(out_dir, WHATIF_EVERY - 1)
    return len(recent) == WHATIF_EVERY - 1 and not any(v.get("kind") == "whatif" for v in recent)


WHATIF_HINT = ("\n💡 A **What if** is due (they keep viewers best: the Alaska one was watched to the end). "
               "Write one in the dashboard's What if tab and it goes in the next slot.")


def next_choices(out_dir: Path | None = None, limit: int = 60) -> dict[str, Any]:
    """The dashboard's "Next video" picker: the queued scripts and the topics still on the list, in the order they
    would be made, with the owner's pick."""
    root = out_root(out_dir)
    options = []
    for path in queued_scripts(out_dir):
        try:
            title = json.loads(path.read_text(encoding="utf-8")).get("title") or path.stem
        except ValueError:
            continue
        options.append({"value": f"queue:{path.name}", "label": f"📥 {title}", "kind": "queue"})
    for t in bw_topics.remaining(root)[:limit]:
        series = t.episode_of
        options.append({"value": f"topic:{t.article}", "label": t.question + (f" (📺 {series})" if series else ""), "kind": "topic"})
    choice = bw_topics.picked(root)
    if choice and not any(o["value"] == choice for o in options):
        choice = ""
    return {"choice": choice, "options": options}


def remove_next(choice: str, out_dir: Path | None = None) -> None:
    """Take a planned video off the Plan: a topic is dropped from the list (adding it again brings it back), a
    queued script moves to queue/removed/. Either way it stops being the owner's pick."""
    root = out_root(out_dir)
    if choice.startswith("topic:"):
        article = choice[6:]
        if article not in {t.article for t in bw_topics.remaining(root)}:
            raise ValueError(f"{article!r} is not on the topic list")
        bw_topics.mark(root, article, used=False, note="removed from the Plan by the owner")
    elif choice.startswith("queue:"):
        path = queue_dir(out_dir) / Path(choice[6:]).name
        if not path.is_file():
            raise ValueError("That script is no longer in the queue")
        (path.parent / "removed").mkdir(exist_ok=True)
        path.rename(path.parent / "removed" / path.name)
        if bw_topics.picked(root) == choice:
            bw_topics.pick(root, "")
    else:
        raise ValueError(f"Can't remove {choice!r}")


def next_script(progress=lambda m: None, out_dir: Path | None = None, client=None, research=None, write=None,
                choice: str | None = None) -> tuple[dict, str]:
    """(the script, where it came from): a queued script first, else the next topic with two readable sources
    whose script passes the checks. A topic that fails is marked rejected and the next is tried (TOPIC_TRIES);
    Wikipedia or Gemini being busy (TryLater) stops at once and leaves the topic on the list."""
    root = out_root(out_dir)
    stored = bw_topics.picked(root)
    # `choice`: the video the Plan showed on the slot whose Make now was pressed; else the owner's pick (the
    # dashboard's "Next video"), which goes before the queue
    choice = choice if choice is not None else stored
    queue = queued_scripts(out_dir)
    if choice.startswith("queue:") and not any(p.name == choice[6:] for p in queue):
        if choice == stored:
            bw_topics.pick(root, "")  # the picked script left the queue some other way
    elif choice.startswith("queue:"):
        queue = sorted(queue, key=lambda p: p.name != choice[6:])
    elif choice.startswith("topic:") and any(t.article == choice[6:] for t in bw_topics.remaining(root)):
        queue = []
    for path in queue:
        try:
            script = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as err:
            logger.warning("Queued script %s is not JSON: %s", path.name, err)
            continue
        done = queue_dir(out_dir) / "used"
        done.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), done / path.name)
        if stored == f"queue:{path.name}":
            bw_topics.pick(root, "")
        return script, f"queue:{path.name}"
    research = research or bw_script.research
    write = write or bw_script.write
    bw_topics.ensure(root, client)
    tried: set[str] = set()
    errors: list[str] = []
    prefer = bw_series.due(recent_series(out_dir))
    first = choice[6:] if choice.startswith("topic:") else ""
    for _ in range(TOPIC_TRIES):
        topic = bw_topics.next_topic(root, tried, prefer=prefer, first=first)
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
    """The description with the credits the Wikimedia pictures it shows ask for (modules.bw_photos): only those
    whose licence wants one (CC BY, CC BY-SA), one short line each; public-domain paintings and photos need none."""
    from modules import bw_photos

    credits = bw_photos.credits([s.opts["_photo"] for s in spec.scenes if s.kind == "photo" and s.opts.get("_photo")]
                                + [s.opts["_painting"] for s in spec.scenes if s.kind == "stage" and s.opts.get("_painting")])
    return f"{description}\n{credits}" if credits else description


def outro_on() -> bool:
    """A Short's subscribe outro (the mascot's last line and the button); off by default since 2026-11-05 so a Short
    loops. BW_OUTRO=on brings it back. Long videos always keep theirs (their end screen)."""
    return _on("BW_OUTRO", "0")


def rewind_on() -> bool:
    """The trip back in time (modules.bw_era): a tape rewind into an old year, an old colour print till today, in the
    videos where it fits (bw_era.fits). BW_REWIND=on."""
    return _on("BW_REWIND", "0")


def moves_on() -> bool:
    """The special transitions at the story's big jumps (modules.bw_moves: page, tear, spyglass, ink); BW_MOVES=off
    drops them."""
    return _on("BW_MOVES")


def sfx_on() -> bool:
    """The code-made sound effects under the voice (modules.bw_sfx); BW_SFX=off drops them."""
    return _on("BW_SFX")


def brand_on() -> bool:
    """The signature look (modules.bw_brand: the talking mascot, the marker hand, the whiteboard); BW_BRAND=off drops it."""
    return setting("BW_BRAND", "on").lower() not in ("off", "0", "no", "false")


def record_voice(text: str, out: Path, progress=lambda m: None, who: str = "", unload: bool = True,
                 pace: float = 1.0, voice: str = "") -> list[bw_render.Word]:
    """Kokoro (the hub's GPU voice) when it is installed, else the Edge voice. `who`: "" the narrator, "panda" the
    mascot (its own voice, made higher and quicker by record_script). `pace` quickens it (a Short: SHORT_PACE).
    `voice`: a Kokoro voice for this part instead of the narrator's (a quarrel: each side speaks in its own)."""
    from trendclip import kokoro_voice

    engine = setting("BW_TTS_ENGINE", "kokoro" if kokoro_voice.available() else "edge").lower()
    if engine == "kokoro":
        voice = setting("BW_PANDA_VOICE", PANDA_VOICE) if who else (voice if kokoro_voice.is_voice(voice) else setting("BW_TTS_VOICE", DEFAULT_VOICE))
        progress(f"Recording the {'mascot' if who else 'narration'} (Kokoro {voice})")
        words = kokoro_voice.synthesize(text, out, voice, round(float(setting("BW_TTS_SPEED", "1.0")) * pace, 3))
        if unload:
            kokoro_voice.unload()
    else:
        from trendclip import voiceover

        voice = setting("BW_PANDA_EDGE_VOICE", "en-US-AnaNeural") if who else setting("BW_EDGE_VOICE", "en-US-AndrewNeural")
        progress(f"Recording the {'mascot' if who else 'narration'} ({voice})")
        rate = setting("BW_TTS_RATE", "+8%")
        if pace != 1.0:  # Edge takes a percentage: +8% at pace 1.12 is +21%
            rate = f"{round((1 + float(rate.strip().rstrip('%')) / 100) * pace * 100 - 100):+d}%"
        words = voiceover.synthesize(text, out, voice, rate)
    return [bw_render.Word(w.text, float(w.start), float(w.end), who) for w in words]


def _pcm(path: Path, filters: str = "") -> bytes:
    """An audio file as mono 16-bit samples at VOICE_RATE, through ffmpeg `filters` when given."""
    import subprocess

    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(path), *(["-af", filters] if filters else []),
           "-f", "s16le", "-ac", "1", "-ar", str(VOICE_RATE), "-"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def record_script(script: dict, out: Path, progress=lambda m: None, pace: float = 1.0) -> list[bw_render.Word]:
    """The narration, with the mascot's lines (a scene's ask / react, modules.bw_brand) in its own voice between the
    narrator's parts: one MP3 and its timed words, the mascot's marked who="panda". `pace` quickens both voices."""
    import subprocess

    from modules import bw_brand

    quarrel = any(str(s.get("voice") or "").strip() for s in script.get("scenes") or [])
    if quarrel:  # each scene's line in its speaker's voice (a scene's "voice"); the mascot's lines between them
        parts = []
        for s in script["scenes"]:
            for who, text, voice in (("panda", s.get("ask"), ""), ("", s.get("narration"), str(s.get("voice") or "")),
                                     ("panda", s.get("react"), "")):
                if " ".join(str(text or "").split()) and (not who or script.get("brand")):
                    parts.append((who, " ".join(str(text).split()), voice))
    else:
        parts = [(who, text, "") for who, text in bw_brand.dialogue(script)]
    if all(not who for who, _, _ in parts) and not quarrel:
        return record_voice(script["narration"], out, progress, pace=pace)
    pitch = float(setting("BW_PANDA_PITCH", "1.22"))
    speed = float(setting("BW_PANDA_SPEED", "1.08"))
    gap = b"\0\0" * int(VOICE_RATE * bw_brand.PANDA_GAP)
    pcm = bytearray()
    words: list[bw_render.Word] = []
    try:
        turn = b"\0\0" * int(VOICE_RATE * 0.12)  # a breath between two speakers
        for k, (who, text, voice) in enumerate(parts):
            tmp = out.with_name(f"{out.stem}.part{k}.mp3")
            got = record_voice(text, tmp, progress, who=who, unload=False, pace=pace, voice=voice)
            if quarrel and not who and pcm and not parts[k - 1][0]:
                pcm += turn
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


MUSIC_FOLDERS = {"": "curious", "whatif": "whatif", "long": "long"}  # music/<folder> per kind of video (BW_MUSIC=auto)
RECENT_MUSIC = 4  # a track used in the last few videos is picked again only when nothing else fits


def pick_music(settings, progress=lambda m: None, kind: str = "", seconds: float = 0.0, out_dir: Path | None = None):
    """The video's track: BW_MUSIC=auto (the default) takes music/curious for a Short, music/whatif for a what-if,
    music/long for a long video (music/curious when that folder is empty), preferring a track at least `seconds`
    long and not in the last RECENT_MUSIC videos. Any other BW_MUSIC is trendclip.music's source ("folder:dramatic")."""
    from trendclip import music

    source = setting("BW_MUSIC", "auto")
    if source.lower() in ("none", "off", "0"):
        return None
    try:
        if source.lower() != "auto":
            return music.pick(settings, source)
        tracks = []
        for folder in dict.fromkeys([MUSIC_FOLDERS.get(kind, "curious"), "curious"]):
            path = settings.music_library_dir / folder
            tracks = sorted(p for p in path.glob("*") if p.suffix.lower() in music.AUDIO_EXTENSIONS) if path.is_dir() else []
            if tracks:
                break
        if not tracks:
            raise music.MusicError("music/curious is empty")
        found = [music._local_track(settings, p, folder) for p in tracks]
        recent = {(v.get("music") or {}).get("title") for v in videos(out_dir, RECENT_MUSIC)}
        fits = [t for t in found if (t.duration_seconds or 0) >= seconds]
        fresh = [t for t in fits if t.title not in recent]
        return random.choice(fresh or fits or [t for t in found if t.title not in recent] or found)
    except Exception as err:  # noqa: BLE001 - the voice alone is fine
        progress(f"No music: {err}")
        return None


FIT_SECONDS = 58.5  # a Short whose voice runs longer is sped up to this (the check stops anything over MAX_SECONDS)
FIT_LONGER = {"attempts": 92.0}
MAX_SPEEDUP = 1.12  # beyond this the voice sounds rushed: the video stays long and its check stops it


def fit_voice(path: Path, words: list[bw_render.Word], duration: float, target: float = FIT_SECONDS) -> list[bw_render.Word]:
    """Speed the recorded voice up (same pitch, ffmpeg atempo) so the video ends by `target`; the words' times
    follow. At most MAX_SPEEDUP; returns the words unchanged when it can't help or ffmpeg fails."""
    import subprocess

    spoken = max((w.end for w in words), default=0.0)
    tail = duration - spoken  # the end card and the pause after the last word don't change
    if spoken <= 0 or duration <= target or target - tail <= 0:
        return words
    factor = min(MAX_SPEEDUP, spoken / (target - tail))
    tmp = path.with_name(path.stem + ".fit" + path.suffix)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-filter:a", f"atempo={factor:.4f}",
                        "-c:a", "libmp3lame", "-b:a", "160k", str(tmp)], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as err:
        logger.warning("Could not speed up %s: %s", path.name, err)
        tmp.unlink(missing_ok=True)
        return words
    tmp.replace(path)
    out = [bw_render.Word(w.text, round(w.start / factor, 3), round(w.end / factor, 3), w.who) for w in words]
    timing = path.with_suffix(".words.json")
    if timing.is_file():
        timing.write_text(json.dumps([{"word": w.text, "start": w.start, "end": w.end, **({"who": w.who} if w.who else {})}
                                      for w in out], indent=1), encoding="utf-8")
    logger.info("Borderline Why voice sped up x%.3f to fit %.0f s", factor, target)
    return out


def check_video(info: dict) -> list[str]:
    seconds = float(info.get("duration_seconds") or 0)
    problems = []
    low, high = LONGER.get(info.get("kind") or "", (MIN_SECONDS, MAX_SECONDS))
    if not low <= seconds <= high:
        problems.append(f"{seconds:.0f} s long (wanted {low:.0f}-{high:.0f})")
    if len(info.get("sources") or []) < 2 and info.get("kind") not in ("whatif", "attempts"):  # written by the owner's LLM (modules.bw_whatif)
        problems.append("fewer than two sources")
    return problems


def script_series(script: dict) -> str:
    """A script's series: what bw_script.finish wrote, else (an older queued script) judged from its topic."""
    if "series" in script:
        return bw_series.normalize(script["series"])
    return bw_series.classify(script.get("question") or script.get("title", ""), script.get("article", ""))


def handle() -> str:
    return channel_common.handle(DIR) or "@BorderlineWhy"


PROGRESS_STALE = timedelta(minutes=30)  # a progress.json not touched for this long is a make that died
STOP_FILE = "stop"  # <key>/stop: the make ends at its next step (Progress), wherever it runs (dashboard or bot)


class Progress:
    """A make's progress: passed on to the caller and kept in <key>/progress.json, so the Plan shows how far a video
    is wherever it is being made (the dashboard's Make now or the bot's own job). The fraction never goes back;
    the script steps, which can't know their length (Gemini's answers and fixes), creep up towards SCRIPT_SHARE."""

    SCRIPT_SHARE = 0.1

    def __init__(self, path: Path, progress=lambda f, m: None):
        self.path, self.progress = path, progress
        self.started = datetime.now().replace(microsecond=0).isoformat()
        self.id = f"{self.started}-{random.randrange(1 << 30):x}"  # which make a stop file is for
        self.fraction, self.steps = 0.0, 0

    def stopped(self) -> bool:
        try:
            return (self.path.parent / STOP_FILE).read_text(encoding="utf-8").strip() == self.id
        except OSError:
            return False

    def __call__(self, fraction: float | None, message: str) -> None:
        if self.stopped():  # the owner pressed Stop: end the make at this step
            raise Stopped("Stopped on the dashboard; nothing was made for this slot")
        if fraction is not None:
            self.fraction = max(self.fraction, fraction)
        self.progress(fraction, message)
        try:
            self.path.write_text(json.dumps({"id": self.id, "message": message, "fraction": round(self.fraction, 3), "started": self.started,
                                             "updated": datetime.now().replace(microsecond=0).isoformat()}), encoding="utf-8")
        except OSError:
            pass

    def step(self, message: str) -> None:
        """A script step (topic, sources, Gemini writing, checking, fixing)."""
        self.steps += 1
        self(self.SCRIPT_SHARE * (1 - 0.8 ** self.steps), message)

    def done(self) -> None:
        """The make ended: its progress and stop files go (not a newer make's for the same slot)."""
        try:
            if json.loads(self.path.read_text(encoding="utf-8")).get("id") == self.id:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        if self.stopped():
            (self.path.parent / STOP_FILE).unlink(missing_ok=True)


def progress_of(key: str, out_dir: Path | None = None, now: datetime | None = None) -> dict | None:
    """A video being made: {message, fraction, started, updated}, or None (none, or it stopped long ago)."""
    try:
        data = json.loads((out_root(out_dir) / key / "progress.json").read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(data["updated"])
    except (OSError, ValueError, KeyError):
        return None
    if (now or datetime.now()) - updated >= PROGRESS_STALE:
        return None
    return data


def request_stop(key: str, out_dir: Path | None = None) -> dict:
    """Stop the video being made for `key`. The slot is free at once (the Plan shows it open and it can be made
    again); the stopped make ends at its next step (a Gemini answer or a rendered frame may take a moment) and its
    work is dropped. Raises ChannelError when nothing is being made there."""
    progress = progress_of(key, out_dir)
    if progress is None:
        raise ChannelError("No video is being made for that slot")
    folder = out_root(out_dir) / key
    (folder / STOP_FILE).write_text(progress.get("id", ""), encoding="utf-8")
    (folder / "progress.json").unlink(missing_ok=True)
    return {"key": key, "stopped": True}


def make(settings, job: Job, progress=lambda f, m: None, *, out_dir: Path | None = None, client=None, script: dict | None = None,
         render=None, voice=None, announce: bool = False, choice: str | None = None) -> dict:
    """The job's video: script, voice, maps, render, check. Raises ChannelError when the check fails (the video
    stays on disk, marked, and is not uploaded). `render` / `voice`: fakes for the tests."""
    work = out_root(out_dir) / job.key
    work.mkdir(parents=True, exist_ok=True)
    tracker = Progress(work / "progress.json", progress)
    try:
        return _make(settings, job, tracker, work, out_dir=out_dir, client=client, script=script, render=render, voice=voice,
                     announce=announce, choice=choice)
    finally:
        tracker.done()


def _make(settings, job: Job, progress: Progress, work: Path, *, out_dir, client, script, render, voice, announce,
          choice: str | None = None) -> dict:
    if script is None:
        progress(0.0, "Choosing the topic")
        script, origin = next_script(progress.step, out_dir, client, choice=choice)
    else:
        script = dict(script)
        origin = script.pop("origin", "given")
    script.setdefault("seed", random.randrange(1, 10_000))
    script.setdefault("look", setting("BW_LOOK", "satellite"))
    script.setdefault("brand", brand_on())
    from modules import bw_balls, bw_brand

    balls = bw_balls.is_balls(script)  # the countries as talking flag balls: its own voices and its own drawing
    if balls:
        script = {**script, "kind": "balls", "hold": LOOP_HOLD,
                  "narration": " ".join(" ".join(str(s.get("narration") or "").split()) for s in script["scenes"])}
    elif outro_on():
        script = bw_brand.add_outro(script)
    else:  # no subscribe outro: the Short ends on its last line and loops (a third of the viewers left at the outro, 2026-11-05)
        script = {**script, "hold": LOOP_HOLD}
    (work / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress(0.1, "Recording the narration")
    if voice:
        words = voice(script["narration"], work / "voice.mp3", lambda m: progress(0.1, m))
    elif balls:
        words = bw_balls.record(script, work / "voice.mp3", lambda m: progress(0.1, m))
    else:
        words = record_script(script, work / "voice.mp3", lambda m: progress(0.1, m), pace=float(setting("BW_SHORT_PACE", str(SHORT_PACE))))
    spec = bw_render.spec_from_dict(script, words)
    fit = FIT_LONGER.get(script.get("kind") or "", FIT_SECONDS)
    if spec.duration > fit:
        progress(0.12, f"The voice runs {spec.duration:.0f} s: speeding it up a little to fit {fit:.0f} s")
        words = fit_voice(work / "voice.mp3", words, spec.duration, fit)
        spec = bw_render.spec_from_dict(script, words)
    spec.sfx = sfx_on()
    spec.moves = moves_on()
    progress(0.15, "Placing the maps")
    if balls:
        spec.cast = script.get("cast") or {}
        render = render or bw_balls.render
    elif missing := bw_render.prepare(spec):
        raise ChannelError("Places not found on the map: " + ", ".join(missing))
    if rewind_on() and not balls:  # the trip back in time, with taste: a real stretch in the past, spaced out (modules.bw_era)
        from modules import bw_era

        spec.rewind = bw_era.fits(spec, [bool(v.get("rewind")) for v in videos(out_dir, bw_era.SPACING)])
    track = pick_music(settings, lambda m: progress(None, m), script.get("kind", ""), spec.duration, out_dir)
    music_path = None
    if track is not None:
        from trendclip import music

        music_path = music.track_path(settings, track)
    (render or bw_render.render)(spec, work / "voice.mp3", work / "video.mp4", music_path,
                                 float(setting("BW_MUSIC_VOLUME", "0.10")), lambda f, m: progress(0.2 + 0.75 * (f or 0), m))
    info = {
        "key": job.key, "title": bw_series.strip(script["title"]), "series": script_series(script), "kind": script.get("kind", ""), "question": script.get("question", ""), "article": script.get("article", ""),
        "origin": origin, "description": with_credits(bw_script.description(script, handle()), spec),
        "hashtags": list(dict.fromkeys([*(script.get("hashtags") or []), "geography", "history", "maps"]))[:15],
        "sources": script.get("sources", []), "duration_seconds": round(spec.duration, 2),
        "words": bw_script.words(script["narration"]), "theme": spec.theme, "music": {"title": track.title} if track else None,
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        "job": {"day": job.day.isoformat(), "slot": job.slot, "post_at": job.post_at.isoformat(timespec="minutes")},
        "rewind": spec.rewind,
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


def upload_request(info: dict, privacy: str | None = None, out_dir: Path | None = None, number: int | None = None,
                   publish_at: datetime | None = None) -> publish.YouTubeUploadRequest:
    return publish.YouTubeUploadRequest(publish_at=publish_at,
        title=youtube_title(info, out_dir, number)[: publish.YT_TITLE_MAX], description=(info.get("description") or "")[:5000],
        tags=[t for t in info.get("hashtags", []) if t], privacy=privacy or upload_privacy(), category_id=CATEGORY, as_short=True)


def upload(key: str, privacy: str | None = None, progress=lambda f, m: None, youtube=None, out_dir: Path | None = None,
           settings=None, publish_at: datetime | None = None) -> dict[str, Any]:
    """Upload the Short. A series episode takes its number now (bw_series: it never skips a number for a video that
    didn't go out), then the channel posts the "what next" comment for the owner to pin. `publish_at`: uploaded
    private, YouTube makes it public then; the comment waits for that (after_publish)."""
    info = load_info(key, out_dir)
    if info is None:
        raise publish.PublishError(f"No Borderline Why video {key}")
    if youtube is None and not connected():
        raise publish.PublishError("Connect the Borderline Why YouTube channel first (/bw connect)")
    client = youtube or publish._youtube(TOKEN)
    thumb = thumbnail(key, out_dir)  # drawn before the upload, so the Short has it from its first minute
    series = "" if info.get("episode") else series_of(info)
    number = bw_series.reserve(out_root(out_dir), series, key) if series else None
    try:
        record = publish.upload_file(video_path(key, out_dir), upload_request(info, privacy, out_dir, number, publish_at), True, progress, client)
    except BaseException:
        if number:
            bw_series.release(out_root(out_dir), series, key)
        raise
    if number:
        bw_series.confirm(out_root(out_dir), series, key, record.get("video_id", ""))
        info.update(base_title=info["title"], title=bw_series.titled(info["title"], series, number),
                    episode={"series": series, "number": number})
    progress(0.99, "Setting the thumbnail")
    record["thumbnail"] = set_thumbnail(client, record["video_id"], thumb) if thumb else "not set: it couldn't be drawn"
    info.setdefault("uploads", {})["youtube"] = record
    save_info(info, out_dir)
    if thumb and record["thumbnail"] == "set":  # again once YouTube has processed the video, else it picks its own frame
        publish.keep_thumbnail((lambda: youtube) if youtube is not None else (lambda: publish._youtube(TOKEN)), record["video_id"], thumb,
                               lambda status: note_thumbnail(key, status, out_dir))
    progress(1.0, "Uploaded")
    if not publish.later(out_root(out_dir), key, record):  # a scheduled video can't take a comment until it is public
        comment(info, youtube, out_dir)  # its own client: it checks the login may comment first
    announce_upload(info, record, settings)
    return record


def after_publish(key: str, out_dir: Path | None = None, youtube=None) -> bool | None:
    """A scheduled upload's publish time has come: True once it is public (the comment is posted now), False while
    YouTube hasn't made it public yet (or can't say), None when the video is gone or vetoed (nothing left to do)."""
    info = load_info(key, out_dir)
    record = ((info or {}).get("uploads") or {}).get("youtube") or {}
    if not record.get("video_id") or is_vetoed(info):
        return None
    if not publish.is_public(youtube or publish._youtube(TOKEN), record["video_id"]):
        return False
    record["privacy"] = "public"
    save_info(info, out_dir)
    comment(info, youtube, out_dir)
    return True


def thumbnail(key: str, out_dir: Path | None = None) -> Path | None:
    """The video's thumbnail (the dashboard's, modules.thumbs: drawn now when missing), or None when it can't be drawn."""
    from modules import thumbs

    root = out_root(out_dir)
    if root.name != CHANNEL:  # thumbs draws only inside output/borderlinewhy/: elsewhere (the tests) a ready one or none
        made = root / key / "thumbnail.jpg"
        return made if made.is_file() else None
    try:
        return thumbs.ensure(CHANNEL, key, root.parent)
    except Exception:  # noqa: BLE001 - the video goes out without it
        logger.warning("Borderline Why thumbnail for %s couldn't be drawn", key, exc_info=True)
        return None


def set_thumbnail(client, video_id: str, path: Path) -> str:
    """Give the uploaded Short its thumbnail: "set", or why YouTube refused (the channel needs phone verification)."""
    return publish.set_thumbnail(client, video_id, path)


def note_thumbnail(key: str, status: str, out_dir: Path | None = None) -> None:
    """How the thumbnail's second setting went (publish.keep_thumbnail), into the upload record."""
    info = load_info(key, out_dir)
    record = ((info or {}).get("uploads") or {}).get("youtube")
    if info and record:
        record["thumbnail"] = f"{status} after processing"
        save_info(info, out_dir)
    if out_dir is None:  # the real channel: a miss is an alert in its Discord channel (trendclip.doctor)
        from trendclip import doctor

        doctor.alert_thumbnail("bw", key, status, title=(info or {}).get("title", ""))


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
            text = bw_series.comment_text(info.get("series") or "", info.get("kind") or "")
            record["comment"] = bw_series.post_comment(youtube or publish._youtube(TOKEN), record["video_id"], text)
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
    post_at = datetime.fromisoformat(info["job"]["post_at"]) if (info.get("job") or {}).get("post_at") else None
    return upload(key, None, progress, youtube, out_dir, settings, publish.scheduled(post_at))


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
    if post_at is not None and settings is not None and (now or assistant.now(settings)) >= post_at - publish.EARLY_UPLOAD and auto_post():
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
    if record.get("video_id") and (record.get("privacy") != "private" or record.get("scheduled_for")):
        from googleapiclient.errors import HttpError

        youtube = youtube or publish._youtube(TOKEN)
        try:
            youtube.videos().update(part="status", body={"id": record["video_id"], "status": {"privacyStatus": "private"}}).execute()
        except HttpError as err:
            save_info(info, out_dir)
            raise publish.PublishError(publish._api_error_text(err).replace("YouTube upload failed", "YouTube refused the change")) from err
        record["privacy"] = "private"
        record.pop("scheduled_for", None)  # the schedule is gone too
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


def when_text(post_at: str | datetime, now: datetime | None = None) -> str:
    """A slot's time as the messages say it: "today at 14:30", "tomorrow at 14:30", "on Tue 3 Nov at 14:30"
    (a video is often made days before its slot, so the time alone reads as today's)."""
    if not post_at:
        return "its slot"
    at = datetime.fromisoformat(post_at) if isinstance(post_at, str) else post_at
    today = (now or datetime.now(at.tzinfo)).astimezone(at.tzinfo).date() if at.tzinfo else (now or datetime.now()).date()
    days = (at.date() - today).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else f"on {at:%a} {at.day} {at:%b}"
    return f"{day} at {at:%H:%M}"


def video_note(info: dict) -> str:
    post_at = when_text((info.get("job") or {}).get("post_at", ""))
    if info.get("check"):
        return "It failed its check and won't go out: " + "; ".join(info["check"])
    if require_approval():
        return f"Press **Approve** and it goes out {post_at} as {upload_privacy()}; without it, nothing is posted."
    return f"It goes out by itself {post_at} as {upload_privacy()}. Press **Don't post this** to stop it."


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
        hint = WHATIF_HINT if whatif_due(out_dir) else ""
        return rest.post(video_line(info) + "\n" + video_note(info) + hint, (f"{info['key']}.mp4", small.read_bytes()), components)
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
    text = f"⬆️ **{info.get('title', info.get('key', ''))}** is on YouTube ({publish.privacy_note(record)}): {record.get('url', '')}"
    if line := comment_line(record):
        text += "\n" + line
    if str(record.get("thumbnail") or "").startswith("not set"):
        text += "\n🖼️ The thumbnail didn't go up: set it in YouTube Studio (" + record["thumbnail"].removeprefix("not set: ")[:120] + ")"
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
