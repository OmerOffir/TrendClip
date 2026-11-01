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
    pw_write.answer("week", json.dumps({"scripts": [good, {**good, "title": "Other", "question": "Why is the other thing cheap?"}]}), tmp_path)
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


# --------------------------------------------------------------------------- the drawn scenes


def test_number_compare_and_chart_scenes(monkeypatch):
    for name in ("INK", "BLUE", "RED", "GREEN"):
        monkeypatch.setattr(bw_brand, name, getattr(bw_brand, name))
    pw_render.use_style()
    bw_render.use_layout("portrait")
    assert pw_render.number_parts("$2.27") == ("$", 2.27, "", 2, False)
    assert pw_render.number_parts("$190,000")[1:] == (190000.0, "", 0, True)
    number = bw_render.Scene(kind="number", narration="a b", start=0.0, end=4.0,
                             opts={"number": {"label": "If it rose with inflation", "from": "99¢", "to": "$2.27"}})
    drawn = pw_render.Drawn(number)
    big = next(it for it in drawn.items if "count" in it)
    assert big["count"]["from"] == 99.0 and pw_render.counted(big, big["at"]).startswith("$99")  # 99¢ counts up from 99
    assert pw_render.counted(big, 9.0) == "$2.27" and drawn.frame(2.0).size == (bw_render.W, bw_render.H)
    compare = bw_render.Scene(kind="compare", narration="a", start=0.0, end=3.0, opts={"compare": {
        "title": "T", "left": {"label": "On the can", "price": "99¢"}, "right": {"label": "Stores", "price": "$1.59"}, "winner": "left"}})
    texts = {it["text"]: it["rgb"] for it in pw_render.Drawn(compare).items if it["kind"] == "write"}
    assert texts["99¢"] == pw_render.GREEN and texts["$1.59"] == pw_render.RED and "vs" in texts
    chart = bw_render.Scene(kind="chart", narration="a", start=0.0, end=3.0, opts={"chart": {
        "title": "T", "lines": [{"label": "Tea", "points": [["1992", 0.99], ["2022", 0.99]], "end": "99¢"}]}})
    assert "Tea 99¢" in [it["text"] for it in pw_render.Drawn(chart).items if it["kind"] == "write"]


def test_a_missing_photo_becomes_a_whiteboard_and_the_rules_check_the_scenes():
    from modules import pw_write

    spec = bw_render.Spec(scenes=[bw_render.Scene(kind="card", narration="x", title="Monte Carlo Casino")], words=[])
    pw_render.restyle(spec)
    assert spec.scenes[0].kind == "sketch" and spec.scenes[0].opts["sketch"]["write"] == ["Monte Carlo Casino"]
    assert pw_write.scene_problems({"kind": "number", "number": {"to": "100 trillion dollars"}})
    assert pw_write.scene_problems({"kind": "compare", "compare": {"left": {"label": "a", "price": "1"}, "right": {}}})
    assert pw_write.scene_problems({"kind": "chart", "chart": {"lines": [{"points": [["1992", "x"]]}]}})
    assert pw_write.scene_problems({"kind": "photo"}) and not pw_write.scene_problems({"kind": "photo", "photo": "Monaco"})
    only_boards = {**_script(), "scenes": [s for s in _script()["scenes"] if s["kind"] in ("tag", "board", "follow")]}
    assert any("vary the pictures" in p for p in pw_write.format_problems(only_boards))


def test_the_plan_can_remove_pin_and_place(tmp_path, monkeypatch):
    from datetime import datetime

    monkeypatch.setenv("PW_SLOTS", "19:00")
    monkeypatch.setenv("PW_LEAD", "6")
    monkeypatch.delenv("PW_HOLD_UNTIL", raising=False)
    settings = _settings()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    base = _script()
    for name, title in (("a.json", "First"), ("b.json", "Second"), ("c.json", "Third")):
        (scripts / name).write_text(json.dumps({**base, "title": title}))
    monkeypatch.setattr(pw_channel, "SCRIPTS_DIR", scripts)
    out = tmp_path / "out"
    now = datetime(2026, 11, 1, 12, 0, tzinfo=settings.tz)

    def plan():
        days = pw_channel.plan_days(settings, now, before=0, after=2, out_dir=out)
        return [(r["state"], (r["script"] or {}).get("title"), bool(r["video"])) for d in days for r in d["slots"]]

    pw_channel.remove_script("a.json", out)  # out of the plan, still listed
    assert plan() == [("later", "Second", False), ("later", "Third", False), ("no_script", None, False)]
    assert next(s for s in pw_channel.scripts(out) if s["file"] == "a.json")["removed"]
    nov3 = pw_channel.job_for(settings, datetime(2026, 11, 3).date())
    pw_channel.pin_script(nov3, "a.json", out)  # chosen for 3 Nov: back from "removed", the others keep their order
    assert plan() == [("later", "Second", False), ("later", "Third", False), ("later", "First", False)]
    assert pw_channel.script_for(nov3, out).name == "a.json"
    for name in ("a.json", "b.json", "c.json"):
        pw_channel.remove_script(name, out)
    assert plan() == [("no_script", None, False)] * 3
    nov1 = pw_channel.job_for(settings, now.date())
    assert pw_channel.due_makes(settings, datetime(2026, 11, 1, 13, 5, tzinfo=settings.tz), set(), out) == []  # nothing to make

    sample = out / "sample-x"  # a made sample put on 1 Nov: it waits for Approve there
    sample.mkdir(parents=True)
    (sample / "video.mp4").write_bytes(b"x")
    (sample / "info.json").write_text(json.dumps({"title": "Sample", "created_at": "2026-11-01T10:00:00", "check": [],
                                                  "script_file": "b.json"}))
    info = pw_channel.place_video("sample-x", nov1, out)
    assert info["job"]["post_at"].startswith("2026-11-01T19:00") and info["placed_from"] == "sample-x"
    assert pw_channel.slot_state(nov1, info, now) == "waiting" and (sample / "video.mp4").is_file()
    with pytest.raises(pw_channel.ChannelError):
        pw_channel.place_video("sample-x", nov1, out)  # the slot has a video now
    pw_channel.restore_script("b.json", out)
    assert "b.json" not in [p.name for p in pw_channel.queued(out)]  # used by the placed video
    assert pw_channel.remove_slot(nov1, out) == {"removed": "b.json", "video": True}
    assert pw_channel.load_info(nov1.key, out) is None and plan()[0] == ("no_script", None, False)


def test_the_tab_removes_and_places(tmp_path, monkeypatch):
    from trendclip.web import pricetagwhy as web
    from trendclip.web.app import app

    monkeypatch.setattr(pw_channel, "OUTPUT_DIR", tmp_path)
    monkeypatch.delenv("PW_HOLD_UNTIL", raising=False)
    client = TestClient(app)
    r = client.post("/api/pricetagwhy/scripts/remove", json={"file": FIRST.name})
    assert r.status_code == 200 and all(not (row["script"] or {}).get("file") == FIRST.name for d in r.json()["days"] for row in d["slots"])
    assert client.post("/api/pricetagwhy/scripts/remove", json={"file": "nope.json"}).status_code == 404
    assert client.post("/api/pricetagwhy/plan/place", json={"day": "2020-01-01", "slot": "19:00", "video": "x"}).status_code == 400
    assert client.post("/api/pricetagwhy/scripts/restore", json={"file": FIRST.name}).status_code == 200
    assert not web._making()


def test_every_drawing_draws_and_the_hatch_stays_inside():
    from modules import pw_draw

    for name in pw_draw.DRAWINGS:
        d, k, dx, dy = pw_draw.scaled(name, (0, 0, 400, 400))
        assert d.strokes and 0 < k and d.w * k <= 400.5 and d.h * k <= 400.5, name
    path = pw_draw.hatch(pw_draw.rect(0, 0, 100, 50)[:-1], 10, 6)
    assert path and all(6 <= x <= 94 and 6 <= y <= 44 for x, y in path)


def test_the_product_opens_drawn_boards_get_pictures_and_the_end_loops(monkeypatch):
    for name in ("INK", "BLUE", "RED", "GREEN"):
        monkeypatch.setattr(bw_brand, name, getattr(bw_brand, name))
    pw_render.use_style()
    bw_render.use_layout("portrait")
    product = bw_render.Scene(kind="product", narration="a b c", start=0.0, end=5.0, opts={"product": {
        "draw": "can", "label": ["ICED", "TEA"], "lines": ["Should cost", {"text": "$2.38", "color": "red"}]}})
    drawn = pw_render.Drawn(product, first=True)
    early = drawn.items[:drawn.late]
    assert early and all(it["at"] < 0 for it in early)  # the can is on the first frame
    assert {it["text"] for it in early if it["kind"] == "write"} == {"ICED", "TEA"}
    assert [it["text"] for it in drawn.items[drawn.late:] if it["kind"] == "write"] == ["Should cost", "$2.38"]
    board = bw_render.Scene(kind="board", narration="x", start=5.0, end=9.0, opts={"board": {"write": ["Reason 1", "No ads"]}},
                            elements=[bw_render.Element(kind="icon", opts={"icon": "tv"})])
    photo = bw_render.Scene(kind="product", narration="x", start=9.0, end=10.0, opts={"product": {"photo": "AriZona Iced Tea"}})
    spec = bw_render.Spec(scenes=[product, board, photo], words=[])
    pw_render.restyle(spec)
    assert board.kind == "sketch" and board.opts["sketch"]["picture"] == "tv" and board.opts["_drawn_icons"]
    assert photo.kind == "photo" and photo.opts["photo"] == "AriZona Iced Tea"
    replay = pw_render.Replay(drawn, 20.0, 22.0)
    assert len(replay.items) == drawn.late and replay.items[0]["at"] == 20.0 and replay.done <= 22.01
    assert pw_render.NAME == "Price Tag Why"


def test_photos_need_a_free_licence():
    from modules import bw_photos

    assert all(bw_photos.FREE.match(x) for x in ("CC BY-SA 4.0", "CC BY 2.0", "CC0", "Public domain", "PD-US"))
    assert not any(bw_photos.FREE.match(x) for x in ("GFDL", "", "All rights reserved"))


def test_the_thumbnail_comes_from_the_product_and_is_set_after_the_upload(tmp_path, monkeypatch):
    from PIL import Image

    from modules import pw_thumb

    w = pw_thumb.words(_script())
    assert w["draw"] == "can" and w["old"] == "$2.38" and w["now"] == "99¢" and w["hook"] == "HOW?!"
    monkeypatch.setattr(pw_thumb, "SS", 1)  # quick
    for style in pw_thumb.STYLES:
        assert Image.open(pw_thumb.make(_script(), tmp_path / f"{style}.jpg", style)).size == (1080, 1920)
    calls = []

    class Thumbs:
        def set(self, videoId, media_body):
            calls.append(videoId)
            return type("Q", (), {"execute": lambda self: (_ for _ in ()).throw(RuntimeError("forbidden"))})()

    client = type("C", (), {"thumbnails": lambda self: Thumbs()})()
    assert pw_channel.set_thumbnail(client, "vid1", tmp_path / "shock.jpg").startswith("not set: forbidden") and calls == ["vid1"]


def test_upcoming_shows_the_designed_thumbnail(tmp_path):
    from modules import thumbs

    folder = tmp_path / "pricetagwhy" / "2026-10-31_2300"
    folder.mkdir(parents=True)
    (folder / "video.mp4").write_bytes(b"v")
    (folder / "thumbnail.jpg").write_bytes(b"old frame")
    assert thumbs.paths("pricetagwhy", "2026-10-31_2300", tmp_path)[1].name == "thumbnail.jpg"
    (folder / "thumb.jpg").write_bytes(b"designed")
    assert thumbs.fresh("pricetagwhy", "2026-10-31_2300", tmp_path).read_bytes() == b"designed"


def test_a_topic_starts_its_own_script_with_its_pillar(tmp_path):
    from modules import pw_write

    view = pw_write.start("one", "Why do  prices end in .99?", out_dir=tmp_path)
    assert view["topics"] == [{"question": "Why do prices end in .99?", "pillar": "everyday prices"}]


def test_the_owner_can_accept_what_the_fact_checker_flagged(tmp_path):
    from modules import pw_write

    pw_write.start("one", "Why is AriZona Iced Tea still 99 cents?", out_dir=tmp_path)
    assert pw_write.answer("one", json.dumps(_script()), tmp_path)["step"] == "facts"
    flagged = pw_write.facts("one", '{"checks": [{"script": 1, "unsupported": ["the $2.38 figure"]}]}', tmp_path)
    assert flagged["step"] == "fix" and flagged["can_accept"] and flagged["after_facts"]
    assert "could not confirm" in flagged["prompt"] and "the $2.38 figure" in flagged["prompt"]
    assert pw_write.accept("one", tmp_path)["step"] == "ready"
    name = pw_write.queue("one", tmp_path)["queued"][0]
    saved = json.loads((pw_channel.queue_dir(tmp_path) / name).read_text())
    assert saved["checked"]["flags"] == ["the $2.38 figure"]
    with pytest.raises(pw_write.WriteError):
        pw_write.accept("one", tmp_path)


def test_the_owner_can_skip_the_fact_check(tmp_path):
    from modules import pw_write

    pw_write.start("one", "Why is AriZona Iced Tea still 99 cents?", out_dir=tmp_path)
    with pytest.raises(pw_write.WriteError):
        pw_write.skip_facts("one", tmp_path)  # the rules check comes first
    pw_write.answer("one", json.dumps(_script()), tmp_path)
    assert pw_write.skip_facts("one", tmp_path)["step"] == "ready"
    name = pw_write.queue("one", tmp_path)["queued"][0]
    assert json.loads((pw_channel.queue_dir(tmp_path) / name).read_text())["checked"]["facts"] == "skipped by the owner"


def test_the_owner_can_skip_the_rules_check_and_the_slots_still_take_it(tmp_path, monkeypatch):
    from modules import pw_write

    monkeypatch.setattr(pw_channel, "SCRIPTS_DIR", tmp_path / "none")
    pw_write.start("one", "Why is AriZona Iced Tea still 99 cents?", out_dir=tmp_path)
    with pytest.raises(pw_write.WriteError):
        pw_write.skip_rules("one", tmp_path)  # nothing pasted yet
    broken = {**_script(), "sources": _script()["sources"][:1]}  # one source: the rules check refuses it
    assert pw_write.answer("one", json.dumps(broken), tmp_path)["step"] == "fix"
    view = pw_write.skip_rules("one", tmp_path)
    assert view["step"] == "facts" and view["skipped_rules"]
    pw_write.skip_facts("one", tmp_path)
    name = pw_write.queue("one", tmp_path)["queued"][0]
    queued = json.loads((pw_channel.queue_dir(tmp_path) / name).read_text())
    assert queued["checked"]["rules"] == "skipped by the owner" and pw_channel.check_script(queued)
    assert [p.name for p in pw_channel.queued(tmp_path)] == [name]  # a slot still makes it
    listed = next(s for s in pw_channel.scripts(tmp_path) if s["file"] == name)
    assert listed["rules_skipped"] and listed["check"]


def test_the_thumbnail_says_the_question_and_reads_any_figure():
    from modules import pw_thumb

    script = {"question": "Why can't we just print money?", "thumbnail": {"hook": "wait, what?"}, "scenes": [{"kind": "product", "product": {
        "draw": "bag", "lines": ["Cost in 1922", {"text": "160 marks", "color": "green"}, "Cost in 1923", {"text": "200B marks", "color": "red"}]}}]}
    w = pw_thumb.words(script)
    assert w["title"] == "WHY CAN'T WE JUST PRINT MONEY?" and w["hook"] == "WAIT, WHAT?"
    assert (w["old_label"], w["old"], w["now_label"], w["now"]) == ("Cost in 1922", "160 marks", "Cost in 1923", "200B marks")


def test_the_description_credits_only_what_the_video_shows(monkeypatch):
    monkeypatch.setenv("PW_SLOTS", "14:00,23:00")
    text = pw_channel.description(_script())
    assert "Sources:" in text and pw_channel.DISCLAIMER in text and "twice a day" in text
    assert "Noto" not in text and "drawn by code" not in text  # AriZona draws its own pictures
    with_icon = {**_script(), "scenes": [{"kind": "tag", "elements": [{"kind": "icon", "icon": "tea"}]}]}
    assert "Noto Emoji" in pw_channel.description(with_icon)


def test_encyclopedias_are_not_sources():
    bad = {**_script(), "sources": _script()["sources"] + [{"title": "W", "url": "https://en.wikipedia.org/wiki/AriZona"},
                                                          {"title": "K", "url": "https://kids.kiddle.co/Hyperinflation"}]}
    found = [p for p in pw_channel.check_script(bad) if "open encyclopedia" in p]
    assert len(found) == 2 and not pw_channel.check_script(_script())
    assert not pw_channel.WIKI.search("https://commons.wikimedia.org/wiki/File:x.jpg")


def test_write_next_puts_a_topic_first_for_the_next_script(tmp_path, monkeypatch):
    from modules import pw_write

    monkeypatch.setattr(pw_channel, "SCRIPTS_DIR", tmp_path / "none")
    last = pw_write.topics(tmp_path)[-1]["question"]
    pw_write.move_first(last, tmp_path)
    assert pw_write.topics(tmp_path)[0]["question"] == last
    assert pw_write.next_topics(1, tmp_path)[0]["question"] == last
    assert pw_write.start("one", out_dir=tmp_path)["topics"][0]["question"] == last
    pillars = [t["pillar"] for t in pw_write.next_topics(4, tmp_path)]
    assert all(a != b for a, b in zip(pillars, pillars[1:]))  # after it, the pillars still take turns
    with pytest.raises(pw_write.WriteError):
        pw_write.move_first("Why is this not on the list?", tmp_path)


def test_new_topics_from_your_own_llm(tmp_path, monkeypatch):
    from modules import pw_write

    monkeypatch.setattr(pw_channel, "SCRIPTS_DIR", tmp_path / "none")
    prompt = pw_write.llm_prompt(20, tmp_path)
    assert "20 new video questions" in prompt and "Why is AriZona Iced Tea still 99 cents?" in prompt  # what to avoid
    assert all(p in prompt for p in pw_write.PILLARS)
    answer = {"topics": [{"question": "Why are movie theater snacks so expensive?", "pillar": "business tricks"},
                         {"question": "Why is AriZona Iced Tea still 99 cents?", "pillar": "everyday prices"},  # already there
                         {"question": "not a question", "pillar": "money history"},
                         {"question": "Why is saffron so expensive?", "pillar": "spices"}]}  # unknown pillar: left empty
    done = pw_write.llm_add("Here you go:\n```json\n" + json.dumps(answer) + "\n```", tmp_path)
    assert done == {"added": ["Why are movie theater snacks so expensive?", "Why is saffron so expensive?"], "skipped": 2}
    assert pw_write.topics(tmp_path)[-1] == {**pw_write.topics(tmp_path)[-1], "question": "Why is saffron so expensive?", "pillar": ""}
    with pytest.raises(pw_write.WriteError):
        pw_write.llm_add('{"topics": []}', tmp_path)


def test_the_topics_route_answers_with_the_list(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip.web.app import app

    monkeypatch.setattr(pw_channel, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(pw_channel, "SCRIPTS_DIR", tmp_path / "none")
    body = TestClient(app).get("/api/pricetagwhy/topics").json()
    assert body["source"] == "here" and body["left"] == len(body["topics"]) > 0 and body["pillars"]
