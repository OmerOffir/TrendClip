"""Upload tab backend: per-platform texts (YouTube / TikTok / Instagram) and YouTube Shorts upload.

Texts come from a template built from the Short (instant, no key) or from Gemini (better hooks,
tags and platform style). Credits (gameplay, music, pop-up images) are always appended.

YouTube upload uses OAuth with the Desktop client in client_secret.json (git-ignored). The login is
done in the browser through the dashboard; the token is saved to token_youtube.json (git-ignored).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, field_validator

from . import script_writer, shorts
from .config import PROJECT_ROOT, Settings
from .models import utcnow

logger = logging.getLogger(__name__)

ProgressFn = Callable[[float | None, str], None]
CLIENT_SECRETS = Path(os.getenv("YOUTUBE_CLIENT_SECRETS", PROJECT_ROOT / "client_secret.json"))
TOKEN_FILE = Path(os.getenv("YOUTUBE_TOKEN_FILE", PROJECT_ROOT / "token_youtube.json"))
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",  # show which channel is connected
    "https://www.googleapis.com/auth/youtube.force-ssl",  # post the Part 1 comment (pinning is manual)
]
COMMENT_SCOPE = SCOPES[2]
GAMING_CATEGORY = "20"
YT_TITLE_MAX, YT_TAGS_MAX_CHARS, CAPTION_MAX = 100, 450, 2200
INSTAGRAM_MAX_HASHTAGS = 5  # Instagram allows at most 5 hashtags per post
MIN_SCHEDULE_MINUTES, MAX_SCHEDULE_DAYS = 15, 180
Privacy = Literal["private", "unlisted", "public"]

# Google answers with the scopes actually granted, which may be a superset; don't treat that as an error.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class PublishError(RuntimeError):
    pass


# --------------------------------------------------------------------------- texts


class YouTubeText(BaseModel):
    title: str = Field(description="Shorts title, at most 90 characters, ending with #shorts.")
    description: str = Field(description="2-4 short sentences and a call to action. No hashtags, no credits.")
    tags: list[str] = Field(description="8-15 search keywords and phrases people type on YouTube, without #.")
    hashtags: list[str] = Field(description="3-5 hashtags starting with #, including #shorts.")


class SocialText(BaseModel):
    caption: str = Field(description="The caption text without hashtags and without credits.")
    hashtags: list[str] = Field(description="Hashtags starting with #.")
    mentions: list[str] = Field(default_factory=list, description=(
        "@handles worth tagging on this platform: only official accounts of the game or its publisher that "
        "you are sure exist (e.g. @rockstargames). Empty when unsure."))


class GeminiTexts(BaseModel):
    youtube: YouTubeText
    tiktok: SocialText = Field(description=(
        "TikTok: caption of 1-2 short lines (under 150 characters) with a hook or a question that invites "
        "comments; 3-5 hashtags mixing broad (#gaming, #storytime) and niche ones."))
    instagram: SocialText = Field(description=(
        "Instagram Reels: caption of 2-4 short lines with line breaks and a call to action (save, share, "
        "follow); at most 5 hashtags."))


class PlatformTexts(GeminiTexts):
    source: Literal["template", "gemini"] = "template"
    model: str = ""
    credit: str = ""


def _hashtags(tags: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for tag in tags:
        tag = "#" + re.sub(r"[^\w]", "", tag.lstrip("#@"))
        if len(tag) > 1 and tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    return out[:limit]


def _mentions(handles: list[str]) -> list[str]:
    out = []
    for h in handles:
        h = "@" + re.sub(r"[^\w.]", "", h.lstrip("@"))
        if len(h) > 1 and h.lower() not in {m.lower() for m in out}:
            out.append(h)
    return out[:5]


def _tags(tags: list[str]) -> list[str]:
    out, total = [], 0
    for tag in tags:
        tag = re.sub(r"[<>\"#]", "", tag).strip()
        if not tag or tag.lower() in {t.lower() for t in out}:
            continue
        cost = len(tag) + (2 if " " in tag else 0) + 1  # YouTube counts quotes around multi-word tags
        if total + cost > YT_TAGS_MAX_CHARS:
            break
        out.append(tag)
        total += cost
    return out


def _yt_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title.replace("<", "").replace(">", "")).strip()
    if "#shorts" not in title.lower():
        title = f"{title[: YT_TITLE_MAX - 8].rstrip()} #shorts"
    return title[:YT_TITLE_MAX]


def _story_part(short: shorts.ShortVideo) -> str:
    """The description without the credit block that render_short appended."""
    text = short.description
    if short.credit and short.credit in text:
        text = text.replace(short.credit, "")
    return text.strip()


def clean(texts: GeminiTexts, credit: str, source: str, model: str = "") -> PlatformTexts:
    yt = texts.youtube
    hashtags = _hashtags(yt.hashtags + ["#shorts"], 5)
    return PlatformTexts(
        youtube=YouTubeText(title=_yt_title(yt.title), description=yt.description.strip(),
                            tags=_tags(yt.tags), hashtags=hashtags),
        tiktok=SocialText(caption=texts.tiktok.caption.strip(), hashtags=_hashtags(texts.tiktok.hashtags, 6),
                          mentions=_mentions(texts.tiktok.mentions)),
        instagram=SocialText(caption=texts.instagram.caption.strip(),
                             hashtags=_hashtags(texts.instagram.hashtags, INSTAGRAM_MAX_HASHTAGS),
                             mentions=_mentions(texts.instagram.mentions)),
        source=source, model=model, credit=credit,
    )


def _social_cta(short: shorts.ShortVideo) -> str:
    if not short.part:
        return "Follow for more 🎮"
    if short.part < (short.parts_total or 2):
        return f"Part {short.part + 1} drops tomorrow 🔔 Follow so you don't miss it!"
    return "Missed Part 1? It's on my page 👆 Drop your crazy stories in the comments!"


def template_texts(short: shorts.ShortVideo) -> PlatformTexts:
    """Instant texts from what the Create tab already produced (no API call)."""
    base = [t for t in short.hashtags if t.lower() != "#shorts"]
    game_tag = "#" + re.sub(r"[^\w]", "", short.game.split("/")[0])
    story = _story_part(short)
    first = re.split(r"(?<=[.!?])\s", story, maxsplit=1)[0] if story else short.title
    tiktok = f"{short.title} 👀" + (f"\n{_social_cta(short)}" if short.part else "")
    texts = GeminiTexts(
        youtube=YouTubeText(title=short.title, description=story,
                            tags=[t.lstrip("#") for t in base] + [short.game, "gaming", "shorts"],
                            hashtags=base[:4]),
        tiktok=SocialText(caption=tiktok, hashtags=base[:3] + [game_tag, "#fyp"]),
        instagram=SocialText(caption=f"{first}\n\n{_social_cta(short)}", hashtags=base[:4] + [game_tag]),
    )
    return clean(texts, short.credit, "template")


PROMPT = """Write upload texts for this vertical video for YouTube Shorts, TikTok and Instagram Reels.
Match each platform's style. English. Do not include credits (they are added automatically).
Do not invent facts that are not in the script.

Game shown in the background: {game}
Current title: {title}
Voiceover script: {script}
Current description: {description}
Current hashtags: {hashtags}"""

SERIES_PROMPT = """
This video is Part {part} of {total} of one story. Every title must contain "(Part {part})" and the
hashtags must include #part{part}. {cta}"""


def _series_prompt(short: shorts.ShortVideo) -> str:
    if not short.part:
        return ""
    total = short.parts_total or 2
    handle = short.channel_handle or "the channel"
    if short.part < total:
        cta = (f"It ends on a cliffhanger: every caption should tease Part {short.part + 1} dropping tomorrow "
               f"and ask people to subscribe/follow ({handle}) so they don't miss it.")
    else:
        cta = ("It resolves the story: mention that Part 1 is on the channel, and ask people to subscribe for "
               "daily side quest stories and to drop their own crazy stories in the comments.")
    return SERIES_PROMPT.format(part=short.part, total=total, cta=cta)


def gemini_texts(settings: Settings, short: shorts.ShortVideo, progress: ProgressFn = lambda f, m: None,
                 client=None) -> PlatformTexts:
    api_key = script_writer.gemini_api_key(settings)
    if client is None and not api_key:
        raise PublishError("GEMINI_API_KEY is not set in .env")
    from google import genai
    from google.genai import errors, types

    model = script_writer.gemini_model(settings)
    client = client or genai.Client(api_key=api_key)
    prompt = PROMPT.format(game=short.game, title=short.title, script=short.script,
                           description=_story_part(short), hashtags=" ".join(short.hashtags))
    prompt += _series_prompt(short)
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=GeminiTexts,
                                         temperature=0.8)
    try:
        response, model = script_writer._generate_with_fallback(client, model, [prompt], config, progress,
                                                                errors.APIError)
    except errors.APIError as err:
        raise script_writer._explain(err, model) from err
    result = response.parsed
    if not isinstance(result, GeminiTexts):
        if not response.text:
            raise PublishError("Gemini returned an empty answer; try again")
        result = GeminiTexts.model_validate_json(response.text)
    return clean(result, short.credit, "gemini", model)


def full_text(text: SocialText, credit: str) -> str:
    """Ready-to-paste caption: text, mentions, hashtags, credits (cut to the platform limit)."""
    parts = [text.caption.strip(), " ".join(text.mentions), " ".join(text.hashtags), credit.strip()]
    return "\n\n".join(p for p in parts if p)[:CAPTION_MAX]


def youtube_description(text: YouTubeText, credit: str, links: str = "") -> str:
    parts = [text.description.strip(), links.strip(), " ".join(text.hashtags), credit.strip()]
    return "\n\n".join(p for p in parts if p)[:5000]


def series_info(settings: Settings, short: shorts.ShortVideo) -> dict[str, Any] | None:
    """Links between the parts of a story, computed live: once a part is on YouTube, the other parts'
    descriptions and the pinned comment point to it."""
    if not short.series_id or not short.part:
        return None
    handle = short.channel_handle or settings.channel_handle
    total = short.parts_total or 2
    siblings = []
    for s in shorts.series_parts(settings, short.series_id):
        yt = s.uploads.get("youtube") or {}
        siblings.append({"filename": s.filename, "part": s.part, "title": s.title, "ready": s.ready,
                         "youtube_url": yt.get("url", ""), "scheduled_for": yt.get("scheduled_for"),
                         "uploaded_at": yt.get("uploaded_at")})
    url = {s["part"]: s["youtube_url"] for s in siblings if s["youtube_url"]}
    channel = f"https://www.youtube.com/{handle}"
    links = [f"Part {p}: {url[p]}" for p in sorted(url) if p != short.part]

    if short.part < total:
        nxt = url.get(short.part + 1)
        comment = (f"Part {short.part + 1} is out 👉 {nxt}\nSubscribe {handle} for daily side quest stories!" if nxt
                   else f"{short.pinned_comment or f'Part {short.part + 1} drops tomorrow!'}\n🔔 {channel}")
    else:
        first = url.get(1)
        comment = (f"Missed Part 1? Watch it here 👉 {first}\n" if first else f"Missed Part 1? It's on {handle} 👉 {channel}\n")
        comment += "Sub for daily side quest stories & drop your crazy stories in the comments!"
    return {"part": short.part, "total": total, "story_name": short.story_name, "handle": handle,
            "siblings": siblings, "links": "\n".join(links), "pinned_comment": comment}


def texts_for(settings: Settings, filename: str, refresh: Literal["", "template", "gemini"] = "",
              progress: ProgressFn = lambda f, m: None) -> shorts.ShortVideo:
    """Texts stored on the Short; created from the template the first time."""
    short = shorts.get_short(settings, filename)
    if short.texts and not refresh:
        return short
    texts = gemini_texts(settings, short, progress) if refresh == "gemini" else template_texts(short)
    return shorts.update_short(settings, filename, lambda s: setattr(s, "texts", texts.model_dump()))


def save_texts(settings: Settings, filename: str, texts: PlatformTexts) -> shorts.ShortVideo:
    cleaned = clean(texts, texts.credit, texts.source, texts.model)
    return shorts.update_short(settings, filename, lambda s: setattr(s, "texts", cleaned.model_dump()))


# --------------------------------------------------------------------------- YouTube OAuth


_flows: dict[str, tuple[float, Any]] = {}
_flows_lock = threading.Lock()
FLOW_TTL = 600


def youtube_status() -> dict[str, Any]:
    status: dict[str, Any] = {"client_secret": CLIENT_SECRETS.is_file(), "connected": False, "channel": None}
    if not status["client_secret"] or not TOKEN_FILE.is_file():
        return status
    try:
        youtube = _youtube()
        items = youtube.channels().list(part="snippet", mine=True).execute().get("items", [])
    except PublishError as err:
        status["error"] = str(err)
        return status
    except Exception as err:  # noqa: BLE001 - token revoked, network...
        logger.warning("YouTube status check failed: %s", err)
        status["error"] = f"Could not reach YouTube: {err}"
        return status
    status["connected"] = True
    status["can_comment"] = can_comment()
    if items:
        snip = items[0]["snippet"]
        status["channel"] = {"id": items[0]["id"], "title": snip.get("title", ""),
                             "thumbnail": ((snip.get("thumbnails") or {}).get("default") or {}).get("url", "")}
    return status


def start_login(redirect_uri: str) -> str:
    if not CLIENT_SECRETS.is_file():
        raise PublishError(f"{CLIENT_SECRETS.name} not found in the project folder")
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(str(CLIENT_SECRETS), scopes=SCOPES, redirect_uri=redirect_uri,
                                         autogenerate_code_verifier=True)
    state = secrets.token_urlsafe(24)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true",
                                    state=state)
    now = time.monotonic()
    with _flows_lock:
        for key in [k for k, (t, _) in _flows.items() if now - t > FLOW_TTL]:
            _flows.pop(key, None)
        _flows[state] = (now, flow)
    return url


def finish_login(state: str, code: str) -> None:
    with _flows_lock:
        entry = _flows.pop(state, None)
    if entry is None:
        raise PublishError("This login link expired; press Connect YouTube again")
    flow = entry[1]
    try:
        flow.fetch_token(code=code)
    except Exception as err:  # noqa: BLE001 - oauthlib raises many types
        raise PublishError(f"Google login failed: {err}") from err
    _save_token(flow.credentials.to_json())


def _save_token(data: str) -> None:
    TOKEN_FILE.write_text(data, encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def logout() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def can_comment() -> bool:
    """Tokens from before comment posting existed lack the scope; reconnecting adds it."""
    import json

    try:
        scopes = json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("scopes") or []
    except (OSError, ValueError):
        return False
    if isinstance(scopes, str):
        scopes = scopes.split()
    return COMMENT_SCOPE in scopes


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_FILE.is_file():
        raise PublishError("YouTube is not connected; press Connect YouTube first")
    # Keep the scopes stored in the token: asking for new ones on refresh fails with invalid_scope.
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
    if not creds.valid:
        if not creds.refresh_token:
            raise PublishError("YouTube login expired; connect again")
        try:
            creds.refresh(Request())
        except Exception as err:  # noqa: BLE001 - RefreshError / transport errors
            raise PublishError(f"YouTube login expired or was revoked; connect again ({err})") from err
        _save_token(creds.to_json())
    return creds


def _youtube():
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=_credentials(), cache_discovery=False)


# --------------------------------------------------------------------------- YouTube upload


class YouTubeUploadRequest(BaseModel):
    title: str = Field(min_length=1, max_length=YT_TITLE_MAX)
    description: str = Field("", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=60)
    privacy: Privacy = "private"
    category_id: str = Field(GAMING_CATEGORY, pattern=r"^\d{1,3}$")
    made_for_kids: bool = False
    synthetic_media: bool = False  # "altered or synthetic content" disclosure
    # Scheduled publishing: uploaded as private, YouTube makes it public at this time.
    publish_at: datetime | None = None

    @field_validator("publish_at")
    @classmethod
    def _future(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("publish_at needs a timezone (send ISO 8601 with Z or an offset)")
        value = value.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)
        if value < now + timedelta(minutes=MIN_SCHEDULE_MINUTES):
            raise ValueError(f"Pick a time at least {MIN_SCHEDULE_MINUTES} minutes from now")
        if value > now + timedelta(days=MAX_SCHEDULE_DAYS):
            raise ValueError(f"YouTube schedules at most {MAX_SCHEDULE_DAYS} days ahead here")
        return value.replace(microsecond=0)


def _api_error_text(err: Exception) -> str:
    content = getattr(err, "content", b"") or b""
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)
    if "uploadLimitExceeded" in text:
        return "YouTube says the channel reached its daily upload limit; try again tomorrow"
    if "quotaExceeded" in text:
        return "YouTube API quota used up for today (an upload costs ~100 units of the 10,000/day)"
    if "youtubeSignupRequired" in text:
        return "This Google account has no YouTube channel yet; create one on youtube.com first"
    return f"YouTube upload failed: {getattr(err, 'reason', '') or text[:300] or err}"


def upload_youtube(settings: Settings, filename: str, req: YouTubeUploadRequest,
                   progress: ProgressFn = lambda f, m: None, youtube=None) -> dict[str, Any]:
    from googleapiclient.errors import HttpError, ResumableUploadError
    from googleapiclient.http import MediaFileUpload

    path = shorts.short_path(settings, filename)
    title = _yt_title(req.title)
    privacy = "private" if req.publish_at else req.privacy  # YouTube only schedules private videos
    status_body = {"privacyStatus": privacy, "selfDeclaredMadeForKids": req.made_for_kids,
                   "containsSyntheticMedia": req.synthetic_media}
    if req.publish_at:
        status_body["publishAt"] = req.publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "snippet": {"title": title, "description": req.description, "tags": _tags(req.tags),
                    "categoryId": req.category_id},
        "status": status_body,
    }
    progress(0.0, "Connecting to YouTube")
    youtube = youtube or _youtube()
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=4 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media,
                                      notifySubscribers=privacy == "public" or bool(req.publish_at))
    response = None
    retries = 0
    try:
        while response is None:
            try:
                status, response = request.next_chunk()
            except HttpError as err:
                if getattr(err.resp, "status", 0) in (500, 502, 503, 504) and retries < 5:
                    retries += 1
                    time.sleep(2 ** retries)
                    continue
                raise
            if status:
                progress(min(status.progress(), 0.99), f"Uploading to YouTube: {status.progress():.0%}")
    except (HttpError, ResumableUploadError) as err:
        raise PublishError(_api_error_text(err)) from err

    video_id = response["id"]
    status = response.get("status") or {}
    record = {
        "video_id": video_id,
        "url": f"https://youtube.com/shorts/{video_id}",
        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
        "privacy": status.get("privacyStatus", privacy),
        "requested_privacy": "scheduled" if req.publish_at else req.privacy,
        # YouTube echoes publishAt only when it accepted the schedule.
        "scheduled_for": status.get("publishAt") or None,
        "requested_publish_at": status_body.get("publishAt"),
        "title": title,
        "uploaded_at": utcnow().isoformat(),
    }
    shorts.update_short(settings, filename, lambda s: s.uploads.__setitem__("youtube", record))
    progress(1.0, "Uploaded")
    return record


class CommentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


def post_comment(settings: Settings, filename: str, text: str, youtube=None) -> dict[str, Any]:
    """Post the comment as the channel. The API cannot pin comments: pin it in YouTube (⋮ → Pin)."""
    from googleapiclient.errors import HttpError

    short = shorts.get_short(settings, filename)
    video_id = (short.uploads.get("youtube") or {}).get("video_id")
    if not video_id:
        raise PublishError("Upload this Short to YouTube first")
    if youtube is None and not can_comment():
        raise PublishError("Reconnect YouTube once to allow posting comments (Disconnect → Connect YouTube)")
    youtube = youtube or _youtube()
    body = {"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text.strip()}}}}
    try:
        response = youtube.commentThreads().insert(part="snippet", body=body).execute()
    except HttpError as err:
        detail = _api_error_text(err).replace("YouTube upload failed", "YouTube refused the comment")
        if (short.uploads.get("youtube") or {}).get("privacy") == "private":
            detail += " (comments only work once the video is public or unlisted)"
        raise PublishError(detail) from err
    record = {"comment_id": response.get("id", ""), "text": text.strip(), "posted_at": utcnow().isoformat(),
              "url": f"https://www.youtube.com/watch?v={video_id}&lc={response.get('id', '')}"}

    def apply(s: shorts.ShortVideo) -> None:
        s.uploads["youtube"] = {**(s.uploads.get("youtube") or {}), "comment": record}

    shorts.update_short(settings, filename, apply)
    return record


def set_posted(settings: Settings, filename: str, platform: Literal["youtube", "tiktok", "instagram"], posted: bool):
    """Manual "I posted it" (YouTube too, for videos uploaded in YouTube Studio)."""
    return shorts.update_short(settings, filename, lambda s: s.posted.__setitem__(platform, posted))
