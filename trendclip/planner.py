"""Plan tab backend: which reel or long video goes out on which day, and where it is already posted.

The plan lives in output/planner.json. An item is either one of the Shorts made here (linked by
filename) or anything else you upload (a long video made elsewhere), just a title. "Posted" is per
platform: marking a linked Short also marks it in the Upload tab, and a YouTube upload done from
the dashboard counts automatically.
"""

from __future__ import annotations

import secrets
import threading
from datetime import date as Day, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from . import shorts
from .config import Settings
from .models import utcnow

Platform = Literal["youtube", "instagram", "tiktok"]
Kind = Literal["reel", "long"]
PLATFORMS: tuple[Platform, ...] = ("youtube", "instagram", "tiktok")
# Daily upload times (in Settings.timezone); each slot of a day holds at most one item.
Slot = Literal["18:00", "23:00"]
SLOTS: tuple[Slot, ...] = ("18:00", "23:00")


class Goals(BaseModel):
    reels_per_day: int = Field(2, ge=0, le=10)
    long_per_week: int = Field(2, ge=0, le=14)
    reel_platforms: list[Platform] = Field(default_factory=lambda: list(PLATFORMS))
    long_platforms: list[Platform] = Field(default_factory=lambda: ["youtube"])

    @field_validator("reel_platforms", "long_platforms")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return [p for p in PLATFORMS if p in value]


class PlanItem(BaseModel):
    id: str
    kind: Kind
    date: Day
    slot: Slot | None = None
    title: str = ""
    short: str | None = None  # filename in output/shorts when it is a Short made here
    notes: str = ""
    platforms: list[Platform] = Field(default_factory=lambda: list(PLATFORMS))
    posted: dict[str, datetime] = Field(default_factory=dict)  # platform -> when it was marked
    created_at: datetime = Field(default_factory=utcnow)


class Plan(BaseModel):
    goals: Goals = Field(default_factory=Goals)
    items: list[PlanItem] = Field(default_factory=list)
    slots_assigned: bool = False  # plans from before upload slots got their times once


class ItemRequest(BaseModel):
    kind: Kind = "reel"
    date: Day
    slot: Slot | Literal[""] | None = None  # None: a reel takes the day's first free slot; "": no fixed time
    title: str = Field("", max_length=150)
    short: str | None = Field(None, max_length=200)
    notes: str = Field("", max_length=500)
    platforms: list[Platform] | None = None  # default: the goal's platforms for this kind


class ItemUpdate(BaseModel):
    kind: Kind | None = None
    date: Day | None = None
    slot: Slot | Literal[""] | None = None  # "" clears the slot
    title: str | None = Field(None, max_length=150)
    short: str | None = Field(None, max_length=200)
    unlink: bool = False  # drop the linked Short (the item becomes a plain title)
    notes: str | None = Field(None, max_length=500)
    platforms: list[Platform] | None = None


_lock = threading.Lock()


def plan_path(settings: Settings) -> Path:
    return settings.output_dir / "planner.json"


def load(settings: Settings) -> Plan:
    path = plan_path(settings)
    try:
        return Plan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Plan()


def _save(settings: Settings, plan: Plan) -> None:
    path = plan_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)


def _find(plan: Plan, item_id: str) -> PlanItem:
    for item in plan.items:
        if item.id == item_id:
            return item
    raise KeyError(item_id)


def _platforms(plan: Plan, kind: Kind, wanted: list[str] | None) -> list[Platform]:
    if wanted is None:
        wanted = plan.goals.reel_platforms if kind == "reel" else plan.goals.long_platforms
    out = [p for p in PLATFORMS if p in wanted]
    if not out:
        raise ValueError("Pick at least one platform")
    return out


def _slot_taken_by(plan: Plan, item: PlanItem) -> PlanItem | None:
    if item.slot is None:
        return None
    return next((o for o in plan.items if o.id != item.id and o.date == item.date and o.slot == item.slot), None)


def _check_slot(plan: Plan, item: PlanItem) -> None:
    if other := _slot_taken_by(plan, item):
        raise ValueError(f"{item.date.isoformat()} {item.slot} already has \"{other.title}\"; "
                         "move or remove it first")


def _first_free(plan: Plan, day: Day, except_id: str | None = None) -> Slot | None:
    taken = {i.slot for i in plan.items if i.date == day and i.id != except_id}
    return next((s for s in SLOTS if s not in taken), None)


def assign_slots(settings: Settings, from_day: Day) -> int:
    """Once per plan: reels planned before upload slots existed get their day's free slots, in the order
    they were planned (from `from_day` on). Returns how many got a time."""
    with _lock:
        plan = load(settings)
        if plan.slots_assigned:
            return 0
        count = 0
        for item in sorted(plan.items, key=lambda i: i.created_at):
            if item.kind == "reel" and item.slot is None and item.date >= from_day:
                item.slot = _first_free(plan, item.date, item.id)
                count += item.slot is not None
        plan.slots_assigned = True
        _save(settings, plan)
        return count


def _link(settings: Settings, item: PlanItem, filename: str) -> None:
    """Point the item at a Short (title copied, so it survives the Short being deleted)."""
    short = shorts.get_short(settings, filename)
    item.short = filename
    item.title = item.title or short.title
    if not short.ready:
        shorts.set_ready(settings, filename, True)  # so it shows up in the Upload tab


def add_item(settings: Settings, req: ItemRequest) -> PlanItem:
    with _lock:
        plan = load(settings)
        slot = req.slot or None
        if req.slot is None and req.kind == "reel":
            slot = _first_free(plan, req.date)
        item = PlanItem(id=secrets.token_hex(6), kind=req.kind, date=req.date, slot=slot,
                        title=req.title.strip(), notes=req.notes.strip(),
                        platforms=_platforms(plan, req.kind, req.platforms))
        _check_slot(plan, item)
        if req.short:
            _link(settings, item, req.short)
        if not item.title:
            raise ValueError("Give it a title or pick one of your Shorts")
        plan.items.append(item)
        _save(settings, plan)
        return item


def update_item(settings: Settings, item_id: str, req: ItemUpdate) -> PlanItem:
    with _lock:
        plan = load(settings)
        item = _find(plan, item_id)
        if req.kind is not None:
            item.kind = req.kind
        moved = req.date is not None and req.date != item.date
        if req.date is not None:
            item.date = req.date
        if req.slot is not None:
            item.slot = req.slot or None
        elif moved and item.slot is not None and _slot_taken_by(plan, item):
            item.slot = _first_free(plan, item.date, item.id)  # its time is taken there: the free one, if any
        _check_slot(plan, item)
        if req.title is not None:
            item.title = req.title.strip()
        if req.notes is not None:
            item.notes = req.notes.strip()
        if req.platforms is not None:
            item.platforms = _platforms(plan, item.kind, req.platforms)
        if req.unlink:
            item.short = None
        elif req.short and req.short != item.short:
            item.title = req.title.strip() if req.title else ""
            _link(settings, item, req.short)
        if not item.title:
            raise ValueError("Give it a title or pick one of your Shorts")
        _save(settings, plan)
        return item


def delete_item(settings: Settings, item_id: str) -> bool:
    with _lock:
        plan = load(settings)
        before = len(plan.items)
        plan.items = [i for i in plan.items if i.id != item_id]
        if len(plan.items) == before:
            return False
        _save(settings, plan)
        return True


def set_goals(settings: Settings, goals: Goals) -> Goals:
    with _lock:
        plan = load(settings)
        plan.goals = goals
        _save(settings, plan)
        return goals


def set_posted(settings: Settings, item_id: str, platform: Platform, posted: bool) -> PlanItem:
    """Mark the item as uploaded (or not) on one platform; a linked Short is updated too."""
    with _lock:
        plan = load(settings)
        item = _find(plan, item_id)
        if posted:
            item.posted.setdefault(platform, utcnow())
        else:
            item.posted.pop(platform, None)
        if item.short:
            try:
                shorts.update_short(settings, item.short, lambda s: s.posted.__setitem__(platform, posted))
            except (ValueError, FileNotFoundError):
                pass  # the Short was deleted; the plan keeps its own record
        _save(settings, plan)
        return item


def find_item(settings: Settings, item_id: str) -> PlanItem:
    return _find(load(settings), item_id)


def slot_item(settings: Settings, day: Day, slot: Slot) -> PlanItem | None:
    return next((i for i in load(settings).items if i.date == day and i.slot == slot), None)


def free_slots(settings: Settings, day: Day) -> list[Slot]:
    taken = {i.slot for i in load(settings).items if i.date == day}
    return [s for s in SLOTS if s not in taken]


def slot_time(settings: Settings, day: Day, slot: Slot) -> datetime:
    """The slot as an aware datetime in the configured time zone."""
    hour, minute = map(int, slot.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=settings.tz)


def next_free_slot(settings: Settings, now: datetime | None = None, days: int = 30) -> tuple[Day, Slot] | None:
    """The first slot from now on with nothing planned (today's passed slots are skipped)."""
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    for n in range(days):
        day = now.date() + timedelta(days=n)
        for slot in free_slots(settings, day):
            if slot_time(settings, day, slot) > now:
                return day, slot
    return None


# --------------------------------------------------------------------------- views


def short_status(short: shorts.ShortVideo | None) -> dict[str, dict[str, Any]]:
    """Per platform: done, and whether the dashboard did the upload itself (auto) with its link."""
    out: dict[str, dict[str, Any]] = {}
    for p in PLATFORMS:
        out[p] = {"done": False, "auto": False, "url": ""}
        if short is None:
            continue
        upload = short.uploads.get(p) if p == "youtube" else None
        if upload:
            out[p] = {"done": True, "auto": True, "url": upload.get("url", ""),
                      "scheduled_for": upload.get("scheduled_for")}
        elif short.posted.get(p):
            out[p]["done"] = True
    return out


def item_view(item: PlanItem, short: shorts.ShortVideo | None) -> dict[str, Any]:
    status = short_status(short)
    for p in PLATFORMS:
        if p in item.posted:
            status[p]["at"] = item.posted[p].isoformat()
            if short is None:  # a linked Short is the source of truth (the Upload tab edits it too)
                status[p]["done"] = True
    data = item.model_dump(mode="json")
    data["status"] = {p: status[p] for p in item.platforms}
    data["done"] = all(status[p]["done"] for p in item.platforms)
    data["short_info"] = None if short is None else {
        "title": short.title, "game": short.game, "duration_seconds": short.duration_seconds,
        "part": short.part, "parts_total": short.parts_total, "ready": short.ready,
    }
    data["short_missing"] = bool(item.short) and short is None
    return data


def view(settings: Settings) -> dict[str, Any]:
    """Everything the Plan tab shows: goals, all items with live status, and Shorts to pick from."""
    plan = load(settings)
    all_shorts = {s.filename: s for s in shorts.list_shorts(settings)}
    planned: dict[str, list[str]] = {}
    for item in plan.items:
        if item.short:
            planned.setdefault(item.short, []).append(item.date.isoformat())
    items = sorted(plan.items, key=lambda i: (i.date, i.kind != "reel", i.slot or "99", i.created_at))
    return {
        "goals": plan.goals.model_dump(mode="json"),
        "slots": list(SLOTS),
        "items": [item_view(i, all_shorts.get(i.short) if i.short else None) for i in items],
        "shorts": [{
            "filename": s.filename, "title": s.title, "game": s.game, "duration_seconds": s.duration_seconds,
            "part": s.part, "parts_total": s.parts_total, "ready": s.ready,
            "created_at": s.created_at.isoformat(), "planned": sorted(planned.get(s.filename, [])),
            "status": short_status(s),
        } for s in all_shorts.values()],
    }
