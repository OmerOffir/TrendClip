"""Post Shorts on Instagram as Reels with the official API (Instagram API with Instagram Login).

Needs a Professional (Creator or Business) account and INSTAGRAM_ACCESS_TOKEN in the channel's
channel.env (README -> Instagram). The token lasts 60 days; channel_stats.instagram_token refreshes
it weekly into token_instagram.json, so it never runs out while TrendClip is used.

This API only takes videos by URL, so the Short is served for a few minutes from this machine at a
secret temporary link (tunnel.py, a Cloudflare quick tunnel). A Reel goes out in three steps: create a
container with that link, wait while Instagram downloads and processes it (then the link closes), and
publish. Instagram allows 100 API posts a day; the caption is the Instagram text from the Upload tab.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import requests

from . import channel_stats, publish, shorts, tunnel, video_assembler, video_downloader
from .config import Settings
from .models import utcnow

logger = logging.getLogger(__name__)

ProgressFn = Callable[[float | None, str], None]
VERSION = "v23.0"
GRAPH = f"https://graph.instagram.com/{VERSION}"
MIN_SECONDS, MAX_SECONDS = 3, 15 * 60
MAX_BYTES = 300 * 1024 * 1024
MAX_BITRATE = 5_000_000  # bits/s served through the tunnel; above it a lighter copy is sent
PROCESS_TIMEOUT = 15 * 60
TIMEOUT = 120
LIMIT_CODES = (4, 9, 17, 32, 613)  # Instagram's "too many calls" errors

_accounts: dict[str, dict[str, Any]] = {}  # token -> account, so the dashboard doesn't re-read it


class InstagramError(RuntimeError):
    def __init__(self, message: str, status: int = 0, code: int = 0) -> None:
        super().__init__(message)
        self.status, self.code = status, code  # HTTP status and Instagram's error code (0 when unknown)


def token(settings: Settings) -> str:
    value = channel_stats.instagram_token(settings, requests.Session())
    if not value:
        raise InstagramError("Add INSTAGRAM_ACCESS_TOKEN to channels/<channel>/channel.env (README -> Instagram)")
    return value


def configured(settings: Settings) -> bool:
    return bool(settings.instagram_access_token) or channel_stats.INSTAGRAM_TOKEN_FILE.is_file()


def _error(resp: requests.Response) -> str:
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    text = err.get("error_user_msg") or err.get("message") or f"HTTP {resp.status_code}"
    if err.get("code") == 190:
        text += " (the token expired or was revoked: put a new INSTAGRAM_ACCESS_TOKEN in channel.env)"
    elif err.get("error_subcode") == 2207042:
        text += " (Instagram's limit: at most 100 API posts a day)"
    elif err.get("code") in LIMIT_CODES:
        text += " (Instagram's rate limit: try again later)"
    return f"Instagram: {text}"


def _call(method: str, url: str, access_token: str, **kwargs: Any) -> dict[str, Any]:
    try:
        resp = requests.request(method, url, params={"access_token": access_token, **kwargs.pop("params", {})},
                                timeout=TIMEOUT, **kwargs)
    except requests.RequestException as err:
        raise InstagramError(f"Could not reach Instagram: {err}") from err
    if resp.status_code >= 400:
        try:
            code = int((resp.json().get("error") or {}).get("code") or 0)
        except (ValueError, TypeError, AttributeError):
            code = 0
        raise InstagramError(_error(resp), status=resp.status_code, code=code)
    return resp.json()


def account(settings: Settings) -> dict[str, Any]:
    access_token = token(settings)
    if access_token not in _accounts:
        me = _call("GET", f"{GRAPH}/me", access_token,
                   params={"fields": "user_id,username,account_type,profile_picture_url"})
        _accounts[access_token] = me
    return _accounts[access_token]


def status(settings: Settings) -> dict[str, Any]:
    out: dict[str, Any] = {"configured": configured(settings), "connected": False, "user": None}
    if not out["configured"]:
        return out
    try:
        me = account(settings)
        out["user"] = {"username": me.get("username", ""), "profile_image_url": me.get("profile_picture_url", "")}
        out["connected"] = True
    except InstagramError as err:
        out["error"] = str(err)
    return out


def caption(short: shorts.ShortVideo) -> str:
    """The Instagram text from the Upload tab: caption, mentions, 5 hashtags and the credits."""
    texts = publish.PlatformTexts.model_validate(short.texts) if short.texts else publish.template_texts(short)
    return publish.full_text(texts.instagram, texts.credit)


def _wait(container: str, access_token: str, progress: ProgressFn) -> None:
    deadline = time.monotonic() + PROCESS_TIMEOUT
    delay = 3
    while True:
        info = _call("GET", f"{GRAPH}/{container}", access_token, params={"fields": "status_code,status"})
        code = info.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Instagram could not process the video: {info.get('status') or code}")
        if time.monotonic() > deadline:
            raise InstagramError("Instagram is still processing the video after 15 minutes; try again later")
        progress(None, "Instagram is processing the video")
        time.sleep(delay)
        delay = min(delay + 2, 15)


def upload_copy(settings: Settings, path: Path, seconds: float) -> Path:
    """The file Instagram downloads: the Short itself when it's light enough, else a ~3.5 Mbit/s copy
    (cached in output/instagram). Instagram's downloader gives up on big files served from a home line."""
    seconds = seconds or video_assembler.media_duration(path) or 1.0
    if path.stat().st_size * 8 / seconds <= MAX_BITRATE:
        return path
    out = settings.output_dir / "instagram" / path.name
    if out.is_file() and out.stat().st_mtime >= path.stat().st_mtime:
        return out
    ffmpeg = video_downloader.ffmpeg_path()
    if not ffmpeg:
        raise InstagramError("ffmpeg is needed to make the Instagram copy of the video")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.mp4")
    result = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(path), "-c:v", "libx264", "-preset", "veryfast",
                             "-b:v", "3500k", "-maxrate", "4000k", "-bufsize", "8000k", "-pix_fmt", "yuv420p",
                             "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-movflags", "+faststart", str(tmp)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise InstagramError(f"Couldn't make the Instagram copy: {result.stderr.strip()[-300:]}")
    tmp.replace(out)
    return out


def post_reel(settings: Settings, filename: str, text: str = "", first_comment: str = "",
              progress: ProgressFn = lambda f, m: None) -> dict[str, Any]:
    """Post the Short as a Reel (also shown in the feed); recorded on the Short as uploads["instagram"].
    first_comment, when given, is posted under the Reel right away."""
    short = shorts.get_short(settings, filename)
    path = shorts.short_path(settings, filename)
    seconds = short.duration_seconds or 0
    if seconds and not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise InstagramError(f"Instagram Reels must be 3 s to 15 min long; this one is {seconds:.0f} s")
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise InstagramError("Instagram takes Reels up to 300 MB; this file is bigger")
    text = (text.strip() or caption(short))[:publish.CAPTION_MAX]

    progress(0.0, "Connecting to Instagram")
    access_token = token(settings)
    user = account(settings)
    progress(0.02, "Making the Instagram copy of the video")
    upload = upload_copy(settings, path, seconds)
    progress(0.05, "Opening a temporary link to the video")
    try:
        with tunnel.public_file(upload) as video_url:
            progress(0.15, "Instagram is downloading the video")
            container = _call("POST", f"{GRAPH}/{user['user_id']}/media", access_token, data={
                "media_type": "REELS", "video_url": video_url, "caption": text, "share_to_feed": "true"})["id"]
            _wait(container, access_token, progress)
    except tunnel.TunnelError as err:
        raise InstagramError(str(err)) from err
    progress(0.9, "Publishing the Reel")
    media_id = _call("POST", f"{GRAPH}/{user['user_id']}/media_publish", access_token,
                     data={"creation_id": container})["id"]
    try:
        url = _call("GET", f"{GRAPH}/{media_id}", access_token, params={"fields": "permalink"}).get("permalink", "")
    except InstagramError:
        url = ""
    record: dict[str, Any] = {"media_id": media_id, "url": url or f"https://www.instagram.com/{user.get('username', '')}/",
                              "caption": text, "username": user.get("username", ""),
                              "uploaded_at": utcnow().isoformat()}
    if first_comment.strip():
        try:
            reply = _call("POST", f"{GRAPH}/{media_id}/comments", access_token,
                          data={"message": first_comment.strip()[:2200]})
            record["comment"] = {"comment_id": reply.get("id", ""), "text": first_comment.strip()}
        except InstagramError as err:
            logger.warning("The Reel is up but the first comment failed: %s", err)
            record["comment_error"] = str(err)
    shorts.update_short(settings, filename, lambda s: s.uploads.__setitem__("instagram", record))
    progress(1.0, "Posted on Instagram")
    return record
