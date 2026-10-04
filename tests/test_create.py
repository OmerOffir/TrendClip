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


def test_write_script_needs_a_key(tmp_path, monkeypatch):
    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s=None: None)
    with pytest.raises(script_writer.ScriptError, match="GEMINI_API_KEY"):
        script_writer.write_script(make_settings(tmp_path), "Minecraft")


def test_api_errors_are_explained():
    err = SimpleNamespace(code=429, message="Resource exhausted")
    assert "quota" in str(script_writer._explain(err, "m"))
    assert "GEMINI_MODEL" in str(script_writer._explain(SimpleNamespace(code=404, message="x"), "m"))


# ---------------------------------------------------------------- render + files


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
