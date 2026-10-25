"""The DarkArchives channel: its folder under channels/, its YouTube login and the upload of its videos.

The dashboard process serves one active channel (CHANNEL in .env, the gameplay channel); DarkArchives
keeps its own channel.env and token_youtube.json in channels/darkarchives/ and goes through the same
OAuth client (client_secret.json) and the same upload code, just with its own token file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from modules import video_gen
from trendclip import publish
from trendclip.config import CHANNELS_DIR, CHANNEL_ENV

logger = logging.getLogger(__name__)

CHANNEL = "darkarchives"
DIR = CHANNELS_DIR / CHANNEL
TOKEN = DIR / "token_youtube.json"
CATEGORY = "24"  # Entertainment (the gameplay channel uploads as Gaming)


def channel_env() -> dict[str, str]:
    """channels/darkarchives/channel.env (handle, stats links…); {} until the folder exists."""
    path = DIR / CHANNEL_ENV
    if not path.is_file():
        return {}
    return {k: (v or "").strip() for k, v in dotenv_values(path).items() if k}


def handle() -> str:
    """The channel's @handle from channel.env ("" until it is set)."""
    return video_gen.channel_handle()


def youtube_status() -> dict[str, Any]:
    return {**publish.youtube_status(TOKEN), "handle": handle(), "folder": DIR.is_dir()}


def start_login(redirect_uri: str) -> str:
    return publish.start_login(redirect_uri, TOKEN)


def logout() -> None:
    publish.logout(TOKEN)


def connected() -> bool:
    return TOKEN.is_file()


def follow_line() -> str:
    h = handle()
    return f"Follow {h} for more dark stories." if h else ""


def upload_request(info: dict, privacy: str = "private") -> publish.YouTubeUploadRequest:
    """Title, description and tags from story_<id>.json; the follow line is added when it is missing."""
    description = info.get("description", "")
    line = follow_line()
    if line and line not in description:
        parts = description.split("\n\n")
        parts.insert(1 if len(parts) > 1 else len(parts), line)
        description = "\n\n".join(parts)
    return publish.YouTubeUploadRequest(
        title=info["title"][: publish.YT_TITLE_MAX], description=description[:5000],
        tags=[t for t in info.get("hashtags", []) if t], privacy=privacy, category_id=CATEGORY, as_short=True)


def upload(story_id: int, privacy: str = "private", progress=lambda f, m: None, youtube=None,
           out_dir: Path | None = None) -> dict[str, Any]:
    """Upload output/darkarchives/story_<id>.mp4 to the DarkArchives channel and record it in its JSON."""
    path = video_gen.video_path(story_id, out_dir)
    info_file = video_gen.info_path(story_id, out_dir)
    if not path.is_file() or not info_file.is_file():
        raise publish.PublishError(f"No video for story {story_id} yet")
    if youtube is None and not connected():
        raise publish.PublishError("Connect the DarkArchives YouTube channel first")
    info = json.loads(info_file.read_text(encoding="utf-8"))
    req = upload_request(info, privacy)
    record = publish.upload_file(path, req, True, progress, youtube or publish._youtube(TOKEN))
    info.setdefault("uploads", {})["youtube"] = record
    info_file.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    progress(1.0, "Uploaded")
    return record
