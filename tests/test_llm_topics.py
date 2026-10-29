"""New topics from your own LLM: the channel's Gemini prompt copied into any LLM, its pasted answer added to the
Dark Outliers story bank (modules/fetcher.py) or the Borderline Why topic list (modules/bw_topics.py)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from db import repository
from modules import bw_channel, bw_topics, fetcher

TODAY = dt.date(2026, 10, 29)
CASES = {"cases": [
    {"title": "Flight 19", "pillar": "mystery", "story": "Five bombers vanished in 1945. " * 10, "hook_score": 90},
    {"title": "Mary Celeste", "pillar": "mystery", "story": "Anniversary: November 4, 1872. A ship was found. " * 5, "hook_score": 60},
    {"title": "Dyatlov Pass incident", "pillar": "mystery", "story": "Nine hikers died. " * 10, "hook_score": 95},
]}
FAME = {"Flight 19": {"views": 40000, "url": "https://en.wikipedia.org/wiki/Flight_19", "pageid": 1}}


@pytest.fixture
def bank(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, "DB_PATH", tmp_path / "bank.db")
    repository.init_db()
    repository.save_story("wikipedia_9", "wikipedia/Disappearances", "Dyatlov Pass incident", "x" * 300, "", 80)
    return tmp_path


def test_dark_outliers_prompt_and_paste(bank):
    prompt = fetcher.llm_prompt(8, today=TODAY)
    assert "Give 8" in prompt and "Today is 2026-10-29" in prompt and "- Dyatlov Pass incident" in prompt
    assert prompt.endswith(fetcher.LLM_FORMAT)

    with pytest.raises(fetcher.LLMTopicsError, match="no JSON"):
        fetcher.llm_add("Sure, here are some cases!")
    with pytest.raises(fetcher.LLMTopicsError, match="no cases"):
        fetcher.llm_add('{"cases": []}')

    out = fetcher.llm_add("```json\n" + json.dumps(CASES) + "\n```", today=TODAY, fame=lambda titles: FAME)
    assert out == {"added": ["Flight 19", "Mary Celeste"], "skipped": 1}  # Dyatlov is in the bank already
    rows = {s["title"]: s for s in repository.all_stories()}
    assert rows["Flight 19"]["source_id"] == "llm_flight_19" and rows["Flight 19"]["score"] == 92
    assert rows["Flight 19"]["url"] == FAME["Flight 19"]["url"] and rows["Mary Celeste"]["score"] == fetcher.ANNIVERSARY_SCORE
    assert repository.source_counts()["llm"] == {"NEW": 2}
    assert fetcher.llm_add(json.dumps(CASES), today=TODAY, fame=lambda titles: {}) == {"added": [], "skipped": 3}


def test_borderline_why_prompt_and_paste(tmp_path):
    bw_topics.mark(tmp_path, "Toledo War", used=True)
    prompt = bw_topics.llm_prompt(tmp_path)
    assert "Propose 20 new questions" in prompt and "Toledo War" in prompt and "Llívia" in prompt and '"topics"' in prompt

    answer = {"topics": [
        {"question": "Why is Alaska's capital unreachable by road?", "article": "Juneau"},
        {"question": "Why does Llívia sit inside France?", "article": "Llívia"},  # a seed already
        {"question": "Why is there a fake country?", "article": "Nowhere Land"},  # no article
        {"question": "Why does Point Nemo exist?", "article": "point nemo"},
    ]}
    titles = {"Juneau": "Juneau, Alaska", "point nemo": "Pole of inaccessibility"}
    out = bw_topics.llm_add(tmp_path, json.dumps(answer), check=titles.get)
    assert out == {"added": ["Why is Alaska's capital unreachable by road?", "Why does Point Nemo exist?"], "skipped": 2}
    left = bw_topics.remaining(tmp_path)
    assert [(t.article, t.source) for t in left[-2:]] == [("Juneau, Alaska", "llm"), ("Pole of inaccessibility", "llm")]
    assert bw_topics.llm_add(tmp_path, json.dumps(answer), check=titles.get)["added"] == []  # never twice
    with pytest.raises(bw_topics.LLMTopicsError, match="no topics"):
        bw_topics.llm_add(tmp_path, '{"topics": []}')


def test_endpoints(bank, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    monkeypatch.setattr(fetcher, "wiki_fame", lambda titles, session=None: FAME)
    monkeypatch.setattr(bw_channel, "out_root", lambda: tmp_path / "bw")
    monkeypatch.setattr("modules.bw_script.article_exists", lambda article: article)
    client = TestClient(web.app)

    assert "Give 3" in client.get("/api/darkarchives/llm-topics?count=3").json()["prompt"]
    assert client.get("/api/darkarchives/llm-topics?count=50").status_code == 422
    assert client.post("/api/darkarchives/llm-topics", json={"text": "no json"}).status_code == 400
    assert client.post("/api/darkarchives/llm-topics", json={"text": json.dumps(CASES)}).json()["added"] == ["Flight 19", "Mary Celeste"]
    assert [s["title"] for s in client.get("/api/darkarchives/stories?source=llm").json()["stories"]] == ["Mary Celeste", "Flight 19"]

    assert "Propose 20" in client.get("/api/borderlinewhy/llm-topics").json()["prompt"]
    r = client.post("/api/borderlinewhy/llm-topics", json={"text": '{"topics": [{"question": "Why is Juneau cut off?", "article": "Juneau"}]}'})
    assert r.json() == {"added": ["Why is Juneau cut off?"], "skipped": 0}
    assert client.post("/api/borderlinewhy/llm-topics", json={"text": "{}"}).status_code == 400
