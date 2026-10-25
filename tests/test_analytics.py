from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trendclip import analytics, publish, shorts
from trendclip.config import Settings
from trendclip.web import app as web

from tests.test_upload import add_short

NOW = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output")


def uploaded(settings, name, video_id, days_ago, script="I accidentally committed a crime at my office. Then it got worse.",
             game="GTA V / Online", title="The Day My Cat Outsmarted the Block", **kw):
    """A Short uploaded to YouTube `days_ago` days before NOW."""
    when = (NOW - timedelta(days=days_ago)).isoformat()
    settings.shorts_dir.mkdir(parents=True, exist_ok=True)
    (settings.shorts_dir / name).write_bytes(b"\x00" * 2048)
    short = shorts.ShortVideo(
        filename=name, game=game, title=title, description="d", hashtags=["#shorts"], script=script, voice="v",
        background="bg.mp4", uploads={"youtube": {"video_id": video_id, "uploaded_at": when, "scheduled_for": None}}, **kw)
    (settings.shorts_dir / name).with_suffix(".json").write_text(short.model_dump_json())
    return short




class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeYouTube:
    """channels / playlistItems (the uploads list) and videos.list statistics."""

    def __init__(self, stats, uploads=()):
        self.stats = stats
        self.uploads = list(uploads)

    def channels(self):
        return SimpleNamespace(list=lambda **kw: Call({"items": [{
            "id": "UCme", "contentDetails": {"relatedPlaylists": {"uploads": "UUme"}}}]}))

    def playlistItems(self):
        return SimpleNamespace(list=lambda **kw: Call({"items": [
            {"contentDetails": {"videoId": vid, "videoPublishedAt": when}, "snippet": {"title": title}}
            for vid, title, when in self.uploads]}))

    def videos(self):
        def list_(**kw):
            ids = kw["id"].split(",")
            return Call({"items": [{"id": i, "statistics": {"viewCount": str(self.stats[i][0]), "likeCount": str(self.stats[i][1]),
                                                             "commentCount": str(self.stats[i][2])}}
                                   for i in ids if i in self.stats]})
        return SimpleNamespace(list=list_)


class FakeAnalytics:
    def __init__(self, rows, fail=None):
        self.rows, self.fail, self.queries = rows, fail, []

    def reports(self):
        def query(**kw):
            self.queries.append(kw)
            if self.fail:
                raise self.fail
            wanted = kw["filters"].removeprefix("video==").split(",")
            return Call({"columnHeaders": [{"name": "video"}, *({"name": m} for m in analytics.ANALYTICS_METRICS)],
                         "rows": [[vid, *self.rows[vid]] for vid in wanted if vid in self.rows]})
        return SimpleNamespace(query=query)


# --------------------------------------------------------------------------- what a video is made of


def test_hook_is_the_first_sentence_and_its_style_is_recognised():
    assert analytics.hook_of("  I accidentally committed a crime.  Then it got worse! ") == "I accidentally committed a crime."
    assert analytics.hook_of("") == ""
    styles = {
        "ONLY 5% OF PEOPLE FINISH THIS.": "dare",
        "Most people get this wrong.": "dare",
        "Which country has the most islands?": "question",
        "3 seconds. That's all it took.": "number",
        "\"You shall not pass,\" he said.": "quote",
        "Name this movie in five seconds.": "command",
        "I accidentally committed a crime at my office.": "first_person",
        "My boss fired the wrong guy.": "first_person",
        "The elevator stopped between floors.": "claim",
    }
    for hook, style in styles.items():
        assert analytics.hook_style(hook) == style, hook
    assert analytics.hook_style("") == ""


def test_length_buckets_and_slots(tmp_path):
    s = make_settings(tmp_path)
    assert [analytics.length_bucket(x) for x in (None, 14, 20, 30, 44, 60, 90, 200)] == \
        ["", "≤15 s", "16-22 s", "23-30 s", "31-45 s", "46-60 s", "1-2 min", ">2 min"]
    at_1430 = datetime(2026, 10, 7, 14, 40, tzinfo=s.tz)
    assert analytics.slot_label(s, at_1430) == "14:30"
    assert analytics.slot_label(s, datetime(2026, 10, 7, 22, 50, tzinfo=s.tz)) == "23:00"
    assert analytics.slot_label(s, datetime(2026, 10, 7, 17, 5, tzinfo=s.tz)) == "17:00"
    assert analytics.slot_label(s, None) == ""


def test_mode_is_recorded_or_read_from_what_was_rendered(tmp_path):
    s = make_settings(tmp_path)
    short = shorts.get_short(s, add_short(s, "a.mp4"))
    assert analytics.mode_of(short) == "story" and analytics.format_of(short) == "short"
    assert analytics.mode_of(short.model_copy(update={"mode": "riddle"})) == "riddle"
    math = short.model_copy(update={"answer": "84", "render_settings": {"flashes": [{"text": "+18"}, {"text": "÷ HALF"}]}})
    assert analytics.mode_of(math) == "math"
    riddle = short.model_copy(update={"answer": "a shadow", "render_settings": {"flashes": [{"text": "CLUE 1"}]}})
    assert analytics.mode_of(riddle) == "riddle"
    trivia = short.model_copy(update={"answer": "Sweden", "title": "90% get this trivia trap wrong",
                                      "render_settings": {"flashes": [{"text": "A) Sweden"}, {"text": "B) Chile"}]}})
    assert analytics.mode_of(trivia) == "trivia"
    movie = short.model_copy(update={"answer": "B", "title": "What did Vader ACTUALLY say?",
                                     "render_settings": {"flashes": [{"text": "A"}, {"text": "B"}, {"text": "C"}]}})
    assert analytics.mode_of(movie) == "movie"
    assert analytics.format_of(short.model_copy(update={"aspect": "landscape"})) == "long"
    assert analytics.format_of(short.model_copy(update={"series_id": "ab12cd", "part": 1})) == "multi"
    assert analytics.mode_from_title("Trivia trap: which planet?") == "trivia"
    assert analytics.mode_from_title("My cat ate my homework") is None


def test_render_records_the_mode(tmp_path):
    req = shorts.RenderRequest(clip="c.mp4", game="GTA", script="Hello there.", mode="clip")
    assert req.mode == "clip"
    with pytest.raises(ValueError):
        shorts.RenderRequest(clip="c.mp4", game="GTA", script="Hello there.", mode="poetry")


def test_facts_from_a_short_that_was_not_uploaded_is_none(tmp_path):
    s = make_settings(tmp_path)
    assert analytics.facts_from_short(s, shorts.get_short(s, add_short(s, "a.mp4"))) is None
    short = uploaded(s, "b.mp4", "vid_b", 3, duration_seconds=21.4, music_mood="funny_quirky")
    facts = analytics.facts_from_short(s, short)
    assert facts.video_id == "vid_b" and facts.hook_style == "first_person" and facts.length_bucket == "16-22 s"
    assert facts.music_mood == "funny_quirky" and facts.made_here and facts.published_at == NOW - timedelta(days=3)


# --------------------------------------------------------------------------- collecting


@pytest.fixture
def analytics_scope(monkeypatch):
    monkeypatch.setattr(publish, "has_scope", lambda scope: True)


def test_collect_stores_facts_and_todays_snapshot(tmp_path, analytics_scope):
    s = make_settings(tmp_path)
    uploaded(s, "a.mp4", "vid_a", 10, game="Minecraft", duration_seconds=20)
    uploaded(s, "b.mp4", "vid_b", 5, game="GTA V / Online", duration_seconds=40,
             script="Which country has the most islands? Is it Sweden?")
    yt = FakeYouTube({"vid_a": (5000, 300, 40), "vid_b": (800, 20, 3), "vid_x": (12000, 500, 90)},
                     uploads=[("vid_a", "A on YouTube", (NOW - timedelta(days=10, hours=2)).isoformat()),
                              ("vid_x", "Trivia trap: made by hand", (NOW - timedelta(days=20)).isoformat()),
                              ("vid_old", "Ancient", (NOW - timedelta(days=500)).isoformat())])
    an = FakeAnalytics({"vid_a": [5100, 1400, 16.5, 82.5, 300, 40, 12, 7], "vid_x": [12000, 3000, 15, 70, 500, 90, 30, 20]})

    result = analytics.collect(s, youtube=yt, analytics=an, at=NOW)
    assert (result.videos, result.from_sidecars, result.from_channel) == (3, 2, 1)  # the 500-day-old upload is skipped
    assert result.snapshots == 3 and result.with_retention == 2 and result.warnings == []
    assert an.queries[0]["ids"] == "channel==MINE" and an.queries[0]["dimensions"] == "video"
    assert set(an.queries[0]["filters"].removeprefix("video==").split(",")) == {"vid_a", "vid_b", "vid_x"}

    videos = {v.video_id: v for v in analytics.load_videos(s)}
    assert videos["vid_a"].title == "The Day My Cat Outsmarted the Block"  # the sidecar's facts win over the upload list
    assert videos["vid_a"].published_at == NOW - timedelta(days=10, hours=2)  # ...but the channel's publish time counts
    assert videos["vid_x"].made_here is False and videos["vid_x"].mode == "trivia" and videos["vid_x"].format == "other"
    snaps = analytics.load_snapshots(s)
    a = snaps["vid_a"][-1]
    assert a.day == NOW.date() and a.views == 5000 and a.likes == 300 and a.avg_view_pct == 82.5 and a.shares == 12
    assert a.source == "analytics" and snaps["vid_b"][-1].source == "data" and snaps["vid_b"][-1].avg_view_pct is None

    # A second read the same day replaces the snapshot instead of adding one.
    yt.stats["vid_a"] = (5500, 310, 41)
    analytics.collect(s, youtube=yt, analytics=an, at=NOW + timedelta(hours=3))
    assert [x.views for x in analytics.load_snapshots(s)["vid_a"]] == [5500]


def test_collect_without_the_analytics_scope_keeps_the_data_api_numbers(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    monkeypatch.setattr(publish, "has_scope", lambda scope: False)
    uploaded(s, "a.mp4", "vid_a", 4)
    result = analytics.collect(s, youtube=FakeYouTube({"vid_a": (100, 5, 1)}), at=NOW)
    assert result.snapshots == 1 and result.with_retention == 0
    assert any("Connect YouTube again" in w for w in result.warnings)
    assert analytics.load_snapshots(s)["vid_a"][-1].views == 100


def test_collect_explains_an_analytics_api_that_is_not_enabled(tmp_path, analytics_scope):
    s = make_settings(tmp_path)
    uploaded(s, "a.mp4", "vid_a", 4)
    err = RuntimeError(b'{"error": {"status": "PERMISSION_DENIED", "reason": "accessNotConfigured"}}'.decode())
    result = analytics.collect(s, youtube=FakeYouTube({"vid_a": (100, 5, 1)}), analytics=FakeAnalytics({}, fail=err), at=NOW)
    assert result.snapshots == 1 and "YouTube Analytics API → Enable" in result.warnings[0]


def test_collect_needs_the_youtube_login(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    monkeypatch.setattr(publish, "TOKEN_FILE", tmp_path / "none.json")
    with pytest.raises(analytics.AnalyticsError, match="YouTube login"):
        analytics.collect(s, at=NOW)


# --------------------------------------------------------------------------- the report


def seed(s, monkeypatch):
    """Six videos: 3 math quizzes doing well, 2 stories, 1 too new; snapshots on day 1 and 7 for one of them."""
    monkeypatch.setattr(publish, "has_scope", lambda scope: True)
    specs = [  # name, id, days ago, game, mode, script, views
        ("m1.mp4", "m1", 12, "Minecraft", "math", "ONLY 5% OF PEOPLE FINISH THIS. Start with 18.", 9000),
        ("m2.mp4", "m2", 9, "Minecraft", "math", "Most people get this wrong. Start with 24.", 7000),
        ("m3.mp4", "m3", 6, "GTA V / Online", "math", "Only 3% get this right. Start with 31.", 5000),
        ("s1.mp4", "s1", 8, "GTA V / Online", "story", "I accidentally committed a crime at my office.", 1200),
        ("s2.mp4", "s2", 4, "Minecraft", "story", "My boss fired the wrong guy.", 600),
        ("new.mp4", "new", 1, "Minecraft", "story", "I quit on my first day.", 50),
    ]
    stats, rows = {}, {}
    for name, vid, days, game, mode, script, views in specs:
        uploaded(s, name, vid, days, game=game, mode=mode, script=script, duration_seconds=20 if mode == "math" else 40,
                 music_mood="chill_lofi" if mode == "story" else "")
        stats[vid] = (views, views // 20, views // 100)
        rows[vid] = [views, views / 60, 15, 80 if mode == "math" else 45, views // 20, views // 100, 3, 1]
    yt, an = FakeYouTube(stats), FakeAnalytics(rows)
    # Earlier snapshots of m1: its first and seventh day.
    analytics.save_facts(s, [analytics.facts_from_short(s, shorts.get_short(s, "m1.mp4"))])
    pub = (NOW - timedelta(days=12)).astimezone(s.tz).date()
    analytics.save_snapshots(s, [analytics.Snapshot(video_id="m1", day=pub + timedelta(days=1), views=2000),
                                 analytics.Snapshot(video_id="m1", day=pub + timedelta(days=7), views=7500)])
    analytics.collect(s, youtube=yt, analytics=an, at=NOW)
    return yt, an


def test_report_ranks_groups_by_median_views(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    seed(s, monkeypatch)
    report = analytics.build_report(s, days=90, at=NOW)
    assert report.videos == 6 and report.ranked == 5  # the 1-day-old video is listed but not ranked
    assert report.median_views == 5000 and report.with_retention == 6 and report.latest_snapshot == NOW.date()
    by_key = {d.key: d for d in report.dimensions}
    mode = by_key["mode"].groups
    assert [g.key for g in mode] == ["math", "story"]
    assert mode[0].n == 3 and mode[0].median_views == 7000 and mode[0].vs_channel == 1.4 and mode[0].avg_view_pct == 80
    assert mode[1].median_views == 900 and mode[1].vs_channel == 0.18 and mode[1].best_title
    assert mode[0].likes_per_k == 50.0 and mode[0].comments_per_k == pytest.approx(9.9, abs=0.1)
    hooks = by_key["hook_style"].groups
    assert hooks[0].key == "dare" and hooks[0].n == 3 and hooks[-1].key == "first_person"
    assert [g.key for g in by_key["length_bucket"].groups] == ["16-22 s", "31-45 s"]
    assert by_key["game"].groups[0].key == "Minecraft" and by_key["game"].groups[0].n == 3  # m1, m2, s2; "new" is too young
    assert [r.video_id for r in report.top_views[:3]] == ["m1", "m2", "m3"]
    assert report.top_hooks[0].hook.startswith("ONLY 5%") and report.top_retention[0].avg_view_pct == 80
    m1 = next(r for r in report.rows if r.video_id == "m1")
    assert (m1.day1_views, m1.day7_views) == (2000, 7500) and m1.views_per_day == 750.0
    new = next(r for r in report.rows if r.video_id == "new")
    assert not new.ranked and (new.day1_views, new.day7_views) == (50, None)  # today is its first day
    assert report.rows[0].video_id == "new"  # newest first
    assert report.notes == []


def test_report_window_notes_and_text(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    seed(s, monkeypatch)
    week = analytics.build_report(s, days=7, at=NOW)
    assert week.videos == 3 and {r.video_id for r in week.rows} == {"m3", "s2", "new"}

    later = analytics.build_report(s, days=90, at=NOW + timedelta(days=3))
    assert later.ranked == 6 and any("last read on" in n for n in later.notes)

    lines = analytics.insight_lines(analytics.build_report(s, days=90, at=NOW))
    assert len(lines) == 3 and lines[0].startswith("• Kind of video → **Math challenge**: 7.0k views median over 3 videos")
    assert "1.4× the channel median" in lines[0] and "80% watched" in lines[0]
    messages = analytics.format_report(analytics.build_report(s, days=90, at=NOW))
    text = "\n".join(messages)
    assert text.startswith("📈 **Insights: the last 90 days** · 6 videos, 23k views, median 5.0k per video")

    assert "__Hook style__" in text and "weakest:" not in text and "https://youtu.be/m1" in text
    assert "weakest: First person" in "\n".join(analytics.format_report(analytics.build_report(s, days=90, at=NOW), per_dimension=1))
    assert all(len(m) <= 2000 for m in messages)



def test_empty_report_says_what_to_do(tmp_path):
    s = make_settings(tmp_path)
    report = analytics.build_report(s, at=NOW)
    assert report.videos == 0 and report.dimensions == [] and "Refresh" in report.notes[0]
    assert analytics.insight_lines(report) == []
    assert "No videos yet" in analytics.format_report(report)[0]


def test_young_videos_only_report_waits(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    monkeypatch.setattr(publish, "has_scope", lambda scope: False)
    uploaded(s, "a.mp4", "vid_a", 1)
    analytics.collect(s, youtube=FakeYouTube({"vid_a": (100, 5, 1)}), at=NOW)
    report = analytics.build_report(s, at=NOW)
    assert report.videos == 1 and report.ranked == 0 and report.dimensions == []
    assert any("younger than 2 days" in n for n in report.notes) and any("Retention" in n for n in report.notes)


# --------------------------------------------------------------------------- dashboard


def test_insights_endpoints(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    yt, an = seed(s, monkeypatch)
    monkeypatch.setattr(web, "_base_settings", lambda: s)
    monkeypatch.setattr(publish, "youtube_status", lambda: {"connected": True, "can_analytics": True})
    client = TestClient(web.app)

    body = client.get("/api/insights?days=90").json()
    assert body["videos"] == 6 and body["dimensions"][1]["groups"][0]["label"] == "Math challenge"
    assert body["youtube"]["connected"] is True
    assert client.get("/api/insights?days=3").status_code == 422

    monkeypatch.setattr(analytics, "collect", lambda settings: analytics.CollectResult(day=NOW.date(), videos=6, snapshots=6))
    body = client.post("/api/insights/refresh?days=30").json()
    assert body["collected"]["snapshots"] == 6 and body["days"] == 30

    def boom(settings):
        raise analytics.AnalyticsError("YouTube is not connected")
    monkeypatch.setattr(analytics, "collect", boom)
    assert client.post("/api/insights/refresh").status_code == 400
