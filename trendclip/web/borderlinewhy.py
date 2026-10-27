"""Borderline Why on the dashboard: the channel's YouTube login (the shared callback saves its token in
channels/borderlinewhy/), its status and its videos. The day-to-day runs from Discord (/bw)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from modules import bw_channel, bw_topics
from trendclip import assistant, publish

router = APIRouter(prefix="/api/borderlinewhy")


def _settings():
    from trendclip.config import get_settings

    return get_settings()


@router.get("/status")
def status() -> dict[str, Any]:
    settings = _settings()
    nxt = bw_channel.next_job(settings, assistant.now(settings))
    return {"youtube": bw_channel.youtube_status(), "discord": bool(bw_channel.discord_channel_id()), "slots": bw_channel.slots(),
            "lead_hours": bw_channel.lead_hours(), "auto_make": bw_channel.auto_make(), "auto_post": bw_channel.auto_post(),
            "require_approval": bw_channel.require_approval(), "privacy": bw_channel.upload_privacy(),
            "next_job": nxt.view() if nxt else None, "topics": bw_topics.summary(bw_channel.out_root()),
            "queued": [p.name for p in bw_channel.queued_scripts()]}


@router.get("/videos")
def videos() -> dict[str, Any]:
    return {"videos": bw_channel.videos()}


@router.get("/videos/{key}/video.mp4", include_in_schema=False)
def video_file(key: str) -> FileResponse:
    if bw_channel.load_info(key) is None:
        raise HTTPException(status_code=404, detail="No such video")
    return FileResponse(bw_channel.video_path(key), media_type="video/mp4")


def _login(request: Request) -> str:
    from .app import _callback_url

    try:
        return bw_channel.start_login(_callback_url(request))
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/youtube/connect", include_in_schema=False)
def youtube_connect_page(request: Request) -> RedirectResponse:
    """Open in a browser (on the hub, or through an ssh tunnel to it): straight to Google's login."""
    return RedirectResponse(_login(request))


@router.post("/youtube/connect")
def youtube_connect(request: Request) -> dict[str, Any]:
    return {"auth_url": _login(request)}


@router.post("/youtube/logout")
def youtube_logout() -> dict[str, Any]:
    bw_channel.logout()
    return {"connected": False}
