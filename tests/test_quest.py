"""The long adventure "Can You Beat Minecraft?": its script, its hearts, its place in the dashboard."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trendclip import quest, shorts
from trendclip.config import Settings
from trendclip.web import app as web


def settings(tmp_path: Path) -> Settings:
    return Settings(output_dir=tmp_path / "out", assets_dir=tmp_path / "assets", stickers_dir=tmp_path / "stickers",
                    youtube_api_key="x", gemini_api_key="")


def test_the_narrator_starts_with_ten_hearts_and_reaches_the_end_alive():
    hearts = quest.run()
    assert hearts[0] == 10 and len(hearts) == len(quest.STOPS) + 1 == 8
    assert all(0 < h <= 10 for h in hearts)  # a dead narrator has no video to finish


def test_every_stop_has_words_and_the_narrators_own_option():
    for stop, lines in zip(quest.STOPS, quest.scripts()):
        assert lines and all(text.strip() for _, text in lines)
        assert stop.mine() in stop.options()
    assert [stop.N for stop in quest.STOPS] == list(range(1, 8))


def test_the_script_fits_a_short():
    words = sum(len(text.split()) for lines in quest.scripts() for _, text in lines)
    assert words < 470  # about 2.7 words a second: 470 words would pass the three minutes


def test_missing_pictures_stop_the_video_before_anything_is_drawn(tmp_path):
    s = settings(tmp_path)
    assert quest.missing(s) == sorted(quest.needed())
    with pytest.raises(ValueError, match="No picture of"):
        quest.create(s)


def test_the_video_waits_in_upload_with_its_texts(tmp_path, monkeypatch):
    s = settings(tmp_path)
    monkeypatch.setattr(quest, "missing", lambda settings: [])
    monkeypatch.setattr("trendclip.video_assembler.media_duration", lambda path: 154.66)

    def fake(settings, output, progress, only=0):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        quest.record(settings, output)

    monkeypatch.setattr(quest, "_make", fake)
    short = quest.create(s)
    assert short.filename.startswith(quest.PREFIX) and short.title == quest.TITLE and short.mode == "gamer"
    assert short.duration_seconds < shorts.SHORTS_MAX_SECONDS
    assert "Comment your hearts" in short.script and f"with {quest.run()[-1]}" in short.pinned_comment
    assert [v.filename for v in quest.videos(s)] == [short.filename]
    assert shorts.list_shorts(s)[0].filename == short.filename


def test_a_failed_drawing_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(quest.sys, "executable", "false")
    with pytest.raises(RuntimeError, match="could not be made"):
        quest._make(settings(tmp_path), tmp_path / "x.mp4", lambda frac, msg: None)


def test_the_dashboard_shows_the_quest_and_refuses_without_pictures(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "_base_settings", lambda: settings(tmp_path))
    client = TestClient(web.app)
    view = client.get("/api/gamer/quest").json()
    assert view["title"] == quest.TITLE and len(view["stops"]) == 7 and view["missing"] and view["videos"] == []
    assert client.post("/api/gamer/quest").status_code == 400
    assert [s["stop"] for s in view["solos"]] == [1, 2, 4, 5, 6, 7]
    assert client.post("/api/gamer/quest", json={"stop": 3}).status_code == 400  # the village is no Short of its own
    page = client.get("/").text
    assert "gqMake" in page and "gqMakeOne" in page


def test_a_stop_alone_dares_first_and_asks_for_the_hearts():
    for stop in quest.solos():
        lines = quest.scripts(stop.N)[0]
        roles = [role for role, _ in lines]
        assert roles[0] == "title" and {"threat", "pause", "mine", "next"} <= set(roles)
        assert lines[0][1] == stop.HOOK and lines[-1][1] == "Comment your hearts."
        words = " ".join(text for _, text in lines)
        assert "Next stop" not in words and "Last stop" not in words and "Stop " not in words
        assert len(words.split()) < 90  # about half a minute at most
        assert 0 < stop.after() <= 10
    with pytest.raises(ValueError, match="not a Short of its own"):
        quest.scripts(3)


def test_a_stop_alone_waits_in_upload_under_its_own_title(tmp_path, monkeypatch):
    s = settings(tmp_path)
    monkeypatch.setattr(quest, "missing", lambda settings: [])
    monkeypatch.setattr("trendclip.video_assembler.media_duration", lambda path: 19.88)
    seen = []

    def fake(settings, output, progress, only=0):
        seen.append(only)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        quest.record(settings, output, None, only)

    monkeypatch.setattr(quest, "_make", fake)
    short = quest.create(s, only=7)
    assert seen == [7] and short.filename.startswith("quest_end_") and short.title == quest.End.SOLO
    assert short.title_card == "PAUSE TO FIGHT THE ENDER DRAGON" and "Next stop" not in short.script
    assert f"I kept {quest.End.after()}" in short.pinned_comment and quest.TITLE in short.description
