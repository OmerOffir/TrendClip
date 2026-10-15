"""Post Shorts on Instagram as Reels with the official API (Instagram API with Instagram Login).

Needs a Professional (Creator or Business) account and INSTAGRAM_ACCESS_TOKEN in the channel's
channel.env (README -> Instagram). The token lasts 60 days; channel_stats.instagram_token refreshes
it weekly into token_instagram.json, so it never runs out while TrendClip is used.

A Reel goes out in three steps: create a container, send the file to rupload.facebook.com straight
from this machine (no public URL needed), and publish once Instagram has processed it. Instagram
allows 100 API posts a day; the caption is the Instagram text from the Upload tab.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import requests

from . import channel_stats, publish, shorts
from .config import Settings
from .models import utcnow

logger = logging.getLogger(__name__)

ProgressFn = Callable[[float | None, str], None]
VERSION = "v23.0"
GRAPH = f"https://graph.instagram.com/{VERSION}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{VERSION}"
MIN_SECONDS, MAX_SECONDS = 3, 15 * 60
MAX_BYTES = 300 * 1024 * 1024
PROCESS_TIMEOUT = 15 * 60
TIMEOUT = 120

_accounts: dict[str, dict[str, Any]] = {}  # token -> account, so the dashboard doesn't re-read it


class InstagramError(RuntimeError):
    pass


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
    elif err.get("code") in (9, 4, 17, 32, 613) or err.get("error_subcode") == 2207042:
        text += " (Instagram's limit: at most 100 API posts a day)"
    return f"Instagram: {text}"


def _call(method: str, url: str, access_token: str, **kwargs: Any) -> dict[str, Any]:
    try:
        resp = requests.request(method, url, params={"access_token": access_token, **kwargs.pop("params", {})},
                                timeout=TIMEOUT, **kwargs)
    except requests.RequestException as err:
        raise InstagramError(f"Could not reach Instagram: {err}") from err
    if resp.status_code >= 400:
        raise InstagramError(_error(resp))
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
    container = _call("POST", f"{GRAPH}/{user['user_id']}/media", access_token, data={
        "media_type": "REELS", "upload_type": "resumable", "caption": text, "share_to_feed": "true"})["id"]

    progress(0.05, "Uploading to Instagram")
    with path.open("rb") as f:
        try:
            resp = requests.post(f"{RUPLOAD}/{container}", data=f, timeout=max(TIMEOUT, size / 200_000), headers={
                "Authorization": f"OAuth {access_token}", "offset": "0", "file_size": str(size)})
        except requests.RequestException as err:
            raise InstagramError(f"Uploading to Instagram failed: {err}") from err
    if resp.status_code >= 400:
        raise InstagramError(_error(resp))

    progress(0.6, "Instagram is processing the video")
    _wait(container, access_token, progress)
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
