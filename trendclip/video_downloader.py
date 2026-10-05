"""Background gameplay footage for the video synthesis step.

Two sources, tried in the order given by `BACKGROUND_SOURCES` (default: pexels, youtube):

* Method A - Pexels Video API: search "<game> gameplay", download the best MP4 file.
* Method B - No-Copyright YouTube channels via yt-dlp: pick a random upload whose title matches
  the game (from the channels in `NCG_CHANNELS`), and download a random N-second segment.
  If the channels have nothing for the game, fall back to a yt-dlp search for
  "<game> no copyright gameplay" restricted to results that say "no copyright".

Every download is saved under assets/backgrounds/ with a JSON sidecar recording where it came
from (credit / licence notes), and copied to assets/backgrounds/latest_gameplay.mp4.

CLI:  python -m trendclip.video_downloader "Minecraft" --source youtube --seconds 60
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field

from . import games
from .config import Settings, get_settings
from .models import utcnow
from .youtube_client import YouTubeAPIError, YouTubeClient

logger = logging.getLogger(__name__)

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
LATEST_FILENAME = "latest_gameplay.mp4"
LIBRARY_TTL_SECONDS = 24 * 3600
MAX_UPLOADS_PER_CHANNEL = 1000
MIN_PEXELS_HEIGHT = 720
INTRO_SKIP_SECONDS = 20

Source = Literal["pexels", "youtube"]
Orientation = Literal["landscape", "portrait"]
ProgressFn = Callable[[float | None, str], None]  # (fraction 0..1 or None, message)


class DownloadError(RuntimeError):
    pass


class BackgroundClip(BaseModel):
    path: str
    filename: str
    source: Source
    game: str
    query: str
    title: str
    source_url: str
    author: str
    author_url: str | None = None
    duration_seconds: float | None = None
    start_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    license_note: str
    downloaded_at: datetime = Field(default_factory=utcnow)


def _noop(_fraction: float | None, _message: str) -> None:
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "game"


def ffmpeg_path() -> str | None:
    system = shutil.which("ffmpeg")
    if system:
        return system
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - optional dependency, any failure means "not available"
        return None


_RESOLUTION_RE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_video(path: Path) -> dict:
    """Actual width/height/duration of a local file via `ffmpeg -i` (empty dict if unavailable)."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        return {}
    try:
        proc = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    out: dict = {}
    if m := _RESOLUTION_RE.search(proc.stderr):
        out["width"], out["height"] = int(m.group(1)), int(m.group(2))
    if m := _DURATION_RE.search(proc.stderr):
        h, mnt, s = m.groups()
        out["duration_seconds"] = round(int(h) * 3600 + int(mnt) * 60 + float(s), 2)
    return out


# --------------------------------------------------------------------------- Method A: Pexels


def _pick_pexels_file(video: dict, orientation: Orientation) -> dict | None:
    files = [
        f for f in video.get("video_files", [])
        if f.get("file_type") == "video/mp4" and f.get("link") and f.get("width") and f.get("height")
    ]
    if not files:
        return None
    want_portrait = orientation == "portrait"

    def rank(f: dict) -> tuple:
        w, h = f["width"], f["height"]
        long_side = max(w, h)
        # Prefer the requested orientation, then the largest file up to 4K.
        return ((h > w) == want_portrait, long_side <= 3840, min(long_side, 3840), w * h)

    best = max(files, key=rank)
    return best if min(best["width"], best["height"]) >= MIN_PEXELS_HEIGHT else None


def _stream_to_file(url: str, output_path: Path, timeout: float, progress: ProgressFn, label: str) -> None:
    tmp = output_path.with_suffix(output_path.suffix + ".part")
    try:
        with requests.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            done = 0
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)
                    done += len(chunk)
                    progress(done / total if total else None, f"{label}: {done / 1e6:.1f} MB")
        os.replace(tmp, output_path)
    finally:
        tmp.unlink(missing_ok=True)


def _trim(path: Path, seconds: int) -> None:
    """Cut a file to its first `seconds` in place (stream copy; starts on the first keyframe)."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise DownloadError("ffmpeg is required to trim clips (install ffmpeg or imageio-ffmpeg)")
    tmp = path.with_name(path.stem + ".trim.mp4")
    proc = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(path), "-t", str(seconds), "-c", "copy", str(tmp)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"ffmpeg trim failed: {proc.stderr.strip()[-200:]}")
    os.replace(tmp, path)


def _crop_to_portrait(path: Path, progress: ProgressFn = _noop) -> None:
    """Centre-crop a landscape file to 9:16 in place (re-encodes the video, copies the audio)."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise DownloadError("ffmpeg is required to crop clips to vertical (install ffmpeg or imageio-ffmpeg)")
    progress(None, "Cropping to vertical 9:16")
    tmp = path.with_name(path.stem + ".crop.mp4")
    proc = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(path),
         "-vf", "crop=trunc(ih*9/16/2)*2:ih,setsar=1", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(tmp)],
        capture_output=True, text=True, timeout=3600,
    )
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise DownloadError(f"ffmpeg crop failed: {proc.stderr.strip()[-200:]}")
    os.replace(tmp, path)


def _fetch_from_pexels(
    query: str,
    output_path: Path,
    api_key: str,
    orientation: Orientation = "landscape",
    timeout: float = 15.0,
    progress: ProgressFn = _noop,
    clip_seconds: int = 0,
) -> dict | None:
    """Download a random good Pexels result for `query`, trimmed to clip_seconds (0 = as is).

    Returns clip metadata, or None when nothing is HD and at least clip_seconds long.
    """
    progress(None, f"Searching Pexels for '{query}'")
    resp = requests.get(
        PEXELS_SEARCH_URL,
        headers={"Authorization": api_key},
        params={"query": query, "orientation": orientation, "per_page": 30},
        timeout=timeout,
    )
    if resp.status_code in (401, 403):
        raise DownloadError("Pexels rejected the API key (check PEXELS_API_KEY)")
    if resp.status_code == 429:
        raise DownloadError("Pexels rate limit reached; try again later")
    resp.raise_for_status()

    candidates = [
        (v, f)
        for v in resp.json().get("videos", [])
        if (v.get("duration") or 0) >= clip_seconds and (f := _pick_pexels_file(v, orientation))
    ]
    if not candidates:
        return None
    video, file = random.choice(candidates)
    _stream_to_file(file["link"], output_path, timeout * 4, progress, "Downloading from Pexels")
    if clip_seconds and (video.get("duration") or 0) > clip_seconds:
        progress(None, "Trimming clip")
        _trim(output_path, clip_seconds)
    user = video.get("user", {})
    return {
        "title": f"Pexels video {video['id']}",
        "source_url": video.get("url", ""),
        "author": user.get("name", "Unknown"),
        "author_url": user.get("url"),
        "duration_seconds": float(clip_seconds) if clip_seconds else video.get("duration"),
        "start_seconds": 0.0 if clip_seconds else None,
        "width": file["width"],
        "height": file["height"],
        "license_note": f"Pexels License (free to use). Credit: Video by {user.get('name', 'Unknown')} on Pexels.",
    }


def fetch_from_pexels(query: str, output_path: str, **kwargs) -> bool:
    """Method A. Saves the best-matching Pexels MP4 to output_path. True on success."""
    settings = kwargs.pop("settings", None) or get_settings()
    if not settings.pexels_enabled:
        logger.warning("PEXELS_API_KEY not set; skipping Pexels")
        return False
    try:
        return _fetch_from_pexels(
            query, Path(output_path), settings.pexels_api_key.get_secret_value(),
            timeout=settings.request_timeout, **kwargs,
        ) is not None
    except (requests.RequestException, DownloadError, OSError) as err:
        logger.warning("Pexels download failed: %s", err)
        return False


# --------------------------------------------------------------------------- Method B: yt-dlp


def _yt_dlp():
    try:
        import yt_dlp
    except ImportError as err:  # pragma: no cover - dependency is in requirements.txt
        raise DownloadError("yt-dlp is not installed (pip install yt-dlp)") from err
    return yt_dlp


def _random_start(duration: float | None, clip_seconds: int) -> float:
    if not duration or not clip_seconds or duration <= clip_seconds + INTRO_SKIP_SECONDS * 2:
        return 0.0
    return round(random.uniform(INTRO_SKIP_SECONDS, duration - clip_seconds - INTRO_SKIP_SECONDS), 1)


def _download_from_youtube(
    video_url: str,
    output_path: Path,
    clip_seconds: int = 60,
    start_seconds: float | None = None,
    max_height: int = 1080,
    progress: ProgressFn = _noop,
) -> dict:
    """Download a segment (or the whole video if clip_seconds=0) as MP4. Returns metadata."""
    yt_dlp = _yt_dlp()
    ffmpeg = ffmpeg_path()
    base = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20, "retries": 3}

    progress(None, "Reading video info")
    try:
        with yt_dlp.YoutubeDL(base) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except yt_dlp.utils.DownloadError as err:
        raise DownloadError(f"yt-dlp could not read {video_url}: {err}") from err

    duration = info.get("duration")
    if clip_seconds and duration and duration < clip_seconds:
        raise DownloadError(f"video is only {duration:.0f}s, shorter than the requested {clip_seconds}s")
    if clip_seconds and start_seconds is None:
        start_seconds = _random_start(duration, clip_seconds)

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            got = d.get("downloaded_bytes") or 0
            progress(got / total if total else None, f"Downloading: {got / 1e6:.1f} MB")
        elif d.get("status") == "finished":
            progress(None, "Processing video")

    # Cap the short side, so vertical uploads still come in 1080x1920 rather than 608x1080.
    portrait = (info.get("height") or 0) > (info.get("width") or 0) > 0
    side = "width" if portrait else "height"
    tmp_base = output_path.with_name(output_path.stem + ".dl")
    opts = {
        **base,
        "format": (
            f"bv*[ext=mp4][{side}<={max_height}]+ba[ext=m4a]/b[ext=mp4][{side}<={max_height}]"
            f"/bv*[{side}<={max_height}]+ba/b"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(tmp_base) + ".%(ext)s",
        "progress_hooks": [hook],
        "fragment_retries": 3,
        "overwrites": True,
    }
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    if clip_seconds:
        if not ffmpeg:
            raise DownloadError("ffmpeg is required to cut clips (install ffmpeg or imageio-ffmpeg)")
        end = start_seconds + clip_seconds
        opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [(start_seconds, end)])
        opts["force_keyframes_at_cuts"] = True

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([info.get("webpage_url") or video_url])
    except yt_dlp.utils.DownloadError as err:
        raise DownloadError(f"yt-dlp download failed: {err}") from err

    produced = sorted(output_path.parent.glob(tmp_base.name + ".*"), key=lambda p: p.stat().st_size, reverse=True)
    produced = [p for p in produced if p.suffix == ".mp4"] or produced
    if not produced:
        raise DownloadError("yt-dlp finished but produced no file")
    os.replace(produced[0], output_path)
    for leftover in output_path.parent.glob(tmp_base.name + ".*"):
        leftover.unlink(missing_ok=True)

    channel = info.get("channel") or info.get("uploader") or "Unknown"
    return {
        "title": info.get("title", ""),
        "source_url": info.get("webpage_url") or video_url,
        "author": channel,
        "author_url": info.get("channel_url") or info.get("uploader_url"),
        "duration_seconds": float(clip_seconds) if clip_seconds else duration,
        "start_seconds": start_seconds if clip_seconds else None,
        "width": info.get("width"),
        "height": info.get("height"),
        "license_note": (
            f"No-copyright gameplay published by {channel}. Follow the channel's stated usage terms "
            "(usually: credit the channel in your description)."
        ),
    }


def download_from_youtube(video_url: str, output_path: str, **kwargs) -> bool:
    """Method B. Saves a segment (default 60 s) of a YouTube video to output_path. True on success."""
    try:
        _download_from_youtube(video_url, Path(output_path), **kwargs)
        return True
    except (DownloadError, OSError) as err:
        logger.warning("YouTube download failed: %s", err)
        return False


# --------------------------------------------------------------------------- No-Copyright library


class LibraryVideo(BaseModel):
    video_id: str
    title: str
    channel_id: str
    channel_title: str
    games: list[str] = Field(default_factory=list)
    duration: float | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def vertical(self) -> bool:
        return bool(re.search(r"\b(vertical|9\s*[:x/]\s*16|shorts?)\b", self.title, re.IGNORECASE))


class NoCopyrightLibrary:
    """Upload lists of the configured No-Copyright channels, cached on disk for a day."""

    def __init__(self, settings: Settings, client_factory: Callable[[], YouTubeClient] | None = None):
        self.settings = settings
        self.cache_path = settings.assets_dir / "cache" / "ncg_library.json"
        self._client_factory = client_factory or (
            lambda: YouTubeClient(settings.youtube_api_key.get_secret_value(), settings.request_timeout)
        )
        self._lock = threading.Lock()
        self._videos: list[LibraryVideo] | None = None
        self.channels: list[dict] = []

    def _load_cache(self) -> bool:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        fresh = time.time() - data.get("fetched_at", 0) < LIBRARY_TTL_SECONDS
        if not fresh or data.get("refs") != self.settings.ncg_channels:
            return False
        self.channels = data["channels"]
        self._videos = [LibraryVideo(**v) for v in data["videos"]]
        return True

    def _refresh(self) -> None:
        client = self._client_factory()
        channels: list[dict] = []
        seen_ids: set[str] = set()
        # Explicit handles / ids first, so plain names that duplicate them cost nothing.
        refs = sorted(self.settings.ncg_channels, key=lambda r: not r.startswith(("@", "UC")))
        for ref in refs:
            if not ref.startswith(("@", "UC")) and any(
                games.normalize(ref) == games.normalize(c["title"]) for c in channels
            ):
                continue
            try:
                info = client.resolve_channel(ref)
            except YouTubeAPIError as err:
                logger.warning("Could not resolve channel %s: %s", ref, err)
                continue
            if info and info["id"] not in seen_ids:
                seen_ids.add(info["id"])
                channels.append(info)

        videos: list[LibraryVideo] = []
        for ch in channels:
            for item in client.list_uploads(ch["uploads"], MAX_UPLOADS_PER_CHANNEL):
                videos.append(
                    LibraryVideo(
                        video_id=item["video_id"],
                        title=item["title"],
                        channel_id=ch["id"],
                        channel_title=ch["title"],
                        games=games.match_title(item["title"]),
                    )
                )
        logger.info("No-Copyright library: %d videos from %d channels", len(videos), len(channels))
        self.channels, self._videos = channels, videos
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(
                {
                    "fetched_at": time.time(),
                    "refs": self.settings.ncg_channels,
                    "channels": channels,
                    "videos": [v.model_dump() for v in videos],
                }
            ),
            encoding="utf-8",
        )

    def videos(self, refresh: bool = False) -> list[LibraryVideo]:
        with self._lock:
            if refresh or (self._videos is None and not self._load_cache()):
                self._refresh()
            return list(self._videos or [])

    def find(self, game_name: str, orientation: Orientation = "landscape") -> list[LibraryVideo]:
        """Best matches for a game: exact game, else same franchise, else title text match."""
        pool = self.videos()
        exact = [v for v in pool if game_name in v.games]
        related = games.related_games(game_name)
        family = [v for v in pool if related.intersection(v.games)]
        needle = games.normalize(game_name)
        text = [v for v in pool if needle.strip() and needle in games.normalize(v.title)]
        matches = exact or family or text
        want_vertical = orientation == "portrait"
        preferred = [v for v in matches if v.vertical == want_vertical]
        return preferred or matches

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.videos():
            for g in v.games:
                out[g] = out.get(g, 0) + 1
        return out


def search_no_copyright(game_name: str, limit: int = 15) -> list[LibraryVideo]:
    """Fallback without quota: yt-dlp search, keeping only results that say 'no copyright'."""
    yt_dlp = _yt_dlp()
    query = f"ytsearch{limit}:{game_name} no copyright gameplay"
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
            result = ydl.extract_info(query, download=False)
    except yt_dlp.utils.DownloadError as err:
        logger.warning("yt-dlp search failed: %s", err)
        return []
    needle = games.normalize(game_name)
    related = games.related_games(game_name) | {game_name}
    out = []
    for entry in result.get("entries") or []:
        title = entry.get("title") or ""
        norm = games.normalize(title)
        if " no copyright " not in norm and " copyright free " not in norm:
            continue
        matched = games.match_title(title)
        if related.intersection(matched) or needle in norm:
            out.append(
                LibraryVideo(
                    video_id=entry["id"],
                    title=title,
                    channel_id=entry.get("channel_id") or "",
                    channel_title=entry.get("channel") or entry.get("uploader") or "Unknown",
                    games=matched,
                )
            )
    return out


# --------------------------------------------------------------------------- orchestration


def _used_video_ids(backgrounds_dir: Path) -> set[str]:
    used = set()
    for sidecar in backgrounds_dir.glob("*.json"):
        try:
            url = json.loads(sidecar.read_text(encoding="utf-8")).get("source_url", "")
        except (OSError, ValueError):
            continue
        if match := re.search(r"v=([\w-]{11})", url):
            used.add(match.group(1))
    return used


def _pick_random(candidates: list[LibraryVideo], used: set[str], k: int = 3) -> list[LibraryVideo]:
    """Up to k random candidates, preferring videos not downloaded before."""
    fresh = [c for c in candidates if c.video_id not in used]
    pool = fresh or candidates
    return random.sample(pool, min(k, len(pool)))


_library_cache: dict[tuple, NoCopyrightLibrary] = {}
_library_lock = threading.Lock()


def get_library(settings: Settings) -> NoCopyrightLibrary:
    key = (tuple(settings.ncg_channels), str(settings.assets_dir))
    with _library_lock:
        if key not in _library_cache:
            _library_cache[key] = NoCopyrightLibrary(settings)
        return _library_cache[key]


def download_background(
    game_name: str,
    settings: Settings | None = None,
    sources: list[Source] | None = None,
    clip_seconds: int | None = None,
    orientation: Orientation | None = None,
    progress: ProgressFn = _noop,
) -> BackgroundClip:
    """Try each source in order; save the clip + sidecar and refresh latest_gameplay.mp4."""
    settings = settings or get_settings()
    sources = sources or list(settings.background_sources)
    clip_seconds = settings.background_clip_seconds if clip_seconds is None else clip_seconds
    orientation = orientation or settings.background_orientation
    out_dir = settings.backgrounds_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    errors: list[str] = []

    for source in sources:
        target = out_dir / f"{_slug(game_name)}_{source}_{stamp}.mp4"
        query = f"{game_name} gameplay"
        meta: dict | None = None
        try:
            if source == "pexels":
                if not settings.pexels_enabled:
                    errors.append("Pexels skipped: PEXELS_API_KEY is not set")
                    continue
                meta = _fetch_from_pexels(
                    query, target, settings.pexels_api_key.get_secret_value(),
                    orientation, settings.request_timeout, progress, clip_seconds,
                )
                if meta is None:
                    errors.append(f"Pexels: no HD results of {clip_seconds}s+ for '{query}'")
            else:
                progress(None, "Looking up No-Copyright channel videos")
                try:
                    candidates = get_library(settings).find(game_name, orientation)
                except YouTubeAPIError as err:
                    errors.append(f"No-Copyright channels unavailable: {err}")
                    candidates = []
                query = f"No-Copyright channels: {game_name}"
                if not candidates:
                    progress(None, f"Searching YouTube for '{game_name} no copyright gameplay'")
                    candidates = search_no_copyright(game_name)
                    query = f"ytsearch: {game_name} no copyright gameplay"
                if not candidates:
                    errors.append(f"YouTube: no no-copyright videos found for '{game_name}'")
                    continue
                for pick in _pick_random(candidates, _used_video_ids(out_dir)):
                    progress(None, f"Downloading '{pick.title}' ({pick.channel_title})")
                    try:
                        meta = _download_from_youtube(pick.url, target, clip_seconds, progress=progress)
                        break
                    except DownloadError as err:
                        errors.append(f"YouTube: {pick.url} failed ({err})")
        except (requests.RequestException, DownloadError, OSError) as err:
            errors.append(f"{source}: {err}")
            meta = None

        if meta:
            try:
                return _save_clip(target, meta, source, game_name, query, orientation, progress)
            except (DownloadError, OSError, subprocess.SubprocessError) as err:
                errors.append(f"{source}: {err}")

    raise DownloadError("; ".join(errors) or "No download source available")


def _save_clip(target: Path, meta: dict, source: Source, game: str, query: str, orientation: Orientation,
               progress: ProgressFn) -> BackgroundClip:
    """Crop to 9:16 if asked, write the sidecar and refresh latest_gameplay.mp4."""
    size = probe_video(target)
    if orientation == "portrait" and (size.get("width") or 0) > (size.get("height") or 0):
        # Few no-copyright uploads are vertical, so a landscape pick is cropped instead.
        try:
            _crop_to_portrait(target, progress)
        except (DownloadError, OSError, subprocess.SubprocessError):
            target.unlink(missing_ok=True)
            raise
        size = probe_video(target)
    meta.update(size)
    clip = BackgroundClip(path=str(target), filename=target.name, source=source, game=game, query=query, **meta)
    out_dir = target.parent
    target.with_suffix(".json").write_text(clip.model_dump_json(indent=2), encoding="utf-8")
    shutil.copyfile(target, out_dir / LATEST_FILENAME)
    (out_dir / LATEST_FILENAME).with_suffix(".json").write_text(clip.model_dump_json(indent=2), encoding="utf-8")
    progress(1.0, "Done")
    return clip


# --------------------------------------------------------------------------- from a pasted link

_VIDEO_LINK_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})"
)
_CHANNEL_LINK_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[\w.%-]+|channel/UC[\w-]{22}|c/[\w.%-]+|user/[\w.%-]+)"
)
CHANNEL_SCAN_LIMIT = 300


def parse_youtube_link(url: str) -> tuple[Literal["video", "channel"], str]:
    """('video', watch URL) or ('channel', channel URL); ValueError for anything else."""
    url = url.strip()
    if m := _VIDEO_LINK_RE.match(url):
        return "video", f"https://www.youtube.com/watch?v={m.group(1)}"
    if m := _CHANNEL_LINK_RE.match(url):
        return "channel", f"https://www.youtube.com/{m.group(1)}"
    raise ValueError("Paste a YouTube video link (youtube.com/watch?v=…, youtu.be/…, /shorts/…) "
                     "or a channel link (youtube.com/@name)")


def channel_videos(channel_url: str, limit: int = CHANNEL_SCAN_LIMIT) -> list[LibraryVideo]:
    """Latest uploads of a channel via yt-dlp (no API quota). Shorts and lives are left out."""
    yt_dlp = _yt_dlp()
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "playlistend": limit,
            "socket_timeout": 20}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.extract_info(f"{channel_url}/videos", download=False)
    except yt_dlp.utils.DownloadError as err:
        raise DownloadError(f"Could not read the channel {channel_url}: {err}") from err
    channel = result.get("channel") or result.get("uploader") or result.get("title") or "Unknown"
    out = []
    for e in result.get("entries") or []:
        if not e or not re.fullmatch(r"[\w-]{11}", e.get("id") or ""):
            continue
        title = e.get("title") or ""
        out.append(LibraryVideo(video_id=e["id"], title=title, channel_id=result.get("channel_id") or "",
                                channel_title=channel, games=games.match_title(title),
                                duration=e.get("duration")))
    return out


def _filter_channel(videos: list[LibraryVideo], game: str, clip_seconds: int,
                    orientation: Orientation) -> list[LibraryVideo]:
    """Long enough videos, of the game if one was given, vertical ones first in portrait mode."""
    long_enough = [v for v in videos if not v.duration or not clip_seconds or v.duration >= clip_seconds + 10]
    pool = long_enough or videos
    if game.strip():
        related = games.related_games(game) | {game}
        needle = games.normalize(game)
        of_game = [v for v in pool if related.intersection(v.games) or needle.strip() in games.normalize(v.title)]
        if not of_game:
            raise DownloadError(f"No videos of '{game}' among this channel's latest {len(videos)} uploads; "
                                "leave the game empty to take any video")
        pool = of_game
    want_vertical = orientation == "portrait"
    return [v for v in pool if v.vertical == want_vertical] or pool


def download_from_link(
    url: str,
    settings: Settings | None = None,
    game: str = "",
    clip_seconds: int | None = None,
    orientation: Orientation | None = None,
    progress: ProgressFn = _noop,
) -> BackgroundClip:
    """A clip from a pasted YouTube video, or from a random video of a pasted channel."""
    settings = settings or get_settings()
    clip_seconds = settings.background_clip_seconds if clip_seconds is None else clip_seconds
    orientation = orientation or settings.background_orientation
    kind, link = parse_youtube_link(url)
    out_dir = settings.backgrounds_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if kind == "video":
        picks = [link]
    else:
        progress(None, "Reading the channel's videos")
        videos = channel_videos(link)
        if not videos:
            raise DownloadError(f"No videos found on {link}")
        picks = [v.url for v in _pick_random(_filter_channel(videos, game, clip_seconds, orientation),
                                             _used_video_ids(out_dir))]

    errors = []
    tmp = out_dir / f"link_{uuid.uuid4().hex[:8]}.mp4"
    for pick in picks:
        progress(None, f"Downloading {pick}")
        try:
            meta = _download_from_youtube(pick, tmp, clip_seconds, progress=progress)
            break
        except DownloadError as err:
            errors.append(f"{pick} failed ({err})")
    else:
        raise DownloadError("; ".join(errors))

    name = game.strip() or next(iter(games.match_title(meta.get("title", ""))), "") or "Gameplay"
    target = out_dir / f"{_slug(name)}_youtube_{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    os.replace(tmp, target)
    meta["license_note"] = (f"From a link you pasted ({meta.get('author', 'unknown channel')}). Make sure you "
                            "may reuse this footage and credit the channel in your description.")
    return _save_clip(target, meta, "youtube", name, f"link: {url.strip()}", orientation, progress)


def get_background_video(game_name: str, **kwargs) -> str:
    """Pexels first, yt-dlp fallback (per BACKGROUND_SOURCES). Returns assets/backgrounds/latest_gameplay.mp4."""
    clip = download_background(game_name, **kwargs)
    return str(Path(clip.path).with_name(LATEST_FILENAME))


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bg-download")


def get_background_video_async(game_name: str, **kwargs) -> Future:
    """Non-blocking variant: returns a Future resolving to the latest_gameplay.mp4 path."""
    return _executor.submit(get_background_video, game_name, **kwargs)


def list_backgrounds(settings: Settings) -> list[BackgroundClip]:
    out = []
    for sidecar in settings.backgrounds_dir.glob("*.json"):
        if sidecar.stem == Path(LATEST_FILENAME).stem or not sidecar.with_suffix(".mp4").exists():
            continue
        try:
            out.append(BackgroundClip.model_validate_json(sidecar.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda c: c.downloaded_at, reverse=True)


def delete_background(settings: Settings, filename: str) -> bool:
    """Remove a downloaded clip and its sidecar. Only plain clip filenames inside the backgrounds dir."""
    if Path(filename).name != filename or not filename.endswith(".mp4") or filename == LATEST_FILENAME:
        raise ValueError(f"Not a downloaded clip: {filename}")
    target = settings.backgrounds_dir / filename
    if not target.exists():
        return False
    target.unlink()
    target.with_suffix(".json").unlink(missing_ok=True)

    latest_meta = settings.backgrounds_dir / Path(LATEST_FILENAME).with_suffix(".json")
    try:
        points_here = json.loads(latest_meta.read_text(encoding="utf-8")).get("filename") == filename
    except (OSError, ValueError):
        points_here = False
    if points_here:
        remaining = list_backgrounds(settings)
        latest = settings.backgrounds_dir / LATEST_FILENAME
        if remaining:
            shutil.copyfile(remaining[0].path, latest)
            latest_meta.write_text(remaining[0].model_dump_json(indent=2), encoding="utf-8")
        else:
            latest.unlink(missing_ok=True)
            latest_meta.unlink(missing_ok=True)
    return True


# --------------------------------------------------------------------------- job manager


class DownloadJob(BaseModel):
    id: str
    game: str
    sources: list[Source]
    clip_seconds: int
    orientation: Orientation
    url: str | None = None  # pasted YouTube video / channel link instead of a game search
    status: Literal["queued", "running", "done", "error"] = "queued"
    progress: float | None = None
    message: str = "Queued"
    clip: BackgroundClip | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class DownloadManager:
    """Runs downloads on worker threads; jobs are polled by id."""

    def __init__(self, max_workers: int = 2, max_jobs: int = 50):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bg-job")
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.Lock()
        self._max_jobs = max_jobs

    def submit(
        self,
        settings: Settings,
        game: str,
        sources: list[Source],
        clip_seconds: int,
        orientation: Orientation,
        url: str | None = None,
    ) -> DownloadJob:
        job = DownloadJob(
            id=uuid.uuid4().hex[:12], game=game, sources=sources,
            clip_seconds=clip_seconds, orientation=orientation, url=url,
        )
        with self._lock:
            self._jobs[job.id] = job
            for old in sorted(self._jobs.values(), key=lambda j: j.created_at)[: -self._max_jobs]:
                if old.status in ("done", "error"):
                    self._jobs.pop(old.id, None)
        self._executor.submit(self._run, job.id, settings)
        return job

    def _update(self, job_id: str, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                for k, v in fields.items():
                    setattr(job, k, v)

    def _run(self, job_id: str, settings: Settings) -> None:
        job = self.get(job_id)
        if job is None:
            return
        self._update(job_id, status="running", message="Starting")
        progress = lambda frac, msg: self._update(job_id, progress=frac, message=msg)  # noqa: E731
        try:
            if job.url:
                clip = download_from_link(job.url, settings=settings, game=job.game, clip_seconds=job.clip_seconds,
                                          orientation=job.orientation, progress=progress)
            else:
                clip = download_background(
                    job.game, settings=settings, sources=job.sources, clip_seconds=job.clip_seconds,
                    orientation=job.orientation, progress=progress,
                )
            self._update(job_id, status="done", progress=1.0, message="Done", clip=clip)
        except Exception as err:  # noqa: BLE001 - surface any failure to the UI instead of losing it
            logger.exception("Background download failed for %s", job.game)
            self._update(job_id, status="error", message="Failed", error=str(err))

    def get(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list(self) -> list[DownloadJob]:
        with self._lock:
            return [j.model_copy(deep=True) for j in sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download background gameplay footage for a game")
    parser.add_argument("game", help='Game name, e.g. "Minecraft" or "GTA V / Online"')
    parser.add_argument("--source", choices=["auto", "pexels", "youtube"], default="auto")
    parser.add_argument("--seconds", type=int, help="Clip length (0 = whole video)")
    parser.add_argument("--orientation", choices=["landscape", "portrait"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    sources = None if args.source == "auto" else [args.source]
    try:
        path = get_background_video(
            args.game, sources=sources, clip_seconds=args.seconds, orientation=args.orientation,
            progress=lambda f, m: print(f"  {m}" + (f" ({f:.0%})" if f is not None else ""), file=sys.stderr),
        )
    except DownloadError as err:
        print(f"Download failed: {err}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
