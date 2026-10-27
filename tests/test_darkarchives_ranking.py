"""Dark Outliers ranking videos: Gemini's draft checked, the script, the bank, the list on screen, production."""

from __future__ import annotations

import json
from datetime import date as Day

import pytest

from db import repository
from modules import da_produce, da_ranking, script_gen, video_gen


def _bank(tmp_path, n=6):
    db = tmp_path / "bank.db"
    repository.init_db(db)
    for i in range(1, n + 1):
        repository.save_story(f"gemini_{i}", "gemini", f"Case {i}", f"In 19{50 + i}, case {i} happened. " * 20, "", 100 - i,
                              pillar="mystery" if i % 2 else "history", db_path=db)
    return db


def _draft(ids=(1, 2, 3, 4, 5), **over):
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    entries = [da_ranking.EntryDraft(story_id=sid, rank=r, label=f"Case {sid} Files",
                                     narration=f"Number {words[r]}. In 1959 nine hikers fled their tent.",
                                     image_prompt="cinematic dark photo of an old house, 35mm")
               for r, sid in enumerate(ids, 1)]
    data = dict(title="5 Cases That Still Make No Sense", hook_text="Five cases never closed. Number five.",
                thumbnail_text="5 CASES. NO ANSWERS", entries=entries)
    data.update(over)
    return da_ranking.RankingDraft(**data)


def test_check_finds_the_problems():
    ids = {1, 2, 3, 4, 5, 6}
    assert da_ranking.check(_draft(), ids, 5) == []
    bad = _draft(ids=(1, 2, 3, 4, 4))
    assert any("different CANDIDATE" in p for p in da_ranking.check(bad, ids, 5))
    assert any("instead of 5" in p for p in da_ranking.check(_draft(ids=(1, 2, 3)), ids, 5))
    wrong_start = _draft()
    wrong_start.entries[0].narration = "In 1959 nine hikers fled their tent into the snow at night."
    assert any('start with "Number one."' in p for p in da_ranking.check(wrong_start, ids, 5))
    too_long = _draft()
    too_long.entries[1].narration = "Number two. " + "word " * 40
    assert any("rank 2: 42 words" in p for p in da_ranking.check(too_long, ids, 5))
    assert any("title" in p for p in da_ranking.check(_draft(title=""), ids, 5))
    over = _draft(hook_text="Five cases the files never closed and nobody can explain any of them today.")  # 14 + 45
    assert any("hook_text over" in p for p in da_ranking.check(over, ids, 5)) and not any("words in all" in p for p in da_ranking.check(over, ids, 5))
    way_over = _draft()
    way_over.entries[0].narration = "Number one. " + "word " * 20  # 22 + 36 + 6
    assert any("64 words in all" in p for p in da_ranking.check(way_over, ids, 5))


def test_the_hook_never_ends_with_the_first_number():
    assert da_ranking.clean_hook("Five cases the files never closed. Number five.") == "Five cases the files never closed."
    assert da_ranking.clean_hook("Five cases the files never closed, number 5") == "Five cases the files never closed."
    assert da_ranking.clean_hook("Number five is the worst of all.") == "Number five is the worst of all."
    assert da_ranking.clean_hook("Five cases the files never closed.") == "Five cases the files never closed."
    stories = {i: {"title": f"Case {i}"} for i in range(1, 6)}
    assert da_ranking.to_script(1, _draft(), stories).hook_text == "Five cases never closed."


def test_to_script_counts_down_and_keeps_the_ranking():
    stories = {i: {"title": f"Case {i}"} for i in range(1, 6)}
    script = da_ranking.to_script(42, _draft(), stories)
    assert script.story_id == 42 and [s.narration[:12] for s in script.scenes] == ["Number five."] + ["Number four."] + ["Number three"] + ["Number two. "] + ["Number one. "]
    assert [e.rank for e in script.ranking] == [1, 2, 3, 4, 5] and script.ranking[0].title == "Case 1"
    assert script.scenes[0].overlay_text == "CASE 5 FILES" and script.thumbnail_text == "5 CASES. NO ANSWERS"
    assert 45 <= script.total_word_count <= 56 and script.estimated_duration_seconds >= 19
    # a script written before rankings existed still loads
    old = json.loads(script.model_dump_json(exclude={"ranking"}))
    assert script_gen.Script.model_validate(old).ranking == []


def test_process_saves_the_ranking_as_a_story(tmp_path, monkeypatch):
    db = _bank(tmp_path)
    scripts = tmp_path / "scripts"
    monkeypatch.setattr(da_ranking, "write_ranking", lambda stories, client=None, n=None, today=None: _draft())
    result = da_ranking.process(db_path=db, out_dir=scripts)
    story = result["story"]
    assert story["source_id"].startswith("ranking_") and story["status"] == "SCRIPT_READY" and story["pillar"] == "mystery"
    assert result["script"].story_id == story["id"] and (scripts / f"story_{story['id']}.json").is_file()
    assert "1. Case 1 Files (story 1: Case 1)" in story["raw_text"]
    for i in range(1, 6):
        assert repository.get_story(i, db)["status"] == "USED" and f"ranking {story['id']}" in repository.get_story(i, db)["note"]
    assert repository.get_story(6, db)["status"] == "NEW"
    assert [s["id"] for s in da_ranking.candidates(db)] == [6]  # the ranking itself is never a candidate
    with pytest.raises(da_ranking.RankingError, match="needs 5"):
        monkeypatch.undo()
        da_ranking.write_ranking(da_ranking.candidates(db), n=5)


def test_panel_events_fill_in_from_the_last_place(tmp_path):
    stories = {i: {"title": f"Case {i}"} for i in range(1, 6)}
    script = da_ranking.to_script(7, _draft(), stories)
    beats = video_gen.beats_of(script)
    t = 0.0
    for b in beats:
        b.start, b.end = t, t + 10.0
        t += 10.0
    styles, events = da_ranking.panel(script, beats, 61.0)
    assert styles[0].startswith("Style: Panel,{font},")
    numbers_before = [e for e in events if e.endswith("5.") or e.endswith("1.")]
    assert any("0:00:00.00,0:00:10.00,Panel" in e and e.endswith("5.") for e in events)  # rank 5 is dim until its beat
    assert any("0:00:00.00,0:00:50.00,Panel" in e and e.endswith("1.") for e in events)  # rank 1 waits the longest
    assert any("0:00:10.00,0:00:20.00,Panel" in e and "5. CASE 5 FILES" in e and "fscx118" in e for e in events)  # spoken: big
    assert any("0:00:20.00,0:01:01.00,Panel" in e and "5.{" in e and "CASE 5 FILES" in e for e in events)  # then white
    assert any("0:00:50.00,0:01:01.00,Panel" in e and "1. CASE 1 FILES" in e for e in events)
    assert len(numbers_before) == 2 and da_ranking.panel(script_gen.Script.model_validate({**script.model_dump(), "ranking": []}), beats, 61.0) == ([], [])


def test_due_every_nth_video(tmp_path, monkeypatch):
    out = tmp_path / "videos"
    out.mkdir()
    monkeypatch.setenv("DA_RANKING_EVERY", "3")
    assert not da_ranking.due(out)  # too young
    for i, ranked in enumerate([False, False], 1):
        (out / f"story_{i}.json").write_text(json.dumps({"created_at": f"2026-11-0{i}T10:00:00", "ranking": []}))
    assert da_ranking.due(out)
    (out / "story_3.json").write_text(json.dumps({"created_at": "2026-11-03T10:00:00", "ranking": [{"rank": 1}]}))
    assert not da_ranking.due(out)
    (out / "story_4.json").write_text(json.dumps({"created_at": "2026-11-04T10:00:00"}))
    (out / "story_5.json").write_text(json.dumps({"created_at": "2026-11-05T10:00:00"}))
    assert da_ranking.due(out)
    monkeypatch.setenv("DA_RANKING_EVERY", "0")
    assert not da_ranking.due(out)
    monkeypatch.setenv("DA_RANK_COUNT", "9")
    assert da_ranking.count() == 8


def test_produce_one_makes_a_ranking_when_asked_and_falls_back(tmp_path, monkeypatch):
    from trendclip.config import get_settings

    settings = get_settings()
    db = _bank(tmp_path)
    scripts, out = tmp_path / "scripts", tmp_path / "videos"
    scripts.mkdir()
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", scripts)
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", out)
    monkeypatch.setattr(da_ranking, "write_ranking", lambda stories, client=None, n=None, today=None: _draft())
    made = []

    def make(story_id, progress, out_dir=None, scripts_dir=None, db_path=None, settings=None, announce=False):
        out.mkdir(exist_ok=True)
        script = script_gen.Script.model_validate_json((scripts_dir / f"story_{story_id}.json").read_text())
        made.append(story_id)
        (out / f"story_{story_id}.mp4").write_bytes(b"\x00" * 8)
        info = {"story_id": story_id, "title": script.title, "filename": f"story_{story_id}.mp4", "duration_seconds": 28.0,
                "pictures": "local", "words": 60, "photos": [], "ranking": [e.model_dump() for e in script.ranking],
                "created_at": "2026-11-01T10:00:00"}
        (out / f"story_{story_id}.json").write_text(json.dumps(info))
        repository.set_status(story_id, "USED", db_path, note="video")
        return info

    result = da_produce.produce_one(settings, Day(2026, 11, 2), "14:30", db_path=db, scripts_dir=scripts, out_dir=out,
                                    make=make, ranking=True)
    assert result["status"] == "planned" and result["info"]["title"] == "5 Cases That Still Make No Sense"
    assert len(result["info"]["ranking"]) == 5 and repository.get_story(result["story_id"], db)["source_id"].startswith("ranking_")

    # the next ranking has one story left: it fails and a single story is told instead
    def fake_process(story_id=None, client=None, out_dir=None, db_path=None):
        story = repository.get_story(story_id, db_path)
        (out_dir / f"story_{story_id}.json").write_text(script_gen.Script(story_id=story_id, title=story["title"], hook_text="h",
                                                                            estimated_duration_seconds=40, total_word_count=120, scenes=[]).model_dump_json())
        repository.set_status(story_id, "SCRIPT_READY", db_path)
        return {"story": story, "script": script_gen.Script(story_id=story_id, title=story["title"], hook_text="h",
                                                             estimated_duration_seconds=40, total_word_count=120, scenes=[]),
                "path": out_dir / f"story_{story_id}.json", "status": "SCRIPT_READY", "reason": ""}

    monkeypatch.setattr(script_gen, "process", fake_process)
    monkeypatch.undo()
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", scripts)
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", out)
    monkeypatch.setattr(script_gen, "process", fake_process)
    monkeypatch.setenv("DA_RANKING_EVERY", "2")
    first = out / f"story_{result['story_id']}.json"
    first.write_text(json.dumps({**json.loads(first.read_text()), "ranking": []}))  # as if the last video told one story
    assert da_ranking.due(out)
    messages = []
    result = da_produce.produce_one(settings, Day(2026, 11, 2), "23:00", lambda f, m: messages.append(m), db_path=db,
                                    scripts_dir=scripts, out_dir=out, make=make, ranking=None)
    assert result["status"] == "planned" and result["info"]["title"] == "Case 6"
    assert any("No ranking this time" in m for m in messages)
    with pytest.raises(da_produce.ProduceError, match="ranking failed"):
        da_produce.produce_one(settings, Day(2026, 11, 3), "14:30", db_path=db, scripts_dir=scripts, out_dir=out, make=make, ranking=True)


def test_ranking_endpoint(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    monkeypatch.setattr(da_ranking, "process", lambda **k: {"story": {"id": 9}, "script": script_gen.Script(
        story_id=9, title="5 Cases", hook_text="h", estimated_duration_seconds=50, total_word_count=100, scenes=[]),
        "path": None, "status": "SCRIPT_READY", "reason": ""})
    client = TestClient(web.app)
    assert client.post("/api/darkarchives/ranking").status_code == 202
    for _ in range(50):
        job = client.get("/api/darkarchives/script-job").json()
        if not job["running"]:
            break
        time.sleep(0.05)
    assert job["ranking"] and job["story_id"] == 9 and job["title"] == "5 Cases"


def test_ranking_video_end_to_end(tmp_path, monkeypatch):
    from tests.test_darkarchives_video import _silent_voice
    from trendclip import voiceover
    from trendclip.config import get_settings
    from trendclip.video_assembler import media_duration

    db = _bank(tmp_path)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(da_ranking, "write_ranking", lambda stories, client=None, n=None, today=None: _draft())
    story_id = da_ranking.process(db_path=db, out_dir=scripts)["story"]["id"]
    from modules import scene_images

    monkeypatch.setattr(voiceover, "synthesize", lambda text, out, voice, rate, **k: _silent_voice(text, out, voice, rate, seconds=9.0))
    monkeypatch.setenv("DA_TTS_ENGINE", "edge")
    monkeypatch.setattr(scene_images, "wikipedia_photos", lambda url, out_dir, **k: [])
    monkeypatch.setenv("DA_MUSIC", "none")
    monkeypatch.setenv("DA_IMAGE_BACKEND", "placeholder")
    monkeypatch.setenv("DA_MOTION", "off")
    monkeypatch.setattr(video_gen, "FPS", 12)
    info = video_gen.make_video(story_id, out_dir=tmp_path / "out", scripts_dir=scripts, db_path=db, settings=get_settings(), announce=False)
    video = tmp_path / "out" / f"story_{story_id}.mp4"
    assert video.is_file() and 9.5 <= media_duration(video) <= 12 and len(info["ranking"]) == 5
    ass = (tmp_path / "out" / f"story_{story_id}.ass").read_text()
    assert "Style: Panel," in ass and "{font}" not in ass and ",Panel,,0,0,0,,{\\pos(56,500)" in ass
    assert "5. CASE 5 FILES" in ass and "1. CASE 1 FILES" in ass and ",Flash," not in ass
    assert "5 CASES. NO\\NANSWERS" in ass and ",Title," in ass and "5 CASES THAT STILL" in ass
