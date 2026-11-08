"""Price Tag Why: the script rules, the price-tag opening and the board lines in the channel's colours, the tab's API."""

import json

import pytest

from fastapi.testclient import TestClient

from modules import bw_brand, bw_render, pw_cast, pw_channel, pw_render


FIRST = pw_channel.SCRIPTS_DIR / "001-arizona-99c.json"


def _script() -> dict:
    return json.loads(FIRST.read_text(encoding="utf-8"))


def test_the_first_script_meets_the_rules():
    script = _script()
    assert pw_channel.check_script(script) == []
    assert 70 <= pw_channel.words(pw_channel.narration(script)) <= 100  # about 30-38 s
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
    early = pw_channel.approve("2026-11-01_1900", settings, tmp_path, now=datetime(2026, 11, 1, 17, 59, tzinfo=settings.tz), youtube=object())
    assert early["record"] is None and not uploaded  # goes out at its slot (uploaded an hour before)
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
    assert view["step"] == "write" and "no follow line" in view["prompt"] and "Follow for tomorrow" not in view["prompt"] and len(view["topics"]) == 2
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
    assert pw_write.scene_problems({"kind": "photo"}) and not pw_write.scene_problems({"kind": "photo", "photo": "Monaco", "mark": {"draw": "bank", "note": "Banks"}})
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


def test_an_opening_photo_that_is_not_found_is_drawn_with_its_lines():
    o = {"photo": "Las Vegas casino slot machines floor", "lines": ["Clocks, 1931", {"text": "Zero", "color": "red"}]}
    spec = bw_render.Spec(scenes=[bw_render.Scene(kind="product", narration="x", opts={"product": dict(o)})], words=[])
    pw_render.restyle(spec)
    scene = spec.scenes[0]
    assert scene.kind == "photo"
    scene.kind, scene.title = "card", o["photo"]  # what bw_render does when the photo can't be found
    pw_render.restyle(spec)
    assert scene.kind == "product" and scene.opts["product"]["draw"] == "clock"
    assert scene.opts["product"]["lines"] == o["lines"]
    assert pw_render.guess_drawing({"lines": ["Nothing here"]}) == "receipt"


class _FakeComments:
    def __init__(self, fail: bool = False):
        self.fail, self.comments = fail, []

    def commentThreads(self):
        return self

    def insert(self, part, body):
        self.comments.append(body)
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("comments are off")
        return {"id": "c1"}


def test_an_upload_gets_its_comment_to_pin_once_and_a_retry_when_it_fails(tmp_path, monkeypatch):
    assert pw_channel.comment_text({}) == pw_channel.COMMENT
    assert pw_channel.comment_text({"comment": "Would you pay $2.38? 👇"}).startswith("Would you pay $2.38? 👇\n\n")
    info = {"key": "k1", "title": "T", "comment": "Would you pay $2.38? 👇",
            "uploads": {"youtube": {"video_id": "v1", "privacy": "public"}}}
    pw_channel.save_info(info, tmp_path)
    (tmp_path / "k1" / "video.mp4").write_bytes(b"x")
    failing = _FakeComments(fail=True)
    assert pw_channel.comment(pw_channel.load_info("k1", tmp_path), failing, tmp_path) is None
    record = pw_channel.load_info("k1", tmp_path)["uploads"]["youtube"]
    assert "comments are off" in pw_channel.comment_line(record)
    texts = []
    monkeypatch.setattr(pw_channel, "announce_text", lambda text, settings=None, components=None: texts.append(components))
    pw_channel.announce_upload({"key": "k1", "title": "T"}, record)
    assert any(b.get("custom_id") == "tc:pwcm:k1" for row in texts[0] for b in row["components"])
    yt = _FakeComments()
    found = pw_channel.comment_again("k1", tmp_path, yt)
    assert found["url"] == "https://www.youtube.com/watch?v=v1&lc=c1" and "Pin it" in pw_channel.comment_line({"comment": found})
    assert yt.comments[0]["snippet"]["topLevelComment"]["snippet"]["textOriginal"] == "Would you pay $2.38? 👇"
    assert pw_channel.comment(pw_channel.load_info("k1", tmp_path), yt, tmp_path) == found and len(yt.comments) == 1  # once


def test_the_hook_is_a_short_drawn_claim_and_the_first_frame_shows_the_question_big():
    from modules import pw_write

    script = _script()
    assert pw_write.hook_problems(script) == []
    slow = json.loads(json.dumps(script))
    slow["scenes"][0]["narration"] = "When Nevada made gambling legal in 1931, its casinos had clocks on the wall. Why?"
    assert any("words" in p for p in pw_write.hook_problems(slow))
    slow["scenes"][0]["narration"] = "When Nevada made gambling legal. Why?"
    assert any("background" in p for p in pw_write.hook_problems(slow))
    slow["scenes"][0]["product"] = {"photo": "Las Vegas casino"}
    assert any("photo can't be the first frame" in p for p in pw_write.hook_problems(slow))
    pw_render.use_style()
    bw_render.use_layout("portrait")
    banner = pw_render.hook_banner("Why is AriZona still 99¢?", (bw_render.W, bw_render.H))
    assert banner.getpixel((bw_render.W // 2, 140))[3] == 255 and banner.getpixel((bw_render.W // 2, 900))[3] == 0


def test_a_photo_mark_draws_what_the_narration_says_on_a_note_with_an_arrow():
    from PIL import Image

    from modules import pw_write

    assert any("mark" in p for p in pw_write.scene_problems({"kind": "photo", "photo": "Slot machine"}))
    pw_render.use_style()
    mark = pw_render.PhotoMark({"draw": "sun", "note": "Glare!"})
    card = Image.new("RGBA", (900, 700), (250, 248, 242, 255))
    assert mark(card, (18, 18, 882, 600), 0.0) is None  # not there yet
    assert mark(card, (18, 18, 882, 600), 0.8) is not None  # the hand is drawing the sun
    done = Image.new("RGBA", (900, 700), (250, 248, 242, 255))
    assert mark(done, (18, 18, 882, 600), 3.0) is None  # finished: no hand
    assert done.getpixel((600, 120)) != (250, 248, 242, 255)  # the note in the top right corner
    assert pw_render.PhotoMark({"draw": "nothing"})(card, (18, 18, 882, 600), 1.0) is None


def test_a_drawing_is_only_ever_the_thing_it_shows():
    from modules import pw_draw, pw_write

    assert set(pw_draw.LOOKS) == set(pw_draw.DRAWINGS)  # the prompt says what every drawing looks like
    wrong = {"kind": "product", "product": {"draw": "house", "label": ["TULIP"], "lines": []}}
    assert any("never write one thing on a drawing of another" in p for p in pw_write.scene_problems(wrong))
    assert not pw_write.scene_problems({"kind": "product", "product": {"draw": "flower", "lines": []}})
    assert not pw_write.scene_problems({"kind": "product", "product": {"draw": "can", "label": ["ICED", "TEA"], "lines": []}})
    assert "dress = a dress on a hanger" in pw_write.rules()


# --------------------------------------------------------------------------- the money story (the long format)

STORY = pw_channel.SCRIPTS_DIR.parent / "examples" / "002-coffee-cart-story.json"


def _story() -> dict:
    return json.loads(STORY.read_text(encoding="utf-8"))


def test_the_story_example_meets_the_story_rules_and_its_math_adds_up():
    from modules import pw_write

    script = _story()
    assert pw_channel.is_story(script) and pw_channel.limits(script) == pw_channel.STORY_SECONDS
    assert pw_write.problems_of(script) == []
    assert pw_write.STORY_WORDS[0] <= pw_channel.words(pw_channel.narration(script)) <= pw_write.STORY_WORDS[1]
    assert pw_write.math_value("5 x 365 x $10 = $18,250") == 18250 and pw_write.math_value("70 x 365 x 20 x $1 = $511,000") == 511000
    assert pw_write.math_value("5 x 365 x $10 = $18,000") is None and pw_write.math_value("five carts = $18,250") is None
    bad = json.loads(json.dumps(script))
    bad["scenes"][2]["step"]["math"] = "5 x 365 x $10 = $18,000"
    bad["scenes"][3]["step"]["draw"] = "chicken"
    bad["scenes"][0]["cast"]["people"].append({"name": "Friend 5"})
    del bad["parable"]
    problems = pw_write.problems_of(bad)
    assert any("adds up" in p for p in problems) and any("step.draw" in p for p in problems)
    assert any("cast.people" in p for p in problems) and any("fewer than two sources" in p for p in problems)
    said = json.loads(json.dumps(script))
    said["scenes"][1]["narration"] = said["scenes"][1]["narration"].replace("$3,650", "three thousand six hundred and fifty")
    assert any("says the result in digits" in p for p in pw_write.problems_of(said))
    quiet = json.loads(json.dumps(script))
    quiet["description"] = "Four friends."
    quiet["scenes"][6]["narration"] = quiet["scenes"][6]["narration"].replace("It's a parable, not a plan. ", "")
    assert any("parable says so" in p for p in pw_channel.check_script(quiet))


def test_a_story_session_writes_from_story_ideas_with_the_story_rules(tmp_path):
    from modules import pw_write

    with pytest.raises(pw_write.WriteError):
        pw_write.start("one", out_dir=tmp_path, format="story")  # no story ideas on the list yet
    pw_write.add_topic("Same $300, three cousins with a lemonade stand: how did one make $900 and one $90,000?",
                       "business tricks", tmp_path, "story")
    fresh = pw_write.view("one", tmp_path)
    assert fresh["formats"] == ["why", "story", "world"] and fresh["stories"][0]["question"].startswith("Same $300")
    assert fresh["topics"][0]["question"].startswith("Why")  # the why questions stay apart
    view = pw_write.start("one", out_dir=tmp_path, format="story")
    assert view["format"] == "story" and "money story" in view["prompt"] and "\"math\": a product" in view["prompt"]
    assert "Friend 4" in view["prompt"]  # the example story is in the prompt
    story = _story()
    del story["format"]
    view = pw_write.answer("one", json.dumps(story), tmp_path)
    assert view["drafts"][0]["problems"] == [] and pw_write.load("one", tmp_path)["drafts"][0]["format"] == "story"
    assert "money stories" in pw_write.llm_prompt(5, tmp_path, format="story")
    assert "money stories" not in pw_write.llm_prompt(5, tmp_path)
    added = pw_write.llm_add(json.dumps({"topics": [{"question": "Same $50, two kids and a stand: who made $5,000?", "pillar": "business tricks", "format": "story"}]}), tmp_path)
    assert added["added"] and [t for t in pw_write.topics(tmp_path) if t.get("format") == "story"][-1]["question"].startswith("Same $50")


def test_the_story_scenes_draw_the_cast_the_math_and_the_bars_on_the_narrator_s_words():
    pw_render.use_style()
    bw_render.use_layout("portrait")
    script = _story()
    spec = bw_render.Spec(scenes=[bw_render.Scene(kind=s["kind"], narration=s["narration"], opts={k: v for k, v in s.items() if k not in ("kind", "narration")})
                                  for s in script["scenes"]], words=[])
    for k, scene in enumerate(spec.scenes):
        scene.start, scene.end = k * 20.0, (k + 1) * 20.0
    spec.words = [bw_render.Word("$18,250", 52.0, 52.6)]  # said late in scene 2
    pw_render.restyle(spec)
    r = pw_render.PwRenderer(spec)
    cast, step, bars, ask = r.drawn[0], r.drawn[2], r.drawn[5], r.drawn[6]
    assert cast.late >= 4 * 9 and all(it["at"] < 0 for it in cast.items[:cast.late])  # four people on the first frame
    # the bean-head cast lives (modules.pw_cast): four people, their faces on the first frame, the moods on the figures
    assert [st["k"] for st in cast.cast] == [0, 1, 2, 3] and all(st["kind"] == "person" for st in cast.cast)
    moods = {st["k"]: [m["expr"] for m in pw_cast.resolve(st["moods"], cast.items)] for st in cast.cast}
    assert moods[0][-1] == "sad" and moods[3][-1] == "grin" and moods[1][-1] == moods[2][-1] == "shocked"
    who = next(st for st in step.cast if st["kind"] == "person")
    assert [m["expr"] for m in pw_cast.resolve(who["moods"], step.items)] == ["think", "smile", "grin", "smug"]
    assert [st["kind"] for st in bars.cast] == ["head"] * 4 and [st["kind"] for st in ask.cast] == ["head"] * 4
    frame = cast.frame(0.0)
    assert frame.getpixel((int(cast.cast[0]["dx"] + 62 * cast.cast[0]["scale"]), int(cast.cast[0]["dy"] + 56 * cast.cast[0]["scale"])))[:3] == pw_render.INK  # a pupil
    result = next(it for it in step.items if it.get("count"))
    assert result["text"] == "$18,250" and 51.5 <= result["at"] <= 52.0  # the result lands on the spoken number
    assert [it for it in step.items if it["kind"] == "write" and it["text"] == "LEASING"]
    assert sum(1 for it in bars.items if it.get("under")) == 4 and ask.items[0]["text"] == "Which friend are you?"
    frame = r.scene_frame(2, 53.0)
    assert frame.size == (bw_render.W, bw_render.H)
    assert r.scene_frame(7, 145.0).size == frame.size


def test_a_story_thumbnail_shows_a_person_and_the_two_results(tmp_path):
    from modules import pw_thumb

    w = pw_thumb.words(_story())
    assert w["draw"] == "person" and w["old"] == "$3,650" and w["now"] == "$511,000" and w["old_label"] == "Friend 1"
    assert pw_thumb.make(_story(), tmp_path / "t.jpg").is_file()


def test_the_story_hook_keeps_moving_and_the_story_stays_short():
    from modules import pw_write

    script = _story()
    assert pw_channel.STORY_SECONDS[1] <= 85 and pw_write.STORY_WORDS[1] <= 190
    long = json.loads(json.dumps(script))
    long["scenes"][1]["narration"] += " " + "And then he kept on pushing that cart every single day of the year." * 2
    assert any("a step is" in p for p in pw_write.problems_of(long))
    quiet = json.loads(json.dumps(script))
    for person in quiet["scenes"][0]["cast"]["people"]:
        person.pop("result", None)
    assert any("result" in p for p in pw_write.problems_of(quiet))
    pw_render.use_style()
    bw_render.use_layout("portrait")
    scene = bw_render.Scene(kind="cast", narration=script["scenes"][0]["narration"], opts={"cast": script["scenes"][0]["cast"]})
    scene.start, scene.end = 0.0, 10.0
    spec = bw_render.Spec(scenes=[scene], words=[bw_render.Word("$500.", 0.1, 0.9), bw_render.Word("$3,650.", 3.2, 4.0),
                                                 bw_render.Word("$511,000.", 5.9, 7.0)])
    board = pw_render.Drawn(scene, first=True)
    board.retime(scene, spec.words)
    small = next(it for it in board.items if it["kind"] == "write" and it["text"] == "$3,650")
    big = next(it for it in board.items if it["kind"] == "write" and it["text"] == "$511,000")
    assert 3.0 <= small["at"] <= 4.5 and 5.7 <= big["at"] <= 7.5  # written as they are said
    sway = [it for it in board.items if "bob" in it]
    a, b = board.frame(1.0), board.frame(1.3)
    assert sway and a.tobytes() != b.tobytes()  # never a still frame


def test_a_fix_answer_with_only_the_broken_script_keeps_the_ones_that_passed(tmp_path):
    from modules import pw_write

    pw_write.start("week", count=3, out_dir=tmp_path)
    good = _script()
    scripts = [{**good, "title": f"Why number {k}?", "question": f"Why {k}?"} for k in range(3)]
    bad = json.loads(json.dumps(scripts[1]))
    bad["scenes"] = bad["scenes"][1:]  # no opening: breaks the rules
    view = pw_write.answer("week", json.dumps({"scripts": [scripts[0], bad, scripts[2]]}), tmp_path)
    assert view["step"] == "fix" and [bool(d["problems"]) for d in view["drafts"]] == [False, True, False]
    prompt = view["prompt"]
    assert "ONLY the script below" in prompt and "Why number 1?" in prompt
    assert "Why number 0?" not in prompt.split("The script to fix")[1]  # the passing ones aren't sent back
    view = pw_write.answer("week", json.dumps({"scripts": [scripts[1]]}), tmp_path)  # just the fixed one
    assert [d["title"] for d in view["drafts"]] == ["Why number 0?", "Why number 1?", "Why number 2?"]
    assert view["step"] == "facts" and not any(d["problems"] for d in view["drafts"])
    with pytest.raises(pw_write.WriteError):  # nothing left to fix: two pasted can't replace one
        session = pw_write.load("week", tmp_path)
        session["step"], session["problems"] = "fix", {"1": [], "2": ["x"], "3": []}
        pw_write._save(session, tmp_path)
        pw_write.answer("week", json.dumps({"scripts": [{**good, "title": "New a"}, {**good, "title": "New b"}]}), tmp_path)


def test_the_videos_move_faster_tight_pauses_sounds_and_no_elevator_music(tmp_path, monkeypatch):
    import numpy as np

    from modules import bw_sfx

    rate = pw_channel.RATE
    talk, hush = np.full(rate // 10, 0.3, dtype=np.float32), np.zeros(rate, dtype=np.float32)
    samples = np.concatenate([talk, hush, talk, np.zeros(rate // 10, dtype=np.float32), talk])
    found = pw_channel.cuts(samples, 0.2)
    assert len(found) == 1  # the 1 s pause is cut down to 0.2 s; the 0.1 s breath stays
    dropped = [(a / rate, b / rate) for a, b in found]
    assert sum(b - a for a, b in dropped) == pytest.approx(0.8, abs=0.02)
    assert pw_channel.shifted(1.2, dropped) == pytest.approx(0.4, abs=0.02) and pw_channel.shifted(0.05, dropped) == 0.05

    folder = tmp_path / "music" / "funny"
    folder.mkdir(parents=True)
    for name in ("Elevator Muzak", "Elevator Bossa Nova", "Quirky Plucks"):
        (folder / f"{name}.mp3").write_bytes(b"")
    monkeypatch.setattr(pw_channel, "PROJECT_ROOT", tmp_path)
    assert {pw_channel.pick_music(seed).stem for seed in range(20)} == {"Quirky Plucks"}

    pw_render.use_style()
    bw_render.use_layout("portrait")
    script = _story()
    spec = bw_render.Spec(scenes=[bw_render.Scene(kind=s["kind"], narration=s["narration"], opts={k: v for k, v in s.items() if k not in ("kind", "narration")})
                                  for s in script["scenes"]], words=[])
    for k, scene in enumerate(spec.scenes):
        scene.start, scene.end = k * 20.0, (k + 1) * 20.0
    pw_render.restyle(spec)
    pw_render.PwRenderer(spec)
    kinds = {name for _, name, _ in spec.sfx_extra}
    assert {"whoosh", "pop", "marker", "cash"} <= kinds and "ding" not in kinds
    assert all(at >= 0.05 for at, _, _ in spec.sfx_extra)  # nothing for what is already drawn on the first frame
    assert bw_sfx.sfx_track(spec, tmp_path / "sfx.wav").stat().st_size > 1000


def test_videos_end_on_their_last_real_scene_with_no_follow_line():
    from modules import pw_write

    story = _story()
    assert story["scenes"][-1]["kind"] == "follow"  # an older script still has one
    cut = pw_channel.without_follow(story)
    assert cut["scenes"][-1]["kind"] == "ask" and "Follow for" not in pw_channel.narration(cut)
    assert not [p for p in pw_write.problems_of(story) if "follow" in p.lower()]  # still queues and makes
    assert not [p for p in pw_write.problems_of(cut) if "follow" in p.lower() or "last scene" in p]
    assert '"follow"' not in pw_write.story_rules() and "the ask is the LAST scene" in pw_write.story_rules()


def test_a_named_cast_is_asked_by_its_own_word_and_a_parable_lists_no_sources():
    from modules import pw_write

    story = json.loads((pw_channel.SCRIPTS_DIR.parent / "examples" / "002-coffee-cart-story.json").read_text(encoding="utf-8"))
    assert pw_write.cast_noun(story) == "friend"
    vendors = json.loads(json.dumps(story))
    for k, p in enumerate(vendors["scenes"][0]["cast"]["people"], 1):
        p["name"] = f"Vendor {k}"
    assert pw_write.cast_noun(vendors) == "vendor"
    assert any("Which vendor are you" in p for p in pw_write.format_problems(vendors))
    for s in vendors["scenes"]:
        if s["kind"] == "ask":
            s["ask"]["question"], s["narration"] = "Which vendor are you?", "Which vendor are you? Tell me below."
    vendors["comment"] = "Which vendor are you: 1, 2, 3 or 4? 👇"
    assert not any("Which vendor" in p for p in pw_write.format_problems(vendors))
    assert "Sources:" not in pw_channel.description({**story, "sources": []})
    assert "Sources:" in pw_channel.description(_script())


def test_a_slot_made_or_emptied_from_the_tab_is_marked_so_the_bot_leaves_it(tmp_path):
    from datetime import datetime

    from trendclip.web import pricetagwhy as web

    settings = _settings()
    job = pw_channel.job_for(settings, datetime(2026, 11, 4).date(), "23:00")
    fake = type("S", (), {"output_dir": tmp_path})()
    web._mark_made(job, fake)
    sent = json.loads((tmp_path / "discord_state.json").read_text())["sent"]
    assert job.make_key in sent
    at = datetime(2026, 11, 4, 22, 22, tzinfo=settings.tz)
    assert all(j.key != job.key for j in pw_channel.due_makes(settings, at, set(sent), tmp_path))


def test_a_world_story_passes_the_rules_and_draws_its_hook_on_the_first_frame(monkeypatch):
    """The full-colour world look (2026-11-08): the example true story is a valid script, every scene is drawn,
    the hook's words are on frame 0, and things said later pop in when the voice says them."""
    import json

    from modules import bw_render, pw_world

    monkeypatch.setattr(pw_world, "ensure_fonts", lambda folder=None: None)
    script = json.loads((pw_channel.PROJECT_ROOT / "content/pricetagwhy/examples/003-spanx-world.json").read_text())
    assert pw_channel.is_world(script) and pw_channel.check_script(script) == []
    assert pw_channel.limits(script) == pw_channel.WORLD_SECONDS
    bad = {**script, "scenes": [{**script["scenes"][0], "head": []}, *script["scenes"][1:]]}
    assert any("hook" in p for p in pw_channel.check_script(bad))
    words, t = [], 0.0
    for s in script["scenes"]:
        for w in s["narration"].split():
            words.append(bw_render.Word(w, t, t + 0.25))
            t += 0.3
    spec = bw_render.spec_from_dict({**script, "seed": 3}, words)
    bw_render.align(spec)
    r = pw_world.WorldRenderer(spec)
    assert r.frame(0.0).size == (bw_render.W, bw_render.H)
    assert all(it["at"] < 0 for it in r.items[0] if it["type"] == "head")  # the hook is there on the first frame
    door = next(it for it in r.items[1] if it["type"] == "prop")
    said = next(w.start for w in words if w.text == "door")
    assert door["at"] == said
    for i, scene in enumerate(spec.scenes):
        assert r.frame((scene.start + scene.end) / 2).mode == "RGB"
    assert any(name == "stamp" for _, name, _ in spec.sfx_extra) and any(name == "cash" for _, name, _ in spec.sfx_extra)


def test_true_world_stories_have_their_own_prompts_topics_and_rules(tmp_path):
    """The third format (2026-11-08): the owner's LLM writes true stories for the world look from its own prompt,
    their ideas are kept apart, and a script that names what can't be drawn or cued is sent back."""
    import json

    from modules import pw_write

    example = json.loads(pw_write.WORLD_EXAMPLE.read_text())
    assert pw_write.problems_of(example) == []
    assert '"format": "world"' in pw_write.world_rules() and "BUT THEN" in pw_write.world_rules()
    assert "TRUE money stories" in pw_write.llm_prompt(5, tmp_path, [], "world")
    pw_write.add_topic("How did $5,000 become Spanx?", "business tricks", tmp_path, "world")
    pw_write.add_topic("Why is popcorn so expensive at the movies?", "everyday prices", tmp_path)
    assert [t["question"] for t in pw_write.of_format(pw_write.topics(tmp_path), "world")] == ["How did $5,000 become Spanx?"]
    assert all(t.get("format") != "world" for t in pw_write.of_format(pw_write.topics(tmp_path), "why"))
    session = pw_write.start("one", "", out_dir=tmp_path, format="world")
    assert session["format"] == "world" and "true story for this idea" in session["prompt"]
    bad = json.loads(json.dumps(example))
    bad["scenes"][1]["prop"]["draw"] = "rocket"
    bad["scenes"][1]["tags"][0]["cue"] = "banana"
    bad["scenes"][0]["head"][-1].pop("from")
    found = " | ".join(pw_write.problems_of(bad))
    assert "prop.draw" in found and "'banana'" in found and "races up" in found
    done = pw_write.answer("one", json.dumps({"scripts": [{k: v for k, v in example.items() if k != "format"}]}), tmp_path)
    assert done["step"] == "facts" and pw_write.load("one", tmp_path)["drafts"][0]["format"] == "world"


def test_a_video_being_made_shows_its_progress_from_its_log(tmp_path):
    """The Plan shows "Making now… 69%" like Borderline Why (the owner, 2026-11-08), for the bot's makes too."""
    import os
    import time

    root = pw_channel.out_root(tmp_path)
    root.mkdir(parents=True, exist_ok=True)
    log = root / "2026-11-08_2300.log"
    log.write_text("[5%] Recording the narration\n[] Recording the narration (Kokoro af_heart)\n[69%] Drawing the maps: frame 1200/1741\n")
    p = pw_channel.make_progress("2026-11-08_2300", tmp_path)
    assert p == {"fraction": 0.69, "message": "Drawing: frame 1200/1741"}
    assert pw_channel.making_now(tmp_path) == ["2026-11-08_2300"]
    old = time.time() - pw_channel.PROGRESS_STALE - 5
    os.utime(log, (old, old))
    assert pw_channel.make_progress("2026-11-08_2300", tmp_path) is None  # a make that went quiet died
    log.write_text("[95%] Video ready\n[100%] Video ready\n{\"key\": \"2026-11-08_2300\"}\n")
    assert pw_channel.make_progress("2026-11-08_2300", tmp_path) is None and pw_channel.making_now(tmp_path) == []


def test_write_it_now_on_a_true_story_topic_uses_the_true_story_prompt(tmp_path):
    """The IKEA topic (a true story) went through "Write it now" with the why prompt and came out a 27 s whiteboard
    video (2026-11-08): a topic's own format wins."""
    from modules import pw_write

    pw_write.add_topic("How did a 17-year-old selling matches build IKEA?", "business tricks", tmp_path, "world")
    session = pw_write.start("one", "How did a 17-year-old selling matches build IKEA?", out_dir=tmp_path)
    assert session["format"] == "world" and "true story for this idea" in session["prompt"]


def test_the_dashboard_writes_true_stories_only(tmp_path):
    """The owner (2026-11-08): no more old-format videos from the tool. New ideas, the Write tabs and the ideas prompt
    default to the true story; an LLM answer that forgets the format still lands as one."""
    from trendclip.web import pricetagwhy as web

    from modules import pw_write

    assert web.StartRequest().format == "world" and web.TopicRequest(question="How did X?").format == "world"
    done = pw_write.llm_add('{"topics": [{"question": "How did a $500 loan become Crocs?", "pillar": "business tricks"}]}', tmp_path)
    assert done["added"] and pw_write.topics(tmp_path)[-1]["format"] == "world"
    html = (pw_channel.PROJECT_ROOT / "trendclip/web/static/index.html").read_text()
    assert '<option value="why">❓ why' not in html and 'id="pwAddFormat" value="world"' in html
