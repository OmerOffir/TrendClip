"""DarkArchives production run: targets, story -> script -> video -> check -> plan, and the endpoint."""

from __future__ import annotations

import json
from datetime import date as Day, datetime

import pytest

from db import repository
from modules import da_plan, da_produce, script_gen, video_gen
from trendclip.config import get_settings


def test_targets_and_settings(tmp_path, monkeypatch):
    settings = get_settings()
    out = tmp_path / "videos"
    at = datetime(2026, 11, 2, 9, 30, tzinfo=settings.tz)
    assert da_produce.targets(settings, at, out) == [(Day(2026, 11, 2), "14:30"), (Day(2026, 11, 2), "23:00"),
                                                     (Day(2026, 11, 3), "14:30"), (Day(2026, 11, 3), "23:00")]
    late = datetime(2026, 11, 2, 12, 0, tzinfo=settings.tz)  # 14:30 is under 3 h away
    assert da_produce.targets(settings, late, out)[0] == (Day(2026, 11, 2), "23:00")
    assert da_produce.produce_time() == "09:30" and da_produce.auto_produce() and da_produce.daily_fetch()
    monkeypatch.setenv("DA_AUTO_PRODUCE_TIME", "7:5")
    assert da_produce.produce_time() == "09:30"
    monkeypatch.setenv("DA_AUTO_PRODUCE", "off")
    assert not da_produce.auto_produce()
    assert da_produce.produce_key(Day(2026, 11, 2)) == "da 2026-11-02 produce"


def test_check_video(monkeypatch):
    good = {"duration_seconds": 60, "pictures": "local", "words": 130}
    assert da_produce.check_video(good) == []
    assert da_produce.check_video({**good, "duration_seconds": 110}) == ["110 s long (wanted 25-90)"]
    assert da_produce.check_video({**good, "pictures": "placeholder (local: boom)"}) == ["placeholder pictures (placeholder (local: boom))"]
    assert da_produce.check_video({**good, "words": 40}) == ["only 40 words"]
    monkeypatch.setenv("DA_ALLOW_PLACEHOLDER", "1")
    assert da_produce.check_video({**good, "pictures": "placeholder"}) == []


def _bank(tmp_path, titles):
    db = tmp_path / "bank.db"
    repository.init_db(db)
    for i, t in enumerate(titles, 1):
        repository.save_story(f"gemini_{i}", "gemini", t, "text " * 60, "", 100 - i, pillar="mystery", db_path=db)
    return db


def _fake_make(out, seconds=60.0, pictures="local"):
    def make(story_id, progress, out_dir=None, scripts_dir=None, db_path=None, settings=None, announce=False):
        out.mkdir(exist_ok=True)
        (out / f"story_{story_id}.mp4").write_bytes(b"\x00" * 8)
        info = {"story_id": story_id, "title": f"Video {story_id}", "filename": f"story_{story_id}.mp4",
                "duration_seconds": seconds if story_id != 2 else 120.0, "pictures": pictures, "words": 130, "photos": [],
                "hashtags": ["shorts"], "created_at": "2026-11-01T10:00:00"}
        (out / f"story_{story_id}.json").write_text(json.dumps(info))
        repository.set_status(story_id, "USED", db_path, note="video")
        return info
    return make


def test_produce_one_skips_rejected_stories_and_bad_videos(tmp_path, monkeypatch):
    settings = get_settings()
    db = _bank(tmp_path, ["Promo podcast", "Too long story", "Good story", "Spare"])
    scripts, out = tmp_path / "scripts", tmp_path / "videos"
    scripts.mkdir()
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", scripts)
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", out)

    def fake_process(story_id=None, client=None, out_dir=None, db_path=None):
        story = repository.get_story(story_id, db_path)
        if story["title"].startswith("Promo"):
            repository.set_status(story_id, "REJECTED", db_path, note="a promo")
            return {"story": story, "script": None, "path": None, "status": "REJECTED", "reason": "a promo"}
        (out_dir / f"story_{story_id}.json").write_text("{}")
        repository.set_status(story_id, "SCRIPT_READY", db_path)
        return {"story": story, "script": script_gen.Script(story_id=story_id, title=story["title"], hook_text="h",
                                                             estimated_duration_seconds=40, total_word_count=120, scenes=[]),
                "path": out_dir / f"story_{story_id}.json", "status": "SCRIPT_READY", "reason": ""}

    monkeypatch.setattr(script_gen, "process", fake_process)
    messages = []
    result = da_produce.produce_one(settings, Day(2026, 11, 2), "14:30", lambda f, m: messages.append(m),
                                    db_path=db, scripts_dir=scripts, out_dir=out, make=_fake_make(out))
    assert result["status"] == "planned" and result["story_id"] == 3 and result["info"]["title"] == "Video 3"
    assert [t["story_id"] for t in result["tried"]] == [2, 3] and result["tried"][0]["problems"] == ["120 s long (wanted 25-90)"]
    assert repository.get_story(1, db)["status"] == "REJECTED" and repository.get_story(2, db)["status"] == "REJECTED"
    assert "production check" in repository.get_story(2, db)["note"] and not (out / "story_2.mp4").exists()
    assert repository.get_story(3, db)["status"] == "USED" and da_plan.slot_item(Day(2026, 11, 2), "14:30", out).story_id == 3
    assert any("rejected" in m for m in messages) and any("Not good enough" in m for m in messages)

    # the slot is taken now: nothing more is made for it
    assert da_produce.produce_one(settings, Day(2026, 11, 2), "14:30", db_path=db, scripts_dir=scripts, out_dir=out,
                                  make=_fake_make(out))["status"] == "taken"
    # an empty bank
    repository.set_status(4, "REJECTED", db)
    with pytest.raises(da_produce.ProduceError, match="No NEW stories"):
        da_produce.produce_one(settings, Day(2026, 11, 3), "14:30", db_path=db, scripts_dir=scripts, out_dir=out,
                               make=_fake_make(out))


def test_run_fetches_and_fills_the_slots(tmp_path, monkeypatch):
    settings = get_settings()
    db = _bank(tmp_path, ["A", "B", "C"])
    scripts, out = tmp_path / "scripts", tmp_path / "videos"
    scripts.mkdir()
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", scripts)
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", out)
    monkeypatch.setattr(da_produce, "refresh_bank", lambda progress, db_path=None: {"gemini": {"saved": 2}})

    def fake_process(story_id=None, client=None, out_dir=None, db_path=None):
        story = repository.get_story(story_id, db_path)
        (out_dir / f"story_{story_id}.json").write_text("{}")
        repository.set_status(story_id, "SCRIPT_READY", db_path)
        return {"story": story, "script": script_gen.Script(story_id=story_id, title=story["title"], hook_text="h",
                                                             estimated_duration_seconds=40, total_word_count=120, scenes=[]),
                "path": out_dir / f"story_{story_id}.json", "status": "SCRIPT_READY", "reason": ""}

    monkeypatch.setattr(script_gen, "process", fake_process)
    at = datetime(2026, 11, 2, 9, 30, tzinfo=settings.tz)
    result = da_produce.run(settings, at, db_path=db, scripts_dir=scripts, out_dir=out, make=_fake_make(out), max_slots=2)
    assert result["fetch"] == {"gemini": {"saved": 2}}
    assert [(r["status"], r["slot"]) for r in result["slots"]] == [("planned", "14:30"), ("planned", "23:00")]
    assert sorted(i.story_id for i in da_plan.load(out).items) == [1, 3]  # story 2's video is 120 s: rejected
    assert da_produce.targets(settings, at, out) == [(Day(2026, 11, 3), "14:30"), (Day(2026, 11, 3), "23:00")]


def test_produce_endpoint(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    monkeypatch.setattr(video_gen, "OUTPUT_DIR", tmp_path / "videos")
    monkeypatch.setattr(da_produce, "run", lambda settings, progress=None, fetch=True, max_slots=None, **k: (
        progress(None, "working") or {"fetch": None, "slots": [{"status": "failed", "day": "2026-11-02", "slot": "14:30", "error": "no stories"}]}))
    client = TestClient(web.app)
    t = client.get("/api/darkarchives/produce/targets").json()
    assert t["auto_produce"] and t["time"] == "09:30" and isinstance(t["targets"], list)
    assert client.post("/api/darkarchives/produce", json={"fetch": False}).status_code == 202
    for _ in range(50):
        job = client.get("/api/darkarchives/produce-job").json()
        if not job["running"]:
            break
        time.sleep(0.05)
    assert job["result"]["slots"][0]["error"] == "no stories" and job["finished_at"]
    assert client.post("/api/darkarchives/produce", json={"max_slots": 0}).status_code == 422
