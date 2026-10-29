"""Daily cleanup: old media nothing needs leaves output/; a video not on YouTube yet or planned ahead stays."""

from __future__ import annotations

import json
import os
from datetime import date as Day, datetime
from pathlib import Path

from modules import cleanup, da_plan
from trendclip import planner
from trendclip.config import get_settings

NOW = datetime(2026, 12, 1, 12, 0).timestamp()
OLD = NOW - 40 * 86400
NEW = NOW - 5 * 86400


def _file(root: Path, rel: str, when: float = OLD, data: bytes = b"\x00" * 10) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (when, when))
    return path


def _json(root: Path, rel: str, data: dict, when: float = OLD) -> Path:
    return _file(root, rel, when, json.dumps(data).encode())


def _setup(tmp_path):
    settings = get_settings().model_copy(update={"output_dir": tmp_path})
    root = tmp_path
    # SideQuestLogic: one uploaded, one never uploaded, one uploaded but planned again for later
    for stem, uploads in (("mc_up", {"youtube": {"url": "u"}}), ("mc_new", {}), ("mc_later", {"youtube": {"url": "u"}})):
        _json(root, f"shorts/{stem}.json", {"uploads": uploads})
        for ext in (".mp4", ".mp3", ".ass"):
            _file(root, f"shorts/{stem}{ext}")
        _file(root, f"discord/{stem}.mp4")
    _file(root, "instagram/mc_up.mp4")
    _file(root, "shorts/failed_render.mp3")  # no sidecar: a failed render
    plan = planner.Plan(items=[planner.PlanItem(id="p1", kind="reel", date=Day(2026, 12, 3), slot="14:30", short="mc_later.mp4")])
    planner.plan_path(settings).write_text(plan.model_dump_json(), encoding="utf-8")
    # Dark Outliers: 1 posted, 2 not on YouTube, 3 posted (plan says so)
    da = root / "darkarchives"
    for sid, uploads in ((1, {"youtube": {"url": "u"}}), (2, {}), (3, {})):
        _json(root, f"darkarchives/story_{sid}.json", {"story_id": sid, "uploads": uploads})
        _file(root, f"darkarchives/story_{sid}.mp4")
        _file(root, f"darkarchives/story_{sid}/pictures/beat_1.jpg")
        _file(root, f"darkarchives/discord/story_{sid}.mp4")
    da_plan._save(da_plan.Plan(items=[da_plan.PlanItem(id="d3", date=Day(2026, 10, 1), slot="14:30", story_id=3,
                                                        posted={"youtube": datetime(2026, 10, 1, 14, 30)})]), da)
    # Borderline Why: posted, waiting for approval, vetoed, set aside
    bw = "borderlinewhy"
    _json(root, f"{bw}/2026-10-01_1430/info.json", {"uploads": {"youtube": {"url": "u"}}})
    _json(root, f"{bw}/2026-10-01_2300/info.json", {})
    _json(root, f"{bw}/2026-10-02_1430/info.json", {"veto": True})
    for key in ("2026-10-01_1430", "2026-10-01_2300", "2026-10-02_1430", "2026-10-01_1430.old-063420"):
        _file(root, f"{bw}/{key}/video.mp4")
    # leftovers and things never touched
    _file(root, "aftermarket/2026-10-27_premarket/pictures/frame_1.jpg")
    _file(root, "samples/map.mp4")
    _file(root, "trends_old.json")
    _file(root, "final_short.mp4", NEW)
    return settings, root


def test_dry_run_lists_only_old_unneeded_media(tmp_path):
    settings, root = _setup(tmp_path)
    report = cleanup.run(settings, days=30, delete=False, now=NOW)
    paths = {f["path"] for f in report.files}
    assert paths == {
        "shorts/mc_up.mp4", "shorts/mc_up.mp3", "shorts/mc_up.ass", "discord/mc_up.mp4", "instagram/mc_up.mp4",
        "shorts/failed_render.mp3",
        "darkarchives/story_1.mp4", "darkarchives/story_1/pictures/beat_1.jpg", "darkarchives/discord/story_1.mp4",
        "darkarchives/story_3.mp4", "darkarchives/story_3/pictures/beat_1.jpg", "darkarchives/discord/story_3.mp4",
        "borderlinewhy/2026-10-01_1430/video.mp4", "borderlinewhy/2026-10-02_1430/video.mp4",
        "borderlinewhy/2026-10-01_1430.old-063420/video.mp4",
        "aftermarket/2026-10-27_premarket/pictures/frame_1.jpg",
    }
    assert report.kept == 12 and report.bytes == 10 * len(paths) and not report.deleted
    assert all(p.exists() for p in (root / "shorts/mc_up.mp4", root / "darkarchives/story_1.mp4"))  # dry run


def test_delete_removes_files_and_empty_picture_folders(tmp_path):
    settings, root = _setup(tmp_path)
    report = cleanup.run(settings, days=30, delete=True, now=NOW)
    assert report.deleted and len(report.files) == 16 and not report.errors
    assert not (root / "shorts/mc_up.mp4").exists() and (root / "shorts/mc_up.json").exists()
    assert (root / "shorts/mc_new.mp4").exists() and (root / "shorts/mc_later.mp4").exists()
    assert not (root / "darkarchives/story_1/pictures").exists() and (root / "darkarchives/story_1").is_dir()
    assert (root / "darkarchives/story_2.mp4").exists() and (root / "darkarchives/story_1.json").exists()
    assert (root / "borderlinewhy/2026-10-01_2300/video.mp4").exists()
    assert (root / "darkarchives/discord").is_dir() and (root / "samples/map.mp4").exists()
    assert (root / "trends_old.json").exists() and (root / "final_short.mp4").exists()
    assert cleanup.run(settings, days=30, delete=True, now=NOW).files == []


def test_unreadable_channel_keeps_its_files(tmp_path, monkeypatch):
    settings, root = _setup(tmp_path)

    def broken(root_, today):
        raise RuntimeError("plan.json is broken")

    monkeypatch.setattr(cleanup, "_dark_needed", broken)
    paths = {f["path"] for f in cleanup.run(settings, days=30, now=NOW).files}
    assert not any(p.startswith("darkarchives/") for p in paths) and "shorts/mc_up.mp4" in paths


def test_zero_days_turns_it_off(tmp_path, monkeypatch):
    settings, _ = _setup(tmp_path)
    monkeypatch.setenv("CLEANUP_KEEP_DAYS", "0")
    assert cleanup.keep_days() == 0 and cleanup.run(settings, now=NOW).files == []
    monkeypatch.setenv("CLEANUP_KEEP_DAYS", "junk")
    assert cleanup.keep_days() == cleanup.DEFAULT_KEEP_DAYS


def test_owner():
    assert cleanup.owner(Path("shorts/mc_1.words.json")) == ("shorts", "mc_1")
    assert cleanup.owner(Path("darkarchives/story_12/clips/clip_1.mp4")) == ("darkarchives", "12")
    assert cleanup.owner(Path("darkarchives/discord/story_12.mp4")) == ("darkarchives", "12")
    assert cleanup.owner(Path("borderlinewhy/2026-10-28_1430/discord/video.mp4")) == ("borderlinewhy", "2026-10-28_1430")
    assert cleanup.owner(Path("motion_test/clip_a.mp4")) is None and cleanup.owner(Path("final_short.mp4")) is None


def test_bot_runs_the_cleanup_once_a_day(tmp_path):
    from trendclip import assistant, discord_bot

    settings = get_settings().model_copy(update={"output_dir": tmp_path})
    spawned = []

    class Bot:
        cleanup_job = discord_bot.TrendClipBot.cleanup_job

        def __init__(self):
            self.settings = settings
            self.sent = assistant.SentLog(settings)

        def cleanup_run(self):
            return "run"

        def _spawn(self, work):
            spawned.append(work)

    bot = Bot()
    at = datetime(2026, 10, 29, 9, 0, tzinfo=settings.tz)
    bot.cleanup_job(at)
    bot.cleanup_job(at.replace(hour=10))
    assert spawned == ["run"]
    bot.cleanup_job(at.replace(day=30))
    assert spawned == ["run", "run"]
