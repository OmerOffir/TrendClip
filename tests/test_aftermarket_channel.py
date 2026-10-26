"""After Market as a channel: the day's jobs, make → check → announce → upload, the veto, the bot's slots and
the tab's endpoints."""

from __future__ import annotations

import asyncio
import json
from datetime import date as Day, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from modules import am_channel, am_data, channel_common
from tests.test_aftermarket import facts_fixture
from trendclip import assistant, hub, publish
from trendclip.config import get_settings


def at(settings, day: Day, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=settings.tz)


MON, SAT, SUN, THANKSGIVING = Day(2026, 10, 26), Day(2026, 10, 31), Day(2026, 11, 1), Day(2026, 11, 26)


def test_slots_lead_and_settings(monkeypatch):
    assert am_channel.slots() == ["15:00", "23:00"] and am_channel.lead_minutes() == 20
    assert am_channel.auto_post() and am_channel.upload_privacy() == "public" and am_channel.category() == "25"
    monkeypatch.setenv("AM_SLOTS", "16:00,bad")
    monkeypatch.setenv("AM_LEAD", "999")
    monkeypatch.setenv("AM_AUTO_POST", "off")
    monkeypatch.setenv("AM_UPLOAD_PRIVACY", "unlisted")
    monkeypatch.setenv("AM_CATEGORY", "27")
    assert am_channel.slots() == ["16:00"] and am_channel.lead_minutes() == 20 and not am_channel.auto_post()
    assert am_channel.upload_privacy() == "unlisted" and am_channel.category() == "27"


def test_the_days_jobs_and_when_they_are_due():
    settings = get_settings()
    jobs = am_channel.jobs_for(settings, MON)
    assert [(j.slot, j.kind, j.start.strftime("%H:%M"), j.post_at.strftime("%H:%M")) for j in jobs] == [
        ("15:00", "premarket", "14:40", "15:00"), ("23:00", "close", "23:00", "23:00")]
    assert jobs[0].key == "am 2026-10-26 15:00 make" and jobs[0].video_key == "2026-10-26_premarket" and not jobs[0].focus
    for day, focus in ((SAT, "the week in review"), (SUN, "the week ahead"), (THANKSGIVING, "the week ahead")):
        weekend = am_channel.jobs_for(settings, day)
        assert [(j.slot, j.kind, j.focus) for j in weekend] == [("15:00", "weekend", focus)], day

    due = lambda h, m, sent=frozenset(): [j.key for j in am_channel.due_jobs(settings, at(settings, MON, h, m), set(sent))]  # noqa: E731
    assert due(14, 39) == [] and due(14, 40) == ["am 2026-10-26 15:00 make"] and due(16, 39) == ["am 2026-10-26 15:00 make"]
    assert due(16, 40) == []  # missed by two hours: the brief would be stale
    assert due(14, 45, {"am 2026-10-26 15:00 make"}) == []
    assert due(23, 0) == ["am 2026-10-26 23:00 make"]
    assert [j.key for j in am_channel.due_jobs(settings, at(settings, MON + timedelta(days=1), 0, 30), set())] == ["am 2026-10-26 23:00 make"]
    nxt = am_channel.next_job(settings, at(settings, MON, 23, 30))
    assert (nxt.day, nxt.kind) == (MON + timedelta(days=1), "premarket")
    nxt = am_channel.next_job(settings, at(settings, Day(2026, 10, 30), 23, 30))
    assert (nxt.day, nxt.focus) == (SAT, "the week in review")
    adhoc = am_channel.job_for(settings, MON, "weekend")
    assert adhoc.kind == "weekend" and adhoc.focus == "the week ahead" and am_channel.job_for(settings, MON, "close").slot == "23:00"


def test_due_jobs_respect_auto_post(monkeypatch):
    settings = get_settings()
    monkeypatch.setenv("AM_AUTO_POST", "0")
    assert am_channel.due_jobs(settings, at(settings, MON, 14, 45), set()) == []


def test_check_video(monkeypatch):
    good = {"duration_seconds": 58.0, "pictures": "local", "words": 130}
    assert am_channel.check_video(good) == []
    assert am_channel.check_video({**good, "duration_seconds": 60.4}) == ["60 s long (wanted 20-60)"]
    assert am_channel.check_video({**good, "duration_seconds": 60.0}) == []
    assert am_channel.check_video({**good, "pictures": "placeholder (local: boom)"}) == ["placeholder pictures (placeholder (local: boom))"]
    assert am_channel.check_video({**good, "words": 20}) == ["only 20 words"]
    monkeypatch.setenv("AM_ALLOW_PLACEHOLDER", "1")
    assert am_channel.check_video({**good, "pictures": "placeholder"}) == []


def _fake_produce(out, seconds=52.0):
    def produce(kind, progress, *, facts=None, template=False, out_dir=None, settings=None, images=None):
        work = out / f"{facts['date']}_{facts['slot']}"
        work.mkdir(parents=True, exist_ok=True)
        (work / "video.mp4").write_bytes(b"\x00" * 32)
        info = {"slot": facts["slot"], "date": facts["date"], "title": f"TSLA +6% {kind}", "filename": "video.mp4",
                "description": "Hook.\n\nNot financial advice.", "hashtags": ["stocks", "shorts"], "duration_seconds": seconds,
                "pictures": "local", "words": 120, "writer": "template" if template else "gemini", "created_at": "2026-10-26T15:00:00",
                "music": {"title": "Deep", "credit": ""}}
        (work / "info.json").write_text(json.dumps(info))
        return info
    return produce


class FakeRest:
    posts: list = []

    def __init__(self, settings):
        self.channel = None

    def post(self, content, file=None, components=None):
        self.posts.append((self.channel, content, file, components))
        return f"msg{len(self.posts)}"


@pytest.fixture
def channel_folder(tmp_path, monkeypatch):
    folder = tmp_path / "am"
    folder.mkdir()
    (folder / "channel.env").write_text("CHANNEL_HANDLE=@AfterMarketDaily\nDISCORD_CHANNEL_ID=1563997738666819707\n")
    monkeypatch.setattr(am_channel, "DIR", folder)
    monkeypatch.setattr(am_channel, "TOKEN", folder / "token_youtube.json")
    monkeypatch.setattr(hub, "Rest", FakeRest)
    FakeRest.posts = []
    return folder


def test_make_announces_with_a_veto_and_refuses_a_long_video(tmp_path, monkeypatch, channel_folder):
    settings = get_settings().with_overrides(discord_bot_token="tok")
    out = tmp_path / "videos"
    built = []

    def build(kind, now=None, progress=lambda m: None, **kw):
        built.append((kind, now))
        return facts_fixture(kind)

    monkeypatch.setattr(am_data, "build", build)
    job = am_channel.jobs_for(settings, MON)[0]
    info = am_channel.make(settings, job, out_dir=out, produce=_fake_produce(out), announce=True)
    assert built == [("premarket", None)] and info["key"] == "2026-10-26_premarket" and info["check"] == []
    assert info["job"] == {"day": "2026-10-26", "slot": "15:00", "post_at": "2026-10-26T15:00+02:00"}
    assert am_channel.load_info("2026-10-26_premarket", out)["title"] == "TSLA +6% premarket"
    assert len(FakeRest.posts) == 1
    channel_id, text, file, components = FakeRest.posts[0]
    assert channel_id == 1563997738666819707 and file[0] == "2026-10-26_premarket.mp4"
    assert "TSLA +6% premarket" in text and "goes out by itself at 15:00 as public" in text
    assert [b["custom_id"] for b in components[0]["components"]] == ["tc:amveto:2026-10-26_premarket", "tc:amup:2026-10-26_premarket"]

    weekend = am_channel.jobs_for(settings, SAT)[0]
    monkeypatch.setattr(am_data, "build", lambda kind, now=None, progress=None, **kw: {**facts_fixture(kind), "date": "2026-10-31"})
    with pytest.raises(am_channel.ChannelError, match="can't go out: 64 s long"):
        am_channel.make(settings, weekend, out_dir=out, produce=_fake_produce(out, seconds=64.0))
    saved = json.loads(am_data.facts_path("2026-10-31", "weekend", out).read_text())
    assert saved["focus"] == "the week in review"
    bad = am_channel.load_info("2026-10-31_weekend", out)
    assert bad["check"] == ["64 s long (wanted 20-60)"]
    assert am_channel.publish_if_allowed("2026-10-31_weekend", out_dir=out) is None  # never uploaded
    assert [v["key"] for v in am_channel.videos(out)] == ["2026-10-31_weekend", "2026-10-26_premarket"]
    assert am_channel.latest_facts(out)["key"] == "2026-10-31_weekend"
    assert am_channel.facts_lines(None) == "No session read yet." and "premarket" in am_channel.facts_lines(facts_fixture("premarket"))


class FakeYouTube:
    def __init__(self):
        self.updates = []

    def videos(self):
        yt = self

        class Videos:
            def update(self, part, body):
                yt.updates.append((part, body))
                return self

            def execute(self):
                return {}
        return Videos()


def test_upload_veto_and_the_slot_time_publish(tmp_path, monkeypatch, channel_folder):
    settings = get_settings().with_overrides(discord_bot_token="tok")
    out = tmp_path / "videos"
    monkeypatch.setattr(am_data, "build", lambda kind, now=None, progress=None, **kw: facts_fixture(kind))
    job = am_channel.jobs_for(settings, MON)[1]
    info = am_channel.make(settings, job, out_dir=out, produce=_fake_produce(out))
    key = info["key"]
    req = am_channel.upload_request(info)
    assert (req.category_id, req.privacy, req.as_short, req.tags) == ("25", "public", True, ["stocks", "shorts"])
    assert req.title == "TSLA +6% close" and "Not financial advice" in req.description

    with pytest.raises(publish.PublishError, match="Connect the After Market"):
        am_channel.upload(key, out_dir=out)
    uploaded = []

    def upload_file(path, req, as_short, progress, youtube):
        uploaded.append((path.name, req.privacy, req.category_id))
        return {"video_id": "vid1", "url": "https://youtube.com/shorts/vid1", "privacy": req.privacy, "studio_url": "https://s/vid1"}

    monkeypatch.setattr(publish, "upload_file", upload_file)
    yt = FakeYouTube()
    record = am_channel.publish_if_allowed(key, out_dir=out, youtube=yt, settings=settings)
    assert uploaded == [("video.mp4", "public", "25")] and record["video_id"] == "vid1"
    assert am_channel.load_info(key, out)["uploads"]["youtube"]["url"] == "https://youtube.com/shorts/vid1"
    assert "is on YouTube (public)" in FakeRest.posts[-1][1]
    assert am_channel.publish_if_allowed(key, out_dir=out, youtube=yt) == record and len(uploaded) == 1  # not twice

    # vetoed after the upload: the video goes private
    result = am_channel.veto(key, out, youtube=yt)
    assert result == {"key": key, "title": "TSLA +6% close", "uploaded": True, "hidden": True}
    assert yt.updates == [("status", {"id": "vid1", "status": {"privacyStatus": "private"}})]
    assert am_channel.load_info(key, out)["uploads"]["youtube"]["privacy"] == "private"
    assert am_channel.video_buttons(key, am_channel.load_info(key, out))[0]["components"][0]["style"] == 5  # only the link stays

    # vetoed before the upload: nothing goes out
    monkeypatch.setattr(am_data, "build", lambda kind, now=None, progress=None, **kw: {**facts_fixture(kind), "date": "2026-10-27"})
    info2 = am_channel.make(settings, am_channel.job_for(settings, Day(2026, 10, 27), "premarket"), out_dir=out, produce=_fake_produce(out))
    assert am_channel.veto(info2["key"], out, youtube=yt)["hidden"] is False
    assert am_channel.publish_if_allowed(info2["key"], out_dir=out, youtube=yt) is None and len(uploaded) == 1
    with pytest.raises(KeyError):
        am_channel.veto("2030-01-01_close", out)

    # the dashboard's run: make, then upload at once
    monkeypatch.setattr(am_data, "build", lambda kind, now=None, progress=None, **kw: {**facts_fixture(kind), "date": "2026-10-28"})
    run = am_channel.run(settings, am_channel.job_for(settings, Day(2026, 10, 28), "close"), out_dir=out, produce=_fake_produce(out), youtube=yt)
    assert run["error"] is None and run["record"]["video_id"] == "vid1" and len(uploaded) == 2
    run = am_channel.run(settings, am_channel.job_for(settings, Day(2026, 10, 28), "premarket"), out_dir=out,
                         produce=_fake_produce(out, seconds=70), youtube=yt)
    assert run["record"] is None and "70 s long" in run["error"] and len(uploaded) == 2
    assert FakeRest.posts[-1][1].startswith("❌ TSLA +6% premarket can't go out")


def test_shared_channel_helpers(tmp_path):
    folder = tmp_path / "x"
    assert channel_common.channel_env(folder) == {} and channel_common.handle(folder) == "" and not channel_common.connected(folder)
    folder.mkdir()
    (folder / "channel.env").write_text("CHANNEL_HANDLE=nohandle\nDISCORD_CHANNEL_ID=12\n")
    assert channel_common.handle(folder) == "" and channel_common.discord_channel_id(folder) == 12
    assert channel_common.channel_dir("darkarchives").name == "darkarchives"
    assert channel_common.rows(channel_common.button("amveto", "k", "x", "danger"), None, channel_common.link_button("w", "u")) == [
        {"type": 1, "components": [{"type": 2, "style": 4, "label": "x", "custom_id": "tc:amveto:k"}, {"type": 2, "style": 5, "label": "w", "url": "u"}]}]


# --------------------------------------------------------------------------- the bot


class Channel:
    def __init__(self):
        self.sent = []

    async def send(self, content="", **kw):
        self.sent.append((content, kw.get("view")))
        return self

    async def edit(self, content="", **kw):
        return self


def test_the_bot_runs_each_slot_once_shows_the_video_and_uploads(tmp_path, monkeypatch, channel_folder):
    from trendclip import discord_bot

    settings = get_settings().with_overrides(output_dir=tmp_path / "output", discord_bot_token="tok")
    out = tmp_path / "videos"
    monkeypatch.setattr(am_data, "build", lambda kind, now=None, progress=None, **kw: facts_fixture(kind))
    monkeypatch.setattr(am_channel.am_video, "OUTPUT_DIR", out)
    monkeypatch.setattr(am_channel.am_video, "produce", _fake_produce(out))
    uploads = []
    monkeypatch.setattr(am_channel, "upload", lambda key, privacy=None, progress=None, youtube=None, out_dir=None, settings=None: (
        uploads.append(key) or {"url": "https://youtube.com/shorts/v", "privacy": "public", "video_id": "v"}))
    clock = [at(settings, MON, 14, 40)]
    monkeypatch.setattr(assistant, "now", lambda s: clock[0])
    channel = Channel()
    started = []

    class Bot:
        render_lock = asyncio.Lock()
        aftermarket_jobs = discord_bot.TrendClipBot.aftermarket_jobs
        aftermarket_run = discord_bot.TrendClipBot.aftermarket_run
        send_file_video = discord_bot.TrendClipBot.send_file_video
        _job_done = discord_bot.TrendClipBot._job_done
        fail = False

        def __init__(self):
            self.settings = settings
            self.sent = assistant.SentLog(settings)
            self.jobs = set()

        async def aftermarket_channel(self):
            return channel

        async def run_job(self, ch, title, fn, lock, attempts=1, give_up=lambda: False):
            assert attempts == discord_bot.AM_ATTEMPTS and give_up() is False
            started.append(title)
            clock[0] += timedelta(minutes=20)  # the render takes a while: the slot's time has come when it is done
            if self.fail:
                raise discord_bot.JobFailed("ffmpeg")
            return fn(lambda f, m: None)

    async def tick(bot):
        bot.aftermarket_jobs(clock[0])
        while bot.jobs:
            await asyncio.gather(*list(bot.jobs))

    bot = Bot()
    asyncio.run(tick(bot))
    assert started == ["📈 After Market, Today (Mon 26 Oct) 15:00 · premarket"]
    text, view = channel.sent[0]
    assert "TSLA +6% premarket" in text and "Uploading it now as public" in text and "Don't post this" in text
    assert [c.custom_id for c in view.children] == ["tc:amveto:2026-10-26_premarket"]
    assert uploads == ["2026-10-26_premarket"]
    assert "am 2026-10-26 15:00 make" in bot.sent.load()

    clock[0] = at(settings, MON, 14, 50)
    asyncio.run(tick(bot))  # the same slot is never started twice
    assert len(started) == 1 and len(uploads) == 1

    clock[0] = at(settings, MON, 23, 0)
    bot.fail = True
    asyncio.run(tick(bot))
    assert started[-1].endswith("23:00 · close") and len(uploads) == 1
    assert channel.sent[-1][0].startswith("📈 No After Market video for Today (Mon 26 Oct) 23:00 · close: nothing was uploaded")
    assert [c.custom_id for c in channel.sent[-1][1].children] == ["tc:amrun:2026-10-26 close"]

    # a veto from the button, and "make another" through the button
    bot.fail = False
    replies = []

    async def fake_reply(interaction, content, **kw):
        replies.append((content, kw.get("view")))

    monkeypatch.setattr(discord_bot, "reply", fake_reply)
    monkeypatch.setattr(am_channel, "veto", lambda key, out_dir=None, youtube=None: {"key": key, "title": "T", "uploaded": True, "hidden": True})
    asyncio.run(discord_bot.act_am_veto(bot, None, "2026-10-26_premarket"))
    assert replies[-1][0] == "🚫 **T** is private on YouTube now." and [c.custom_id for c in replies[-1][1].children] == ["tc:amrun:2026-10-26 premarket"]
    asyncio.run(discord_bot.act_am_run(bot, None, "2026-10-26 nope"))
    assert replies[-1][0].startswith("Couldn't read the job")

    async def run_button():
        await discord_bot.act_am_run(bot, None, "2026-10-26 close")
        while bot.jobs:
            await asyncio.gather(*list(bot.jobs))

    asyncio.run(run_button())
    assert replies[-1][0].startswith("📈 Making the After Market close video") and uploads[-1] == "2026-10-26_close"


# --------------------------------------------------------------------------- the tab


def test_the_tab(tmp_path, monkeypatch, channel_folder):
    from trendclip.web import aftermarket as tab
    from trendclip.web import app as web

    out = tmp_path / "videos"
    monkeypatch.setattr(am_channel.am_video, "OUTPUT_DIR", out)
    monkeypatch.setattr(am_data, "OUTPUT_DIR", out)
    monkeypatch.setattr(am_data, "build", lambda kind, now=None, progress=None, **kw: facts_fixture(kind))
    monkeypatch.setattr(am_channel.am_video, "produce", _fake_produce(out))
    monkeypatch.setattr(tab, "_run_job", {"running": False})
    client = TestClient(web.app)

    s = client.get("/api/aftermarket/status").json()
    assert s["slots"] == ["15:00", "23:00"] and s["folder"] and s["discord"] and s["facts"] is None and s["handle"] == "@AfterMarketDaily"
    assert [j["kind"] for j in s["today"]] in (["premarket", "close"], ["weekend"])
    assert client.get("/api/aftermarket/videos").json()["videos"] == []
    assert client.get("/api/aftermarket/videos/2026-10-26_close/video").status_code == 404
    assert client.get("/api/aftermarket/videos/../x/video").status_code == 404

    r = client.post("/api/aftermarket/run", json={"kind": "close", "upload": False})
    assert r.status_code == 202 and r.json()["running"] and r.json()["job"]["kind"] == "close"
    for _ in range(100):
        job = client.get("/api/aftermarket/run-job").json()
        if not job["running"]:
            break
        import time

        time.sleep(0.05)
    assert job["result"] == {"key": "2026-10-26_close", "title": "TSLA +6% close", "record": None, "error": None}
    assert client.post("/api/aftermarket/run", json={"kind": "nope"}).status_code == 422

    videos = client.get("/api/aftermarket/videos").json()["videos"]
    assert [v["key"] for v in videos] == ["2026-10-26_close"] and videos[0]["youtube_texts"]["title"].endswith("#shorts")
    assert client.get("/api/aftermarket/videos/2026-10-26_close/video").status_code == 200
    assert client.get("/api/aftermarket/videos/2026-10-26_close/facts").json()["slot"] == "close"
    s = client.get("/api/aftermarket/status").json()
    assert s["facts"]["key"] == "2026-10-26_close" and s["facts"]["movers"][0]["symbol"] == "TSLA" and "TSLA" in s["facts"]["summary"]

    r = client.post("/api/aftermarket/videos/2026-10-26_close/upload", json={})
    assert r.status_code == 400 and "Connect the After Market" in r.json()["detail"]
    assert client.post("/api/aftermarket/videos/2026-10-26_close/veto").json()["hidden"] is False
    assert am_channel.load_info("2026-10-26_close", out)["veto"]
    assert client.get("/api/aftermarket/youtube/status").json()["connected"] is False
    assert client.post("/api/aftermarket/youtube/logout").json() == {"connected": False}
