"""Create tab backend: Gemini script → edge-tts voiceover → karaoke Short, run as background jobs."""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from . import music, popups, script_writer, stickers, video_assembler, voiceover
from .config import Settings
from .models import utcnow
from .popups import Popup
from .stickers import Reaction
from .video_downloader import LATEST_FILENAME, BackgroundClip

logger = logging.getLogger(__name__)

FINAL_SHORT = "final_short.mp4"
ProgressFn = Callable[[float | None, str], None]


class ShortVideo(BaseModel):
    filename: str
    game: str
    title: str
    description: str
    hashtags: list[str]
    script: str
    voice: str
    duration_seconds: float | None = None
    background: str
    background_title: str = ""
    credit: str = ""
    music_title: str = ""
    music_url: str = ""
    title_card: str = ""
    popups: list[str] = Field(default_factory=list)  # words that got a pop-up image
    stickers: list[str] = Field(default_factory=list)  # files from stickers/ that were used
    end_card: str = ""
    # Multi-part stories: parts of one story share series_id; the Upload tab links them together.
    series_id: str | None = None
    story_name: str = ""
    part: int | None = None
    parts_total: int | None = None
    pinned_comment: str = ""
    channel_handle: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    # Upload tab: marked ready in Create; per-platform texts; where it was published.
    ready: bool = False
    ready_at: datetime | None = None
    texts: dict[str, Any] = Field(default_factory=dict)  # {"youtube": {...}, "tiktok": {...}, "instagram": {...}}
    uploads: dict[str, Any] = Field(default_factory=dict)  # {"youtube": {"video_id", "url", "privacy", ...}}
    posted: dict[str, bool] = Field(default_factory=dict)  # manual "I posted it" for TikTok / Instagram


class RenderRequest(BaseModel):
    clip: str = Field(min_length=1)
    game: str = Field(min_length=1, max_length=80)
    script: str = Field(min_length=3, max_length=3000)
    title: str = Field("", max_length=150)
    description: str = Field("", max_length=4000)
    hashtags: list[str] = Field(default_factory=list)
    voice: str | None = None
    rate: str | None = Field(None, pattern=r"^[+-]\d{1,3}%$")
    highlight: str = "yellow"
    fit: Literal["crop", "blur"] = "crop"
    max_words: int = Field(3, ge=1, le=6)
    # "none", "random" (any music channel) or a music channel id; music_track pins a previewed track.
    music_source: str = Field("none", max_length=40)
    music_track: str | None = Field(None, pattern=r"^[\w-]{11}$")
    music_volume: float | None = Field(None, ge=0, le=1)
    title_card: str = Field("", max_length=60)
    popups: list[Popup] = Field(default_factory=list, max_length=10)
    end_card: str = Field("", max_length=80)
    reactions: list[Reaction] = Field(default_factory=list, max_length=6)
    stickers: bool = True  # reaction stickers from stickers/ (Gemini beats, else spoken-word cues)
    cta_sticker: bool = True  # subscribe / Part 2 sticker when the call to action starts
    background_start: float = Field(0.0, ge=0)
    music_start: float = Field(0.0, ge=0)
    output_name: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    series_id: str | None = Field(None, pattern=r"^[a-f0-9]{6,32}$")
    story_name: str = Field("", max_length=80)
    part: int | None = Field(None, ge=1, le=9)
    parts_total: int | None = Field(None, ge=1, le=9)
    pinned_comment: str = Field("", max_length=1000)
    channel_handle: str = Field("", max_length=32)


class SeriesPart(BaseModel):
    script: str = Field(min_length=3, max_length=3000)
    title: str = Field("", max_length=150)
    description: str = Field("", max_length=4000)
    hashtags: list[str] = Field(default_factory=list)
    title_card: str = Field("", max_length=60)
    popups: list[Popup] = Field(default_factory=list, max_length=10)
    end_card: str = Field("", max_length=80)
    reactions: list[Reaction] = Field(default_factory=list, max_length=6)


class SeriesRenderRequest(BaseModel):
    """Both parts of a story with shared voice, style, music and background clip."""

    clip: str = Field(min_length=1)
    game: str = Field(min_length=1, max_length=80)
    story_name: str = Field("Story", max_length=80)
    series_id: str | None = Field(None, pattern=r"^[a-f0-9]{6,32}$")
    pinned_comment: str = Field("", max_length=1000)
    channel_handle: str = Field("", max_length=32)
    parts: list[SeriesPart] = Field(min_length=2, max_length=2)
    voice: str | None = None
    rate: str | None = Field(None, pattern=r"^[+-]\d{1,3}%$")
    highlight: str = "yellow"
    fit: Literal["crop", "blur"] = "crop"
    max_words: int = Field(3, ge=1, le=6)
    music_source: str = Field("none", max_length=40)
    music_track: str | None = Field(None, pattern=r"^[\w-]{11}$")
    music_volume: float | None = Field(None, ge=0, le=1)
    stickers: bool = True
    cta_sticker: bool = True


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "short"


def clip_path(settings: Settings, filename: str) -> Path:
    """A downloaded background clip, by plain filename only."""
    if Path(filename).name != filename or not filename.endswith(".mp4") or filename == LATEST_FILENAME:
        raise ValueError(f"Not a downloaded clip: {filename}")
    path = settings.backgrounds_dir / filename
    if not path.is_file():
        raise FileNotFoundError(f"Clip not found: {filename}")
    return path


def _clip_meta(path: Path) -> BackgroundClip | None:
    try:
        return BackgroundClip.model_validate_json(path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def credit_line(meta: BackgroundClip | None) -> str:
    if not meta:
        return ""
    who = meta.author or "the original creator"
    return f"Gameplay footage: {who} - {meta.source_url}".strip(" -")


def write_script(settings: Settings, clip: str, game: str, trend_titles: list[str], notes: str,
                 target_seconds: int, watch_clip: bool, progress: ProgressFn,
                 mode: script_writer.ScriptMode = "clip", format: script_writer.ScriptFormat = "short",
                 handle: str | None = None) -> dict[str, Any]:
    path = clip_path(settings, clip)
    if format == "multi":
        series = script_writer.write_series(settings, game, notes=notes, target_seconds=target_seconds,
                                            handle=handle, progress=progress)
        return {"format": "multi", **series.model_dump()}
    result = script_writer.write_script(
        settings, game, path, trend_titles=trend_titles, notes=notes,
        target_seconds=target_seconds, watch_clip=watch_clip, mode=mode, format=format, progress=progress,
    )
    return result.model_dump()


def resolve_music(settings: Settings, req: RenderRequest, progress: ProgressFn) -> music.DownloadedTrack | None:
    if req.music_source in ("", "none"):
        return None
    if req.music_track and (track := music.downloaded(settings, req.music_track)):
        return track
    progress(None, "Picking background music")
    return music.pick_track(settings, req.music_source)


def resolve_popups(settings: Settings, wanted: list[popups.Popup], words: list, progress: ProgressFn):
    """(overlays, assets, words shown): pop-ups timed to their spoken word, images fetched (cached).
    A pop-up without a usable image is skipped rather than failing the render."""
    timed = popups.schedule(wanted, words)
    if timed:
        progress(None, f"Fetching {len(timed)} pop-up images")
    overlays, assets, shown = [], [], []
    for t in timed:
        asset = popups.fetch_asset(settings, t.popup)
        if not asset:
            continue
        x, tilt = video_assembler.popup_layout(len(overlays))
        overlays.append(video_assembler.Overlay(settings.assets_dir / "popups" / asset.filename,
                                                t.start, t.end, x, tilt))
        assets.append(asset)
        shown.append(t.popup.word)
    return overlays, assets, shown


def resolve_stickers(settings: Settings, req: RenderRequest, words: list, popup_overlays: list,
                     progress: ProgressFn) -> tuple[list, list[str]]:
    """Reaction stickers at the story beats + a subscribe / Part 2 sticker at the call to action.
    Sticker problems never fail the render."""
    if not (req.stickers or req.cta_sticker):
        return [], []
    try:
        lib = stickers.library(settings) if req.stickers else []
        if req.stickers and lib:
            progress(None, f"Placing stickers ({len(lib)} in the library)")
        cta = None
        if req.cta_sticker:
            cta = "part2" if req.part and req.part < (req.parts_total or 1) else "subscribe"
        return stickers.plan(
            settings, [s for s in lib if s.category != "off"], words, req.reactions, cta=cta, auto=req.stickers,
            popups=popup_overlays, title_seconds=3.0 if req.title_card.strip() else 0.0,
            end_seconds=3.5 if req.end_card.strip() else 0.0, seed=req.script,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Sticker placement failed; rendering without stickers")
        return [], []


def render_short(settings: Settings, req: RenderRequest, progress: ProgressFn) -> ShortVideo:
    background = clip_path(settings, req.clip)
    meta = _clip_meta(background)
    out_dir = settings.shorts_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / (req.output_name or f"{_slug(req.game)}_{time.strftime('%Y%m%d-%H%M%S')}")
    voice = req.voice or settings.tts_voice

    track = resolve_music(settings, req, progress)

    progress(None, f"Recording the voiceover ({voice})")
    words = voiceover.synthesize(req.script, base.with_suffix(".mp3"), voice, req.rate or settings.tts_rate)

    overlays, assets, shown = resolve_popups(settings, req.popups, words, progress)
    sticker_overlays, used = resolve_stickers(settings, req, words, overlays, progress)

    progress(0.0, "Rendering video")
    output = video_assembler.assemble_video(
        background, base.with_suffix(".mp3"), words, base.with_suffix(".mp4"),
        fit=req.fit, highlight=req.highlight, max_words=req.max_words, progress=progress,
        background_start=req.background_start,
        music=settings.music_dir / track.filename if track else None,
        music_volume=settings.music_volume if req.music_volume is None else req.music_volume,
        music_start=req.music_start,
        overlays=overlays + sticker_overlays, title_card=req.title_card.strip(), end_card=req.end_card.strip(),
    )

    credit = credit_line(meta)
    if track:
        credit = f"{credit}\n{track.credit}".strip()
    if assets:
        credit = f"{credit}\n{popups.credits(assets)}".strip()
    description = req.description.strip()
    if credit and credit not in description:
        description = f"{description}\n\n{credit}".strip()
    short = ShortVideo(
        filename=output.name, game=req.game, title=req.title.strip() or f"{req.game} #shorts",
        description=description, hashtags=req.hashtags, script=req.script, voice=voice,
        duration_seconds=round(video_assembler.media_duration(output), 2),
        background=req.clip, background_title=meta.title if meta else "", credit=credit,
        music_title=track.title if track else "", music_url=track.url if track else "",
        title_card=req.title_card.strip(), popups=shown, stickers=used, end_card=req.end_card.strip(),
        series_id=req.series_id, story_name=req.story_name, part=req.part, parts_total=req.parts_total,
        pinned_comment=req.pinned_comment.strip(), channel_handle=req.channel_handle,
    )
    output.with_suffix(".json").write_text(short.model_dump_json(indent=2), encoding="utf-8")
    shutil.copyfile(output, settings.output_dir / FINAL_SHORT)
    return short


def series_name(settings: Settings, story_name: str, parts: int) -> str:
    """'TheWrongUber' -> files TheWrongUber_Part1.mp4 ...; a number is added if that story exists."""
    base = re.sub(r"[^A-Za-z0-9]+", "", story_name)[:50] or "Story"
    name, n = base, 1
    while any((settings.shorts_dir / f"{name}_Part{i}.mp4").exists() for i in range(1, parts + 1)):
        n += 1
        name = f"{base}{n}"
    return name


def render_series(settings: Settings, req: SeriesRenderRequest, progress: ProgressFn) -> dict[str, Any]:
    """Render every part in order. Parts share the voice, music track and clip; each part continues
    the gameplay (and music) where the previous one stopped, so viewers never see the same footage."""
    series_id = req.series_id or uuid.uuid4().hex[:10]
    total = len(req.parts)
    settings.shorts_dir.mkdir(parents=True, exist_ok=True)
    name = series_name(settings, req.story_name, total)
    handle = req.channel_handle or settings.channel_handle

    track = resolve_music(settings, RenderRequest(
        clip=req.clip, game=req.game, script="...", music_source=req.music_source, music_track=req.music_track,
    ), progress)
    offset = 0.0
    shorts: list[ShortVideo] = []
    for i, part in enumerate(req.parts):
        n = i + 1

        def scaled(frac: float | None, msg: str, i: int = i, n: int = n) -> None:
            progress(None if frac is None else (i + frac) / total, f"Part {n}/{total}: {msg}")

        short = render_short(settings, RenderRequest(
            clip=req.clip, game=req.game, script=part.script, title=part.title, description=part.description,
            hashtags=part.hashtags, voice=req.voice, rate=req.rate, highlight=req.highlight, fit=req.fit,
            max_words=req.max_words, music_source=req.music_source if track else "none",
            music_track=track.video_id if track else None, music_volume=req.music_volume,
            title_card=part.title_card, popups=part.popups, end_card=part.end_card,
            reactions=part.reactions, stickers=req.stickers, cta_sticker=req.cta_sticker,
            background_start=offset, music_start=offset, output_name=f"{name}_Part{n}",
            series_id=series_id, story_name=req.story_name, part=n, parts_total=total,
            pinned_comment=req.pinned_comment if n == 1 else "", channel_handle=handle,
        ), scaled)
        offset += short.duration_seconds or 0.0
        shorts.append(short)
    return {"series_id": series_id, "story_name": req.story_name, "name": name,
            "shorts": [s.model_dump(mode="json") for s in shorts]}


def series_parts(settings: Settings, series_id: str | None) -> list[ShortVideo]:
    if not series_id:
        return []
    return sorted((s for s in list_shorts(settings) if s.series_id == series_id), key=lambda s: s.part or 0)


def list_shorts(settings: Settings) -> list[ShortVideo]:
    out = []
    for sidecar in settings.shorts_dir.glob("*.json"):
        if sidecar.name.endswith(".words.json") or not sidecar.with_suffix(".mp4").exists():
            continue
        try:
            out.append(ShortVideo.model_validate_json(sidecar.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda s: s.created_at, reverse=True)


def short_path(settings: Settings, filename: str) -> Path:
    if Path(filename).name != filename or not filename.endswith(".mp4"):
        raise ValueError(f"Not a Short: {filename}")
    path = settings.shorts_dir / filename
    if not path.is_file() or not path.with_suffix(".json").is_file():
        raise FileNotFoundError(f"Short not found: {filename}")
    return path


_sidecar_lock = threading.Lock()


def get_short(settings: Settings, filename: str) -> ShortVideo:
    path = short_path(settings, filename)
    return ShortVideo.model_validate_json(path.with_suffix(".json").read_text(encoding="utf-8"))


def update_short(settings: Settings, filename: str, fn: Callable[[ShortVideo], None]) -> ShortVideo:
    """Read-modify-write the Short's JSON sidecar (serialised: jobs and requests may race)."""
    with _sidecar_lock:
        short = get_short(settings, filename)
        fn(short)
        sidecar = short_path(settings, filename).with_suffix(".json")
        tmp = sidecar.with_suffix(".json.tmp")
        tmp.write_text(short.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(sidecar)
        return short


def set_ready(settings: Settings, filename: str, ready: bool) -> ShortVideo:
    def apply(short: ShortVideo) -> None:
        short.ready = ready
        short.ready_at = utcnow() if ready else None

    return update_short(settings, filename, apply)


def delete_short(settings: Settings, filename: str) -> bool:
    if Path(filename).name != filename or not filename.endswith(".mp4"):
        raise ValueError(f"Not a Short: {filename}")
    target = settings.shorts_dir / filename
    if not target.exists():
        return False
    for path in (target, target.with_suffix(".json"), target.with_suffix(".mp3"),
                 target.with_suffix(".ass"), target.with_suffix(".words.json")):
        path.unlink(missing_ok=True)
    return True


# --------------------------------------------------------------------------- jobs


class CreateJob(BaseModel):
    id: str
    kind: Literal["script", "render", "copy", "upload"]
    game: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    progress: float | None = None
    message: str = "Queued"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class CreateManager:
    """Script and render jobs on worker threads, polled by id (renders are CPU heavy: one at a time)."""

    def __init__(self, max_jobs: int = 50):
        self._scripts = ThreadPoolExecutor(max_workers=2, thread_name_prefix="script-job")
        self._renders = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render-job")
        self._uploads = ThreadPoolExecutor(max_workers=1, thread_name_prefix="upload-job")
        self._jobs: dict[str, CreateJob] = {}
        self._lock = threading.Lock()
        self._max_jobs = max_jobs

    def _add(self, kind: str, game: str) -> CreateJob:
        job = CreateJob(id=uuid.uuid4().hex[:12], kind=kind, game=game)
        with self._lock:
            self._jobs[job.id] = job
            for old in sorted(self._jobs.values(), key=lambda j: j.created_at)[: -self._max_jobs]:
                if old.status in ("done", "error"):
                    self._jobs.pop(old.id, None)
        return job

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            if job := self._jobs.get(job_id):
                for key, value in fields.items():
                    setattr(job, key, value)

    def _run(self, job_id: str, fn: Callable[[ProgressFn], Any]) -> None:
        self._update(job_id, status="running", message="Starting")
        try:
            result = fn(lambda frac, msg: self._update(job_id, progress=frac, message=msg))
            if isinstance(result, BaseModel):
                result = result.model_dump(mode="json")
            self._update(job_id, status="done", progress=1.0, message="Done", result=result)
        except Exception as err:  # noqa: BLE001 - surface any failure to the UI
            logger.exception("Create job %s failed", job_id)
            self._update(job_id, status="error", message="Failed", error=str(err))

    def submit_script(self, settings: Settings, clip: str, game: str, trend_titles: list[str],
                      notes: str, target_seconds: int, watch_clip: bool,
                      mode: script_writer.ScriptMode = "clip", format: script_writer.ScriptFormat = "short",
                      handle: str | None = None) -> CreateJob:
        job = self._add("script", game)
        self._scripts.submit(self._run, job.id, lambda p: write_script(
            settings, clip, game, trend_titles, notes, target_seconds, watch_clip, p, mode, format, handle))
        return job

    def submit_render(self, settings: Settings, req: RenderRequest) -> CreateJob:
        job = self._add("render", req.game)
        self._renders.submit(self._run, job.id, lambda p: render_short(settings, req, p))
        return job

    def submit_series(self, settings: Settings, req: SeriesRenderRequest) -> CreateJob:
        job = self._add("render", req.game)
        self._renders.submit(self._run, job.id, lambda p: render_series(settings, req, p))
        return job

    def submit(self, kind: Literal["copy", "upload"], game: str, fn: Callable[[ProgressFn], Any]) -> CreateJob:
        """Platform texts (Gemini) run with scripts; uploads one at a time."""
        job = self._add(kind, game)
        (self._uploads if kind == "upload" else self._scripts).submit(self._run, job.id, fn)
        return job

    def get(self, job_id: str) -> CreateJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list(self) -> list[CreateJob]:
        with self._lock:
            return [j.model_copy(deep=True)
                    for j in sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)]
