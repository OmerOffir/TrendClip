from types import SimpleNamespace

import pytest

from trendclip import music, script_writer, shorts, video_assembler
from trendclip.config import Settings


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output",
                    music_library_dir=tmp_path / "music")


def add_tracks(settings, folder, *names):
    d = settings.music_library_dir / folder
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"audio")
    return d


def test_mood_helpers():
    assert music.normalize_mood("Dramatic_Suspense") == "dramatic_suspense"
    assert music.normalize_mood("chill") == "chill_lofi" and music.normalize_mood("rock") is None
    assert music.guess_mood("I accidentally waved at the wrong person. So awkward.") == "funny_quirky"
    assert music.guess_mood("Suddenly I heard footsteps. The door was locked.") == "dramatic_suspense"
    assert music.guess_mood("Three tips for building a base.") == "chill_lofi"
    assert music.clamp_volume(0.4) == 0.15 and music.clamp_volume(0.05) == 0.12 and music.clamp_volume(0.14) == 0.14


def test_pick_by_mood_uses_the_local_folder(tmp_path):
    settings = make_settings(tmp_path)
    d = add_tracks(settings, "funny", "Goofy_Walk.mp3", "boing.m4a", "notes.txt", ".hidden.mp3")
    (d / "Goofy_Walk.txt").write_text("Music: Goofy Walk by Someone (CC BY 4.0)")
    picked = {music.pick_by_mood(settings, "funny_quirky").filename for _ in range(30)}
    assert picked == {"funny/Goofy_Walk.mp3", "funny/boing.m4a"}

    t = music.pick_by_mood(settings, "funny_quirky", exclude={"local:funny/boing.m4a"})
    assert t.local and t.mood == "funny_quirky" and t.video_id == "local:funny/Goofy_Walk.mp3"
    assert t.title == "Goofy Walk" and t.credit.startswith("Music: Goofy Walk")
    assert music.track_path(settings, t) == d / "Goofy_Walk.mp3"
    assert music.downloaded(settings, t.video_id).filename == t.filename
    assert music.downloaded(settings, "local:funny/missing.mp3") is None
    assert music.downloaded(settings, "local:../secrets/x.mp3") is None
    assert music.mood_counts(settings)["funny_quirky"]["tracks"] == 2


def test_empty_mood_folder_falls_back_to_a_youtube_channel(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    lib = SimpleNamespace(channels=[{"id": "UCncs", "title": "NoCopyrightSounds"},
                                    {"id": "UCchill", "title": "Chillhop Music"}], tracks=lambda: ["x"])
    monkeypatch.setattr(music, "get_library", lambda s: lib)
    asked = []

    def fake_pick(settings, source, exclude=None):
        asked.append(source)
        return music.DownloadedTrack(video_id="aaaaaaaaaaa", title="Lofi", channel_title="Chillhop Music",
                                     url="u", filename="aaaaaaaaaaa.m4a", credit="c")

    monkeypatch.setattr(music, "pick_track", fake_pick)
    t = music.pick_by_mood(settings, "chill_lofi")
    assert asked == ["UCchill"] and t.fallback and t.mood == "chill_lofi" and not t.local


def test_render_picks_music_by_mood(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    add_tracks(settings, "dramatic", "tension.mp3")
    req = shorts.RenderRequest(clip="c.mp4", game="g", script="Suddenly the lights went out. Footsteps.",
                               music_source="mood")
    track = shorts.resolve_music(settings, req, lambda f, m: None)  # no mood given: guessed from the script
    assert track.filename == "dramatic/tension.mp3"
    pinned = shorts.RenderRequest(clip="c.mp4", game="g", script="hi there you", music_source="mood",
                                  music_track="local:dramatic/tension.mp3", music_mood="chill_lofi")
    assert shorts.resolve_music(settings, pinned, lambda f, m: None).video_id == "local:dramatic/tension.mp3"
    assert shorts.music_volume(settings, 0.5) == 0.15 and shorts.music_volume(settings, None) == 0.14
    with pytest.raises(ValueError):
        shorts.RenderRequest(clip="c.mp4", game="g", script="hi there", music_track="local:../../etc/passwd")


def test_gemini_classifies_the_mood(tmp_path):
    class Models:
        def generate_content(self, model, contents, config):
            return SimpleNamespace(parsed=script_writer.GeminiShort(
                on_screen="x", hook="x", script="I walked into the wrong wedding. Everyone stared at me.",
                title="t", description="d", hashtags=[], music_mood="Funny_Quirky"), text="")

    result = script_writer.write_script(make_settings(tmp_path), "Minecraft", mode="story",
                                        client=SimpleNamespace(models=Models()))
    assert result.music_mood == "funny_quirky"
    assert "music_mood" in script_writer.SYSTEM_INSTRUCTION


def test_mixer_levels_the_music_before_the_volume():
    f = video_assembler.music_filter(30, 0.14)
    assert f.index("loudnorm") < f.index("volume=0.140") and "sidechaincompress" in f
