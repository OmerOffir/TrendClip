"""FastAPI dashboard: JSON API over the Phase 1 pipeline + a static single-page UI.

Run: python3 run.py --web   (or: uvicorn trendclip.web.app:app --reload)
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from html import escape as html_escape

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__, music, publish, script_writer, shorts, stickers, video_downloader, voiceover
from ..config import ConfigError, Settings, get_settings
from ..main import make_youtube_client, run_pipeline
from ..youtube_client import YouTubeAPIError, YouTubeAuthError, YouTubeQuotaError

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# Each trends call costs ~2-5 quota units; caching keeps casual browsing nearly free.
TRENDS_TTL_SECONDS = 300
CATEGORIES_TTL_SECONDS = 24 * 3600

REGIONS: dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "IN": "India",
    "IL": "Israel",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "NL": "Netherlands",
    "SE": "Sweden",
    "PL": "Poland",
    "TR": "Turkey",
    "SA": "Saudi Arabia",
    "AE": "United Arab Emirates",
    "BR": "Brazil",
    "MX": "Mexico",
    "JP": "Japan",
    "KR": "South Korea",
}

# Multi-region views: each region's chart is fetched and merged (≈2 quota units per region).
REGION_GROUPS: dict[str, str] = {
    "Worldwide (10 regions)": "US,GB,CA,AU,IN,DE,FR,BR,JP,KR",
    "English-speaking": "US,GB,CA,AU",
    "Europe": "GB,DE,FR,ES,IT,NL,SE,PL",
}
DEFAULT_WEB_REGION = REGION_GROUPS["Worldwide (10 regions)"]


class _TTLCache:
    def __init__(self) -> None:
        self._data: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, ttl: float) -> Any | None:
        with self._lock:
            hit = self._data.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        return None

    def set(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)


def _scan_stickers() -> None:
    try:
        found = stickers.scan(get_settings())
        logger.info("Sticker library: %d files %s", len(found), stickers.summary(found))
    except Exception:  # noqa: BLE001 - the library is rescanned on the next render anyway
        logger.exception("Sticker scan failed")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Scan (and tag new stickers with Gemini) in the background so startup stays instant.
    threading.Thread(target=_scan_stickers, name="sticker-scan", daemon=True).start()
    yield


app = FastAPI(title="TrendClipper", version=__version__, lifespan=_lifespan)
_cache = _TTLCache()
_downloads = video_downloader.DownloadManager()
_create = shorts.CreateManager()


def _settings_ok() -> bool:
    try:
        get_settings()
        return True
    except ConfigError:
        return False


def _base_settings() -> Settings:
    try:
        return get_settings()
    except ConfigError as err:
        raise HTTPException(status_code=500, detail=str(err)) from err


def _youtube_http_error(err: YouTubeAPIError) -> HTTPException:
    if isinstance(err, YouTubeQuotaError):
        return HTTPException(status_code=429, detail=f"YouTube quota exhausted: {err}")
    if isinstance(err, YouTubeAuthError):
        return HTTPException(status_code=502, detail=f"YouTube rejected the API key: {err}")
    return HTTPException(status_code=502, detail=str(err))


@app.get("/api/config")
def config() -> dict[str, Any]:
    settings = _base_settings()
    return {
        "version": __version__,
        "regions": REGIONS,
        "region_groups": REGION_GROUPS,
        "defaults": {
            "region": DEFAULT_WEB_REGION,
            "category": settings.yt_category_id or "all",
            "max_results": settings.yt_max_results_per_region,
            "top": settings.top_topics,
        },
        "cache_ttl_seconds": TRENDS_TTL_SECONDS,
        "backgrounds": {
            "pexels_enabled": settings.pexels_enabled,
            "sources": settings.background_sources,
            "clip_seconds": settings.background_clip_seconds,
            "orientation": settings.background_orientation,
            "channels": settings.ncg_channels,
        },
    }


@app.get("/api/categories")
def categories(region: str = Query("US", pattern=r"^[A-Za-z]{2}$")) -> dict[str, Any]:
    region = region.upper()
    key = ("categories", region)
    cached = _cache.get(key, CATEGORIES_TTL_SECONDS)
    if cached is None:
        settings = _base_settings()
        try:
            cached = make_youtube_client(settings).get_categories(region)
        except YouTubeAPIError as err:
            raise _youtube_http_error(err) from err
        cached.sort(key=lambda c: c["title"])
        _cache.set(key, cached)
    return {"region": region, "categories": cached}


@app.get("/api/trends")
def trends(
    region: str = Query("US", description="Region code, or comma-separated codes"),
    category: str = Query("20", description="YouTube category id, or 'all'"),
    max_results: int = Query(50, ge=1, le=200),
    top: int = Query(10, ge=1, le=50),
    refresh: bool = Query(False, description="Bypass the cache"),
) -> Response:
    try:
        settings = _base_settings().with_overrides(
            yt_regions=region,
            yt_category_id=category,
            yt_max_results_per_region=max_results,
            top_topics=top,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    key = ("trends", tuple(settings.yt_regions), settings.yt_category_id, max_results, top)
    cached = None if refresh else _cache.get(key, TRENDS_TTL_SECONDS)
    if cached is not None:
        return Response(cached, media_type="application/json", headers={"X-Cache": "HIT"})

    try:
        result = run_pipeline(settings)
    except YouTubeAPIError as err:
        raise _youtube_http_error(err) from err
    payload = result.model_dump_json()
    _cache.set(key, payload)
    return Response(payload, media_type="application/json", headers={"X-Cache": "MISS"})


class DownloadRequest(BaseModel):
    game: str = Field(min_length=1, max_length=80)
    source: Literal["auto", "youtube", "pexels"] = "auto"
    seconds: int = Field(60, ge=0, le=3600)  # 0 = whole video
    orientation: Literal["landscape", "portrait"] = "landscape"


@app.get("/api/backgrounds/library")
def background_library(refresh: bool = False) -> dict[str, Any]:
    """No-Copyright channel catalogue: clips available per game (cached on disk for a day)."""
    settings = _base_settings()
    library = video_downloader.get_library(settings)
    try:
        videos = library.videos(refresh=refresh)
    except YouTubeAPIError as err:
        raise _youtube_http_error(err) from err
    return {
        "channels": [{"id": c["id"], "title": c["title"], "video_count": c["video_count"]} for c in library.channels],
        "total": len(videos),
        "counts": library.counts(),
    }


@app.get("/api/backgrounds")
def backgrounds() -> list[dict[str, Any]]:
    settings = _base_settings()
    return [clip.model_dump(mode="json") for clip in video_downloader.list_backgrounds(settings)]


@app.delete("/api/backgrounds/{filename}")
def delete_background(filename: str) -> dict[str, Any]:
    try:
        removed = video_downloader.delete_background(_base_settings(), filename)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    if not removed:
        raise HTTPException(status_code=404, detail="Clip not found")
    return {"deleted": filename}


@app.post("/api/backgrounds/download", status_code=202)
def start_download(req: DownloadRequest) -> dict[str, Any]:
    settings = _base_settings()
    sources = list(settings.background_sources) if req.source == "auto" else [req.source]
    if sources == ["pexels"] and not settings.pexels_enabled:
        raise HTTPException(status_code=400, detail="Pexels needs PEXELS_API_KEY in .env")
    job = _downloads.submit(settings, req.game, sources, req.seconds, req.orientation)
    return job.model_dump(mode="json")


@app.get("/api/backgrounds/jobs")
def download_jobs() -> list[dict[str, Any]]:
    return [job.model_dump(mode="json") for job in _downloads.list()]


@app.get("/api/backgrounds/jobs/{job_id}")
def download_job(job_id: str) -> dict[str, Any]:
    job = _downloads.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job.model_dump(mode="json")


# --------------------------------------------------------------------------- Create tab


class ScriptRequest(BaseModel):
    clip: str = Field(min_length=1)
    game: str = Field(min_length=1, max_length=80)
    trend_titles: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field("", max_length=1000)
    target_seconds: int = Field(30, ge=10, le=180)  # per part for multi
    watch_clip: bool = True
    mode: Literal["clip", "story"] = "clip"
    format: Literal["short", "long", "multi"] = "short"
    channel_handle: str | None = Field(None, pattern=r"^@[\w.-]{3,30}$")


class MusicPickRequest(BaseModel):
    source: str = Field("random", max_length=40)
    exclude: list[str] = Field(default_factory=list, max_length=50)


def _clip_or_http(settings: Settings, filename: str) -> None:
    try:
        shorts.clip_path(settings, filename)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


@app.get("/api/create/status")
def create_status() -> dict[str, Any]:
    settings = _base_settings()
    return {
        "gemini_enabled": script_writer.gemini_api_key(settings) is not None,
        "gemini_model": script_writer.gemini_model(settings),
        "voice": settings.tts_voice,
        "rate": settings.tts_rate,
        "target_seconds": settings.short_target_seconds,
        "words_per_second": script_writer.WORDS_PER_SECOND,
        "channel_handle": settings.channel_handle,
        "ctas": script_writer.series_ctas(settings.channel_handle),
    }


@app.get("/api/create/voices")
def create_voices() -> list[dict[str, Any]]:
    try:
        return voiceover.list_voices("en-")
    except voiceover.VoiceError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


@app.post("/api/create/script", status_code=202)
def create_script(req: ScriptRequest) -> dict[str, Any]:
    settings = _base_settings()
    _clip_or_http(settings, req.clip)
    if script_writer.gemini_api_key(settings) is None:
        raise HTTPException(status_code=400, detail="Add GEMINI_API_KEY to .env (https://aistudio.google.com/apikey)")
    job = _create.submit_script(settings, req.clip, req.game, req.trend_titles, req.notes,
                                req.target_seconds, req.watch_clip,
                                "story" if req.format == "multi" else req.mode, req.format, req.channel_handle)
    return job.model_dump(mode="json")


@app.get("/api/create/music")
def create_music() -> dict[str, Any]:
    """Music channels with their track counts (upload lists cached on disk for a day)."""
    settings = _base_settings()
    try:
        sources = music.get_library(settings).sources()
    except YouTubeAPIError as err:
        raise _youtube_http_error(err) from err
    except music.MusicError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return {"sources": sources, "volume": settings.music_volume}


@app.post("/api/create/music/pick")
def pick_music(req: MusicPickRequest) -> dict[str, Any]:
    """Download a random track (a few seconds) so it can be previewed before rendering."""
    settings = _base_settings()
    try:
        track = music.pick_track(settings, req.source, exclude=set(req.exclude))
    except YouTubeAPIError as err:
        raise _youtube_http_error(err) from err
    except music.MusicError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return track.model_dump(mode="json")


@app.post("/api/create/render", status_code=202)
def create_render(req: shorts.RenderRequest) -> dict[str, Any]:
    settings = _base_settings()
    _clip_or_http(settings, req.clip)
    return _create.submit_render(settings, req).model_dump(mode="json")


@app.post("/api/create/render-series", status_code=202)
def create_render_series(req: shorts.SeriesRenderRequest) -> dict[str, Any]:
    """Part 1 + Part 2 in one job: output/shorts/<StoryName>_Part1.mp4, _Part2.mp4."""
    settings = _base_settings()
    _clip_or_http(settings, req.clip)
    return _create.submit_series(settings, req).model_dump(mode="json")


def _sticker_list(found: list[stickers.StickerInfo]) -> dict[str, Any]:
    settings = _base_settings()
    return {
        "folder": str(settings.stickers_dir),
        "moods": list(stickers.MOODS),
        "counts": stickers.summary(found),
        "stickers": [{**s.model_dump(), "animated": s.animated} for s in found],
    }


@app.get("/api/stickers")
def list_stickers() -> dict[str, Any]:
    """The stickers/ folder with each file's category and moods (new files are tagged on the way)."""
    return _sticker_list(stickers.library(_base_settings()))


@app.post("/api/stickers/rescan")
def rescan_stickers(retag: bool = False) -> dict[str, Any]:
    settings = _base_settings()
    if retag:
        stickers._cache_file(settings).unlink(missing_ok=True)
        stickers._gemini_failed_at.clear()
    return _sticker_list(stickers.scan(settings))


@app.get("/api/create/jobs")
def create_jobs() -> list[dict[str, Any]]:
    return [job.model_dump(mode="json") for job in _create.list()]


@app.get("/api/create/jobs/{job_id}")
def create_job(job_id: str) -> dict[str, Any]:
    job = _create.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job")
    return job.model_dump(mode="json")


@app.get("/api/shorts")
def list_shorts() -> list[dict[str, Any]]:
    return [s.model_dump(mode="json") for s in shorts.list_shorts(_base_settings())]


@app.delete("/api/shorts/{filename}")
def delete_short(filename: str) -> dict[str, Any]:
    try:
        removed = shorts.delete_short(_base_settings(), filename)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    if not removed:
        raise HTTPException(status_code=404, detail="Short not found")
    return {"deleted": filename}


# --------------------------------------------------------------------------- Upload tab


class ReadyRequest(BaseModel):
    ready: bool


class PostedRequest(BaseModel):
    posted: bool


def _short_or_http(settings: Settings, filename: str) -> shorts.ShortVideo:
    try:
        return shorts.get_short(settings, filename)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


def _with_texts(short: shorts.ShortVideo) -> dict[str, Any]:
    """Short + ready-to-paste texts for each platform (+ links between the parts of a story)."""
    data = short.model_dump(mode="json")
    series = publish.series_info(_base_settings(), short) if short.series_id else None
    data["series"] = series
    if short.texts:
        t = publish.PlatformTexts.model_validate(short.texts)
        data["paste"] = {
            "youtube_description": publish.youtube_description(t.youtube, t.credit,
                                                               series["links"] if series else ""),
            "tiktok": publish.full_text(t.tiktok, t.credit),
            "instagram": publish.full_text(t.instagram, t.credit),
        }
    return data


@app.post("/api/shorts/{filename}/ready")
def mark_ready(filename: str, req: ReadyRequest) -> dict[str, Any]:
    settings = _base_settings()
    _short_or_http(settings, filename)
    return shorts.set_ready(settings, filename, req.ready).model_dump(mode="json")


@app.get("/api/upload/shorts")
def upload_queue() -> list[dict[str, Any]]:
    """Shorts marked ready to upload, newest first, with their platform texts."""
    settings = _base_settings()
    ready = [s for s in shorts.list_shorts(settings) if s.ready]
    out = []
    for s in sorted(ready, key=lambda s: s.ready_at or s.created_at, reverse=True):
        if not s.texts:
            s = publish.texts_for(settings, s.filename)
        out.append(_with_texts(s))
    return out


@app.put("/api/upload/shorts/{filename}/texts")
def save_texts(filename: str, texts: publish.PlatformTexts) -> dict[str, Any]:
    settings = _base_settings()
    _short_or_http(settings, filename)
    return _with_texts(publish.save_texts(settings, filename, texts))


@app.post("/api/upload/shorts/{filename}/texts/template")
def template_texts(filename: str) -> dict[str, Any]:
    settings = _base_settings()
    _short_or_http(settings, filename)
    return _with_texts(publish.texts_for(settings, filename, refresh="template"))


@app.post("/api/upload/shorts/{filename}/texts/gemini", status_code=202)
def gemini_texts(filename: str) -> dict[str, Any]:
    settings = _base_settings()
    short = _short_or_http(settings, filename)
    if script_writer.gemini_api_key(settings) is None:
        raise HTTPException(status_code=400, detail="Add GEMINI_API_KEY to .env (https://aistudio.google.com/apikey)")
    job = _create.submit("copy", short.game, lambda p: _with_texts(
        publish.texts_for(settings, filename, refresh="gemini", progress=p)))
    return job.model_dump(mode="json")


@app.post("/api/upload/shorts/{filename}/posted/{platform}")
def mark_posted(filename: str, platform: Literal["tiktok", "instagram"], req: PostedRequest) -> dict[str, Any]:
    settings = _base_settings()
    _short_or_http(settings, filename)
    return _with_texts(publish.set_posted(settings, filename, platform, req.posted))


@app.post("/api/upload/shorts/{filename}/youtube/comment")
def youtube_comment(filename: str, req: publish.CommentRequest) -> dict[str, Any]:
    settings = _base_settings()
    _short_or_http(settings, filename)
    try:
        publish.post_comment(settings, filename, req.text)
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return _with_texts(shorts.get_short(settings, filename))


@app.get("/api/upload/youtube/status")
def youtube_status() -> dict[str, Any]:
    return publish.youtube_status()


def _callback_url(request: Request) -> str:
    # Desktop OAuth clients accept any loopback port; keep the host the browser used.
    host = "localhost" if request.url.hostname in ("localhost", "127.0.0.1", "::1") else request.url.hostname
    return f"http://{host}:{request.url.port or 80}/api/upload/youtube/callback"


@app.post("/api/upload/youtube/connect")
def youtube_connect(request: Request) -> dict[str, Any]:
    try:
        return {"auth_url": publish.start_login(_callback_url(request))}
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.get("/api/upload/youtube/callback", include_in_schema=False)
def youtube_callback(state: str = "", code: str = "", error: str = "") -> HTMLResponse:
    if error:
        message, ok = f"Google login was cancelled or refused ({error}).", False
    else:
        try:
            publish.finish_login(state, code)
            message, ok = "YouTube connected. You can close this tab.", True
        except publish.PublishError as err:
            message, ok = str(err), False
    color = "#3ddc97" if ok else "#ff4d5e"
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><title>TrendClipper · YouTube</title>
<body style="background:#0b0d12;color:#e8ebf2;font:16px system-ui;display:grid;place-items:center;height:90vh">
<div style="text-align:center"><h2 style="color:{color}">{'Connected' if ok else 'Not connected'}</h2>
<p>{html_escape(message)}</p><p><a style="color:#8b93a7" href="/#upload">Back to TrendClipper</a></p></div>
<script>if ({'true' if ok else 'false'}) {{ try {{ window.opener && window.opener.postMessage("trendclip:youtube", "*"); }} catch (e) {{}}
setTimeout(() => {{ if (window.opener) window.close(); else location.href = "/#upload"; }}, 1500); }}</script>""")


@app.post("/api/upload/youtube/logout")
def youtube_logout() -> dict[str, Any]:
    publish.logout()
    return {"connected": False}


@app.post("/api/upload/shorts/{filename}/youtube", status_code=202)
def upload_youtube(filename: str, req: publish.YouTubeUploadRequest) -> dict[str, Any]:
    settings = _base_settings()
    short = _short_or_http(settings, filename)
    if not publish.TOKEN_FILE.is_file():
        raise HTTPException(status_code=400, detail="Connect YouTube first")
    job = _create.submit("upload", short.game, lambda p: publish.upload_youtube(settings, filename, req, p))
    return job.model_dump(mode="json")


@app.middleware("http")
async def _no_stale_assets(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        # Force revalidation so a browser never pairs new HTML with an old app.js.
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if _settings_ok():
    _s = get_settings()
    for _route, _dir in (("backgrounds", _s.backgrounds_dir), ("shorts", _s.shorts_dir), ("music", _s.music_dir),
                         ("stickers", _s.stickers_dir)):
        _dir.mkdir(parents=True, exist_ok=True)
        app.mount(f"/media/{_route}", StaticFiles(directory=_dir), name=_route)


def _asset_version() -> str:
    return str(max(int(p.stat().st_mtime) for p in STATIC_DIR.iterdir() if p.is_file()))


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    version = _asset_version()
    for asset in ("styles.css", "app.js", "create.js", "upload.js"):
        html = html.replace(f"/static/{asset}", f"/static/{asset}?v={version}")
    return HTMLResponse(html)
