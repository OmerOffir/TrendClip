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

from . import script_writer, video_assembler, voiceover
from .config import Settings
from .models import utcnow
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
    created_at: datetime = Field(default_factory=utcnow)


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
                 target_seconds: int, watch_clip: bool, progress: ProgressFn) -> dict[str, Any]:
    path = clip_path(settings, clip)
    result = script_writer.write_script(
        settings, game, path, trend_titles=trend_titles, notes=notes,
        target_seconds=target_seconds, watch_clip=watch_clip, progress=progress,
    )
    return result.model_dump()


def render_short(settings: Settings, req: RenderRequest, progress: ProgressFn) -> ShortVideo:
    background = clip_path(settings, req.clip)
    meta = _clip_meta(background)
    out_dir = settings.shorts_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{_slug(req.game)}_{time.strftime('%Y%m%d-%H%M%S')}"
    voice = req.voice or settings.tts_voice

    progress(None, f"Recording the voiceover ({voice})")
    words = voiceover.synthesize(req.script, base.with_suffix(".mp3"), voice, req.rate or settings.tts_rate)

    progress(0.0, "Rendering video")
    output = video_assembler.assemble_video(
        background, base.with_suffix(".mp3"), words, base.with_suffix(".mp4"),
        fit=req.fit, highlight=req.highlight, max_words=req.max_words, progress=progress,
    )

    credit = credit_line(meta)
    description = req.description.strip()
    if credit and credit not in description:
        description = f"{description}\n\n{credit}".strip()
    short = ShortVideo(
        filename=output.name, game=req.game, title=req.title.strip() or f"{req.game} #shorts",
        description=description, hashtags=req.hashtags, script=req.script, voice=voice,
        duration_seconds=round(video_assembler.media_duration(output), 2),
        background=req.clip, background_title=meta.title if meta else "", credit=credit,
    )
    output.with_suffix(".json").write_text(short.model_dump_json(indent=2), encoding="utf-8")
    shutil.copyfile(output, settings.output_dir / FINAL_SHORT)
    return short


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
    kind: Literal["script", "render"]
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
                      notes: str, target_seconds: int, watch_clip: bool) -> CreateJob:
        job = self._add("script", game)
        self._scripts.submit(self._run, job.id, lambda p: write_script(
            settings, clip, game, trend_titles, notes, target_seconds, watch_clip, p))
        return job

    def submit_render(self, settings: Settings, req: RenderRequest) -> CreateJob:
        job = self._add("render", req.game)
        self._renders.submit(self._run, job.id, lambda p: render_short(settings, req, p))
        return job

    def get(self, job_id: str) -> CreateJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list(self) -> list[CreateJob]:
        with self._lock:
            return [j.model_copy(deep=True)
                    for j in sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)]
