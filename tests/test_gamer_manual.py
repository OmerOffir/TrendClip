import json
import random
from datetime import date

import pytest
from fastapi.testclient import TestClient

from trendclip import analytics, assistant, gamer_manual, planner, quiz, script_writer
from trendclip.web import app as web

from tests.test_quiz import gamer, make_settings
from tests.test_upload import add_short


def clip(tmp_path, game, name):
    return assistant.BackgroundClip(filename=name, path=str(tmp_path / name), game=game, source="youtube", query=game,
                                    title="t", source_url="u", author="a", license_note="free")


def candidates(*items):
    return json.dumps({"candidates": [i.model_dump() for i in items]})


def test_write_it_myself_prompt_answer_fact_check_then_ready(tmp_path):
    s = make_settings(tmp_path)
    view = gamer_manual.start(s, "Minecraft", random.Random(1))
    assert view["step"] == "write" and view["level"] == 1
    assert "about Minecraft" in view["prompt"] and '"candidates"' in view["prompt"] and "Write 4 candidates" in view["prompt"]
    assert quiz.QUIZ_INSTRUCTION[:40] in view["prompt"]  # no system prompt by hand: it leads the prompt

    bad = gamer(trap="10")  # two options are the same
    pasted = "Sure! Here you go:\n```json\n" + candidates(gamer(), bad).replace('"10"', '"10",', 0) + "\n```"
    view = gamer_manual.answer(s, view["id"], pasted)
    assert view["step"] == "facts" and len(view["questions"]) == 1 and "three short, different" in view["problems"][0]
    assert "wiki" in view["prompt"] and "1. Q: How many obsidian blocks" in view["prompt"]

    with pytest.raises(gamer_manual.ManualError, match="verdict lines"):
        gamer_manual.facts(s, view["id"], "Looks right to me")
    view = gamer_manual.facts(s, view["id"], "**1: PASS** - checked on the wiki")
    assert view["step"] == "ready" and view["chosen"] == 0 and view["questions"][0]["passed"] is True
    game, question, shape = gamer_manual.question(s, view["id"])
    assert game == "Minecraft" and question.correct == "10" and shape in quiz.GAMER_SHAPES
    assert gamer_manual.sessions(s)[0]["id"] == view["id"]


def test_failed_fact_check_asks_for_new_questions_and_never_the_rejected_ones(tmp_path):
    s = make_settings(tmp_path)
    sid = gamer_manual.start(s, "GTA V / Online")["id"]
    gamer_manual.answer(s, sid, candidates(gamer()))
    view = gamer_manual.facts(s, sid, "1: FAIL: corners count in Bedrock")
    assert view["step"] == "write" and "corners count in Bedrock" in view["problems"][0]
    assert "How many obsidian blocks make the smallest Nether portal (10)" in view["prompt"]  # listed as already asked
    view = gamer_manual.answer(s, sid, candidates(gamer()))  # the same one again
    assert view["step"] == "write" and "already asked" in view["problems"][0]
    view = gamer_manual.answer(s, sid, gamer(question="Which car is NOT in GTA V", correct="Ferrari Enzo").model_dump_json())
    assert view["step"] == "facts"  # one question without the list around it is fine too


def test_a_manual_question_becomes_the_next_level_over_its_own_game(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    mc = clip(tmp_path, "Minecraft", "mc.mp4")
    monkeypatch.setattr(assistant, "fresh_clip", lambda settings, game, progress, only: mc if only == ("Minecraft",) else None)
    rendered = {}
    monkeypatch.setattr(assistant, "_render_script", lambda settings, c, script, progress, mode: rendered.update(
        clip=c, script=script, mode=mode) or "short")
    assert assistant.create_gamer_short(s, "Minecraft", gamer(), "number") == "short"
    assert rendered["mode"] == "gamer" and rendered["script"].title_card == "LEVEL 1 - MINECRAFT FANS ONLY"
    assert rendered["script"].model == "manual" and quiz.history(s, "gamer")[0]["shape"] == "number"
    other = clip(tmp_path, "GTA V / Online", "gta.mp4")
    monkeypatch.setattr(assistant, "fresh_clip", lambda *a, **k: other)
    with pytest.raises(assistant.DownloadError, match="about Minecraft"):  # never a question over another game
        assistant.create_gamer_short(s, "Minecraft", gamer(), "number")


def test_the_dashboard_runs_the_whole_flow(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    monkeypatch.setattr(web, "_base_settings", lambda: s)
    monkeypatch.setattr(web.video_downloader, "get_library", lambda settings: (_ for _ in ()).throw(RuntimeError("offline")))
    client = TestClient(web.app)
    listing = client.get("/api/gamer/manual").json()
    assert listing["games"] == ["Minecraft"] and listing["level"] == 1 and listing["slots"]
    sid = client.post("/api/gamer/manual", json={"game": "Minecraft"}).json()["id"]
    assert client.post(f"/api/gamer/manual/{sid}/answer", json={"text": "no json here"}).status_code == 400
    client.post(f"/api/gamer/manual/{sid}/answer", json={"text": candidates(gamer(), gamer(question="Q2", correct="7"))})
    view = client.post(f"/api/gamer/manual/{sid}/facts", json={"text": "1: FAIL: no\n2: PASS"}).json()
    assert view["chosen"] == 1
    assert client.post(f"/api/gamer/manual/{sid}/choose", json={"index": 0}).status_code == 400  # it failed
    made, name = {}, add_short(s, "mc.mp4")
    monkeypatch.setattr(assistant, "create_gamer_short", lambda settings, game, q, shape, progress: made.update(
        game=game, q=q.question) or web.shorts.get_short(settings, name))
    slot = listing["slots"][0]
    job = client.post(f"/api/gamer/manual/{sid}/make", json=slot).json()
    for _ in range(100):
        state = client.get(f"/api/create/jobs/{job['id']}").json()
        if state["status"] in ("done", "error"):
            break
        import time
        time.sleep(0.05)
    assert state["status"] == "done", state
    assert made == {"game": "Minecraft", "q": "Q2"}
    assert client.get(f"/api/gamer/manual/{sid}").json()["step"] == "made"
    item = planner.slot_item(s, date.fromisoformat(slot["day"]), slot["slot"])
    assert item and item.short == "mc.mp4"


def test_a_gamer_iq_out_of_quota_becomes_a_math_challenge(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    made = []

    def create(settings, game, progress, mode, only=(), target_seconds=None):
        made.append(mode)
        if mode == "gamer":
            raise script_writer.QuotaError("Gemini quota reached")
        return mode

    monkeypatch.setattr(assistant, "create_random_short", create)
    assert assistant.create_auto_short(s, "Minecraft", lambda f, m: None, "gamer") == "math" and made == ["gamer", "math"]
    monkeypatch.setattr(assistant, "create_random_short", lambda *a, **k: (_ for _ in ()).throw(
        script_writer.ScriptError("No question passed the fact-check")))
    with pytest.raises(script_writer.ScriptError):  # not quota: the bot tries Gamer IQ again
        assistant.create_auto_short(s, "Minecraft", lambda f, m: None, "gamer")


def test_one_video_a_day_is_on_the_daily_game_and_the_other_avoids_it(tmp_path):
    s = make_settings(tmp_path).with_overrides(auto_pick_daily_game="Minecraft")
    day = date(2026, 11, 2)
    own = planner.SLOTS[day.toordinal() % 2]
    other = next(x for x in planner.SLOTS if x != own)
    assert analytics.daily_game(s, day, own) == ("Minecraft", None)
    assert analytics.daily_game(s, day, other) == (None, "Minecraft")
    assert analytics.daily_game(s, day + date.resolution, own) == (None, "Minecraft")  # the slot alternates
    # The other slot filled with GTA first: the daily slot still gets Minecraft; once it has it, never twice.
    planner.add_item(s, planner.ItemRequest(date=day, slot=other, short=add_short(s, "g.mp4")))
    assert analytics.daily_game(s, day, own) == ("Minecraft", None)
    # Minecraft missing and only this slot left: it takes Minecraft even if it isn't its usual slot.
    nxt = day + date.resolution
    nxt_own = planner.SLOTS[nxt.toordinal() % 2]
    nxt_other = next(x for x in planner.SLOTS if x != nxt_own)
    planner.add_item(s, planner.ItemRequest(date=nxt, slot=nxt_own, short=add_short(s, "g2.mp4")))
    assert analytics.daily_game(s, nxt, nxt_other) == ("Minecraft", None)
    mc = add_short(s, "m.mp4")
    import json as _j
    path = s.shorts_dir / "m.json"
    path.write_text(_j.dumps({**_j.loads(path.read_text()), "game": "Minecraft"}))
    planner.add_item(s, planner.ItemRequest(date=nxt + date.resolution, slot=planner.SLOTS[0], short=mc))
    assert analytics.daily_game(s, nxt + date.resolution, planner.SLOTS[1]) == (None, "Minecraft")
    assert analytics.daily_game(s.with_overrides(auto_pick_daily_game=""), day, own) == (None, None)
