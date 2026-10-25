"""DarkArchives plan: slots, items, the bot's due posts and the tab's endpoints."""

from __future__ import annotations

import json
from datetime import date as Day, datetime, timedelta

import pytest

from modules import da_plan, video_gen
from trendclip.config import get_settings


def _video(out, story_id, title="The Ship", uploaded=False):
    out.mkdir(exist_ok=True)
    (out / f"story_{story_id}.mp4").write_bytes(b"\x00" * 16)
    info = {"story_id": story_id, "title": title, "filename": f"story_{story_id}.mp4", "duration_seconds": 60.5,
            "created_at": f"2026-10-2{story_id}T10:00:00"}
    if uploaded:
        info["uploads"] = {"youtube": {"url": "https://youtube.com/shorts/x", "privacy": "public", "studio_url": "https://s/x"}}
    (out / f"story_{story_id}.json").write_text(json.dumps(info))


def test_slots_and_settings(monkeypatch):
    assert da_plan.slots() == ["14:30", "23:00"]
    monkeypatch.setenv("DA_SLOTS", "19:00,bad")
    assert da_plan.slots() == ["19:00"]
    monkeypatch.setenv("DA_SLOTS", "bad")
    assert da_plan.slots() == ["14:30", "23:00"]
    monkeypatch.setenv("DA_AUTO_POST", "off")
    assert not da_plan.auto_post()
    monkeypatch.setenv("DA_UPLOAD_PRIVACY", "unlisted")
    assert da_plan.upload_privacy() == "unlisted"


def test_plan_items(tmp_path, monkeypatch):
    out = tmp_path / "videos"
    _video(out, 1)
    _video(out, 2, "Second", uploaded=True)
    day = Day(2026, 11, 2)
    a = da_plan.add_item(da_plan.ItemRequest(story_id=1, date=day), out)
    assert a.slot == "14:30" and a.title == "The Ship"
    b = da_plan.add_item(da_plan.ItemRequest(story_id=2, date=day), out)
    assert b.slot == "23:00"
    with pytest.raises(ValueError, match="is full"):
        da_plan.add_item(da_plan.ItemRequest(story_id=1, date=day), out)
    with pytest.raises(ValueError, match="already has"):
        da_plan.add_item(da_plan.ItemRequest(story_id=1, date=day, slot="23:00"), out)
    with pytest.raises(ValueError, match="no video"):
        da_plan.add_item(da_plan.ItemRequest(story_id=9, date=day), out)
    moved = da_plan.update_item(a.id, da_plan.ItemUpdate(date=day + timedelta(days=1)), out)
    assert moved.date == day + timedelta(days=1) and moved.slot == "14:30"
    with pytest.raises(ValueError, match="already has"):
        da_plan.update_item(a.id, da_plan.ItemUpdate(date=day, slot="23:00"), out)
    assert da_plan.planned_dates(out) == {1: ["2026-11-03 14:30"], 2: ["2026-11-02 23:00"]}
    v = da_plan.view(out)
    assert [i["story_id"] for i in v["items"]] == [2, 1] and v["items"][0]["youtube"]["done"] and not v["items"][1]["youtube"]["done"]
    assert [x["story_id"] for x in v["videos"]] == [2, 1] and v["videos"][1]["planned"] == ["2026-11-03 14:30"]
    assert da_plan.set_posted(a.id, True, out).posted["youtube"]
    assert da_plan.delete_item(a.id, out) and not da_plan.delete_item(a.id, out)
    with pytest.raises(KeyError):
        da_plan.update_item("nope", da_plan.ItemUpdate(), out)


def test_due_posts_and_post_now(tmp_path, monkeypatch):
    settings = get_settings()
    out = tmp_path / "videos"
    _video(out, 1)
    _video(out, 2, uploaded=True)
    day = Day(2026, 11, 2)
    item = da_plan.add_item(da_plan.ItemRequest(story_id=1, date=day, slot="23:00"), out)
    at = datetime(2026, 11, 2, 23, 0, 30, tzinfo=settings.tz)
    assert da_plan.due_posts(settings, at - timedelta(minutes=1), set(), out) == []
    assert da_plan.due_posts(settings, at, set(), out) == [("da 2026-11-02 23:00 post", day, "23:00")]
    assert da_plan.due_posts(settings, at, {"da 2026-11-02 23:00 post"}, out) == []
    assert da_plan.due_posts(settings, at + timedelta(hours=3), set(), out) == []  # missed by more than the window
    monkeypatch.setenv("DA_AUTO_POST", "0")
    assert da_plan.due_posts(settings, at, set(), out) == []
    monkeypatch.delenv("DA_AUTO_POST")

    calls = []

    def fake_upload(story_id, privacy, progress, out_dir=None):
        calls.append((story_id, privacy))
        return {"url": "https://youtube.com/shorts/new", "privacy": privacy}

    record = da_plan.post_now(item, out_dir=out, upload=fake_upload)
    assert calls == [(1, "public")] and record["url"].endswith("/new")
    assert da_plan.is_posted(da_plan.slot_item(day, "23:00", out), out)
    assert da_plan.due_posts(settings, at, set(), out) == []  # posted: nothing due any more

    other = da_plan.add_item(da_plan.ItemRequest(story_id=2, date=day + timedelta(days=1)), out)
    assert da_plan.post_now(other, out_dir=out, upload=fake_upload)["privacy"] == "public" and len(calls) == 1  # already up


def test_plan_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    out = tmp_path / "videos"
    _video(out, 1)
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", out)
    client = TestClient(web.app)
    assert client.get("/api/darkarchives/videos").json() == {"videos": [{**json.loads((out / "story_1.json").read_text()), "planned": []}], "slots": ["14:30", "23:00"]}
    assert client.post("/api/darkarchives/plan/items", json={"story_id": 1, "date": "2026-11-02"}).status_code == 201
    assert client.post("/api/darkarchives/plan/items", json={"story_id": 1, "date": "2026-11-02"}).json()["slot"] == "23:00"
    assert client.post("/api/darkarchives/plan/items", json={"story_id": 1, "date": "2026-11-02"}).status_code == 400  # the day is full
    assert client.post("/api/darkarchives/plan/items", json={"story_id": 1, "date": "2026-11-02", "slot": "7pm"}).status_code == 422
    plan = client.get("/api/darkarchives/plan").json()
    client.delete(f"/api/darkarchives/plan/items/{plan['items'][1]['id']}")
    item = plan["items"][0]
    assert plan["slots"] == ["14:30", "23:00"] and plan["auto_post"] and item["slot"] == "14:30" and item["video"]["title"] == "The Ship"
    assert client.get("/api/darkarchives/videos").json()["videos"][0]["planned"] == ["2026-11-02 14:30"]
    assert client.post(f"/api/darkarchives/plan/items/{item['id']}", json={"date": "2026-11-03"}).json()["date"] == "2026-11-03"
    assert client.post(f"/api/darkarchives/plan/items/{item['id']}/posted", json={"posted": True}).json()["youtube"]["done"]
    assert client.post("/api/darkarchives/plan/items/nope", json={"date": "2026-11-03"}).status_code == 404
    assert client.delete(f"/api/darkarchives/plan/items/{item['id']}").json() == {"deleted": item["id"]}
    assert client.delete(f"/api/darkarchives/plan/items/{item['id']}").status_code == 404
