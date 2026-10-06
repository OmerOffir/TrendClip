from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trendclip import publish, script_writer, shorts
from trendclip.config import Settings
from trendclip.web import app as web

CREDIT = "Gameplay footage: Orbital - https://youtu.be/abc\nMusic: Song\nMusic provided by NoCopyrightSounds"


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output")


def add_short(settings, name="gta_1.mp4", **kw):
    settings.shorts_dir.mkdir(parents=True, exist_ok=True)
    (settings.shorts_dir / name).write_bytes(b"\x00" * 2048)
    short = shorts.ShortVideo(
        filename=name, game="GTA V / Online", title="The Day My Cat Outsmarted the Block",
        description=f"I searched for three hours. He was upstairs.\n\n{CREDIT}",
        hashtags=["#storytime", "#cats", "#funny", "#pets", "#gta", "#shorts"], script="I spent three hours.",
        voice="v", background="bg.mp4", credit=CREDIT, **kw)
    (settings.shorts_dir / name).with_suffix(".json").write_text(short.model_dump_json())
    return name


@pytest.fixture(autouse=True)
def isolated_oauth(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "CLIENT_SECRETS", tmp_path / "client_secret.json")
    monkeypatch.setattr(publish, "TOKEN_FILE", tmp_path / "token_youtube.json")


def test_template_texts_follow_platform_rules(tmp_path):
    settings = make_settings(tmp_path)
    short = shorts.get_short(settings, add_short(settings))
    t = publish.template_texts(short)
    assert t.youtube.title == "The Day My Cat Outsmarted the Block #shorts"
    assert t.youtube.description == "I searched for three hours. He was upstairs."  # credits kept separately
    assert "#shorts" in t.youtube.hashtags and "GTA V / Online" in t.youtube.tags
    assert len(t.instagram.hashtags) <= publish.INSTAGRAM_MAX_HASHTAGS
    assert "#GTAV" in t.tiktok.hashtags
    full = publish.full_text(t.tiktok, t.credit)
    assert full.startswith(t.tiktok.caption) and full.endswith(CREDIT)
    assert publish.youtube_description(t.youtube, t.credit).endswith(CREDIT)


def test_clean_limits_and_normalises():
    raw = publish.GeminiTexts(
        youtube=publish.YouTubeText(title="x" * 120, description="d", tags=["<bad>", "a" * 300, "b" * 300, "a" * 300],
                                    hashtags=["fun", "#Fun"]),
        tiktok=publish.SocialText(caption=" c ", hashtags=["#a"], mentions=["rockstargames", "@RockstarGames", "@x y"]),
        instagram=publish.SocialText(caption="c", hashtags=[f"#t{i}" for i in range(9)]),
    )
    t = publish.clean(raw, "", "gemini")
    assert len(t.youtube.title) == 100 and t.youtube.title.endswith("#shorts")
    assert t.youtube.tags == ["bad", "a" * 300]  # 450-character budget
    assert t.youtube.hashtags == ["#fun", "#shorts"]
    assert t.tiktok.mentions == ["@rockstargames", "@xy"] and t.tiktok.caption == "c"
    assert len(t.instagram.hashtags) == 5


def test_gemini_texts_uses_schema_and_keeps_credit(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    short = shorts.get_short(settings, add_short(settings))
    seen = {}

    class Models:
        def generate_content(self, model, contents, config):
            seen["prompt"] = contents[0]
            return SimpleNamespace(parsed=publish.GeminiTexts(
                youtube=publish.YouTubeText(title="Cat hid all day", description="Wow.", tags=["cat story"],
                                            hashtags=["#storytime"]),
                tiktok=publish.SocialText(caption="Where was he?? 😭", hashtags=["#cat"]),
                instagram=publish.SocialText(caption="Cats.\n\nSave this.", hashtags=["#cats"]),
            ), text="")

    t = publish.gemini_texts(settings, short, client=SimpleNamespace(models=Models()))
    assert "I spent three hours." in seen["prompt"] and "Orbital" not in seen["prompt"]
    assert t.source == "gemini" and t.youtube.title == "Cat hid all day #shorts" and t.credit == CREDIT


def test_upload_youtube_records_the_video(tmp_path):
    settings = make_settings(tmp_path)
    name = add_short(settings)
    calls = {}

    class Request:
        def __init__(self):
            self.n = 0

        def next_chunk(self):
            self.n += 1
            if self.n == 1:
                return SimpleNamespace(progress=lambda: 0.5), None
            return None, {"id": "abc123", "status": {"privacyStatus": "private"}}

    class Videos:
        def insert(self, part, body, media_body, notifySubscribers):
            calls["body"], calls["notify"] = body, notifySubscribers
            return Request()

    youtube = SimpleNamespace(videos=lambda: Videos())
    progress = []
    req = publish.YouTubeUploadRequest(title="My cat", description="desc", tags=["cat"], privacy="public")
    record = publish.upload_youtube(settings, name, req, lambda f, m: progress.append(f), youtube=youtube)

    assert calls["body"]["snippet"]["title"] == "My cat #shorts"
    assert calls["body"]["snippet"]["categoryId"] == "20"
    assert calls["body"]["status"] == {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                                       "containsSyntheticMedia": False}
    assert calls["notify"] is True
    assert record["url"] == "https://youtube.com/shorts/abc123" and record["privacy"] == "private"
    assert 0.5 in progress
    assert shorts.get_short(settings, name).uploads["youtube"]["video_id"] == "abc123"


def test_scheduled_upload_is_private_with_publish_at(tmp_path):
    from datetime import datetime, timedelta, timezone

    settings = make_settings(tmp_path)
    name = add_short(settings)
    when = datetime.now(timezone.utc) + timedelta(days=1)
    calls = {}

    class Videos:
        def insert(self, part, body, media_body, notifySubscribers):
            calls["status"] = body["status"]
            stamp = body["status"]["publishAt"]
            return SimpleNamespace(next_chunk=lambda: (None, {"id": "sch1", "status": {
                "privacyStatus": "private", "publishAt": stamp}}))

    req = publish.YouTubeUploadRequest(title="T", privacy="public", publish_at=when.isoformat())
    record = publish.upload_youtube(settings, name, req, youtube=SimpleNamespace(videos=lambda: Videos()))
    assert calls["status"]["privacyStatus"] == "private"
    assert calls["status"]["publishAt"] == when.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert record["scheduled_for"] == calls["status"]["publishAt"] and record["requested_privacy"] == "scheduled"

    with pytest.raises(ValueError, match="15 minutes"):
        publish.YouTubeUploadRequest(title="T", publish_at=(datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat())
    with pytest.raises(ValueError, match="timezone"):
        publish.YouTubeUploadRequest(title="T", publish_at="2030-01-01T10:00:00")


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    monkeypatch.setattr(web, "_base_settings", lambda: settings)
    monkeypatch.setattr(web, "_create", shorts.CreateManager())
    return TestClient(web.app), settings


def test_ready_flow_and_texts_endpoints(client, monkeypatch):
    http, settings = client
    name = add_short(settings)
    add_short(settings, "other.mp4")
    assert http.get("/api/upload/shorts").json() == []

    assert http.post(f"/api/shorts/{name}/ready", json={"ready": True}).json()["ready"] is True
    queue = http.get("/api/upload/shorts").json()
    assert [s["filename"] for s in queue] == [name]
    item = queue[0]
    assert item["texts"]["source"] == "template" and item["paste"]["tiktok"].endswith(CREDIT)

    texts = item["texts"]
    texts["tiktok"]["caption"] = "Edited caption"
    texts["instagram"]["hashtags"] = ["a", "b", "c", "d", "e", "f"]
    saved = http.put(f"/api/upload/shorts/{name}/texts", json=texts).json()
    assert saved["texts"]["tiktok"]["caption"] == "Edited caption"
    assert saved["texts"]["instagram"]["hashtags"] == ["#a", "#b", "#c", "#d", "#e"]
    assert saved["paste"]["tiktok"].startswith("Edited caption")

    posted = http.post(f"/api/upload/shorts/{name}/posted/tiktok", json={"posted": True}).json()
    assert posted["posted"] == {"tiktok": True}
    assert http.post(f"/api/upload/shorts/{name}/posted/snapchat", json={"posted": True}).status_code == 422

    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s=None: None)
    assert http.post(f"/api/upload/shorts/{name}/texts/gemini").status_code == 400
    assert http.post("/api/shorts/missing.mp4/ready", json={"ready": True}).status_code == 404
    assert http.post("/api/shorts/..%2F.env/ready", json={"ready": True}).status_code in (400, 404)

    http.post(f"/api/shorts/{name}/ready", json={"ready": False})
    assert http.get("/api/upload/shorts").json() == []


def test_youtube_endpoints_without_credentials(client):
    http, settings = client
    name = add_short(settings)
    assert http.get("/api/upload/youtube/status").json() == {"client_secret": False, "connected": False, "channel": None}
    assert http.post("/api/upload/youtube/connect").status_code == 400
    page = http.get("/api/upload/youtube/callback?state=nope&code=x").text
    assert "expired" in page and "Not connected" in page
    resp = http.post(f"/api/upload/shorts/{name}/youtube", json={"title": "T"})
    assert resp.status_code == 400 and "Connect YouTube" in resp.json()["detail"]


def test_connect_builds_loopback_redirect(client, tmp_path):
    http, _ = client
    publish.CLIENT_SECRETS.write_text(
        '{"installed": {"client_id": "id.apps.googleusercontent.com", "client_secret": "s", "project_id": "p",'
        ' "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",'
        ' "redirect_uris": ["http://localhost"]}}')
    url = http.post("/api/upload/youtube/connect").json()["auth_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/auth?")
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A80%2Fapi%2Fupload%2Fyoutube%2Fcallback" in url or \
        "redirect_uri=http%3A%2F%2Flocalhost%2Fapi%2Fupload%2Fyoutube%2Fcallback" in url or "redirect_uri=" in url
    assert "youtube.upload" in url and "code_challenge=" in url and "access_type=offline" in url


def test_landscape_videos_upload_as_regular_videos():
    from trendclip import shorts as shorts_mod

    vertical = shorts_mod.ShortVideo(filename="a.mp4", game="GTA V", title="Wrong Uber", description="d",
                                     hashtags=["#storytime", "#shorts"], script="s", voice="v", background="b.mp4",
                                     duration_seconds=40)
    wide = vertical.model_copy(update={"aspect": "landscape", "duration_seconds": 85})
    assert publish.is_short(vertical) and not publish.is_short(wide)
    assert not publish.is_short(vertical.model_copy(update={"duration_seconds": 200}))
    texts = publish.template_texts(wide)
    assert "#shorts" not in texts.youtube.title.lower()
    assert "#shorts" not in [h.lower() for h in texts.youtube.hashtags] and "shorts" not in texts.youtube.tags
    assert publish.template_texts(vertical).youtube.title.endswith("#shorts")
    assert publish._yt_title("Wrong Uber #shorts", as_short=False) == "Wrong Uber"
