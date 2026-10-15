import asyncio
from datetime import datetime, timedelta

import pytest

from trendclip import hub, planner, shorts
from trendclip.config import Settings

from tests.test_upload import add_short


def make(tmp_path, name, **kw):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / name / "assets",
                    output_dir=tmp_path / name / "output", hub_channel_id=42, **kw)


class Attachment:
    def __init__(self, data):
        self.data = data

    async def read(self):
        return self.data


class Message:
    def __init__(self, content, attachments):
        self.content = content
        self.attachments = attachments


class FakeDiscord:
    """The hub channel: the sender's REST calls, answered by the hub's handler like the bot does."""

    def __init__(self, hub_settings):
        self.hub_settings = hub_settings
        self.messages: dict[str, Message] = {}
        self.next_id = 100

    def __call__(self, _settings):
        return self

    def post(self, content, file=None):
        mid = str(self.next_id)
        self.next_id += 1
        self.messages[mid] = msg = Message(content, [Attachment(file[1])] if file else [])
        if hub.is_request(content):
            reply, _, attach = asyncio.run(hub.handle(self.hub_settings, msg, self))
            self.post(reply, attach)
        return mid

    async def fetch_message(self, mid):
        return self.messages[str(mid)]

    def after(self, mid):
        return [{"id": k, "content": m.content, "attachments": [{"url": k}] if m.attachments else []}
                for k, m in self.messages.items() if int(k) > int(mid)]

    def download(self, url):
        return self.messages[url].attachments[0].data

    def delete(self, mid):
        self.messages.pop(mid, None)


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
def discord(linux, monkeypatch):
    fake = FakeDiscord(linux)
    monkeypatch.setattr(hub, "Rest", fake)
    monkeypatch.setattr(hub, "POLL_EVERY", 0)
    monkeypatch.setattr(hub, "PART_BYTES", 1000)
    return fake


def test_roles(tmp_path):
    assert hub.is_hub(make(tmp_path, "a", hub_role="HUB"))
    assert hub.is_sender(make(tmp_path, "b", hub_role="sender", discord_bot_token="t"))
    assert not hub.is_sender(make(tmp_path, "c", hub_role="sender"))
    assert not hub.is_hub(Settings(youtube_api_key="k", hub_role="hub"))


def test_free_slots_skip_passed_and_taken_times(linux):
    now = datetime(2026, 10, 12, 20, 0, tzinfo=linux.tz)
    add_short(linux)
    planner.add_item(linux, planner.ItemRequest(date=now.date() + timedelta(days=1), slot="14:30", short="gta_1.mp4"))

    free = hub.free_slots(linux, days=2, now=now)

    assert free == [{"date": "2026-10-12", "slot": "23:00"}, {"date": "2026-10-13", "slot": "23:00"}]


def test_mac_sends_a_short_onto_the_linux_plan_through_discord(linux, mac, tomorrow, discord):
    name = add_short(mac, pinned_comment="Which number did you get? 👇")

    reply = hub.send(mac, hub.SendRequest(filename=name, date=tomorrow, slot="14:30"))

    assert reply["date"] == tomorrow.isoformat() and reply["slot"] == "14:30" and reply["short"] == name
    got = shorts.get_short(linux, name)
    assert got.ready and got.pinned_comment == "Which number did you get? 👇"
    assert (linux.shorts_dir / name).read_bytes() == b"\x00" * 2048  # joined from 3 pieces
    assert planner.slot_item(linux, tomorrow, "14:30").short == name
    assert shorts.get_short(mac, name).sent["hub"]["slot"] == "14:30"
    assert discord.messages == {}  # the transfer messages are cleaned up
    assert not list(linux.shorts_dir.glob(".incoming-*"))


def test_a_taken_time_is_refused_with_the_free_ones(linux, mac, tomorrow, discord):
    add_short(linux, name="linux_1.mp4")
    planner.add_item(linux, planner.ItemRequest(date=tomorrow, slot="14:30", short="linux_1.mp4"))
    name = add_short(mac)

    with pytest.raises(hub.HubError) as err:
        hub.send(mac, hub.SendRequest(filename=name, date=tomorrow, slot="14:30"))

    assert err.value.status == 409 and "taken" in str(err.value)
    assert {"date": tomorrow.isoformat(), "slot": "23:00"} in err.value.free
    assert not (linux.shorts_dir / name).exists()


def test_hub_rechecks_the_time_when_the_video_arrives(linux, mac, tomorrow, discord, monkeypatch):
    name = add_short(mac)
    add_short(linux, name="linux_1.mp4")
    asked = hub.remote_free

    def free_then_taken(settings):
        free = asked(settings)
        planner.add_item(linux, planner.ItemRequest(date=tomorrow, slot="23:00", short="linux_1.mp4"))
        return free

    monkeypatch.setattr(hub, "remote_free", free_then_taken)

    with pytest.raises(hub.HubError) as err:
        hub.send(mac, hub.SendRequest(filename=name, date=tomorrow, slot="23:00"))

    assert "already has" in str(err.value) and err.value.free
    assert not (linux.shorts_dir / name).exists()
    assert "hub" not in shorts.get_short(mac, name).sent


def test_same_filename_on_the_hub_gets_a_new_name(linux, mac, tomorrow, discord):
    add_short(linux)
    name = add_short(mac)

    reply = hub.send(mac, hub.SendRequest(filename=name, date=tomorrow, slot="23:00"))

    assert reply["short"] == "gta_1-2.mp4"
    assert shorts.get_short(linux, "gta_1-2.mp4").filename == "gta_1-2.mp4"


def test_the_mac_plan_goes_to_linux_at_its_planned_times(linux, mac, tomorrow, discord):
    for name, slot in (("a.mp4", "14:30"), ("b.mp4", "23:00")):
        add_short(mac, name=name)
        planner.add_item(mac, planner.ItemRequest(date=tomorrow, slot=slot, short=name))
    add_short(linux, name="linux_1.mp4")
    planner.add_item(linux, planner.ItemRequest(date=tomorrow, slot="23:00", short="linux_1.mp4"))

    results = hub.send_plan(mac)

    assert [r["ok"] for r in results] == [True, False]
    assert "already taken" in results[1]["message"] and "Next free" in results[1]["message"]
    assert planner.slot_item(linux, tomorrow, "14:30").short == "a.mp4"
    assert [i.short for i in planner.load(mac).items] == ["b.mp4"]  # the refused one stays to be moved


def test_the_mac_sees_the_linux_plan(linux, mac, tomorrow, discord):
    add_short(linux, name="linux_1.mp4")
    planner.add_item(linux, planner.ItemRequest(date=tomorrow, slot="23:00", short="linux_1.mp4"))

    plan = hub.remote_plan(mac)

    assert plan["days"] == hub.FREE_DAYS
    assert [(i["date"], i["slot"], i["title"]) for i in plan["items"]] == [
        (tomorrow.isoformat(), "23:00", "The Day My Cat Outsmarted the Block")]
    assert {"date": tomorrow.isoformat(), "slot": "14:30"} in plan["free"]
    assert {"date": tomorrow.isoformat(), "slot": "23:00"} not in plan["free"]
    assert discord.messages == {}


def test_only_ask_and_send_are_requests():
    assert hub.is_request("tc-hub ask ab12")
    assert not any(hub.is_request(f"tc-hub {op} ab12 {{}}") for op in hub.REPLIES)
    assert not hub.is_request("tc-hub part ab12 1/2")
    assert not hub.is_request("hello")
