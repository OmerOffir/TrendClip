from fastapi.testclient import TestClient

from trendclip.config import Settings
from trendclip.models import PipelineResult, RunStats
from trendclip.web import app as web


class FakeYouTube:
    def get_categories(self, region):
        return [{"id": "28", "title": "Science & Technology"}, {"id": "20", "title": "Gaming"}]


def _client(monkeypatch, calls):
    monkeypatch.setattr(web, "_base_settings", lambda: Settings(youtube_api_key="k"))
    monkeypatch.setattr(web, "make_youtube_client", lambda settings: FakeYouTube())
    monkeypatch.setattr(web, "_cache", web._TTLCache())

    def fake_run(settings):
        calls.append(settings)
        return PipelineResult(
            regions=settings.yt_regions,
            category_id=settings.yt_category_id,
            stats=RunStats(),
            candidates=[],
            outlier_videos=[],
        )

    monkeypatch.setattr(web, "run_pipeline", fake_run)
    return TestClient(web.app)


def test_categories_sorted(monkeypatch):
    client = _client(monkeypatch, [])
    body = client.get("/api/categories?region=us").json()
    assert body["region"] == "US"
    assert [c["title"] for c in body["categories"]] == ["Gaming", "Science & Technology"]


def test_trends_category_selection_and_cache(monkeypatch):
    calls = []
    client = _client(monkeypatch, calls)

    r = client.get("/api/trends?region=GB&category=28")
    assert r.status_code == 200 and r.headers["X-Cache"] == "MISS"
    assert r.json()["category_id"] == "28" and calls[-1].yt_regions == ["GB"]

    assert client.get("/api/trends?region=GB&category=28").headers["X-Cache"] == "HIT"
    assert client.get("/api/trends?region=GB&category=28&refresh=true").headers["X-Cache"] == "MISS"

    r = client.get("/api/trends?category=all")
    assert r.json()["category_id"] is None and calls[-1].yt_category_id is None
    assert len(calls) == 3


def test_trends_rejects_bad_input(monkeypatch):
    client = _client(monkeypatch, [])
    assert client.get("/api/trends?category=gaming").status_code == 400
    assert client.get("/api/trends?region=USA").status_code == 400


def test_index_served(monkeypatch):
    client = _client(monkeypatch, [])
    r = client.get("/")
    assert r.status_code == 200 and "TrendClipper" in r.text
    assert client.get("/static/app.js").status_code == 200
