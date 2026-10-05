import json

import pytest

from trendclip import video_downloader as vd
from trendclip.config import Settings
from trendclip.games import match_title, related_games


def make_settings(tmp_path, **kw):
    return Settings(youtube_api_key="k", assets_dir=tmp_path, **kw)


class FakeClient:
    """Stands in for YouTubeClient: two channels, a handful of uploads."""

    def __init__(self):
        self.resolved = []

    def resolve_channel(self, ref):
        self.resolved.append(ref)
        table = {
            "@NoCopyrightGameplays": {"id": "UC1", "title": "No Copyright Gameplay", "uploads": "UU1", "video_count": 3},
            "@OrbitalNCG": {"id": "UC2", "title": "Orbital - No Copyright Gameplay", "uploads": "UU2", "video_count": 2},
        }
        return table.get(ref)

    def list_uploads(self, playlist_id, max_items):
        return {
            "UU1": [
                {"video_id": "aaaaaaaaaaa", "title": "Minecraft Parkour Gameplay No Copyright"},
                {"video_id": "bbbbbbbbbbb", "title": "Minecraft Parkour No Copyright (Vertical)"},
                {"video_id": "ccccccccccc", "title": "GTA 5 Driving No Copyright Gameplay"},
            ],
            "UU2": [
                {"video_id": "ddddddddddd", "title": "Fortnite Zero Build No Copyright 4K"},
                {"video_id": "eeeeeeeeeee", "title": "Relaxing ASMR Cooking"},
            ],
        }[playlist_id]


def test_match_title_and_related_games():
    assert match_title("GTA 5 Driving No Copyright Gameplay") == ["GTA V / Online"]
    assert "GTA V / Online" in related_games("GTA VI")
    assert related_games("Unknown Game") == set()


def test_library_resolves_channels_once_and_finds_games(tmp_path):
    fake = FakeClient()
    settings = make_settings(tmp_path)
    lib = vd.NoCopyrightLibrary(settings, client_factory=lambda: fake)

    assert len(lib.videos()) == 5
    # "No Copyright Gameplay" duplicates the handle's title, so it is never looked up (no search cost).
    assert fake.resolved == ["@NoCopyrightGameplays", "@OrbitalNCG"]

    assert [v.video_id for v in lib.find("Minecraft")] == ["aaaaaaaaaaa"]
    assert [v.video_id for v in lib.find("Minecraft", "portrait")] == ["bbbbbbbbbbb"]
    assert [v.video_id for v in lib.find("GTA VI")] == ["ccccccccccc"]  # same franchise
    assert lib.find("Pokemon") == []
    assert lib.counts()["Fortnite"] == 1

    # Second instance loads from the disk cache without touching the API.
    fresh = FakeClient()
    cached = vd.NoCopyrightLibrary(settings, client_factory=lambda: fresh)
    assert len(cached.videos()) == 5 and fresh.resolved == []


def test_pick_pexels_file_prefers_orientation_and_resolution():
    video = {
        "video_files": [
            {"file_type": "video/mp4", "link": "a", "width": 1920, "height": 1080},
            {"file_type": "video/mp4", "link": "b", "width": 3840, "height": 2160},
            {"file_type": "video/mp4", "link": "c", "width": 1080, "height": 1920},
            {"file_type": "video/mp4", "link": "d", "width": 640, "height": 360},
        ]
    }
    assert vd._pick_pexels_file(video, "landscape")["link"] == "b"
    assert vd._pick_pexels_file(video, "portrait")["link"] == "c"
    assert vd._pick_pexels_file({"video_files": [video["video_files"][3]]}, "landscape") is None


def test_random_start_skips_intro_and_fits_clip():
    for _ in range(50):
        start = vd._random_start(600, 60)
        assert vd.INTRO_SKIP_SECONDS <= start <= 600 - 60 - vd.INTRO_SKIP_SECONDS
    assert vd._random_start(70, 60) == 0.0
    assert vd._random_start(None, 60) == 0.0


def test_download_background_youtube_path(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    lib = vd.NoCopyrightLibrary(settings, client_factory=FakeClient)
    monkeypatch.setattr(vd, "get_library", lambda s: lib)
    monkeypatch.setattr(vd, "probe_video", lambda p: {"width": 1920, "height": 1080, "duration_seconds": 30.0})

    def fake_download(url, target, clip_seconds, progress=vd._noop):
        target.write_bytes(b"mp4")
        return {"title": "Minecraft Parkour", "source_url": url, "author": "No Copyright Gameplay", "license_note": "ok"}

    monkeypatch.setattr(vd, "_download_from_youtube", fake_download)

    path = vd.get_background_video("Minecraft", settings=settings, sources=["pexels", "youtube"], clip_seconds=30)
    out = settings.backgrounds_dir
    assert path == str(out / "latest_gameplay.mp4")
    assert (out / "latest_gameplay.mp4").read_bytes() == b"mp4"
    meta = json.loads((out / "latest_gameplay.json").read_text())
    assert meta["source"] == "youtube" and meta["width"] == 1920
    assert len(vd.list_backgrounds(settings)) == 1


def test_delete_background_removes_clip_and_repoints_latest(tmp_path):
    settings = make_settings(tmp_path)
    out = settings.backgrounds_dir
    out.mkdir(parents=True)
    for name, when in (("old.mp4", "2026-10-01T00:00:00Z"), ("new.mp4", "2026-10-02T00:00:00Z")):
        clip = vd.BackgroundClip(path=str(out / name), filename=name, source="youtube", game="Minecraft",
                                 query="q", title=name, source_url="u", author="a", license_note="n",
                                 downloaded_at=when)
        (out / name).write_bytes(name.encode())
        (out / name).with_suffix(".json").write_text(clip.model_dump_json())
    (out / "latest_gameplay.mp4").write_bytes(b"new.mp4")
    (out / "latest_gameplay.json").write_text((out / "new.json").read_text())

    assert vd.delete_background(settings, "new.mp4") is True
    assert not (out / "new.mp4").exists() and not (out / "new.json").exists()
    assert (out / "latest_gameplay.mp4").read_bytes() == b"old.mp4"
    assert vd.delete_background(settings, "new.mp4") is False
    for bad in ("../.env", "latest_gameplay.mp4", "old.json"):
        with pytest.raises(ValueError):
            vd.delete_background(settings, bad)


def test_short_source_video_is_rejected(monkeypatch, tmp_path):
    class FakeYDL:
        def __init__(self, opts): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False): return {"duration": 25}

    class FakeModule:
        YoutubeDL = FakeYDL
        class utils:
            DownloadError = Exception

    monkeypatch.setattr(vd, "_yt_dlp", lambda: FakeModule)
    with pytest.raises(vd.DownloadError, match="shorter than the requested 60s"):
        vd._download_from_youtube("https://youtu.be/x", tmp_path / "x.mp4", clip_seconds=60)


def test_landscape_pick_is_cropped_when_vertical_is_asked(tmp_path, monkeypatch):
    import subprocess

    ffmpeg = vd.ffmpeg_path()
    if not ffmpeg:
        pytest.skip("no ffmpeg")
    settings = make_settings(tmp_path)
    lib = vd.NoCopyrightLibrary(settings, client_factory=FakeClient)
    monkeypatch.setattr(vd, "get_library", lambda s: lib)

    def fake_download(url, target, clip_seconds, progress):
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=640x360:rate=30:duration=1", "-pix_fmt", "yuv420p", str(target)], check=True)
        return {"title": "GTA", "source_url": url, "author": "NCG", "license_note": "credit"}

    monkeypatch.setattr(vd, "_download_from_youtube", fake_download)
    clip = vd.download_background("GTA V / Online", settings=settings, sources=["youtube"], orientation="portrait")
    assert (clip.width, clip.height) == (202, 360)
    assert vd.probe_video(settings.backgrounds_dir / vd.LATEST_FILENAME)["width"] == 202
    wide = vd.download_background("GTA V / Online", settings=settings, sources=["youtube"], orientation="landscape")
    assert (wide.width, wide.height) == (640, 360)


def test_parse_youtube_link():
    vid = ("video", "https://www.youtube.com/watch?v=MoGEOc3kcz8")
    for url in ("https://www.youtube.com/watch?v=MoGEOc3kcz8", "youtu.be/MoGEOc3kcz8?t=40",
                "https://m.youtube.com/watch?feature=share&v=MoGEOc3kcz8", "https://youtube.com/shorts/MoGEOc3kcz8"):
        assert vd.parse_youtube_link(url) == vid
    assert vd.parse_youtube_link(" https://www.youtube.com/@NoCopyrightGameplays/videos ") == \
        ("channel", "https://www.youtube.com/@NoCopyrightGameplays")
    assert vd.parse_youtube_link("youtube.com/channel/UC4r6nalq_1-9rc80qdVLatQ")[0] == "channel"
    for bad in ("https://vimeo.com/123", "https://www.youtube.com/results?search_query=x", "hello"):
        with pytest.raises(ValueError):
            vd.parse_youtube_link(bad)


def channel_list():
    def v(i, title, duration):
        return vd.LibraryVideo(video_id=f"vid{i:08d}", title=title, channel_id="UC", channel_title="NCG",
                               games=match_title(title), duration=duration)
    return [v(1, "Minecraft Parkour No Copyright", 600), v(2, "Minecraft Parkour (Vertical)", 900),
            v(3, "GTA 5 Driving No Copyright", 1200), v(4, "Minecraft short", 20)]


def test_filter_channel_by_game_length_and_orientation():
    vids = channel_list()
    assert [v.video_id for v in vd._filter_channel(vids, "", 60, "landscape")] == ["vid00000001", "vid00000003"]
    assert [v.video_id for v in vd._filter_channel(vids, "Minecraft", 60, "portrait")] == ["vid00000002"]
    assert [v.video_id for v in vd._filter_channel(vids, "GTA V / Online", 60, "portrait")] == ["vid00000003"]
    with pytest.raises(vd.DownloadError, match="No videos of 'Fortnite'"):
        vd._filter_channel(vids, "Fortnite", 60, "landscape")


def test_download_from_link_channel_and_video(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(vd, "channel_videos", lambda url: channel_list())
    got = []

    def fake_download(url, target, clip_seconds, progress):
        got.append(url)
        target.write_bytes(b"video")
        title = "GTA 5 Driving No Copyright" if url.endswith("3") else "Some video"
        return {"title": title, "source_url": url, "author": "NCG", "license_note": "x"}

    monkeypatch.setattr(vd, "_download_from_youtube", fake_download)
    monkeypatch.setattr(vd, "probe_video", lambda p: {"width": 1080, "height": 1920})
    clip = vd.download_from_link("https://www.youtube.com/@NCG", settings=settings, game="GTA V / Online",
                                 clip_seconds=30, orientation="landscape")
    assert got == ["https://www.youtube.com/watch?v=vid00000003"]
    assert clip.game == "GTA V / Online" and clip.filename.startswith("gta-v-online_youtube_")
    assert clip.query == "link: https://www.youtube.com/@NCG" and "pasted" in clip.license_note
    assert (settings.backgrounds_dir / clip.filename).exists() and not list(settings.backgrounds_dir.glob("link_*"))

    clip = vd.download_from_link("https://youtu.be/abcdefghijk", settings=settings, clip_seconds=30)
    assert got[-1] == "https://www.youtube.com/watch?v=abcdefghijk" and clip.game == "Gameplay"
    with pytest.raises(ValueError):
        vd.download_from_link("https://example.com", settings=settings)


def test_download_background_reports_all_failures(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    lib = vd.NoCopyrightLibrary(settings, client_factory=FakeClient)
    monkeypatch.setattr(vd, "get_library", lambda s: lib)
    monkeypatch.setattr(vd, "search_no_copyright", lambda game: [])
    with pytest.raises(vd.DownloadError) as exc:
        vd.download_background("Pokemon", settings=settings, sources=["pexels", "youtube"])
    message = str(exc.value)
    assert "PEXELS_API_KEY" in message and "no no-copyright videos" in message
