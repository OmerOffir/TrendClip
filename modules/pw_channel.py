"""The Price Tag Why channel (@PriceTagWhy): whiteboard answers to the money questions everyone wonders about, drawn
by Borderline Why's renderer in its own colours (modules.pw_render). Brief: channel-brief-pricetag-why.md.

Its day: one video per PW_SLOTS time (default 14:00 and 23:00 in Settings.timezone, like the other channels). PW_LEAD hours before the
slot (default 6) the bot makes it from the next hand-checked script in the queue (content/pricetagwhy/scripts/ in git,
then output/pricetagwhy/queue/ on the hub; each script is made once, oldest name first) and shows it on the dashboard
and in the channel's Discord channel. With PW_REQUIRE_APPROVAL=1 (the default for this new channel) it goes to YouTube
at the slot only after Approve; approving after the slot uploads it at once. A slot with no script left gets no video.

    python -m modules.pw_channel make content/bandstory/examples/001-oasis.json [--key sample-arizona]
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
import shutil
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
NAME = "Encore Story"  # was "Price Tag Why" until 2026-11-12: the YouTube channel was renamed for band stories
DIR = channel_common.channel_dir(CHANNEL)
TOKEN = channel_common.token_path(CHANNEL)
OUTPUT_DIR = PROJECT_ROOT / "output" / CHANNEL
SCRIPTS_DIR = PROJECT_ROOT / "content" / CHANNEL / "scripts"  # hand-checked scripts, kept in git
CATEGORY = "27"  # Education
MIN_SECONDS, MAX_SECONDS = 22.0, 45.0  # the brief asks 30-38 s: viewers swiped away from 45-50 s ones
STORY_SECONDS = (45.0, 85.0)  # the owner (2026-11-03): the 123 s sample was too long for the algorithm  # a money story (modules.pw_write.STORY): a cast, 3-4 moves with their math, the bars, the ask
STORY_KINDS = ("cast", "step", "bars", "ask")
WORLD_SECONDS = (40.0, 90.0)  # a true story in the world look (modules.pw_world)
BAND_SECONDS = (30.0, 58.0)  # a band's life story (modules.band_world): under a minute, the channel's only format since 2026-11-12
BAND_CATEGORY = "10"  # Music
BAND_VOICE = "en-US-BrianMultilingualNeural"  # Edge: the owner found Kokoro am_puck robotic (2026-11-12); PW_BAND_VOICE overrides
BAND_ENGINE = "edge"  # PW_BAND_ENGINE: edge | kokoro
BAND_RATE = "+22%"  # Edge reads slowly; with pauses held to BAND_PAUSE a 150-word story is about 53 s
BAND_PAUSE = 0.32  # seconds: room to breathe between sentences (0.2 made the narrator sound breathless)
BAND_HASHTAGS = ("music", "bandhistory", "rockhistory", "shorts")
BAND_COMMENT = "Which band should we do next? Name one below 👇"


def is_story(script: dict) -> bool:
    """A money-story script (format "story", or any of its scene kinds): longer, with a cast instead of a product."""
    return script.get("format") == "story" or any((s.get("kind") in STORY_KINDS) for s in script.get("scenes") or [])


def is_world(script: dict) -> bool:
    """A true money story in the full-colour world look (modules.pw_world, 2026-11-08)."""
    return script.get("format") == "world"


def is_band(script: dict) -> bool:
    """A band's life story in under a minute (modules.band_world, 2026-11-12): Encore Story's format."""
    return script.get("format") == "band"


def is_tags(script: dict) -> bool:
    """A true money story in the pegboard look (modules.pw_tags, 2026-11-10): everything is a price tag on a string."""
    return script.get("format") == "tags"


def is_gta(script: dict) -> bool:
    """A GTA 6 pause Short (modules.gta6, 2026-11-14): the channel's format from GTA 6's launch week."""
    return script.get("format") == "gta"


GTA_SECONDS = (6.0, 40.0)


def limits(script: dict) -> tuple[float, float]:
    """(min, max) seconds this script's video may run."""
    if is_gta(script):
        return GTA_SECONDS
    if is_band(script):
        return BAND_SECONDS
    if is_world(script) or is_tags(script):
        return WORLD_SECONDS
    return STORY_SECONDS if is_story(script) else (MIN_SECONDS, MAX_SECONDS)
VOICE = "af_heart"  # Kokoro-82M (Apache 2.0, commercial use allowed): warm and curious, unlike Borderline Why's voices
MUSIC = "funny"  # a music/ folder; PW_MUSIC=none for the voice alone
DISCLAIMER = "Not financial advice, just the fascinating reasons behind the prices."
HASHTAGS = ("money", "economics", "pricetagwhy", "shorts")
DEFAULT_SLOTS = "14:00,23:00"  # two a day, like the other channels (PW_SLOTS, in Settings.timezone)
DEFAULT_LEAD = 6  # hours before the slot the video is made (PW_LEAD)
POST_WINDOW = timedelta(hours=6)  # a slot's upload missed by more than this (bot was off) waits for a button
MAKE_TIMEOUT = 1800  # seconds a make process may take
SLOT_KEY = re.compile(r"\d{4}-\d{2}-\d{2}_\d{4}")


class ChannelError(RuntimeError):
    pass


def handle() -> str:
    return channel_common.handle(DIR) or "@EncoreStory"


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


def without_follow(script: dict) -> dict:
    """The script with no follow line: the owner (2026-11-04) cut the ending where the hand drew the opening again
    (8 s of the $400 vendors story), so a video ends on its last real line. Older scripts' follow scenes are skipped."""
    return {**script, "scenes": [s for s in script.get("scenes") or [] if s.get("kind") != "follow"]}


def narration(script: dict) -> str:
    return " ".join(" ".join(str(s.get("narration") or "").split()) for s in script.get("scenes") or [] if s.get("narration"))


def words(text: str) -> int:
    return len(re.findall(r"[\w'¢$%.,]+", text))


WIKI = re.compile(r"^https?://([\w-]+\.)*(wikipedia\.org|wikiwand\.com|kiddle\.co|fandom\.com|wikia\.com)(/|$)", re.I)


def check_script(script: dict) -> list[str]:
    """The brief's rules a script must meet before it is drawn."""
    if is_gta(script):  # built from fixed words and the set's pictures: nothing to check by hand
        from modules import gta6

        name = str(script.get("set") or "")
        return [] if name in gta6.SETS or name.removeprefix("land-") in gta6.LANDS else [f"unknown GTA 6 set {name!r}"]
    problems = []
    scenes = script.get("scenes") or []
    if is_band(script):
        band = script.get("band") or {}
        members = band.get("members") or {}
        if not band.get("name") or not members:
            problems.append("a band story names its band and draws its members: band.name and band.members")
        if not scenes or not all(s.get("kind") == "band" for s in scenes):
            problems.append("every scene of a band story is of kind band")
        for n, sc in enumerate(scenes, 1):
            for m in sc.get("members") or []:
                name = m.get("name") if isinstance(m, dict) else m
                if name not in members:
                    problems.append(f"scene {n} has {name!r}, who is not in band.members")
    elif is_world(script):
        if not scenes or not all(s.get("kind") == "world" for s in scenes):
            problems.append("every scene of a world story is of kind world")
        elif not (scenes[0].get("head") or scenes[0].get("big")):
            problems.append("a world story opens on its hook: head lines (or a big figure) on the first scene")
    elif is_tags(script):
        if not scenes or not all(s.get("kind") == "tags" for s in scenes):
            problems.append("every scene of a tags story is of kind tags")
        elif not any(not h.get("cue") for h in scenes[0].get("hang") or []):
            problems.append("a tags story opens with its tags up: something in the first scene's hang without a cue")
    elif is_story(script):
        if not scenes or scenes[0].get("kind") != "cast":
            problems.append("a story's first scene is its cast (kind cast)")
    elif not scenes or scenes[0].get("kind") not in ("tag", "product"):
        problems.append("the first scene must be the product (kind product) or the price tag (kind tag)")
    if len(without_follow(script)["scenes"]) < 3:
        problems.append("a video needs at least 3 scenes: the opening and what explains it")
    sources = script.get("sources") or []
    parable = bool(script.get("parable"))  # a made-up story that teaches an idea: its math is checked, not its facts
    if parable and "parable" not in (str(script.get("description") or "") + narration(script)).lower():
        problems.append("a parable says so: the word parable in the narration or the description")
    if len(sources) < 2 and not parable:
        problems.append("fewer than two sources")
    for n, src in enumerate(sources, 1):
        if WIKI.search(str(src.get("url") or "")):
            problems.append(f"source {n} is an open encyclopedia ({src.get('url', '')[:60]}): use it to find the real "
                            "source (news, official, academic) and cite that instead")
    for c in script.get("claims") or []:
        if len(set(c.get("sources") or [])) < 2:
            problems.append(f"claim checked in fewer than two sources: {c.get('claim', '')[:60]}")
        if any(not 1 <= n <= len(sources) for n in c.get("sources") or []):
            problems.append(f"claim cites a source that isn't listed: {c.get('claim', '')[:60]}")
    if not script.get("claims") and not parable:
        problems.append("no claims listed with their sources")
    advice = re.compile(r"\b(you should (buy|invest|sell)|buy this|invest in|financial advice)\b", re.I)
    if advice.search(narration(script)) and not is_band(script):
        problems.append("the narration gives advice (explain, never advise)")
    return problems


def rules_skipped(script: dict) -> bool:
    """The owner kept the script though it broke the rules (Write it myself / Write a week: Skip the rules check)."""
    return (script.get("checked") or {}).get("rules") == "skipped by the owner"


def blocking(script: dict) -> list[str]:
    """The rule problems that keep a script from being drawn: none when the owner skipped the rules check."""
    return [] if rules_skipped(script) else check_script(script)


def queue_dir(out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / "queue"


def landing(settings, names: list[str]) -> dict[str, dict[str, str] | None]:
    """The slot each queued script lands on in the Plan ({"day", "slot", "start"}), None while no slot is free."""
    rows = [r for d in plan_view(settings)["days"] for r in d["slots"]]
    return {name: next(({"day": r["day"], "slot": r["slot"], "start": r["start"]} for r in rows
                        if (r.get("script") or {}).get("file") == name), None) for name in names}


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


def plan_path(out_dir: Path | None = None) -> Path:
    return out_root(out_dir) / "plan.json"


def load_plan(out_dir: Path | None = None) -> dict[str, Any]:
    """The owner's changes to the plan: "removed" scripts (out of the queue until put back) and "pins" (slot key ->
    the script that slot is made from, ahead of the queue's order)."""
    data = _read(plan_path(out_dir)) or {}
    return {"removed": list(data.get("removed") or []), "pins": dict(data.get("pins") or {})}


def save_plan(plan: dict[str, Any], out_dir: Path | None = None) -> None:
    path = plan_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def queued(out_dir: Path | None = None, scripts_dir: Path | None = None) -> list[Path]:
    """The scripts the next slots take, in order: not made yet, not removed from the plan and passing the rule check."""
    used = used_scripts(out_dir)
    removed = set(load_plan(out_dir)["removed"])
    return [p for p in script_files(out_dir, scripts_dir)
            if p.name not in used and p.name not in removed and (s := _read(p)) is not None and not blocking(s)]


MIX_DAYS = ("2026-11-12", "2026-11-18")  # the week's test (the owner, 2026-11-11): a "Same $X" story and a true story every day


def wanted(job: "Job") -> str | None:
    """The kind of script a slot asks for during the test: "story" or "true", swapping the two slots day by day (so
    the hour is not what is compared); None outside the test."""
    if not MIX_DAYS[0] <= job.day.isoformat() <= MIX_DAYS[1]:
        return None
    return "story" if (job.day.toordinal() + (job.slot >= "18:00")) % 2 == 0 else "true"


def kind_of(path: Path) -> str:
    script = _read(path) or {}
    return "gta" if is_gta(script) else "band" if is_band(script) else "story" if is_story(script) else "true" if is_world(script) or is_tags(script) else "why"


def take(left: list[Path], job: "Job") -> Path | None:
    """The slot's script out of the free ones: the first of the kind it asks for, else simply the first."""
    kind = wanted(job)
    return next((p for p in left if kind and kind_of(p) == kind), left[0] if left else None)


def script_for(job: "Job", out_dir: Path | None = None, scripts_dir: Path | None = None) -> Path | None:
    """The script a slot is made from: the one pinned to it, else the first queued script no slot is pinned to (of
    the kind the slot asks for, while the test runs)."""
    left = queued(out_dir, scripts_dir)
    pins = load_plan(out_dir)["pins"]
    pinned = next((p for p in left if p.name == pins.get(job.key)), None)
    found = pinned or take([p for p in left if p.name not in pins.values()], job)
    if found is None and not on_hold(job.day):
        from modules import gta6

        if gta6.mode_on():  # GTA 6 needs no hand-written script: the rotation's next set
            return gta6.new_script(queue_dir(out_dir))
    return found


def remove_script(name: str, out_dir: Path | None = None) -> None:
    """Take a script out of the plan (it stays in the Scripts list, where it can be put back)."""
    plan = load_plan(out_dir)
    if name not in plan["removed"]:
        plan["removed"].append(name)
    plan["pins"] = {k: v for k, v in plan["pins"].items() if v != name}
    save_plan(plan, out_dir)


def restore_script(name: str, out_dir: Path | None = None) -> None:
    plan = load_plan(out_dir)
    plan["removed"] = [n for n in plan["removed"] if n != name]
    save_plan(plan, out_dir)


def remove_slot(job: "Job", out_dir: Path | None = None) -> dict[str, Any]:
    """Empty a made slot: its video is set aside (not one already on YouTube) and its script leaves the plan (a slot
    not made yet is emptied with remove_script on the script shown for it)."""
    info = load_info(job.key, out_dir)
    name = (info or {}).get("script_file", "")
    if info:
        set_aside(job.key, out_dir)
    if name:
        remove_script(name, out_dir)
    plan = load_plan(out_dir)
    if plan["pins"].pop(job.key, None):
        save_plan(plan, out_dir)
    return {"removed": name, "video": bool(info)}


def pin_script(job: "Job", name: str, out_dir: Path | None = None) -> None:
    """Make a slot from this script (taken back from "removed" if it was there)."""
    if load_info(job.key, out_dir):
        raise ChannelError("That slot already has a video: remove it first")
    restore_script(name, out_dir)
    if name not in {p.name for p in queued(out_dir)}:
        raise ChannelError(f"{name} can't be used: it was made for a slot already or breaks the rules")
    plan = load_plan(out_dir)
    plan["pins"] = {k: v for k, v in plan["pins"].items() if v != name}
    plan["pins"][job.key] = name
    save_plan(plan, out_dir)


def place_video(key: str, job: "Job", out_dir: Path | None = None) -> dict:
    """Put a made video (a sample) on a slot: copied into the slot's folder, where it waits for Approve like one the
    bot made. Its script (when known) counts as used."""
    src = load_info(key, out_dir)
    if src is None:
        raise ChannelError("No such video")
    if src.get("job"):
        raise ChannelError("That video already belongs to a slot")
    if src.get("check"):
        raise ChannelError("That video breaks the rules: " + "; ".join(src["check"]))
    if load_info(job.key, out_dir):
        raise ChannelError("That slot already has a video: remove it first")
    stale = out_root(out_dir) / job.key
    if stale.exists():
        set_aside(job.key, out_dir)
    shutil.copytree(out_root(out_dir) / key, out_root(out_dir) / job.key)
    info = {**src, "key": job.key, "job": {"day": job.day.isoformat(), "slot": job.slot, "post_at": job.post_at.isoformat(timespec="minutes")},
            "placed_from": key}
    info.pop("approved", None)
    info.pop("veto", None)
    save_info(info, out_dir)
    plan = load_plan(out_dir)
    if plan["pins"].pop(job.key, None):
        save_plan(plan, out_dir)
    return load_info(job.key, out_dir)


def scripts(out_dir: Path | None = None, scripts_dir: Path | None = None) -> list[dict[str, Any]]:
    """Every script with its rule check and whether a slot's video was made from it."""
    used = used_scripts(out_dir)
    plan = load_plan(out_dir)
    out = []
    for path in script_files(out_dir, scripts_dir):
        script = _read(path)
        if script is None:
            out.append({"file": path.name, "title": path.stem, "question": "", "words": 0, "sources": [], "claims": [],
                        "check": ["not valid JSON"], "used": False})
            continue
        out.append({"file": path.name, "title": script.get("title", ""), "question": script.get("question", ""), "topic": script.get("topic", ""),
                    "words": words(narration(script)), "sources": script.get("sources", []), "claims": script.get("claims", []),
                    "check": check_script(script), "rules_skipped": rules_skipped(script), "used": path.name in used, "removed": path.name in plan["removed"],
                    "pinned": next((k for k, v in plan["pins"].items() if v == path.name), ""),
                    "band": is_band(script), "song": script.get("song") if isinstance(script.get("song"), dict) else None,
                    "in_queue": path.parent == queue_dir(out_dir)})
    return out


def find_script(name: str, out_dir: Path | None = None, scripts_dir: Path | None = None) -> Path | None:
    return next((p for p in script_files(out_dir, scripts_dir) if p.name == name), None)


def shows_icons(script: dict) -> bool:
    """Whether the video pops in Noto emoji (on a tag scene, or a board with no drawing of its own), which the
    animated set's licence (CC BY 4.0) wants credited."""
    from modules import pw_draw

    for scene in script.get("scenes") or []:
        icons = [el.get("icon") for el in scene.get("elements") or [] if el.get("kind") == "icon"]
        if not icons:
            continue
        if scene.get("kind") == "tag":
            return True
        if scene.get("kind") == "board":
            picture = (scene.get("board") or {}).get("picture") or next((pw_draw.FROM_ICON[n] for n in icons if n in pw_draw.FROM_ICON), "")
            if picture not in pw_draw.DRAWINGS:
                return True
    return False


def description(script: dict) -> str:
    """The YouTube description: the question, the short answer, the sources, the disclaimer, hashtags; a credit only
    for what needs one (Noto emoji here, Wikimedia pictures in with_credits)."""
    if is_band(script):
        tags = list(dict.fromkeys([*(script.get("hashtags") or []), *BAND_HASHTAGS]))[:7]
        lines = [script.get("description", "")]
        if script.get("sources"):
            lines += ["", "Sources:"] + [f"- {s['title']}: {s['url']}" for s in script["sources"]]
        often = "twice a day" if len(slots()) == 2 else "every day"
        lines += ["", "Drawn and animated by us; the music is our own.", "",
                  f"A band's whole story in under a minute, {often}: {handle()}", "", " ".join(f"#{t}" for t in tags)]
        return "\n".join(lines).strip()
    tags = list(dict.fromkeys([*(script.get("hashtags") or []), *HASHTAGS]))[:7]
    lines = [script.get("question", ""), "", script.get("description", "")]
    if script.get("sources"):  # a parable has none
        lines += ["", "Sources:"] + [f"- {s['title']}: {s['url']}" for s in script["sources"]]
    if shows_icons(script):
        lines += ["", "Icons: Noto Emoji by Google (Apache 2.0; animated Noto Emoji, CC BY 4.0)."]
    often = "twice a day" if len(slots()) == 2 else "every day"
    what = "true money story" if is_world(script) or is_tags(script) else "'why'"
    lines += ["", DISCLAIMER, "", f"A new {what} {often}: {handle()}", "", " ".join(f"#{t}" for t in tags)]
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
            and not on_hold(job.day) and script_for(job, out_dir) is not None]  # an emptied plan makes nothing


def due_posts(settings, at: datetime, sent: set[str]) -> list[Job]:
    """Jobs whose upload time has come (publish.EARLY_UPLOAD before the slot: private, scheduled for the slot; up to
    POST_WINDOW after it) and whose upload was not tried yet."""
    from trendclip import publish

    if not auto_post():
        return []
    at = at.astimezone(settings.tz)
    return [job for day in (at.date() - timedelta(days=1), at.date(), at.date() + timedelta(days=1)) for job in jobs_for(settings, day)
            if job.post_key not in sent and job.post_at - publish.EARLY_UPLOAD <= at < job.post_at + POST_WINDOW and not on_hold(job.day)]


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
    pins = load_plan(out_dir)["pins"]
    samples = samples_by_script(out_dir)
    left = list(queued(out_dir))
    by_name = {p.name: p for p in left}
    left = [p for p in left if p.name not in pins.values()]
    days = []
    for n in range(-before, after + 1):
        day = now.date() + timedelta(days=n)
        rows = []
        for job in jobs_for(settings, day):
            info = load_info(job.key, out_dir)
            script = None
            if info is None and now < job.post_at and not on_hold(day):
                script = by_name.get(pins.get(job.key)) or take(left, job)
                if script in left:
                    left.remove(script)
            prog = make_progress(job.key, out_dir) if info is None else None
            rows.append({**job.view(), "state": "making" if prog else slot_state(job, info, now, script), "video": _plan_video(info),
                         "progress": prog, "sample": samples.get(script.name) if script and info is None else None,
                         "script": {"file": script.name, "title": (_read(script) or {}).get("title", ""),
                                    "pinned": pins.get(job.key) == script.name} if script else None})
        days.append({"day": day.isoformat(), "weekday": day.strftime("%A"), "today": day == now.date(), "slots": rows})
    return days


def samples_by_script(out_dir: Path | None = None) -> dict[str, dict]:
    """The newest good sample made from each script (the Scripts list's "Make a sample"): the Plan shows it on the slot
    that script is planned for, to watch and put on the slot (2026-11-08: the IKEA sample was made, the slot didn't show it)."""
    found: dict[str, dict] = {}
    for v in videos(out_dir):  # newest first
        name = v.get("script_file")
        if v["state"] == "sample" and name and name not in found and v.get("has_video") and not v.get("check"):
            found[name] = {**(_plan_video(v) or {}), "video_t": v.get("video_t", "")}
    return found


def _plan_video(info: dict | None) -> dict | None:
    if info is None:
        return None
    return {"key": info["key"], "title": info.get("title", ""), "duration_seconds": info.get("duration_seconds"),
            "check": info.get("check") or [], "youtube": (info.get("uploads") or {}).get("youtube"),
            "script_file": info.get("script_file", ""), "created_at": info.get("created_at", ""),
            "video_t": info.get("video_t", ""), **({"ear": info["ear"]} if isinstance(info.get("ear"), list) else {})}


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
    return None if data is None else {**data, "key": key, "video_t": video_t(video_file(key, out_dir))}


def video_t(video: Path) -> str:
    """When the video file last changed: the dashboard's player link carries it, so a re-made video is fetched anew
    (a browser holding the old file's bytes asked past the new, shorter one's end and froze)."""
    try:
        return str(int(video.stat().st_mtime))
    except OSError:
        return ""


def save_info(info: dict, out_dir: Path | None = None) -> Path:
    path = info_path(info["key"], out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in info.items() if k not in ("key", "video_t")}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
        info["video_t"] = video_t(path.parent / "video.mp4")
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


def with_credits(text: str, spec: bw_render.Spec) -> str:
    """The description with the credits of the Wikimedia pictures the video shows (photo scenes, modules.bw_photos)."""
    from modules import bw_photos

    credits = bw_photos.credits([s.opts["_photo"] for s in spec.scenes if s.kind == "photo" and s.opts.get("_photo")])
    return f"{text}\n\n{credits}" if credits else text


def record(text: str, out: Path, progress=lambda m: None, voice: str = "", speed: float = 0.0, engine: str = "",
           rate: str = "", pause: float = 0.0) -> list[bw_render.Word]:
    """The narration in the channel's voice: Kokoro (the hub's) when installed, else an Edge voice. `engine`, `voice`,
    `rate` and `pause` override the settings (Encore Story's narrator, band_voice)."""
    from trendclip import kokoro_voice

    engine = (engine or setting("PW_TTS_ENGINE", "kokoro")).lower()
    if engine == "edge":
        from trendclip import voiceover

        voice = voice or setting("PW_EDGE_VOICE", "en-US-EmmaNeural")
        progress(f"Recording the narration ({voice})")
        got = voiceover.synthesize(text, out, voice, rate or setting("PW_TTS_RATE", "+12%"))
    elif kokoro_voice.available() and engine == "kokoro":
        voice = voice or setting("PW_TTS_VOICE", VOICE)
        progress(f"Recording the narration (Kokoro {voice})")
        got = kokoro_voice.synthesize(text, out, voice, speed or float(setting("PW_TTS_SPEED", "1.15")))
        kokoro_voice.unload()
    else:
        from trendclip import voiceover

        voice = setting("PW_EDGE_VOICE", "en-US-EmmaNeural")
        progress(f"Recording the narration ({voice})")
        got = voiceover.synthesize(text, out, voice, setting("PW_TTS_RATE", "+12%"))
    words = [bw_render.Word(w.text, float(w.start), float(w.end)) for w in got]
    try:
        return tighten(out, words, pause or float(setting("PW_PAUSE_MAX", str(PAUSE_MAX))))
    except Exception as err:  # noqa: BLE001 - the untouched narration still makes the video
        logger.warning("The narration's pauses were left as they are: %s", err)
        return words


PAUSE_MAX = 0.2  # seconds: a longer silence in the narration is cut down to this (the owner, 2026-11-03: the flow was slow)
RATE = 24000


def cuts(samples, longest: float = PAUSE_MAX, rate: int = RATE, floor: float = 0.018) -> list[tuple[int, int]]:
    """(from, to) sample ranges to drop: the middle of every silence longer than `longest` (10 ms windows under
    `floor`, about -35 dB), so each pause keeps `longest` seconds, half on each side."""
    import numpy as np

    win = rate // 100
    n = len(samples) // win
    if n == 0:
        return []
    rms = np.sqrt(np.mean(samples[: n * win].reshape(n, win) ** 2, axis=1))
    quiet = rms < floor
    keep = int(longest * 100)
    found, k = [], 0
    while k < n:
        if not quiet[k]:
            k += 1
            continue
        j = k
        while j < n and quiet[j]:
            j += 1
        if j - k > keep:
            found.append(((k + keep // 2) * win, (j - (keep - keep // 2)) * win))
        k = j
    return found


def shifted(t: float, dropped: list[tuple[float, float]]) -> float:
    """A time in the old narration, in the tightened one (`dropped`: (from, to) seconds cut out)."""
    gone = 0.0
    for a, b in dropped:
        if t >= b:
            gone += b - a
        elif t > a:
            gone += t - a
    return t - gone


def tighten(mp3: Path, words: list[bw_render.Word], longest: float = PAUSE_MAX) -> list[bw_render.Word]:
    """Cut the narration's long pauses down to `longest` seconds, in place (the MP3 and its words.json), and return
    the words at their new times."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                         capture_output=True, check=True).stdout
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    found = cuts(samples, longest)
    if not found:
        return words
    keep = np.ones(len(samples), dtype=bool)
    for a, b in found:
        keep[a:b] = False
    pcm = (samples[keep] * 32768).clip(-32768, 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-i", "-",
                    "-c:a", "libmp3lame", "-b:a", "160k", str(mp3)], input=pcm, check=True)
    dropped = [(a / RATE, b / RATE) for a, b in found]
    words = [bw_render.Word(w.text, round(shifted(w.start, dropped), 3), round(shifted(w.end, dropped), 3)) for w in words]
    mp3.with_suffix(".words.json").write_text(
        json.dumps([{"word": w.text, "start": w.start, "end": w.end} for w in words], indent=1), encoding="utf-8")
    return words


def pick_music(seed: int) -> Path | None:
    folder = setting("PW_MUSIC", MUSIC)
    if folder.lower() in ("none", "off", "0"):
        return None
    tracks = sorted((PROJECT_ROOT / "music" / folder).glob("*.mp3"))
    lively = [t for t in tracks if not any(w in t.stem.lower() for w in CALM_MUSIC)]  # elevator music drags the pace
    tracks = lively or tracks
    return random.Random(seed).choice(tracks) if tracks else None


CALM_MUSIC = ("elevator", "bossa")
BAND_MUSIC = "encore"  # music/encore/: the owner's Suno instrumentals for Encore Story (2026-11-12)
BAND_LOUDNESS = -17.0  # LUFS: Suno masters loud; brought to the level of our own beds so the voice stays on top


SONG_EXTS = (".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac")
SONG_MAX = 40 * 1024 * 1024
SONG_VOLUME = 0.5  # the band's own song is heard (the voice ducks it): the competitor's main draw (2026-11-13)


def songs_dir(out_dir: Path | None = None) -> Path:
    """The owner's uploads of the bands' own songs: on the hub only, never in git (they are the labels' recordings)."""
    return out_root(out_dir) / "songs"


def set_song(script_file: str, data: bytes, filename: str, start: float, out_dir: Path | None = None) -> dict:
    """The owner's file of the band's song for one queued script, played from `start` seconds."""
    path = find_script(script_file, out_dir)
    if path is None or path.parent != queue_dir(out_dir):
        raise ChannelError("No such script in the queue")
    ext = Path(filename).suffix.lower()
    if ext not in SONG_EXTS:
        raise ChannelError(f"A song is one of {', '.join(SONG_EXTS)}")
    if not data or len(data) > SONG_MAX:
        raise ChannelError("The song file is empty or over 40 MB")
    folder = songs_dir(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    name = path.stem + ext
    for old in folder.glob(path.stem + ".*"):
        old.unlink()
    (folder / name).write_bytes(data)
    script = json.loads(path.read_text(encoding="utf-8"))
    script["song"] = {"file": name, "start": round(max(0.0, float(start)), 1), "name": Path(filename).stem[:60]}
    path.write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return script["song"]


def remove_song(script_file: str, out_dir: Path | None = None) -> None:
    path = find_script(script_file, out_dir)
    if path is None or path.parent != queue_dir(out_dir):
        raise ChannelError("No such script in the queue")
    script = json.loads(path.read_text(encoding="utf-8"))
    song = script.pop("song", None)
    if isinstance(song, dict) and song.get("file"):
        (songs_dir(out_dir) / Path(str(song["file"])).name).unlink(missing_ok=True)
    path.write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def song_track(script: dict, seconds: float, out: Path, voice: Path, out_dir: Path | None = None) -> tuple[Path, float, str] | None:
    """The band's own song (the owner's upload) from its chosen start, faded in and out, ducked under the voice so
    it swells in every pause; None when the script has no song or its file is gone."""
    import subprocess

    from modules import band_music

    song = script.get("song")
    if not isinstance(song, dict) or not song.get("file"):
        return None
    src = songs_dir(out_dir) / Path(str(song["file"])).name
    if not src.is_file():
        logger.warning("The song %s is missing: our own music instead", src.name)
        return None
    start, length = float(song.get("start") or 0), seconds + 3
    flt = (f"[0:a]afade=t=in:d=0.3,afade=t=out:st={max(0.5, length - 2.5):.2f}:d=2.5,loudnorm=I={BAND_LOUDNESS}:TP=-2:LRA=11,"
           "aresample=44100,aformat=channel_layouts=stereo[m];[1:a]aresample=44100,aformat=channel_layouts=stereo[v];"
           "[m][v]sidechaincompress=threshold=0.02:ratio=6:attack=30:release=350:makeup=1[out]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(src), "-i", str(voice),
                    "-filter_complex", flt, "-map", "[out]", "-ar", "44100", "-b:a", "192k", str(out)], check=True, timeout=180)
    bpm = band_music.tempo(band_music.load(out))
    return out, bpm, f"song: {song.get('name') or src.stem} from {start:.0f} s"


def band_track(script: dict, seconds: float, out: Path) -> tuple[Path, float, str]:
    """A band story's music and its tempo: one of the owner's tracks in music/encore (a different track and starting
    point per seed, on a strong beat, faded in, as loud as the rest), else an original anthem made in code
    (modules.band_music). Never the band's own song: covers get claimed. Returns (file, bpm, what was used)."""
    import subprocess

    from modules import band_music, band_world

    tracks = sorted((PROJECT_ROOT / "music" / setting("PW_BAND_MUSIC", BAND_MUSIC)).glob("*.mp3"))
    seed = int(script.get("seed") or 0)
    if tracks:
        track = random.Random(seed).choice(tracks)
        try:
            x = band_music.load(track)
            bpm = band_music.tempo(x)
            start = band_music.pick_start(x, seconds + 3, seed, bpm)
            fade = "afade=t=in:d=0.4," if start > 0 else ""
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(track), "-t", f"{seconds + 3:.2f}",
                            "-af", f"{fade}loudnorm=I={BAND_LOUDNESS}:TP=-2:LRA=11", "-ar", "44100", "-b:a", "192k", str(out)],
                           check=True, timeout=120)
            used = f"{track.stem} from {start:.1f} s at {bpm} BPM"
            logger.info("Encore Story music: %s", used)
            return out, bpm, used
        except Exception as err:  # noqa: BLE001 - the made-here anthem still makes the video
            logger.warning("The track %s could not be used (%s): the anthem made in code instead", track.name, err)
    path = band_music.bed(seconds + 2, out, band_music.style_for(script), seed)
    return path, float(band_world.BPM), "anthem made in code"


def make(script: dict, key: str, out_dir: Path | None = None, progress=lambda f, m: None, job: dict | None = None,
         script_file: str = "") -> dict:
    """Draw a script into output/pricetagwhy/<key>/ (in this process: call it from `python -m modules.pw_channel`).
    `job`: the slot it is for ({day, slot, post_at}); `script_file`: the queued script it came from (used up)."""
    from modules import pw_render, pw_thumb

    if problems := blocking(script):
        raise ChannelError("The script breaks the rules: " + "; ".join(problems))
    if is_gta(script):
        return make_gta(script, key, out_dir, progress, job, script_file)
    pw_render.use_style()
    work = out_root(out_dir) / key
    work.mkdir(parents=True, exist_ok=True)
    script = without_follow(script)
    script = {**script, "narration": narration(script), "look": "satellite", "brand": True}
    script.setdefault("seed", random.randrange(1, 10_000))
    (work / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress(0.05, "Recording the narration")
    ear = used = None
    if is_band(script):  # a band story: its own narrator, at a normal pace
        spoken = record(script["narration"], work / "voice.mp3", lambda m: progress(None, m),
                        setting("PW_BAND_VOICE", BAND_VOICE), float(setting("PW_BAND_SPEED", "1.0")),
                        setting("PW_BAND_ENGINE", BAND_ENGINE), setting("PW_BAND_RATE", BAND_RATE),
                        float(setting("PW_BAND_PAUSE", str(BAND_PAUSE))))
    else:
        spoken = record(script["narration"], work / "voice.mp3", lambda m: progress(None, m))
    if is_tags(script):  # the script names what hangs; where it hangs is filled in here
        from modules import pw_tags

        script = pw_tags.arrange(script)
    spec = bw_render.spec_from_dict(script, spoken)
    spec.hold = 0.5  # the video ends half a second after the last word (no follow line, no redraw)
    spec.sfx = setting("PW_SFX", "on").lower() not in ("off", "0", "no")  # pops, marker, whooshes, a cash ring (pw_render)
    music = pick_music(int(script["seed"]))
    volume = float(setting("PW_MUSIC_VOLUME", "0.09"))
    if is_band(script):  # Encore Story (modules.band_world): the members play to a rock bed made in code
        from modules import band_world

        spec.hold = 0.8
        script = band_world.titled(script, spec.duration)
        (work / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        progress(None, "Listening to the narration")
        from modules import audio_check  # the ear: words the voice says wrong ("dies" like "dices"), shown on the card

        ear = audio_check.ear(work / "voice.mp3", spoken)
        got = None
        try:
            got = song_track(script, spec.duration, work / "music.mp3", work / "voice.mp3", out_dir)
        except Exception as err:  # noqa: BLE001 - our own music still makes the video
            logger.warning("The song could not be used (%s): our own music instead", err)
        music, bpm, used = got or band_track(script, spec.duration, work / "music.mp3")
        band_world.set_tempo(bpm)
        progress(None, f"Music: {used}")
        volume = float(setting("PW_SONG_VOLUME", str(SONG_VOLUME))) if got else float(script.get("music_volume") or setting("PW_BAND_MUSIC_VOLUME", "0.15"))
        band_world.render(spec, script["band"], work / "voice.mp3", work / "video.mp4", music, volume,
                          lambda f, m: progress(0.1 + 0.85 * (f or 0), m))
    elif is_world(script):  # the full-colour world (modules.pw_world): no maps, no photos, no marker hand
        from modules import pw_world

        pw_world.render(spec, work / "voice.mp3", work / "video.mp4", music, volume, lambda f, m: progress(0.1 + 0.85 * (f or 0), m))
    elif is_tags(script):  # the pegboard (modules.pw_tags): the story hangs as price tags on strings
        from modules import pw_tags

        pw_tags.render(spec, work / "voice.mp3", work / "video.mp4", music, volume, lambda f, m: progress(0.1 + 0.85 * (f or 0), m))
    else:
        pw_render.restyle(spec)  # a product with no drawing becomes a photo scene before the photos are fetched
        if missing := bw_render.prepare(spec):
            raise ChannelError("Places not found on the map: " + ", ".join(missing))
        pw_render.render(spec, work / "voice.mp3", work / "video.mp4", music, volume,
                         lambda f, m: progress(0.1 + 0.85 * (f or 0), m), handle=handle(), question=script.get("question", ""))
    info = {
        "key": key, "title": script["title"], "question": script.get("question", ""), "description": with_credits(description(script), spec),
        "hashtags": list(dict.fromkeys([*(script.get("hashtags") or []), *(BAND_HASHTAGS if is_band(script) else HASHTAGS)]))[:6],
        "sources": script.get("sources", []), "claims": script.get("claims", []), "duration_seconds": round(spec.duration, 2),
        "words": words(script["narration"]), "voice": setting("PW_TTS_VOICE", VOICE), "music": used or (music.stem if music else None),
        "comment": comment_text(script), **({"category": BAND_CATEGORY} if is_band(script) else {}),
        "thumb_at": round(max(0.5, spec.scenes[0].end - 0.2), 2),
        **({"ear": ear} if is_band(script) and ear is not None else {}),  # the opening, fully drawn (modules.thumbs)
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        **({"job": job} if job else {}), **({"script_file": script_file} if script_file else {}),
    }
    try:  # a picture made to be clicked (modules.pw_thumb), not a frame of the video; a world story's hook frame
        if is_band(script):  # the whole band on stage, the name, "IN 45 SECONDS" and the hook (band_world.thumbnail)
            from modules import band_world

            band_world.thumbnail(script, work / "thumb.jpg")
        elif is_world(script):
            from modules import pw_world

            at = pw_world.thumb_time(spec)
            pw_world.still(spec, at).save(work / "thumb.jpg", quality=92)
            info["thumb_at"] = round(at, 2)
        elif is_tags(script):  # the first scene with everything hung and stuck on
            from modules import pw_tags

            pw_tags.still(spec, info["thumb_at"]).save(work / "thumb.jpg", quality=92)
        else:
            pw_thumb.make(script, work / "thumb.jpg", thumb_style(script))
        info["thumbnail"] = "thumb.jpg"
    except Exception:  # noqa: BLE001 - the video is still good without it
        logger.exception("Price Tag Why thumbnail for %s failed", key)
    seconds = info["duration_seconds"]
    low, high = limits(script)
    info["check"] = [] if low <= seconds <= high else [f"{seconds:.0f} s long (wanted {low:.0f}-{high:.0f})"]
    save_info(info, out_dir)
    progress(1.0, "Video ready")
    return info


def make_gta(script: dict, key: str, out_dir: Path | None = None, progress=lambda f, m: None, job: dict | None = None,
             script_file: str = "") -> dict:
    """A GTA 6 pause Short (modules.gta6) into output/pricetagwhy/<key>/, with the info the upload reads."""
    from modules import gta6

    work = out_root(out_dir) / key
    work.mkdir(parents=True, exist_ok=True)
    (work / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    got = gta6.make(script, work, progress)
    for junk in ("final_short.mp4",):
        (work / junk).unlink(missing_ok=True)
    info = {"key": key, **{k: v for k, v in got.items() if v is not None}, "question": "", "sources": [], "claims": [],
            "words": words(gta6.words_for(str(script.get("set") or "ride"))["script"]), "music": None,
            "created_at": datetime.now().replace(microsecond=0).isoformat(),
            **({"job": job} if job else {}), **({"script_file": script_file} if script_file else {})}
    low, high = limits(script)
    seconds = float(info.get("duration_seconds") or 0)
    info["check"] = [] if low <= seconds <= high else [f"{seconds:.0f} s long (wanted {low:.0f}-{high:.0f})"]
    save_info(info, out_dir)
    progress(1.0, "Video ready")
    return info


def _make_cmd(script_path: Path, key: str, out_dir: Path | None, job: Job | None) -> list[str]:
    cmd = [sys.executable, "-m", "modules.pw_channel", "make", str(script_path), "--key", key, "--script-file", script_path.name]
    if job is not None:
        cmd += ["--day", job.day.isoformat(), "--slot", job.slot, "--post-at", job.post_at.isoformat(timespec="minutes")]
    return cmd + (["--out", str(out_dir)] if out_dir else [])


def start_make(script_path: Path, key: str, out_dir: Path | None = None, job: Job | None = None) -> subprocess.Popen:
    """make() in a process of its own (the dashboard's way in), its log in output/pricetagwhy/<key>.log."""
    root = out_root(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    log = (root / f"{key}.log").open("w")
    return subprocess.Popen(_make_cmd(script_path, key, out_dir, job), cwd=str(PROJECT_ROOT), stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)


PROGRESS_LINE = re.compile(r"^\[(\d{1,3})%\]\s*(.*)$")
PROGRESS_STALE = 180  # seconds without a new log line: the make died (a live one writes every few seconds)


def make_progress(key: str, out_dir: Path | None = None, now: float | None = None) -> dict[str, Any] | None:
    """How far a video being made is ({"fraction": 0-1, "message": "Drawing the maps: frame 1200/1741"}), read from its
    log (output/pricetagwhy/<key>.log, written by the bot's makes and the tab's alike); None when nothing is being made
    for `key` (finished, failed, or quiet for PROGRESS_STALE)."""
    log = out_root(out_dir) / f"{key}.log"
    if not log.is_file() or (now or time.time()) - log.stat().st_mtime > PROGRESS_STALE:
        return None
    lines = [ln.strip() for ln in log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    if not lines or lines[-1].startswith("{") or "Traceback" in "\n".join(lines[-30:]):
        return None  # finished (the result line) or crashed
    fraction, message = 0.0, ""
    for ln in lines:
        m = PROGRESS_LINE.match(ln)
        if m:
            fraction, message = int(m.group(1)) / 100, m.group(2)
        elif ln.startswith("[] "):
            message = ln[3:]
    if fraction >= 1:
        return None
    return {"fraction": round(fraction, 2), "message": message.replace("Drawing the maps", "Drawing")}


def making_now(out_dir: Path | None = None) -> list[str]:
    """The keys being made right now (any process), from their logs."""
    root = out_root(out_dir)
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.log") if make_progress(p.stem, out_dir) is not None)


def make_slot(settings, job: Job, out_dir: Path | None = None, run=subprocess.run) -> dict:
    """The slot's video from its script (script_for), drawn in its own process (blocks until it is done). Raises
    ChannelError when no script is left or the make fails; the info (with "check" when it can't go out) otherwise."""
    script = script_for(job, out_dir)
    if script is None:
        raise ChannelError("No script left in the queue: add one to content/pricetagwhy/scripts/")
    root = out_root(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / f"{job.key}.log").open("w") as log:
        result = run(_make_cmd(script, job.key, out_dir, job), cwd=str(PROJECT_ROOT), stdout=log, stderr=subprocess.STDOUT,
                     timeout=MAKE_TIMEOUT)
    info = load_info(job.key, out_dir)
    if result.returncode or info is None:
        tail = (root / f"{job.key}.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
        raise ChannelError(f"Making {script.name} failed: " + (" | ".join(tail) or f"exit {result.returncode}"))
    return info


# --------------------------------------------------------------------------- approve, veto, upload


def is_vetoed(info: dict) -> bool:
    return bool(info.get("veto"))


def is_approved(info: dict) -> bool:
    return bool(info.get("approved"))


def ready(info: dict) -> bool:
    """May go out: not vetoed, passed its check, and approved when approval is required."""
    return not is_vetoed(info) and not info.get("check") and (is_approved(info) or not require_approval())


def thumb_style(script: dict) -> str:
    """The script's thumbnail.style, else PW_THUMB_STYLE (shock | versus | tag)."""
    from modules import pw_thumb

    style = str((script.get("thumbnail") or {}).get("style") or setting("PW_THUMB_STYLE", "shock")).strip().lower()
    return style if style in pw_thumb.STYLES else "shock"


def thumb_path(key: str, out_dir: Path | None = None) -> Path | None:
    if not re.fullmatch(r"[\w.-]+", key or ""):
        return None
    path = out_root(out_dir) / key / "thumb.jpg"
    return path if path.is_file() else None


def set_thumbnail(client, video_id: str, path: Path) -> str:
    """Give the uploaded Short its thumbnail: "set", or why YouTube refused (custom Shorts thumbnails need the
    YouTube Partner Program; until then YouTube picks a frame)."""
    from googleapiclient.http import MediaFileUpload

    try:
        client.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(path), mimetype="image/jpeg")).execute()
        return "set"
    except Exception as err:  # noqa: BLE001 - the video is up either way
        logger.warning("Price Tag Why thumbnail for %s not set: %s", video_id, err)
        return f"not set: {str(err)[:200]}"


def note_thumbnail(key: str, status: str, out_dir: Path | None = None) -> None:
    """How the thumbnail's second setting went (publish.keep_thumbnail), into the upload record."""
    info = load_info(key, out_dir)
    record = ((info or {}).get("uploads") or {}).get("youtube")
    if info and record:
        record["thumbnail"] = f"{status} after processing"
        save_info(info, out_dir)
    if out_dir is None:  # the real channel: a miss is an alert in its Discord channel (trendclip.doctor)
        from trendclip import doctor

        doctor.alert_thumbnail("pw", key, status, title=(info or {}).get("title", ""))


def upload_request(info: dict, privacy: str | None = None, publish_at: datetime | None = None):
    from trendclip import publish

    return publish.YouTubeUploadRequest(publish_at=publish_at,
        title=info["title"][: publish.YT_TITLE_MAX], description=(info.get("description") or "")[:5000],
        tags=[t for t in info.get("hashtags", []) if t], privacy=privacy or upload_privacy(), category_id=info.get("category") or CATEGORY, as_short=True)


def upload(key: str, privacy: str | None = None, progress=lambda f, m: None, youtube=None, out_dir: Path | None = None,
           settings=None, publish_at: datetime | None = None) -> dict[str, Any]:
    """`publish_at`: uploaded private, YouTube makes it public then; the comment waits for that (after_publish)."""
    from trendclip import publish

    info = load_info(key, out_dir)
    if info is None:
        raise publish.PublishError(f"No Encore Story video {key}")
    if info.get("check"):
        raise publish.PublishError(f"{info['title']} failed its check: " + "; ".join(info["check"]))
    if on_hold(Day.today()):
        raise publish.PublishError(f"Uploads are on hold until {hold_until():%d %b} (PW_HOLD_UNTIL: the YouTube channel's new name)")
    if youtube is None and not connected():
        raise publish.PublishError("Connect the Encore Story YouTube channel first (dashboard → Encore Story → Connect YouTube)")
    client = youtube or publish._youtube(TOKEN)
    record = publish.upload_file(video_file(key, out_dir), upload_request(info, privacy, publish_at), True, progress, client)
    if thumb := thumb_path(key, out_dir):
        progress(0.99, "Setting the thumbnail")
        record["thumbnail"] = set_thumbnail(client, record["video_id"], thumb)
    info.setdefault("uploads", {})["youtube"] = record
    save_info(info, out_dir)
    if thumb and record.get("thumbnail") == "set":  # again once YouTube has processed the video, else it picks its own frame
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
    from trendclip import publish

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


# --------------------------------------------------------------------------- the comment to pin

COMMENT = "Which price tag should we explain next? Name a product below 👇"


def comment_text(script: dict) -> str:
    """The comment posted under the video for the owner to pin: the script's own question to viewers ("comment"),
    then the ask for the next topic."""
    own = " ".join(str(script.get("comment") or "").split())[:400]
    ask = BAND_COMMENT if is_band(script) else COMMENT
    return f"{own}\n\n{ask}" if own else ask


def can_comment() -> bool:
    from trendclip import publish

    return publish.has_scope(publish.COMMENT_SCOPE, TOKEN)


def comment(info: dict, youtube=None, out_dir: Path | None = None) -> dict | None:
    """Post the comment under the uploaded video (once), as Borderline Why does. The result goes in the upload record
    ("comment", or "comment_error": Discord offers a retry). Never raises."""
    from modules import bw_series
    from trendclip import publish

    record = (info.get("uploads") or {}).get("youtube") or {}
    if not record.get("video_id") or record.get("comment"):
        return record.get("comment")
    error = ""
    if record.get("privacy") == "private":
        error = "comments only work once the video is public or unlisted"
    elif youtube is None and not can_comment():
        error = "reconnect the Encore Story YouTube channel once to allow comments"
    else:
        try:
            text = info.get("comment") or COMMENT
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
    """The retry (Discord, or the command line for videos uploaded before comments): the comment record. Raises
    PublishError with the reason when it fails."""
    from trendclip import publish

    info = load_info(key, out_dir)
    if info is None or not (info.get("uploads") or {}).get("youtube"):
        raise publish.PublishError("That Encore Story video is not on YouTube")
    found = comment(info, youtube, out_dir)
    if found is None:
        raise publish.PublishError(info["uploads"]["youtube"].get("comment_error") or "the comment didn't go through")
    return found


def comment_line(record: dict) -> str:
    """The upload message's comment line: the pin reminder (the API can't pin), or why it didn't go through."""
    if found := record.get("comment"):
        return f"💬 Comment posted: “{found['text']}”. **Pin it** on YouTube (the comment's ⋮ → Pin); the API can't: <{found['url']}>"
    if error := record.get("comment_error"):
        return f"💬 The comment didn't go through: {error}"
    return ""


def publish_if_allowed(key: str, progress=lambda f, m: None, out_dir: Path | None = None, youtube=None, settings=None) -> dict | None:
    """The upload at the slot's time: the record, or None when the video may not go out (yet)."""
    info = load_info(key, out_dir)
    if info is None or not ready(info) or not auto_post():
        return None
    if found := (info.get("uploads") or {}).get("youtube"):
        return found
    from trendclip import publish

    post_at = datetime.fromisoformat(info["job"]["post_at"]) if (info.get("job") or {}).get("post_at") else None
    return upload(key, None, progress, youtube, out_dir, settings, publish.scheduled(post_at))


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
    from trendclip import publish

    if post_at is not None and settings is not None and (now or assistant.now(settings)) >= post_at - publish.EARLY_UPLOAD and auto_post():
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
    if found.get("video_id") and (found.get("privacy") != "private" or found.get("scheduled_for")):
        from googleapiclient.errors import HttpError

        youtube = youtube or publish._youtube(TOKEN)
        try:
            youtube.videos().update(part="status", body={"id": found["video_id"], "status": {"privacyStatus": "private"}}).execute()
        except HttpError as err:
            save_info(info, out_dir)
            raise publish.PublishError(publish._api_error_text(err).replace("YouTube upload failed", "YouTube refused the change")) from err
        found["privacy"] = "private"
        found.pop("scheduled_for", None)  # the schedule is gone too
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
    from trendclip import publish

    lb, b = channel_common.link_button, channel_common.button
    text = f"⬆️ **{info.get('title', info.get('key', ''))}** is on YouTube ({publish.privacy_note(found)}): {found.get('url', '')}"
    if line := comment_line(found):
        text += "\n" + line
    return announce_text(text, settings, channel_common.rows(
        lb("▶️ Watch", found.get("url", "")) if found.get("url") else None, b("pwveto", info["key"], "🚫 Make it private", "danger"),
        b("pwcm", info["key"], "💬 Post the comment", "primary") if found.get("comment_error") else None,
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
    c = sub.add_parser("comment", help="post the comment to pin under uploaded videos that have none yet")
    c.add_argument("keys", nargs="*", help="video keys (default: every uploaded one)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if args.cmd == "comment":
        keys = args.keys or sorted(p.parent.name for p in out_root(None).glob("*/info.json"))
        for key in keys:
            info = load_info(key)
            if not info or not (info.get("uploads") or {}).get("youtube"):
                continue
            try:
                print(key, comment_line({"comment": comment_again(key)}))
            except Exception as err:  # noqa: BLE001
                print(key, "failed:", err)
        return 0
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
