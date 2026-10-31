"""Pricetag Why: the script rules, the price-tag opening and the board lines in the channel's colours, the tab's API."""

import json

from fastapi.testclient import TestClient

from modules import bw_brand, bw_render, pw_channel, pw_render


def _script() -> dict:
    return json.loads((pw_channel.SCRIPTS_DIR / "001-arizona-99c.json").read_text(encoding="utf-8"))


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
