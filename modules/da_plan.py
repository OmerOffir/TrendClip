"""Dark Outliers plan: which video goes out on which day and time, and the bot's post at that time.

The plan lives in output/darkarchives/plan.json, apart from the gameplay channel's planner. Every day
has the slots of DA_SLOTS (default 14:30 and 23:00, in Settings.timezone); a slot holds one video. With
DA_AUTO_POST the Discord bot uploads the slot's video to the Dark Outliers YouTube channel at its time
(DA_UPLOAD_PRIVACY, default public) and reports in the channel's Discord channel. A video already on
YouTube counts as posted.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from datetime import date as Day, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from modules import video_gen
from modules.scene_images import setting

logger = logging.getLogger(__name__)

DEFAULT_SLOTS = "14:30,23:00"  # two a day, like the gameplay channel (DA_SLOTS)
POST_WINDOW = timedelta(hours=2)  # a slot's post missed by more than this (bot was off) is skipped
_lock = threading.Lock()


class PlanItem(BaseModel):
    id: str
    date: Day
    slot: str
    story_id: int
    title: str = ""
    posted: dict[str, datetime] = Field(default_factory=dict)  # "youtube" -> when it was marked or uploaded
    created_at: datetime = Field(default_factory=lambda: datetime.now().replace(microsecond=0))


class Plan(BaseModel):
    items: list[PlanItem] = Field(default_factory=list)


class ItemRequest(BaseModel):
    story_id: int = Field(ge=1)
    date: Day
    slot: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")  # None: the day's first free slot


class ItemUpdate(BaseModel):
    date: Day | None = None
    slot: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")


def slots() -> list[str]:
    """DA_SLOTS: upload times, "HH:MM,HH:MM", sorted (default 14:30 and 23:00)."""
    found = sorted({s.strip() for s in setting("DA_SLOTS", DEFAULT_SLOTS).split(",") if re.fullmatch(r"\s*([01]\d|2[0-3]):[0-5]\d\s*", s)})
    return found or DEFAULT_SLOTS.split(",")


def auto_post() -> bool:
    return setting("DA_AUTO_POST", "1").strip().lower() not in ("0", "false", "off", "none")


def upload_privacy() -> str:
    value = setting("DA_UPLOAD_PRIVACY", "public").strip().lower()
    return value if value in ("private", "unlisted", "public") else "public"


def plan_path(out_dir: Path | None = None) -> Path:
    return (out_dir or video_gen.OUTPUT_DIR) / "plan.json"


def load(out_dir: Path | None = None) -> Plan:
    try:
        return Plan.model_validate_json(plan_path(out_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Plan()


def _save(plan: Plan, out_dir: Path | None = None) -> None:
    path = plan_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def _find(plan: Plan, item_id: str) -> PlanItem:
    for item in plan.items:
        if item.id == item_id:
            return item
    raise KeyError(item_id)


def _taken(plan: Plan, day: Day, slot: str, except_id: str | None = None) -> PlanItem | None:
    return next((i for i in plan.items if i.date == day and i.slot == slot and i.id != except_id), None)


def _first_free(plan: Plan, day: Day, except_id: str | None = None) -> str | None:
    return next((s for s in slots() if _taken(plan, day, s, except_id) is None), None)


def video_info(story_id: int, out_dir: Path | None = None) -> dict | None:
    path = video_gen.info_path(story_id, out_dir)
    if not path.is_file() or not video_gen.video_path(story_id, out_dir).is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def videos(out_dir: Path | None = None) -> list[dict]:
    """Every finished video (output/darkarchives/story_<id>.json), newest first."""
    out = []
    for story_id in video_gen.existing_video_ids(out_dir):
        if info := video_info(story_id, out_dir):
            out.append(info)
    return sorted(out, key=lambda i: i.get("created_at", ""), reverse=True)


def add_item(req: ItemRequest, out_dir: Path | None = None) -> PlanItem:
    with _lock:
        plan = load(out_dir)
        info = video_info(req.story_id, out_dir)
        if info is None:
            raise ValueError(f"Story {req.story_id} has no video yet")
        slot = req.slot or _first_free(plan, req.date)
        if slot is None:
            raise ValueError(f"{req.date.isoformat()} is full ({', '.join(slots())})")
        if slot not in slots():
            raise ValueError(f"The slots are {', '.join(slots())} (DA_SLOTS)")
        if other := _taken(plan, req.date, slot):
            raise ValueError(f"{req.date.isoformat()} {slot} already has \"{other.title}\"; move or remove it first")
        item = PlanItem(id=secrets.token_hex(6), date=req.date, slot=slot, story_id=req.story_id, title=info.get("title", ""))
        plan.items.append(item)
        _save(plan, out_dir)
        return item


def update_item(item_id: str, req: ItemUpdate, out_dir: Path | None = None) -> PlanItem:
    with _lock:
        plan = load(out_dir)
        item = _find(plan, item_id)
        if req.date is not None:
            item.date = req.date
        if req.slot is not None:
            if req.slot not in slots():
                raise ValueError(f"The slots are {', '.join(slots())} (DA_SLOTS)")
            item.slot = req.slot
        elif req.date is not None and _taken(plan, item.date, item.slot, item.id):
            item.slot = _first_free(plan, item.date, item.id) or item.slot
        if other := _taken(plan, item.date, item.slot, item.id):
            raise ValueError(f"{item.date.isoformat()} {item.slot} already has \"{other.title}\"; move or remove it first")
        _save(plan, out_dir)
        return item


def delete_item(item_id: str, out_dir: Path | None = None) -> bool:
    with _lock:
        plan = load(out_dir)
        before = len(plan.items)
        plan.items = [i for i in plan.items if i.id != item_id]
        if len(plan.items) == before:
            return False
        _save(plan, out_dir)
        return True


def set_posted(item_id: str, posted: bool, out_dir: Path | None = None) -> PlanItem:
    with _lock:
        plan = load(out_dir)
        item = _find(plan, item_id)
        if posted:
            item.posted.setdefault("youtube", datetime.now().replace(microsecond=0))
        else:
            item.posted.pop("youtube", None)
        _save(plan, out_dir)
        return item


def slot_item(day: Day, slot: str, out_dir: Path | None = None) -> PlanItem | None:
    return _taken(load(out_dir), day, slot)


def slot_time(settings, day: Day, slot: str) -> datetime:
    hour, minute = map(int, slot.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=settings.tz)


def next_free_slot(settings, now: datetime | None = None, out_dir: Path | None = None, days: int = 30) -> tuple[Day, str] | None:
    """The first slot from now on with nothing planned (today's passed slots are skipped)."""
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    plan = load(out_dir)
    for n in range(days):
        day = now.date() + timedelta(days=n)
        for slot in slots():
            if slot_time(settings, day, slot) > now and _taken(plan, day, slot) is None:
                return day, slot
    return None


def planned_dates(out_dir: Path | None = None) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for item in load(out_dir).items:
        out.setdefault(item.story_id, []).append(f"{item.date.isoformat()} {item.slot}")
    return {k: sorted(v) for k, v in out.items()}


# --------------------------------------------------------------------------- the bot's post


def post_key(day: Day, slot: str) -> str:
    return f"da {day.isoformat()} {slot} post"


def due_posts(settings, at: datetime, sent: set[str], out_dir: Path | None = None) -> list[tuple[str, Day, str]]:
    """(key, day, slot) for every slot of today whose time has come (within POST_WINDOW) with a video
    planned and not yet posted, when DA_AUTO_POST is on."""
    if not auto_post():
        return []
    at = at.astimezone(settings.tz)
    day = at.date()
    out = []
    for slot in slots():
        when = slot_time(settings, day, slot)
        key = post_key(day, slot)
        if key in sent or not when <= at < when + POST_WINDOW:
            continue
        item = slot_item(day, slot, out_dir)
        if item is not None and not is_posted(item, out_dir):
            out.append((key, day, slot))
    return out


def is_posted(item: PlanItem, out_dir: Path | None = None) -> bool:
    info = video_info(item.story_id, out_dir) or {}
    return bool(item.posted.get("youtube")) or bool((info.get("uploads") or {}).get("youtube"))


def post_now(item: PlanItem, progress=lambda f, m: None, out_dir: Path | None = None, upload=None) -> dict:
    """Upload the planned video (DA_UPLOAD_PRIVACY) and mark the item posted; a video already on YouTube
    is not uploaded twice. `upload`: da_channel.upload (tests pass a fake)."""
    from modules import da_channel

    info = video_info(item.story_id, out_dir)
    if info is None:
        raise ValueError(f"The video for story {item.story_id} is not on this machine")
    record = (info.get("uploads") or {}).get("youtube")
    if not record:
        record = (upload or da_channel.upload)(item.story_id, upload_privacy(), progress, out_dir=out_dir)
    set_posted(item.id, True, out_dir)
    return record


# --------------------------------------------------------------------------- views


def item_view(item: PlanItem, out_dir: Path | None = None) -> dict[str, Any]:
    info = video_info(item.story_id, out_dir)
    upload = ((info or {}).get("uploads") or {}).get("youtube") or {}
    data = item.model_dump(mode="json")
    data["video"] = None if info is None else {"title": info.get("title", ""), "duration_seconds": info.get("duration_seconds"),
                                                "filename": info.get("filename", "")}
    data["youtube"] = {"done": bool(upload) or bool(item.posted.get("youtube")), "url": upload.get("url", ""),
                       "privacy": upload.get("privacy", ""), "studio_url": upload.get("studio_url", "")}
    data["video_missing"] = info is None
    return data


def view(out_dir: Path | None = None) -> dict[str, Any]:
    plan = load(out_dir)
    items = sorted(plan.items, key=lambda i: (i.date, i.slot, i.created_at))
    planned = planned_dates(out_dir)
    return {
        "slots": slots(), "auto_post": auto_post(), "privacy": upload_privacy(),
        "items": [item_view(i, out_dir) for i in items],
        "videos": [{**v, "planned": planned.get(v.get("story_id"), [])} for v in videos(out_dir)],
    }
