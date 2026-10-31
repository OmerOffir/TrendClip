"""The Price Tag Why channel (@PriceTagWhy): whiteboard answers to the money questions everyone wonders about, drawn
by Borderline Why's renderer in its own colours (modules.pw_render). Brief: channel-brief-pricetag-why.md.

Its day: one video per PW_SLOTS time (default 19:00 in Settings.timezone, noon in New York). PW_LEAD hours before the
slot (default 6) the bot makes it from the next hand-checked script in the queue (content/pricetagwhy/scripts/ in git,
then output/pricetagwhy/queue/ on the hub; each script is made once, oldest name first) and shows it on the dashboard
and in the channel's Discord channel. With PW_REQUIRE_APPROVAL=1 (the default for this new channel) it goes to YouTube
at the slot only after Approve; approving after the slot uploads it at once. A slot with no script left gets no video.

    python -m modules.pw_channel make content/pricetagwhy/scripts/001-arizona-99c.json [--key sample-arizona]
    python -m modules.pw_channel check            # the rule check of every queued script

A video is drawn in a process of its own (pw_render.use_style changes module-wide colours); make_slot starts one.
Files: output/pricetagwhy/<key>/ (script.json, voice.mp3, video.mp4, info.json); a slot's key is <date>_<HHMM>.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date as Day, datetime, timedelta
from pathlib import Path
from typing import Any

from modules import bw_render, channel_common
from modules.scene_images import setting
from trendclip.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

CHANNEL = "pricetagwhy"
NAME = "Price Tag Why"
DIR = channel_common.channel_dir(CHANNEL)
TOKEN = channel_common.token_path(CHANNEL)
OUTPUT_DIR = PROJECT_ROOT / "output" / CHANNEL
SCRIPTS_DIR = PROJECT_ROOT / "content" / CHANNEL / "scripts"  # hand-checked scripts, kept in git
CATEGORY = "27"  # Education
MIN_SECONDS, MAX_SECONDS = 30.0, 60.0  # the brief asks 35-50 s
VOICE = "af_heart"  # Kokoro-82M (Apache 2.0, commercial use allowed): warm and curious, unlike Borderline Why's voices
MUSIC = "funny"  # a music/ folder; PW_MUSIC=none for the voice alone
DISCLAIMER = "Not financial advice, just the fascinating reasons behind the prices."
HASHTAGS = ("money", "economics", "pricetagwhy", "shorts")
DEFAULT_SLOTS = "19:00"  # one a day (PW_SLOTS, in Settings.timezone)
DEFAULT_LEAD = 6  # hours before the slot the video is made (PW_LEAD)
POST_WINDOW = timedelta(hours=6)  # a slot's upload missed by more than this (bot was off) waits for a button
MAKE_TIMEOUT = 1800  # seconds a make process may take
SLOT_KEY = re.compile(r"\d{4}-\d{2}-\d{2}_\d{4}")


class ChannelError(RuntimeError):
    pass


def handle() -> str:
    return channel_common.handle(DIR) or "@PriceTagWhy"


def out_root(out_dir: Path | None = None) -> Path:
    return out_dir or OUTPUT_DIR


# --------------------------------------------------------------------------- settings


def slots() -> list[str]:
    found = sorted({s.strip() for s in setting("PW_SLOTS", DEFAULT_SLOTS).split(",") if re.fullmatch(r"\s*([01]\d|2[0-3]):[0-5]\d\s*", s)})
    return found or DEFAULT_SLOTS.split(",")


def lead_hours() -> float:
    try:
        value = float(setting("PW_LEAD", str(DEFAULT_LEAD)))
    except ValueError:
        return DEFAULT_LEAD
    return value if 0 <= value <= 20 else DEFAULT_LEAD


def _on(name: str, default: str = "1") -> bool:
    return setting(name, default).strip().lower() not in ("0", "false", "off", "no", "none")


def auto_make() -> bool:
    return _on("PW_AUTO_MAKE")


def auto_post() -> bool:
    return _on("PW_AUTO_POST")


def require_approval() -> bool:
    return _on("PW_REQUIRE_APPROVAL", "1")


def hold_until() -> Day | None:
    """PW_HOLD_UNTIL (YYYY-MM-DD): before that day nothing is made for a slot and nothing is uploaded (2026-10-31: the
    YouTube channel's new name can only be set on 2026-11-09). Samples can still be made from the tab."""
    try:
        return Day.fromisoformat(setting("PW_HOLD_UNTIL", "").strip())
    except ValueError:
        return None


def hold_text() -> str:
    found = hold_until()
    return found.isoformat() if found else ""


def on_hold(day: Day) -> bool:
    found = hold_until()
    return found is not None and day < found


def upload_privacy() -> str:
    value = setting("PW_UPLOAD_PRIVACY", "public").strip().lower()
    return value if value in ("private", "unlisted", "public") else "public"


# --------------------------------------------------------------------------- scripts


def narration(script: dict) -> str:
    return " ".join(" ".join(str(s.get("narration") or "").split()) for s in script.get("scenes") or [] if s.get("narration"))


def words(text: str) -> int:
    return len(re.findall(r"[\w'¢$%.,]+", text))


def check_script(script: dict) -> list[str]:
    """The brief's rules a script must meet before it is drawn."""
    problems = []
    scenes = script.get("scenes") or []
    if not scenes or scenes[0].get("kind") != "tag":
        problems.append("the first scene must be the price tag (kind tag)")
    if not scenes or scenes[-1].get("kind") != "follow":
        problems.append("the last scene must be the follow line (kind follow)")
    sources = script.get("sources") or []
    if len(sources) < 2:
        problems.append("fewer than two sources")
    for c in script.get("claims") or []:
        if len(set(c.get("sources") or [])) < 2:
            problems.append(f"claim checked in fewer than two sources: {c.get('claim', '')[:60]}")
        if any(not 1 <= n <= len(sources) for n in c.get("sources") or []):
            problems.append(f"claim cites a source that isn't listed: {c.get('claim', '')[:60]}")
    if not script.get("claims"):
        problems.append("no claims listed with their sources")
    advice = re.compile(r"\b(you should (buy|invest|sell)|buy this|invest in|financial advice)\b", re.I)
    if advice.search(narration(script)):
        problems.append("the narration gives advice (explain, never advise)")
    return problems


def queue_dir(out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / "queue"


def script_files(out_dir: Path | None = None, scripts_dir: Path | None = None) -> list[Path]:
    """Every script there is: the ones in git first, then the hub's queue, each by name."""
    found = sorted((scripts_dir or SCRIPTS_DIR).glob("*.json"))
    if queue_dir(out_dir).is_dir():
        found += sorted(queue_dir(out_dir).glob("*.json"))
    return found


def used_scripts(out_dir: Path | None = None) -> set[str]:
    """Script file names a slot's video was made from (a slot set aside gives its script back)."""
    root = out_root(out_dir)
    if not root.is_dir():
        return set()
    used = set()
    for path in root.glob("*/info.json"):
        if SLOT_KEY.fullmatch(path.parent.name):
            try:
                name = json.loads(path.read_text(encoding="utf-8")).get("script_file")
            except (OSError, ValueError):
                continue
            if name:
                used.add(name)
    return used


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def queued(out_dir: Path | None = None, scripts_dir: Path | None = None) -> list[Path]:
    """The scripts the next slots take, in order: not made yet and passing the rule check."""
    used = used_scripts(out_dir)
    return [p for p in script_files(out_dir, scripts_dir)
            if p.name not in used and (s := _read(p)) is not None and not check_script(s)]


def scripts(out_dir: Path | None = None, scripts_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every script with its rule check and whether a slot's video was made from it."""
    used = used_scripts(out_dir)
    out = []
    for path in script_files(out_dir, scripts_dir):
        script = _read(path)
        if script is None:
            out.append({"file": path.name, "title": path.stem, "question": "", "words": 0, "sources": [], "claims": [],
                        "check": ["not valid JSON"], "used": False})
            continue
        out.append({"file": path.name, "title": script.get("title", ""), "question": script.get("question", ""),
                    "words": words(narration(script)), "sources": script.get("sources", []), "claims": script.get("claims", []),
                    "check": check_script(script), "used": path.name in used})
    return out


def find_script(name: str, out_dir: Path | None = None, scripts_dir: Path | None = None) -> Path | None:
    return next((p for p in script_files(out_dir, scripts_dir) if p.name == name), None)


def description(script: dict) -> str:
    """The YouTube description: the question, the short answer, the sources, the credits, the disclaimer, hashtags."""
    tags = list(dict.fromkeys([*(script.get("hashtags") or []), *HASHTAGS]))[:7]
    lines = [script.get("question", ""), "", script.get("description", ""), "", "Sources:"]
    lines += [f"- {s['title']}: {s['url']}" for s in script.get("sources", [])]
    lines += ["", "Whiteboard animation drawn by code. Icons: Noto Emoji by Google (Apache 2.0; animated Noto Emoji, "
              "CC BY 4.0). No AI-generated images.", "", DISCLAIMER, "", f"A new 'why' every day: {handle()}",
              "", " ".join(f"#{t}" for t in tags)]
    return "\n".join(lines).strip()


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
        return f"{self.day.isoformat()} pw {self.slot} make"  # SentLog keys start with the date

    @property
    def post_key(self) -> str:
        return f"{self.day.isoformat()} pw {self.slot} post"

    def view(self) -> dict[str, Any]:
        return {"day": self.day.isoformat(), "slot": self.slot, "start": self.start.isoformat(timespec="minutes"),
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
    return [job for day in (at.date(), at.date() + timedelta(days=1)) for job in jobs_for(settings, day)
            if job.make_key not in sent and job.start <= at < job.post_at and load_info(job.key, out_dir) is None
            and not on_hold(job.day)]


def due_posts(settings, at: datetime, sent: set[str]) -> list[Job]:
    """Jobs whose slot has come (within POST_WINDOW) and whose upload was not tried yet."""
    if not auto_post():
        return []
    at = at.astimezone(settings.tz)
    return [job for day in (at.date() - timedelta(days=1), at.date()) for job in jobs_for(settings, day)
            if job.post_key not in sent and job.post_at <= at < job.post_at + POST_WINDOW and not on_hold(job.day)]


def slot_state(job: Job, info: dict | None, now: datetime, script: Path | None = None) -> str:
    """posted | blocked | vetoed | approved | waiting (for Approve) | due (being made) | missed (no video, slot passed) |
    later (made at job.start from `script`) | no_script (nothing left in the queue for it) | held (PW_HOLD_UNTIL)."""
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
    if on_hold(job.day):
        return "held"
    if script is None:
        return "no_script"
    return "due" if now >= job.start else "later"


def plan_days(settings, now: datetime, before: int = 1, after: int = 6, out_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every slot from `before` days back to `after` days ahead: its video, or the queued script it will be made from."""
    now = now.astimezone(settings.tz)
    left = list(queued(out_dir))
    days = []
    for n in range(-before, after + 1):
        day = now.date() + timedelta(days=n)
        rows = []
        for job in jobs_for(settings, day):
            info = load_info(job.key, out_dir)
            script = left.pop(0) if info is None and now < job.post_at and not on_hold(day) and left else None
            rows.append({**job.view(), "state": slot_state(job, info, now, script), "video": _plan_video(info),
                         "script": {"file": script.name, "title": (_read(script) or {}).get("title", "")} if script else None})
        days.append({"day": day.isoformat(), "weekday": day.strftime("%A"), "today": day == now.date(), "slots": rows})
    return days


def _plan_video(info: dict | None) -> dict | None:
    if info is None:
        return None
    return {"key": info["key"], "title": info.get("title", ""), "duration_seconds": info.get("duration_seconds"),
            "check": info.get("check") or [], "youtube": (info.get("uploads") or {}).get("youtube"),
            "script_file": info.get("script_file", ""), "created_at": info.get("created_at", "")}


def plan_view(settings, now: datetime | None = None) -> dict[str, Any]:
    from trendclip import assistant

    now = now or assistant.now(settings)
    days = plan_days(settings, now)
    return {"days": days, "slots": slots(), "lead_hours": lead_hours(), "timezone": settings.timezone,
            "require_approval": require_approval(), "privacy": upload_privacy(), "auto_make": auto_make(),
            "hold_until": hold_text(),
            "now": now.isoformat(timespec="minutes"), "queued": len(queued()),
            "waiting": sum(1 for d in days for r in d["slots"] if r["state"] == "waiting")}


# --------------------------------------------------------------------------- files


def info_path(key: str, out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / key / "info.json"


def video_file(key: str, out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / key / "video.mp4"


def load_info(key: str, out_dir: Path | None = None) -> dict | None:
    if not re.fullmatch(r"[\w.-]+", key or ""):
        return None
    path = info_path(key, out_dir)
    if not path.is_file() or not video_file(key, out_dir).is_file():
        return None
    data = _read(path)
    return None if data is None else {**data, "key": key}


def save_info(info: dict, out_dir: Path | None = None) -> Path:
    path = info_path(info["key"], out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in info.items() if k != "key"}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def set_aside(key: str, out_dir: Path | None = None) -> Path | None:
    """Move a slot's folder to <key>.old-<time> so the slot can be made again (its script goes back to the queue);
    an uploaded video stays put."""
    folder = out_root(out_dir) / key
    if not folder.is_dir():
        return None
    info = load_info(key, out_dir)
    if info and (info.get("uploads") or {}).get("youtube"):
        raise ChannelError(f"{info['title']} is already on YouTube; it stays in {key}")
    target = folder.with_name(f"{key}.old-{time.strftime('%H%M%S')}")
    folder.rename(target)
    return target


def videos(out_dir: Path | None = None) -> list[dict[str, Any]]:
    root = out_root(out_dir)
    found = []
    for path in root.glob("*/info.json"):
        if ".old-" in path.parent.name:
            continue
        info = _read(path)
        if info is None:
            continue
        info["key"] = path.parent.name
        info["has_video"] = (path.parent / "video.mp4").is_file()
        info["state"] = video_state(info)
        found.append(info)
    return sorted(found, key=lambda v: v.get("created_at", ""), reverse=True)


def video_state(info: dict) -> str:
    """posted | blocked | vetoed | approved | waiting | sample (made by hand, not for a slot)."""
    if (info.get("uploads") or {}).get("youtube"):
        return "posted"
    if info.get("check"):
        return "blocked"
    if is_vetoed(info):
        return "vetoed"
    if not info.get("job"):
        return "sample"
    return "approved" if is_approved(info) or not require_approval() else "waiting"


def video_path(key: str, out_dir: Path | None = None) -> Path | None:
    if not re.fullmatch(r"[\w.-]+", key or ""):
        return None
    path = video_file(key, out_dir)
    return path if path.is_file() else None


# --------------------------------------------------------------------------- making a video


def record(text: str, out: Path, progress=lambda m: None) -> list[bw_render.Word]:
    """The narration in the channel's voice: Kokoro (the hub's) when installed, else an Edge voice."""
    from trendclip import kokoro_voice

    if kokoro_voice.available() and setting("PW_TTS_ENGINE", "kokoro").lower() == "kokoro":
        voice = setting("PW_TTS_VOICE", VOICE)
        progress(f"Recording the narration (Kokoro {voice})")
        got = kokoro_voice.synthesize(text, out, voice, float(setting("PW_TTS_SPEED", "1.0")))
        kokoro_voice.unload()
    else:
        from trendclip import voiceover

        voice = setting("PW_EDGE_VOICE", "en-US-EmmaNeural")
        progress(f"Recording the narration ({voice})")
        got = voiceover.synthesize(text, out, voice, setting("PW_TTS_RATE", "+5%"))
    return [bw_render.Word(w.text, float(w.start), float(w.end)) for w in got]


def pick_music(seed: int) -> Path | None:
    folder = setting("PW_MUSIC", MUSIC)
    if folder.lower() in ("none", "off", "0"):
        return None
    tracks = sorted((PROJECT_ROOT / "music" / folder).glob("*.mp3"))
    return random.Random(seed).choice(tracks) if tracks else None


def make(script: dict, key: str, out_dir: Path | None = None, progress=lambda f, m: None, job: dict | None = None,
         script_file: str = "") -> dict:
    """Draw a script into output/pricetagwhy/<key>/ (in this process: call it from `python -m modules.pw_channel`).
    `job`: the slot it is for ({day, slot, post_at}); `script_file`: the queued script it came from (used up)."""
    from modules import bw_brand, pw_render

    if problems := check_script(script):
        raise ChannelError("The script breaks the rules: " + "; ".join(problems))
    pw_render.use_style()
    work = out_root(out_dir) / key
    work.mkdir(parents=True, exist_ok=True)
    script = {**script, "narration": narration(script), "look": "satellite", "brand": True}
    script.setdefault("seed", random.randrange(1, 10_000))
    (work / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress(0.05, "Recording the narration")
    spoken = record(script["narration"], work / "voice.mp3", lambda m: progress(None, m))
    spec = bw_render.spec_from_dict(script, spoken)
    spec.hold = bw_brand.END_HOLD["short"]  # the end card stays a moment after the follow line
    if missing := bw_render.prepare(spec):
        raise ChannelError("Places not found on the map: " + ", ".join(missing))
    music = pick_music(int(script["seed"]))
    pw_render.render(spec, work / "voice.mp3", work / "video.mp4", music, float(setting("PW_MUSIC_VOLUME", "0.07")),
                     lambda f, m: progress(0.1 + 0.85 * (f or 0), m), handle=handle())
    info = {
        "key": key, "title": script["title"], "question": script.get("question", ""), "description": description(script),
        "hashtags": list(dict.fromkeys([*(script.get("hashtags") or []), *HASHTAGS]))[:6],
        "sources": script.get("sources", []), "claims": script.get("claims", []), "duration_seconds": round(spec.duration, 2),
        "words": words(script["narration"]), "voice": setting("PW_TTS_VOICE", VOICE), "music": music.stem if music else None,
        "thumb_at": round(max(0.5, spec.scenes[0].end - 0.2), 2),  # the opening, fully drawn (modules.thumbs)
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        **({"job": job} if job else {}), **({"script_file": script_file} if script_file else {}),
    }
    seconds = info["duration_seconds"]
    info["check"] = [] if MIN_SECONDS <= seconds <= MAX_SECONDS else [f"{seconds:.0f} s long (wanted {MIN_SECONDS:.0f}-{MAX_SECONDS:.0f})"]
    save_info(info, out_dir)
    progress(1.0, "Video ready")
    return info


def _make_cmd(script_path: Path, key: str, out_dir: Path | None, job: Job | None) -> list[str]:
    cmd = [sys.executable, "-m", "modules.pw_channel", "make", str(script_path), "--key", key]
    if job is not None:
        cmd += ["--day", job.day.isoformat(), "--slot", job.slot, "--post-at", job.post_at.isoformat(timespec="minutes"),
                "--script-file", script_path.name]
    return cmd + (["--out", str(out_dir)] if out_dir else [])


def start_make(script_path: Path, key: str, out_dir: Path | None = None, job: Job | None = None) -> subprocess.Popen:
    """make() in a process of its own (the dashboard's way in), its log in output/pricetagwhy/<key>.log."""
    root = out_root(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    log = (root / f"{key}.log").open("w")
    return subprocess.Popen(_make_cmd(script_path, key, out_dir, job), cwd=str(PROJECT_ROOT), stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)


def make_slot(settings, job: Job, out_dir: Path | None = None, run=subprocess.run) -> dict:
    """The slot's video from the next queued script, drawn in its own process (blocks until it is done). Raises
    ChannelError when no script is left or the make fails; the info (with "check" when it can't go out) otherwise."""
    left = queued(out_dir)
    if not left:
        raise ChannelError("No script left in the queue: add one to content/pricetagwhy/scripts/")
    root = out_root(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / f"{job.key}.log").open("w") as log:
        result = run(_make_cmd(left[0], job.key, out_dir, job), cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT,
                     timeout=MAKE_TIMEOUT)
    info = load_info(job.key, out_dir)
    if result.returncode or info is None:
        tail = (root / f"{job.key}.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        raise ChannelError(f"Making {left[0].name} failed: " + (" | ".join(tail) or f"exit {result.returncode}"))
    return info


# --------------------------------------------------------------------------- approve, veto, upload


def is_vetoed(info: dict) -> bool:
    return bool(info.get("veto"))


def is_approved(info: dict) -> bool:
    return bool(info.get("approved"))


def ready(info: dict) -> bool:
    """May go out: not vetoed, passed its check, and approved when approval is required."""
    return not is_vetoed(info) and not info.get("check") and (is_approved(info) or not require_approval())


def upload_request(info: dict, privacy: str | None = None):
    from trendclip import publish

    return publish.YouTubeUploadRequest(
        title=info["title"][: publish.YT_TITLE_MAX], description=(info.get("description") or "")[:5000],
        tags=[t for t in info.get("hashtags", []) if t], privacy=privacy or upload_privacy(), category_id=CATEGORY, as_short=True)


def upload(key: str, privacy: str | None = None, progress=lambda f, m: None, youtube=None, out_dir: Path | None = None,
           settings=None) -> dict[str, Any]:
    from trendclip import publish

    info = load_info(key, out_dir)
    if info is None:
        raise publish.PublishError(f"No Price Tag Why video {key}")
    if info.get("check"):
        raise publish.PublishError(f"{info['title']} failed its check: " + "; ".join(info["check"]))
    if on_hold(Day.today()):
        raise publish.PublishError(f"Uploads are on hold until {hold_until():%d %b} (PW_HOLD_UNTIL: the YouTube channel's new name)")
    if youtube is None and not connected():
        raise publish.PublishError("Connect the Price Tag Why YouTube channel first (dashboard → Price Tag Why → Connect YouTube)")
    client = youtube or publish._youtube(TOKEN)
    record = publish.upload_file(video_file(key, out_dir), upload_request(info, privacy), True, progress, client)
    info.setdefault("uploads", {})["youtube"] = record
    save_info(info, out_dir)
    progress(1.0, "Uploaded")
    announce_upload(info, record, settings)
    return record


def publish_if_allowed(key: str, progress=lambda f, m: None, out_dir: Path | None = None, youtube=None, settings=None) -> dict | None:
    """The upload at the slot's time: the record, or None when the video may not go out (yet)."""
    info = load_info(key, out_dir)
    if info is None or not ready(info) or not auto_post():
        return None
    if found := (info.get("uploads") or {}).get("youtube"):
        return found
    return upload(key, None, progress, youtube, out_dir, settings)


def approve(key: str, settings=None, out_dir: Path | None = None, now: datetime | None = None, youtube=None) -> dict[str, Any]:
    """Approve: it goes out at its slot, or at once when the slot has passed. {"title", "record", "post_at"}."""
    from trendclip import assistant

    info = load_info(key, out_dir)
    if info is None:
        raise KeyError(key)
    info["approved"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    info.pop("veto", None)
    save_info(info, out_dir)
    post_at = datetime.fromisoformat(info["job"]["post_at"]) if info.get("job") else None
    found = None
    if post_at is not None and settings is not None and (now or assistant.now(settings)) >= post_at and auto_post():
        found = publish_if_allowed(key, out_dir=out_dir, youtube=youtube, settings=settings)
    return {"title": info["title"], "record": found, "post_at": post_at}


def veto(key: str, out_dir: Path | None = None, youtube=None) -> dict[str, Any]:
    """"Don't post this": before the upload it is cancelled; after it the video is made private."""
    from trendclip import publish

    info = load_info(key, out_dir)
    if info is None:
        raise KeyError(key)
    info["veto"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    info.pop("approved", None)
    found = (info.get("uploads") or {}).get("youtube") or {}
    hidden = False
    if found.get("video_id") and found.get("privacy") != "private":
        from googleapiclient.errors import HttpError

        youtube = youtube or publish._youtube(TOKEN)
        try:
            youtube.videos().update(part="status", body={"id": found["video_id"], "status": {"privacyStatus": "private"}}).execute()
        except HttpError as err:
            save_info(info, out_dir)
            raise publish.PublishError(publish._api_error_text(err).replace("YouTube upload failed", "YouTube refused the change")) from err
        found["privacy"] = "private"
        hidden = True
    save_info(info, out_dir)
    return {"key": key, "title": info.get("title", key), "uploaded": bool(found), "hidden": hidden}


# --------------------------------------------------------------------------- Discord (channels/pricetagwhy/channel.env)


def discord_channel_id() -> int | None:
    return channel_common.discord_channel_id(DIR)


def video_line(info: dict) -> str:
    sources = len(info.get("sources") or [])
    return (f"💲 **{info.get('title', '')}** · {info.get('duration_seconds', 0):.0f} s · {info.get('words', '?')} words · "
            f"{sources} sources" + (f" · 🎵 {info['music']}" if info.get("music") else ""))


def video_note(info: dict) -> str:
    post_at = (info.get("job") or {}).get("post_at", "")[11:16]
    if info.get("check"):
        return "It failed its check and won't go out: " + "; ".join(info["check"])
    if require_approval():
        return f"Press **Approve** and it goes out at {post_at} as {upload_privacy()}; without it, nothing is posted."
    return f"It goes out by itself at {post_at} as {upload_privacy()}. Press **Don't post this** to stop it."


def buttons(key: str) -> list[dict[str, Any]]:
    b = channel_common.button
    found = [b("pwveto", key, "🚫 Don't post this", "danger"), b("pwup", key, "⬆️ Upload now", "primary")]
    if require_approval():
        found.insert(0, b("pwok", key, "✅ Approve", "success"))
    return channel_common.rows(*found)


def announce_text(text: str, settings=None, components: list[dict[str, Any]] | None = None) -> str | None:
    """A message in the channel's Discord channel; None (and nothing sent) until channel.env names one. Never raises."""
    try:
        rest = channel_common.rest(DIR, settings)
        return None if rest is None else rest.post(text, components=components)
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post in the Price Tag Why channel failed: %s", err)
        return None


def announce_video(info: dict, settings=None, out_dir: Path | None = None) -> str | None:
    """Post the finished video (shrunk for Discord) with its buttons. Never raises."""
    from trendclip import assistant

    try:
        rest = channel_common.rest(DIR, settings)
        if rest is None:
            return None
        path = video_file(info["key"], out_dir)
        small = assistant.fit_for_discord(path, path.parent / "discord" / path.name)
        return rest.post(video_line(info) + "\n" + video_note(info), (f"{info['key']}.mp4", small.read_bytes()), buttons(info["key"]))
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post for Price Tag Why %s failed: %s", info.get("key"), err)
        return None


def announce_upload(info: dict, found: dict, settings=None) -> str | None:
    lb, b = channel_common.link_button, channel_common.button
    text = f"⬆️ **{info.get('title', info.get('key', ''))}** is on YouTube ({found.get('privacy', '?')}): {found.get('url', '')}"
    return announce_text(text, settings, channel_common.rows(
        lb("▶️ Watch", found.get("url", "")) if found.get("url") else None, b("pwveto", info["key"], "🚫 Make it private", "danger"),
        lb("🎬 YouTube Studio", found.get("studio_url", "")) if found.get("studio_url") else None))


# --------------------------------------------------------------------------- YouTube login


def youtube_status() -> dict[str, Any]:
    return channel_common.youtube_status(DIR)


def start_login(redirect_uri: str) -> str:
    DIR.mkdir(parents=True, exist_ok=True)  # the token is saved in channels/pricetagwhy/
    return channel_common.start_login(DIR, redirect_uri)


def logout() -> None:
    channel_common.logout(DIR)


def connected() -> bool:
    return channel_common.connected(DIR)


# --------------------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m modules.pw_channel")
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="draw a hand-checked script")
    m.add_argument("script", type=Path)
    m.add_argument("--key", default="")
    m.add_argument("--out", type=Path, default=None)
    m.add_argument("--day", default="")
    m.add_argument("--slot", default="")
    m.add_argument("--post-at", default="")
    m.add_argument("--script-file", default="")
    sub.add_parser("check", help="the rule check of every script in the queue")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.cmd == "check":
        for s in scripts():
            print(f"{s['file']}: {s['words']} words, {len(s['sources'])} sources, " + ("made" if s["used"] else "queued") + ", "
                  + ("OK" if not s["check"] else "; ".join(s["check"])))
        return 0
    script = json.loads(args.script.read_text(encoding="utf-8"))
    key = args.key or f"{datetime.now():%Y-%m-%d_%H%M}"
    job = {"day": args.day, "slot": args.slot, "post_at": args.post_at} if args.post_at else None
    info = make(script, key, args.out, lambda f, m: print(f"[{'' if f is None else f'{f:.0%}'}] {m}", flush=True), job, args.script_file)
    print(json.dumps({k: info[k] for k in ("key", "title", "duration_seconds", "words", "check")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
