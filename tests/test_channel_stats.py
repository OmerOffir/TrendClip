import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from trendclip import channel_stats as cs
from trendclip.config import Settings

NOW = datetime(2026, 10, 8, 7, 0, tzinfo=timezone.utc)


def make_settings(tmp_path, **kw):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "out", **kw)


def test_handles_accept_profile_links(tmp_path):
    s = make_settings(tmp_path, stats_youtube="https://www.youtube.com/@SideQuestLogic-t6g",
                      stats_tiktok="https://www.tiktok.com/@sidequestlogic",
                      stats_instagram="https://www.instagram.com/sidequestlogic/reels/")
    assert (s.stats_youtube, s.stats_tiktok, s.stats_instagram) == ("@SideQuestLogic-t6g", "sidequestlogic", "sidequestlogic")
    assert make_settings(tmp_path, stats_youtube="SideQuestLogic").stats_youtube == "@SideQuestLogic"
    assert make_settings(tmp_path, stats_tiktok="@name").stats_tiktok == "name"


class Call:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeYouTube:
    def __init__(self):
        self.pages = 0

    def channels(self):
        return SimpleNamespace(list=lambda **kw: Call({"items": [{
            "statistics": {"subscriberCount": "12", "viewCount": "1078", "videoCount": "9"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}))

    def playlistItems(self):
        def list_(**kw):
            self.pages += 1
            return Call({"items": [
                {"contentDetails": {"videoId": "new", "videoPublishedAt": "2026-10-07T15:00:00Z"}, "snippet": {}},
                {"contentDetails": {"videoId": "old", "videoPublishedAt": "2026-10-05T15:00:00Z"}, "snippet": {}},
            ], "nextPageToken": "more"})
        return SimpleNamespace(list=list_)

    def videos(self):
        return SimpleNamespace(list=lambda **kw: Call({"items": [{
            "id": "new", "snippet": {"title": "Part 1", "publishedAt": "2026-10-07T15:00:00Z"},
            "statistics": {"viewCount": "23", "likeCount": "1", "commentCount": "1"}}]} if kw["id"] == "new" else {}))


def test_youtube_takes_only_last_24h_uploads(tmp_path):
    api = FakeYouTube()
    out = cs.youtube_stats(make_settings(tmp_path), NOW - timedelta(hours=24), api=api)
    assert out.followers == 12 and out.total_views == 1078
    assert [(v.id, v.views, v.likes, v.comments) for v in out.videos] == [("new", 23, 1, 1)]
    assert api.pages == 1  # stopped at the first older upload


class FakeTikTok:
    def __init__(self, items):
        self.items = items

    def get(self, url, params=None, **kw):
        if "item_list" in url:
            return SimpleNamespace(status_code=200, json=lambda: {"itemList": self.items, "hasMore": False})
        data = {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"statusCode": 0, "userInfo": {
            "user": {"secUid": "SEC"}, "stats": {"followerCount": 5, "heartCount": 22, "videoCount": 2}}}}}
        html = f'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">{json.dumps(data)}</script>'
        return SimpleNamespace(status_code=200, text=html)


def test_tiktok_profile_and_videos(tmp_path):
    items = [
        {"id": "1", "desc": "Fresh one #fyp", "createTime": int((NOW - timedelta(hours=3)).timestamp()),
         "stats": {"playCount": 73, "diggCount": 3, "commentCount": 0, "shareCount": 1}},
        {"id": "2", "desc": "Older", "createTime": int((NOW - timedelta(days=3)).timestamp()),
         "stats": {"playCount": 200, "diggCount": 9, "commentCount": 2, "shareCount": 0}},
    ]
    out = cs.tiktok_stats(make_settings(tmp_path), NOW - timedelta(hours=24), session=FakeTikTok(items))
    assert (out.followers, out.total_likes, out.total_views) == (5, 22, 273)
    assert [(v.id, v.views, v.likes, v.shares) for v in out.videos] == [("1", 73, 3, 1)]
    assert out.videos[0].url == "https://www.tiktok.com/@sidequestlogic/video/1"


def test_instagram_without_token_says_how_to_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "INSTAGRAM_TOKEN_FILE", tmp_path / "token_instagram.json")
    refused = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(status_code=401, json=lambda: {"require_login": True}))
    try:
        cs.instagram_stats(make_settings(tmp_path), NOW, session=refused)
    except cs.StatsError as err:
        assert "INSTAGRAM_ACCESS_TOKEN" in str(err)
    else:
        raise AssertionError("expected a StatsError")


def test_instagram_api_with_token(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "INSTAGRAM_TOKEN_FILE", tmp_path / "token_instagram.json")
    answers = {
        "refresh_access_token": {"access_token": "NEW"},
        "me": {"username": "sidequestlogic", "followers_count": 40, "media_count": 3},
        "me/media": {"data": [
            {"id": "a", "caption": "New reel", "timestamp": "2026-10-07T20:00:00+0000", "like_count": 6,
             "comments_count": 2, "permalink": "https://www.instagram.com/reel/a/"},
            {"id": "b", "caption": "Old", "timestamp": "2026-10-01T20:00:00+0000", "like_count": 1, "comments_count": 0},
        ]},
        "a/insights": {"data": [{"values": [{"value": 321}]}]},
    }
    seen = []

    def get(url, params=None, **kw):
        path = url.split(".com/")[1].removeprefix("v23.0/")
        seen.append((path, params.get("access_token")))
        return SimpleNamespace(status_code=200, json=lambda: answers[path])

    s = make_settings(tmp_path, instagram_access_token="OLD-TOKEN")
    out = cs.instagram_stats(s, NOW - timedelta(hours=24), session=SimpleNamespace(get=get))
    assert out.followers == 40 and [(v.id, v.views, v.likes, v.comments) for v in out.videos] == [("a", 321, 6, 2)]
    assert seen[0] == ("refresh_access_token", "OLD-TOKEN") and seen[1] == ("me", "NEW")
    assert json.loads((tmp_path / "token_instagram.json").read_text())["access_token"] == "NEW"
    seen.clear()
    cs.instagram_token(s, SimpleNamespace(get=get))  # refreshed recently: no new refresh
    assert seen == []


def test_report_shows_changes_since_yesterday_and_survives_a_failing_platform(tmp_path, monkeypatch):
    s = make_settings(tmp_path)
    numbers = iter([(10, 500), (13, 650)])

    def youtube(settings, since):
        followers, views = next(numbers)
        return cs.PlatformStats(platform="youtube", handle="@me", url="https://www.youtube.com/@me",
                                followers=followers, total_views=views,
                                videos=[cs.VideoStats(id="v", title="Big one #shorts", url="https://youtu.be/v",
                                                      published=NOW - timedelta(hours=2), views=90, likes=4, comments=1)])

    def broken(settings, since):
        raise cs.StatsError("Instagram refuses logged-out requests")

    monkeypatch.setattr(cs, "FETCHERS", {"youtube": youtube, "instagram": broken})
    first = cs.build_report(s, at=NOW - timedelta(days=1))
    assert first.platforms[0].since is None
    report = cs.build_report(s, at=NOW)
    yt, ig = report.platforms
    assert (yt.followers_change, yt.views_change) == (3, 150) and ig.error
    text = cs.format_report(report, timezone.utc)
    assert "**13** subscribers (+3)" in text[1] and "650 views overall (+150)" in text[1]
    assert "[Big one](<https://youtu.be/v>) · today 05:00" in text[1] and "👁 90 · ❤️ 4 · 💬 1" in text[1]
    assert "Couldn't read it" in text[2] and all(len(m) < 2000 for m in text)
