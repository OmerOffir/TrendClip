"""Price Tag Why tab (modules.pw_channel): the channel's YouTube login (the shared callback saves its token in
channels/pricetagwhy/), the week's plan (each slot's video, or the queued script it will be made from) with Approve /
Don't post / Upload now / Make now, the scripts with their rule check and sources, and the made videos. The bot runs
the same day by itself."""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from modules import pw_channel, pw_write
from trendclip import publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pricetagwhy")
_procs: dict[str, Any] = {}  # key -> a make process started from the tab
_upload: dict[str, Any] = {"running": False}
_lock = threading.Lock()


def _settings():
    from trendclip.config import get_settings

    return get_settings()


class MakeRequest(BaseModel):
    file: str


class SlotRequest(BaseModel):
    day: date
    slot: str


class UploadRequest(BaseModel):
    privacy: str = ""


def _making() -> list[str]:
    """Videos being made: started from the tab, or by the bot (seen in their logs)."""
    mine = [key for key, proc in _procs.items() if proc.poll() is None]
    return sorted({*mine, *pw_channel.making_now()})


def _progress() -> dict[str, dict[str, Any]]:
    return {key: p for key in _making() if (p := pw_channel.make_progress(key))}


@router.get("/status")
def status() -> dict[str, Any]:
    settings = _settings()
    return {"name": pw_channel.NAME, "handle": pw_channel.handle(), "voice": pw_channel.VOICE, "making": _making(), "progress": _progress(),
            "folder": pw_channel.DIR.is_dir(), "youtube": pw_channel.youtube_status(), "discord": bool(pw_channel.discord_channel_id()),
            "slots": pw_channel.slots(), "lead_hours": pw_channel.lead_hours(), "timezone": settings.timezone,
            "require_approval": pw_channel.require_approval(), "privacy": pw_channel.upload_privacy(),
            "auto_make": pw_channel.auto_make(), "queued": len(pw_channel.queued()), "upload": dict(_upload),
            "hold_until": pw_channel.hold_text()}


@router.get("/plan")
def plan() -> dict[str, Any]:
    return {**pw_channel.plan_view(_settings()), "making": _making()}


@router.get("/scripts")
def scripts() -> dict[str, Any]:
    return {"scripts": pw_channel.scripts()}


@router.get("/videos")
def videos() -> dict[str, Any]:
    return {"videos": pw_channel.videos(), "making": _making()}


@router.get("/videos/{key}/video.mp4", include_in_schema=False)
def video_file(key: str) -> FileResponse:
    path = pw_channel.video_path(key)
    if path is None:
        raise HTTPException(status_code=404, detail="No such video")
    return FileResponse(path, media_type="video/mp4")


@router.get("/videos/{key}/thumb.jpg", include_in_schema=False)
def thumb_file(key: str) -> FileResponse:
    path = pw_channel.thumb_path(key)
    if path is None:
        raise HTTPException(status_code=404, detail="No thumbnail")
    return FileResponse(path, media_type="image/jpeg")


def _start(script_path, key: str, job=None) -> dict[str, Any]:
    if _making():
        raise HTTPException(status_code=409, detail="A video is already being made")
    _procs[key] = pw_channel.start_make(script_path, key, job=job)
    return {"key": key, "making": _making()}


@router.post("/make")
def make(req: MakeRequest) -> dict[str, Any]:
    """A sample from one script (not for a slot: its script stays in the queue)."""
    if not re.fullmatch(r"[\w.-]+\.json", req.file):
        raise HTTPException(status_code=400, detail="Not a script file")
    path = pw_channel.find_script(req.file)
    if path is None:
        raise HTTPException(status_code=404, detail="No such script")
    found = next((s for s in pw_channel.scripts() if s["file"] == req.file), None)
    if found and found["check"] and not found["rules_skipped"]:
        raise HTTPException(status_code=400, detail="The script breaks the rules: " + "; ".join(found["check"]))
    return _start(path, f"sample-{datetime.now():%Y%m%d-%H%M}-{path.stem}")


@router.post("/make-slot")
def make_slot(req: SlotRequest) -> dict[str, Any]:
    """A slot's video now, from the next queued script (a video already there is set aside first)."""
    settings = _settings()
    if req.slot not in pw_channel.slots():
        raise HTTPException(status_code=400, detail=f"{req.slot} is not a Price Tag Why time ({', '.join(pw_channel.slots())})")
    job = pw_channel.job_for(settings, req.day, req.slot)
    if pw_channel.on_hold(job.day):
        raise HTTPException(status_code=400, detail=f"Slots are on hold until {pw_channel.hold_until():%d %b}: make a sample from the Scripts list instead")
    script = pw_channel.script_for(job)
    if script is None:
        raise HTTPException(status_code=400, detail="No script left in the queue: add one to content/pricetagwhy/scripts/")
    if _making():
        raise HTTPException(status_code=409, detail="A video is already being made")
    try:
        pw_channel.set_aside(job.key)
    except pw_channel.ChannelError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    _mark_made(job, settings)
    return _start(script, job.key, job)


class PlaceRequest(SlotRequest):
    video: str = ""  # a sample's key
    script: str = ""  # a script file name


class ScriptRequest(BaseModel):
    file: str


def _slot_job(req: SlotRequest):
    settings = _settings()
    if req.slot not in pw_channel.slots():
        raise HTTPException(status_code=400, detail=f"{req.slot} is not a Price Tag Why time ({', '.join(pw_channel.slots())})")
    job = pw_channel.job_for(settings, req.day, req.slot)
    if datetime.now(settings.tz) >= job.post_at:
        raise HTTPException(status_code=400, detail="That slot has passed")
    return job


def _plan_change(fn, *args) -> dict[str, Any]:
    try:
        fn(*args)
    except pw_channel.ChannelError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return plan()


@router.post("/plan/place")
def place(req: PlaceRequest) -> dict[str, Any]:
    """Put a made video (a sample) or a chosen script on a slot."""
    job = _slot_job(req)
    if req.video:
        if req.video in _making():
            raise HTTPException(status_code=409, detail="That video is still being made")
        _mark_made(job)
        return _plan_change(pw_channel.place_video, req.video, job)
    if not req.script:
        raise HTTPException(status_code=400, detail="Choose a video or a script")
    return _plan_change(pw_channel.pin_script, job, req.script)


@router.post("/plan/remove")
def remove_slot(req: SlotRequest) -> dict[str, Any]:
    """Empty a slot that has a video (it is set aside; its script leaves the plan). The bot does not refill it."""
    job = pw_channel.job_for(_settings(), req.day, req.slot)
    _mark_made(job)
    return _plan_change(pw_channel.remove_slot, job)


@router.post("/scripts/remove")
def remove_script(req: ScriptRequest) -> dict[str, Any]:
    """Take a script out of the plan; it stays in the Scripts list."""
    if pw_channel.find_script(req.file) is None:
        raise HTTPException(status_code=404, detail="No such script")
    return _plan_change(pw_channel.remove_script, req.file)


@router.post("/scripts/restore")
def restore_script(req: ScriptRequest) -> dict[str, Any]:
    if pw_channel.find_script(req.file) is None:
        raise HTTPException(status_code=404, detail="No such script")
    return _plan_change(pw_channel.restore_script, req.file)


def _mark_made(job, settings=None) -> None:
    """Tell the bot this slot is taken care of from here (the sent log), so it never starts its own video for it: a
    hand-made video has no such mark, and the bot filled a slot emptied from the tab within the minute."""
    from trendclip import assistant

    assistant.SentLog(settings or _settings()).add(job.make_key, job.day)


def _known(key: str) -> dict:
    info = pw_channel.load_info(key)
    if info is None:
        raise HTTPException(status_code=404, detail="No such video")
    return info


@router.post("/videos/{key}/approve")
def approve(key: str) -> dict[str, Any]:
    _known(key)
    try:
        result = pw_channel.approve(key, _settings())
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=f"Approved, but the upload failed: {err}") from err
    return {"title": result["title"], "record": result["record"],
            "post_at": result["post_at"].isoformat(timespec="minutes") if result["post_at"] else None}


@router.post("/videos/{key}/veto")
def veto(key: str) -> dict[str, Any]:
    _known(key)
    try:
        return pw_channel.veto(key)
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post("/videos/{key}/upload")
def upload(key: str, req: UploadRequest) -> dict[str, Any]:
    """Upload now, on a thread (GET /upload-job follows it)."""
    info = _known(key)
    if (info.get("uploads") or {}).get("youtube"):
        raise HTTPException(status_code=400, detail="Already on YouTube")
    if not pw_channel.connected():
        raise HTTPException(status_code=400, detail="Connect the Price Tag Why YouTube channel first")
    privacy = req.privacy if req.privacy in ("public", "unlisted", "private") else None
    with _lock:
        if _upload.get("running"):
            raise HTTPException(status_code=409, detail="An upload is already running")
        _upload.clear()
        _upload.update(running=True, key=key, message="Starting", error=None, result=None)

    def progress(fraction, message):
        with _lock:
            _upload.update(message=message, fraction=fraction)

    def run():
        try:
            record = pw_channel.upload(key, privacy, progress, settings=_settings())
            with _lock:
                _upload.update(running=False, result=record.get("url"), message="Done")
        except Exception as err:  # noqa: BLE001 - shown in the tab
            logger.exception("Price Tag Why upload of %s failed", key)
            with _lock:
                _upload.update(running=False, error=str(err), message="Failed")

    threading.Thread(target=run, daemon=True, name="pw-upload").start()
    return dict(_upload)


@router.get("/upload-job")
def upload_job() -> dict[str, Any]:
    with _lock:
        return dict(_upload)


def _login(request: Request) -> str:
    from .app import _callback_url

    try:
        return pw_channel.start_login(_callback_url(request))
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/youtube/connect", include_in_schema=False)
def youtube_connect_page(request: Request) -> RedirectResponse:
    """Open in a browser on the dashboard machine: straight to Google's login."""
    return RedirectResponse(_login(request))


@router.post("/youtube/connect")
def youtube_connect(request: Request) -> dict[str, Any]:
    return {"auth_url": _login(request)}


@router.post("/youtube/logout")
def youtube_logout() -> dict[str, Any]:
    pw_channel.logout()
    return {"connected": False}


# --------------------------------------------------------------------------- topics and writing with any LLM (modules.pw_write)


class TopicRequest(BaseModel):
    question: str
    pillar: str = ""
    format: str = "band"  # Encore Story adds band ideas (2026-11-12)


class StartRequest(BaseModel):
    question: str = ""
    count: int = pw_write.DEFAULT_COUNT
    format: str = "band"  # band | why | story | world (pw_write.FORMATS); Encore Story writes band stories


class PasteRequest(BaseModel):
    text: str


def _write(fn, *args) -> dict[str, Any]:
    try:
        return fn(*args)
    except pw_write.WriteError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


def _hub_topics(strict: bool = False) -> tuple[list[dict[str, Any]] | None, str]:
    """(the Linux hub's topic list, "") on the sending machine, asked through Discord: the one the scripts are written
    from, so two machines never write from different lists. (None, "") on the hub itself (this machine's list);
    (None, the error) when Linux doesn't answer, or HTTP 502 when `strict`."""
    from modules import channel_hub
    from trendclip import hub

    if not hub.is_sender(_settings()):
        return None, ""
    try:
        return channel_hub.remote_view(_settings(), "pricetagwhy")["topics"], ""
    except (hub.HubError, KeyError) as err:
        if strict:
            raise HTTPException(status_code=502, detail=f"Couldn't read the Linux machine's topics: {err}") from err
        return None, str(err)


@router.get("/topics")
def topics() -> dict[str, Any]:
    """The topic list: the Linux hub's on the sending machine ("source": "linux"), this machine's otherwise."""
    remote, error = _hub_topics()
    found = pw_write.topics() if remote is None else remote
    return {"topics": found, "left": sum(1 for t in found if not t["used"]), "pillars": list(pw_write.BAND_PILLARS),
            "source": "here" if remote is None else "linux", "hub_error": error}


@router.post("/topics")
def add_topic(req: TopicRequest) -> dict[str, Any]:
    """A question for the list; on the sending machine it goes to the Linux hub's list through Discord, the one the
    scripts are written from there."""
    from modules import channel_hub
    from trendclip import hub

    if hub.is_sender(_settings()):
        try:
            sent = channel_hub.add_one(_settings(), "pricetagwhy", {"question": req.question, "pillar": req.pillar, "format": req.format})
        except hub.HubError as err:
            raise HTTPException(status_code=err.status if 400 <= err.status < 600 else 502, detail=str(err)) from err
        return {**topics(), "sent": sent}
    _write(pw_write.add_topic, req.question, req.pillar, None, req.format)
    return topics()


@router.get("/llm-topics")
def llm_topics_prompt(count: int = 20, format: str = "band") -> dict[str, Any]:
    """The prompt for new topics (or, format=story, new money-story ideas), to paste into any LLM."""
    return {"prompt": pw_write.llm_prompt(max(1, min(40, count)), None, _hub_topics()[0], format)}


class LLMText(BaseModel):
    text: str


@router.post("/llm-topics")
def llm_topics_add(req: LLMText) -> dict[str, Any]:
    """The LLM's topics into the list; on the sending machine into the Linux hub's list through Discord."""
    from modules import channel_hub
    from trendclip import hub, llm_json

    try:
        return channel_hub.add_topics(_settings(), "pricetagwhy", req.text,
                                      lambda text: llm_json.parse(text, pw_write.Proposals, pw_write.WriteError))
    except (pw_write.WriteError, hub.HubError) as err:
        status = getattr(err, "status", 400)
        raise HTTPException(status_code=status if 400 <= status < 600 else 502, detail=str(err)) from err


@router.post("/topics/first")
def first_topic(req: TopicRequest) -> dict[str, Any]:
    """The topic the next written script takes."""
    _write(pw_write.move_first, req.question)
    return topics()


@router.post("/topics/remove")
def remove_topic(req: TopicRequest) -> dict[str, Any]:
    pw_write.remove_topic(req.question)
    return topics()


@router.get("/write/{kind}")
def write_view(kind: str) -> dict[str, Any]:
    from trendclip import hub

    remote = _hub_topics()[0] if pw_write.load(kind) is None else None  # the next topics shown before Start
    return {**_write(pw_write.view, kind, None, remote), "sender": hub.is_sender(_settings())}


@router.post("/write/{kind}")
def write_start(kind: str, req: StartRequest) -> dict[str, Any]:
    """A new writing session, from the Linux hub's topic list on the sending machine."""
    return _write(pw_write.start, kind, req.question, req.count, None, _hub_topics(strict=True)[0], req.format)


@router.post("/write/{kind}/answer")
def write_answer(kind: str, req: PasteRequest) -> dict[str, Any]:
    return _write(pw_write.answer, kind, req.text)


@router.post("/write/{kind}/facts")
def write_facts(kind: str, req: PasteRequest) -> dict[str, Any]:
    return _write(pw_write.facts, kind, req.text)


@router.post("/write/{kind}/accept")
def write_accept(kind: str) -> dict[str, Any]:
    return _write(pw_write.accept, kind)


@router.post("/write/{kind}/skip-rules")
def write_skip_rules(kind: str) -> dict[str, Any]:
    return _write(pw_write.skip_rules, kind)


@router.post("/write/{kind}/skip-facts")
def write_skip_facts(kind: str) -> dict[str, Any]:
    return _write(pw_write.skip_facts, kind)


@router.post("/write/{kind}/drop/{i}")
def write_drop(kind: str, i: int) -> dict[str, Any]:
    return _write(pw_write.drop, kind, i)


@router.post("/write/{kind}/queue")
def write_queue(kind: str) -> dict[str, Any]:
    """Into the queue, with the slot each script lands on (Plan) so the tab can say when it goes out. On the sending
    machine the scripts go to the Linux hub's queue through Discord, and the slots are the hub's."""
    from modules import channel_hub
    from trendclip import hub

    settings = _settings()
    if hub.is_sender(settings):
        try:
            return _write(pw_write.queue, kind, None, lambda bundle: channel_hub.queue_scripts(settings, "pricetagwhy", bundle))
        except hub.HubError as err:
            raise HTTPException(status_code=err.status if 400 <= err.status < 600 else 502, detail=str(err)) from err
    done = _write(pw_write.queue, kind)
    return {**done, "slots": pw_channel.landing(settings, done["queued"])}
