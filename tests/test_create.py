import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trendclip import script_writer, shorts, voiceover
from trendclip.config import Settings
from trendclip.video_assembler import Word
from trendclip.video_downloader import BackgroundClip
from trendclip.web import app as web


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output")


def add_clip(settings, name="minecraft_youtube_1.mp4"):
    settings.backgrounds_dir.mkdir(parents=True, exist_ok=True)
    path = settings.backgrounds_dir / name
    path.write_bytes(b"mp4")
    clip = BackgroundClip(path=str(path), filename=name, source="youtube", game="Minecraft", query="q",
                          title="Minecraft Parkour", source_url="https://youtu.be/abc", author="Orbital",
                          license_note="credit")
    path.with_suffix(".json").write_text(clip.model_dump_json())
    return name


# ---------------------------------------------------------------- voiceover


def test_restore_punctuation_keeps_script_tokens():
    spoken = [Word("Watch", 0.1, 0.3), Word("closely", 0.3, 0.8), Word("this", 0.9, 1.0),
              Word("is", 1.0, 1.1), Word("GTA", 1.1, 1.4), Word("6", 1.4, 1.6), Word("now", 1.7, 2.0)]
    out = voiceover.restore_punctuation("Watch closely, this is GTA-6! Now.", spoken)
    assert [w.text for w in out] == ["Watch", "closely,", "this", "is", "GTA", "6!", "Now."]
    assert out[1].start == 0.3  # timings are untouched


def test_restore_punctuation_survives_unmatched_words():
    spoken = [Word("one", 0, 0.2), Word("hundred", 0.2, 0.5), Word("kills", 0.5, 0.9)]
    out = voiceover.restore_punctuation("100 kills.", spoken)
    assert [w.text for w in out] == ["one", "hundred", "kills."]


# ---------------------------------------------------------------- Gemini


class FakeFiles:
    def __init__(self):
        self.deleted = []
        self.calls = 0

    def upload(self, file, config):
        return SimpleNamespace(name="files/1", state=SimpleNamespace(name="PROCESSING"))

    def get(self, name):
        self.calls += 1
        return SimpleNamespace(name=name, state=SimpleNamespace(name="ACTIVE"))

    def delete(self, name):
        self.deleted.append(name)


class FakeModels:
    def __init__(self):
        self.contents = None

    def generate_content(self, model, contents, config):
        self.contents = contents
        return SimpleNamespace(parsed=script_writer.GeminiShort(
            on_screen="A player parkours over lava.",
            hook="This jump should be impossible.",
            script="This jump should be impossible. [beat] Watch the timing! #minecraft Follow for more.",
            title='"Impossible Minecraft Jump"',
            description="A clean parkour run.",
            hashtags=["minecraft", "#parkour", "#Minecraft"],
        ), text="")


def test_write_script_watches_clip_and_cleans_output(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    clip = settings.backgrounds_dir / add_clip(settings)
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(b"x")
    monkeypatch.setattr(script_writer, "_preview_clip", lambda c, s: preview)
    monkeypatch.setattr(script_writer.time, "sleep", lambda s: None)
    client = SimpleNamespace(files=FakeFiles(), models=FakeModels())

    result = script_writer.write_script(settings, "Minecraft", clip, trend_titles=["Top 10 jumps"],
                                        notes="funny", target_seconds=20, client=client)

    assert result.watched_clip and client.files.deleted == ["files/1"] and not preview.exists()
    prompt = client.models.contents[-1]
    assert "Game: Minecraft" in prompt and "Top 10 jumps" in prompt and "funny" in prompt and "20 seconds" in prompt
    assert result.script == "This jump should be impossible. Watch the timing! Follow for more."
    assert result.title == "Impossible Minecraft Jump"
    assert result.hashtags == ["#minecraft", "#parkour", "#shorts"]


def test_overloaded_model_retries_then_falls_back(monkeypatch):
    class Busy(Exception):
        def __init__(self, code):
            self.code = code

    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(model)
            if model == "main":
                raise Busy(503)
            return "ok"

    monkeypatch.setattr(script_writer.time, "sleep", lambda s: None)
    monkeypatch.setattr(script_writer, "FALLBACK_MODELS", ["backup"])
    client = SimpleNamespace(models=Models())
    resp, used = script_writer._generate_with_fallback(client, "main", [], None, lambda f, m: None, Busy)
    assert (resp, used) == ("ok", "backup")
    assert calls == ["main", "main", "main", "backup"]

    class Bad(Models):
        def generate_content(self, model, contents, config):
            raise Busy(400)

    with pytest.raises(Busy):
        script_writer._generate_with_fallback(SimpleNamespace(models=Bad()), "main", [], None, lambda f, m: None, Busy)


def test_script_never_outlasts_the_clip(tmp_path):
    long_story = " ".join(f"Sentence number {i} goes on and on." for i in range(60))  # 420 words
    short_story = "A short hook. " + " ".join(f"Beat {i} happens." for i in range(40))  # 123 words
    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(contents[-1])
            if config.response_schema is script_writer.Shortened:
                return SimpleNamespace(parsed=script_writer.Shortened(script=short_story), text="")
            return SimpleNamespace(parsed=script_writer.GeminiShort(
                on_screen="x", hook="x", script=long_story, title="t", description="d", hashtags=[]), text="")

    client = SimpleNamespace(models=Models())
    result = script_writer.write_script(make_settings(tmp_path), "Minecraft", mode="story", target_seconds=75,
                                        format="long", max_seconds=40, client=client)
    assert "about 40 seconds" in calls[0]  # the target was capped to the clip
    assert "AT MOST 132 words" in calls[1]  # 40 s * 3.3 words/s
    assert result.script == short_story and result.word_count <= 132

    class Stubborn(Models):
        def generate_content(self, model, contents, config):
            if config.response_schema is script_writer.Shortened:
                raise RuntimeError("busy")
            return super().generate_content(model, contents, config)

    result = script_writer.write_script(make_settings(tmp_path), "Minecraft", mode="story", target_seconds=30,
                                        max_seconds=60, client=SimpleNamespace(models=Stubborn()))
    assert result.word_count <= script_writer.max_words(30) + 5  # cut at a sentence end
    assert result.script.endswith("and on.")


def test_write_script_needs_a_key(tmp_path, monkeypatch):
    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s=None: None)
    with pytest.raises(script_writer.ScriptError, match="GEMINI_API_KEY"):
        script_writer.write_script(make_settings(tmp_path), "Minecraft")


def test_api_errors_are_explained():
    err = SimpleNamespace(code=429, message="Resource exhausted")
    assert "quota" in str(script_writer._explain(err, "m"))
    assert "GEMINI_MODEL" in str(script_writer._explain(SimpleNamespace(code=404, message="x"), "m"))


# ---------------------------------------------------------------- render + files


def test_edit_rerenders_in_place_and_reuses_the_voice(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    clip = add_clip(settings)
    synth, assembled = [], []

    def fake_synth(text, mp3, voice, rate):
        synth.append(text)
        mp3.write_bytes(text.encode())
        words = [Word("hi", 0, 0.5)]
        mp3.with_suffix(".words.json").write_text('[{"word": "hi", "start": 0, "end": 0.5}]')
        return words

    def fake_assemble(bg, voice, words, out, **kw):
        assembled.append(kw)
        out.write_bytes(f"video {len(assembled)}".encode())
        return out

    monkeypatch.setattr(shorts.voiceover, "synthesize", fake_synth)
    monkeypatch.setattr(shorts.video_assembler, "assemble_video", fake_assemble)
    monkeypatch.setattr(shorts.video_assembler, "media_duration", lambda p: 20.0)
    req = shorts.RenderRequest(clip=clip, game="Minecraft", script="Hello there friends", title="T",
                               description="D", hashtags=["#shorts"], title_card="OLD", output_name="my_short")
    short = shorts.render_short(settings, req, lambda f, m: None)
    shorts.set_ready(settings, short.filename, True)
    shorts.update_short(settings, short.filename, lambda s: s.uploads.update(youtube={"video_id": "x"}))

    saved, legacy = shorts.edit_settings(settings, shorts.get_short(settings, "my_short.mp4"))
    assert not legacy and saved["description"] == "D" and saved["title_card"] == "OLD" and saved["output_name"] is None

    # Only the title card changed: the voiceover is reused, the file name and ready / upload state stay.
    edit = shorts.RenderRequest(**{**saved, "title_card": "NEW"})
    new = shorts.rerender_short(settings, "my_short.mp4", edit, lambda f, m: None)
    assert synth == ["Hello there friends"] and assembled[-1]["title_card"] == "NEW"
    assert new.filename == "my_short.mp4" and new.ready and new.uploads == {"youtube": {"video_id": "x"}}
    assert new.edited_at and new.created_at == short.created_at and new.render_settings["title_card"] == "NEW"
    assert (settings.shorts_dir / "my_short.mp4").read_bytes() == b"video 2"
    assert sorted(p.name for p in settings.shorts_dir.iterdir()) == [
        "my_short.json", "my_short.mp3", "my_short.mp4", "my_short.words.json"]

    # A new script records a new voiceover; a failed render keeps the old video untouched.
    shorts.rerender_short(settings, "my_short.mp4", edit.model_copy(update={"script": "New words here"}),
                          lambda f, m: None)
    assert synth[-1] == "New words here"

    def broken(*a, **kw):
        raise RuntimeError("ffmpeg died")

    monkeypatch.setattr(shorts.video_assembler, "assemble_video", broken)
    with pytest.raises(RuntimeError):
        shorts.rerender_short(settings, "my_short.mp4", edit, lambda f, m: None)
    assert (settings.shorts_dir / "my_short.mp4").read_bytes() == b"video 3"
    assert not list(settings.shorts_dir.glob("*__edit*"))


def test_edit_settings_rebuilds_old_shorts(tmp_path):
    credit = "Gameplay footage: Orbital - https://youtu.be/abc\nMusic: Song - https://www.youtube.com/watch?v=aaaaaaaaaaa"
    old = shorts.ShortVideo(filename="a.mp4", game="Minecraft", title="T", description=f"Story text.\n\n{credit}",
                            hashtags=["#shorts"], script="Hi there you", voice="v", background="clip.mp4",
                            credit=credit, music_url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
                            popups=["cat"], title_card="CARD")
    req, legacy = shorts.edit_settings(make_settings(tmp_path), old)
    assert legacy and req["description"] == "Story text." and req["music_track"] == "aaaaaaaaaaa"
    assert req["music_source"] == "random" and req["popups"] == [{"word": "cat", "emoji": "", "query": "cat"}]


def test_render_short_writes_video_metadata_and_final_copy(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    clip = add_clip(settings)

    def fake_synth(text, mp3, voice, rate):
        mp3.write_bytes(b"mp3")
        return [Word("hi", 0, 0.5)]

    def fake_assemble(bg, voice, words, out, **kw):
        out.write_bytes(b"video")
        return out

    monkeypatch.setattr(shorts.voiceover, "synthesize", fake_synth)
    monkeypatch.setattr(shorts.video_assembler, "assemble_video", fake_assemble)
    monkeypatch.setattr(shorts.video_assembler, "media_duration", lambda p: 21.5)

    req = shorts.RenderRequest(clip=clip, game="Minecraft", script="Hello there friends", title="T",
                               description="D", hashtags=["#shorts"])
    short = shorts.render_short(settings, req, lambda f, m: None)

    assert short.duration_seconds == 21.5 and short.voice == settings.tts_voice
    assert "Gameplay footage: Orbital - https://youtu.be/abc" in short.description
    assert (settings.output_dir / "final_short.mp4").read_bytes() == b"video"
    assert [s.filename for s in shorts.list_shorts(settings)] == [short.filename]

    assert shorts.delete_short(settings, short.filename)
    assert list(settings.shorts_dir.iterdir()) == []
    with pytest.raises(ValueError):
        shorts.delete_short(settings, "../x.mp4")


def test_clip_path_rejects_traversal_and_latest(tmp_path):
    settings = make_settings(tmp_path)
    for bad in ("../.env", "latest_gameplay.mp4", "a.json"):
        with pytest.raises(ValueError):
            shorts.clip_path(settings, bad)
    with pytest.raises(FileNotFoundError):
        shorts.clip_path(settings, "missing.mp4")


# ---------------------------------------------------------------- web


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(web, "_base_settings", lambda: settings)
    monkeypatch.setattr(web, "_create", shorts.CreateManager())
    return TestClient(web.app), settings


def test_create_endpoints(client, monkeypatch):
    http, settings = client
    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s=None: None)
    status = http.get("/api/create/status").json()
    assert status["gemini_enabled"] is False and status["voice"] == settings.tts_voice

    clip = add_clip(settings)
    resp = http.post("/api/create/script", json={"clip": clip, "game": "Minecraft"})
    assert resp.status_code == 400 and "GEMINI_API_KEY" in resp.json()["detail"]
    assert http.post("/api/create/script", json={"clip": "nope.mp4", "game": "M"}).status_code == 404

    bad = http.post("/api/create/render", json={"clip": clip, "game": "M", "script": "hi there", "rate": "fast"})
    assert bad.status_code == 422

    monkeypatch.setattr(shorts, "render_short", lambda s, req, p: {"filename": "x.mp4", "title": req.title})
    job = http.post("/api/create/render", json={"clip": clip, "game": "M", "script": "hi there you", "title": "T"}).json()
    for _ in range(50):
        state = http.get(f"/api/create/jobs/{job['id']}").json()
        if state["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert state["status"] == "done" and state["result"]["title"] == "T"
    assert http.get("/api/shorts").json() == []
    assert http.delete("/api/shorts/none.mp4").status_code == 404
