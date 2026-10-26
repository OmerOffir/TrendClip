"""The Dark Outliers channel: its folder under channels/, its YouTube login, the upload of its videos and
its Discord channel (DISCORD_CHANNEL_ID in channel.env), where every finished video and upload is posted.

The dashboard process serves one active channel (CHANNEL in .env, the gameplay channel); Dark Outliers
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
from modules.scene_images import setting
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
    """Discord's REST API as the bot, aimed at the Dark Outliers channel; None when either is not set."""
    from trendclip.config import get_settings

    settings = settings or get_settings()
    channel = discord_channel_id()
    if not channel or not settings.discord_bot_token:
        return None
    rest = hub.Rest(settings)
    rest.channel = channel
    return rest


STYLES = {"primary": 1, "secondary": 2, "success": 3, "danger": 4}


def button(action: str, arg: str | int, label: str, style: str = "secondary") -> dict[str, Any]:
    """A Discord button the bot answers (its custom id is `tc:<action>:<arg>`, like the bot's own)."""
    return {"type": 2, "style": STYLES.get(style, 2), "label": label[:80], "custom_id": f"tc:{action}:{arg}"[:100]}


def link_button(label: str, url: str) -> dict[str, Any]:
    return {"type": 2, "style": 5, "label": label[:80], "url": url}


def rows(*buttons: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Message components: up to 5 buttons a row."""
    items = [b for b in buttons if b]
    return [{"type": 1, "components": items[i:i + 5]} for i in range(0, len(items), 5)]


def video_buttons(story_id: int, item_id: str | None = None, uploaded: dict | None = None) -> list[dict[str, Any]]:
    """The buttons under a video post: veto when it is planned, plan when it is not, upload, delete."""
    out = []
    if item_id:
        out.append(button("daveto", item_id, "🚫 Don't post this", "danger"))
    elif not uploaded:
        out.append(button("daplan", story_id, "📅 Plan the next free slot", "success"))
    if uploaded:
        out.append(link_button("▶️ Watch on YouTube", uploaded.get("url", "")) if uploaded.get("url") else None)
    else:
        out.append(button("daup", story_id, "⬆️ Upload to YouTube now", "primary"))
    out.append(button("dadel", story_id, "🗑 Delete the video"))
    return rows(*out)


def announce_video(story_id: int, info: dict, settings=None, out_dir: Path | None = None, note: str | None = None,
                   components: list[dict[str, Any]] | None = None) -> str | None:
    """Post the finished video (shrunk to Discord's 10 MB when needed) in the Dark Outliers channel.
    `note` replaces the "upload it from the tab" line. Never raises: the video is made whether or not
    Discord takes it."""
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
                + "\n" + (note or f"Story {story_id}: upload it from the Dark Outliers tab (Used → the story → Upload to YouTube)."))
        if components is None:
            components = video_buttons(story_id, uploaded=(info.get("uploads") or {}).get("youtube"))
        return rest.post(text, (path.name, small.read_bytes()), components)
    except Exception as err:  # noqa: BLE001
        logger.warning("Discord post for story %s failed: %s", story_id, err)
        return None


def announce_upload(story_id: int, info: dict, record: dict, settings=None) -> str | None:
    """Post the YouTube link in the Dark Outliers channel once a video is uploaded. Never raises."""
    try:
        rest = _rest(settings)
        if rest is None:
            return None
        return rest.post(f"⬆️ **{info.get('title', f'story {story_id}')}** is on YouTube ({record.get('privacy', '?')}): "
                         f"{record.get('url', '')}",
                         components=rows(link_button("▶️ Watch", record.get("url", "")) if record.get("url") else None,
                                         link_button("🎬 YouTube Studio", record.get("studio_url", "")) if record.get("studio_url") else None))
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


def comment_text(story_id: int, info: dict | None = None) -> str:
    """The channel's own first comment: the script's closing question (it invites answers, and comments
    are an engagement signal), then the follow line."""
    import re

    question = ""
    from modules import script_gen

    path = script_gen.script_path(story_id)
    if path.is_file():
        try:
            scenes = json.loads(path.read_text(encoding="utf-8")).get("scenes") or []
            last = (scenes[-1].get("narration") or "") if scenes else ""
            sentences = [t.strip() for t in re.split(r"(?<=[.!?])\s+", last) if t.strip()]
            question = next((t for t in reversed(sentences) if t.endswith("?")), "")
        except ValueError:
            question = ""
    question = question or "What do you think really happened? Tell me below."
    line = follow_line() or "Follow for more dark stories."
    return f"{question}\n\n{line}"


def post_comment(story_id: int, text: str, youtube=None) -> dict[str, Any]:
    """Comment on the uploaded video as the channel (YouTube only takes comments on public or unlisted videos)."""
    from googleapiclient.errors import HttpError

    info_file = video_gen.info_path(story_id)
    info = json.loads(info_file.read_text(encoding="utf-8"))
    video_id = ((info.get("uploads") or {}).get("youtube") or {}).get("video_id")
    if not video_id:
        raise publish.PublishError("Upload the video first")
    youtube = youtube or publish._youtube(TOKEN)
    body = {"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text.strip()}}}}
    try:
        response = youtube.commentThreads().insert(part="snippet", body=body).execute()
    except HttpError as err:
        raise publish.PublishError(publish._api_error_text(err).replace("YouTube upload failed", "YouTube refused the comment")) from err
    record = {"comment_id": response.get("id", ""), "text": text.strip(),
              "url": f"https://www.youtube.com/watch?v={video_id}&lc={response.get('id', '')}"}
    info["uploads"]["youtube"]["comment"] = record
    info_file.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return record


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
    """Upload output/darkarchives/story_<id>.mp4 to the Dark Outliers channel and record it in its JSON."""
    path = video_gen.video_path(story_id, out_dir)
    info_file = video_gen.info_path(story_id, out_dir)
    if not path.is_file() or not info_file.is_file():
        raise publish.PublishError(f"No video for story {story_id} yet")
    if youtube is None and not connected():
        raise publish.PublishError("Connect the Dark Outliers YouTube channel first")
    info = json.loads(info_file.read_text(encoding="utf-8"))
    req = upload_request(info, privacy)
    record = publish.upload_file(path, req, True, progress, youtube or publish._youtube(TOKEN))
    info.setdefault("uploads", {})["youtube"] = record
    info_file.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if record.get("privacy") in ("public", "unlisted") and setting("DA_FIRST_COMMENT", "1") not in ("0", "false", "off"):
        try:
            progress(None, "Posting the channel's comment")
            record["comment"] = post_comment(story_id, comment_text(story_id, info), youtube)
        except Exception as err:  # noqa: BLE001 - the video is up; the comment is a bonus
            logger.warning("Comment on story %s failed: %s", story_id, err)
    progress(1.0, "Uploaded")
    announce_upload(story_id, info, record)
    return record
