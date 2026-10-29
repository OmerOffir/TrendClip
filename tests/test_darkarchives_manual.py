"""Dark Outliers "Write it myself": a story's script written by hand with any LLM (modules/da_manual.py) and its
dashboard endpoints."""

from __future__ import annotations

import json
import time

import pytest

from db import repository
from modules import da_manual, da_plan, da_produce, script_gen, video_gen

STORY = ("In December 1872 the Mary Celeste was found drifting near the Azores. The lifeboat was gone. "
         "Food and water were still aboard. The crew of ten was never seen again. ") * 3


def _draft(**change) -> dict:
    draft = {
        "usable": True, "reject_reason": "", "title": "The Ship That Sailed Alone",
        "hook_text": "A ship sailed on for days with no one aboard.",
        "thumbnail_text": "a ship sailed with no one aboard", "hook_image_prompt": "cinematic photo of an empty ship's deck, close-up",
        "scenes": [
            {"narration": "In December 1872 sailors found the Mary Celeste drifting near the Azores.",
             "visual_keywords": ["foggy sea", "old ship"], "image_prompt": "cinematic photo of a drifting ship", "overlay_text": "December 1872"},
            {"narration": "Food and water were still aboard. Only the lifeboat was missing.",
             "visual_keywords": ["ship galley", "rope"], "image_prompt": "cinematic photo of a ship galley", "overlay_text": "LIFEBOAT GONE"},
            {"narration": "The crew of ten was never seen again.",
             "visual_keywords": ["empty sea", "storm"], "image_prompt": "cinematic photo of an empty sea", "overlay_text": "TEN MEN GONE"},
            {"narration": "So what made them leave a sound ship?",
             "visual_keywords": ["lifeboat", "fog"], "image_prompt": "cinematic photo of an empty lifeboat in fog", "overlay_text": "WHY LEAVE?"},
        ],
    }
    draft.update(change)
    return draft


@pytest.fixture
def bank(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, "DB_PATH", tmp_path / "bank.db")
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", tmp_path / "scripts")
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", tmp_path / "videos")
    monkeypatch.setenv("DA_SCRIPT_LEARNING", "0")
    repository.init_db()
    repository.save_story("wikipedia_1", "wikipedia/Ghost ships", "Mary Celeste", STORY,
                          "https://en.wikipedia.org/wiki/Mary_Celeste", 90, pillar="mystery")
    repository.save_story("reddit_x", "reddit/ColdCases", "Some post", "A long post. " * 30, "https://reddit.com/x", 60, pillar="mystery")
    return tmp_path


def test_write_check_facts_then_ready(bank):
    s = da_manual.start()  # the next story: the best one
    assert s["id"] == "story-1" and s["step"] == "write" and s["title"] == "Mary Celeste"
    assert "Mary Celeste" in s["prompt"] and "ANSWER FORMAT" in s["prompt"] and "hook_image_prompt" in s["prompt"]
    assert repository.get_story(1)["status"] == "NEW"  # the morning run may still take it
    assert da_manual.start(1)["id"] == "story-1"  # picked up again, not opened twice

    with pytest.raises(da_manual.ManualError, match="no JSON"):
        da_manual.answer("story-1", "Sure! Here is the script.")
    short = _draft(scenes=_draft()["scenes"][:3])
    s = da_manual.answer("story-1", "```json\n" + json.dumps(short) + "\n```")
    assert s["step"] == "fix" and any("words" in p for p in s["problems"]) and "Your script breaks the rules" in s["prompt"]
    with pytest.raises(da_manual.ManualError, match="passes the rules"):
        da_manual.facts("story-1", '{"unsupported": []}')

    s = da_manual.answer("story-1", json.dumps(_draft()))
    assert s["step"] == "facts" and s["preview"]["words"] >= script_gen.MIN_WORDS and len(s["preview"]["scenes"]) == 4
    assert "fact-check" in s["prompt"] and STORY[:60] in s["prompt"] and '"unsupported"' in s["prompt"]

    s = da_manual.facts("story-1", '{"unsupported": ["crew of ten - the source says ten, fine; the date is wrong"]}')
    assert s["step"] == "facts_fix" and "A fact checker found" in s["prompt"] and "the date is wrong" in s["prompt"]
    with pytest.raises(da_manual.ManualError, match="both checks"):
        da_manual.story_of("story-1")
    s = da_manual.answer("story-1", json.dumps(_draft()))
    assert s["step"] == "facts"
    s = da_manual.facts("story-1", '{"unsupported": []}')
    assert s["step"] == "ready" and da_manual.story_of("story-1") == 1
    saved = script_gen.Script.model_validate_json(script_gen.script_path(1).read_text())
    assert saved.thumbnail_text == "A SHIP SAILED WITH NO ONE ABOARD" and saved.hook_image_prompt.startswith("cinematic") and saved.scenes[0].overlay_text == "DECEMBER 1872"
    assert repository.get_story(1)["status"] == "SCRIPT_READY" and "by hand" in repository.get_story(1)["note"]

    da_manual.mark_made("story-1", {"date": "2026-11-02", "slot": "14:30"})
    assert da_manual.view("story-1")["planned"] == "2026-11-02 14:30"
    with pytest.raises(da_manual.ManualError, match="already made"):
        da_manual.answer("story-1", json.dumps(_draft()))
    assert [x["id"] for x in da_manual.sessions()] == ["story-1"]


def test_unusable_story_is_rejected_and_bad_ids(bank):
    da_manual.start(2)
    s = da_manual.answer("story-2", '{"usable": false, "reject_reason": "a request for help, no story"}')
    assert s["step"] == "rejected" and repository.get_story(2)["status"] == "REJECTED"
    assert "request for help" in repository.get_story(2)["note"]
    for bad in ("../x", "story-9", ""):
        with pytest.raises(da_manual.ManualError):
            da_manual.view(bad)
    with pytest.raises(da_manual.ManualError, match="No story"):
        da_manual.start(99)
    assert [x["title"] for x in da_manual.stories()] == ["Mary Celeste"] and da_manual.stories()[0]["next"]


def test_manual_endpoints_make_and_plan(bank, monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip.web import app as web
    from trendclip.web import darkarchives

    client = TestClient(web.app)
    listing = client.get("/api/darkarchives/manual").json()
    assert listing["stories"][0]["id"] == 1 and listing["sessions"] == [] and listing["slots"]
    s = client.post("/api/darkarchives/manual", json={}).json()
    assert s["id"] == "story-1"
    assert client.post("/api/darkarchives/manual/story-1/make", json={}).status_code == 400  # not checked yet
    assert client.post("/api/darkarchives/manual/story-1/answer", json={"text": "no json here"}).status_code == 400
    assert client.post("/api/darkarchives/manual/story-1/answer", json={"text": json.dumps(_draft())}).json()["step"] == "facts"
    assert client.post("/api/darkarchives/manual/story-1/facts", json={"text": '{"unsupported": []}'}).json()["step"] == "ready"

    made = []

    def fake_make(story_id, progress, **k):
        info = {"story_id": story_id, "title": "The Ship That Sailed Alone", "duration_seconds": 25, "pictures": "local", "words": 55}
        video_gen.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        video_gen.video_path(story_id).write_bytes(b"mp4")
        video_gen.info_path(story_id).write_text(json.dumps(info))
        made.append(story_id)
        return info

    monkeypatch.setattr(video_gen, "make_video", fake_make)
    announced = []
    monkeypatch.setattr(da_produce, "plan_video", lambda settings, story_id, info, day, slot, out_dir=None, announce=False:
                        announced.append(announce) or da_plan.add_item(da_plan.ItemRequest(story_id=story_id, date=day, slot=slot)))
    day, slot = listing["slots"][0].split()
    assert client.post("/api/darkarchives/manual/story-1/make", json={"day": day, "slot": "07:07"}).status_code == 400
    r = client.post("/api/darkarchives/manual/story-1/make", json={"day": day, "slot": slot})
    assert r.status_code == 202
    for _ in range(100):
        if not client.get("/api/darkarchives/video-job").json()["running"]:
            break
        time.sleep(0.05)
    job = client.get("/api/darkarchives/video-job").json()
    assert job.get("error") is None and job["planned"] == f"{day} {slot}" and made == [1] and announced == [True]
    assert client.get("/api/darkarchives/manual/story-1").json()["step"] == "made"
    assert f"{day} {slot}" not in darkarchives.open_slots()
    assert client.post("/api/darkarchives/manual/story-1/make", json={"day": day, "slot": slot}).status_code == 400  # made already
