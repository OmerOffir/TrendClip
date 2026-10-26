"""What every extra channel (Dark Outliers, After Market) shares: its folder under channels/<name>/, its
channel.env (handle, Discord channel id), its own YouTube login (token_youtube.json, through the shared
OAuth client), Discord's REST API aimed at its channel, and the message buttons the bot answers.

The dashboard process serves one active channel (CHANNEL in .env, the gameplay channel); these channels
keep their files next to it and go through the same upload code with their own token file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from trendclip import hub, publish
from trendclip.config import CHANNELS_DIR, CHANNEL_ENV


Channel = str | Path  # a channel's name under channels/, or its folder itself (tests point it at a temp folder)


def channel_dir(channel: Channel) -> Path:
    return channel if isinstance(channel, Path) else CHANNELS_DIR / channel


def token_path(channel: Channel) -> Path:
    return channel_dir(channel) / "token_youtube.json"


def channel_env(channel: Channel) -> dict[str, str]:
    """channels/<channel>/channel.env (handle, stats links, Discord channel…); {} until the folder exists."""
    path = channel_dir(channel) / CHANNEL_ENV
    if not path.is_file():
        return {}
    return {k: (v or "").strip() for k, v in dotenv_values(path).items() if k}


def handle(channel: Channel) -> str:
    """The channel's @handle from channel.env ("" until it is set)."""
    value = channel_env(channel).get("CHANNEL_HANDLE", "")
    return value if value.startswith("@") and len(value) > 1 else ""


def discord_channel_id(channel: Channel) -> int | None:
    value = channel_env(channel).get("DISCORD_CHANNEL_ID", "")
    return int(value) if value.isdigit() else None


def rest(channel: Channel, settings=None):
    """Discord's REST API as the bot, aimed at the channel's Discord channel; None when either is not set."""
    from trendclip.config import get_settings

    settings = settings or get_settings()
    channel_id = discord_channel_id(channel)
    if not channel_id or not settings.discord_bot_token:
        return None
    api = hub.Rest(settings)
    api.channel = channel_id
    return api


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


# --------------------------------------------------------------------------- the YouTube login


def youtube_status(channel: Channel) -> dict[str, Any]:
    return {**publish.youtube_status(token_path(channel)), "handle": handle(channel), "folder": channel_dir(channel).is_dir()}


def start_login(channel: Channel, redirect_uri: str) -> str:
    return publish.start_login(redirect_uri, token_path(channel))


def logout(channel: Channel) -> None:
    publish.logout(token_path(channel))


def connected(channel: Channel) -> bool:
    return token_path(channel).is_file()
