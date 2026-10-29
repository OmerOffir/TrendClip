"""Write a week: one prompt for several topics, the pasted scripts checked one by one, the fact check, and the ready
scripts into the hub's queue (modules/bw_week.py, channel_hub.BorderlineWhy.queue)."""

import json

import pytest

from modules import bw_channel, bw_script, bw_topics, bw_week, channel_hub
from tests.test_borderlinewhy import _draft, _research, settings  # noqa: F401 - the fixture


@pytest.fixture
def week(tmp_path, monkeypatch):
    """A session of two researched topics, with the geocoder and the rule check faked."""
    base = _research()
    second = bw_script.Research(bw_topics.Topic(question="Why does Spain own Llívia?", article="Llívia"), "Llívia", base.sources)
    monkeypatch.setattr(bw_topics, "next_topic", lambda out_dir, skip=None: next(
        (t for t in (bw_topics.Topic(question=base.topic.question, article=base.article), second.topic) if t.article not in (skip or set())), None))
    monkeypatch.setattr(bw_script, "research", lambda topic, progress=None: base if topic.article == base.article else second)
    monkeypatch.setattr(bw_script, "check", lambda draft, res, find=None: [])
    bw_week.start(2, out_dir=tmp_path)
    return tmp_path


def _answer(*topics, **changes):
    return json.dumps({"scripts": [{**_draft().model_dump(), "topic": i, **changes} for i in topics]})


def test_start_reads_the_next_topics_and_the_prompt_holds_the_rules_once(week):
    view = bw_week.view(week)
    assert [t["question"] for t in view["topics"]] == ["Why does Minnesota have a notch?", "Why does Spain own Llívia?"]
    assert view["to_write"] == 2 and view["to_check"] == 0 and view["ready"] == 0
    prompt = view["write_prompt"]
    assert prompt.count("THE NARRATION") == 1 and prompt.count("THE CLAIMS") == 1  # the rules once
    assert '=== TOPIC 1: "Why does Minnesota have a notch?" ===' in prompt and '=== TOPIC 2: "Why does Spain own Llívia?" ===' in prompt
    assert prompt.count("[source 1]") == 2  # each topic's own sources
    assert '{"scripts": [ {"topic": 1, ...}' in prompt and '"usable": true' in prompt  # the answer shape and the example
    assert "{question}" not in prompt and "{sources}" not in prompt and "{min_words}" not in prompt
    assert view["facts_prompt"] == ""


def test_pasted_scripts_are_checked_one_by_one(week, monkeypatch):
    problems = {1: [], 2: ["the narration is 188 words"]}
    monkeypatch.setattr(bw_script, "check", lambda draft, res, find=None: problems[2 if res.article == "Llívia" else 1])
    view = bw_week.answer(_answer(1, 2), out_dir=week)
    steps = {t["i"]: t["step"] for t in view["topics"]}
    assert steps == {1: "facts", 2: "fix"}
    assert view["topics"][1]["problems"] == ["the narration is 188 words"]
    assert view["to_write"] == 1 and view["to_check"] == 1
    # the write prompt now carries only topic 2, with its draft and what was wrong; the facts prompt topic 1
    assert '=== TOPIC 2:' in view["write_prompt"] and '=== TOPIC 1:' not in view["write_prompt"]
    assert "Topics 2 come with your previous draft" in view["write_prompt"] and "the narration is 188 words" in view["write_prompt"]
    assert '=== TOPIC 1:' in view["facts_prompt"] and "Check 1 YouTube narrations" in view["facts_prompt"]
    assert '{"checks": [' in view["facts_prompt"]


def test_a_rejected_topic_is_marked_and_the_wrong_numbers_are_refused(week):
    view = bw_week.answer(_answer(2, usable=False, reject_reason="the sources disagree"), out_dir=week)
    assert view["topics"][1]["step"] == "rejected"
    assert "Llívia" in bw_topics.load(week).rejected
    with pytest.raises(bw_week.WeekError, match="topic numbers"):
        bw_week.answer(_answer(9), out_dir=week)
    with pytest.raises(bw_week.WeekError, match="no JSON object"):
        bw_week.answer("sorry, I can't", out_dir=week)


def test_the_fact_check_makes_scripts_ready_or_sends_them_back(week):
    bw_week.answer(_answer(1, 2), out_dir=week)
    view = bw_week.facts(json.dumps({"checks": [{"topic": 1, "unsupported": []},
                                                {"topic": 2, "unsupported": ["Built in 1900 - no source gives a year"]}]}), out_dir=week)
    steps = {t["i"]: t["step"] for t in view["topics"]}
    assert steps == {1: "ready", 2: "facts_fix"}
    assert "Built in 1900" in view["write_prompt"] and "A fact checker compared your narration" in view["write_prompt"]
    scripts = bw_week.scripts(week)
    assert len(scripts) == 1 and scripts[0]["origin"] == "week" and scripts[0]["week_topic"] == 1
    assert scripts[0]["narration"] and scripts[0]["scenes"]
    # the fixed draft goes through both checks again
    view = bw_week.answer(_answer(2), out_dir=week)
    assert {t["i"]: t["step"] for t in view["topics"]} == {1: "ready", 2: "facts"}
    view = bw_week.drop(2, week)
    assert view["topics"][1]["step"] == "dropped" and view["to_check"] == 0


def test_send_puts_the_ready_scripts_in_the_queue_and_marks_the_topics(week, monkeypatch):
    bw_week.answer(_answer(1, 2), out_dir=week)
    bw_week.facts(json.dumps({"checks": [{"topic": 1, "unsupported": []}, {"topic": 2, "unsupported": []}]}), out_dir=week)
    delivered = {}

    def deliver(settings, name, bundle):
        delivered.update(name=name, bundle=bundle)
        return {"queued": [{"week_topic": 1, "name": "q1.json", "title": "A"}], "skipped": [{"week_topic": 2, "why": "already made"}],
                "slots": [{"date": "2026-10-30", "slot": "14:30"}], "in_queue": 3}

    view = bw_week.send(None, week, deliver=deliver)
    assert delivered["name"] == "borderlinewhy" and len(delivered["bundle"]["scripts"]) == 2
    steps = {t["i"]: t["step"] for t in view["topics"]}
    assert steps == {1: "sent", 2: "ready"}
    assert view["topics"][0]["sent"]["name"] == "q1.json"
    assert view["topics"][1]["problems"] == ["Linux did not take it: already made"]
    assert view["last_send"]["in_queue"] == 3 and view["reply"]["queued"][0]["week_topic"] == 1
    assert "Northwest Angle" in bw_topics.load(week).used  # marked here too


def test_the_hub_queues_the_scripts_and_skips_a_topic_it_already_made(tmp_path, monkeypatch, settings):
    monkeypatch.setattr(bw_channel, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(bw_channel, "announce_text", lambda text, settings=None, components=None: None)
    bw_topics.mark(tmp_path, "Llívia", used=True)
    script = {**bw_script.finish(_draft(), _research()), "week_topic": 1}
    old = {**script, "article": "Llívia", "week_topic": 2}
    empty = {"title": "nothing", "week_topic": 3}
    result = channel_hub.BorderlineWhy().queue(settings, {"scripts": [script, old, empty]})
    assert [q["week_topic"] for q in result["queued"]] == [1]
    assert [(s["week_topic"], s["why"]) for s in result["skipped"]] == [
        (2, 'the hub already made or queued "Llívia"'), (3, "no narration or scenes")]
    files = bw_channel.queued_scripts(tmp_path)
    assert len(files) == 1 and files[0].name.endswith("-01-why-does-minnesota-have-a-notch.json")
    assert json.loads(files[0].read_text())["narration"].startswith(script["narration"][:20])
    assert "Northwest Angle" in bw_topics.load(tmp_path).used
    # the same script again is skipped: it is in the queue already
    again = channel_hub.BorderlineWhy().queue(settings, {"scripts": [script]})
    assert again["queued"] == [] and "queued" in again["skipped"][0]["why"]
    # the daily job takes it, oldest first
    got, origin = bw_channel.next_script(out_dir=tmp_path)
    assert got["title"] == "Why does Minnesota have a notch?" and origin.startswith("queue:")


def test_queue_scripts_goes_straight_to_the_queue_on_the_hub_itself(settings, monkeypatch):
    seen = {}
    monkeypatch.setattr(channel_hub.hub, "is_sender", lambda s: False)
    monkeypatch.setattr(channel_hub.BorderlineWhy, "queue", lambda self, s, bundle, now=None: seen.update(bundle) or {"queued": []})
    channel_hub.queue_scripts(settings, "borderlinewhy", {"scripts": [1]})
    assert seen == {"scripts": [1]}
    monkeypatch.setattr(channel_hub.DarkOutliers, "queue", lambda self, s, bundle, now=None: seen.update(bundle) or {"queued": []})
    channel_hub.queue_scripts(settings, "darkarchives", {"stories": [2]})
    assert seen == {"scripts": [1], "stories": [2]}
    with pytest.raises(channel_hub.HubError, match="Unknown channel"):
        channel_hub.queue_scripts(settings, "mapchannel", {"scripts": [1]})
