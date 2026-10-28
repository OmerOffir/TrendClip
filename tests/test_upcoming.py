"""The Upcoming view: every channel's slots in one list, empty slots shown, a video planned twice flagged."""

from __future__ import annotations

from datetime import date as Day, datetime

from modules import da_plan, upcoming, video_gen
from trendclip.config import get_settings


def test_upcoming_lists_every_channel_and_flags_doubles(tmp_path, monkeypatch):
    settings = get_settings()
    out = tmp_path / "da"
    out.mkdir()
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", out)
    import json

    for sid in (20, 21):
        (out / f"story_{sid}.mp4").write_bytes(b"\x00")
        (out / f"story_{sid}.json").write_text(json.dumps({"story_id": sid, "title": f"Story {sid}", "filename": f"story_{sid}.mp4",
                                                           "duration_seconds": 28.0, "created_at": "2026-10-28T09:00:00"}))
    da_plan.add_item(da_plan.ItemRequest(story_id=20, date=Day(2026, 10, 28), slot="14:30"), out)
    da_plan.add_item(da_plan.ItemRequest(story_id=20, date=Day(2026, 10, 28), slot="23:00"), out)
    da_plan.add_item(da_plan.ItemRequest(story_id=21, date=Day(2026, 10, 29), slot="14:30"), out)
    monkeypatch.setattr(upcoming, "sidequest_rows", lambda settings, now, days: [
        upcoming._row("sidequestlogic", days[0], "14:30", title="Gameplay", state="posted", ref="a.mp4", seconds=40),
        upcoming._row("sidequestlogic", days[0], "23:00")])
    monkeypatch.setattr(upcoming, "borderline_rows", lambda settings, now, days: [
        upcoming._row("borderlinewhy", days[1], "18:00", title="Why is there a hole", state="approval", ref="k1", seconds=50)])
    now = datetime(2026, 10, 28, 13, 0, tzinfo=settings.tz)
    data = upcoming.view(settings, now)
    today = data["days"][0]
    assert today["day"] == "2026-10-28" and today["today"] and [d["day"] for d in data["days"]][-1] == "2026-11-03"
    rows = {(r["channel"], r["time"]): r for r in today["rows"]}
    assert rows[("darkarchives", "14:30")]["title"] == "Story 20" and rows[("darkarchives", "14:30")]["twice"]
    assert rows[("darkarchives", "23:00")]["twice"] and rows[("darkarchives", "23:00")]["state"] == "waits"
    assert rows[("sidequestlogic", "23:00")]["state"] == "empty" and rows[("sidequestlogic", "14:30")]["state"] == "posted"
    assert [r["time"] for r in today["rows"]] == sorted(r["time"] for r in today["rows"])
    tomorrow = {(r["channel"], r["time"]): r for r in data["days"][1]["rows"]}
    assert tomorrow[("darkarchives", "14:30")]["title"] == "Story 21" and not tomorrow[("darkarchives", "14:30")]["twice"]
    assert tomorrow[("darkarchives", "23:00")]["state"] == "empty" and tomorrow[("borderlinewhy", "18:00")]["state"] == "approval"
    assert data["twice"] == 2 and data["empty"] >= 1 + 1 + 2 * 5


def test_upcoming_survives_a_broken_channel(monkeypatch):
    settings = get_settings()

    def boom(*a, **k):
        raise RuntimeError("plan file corrupt")

    monkeypatch.setattr(upcoming, "sidequest_rows", boom)
    monkeypatch.setattr(upcoming, "dark_rows", lambda now, days: [])
    monkeypatch.setattr(upcoming, "borderline_rows", lambda settings, now, days: [])
    data = upcoming.view(settings, datetime(2026, 10, 28, 13, 0, tzinfo=settings.tz))
    errors = [r for d in data["days"] for r in d["rows"] if r["state"] == "error"]
    assert len(errors) == 1 and errors[0]["channel"] == "sidequestlogic" and "corrupt" in errors[0]["title"]


def test_upcoming_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    monkeypatch.setattr(upcoming, "view", lambda settings, now=None: {"now": "2026-10-28T13:00", "days": [], "empty": 0, "twice": 0, "channels": {}})
    assert TestClient(web.app).get("/api/upcoming").json()["empty"] == 0
