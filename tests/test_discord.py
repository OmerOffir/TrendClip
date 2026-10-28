from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


import pytest

from trendclip import assistant, planner, publish, quiz, script_writer, shorts, video_downloader
from trendclip.config import Settings
from trendclip.video_downloader import BackgroundClip, DownloadError

from tests.test_upload import add_short

DAY = date(2026, 10, 7)


@pytest.fixture
def settings(tmp_path):
    # The production run (AUTO_PRODUCE) has its own tests; off here so the reminder tests stay about one thing.
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output",
                    discord_morning_time="10:00", discord_reminder_minutes=30, discord_leaderboard_time="",
                    auto_produce=False)


def at(settings, hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=settings.tz)


@pytest.fixture
def clock(settings, monkeypatch):
    """assistant.now() returns clock[0]."""
    current = [at(settings, 12)]
    monkeypatch.setattr(assistant, "now", lambda s: current[0])
    monkeypatch.setattr(assistant, "today", lambda s: current[0].date())
    return current


# --------------------------------------------------------------------------- slots


def test_a_slot_holds_one_item_and_moves_take_a_free_slot(settings):
    a, b, c = (add_short(settings, f"{n}.mp4") for n in "abc")
    first = planner.add_item(settings, planner.ItemRequest(date=DAY, slot="14:30", short=a))
    with pytest.raises(ValueError, match="already has"):
        planner.add_item(settings, planner.ItemRequest(date=DAY, slot="14:30", short=b))
    second = planner.add_item(settings, planner.ItemRequest(date=DAY + timedelta(days=1), slot="14:30", short=b))

    moved = planner.update_item(settings, second.id, planner.ItemUpdate(date=DAY))  # dragged onto a taken slot
    assert moved.date == DAY and moved.slot == "23:00"
    third = planner.add_item(settings, planner.ItemRequest(date=DAY + timedelta(days=1), slot="14:30", short=c))
    assert planner.update_item(settings, third.id, planner.ItemUpdate(date=DAY)).slot is None  # day is full
    with pytest.raises(ValueError):
        planner.update_item(settings, third.id, planner.ItemUpdate(slot="14:30"))
    assert planner.update_item(settings, first.id, planner.ItemUpdate(slot="")).slot is None
    assert planner.free_slots(settings, DAY) == ["14:30"]
    assert [i["slot"] for i in planner.view(settings)["items"]] == ["23:00", None, None]


def test_new_reels_take_the_first_free_slot_unless_told_otherwise(settings):
    a, b, c = (add_short(settings, f"{n}.mp4") for n in "abc")
    assert planner.add_item(settings, planner.ItemRequest(date=DAY, short=a)).slot == "14:30"
    assert planner.add_item(settings, planner.ItemRequest(date=DAY, short=b, slot="")).slot is None
    assert planner.add_item(settings, planner.ItemRequest(date=DAY, short=c)).slot == "23:00"
    assert planner.add_item(settings, planner.ItemRequest(kind="long", date=DAY, title="Long")).slot is None


def test_old_plans_get_their_times_once(settings):
    names = [add_short(settings, f"{n}.mp4") for n in "abcd"]
    planner._save(settings, planner.Plan(items=[
        planner.PlanItem(id=str(i), kind="reel", date=day, short=n, title=n)
        for i, (day, n) in enumerate([(DAY - timedelta(days=1), names[0]), (DAY, names[1]), (DAY, names[2]),
                                      (DAY, names[3])])
    ]))
    assert planner.assign_slots(settings, DAY) == 2
    assert [i.slot for i in planner.load(settings).items] == [None, "14:30", "23:00", None]  # past day untouched
    planner.update_item(settings, "1", planner.ItemUpdate(slot=""))
    assert planner.assign_slots(settings, DAY) == 0  # only once: later "no fixed time" choices are kept
    assert planner.load(settings).items[1].slot is None


def test_next_free_slot_skips_passed_and_taken_slots(settings):
    add_short(settings, "a.mp4")
    planner.add_item(settings, planner.ItemRequest(date=DAY, slot="23:00", short="a.mp4"))
    assert planner.next_free_slot(settings, at(settings, 12)) == (DAY, "14:30")
    assert planner.next_free_slot(settings, at(settings, 18, 30)) == (DAY + timedelta(days=1), "14:30")
    assert planner.slot_time(settings, DAY, "23:00").utcoffset() == timedelta(hours=3)  # Israel summer time


def test_slots_through_the_api(settings, monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    monkeypatch.setattr(web, "_base_settings", lambda: settings)
    client = TestClient(web.app)
    name = add_short(settings)
    r = client.post("/api/plan/items", json={"date": "2026-10-07", "slot": "23:00", "short": name})
    assert r.status_code == 200 and r.json()["slot"] == "23:00"
    assert client.post("/api/plan/items", json={"date": "2026-10-07", "slot": "23:00", "title": "x"}).status_code == 400
    assert client.post("/api/plan/items", json={"date": "2026-10-07", "slot": "19:00", "title": "x"}).status_code == 422
    r = client.patch(f"/api/plan/items/{r.json()['id']}", json={"slot": ""})
    assert r.json()["slot"] is None
    assert client.get("/api/plan").json()["slots"] == ["14:30", "23:00"]


# --------------------------------------------------------------------------- plan helpers


def test_parse_day(settings, clock):
    assert assistant.parse_day(settings, None) == DAY
    assert assistant.parse_day(settings, "Tomorrow") == DAY + timedelta(days=1)
    assert assistant.parse_day(settings, "+3") == DAY + timedelta(days=3)
    assert assistant.parse_day(settings, "2026-12-01") == date(2026, 12, 1)
    with pytest.raises(ValueError):
        assistant.parse_day(settings, "someday")


def test_plan_short_fills_the_next_free_slot(settings, clock):
    a, b, c = (add_short(settings, f"{n}.mp4") for n in "abc")
    assert (assistant.plan_short(settings, a).date, planner.load(settings).items[0].slot) == (DAY, "14:30")
    item = assistant.plan_short(settings, b, DAY)
    assert item.slot == "23:00"
    with pytest.raises(ValueError, match="full"):
        assistant.plan_short(settings, c, DAY)
    assert assistant.plan_short(settings, c).date == DAY + timedelta(days=1)

    entries = assistant.day_slots(settings, DAY)
    assert [(e.slot, e.short.filename) for e in entries] == [("14:30", a), ("23:00", b)]
    assert assistant.backlog(settings) == []
    removed = assistant.unplan(settings, item.id)
    assert removed.short == b and [s.filename for s in assistant.backlog(settings)] == [b]
    moved = assistant.move(settings, planner.load(settings).items[0].id, DAY + timedelta(days=2), "23:00")
    assert (moved.date, moved.slot) == (DAY + timedelta(days=2), "23:00")


def test_uploaded_shorts_are_not_in_the_backlog(settings):
    add_short(settings, "a.mp4", uploads={"youtube": {"video_id": "x", "url": "u"}})
    add_short(settings, "b.mp4")
    assert [s.filename for s in assistant.backlog(settings)] == ["b.mp4"]


# --------------------------------------------------------------------------- reminders


def test_reminders_fire_once_in_their_window(settings):
    settings = settings.with_overrides(emergency_video=False)
    assert assistant.due_reminders(settings, at(settings, 9, 59), set()) == []
    morning = assistant.due_reminders(settings, at(settings, 10), set())
    assert [r.key for r in morning] == ["2026-10-07 morning"]
    assert assistant.due_reminders(settings, at(settings, 16, 31), set()) == []  # summary missed by > 6 h
    slot = assistant.due_reminders(settings, at(settings, 14), {"2026-10-07 morning"})
    assert [(r.kind, r.slot) for r in slot] == [("slot", "14:30")]
    post = assistant.due_reminders(settings, at(settings, 14, 30), {"2026-10-07 morning"})
    assert [(r.kind, r.slot, r.key) for r in post] == [("post", "14:30", "2026-10-07 14:30 post")]
    sent = {"2026-10-07 morning", "2026-10-07 14:30 post"}
    assert assistant.due_reminders(settings, at(settings, 14, 31), sent) == []  # posted once
    assert [r.kind for r in assistant.due_reminders(settings, at(settings, 16, 29), {"2026-10-07 morning"})] == \
        ["post"]  # the bot was off at 14:30: it still posts within 2 h
    late = assistant.due_reminders(settings, at(settings, 22, 45), {"2026-10-07 morning"})
    assert [r.key for r in late] == ["2026-10-07 23:00"]
    quiet = settings.with_overrides(discord_reminder_minutes=0, auto_post=False)
    assert assistant.due_reminders(quiet, at(settings, 22, 45), {"2026-10-07 morning"}) == []
    assert assistant.due_reminders(quiet, at(settings, 23, 0), {"2026-10-07 morning"}) == []


def test_an_empty_slot_gets_an_emergency_video_an_hour_before(settings):
    seen = {"2026-10-07 morning"}
    assert [r.kind for r in assistant.due_reminders(settings, at(settings, 13, 29), seen)] == []
    due = assistant.due_reminders(settings, at(settings, 13, 30), seen)
    assert [(r.kind, r.slot, r.key) for r in due] == [("emergency", "14:30", "2026-10-07 14:30 emergency")]
    assert assistant.due_reminders(settings, at(settings, 13, 31), seen | {"2026-10-07 14:30 emergency"}) == []
    assistant.plan_short(settings, add_short(settings, "a.mp4"), DAY, "23:00")
    assert "emergency" not in [r.kind for r in assistant.due_reminders(settings, at(settings, 22), seen)]
    off = settings.with_overrides(emergency_video=False)
    assert assistant.due_reminders(off, at(settings, 13, 30), seen) == []


def _emergency_bot(settings, made, posted):
    import asyncio

    from trendclip import discord_bot

    class Channel:
        def __init__(self):
            self.sent = []

        async def send(self, content="", **kw):
            self.sent.append(content)

    class Bot:
        render_lock = asyncio.Lock()
        emergency: set = set()
        emergency_video = discord_bot.TrendClipBot.emergency_video

        def __init__(self):
            self.settings = settings
            self.sent = assistant.SentLog(settings)

        async def run_job(self, channel, title, fn, lock, attempts=1, give_up=lambda: False):
            made.append(title)
            assert attempts == discord_bot.EMERGENCY_ATTEMPTS and give_up() is False
            return fn(lambda f, m: None)

        async def send_video(self, channel, filename, content, view=None, followup=None):
            channel.sent.append(content)

        async def auto_post(self, channel, day, slot):
            posted.append(slot)

    return Bot(), Channel()


def test_the_emergency_video_is_made_and_planned_in_the_empty_slot(settings, monkeypatch, clock):
    import asyncio

    from trendclip import discord_bot

    from trendclip import analytics

    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s: "key")
    asked = []

    def plan(s, games=None, rng=None, day=None):  # the numbers-based choice, restricted to the emergency games
        asked.append(games)
        return analytics.Pick(mode="math", game="Minecraft", days=60, videos=9)

    monkeypatch.setattr(discord_bot, "plan_pick", plan)
    kinds = []

    def create(s, game, progress, kind, only, target_seconds=None):
        kinds.append((game, kind))
        assert only == ("GTA V / Online", "Minecraft") and target_seconds is None
        return shorts.get_short(s, add_short(s, "e.mp4"))

    monkeypatch.setattr(assistant, "create_random_short", create)
    made, posted = [], []
    bot, channel = _emergency_bot(settings, made, posted)
    clock[0] = at(settings, 13, 30, day=DAY)
    asyncio.run(bot.emergency_video(channel, DAY, "14:30"))
    assert kinds == [("Minecraft", "math")] and made == ["Emergency math challenge for 14:30"]
    assert asked == [["GTA V / Online", "Minecraft"]]
    assert "with Minecraft gameplay" in channel.sent[0] and "🎲 Picked by the last 60 days' numbers" in channel.sent[0]

    assert planner.slot_item(settings, DAY, "14:30").short == "e.mp4"
    assert "goes out by itself at 14:30" in channel.sent[-1] and posted == []
    assert not bot.emergency

    clock[0] = at(settings, 23, 5, day=DAY)  # finished after its slot: posted right away, and only once
    asyncio.run(bot.emergency_video(channel, DAY, "23:00"))
    assert posted == ["23:00"] and assistant.post_key(DAY, "23:00") in bot.sent.load()

    made.clear()
    asyncio.run(bot.emergency_video(channel, DAY, "14:30"))  # the slot is taken now: nothing to make
    assert made == []


def test_a_failed_job_is_tried_again_until_it_works(monkeypatch):
    import asyncio

    from trendclip import discord_bot

    monkeypatch.setattr(discord_bot, "RETRY_WAIT", 0)

    class Msg:
        def __init__(self, log):
            self.log = log

        async def edit(self, content):
            self.log.append(content)

    class Channel:
        def __init__(self):
            self.log = []

        async def send(self, content):
            self.log.append(content)
            return Msg(self.log)

    def flaky(fails):
        calls = []

        def fn(progress):
            calls.append(1)
            if len(calls) <= fails:
                raise RuntimeError("ffmpeg exited with 1")
            return "video.mp4"
        return fn, calls

    run = discord_bot.TrendClipBot.run_job
    channel = Channel()
    fn, calls = flaky(2)
    assert asyncio.run(run(None, channel, "Creating a video", fn, asyncio.Lock(), attempts=3)) == "video.mp4"
    assert len(calls) == 3 and "attempt 1 failed (ffmpeg exited with 1)" in channel.log[1]
    assert channel.log[-1] == "✅ Creating a video: done (on attempt 3)"

    fn, calls = flaky(5)
    with pytest.raises(discord_bot.JobFailed):
        asyncio.run(run(None, Channel(), "Creating a video", fn, asyncio.Lock(), attempts=3))
    assert len(calls) == 3

    fn, calls = flaky(5)  # the slot was filled meanwhile: stop trying
    with pytest.raises(discord_bot.JobFailed):
        asyncio.run(run(None, Channel(), "Emergency", fn, asyncio.Lock(), attempts=10, give_up=lambda: True))
    assert len(calls) == 1


def test_sent_log_survives_restarts_and_forgets_old_days(settings):
    log = assistant.SentLog(settings)
    log.add("2026-09-01 morning", date(2026, 9, 1))
    log.add("2026-10-07 14:30", DAY)
    assert assistant.SentLog(settings).load() == {"2026-10-07 14:30"}


# --------------------------------------------------------------------------- videos


def test_upload_request_uses_the_upload_tab_texts(settings):
    name = add_short(settings)
    req = assistant.upload_request(settings, name)
    assert req.privacy == "public" and req.publish_at is None
    assert req.title == "The Day My Cat Outsmarted the Block #shorts"
    assert req.description.startswith("I searched for three hours.") and "Gameplay footage" not in req.description
    assert "GTA V / Online" in req.tags
    assert shorts.get_short(settings, name).texts  # saved, so the Upload tab shows the same texts


def test_upload_now_publishes_publicly(settings, monkeypatch):
    name = add_short(settings)
    seen = {}

    def fake_upload(s, filename, req, progress):
        seen.update(filename=filename, privacy=req.privacy)
        return {"url": "https://youtube.com/shorts/x"}

    monkeypatch.setattr(publish, "upload_youtube", fake_upload)
    assert assistant.upload_now(settings, name)["url"].endswith("/x")
    assert seen == {"filename": name, "privacy": "public"}


def test_small_videos_are_sent_as_they_are(settings):
    name = add_short(settings)
    assert assistant.discord_copy(settings, name) == settings.shorts_dir / name


def _clip(settings, game="Minecraft"):
    settings.backgrounds_dir.mkdir(parents=True, exist_ok=True)
    path = settings.backgrounds_dir / f"{game.lower()}_youtube_1.mp4"
    path.write_bytes(b"\x00")
    clip = BackgroundClip(path=str(path), filename=path.name, source="youtube", game=game, query="q", title="t",
                          source_url="https://youtu.be/abc", author="Orbital", license_note="")
    path.with_suffix(".json").write_text(clip.model_dump_json())
    return clip


def test_fresh_clip_falls_back_to_downloaded_clips(settings, monkeypatch):
    old = _clip(settings)

    def fail(*a, **k):
        raise DownloadError("offline")

    monkeypatch.setattr(video_downloader, "download_background", fail)
    assert assistant.fresh_clip(settings, "Minecraft").filename == old.filename
    other = _clip(settings, "Fortnite")
    only = ("GTA V / Online", "Minecraft")
    assert {assistant.fresh_clip(settings, "GTA V / Online", only=only).filename for _ in range(10)} == {old.filename}
    Path(other.path).unlink()
    Path(other.path).with_suffix(".json").unlink()
    old_path = Path(old.path)
    old_path.unlink()
    old_path.with_suffix(".json").unlink()
    with pytest.raises(DownloadError, match="no clips"):
        assistant.fresh_clip(settings, "Minecraft")


def test_create_random_short_writes_a_story_and_renders_it(settings, monkeypatch):
    clip = _clip(settings, "Roblox")
    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s=None: "key")
    monkeypatch.setattr(assistant, "random_game", lambda s: "Roblox")
    monkeypatch.setattr(video_downloader, "download_background", lambda game, s, **k: clip)
    monkeypatch.setattr(video_downloader, "probe_video", lambda p: {"duration_seconds": 60.0})
    asked = {}

    def fake_script(s, game, path, **kw):
        asked.update(game=game, **kw)
        return script_writer.ShortScript(
            on_screen="", hook="Hook.", script="Hook. A story.", title="My Story", description="d",
            hashtags=["#shorts"], game=game, model="m", watched_clip=False, word_count=3, estimated_seconds=2,
            music_mood="funny_quirky", pinned_comment="Ever done this?")

    rendered = []

    def fake_render(s, req, progress):
        rendered.append(req)
        if req.music_source == "mood":
            from trendclip.music import MusicError
            raise MusicError("no tracks")
        return shorts.ShortVideo(filename="roblox_1.mp4", game=req.game, title=req.title, description="",
                                 hashtags=[], script=req.script, voice="v", background=req.clip)

    monkeypatch.setattr(script_writer, "write_script", fake_script)
    monkeypatch.setattr(shorts, "render_short", fake_render)
    short = assistant.create_random_short(settings)
    assert short.title == "My Story" and asked["mode"] == "story" and asked["max_seconds"] == 60.0
    assert [r.music_source for r in rendered] == ["mood", "none"]  # retried without music
    assert rendered[0].music_mood == "funny_quirky" and rendered[0].pinned_comment == "Ever done this?"


def test_create_from_text_keeps_your_words(settings, monkeypatch):
    clip = _clip(settings, "GTA V")
    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s=None: "key")
    monkeypatch.setattr(video_downloader, "download_background", lambda game, s, **k: clip)
    monkeypatch.setattr(video_downloader, "probe_video", lambda p: {"duration_seconds": 50.0})
    asked = {}

    def fake_describe(s, game, script, **kw):
        asked.update(game=game, script=script, **kw)
        return script_writer.ShortScript(
            on_screen="", hook="x", script=script, title="Mine", description="d", hashtags=["#shorts"], game=game,
            model="m", watched_clip=False, word_count=9, estimated_seconds=3, flashes=[quiz.Flash(word="24", text="24")],
            answer="21")

    rendered = []
    monkeypatch.setattr(script_writer, "describe_script", fake_describe)
    monkeypatch.setattr(shorts, "render_short", lambda s, req, p: rendered.append(req) or shorts.ShortVideo(
        filename="gta_1.mp4", game=req.game, title=req.title, description="", hashtags=[], script=req.script,
        voice="v", background=req.clip, answer=req.answer))
    text = "Start with 24. Add 18. Divide by 2. Comment your answer!"
    short = assistant.create_from_text(settings, text, "GTA V", mode="math")
    assert asked["script"] == text and asked["mode"] == "math" and asked["max_seconds"] == 50.0
    assert asked["target_seconds"] >= settings.short_target_seconds
    assert rendered[0].flashes[0].text == "24" and short.answer == "21"
    with pytest.raises(ValueError):
        assistant.create_from_text(settings, "too short", "GTA V")


def test_own_text_waits_for_its_kind_then_renders(monkeypatch):
    import asyncio

    from trendclip import discord_bot

    made, said = [], []

    class Bot:
        texts = {}
        remember_text = discord_bot.TrendClipBot.remember_text

        async def create_random(self, interaction, game, kind, text=None):
            made.append((game, kind, text))

    async def fake_reply(interaction, content, **kw):
        said.append(content)

    monkeypatch.setattr(discord_bot, "reply", fake_reply)
    bot = Bot()
    key = bot.remember_text("I walked into the wrong wedding.", "GTA V")
    asyncio.run(discord_bot.act_make_from_text(bot, None, f"story:{key}"))
    assert made == [("GTA V", "story", "I walked into the wrong wedding.")] and not bot.texts
    asyncio.run(discord_bot.act_make_from_text(bot, None, f"math:{key}"))  # used up / bot restarted
    assert "don't have that text" in said[-1]
    for i in range(discord_bot.PENDING_TEXTS + 5):
        bot.remember_text(f"text {i}", None)
    assert len(bot.texts) == discord_bot.PENDING_TEXTS
    assert len(f"tc:mkt:trivia:{key}") <= discord_bot.CUSTOM_ID_MAX


# --------------------------------------------------------------------------- bot helpers


def test_buttons_carry_their_action_and_skip_too_long_ids():
    from trendclip import discord_bot

    b = discord_bot.button("pt", "1430:gta_1.mp4", "Plan today 14:30")
    assert b.item.custom_id == "tc:pt:1430:gta_1.mp4"
    assert discord_bot.button("show", "x" * 120, "Show") is None
    assert discord_bot.code_slot("2300") == "23:00"
    with pytest.raises(ValueError):
        discord_bot.code_slot("1900")
    assert set(discord_bot.ACTIONS) >= {"show", "up", "upgo", "pl", "pt", "rm", "mk"}


def test_create_asks_story_math_or_riddle_with_buttons(settings, monkeypatch):

    import asyncio

    from trendclip import discord_bot

    ids = [b.item.custom_id for b in discord_bot.kind_buttons("GTA V")]
    assert ids == ["tc:mk:pick:GTA V", "tc:mk:math:GTA V", "tc:mk:gamer:GTA V", "tc:mk:trivia:GTA V", "tc:mk:riddle:GTA V",
                   "tc:mk:movie:GTA V", "tc:mk:paradox:GTA V", "tc:mk:story:GTA V"]
    assert [b.item.custom_id for b in discord_bot.kind_buttons("x" * 120)] == [
        "tc:mk:pick", "tc:mk:math", "tc:mk:gamer", "tc:mk:trivia", "tc:mk:riddle", "tc:mk:movie", "tc:mk:paradox", "tc:mk:story"]
    import random

    from trendclip import analytics

    # Without AUTO_PICK: the fixed weights, a quiz kind, math most often.
    fixed = settings.with_overrides(auto_pick=False)
    picks = [discord_bot.plan_pick(fixed, ["GTA V"], random.Random(i)) for i in range(400)]
    assert "story" not in {p.mode for p in picks} and max({p.mode for p in picks}, key=[p.mode for p in picks].count) == "math"
    assert all(p.game == "GTA V" for p in picks)
    # With AUTO_PICK: the numbers decide; a broken analytics file falls back to the fixed weights.
    monkeypatch.setattr(analytics, "trending_games", lambda s: {"Minecraft": 5.0})  # no pipeline run in tests
    seen = {}

    def planned(s, games=None, rng=None, trending=None, kinds=None):
        seen["trending"] = trending
        return analytics.Pick(mode="story", game=(games or [None])[0])
    monkeypatch.setattr(analytics, "plan_video", planned)
    assert discord_bot.plan_pick(settings, ["Minecraft"]).model_dump(include={"mode", "game"}) == {"mode": "story", "game": "Minecraft"}
    assert seen["trending"] == {"Minecraft": 5.0}
    discord_bot.plan_pick(settings.with_overrides(auto_pick_trends=False), ["Minecraft"])
    assert seen["trending"] is None

    def broken(s, games=None, rng=None, trending=None):
        raise OSError("db locked")

    monkeypatch.setattr(analytics, "plan_video", broken)
    assert discord_bot.plan_pick(settings, ["Minecraft"], random.Random(1)).mode in discord_bot.PICK_WEIGHTS

    made, asked = [], []

    class Bot:
        settings = fixed

        async def create_random(self, interaction, game, kind, pick=None):
            made.append((game, kind, pick is not None))

    async def fake_ask(interaction, game):
        asked.append(game)

    monkeypatch.setattr(discord_bot, "ask_kind", fake_ask)
    monkeypatch.setattr(discord_bot, "library_games", lambda s: ["Minecraft"])
    asyncio.run(discord_bot.act_make(Bot(), None, "math:GTA V"))
    asyncio.run(discord_bot.act_make(Bot(), None, "riddle"))
    asyncio.run(discord_bot.act_make(Bot(), None, "-"))  # old "Create a random video" button
    asyncio.run(discord_bot.act_make(Bot(), None, "pick:GTA V"))  # the game stays; the kind is drawn
    asyncio.run(discord_bot.act_make(Bot(), None, "pick"))  # kind and game drawn (from the library's games)
    assert made[:2] == [("GTA V", "math", False), (None, "riddle", False)] and asked == [None]
    assert made[2][0] == "GTA V" and made[2][1] in discord_bot.PICK_WEIGHTS and made[2][2]
    assert made[3] == ("Minecraft", made[3][1], True) and made[3][1] in discord_bot.PICK_WEIGHTS



def test_caption_message_is_copy_ready_for_youtube_tiktok_and_instagram(settings):
    from trendclip import discord_bot

    short = shorts.get_short(settings, add_short(settings))
    text = discord_bot.caption_message(settings, short, "📝 `14:30` **The Day**")
    for label in ("▶️ YouTube", "📸 Instagram"):
        assert label in text
    assert "TikTok" not in text  # off by default (PLATFORMS)
    with_tiktok = discord_bot.caption_message(settings.with_overrides(platforms=["youtube", "instagram", "tiktok"]),
                                              short, "📝")
    assert "🎵 TikTok" in with_tiktok and discord_bot.platform_names(settings) == "YouTube and Instagram"

    assert "I searched for three hours" in text  # the description text
    assert "#storytime" in text and "#shorts" in text and "#fyp" in with_tiktok  # #fyp is TikTok's

    assert text.count("```") == 4 and with_tiktok.count("```") == 6 and len(text) <= discord_bot.MESSAGE_MAX



def test_replies_command_reads_queues_sends_and_reports(settings, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from trendclip import discord_bot, ig_replies, leaderboard

    said, steps = [], []

    class Response:
        async def defer(self, **kw):
            steps.append("defer")

    class Bot:
        def __init__(self, why=""):
            self.settings, self.why = settings, why

        def replies_on(self):
            return self.why

        async def check_comments(self, channel, wait=True):
            steps.append("read" if wait else "read now")
            return leaderboard.Board(channel_id="UCme")

        async def check_instagram(self, channel, wait=True):
            steps.append("ig read" if wait else "ig read now")
            return leaderboard.Board()

    async def fake_reply(interaction, content, **kw):
        said.append(content)

    monkeypatch.setattr(discord_bot, "reply", fake_reply)
    monkeypatch.setattr(leaderboard, "send_next_reply",
                        lambda s, ch, day: steps.append("send") or {"name": "amy"})
    monkeypatch.setattr(leaderboard, "reply_overview", lambda s, board, day: {
        "replied": 7, "queued": 3, "quiz_wait": 2, "next_quiz": "2026-10-15T00:00:00+00:00", "unchecked": 0,
        "skipped": 1, "failed": 0, "sent_today": 8, "daily_max": 100})

    monkeypatch.setattr(ig_replies, "enabled", lambda s: False)
    interaction = SimpleNamespace(client=Bot("YouTube isn't connected."), response=Response(), channel=None)
    asyncio.run(discord_bot.replies_cmd.callback(interaction))
    assert said == ["YouTube isn't connected."] and steps == []

    interaction.client = Bot()
    asyncio.run(discord_bot.replies_cmd.callback(interaction, now=True))
    assert steps == ["defer", "read now", "send"]
    steps.clear()
    interaction.response = Response()
    asyncio.run(discord_bot.replies_cmd.callback(interaction))
    assert steps == ["defer", "read", "send"]
    text = "\n".join(said[1:])
    assert "Replied: 7" in text and "In the queue: 3" in text and "just sent one to amy" in text
    assert "quiz / paradox videos" in text and "No reply (spam" in text and "8 of 100" in text
    assert "Not checked" not in text and "refused" not in text and "Instagram" not in text
    assert discord_bot.replies_cmd in discord_bot.COMMANDS

    monkeypatch.setattr(ig_replies, "enabled", lambda s: True)
    monkeypatch.setattr(ig_replies, "send_next_reply", lambda s, day: steps.append("ig send"))
    monkeypatch.setattr(ig_replies, "reply_overview", lambda s, board, day: {
        "replied": 0, "queued": 0, "quiz_wait": 0, "next_quiz": "", "unchecked": 0, "skipped": 0, "failed": 0,
        "sent_today": 0, "daily_max": 50, "paused_until": ""})
    said.clear(), steps.clear()
    interaction.client = Bot("YouTube isn't connected.")
    asyncio.run(discord_bot.replies_cmd.callback(interaction))
    assert steps == ["defer", "ig read", "ig send"]
    text = "\n".join(said)
    assert "YouTube isn't connected" in text and "Instagram replies" in text and "Development mode" in text


def test_a_planned_short_posts_itself_at_its_time_and_reports(settings, monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace

    from trendclip import discord_bot, instagram, planner, publish

    video = tmp_path / "q.mp4"
    video.write_bytes(b"\x00")
    short = SimpleNamespace(title="Only 3% get this", uploads={}, posted={}, pinned_comment="Answer below 👇")
    item = planner.PlanItem(id="i1", kind="reel", date=assistant.today(settings), slot="14:30", title=short.title,
                            short="q.mp4", platforms=["youtube", "instagram", "tiktok"])
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(planner, "slot_item", lambda s, day, slot: item)
    monkeypatch.setattr(discord_bot.shorts, "get_short", lambda s, f: short)
    monkeypatch.setattr(discord_bot.shorts, "short_path", lambda s, f: video)
    monkeypatch.setattr(publish, "TOKEN_FILE", token)
    monkeypatch.setattr(publish, "can_comment", lambda: True)
    monkeypatch.setattr(instagram, "configured", lambda s: True)
    monkeypatch.setattr(assistant, "pinned_comment", lambda s, f: "Answer below 👇")
    commented, ran = [], []
    monkeypatch.setattr(publish, "post_comment", lambda s, f, text: commented.append(text))
    monkeypatch.setattr(assistant, "upload_now", lambda s, f, p: {"url": "https://youtu.be/v1", "privacy": "public"})
    monkeypatch.setattr(instagram, "post_reel", lambda s, f, first_comment, progress: {
        "url": "https://instagram.com/reel/r1"})

    class Channel:
        def __init__(self):
            self.sent = []

        async def send(self, content="", **kw):
            self.sent.append((content, kw.get("view")))

    class Bot:
        settings_ = settings
        upload_lock = asyncio.Lock()
        auto_targets = discord_bot.TrendClipBot.auto_targets
        auto_post = discord_bot.TrendClipBot.auto_post

        @property
        def settings(self):
            return self.settings_

        async def run_job(self, channel, title, fn, lock):
            ran.append(title)
            try:
                return fn(lambda f, m: None)
            except Exception as err:
                raise discord_bot.JobFailed(str(err)) from err

    channel = Channel()
    asyncio.run(Bot().auto_post(channel, item.date, "14:30"))
    assert ran == ["YouTube upload", "Instagram post"]
    assert "posting \"Only 3% get this\" on YouTube and Instagram" in channel.sent[0][0]
    report = channel.sent[-1][0]
    assert report.startswith("✅") and "https://youtu.be/v1" in report and "https://instagram.com/reel/r1" in report
    assert "TikTok has no upload API" in report and commented == ["Answer below 👇"]

    def broken(*a, **kw):
        raise instagram.InstagramError("Instagram: token expired")

    monkeypatch.setattr(instagram, "post_reel", broken)
    channel = Channel()
    asyncio.run(Bot().auto_post(channel, item.date, "14:30"))
    report, view = channel.sent[-1]
    assert report.startswith("⚠️") and "Instagram failed: Instagram: token expired" in report
    assert [b.custom_id for b in view.children] == ["tc:ig:q.mp4"]

    short.uploads = {"youtube": {"url": "u"}, "instagram": {"url": "i"}}
    ran.clear()
    item.platforms = ["youtube", "instagram"]
    channel = Channel()
    asyncio.run(Bot().auto_post(channel, item.date, "14:30"))
    assert ran == [] and "already on YouTube and Instagram" in channel.sent[0][0]

    # posted by hand in the YouTube / Instagram app, then ticked "posted" in the Upload tab or the plan
    short.uploads, short.posted = {}, {"youtube": True}
    item.posted = {"instagram": item.date}
    channel = Channel()
    asyncio.run(Bot().auto_post(channel, item.date, "14:30"))
    assert ran == [] and "already on YouTube and Instagram" in channel.sent[0][0]


# --------------------------------------------------------------------------- the production run


def test_the_production_run_is_due_once_a_morning_and_knows_which_slots_to_fill(settings):
    settings = settings.with_overrides(auto_produce=True)
    at_1015 = at(settings, 10, 15)
    due = assistant.due_reminders(settings, at_1015, {"2026-10-07 morning"})
    assert [(r.kind, r.key) for r in due] == [("produce", "2026-10-07 produce")]
    assert "produce" not in [r.kind for r in assistant.due_reminders(settings, at(settings, 10, 14), {"2026-10-07 morning"})]
    assert assistant.due_reminders(settings, at_1015, {"2026-10-07 morning", "2026-10-07 produce"}) == []
    off = settings.with_overrides(auto_produce=False)
    assert "produce" not in [r.kind for r in assistant.due_reminders(off, at_1015, {"2026-10-07 morning"})]

    tomorrow = DAY + timedelta(days=1)
    assert assistant.produce_targets(settings, at_1015) == [(DAY, "14:30"), (DAY, "23:00"), (tomorrow, "14:30"),
                                                             (tomorrow, "23:00")]
    assert assistant.produce_targets(settings, at(settings, 12)) == [(DAY, "23:00"), (tomorrow, "14:30"), (tomorrow, "23:00")]
    add_short(settings, "p.mp4")
    planner.add_item(settings, planner.ItemRequest(date=tomorrow, slot="14:30", short="p.mp4"))
    assert assistant.produce_targets(settings, at(settings, 21)) == [(tomorrow, "23:00")]


def test_the_production_run_fills_the_empty_slots_and_a_veto_frees_one(settings, monkeypatch, clock):
    import asyncio

    from trendclip import analytics, discord_bot

    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s: "key")
    monkeypatch.setattr(discord_bot, "library_games", lambda s: ["Minecraft", "Roblox"])
    monkeypatch.setattr(discord_bot, "plan_pick", lambda s, games=None, rng=None, day=None: analytics.Pick(
        mode="story", game=games[0], target_seconds=22, days=60, videos=9))
    made = []

    def create(s, game, progress, kind, only, target_seconds=None):
        name = f"prod{len(made)}.mp4"
        made.append((game, kind, target_seconds))
        return shorts.get_short(s, add_short(s, name))

    monkeypatch.setattr(assistant, "create_random_short", create)
    replies = []

    async def fake_reply(interaction, content, **kw):
        replies.append(content)

    monkeypatch.setattr(discord_bot, "reply", fake_reply)

    class Channel:
        sent: list = []

        async def send(self, content="", **kw):
            self.sent.append(content)

    class Bot:
        render_lock = asyncio.Lock()
        produce_videos = discord_bot.TrendClipBot.produce_videos
        produce_one = discord_bot.TrendClipBot.produce_one
        fail = False

        def __init__(self):
            self.settings = settings

        async def run_job(self, channel, title, fn, lock, attempts=1, give_up=lambda: False):
            assert attempts == discord_bot.PRODUCE_ATTEMPTS and give_up() is False
            if self.fail:
                raise discord_bot.JobFailed("ffmpeg")
            return fn(lambda f, m: None)

        async def send_video(self, channel, filename, content, view=None, followup=None):
            channel.sent.append(content)

    bot, channel = Bot(), Channel()
    clock[0] = at(settings, 10, 15)
    asyncio.run(bot.produce_videos(channel))
    tomorrow = DAY + timedelta(days=1)
    assert [m[:2] for m in made] == [("Minecraft", "story")] * 4 and made[0][2] == 22
    assert all(planner.slot_item(settings, d, s) for d in (DAY, tomorrow) for s in planner.SLOTS)
    assert channel.sent[0].startswith("🏭 **Making the videos for Today") and "Tomorrow (Thu 08 Oct) 23:00**" in channel.sent[0]

    assert "🎲 Picked by the last 60 days' numbers" in channel.sent[1]
    assert sum("is planned for" in m and "goes out by itself" in m for m in channel.sent) == 4

    asyncio.run(bot.produce_videos(channel))  # nothing left to fill
    assert channel.sent[-1].startswith("🏭 Every slot of today and tomorrow is planned") and len(made) == 4

    item = planner.slot_item(settings, tomorrow, "14:30")
    asyncio.run(discord_bot.act_veto(bot, None, item.id))
    assert planner.slot_item(settings, tomorrow, "14:30") is None and "won't be posted" in replies[-1]
    assert shorts.get_short(settings, item.short)  # the video itself is kept
    asyncio.run(discord_bot.act_veto(bot, None, item.id))
    assert "no longer on the plan" in replies[-1]

    click = SimpleNamespace(channel=channel)
    asyncio.run(discord_bot.act_produce(bot, click, f"{tomorrow.isoformat()} 14:30"))  # "Make another now"
    assert planner.slot_item(settings, tomorrow, "14:30").short == "prod4.mp4" and replies[-1].startswith("On it")
    asyncio.run(discord_bot.act_produce(bot, click, f"{tomorrow.isoformat()} 14:30"))  # taken: nothing made
    assert len(made) == 5
    with pytest.raises(ValueError):
        asyncio.run(discord_bot.act_produce(bot, click, "2026-13-01 14:30"))


    planner.delete_item(settings, planner.slot_item(settings, tomorrow, "23:00").id)
    bot.fail = True
    asyncio.run(bot.produce_one(channel, tomorrow, "23:00"))
    assert channel.sent[-1].startswith("🏭 Couldn't make the video for Tomorrow") and "23:00 after 5 attempts" in channel.sent[-1]

    assert planner.slot_item(settings, tomorrow, "23:00") is None  # left to the emergency video / the button


def test_every_slash_command_fits_discords_limits():
    """Discord refuses the whole command list when one description is over 100 characters, and then the
    bot never starts (no morning summary, no production run, no automatic posts)."""
    from discord import app_commands

    from trendclip import discord_bot

    def walk(commands, prefix=""):
        for c in commands:
            name = f"{prefix}{c.name}"
            assert 1 <= len(c.name) <= 32 and c.name == c.name.lower(), name
            assert 1 <= len(c.description) <= 100, f"{name}: {len(c.description)} characters"
            if isinstance(c, app_commands.Group):
                walk(c.commands, f"{name} ")
            else:
                for p in c.parameters:
                    assert 1 <= len(p.description) <= 100, f"{name} {p.name}: {len(p.description)} characters"

    walk(discord_bot.COMMANDS)


def test_reminders_run_posts_first_and_never_wait_for_the_morning_summary(settings, monkeypatch, clock):
    """After a restart the whole backlog is due at once; the 14:30 post must not queue behind the morning
    summary (stats, insights, comments: minutes)."""
    import asyncio

    from trendclip import discord_bot

    cfg = settings.with_overrides(discord_channel_id=1, auto_produce=True)
    order, started = [], asyncio.Event()

    class Channel:
        async def send(self, *a, **kw):
            pass

    class Bot:
        settings = cfg
        jobs: set = set()
        _job_done = discord_bot.TrendClipBot._job_done
        reminders = None

        def __init__(self):
            self.sent = assistant.SentLog(cfg)

        def get_channel(self, _id):
            return Channel()

        def darkarchives_posts(self, at):
            pass

        def borderlinewhy_jobs(self, at):
            pass

        async def morning_summary(self, channel, day):

            order.append("morning")
            await started.wait()  # slow: blocks until the test lets it finish

        async def auto_post(self, channel, day, slot):
            order.append(f"post {slot}")

        async def produce_videos(self, channel):
            order.append("produce")

        async def leaderboard_post(self, channel):
            order.append("leaderboard")

        async def emergency_video(self, channel, day, slot):
            order.append(f"emergency {slot}")

    bot = Bot()
    clock[0] = at(cfg, 14, 45)  # morning (10:00), produce (10:15) and the 14:30 post are all due


    async def run():
        await discord_bot.TrendClipBot.reminders.coro(bot)
        await asyncio.sleep(0)  # let the tasks start
        assert order[:1] == ["post 14:30"] and set(order) == {"post 14:30", "produce", "morning"}
        keys = bot.sent.load()
        assert {"2026-10-07 14:30 post", "2026-10-07 produce", "2026-10-07 morning"} <= keys  # marked at once
        started.set()
        await asyncio.gather(*bot.jobs)

    asyncio.run(run())
