"""Two machines, one plan, joined through Discord.

The hub (HUB_ROLE=hub) is the always-on machine: it keeps the plan and runs the Discord bot. The
sender (HUB_ROLE=sender) posts its finished Shorts to the hub channel with the same bot token over
Discord's REST API only, so it never logs in as a second bot. The hub decides whether an upload
time is free, so two videos never take the same slot, and a taken time comes back with the free ones.

Messages (all written by the bot itself; the hub ignores anyone else's):
    tc-hub ask <nonce>                                           -> tc-hub free <nonce> {"free": [...]}
    tc-hub plan <nonce>                                          -> tc-hub week <nonce> {} + plan.json
    tc-hub part <nonce> <i>/<n>          + attachment: a piece of the MP4 (Discord caps files at 10 MB)
    tc-hub send <nonce> <date> <slot> <filename> <part ids>  + short.json -> tc-hub done|fail <nonce> {...}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from datetime import date as Day, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import planner, shorts
from .config import Settings
from .models import utcnow

logger = logging.getLogger(__name__)

PREFIX = "tc-hub"
REQUESTS = ("ask", "send", "plan")
REPLIES = ("free", "week", "done", "fail")  # never a request word, or the hub would answer itself
FREE_DAYS = 7
PART_BYTES = 8 * 1024 * 1024
ASK_WAIT = 30.0
SEND_WAIT = 180.0
POLL_EVERY = 1.5
API = "https://discord.com/api/v10"


class HubError(RuntimeError):
    def __init__(self, message: str, status: int = 400, free: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.free = free or []


class SendRequest(BaseModel):
    filename: str = Field(min_length=5, max_length=200)
    date: Day
    slot: planner.Slot


def channel_id(settings: Settings) -> int | None:
    return settings.hub_channel_id or settings.discord_channel_id


def is_hub(settings: Settings) -> bool:
    return settings.hub_role == "hub" and bool(channel_id(settings))


def is_sender(settings: Settings) -> bool:
    return settings.hub_role == "sender" and bool(channel_id(settings)) and settings.discord_bot_token is not None


def free_slots(settings: Settings, days: int = FREE_DAYS, now: datetime | None = None) -> list[dict[str, str]]:
    """Upload times still open on the hub, from now on."""
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    out = []
    for n in range(days):
        day = now.date() + timedelta(days=n)
        for slot in planner.free_slots(settings, day):
            if planner.slot_time(settings, day, slot) > now:
                out.append({"date": day.isoformat(), "slot": slot})
    return out


def _taken(settings: Settings, day: Day, slot: str) -> HubError:
    other = planner.slot_item(settings, day, slot)
    title = f'"{other.title}"' if other else "another video"
    return HubError(f"{day.isoformat()} {slot} already has {title}. Pick another time.", 409,
                    free_slots(settings))


def _target(settings: Settings, filename: str) -> Path:
    if Path(filename).name != filename or not filename.endswith(".mp4"):
        raise HubError(f"Not a Short: {filename}")
    target = settings.shorts_dir / filename
    n = 1
    while target.exists() or target.with_suffix(".json").exists():
        n += 1
        target = settings.shorts_dir / f"{Path(filename).stem}-{n}.mp4"
    return target


def receive(settings: Settings, filename: str, day: Day, slot: str,
            meta: dict[str, Any], video: Path) -> planner.PlanItem:
    """Hub side: keep the sent Short and put it on the plan at `slot`, or refuse a taken time."""
    slot = planner.OLD_SLOTS.get(slot, slot)
    if slot not in planner.SLOTS:
        raise HubError(f"Unknown upload time {slot}")
    if slot not in planner.free_slots(settings, day):
        raise _taken(settings, day, slot)
    try:
        short = shorts.ShortVideo.model_validate(meta)
    except ValueError as err:
        raise HubError(f"The Short's details are not readable: {err}") from err
    target = _target(settings, filename)
    short = short.model_copy(update={
        "filename": target.name, "ready": True, "ready_at": utcnow(), "uploads": {}, "posted": {}, "sent": {},
    })
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(video, target)
    target.with_suffix(".json").write_text(short.model_dump_json(indent=2), encoding="utf-8")
    kind = "long" if short.aspect == "landscape" else "reel"
    try:
        return planner.add_item(settings, planner.ItemRequest(kind=kind, date=day, slot=slot, short=target.name))
    except ValueError as err:
        shorts.delete_short(settings, target.name)
        raise HubError(str(err), 409, free_slots(settings)) from err


def plan_view(settings: Settings, days: int = FREE_DAYS, now: datetime | None = None) -> dict[str, Any]:
    """The hub's plan from today on, small enough to send back: what's on each time and what's open."""
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    first, last = now.date(), now.date() + timedelta(days=days - 1)
    items = sorted((i for i in planner.load(settings).items if first <= i.date <= last),
                   key=lambda i: (i.date, i.slot or "99"))
    return {
        "today": first.isoformat(), "days": days, "free": free_slots(settings, days, now),
        "items": [{"date": i.date.isoformat(), "slot": i.slot, "kind": i.kind, "title": i.title,
                   "platforms": i.platforms, "posted": sorted(i.posted)} for i in items],
    }


def _reply(op: str, nonce: str, data: dict[str, Any]) -> str:
    return f"{PREFIX} {op} {nonce} {json.dumps(data, ensure_ascii=False)}"


def is_request(content: str) -> bool:
    words = content.split(" ", 2)
    return len(words) == 3 and words[0] == PREFIX and words[1] in REQUESTS


async def handle(settings: Settings, message: Any,
                 channel: Any) -> tuple[str, planner.PlanItem | None, tuple[str, bytes] | None]:
    """Hub side: answer an `ask`, `plan` or `send` message. Returns the reply text, the planned item and
    a file to attach (the plan, which can be longer than a Discord message).

    `message` needs .content and .attachments (each with an async .read()); `channel` needs an
    async .fetch_message(id), as discord.py's objects have."""
    words = message.content.split(" ")
    op, nonce = words[1], words[2]
    if op == "ask":
        return _reply("free", nonce, {"free": free_slots(settings)}), None, None
    if op == "plan":
        plan = json.dumps(plan_view(settings), ensure_ascii=False).encode("utf-8")
        return _reply("week", nonce, {}), None, ("plan.json", plan)
    partial = settings.shorts_dir / f".incoming-{nonce}.part"
    try:
        if len(words) != 7 or not message.attachments:
            raise HubError("The send message is incomplete")
        day, slot, filename, ids = Day.fromisoformat(words[3]), words[4], words[5], words[6].split(",")
        meta = json.loads(await message.attachments[0].read())
        settings.shorts_dir.mkdir(parents=True, exist_ok=True)
        with partial.open("wb") as out:
            for mid in ids:
                part = await channel.fetch_message(int(mid))
                out.write(await part.attachments[0].read())
        item = await asyncio.to_thread(receive, settings, filename, day, slot, meta, partial)
    except HubError as err:
        return _reply("fail", nonce, {"message": str(err), "free": err.free}), None, None
    except Exception as err:  # noqa: BLE001 - the sender is told instead of waiting for a reply forever
        return _reply("fail", nonce, {"message": f"The hub couldn't take the video ({type(err).__name__}: {err})",
                                      "free": free_slots(settings)}), None, None
    finally:
        partial.unlink(missing_ok=True)
    return _reply("done", nonce, {"date": item.date.isoformat(), "slot": item.slot, "short": item.short,
                                  "title": item.title}), item, None


# --------------------------------------------------------------------------- the sending machine


class Rest:
    """The few Discord REST calls the sender needs, as the bot (no gateway, so no second bot online)."""

    def __init__(self, settings: Settings) -> None:
        self.channel = channel_id(settings)
        self.headers = {"Authorization": f"Bot {settings.discord_bot_token.get_secret_value()}"}

    def _call(self, method: str, path: str, **kw: Any) -> Any:
        import requests

        for _ in range(5):
            try:
                resp = requests.request(method, f"{API}{path}", headers=self.headers, timeout=120, **kw)
            except requests.RequestException as err:
                raise HubError(f"Can't reach Discord ({type(err).__name__})", 502) from err
            if resp.status_code == 429:
                time.sleep(float(resp.json().get("retry_after", 1.0)) + 0.1)
                continue
            if resp.status_code == 404 and method == "DELETE":
                return None
            if resp.status_code >= 400:
                raise HubError(f"Discord refused the request ({resp.status_code}): {resp.text[:200]}", 502)
            return resp.json() if resp.content else None
        raise HubError("Discord kept rate limiting; try again in a minute", 429)

    def post(self, content: str, file: tuple[str, bytes] | None = None,
             components: list[dict[str, Any]] | None = None) -> str:
        """`components`: Discord message components (rows of buttons); a button whose custom_id is
        `tc:<action>:<arg>` is answered by the bot like one it sent itself."""
        path = f"/channels/{self.channel}/messages"
        payload: dict[str, Any] = {"content": content}
        if components:
            payload["components"] = components
        if file is None:
            return self._call("POST", path, json=payload)["id"]
        return self._call("POST", path, data={"payload_json": json.dumps(payload)}, files={"files[0]": file})["id"]

    def after(self, message_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/channels/{self.channel}/messages", params={"after": message_id, "limit": 50})

    def delete(self, message_id: str) -> None:
        self._call("DELETE", f"/channels/{self.channel}/messages/{message_id}")

    def download(self, url: str) -> bytes:
        import requests

        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as err:
            raise HubError(f"Couldn't download the hub's answer ({type(err).__name__})", 502) from err
        return resp.content


def _await_reply(rest: Rest, nonce: str, after: str, wait: float) -> tuple[str, dict[str, Any]]:
    """The hub's reply to `nonce`; an attached JSON file comes back as data["file"]."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        for msg in rest.after(after):
            words = msg.get("content", "").split(" ", 3)
            if len(words) == 4 and words[0] == PREFIX and words[1] in REPLIES and words[2] == nonce:
                data = json.loads(words[3])
                if msg.get("attachments"):
                    data["file"] = json.loads(rest.download(msg["attachments"][0]["url"]))
                rest.delete(msg["id"])
                return words[1], data
        time.sleep(POLL_EVERY)
    raise HubError("The Linux bot didn't answer. Is it running there (systemctl --user status trendclip)?", 504)


def _require_sender(settings: Settings) -> None:
    if not is_sender(settings):
        raise HubError("Set HUB_ROLE=sender, DISCORD_BOT_TOKEN and a hub channel in .env to send videos", 400)


def _ask(settings: Settings, op: str) -> dict[str, Any]:
    _require_sender(settings)
    rest = Rest(settings)
    nonce = secrets.token_hex(4)
    ask = rest.post(f"{PREFIX} {op} {nonce}")
    try:
        _, data = _await_reply(rest, nonce, ask, ASK_WAIT)
    finally:
        rest.delete(ask)
    return data


def remote_free(settings: Settings) -> list[dict[str, str]]:
    """Sending side: ask the hub for its open upload times."""
    return _ask(settings, "ask")["free"]


def remote_plan(settings: Settings) -> dict[str, Any]:
    """Sending side: the hub's plan for the coming week (see plan_view)."""
    data = _ask(settings, "plan")
    if "file" not in data:
        raise HubError("The Linux bot answered without its plan. Update the code there (git pull) and restart it.", 502)
    return data["file"]


def send(settings: Settings, req: SendRequest) -> dict[str, Any]:
    """Sending side: check the time on the hub, then post the Short to it in 8 MB pieces."""
    path = shorts.short_path(settings, req.filename)
    short = shorts.get_short(settings, req.filename)
    if planner.slot_time(settings, req.date, req.slot) <= datetime.now(settings.tz):
        raise HubError(f"{req.date.isoformat()} {req.slot} has already passed. Pick a later time.", 409,
                       remote_free(settings))
    free = remote_free(settings)
    if {"date": req.date.isoformat(), "slot": req.slot} not in free:
        raise HubError(f"{req.date.isoformat()} {req.slot} is already taken on the hub. Pick another time.", 409, free)
    rest = Rest(settings)
    nonce = secrets.token_hex(4)
    data = path.read_bytes()
    pieces = [data[i:i + PART_BYTES] for i in range(0, len(data), PART_BYTES)]
    posted: list[str] = []
    try:
        for i, piece in enumerate(pieces, 1):
            posted.append(rest.post(f"{PREFIX} part {nonce} {i}/{len(pieces)}", (f"{nonce}.part{i}", piece)))
        posted.append(rest.post(
            f"{PREFIX} send {nonce} {req.date.isoformat()} {req.slot} {req.filename} {','.join(posted)}",
            ("short.json", short.model_dump_json().encode("utf-8"))))
        op, reply = _await_reply(rest, nonce, posted[-1], SEND_WAIT)
    finally:
        for mid in posted:
            rest.delete(mid)
    if op != "done":
        raise HubError(reply.get("message") or "The hub refused the video", 409, reply.get("free"))
    shorts.update_short(settings, req.filename, lambda s: s.sent.__setitem__(
        "hub", {"date": reply["date"], "slot": reply["slot"], "filename": reply["short"], "at": utcnow().isoformat()}))
    return reply


def send_plan(settings: Settings) -> list[dict[str, Any]]:
    """Sending side: send every upcoming planned Short to the hub at its planned time. Each one the hub
    takes leaves this machine's plan (the hub's plan is the one that counts); refused ones stay."""
    _require_sender(settings)
    today = datetime.now(settings.tz).date()
    results = []
    for item in sorted(planner.load(settings).items, key=lambda i: (i.date, i.slot or "")):
        if item.date < today or not item.short or item.posted:
            continue
        line = {"title": item.title, "date": item.date.isoformat(), "slot": item.slot, "ok": False}
        if item.slot is None:
            results.append({**line, "message": "No upload time; give it 14:30 or 23:00 first."})
            continue
        try:
            reply = send(settings, SendRequest(filename=item.short, date=item.date, slot=item.slot))
        except (HubError, ValueError, FileNotFoundError) as err:
            message = str(err)
            if isinstance(err, HubError) and err.free:
                message += f" Next free: {err.free[0]['date']} {err.free[0]['slot']}."
            results.append({**line, "message": message})
            if isinstance(err, HubError) and err.status in (502, 504):
                break  # Discord or the hub is down: the rest would fail the same way
            continue
        planner.delete_item(settings, item.id)
        results.append({**line, "ok": True, "message": f"On the Linux plan as {reply['short']}."})
    for r in results:
        logger.info("Send to hub: %s %s %s: %s", r["date"], r["slot"], r["title"], r["message"])
    return results
