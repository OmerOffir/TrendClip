"""Dark Outliers video: beat timing, the Ken Burns background, the whole build (placeholder pictures, a silent
voice) and the tab's video endpoints."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from db import repository
from modules import scene_images, script_gen, video_gen
from trendclip.video_assembler import Word, estimate_word_timings, ffmpeg_with_libass, media_duration


def _script(story_id=1, scenes=3):
    return script_gen.Script(
        story_id=story_id, title="The Ship Nobody Steered", hook_text="A ship sailed for days with no one aboard.",
        estimated_duration_seconds=40, total_word_count=120,
        scenes=[script_gen.Scene(scene_id=i, narration=f"Scene {i} tells one more unsettling fact about the ship.",
                                 visual_keywords=["foggy sea", "old ship"], image_prompt=f"cinematic photo of scene {i}",
                                 overlay_text=f"BEAT {i}") for i in range(1, scenes + 1)])


def test_split_beats_uses_spoken_words_and_pads_short_beats():
    beats = video_gen.beats_of(_script(scenes=2))
    text = video_gen.spoken_text(_script(scenes=2))
    # "GTA-6"-style splits and merged tokens still land in the right beat: match by letters
    words = estimate_word_timings(text, 12.0)
    hook_words = len(_script().hook_text.split())
    timed = video_gen.split_beats(beats, words, tail=2.0)
    assert [b.label for b in timed] == ["hook", "scene 1", "scene 2"]
    assert timed[0].start == 0.0 and timed[0].end == words[hook_words].start
    assert timed[1].end == timed[2].start and timed[2].end == round(words[-1].end + 2.0, 3)

    rushed = [Word("A", 0, 0.1), Word("ship", 0.1, 0.2), Word("sailed", 0.2, 0.3), Word("for", 0.3, 0.35),
              Word("days", 0.35, 0.4), Word("with", 0.4, 0.45), Word("no", 0.45, 0.5), Word("one", 0.5, 0.55),
              Word("aboard.", 0.55, 0.6)] + [Word(w, 0.6 + i * 0.3, 0.9 + i * 0.3) for i, w in enumerate(text.split()[9:])]
    timed = video_gen.split_beats(video_gen.beats_of(_script(scenes=2)), rushed, tail=2.0)
    assert timed[0].end == video_gen.MIN_BEAT == timed[1].start  # the 0.6 s hook is stretched to a visible beat


def test_cover_text_from_gemini_or_the_hook():
    sc = _script()
    assert video_gen.cover_text(sc) == "A SHIP SAILED FOR"
    sc.hook_text = "Five bombers vanished, and nobody knows why."
    assert video_gen.cover_text(sc) == "FIVE BOMBERS VANISHED"
    sc.thumbnail_text = "5 planes. no wreckage"
    assert video_gen.cover_text(sc) == "5 PLANES. NO WRECKAGE"


def test_cta_line_joins_the_last_beat():
    beats = video_gen.beats_of(_script(scenes=2), "Follow for more dark stories.")
    assert beats[-1].text.endswith("ship. Follow for more dark stories.") and beats[0].text == _script().hook_text
    assert video_gen.beats_of(_script(scenes=2), "")[-1].text.endswith("ship.")
    assert video_gen.off("none") and video_gen.off(" ") and not video_gen.off("Follow")


def test_cta_sticker_stays_until_the_end():
    from trendclip.config import get_settings

    words = estimate_word_timings("One fact. Another fact here. Follow for more dark stories.", 6.0)
    overlays, used = video_gen.cta_sticker(get_settings(), words, [], True, True)
    assert used == ["built-in subscribe button"] and len(overlays) == 1
    assert overlays[0].start < words[-4].start + 0.01 and overlays[0].end == words[-1].end + 1.2


def test_photo_slots_and_description():
    assert video_gen.photo_slots(9, 2) == [1, 5] and video_gen.photo_slots(9, 1) == [1]
    assert video_gen.photo_slots(1, 2) == [] and video_gen.photo_slots(3, 2) == [1]
    text = video_gen.description_for(_script(), {"url": "https://en.wikipedia.org/wiki/Mary_Celeste", "source_name": "wikipedia/Ghost ships"},
                                     ["Photo: X (CC BY-SA 4.0), Wikimedia Commons", "Photo: X (CC BY-SA 4.0), Wikimedia Commons"],
                                     "Music: Sombra")
    assert text.startswith("A ship sailed for days with no one aboard.\n\nSource: https://en.wikipedia.org/wiki/Mary_Celeste\n")
    assert text.count("Photo: X") == 1 and "Music: Sombra" in text and text.endswith("#shorts")


def test_commons_file_picker_and_article_title():
    assert scene_images.article_title("https://en.wikipedia.org/wiki/Mary_Celeste#Crew") == ("en", "Mary Celeste")
    assert scene_images.article_title("https://en.m.wikipedia.org/wiki/Dyatlov_Pass_incident") == ("en", "Dyatlov Pass incident")
    assert scene_images.article_title("https://reddit.com/r/x") is None
    pages = [
        {"title": "File:Mary Celeste engraving.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 1200, "height": 800,
         "thumburl": "https://u/thumb.jpg", "url": "https://u/full.jpg", "descriptionurl": "https://commons/x",
         "extmetadata": {"Artist": {"value": "<a href='x'>Rudolph Ruzicka</a>"}, "LicenseShortName": {"value": "Public domain"}}}]},
        {"title": "File:Flag of Canada.svg", "imageinfo": [{"mime": "image/svg+xml", "width": 1200, "height": 600}]},
        {"title": "File:Location map.png", "imageinfo": [{"mime": "image/png", "width": 1200, "height": 900}]},
        {"title": "File:Tiny.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 200, "height": 150}]},
        {"title": "File:Harbour.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 2000, "height": 1500, "thumburl": "https://u/h.jpg"}]},
        {"title": "File:Atlantic hurricanes 1851-2020.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 3000, "height": 1800,
         "thumburl": "https://u/t.jpg", "extmetadata": {"Categories": {"value": "Self-published work|Cumulative Atlantic hurricane trackmaps"}}}]},
        {"title": "File:Routes.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 3000, "height": 1800, "thumburl": "https://u/r.jpg",
         "extmetadata": {"ImageDescription": {"value": "This map shows the <b>routes</b> of the ships."}}}]},
        {"title": "File:Deck.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 2200, "height": 1800, "thumburl": "https://u/d.jpg",
         "extmetadata": {"Categories": {"value": "Photographs by John|Flagships of the Royal Navy|Charter boats"}}}]},
    ]
    picked = scene_images.pick_commons_files(pages, limit=2)
    assert [f.title for f in picked] == ["File:Deck.jpg", "File:Harbour.jpg"]
    assert [f.title for f in scene_images.pick_commons_files(pages, limit=9)][-1] == "File:Mary Celeste engraving.jpg"
    assert scene_images.pick_commons_files(pages, limit=9)[-1].credit == "Photo: Rudolph Ruzicka (Public domain), Wikimedia Commons"
    assert picked[0].credit == "Photo: Wikimedia Commons"


def test_grade_keeps_size_and_darkens_edges(tmp_path):
    from PIL import Image

    src = tmp_path / "p.jpg"
    Image.new("RGB", (180, 320), (200, 120, 90)).save(src)
    out = scene_images.graded_copy(src)
    img = Image.open(out)
    assert out.name == "graded_p.jpg" and img.size == (180, 320)
    centre, corner = img.getpixel((90, 160)), img.getpixel((2, 2))
    assert sum(corner) < sum(centre) * 0.7  # the vignette
    assert centre[2] > 90 * 0.9 and centre[0] < 200  # colder and less saturated than the source


def test_backend_order(monkeypatch):
    monkeypatch.setattr(scene_images.LocalSDXL, "available", staticmethod(lambda: False))
    monkeypatch.setattr(scene_images.GeminiImages, "available", staticmethod(lambda: True))
    assert scene_images.backend_order("auto") == ["gemini", "placeholder"]
    assert scene_images.backend_order("placeholder") == ["placeholder"]
    assert scene_images.backend_order("local") == ["local", "placeholder"]
    with pytest.raises(scene_images.ImageError):
        scene_images.backend_order("dalle")


def test_make_pictures_falls_back_to_the_next_backend(tmp_path, monkeypatch):
    class Broken:
        name = "gemini"

        def make(self, prompt, path, seed, size=scene_images.SIZE):
            raise RuntimeError("quota")

    monkeypatch.setitem(scene_images.BACKENDS, "gemini", Broken)
    pictures, used = scene_images.make_pictures(["a", "b"], tmp_path, "gemini", size=(90, 160))
    assert [p.source for p in pictures] == ["placeholder", "placeholder"] and used.startswith("placeholder (gemini: quota")
    assert all(p.path.is_file() for p in pictures)


def _silent_voice(text, output_mp3, voice, rate, **kwargs):
    seconds = 4.0
    subprocess.run([ffmpeg_with_libass(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", str(seconds), "-codec:a", "libmp3lame", "-qscale:a", "6", str(output_mp3)], check=True)
    words = estimate_word_timings(text, seconds)
    output_mp3.with_suffix(".words.json").write_text(json.dumps([{"word": w.text, "start": w.start, "end": w.end} for w in words]))
    return words


def test_make_video_end_to_end(tmp_path, monkeypatch):
    from trendclip import voiceover
    from trendclip.config import get_settings

    db = tmp_path / "bank.db"
    repository.init_db(db)
    repository.save_story("wikipedia_1", "wikipedia/Ghost ships", "Mary Celeste", "text " * 60,
                          "https://en.wikipedia.org/wiki/Mary_Celeste", 90, db_path=db)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "story_1.json").write_text(_script().model_dump_json())
    monkeypatch.setattr(voiceover, "synthesize", _silent_voice)
    monkeypatch.setattr(scene_images, "wikipedia_photos", lambda url, out_dir, **k: [])  # no network in tests
    monkeypatch.setenv("DA_MUSIC", "none")
    monkeypatch.setenv("DA_IMAGE_BACKEND", "placeholder")
    monkeypatch.setattr(video_gen, "FPS", 12)
    messages = []

    info = video_gen.make_video(1, lambda f, m: messages.append(m), out_dir=tmp_path / "out", scripts_dir=scripts,
                                db_path=db, settings=get_settings(), announce=False)
    video = tmp_path / "out" / "story_1.mp4"
    assert video.is_file() and info["filename"] == "story_1.mp4" and 4.8 <= media_duration(video) <= 6.5
    assert [b["label"] for b in info["beats"]] == ["hook", "scene 1", "scene 2", "scene 3"]
    assert info["beats"][-1]["text"].endswith("Follow for more dark stories.") and info["cta_line"] == "Follow for more dark stories."
    assert info["stickers"] == ["built-in subscribe button"] and info["end_card"] == "Follow for more · dark stories"
    assert json.loads((tmp_path / "out" / "story_1" / "voice.words.json").read_text())[-1]["word"] == "stories."
    assert info["pictures"] == "placeholder" and info["photos"] == [] and info["music"] is None
    assert (tmp_path / "out" / "story_1" / "pictures" / "beat_4.jpg").is_file()
    assert (tmp_path / "out" / "story_1" / "pictures" / "graded_beat_4.jpg").is_file()
    assert (tmp_path / "out" / "story_1" / "pictures" / "background.mp4").is_file()
    assert json.loads((tmp_path / "out" / "story_1.json").read_text())["title"] == "The Ship Nobody Steered"
    ass = (tmp_path / "out" / "story_1.ass").read_text()
    assert ",EndCard," in ass and "Follow for more\\Ndark stories" in ass and ",Title," in ass and "BEAT 3" in ass
    assert "0:00:00.00,0:00:00.80,Cover,,0,0,0,,{\\pos(540,960)\\fad(0,160)}A SHIP SAILED\\NFOR" in ass
    assert "{\\fad(0,300)}THE SHIP NOBODY\\NSTEERED" in ass and info["cover_text"] == "A SHIP SAILED FOR"
    assert repository.get_story(1, db)["status"] == "USED" and "story_1.mp4" in repository.get_story(1, db)["note"]
    assert any("Drawing picture 4 of 4" in m for m in messages) and messages[-1] == "Done"
    assert video_gen.existing_video_ids(tmp_path / "out") == [1]

    with pytest.raises(video_gen.VideoGenError, match="No script"):
        video_gen.make_video(2, out_dir=tmp_path / "out", scripts_dir=scripts, db_path=db, settings=get_settings(), announce=False)


def test_video_api(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    monkeypatch.setattr(repository, "DB_PATH", tmp_path / "bank.db")
    repository.init_db()
    repository.save_story("gemini_b", "gemini", "Ship case", "the ship text", "", 70)
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", tmp_path / "scripts")
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", tmp_path / "videos")
    client = TestClient(web.app)

    assert client.post("/api/darkarchives/stories/1/video").status_code == 400  # no script yet
    assert client.post("/api/darkarchives/stories/9/video").status_code == 404
    assert client.get("/api/darkarchives/stories/1/video").status_code == 404
    assert client.get("/api/darkarchives/stories/1/video/info").status_code == 404
    assert client.get("/api/darkarchives/stories").json()["videos"] == []

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "story_1.json").write_text(_script().model_dump_json())

    def fake_make(story_id, progress):
        progress(0.5, "Drawing")
        (tmp_path / "videos").mkdir(exist_ok=True)
        (tmp_path / "videos" / "story_1.mp4").write_bytes(b"\x00" * 16)
        (tmp_path / "videos" / "story_1.json").write_text(json.dumps({"story_id": 1, "title": "T"}))
        return {"story_id": 1, "title": "T"}

    monkeypatch.setattr(video_gen, "make_video", fake_make)
    assert client.post("/api/darkarchives/stories/1/video").status_code == 202
    for _ in range(50):
        job = client.get("/api/darkarchives/video-job").json()
        if not job["running"]:
            break
        time.sleep(0.05)
    assert job["result"] == {"story_id": 1, "title": "T"} and job["story_id"] == 1 and job["finished_at"]
    assert client.get("/api/darkarchives/stories/1/video/info").json() == {"story_id": 1, "title": "T"}
    assert client.get("/api/darkarchives/stories/1/video").headers["content-type"] == "video/mp4"
    assert client.get("/api/darkarchives/stories").json()["videos"] == [1]
    assert client.get("/api/darkarchives/stories/1/video/pictures/beat_1.jpg").status_code == 404
    assert client.get("/api/darkarchives/stories/1/video/pictures/../x.jpg").status_code == 404


def test_channel_handle_banner_and_description(tmp_path, monkeypatch):
    monkeypatch.setattr(video_gen, "CHANNEL_DIR", tmp_path / "darkarchives")
    assert video_gen.channel_handle() == "" and video_gen.default_end_card() == "Follow for more · dark stories"
    (tmp_path / "darkarchives").mkdir()
    (tmp_path / "darkarchives" / "channel.env").write_text("CHANNEL_HANDLE=@DarkArchives-j1l\nSTATS_YOUTUBE=\n")
    assert video_gen.channel_handle() == "@DarkArchives-j1l"
    assert video_gen.default_end_card() == "@DarkArchives-j1l · Follow for more"
    text = video_gen.description_for(_script(), {"url": "https://x", "source_name": "s"}, [])
    assert text.split("\n\n")[1] == "Follow @DarkArchives-j1l for more dark stories."


def test_da_channel_upload_records_the_video(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from modules import da_channel

    monkeypatch.setattr(video_gen, "CHANNEL_DIR", tmp_path / "darkarchives")
    (tmp_path / "darkarchives").mkdir()
    (tmp_path / "darkarchives" / "channel.env").write_text("CHANNEL_HANDLE=@DarkArchives-j1l\n")
    out = tmp_path / "videos"
    out.mkdir()
    (out / "story_3.mp4").write_bytes(b"\x00" * 32)
    (out / "story_3.json").write_text(json.dumps({"story_id": 3, "title": "The Ship", "description": "Hook.\n\nSource: x",
                                                   "hashtags": ["darkarchives", "shorts"]}))

    class Insert:
        def __init__(self, body):
            self.body = body

        def next_chunk(self):
            return None, {"id": "vid123", "status": {"privacyStatus": self.body["status"]["privacyStatus"]}}

    bodies = []
    youtube = SimpleNamespace(videos=lambda: SimpleNamespace(insert=lambda **kw: bodies.append(kw["body"]) or Insert(kw["body"])))
    with pytest.raises(Exception, match="Connect the Dark Outliers"):
        da_channel.upload(3, out_dir=out)
    record = da_channel.upload(3, "unlisted", youtube=youtube, out_dir=out)
    assert record["video_id"] == "vid123" and record["url"] == "https://youtube.com/shorts/vid123" and record["privacy"] == "unlisted"
    assert bodies[0]["snippet"]["categoryId"] == "24" and bodies[0]["snippet"]["title"] == "The Ship #shorts"
    assert bodies[0]["snippet"]["description"] == "Hook.\n\nFollow @DarkArchives-j1l for more dark stories.\n\nSource: x"
    assert json.loads((out / "story_3.json").read_text())["uploads"]["youtube"]["video_id"] == "vid123"


def test_youtube_endpoints_for_darkarchives(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from modules import da_channel
    from trendclip import publish
    from trendclip.web import app as web

    monkeypatch.setattr(publish, "CLIENT_SECRETS", tmp_path / "client_secret.json")
    monkeypatch.setattr(da_channel, "TOKEN", tmp_path / "da" / "token_youtube.json")
    monkeypatch.setattr(da_channel, "DIR", tmp_path / "da")
    monkeypatch.setattr(video_gen, "CHANNEL_DIR", tmp_path / "da")
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", tmp_path / "videos")
    client = TestClient(web.app)
    status = client.get("/api/darkarchives/youtube/status").json()
    assert status == {"client_secret": False, "connected": False, "channel": None, "handle": "", "folder": False}
    assert client.post("/api/darkarchives/youtube/connect").status_code == 400
    assert client.post("/api/darkarchives/stories/1/upload", json={"privacy": "private"}).status_code == 404
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "story_1.mp4").write_bytes(b"\x00")
    resp = client.post("/api/darkarchives/stories/1/upload", json={"privacy": "private"})
    assert resp.status_code == 400 and "Connect" in resp.json()["detail"]
    assert client.post("/api/darkarchives/stories/1/upload", json={"privacy": "everyone"}).status_code == 422
    assert client.post("/api/darkarchives/youtube/logout").json() == {"connected": False}
    assert client.get("/api/darkarchives/upload-job").json() == {"running": False}


def test_discord_announcements(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from modules import da_channel
    from trendclip import hub
    from trendclip.config import get_settings

    monkeypatch.setattr(da_channel, "DIR", tmp_path / "da")
    settings = get_settings().with_overrides(discord_bot_token="tok")
    out = tmp_path / "videos"
    out.mkdir()
    (out / "story_5.mp4").write_bytes(b"\x00" * 64)
    info = {"title": "The Ship", "duration_seconds": 64.9, "pictures": "local", "photos": ["a", "b"], "music": {"title": "Sombra"}}
    assert da_channel.announce_video(5, info, settings, out) is None  # no channel folder: nothing to post to

    (tmp_path / "da").mkdir()
    (tmp_path / "da" / "channel.env").write_text("CHANNEL_HANDLE=@DarkArchives-j1l\nDISCORD_CHANNEL_ID=1563997738666819707\n")
    posts = []

    class FakeRest:
        def __init__(self, settings):
            self.channel = None

        def post(self, content, file=None, components=None):
            posts.append((self.channel, content, file, components))
            return "msg1"

    monkeypatch.setattr(hub, "Rest", FakeRest)
    assert da_channel.announce_video(5, info, settings, out) == "msg1"
    channel, content, file, components = posts[-1]
    assert channel == 1563997738666819707 and file == ("story_5.mp4", b"\x00" * 64)
    assert content.startswith("🎬 **The Ship** · 65 s · pictures: local · 2 photos · 🎵 Sombra\nStory 5:")
    assert [b["custom_id"] for b in components[0]["components"]] == ["tc:daplan:5", "tc:daup:5", "tc:dadel:5"]
    assert da_channel.video_buttons(5, item_id="abc")[0]["components"][0] == {"type": 2, "style": 4, "label": "🚫 Don't post this", "custom_id": "tc:daveto:abc"}
    up = {"url": "https://youtube.com/shorts/x"}
    assert [b.get("custom_id") or b.get("url") for b in da_channel.video_buttons(5, uploaded=up)[0]["components"]] == ["https://youtube.com/shorts/x", "tc:dadel:5"]
    assert da_channel.announce_upload(5, info, {"privacy": "private", "url": "https://youtube.com/shorts/x", "studio_url": "https://studio/x"}, settings) == "msg1"
    assert posts[-1][1] == "⬆️ **The Ship** is on YouTube (private): https://youtube.com/shorts/x"
    assert [b["url"] for b in posts[-1][3][0]["components"]] == ["https://youtube.com/shorts/x", "https://studio/x"]
    assert da_channel.announce_video(5, info, settings.model_copy(update={"discord_bot_token": None}), out) is None


def test_first_comment_after_a_public_upload(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from modules import da_channel

    monkeypatch.setattr(video_gen, "CHANNEL_DIR", tmp_path / "darkarchives")
    (tmp_path / "darkarchives").mkdir()
    (tmp_path / "darkarchives" / "channel.env").write_text("CHANNEL_HANDLE=@DarkOutliersOfficial\n")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", scripts)
    out = tmp_path / "videos"
    out.mkdir()
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", out)
    sc = _script(story_id=3)
    sc.scenes[-1].narration = "Not one was found. So who steered her for ten days?"
    (scripts / "story_3.json").write_text(sc.model_dump_json())
    (out / "story_3.mp4").write_bytes(b"\x00" * 32)
    (out / "story_3.json").write_text(json.dumps({"story_id": 3, "title": "The Ship", "description": "Hook.", "hashtags": ["shorts"]}))
    assert da_channel.comment_text(3) == "So who steered her for ten days?\n\nFollow @DarkOutliersOfficial for more dark stories."
    assert da_channel.comment_text(99).startswith("What do you think really happened?")

    class Insert:
        def __init__(self, body):
            self.body = body

        def next_chunk(self):
            return None, {"id": "vid9", "status": {"privacyStatus": self.body["status"]["privacyStatus"]}}

        def execute(self):
            return {"id": "cmt1"}

    bodies = []
    threads = SimpleNamespace(insert=lambda **kw: bodies.append(kw["body"]) or Insert(kw["body"]))
    youtube = SimpleNamespace(videos=lambda: SimpleNamespace(insert=lambda **kw: Insert(kw["body"])), commentThreads=lambda: threads)
    record = da_channel.upload(3, "public", youtube=youtube, out_dir=out)
    assert record["comment"]["comment_id"] == "cmt1" and bodies[0]["snippet"]["videoId"] == "vid9"
    assert bodies[0]["snippet"]["topLevelComment"]["snippet"]["textOriginal"].startswith("So who steered her")
    assert json.loads((out / "story_3.json").read_text())["uploads"]["youtube"]["comment"]["comment_id"] == "cmt1"
    bodies.clear()
    (out / "story_3.json").write_text(json.dumps({"story_id": 3, "title": "The Ship", "description": "Hook.", "hashtags": ["shorts"]}))
    assert "comment" not in da_channel.upload(3, "private", youtube=youtube, out_dir=out) and bodies == []
