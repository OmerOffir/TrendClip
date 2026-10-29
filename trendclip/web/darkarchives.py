"""Dark Outliers tab: browse the story bank (db/content_bank.db), fetch stories, write scripts and make the videos."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from db import repository
from modules import da_channel, da_insights, da_plan, da_produce, fetcher, script_gen, video_gen
from trendclip import publish

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/darkarchives")
Source = Literal["reddit", "wikipedia", "gemini", "rss", "llm"]
Status = Literal["NEW", "PROCESSING", "SCRIPT_READY", "USED", "REJECTED"]
Pillar = Literal["mystery", "history", "outrage"]

_lock = threading.Lock()
_job: dict[str, Any] = {"running": False}
_script_job: dict[str, Any] = {"running": False}
_video_job: dict[str, Any] = {"running": False}
_upload_job: dict[str, Any] = {"running": False}
_produce_job: dict[str, Any] = {"running": False}


@router.get("/stories")
def stories(status: Status | None = None, source: Source | None = None, pillar: Pillar | None = None,
            q: str = Query("", max_length=100), limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    repository.init_db()
    return {
        "stories": repository.list_stories(status, source, q, limit, pillar),
        "counts": repository.counts(),
        "sources": repository.source_counts(),
        "mix": repository.pillar_mix(),
        "pillar_labels": repository.PILLAR_LABELS,
        "next_id": (repository.get_next_story() or {}).get("id"),
        "next_pillar": repository.next_pillar(),
        "videos": video_gen.existing_video_ids(),
    }


@router.get("/stories/{story_id}")
def story(story_id: int) -> dict[str, Any]:
    found = repository.get_story(story_id)
    if not found:
        raise HTTPException(status_code=404, detail="No such story")
    return found


class StatusUpdate(BaseModel):
    status: Status


@router.post("/stories/{story_id}/status")
def set_status(story_id: int, body: StatusUpdate) -> dict[str, Any]:
    if not repository.get_story(story_id):
        raise HTTPException(status_code=404, detail="No such story")
    repository.set_status(story_id, body.status)
    return repository.get_story(story_id)


class FetchRequest(BaseModel):
    sources: list[Source] = Field(default_factory=lambda: list(fetcher.SOURCES), min_length=1)
    reddit: int = Field(25, ge=1, le=100)
    wikipedia: int = Field(10, ge=1, le=50)
    gemini: int = Field(5, ge=1, le=20)
    rss: int = Field(10, ge=1, le=50)


def _run(req: FetchRequest) -> None:
    def progress(name, report):
        with _lock:
            _job.update(current=name, report=dict(report))

    try:
        report = fetcher.run(req.sources, req.reddit, req.wikipedia, req.gemini, req.rss, progress=progress)
        with _lock:
            _job.update(report=report)
    except Exception as err:  # noqa: BLE001 - shown in the tab
        with _lock:
            _job.update(error=str(err)[:300])
    finally:
        with _lock:
            _job.update(running=False, current=None, finished_at=time.time())


@router.post("/fetch", status_code=202)
def start_fetch(req: FetchRequest) -> dict[str, Any]:
    with _lock:
        if _job.get("running"):
            raise HTTPException(status_code=409, detail="A fetch is already running")
        _job.clear()
        _job.update(running=True, sources=list(req.sources), started_at=time.time(), report={}, current=None)
    threading.Thread(target=_run, args=(req,), name="darkarchives-fetch", daemon=True).start()
    return fetch_status()


@router.get("/fetch")
def fetch_status() -> dict[str, Any]:
    with _lock:
        return dict(_job)


@router.get("/llm-topics")
def llm_topics_prompt(count: int = Query(10, ge=1, le=20)) -> dict[str, Any]:
    """Gemini's research prompt, to paste into any LLM ("New stories from your LLM")."""
    return {"prompt": fetcher.llm_prompt(count)}


class LLMText(BaseModel):
    text: str = Field(min_length=2, max_length=500_000)


@router.post("/llm-topics")
def llm_topics_add(req: LLMText) -> dict[str, Any]:
    """The LLM's cases into the bank, scored like Gemini's by their Wikipedia page views (the hub's bank, through
    Discord, on the sending machine)."""
    from modules import channel_hub
    from trendclip import llm_json
    from trendclip.hub import HubError

    try:
        return channel_hub.add_topics(_settings_for_targets(), "darkarchives", req.text,
                                      lambda text: llm_json.parse(text, fetcher.GeminiCases, fetcher.LLMTopicsError))
    except (fetcher.LLMTopicsError, HubError) as err:
        status = getattr(err, "status", 400)
        raise HTTPException(status_code=status if 400 <= status < 600 else 502, detail=str(err)) from err


@router.get("/stories/{story_id}/script")
def get_script(story_id: int) -> dict[str, Any]:
    path = script_gen.script_path(story_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No script for this story yet")
    return json.loads(path.read_text(encoding="utf-8"))


def _write(story_id: int) -> None:
    try:
        result = script_gen.process(story_id)
        with _lock:
            _script_job.update(status=result["status"], reason=result.get("reason", ""))
    except Exception as err:  # noqa: BLE001 - shown in the tab
        with _lock:
            _script_job.update(error=str(err)[:300])
    finally:
        with _lock:
            _script_job.update(running=False, finished_at=time.time())


def _rank() -> None:
    from modules import da_ranking

    try:
        result = da_ranking.process()
        with _lock:
            _script_job.update(status=result["status"], story_id=result["story"]["id"], title=result["script"].title)
    except Exception as err:  # noqa: BLE001 - shown in the tab
        with _lock:
            _script_job.update(error=str(err)[:300])
    finally:
        with _lock:
            _script_job.update(running=False, finished_at=time.time())


@router.post("/ranking", status_code=202)
def start_ranking() -> dict[str, Any]:
    """Gemini ranks the best NEW stories of the bank into one countdown script (a new story of its own)."""
    with _lock:
        if _script_job.get("running"):
            raise HTTPException(status_code=409, detail="A script is already being written")
        _script_job.clear()
        _script_job.update(running=True, story_id=None, ranking=True, started_at=time.time())
    threading.Thread(target=_rank, name="darkarchives-ranking", daemon=True).start()
    return script_status()


@router.post("/stories/{story_id}/script", status_code=202)
def start_script(story_id: int) -> dict[str, Any]:
    """Gemini writes the Shorts script in the background (it can take a minute or more)."""
    story = repository.get_story(story_id)
    if not story:
        raise HTTPException(status_code=404, detail="No such story")
    if story["status"] == "USED":
        raise HTTPException(status_code=400, detail="This story was already used in a video")
    with _lock:
        if _script_job.get("running"):
            raise HTTPException(status_code=409, detail="A script is already being written")
        _script_job.clear()
        _script_job.update(running=True, story_id=story_id, started_at=time.time())
    threading.Thread(target=_write, args=(story_id,), name="darkarchives-script", daemon=True).start()
    return script_status()


@router.get("/script-job")
def script_status() -> dict[str, Any]:
    with _lock:
        return dict(_script_job)


# --------------------------------------------------------------------------- videos


def _make(story_id: int) -> None:
    def progress(frac, msg):
        with _lock:
            _video_job.update(progress=frac, message=msg)

    try:
        info = video_gen.make_video(story_id, progress)
        with _lock:
            _video_job.update(result=info)
    except Exception as err:  # noqa: BLE001 - shown in the tab
        logger.exception("Dark Outliers video for story %s failed", story_id)
        with _lock:
            _video_job.update(error=str(err)[:400])
    finally:
        with _lock:
            _video_job.update(running=False, finished_at=time.time())


@router.post("/stories/{story_id}/video", status_code=202)
def start_video(story_id: int) -> dict[str, Any]:
    """Make the Short in the background (voice, pictures, motion, captions: several minutes)."""
    if not repository.get_story(story_id):
        raise HTTPException(status_code=404, detail="No such story")
    if not script_gen.script_path(story_id).is_file():
        raise HTTPException(status_code=400, detail="Write the Shorts script first")
    with _lock:
        if _video_job.get("running"):
            raise HTTPException(status_code=409, detail="A video is already being made")
        _video_job.clear()
        _video_job.update(running=True, story_id=story_id, started_at=time.time(), progress=0.0, message="Starting")
    threading.Thread(target=_make, args=(story_id,), name="darkarchives-video", daemon=True).start()
    return video_status()


@router.get("/video-job")
def video_status() -> dict[str, Any]:
    with _lock:
        return dict(_video_job)


@router.get("/stories/{story_id}/video/info")
def video_info(story_id: int) -> dict[str, Any]:
    path = video_gen.info_path(story_id)
    if not path.is_file() or not video_gen.video_path(story_id).is_file():
        raise HTTPException(status_code=404, detail="No video for this story yet")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/stories/{story_id}/video")
def video_file(story_id: int) -> FileResponse:
    path = video_gen.video_path(story_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No video for this story yet")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.delete("/stories/{story_id}/video")
def delete_video(story_id: int) -> dict[str, Any]:
    """Remove the video (and its pictures, plan entries); the story can be made again."""
    if not video_gen.video_path(story_id).is_file() and not video_gen.info_path(story_id).is_file():
        raise HTTPException(status_code=404, detail="No video for this story")
    with _lock:
        busy = _video_job.get("running") and _video_job.get("story_id") == story_id
        uploading = _upload_job.get("running") and _upload_job.get("story_id") == story_id
    if busy or uploading:
        raise HTTPException(status_code=409, detail="This video is being made or uploaded right now")
    return video_gen.delete_video(story_id)


@router.get("/stories/{story_id}/video/pictures/{name}")
def video_picture(story_id: int, name: str) -> FileResponse:
    if not re.fullmatch(r"(beat|photo)_\d{1,2}\.jpg", name):
        raise HTTPException(status_code=404, detail="No such picture")
    path = video_gen.OUTPUT_DIR / f"story_{story_id}" / "pictures" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such picture")
    return FileResponse(path, media_type="image/jpeg")


# --------------------------------------------------------------------------- the YouTube channel


@router.get("/youtube/status")
def youtube_status() -> dict[str, Any]:
    return da_channel.youtube_status()


@router.post("/youtube/connect")
def youtube_connect(request: Request) -> dict[str, Any]:
    """The Google login for the Dark Outliers channel; the shared callback saves its token in its folder."""
    from .app import _callback_url

    try:
        return {"auth_url": da_channel.start_login(_callback_url(request))}
    except publish.PublishError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post("/youtube/logout")
def youtube_logout() -> dict[str, Any]:
    da_channel.logout()
    return {"connected": False}


class UploadRequest(BaseModel):
    privacy: publish.Privacy = "private"


def _upload(story_id: int, privacy: str) -> None:
    def progress(frac, msg):
        with _lock:
            _upload_job.update(progress=frac, message=msg)

    try:
        record = da_channel.upload(story_id, privacy, progress)
        with _lock:
            _upload_job.update(result=record)
    except Exception as err:  # noqa: BLE001 - shown in the tab
        logger.exception("Dark Outliers upload of story %s failed", story_id)
        with _lock:
            _upload_job.update(error=str(err)[:400])
    finally:
        with _lock:
            _upload_job.update(running=False, finished_at=time.time())


@router.post("/stories/{story_id}/upload", status_code=202)
def start_upload(story_id: int, body: UploadRequest) -> dict[str, Any]:
    if not video_gen.video_path(story_id).is_file():
        raise HTTPException(status_code=404, detail="No video for this story yet")
    if not da_channel.connected():
        raise HTTPException(status_code=400, detail="Connect the Dark Outliers YouTube channel first")
    with _lock:
        if _upload_job.get("running"):
            raise HTTPException(status_code=409, detail="An upload is already running")
        _upload_job.clear()
        _upload_job.update(running=True, story_id=story_id, started_at=time.time(), progress=0.0, message="Starting")
    threading.Thread(target=_upload, args=(story_id, body.privacy), name="darkarchives-upload", daemon=True).start()
    return upload_status()


@router.get("/upload-job")
def upload_status() -> dict[str, Any]:
    with _lock:
        return dict(_upload_job)


# --------------------------------------------------------------------------- videos and the plan


@router.get("/videos")
def videos() -> dict[str, Any]:
    """Every finished video with where it is planned and whether it is on YouTube."""
    planned = da_plan.planned_dates()
    return {"videos": [{**v, "planned": planned.get(v.get("story_id"), []), "youtube": youtube_texts(v)}
                       for v in da_plan.videos()],
            "slots": da_plan.slots(), "privacy": da_plan.upload_privacy()}


def youtube_texts(info: dict) -> dict[str, Any]:
    """What the upload sends: the title (with #shorts), the description (with the follow line) and the tags."""
    req = da_channel.upload_request(info)
    return {"title": publish._yt_title(req.title, True), "description": req.description, "tags": publish._tags(req.tags)}


@router.get("/plan")
def plan(fresh: bool = False) -> dict[str, Any]:
    """This machine's plan; on the sending machine the Linux machine's (the one that counts), see channel_hub.plan_tab."""
    from modules import channel_hub
    from trendclip.config import get_settings

    return channel_hub.plan_tab(get_settings(), "darkarchives", da_plan.view, fresh)


@router.post("/plan/items", status_code=201)
def add_plan_item(req: da_plan.ItemRequest) -> dict[str, Any]:
    try:
        return da_plan.item_view(da_plan.add_item(req))
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.post("/plan/items/{item_id}")
def update_plan_item(item_id: str, req: da_plan.ItemUpdate) -> dict[str, Any]:
    try:
        return da_plan.item_view(da_plan.update_item(item_id, req))
    except KeyError:
        raise HTTPException(status_code=404, detail="No such plan item") from None
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.delete("/plan/items/{item_id}")
def delete_plan_item(item_id: str) -> dict[str, Any]:
    if not da_plan.delete_item(item_id):
        raise HTTPException(status_code=404, detail="No such plan item")
    return {"deleted": item_id}


class PostedUpdate(BaseModel):
    posted: bool = True


@router.post("/plan/items/{item_id}/posted")
def set_plan_posted(item_id: str, body: PostedUpdate) -> dict[str, Any]:
    try:
        return da_plan.item_view(da_plan.set_posted(item_id, body.posted))
    except KeyError:
        raise HTTPException(status_code=404, detail="No such plan item") from None


# --------------------------------------------------------------------------- the production run


def _produce(fetch: bool, max_slots: int | None) -> None:
    from trendclip.config import get_settings

    def progress(frac, msg):
        with _lock:
            _produce_job.update(progress=frac, message=msg)

    try:
        result = da_produce.run(get_settings(), progress=progress, fetch=fetch, max_slots=max_slots, announce=True)
        with _lock:
            _produce_job.update(result={"fetch": result["fetch"], "slots": [
                {k: v for k, v in r.items() if k != "info"} | ({"title": r["info"]["title"]} if r.get("info") else {})
                for r in result["slots"]]})
    except Exception as err:  # noqa: BLE001
        logger.exception("Dark Outliers production run failed")
        with _lock:
            _produce_job.update(error=str(err)[:400])
    finally:
        with _lock:
            _produce_job.update(running=False, finished_at=time.time())


class ProduceRequest(BaseModel):
    fetch: bool = True
    max_slots: int | None = Field(None, ge=1, le=10)


@router.post("/produce", status_code=202)
def start_produce(body: ProduceRequest) -> dict[str, Any]:
    """The production run now (what the bot does at DA_AUTO_PRODUCE_TIME): fetch, then a video per empty slot."""
    with _lock:
        if _produce_job.get("running") or _video_job.get("running"):
            raise HTTPException(status_code=409, detail="A video is already being made")
        _produce_job.clear()
        _produce_job.update(running=True, started_at=time.time(), progress=None, message="Starting",
                            targets=[f"{d.isoformat()} {s}" for d, s in da_produce.targets(_settings_for_targets(), _now())])
    threading.Thread(target=_produce, args=(body.fetch, body.max_slots), name="darkarchives-produce", daemon=True).start()
    return produce_status()


def _settings_for_targets():
    from trendclip.config import get_settings

    return get_settings()


def _now():
    from datetime import datetime

    return datetime.now(_settings_for_targets().tz)


@router.get("/produce-job")
def produce_status() -> dict[str, Any]:
    with _lock:
        return dict(_produce_job)


@router.get("/insights")
def insights(refresh: bool = False) -> dict[str, Any]:
    """The channel's learning loop: every uploaded video's numbers (cached a few hours; `refresh` reads YouTube
    now), the best and worst, what each pillar / hook style / length / slot gets, and the lessons the script
    prompts end with. Without the channel's login the report carries the error instead of failing."""
    try:
        data = da_insights.refresh(_settings_for_targets(), force=refresh)
    except da_insights.InsightsError as err:
        return {**da_insights.report({}), "error": str(err)}
    return da_insights.report(data)


@router.get("/produce/targets")
def produce_targets() -> dict[str, Any]:
    return {"targets": [f"{d.isoformat()} {s}" for d, s in da_produce.targets(_settings_for_targets(), _now())],
            "auto_produce": da_produce.auto_produce(), "time": da_produce.produce_time(), "daily_fetch": da_produce.daily_fetch()}


# --------------------------------------------------------------------------- Write it myself (modules/da_manual.py)


class ManualStart(BaseModel):
    story_id: int | None = Field(None, ge=1)


class ManualText(BaseModel):
    text: str = Field(min_length=2, max_length=200_000)


class ManualMake(BaseModel):
    day: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    slot: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")


def _manual(fn, *args):
    from modules import da_manual

    try:
        return fn(*args)
    except da_manual.ManualError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


def open_slots(days: int = 7) -> list[str]:
    """The empty slots of the coming days, from now on ("2026-10-29 14:00")."""
    from datetime import timedelta

    settings, now = _settings_for_targets(), _now()
    return [f"{day.isoformat()} {slot}" for n in range(days) for day in [now.date() + timedelta(days=n)]
            for slot in da_plan.slots()
            if da_plan.slot_time(settings, day, slot) > now and da_plan.slot_item(day, slot) is None]


@router.get("/manual")
def manual_list() -> dict[str, Any]:
    from modules import da_manual

    return {"sessions": da_manual.sessions(), "stories": da_manual.stories(), "slots": open_slots()}


@router.post("/manual")
def manual_start(req: ManualStart) -> dict[str, Any]:
    """Open a session for a story (the next one when none is given) and give the writing prompt."""
    from modules import da_manual

    return _manual(da_manual.start, req.story_id)


@router.get("/manual/{sid}")
def manual_view(sid: str) -> dict[str, Any]:
    from modules import da_manual

    return _manual(da_manual.view, sid)


@router.post("/manual/{sid}/answer")
def manual_answer(sid: str, req: ManualText) -> dict[str, Any]:
    from modules import da_manual

    return _manual(da_manual.answer, sid, req.text)


@router.post("/manual/{sid}/facts")
def manual_facts(sid: str, req: ManualText) -> dict[str, Any]:
    from modules import da_manual

    return _manual(da_manual.facts, sid, req.text)


def _make_manual(sid: str, story_id: int, day, slot: str) -> None:
    from modules import da_manual
    from trendclip.config import get_settings

    def progress(frac, msg):
        with _lock:
            _video_job.update(progress=frac, message=msg)

    try:
        settings = get_settings()
        info = video_gen.make_video(story_id, progress, settings=settings, announce=False)
        problems = da_produce.check_video(info)
        if problems:
            raise RuntimeError("The video is made (it's in Videos) but not planned: " + "; ".join(problems))
        if da_plan.slot_item(day, slot) is not None:
            raise RuntimeError(f"{day} {slot} was taken meanwhile: the video is in Videos, plan it from there")
        item = da_produce.plan_video(settings, story_id, info, day, slot, announce=True)
        da_manual.mark_made(sid, item.model_dump(mode="json"))
        with _lock:
            _video_job.update(result=info, planned=f"{day} {slot}")
    except Exception as err:  # noqa: BLE001 - shown in the tab
        logger.exception("Dark Outliers manual video for story %s failed", story_id)
        with _lock:
            _video_job.update(error=str(err)[:400])
    finally:
        with _lock:
            _video_job.update(running=False, finished_at=time.time())


@router.post("/manual/{sid}/make", status_code=202)
def manual_make(sid: str, req: ManualMake | None = None) -> dict[str, Any]:
    """Make the video of a checked script and plan it for a slot (the next free one when none is given);
    it is posted in Discord with the "Don't post this" button, like the morning run's."""
    from datetime import date

    from modules import da_manual

    story_id = _manual(da_manual.story_of, sid)
    if req and req.day and req.slot:
        if req.slot not in da_plan.slots():
            raise HTTPException(status_code=400, detail=f"{req.slot} is not a slot (DA_SLOTS: {', '.join(da_plan.slots())})")
        day, slot = date.fromisoformat(req.day), req.slot
        if da_plan.slot_item(day, slot) is not None:
            raise HTTPException(status_code=409, detail=f"{req.day} {slot} already has a video")
    else:
        found = da_plan.next_free_slot(_settings_for_targets(), _now())
        if found is None:
            raise HTTPException(status_code=409, detail="No free slot in the next 30 days")
        day, slot = found
    with _lock:
        if _video_job.get("running"):
            raise HTTPException(status_code=409, detail="A video is already being made")
        _video_job.clear()
        _video_job.update(running=True, story_id=story_id, started_at=time.time(), progress=0.0, message="Starting")
    threading.Thread(target=_make_manual, args=(sid, story_id, day, slot), name="darkarchives-manual-video", daemon=True).start()
    return video_status()


# --------------------------------------------------------------------------- Write a week (modules/da_week.py)


class WeekStart(BaseModel):
    count: int = Field(10, ge=0, le=21)
    rankings: int | None = Field(None, ge=0, le=7)


class WeekText(BaseModel):
    text: str = Field(min_length=2, max_length=2_000_000)


def _week(fn, *args, **kwargs):
    from modules import da_week

    try:
        return fn(*args, **kwargs)
    except da_week.WeekError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@router.get("/week")
def week_view() -> dict[str, Any]:
    """The week session (None before the first start), the default number of rankings, whether this machine sends."""
    from modules import da_week
    from trendclip import hub

    view = _week(da_week.view) if da_week.exists() else None
    new = len([s for s in repository.all_stories("NEW") if not s["source_id"].startswith("ranking_")])
    return {"week": view, "sender": hub.is_sender(_settings_for_targets()), "new": new,
            "default_rankings": da_week.default_rankings(da_week.DEFAULT_COUNT), "waiting": da_produce.waiting()}


@router.post("/week")
def week_start(req: WeekStart | None = None) -> dict[str, Any]:
    """Pick the next stories and ranking pools from the bank (no Gemini) and open the session."""
    from modules import da_week

    req = req or WeekStart()
    return _week(da_week.start, req.count, req.rankings)


@router.post("/week/answer")
def week_answer(req: WeekText) -> dict[str, Any]:
    from modules import da_week

    return _week(da_week.answer, req.text)


@router.post("/week/facts")
def week_facts(req: WeekText) -> dict[str, Any]:
    from modules import da_week

    return _week(da_week.facts, req.text)


@router.post("/week/drop/{kind}/{i}")
def week_drop(kind: Literal["story", "ranking"], i: int) -> dict[str, Any]:
    from modules import da_week

    return _week(da_week.drop, kind, i)


@router.post("/week/send")
def week_send() -> dict[str, Any]:
    """The ready scripts to the hub (through Discord from the sending machine; straight into the bank on the hub)."""
    from modules import da_week
    from trendclip.hub import HubError

    try:
        return _week(da_week.send, _settings_for_targets())
    except HubError as err:
        raise HTTPException(status_code=err.status if 400 <= err.status < 600 else 502, detail=str(err)) from err
