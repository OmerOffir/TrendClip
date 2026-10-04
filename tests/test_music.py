import subprocess
from types import SimpleNamespace

import pytest

from trendclip import music, script_writer, shorts, video_assembler
from trendclip.config import Settings


def make_settings(tmp_path, **kw):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output",
                    music_channels=["@NCS", "@Chill"], **kw)


class FakeYouTube:
    channels = {
        "@NCS": {"id": "UCncs", "title": "NoCopyrightSounds", "uploads": "UUncs"},
        "@Chill": {"id": "UCchill", "title": "Chillhop Music", "uploads": "UUchill"},
    }
    uploads = {
        "UUncs": [("aaaaaaaaaaa", "Alan Song - Light [NCS Release]"), ("bbbbbbbbbbb", "NCS: The Best of 2025 (Mix)"),
                  ("ccccccccccc", "NCS 24/7 Live Stream")],
        "UUchill": [("ddddddddddd", "Sleepy Fish - Rain"), ("eeeeeeeeeee", "lofi beats to study to"),
                    ("fffffffffff", "Chillhop Essentials Summer 2024")],
    }

    def __init__(self):
        self.calls = 0

    def resolve_channel(self, ref):
        self.calls += 1
        return self.channels.get(ref)

    def list_uploads(self, playlist, limit):
        return [{"video_id": v, "title": t} for v, t in self.uploads[playlist]]


def test_library_keeps_single_tracks_and_caches(tmp_path):
    settings = make_settings(tmp_path)
    yt = FakeYouTube()
    lib = music.MusicLibrary(settings, client_factory=lambda: yt)
    assert [t.video_id for t in lib.tracks()] == ["aaaaaaaaaaa", "ddddddddddd"]
    assert lib.sources() == [
        {"id": "UCncs", "title": "NoCopyrightSounds", "mood": "energetic EDM", "tracks": 1},
        {"id": "UCchill", "title": "Chillhop Music", "mood": "chill lo-fi", "tracks": 1},
    ]
    assert [t.video_id for t in lib.candidates("UCchill")] == ["ddddddddddd"]

    again = music.MusicLibrary(settings, client_factory=lambda: pytest.fail("should use the cache"))
    assert len(again.tracks()) == 2 and again.channels[0]["id"] == "UCncs"


def test_pick_track_skips_failures_and_excluded(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    lib = music.MusicLibrary(settings, client_factory=FakeYouTube)
    monkeypatch.setattr(music, "get_library", lambda s: lib)
    tried = []

    def fake_download(s, track):
        tried.append(track.video_id)
        if track.video_id == "aaaaaaaaaaa":
            raise music.MusicError("too long")
        return music.DownloadedTrack(video_id=track.video_id, title=track.title, channel_title=track.channel_title,
                                     url=track.url, filename=f"{track.video_id}.m4a", credit="c")

    monkeypatch.setattr(music, "download_track", fake_download)
    assert music.pick_track(settings, "random").video_id == "ddddddddddd"
    with pytest.raises(music.MusicError, match="No tracks"):
        music.pick_track(settings, "UCchill", exclude={"ddddddddddd"})


def test_credit_lines():
    assert "Music provided by NoCopyrightSounds" in music.credit_for("Song", "NoCopyrightSounds", "u")
    assert music.credit_for("Rain", "Chillhop Music", "u").startswith("Music: Rain - provided by Chillhop Music")


def test_resolve_music_respects_none_and_pinned_track(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.music_dir.mkdir(parents=True)
    pinned = music.DownloadedTrack(video_id="ddddddddddd", title="Rain", channel_title="Chillhop Music",
                                   url="u", filename="ddddddddddd.m4a", credit="c")
    (settings.music_dir / "ddddddddddd.m4a").write_bytes(b"a")
    (settings.music_dir / "ddddddddddd.json").write_text(pinned.model_dump_json())
    monkeypatch.setattr(music, "pick_track", lambda s, src: pytest.fail("should not pick"))

    def req(**kw):
        return shorts.RenderRequest(clip="c.mp4", game="g", script="one two three", **kw)

    noop = lambda f, m: None  # noqa: E731
    assert shorts.resolve_music(settings, req(music_source="none", music_track="ddddddddddd"), noop) is None
    assert shorts.resolve_music(settings, req(music_source="random", music_track="ddddddddddd"), noop).title == "Rain"


def test_assemble_mixes_music_for_real(tmp_path):
    try:
        ffmpeg = video_assembler.ffmpeg_with_libass()
    except video_assembler.AssemblyError:
        pytest.skip("no ffmpeg with libass")
    gen = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
    subprocess.run(gen + ["testsrc=size=640x360:rate=30:duration=2", "-pix_fmt", "yuv420p", str(tmp_path / "bg.mp4")], check=True)
    subprocess.run(gen + ["sine=frequency=300:duration=2", "-ac", "1", str(tmp_path / "voice.mp3")], check=True)
    subprocess.run(gen + ["sine=frequency=500:duration=1", "-ac", "2", str(tmp_path / "song.m4a")], check=True)

    out = video_assembler.assemble_video(
        tmp_path / "bg.mp4", tmp_path / "voice.mp3", [{"word": "hello", "start": 0, "end": 1.5}],
        tmp_path / "out.mp4", music=tmp_path / "song.m4a", music_volume=0.2, preset="ultrafast")

    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
    assert "1080x1920" in probe and "stereo" in probe
    assert abs(video_assembler.media_duration(out) - 2.0) < 0.2  # short music is looped, voice sets the length


def test_music_filter_keeps_voice_level():
    graph = video_assembler.music_filter(30, 0.16)
    assert "pan=stereo|c0=c0|c1=c0" in graph and "volume=0.160" in graph
    assert "afade=t=out:st=28.50" in graph and "normalize=0" in graph


def test_story_mode_prompt_is_not_about_the_clip():
    clip = script_writer.build_prompt("Minecraft", 30, ["Top 10 jumps"], "funny", watched=False, mode="story")
    narrate = script_writer.build_prompt("Minecraft", 30, ["Top 10 jumps"], "funny", watched=True, mode="clip")
    assert clip != narrate
    assert "NOT about the game or the footage" in clip and "Top 10 jumps" not in clip
    assert "funny" in clip


def test_story_mode_skips_watching(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.backgrounds_dir.mkdir(parents=True)
    clip = settings.backgrounds_dir / "m.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(script_writer, "_preview_clip", lambda c, s: pytest.fail("story mode must not upload"))
    seen = {}

    class Models:
        def generate_content(self, model, contents, config):
            seen["contents"], seen["config"] = contents, config
            return SimpleNamespace(parsed=script_writer.GeminiShort(
                on_screen="A story about a haunted vending machine.", hook="My vending machine is haunted.",
                script="My vending machine is haunted. It gives me exact change. Every time.",
                title="Haunted", description="d", hashtags=["story"]), text="")

    result = script_writer.write_script(settings, "Minecraft", clip, mode="story", watch_clip=True,
                                        client=SimpleNamespace(models=Models(), files=None))
    assert result.mode == "story" and not result.watched_clip
    assert all(isinstance(c, str) for c in seen["contents"])
