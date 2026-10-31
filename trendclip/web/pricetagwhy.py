"""Pricetag Why tab (modules.pw_channel): the hand-checked scripts with their rule check and sources, "make the
video" for one of them (drawn in a process of its own), and the made videos with their description. The schedule,
Discord and YouTube upload come in a later step."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from modules import pw_channel

router = APIRouter(prefix="/api/pricetagwhy")
_procs: dict[str, Any] = {}  # key -> the make process started from the tab


class MakeRequest(BaseModel):
    file: str


def _making() -> list[str]:
    return [key for key, proc in _procs.items() if proc.poll() is None]


@router.get("/status")
def status() -> dict[str, Any]:
    return {"name": pw_channel.NAME, "handle": pw_channel.handle(), "voice": pw_channel.VOICE, "making": _making(),
            "folder": pw_channel.DIR.is_dir()}


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


@router.post("/make")
def make(req: MakeRequest) -> dict[str, Any]:
    if not re.fullmatch(r"[\w.-]+\.json", req.file):
        raise HTTPException(status_code=400, detail="Not a script file")
    path = pw_channel.SCRIPTS_DIR / req.file
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such script")
    if _making():
        raise HTTPException(status_code=409, detail="A video is already being made")
    found = next((s for s in pw_channel.scripts() if s["file"] == req.file), None)
    if found and found["check"]:
        raise HTTPException(status_code=400, detail="The script breaks the rules: " + "; ".join(found["check"]))
    key = f"{datetime.now():%Y-%m-%d_%H%M}_{path.stem}"
    _procs[key] = pw_channel.start_make(path, key)
    return {"key": key, "making": _making()}
