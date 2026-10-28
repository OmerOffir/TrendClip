import asyncio
import json
import os
import time
from datetime import datetime, timedelta

import pytest

from db import repository
from modules import bw_channel, channel_hub, da_channel, da_plan, script_gen, video_gen
from trendclip import hub

from tests.test_hub import Attachment, FakeDiscord, Message, make

PATHS = ((video_gen, "OUTPUT_DIR", "darkarchives"), (script_gen, "OUTPUT_DIR", "scripts"),
         (bw_channel, "OUTPUT_DIR", "borderlinewhy"), (repository, "DB_PATH", "bank.db"))


class Machines:
    """The channel modules keep their folders in module globals: point them at one machine or the other."""

    def __init__(self, tmp_path):
        self.root = tmp_path

    def use(self, side):
        for module, attr, name in PATHS:
            setattr(module, attr, self.root / side / name)
        repository.init_db()


class ChannelDiscord(FakeDiscord):
    """Requests are answered by the hub's handler with the Linux folders, like the bot on the other machine."""

    def __init__(self, hub_settings, machines):
        super().__init__(hub_settings)
        self.machines = machines

    def post(self, content, file=None):
        if not hub.is_request(content):
            return super().post(content, file)
        mid = str(self.next_id)
        self.next_id += 1
        self.messages[mid] = msg = Message(content, [Attachment(file[1])] if file else [])
        self.machines.use("linux")
        try:
            reply, _, attach = asyncio.run(hub.handle(self.hub_settings, msg, self))
        finally:
            self.machines.use("mac")
        super().post(reply, attach)
        return mid


@pytest.fixture
def linux(tmp_path):
    return make(tmp_path, "linux", hub_role="hub")


@pytest.fixture
def mac(tmp_path):
    return make(tmp_path, "mac", hub_role="sender", discord_bot_token="t")


@pytest.fixture
def tomorrow(linux):
    return datetime.now(linux.tz).date() + timedelta(days=1)


@pytest.fixture
def machines(tmp_path, monkeypatch):
    for module, attr, _ in PATHS:
        monkeypatch.setattr(module, attr, getattr(module, attr))  # restored after the test
    m = Machines(tmp_path)
    m.use("linux")
    m.use("mac")
    return m


@pytest.fixture
def announced(monkeypatch):
    posts = []
    monkeypatch.setattr(da_channel, "announce_video", lambda sid, info, settings, note="", components=None: posts.append(("da", sid, note)))
    monkeypatch.setattr(bw_channel, "announce_video", lambda info, settings=None, out_dir=None: posts.append(("bw", info["key"])))
    return posts


@pytest.fixture
def discord(linux, machines, monkeypatch):
    fake = ChannelDiscord(linux, machines)
    monkeypatch.setattr(hub, "Rest", fake)
    monkeypatch.setattr(hub, "POLL_EVERY", 0)
    monkeypatch.setattr(hub, "PART_BYTES", 1000)
    return fake


def _story(source_id, title="The Ship"):
    repository.save_story(source_id, "r/UnresolvedMysteries", title, "A ship was found empty. " * 20, f"https://x/{source_id}", 500,
                          pillar="mystery")
    return repository.find_source(source_id)["id"]


def _da_video(story_id, title="The Ship", ranking=()):
    out = video_gen.OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    video_gen.video_path(story_id).write_bytes(b"\x01" * 2500)
    video_gen.info_path(story_id).write_text(json.dumps({
        "story_id": story_id, "title": title, "filename": f"story_{story_id}.mp4", "duration_seconds": 55,
        "description": "Hook.", "hashtags": ["darkarchives"], "beats": [], "uploads": {}, "created_at": "2026-10-20T10:00:00"}))
    script_gen.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    script = {"story_id": story_id, "title": title, "ranking": [{"rank": 1, "label": "x", "story_id": s, "title": "t"} for s in ranking]}
    script_gen.script_path(story_id).write_text(json.dumps(script))


def test_a_dark_outliers_video_goes_to_the_linux_plan_with_its_story(linux, mac, tomorrow, discord, machines, announced):
    machines.use("linux")
    _story("reddit_other", "Something else")  # Linux numbers its stories differently
    told_on_linux = _story("reddit_told", "Told in the ranking")
    machines.use("mac")
    told = _story("reddit_told", "Told in the ranking")
    sid = _story("ranking_2026", "Top 3 ships")
    _da_video(sid, "Top 3 ships", ranking=[told])
    da_plan.add_item(da_plan.ItemRequest(story_id=sid, date=tomorrow, slot="14:30"))

    reply = channel_hub.send(mac, "darkarchives", sid, tomorrow, "23:00")

    assert reply["date"] == tomorrow.isoformat() and reply["slot"] == "23:00"
    assert da_plan.load().items == []  # left this machine's plan
    assert json.loads(video_gen.info_path(sid).read_text())["sent"]["hub"]["slot"] == "23:00"
    assert discord.messages == {}
    machines.use("linux")
    linux_id = reply["ref"]
    assert linux_id != sid and repository.get_story(linux_id)["status"] == "USED"
    assert repository.get_story(told_on_linux)["status"] == "USED"
    assert video_gen.video_path(linux_id).read_bytes() == b"\x01" * 2500  # joined from 3 pieces
    info = json.loads(video_gen.info_path(linux_id).read_text())
    assert info["story_id"] == linux_id and info["filename"] == f"story_{linux_id}.mp4" and "sent" not in info
    assert json.loads(script_gen.script_path(linux_id).read_text())["story_id"] == linux_id
    assert da_plan.slot_item(tomorrow, "23:00").story_id == linux_id
    assert announced == [("da", linux_id, f"📥 From the other machine · planned for {tomorrow.isoformat()} 23:00.")]
    assert not list(linux.shorts_dir.glob(".incoming-*"))


def test_a_taken_dark_outliers_time_and_a_story_linux_already_made_are_refused(linux, mac, tomorrow, discord, machines, announced):
    machines.use("linux")
    there = _story("reddit_ship")
    _da_video(there)
    da_plan.add_item(da_plan.ItemRequest(story_id=there, date=tomorrow, slot="14:30"))
    machines.use("mac")
    sid = _story("reddit_ship")
    _da_video(sid)

    with pytest.raises(hub.HubError) as taken:
        channel_hub.send(mac, "darkarchives", sid, tomorrow, "14:30")
    with pytest.raises(hub.HubError) as made:
        channel_hub.send(mac, "darkarchives", sid, tomorrow, "23:00")

    assert taken.value.status == 409 and "taken" in str(taken.value)
    assert {"date": tomorrow.isoformat(), "slot": "23:00"} in taken.value.free
    assert "already has a video" in str(made.value)
    assert "sent" not in json.loads(video_gen.info_path(sid).read_text())
    assert announced == []


def test_the_dark_outliers_plan_goes_to_linux_and_linux_shows_its_plan(linux, mac, tomorrow, discord, machines, announced):
    machines.use("linux")
    there = _story("reddit_linux", "Linux story")
    _da_video(there, "Linux story")
    da_plan.add_item(da_plan.ItemRequest(story_id=there, date=tomorrow, slot="23:00"))
    machines.use("mac")
    for source, slot in (("reddit_a", "14:30"), ("reddit_b", "23:00")):
        sid = _story(source, source)
        _da_video(sid, source)
        da_plan.add_item(da_plan.ItemRequest(story_id=sid, date=tomorrow, slot=slot))

    assert [p["title"] for p in channel_hub.planned(mac, "darkarchives")] == ["reddit_a", "reddit_b"]
    results = channel_hub.send_plan(mac, "darkarchives")
    plan = channel_hub.remote_plan(mac, "darkarchives")

    assert [r["ok"] for r in results] == [True, False]
    assert "already taken" in results[1]["message"] and "Next free" in results[1]["message"]
    assert [i.title for i in da_plan.load().items] == ["reddit_b"]  # the refused one stays to be moved
    assert [(i["slot"], i["title"]) for i in plan["items"]] == [("14:30", "reddit_a"), ("23:00", "Linux story")]
    assert {"date": tomorrow.isoformat(), "slot": "14:30"} not in plan["free"]


def _bw_video(settings, day, slot, title="Why is Kansas flatter than a pancake?"):
    job = bw_channel.job_for(settings, day, slot)
    folder = bw_channel.out_root() / job.key
    folder.mkdir(parents=True, exist_ok=True)
    bw_channel.video_path(job.key).write_bytes(b"\x02" * 1800)
    (folder / "script.json").write_text(json.dumps({"title": title, "narration": "So."}))
    bw_channel.save_info({"key": job.key, "title": title, "duration_seconds": 50, "article": "Kansas",
                          "sources": [{"url": "a"}, {"url": "b"}], "uploads": {}, "created_at": "2026-10-20T10:00:00",
                          "job": {"day": day.isoformat(), "slot": slot, "post_at": job.post_at.isoformat(timespec="minutes")}})
    return job.key


def test_a_borderline_why_video_becomes_the_linux_slots_video(linux, mac, tomorrow, discord, machines, announced):
    key = _bw_video(mac, tomorrow, "14:30")
    later = tomorrow + timedelta(days=1)

    reply = channel_hub.send(mac, "borderlinewhy", key, later, "23:00")

    assert reply["ref"] == f"{later.isoformat()}_2300"
    assert bw_channel.load_info(key)["sent"]["hub"]["date"] == later.isoformat()
    assert channel_hub.planned(mac, "borderlinewhy") == []
    machines.use("linux")
    info = bw_channel.load_info(reply["ref"])
    assert info["title"] == "Why is Kansas flatter than a pancake?" and info["check"] == []
    assert info["job"]["day"] == later.isoformat() and info["job"]["slot"] == "23:00" and "sent" not in info
    assert bw_channel.video_path(reply["ref"]).read_bytes() == b"\x02" * 1800
    assert (bw_channel.out_root() / reply["ref"] / "script.json").is_file()
    assert announced == [("bw", reply["ref"])]


def test_borderline_why_free_times_skip_made_and_being_made_slots(linux, tomorrow, machines):
    machines.use("linux")
    _bw_video(linux, tomorrow, "14:30")
    making = bw_channel.out_root() / f"{tomorrow.isoformat()}_2300"
    making.mkdir(parents=True)
    now = datetime.combine(tomorrow, datetime.min.time(), tzinfo=linux.tz)

    free = channel_hub.CHANNELS["borderlinewhy"].free(linux, now)

    assert {"date": tomorrow.isoformat(), "slot": "14:30"} not in free
    assert {"date": tomorrow.isoformat(), "slot": "23:00"} not in free  # the bot is making it
    old = time.time() - channel_hub.MAKING_GRACE - 60
    os.utime(making, (old, old))  # a make that died long ago
    assert {"date": tomorrow.isoformat(), "slot": "23:00"} in channel_hub.CHANNELS["borderlinewhy"].free(linux, now)


def test_an_unknown_channel_is_answered(linux, mac, discord):
    with pytest.raises(hub.HubError) as err:
        channel_hub.remote_free(mac, "nope")
    assert err.value.status == 404

    reply, attach = asyncio.run(channel_hub.handle(linux, ["tc-hub", "cask", "ab12", "nope"], None, None))
    assert reply.startswith("tc-hub fail ab12") and "Unknown channel" in reply and attach is None


def test_channel_requests_are_hub_requests():
    assert all(hub.is_request(f"tc-hub {op} ab12 darkarchives") for op in ("cask", "cplan", "csend"))
