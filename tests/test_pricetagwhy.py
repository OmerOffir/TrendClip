"""Price Tag Why: the script rules, the price-tag opening and the board lines in the channel's colours, the tab's API."""

import json

import pytest

from fastapi.testclient import TestClient

from modules import bw_brand, bw_render, pw_channel, pw_render


FIRST = pw_channel.SCRIPTS_DIR / "001-arizona-99c.json"


def _script() -> dict:
    return json.loads(FIRST.read_text(encoding="utf-8"))


def test_the_first_script_meets_the_rules():
    script = _script()
    assert pw_channel.check_script(script) == []
    assert 90 <= pw_channel.words(pw_channel.narration(script)) <= 135  # about 35-50 s
    desc = pw_channel.description(script)
    assert "Not financial advice" in desc and desc.count("https://") == len(script["sources"])


def test_the_rules_catch_a_bad_script():
    script = _script()
    script["claims"][0]["sources"] = [1]
    script["scenes"] = script["scenes"][1:]
    script["scenes"][0]["narration"] += " You should buy this tea."
    problems = pw_channel.check_script(script)
    assert any("price tag" in p for p in problems) and any("fewer than two" in p for p in problems)
    assert any("advice" in p for p in problems)


def test_board_lines_take_a_colour_and_a_strike():
    assert bw_brand.pen_line("plain") == {"text": "plain", "color": "", "strike": False}
    assert bw_brand.pen_line({"text": "x", "color": "green", "strike": 1})["strike"] is True
    bw_render.use_layout("portrait")
    scene = bw_render.Scene(kind="board", narration="a b c", start=0.0, end=4.0,
                            opts={"board": {"write": ["Title", {"text": "TV ads", "color": "red", "strike": True}, "Next"]}})
    board = bw_brand.Board(scene)
    writes = [it for it in board.items if it["kind"] == "write"]
    assert [it["text"] for it in writes] == ["Title", "TV ads", "Next"]
    assert writes[1]["rgb"] == bw_brand.RED and len([it for it in board.items if it["kind"] == "line"]) == 2  # underline + strike


def test_the_tag_opening_draws_the_tag_then_writes(monkeypatch):
    monkeypatch.setattr(bw_brand, "INK", bw_brand.INK)
    monkeypatch.setattr(bw_brand, "BLUE", bw_brand.BLUE)
    monkeypatch.setattr(bw_brand, "RED", bw_brand.RED)
    monkeypatch.setattr(bw_brand, "GREEN", bw_brand.GREEN)
    pw_render.use_style()
    assert bw_brand.pen_color("green", None) == pw_render.GREEN
    bw_render.use_layout("portrait")
    scene = bw_render.Scene(kind="tag", narration="Why? Since 1992.", start=0.0, end=5.0,
                            opts={"tag": {"question": ["Why is it", "still 99¢?"], "price": "99¢", "note": "since 1992"}})
    tag = pw_render.TagBoard(scene)
    assert [it["kind"] for it in tag.items[:3]] == ["line", "line", "line"]  # the tag, its hole, its string
    assert [it["text"] for it in tag.items if it["kind"] == "write"] == ["Why is it", "still 99¢?", "99¢", "since 1992"]
    assert tag.items[0]["at"] < 0.2 and tag.frame(4.9).size == (bw_render.W, bw_render.H)


def test_the_tab_lists_scripts_and_videos(tmp_path, monkeypatch):
    from trendclip.web import app as web_app

    (tmp_path / "k1").mkdir()
    (tmp_path / "k1" / "info.json").write_text(json.dumps({"title": "T", "created_at": "2026-10-31T10:00:00"}))
    (tmp_path / "k1" / "video.mp4").write_bytes(b"x")
    monkeypatch.setattr(pw_channel, "OUTPUT_DIR", tmp_path)
    client = TestClient(web_app.app)
    assert client.get("/api/pricetagwhy/scripts").json()["scripts"][0]["check"] == []
    assert client.get("/api/pricetagwhy/videos").json()["videos"][0]["key"] == "k1"
    assert client.get("/api/pricetagwhy/videos/k1/video.mp4").status_code == 200
    assert client.get("/api/pricetagwhy/videos/nope/video.mp4").status_code == 404
    assert client.post("/api/pricetagwhy/make", json={"file": "../x.json"}).status_code == 400


def test_dark_outliers_is_retired_unless_switched_on(monkeypatch):
    from modules import da_channel

    monkeypatch.delenv("DA_ENABLED", raising=False)
    monkeypatch.setattr("dotenv.dotenv_values", lambda *a, **k: {})
    assert da_channel.retired()
    monkeypatch.setenv("DA_ENABLED", "1")
    assert not da_channel.retired()


# --------------------------------------------------------------------------- the schedule


def _settings():
    from trendclip.config import get_settings

    return get_settings()


def _made(out, key, script_file="001-arizona-99c.json", **extra):
    (out / key).mkdir(parents=True)
    (out / key / "video.mp4").write_bytes(b"x")
    info = {"title": "T", "created_at": "2026-11-01T13:00:00", "check": [], "script_file": script_file,
            "job": {"day": key[:10], "slot": f"{key[11:13]}:{key[13:15]}", "post_at": f"{key[:10]}T{key[11:13]}:{key[13:15]}+02:00"}, **extra}
    (out / key / "info.json").write_text(json.dumps(info))


def test_slots_take_the_queued_scripts_in_order(tmp_path, monkeypatch):
    from datetime import datetime

    monkeypatch.setenv("PW_SLOTS", "19:00")
    monkeypatch.setenv("PW_LEAD", "6")
    settings = _settings()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    base = _script()
    for name, title in (("a.json", "First"), ("b.json", "Second")):
        (scripts / name).write_text(json.dumps({**base, "title": title}))
    (scripts / "bad.json").write_text(json.dumps({**base, "sources": []}))  # fails the rules: never queued
    monkeypatch.setattr(pw_channel, "SCRIPTS_DIR", scripts)
    out = tmp_path / "out"
    assert [p.name for p in pw_channel.queued(out)] == ["a.json", "b.json"]
    now = datetime(2026, 11, 1, 12, 0, tzinfo=settings.tz)
    days = pw_channel.plan_days(settings, now, before=0, after=2, out_dir=out)
    states = [(d["day"], r["state"], (r["script"] or {}).get("title")) for d in days for r in d["slots"]]
    assert states == [("2026-11-01", "later", "First"), ("2026-11-02", "later", "Second"), ("2026-11-03", "no_script", None)]
    due = pw_channel.due_makes(settings, datetime(2026, 11, 1, 13, 5, tzinfo=settings.tz), set(), out)
    assert [j.key for j in due] == ["2026-11-01_1900"]

    def run(cmd, **kw):  # the make process: writes the slot's video
        assert cmd[cmd.index("--script-file") + 1] == "a.json" and cmd[cmd.index("--key") + 1] == "2026-11-01_1900"
        _made(out, "2026-11-01_1900", "a.json")
        return type("R", (), {"returncode": 0})()

    info = pw_channel.make_slot(settings, due[0], out, run=run)
    assert info["key"] == "2026-11-01_1900" and [p.name for p in pw_channel.queued(out)] == ["b.json"]
    assert pw_channel.slot_state(due[0], info, now) == "waiting"  # approval is on by default
    pw_channel.set_aside("2026-11-01_1900", out)  # "Make another": the script goes back to the queue
    assert [p.name for p in pw_channel.queued(out)] == ["a.json", "b.json"]


def test_approve_veto_and_upload_at_the_slot(tmp_path, monkeypatch):
    from datetime import datetime

    monkeypatch.setenv("PW_AUTO_POST", "1")
    settings = _settings()
    _made(tmp_path, "2026-11-01_1900")
    assert pw_channel.publish_if_allowed("2026-11-01_1900", out_dir=tmp_path) is None  # not approved yet
    uploaded = []
    monkeypatch.setattr("trendclip.publish.upload_file", lambda path, req, short, progress, client: uploaded.append(req) or
                        {"video_id": "v1", "url": "https://youtu.be/v1", "privacy": req.privacy})
    monkeypatch.setattr(pw_channel, "announce_upload", lambda *a, **k: None)
    early = pw_channel.approve("2026-11-01_1900", settings, tmp_path, now=datetime(2026, 11, 1, 18, 0, tzinfo=settings.tz), youtube=object())
    assert early["record"] is None and not uploaded  # goes out at its slot
    late = pw_channel.publish_if_allowed("2026-11-01_1900", out_dir=tmp_path, youtube=object())
    assert late["url"] == "https://youtu.be/v1" and uploaded[0].category_id == "27"
    assert pw_channel.load_info("2026-11-01_1900", tmp_path)["uploads"]["youtube"]["video_id"] == "v1"
    with pytest.raises(pw_channel.ChannelError):
        pw_channel.set_aside("2026-11-01_1900", tmp_path)  # on YouTube: stays put
    _made(tmp_path, "2026-11-02_1900")
    assert pw_channel.veto("2026-11-02_1900", tmp_path)["uploaded"] is False
    assert pw_channel.video_state(pw_channel.load_info("2026-11-02_1900", tmp_path)) == "vetoed"


def test_the_buttons_follow_the_approval_setting(monkeypatch):
    monkeypatch.setenv("PW_REQUIRE_APPROVAL", "1")
    ids = [b["custom_id"] for row in pw_channel.buttons("k") for b in row["components"]]
    assert ids == ["tc:pwok:k", "tc:pwveto:k", "tc:pwup:k"]
    from trendclip import discord_bot

    assert all(a in discord_bot.ACTIONS for a in ("pwok", "pwveto", "pwup", "pwupgo", "pwrun"))


# --------------------------------------------------------------------------- writing with any LLM


def test_write_a_week_rules_facts_and_queue(tmp_path, monkeypatch):
    from modules import pw_write

    monkeypatch.setattr(pw_channel, "SCRIPTS_DIR", tmp_path / "none")
    found = pw_write.topics(tmp_path)
    assert len(found) == len(pw_write.SEED_TOPICS) and not found[0]["used"]
    pillars = [t["pillar"] for t in pw_write.next_topics(4, tmp_path)]
    assert all(a != b for a, b in zip(pillars, pillars[1:]))  # the pillars take turns
    with pytest.raises(pw_write.WriteError):
        pw_write.add_topic("not a question", out_dir=tmp_path)
    view = pw_write.start("week", count=2, out_dir=tmp_path)
    assert view["step"] == "write" and "Follow for tomorrow's why" in view["prompt"] and len(view["topics"]) == 2
    good = _script()
    bad = {**good, "scenes": good["scenes"][:2]}
    view = pw_write.answer("week", json.dumps({"scripts": [good, bad]}), tmp_path)
    assert view["step"] == "fix" and not view["drafts"][0]["problems"] and view["drafts"][1]["problems"]
    assert "broke these rules" in view["prompt"]
    view = pw_write.answer("week", "```json\n" + json.dumps({"scripts": [good, {**good, "title": "Other"}]}) + "\n```", tmp_path)
    assert view["step"] == "facts" and "Open every source link" in view["prompt"]
    view = pw_write.facts("week", json.dumps({"checks": [{"script": 2, "unsupported": ["the year"]}]}), tmp_path)
    assert view["step"] == "fix" and view["drafts"][1]["flagged"] == ["the year"]
    pw_write.answer("week", json.dumps({"scripts": [good, {**good, "title": "Other"}]}), tmp_path)
    assert pw_write.facts("week", json.dumps({"checks": [{"script": 1, "unsupported": []}]}), tmp_path)["step"] == "ready"
    done = pw_write.queue("week", tmp_path)
    assert len(done["queued"]) == 2 and done["waiting"] == 2
    assert sum(t["used"] for t in pw_write.topics(tmp_path)) >= 2
    assert pw_write.view("week", tmp_path)["step"] == "none"


def test_one_script_pasted_on_its_own(tmp_path, monkeypatch):
    from modules import pw_write

    monkeypatch.setattr(pw_channel, "SCRIPTS_DIR", tmp_path / "none")
    pw_write.start("one", question="Why is AriZona Iced Tea still 99 cents?", out_dir=tmp_path)
    assert pw_write.answer("one", json.dumps(_script()), tmp_path)["step"] == "facts"


def test_upcoming_shows_the_channel_with_its_queued_script(tmp_path, monkeypatch):
    from datetime import datetime

    from modules import upcoming

    monkeypatch.setenv("PW_SLOTS", "19:00")
    monkeypatch.setattr(pw_channel, "OUTPUT_DIR", tmp_path)
    settings = _settings()
    now = datetime(2026, 11, 1, 12, 0, tzinfo=settings.tz)
    rows = upcoming.pricetag_rows(settings, now, ["2026-11-01", "2026-11-02"])
    assert [(r["day"], r["state"]) for r in rows] == [("2026-11-01", "empty"), ("2026-11-02", "empty")]
    assert [i["title"] for i in upcoming.waiting(settings)["pricetagwhy"]["items"]] == ["Why is AriZona Iced Tea still 99 cents?"]


def test_the_hold_stops_making_and_uploading_until_its_day(tmp_path, monkeypatch):
    from datetime import datetime

    from trendclip import publish

    monkeypatch.setenv("PW_SLOTS", "19:00")
    monkeypatch.setenv("PW_HOLD_UNTIL", "2026-11-09")
    settings = _settings()
    at = datetime(2026, 11, 1, 14, 0, tzinfo=settings.tz)
    assert pw_channel.due_makes(settings, at, set(), tmp_path) == []
    assert pw_channel.due_posts(settings, datetime(2026, 11, 1, 19, 5, tzinfo=settings.tz), set()) == []
    days = pw_channel.plan_days(settings, at, before=0, after=0, out_dir=tmp_path)
    assert days[0]["slots"][0]["state"] == "held" and days[0]["slots"][0]["script"] is None
    later = datetime(2026, 11, 9, 14, 0, tzinfo=settings.tz)
    assert [j.key for j in pw_channel.due_makes(settings, later, set(), tmp_path)] == ["2026-11-09_1900"]
    _made(tmp_path, "2026-11-01_1900")
    monkeypatch.setattr(pw_channel, "Day", type("D", (), {"today": staticmethod(lambda: datetime(2026, 11, 1).date()),
                                                         "fromisoformat": staticmethod(lambda s: datetime.fromisoformat(s).date())}))
    with pytest.raises(publish.PublishError, match="on hold"):
        pw_channel.upload("2026-11-01_1900", youtube=object(), out_dir=tmp_path)
