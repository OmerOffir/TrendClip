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


def test_cta_sticker_stays_until_the_end(monkeypatch):
    from trendclip.config import get_settings

    words = estimate_word_timings("One fact. Another fact here.", 4.0)
    assert video_gen.cta_sticker(get_settings(), words, [], True, True) == ([], [])  # off by default: the Short ends on the story
    monkeypatch.setenv("DA_CTA_STICKER", "subscribe")

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
        {"title": "File:Mary Celeste engraving.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 1000, "height": 800,
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
    pages.append({"title": "File:Wide panorama.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 4000, "height": 2000,
                  "thumburl": "https://u/w.jpg"}]})  # too wide to fill a 9:16 frame
    picked = scene_images.pick_commons_files(pages, limit=2)
    assert [f.title for f in picked] == ["File:Deck.jpg", "File:Harbour.jpg"]
    assert [f.title for f in scene_images.pick_commons_files(pages, limit=9)][-1] == "File:Mary Celeste engraving.jpg"
    assert scene_images.pick_commons_files(pages, limit=9)[-1].credit == "Photo: Rudolph Ruzicka (Public domain), Wikimedia Commons"
    assert picked[0].credit == "Photo: Wikimedia Commons"
    old = [{"title": "File:Woodcut.jpg", "imageinfo": [{"mime": "image/jpeg", "width": 1000, "height": 1000, "thumburl": "https://u/o.jpg",
            "extmetadata": {"Artist": {"value": "Hartmann Schedel (1440-1514)"}}}]}]
    assert scene_images.pick_commons_files(old, year=1980) == [] and len(scene_images.pick_commons_files(old, year=1490)) == 1
    assert len(scene_images.pick_commons_files(old)) == 1


def test_photos_fill_the_frame_and_the_hook_shows_the_subject():
    from PIL import Image

    framed = scene_images.frame_photo(Image.new("RGB", (1200, 900), (200, 30, 30)))
    assert framed.size == (1080, 1920) and framed.getpixel((5, 5)) == framed.getpixel((540, 960)) == (200, 30, 30)
    sc = _script()
    assert video_gen.beats_of(sc)[0].image_prompt == "cinematic photo of scene 1" + video_gen.HOOK_FRAMING
    sc.hook_image_prompt = "cinematic photo of an empty ship's deck, close-up"
    assert video_gen.beats_of(sc)[0].image_prompt == sc.hook_image_prompt
    sc.scenes[1].narration = "In December 1872 the ship was found."
    assert video_gen.story_year(sc) == 1872 and video_gen.story_year(_script()) is None


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


def _silent_voice(text, output_mp3, voice, rate, seconds=4.0, **kwargs):
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
    monkeypatch.setenv("DA_TTS_ENGINE", "edge")  # the test's fake voice sits on the Edge path
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
    assert info["beats"][-1]["text"].endswith("about the ship.") and info["cta_line"] == ""  # it ends on the story
    assert info["stickers"] == [] and info["end_card"] == ""
    assert json.loads((tmp_path / "out" / "story_1" / "voice.words.json").read_text())[-1]["word"] == "ship."
    assert info["pictures"] == "placeholder" and info["photos"] == [] and info["music"] is None
    assert (tmp_path / "out" / "story_1" / "pictures" / "beat_4.jpg").is_file()
    assert (tmp_path / "out" / "story_1" / "pictures" / "graded_beat_4.jpg").is_file()
    assert (tmp_path / "out" / "story_1" / "pictures" / "background.mp4").is_file()
    assert json.loads((tmp_path / "out" / "story_1.json").read_text())["title"] == "The Ship Nobody Steered"
    ass = (tmp_path / "out" / "story_1.ass").read_text()
    assert ",EndCard," not in ass and ",Title," in ass and "BEAT 3" in ass
    # an older script (no headline): the title in the box, no fragment under it
    assert ",Cover," not in ass and info["cover_text"] == ""
    assert "{\\fad(0,300)}THE SHIP NOBODY\\NSTEERED" in ass
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


def test_kokoro_words_and_sentences(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from trendclip import kokoro_voice as kv

    assert kv.paragraph("One fact.\nAnother one!  A third…\n\nLast") == "One fact. Another one! A third… Last"
    import numpy as np
    silent = np.zeros(24000, dtype=np.float32)
    assert kv.trim(silent)[1] == 0.0 and kv.trim(silent)[0].size == 24000  # all silence: kept as it is
    loud = silent.copy(); loud[12000:14400] = 0.5  # half a second of silence, 0.1 s of sound, then silence
    cut_audio, cut = kv.trim(loud)
    assert 0.40 <= cut <= 0.46 and cut_audio.size < 24000 and float(cut_audio.max()) == 0.5
    T = lambda text, s=None, e=None: SimpleNamespace(text=text, start_ts=s, end_ts=e)  # noqa: E731
    words = kv.words_from_tokens([T("In"), T("1956", 0.1, 0.6), T(","), T("doctors", 0.7, 1.1), T("infected", None, None), T("children", 1.6, 2.1), T(".")], offset=10.0)
    assert [(w.text, w.start, w.end) for w in words] == [("In", 10.0, 10.25), ("1956,", 10.1, 10.6), ("doctors", 10.7, 11.1),
                                                          ("infected", 11.1, 11.35), ("children.", 11.6, 12.1)]

    class FakePipe:
        def __call__(self, text, voice, speed):
            for line in text.split(". "):  # the model's own chunks: a few sentences each
                line = line if line.endswith(".") else line + "."
                n = len(line.split())
                toks = [T(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(line.rstrip(".?!").split())] + [T(line[-1])]
                audio = np.zeros(int(24000 * 0.4 * n), dtype=np.float32)
                audio[::2] = 0.3  # sound from the first sample: nothing to trim
                yield SimpleNamespace(audio=audio, tokens=toks, graphemes=line)

    monkeypatch.setattr(kv, "_pipeline", FakePipe())
    monkeypatch.setattr("trendclip.local_voice._mp3", lambda pcm, sr: b"MP3")
    words = kv.synthesize("Two words. Three more here.", tmp_path / "v.mp3", "bm_george", 1.0)
    assert [w.text for w in words] == ["Two", "words.", "Three", "more", "here."]
    assert words[2].start == round(0.8 + kv.PAUSE, 3)  # the second chunk starts after the first one's audio and the pause
    assert (tmp_path / "v.mp3").read_bytes() == b"MP3" and json.loads((tmp_path / "v.words.json").read_text())[-1]["word"] == "here."
    kv.unload()
    assert kv._pipeline is None


def test_headline_box_and_close_up_cuts():
    script = _script()
    assert video_gen.headline(script) == "The Ship Nobody Steered"  # an older script: its title
    script.thumbnail_text = "NO ONE"
    assert video_gen.headline(script) == "The Ship Nobody Steered"  # a 2-4 word fragment: still the title
    script.thumbnail_text = "a ship sailed with no one aboard"
    assert video_gen.headline(script) == "A SHIP SAILED WITH NO ONE ABOARD"

    assert video_gen.shot_cuts(2.5) == [] and video_gen.shot_cuts(4.5) == [2.25]
    assert video_gen.shot_cuts(6.2) == [3.1] and video_gen.shot_cuts(7.5) == [2.5, 5.0]
    assert video_gen.shot_cuts(20.0) == [6.667, 13.333]  # at most 3 shots
    assert video_gen.shot_cuts(9.0, every=0) == []
    f = video_gen.shots_filter("p1", "v1", [2.3], (1080, 1920))
    assert f.startswith("[p1]split=2[v1s0][v1s1];[v1s0]trim=start=0.000:end=2.300,setpts=PTS-STARTPTS[v1c0]")
    assert "trim=start=2.300,setpts=PTS-STARTPTS,crop=iw*0.7:ih*0.7:(iw-ow)/2:(ih-oh)*0.4,scale=1080:1920" in f
    assert f.endswith("[v1c0][v1c1]concat=n=2:v=1:a=0,fps=30,settb=1/30,format=yuv420p[v1]")


def test_a_long_beat_renders_with_its_close_up_cuts(tmp_path):
    from PIL import Image

    pics = []
    for i, colour in enumerate(("red", "blue", "green")):
        pics.append(tmp_path / f"p{i}.jpg")
        Image.new("RGB", (90, 160), colour).save(pics[-1])
    clip = tmp_path / "clip.mp4"  # a depth-camera clip stands in for the middle beat
    subprocess.run([ffmpeg_with_libass(), "-y", "-loglevel", "error", "-loop", "1", "-i", str(pics[1]), "-t", "6",
                    "-r", "12", "-pix_fmt", "yuv420p", str(clip)], check=True)
    beats = [video_gen.Beat(0, "hook", "x", "", "p", 0.0, 1.5), video_gen.Beat(1, "scene 1", "x", "", "p", 1.5, 6.5),
             video_gen.Beat(2, "scene 2", "x", "", "p", 6.5, 11.0)]  # a still, cut too
    out = video_gen.build_background([pics[0], clip, pics[2]], beats, tmp_path / "bg.mp4", size=(90, 160), fps=12, cut=2.0)
    assert out.is_file() and 11.0 <= media_duration(out) <= 12.5
