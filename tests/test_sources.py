import pytest
from fastapi.testclient import TestClient

from trendclip import video_downloader as vd
from trendclip.config import Settings

CH_A = "UCbVQhpMxL5uSoL3Qh5Mc1_A"  # Dope Gameplays
CH_B = "UCyjMmPUSDB8yzb4GutNbasw"
CH_C = "UCMmaGztaX7qfNnwwPY9jdTw"


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output")


def entry(vid, title, channel, channel_id, description="", duration=600, **kw):
    return {"id": vid, "title": title, "channel": channel, "channel_id": channel_id, "uploader_id": "@" + channel.replace(" ", ""),
            "description": description, "duration": duration, "view_count": 1000, **kw}


SEARCH = {
    "minecraft no copyright gameplay": [
        entry("aaaaaaaaaa1", "Minecraft Parkour Gameplay (No Copyright)", "Dope Gameplays", CH_A),
        entry("aaaaaaaaaa2", "Minecraft hardcore funny moments", "joel", CH_C),
        entry("aaaaaaaaaa3", "Live now!", "Dope Gameplays", CH_A, live_status="is_live"),
    ],
    "minecraft copyright free gameplay": [
        entry("aaaaaaaaaa1", "Minecraft Parkour Gameplay (No Copyright)", "Dope Gameplays", CH_A),  # duplicate
        entry("aaaaaaaaaa4", "Minecraft Parkour 4K 9:16", "GameplaysForFree", CH_B),  # channel name says it
    ],
    "minecraft free to use gameplay no commentary": [
        entry("aaaaaaaaaa5", "Minecraft Survival 1 hour", "joel", CH_C, description="Free to use for your videos, credit me"),
        entry("aaaaaaaaaa6", "GTA 5 Copyright-Free driving", "Dope Gameplays", CH_A),
    ],
}


@pytest.fixture
def fake_search(monkeypatch):
    asked = []

    def search(query, limit):
        asked.append(query.lower())
        return [dict(e) for e in SEARCH.get(query.lower(), [])]

    monkeypatch.setattr(vd, "_yt_search", search)
    return asked


def test_search_keeps_videos_that_say_free_to_use(fake_search):
    results = vd.search_youtube("  Minecraft ")
    assert sorted(fake_search) == sorted(SEARCH)  # three phrasings
    by_id = {r.video_id: r for r in results}
    # Interleaved (each phrasing's first result, then the seconds), no dupes, no live streams.
    assert list(by_id) == ["aaaaaaaaaa1", "aaaaaaaaaa5", "aaaaaaaaaa4", "aaaaaaaaaa6"]
    assert by_id["aaaaaaaaaa1"].says_free == "title" and by_id["aaaaaaaaaa1"].games == ["Minecraft"]
    assert by_id["aaaaaaaaaa4"].says_free == "channel" and by_id["aaaaaaaaaa4"].vertical
    assert by_id["aaaaaaaaaa5"].says_free == "description"
    assert by_id["aaaaaaaaaa6"].says_free == "title"  # "Copyright-Free"

    everything = vd.search_youtube("minecraft", only_free=False)
    assert "aaaaaaaaaa2" in {r.video_id for r in everything}


def test_channel_suggestions(fake_search):
    results = vd.search_youtube("minecraft")
    channels = vd.suggest_channels(results, known_ids={CH_B})
    assert [c["channel_id"] for c in channels] == [CH_A, CH_B, CH_C]
    dope = channels[0]
    assert dope["hits"] == 2 and dope["games"] == ["Minecraft", "GTA V / Online"] and not dope["in_sources"]
    assert dope["url"] == "https://www.youtube.com/@DopeGameplays"
    assert channels[1]["in_sources"] and channels[1]["says_in_name"]


def test_fallback_search_only_returns_the_game(fake_search):
    found = vd.search_no_copyright("Minecraft")
    assert "aaaaaaaaaa6" not in {v.video_id for v in found}  # GTA
    assert {v.video_id for v in found} == {"aaaaaaaaaa1", "aaaaaaaaaa4", "aaaaaaaaaa5"}
    assert found[0].duration == 600


def test_saved_channels_join_the_library(tmp_path):
    settings = make_settings(tmp_path)
    assert vd.channel_refs(settings) == settings.ncg_channels
    vd.add_channel(settings, CH_A, "Dope Gameplays", "@DopeGameplays")
    vd.add_channel(settings, CH_A, "Dope Gameplays (renamed)")  # no duplicate
    assert [c["title"] for c in vd.saved_channels(settings)] == ["Dope Gameplays (renamed)"]
    assert vd.channel_refs(settings) == settings.ncg_channels + [CH_A]
    with pytest.raises(ValueError):
        vd.add_channel(settings, "@not-an-id")

    class Client:
        def resolve_channel(self, ref):
            return {"id": ref, "title": "Dope", "uploads": "UU", "video_count": 1} if ref == CH_A else None

        def list_uploads(self, playlist_id, max_items):
            return [{"video_id": "bbbbbbbbbb1", "title": "Minecraft Parkour No Copyright"}]

    lib = vd.NoCopyrightLibrary(settings, client_factory=Client)
    assert [v.channel_id for v in lib.videos()] == [CH_A]
    assert vd.NoCopyrightLibrary(settings, client_factory=Client)._load_cache()  # cached with these refs
    assert vd.remove_channel(settings, CH_A) and not vd.remove_channel(settings, CH_A)
    assert not vd.NoCopyrightLibrary(settings, client_factory=Client)._load_cache()  # refs changed: refresh


def test_get_gameplay_from_one_channel_or_random(tmp_path, monkeypatch):
    from tests.test_video_downloader import FakeClient

    settings = make_settings(tmp_path)
    lib = vd.NoCopyrightLibrary(settings, client_factory=FakeClient)
    monkeypatch.setattr(vd, "get_library", lambda s: lib)
    monkeypatch.setattr(vd, "probe_video", lambda p: {"width": 1920, "height": 1080})
    monkeypatch.setattr(vd, "_reframe", lambda *a, **k: None)
    monkeypatch.setattr(vd, "search_no_copyright", lambda game: [])
    got = []

    def fake_download(url, target, clip_seconds, progress=vd._noop):
        got.append(url)
        target.write_bytes(b"mp4")
        return {"title": "t", "source_url": url, "author": "a", "license_note": "n"}

    monkeypatch.setattr(vd, "_download_from_youtube", fake_download)
    assert {v.channel_id for v in lib.matches("Minecraft")} == {"UC1"}
    assert lib.matches("Minecraft", "UC2") == [] and len(lib.matches("Fortnite", "UC2")) == 1

    clip = vd.download_background("Fortnite", settings=settings, sources=["youtube"], channel_id="UC2")
    assert got[-1].endswith("ddddddddddd") and clip.query == "Orbital - No Copyright Gameplay: Fortnite"
    with pytest.raises(vd.DownloadError, match="Orbital - No Copyright Gameplay has no videos of 'Minecraft'"):
        vd.download_background("Minecraft", settings=settings, sources=["youtube"], channel_id="UC2")
    vd.download_background("Minecraft", settings=settings, sources=["youtube"])  # random: any channel
    assert got[-1].endswith(("aaaaaaaaaaa", "bbbbbbbbbbb"))


def test_options_and_download_endpoints(tmp_path, monkeypatch):
    from tests.test_video_downloader import FakeClient
    from trendclip.web import app as web

    settings = make_settings(tmp_path)
    lib = vd.NoCopyrightLibrary(settings, client_factory=FakeClient)
    monkeypatch.setattr(vd, "get_library", lambda s: lib)
    monkeypatch.setattr(web, "_base_settings", lambda: settings)
    submitted = []
    monkeypatch.setattr(web._downloads, "submit", lambda *a, **k: submitted.append((a, k)) or vd.DownloadJob(
        id="j", game=a[1], sources=a[2], clip_seconds=a[3], orientation=a[4], **k))
    client = TestClient(web.app)

    body = client.get("/api/backgrounds/options", params={"game": "Minecraft"}).json()
    assert body["total"] == 2 and not body["pexels"]
    assert [(c["title"], c["videos"], c["vertical"]) for c in body["channels"]] == [
        ("No Copyright Gameplay", 2, 1), ("Orbital - No Copyright Gameplay", 0, 0)]

    r = client.post("/api/backgrounds/download", json={"game": "Minecraft"})
    assert r.status_code == 202 and submitted[-1][0][2] == ["youtube"] and submitted[-1][1]["channel_id"] is None
    r = client.post("/api/backgrounds/download", json={"game": "Fortnite", "channel_id": "UC2" + "x" * 21})
    assert r.status_code == 400  # not one of your channels
    lib.channels.append({"id": "UC2" + "x" * 21, "title": "Orbital"})
    r = client.post("/api/backgrounds/download", json={"game": "Fortnite", "channel_id": "UC2" + "x" * 21})
    assert r.status_code == 202 and r.json()["channel_title"] == "Orbital"
    assert client.post("/api/backgrounds/download", json={"game": "x", "source": "pexels"}).status_code == 400


def test_source_endpoints(tmp_path, monkeypatch, fake_search):
    from trendclip.web import app as web

    settings = make_settings(tmp_path)
    monkeypatch.setattr(web, "_base_settings", lambda: settings)

    class Lib:
        channels = [{"id": CH_C, "title": "joel"}]

        def videos(self):
            return []

    monkeypatch.setattr(vd, "get_library", lambda s: Lib())
    client = TestClient(web.app)

    body = client.get("/api/sources/search", params={"q": "minecraft"}).json()
    assert [r["video_id"] for r in body["results"]][:2] == ["aaaaaaaaaa1", "aaaaaaaaaa5"]
    first = body["results"][0]
    assert first["url"].endswith("aaaaaaaaaa1") and not first["in_sources"] and not first["used"]
    assert next(c for c in body["channels"] if c["channel_id"] == CH_C)["in_sources"]  # already in the library
    assert len(client.get("/api/sources/search", params={"q": "minecraft", "all": "true"}).json()["results"]) == 5

    r = client.post("/api/sources/channels", json={"channel_id": CH_A, "title": "Dope Gameplays"})
    assert r.status_code == 200 and r.json()["added"][0]["channel_id"] == CH_A
    assert client.post("/api/sources/channels", json={"channel_id": "nope"}).status_code == 422
    body = client.get("/api/sources/search", params={"q": "minecraft"}).json()
    assert body["results"][0]["in_sources"]
    assert client.get("/api/sources/channels").json()["env"] == settings.ncg_channels
    assert client.delete(f"/api/sources/channels/{CH_A}").status_code == 200
    assert client.delete(f"/api/sources/channels/{CH_A}").status_code == 404
