"""Dark Outliers "Write a week": one prompt for the next stories and the ranking groups, the pasted scripts checked one
by one, the fact check, the ready scripts into the hub's bank, and the production run using them before Gemini
(modules/da_week.py, channel_hub.DarkOutliers.queue, da_produce.prewritten)."""

from __future__ import annotations

import json

import pytest

from db import repository
from modules import channel_hub, da_channel, da_produce, da_ranking, da_week, script_gen, video_gen
from tests.test_darkarchives_manual import _draft
from tests.test_darkarchives_ranking import _draft as _ranking

STORY = ("In December 1872 the Mary Celeste was found drifting near the Azores. The lifeboat was gone. "
         "Food and water were still aboard. The crew of ten was never seen again. ") * 3


@pytest.fixture
def bank(tmp_path, monkeypatch):
    """A bank of 9 NEW stories (ids 1-9, best first), the folders of this machine, rankings every 3rd video."""
    monkeypatch.setattr(repository, "DB_PATH", tmp_path / "bank.db")
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", tmp_path / "scripts")
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", tmp_path / "videos")
    monkeypatch.setenv("DA_SCRIPT_LEARNING", "0")
    monkeypatch.setenv("DA_RANKING_EVERY", "3")
    monkeypatch.setenv("DA_RANK_COUNT", "5")
    repository.init_db()
    for i in range(1, 10):
        repository.save_story(f"src_{i}", "r/UnresolvedMysteries", f"Case {i}" if i > 1 else "Mary Celeste", STORY,
                              f"https://x/{i}", 100 - i, pillar="mystery" if i % 2 else "history")
    return tmp_path


def _answer(*ids, rankings=(), **changes):
    scripts = [{**_draft(), "story": i, **changes} for i in ids]
    return json.dumps({"scripts": scripts, "rankings": list(rankings)})


def _ranking_json(k, ids=(3, 4, 5, 6, 7), **over):
    return {"ranking": k, **json.loads(_ranking(ids, **over).model_dump_json())}


def test_start_picks_the_best_stories_and_the_ranking_pools_and_the_prompt_holds_the_rules_once(bank):
    assert da_week.default_rankings(10) == 5 and da_week.default_rankings(2) == 1
    view = da_week.start(2, 1)
    assert [s["id"] for s in view["stories"]] == [1, 2]
    assert len(view["groups"]) == 1 and [s["id"] for s in view["groups"][0]["pool"]] == [3, 4, 5, 6, 7, 8, 9]
    assert view["to_write"] == 3 and view["to_check"] == 0 and view["ready"] == 0
    prompt = view["write_prompt"]
    assert prompt.count("TONE: documentary-style") == 1 and prompt.count("TRUTH: use only facts") == 2  # the story rules, the ranking rules
    assert "=== STORY 1: Mary Celeste ===" in prompt and "=== STORY 2: Case 2 ===" in prompt and "=== RANKING 1 ===" in prompt
    assert prompt.count("Content pillar:") == 2  # one per story
    assert prompt.count("[id 3] Case 3") == 1 and "[id 1]" not in prompt  # the pool is its own, the single stories are not in it
    assert '{"scripts": [ {"story": <id>, ...}, ... ], "rankings": [ {"ranking": 1, ...}, ... ]}' in prompt
    assert '"hook_image_prompt"' in prompt and "with exactly 5 entries" in prompt
    assert "{pillar}" not in prompt and "{source_name}" not in prompt and "{min_words}" not in prompt and "{count}" not in prompt
    assert view["facts_prompt"] == ""
    with pytest.raises(da_week.WeekError, match="Only 9 NEW stories"):
        da_week.start(12, 0)
    # rankings off: none by default
    view = da_week.start(2)
    assert len(view["groups"]) == 1  # every 3rd video: 2 stories -> 1 ranking


def test_pasted_scripts_are_checked_one_by_one(bank):
    da_week.start(2, 1)
    short = _draft()["scenes"][:2]
    view = da_week.answer(json.dumps({"scripts": [{**_draft(), "story": 1}, {**_draft(scenes=short), "story": 2}],
                                      "rankings": [_ranking_json(1, hook_text="")]}))
    steps = {s["id"]: s["step"] for s in view["stories"]}
    assert steps == {1: "facts", 2: "fix"} and view["groups"][0]["step"] == "fix"
    assert any("words" in p for p in view["stories"][1]["problems"]) and "missing title or hook_text" in view["groups"][0]["problems"]
    assert view["to_write"] == 2 and view["to_check"] == 1
    prompt = view["write_prompt"]
    assert "=== STORY 2:" in prompt and "=== STORY 1:" not in prompt and "=== RANKING 1 ===" in prompt
    assert "Story 2, ranking 1 come with your previous draft" in prompt
    assert "Your script breaks the rules" in prompt and "Your ranking breaks the rules" in prompt
    assert "=== STORY 1: Mary Celeste ===" in view["facts_prompt"] and "You fact-check 1 scripts" in view["facts_prompt"]
    assert '{"checks": [' in view["facts_prompt"]
    # the fixed ranking is ready at once: its facts are the candidates' own words
    view = da_week.answer(json.dumps({"rankings": [_ranking_json(1)]}))
    assert view["groups"][0]["step"] == "ready" and view["groups"][0]["preview"]["entries"][0]["label"] == "Case 3 Files"


def test_a_rejected_story_is_marked_in_the_bank_and_wrong_ids_are_refused(bank):
    da_week.start(2, 0)
    view = da_week.answer(_answer(2, usable=False, reject_reason="a promo"))
    assert view["stories"][1]["step"] == "rejected"
    assert repository.get_story(2)["status"] == "REJECTED" and "a promo" in repository.get_story(2)["note"]
    with pytest.raises(da_week.WeekError, match="check the ids"):
        da_week.answer(_answer(9))
    with pytest.raises(da_week.WeekError, match="no JSON object"):
        da_week.answer("sorry, I can't")
    assert "{pillar}" not in da_week.view()["write_prompt"]


def test_the_fact_check_makes_scripts_ready_or_sends_them_back(bank):
    da_week.start(2, 0)
    da_week.answer(_answer(1, 2))
    view = da_week.facts(json.dumps({"checks": [{"story": 1, "unsupported": []},
                                                {"story": 2, "unsupported": ["Built in 1900 - no source gives a year"]}]}))
    assert {s["id"]: s["step"] for s in view["stories"]} == {1: "ready", 2: "facts_fix"}
    assert "Built in 1900" in view["write_prompt"] and "A fact checker found statements" in view["write_prompt"]
    data = da_week.bundle()
    assert [s["week_i"] for s in data["stories"]] == [1] and data["stories"][0]["story"]["source_id"] == "src_1"
    assert data["stories"][0]["draft"]["title"] == "The Ship That Sailed Alone" and data["rankings"] == []
    view = da_week.answer(_answer(2))
    assert {s["id"]: s["step"] for s in view["stories"]} == {1: "ready", 2: "facts"}
    view = da_week.drop("story", 2)
    assert view["stories"][1]["step"] == "dropped" and view["to_check"] == 0
    with pytest.raises(da_week.WeekError, match="No ranking 4"):
        da_week.drop("ranking", 4)


def test_send_marks_the_stories_used_here_and_keeps_what_linux_refused(bank):
    da_week.start(2, 1)
    da_week.answer(_answer(1, 2, rankings=[_ranking_json(1)]))
    da_week.facts(json.dumps({"checks": [{"story": 1, "unsupported": []}, {"story": 2, "unsupported": []}]}))
    delivered = {}

    def deliver(settings, name, bundle):
        delivered.update(name=name, bundle=bundle)
        return {"queued": [{"kind": "story", "week_i": 1, "hub_id": 41}, {"kind": "ranking", "week_i": 1, "hub_id": 42}],
                "skipped": [{"kind": "story", "week_i": 2, "why": "already made"}], "waiting": {"stories": 1, "rankings": 1}}

    view = da_week.send(None, deliver=deliver, local=False)
    assert delivered["name"] == "darkarchives" and len(delivered["bundle"]["stories"]) == 2 and len(delivered["bundle"]["rankings"]) == 1
    assert {s["id"]: s["step"] for s in view["stories"]} == {1: "sent", 2: "ready"} and view["groups"][0]["step"] == "sent"
    assert view["stories"][0]["sent"]["hub_id"] == 41 and view["stories"][1]["problems"] == ["Linux did not take it: already made"]
    assert repository.get_story(1)["status"] == "USED" and repository.get_story(2)["status"] == "NEW"
    assert {repository.get_story(i)["status"] for i in (3, 4, 5, 6, 7)} == {"USED"} and repository.get_story(8)["status"] == "NEW"
    assert view["last_send"]["waiting"] == {"stories": 1, "rankings": 1} and view["reply"]["queued"][0]["hub_id"] == 41


def _hub_bundle(story_ids=(1,), ranking_ids=(3, 4, 5, 6, 7)):
    stories = [{"story": repository.get_story(i), "draft": _draft(), "week_i": i} for i in story_ids]
    rankings = []
    if ranking_ids:
        rankings = [{"draft": json.loads(_ranking(ranking_ids).model_dump_json()), "pool": [repository.get_story(i) for i in ranking_ids],
                     "n": 5, "week_k": 1}]
    return {"stories": stories, "rankings": rankings}


def test_the_hub_keeps_the_scripts_in_its_bank_and_the_production_run_uses_them_first(bank, monkeypatch):
    posts = []
    monkeypatch.setattr(da_channel, "announce_text", lambda text, settings=None: posts.append(text))
    sent = _hub_bundle()  # the sender's records: on the hub only story 1 exists, under another id
    # the hub's bank: a different machine
    monkeypatch.setattr(repository, "DB_PATH", bank / "hub.db")
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", bank / "hub-scripts")
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", bank / "hub-videos")
    repository.init_db()
    repository.save_story("other", "r/x", "Something else", STORY, "", 50, pillar="mystery")
    repository.save_story("other2", "r/x", "Something else again", STORY, "", 50, pillar="history")
    repository.save_story("src_1", "r/UnresolvedMysteries", "Mary Celeste", STORY, "https://x/1", 99, pillar="mystery")
    result = channel_hub.DarkOutliers().queue(None, sent)
    assert [(q["kind"], q["week_i"]) for q in result["queued"]] == [("story", 1), ("ranking", 1)] and result["skipped"] == []
    hub_story = repository.find_source("src_1")
    assert hub_story["id"] == 3 and hub_story["status"] == "SCRIPT_READY" and hub_story["note"].startswith("written for later")
    assert script_gen.script_path(3).is_file() and json.loads(script_gen.script_path(3).read_text())["story_id"] == 3
    ranking = repository.get_story(result["queued"][1]["hub_id"])
    assert ranking["source_id"].startswith("ranking_") and ranking["status"] == "SCRIPT_READY"
    script = script_gen.Script.model_validate_json(script_gen.script_path(ranking["id"]).read_text())
    assert [e.story_id for e in script.ranking] == [repository.find_source(f"src_{i}")["id"] for i in (3, 4, 5, 6, 7)] == [4, 5, 6, 7, 8]  # the hub's ids
    assert {repository.find_source(f"src_{i}")["status"] for i in (3, 4, 5, 6, 7)} == {"USED"}
    assert result["waiting"] == {"stories": 1, "rankings": 1} and "2 Dark Outliers script(s)" in posts[0]
    # sent again: both are skipped (a story the hub scripted, a ranking whose stories the hub told)
    again = channel_hub.DarkOutliers().queue(None, sent)
    assert again["queued"] == [] and [s["why"].split(' "')[0] for s in again["skipped"]] == ["the hub already has a script for", "the hub already told or scripted"]
    # the production run takes them before Gemini
    monkeypatch.setattr(script_gen, "process", lambda *a, **k: pytest.fail("Gemini was asked"))
    monkeypatch.setattr(da_ranking, "process", lambda *a, **k: pytest.fail("Gemini was asked for a ranking"))
    got = da_produce.next_script()
    assert got["story"]["id"] == 3 and got["script"].title == "The Ship That Sailed Alone"
    got = da_produce.ranking_script()
    assert got["story"]["id"] == ranking["id"] and got["script"].ranking
    # once its video exists, the script is no longer waiting
    video_gen.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    video_gen.info_path(3).write_text("{}")
    assert da_produce.waiting() == {"stories": 0, "rankings": 1}
    assert da_produce.prewritten() is None


def test_the_hub_skips_what_it_cannot_use(bank, monkeypatch):
    monkeypatch.setattr(da_channel, "announce_text", lambda text, settings=None: None)
    repository.set_status(1, "USED", note="made")
    bundle = _hub_bundle(story_ids=(1, 2), ranking_ids=(3, 4, 5, 6, 7))
    bundle["rankings"][0]["draft"]["entries"][0]["story_id"] = 99  # outside its candidates
    bundle["stories"].append({"story": {}, "draft": _draft(), "week_i": 7})
    result = channel_hub.DarkOutliers().queue(None, bundle)
    assert [q["week_i"] for q in result["queued"]] == [2]
    assert [(s["kind"], s["week_i"], s["why"].split(' "')[0]) for s in result["skipped"]] == [
        ("story", 1, "the hub already made"), ("story", 7, "no story or no draft"), ("ranking", 1, "its entries point outside its candidates")]
    with pytest.raises(channel_hub.HubError, match="No scripts"):
        channel_hub.DarkOutliers().queue(None, {"stories": [], "rankings": []})


def test_the_week_endpoints(bank, monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    monkeypatch.setattr(channel_hub.hub, "is_sender", lambda s: False)
    monkeypatch.setattr(da_channel, "announce_text", lambda text, settings=None: None)
    client = TestClient(web.app)
    body = client.get("/api/darkarchives/week").json()
    assert body["week"] is None and body["new"] == 9 and body["default_rankings"] == 5 and body["waiting"] == {"stories": 0, "rankings": 0}
    body = client.post("/api/darkarchives/week", json={"count": 2, "rankings": 0}).json()
    assert [s["id"] for s in body["stories"]] == [1, 2] and body["groups"] == []
    assert client.post("/api/darkarchives/week/answer", json={"text": "nope"}).status_code == 400
    body = client.post("/api/darkarchives/week/answer", json={"text": _answer(1, 2)}).json()
    assert body["to_check"] == 2
    body = client.post("/api/darkarchives/week/drop/story/2").json()
    assert body["stories"][1]["step"] == "dropped"
    assert client.post("/api/darkarchives/week/drop/topic/2").status_code == 422
    body = client.post("/api/darkarchives/week/facts", json={"text": json.dumps({"checks": [{"story": 1, "unsupported": []}]})}).json()
    assert body["ready"] == 1
    # on the hub itself the send goes straight into this bank: the story keeps its script, SCRIPT_READY, for the production run
    body = client.post("/api/darkarchives/week/send").json()
    assert body["stories"][0]["step"] == "sent" and body["reply"]["queued"][0]["hub_id"] == 1
    assert repository.get_story(1)["status"] == "SCRIPT_READY" and da_produce.waiting() == {"stories": 1, "rankings": 0}
    assert client.get("/api/darkarchives/week").json()["waiting"] == {"stories": 1, "rankings": 0}
