"""After Market tab: the last session's facts, the videos made and their uploads, "run this slot now", and
the channel's YouTube login."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from modules import am_channel, am_data
from trendclip import assistant, publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/aftermarket")
_lock = threading.Lock()
_run_job: dict[str, Any] = {"running": False}


def _settings():
    from trendclip.config import get_settings

    return get_settings()


@router.get("/status")
def status() -> dict[str, Any]:
    """The tab's header: automation settings, the next job, the last facts read, the YouTube login."""
    settings = _settings()
    now = assistant.now(settings)
    nxt = am_channel.next_job(settings, now)
    facts = am_channel.latest_facts()
    return {
        "slots": am_channel.slots(), "lead_minutes": am_channel.lead_minutes(), "auto_post": am_channel.auto_post(),
        "privacy": am_channel.upload_privacy(), "category": am_channel.category(), "timezone": settings.timezone,
        "now": now.isoformat(timespec="minutes"), "now_kind": am_data.slot_for(now),
        "next_job": nxt.view() if nxt else None,
        "today": [j.view() for j in am_channel.jobs_for(settings, now.date())],
        "facts": None if facts is None else {
            "key": facts["key"], "slot": facts.get("slot"), "ny_time": facts.get("ny_time"), "made_at": facts.get("made_at"),
            "focus": facts.get("focus", ""), "summary": am_data.summary(facts), "problems": facts.get("problems", []),
            "indices": facts.get("indices", []), "futures": facts.get("futures", []), "movers": facts.get("movers", []),
            "crypto": facts.get("crypto", []), "earnings_today": (facts.get("earnings") or {}).get("today", []),
            "macro": facts.get("macro", []), "news": (facts.get("news") or [])[:6]},
        "handle": am_channel.handle(), "folder": am_channel.DIR.is_dir(), "discord": bool(am_channel.discord_channel_id()),
    }


@router.get("/videos")
def videos() -> dict[str, Any]:
    return {"videos": [{**v, "youtube_texts": _texts(v)} for v in am_channel.videos()], "privacy": am_channel.upload_privacy()}


def _texts(info: dict) -> dict[str, Any]:
    try:
        req = am_channel.upload_request(info)
    except Exception:  # noqa: BLE001 - an old info.json without a title
        return {}
    return {"title": publish._yt_title(req.title, True), "description": req.description, "tags": publish._tags(req.tags)}


def _info_or_404(key: str) -> dict:
    if not key.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=404, detail="No such video")
    info = am_channel.load_info(key)
    if info is None:
        raise HTTPException(status_code=404, detail="No such video")
    return info


@router.get("/videos/{key}/video")
def video_file(key: str) -> FileResponse:
    _info_or_404(key)
    return FileResponse(am_channel.video_path(key), media_type="video/mp4", filename=f"aftermarket_{key}.mp4")


@router.get("/videos/{key}/facts")
def video_facts(key: str) -> dict[str, Any]:
    _info_or_404(key)
    path = am_data.facts_path(key[:10], key[11:])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No facts for this video")
    import json

    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/videos/{key}/veto")
def veto(key: str) -> dict[str, Any]:
    _info_or_404(key)
    try:
        return am_channel.veto(key)
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


class UploadRequest(BaseModel):
    privacy: publish.Privacy | None = None


@router.post("/videos/{key}/upload")
def upload(key: str, body: UploadRequest) -> dict[str, Any]:
    """Upload one finished video now (a failed check still blocks it)."""
    info = _info_or_404(key)
    if info.get("check"):
        raise HTTPException(status_code=400, detail="This video failed its check: " + "; ".join(info["check"]))
    if not am_channel.connected():
        raise HTTPException(status_code=400, detail="Connect the After Market YouTube channel first")
    try:
        return am_channel.upload(key, body.privacy, settings=_settings())
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


# --------------------------------------------------------------------------- run a slot now


class RunRequest(BaseModel):
    kind: Literal["auto", "premarket", "close", "weekend"] = "auto"
    upload: bool = True
    template: bool = False  # the built-in script, no Gemini


def _run(job: am_channel.Job, upload: bool, template: bool) -> None:
    def progress(frac, msg):
        with _lock:
            _run_job.update(progress=frac, message=msg)

    try:
        result = am_channel.run(_settings(), job, progress, upload_now=upload, template=template)
        with _lock:
            _run_job.update(result={"key": (result["info"] or {}).get("key"), "title": (result["info"] or {}).get("title"),
                                    "record": result["record"], "error": result["error"]})
    except Exception as err:  # noqa: BLE001 - shown in the tab
        logger.exception("After Market run failed")
        with _lock:
            _run_job.update(error=str(err)[:400])
    finally:
        with _lock:
            _run_job.update(running=False, finished_at=time.time())


@router.post("/run", status_code=202)
def start_run(body: RunRequest) -> dict[str, Any]:
    """What the bot does at a slot: the facts now, the script, the video, the check, the upload."""
    settings = _settings()
    now = assistant.now(settings)
    kind = am_data.slot_for(now) if body.kind == "auto" else body.kind
    job = am_channel.job_for(settings, now.date(), kind)
    with _lock:
        if _run_job.get("running"):
            raise HTTPException(status_code=409, detail="A video is already being made")
        _run_job.clear()
        _run_job.update(running=True, started_at=time.time(), progress=None, message="Starting", job=job.view(), upload=body.upload)
    threading.Thread(target=_run, args=(job, body.upload, body.template), name="aftermarket-run", daemon=True).start()
    return run_status()


@router.get("/run-job")
def run_status() -> dict[str, Any]:
    with _lock:
        return dict(_run_job)


# --------------------------------------------------------------------------- YouTube


@router.get("/youtube/status")
def youtube_status() -> dict[str, Any]:
    return am_channel.youtube_status()


@router.post("/youtube/connect")
def youtube_connect(request: Request) -> dict[str, Any]:
    """The Google login for the After Market channel; the shared callback saves its token in its folder."""
    from .app import _callback_url

    try:
        return {"auth_url": am_channel.start_login(_callback_url(request))}
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post("/youtube/logout")
def youtube_logout() -> dict[str, Any]:
    am_channel.logout()
    return {"connected": False}
