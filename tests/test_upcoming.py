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


def test_upcoming_on_the_sender_is_the_linux_week(monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip import hub
    from trendclip.web import app as web

    web._UPCOMING_CACHE.clear()
    asks = []
    monkeypatch.setattr(hub, "is_sender", lambda settings: True)
    monkeypatch.setattr(upcoming, "local_unsent", lambda settings: [{"channel": "darkarchives", "title": "Here only"}])
    monkeypatch.setattr(hub, "remote_upcoming", lambda settings: asks.append(1) or {"days": [], "empty": 5, "twice": 0})
    client = TestClient(web.app)
    body = client.get("/api/upcoming").json()
    assert body["source"] == "linux" and body["empty"] == 5 and body["local"][0]["title"] == "Here only"
    client.get("/api/upcoming")
    client.get("/api/upcoming?fresh=1")
    assert len(asks) == 2  # the second call came from the cache

    def down(settings):
        raise hub.HubError("The Linux bot didn't answer.", 504)

    web._UPCOMING_CACHE.clear()
    monkeypatch.setattr(hub, "remote_upcoming", down)
    monkeypatch.setattr(upcoming, "view", lambda settings, now=None: {"days": [], "empty": 0, "twice": 0})
    body = client.get("/api/upcoming").json()
    assert body["source"] == "here" and "didn't answer" in body["hub_error"]


def test_empty_slots_name_the_scripts_written_for_later(tmp_path, monkeypatch):
    """Each channel's queue (Write a week) fills its empty slots in time order on the page, oldest script first."""
    settings = get_settings()
    monkeypatch.setattr(upcoming, "sidequest_rows", lambda settings, now, days: [
        upcoming._row("sidequestlogic", days[0], "23:00"), upcoming._row("sidequestlogic", days[1], "14:30")])
    monkeypatch.setattr(upcoming, "dark_rows", lambda now, days: [
        upcoming._row("darkarchives", days[0], "14:30", title="Made", state="waits", ref=3), upcoming._row("darkarchives", days[0], "23:00"),
        upcoming._row("darkarchives", days[1], "14:30"), upcoming._row("darkarchives", days[1], "23:00")])
    monkeypatch.setattr(upcoming, "borderline_rows", lambda settings, now, days: [
        upcoming._row("borderlinewhy", days[0], "23:00", state="empty", made_at="2026-10-28T15:00")])
    monkeypatch.setattr(upcoming, "waiting", lambda settings: {
        "sidequestlogic": {"items": [{"title": "Minecraft: How many?", "game": "Minecraft"}], "count": 1, "note": ""},
        "darkarchives": {"items": [{"title": "The Ship", "ranking": False}, {"title": "The Tent", "ranking": False},
                                   {"title": "ranking: 5 Cases", "ranking": True}], "count": 3, "note": ""},
        "borderlinewhy": {"items": [], "count": 0, "note": ""}})
    data = upcoming.view(settings, datetime(2026, 10, 28, 13, 0, tzinfo=settings.tz))
    rows = {(r["channel"], r["day"], r["time"]): r for d in data["days"] for r in d["rows"]}
    assert rows[("sidequestlogic", "2026-10-28", "23:00")]["waiting"] == "Minecraft: How many?"
    assert rows[("sidequestlogic", "2026-10-29", "14:30")]["waiting"] == ""
    assert rows[("darkarchives", "2026-10-28", "14:30")]["waiting"] == ""  # it has a video
    assert rows[("darkarchives", "2026-10-28", "23:00")]["waiting"] == "The Ship"
    assert rows[("darkarchives", "2026-10-29", "14:30")]["waiting"] == "The Tent"
    assert rows[("darkarchives", "2026-10-29", "23:00")]["waiting"] == "ranking: 5 Cases"
    assert rows[("borderlinewhy", "2026-10-28", "23:00")]["waiting"] == ""
    assert data["covered"] == 4 and data["waiting"]["darkarchives"]["count"] == 3


def test_waiting_reads_every_channels_queue(tmp_path, monkeypatch):
    import json

    from modules import bw_channel, da_produce
    from trendclip import gamer_queue

    settings = get_settings()
    monkeypatch.setattr(bw_channel, "OUTPUT_DIR", tmp_path / "bw")
    queue = bw_channel.queue_dir()
    queue.mkdir(parents=True)
    (queue / "20261029-01-a.json").write_text(json.dumps({"title": "Why is Kansas flat?"}))
    monkeypatch.setattr(da_produce, "queued", lambda *a, **k: [{"id": 4, "title": "The Ship", "ranking": False}])
    monkeypatch.setattr(gamer_queue, "entries", lambda s: [{"game": "Minecraft", "question": {"question": "How many?"}}])
    w = upcoming.waiting(settings)
    assert w["borderlinewhy"]["items"] == [{"title": "Why is Kansas flat?"}]
    assert w["darkarchives"]["items"] == [{"title": "The Ship", "ranking": False}]
    assert w["sidequestlogic"]["items"] == [{"title": "Minecraft: How many?", "game": "Minecraft"}] and w["sidequestlogic"]["note"] == ""
    monkeypatch.setattr(da_produce, "queued", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no bank")))
    assert upcoming.waiting(settings)["darkarchives"] == {"items": [], "count": 0, "note": "no bank"}
