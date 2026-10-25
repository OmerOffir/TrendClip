"""The DarkArchives channel: its folder under channels/, its YouTube login, the upload of its videos and
its Discord channel (DISCORD_CHANNEL_ID in channel.env), where every finished video and upload is posted.

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
from trendclip import assistant, hub, publish
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


def discord_channel_id() -> int | None:
    value = channel_env().get("DISCORD_CHANNEL_ID", "")
    return int(value) if value.isdigit() else None


def _rest(settings=None):
    """Discord's REST API as the bot, aimed at the DarkArchives channel; None when either is not set."""
    from trendclip.config import get_settings

    settings = settings or get_settings()
    channel = discord_channel_id()
    if not channel or not settings.discord_bot_token:
        return None
    rest = hub.Rest(settings)
    rest.channel = channel
    return rest


def announce_video(story_id: int, info: dict, settings=None, out_dir: Path | None = None) -> str | None:
    """Post the finished video (shrunk to Discord's 10 MB when needed) in the DarkArchives channel.
    Never raises: the video is made whether or not Discord takes it."""
    try:
        rest = _rest(settings)
        if rest is None:
            return None
        path = video_gen.video_path(story_id, out_dir)
        small = assistant.fit_for_discord(path, path.parent / "discord" / path.name)
        photos = len(info.get("photos") or [])
        text = (f"🎬 **{info.get('title', path.stem)}** · {info.get('duration_seconds', 0):.0f} s · "
                f"pictures: {info.get('pictures', '?')}" + (f" · {photos} photo{'s' if photos != 1 else ''}" if photos else "")
                + (f" · 🎵 {info['music']['title']}" if info.get("music") else "")
                + f"\nStory {story_id}: upload it from the DarkArchives tab (Used → the story → Upload to YouTube).")
        return rest.post(text, (path.name, small.read_bytes()))
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post for story %s failed: %s", story_id, err)
        return None


def announce_upload(story_id: int, info: dict, record: dict, settings=None) -> str | None:
    """Post the YouTube link in the DarkArchives channel once a video is uploaded. Never raises."""
    try:
        rest = _rest(settings)
        if rest is None:
            return None
        return rest.post(f"⬆️ **{info.get('title', f'story {story_id}')}** is on YouTube ({record.get('privacy', '?')}): "
                         f"{record.get('url', '')}\nYouTube Studio: <{record.get('studio_url', '')}>")
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post for the upload of story %s failed: %s", story_id, err)
        return None


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
    announce_upload(story_id, info, record)
    return record
