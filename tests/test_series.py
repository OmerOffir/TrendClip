from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trendclip import publish, script_writer, shorts, video_assembler
from trendclip.config import Settings
from trendclip.popups import Popup
from trendclip.script_writer import GeminiSeries, GeminiShort
from trendclip.web import app as web


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output")


@pytest.fixture(autouse=True)
def isolated_oauth(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "CLIENT_SECRETS", tmp_path / "client_secret.json")
    monkeypatch.setattr(publish, "TOKEN_FILE", tmp_path / "token_youtube.json")


def gemini_part(script, title, card, popups=()):
    return GeminiShort(on_screen="A ride goes wrong.", hook=script.split(".")[0], script=script, title=title,
                       description="A ride I will never forget.", hashtags=["#storytime", "#uber", "#part1", "#shorts"],
                       title_card=card, popups=list(popups))


def test_long_and_multi_prompts():
    long = script_writer.build_prompt("GTA V", 90, [], "", False, "story", "long")
    assert "LONG-FORM" in long and "289 to 302 words" in long
    multi = script_writer.build_prompt("GTA V", 45, [], "office", False, "story", "multi")
    words = int(45 * script_writer.WORDS_PER_SECOND) - script_writer.CTA_WORDS
    assert "TWO-PART SERIES" in multi and f"{words - 8} to {words + 5} words in EACH part" in multi
    assert "CLIFFHANGER" in multi and "office" in multi
    short = script_writer.build_prompt("GTA V", 30, [], "", False, "story")
    assert "LONG-FORM" not in short and "TWO-PART" not in short


def test_spoken_handle_and_ctas():
    assert script_writer.spoken_handle("@SideQuestLogic") == "Side Quest Logic"
    assert script_writer.spoken_handle("@side_quest.logic") == "side quest logic"
    p1, p2 = script_writer.series_ctas("@SideQuestLogic")
    assert p1 == "Sub to Side Quest Logic for Part 2 dropping tomorrow!"
    assert p2.startswith("Sub for daily side quest stories")


def test_write_series_adds_ctas_titles_and_tags(tmp_path):
    settings = make_settings(tmp_path)
    seen = {}

    class Models:
        def generate_content(self, model, contents, config):
            seen["prompt"], seen["schema"] = contents[0], config.response_schema
            return SimpleNamespace(parsed=GeminiSeries(
                story_name="the wrong uber's!",
                part1=gemini_part("I got in the wrong `car`. The driver locked the doors. Then he turned around. "
                                  "Follow for part two!", "I Got In The Wrong Uber (Part 1)", "WRONG CAR",
                                  [Popup(word="car", emoji="🚗", query="car")]),
                part2=gemini_part("So the driver turned around. It was my dad. He drives Uber now.",
                                  "It Was My DAD", "PLOT TWIST"),
                pinned_comment="Guess who the driver was 👀 #storytime https://spam.example",
            ), text="")

    series = script_writer.write_series(settings, "GTA V", target_seconds=45, handle="@SideQuestLogic",
                                        client=SimpleNamespace(models=Models()))
    assert seen["schema"] is GeminiSeries and "TWO-PART SERIES" in seen["prompt"]
    p1, p2 = series.parts
    assert series.story_name == "TheWrongUbers" and len(series.series_id) == 10
    assert "`" not in p1.script
    assert p1.script.endswith("turned around. Sub to Side Quest Logic for Part 2 dropping tomorrow!")
    assert "Follow for part two" not in p1.script  # Gemini's own CTA is replaced by ours
    assert p2.script.endswith("Sub for daily side quest stories and drop your crazy stories in the comments!")
    assert p1.title == "I Got In The Wrong Uber (Part 1)" and p2.title == "It Was My DAD (Part 2)"
    assert p1.hashtags[:2] == ["#part1", "#storytime"] and p1.hashtags[-1] == "#shorts"
    assert p2.hashtags[0] == "#part2" and "#part1" not in p2.hashtags
    assert p1.title_card == "PART 1: WRONG CAR" and p2.title_card == "PART 2: PLOT TWIST"
    assert p1.end_card == "PART 2 TOMORROW · SUB @SideQuestLogic"
    assert "Part 2 drops tomorrow" in p1.description and "Missed Part 1" in p2.description
    assert (p1.part, p1.parts_total, p1.format, p2.part) == (1, 2, "multi", 2)
    assert [p.word for p in p1.popups] == ["car"]
    assert "http" not in series.pinned_comment and "#" not in series.pinned_comment
    assert "@SideQuestLogic" in series.pinned_comment


def test_end_card_shows_over_the_last_seconds(tmp_path):
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("one two three four".split() * 5)]
    path = video_assembler.create_karaoke_ass_file(words, tmp_path / "s.ass", title_card="HI",
                                                   end_card="PART 2 TOMORROW · SUB @SideQuestLogic")
    text = path.read_text()
    end_line = next(line for line in text.splitlines() if ",EndCard," in line)
    assert end_line.startswith("Dialogue: 1,0:00:06.40,0:00:10.90,EndCard")  # last word ends at 9.9 s
    assert end_line.endswith(r"PART 2 TOMORROW\NSUB @SideQuestLogic")  # one phrase per line, case kept
    two = video_assembler.end_card_event("SUB @SideQuestLogic · DROP YOUR STORY BELOW", 0, 1)
    assert two.endswith(r"SUB @SideQuestLogic\NDROP YOUR STORY\NBELOW")
    assert "Style: EndCard" in text


def test_render_series_names_offsets_and_links(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.backgrounds_dir.mkdir(parents=True)
    (settings.backgrounds_dir / "clip.mp4").write_bytes(b"x")
    calls = []

    def fake_render(settings_, req, progress):
        calls.append(req)
        progress(0.5, "Rendering video")
        out = settings_.shorts_dir / f"{req.output_name}.mp4"
        out.write_bytes(b"x")
        short = shorts.ShortVideo(filename=out.name, game=req.game, title=req.title, description=req.description,
                                  hashtags=req.hashtags, script=req.script, voice="v", duration_seconds=47.0,
                                  background=req.clip, series_id=req.series_id, story_name=req.story_name,
                                  part=req.part, parts_total=req.parts_total, pinned_comment=req.pinned_comment,
                                  channel_handle=req.channel_handle)
        out.with_suffix(".json").write_text(short.model_dump_json())
        return short

    monkeypatch.setattr(shorts, "render_short", fake_render)
    part = {"script": "one two three four", "title": "T", "hashtags": ["#part1"]}
    req = shorts.SeriesRenderRequest(clip="clip.mp4", game="GTA V", story_name="The Wrong Uber",
                                     pinned_comment="Who was it?", channel_handle="@SideQuestLogic",
                                     parts=[part, {**part, "title": "T2"}])
    progress = []
    result = shorts.render_series(settings, req, lambda f, m: progress.append((f, m)))

    assert [s["filename"] for s in result["shorts"]] == ["TheWrongUber_Part1.mp4", "TheWrongUber_Part2.mp4"]
    assert [c.background_start for c in calls] == [0.0, 47.0]  # Part 2 continues the gameplay
    assert [c.music_start for c in calls] == [0.0, 47.0]
    assert [c.pinned_comment for c in calls] == ["Who was it?", ""]
    assert calls[0].series_id == calls[1].series_id == result["series_id"]
    assert (0.25, "Part 1/2: Rendering video") in progress and (0.75, "Part 2/2: Rendering video") in progress

    # A second story with the same name does not overwrite the first.
    assert shorts.series_name(settings, "The Wrong Uber", 2) == "TheWrongUber2"

    p1, p2 = shorts.series_parts(settings, result["series_id"])
    info = publish.series_info(settings, p1)
    assert info["links"] == "" and "Who was it?" in info["pinned_comment"]
    assert "youtube.com/@SideQuestLogic" in info["pinned_comment"]

    shorts.update_short(settings, p2.filename, lambda s: s.uploads.__setitem__(
        "youtube", {"video_id": "p2", "url": "https://youtube.com/shorts/p2"}))
    info = publish.series_info(settings, shorts.get_short(settings, p1.filename))
    assert info["links"] == "Part 2: https://youtube.com/shorts/p2"
    assert info["pinned_comment"].startswith("Part 2 is out 👉 https://youtube.com/shorts/p2")
    info2 = publish.series_info(settings, shorts.get_short(settings, p2.filename))
    assert info2["pinned_comment"].startswith("Missed Part 1? It's on @SideQuestLogic")

    texts = publish.template_texts(shorts.get_short(settings, p1.filename))
    assert "Part 2 drops tomorrow" in texts.instagram.caption and "Part 2 drops tomorrow" in texts.tiktok.caption


def test_post_comment(tmp_path):
    settings = make_settings(tmp_path)
    settings.shorts_dir.mkdir(parents=True)
    (settings.shorts_dir / "s.mp4").write_bytes(b"x")
    short = shorts.ShortVideo(filename="s.mp4", game="g", title="t", description="", hashtags=[], script="s",
                              voice="v", background="b", uploads={"youtube": {"video_id": "vid1", "privacy": "public"}})
    (settings.shorts_dir / "s.json").write_text(short.model_dump_json())
    sent = {}

    class Threads:
        def insert(self, part, body):
            sent["body"] = body
            return SimpleNamespace(execute=lambda: {"id": "c1"})

    record = publish.post_comment(settings, "s.mp4", " Part 2 tomorrow! ", youtube=SimpleNamespace(commentThreads=Threads))
    assert sent["body"]["snippet"]["videoId"] == "vid1"
    assert sent["body"]["snippet"]["topLevelComment"]["snippet"]["textOriginal"] == "Part 2 tomorrow!"
    assert record["comment_id"] == "c1" and "lc=c1" in record["url"]
    assert shorts.get_short(settings, "s.mp4").uploads["youtube"]["comment"]["comment_id"] == "c1"
    assert shorts.get_short(settings, "s.mp4").uploads["youtube"]["video_id"] == "vid1"

    with pytest.raises(publish.PublishError, match="Reconnect"):
        publish.post_comment(settings, "s.mp4", "hi")  # no token with the comment scope


def test_endpoints_accept_formats(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.backgrounds_dir.mkdir(parents=True)
    (settings.backgrounds_dir / "clip.mp4").write_bytes(b"x")
    monkeypatch.setattr(web, "_base_settings", lambda: settings)
    monkeypatch.setattr(web, "_create", shorts.CreateManager())
    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s=None: "key")
    submitted = {}
    monkeypatch.setattr(web._create, "submit_script", lambda *a: submitted.setdefault("args", a) and
                        shorts.CreateJob(id="j", kind="script", game="g"))
    http = TestClient(web.app)

    status = http.get("/api/create/status").json()
    assert status["channel_handle"] == "@SideQuestLogic"
    body = {"clip": "clip.mp4", "game": "GTA V", "target_seconds": 45, "format": "multi",
            "channel_handle": "@SideQuestLogic", "mode": "clip"}
    assert http.post("/api/create/script", json=body).status_code == 202
    assert submitted["args"][-3:] == ("story", "multi", "@SideQuestLogic")  # multi is always a story
    assert http.post("/api/create/script", json={**body, "channel_handle": "no-at"}).status_code == 422
    assert http.post("/api/create/script", json={**body, "format": "long", "target_seconds": 90}).status_code == 202

    one_part = {"clip": "clip.mp4", "game": "GTA V", "parts": [{"script": "one two three"}]}
    assert http.post("/api/create/render-series", json=one_part).status_code == 422
