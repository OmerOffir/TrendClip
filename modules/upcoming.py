"""Everything planned to go out, every channel in one list (the dashboard's Upcoming tab).

One row per upload slot of the coming week for SideQuestLogic (trendclip.planner), Dark Outliers
(modules.da_plan) and Borderline Why (modules.bw_channel): the video that takes it and its state, or an
empty slot shown as such, so a gap is visible before a video is planned twice to fill it. A video planned
more than once is flagged on every one of its rows.
"""

from __future__ import annotations

from datetime import date as Day, datetime, timedelta
from typing import Any

DAYS = 7

CHANNELS = {
    "sidequestlogic": "SideQuestLogic",
    "darkarchives": "Dark Outliers",
    "borderlinewhy": "Borderline Why",
}


def _row(channel: str, day: str, time: str, **extra) -> dict[str, Any]:
    return {"channel": channel, "label": CHANNELS[channel], "day": day, "time": time, "title": "", "seconds": None,
            "state": "empty", "url": "", "ref": None, "twice": False, **extra}


def _passed(day: str, time: str, now: datetime) -> bool:
    return f"{day} {time}" < now.strftime("%Y-%m-%d %H:%M")


def sidequest_rows(settings, now: datetime, days: list[str]) -> list[dict[str, Any]]:
    from trendclip import planner

    view = planner.view(settings)
    rows = []
    taken = set()
    for item in view["items"]:
        if item["date"] not in days:
            continue
        time = item.get("slot") or ""
        taken.add((item["date"], time))
        info = item.get("short_info") or {}
        done = item.get("done")
        yt = (item.get("status") or {}).get("youtube") or {}
        rows.append(_row("sidequestlogic", item["date"], time, title=item.get("title") or info.get("title", ""),
                         seconds=info.get("duration_seconds"), ref=item.get("short") or item["id"],
                         state="posted" if done else "missing" if item.get("short_missing") else
                         "late" if time and _passed(item["date"], time, now) else "waits",
                         url=yt.get("url", ""), kind=item.get("kind", "")))
    for day in days:
        for slot in view["slots"]:
            if (day, slot) not in taken:
                rows.append(_row("sidequestlogic", day, slot, state="passed" if _passed(day, slot, now) else "empty"))
    return rows


def dark_rows(now: datetime, days: list[str]) -> list[dict[str, Any]]:
    from modules import da_plan

    view = da_plan.view()
    rows = []
    taken = set()
    for item in view["items"]:
        if item["date"] not in days:
            continue
        taken.add((item["date"], item["slot"]))
        yt = item["youtube"]
        video = item.get("video") or {}
        rows.append(_row("darkarchives", item["date"], item["slot"], title=item.get("title") or video.get("title", ""),
                         seconds=video.get("duration_seconds"), ref=item["story_id"], url=yt.get("url", ""),
                         state="posted" if yt["done"] else "missing" if item.get("video_missing") else
                         "late" if _passed(item["date"], item["slot"], now) else "waits"))
    for day in days:
        for slot in view["slots"]:
            if (day, slot) not in taken:
                rows.append(_row("darkarchives", day, slot, state="passed" if _passed(day, slot, now) else "empty"))
    return rows


def borderline_rows(settings, now: datetime, days: list[str]) -> list[dict[str, Any]]:
    from modules import bw_channel

    rows = []
    for day in bw_channel.plan_days(settings, now, before=0, after=DAYS - 1):
        if day["day"] not in days:
            continue
        for r in day["slots"]:
            video = r.get("video") or {}
            state = r["state"]
            if video:
                state = {"posted": "posted", "blocked": "blocked", "vetoed": "vetoed", "approved": "waits",
                         "waiting": "approval"}.get(state, state)
            else:
                state = {"missed": "passed", "due": "making", "later": "empty"}.get(state, state)
            yt = video.get("youtube") or {}
            rows.append(_row("borderlinewhy", day["day"], r["slot"], title=video.get("title", ""),
                             seconds=video.get("duration_seconds"), ref=video.get("key"), url=yt.get("url", "") if yt else "",
                             state=state, made_at=r.get("start", "")))
    return rows


def view(settings, now: datetime | None = None) -> dict[str, Any]:
    """{"now", "days": [{"day", "weekday", "today", "rows": [...]}], "empty": n, "twice": n}: the week's
    slots of every channel in time order."""
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    days = [(now.date() + timedelta(days=n)).isoformat() for n in range(DAYS)]
    rows: list[dict[str, Any]] = []
    for source in (lambda: sidequest_rows(settings, now, days), lambda: dark_rows(now, days),
                   lambda: borderline_rows(settings, now, days)):
        try:
            rows += source()
        except Exception as err:  # noqa: BLE001 - one channel's trouble never hides the others
            rows.append(_row(_source_name(source), days[0], "", state="error", title=str(err)[:120]))
    seen: dict[tuple[str, Any], int] = {}
    for r in rows:
        if r["ref"] is not None and r["state"] not in ("posted",):
            seen[(r["channel"], r["ref"])] = seen.get((r["channel"], r["ref"]), 0) + 1
    for r in rows:
        r["twice"] = r["ref"] is not None and seen.get((r["channel"], r["ref"]), 0) > 1
    rows.sort(key=lambda r: (r["day"], r["time"], r["channel"]))
    out_days = []
    for day in days:
        day_rows = [r for r in rows if r["day"] == day]
        out_days.append({"day": day, "weekday": Day.fromisoformat(day).strftime("%A"), "today": day == days[0], "rows": day_rows})
    return {"now": now.isoformat(timespec="minutes"), "days": out_days,
            "empty": sum(1 for r in rows if r["state"] == "empty"), "twice": sum(1 for r in rows if r["twice"]),
            "channels": CHANNELS, "gemini": gemini_status(settings, now)}


def gemini_status(settings, now: datetime) -> dict[str, Any] | None:
    """Today's use of Gemini's free tier (trendclip.gemini_quota.status), None when it can't be read."""
    try:
        from trendclip import gemini_quota

        return gemini_quota.status(tz=settings.tz, now=now)
    except Exception as err:  # noqa: BLE001 - a side note on the page, never the reason it fails
        return {"error": str(err)[:120]}


def local_unsent(settings) -> list[dict[str, Any]]:
    """On the sending machine: the videos planned here that are not on the Linux plan yet, per channel
    (what each channel's "Send plan to Linux" sends)."""
    from modules import channel_hub
    from trendclip import hub

    out = [{"channel": "sidequestlogic", "label": CHANNELS["sidequestlogic"], "day": i.date.isoformat(),
            "time": i.slot or "", "title": i.title} for i in hub.planned(settings)]
    for name in ("darkarchives", "borderlinewhy"):
        try:
            out += [{"channel": name, "label": CHANNELS[name], "day": p["date"], "time": p["slot"], "title": p["title"]}
                    for p in channel_hub.planned(settings, name)]
        except Exception:  # noqa: BLE001 - one channel's trouble never hides the others
            continue
    return sorted(out, key=lambda r: (r["day"], r["time"], r["channel"]))


def _source_name(source) -> str:
    code = source.__code__.co_names
    return "sidequestlogic" if "sidequest_rows" in code else "darkarchives" if "dark_rows" in code else "borderlinewhy"
