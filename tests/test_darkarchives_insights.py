"""Dark Outliers insights: the channel's numbers, the lessons, the mix bias, the prompt, the endpoint."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from db import repository
from modules import da_channel, da_insights, da_produce, script_gen, video_gen
from trendclip import publish
from trendclip.config import get_settings

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeYouTube:
    def __init__(self, stats):
        self.stats = stats

    def videos(self):
        def list_(**kw):
            ids = kw["id"].split(",")
            return Call({"items": [{"id": i, "statistics": {"viewCount": str(self.stats[i][0]), "likeCount": str(self.stats[i][1]),
                                                             "commentCount": str(self.stats[i][2])}}
                                   for i in ids if i in self.stats]})
        return SimpleNamespace(list=list_)


class FakeAnalytics:
    """`rows`: video -> [views, avgDuration, avgPct, likes, comments, shares, subs]; `days`: video -> {day: views}."""

    def __init__(self, rows, days=None, fail=None):
        self.rows, self.days, self.fail, self.queries = rows, days or {}, fail, []

    def reports(self):
        def query(**kw):
            self.queries.append(kw)
            if self.fail:
                raise self.fail
            if kw["dimensions"] == "day":
                vid = kw["filters"].removeprefix("video==")
                per_day = {d: v for d, v in self.days.get(vid, {}).items() if kw["startDate"] <= d <= kw["endDate"]}
                return Call({"columnHeaders": [{"name": "day"}, {"name": "views"}], "rows": [[d, v] for d, v in sorted(per_day.items())]})
            wanted = kw["filters"].removeprefix("video==").split(",")
            return Call({"columnHeaders": [{"name": "video"}, *({"name": m} for m in da_insights.ANALYTICS_METRICS)],
                         "rows": [[vid, *self.rows[vid]] for vid in wanted if vid in self.rows]})
        return SimpleNamespace(query=query)


def _video(out, story_id, title, hook, uploaded_at, seconds=60.0, video_id=None, thumb=None, scripts=None):
    out.mkdir(exist_ok=True)
    (out / f"story_{story_id}.mp4").write_bytes(b"\x00" * 8)
    info = {"story_id": story_id, "title": title, "filename": f"story_{story_id}.mp4", "duration_seconds": seconds,
            "created_at": "2026-11-01T10:00:00", "beats": [{"index": 0, "label": "hook", "text": hook}]}
    if video_id:
        info["uploads"] = {"youtube": {"video_id": video_id, "url": f"https://youtube.com/shorts/{video_id}", "privacy": "public",
                                       "uploaded_at": uploaded_at}}
    (out / f"story_{story_id}.json").write_text(json.dumps(info))
    if scripts is not None and thumb is not None:
        scripts.mkdir(exist_ok=True)
        script = script_gen.Script(story_id=story_id, title=title, hook_text=hook, estimated_duration_seconds=45, total_word_count=100,
                                   scenes=[], thumbnail_text=thumb)
        (scripts / f"story_{story_id}.json").write_text(script.model_dump_json())


def _bank(tmp_path, pillars):
    db = tmp_path / "bank.db"
    repository.init_db(db)
    for i, pillar in enumerate(pillars, 1):
        repository.save_story(f"s{i}", "gemini", f"Story {i}", "text " * 50, "", 50, pillar=pillar, db_path=db)
        repository.set_status(i, "USED", db)
    return db


# six uploaded videos: 3 history (strong), 2 mystery, 1 outrage; two slots; hooks of every style
VIDEOS = [  # story, title, hook, uploaded (UTC), seconds, video_id, thumbnail
    (1, "The CIA Project Erased", "For two decades, civilians were test subjects.", "2026-11-01T12:30:00+00:00", 70, "v1", "THE FORBIDDEN EXPERIMENTS"),
    (2, "The Devil's Triangle", "Five bombers vanished without a trace.", "2026-11-02T21:00:00+00:00", 65, "v2", "FIVE BOMBERS VANISHED"),
    (3, "Kept in a Clock", "Why was she never buried?", "2026-11-03T12:30:00+00:00", 57, "v3", "KEPT IN A CLOCK"),
    (4, "The Willowbrook Experiments", "Parents agreed to infect their own children.", "2026-11-04T21:00:00+00:00", 64, "v4", "INFECTED ON PURPOSE?"),
    (5, "The Chicago Badge", "Chicago police hunted a child killer for sixty years.", "2026-11-05T12:30:00+00:00", 57, "v5", "HE WORE THE BADGE"),
    (6, "The Lost Colony", "One hundred settlers disappeared overnight.", "2026-11-15T21:00:00+00:00", 80, "v6", "GONE OVERNIGHT"),
]
PILLARS = ["history", "mystery", "history", "outrage", "mystery", "history"]
STATS = {"v1": (5000, 300, 40), "v2": (900, 50, 4), "v3": (3000, 150, 20), "v4": (400, 10, 1), "v5": (700, 30, 5), "v6": (2000, 80, 9)}
ROWS = {"v1": [5000, 52, 74, 300, 40, 12, 7], "v2": [900, 30, 46, 50, 4, 2, 0], "v3": [3000, 48, 84, 150, 20, 6, 3],
        "v4": [400, 25, 39, 10, 1, 0, 0], "v5": [700, 35, 61, 30, 5, 1, 0], "v6": [2000, 60, 75, 80, 9, 4, 2]}
DAYS = {"v1": {"2026-11-01": 2000, "2026-11-02": 1500, "2026-11-03": 500, "2026-11-04": 400, "2026-11-05": 300, "2026-11-06": 200, "2026-11-07": 100},
        "v6": {"2026-11-15": 1200, "2026-11-16": 600, "2026-11-17": 200}}


@pytest.fixture
def channel(tmp_path, monkeypatch):
    """Six uploaded videos in a temp output folder, their bank, their scripts and a token with the analytics scope."""
    out, scripts = tmp_path / "videos", tmp_path / "scripts"
    for story_id, title, hook, when, seconds, video_id, thumb in VIDEOS:
        _video(out, story_id, title, hook, when, seconds, video_id, thumb, scripts)
    _video(out, 7, "Not uploaded", "A hook.", "", 50)
    db = _bank(tmp_path, PILLARS)
    monkeypatch.setattr(video_gen, "OUTPUT_DIR", out)
    monkeypatch.setattr(script_gen, "OUTPUT_DIR", scripts)
    monkeypatch.setattr(repository, "DB_PATH", db)
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"scopes": [publish.ANALYTICS_SCOPE]}))
    monkeypatch.setattr(da_channel, "TOKEN", token)
    monkeypatch.delenv("DA_SCRIPT_LEARNING", raising=False)
    monkeypatch.delenv("DA_PILLAR_BIAS", raising=False)
    return SimpleNamespace(out=out, scripts=scripts, db=db, settings=get_settings())


def test_hook_styles_and_buckets():
    assert da_insights.hook_style("Why was she never buried?") == "question"
    assert da_insights.hook_style("Five bombers vanished without a trace.") == "number"
    assert da_insights.hook_style("For two decades, civilians were test subjects.") == "number"
    assert da_insights.hook_style("In 1945 a ship sailed alone.") == "number"
    assert da_insights.hook_style("Parents agreed to infect their own children.") == "statement"
    assert da_insights.hook_style("") == ""
    assert da_insights.thumbnail_bucket("KEPT IN A CLOCK") == "4+ words" and da_insights.thumbnail_bucket("HE WORE THE BADGE") == "4+ words"
    assert da_insights.thumbnail_bucket("GONE OVERNIGHT") == "1-2 words" and da_insights.thumbnail_bucket("FIVE BOMBERS VANISHED") == "3 words"
    assert da_insights.length_bucket(40) == "<45 s" and da_insights.length_bucket(60) == "45-60 s"
    assert da_insights.length_bucket(70) == "61-75 s" and da_insights.length_bucket(90) == ">75 s" and da_insights.length_bucket(None) == ""


def test_facts_read_the_story_files_the_bank_and_the_scripts(channel):
    facts = da_insights.facts(channel.settings, at=NOW)
    assert [f.story_id for f in facts] == [6, 5, 4, 3, 2, 1]  # newest upload first; story 7 is not on YouTube
    first = facts[-1]
    assert first.video_id == "v1" and first.pillar == "history" and first.hook_style == "number"
    assert first.thumbnail_text == "THE FORBIDDEN EXPERIMENTS" and first.thumbnail_bucket == "3 words"
    assert first.length_bucket == "61-75 s" and first.slot == "14:30" and facts[0].slot == "23:00"  # 12:30 / 21:00 UTC in Jerusalem
    assert first.age_days == pytest.approx(18.98, abs=0.01) and facts[0].pillar == "history"


def test_fetch_caches_the_numbers_and_the_first_days(channel):
    an = FakeAnalytics(ROWS, DAYS)
    data = da_insights.fetch(channel.settings, youtube=FakeYouTube(STATS), analytics=an, at=NOW)
    assert data["fetched_at"] == NOW.isoformat() and data["warnings"] == []
    by_id = {v["video_id"]: v for v in data["videos"]}
    assert by_id["v1"]["views"] == 5000 and by_id["v1"]["likes"] == 300 and by_id["v1"]["avg_view_pct"] == 74
    assert by_id["v1"]["avg_view_seconds"] == 52 and by_id["v1"]["shares"] == 12 and by_id["v1"]["subs_gained"] == 7
    assert by_id["v1"]["views_day1"] == 3500 and by_id["v1"]["views_day7"] == 5000  # old enough: the week is final
    assert by_id["v6"]["views_day1"] == 1800 and by_id["v6"]["views_day7"] is None  # five days old: not yet
    assert by_id["v2"]["views_day1"] == 0  # no day rows: nothing watched on those days
    assert da_insights.load() == data and da_insights.cache_path().is_file()
    assert an.queries[0]["dimensions"] == "video" and an.queries[0]["startDate"] == "2026-10-31"
    assert sum(q["dimensions"] == "day" for q in an.queries) == 6

    # a second read keeps the first days that were final (no new day query for v1) and asks again for v6
    an2 = FakeAnalytics(ROWS, DAYS)
    again = da_insights.fetch(channel.settings, youtube=FakeYouTube(STATS), analytics=an2, at=NOW + timedelta(hours=5), previous=data)
    asked = [q["filters"] for q in an2.queries if q["dimensions"] == "day"]
    assert "video==v1" not in asked and "video==v6" in asked
    assert {v["video_id"]: v["views_day7"] for v in again["videos"]}["v1"] == 5000


def test_refresh_uses_the_cache_for_a_few_hours_and_keeps_it_when_youtube_fails(channel, monkeypatch):
    calls = []

    def fake_fetch(settings, out_dir=None, at=None, previous=None, **kw):
        calls.append(at)
        if len(calls) > 2:
            raise RuntimeError("quota")
        data = {"fetched_at": at.isoformat(), "videos": [{"story_id": 1, "video_id": "v1", "views": 10 * len(calls)}], "warnings": []}
        da_insights._save(data, out_dir)
        return data

    monkeypatch.setattr(da_insights, "fetch", fake_fetch)
    first = da_insights.refresh(channel.settings, at=NOW)
    assert da_insights.refresh(channel.settings, at=NOW + timedelta(hours=3)) == first and len(calls) == 1
    assert da_insights.refresh(channel.settings, at=NOW + timedelta(hours=5))["videos"][0]["views"] == 20  # older than 4 h
    monkeypatch.setenv("DA_INSIGHTS_HOURS", "1")
    assert da_insights.cache_hours() == 1.0
    kept = da_insights.refresh(channel.settings, at=NOW + timedelta(hours=7))  # the read fails: the old numbers stay
    assert kept["videos"][0]["views"] == 20 and kept["warnings"] == ["Not refreshed: quota"]
    assert da_insights.refresh(channel.settings, at=NOW + timedelta(hours=7, minutes=10))["videos"][0]["views"] == 20 and len(calls) == 4
    da_insights.cache_path().unlink()
    with pytest.raises(da_insights.InsightsError, match="quota"):
        da_insights.refresh(channel.settings, at=NOW)


def test_fetch_without_the_analytics_scope_or_login_explains(channel, monkeypatch):
    da_channel.TOKEN.write_text(json.dumps({"scopes": [publish.SCOPES[0]]}))
    data = da_insights.fetch(channel.settings, youtube=FakeYouTube(STATS), at=NOW)
    assert data["videos"][0]["views"] == 2000 and data["videos"][0]["avg_view_pct"] is None
    assert "analytics permission" in data["warnings"][0]
    err = RuntimeError("accessNotConfigured: YouTube Analytics API has not been used")
    data = da_insights.fetch(channel.settings, youtube=FakeYouTube(STATS), analytics=FakeAnalytics({}, fail=err), at=NOW)
    assert data["videos"][0]["views"] == 2000 and "not enabled" in data["warnings"][0]
    da_channel.TOKEN.unlink()
    with pytest.raises(da_insights.InsightsError, match="Connect YouTube"):
        da_insights.fetch(channel.settings, at=NOW)


def test_report_and_lessons(channel):
    data = da_insights.fetch(channel.settings, youtube=FakeYouTube(STATS), analytics=FakeAnalytics(ROWS, DAYS), at=NOW)
    rep = da_insights.report(data)
    assert rep["videos"] == 6 and rep["measured"] == 6 and rep["with_retention"] == 6 and rep["learning"]
    assert [v["title"] for v in rep["top"][:3]] == ["The CIA Project Erased", "Kept in a Clock", "The Lost Colony"]
    assert [v["title"] for v in rep["worst"]] == ["The Willowbrook Experiments", "The Chicago Badge", "The Devil's Triangle"]
    assert rep["retention_best"][0]["title"] == "Kept in a Clock" and rep["totals"]["views"] == 12000 and rep["views_median"] == 1450
    pillars = rep["dimensions"]["pillar"]["groups"]
    assert pillars[0]["key"] == "history" and pillars[0]["n"] == 3 and pillars[0]["views_median"] == 3000 and pillars[0]["avg_view_pct"] == 77.7
    assert pillars[-1]["key"] == "outrage" and pillars[0]["vs_channel"] == 2.07
    slots = {g["key"]: g["views_median"] for g in rep["dimensions"]["slot"]["groups"]}
    assert slots == {"14:30": 3000, "23:00": 900}

    text = rep["lessons"]
    lines = text.splitlines()
    assert lines[0] == "WHAT WORKED ON DARK OUTLIERS (YouTube, 6 videos measured, 6 with retention):" and len(lines) <= 12
    assert "- Pillar: Creepy history gets 3.0k views median over 3 videos (78% watched); Unresolved mysteries only 800." in lines
    assert "- Hooks: Number-led hooks get 2.0k views median over 3 videos (65% watched); Statement hooks only 550." in lines
    assert "- Upload slot: the 14:30 slot gets 3.0k views median over 3 videos (73% watched); the 23:00 slot only 900." in lines
    assert "- Best titles: “The CIA Project Erased” (5.0k views, 74% watched); “Kept in a Clock” (3.0k views, 84% watched); " \
           "“The Lost Colony” (2.0k views, 75% watched)" in lines
    assert "- Weakest titles: “The Willowbrook Experiments” (400 views, 39% watched); “The Chicago Badge” (700 views, 61% watched)" in lines
    assert "- The hook that held viewers best: “Why was she never buried?” (84% watched)" in lines
    assert lines[-1].startswith("Use these as evidence")
    assert rep["best_lesson"].startswith("Pillar: Creepy history gets 3.0k views median")
    assert rep["pillar_bias"] == {"history": 0.15, "mystery": -0.09}  # history capped at +15%; one outrage video is too few

    # too few videos: no lessons, a plain line for the morning, no bias
    few = {**data, "videos": data["videos"][:3]}
    assert da_insights.lessons(few) == "" and da_insights.pillar_bias(few) == {}
    assert da_insights.best_lesson(few) == "3 videos measured (lessons start at 5); best so far: “The Lost Colony” with 2.0k views, 75% watched"
    assert da_insights.best_lesson({"videos": []}) == "" and not da_insights.report({})["learning"]
    text = "\n".join(da_insights.format_report(rep))
    assert text.startswith("📈 **Dark Outliers insights** · 6 of 6 videos measured · 6 with retention · 12k views in all")
    assert "• **The CIA Project Erased** · 5.0k views · 74% watched · 3.5k on day 1 · history" in text and "- Pillar:" in text
    assert da_insights.format_report(da_insights.report({}))[0].startswith("📈 No Dark Outliers video")


def test_pillar_bias_moves_the_mix_within_its_cap(channel, monkeypatch):
    data = da_insights.fetch(channel.settings, youtube=FakeYouTube(STATS), analytics=FakeAnalytics(ROWS), at=NOW)
    assert da_insights.pillar_bias(data, cap=0.05) == {"history": 0.05, "mystery": -0.05}
    targets = da_insights.biased_targets({"history": 0.15, "mystery": -0.09})
    assert targets == {"mystery": 0.481, "history": 0.425, "outrage": 0.094} and sum(targets.values()) == pytest.approx(1.0)
    assert da_insights.biased_targets({"outrage": -0.5})["outrage"] == pytest.approx(0.05 / 0.95, abs=0.001)  # never under 5%
    assert da_insights.biased_targets({})["mystery"] == 0.6
    monkeypatch.setenv("DA_PILLAR_BIAS", "0")
    assert da_insights.pillar_bias(data) == {} and da_produce.mix_targets() is None
    monkeypatch.setenv("DA_PILLAR_BIAS", "0.1")
    assert da_produce.mix_targets() == da_insights.biased_targets({"history": 0.1, "mystery": -0.09}) == {"mystery": 0.505, "history": 0.396, "outrage": 0.099}

    # the bank follows the biased shares: with 3 history, 2 mystery, 1 outrage made, plain targets want mystery next
    for i, pillar in enumerate(("mystery", "history", "outrage"), 20):
        repository.save_story(f"new{i}", "gemini", f"New {pillar}", "text " * 50, "", 90, pillar=pillar, db_path=channel.db)
    assert repository.next_pillar(channel.db) == "mystery"
    shifted = {"mystery": 0.3, "history": 0.6, "outrage": 0.1}
    assert repository.next_pillar(channel.db, shifted) == "history"
    assert repository.get_next_story(channel.db, shifted)["title"] == "New history"


def test_prompts_end_with_the_lessons_once_there_are_enough_videos(channel, monkeypatch):
    story = {"source_name": "reddit/ColdCases", "title": "T", "raw_text": "x", "pillar": "history"}
    assert "WHAT WORKED" not in script_gen.build_prompt(story)  # nothing measured yet (and no network: no cache, nothing on YouTube)
    da_insights.fetch(channel.settings, youtube=FakeYouTube(STATS), analytics=FakeAnalytics(ROWS), at=NOW)
    monkeypatch.setattr(da_insights, "fetch", lambda *a, **k: pytest.fail("the prompt must use the cache"))
    prompt = script_gen.build_prompt(story)
    assert prompt.endswith("avoid what lost viewers.") and "\n\nWHAT WORKED ON DARK OUTLIERS" in prompt and prompt.startswith("You write scripts")
    assert script_gen.build_prompt(story, brief="") == prompt.split("\n\nWHAT WORKED")[0]
    monkeypatch.setenv("DA_SCRIPT_LEARNING", "0")
    assert "WHAT WORKED" not in script_gen.build_prompt(story)
    monkeypatch.delenv("DA_SCRIPT_LEARNING")
    cached = da_insights.load()
    da_insights._save({**cached, "videos": cached["videos"][:4]})
    assert "WHAT WORKED" not in script_gen.build_prompt(story)  # four videos: too few

    def boom(*a, **k):
        raise RuntimeError("disk")

    monkeypatch.setattr(da_insights, "refresh", boom)
    assert script_gen.build_prompt(story).startswith("You write scripts")  # a broken cache never stops a script


def test_insights_endpoint(channel, monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    client = TestClient(web.app)
    token = da_channel.TOKEN.read_text()
    da_channel.TOKEN.unlink()
    empty = client.get("/api/darkarchives/insights").json()
    assert "error" in empty and empty["videos"] == 0 and "Connect YouTube" in empty["error"]
    da_channel.TOKEN.write_text(token)
    da_insights.fetch(channel.settings, youtube=FakeYouTube(STATS), analytics=FakeAnalytics(ROWS, DAYS), at=NOW)
    seen = []
    monkeypatch.setattr(da_insights, "fetch", lambda settings, out_dir=None, at=None, previous=None, **kw: seen.append(1) or previous)
    rep = client.get("/api/darkarchives/insights").json()
    assert rep["videos"] == 6 and rep["top"][0]["title"] == "The CIA Project Erased" and rep["lessons"].startswith("WHAT WORKED")
    assert rep["pillar_bias"]["history"] == 0.15 and rep["dimensions"]["hook_style"]["label"] == "Hook style" and "error" not in rep
    assert client.get("/api/darkarchives/insights?refresh=true").json()["videos"] == 6 and seen == [1]
