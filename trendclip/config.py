"""Runtime configuration loaded from environment variables / a local .env file."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values, load_dotenv
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# One folder per channel (channels/sidequestlogic/): its channel.env (handles, its tokens / ids) and its
# login files (token_youtube.json, token_instagram.json). Keys shared by every channel stay in .env.
CHANNELS_DIR = PROJECT_ROOT / "channels"
CHANNEL_ENV = "channel.env"
DEFAULT_CHANNEL = "sidequestlogic"

# Maps Settings field -> environment variable name.
_ENV_MAP: dict[str, str] = {
    "youtube_api_key": "YOUTUBE_API_KEY",
    "yt_regions": "YT_REGIONS",
    "yt_category_id": "YT_CATEGORY_ID",
    "yt_max_results_per_region": "YT_MAX_RESULTS_PER_REGION",
    "yt_outlier_ratio": "YT_OUTLIER_RATIO",
    "top_topics": "TOP_TOPICS",
    "request_timeout": "REQUEST_TIMEOUT",
    "output_dir": "OUTPUT_DIR",
    "pexels_api_key": "PEXELS_API_KEY",
    "ncg_channels": "NCG_CHANNELS",
    "background_clip_seconds": "BACKGROUND_CLIP_SECONDS",
    "background_orientation": "BACKGROUND_ORIENTATION",
    "background_sources": "BACKGROUND_SOURCES",
    "assets_dir": "ASSETS_DIR",
    "gemini_api_key": "GEMINI_API_KEY",
    "gemini_model": "GEMINI_MODEL",
    "tts_voice": "TTS_VOICE",
    "tts_rate": "TTS_RATE",
    "short_target_seconds": "SHORT_TARGET_SECONDS",
    "music_channels": "MUSIC_CHANNELS",
    "music_volume": "MUSIC_VOLUME",
    "channel_handle": "CHANNEL_HANDLE",
    "stickers_dir": "STICKERS_DIR",
    "music_library_dir": "MUSIC_LIBRARY_DIR",
    "timezone": "TIMEZONE",
    "discord_bot_token": "DISCORD_BOT_TOKEN",
    "discord_guild_id": "DISCORD_GUILD_ID",
    "discord_channel_id": "DISCORD_CHANNEL_ID",
    "discord_allowed_users": "DISCORD_ALLOWED_USERS",
    "discord_morning_time": "DISCORD_MORNING_TIME",
    "discord_reminder_minutes": "DISCORD_REMINDER_MINUTES",
    "auto_post": "AUTO_POST",
    "emergency_video": "EMERGENCY_VIDEO",
    "auto_pick": "AUTO_PICK",
    "auto_pick_explore": "AUTO_PICK_EXPLORE",
    "auto_pick_days": "AUTO_PICK_DAYS",
    "auto_pick_trends": "AUTO_PICK_TRENDS",
    "script_learning": "SCRIPT_LEARNING",
    "auto_produce": "AUTO_PRODUCE",
    "auto_produce_time": "AUTO_PRODUCE_TIME",
    "discord_leaderboard_time": "DISCORD_LEADERBOARD_TIME",
    "youtube_auto_replies": "YOUTUBE_AUTO_REPLIES",
    "youtube_reply_daily_max": "YOUTUBE_REPLY_DAILY_MAX",
    "youtube_reply_check_hours": "YOUTUBE_REPLY_CHECK_HOURS",
    "instagram_auto_replies": "INSTAGRAM_AUTO_REPLIES",
    "instagram_reply_daily_max": "INSTAGRAM_REPLY_DAILY_MAX",
    "instagram_youtube_name": "INSTAGRAM_YOUTUBE_NAME",
    "stats_youtube": "STATS_YOUTUBE",
    "stats_tiktok": "STATS_TIKTOK",
    "stats_instagram": "STATS_INSTAGRAM",
    "instagram_access_token": "INSTAGRAM_ACCESS_TOKEN",
    "hub_role": "HUB_ROLE",
    "hub_channel_id": "HUB_CHANNEL_ID",
}

# UCht8qITGkBvXKsR1Byln-wA is the original "Audio Library" channel; @audiolibrarymusicforconten9614 is a
# small look-alike that reuploads commercial songs.
DEFAULT_MUSIC_CHANNELS = ["@NoCopyrightSounds", "UCht8qITGkBvXKsR1Byln-wA", "@ChillhopMusic"]

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_TTS_VOICE = "en-US-AndrewMultilingualNeural"

DEFAULT_NCG_CHANNELS = ["@NoCopyrightGameplays", "@OrbitalNCG", "No Copyright Gameplay"]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseModel):
    youtube_api_key: SecretStr

    yt_regions: list[str] = Field(default_factory=lambda: ["US"])
    # 20 = Gaming, 28 = Science & Technology; None (env "all") = every category.
    yt_category_id: str | None = "20"
    # The mostPopular chart is capped at 200 results per region by YouTube.
    yt_max_results_per_region: int = Field(50, ge=1, le=200)
    # A video is an outlier when views >= ratio * channel subscribers.
    yt_outlier_ratio: float = Field(2.0, gt=0)

    top_topics: int = Field(10, ge=1, le=100)
    request_timeout: float = Field(15.0, gt=0)
    output_dir: Path = PROJECT_ROOT / "output"

    # Background gameplay downloads (Phase 1.5 -> input for video synthesis).
    pexels_api_key: SecretStr | None = None
    # Handles (@name), channel ids (UC...) or plain channel names.
    ncg_channels: list[str] = Field(default_factory=lambda: list(DEFAULT_NCG_CHANNELS))
    background_clip_seconds: int = Field(60, ge=0, le=3600)  # 0 = whole video
    background_orientation: Literal["landscape", "portrait"] = "landscape"
    # Order tried by get_background_video(); sources without credentials are skipped.
    background_sources: list[Literal["pexels", "youtube"]] = Field(
        default_factory=lambda: ["pexels", "youtube"]
    )
    assets_dir: Path = PROJECT_ROOT / "assets"
    # Your own reaction / subscribe stickers (PNG, GIF, WebP, JPG); scanned and tagged automatically.
    stickers_dir: Path = PROJECT_ROOT / "stickers"
    # Your own background music, sorted by story mood: music/funny and music/chill (lo-fi / quirky only).
    music_library_dir: Path = PROJECT_ROOT / "music"

    # Create tab: Gemini writes the script, edge-tts speaks it.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = DEFAULT_GEMINI_MODEL
    tts_voice: str = DEFAULT_TTS_VOICE
    tts_rate: str = Field("+5%", pattern=r"^[+-]\d{1,3}%$")
    short_target_seconds: int = Field(22, ge=10, le=180)  # up to 30: the viral 18-22 s rules
    # Channel handle used in the multi-part calls to action ("Sub to @handle for Part 2").
    channel_handle: str = Field("@SideQuestLogic", pattern=r"^@[\w.-]{3,30}$")
    # Background music channels (handles / UC ids) and the music level under the voice (0-1).
    music_channels: list[str] = Field(default_factory=lambda: list(DEFAULT_MUSIC_CHANNELS))
    music_volume: float = Field(0.14, ge=0, le=1)  # 12-15% (about -17 dB) sits well under speech

    # The plan's upload slots (14:30 / 23:00) and the bot's reminders are in this time zone.
    timezone: str = "Asia/Jerusalem"
    # Discord bot: commands are registered on one server; reminders go to one channel.
    discord_bot_token: SecretStr | None = None
    discord_guild_id: int | None = None
    discord_channel_id: int | None = None
    discord_allowed_users: list[int] = Field(default_factory=list)  # empty: everyone on the server
    discord_morning_time: str = Field("10:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    discord_reminder_minutes: int = Field(30, ge=0, le=180)  # 0 = no slot reminders
    # At 14:30 / 23:00 the bot posts the planned Short on YouTube and Instagram itself (its plan platforms).
    auto_post: bool = True
    # An hour before 14:30 / 23:00 with nothing planned, the bot makes a random video and plans it there.
    emergency_video: bool = True
    # "Pick for me" and the emergency video choose the kind, game and length by the channel's own numbers
    # (analytics.plan_video): the last AUTO_PICK_DAYS days, drawing at random AUTO_PICK_EXPLORE of the time.
    auto_pick: bool = True
    auto_pick_explore: float = Field(0.2, ge=0, le=1)
    auto_pick_days: int = Field(60, ge=7, le=365)
    # The game pick also reads the trend pipeline (YT_REGIONS, a few quota units, at most twice a day):
    # proven games that trend now get a boost, and the exploration share goes to untried games by momentum.
    auto_pick_trends: bool = True

    # Every script prompt gets the channel's own lessons (analytics.lessons): the hooks that kept viewers, the
    # styles that lost them, the length that works, the endings that drew comments, the titles used lately.
    script_learning: bool = True
    # Full automation: at AUTO_PRODUCE_TIME the bot makes a video for every empty slot of tomorrow (and of
    # today when the slot is still 3+ hours away), by the channel's numbers, plans it there and posts it to
    # Discord; it goes out at its time unless "Don't post this" is pressed. The emergency video stays the
    # fallback for a slot still empty an hour before.
    auto_produce: bool = True
    auto_produce_time: str = Field("10:15", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")



    # The leaderboard picture + a post caption to copy to YouTube; empty = off.
    discord_leaderboard_time: str = Field("13:30", pattern=r"^(([01]\d|2[0-3]):[0-5]\d)?$")
    # Reply on YouTube to every fan's comment (they get a notification); see leaderboard.py.
    youtube_auto_replies: bool = True
    # A reply costs 50 of the 10,000 daily API units (an upload ~1,600): 100 replies use half.
    youtube_reply_daily_max: int = Field(100, ge=1, le=180)
    # Read the comments this often to reply to new ones (0 = only the morning read and /replies).
    youtube_reply_check_hours: float = Field(3, ge=0, le=24)
    # Reply on Instagram too (one per fan per Reel; every reply invites them to the YouTube leaderboard).
    instagram_auto_replies: bool = True
    instagram_reply_daily_max: int = Field(50, ge=1, le=200)
    # How Instagram replies name the YouTube channel ("… SideQuest Logic on YouTube"); no links or @.
    instagram_youtube_name: str = Field("SideQuest Logic", min_length=1, max_length=40)

    # The channel folder under channels/ whose channel.env and login files are used.
    channel: str = DEFAULT_CHANNEL

    # Your own channels for the morning stats report (empty = skip that platform).
    stats_youtube: str = "@SideQuestLogic-t6g"
    stats_tiktok: str = "sidequestlogic"
    stats_instagram: str = "sidequestlogic"
    # Instagram API with Instagram Login (professional account); without it, public profile data is tried.
    instagram_access_token: SecretStr | None = None

    # Two machines, one plan, joined through Discord. HUB_ROLE=hub on the always-on machine (keeps
    # the plan, runs the bot); HUB_ROLE=sender on the other, which posts its finished Shorts to the
    # hub channel with the same bot token (default channel: DISCORD_CHANNEL_ID).
    hub_role: Literal["", "hub", "sender"] = ""
    hub_channel_id: int | None = None

    @field_validator("hub_role", mode="before")
    @classmethod
    def _lower_role(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("stats_youtube", "stats_tiktok", "stats_instagram", mode="before")
    @classmethod
    def _handle_from_link(cls, value: object, info) -> object:  # noqa: ANN001 - pydantic ValidationInfo
        """Accept a profile link or a handle. YouTube: '@name' or a UC… id; TikTok / Instagram: 'name'."""
        import re

        if not isinstance(value, str):
            return value
        text = value.strip().rstrip("/")
        if info.field_name == "stats_youtube":
            if m := re.search(r"youtube\.com/(?:channel/(UC[\w-]{22})|(@[\w.-]+))", text):
                return m.group(1) or m.group(2)
            return text if not text or text.startswith(("@", "UC")) else "@" + text
        if m := re.search(r"(?:tiktok|instagram)\.com/@?([\w.]+)", text):
            return m.group(1)
        return text.lstrip("@")

    @field_validator("ncg_channels", "background_sources", "music_channels", "discord_allowed_users",
                     mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as err:
            raise ValueError(f"Unknown time zone {value!r} (use an IANA name like Asia/Jerusalem)") from err
        return value

    @property
    def tz(self):
        from zoneinfo import ZoneInfo

        return ZoneInfo(self.timezone)

    @field_validator("assets_dir", "stickers_dir", "music_library_dir", mode="after")
    @classmethod
    def _resolve_assets_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    @property
    def channel_dir(self) -> Path:
        return CHANNELS_DIR / self.channel

    @property
    def pexels_enabled(self) -> bool:
        return self.pexels_api_key is not None and bool(self.pexels_api_key.get_secret_value())

    @property
    def backgrounds_dir(self) -> Path:
        return self.assets_dir / "backgrounds"

    @property
    def shorts_dir(self) -> Path:
        return self.output_dir / "shorts"

    @property
    def music_dir(self) -> Path:
        return self.assets_dir / "music"

    @field_validator("yt_regions", mode="before")
    @classmethod
    def _parse_regions(cls, value: object) -> object:
        if isinstance(value, str):
            value = [part for part in value.split(",")]
        if isinstance(value, list):
            regions = [str(r).strip().upper() for r in value if str(r).strip()]
            invalid = [r for r in regions if len(r) != 2 or not r.isalpha()]
            if invalid:
                raise ValueError(f"Region codes must be ISO 3166-1 alpha-2, got {invalid}")
            if not regions:
                raise ValueError("At least one region is required")
            return regions
        return value

    @field_validator("yt_category_id", mode="before")
    @classmethod
    def _parse_category(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"", "all", "0"}:
            return None
        if not text.isdigit():
            raise ValueError(f"Category must be a numeric YouTube category id or 'all', got {value!r}")
        return text

    @field_validator("output_dir", mode="after")
    @classmethod
    def _resolve_output_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else PROJECT_ROOT / value

    def with_overrides(self, **overrides: object) -> Settings:
        """Validated copy with the given fields replaced (None values are ignored).

        Use the sentinel "all" (not None) to clear the category filter.
        """
        updates = {k: v for k, v in overrides.items() if v is not None}
        if not updates:
            return self
        return Settings.model_validate({**self.model_dump(), **updates})


def _read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for field, env_name in _ENV_MAP.items():
        raw = os.getenv(env_name)
        if raw is not None and raw.strip() != "":
            values[field] = raw.strip()
    return values


def active_channel(env_file: str | Path | None = None) -> str:
    """The channel this process works for: CHANNEL from the environment or .env (default sidequestlogic)."""
    raw = os.getenv("CHANNEL") or dotenv_values(env_file or PROJECT_ROOT / ".env").get("CHANNEL") or DEFAULT_CHANNEL
    name = raw.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", name):
        raise ConfigError(f"CHANNEL must be a folder name under channels/ (letters, digits, - or _), got {raw!r}")
    return name


def channel_dir(channel: str | None = None) -> Path:
    return CHANNELS_DIR / (channel or active_channel())


def channel_file(name: str, channel: str | None = None, shared: bool = False) -> Path:
    """A channel's own file (login tokens…). `shared`: fall back to the project folder when the channel
    has none (client_secret.json: one Google OAuth client can connect any channel)."""
    own = channel_dir(channel) / name
    return PROJECT_ROOT / name if shared and not own.is_file() else own


@lru_cache(maxsize=1)
def get_settings(env_file: str | None = None) -> Settings:
    """Load settings once per process. Real environment variables win over channels/<CHANNEL>/channel.env,
    which wins over the shared .env."""
    root_file = env_file or PROJECT_ROOT / ".env"
    load_dotenv(root_file, override=False)
    root = dotenv_values(root_file)
    channel = active_channel(root_file)
    values = _read_env()
    for field, env_name in _ENV_MAP.items():
        own = (dotenv_values(channel_dir(channel) / CHANNEL_ENV).get(env_name) or "").strip()
        if own and (os.getenv(env_name) is None or os.getenv(env_name) == root.get(env_name)):
            values[field] = own
    values["channel"] = channel
    try:
        return Settings(**values)
    except ValidationError as exc:
        problems = "; ".join(
            f"{_ENV_MAP.get(str(err['loc'][0]), err['loc'][0])}: {err['msg']}" for err in exc.errors()
        )
        raise ConfigError(f"Invalid configuration ({problems}). See .env.example.") from exc
