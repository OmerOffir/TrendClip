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
    return [key for key, proc in _procs.items() if proc.poll() is None]


@router.get("/status")
def status() -> dict[str, Any]:
    settings = _settings()
    return {"name": pw_channel.NAME, "handle": pw_channel.handle(), "voice": pw_channel.VOICE, "making": _making(),
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
    if found and found["check"]:
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
    left = pw_channel.queued()
    if not left:
        raise HTTPException(status_code=400, detail="No script left in the queue: add one to content/pricetagwhy/scripts/")
    if _making():
        raise HTTPException(status_code=409, detail="A video is already being made")
    try:
        pw_channel.set_aside(job.key)
    except pw_channel.ChannelError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return _start(left[0], job.key, job)


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


class StartRequest(BaseModel):
    question: str = ""
    count: int = pw_write.DEFAULT_COUNT


class PasteRequest(BaseModel):
    text: str


def _write(fn, *args) -> dict[str, Any]:
    try:
        return fn(*args)
    except pw_write.WriteError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/topics")
def topics() -> dict[str, Any]:
    found = pw_write.topics()
    return {"topics": found, "left": sum(1 for t in found if not t["used"]), "pillars": list(pw_write.PILLARS)}


@router.post("/topics")
def add_topic(req: TopicRequest) -> dict[str, Any]:
    _write(pw_write.add_topic, req.question, req.pillar)
    return topics()


@router.post("/topics/remove")
def remove_topic(req: TopicRequest) -> dict[str, Any]:
    pw_write.remove_topic(req.question)
    return topics()


@router.get("/write/{kind}")
def write_view(kind: str) -> dict[str, Any]:
    return _write(pw_write.view, kind)


@router.post("/write/{kind}")
def write_start(kind: str, req: StartRequest) -> dict[str, Any]:
    return _write(pw_write.start, kind, req.question, req.count)


@router.post("/write/{kind}/answer")
def write_answer(kind: str, req: PasteRequest) -> dict[str, Any]:
    return _write(pw_write.answer, kind, req.text)


@router.post("/write/{kind}/facts")
def write_facts(kind: str, req: PasteRequest) -> dict[str, Any]:
    return _write(pw_write.facts, kind, req.text)


@router.post("/write/{kind}/drop/{i}")
def write_drop(kind: str, i: int) -> dict[str, Any]:
    return _write(pw_write.drop, kind, i)


@router.post("/write/{kind}/queue")
def write_queue(kind: str) -> dict[str, Any]:
    return _write(pw_write.queue, kind)
