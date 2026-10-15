from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trendclip import channel_stats, instagram, planner, shorts
from trendclip.config import Settings

CREDIT = "Gameplay footage: Orbital - https://youtu.be/abc\nMusic: Song"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(channel_stats, "INSTAGRAM_TOKEN_FILE", tmp_path / "token_instagram.json")
    monkeypatch.setattr(channel_stats, "instagram_token", lambda s, session=None: "tok")
    monkeypatch.setattr(instagram, "_accounts", {})
    monkeypatch.setattr(instagram.time, "sleep", lambda s: None)
    return Settings(youtube_api_key="k", output_dir=tmp_path / "output", assets_dir=tmp_path / "assets",
                    instagram_access_token="tok")


def add_short(settings, name="q.mp4", **kw):
    settings.shorts_dir.mkdir(parents=True, exist_ok=True)
    (settings.shorts_dir / name).write_bytes(b"\x00" * 2048)
    fields = {"duration_seconds": 30, **kw}
    short = shorts.ShortVideo(filename=name, game="gta", title="Only 3% get this", description="",
                              hashtags=["#riddle", "#quiz"], script="", voice="", background="", credit=CREDIT,
                              pinned_comment="Comment your answer 👇", ready=True, **fields)
    (settings.shorts_dir / name).with_suffix(".json").write_text(short.model_dump_json())
    return name


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class FakeInstagram:
    """graph.instagram.com + rupload: records every call; the container needs one status check."""

    def __init__(self, fail_comment=False):
        self.calls, self.checks, self.fail_comment = [], 0, fail_comment

    def request(self, method, url, params=None, data=None, timeout=None, **kw):
        path = url.split(instagram.VERSION, 1)[1]
        self.calls.append((method, path, data))
        assert params["access_token"] == "tok"
        if path == "/me":
            return Resp(200, {"user_id": "17841", "username": "sidequestlogic"})
        if path == "/17841/media":
            return Resp(200, {"id": "c1", "uri": "https://rupload.facebook.com/x"})
        if path == "/c1":
            self.checks += 1
            return Resp(200, {"status_code": "IN_PROGRESS" if self.checks == 1 else "FINISHED"})
        if path == "/17841/media_publish":
            return Resp(200, {"id": "m1"})
        if path == "/m1":
            return Resp(200, {"permalink": "https://www.instagram.com/reel/ABC/"})
        if path == "/m1/comments":
            if self.fail_comment:
                return Resp(400, {"error": {"message": "Comments are off", "code": 100}})
            return Resp(200, {"id": "k1"})
        raise AssertionError(path)

    def post(self, url, data=None, headers=None, timeout=None):
        assert url.endswith("/ig-api-upload/v23.0/c1")
        assert headers["Authorization"] == "OAuth tok" and headers["file_size"] == "2048"
        self.calls.append(("UPLOAD", "c1", len(data.read())))
        return Resp(200, {"success": True})


def test_post_reel_creates_uploads_waits_and_publishes(settings, monkeypatch):
    fake = FakeInstagram()
    monkeypatch.setattr(instagram.requests, "request", fake.request)
    monkeypatch.setattr(instagram.requests, "post", fake.post)
    name = add_short(settings)

    record = instagram.post_reel(settings, name, first_comment="Comment your answer 👇")
    assert record["url"] == "https://www.instagram.com/reel/ABC/" and record["media_id"] == "m1"
    assert record["comment"]["comment_id"] == "k1"
    steps = [(c[0], c[1]) for c in fake.calls]
    assert steps == [("GET", "/me"), ("POST", "/17841/media"), ("UPLOAD", "c1"), ("GET", "/c1"), ("GET", "/c1"),
                     ("POST", "/17841/media_publish"), ("GET", "/m1"), ("POST", "/m1/comments")]
    container = fake.calls[1][2]
    assert container["media_type"] == "REELS" and container["upload_type"] == "resumable"
    assert "#riddle" in container["caption"] and "Orbital" in container["caption"]  # the Instagram text
    assert fake.calls[2][2] == 2048

    short = shorts.get_short(settings, name)
    assert short.uploads["instagram"]["media_id"] == "m1"
    assert planner.short_status(short)["instagram"] == {
        "done": True, "auto": True, "url": "https://www.instagram.com/reel/ABC/", "scheduled_for": None}


def test_a_failed_comment_keeps_the_reel(settings, monkeypatch):
    fake = FakeInstagram(fail_comment=True)
    monkeypatch.setattr(instagram.requests, "request", fake.request)
    monkeypatch.setattr(instagram.requests, "post", fake.post)
    name = add_short(settings)
    record = instagram.post_reel(settings, name, "My own caption", first_comment="hi")
    assert "Comments are off" in record["comment_error"] and fake.calls[1][2]["caption"] == "My own caption"
    assert shorts.get_short(settings, name).uploads["instagram"]["url"]


def test_errors_are_explained(settings, monkeypatch):
    name = add_short(settings, "long.mp4", duration_seconds=1000)
    with pytest.raises(instagram.InstagramError, match="3 s to 15 min"):
        instagram.post_reel(settings, name)

    monkeypatch.setattr(instagram.requests, "request", lambda *a, **kw: Resp(
        400, {"error": {"message": "Error validating access token", "code": 190}}))
    with pytest.raises(instagram.InstagramError, match="new INSTAGRAM_ACCESS_TOKEN"):
        instagram.post_reel(settings, add_short(settings))
    assert instagram.status(settings)["connected"] is False

    monkeypatch.setattr(channel_stats, "instagram_token", lambda s, session=None: None)
    with pytest.raises(instagram.InstagramError, match="INSTAGRAM_ACCESS_TOKEN"):
        instagram.token(settings)


def test_instagram_routes(settings, monkeypatch):
    from trendclip.web import app as web

    monkeypatch.setattr(web, "_base_settings", lambda: settings)
    monkeypatch.setattr(instagram.requests, "request", FakeInstagram().request)
    client = TestClient(web.app)
    assert client.get("/api/upload/instagram/status").json() == {
        "configured": True, "connected": True, "user": {"username": "sidequestlogic", "profile_image_url": ""}}
    assert client.post("/api/upload/shorts/missing.mp4/instagram", json={}).status_code == 404

    jobs = []
    monkeypatch.setattr(web._create, "submit", lambda kind, game, fn: jobs.append(fn) or SimpleNamespace(
        model_dump=lambda mode: {"id": "j1"}))
    name = add_short(settings)
    monkeypatch.setattr(instagram, "post_reel", lambda s, f, caption, comment, p: jobs.append((f, caption, comment)))
    client.post(f"/api/upload/shorts/{name}/instagram", json={"caption": "Hi", "first_comment": "Answer?"})
    jobs[0](lambda f, m: None)
    assert jobs[1] == (name, "Hi", "Answer?")

    monkeypatch.setattr(web, "_base_settings", lambda: settings.model_copy(update={"instagram_access_token": None}))
    assert client.post(f"/api/upload/shorts/{name}/instagram", json={}).status_code == 400
