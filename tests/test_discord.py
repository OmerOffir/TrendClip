from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from trendclip import assistant, planner, publish, quiz, script_writer, shorts, video_downloader
from trendclip.config import Settings
from trendclip.video_downloader import BackgroundClip, DownloadError

from tests.test_upload import add_short

DAY = date(2026, 10, 7)


@pytest.fixture
def settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output",
                    discord_morning_time="10:00", discord_reminder_minutes=30)


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
    first = planner.add_item(settings, planner.ItemRequest(date=DAY, slot="18:00", short=a))
    with pytest.raises(ValueError, match="already has"):
        planner.add_item(settings, planner.ItemRequest(date=DAY, slot="18:00", short=b))
    second = planner.add_item(settings, planner.ItemRequest(date=DAY + timedelta(days=1), slot="18:00", short=b))

    moved = planner.update_item(settings, second.id, planner.ItemUpdate(date=DAY))  # dragged onto a taken slot
    assert moved.date == DAY and moved.slot == "23:00"
    third = planner.add_item(settings, planner.ItemRequest(date=DAY + timedelta(days=1), slot="18:00", short=c))
    assert planner.update_item(settings, third.id, planner.ItemUpdate(date=DAY)).slot is None  # day is full
    with pytest.raises(ValueError):
        planner.update_item(settings, third.id, planner.ItemUpdate(slot="18:00"))
    assert planner.update_item(settings, first.id, planner.ItemUpdate(slot="")).slot is None
    assert planner.free_slots(settings, DAY) == ["18:00"]
    assert [i["slot"] for i in planner.view(settings)["items"]] == ["23:00", None, None]


def test_new_reels_take_the_first_free_slot_unless_told_otherwise(settings):
    a, b, c = (add_short(settings, f"{n}.mp4") for n in "abc")
    assert planner.add_item(settings, planner.ItemRequest(date=DAY, short=a)).slot == "18:00"
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
    assert [i.slot for i in planner.load(settings).items] == [None, "18:00", "23:00", None]  # past day untouched
    planner.update_item(settings, "1", planner.ItemUpdate(slot=""))
    assert planner.assign_slots(settings, DAY) == 0  # only once: later "no fixed time" choices are kept
    assert planner.load(settings).items[1].slot is None


def test_next_free_slot_skips_passed_and_taken_slots(settings):
    add_short(settings, "a.mp4")
    planner.add_item(settings, planner.ItemRequest(date=DAY, slot="23:00", short="a.mp4"))
    assert planner.next_free_slot(settings, at(settings, 12)) == (DAY, "18:00")
    assert planner.next_free_slot(settings, at(settings, 18, 30)) == (DAY + timedelta(days=1), "18:00")
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
    assert client.get("/api/plan").json()["slots"] == ["18:00", "23:00"]


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
    assert (assistant.plan_short(settings, a).date, planner.load(settings).items[0].slot) == (DAY, "18:00")
    item = assistant.plan_short(settings, b, DAY)
    assert item.slot == "23:00"
    with pytest.raises(ValueError, match="full"):
        assistant.plan_short(settings, c, DAY)
    assert assistant.plan_short(settings, c).date == DAY + timedelta(days=1)

    entries = assistant.day_slots(settings, DAY)
    assert [(e.slot, e.short.filename) for e in entries] == [("18:00", a), ("23:00", b)]
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
    assert assistant.due_reminders(settings, at(settings, 9, 59), set()) == []
    morning = assistant.due_reminders(settings, at(settings, 10), set())
    assert [r.key for r in morning] == ["2026-10-07 morning"]
    assert assistant.due_reminders(settings, at(settings, 16, 30), set()) == []  # summary missed by > 6 h
    slot = assistant.due_reminders(settings, at(settings, 17, 30), {"2026-10-07 morning"})
    assert [(r.kind, r.slot) for r in slot] == [("slot", "18:00")]
    assert assistant.due_reminders(settings, at(settings, 18), {"2026-10-07 morning"}) == []
    late = assistant.due_reminders(settings, at(settings, 22, 45), {"2026-10-07 morning"})
    assert [r.key for r in late] == ["2026-10-07 23:00"]
    quiet = settings.with_overrides(discord_reminder_minutes=0)
    assert assistant.due_reminders(quiet, at(settings, 22, 45), {"2026-10-07 morning"}) == []


def test_sent_log_survives_restarts_and_forgets_old_days(settings):
    log = assistant.SentLog(settings)
    log.add("2026-09-01 morning", date(2026, 9, 1))
    log.add("2026-10-07 18:00", DAY)
    assert assistant.SentLog(settings).load() == {"2026-10-07 18:00"}


# --------------------------------------------------------------------------- videos


def test_upload_request_uses_the_upload_tab_texts(settings):
    name = add_short(settings)
    req = assistant.upload_request(settings, name)
    assert req.privacy == "public" and req.publish_at is None
    assert req.title == "The Day My Cat Outsmarted the Block #shorts"
    assert req.description.startswith("I searched for three hours.") and "Gameplay footage" in req.description
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

    b = discord_bot.button("pt", "1800:gta_1.mp4", "Plan today 18:00")
    assert b.item.custom_id == "tc:pt:1800:gta_1.mp4"
    assert discord_bot.button("show", "x" * 120, "Show") is None
    assert discord_bot.code_slot("2300") == "23:00"
    with pytest.raises(ValueError):
        discord_bot.code_slot("1900")
    assert set(discord_bot.ACTIONS) >= {"show", "up", "upgo", "pl", "pt", "rm", "mk"}


def test_create_asks_story_math_or_riddle_with_buttons(monkeypatch):
    import asyncio

    from trendclip import discord_bot

    ids = [b.item.custom_id for b in discord_bot.kind_buttons("GTA V")]
    assert ids == ["tc:mk:story:GTA V", "tc:mk:math:GTA V", "tc:mk:riddle:GTA V", "tc:mk:trivia:GTA V"]
    assert [b.item.custom_id for b in discord_bot.kind_buttons("x" * 120)] == [
        "tc:mk:story", "tc:mk:math", "tc:mk:riddle", "tc:mk:trivia"]

    made, asked = [], []

    class Bot:
        async def create_random(self, interaction, game, kind):
            made.append((game, kind))

    async def fake_ask(interaction, game):
        asked.append(game)

    monkeypatch.setattr(discord_bot, "ask_kind", fake_ask)
    asyncio.run(discord_bot.act_make(Bot(), None, "math:GTA V"))
    asyncio.run(discord_bot.act_make(Bot(), None, "riddle"))
    asyncio.run(discord_bot.act_make(Bot(), None, "-"))  # old "Create a random video" button
    assert made == [("GTA V", "math"), (None, "riddle")] and asked == [None]


def test_caption_message_is_copy_ready_for_youtube_tiktok_and_instagram(settings):
    from trendclip import discord_bot

    short = shorts.get_short(settings, add_short(settings))
    text = discord_bot.caption_message(settings, short, "📝 `18:00` **The Day**")
    for label in ("▶️ YouTube", "🎵 TikTok", "📸 Instagram"):
        assert label in text
    assert "I searched for three hours" in text  # the description text
    assert "#storytime" in text and "#fyp" in text and "#shorts" in text
    assert text.count("```") == 6 and len(text) <= discord_bot.MESSAGE_MAX
