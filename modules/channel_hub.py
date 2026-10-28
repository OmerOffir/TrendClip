"""Send Dark Outliers and Borderline Why videos to the hub (the Linux machine that runs the bot), the way
the gameplay channel's Shorts go (trendclip/hub.py has the Discord side and the message format).

The hub decides whether a time is free, so two videos never take one slot:
  darkarchives   a slot of DA_SLOTS with nothing on the hub's plan (output/darkarchives/plan.json). The
                 video arrives with its story: the hub saves the story in its own bank (or finds it there by
                 source_id) and marks it USED, with the stories a ranking counts down, so the hub never tells
                 them again; the video becomes story_<hub id>.mp4, is planned at the time and posted in the
                 channel's Discord channel with "Don't post this".
  borderlinewhy  a slot of BW_SLOTS with no video yet, not being made: the video becomes that slot's
                 (output/borderlinewhy/<date>_<HHMM>/), so the bot doesn't make one, and is posted in the
                 channel's Discord channel with Approve / Don't post / Upload now, like the hub's own.

On this machine a sent video is marked "sent" (its info JSON) and leaves the local plan; the hub's plan is
the one that counts.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date as Day, datetime, timedelta
from pathlib import Path
from typing import Any

from trendclip import hub
from trendclip.hub import HubError

logger = logging.getLogger(__name__)

DAYS = 7  # the free times and the plan cover this many days from today
MAKING_GRACE = 30 * 60  # a Borderline Why slot folder touched this recently (no video yet) is being made


def _now(settings, now: datetime | None = None) -> datetime:
    return (now or datetime.now(settings.tz)).astimezone(settings.tz)


def _sent_record(reply: dict[str, Any]) -> dict[str, Any]:
    return {"date": reply["date"], "slot": reply["slot"], "ref": reply.get("ref"),
            "at": datetime.now().replace(microsecond=0).isoformat()}


# --------------------------------------------------------------------------- Dark Outliers


class DarkOutliers:
    name = "darkarchives"
    label = "Dark Outliers"

    # ---- hub side
    def free(self, settings, now: datetime | None = None) -> list[dict[str, str]]:
        from modules import da_plan

        now = _now(settings, now)
        plan = da_plan.load()
        out = []
        for n in range(DAYS):
            day = now.date() + timedelta(days=n)
            for slot in da_plan.slots():
                if da_plan.slot_time(settings, day, slot) > now and da_plan._taken(plan, day, slot) is None:
                    out.append({"date": day.isoformat(), "slot": slot})
        return out

    def plan(self, settings, now: datetime | None = None) -> dict[str, Any]:
        from modules import da_plan

        now = _now(settings, now)
        last = now.date() + timedelta(days=DAYS - 1)
        items = [i for i in da_plan.load().items if now.date() <= i.date <= last]
        return {"today": now.date().isoformat(), "days": DAYS, "slots": da_plan.slots(), "free": self.free(settings, now),
                "items": [{"date": i.date.isoformat(), "slot": i.slot, "title": i.title,
                           "state": "posted" if da_plan.is_posted(i) else "planned"}
                          for i in sorted(items, key=lambda i: (i.date, i.slot))]}

    def receive(self, settings, day: Day, slot: str, bundle: dict[str, Any], video: Path,
                now: datetime | None = None) -> dict[str, Any]:
        from db import repository
        from modules import da_channel, da_plan, script_gen, video_gen

        if slot not in da_plan.slots():
            raise HubError(f"{slot} is not a Dark Outliers time on the hub ({', '.join(da_plan.slots())})", 409,
                           self.free(settings, now))
        if da_plan.slot_time(settings, day, slot) <= _now(settings, now):
            raise HubError(f"{day.isoformat()} {slot} has already passed on the hub", 409, self.free(settings, now))
        if other := da_plan.slot_item(day, slot):
            raise HubError(f"{day.isoformat()} {slot} already has \"{other.title}\" on the hub. Pick another time.", 409,
                           self.free(settings, now))
        story = bundle.get("story") or {}
        if not story.get("source_id"):
            raise HubError("The video came without its story")
        repository.init_db()
        found = repository.find_source(story["source_id"])
        if found and da_plan.video_info(found["id"]) is not None:
            raise HubError(f"The hub already has a video of \"{found['title']}\" (story {found['id']})", 409)
        if found is None:
            repository.save_story(story["source_id"], story.get("source_name") or "", story.get("title") or "",
                                  story.get("raw_text") or "", story.get("url") or "", story.get("score") or 0,
                                  pillar=story.get("pillar") if story.get("pillar") in repository.PILLARS else None)
            found = repository.find_source(story["source_id"])
        story_id = found["id"]
        repository.set_status(story_id, "USED", note="video sent from the other machine")
        for source_id in bundle.get("used") or []:  # the stories a ranking counted down
            if (told := repository.find_source(source_id)) and told["status"] != "USED":
                repository.set_status(told["id"], "USED", note=f"in ranking {story_id} (sent from the other machine)")
        target = video_gen.video_path(story_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(video, target)
        info = {**(bundle.get("info") or {}), "story_id": story_id, "filename": target.name, "uploads": {},
                "received_at": datetime.now().replace(microsecond=0).isoformat()}
        info.pop("sent", None)
        video_gen.info_path(story_id).write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if script := bundle.get("script"):
            path = script_gen.script_path(story_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({**script, "story_id": story_id}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        try:
            item = da_plan.add_item(da_plan.ItemRequest(story_id=story_id, date=day, slot=slot))
        except ValueError as err:
            raise HubError(str(err), 409, self.free(settings, now)) from err
        da_channel.announce_video(story_id, info, settings,
                                  note=f"📥 From the other machine · planned for {day.isoformat()} {slot}.",
                                  components=da_channel.video_buttons(story_id, item.id))
        return {"date": day.isoformat(), "slot": slot, "title": info.get("title", ""), "ref": story_id}

    # ---- sending side
    def bundle(self, ref: int | str) -> tuple[bytes, dict[str, Any]]:
        from db import repository
        from modules import da_plan, script_gen, video_gen

        story_id = int(ref)
        info = da_plan.video_info(story_id)
        if info is None:
            raise HubError(f"Story {story_id} has no video on this machine", 404)
        if (info.get("uploads") or {}).get("youtube"):
            raise HubError(f"\"{info.get('title')}\" is already on YouTube", 409)
        story = repository.get_story(story_id)
        if story is None:
            raise HubError(f"Story {story_id} is not in this machine's story bank", 404)
        script = None
        path = script_gen.script_path(story_id)
        if path.is_file():
            try:
                script = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                script = None
        used = []
        for entry in (script or {}).get("ranking") or []:
            if (told := repository.get_story(entry.get("story_id") or 0)) is not None:
                used.append(told["source_id"])
        keep = ("source_id", "source_name", "title", "raw_text", "url", "score", "pillar")
        return video_gen.video_path(story_id).read_bytes(), {
            "story": {k: story.get(k) for k in keep}, "info": info, "script": script, "used": used}

    def after_send(self, ref: int | str, reply: dict[str, Any]) -> None:
        from modules import da_plan, video_gen

        story_id = int(ref)
        path = video_gen.info_path(story_id)
        info = json.loads(path.read_text(encoding="utf-8"))
        info.setdefault("sent", {})["hub"] = _sent_record(reply)
        path.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for item in da_plan.load().items:
            if item.story_id == story_id:
                da_plan.delete_item(item.id)

    def planned(self, settings, now: datetime | None = None) -> list[dict[str, Any]]:
        """This machine's upcoming plan: what "Send plan to Linux" sends, at the same day and time."""
        from modules import da_plan

        now = _now(settings, now)
        out = []
        for item in sorted(da_plan.load().items, key=lambda i: (i.date, i.slot)):
            if da_plan.slot_time(settings, item.date, item.slot) <= now or da_plan.is_posted(item):
                continue
            out.append({"ref": item.story_id, "date": item.date, "slot": item.slot, "title": item.title})
        return out


# --------------------------------------------------------------------------- Borderline Why


class BorderlineWhy:
    name = "borderlinewhy"
    label = "Borderline Why"

    def _is_free(self, job, now: datetime) -> bool:
        from modules import bw_channel

        if job.post_at <= now or bw_channel.load_info(job.key) is not None:
            return False
        folder = bw_channel.out_root() / job.key
        return not (folder.is_dir() and time.time() - folder.stat().st_mtime < MAKING_GRACE)

    # ---- hub side
    def free(self, settings, now: datetime | None = None) -> list[dict[str, str]]:
        from modules import bw_channel

        now = _now(settings, now)
        return [{"date": job.day.isoformat(), "slot": job.slot}
                for n in range(DAYS) for job in bw_channel.jobs_for(settings, now.date() + timedelta(days=n))
                if self._is_free(job, now)]

    def plan(self, settings, now: datetime | None = None) -> dict[str, Any]:
        from modules import bw_channel

        now = _now(settings, now)
        items = [{"date": d["day"], "slot": r["slot"], "title": (r["video"] or {}).get("title", ""), "state": r["state"]}
                 for d in bw_channel.plan_days(settings, now, before=0, after=DAYS - 1) for r in d["slots"] if r["video"]]
        return {"today": now.date().isoformat(), "days": DAYS, "slots": bw_channel.slots(), "free": self.free(settings, now),
                "items": items}

    def receive(self, settings, day: Day, slot: str, bundle: dict[str, Any], video: Path,
                now: datetime | None = None) -> dict[str, Any]:
        from modules import bw_channel, bw_topics

        now = _now(settings, now)
        if slot not in bw_channel.slots():
            raise HubError(f"{slot} is not a Borderline Why time on the hub ({', '.join(bw_channel.slots())})", 409,
                           self.free(settings, now))
        job = bw_channel.job_for(settings, day, slot)
        if job.post_at <= now:
            raise HubError(f"{day.isoformat()} {slot} has already passed on the hub", 409, self.free(settings, now))
        if not self._is_free(job, now):
            have = bw_channel.load_info(job.key)
            what = f"\"{have['title']}\"" if have else "a video being made right now"
            raise HubError(f"{day.isoformat()} {slot} already has {what} on the hub. Pick another time.", 409,
                           self.free(settings, now))
        try:
            bw_channel.set_aside(job.key)  # a failed make's leftovers
        except bw_channel.ChannelError as err:
            raise HubError(str(err), 409, self.free(settings, now)) from err
        folder = bw_channel.out_root() / job.key
        folder.mkdir(parents=True, exist_ok=True)
        if script := bundle.get("script"):
            (folder / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(video, bw_channel.video_path(job.key))
        info = {**(bundle.get("info") or {}), "key": job.key, "uploads": {},
                "job": {"day": day.isoformat(), "slot": slot, "post_at": job.post_at.isoformat(timespec="minutes")},
                "received_at": datetime.now().replace(microsecond=0).isoformat()}
        for gone in ("sent", "veto"):
            info.pop(gone, None)
        info["check"] = bw_channel.check_video(info)
        bw_channel.save_info(info)
        if info.get("article"):
            try:
                bw_topics.mark(bw_channel.out_root(), info["article"], used=True)
            except Exception as err:  # noqa: BLE001 - a topic the hub's list doesn't have
                logger.info("Borderline Why topic %s not marked on the hub: %s", info["article"], err)
        bw_channel.announce_video(info, settings)
        return {"date": day.isoformat(), "slot": slot, "title": info.get("title", ""), "ref": job.key}

    # ---- sending side
    def bundle(self, ref: int | str) -> tuple[bytes, dict[str, Any]]:
        from modules import bw_channel

        key = str(ref)
        info = bw_channel.load_info(key)
        if info is None:
            raise HubError(f"No Borderline Why video {key} on this machine", 404)
        if (info.get("uploads") or {}).get("youtube"):
            raise HubError(f"\"{info.get('title')}\" is already on YouTube", 409)
        if info.get("check"):
            raise HubError(f"\"{info.get('title')}\" failed its check: " + "; ".join(info["check"]), 409)
        script_file = bw_channel.out_root() / key / "script.json"
        script = None
        if script_file.is_file():
            try:
                script = json.loads(script_file.read_text(encoding="utf-8"))
            except ValueError:
                script = None
        return bw_channel.video_path(key).read_bytes(), {"info": {k: v for k, v in info.items() if k != "key"}, "script": script}

    def after_send(self, ref: int | str, reply: dict[str, Any]) -> None:
        from modules import bw_channel

        info = bw_channel.load_info(str(ref))
        if info is not None:
            info.setdefault("sent", {})["hub"] = _sent_record(reply)
            bw_channel.save_info(info)

    def planned(self, settings, now: datetime | None = None) -> list[dict[str, Any]]:
        """The videos made here for a slot still to come, not sent, vetoed or failed: sent at their slot."""
        from modules import bw_channel

        now = _now(settings, now)
        out = []
        for info in bw_channel.videos():
            job = info.get("job") or {}
            if not job.get("post_at") or datetime.fromisoformat(job["post_at"]) <= now:
                continue
            if (info.get("uploads") or {}).get("youtube") or (info.get("sent") or {}).get("hub") or info.get("check") \
                    or bw_channel.is_vetoed(info):
                continue
            out.append({"ref": info["key"], "date": Day.fromisoformat(job["day"]), "slot": job["slot"], "title": info.get("title", "")})
        return sorted(out, key=lambda r: (r["date"], r["slot"]))


CHANNELS = {c.name: c for c in (DarkOutliers(), BorderlineWhy())}


def channel(name: str):
    try:
        return CHANNELS[name]
    except KeyError:
        raise HubError(f"Unknown channel {name!r} (one of {', '.join(CHANNELS)})", 404) from None


# --------------------------------------------------------------------------- the hub's answers


async def handle(settings, words: list[str], message: Any, discord_channel: Any) -> tuple[str, tuple[str, bytes] | None]:
    """Hub side: answer a cask / cplan / csend message (see trendclip/hub.py). (reply, file to attach)."""
    import asyncio

    op, nonce = words[1], words[2]
    partial = settings.shorts_dir / f".incoming-{nonce}.part"
    try:
        ch = channel(words[3] if len(words) > 3 else "")
        if op == "cask":
            return hub._reply("free", nonce, {"free": await asyncio.to_thread(ch.free, settings)}), None
        if op == "cplan":
            plan = await asyncio.to_thread(ch.plan, settings)
            return hub._reply("week", nonce, {}), ("plan.json", json.dumps(plan, ensure_ascii=False).encode("utf-8"))
        if len(words) != 7 or not message.attachments:
            raise HubError("The send message is incomplete")
        day, slot, ids = Day.fromisoformat(words[4]), words[5], words[6].split(",")
        bundle = json.loads(await message.attachments[0].read())
        await hub.read_parts(discord_channel, ids, partial)
        result = await asyncio.to_thread(ch.receive, settings, day, slot, bundle, partial)
    except HubError as err:
        return hub._reply("fail", nonce, {"message": str(err), "free": err.free}), None
    except Exception as err:  # noqa: BLE001 - the sender is told instead of waiting for a reply forever
        logger.exception("Hub: %s for %s failed", op, words[3] if len(words) > 3 else "?")
        return hub._reply("fail", nonce, {"message": f"The hub couldn't take the video ({type(err).__name__}: {err})"}), None
    finally:
        partial.unlink(missing_ok=True)
    return hub._reply("done", nonce, result), None


# --------------------------------------------------------------------------- the sending machine


def _ask(settings, op: str, name: str) -> dict[str, Any]:
    channel(name)
    try:
        data = hub._ask(settings, op, name)
    except HubError as err:
        if err.status == 504:
            raise HubError(f"{err} If it is, update the code there (git pull) and restart it: older code doesn't "
                           f"know {channel(name).label}.", 504) from err
        raise
    if "message" in data:  # a "fail" reply
        raise HubError(data["message"], 502)
    return data


def remote_free(settings, name: str) -> list[dict[str, str]]:
    return _ask(settings, "cask", name)["free"]


def remote_plan(settings, name: str) -> dict[str, Any]:
    data = _ask(settings, "cplan", name)
    if "file" not in data:
        raise HubError("The Linux bot answered without its plan. Update the code there (git pull) and restart it.", 502)
    return data["file"]


def send(settings, name: str, ref: int | str, day: Day, slot: str) -> dict[str, Any]:
    """Sending side: check the time on the hub, then post the video to it. The hub's reply
    {"date", "slot", "title", "ref"}; a refused time raises HubError with the free ones."""
    ch = channel(name)
    hub._require_sender(settings)
    hour, minute = map(int, slot.split(":"))
    if datetime(day.year, day.month, day.day, hour, minute, tzinfo=settings.tz) <= datetime.now(settings.tz):
        raise HubError(f"{day.isoformat()} {slot} has already passed. Pick a later time.", 409, remote_free(settings, name))
    free = remote_free(settings, name)
    if {"date": day.isoformat(), "slot": slot} not in free:
        raise HubError(f"{day.isoformat()} {slot} is already taken on the Linux plan. Pick another time.", 409, free)
    video, bundle = ch.bundle(ref)
    op, reply = hub.send_parts(settings, video, f"{hub.PREFIX} csend {{nonce}} {name} {day.isoformat()} {slot}",
                               ("bundle.json", json.dumps(bundle, ensure_ascii=False).encode("utf-8")))
    if op != "done":
        raise HubError(reply.get("message") or "The hub refused the video", 409, reply.get("free"))
    ch.after_send(ref, reply)
    logger.info("Sent %s %s to the hub: %s %s", name, ref, reply["date"], reply["slot"])
    return reply


def planned(settings, name: str) -> list[dict[str, Any]]:
    return [{**p, "date": p["date"].isoformat()} for p in channel(name).planned(settings)]


def send_plan(settings, name: str) -> list[dict[str, Any]]:
    """Sending side: every upcoming planned video goes to the hub at its own day and time; one Discord or
    hub outage stops the rest (they would fail the same way)."""
    ch = channel(name)
    hub._require_sender(settings)
    results = []
    for p in ch.planned(settings):
        line = {"title": p["title"], "date": p["date"].isoformat(), "slot": p["slot"], "ok": False}
        try:
            reply = send(settings, name, p["ref"], p["date"], p["slot"])
        except (HubError, ValueError, OSError) as err:
            message = str(err)
            if isinstance(err, HubError) and err.free:
                message += f" Next free: {err.free[0]['date']} {err.free[0]['slot']}."
            results.append({**line, "message": message})
            if isinstance(err, HubError) and err.status in (502, 504):
                break
            continue
        results.append({**line, "ok": True, "message": "On the Linux plan."})
    return results
