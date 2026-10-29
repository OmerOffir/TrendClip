"""The Gemini free-tier ledger: closed models are skipped, the retry time travels with the error, a half-written
Borderline Why draft is carried on, and the optional jobs step aside when the budget is low."""

from datetime import datetime, timedelta, timezone

import pytest
from google.genai import errors

from modules import bw_script
from trendclip import gemini_quota, script_writer

NOON = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)  # 03:00 in California: the reset is at 07:00 UTC


def quota_429(model: str, retry: str = "2817s") -> errors.ClientError:
    return errors.ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": (
        "You exceeded your current quota. * Quota exceeded for metric: generate_content_free_tier_requests, limit: 20, "
        f"model: {model}, description: Generate content requests per day limit exceeded on Free Tier. Please retry in {retry}.")}})


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "gemini_quota.json"
    monkeypatch.setattr(gemini_quota, "LEDGER", path)
    monkeypatch.setattr(gemini_quota, "_setting", lambda name, default: default)
    return gemini_quota.Ledger(path)


def test_the_quota_day_and_reset_follow_california(ledger):
    assert gemini_quota.quota_day(NOON) == "2026-10-29"
    assert gemini_quota.next_reset(NOON) == datetime(2026, 10, 30, 7, 0, tzinfo=timezone.utc)
    late = datetime(2026, 10, 29, 6, 30, tzinfo=timezone.utc)  # 23:30 the day before in California
    assert gemini_quota.quota_day(late) == "2026-10-28"
    assert gemini_quota.next_reset(late) == datetime(2026, 10, 29, 7, 0, tzinfo=timezone.utc)


def test_the_retry_wait_is_read_from_googles_answer():
    assert gemini_quota.retry_seconds(quota_429("gemini-2.5-flash")) == 2817
    assert gemini_quota.retry_seconds(RuntimeError("no hint")) is None
    assert gemini_quota.is_daily(quota_429("m"))
    assert not gemini_quota.is_daily(RuntimeError("Quota exceeded for metric: requests per minute"))


def test_a_429_closes_the_model_until_the_time_google_gave_and_a_success_counts(ledger):
    until = ledger.record_closed("gemini-3.8-flash", quota_429("gemini-3.8-flash"), now=NOON)
    assert until == NOON + timedelta(seconds=2817)
    assert ledger.closed_until("gemini-3.8-flash", NOON) == until
    assert ledger.closed_until("gemini-3.8-flash", until + timedelta(seconds=1)) is None
    assert ledger.used_today("gemini-3.8-flash", NOON) == 20  # the day's budget is spent
    # no hint from Google: closed until the next reset
    assert ledger.record_closed("gemini-2.5-flash", None, now=NOON) == gemini_quota.next_reset(NOON)
    ledger.record_success("gemini-3.7-flash", now=NOON)
    ledger.record_success("gemini-3.7-flash", now=NOON)
    assert ledger.used_today("gemini-3.7-flash", NOON) == 2
    assert ledger.used_today("gemini-3.7-flash", NOON + timedelta(days=1)) == 0  # a new day
    fresh = gemini_quota.Ledger(ledger.path)  # another process reads the same file
    assert fresh.open_models(["gemini-3.8-flash", "gemini-2.5-flash", "gemini-3.7-flash"], NOON) == ["gemini-3.7-flash"]


def test_the_budget_counts_only_models_known_to_work(ledger):
    chain = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-2.5-flash"]
    ledger.record_success("gemini-3.8-flash", now=NOON)
    info = ledger.budget(chain, NOON)
    assert info["models"] == ["gemini-3.8-flash"] and info["used"] == 1 and info["left"] == 19
    ledger.record_success("gemini-2.5-flash", now=NOON)
    info = ledger.budget(chain, NOON)
    assert info["models"] == ["gemini-3.8-flash", "gemini-2.5-flash"] and info["left"] == 38
    ledger.record_closed("gemini-3.8-flash", quota_429("gemini-3.8-flash"), now=NOON)
    info = ledger.budget(chain, NOON)
    assert info["left"] == 19 and info["closed"] == ["gemini-3.8-flash"]
    assert not gemini_quota.low(chain, now=NOON)
    for _ in range(14):
        ledger.record_success("gemini-2.5-flash", now=NOON)
    assert gemini_quota.low(chain, now=NOON)  # 5 left, under the reserve of 6


class FakeModels:
    def __init__(self, answers: dict[str, object]):
        self.answers = answers
        self.asked: list[str] = []

    def generate_content(self, model, contents, config):
        self.asked.append(model)
        answer = self.answers.get(model, RuntimeError(f"unexpected model {model}"))
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeClient:
    def __init__(self, answers):
        self.models = FakeModels(answers)


def test_a_closed_model_is_skipped_and_a_429_closes_it_for_the_next_caller(ledger, monkeypatch):
    monkeypatch.setattr(script_writer, "FALLBACK_MODELS", ["gemini-3.7-flash", "gemini-2.5-flash"])
    monkeypatch.setattr(script_writer, "RETRY_DELAYS", (0, 0, 0))
    client = FakeClient({"gemini-3.8-flash": quota_429("gemini-3.8-flash"), "gemini-3.7-flash": quota_429("gemini-3.7-flash"),
                         "gemini-2.5-flash": "the answer"})
    response, used = script_writer._generate_with_fallback(client, "gemini-3.8-flash", ["hi"], None, lambda f, m: None, errors.APIError)
    assert (response, used) == ("the answer", "gemini-2.5-flash")
    assert client.models.asked == ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-2.5-flash"]
    # the next caller does not ask the two closed models at all
    client.models.asked.clear()
    script_writer._generate_with_fallback(client, "gemini-3.8-flash", ["hi"], None, lambda f, m: None, errors.APIError)
    assert client.models.asked == ["gemini-2.5-flash"]
    assert gemini_quota.Ledger(ledger.path).used_today("gemini-2.5-flash") == 2


def test_when_every_model_is_closed_no_request_is_made_and_the_error_says_when(ledger, monkeypatch):
    monkeypatch.setattr(script_writer, "FALLBACK_MODELS", ["gemini-2.5-flash"])
    ledger.record_closed("gemini-3.8-flash", quota_429("gemini-3.8-flash", "3600s"))
    ledger.record_closed("gemini-2.5-flash", quota_429("gemini-2.5-flash", "7200s"))
    client = FakeClient({})
    with pytest.raises(errors.APIError) as caught:
        script_writer._generate_with_fallback(client, "gemini-3.8-flash", ["hi"], None, lambda f, m: None, errors.APIError)
    assert client.models.asked == []
    assert caught.value.code == 429
    explained = script_writer._explain(caught.value, "gemini-3.8-flash")
    assert isinstance(explained, script_writer.QuotaError)
    opens = gemini_quota.Ledger(ledger.path).closed_until("gemini-3.8-flash")
    assert abs((explained.retry_at - opens).total_seconds()) < 5  # the first model to open again
    assert "used up until" in str(explained) and "tries again" in str(explained)


def test_a_rate_limit_without_a_closed_model_asks_for_ten_minutes(ledger):
    err = script_writer.quota_error("gemini-3.8-flash")
    assert "10 minutes" in str(err) and "quota" in str(err)
    assert timedelta(minutes=9) < err.retry_at - gemini_quota._now() <= timedelta(minutes=10)


def test_borderline_why_carries_the_quota_time_and_resumes_the_kept_draft(tmp_path, monkeypatch, ledger):
    from tests.test_borderlinewhy import _draft, _research

    monkeypatch.setattr(bw_script, "gemini", lambda client=None: (None, "m"))
    rules = [["the narration is 188 words"]] * 2  # flagged on both tries: the fix is still owed on the second
    monkeypatch.setattr(bw_script, "check", lambda draft, res: rules.pop(0) if rules else [])
    monkeypatch.setattr(bw_script, "verify", lambda client, model, draft, res: [])
    asked = []

    def out_of_quota(client, model, prompt, schema, temperature=0.7):
        asked.append(prompt)
        if len(asked) == 2:  # the draft came, the rule fix hits the quota
            later = bw_script.TryLater("Gemini's free quota is used up until 09:00")
            later.retry_at = NOON
            raise later
        return _draft()

    monkeypatch.setattr(bw_script, "_ask", out_of_quota)
    drafts = tmp_path / "drafts"
    with pytest.raises(bw_script.TryLater) as caught:
        bw_script.write(_research(), today="2026-10-29", drafts_dir=drafts)
    assert caught.value.retry_at == NOON
    kept = list(drafts.glob("*.json"))
    assert len(kept) == 1 and "fixes" in kept[0].read_text()

    steps = []
    script = bw_script.write(_research(), today="2026-10-29", progress=steps.append, drafts_dir=drafts)
    assert script["title"] == "Why does Minnesota have a notch?"
    assert len(asked) == 3  # the draft, the failed fix, the fix on the second try: never a second draft
    assert "Carrying on with the draft kept from the last try" in steps
    assert list(drafts.glob("*.json")) == []  # done: nothing kept


def test_the_optional_jobs_step_aside_when_the_budget_is_low(ledger, monkeypatch):
    from modules import fetcher
    from trendclip import leaderboard

    monkeypatch.setattr(gemini_quota, "low", lambda *a, **k: True)
    monkeypatch.setattr(fetcher, "fetch_gemini", lambda *a, **k: pytest.fail("Gemini must not be asked"))
    monkeypatch.setattr(fetcher.repository, "init_db", lambda db_path=None: None)
    report = fetcher.run(("gemini",), db_path=None)
    assert "skipped" in report["gemini"]

    class Comment:
        id, video_id, text = "c1", "v", "hi"

    class Board:
        comments = [Comment()]
        verdicts: dict = {}
        videos = {"v": None}

    monkeypatch.setattr(script_writer, "gemini_api_key", lambda settings=None: "key")
    assert leaderboard.verify(None, Board()) == 0
