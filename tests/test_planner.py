from datetime import date

import pytest
from fastapi.testclient import TestClient

from trendclip import planner, shorts
from trendclip.config import Settings

from tests.test_upload import add_short

DAY = date(2026, 10, 6)


@pytest.fixture
def settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output")


def test_planning_a_short_copies_its_title_and_marks_it_ready(settings):
    name = add_short(settings)
    item = planner.add_item(settings, planner.ItemRequest(date=DAY, short=name))

    assert item.title == "The Day My Cat Outsmarted the Block"
    assert item.platforms == ["youtube", "instagram", "tiktok"]
    assert shorts.get_short(settings, name).ready
    view = planner.view(settings)
    assert view["goals"]["reels_per_day"] == 2 and view["goals"]["long_per_week"] == 2
    assert view["shorts"][0]["planned"] == ["2026-10-06"]
    assert view["items"][0]["short_info"]["game"] == "GTA V / Online" and not view["items"][0]["done"]


def test_long_videos_default_to_the_long_goal_platforms(settings):
    item = planner.add_item(settings, planner.ItemRequest(kind="long", date=DAY, title="  Hardcore 100 days "))
    assert item.title == "Hardcore 100 days" and item.platforms == ["youtube"]

    planner.set_goals(settings, planner.Goals(long_platforms=["tiktok", "youtube", "youtube"]))
    assert planner.load(settings).goals.long_platforms == ["youtube", "tiktok"]
    with pytest.raises(ValueError):
        planner.add_item(settings, planner.ItemRequest(kind="long", date=DAY))  # no title, no Short
    with pytest.raises(ValueError):
        planner.add_item(settings, planner.ItemRequest(date=DAY, title="x", platforms=[]))


def test_posted_marks_sync_with_the_upload_tab(settings):
    name = add_short(settings)
    item = planner.add_item(settings, planner.ItemRequest(date=DAY, short=name))

    planner.set_posted(settings, item.id, "instagram", True)
    planner.set_posted(settings, item.id, "youtube", True)
    assert shorts.get_short(settings, name).posted == {"instagram": True, "youtube": True}

    shorts.update_short(settings, name, lambda s: s.posted.__setitem__("tiktok", True))  # Upload tab tick
    assert planner.view(settings)["items"][0]["done"]

    shorts.update_short(settings, name, lambda s: s.posted.__setitem__("instagram", False))  # untick there
    v = planner.view(settings)["items"][0]
    assert not v["done"] and not v["status"]["instagram"]["done"]


def test_a_dashboard_youtube_upload_counts_automatically(settings):
    name = add_short(settings, uploads={"youtube": {"video_id": "abc", "url": "https://youtu.be/abc"}})
    item = planner.add_item(settings, planner.ItemRequest(date=DAY, short=name, platforms=["youtube"]))
    v = planner.view(settings)["items"][0]
    assert v["id"] == item.id and v["done"]
    assert v["status"]["youtube"] == {"done": True, "auto": True, "url": "https://youtu.be/abc", "scheduled_for": None}


def test_deleted_short_keeps_the_plan_record(settings):
    name = add_short(settings)
    item = planner.add_item(settings, planner.ItemRequest(date=DAY, short=name, platforms=["tiktok"]))
    planner.set_posted(settings, item.id, "tiktok", True)
    shorts.delete_short(settings, name)

    v = planner.view(settings)["items"][0]
    assert v["short_missing"] and v["done"] and v["title"] == "The Day My Cat Outsmarted the Block"
    planner.set_posted(settings, item.id, "tiktok", False)  # no Short to update, no error
    assert not planner.view(settings)["items"][0]["done"]


def test_move_relink_and_delete(settings):
    a = add_short(settings, "a.mp4")
    b = add_short(settings, "b.mp4")
    shorts.update_short(settings, b, lambda s: setattr(s, "title", "Second story"))
    item = planner.add_item(settings, planner.ItemRequest(date=DAY, short=a))

    moved = planner.update_item(settings, item.id, planner.ItemUpdate(date=date(2026, 10, 8), notes=" 18:00 "))
    assert moved.date == date(2026, 10, 8) and moved.notes == "18:00" and moved.short == a
    relinked = planner.update_item(settings, item.id, planner.ItemUpdate(short=b))
    assert relinked.short == b and relinked.title == "Second story"
    plain = planner.update_item(settings, item.id, planner.ItemUpdate(unlink=True, title="My own edit"))
    assert plain.short is None and plain.title == "My own edit"
    with pytest.raises(KeyError):
        planner.update_item(settings, "nope", planner.ItemUpdate(title="x"))

    assert planner.delete_item(settings, item.id) and not planner.delete_item(settings, item.id)
    assert planner.view(settings)["items"] == []


def test_plan_endpoints(settings, monkeypatch):
    from trendclip.web import app as web

    monkeypatch.setattr(web, "_base_settings", lambda: settings)
    client = TestClient(web.app)
    name = add_short(settings)

    r = client.post("/api/plan/items", json={"date": "2026-10-06", "short": name})
    assert r.status_code == 200 and r.json()["status"]["tiktok"]["done"] is False
    item_id = r.json()["id"]
    assert client.post("/api/plan/items", json={"date": "2026-10-06", "short": "missing.mp4"}).status_code == 404
    assert client.post("/api/plan/items", json={"date": "2026-10-06"}).status_code == 400
    assert client.post("/api/plan/items", json={"date": "not a day", "title": "x"}).status_code == 422

    for p in ("youtube", "instagram", "tiktok"):
        r = client.post(f"/api/plan/items/{item_id}/posted/{p}", json={"posted": True})
    assert r.json()["done"]
    assert client.post(f"/api/plan/items/{item_id}/posted/facebook", json={"posted": True}).status_code == 422

    r = client.post(f"/api/upload/shorts/{name}/posted/youtube", json={"posted": False})  # Upload tab, YouTube by hand
    assert r.status_code == 200 and r.json()["posted"]["youtube"] is False
    assert not client.get("/api/plan").json()["items"][0]["done"]

    r = client.patch(f"/api/plan/items/{item_id}", json={"date": "2026-10-07"})
    assert r.json()["date"] == "2026-10-07"
    r = client.put("/api/plan/goals", json={"reels_per_day": 3, "long_per_week": 1,
                                            "reel_platforms": ["tiktok"], "long_platforms": ["youtube"]})
    assert r.status_code == 200 and client.get("/api/plan").json()["goals"]["reels_per_day"] == 3
    assert client.put("/api/plan/goals", json={"reels_per_day": 99}).status_code == 422
    assert client.delete(f"/api/plan/items/{item_id}").status_code == 200
    assert client.delete(f"/api/plan/items/{item_id}").status_code == 404
