"""Debugging from the phone: /check (YouTube live + the served thumbnail against ours), the thumbnail alert after an
upload, "Set it again", and /log."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from modules import pw_channel
from tests.test_upload import add_short
from trendclip import doctor, publish, shorts
from trendclip.config import Settings


def jpeg(color, size=(1080, 1920)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def pictures(monkeypatch):
    """The pictures YouTube serves per URL (None = 404)."""
    store: dict[str, bytes | None] = {}
    monkeypatch.setattr(doctor, "fetch", lambda url: store.get(url))
    monkeypatch.setattr(doctor, "VERIFY_WAIT", 0.0)
    return store


def test_distance_sees_the_same_picture_at_another_size(tmp_path):
    ours = tmp_path / "ours.jpg"
    ours.write_bytes(jpeg((20, 40, 200)))
    assert doctor.distance(ours, jpeg((20, 40, 200), (720, 1280))) < 2
    assert doctor.distance(ours, jpeg((240, 240, 30))) > 60


def test_verify_thumbnail_says_what_youtube_serves(tmp_path, pictures):
    ours = tmp_path / "ours.jpg"
    ours.write_bytes(jpeg((20, 40, 200)))
    assert doctor.verify_thumbnail("v1", ours) == "set, but the Shorts shelf has no custom thumbnail yet"
    pictures[doctor.SERVED.format(id="v1")] = jpeg((240, 240, 30))
    assert doctor.verify_thumbnail("v1", ours).startswith("set, but YouTube shows another picture (distance")
    pictures[doctor.SERVED.format(id="v1")] = jpeg((20, 40, 200), (480, 854))
    assert doctor.verify_thumbnail("v1", ours) == "set"
    assert doctor.verify_thumbnail("v1", ours, wait=-1) == "set"  # verification off
    assert doctor.thumbnail_ok("set") and doctor.thumbnail_ok("set after processing")
    assert not doctor.thumbnail_ok("set, but x") and not doctor.thumbnail_ok("not set: refused")


class FakeYouTube:
    def __init__(self, item=None, fail=None):
        self.item, self.fail, self.sets = item, fail, []

    def videos(self):
        return self

    def list(self, part, id):
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError(self.fail)
        return {"items": [self.item] if self.item else []}

    def thumbnails(self):
        return self

    def set(self, videoId, media_body):
        self.sets.append(videoId)
        return self


LIVE_ITEM = {"snippet": {"title": "Why Is Coffee $7? #shorts", "publishedAt": "2026-11-04T12:00:00Z",
                         "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/v1/hqdefault.jpg"}}},
             "status": {"privacyStatus": "public", "uploadStatus": "processed"},
             "statistics": {"viewCount": "1234", "likeCount": "56", "commentCount": "7"},
             "processingDetails": {"processingStatus": "succeeded"}}


def _pw_video(root: Path, key: str, uploaded=True, thumb=True):
    folder = root / key
    folder.mkdir(parents=True)
    (folder / "video.mp4").write_bytes(b"\x00" * 16)
    if thumb:
        (folder / "thumb.jpg").write_bytes(jpeg((20, 40, 200)))
    info = {"title": "Why Is Coffee $7?", "created_at": f"2026-11-0{key[-1]}T12:00:00", "job": {"slot": "14:00"}}
    if uploaded:
        info["uploads"] = {"youtube": {"video_id": "v1", "url": "https://youtube.com/shorts/v1", "privacy": "public",
                                       "thumbnail": "set after processing", "studio_url": "https://studio/v1",
                                       "comment": {"url": "https://www.youtube.com/watch?v=v1&lc=c1"}}}
    (folder / "info.json").write_text(json.dumps(info))


@pytest.fixture
def pw_root(tmp_path, monkeypatch):
    root = tmp_path / "pricetagwhy"
    monkeypatch.setattr(pw_channel, "OUTPUT_DIR", root)
    return root


def test_check_reads_youtube_live_and_compares_the_thumbnail(pw_root, pictures):
    with pytest.raises(doctor.DoctorError, match="no upload yet"):
        doctor.check("pw", youtube=FakeYouTube(LIVE_ITEM))
    _pw_video(pw_root, "2026-11-03_1400", uploaded=False)
    _pw_video(pw_root, "2026-11-04_1400")
    pictures[doctor.SERVED.format(id="v1")] = jpeg((240, 240, 30))
    pictures[doctor.FRAME.format(id="v1")] = jpeg((0, 0, 0))
    r = doctor.check("pw", youtube=FakeYouTube(LIVE_ITEM))
    assert r["key"] == "2026-11-04_1400" and r["live"]["views"] == 1234 and r["live"]["privacy"] == "public" and not r["error"]
    assert r["thumbnail"]["match"] is False and r["thumbnail"]["served"] and r["ours"] == pw_root / "2026-11-04_1400" / "thumb.jpg"
    lines = doctor.format_report(r)
    assert lines[0].startswith("🔎 **Why Is Coffee $7?** · Encore Story")
    assert "**public** · processed" in lines[1] and "1234 views · 56 likes · 7 comments" in lines[2]
    assert "ours drawn · record says: set after processing · ❌ YouTube serves another picture" in lines[3]
    assert lines[4] == "💬 Comment posted: <https://www.youtube.com/watch?v=v1&lc=c1>"

    pictures[doctor.SERVED.format(id="v1")] = jpeg((20, 40, 200), (720, 1280))
    r = doctor.check("pw", "2026-11-04_1400", youtube=FakeYouTube(LIVE_ITEM))
    assert r["thumbnail"]["match"] is True and "✅ YouTube serves our picture" in doctor.format_report(r)[3]

    r = doctor.check("pw", youtube=FakeYouTube(fail="invalid_grant: Token has been expired or revoked"))
    assert r["live"] is None and "invalid_grant" in r["error"]
    assert "reconnect the channel's YouTube login" in doctor.format_report(r)[1]
    r = doctor.check("pw", youtube=FakeYouTube(None))
    assert "YouTube has no video v1" in doctor.format_report(r)[1]
    with pytest.raises(doctor.DoctorError, match="No Encore Story upload nope"):
        doctor.check("pw", "nope", youtube=FakeYouTube(LIVE_ITEM))
    with pytest.raises(doctor.DoctorError, match="No channel"):
        doctor.uploads("xx")


def test_the_gameplay_channel_uploads_and_set_again(tmp_path, pictures, monkeypatch):
    settings = Settings(youtube_api_key="k", output_dir=tmp_path / "output", assets_dir=tmp_path / "assets")
    add_short(settings, "plain.mp4")
    name = add_short(settings, "up.mp4", uploads={"youtube": {"video_id": "v9", "url": "https://youtube.com/shorts/v9", "privacy": "public"}})
    found = doctor.uploads("sq", settings)
    assert [u.key for u in found] == [name] and found[0].video_id == "v9" and found[0].url.endswith("v9")
    ours = settings.shorts_dir / "up.thumbnail.jpg"
    ours.write_bytes(jpeg((9, 9, 9)))
    monkeypatch.setattr(publish, "short_thumbnail", lambda s, filename: ours)
    yt = FakeYouTube(LIVE_ITEM)
    pictures[doctor.SERVED.format(id="v9")] = jpeg((9, 9, 9), (720, 1280))
    assert doctor.set_again("sq", name, settings, youtube=yt) == "set" and yt.sets == ["v9"]
    assert shorts.get_short(settings, name).uploads["youtube"]["thumbnail"] == "set by hand"
    with pytest.raises(doctor.DoctorError, match="no thumbnail drawn"):
        monkeypatch.setattr(publish, "short_thumbnail", lambda s, filename: None)
        doctor.set_again("sq", name, settings, youtube=yt)


class FakeRest:
    def __init__(self):
        self.posts = []

    def post(self, content, file=None, components=None):
        self.posts.append((content, components))
        return "m1"


def test_the_alert_comes_only_when_the_thumbnail_missed(pw_root, monkeypatch):
    _pw_video(pw_root, "2026-11-04_1400")
    api = FakeRest()
    monkeypatch.setattr(doctor, "rest", lambda channel, settings=None: api)
    assert doctor.alert_thumbnail("pw", "2026-11-04_1400", "set") is None
    assert doctor.alert_thumbnail("pw", "2026-11-04_1400", "unknown") is None and api.posts == []
    assert doctor.alert_thumbnail("pw", "2026-11-04_1400", "set, but YouTube shows another picture (distance 80)") == "m1"
    text, components = api.posts[0]
    assert text.startswith("🖼️ **Why Is Coffee $7?**: the thumbnail is not what we set (set, but YouTube shows another picture")
    ids = [c.get("custom_id") or c.get("url") for c in components[0]["components"]]
    assert ids == ["tc:thumb:pw 2026-11-04_1400", "tc:check:pw 2026-11-04_1400", "https://youtube.com/shorts/v1", "https://studio/v1"]
    monkeypatch.setattr(doctor, "rest", lambda channel, settings=None: None)
    assert doctor.alert_thumbnail("pw", "2026-11-04_1400", "not set: refused") is None  # no Discord channel: nothing, no error

    # the channel's note after the second setting raises the alert (the real channel only: out_dir None)
    monkeypatch.setattr(doctor, "rest", lambda channel, settings=None: api)
    pw_channel.note_thumbnail("2026-11-04_1400", "not set: quota")
    assert pw_channel.load_info("2026-11-04_1400")["uploads"]["youtube"]["thumbnail"] == "not set: quota after processing"
    assert len(api.posts) == 2 and "not set: quota" in api.posts[1][0]


def test_keep_thumbnail_verifies_what_youtube_serves(tmp_path, pictures, monkeypatch):
    monkeypatch.setattr(publish, "PROCESSING_POLL", 0.0)
    ours = tmp_path / "t.jpg"
    ours.write_bytes(jpeg((1, 2, 3)))
    yt = FakeYouTube({"processingDetails": {"processingStatus": "succeeded"}})
    noted = []
    assert publish.keep_thumbnail_now(lambda: yt, "v1", ours, noted.append) == "set, but the Shorts shelf has no custom thumbnail yet"
    pictures[doctor.SERVED.format(id="v1")] = jpeg((1, 2, 3))
    assert publish.keep_thumbnail_now(lambda: yt, "v1", ours, noted.append) == "set"
    assert noted == ["set, but the Shorts shelf has no custom thumbnail yet", "set"] and yt.sets == ["v1", "v1"]


def test_log_lines_filter_errors_channels_and_clip(tmp_path):
    log = tmp_path / "discord_bot.log"
    log.write_text("\n".join([
        "2026-11-04 14:00:01,000 INFO    trendclip.discord_bot: Discord bot ready",
        "2026-11-04 14:00:02,000 INFO    modules.pw_channel: Price Tag Why 2026-11-04_1400 made",
        "2026-11-04 14:00:03,000 ERROR   trendclip.discord_bot: Background job failed",
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "RuntimeError: boom " + "x" * 300,
        "2026-11-04 14:00:04,000 INFO    modules.bw_channel: Borderline Why made",
        "2026-11-04 14:00:05,000 WARNING trendclip.publish: Thumbnail for v1 not set: refused",
    ]))
    errors = doctor.log_lines(log, 40, "errors")
    assert errors[0] == "14:00:03,000 ERROR   discord_bot: Background job failed" and errors[1] == "Traceback (most recent call last):"
    assert errors[3].startswith("RuntimeError: boom") and len(errors[3]) == 160 and errors[3].endswith("…")
    assert errors[-1].endswith("Thumbnail for v1 not set: refused") and len(errors) == 5
    assert [l.split(": ", 1)[1] for l in doctor.log_lines(log, 40, "pw")] == ["Price Tag Why 2026-11-04_1400 made"]
    assert len(doctor.log_lines(log, 40, "bw")) == 1 and len(doctor.log_lines(log, 2, "all")) == 2
    assert doctor.log_lines(tmp_path / "missing.log") == []


def test_the_buttons_and_commands(pw_root, pictures, monkeypatch):
    from trendclip import discord_bot

    _pw_video(pw_root, "2026-11-04_1400")
    pictures[doctor.SERVED.format(id="v1")] = jpeg((20, 40, 200))
    monkeypatch.setattr(doctor, "client", lambda channel: FakeYouTube(LIVE_ITEM))
    replies = []

    async def fake_reply(interaction, content, **kw):
        replies.append((content, kw))

    monkeypatch.setattr(discord_bot, "reply", fake_reply)
    bot = SimpleNamespace(settings=Settings(youtube_api_key="k", output_dir=pw_root.parent))

    class Interaction:
        response = SimpleNamespace(defer=lambda **kw: asyncio.sleep(0), is_done=lambda: True)
        namespace = SimpleNamespace(channel="pw")

    asyncio.run(discord_bot.act_check(bot, Interaction(), "pw"))
    content, kw = replies[-1]
    assert content.startswith("🔎 **Why Is Coffee $7?**") and "✅ YouTube serves our picture" in content
    assert [f.filename for f in kw["files"]] == ["ours.jpg", "youtube-serves.jpg"]
    assert [c.custom_id for c in kw["view"].children] == ["tc:check:pw 2026-11-04_1400"]  # it matches: no Set it again
    asyncio.run(discord_bot.act_check(bot, Interaction(), "zz"))
    assert replies[-1][0].startswith("🔎 no channel 'zz'")

    monkeypatch.setattr(doctor, "set_again", lambda channel, key, settings=None, youtube=None: "set, but YouTube shows another picture (distance 50)")
    asyncio.run(discord_bot.act_thumb(bot, Interaction(), "pw 2026-11-04_1400"))
    assert replies[-1][0].startswith("🖼️ Set again, but YouTube still serves something else (YouTube shows another picture")
    monkeypatch.setattr(doctor, "set_again", lambda channel, key, settings=None, youtube=None: "set")
    asyncio.run(discord_bot.act_thumb(bot, Interaction(), "pw 2026-11-04_1400"))
    assert replies[-1][0] == "🖼️ Set again, and YouTube serves it now ✅"

    choices = asyncio.run(discord_bot.check_video_choices(SimpleNamespace(namespace=Interaction.namespace, client=bot), "coffee"))
    assert [c.value for c in choices] == ["2026-11-04_1400"]
