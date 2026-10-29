"""SideQuestLogic "Write a week": one prompt for several games' Gamer IQ questions, the pasted candidates checked, the
fact-check verdicts, the passed questions into the hub's queue, and the production run asking them before Gemini
(trendclip/gamer_week.py, trendclip/gamer_queue.py, channel_hub.GamerIQ)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from modules import channel_hub
from trendclip import gamer_queue, gamer_week, quiz, script_writer
from trendclip.web import app as web
from tests.test_quiz import gamer, make_settings


def _answer(*items):
    return json.dumps({"candidates": [{**q.model_dump(), "game": game, "shape": shape} for game, shape, q in items]})


def test_the_games_line_and_the_prompt_hold_each_game_once(tmp_path):
    assert gamer_week.parse_games("Minecraft 7, GTA V / Online: 3, Fortnite") == [("Minecraft", 7), ("GTA V / Online", 3), ("Fortnite", 7)]
    assert gamer_week.parse_games("Minecraft 2; minecraft 0, Minecraft 1") == [("Minecraft", 3)]
    s = make_settings(tmp_path)
    with pytest.raises(gamer_week.WeekError, match="which games"):
        gamer_week.start(s, "  ")
    view = gamer_week.start(s, "Minecraft 2, GTA V 1")
    assert view["games"] == [{"game": "Minecraft", "count": 2}, {"game": "GTA V", "count": 1}] and view["to_write"] == 3
    prompt = view["write_prompt"]
    assert prompt.count(quiz.GAMER_INTRO[:30]) == 1 and prompt.count("Shapes: mix them") == 1
    assert "=== GAME: Minecraft (4 candidates, 2 will be used) ===" in prompt and "=== GAME: GTA V (3 candidates, 1 will be used) ===" in prompt
    assert "about Minecraft" in prompt and "about GTA V" in prompt and "{shape}" not in prompt and "{count}" not in prompt
    assert '"game": "Minecraft"' in prompt and '"shape": "number"' in prompt
    assert view["facts_prompt"] == "" and view["level"] == 1


def test_pasted_candidates_get_the_shape_checks_and_wait_for_the_fact_check(tmp_path):
    s = make_settings(tmp_path)
    gamer_week.start(s, "Minecraft 2, GTA V 1")
    quiz._remember(s, "gamer", answer="10", question="How many obsidian blocks make the smallest Nether portal?", game="Minecraft",
                   shape="number", level="1")  # asked on this machine already
    view = gamer_week.answer(s, "Sure!\n```json\n" + _answer(
        ("Minecraft", "number", gamer()),  # already asked
        ("minecraft", "fake", gamer(question="Which mob is NOT in Minecraft", trap="Drowned", distractor="Phantom", correct="Hollow")),
        ("Minecraft", "first", gamer(question="Which came first in Minecraft", trap="10", distractor="10", correct="12")),  # same options
        ("Minecraft", "lore", gamer(question="What was the creeper originally meant to be in Minecraft", trap="a cow", distractor="a zombie", correct="a pig")),
        ("Minecraft", "lore", gamer(question="How many blocks tall is a Minecraft Enderman", trap="2", distractor="4", correct="3")),  # one too many
        ("Fortnite", "number", gamer(question="How many players start a Fortnite match", trap="99", distractor="120", correct="100")),
    ) + "\n```")
    assert [(q["game"], q["shape"], q["step"]) for q in view["questions"]] == [("Minecraft", "fake", "facts"), ("Minecraft", "lore", "facts")]
    assert view["questions"][0]["spoken"] == "Which mob is NOT in Minecraft?" and view["questions"][0]["options"] == ["Drowned", "Phantom", "Hollow"]
    assert [p.split(":")[-1].strip()[:28] for p in view["problems"]] == [
        "already asked", "needs three short, different", "Minecraft has enough questio", "'Fortnite' is not a game of "]
    assert view["to_write"] == 1 and view["need"] == [{"game": "GTA V", "missing": 1}] and view["to_check"] == 2
    assert "=== GAME: GTA V" in view["write_prompt"] and "=== GAME: Minecraft" not in view["write_prompt"]
    assert "Which mob is NOT in Minecraft" in view["write_prompt"]  # the LLM is told not to repeat it
    assert "1. [Minecraft] Q: Which mob is NOT in Minecraft?" in view["facts_prompt"] and "2. [Minecraft] Q: What was the creeper" in view["facts_prompt"]
    with pytest.raises(gamer_week.WeekError, match="No usable question"):
        gamer_week.answer(s, _answer(("Minecraft", "number", gamer())))
    with pytest.raises(gamer_week.WeekError, match="no JSON object"):
        gamer_week.answer(s, "sorry")


def test_verdicts_make_questions_ready_or_reject_them_for_good(tmp_path):
    s = make_settings(tmp_path)
    gamer_week.start(s, "Minecraft 2")
    gamer_week.answer(s, _answer(("Minecraft", "number", gamer()),
                                 ("Minecraft", "fake", gamer(question="Which mob is NOT in Minecraft", trap="Drowned", distractor="Phantom", correct="Hollow"))))
    with pytest.raises(gamer_week.WeekError, match="verdict lines"):
        gamer_week.facts(s, "all good")
    view = gamer_week.facts(s, "1: PASS - on the wiki\n2: FAIL: the Hollow is a real mob")
    assert [q["step"] for q in view["questions"]] == ["ready", "rejected"] and view["questions"][1]["why"] == "the Hollow is a real mob"
    assert view["ready"] == 1 and view["to_write"] == 1  # Minecraft is one short again
    assert "Which mob is NOT in Minecraft (Hollow)" in view["write_prompt"]  # never asked for again
    view = gamer_week.drop(s, 1)
    assert view["questions"][0]["step"] == "dropped" and view["ready"] == 0 and view["to_write"] == 2
    with pytest.raises(gamer_week.WeekError, match="No question 9"):
        gamer_week.drop(s, 9)


def test_send_puts_the_passed_questions_in_the_queue_and_keeps_what_linux_refused(tmp_path):
    s = make_settings(tmp_path)
    gamer_week.start(s, "Minecraft 2")
    gamer_week.answer(s, _answer(("Minecraft", "number", gamer()),
                                 ("Minecraft", "fake", gamer(question="Which mob is NOT in Minecraft", trap="Drowned", distractor="Phantom", correct="Hollow"))))
    gamer_week.facts(s, "1: PASS\n2: PASS")
    delivered = {}

    def deliver(settings, name, bundle):
        delivered.update(name=name, bundle=bundle)
        return {"queued": [{"week_i": 1, "name": "q1.json"}], "skipped": [{"week_i": 2, "why": "already asked or queued on the hub"}],
                "in_queue": {"Minecraft": 3}}

    view = gamer_week.send(s, deliver=deliver)
    assert delivered["name"] == "sidequestlogic" and [q["week_i"] for q in delivered["bundle"]["questions"]] == [1, 2]
    assert delivered["bundle"]["questions"][0]["question"]["correct"] == "10" and delivered["bundle"]["questions"][0]["shape"] == "number"
    assert [q["step"] for q in view["questions"]] == ["sent", "ready"] and view["questions"][0]["sent"]["name"] == "q1.json"
    assert view["questions"][1]["why"] == "Linux did not take it: already asked or queued on the hub"
    assert view["last_send"]["in_queue"] == {"Minecraft": 3} and view["reply"]["queued"][0]["week_i"] == 1
    with pytest.raises(gamer_week.WeekError, match="already on Linux"):
        gamer_week.drop(s, 1)


def test_the_hub_queues_the_questions_and_the_production_run_asks_them_before_gemini(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    posts = []
    monkeypatch.setattr(channel_hub.hub, "Rest", lambda settings: type("R", (), {"post": lambda self, text, *a, **k: posts.append(text)})())
    monkeypatch.setattr(channel_hub.hub, "is_sender", lambda settings: False)
    quiz._remember(s, "gamer", answer="3", question="How many blocks tall is a Minecraft Enderman?", game="Minecraft", shape="number", level="1")
    questions = [
        {"game": "Minecraft", "shape": "number", "question": gamer().model_dump(), "week_i": 1},
        {"game": "GTA V / Online", "shape": "lore", "question": gamer(question="What city is GTA V set in", trap="Liberty City", distractor="Vice City", correct="Los Santos").model_dump(), "week_i": 2},
        {"game": "Minecraft", "shape": "number", "question": gamer(question="How many blocks tall is a Minecraft Enderman", trap="2", distractor="4", correct="3").model_dump(), "week_i": 3},
        {"game": "", "shape": "number", "question": gamer().model_dump(), "week_i": 4},
        {"game": "Minecraft", "shape": "fake", "question": {"question": "half a question"}, "week_i": 5},
    ]
    result = channel_hub.queue_scripts(s, "sidequestlogic", {"questions": questions})
    assert [q["week_i"] for q in result["queued"]] == [1, 2] and result["in_queue"] == {"Minecraft": 1, "GTA V / Online": 1}
    assert [(sk["week_i"], sk["why"].split(",")[0][:23]) for sk in result["skipped"]] == [
        (3, "already asked or queued"), (4, "no game"), (5, "not a complete question")]
    assert result["queued"][0]["name"].endswith("-01-minecraft.json") and "2 Gamer IQ question(s)" in posts[0]
    # the same question again: queued already
    again = channel_hub.queue_scripts(s, "sidequestlogic", {"questions": questions[:1]})
    assert again["queued"] == [] and again["skipped"][0]["why"] == "already asked or queued on the hub"
    with pytest.raises(channel_hub.HubError, match="No questions"):
        channel_hub.queue_scripts(s, "sidequestlogic", {"questions": []})
    # the production run prefers the games with questions waiting, Minecraft's question first
    assert gamer_queue.prefer(s, ["Fortnite", "GTA V / Online", "Minecraft"]) == ["Minecraft", "GTA V / Online"]
    assert gamer_queue.prefer(s, ["Fortnite"]) == ["Fortnite"]
    # the writer takes the Minecraft question without Gemini; the next Minecraft video asks Gemini again
    gemini = type("C", (), {"models": type("M", (), {"generate_content": lambda *a, **k: pytest.fail("Gemini was asked")})()})()
    script = script_writer.write_script(s, "Minecraft", mode="gamer", client=gemini)
    assert script.model == "week" and script.script.startswith("How many obsidian blocks") and "Level 2" in script.title
    assert gamer_queue.games(s) == {"GTA V / Online": 1} and quiz.gamer_level(s) == 3
    assert gamer_queue.take(s, "minecraft") is None


def test_the_week_endpoints(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    monkeypatch.setattr(web, "_base_settings", lambda: s)
    monkeypatch.setattr(web.video_downloader, "get_library", lambda settings: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(channel_hub.hub, "Rest", lambda settings: type("R", (), {"post": lambda self, text, *a, **k: None})())
    monkeypatch.setattr(channel_hub.hub, "is_sender", lambda settings: False)
    client = TestClient(web.app)
    body = client.get("/api/gamer/week").json()
    assert body["week"] is None and body["games"] == ["Minecraft"] and body["in_queue"] == {} and body["level"] == 1
    assert client.post("/api/gamer/week", json={"games": " "}).status_code == 400
    body = client.post("/api/gamer/week", json={"games": "Minecraft 1"}).json()
    assert body["to_write"] == 1
    assert client.post("/api/gamer/week/answer", json={"text": "nope"}).status_code == 400
    body = client.post("/api/gamer/week/answer", json={"text": _answer(("Minecraft", "number", gamer()))}).json()
    assert body["to_check"] == 1
    body = client.post("/api/gamer/week/facts", json={"text": "1: PASS"}).json()
    assert body["ready"] == 1
    body = client.post("/api/gamer/week/send").json()
    assert body["questions"][0]["step"] == "sent" and body["reply"]["in_queue"] == {"Minecraft": 1}
    assert client.get("/api/gamer/week").json()["in_queue"] == {"Minecraft": 1}
    assert client.post("/api/gamer/week/drop/1").status_code == 400  # on Linux already
