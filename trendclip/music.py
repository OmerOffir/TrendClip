"""Background music for Shorts from free-to-use music channels (NoCopyrightSounds, Audio Library,
Chillhop). Upload lists are cached for a day; picked tracks are downloaded once (audio only) to
assets/music/<video_id>.m4a with a .json credit sidecar."""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from .config import Settings
from .youtube_client import YouTubeAPIError, YouTubeClient

logger = logging.getLogger(__name__)

LIBRARY_TTL_SECONDS = 24 * 3600
MAX_UPLOADS_PER_CHANNEL = 1000
MAX_TRACK_SECONDS = 8 * 60
MIN_TRACK_SECONDS = 45
# Uploads that are not a single song (mixes, streams, playlists, shorts, announcements).
NOT_A_TRACK = re.compile(
    r"\b(mix|mixtape|compilation|playlist|radio|live|stream|24/7|hours?|1h|2h|full album|album|essentials|"
    r"beats to|best of|top \d+|#shorts?|shorts|trailer|teaser|announcement|q&a|vlog|behind the scenes|"
    r"documentary|tutorial|reaction)\b",
    re.IGNORECASE,
)
CHANNEL_MOODS = {
    "nocopyrightsounds": "energetic EDM",
    "chillhop": "chill lo-fi",
    "audio library": "mixed genres",
}


class MusicError(RuntimeError):
    pass


class MusicTrack(BaseModel):
    video_id: str
    title: str
    channel_id: str
    channel_title: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


class DownloadedTrack(BaseModel):
    video_id: str  # YouTube id, or "local:<folder>/<file>" for the mood folders
    title: str
    channel_title: str
    url: str
    filename: str  # relative to assets/music (YouTube) or music/ (local)
    duration_seconds: float | None = None
    credit: str
    local: bool = False
    mood: str = ""  # story mood it was picked for
    fallback: bool = False  # the mood folder was empty, so a YouTube channel track was used


# --------------------------------------------------------------------------- story moods

MOODS = ("funny_quirky", "dramatic_suspense", "chill_lofi")
MOOD_FOLDERS = {"funny_quirky": "funny", "dramatic_suspense": "dramatic", "chill_lofi": "chill"}
MOOD_LABELS = {"funny_quirky": "Funny / quirky", "dramatic_suspense": "Dramatic / suspense", "chill_lofi": "Chill lo-fi"}
# Which free music channels to fall back on while a mood folder is empty.
MOOD_CHANNELS = {"funny_quirky": ("audio library",), "dramatic_suspense": ("nocopyrightsounds", "audio library"),
                 "chill_lofi": ("chillhop",)}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"}
VOLUME_RANGE = (0.12, 0.15)  # 12-15%: always clearly under the voice
LOCAL_ID = re.compile(r"^local:(funny|dramatic|chill)/([^/\\]{1,120})$")

_MOOD_WORDS = {
    "dramatic_suspense": r"\b(suddenly|scream\w*|dark|blood|police|missing|vanish\w*|secret|creep\w*|haunt\w*|"
                         r"ghost|shadow|footsteps|knock\w*|terrif\w*|betray\w*|lied|truth|never came back|"
                         r"twist|mystery|strange|disappear\w*|locked|warning)\b",
    "funny_quirky": r"\b(awkward|embarrass\w*|accidentally|weird|ridiculous|hilarious|laugh\w*|oops|"
                    r"cringe|prank\w*|wrong (person|house|room)|my (mom|dad|grandma)|goat|chicken|pants)\b",
}


def normalize_mood(value: str | None) -> str | None:
    value = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if value in MOODS:
        return value
    for mood_id, folder in MOOD_FOLDERS.items():
        if value and (value.startswith(folder) or folder in value):
            return mood_id
    return None


def guess_mood(text: str) -> str:
    """Keyword guess for scripts Gemini didn't classify (typed by hand)."""
    scores = {m: len(re.findall(p, text, re.IGNORECASE)) for m, p in _MOOD_WORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "chill_lofi"


def clamp_volume(volume: float) -> float:
    return min(max(volume, VOLUME_RANGE[0]), VOLUME_RANGE[1])


def mood_dir(settings: Settings, mood_id: str) -> Path:
    return settings.music_library_dir / MOOD_FOLDERS[mood_id]


def local_tracks(settings: Settings, mood_id: str) -> list[Path]:
    folder = mood_dir(settings, mood_id)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS and not p.name.startswith("."))


def mood_counts(settings: Settings) -> dict[str, dict]:
    return {m: {"folder": f"{settings.music_library_dir.name}/{MOOD_FOLDERS[m]}", "label": MOOD_LABELS[m],
                "tracks": len(local_tracks(settings, m))} for m in MOODS}


def _local_track(settings: Settings, path: Path, mood_id: str) -> DownloadedTrack:
    from .video_downloader import probe_video

    folder = path.parent.name
    # Optional credit next to the file: "song.mp3" + "song.txt" (e.g. for Creative Commons tracks).
    credit_file = path.with_suffix(".txt")
    credit = credit_file.read_text(encoding="utf-8").strip() if credit_file.is_file() else ""
    title = re.sub(r"[_]+", " ", path.stem).strip()
    return DownloadedTrack(
        video_id=f"local:{folder}/{path.name}", title=title, channel_title=f"music/{folder}", url="",
        filename=f"{folder}/{path.name}", duration_seconds=probe_video(path).get("duration_seconds"),
        credit=credit, local=True, mood=mood_id,
    )


def track_path(settings: Settings, track: DownloadedTrack) -> Path:
    return (settings.music_library_dir if track.local else settings.music_dir) / track.filename


def pick_by_mood(settings: Settings, mood_id: str, exclude: set[str] | None = None) -> DownloadedTrack:
    """Random track from music/<funny|dramatic|chill>; a matching YouTube channel while the folder is empty."""
    mood_id = normalize_mood(mood_id) or "chill_lofi"
    files = local_tracks(settings, mood_id)
    if files:
        fresh = [p for p in files if f"local:{p.parent.name}/{p.name}" not in (exclude or set())]
        return _local_track(settings, random.choice(fresh or files), mood_id)
    library = get_library(settings)
    wanted = MOOD_CHANNELS[mood_id]
    channel_ids = [c["id"] for c in library.channels or []
                   if any(w in c["title"].lower() for w in wanted)] if library.tracks() else []
    source = channel_ids[0] if channel_ids else "random"
    logger.info("music/%s is empty; using a YouTube track (%s)", MOOD_FOLDERS[mood_id], source)
    track = pick_track(settings, source, exclude)
    return track.model_copy(update={"mood": mood_id, "fallback": True})


def pick_local(settings: Settings, exclude: set[str] | None = None) -> DownloadedTrack:
    """Random track from any of the music/ mood folders."""
    files = [(p, m) for m in MOODS for p in local_tracks(settings, m)]
    if not files:
        raise MusicError(f"No music files in {settings.music_library_dir.name}/funny, /dramatic or /chill yet")
    fresh = [(p, m) for p, m in files if f"local:{p.parent.name}/{p.name}" not in (exclude or set())]
    path, mood_id = random.choice(fresh or files)
    return _local_track(settings, path, mood_id)


def pick(settings: Settings, source: str, mood_id: str | None = None,
         exclude: set[str] | None = None) -> DownloadedTrack:
    """"mood" = the folder for mood_id, "mine" = any music/ folder, else a channel id or "random"."""
    if source == "mood":
        return pick_by_mood(settings, mood_id or "chill_lofi", exclude)
    if source == "mine":
        return pick_local(settings, exclude)
    return pick_track(settings, source, exclude)


def mood(channel_title: str) -> str:
    low = channel_title.lower()
    return next((m for key, m in CHANNEL_MOODS.items() if key in low), "")


def credit_for(title: str, channel_title: str, url: str) -> str:
    low = channel_title.lower()
    if "nocopyrightsounds" in low:
        return f"Music: {title}\nMusic provided by NoCopyrightSounds\nWatch: {url}"
    return f"Music: {title} - provided by {channel_title}\n{url}"


class MusicLibrary:
    def __init__(self, settings: Settings, client_factory: Callable[[], YouTubeClient] | None = None):
        self.settings = settings
        self.cache_path = settings.assets_dir / "cache" / "music_library.json"
        self._client_factory = client_factory or (
            lambda: YouTubeClient(settings.youtube_api_key.get_secret_value(), settings.request_timeout)
        )
        self._lock = threading.Lock()
        self._tracks: list[MusicTrack] | None = None
        self.channels: list[dict] = []

    def _load_cache(self) -> bool:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if time.time() - data.get("fetched_at", 0) > LIBRARY_TTL_SECONDS or data.get("refs") != self.settings.music_channels:
            return False
        self.channels = data["channels"]
        self._tracks = [MusicTrack(**t) for t in data["tracks"]]
        return True

    def _refresh(self) -> None:
        client = self._client_factory()
        channels, tracks = [], []
        for ref in self.settings.music_channels:
            try:
                info = client.resolve_channel(ref)
            except YouTubeAPIError as err:
                logger.warning("Could not resolve music channel %s: %s", ref, err)
                continue
            if not info or any(c["id"] == info["id"] for c in channels):
                continue
            channels.append(info)
            for item in client.list_uploads(info["uploads"], MAX_UPLOADS_PER_CHANNEL):
                if not NOT_A_TRACK.search(item["title"]):
                    tracks.append(MusicTrack(video_id=item["video_id"], title=item["title"],
                                             channel_id=info["id"], channel_title=info["title"]))
        if not channels:
            raise MusicError("None of the music channels could be found (check MUSIC_CHANNELS)")
        self.channels, self._tracks = channels, tracks
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps({
            "fetched_at": time.time(),
            "refs": self.settings.music_channels,
            "channels": channels,
            "tracks": [t.model_dump() for t in tracks],
        }), encoding="utf-8")
        logger.info("Music library: %d tracks from %d channels", len(tracks), len(channels))

    def tracks(self, refresh: bool = False) -> list[MusicTrack]:
        with self._lock:
            if refresh or (self._tracks is None and not self._load_cache()):
                self._refresh()
            return list(self._tracks or [])

    def sources(self) -> list[dict]:
        tracks = self.tracks()
        return [
            {"id": c["id"], "title": c["title"], "mood": mood(c["title"]),
             "tracks": sum(1 for t in tracks if t.channel_id == c["id"])}
            for c in self.channels
        ]

    def candidates(self, source: str = "random") -> list[MusicTrack]:
        tracks = self.tracks()
        if source in ("", "random", "any"):
            return tracks
        return [t for t in tracks if t.channel_id == source]


_libraries: dict[tuple, MusicLibrary] = {}
_libraries_lock = threading.Lock()


def get_library(settings: Settings) -> MusicLibrary:
    key = (tuple(settings.music_channels), str(settings.assets_dir))
    with _libraries_lock:
        if key not in _libraries:
            _libraries[key] = MusicLibrary(settings)
        return _libraries[key]


def _sidecar(settings: Settings, video_id: str) -> Path:
    return settings.music_dir / f"{video_id}.json"


def downloaded(settings: Settings, video_id: str) -> DownloadedTrack | None:
    """A YouTube track already on disk, or a file in the mood folders ("local:chill/song.mp3")."""
    if m := LOCAL_ID.match(video_id or ""):
        path = settings.music_library_dir / m.group(1) / m.group(2)
        mood_id = next(k for k, v in MOOD_FOLDERS.items() if v == m.group(1))
        return _local_track(settings, path, mood_id) if path.is_file() else None
    if not re.fullmatch(r"[\w-]{11}", video_id or ""):
        return None
    try:
        track = DownloadedTrack.model_validate_json(_sidecar(settings, video_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return track if (settings.music_dir / track.filename).is_file() else None


def download_track(settings: Settings, track: MusicTrack) -> DownloadedTrack:
    """Audio-only download (m4a preferred), reused if already on disk."""
    if cached := downloaded(settings, track.video_id):
        return cached
    from .video_downloader import _yt_dlp, ffmpeg_path

    yt_dlp = _yt_dlp()
    out_dir = settings.music_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    base = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "socket_timeout": 20, "retries": 3}
    try:
        with yt_dlp.YoutubeDL(base) as ydl:
            info = ydl.extract_info(track.url, download=False)
    except yt_dlp.utils.DownloadError as err:
        raise MusicError(f"Could not read {track.url}: {err}") from err
    duration = info.get("duration") or 0
    if duration and not MIN_TRACK_SECONDS <= duration <= MAX_TRACK_SECONDS:
        raise MusicError(f"'{track.title}' is {duration // 60}m{duration % 60:02d}s, not a single track")

    opts = {
        **base,
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(out_dir / f"{track.video_id}.%(ext)s"),
        "overwrites": True,
    }
    if ffmpeg := ffmpeg_path():
        opts["ffmpeg_location"] = ffmpeg
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([track.url])
    except yt_dlp.utils.DownloadError as err:
        raise MusicError(f"Download failed for '{track.title}': {err}") from err
    files = [p for p in out_dir.glob(f"{track.video_id}.*") if p.suffix not in (".json", ".part")]
    if not files:
        raise MusicError(f"Download produced no file for '{track.title}'")

    result = DownloadedTrack(
        video_id=track.video_id, title=info.get("title") or track.title, channel_title=track.channel_title,
        url=track.url, filename=files[0].name, duration_seconds=duration or None,
        credit=credit_for(info.get("title") or track.title, track.channel_title, track.url),
    )
    _sidecar(settings, track.video_id).write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def pick_track(settings: Settings, source: str = "random", exclude: set[str] | None = None,
               attempts: int = 4) -> DownloadedTrack:
    """Random track from a channel id (or 'random' for any channel), downloaded and ready to mix."""
    candidates = [t for t in get_library(settings).candidates(source) if t.video_id not in (exclude or set())]
    if not candidates:
        raise MusicError("No tracks found for that music source")
    errors = []
    for track in random.sample(candidates, min(attempts, len(candidates))):
        try:
            return download_track(settings, track)
        except MusicError as err:
            errors.append(str(err))
    raise MusicError("; ".join(errors))
