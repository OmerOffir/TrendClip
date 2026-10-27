"""Borderline Why tab: the channel's YouTube login (the shared callback saves its token in
channels/borderlinewhy/), its status, its videos with Approve / Don't post / Upload, "make the next video
now", and the topic list (with questions added by hand). The bot runs the same day by itself (/bw)."""

from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from modules import bw_channel, bw_script, bw_topics
from trendclip import assistant, publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/borderlinewhy")
_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {"make": {"running": False}, "upload": {"running": False}}


def _settings():
    from trendclip.config import get_settings

    return get_settings()


def _job(name: str) -> dict[str, Any]:
    with _lock:
        return dict(_jobs[name])


def _start(name: str, fn, **meta) -> dict[str, Any]:
    """Run `fn(progress)` on a thread; one job of each kind at a time."""
    with _lock:
        if _jobs[name].get("running"):
            raise HTTPException(status_code=409, detail=f"A {name} job is already running")
        _jobs[name] = {"running": True, "message": "Starting", "fraction": None, "error": None, "result": None, **meta}

    def progress(fraction, message):
        with _lock:
            _jobs[name].update(message=message, **({"fraction": fraction} if fraction is not None else {}))

    def run():
        try:
            result = fn(progress)
            with _lock:
                _jobs[name].update(running=False, result=result, message="Done")
        except Exception as err:  # noqa: BLE001 - shown in the tab
            logger.exception("Borderline Why %s job failed", name)
            with _lock:
                _jobs[name].update(running=False, error=str(err), message="Failed")

    threading.Thread(target=run, daemon=True, name=f"bw-{name}").start()
    return _job(name)


@router.get("/status")
def status() -> dict[str, Any]:
    settings = _settings()
    nxt = bw_channel.next_job(settings, assistant.now(settings))
    return {"youtube": bw_channel.youtube_status(), "discord": bool(bw_channel.discord_channel_id()), "slots": bw_channel.slots(),
            "lead_hours": bw_channel.lead_hours(), "auto_make": bw_channel.auto_make(), "auto_post": bw_channel.auto_post(),
            "require_approval": bw_channel.require_approval(), "privacy": bw_channel.upload_privacy(), "timezone": settings.timezone,
            "next_job": nxt.view() if nxt else None, "topics": bw_topics.summary(bw_channel.out_root()),
            "queued": [p.name for p in bw_channel.queued_scripts()], "make": _job("make"), "upload": _job("upload")}


def _texts(info: dict) -> dict[str, Any]:
    req = bw_channel.upload_request(info)
    return {"title": publish._yt_title(req.title, True), "description": req.description, "tags": publish._tags(req.tags)}


def _state(info: dict) -> str:
    if (info.get("uploads") or {}).get("youtube"):
        return "posted"
    if info.get("check"):
        return "blocked"
    if bw_channel.is_vetoed(info):
        return "vetoed"
    if bw_channel.is_approved(info) or not bw_channel.require_approval():
        return "approved"
    return "waiting"


@router.get("/videos")
def videos() -> dict[str, Any]:
    out = []
    for info in bw_channel.videos():
        out.append({**info, "state": _state(info), "youtube": _texts(info)})
    return {"videos": out, "privacy": bw_channel.upload_privacy(), "require_approval": bw_channel.require_approval()}


@router.get("/videos/{key}/video.mp4", include_in_schema=False)
def video_file(key: str) -> FileResponse:
    if bw_channel.load_info(key) is None:
        raise HTTPException(status_code=404, detail="No such video")
    return FileResponse(bw_channel.video_path(key), media_type="video/mp4")


def _known(key: str) -> dict:
    info = bw_channel.load_info(key)
    if info is None:
        raise HTTPException(status_code=404, detail="No such video")
    return info


@router.post("/videos/{key}/approve")
def approve(key: str) -> dict[str, Any]:
    """Approve: it goes out at its slot (at once when the slot has passed: that upload runs as the upload job)."""
    info = _known(key)
    settings = _settings()
    result = bw_channel.approve(key, None)  # recorded now; a late upload goes through the job below
    post_at = result["post_at"]
    late = post_at is not None and assistant.now(settings) >= post_at and bw_channel.auto_post()
    if late and not (info.get("uploads") or {}).get("youtube"):
        _start("upload", lambda p: bw_channel.publish_if_allowed(key, p, settings=settings), key=key)
    return {"approved": True, "uploading": late, "post_at": post_at.isoformat() if post_at else None}


@router.post("/videos/{key}/veto")
def veto(key: str) -> dict[str, Any]:
    _known(key)
    try:
        return bw_channel.veto(key)
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


class UploadRequest(BaseModel):
    privacy: str = Field("public", pattern="^(private|unlisted|public)$")


@router.post("/videos/{key}/upload")
def upload(key: str, req: UploadRequest) -> dict[str, Any]:
    info = _known(key)
    if info.get("check"):
        raise HTTPException(status_code=400, detail="It failed its check: " + "; ".join(info["check"]))
    if not bw_channel.connected():
        raise HTTPException(status_code=400, detail="Connect the Borderline Why YouTube channel first")
    settings = _settings()
    return _start("upload", lambda p: bw_channel.upload(key, req.privacy, p, settings=settings), key=key)


@router.get("/upload-job")
def upload_job() -> dict[str, Any]:
    return _job("upload")


class MakeRequest(BaseModel):
    day: date | None = None  # with slot: that slot (the Plan tab); else the next slot to come
    slot: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")


@router.post("/make")
def make(req: MakeRequest | None = None) -> dict[str, Any]:
    """Make a slot's video now (a video already made for it is set aside), posted in Discord too."""
    settings = _settings()
    now = assistant.now(settings)
    if req and req.day and req.slot:
        job = bw_channel.job_for(settings, req.day, req.slot)
        if job.slot != req.slot:
            raise HTTPException(status_code=400, detail=f"{req.slot} is not a slot (BW_SLOTS: {', '.join(bw_channel.slots())})")
    else:
        job = bw_channel.next_job(settings, now) or bw_channel.job_for(settings, now.date())
    try:
        bw_channel.set_aside(job.key)
    except bw_channel.ChannelError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    return _start("make", lambda p: bw_channel.make(settings, job, p, announce=True)["key"], key=job.key)


@router.get("/plan")
def plan() -> dict[str, Any]:
    settings = _settings()
    now = assistant.now(settings)
    days = bw_channel.plan_days(settings, now)
    return {"days": days, "slots": bw_channel.slots(), "lead_hours": bw_channel.lead_hours(), "timezone": settings.timezone,
            "require_approval": bw_channel.require_approval(), "privacy": bw_channel.upload_privacy(),
            "now": now.isoformat(timespec="minutes"), "making": (_job("make").get("key") if _job("make").get("running") else None),
            "waiting": sum(1 for d in days for r in d["slots"] if r["state"] == "waiting")}


@router.get("/make-job")
def make_job() -> dict[str, Any]:
    return _job("make")


@router.get("/topics")
def topics() -> dict[str, Any]:
    root = bw_channel.out_root()
    return {"topics": bw_topics.listing(root), "summary": bw_topics.summary(root), "queued": [p.name for p in bw_channel.queued_scripts()]}


class TopicRequest(BaseModel):
    question: str = Field(min_length=8, max_length=140)
    article: str = Field(min_length=2, max_length=200)


@router.post("/topics")
def add_topic(req: TopicRequest) -> dict[str, Any]:
    """A question of your own; its Wikipedia article must exist (the research starts there). It is made next."""
    try:
        title = bw_script.article_exists(req.article)
    except Exception as err:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Wikipedia did not answer: {err}") from err
    if not title:
        raise HTTPException(status_code=400, detail=f"English Wikipedia has no article called {req.article!r}")
    return bw_topics.add(bw_channel.out_root(), req.question, title).model_dump()


def _login(request: Request) -> str:
    from .app import _callback_url

    try:
        return bw_channel.start_login(_callback_url(request))
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/youtube/status")
def youtube_status() -> dict[str, Any]:
    return bw_channel.youtube_status()


@router.get("/youtube/connect", include_in_schema=False)
def youtube_connect_page(request: Request) -> RedirectResponse:
    """Open in a browser on the dashboard machine (or through an ssh tunnel): straight to Google's login."""
    return RedirectResponse(_login(request))


@router.post("/youtube/connect")
def youtube_connect(request: Request) -> dict[str, Any]:
    return {"auth_url": _login(request)}


@router.post("/youtube/logout")
def youtube_logout() -> dict[str, Any]:
    bw_channel.logout()
    return {"connected": False}
