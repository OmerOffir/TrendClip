import os
from datetime import datetime, timedelta, timezone

import pytest

from trendclip import aggregator
from trendclip.config import ConfigError, Settings, get_settings
from trendclip.models import YouTubeVideo
from trendclip.youtube_client import YouTubeClient, flag_outliers, parse_iso8601_duration

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def make_video(vid, title, views, hours_old, subs=100_000, tags=None, likes=None, channel="Chan"):
    return YouTubeVideo(
        video_id=vid,
        title=title,
        channel_id=f"c-{vid}",
        channel_title=channel,
        published_at=NOW - timedelta(hours=hours_old),
        fetched_at=NOW,
        views=views,
        likes=likes,
        comments=None,
        tags=tags or [],
        channel_subscribers=subs,
        regions=["US"],
    )


def test_velocity_metrics():
    v = make_video("a", "x", views=100_000, hours_old=10, subs=50_000, likes=5_000)
    assert v.age_hours == 10
    assert v.views_per_hour == 10_000
    assert v.outlier_ratio == 2.0
    assert v.engagement_rate == 0.05
    assert v.velocity_score > make_video("b", "x", 100_000, 100, subs=50_000, likes=5_000).velocity_score


def test_velocity_floors():
    brand_new = make_video("a", "x", views=500, hours_old=0, subs=10)
    assert brand_new.age_hours == 1.0
    assert brand_new.outlier_ratio == 0.5  # subscribers floored to 1,000
    hidden = make_video("b", "x", views=500, hours_old=5, subs=None)
    assert hidden.outlier_ratio is None


def test_flag_outliers():
    videos = [make_video(str(i), "t", views=10_000, hours_old=10) for i in range(9)]
    videos.append(make_video("small", "t", views=50_000, hours_old=48, subs=5_000))
    flag_outliers(videos, ratio_threshold=2.0)
    flagged = {v.video_id for v in videos if v.is_outlier}
    assert "small" in flagged


def test_duration_parsing():
    assert parse_iso8601_duration("PT1H2M3S") == 3723
    assert parse_iso8601_duration("PT45S") == 45
    assert parse_iso8601_duration("P1DT1S") == 86401
    assert parse_iso8601_duration("P0D") == 0
    assert parse_iso8601_duration("garbage") is None


def test_parse_video_handles_hidden_stats():
    item = {
        "id": "abc",
        "snippet": {
            "title": "GTA 6 Trailer 3",
            "channelId": "UC1",
            "channelTitle": "Rockstar",
            "publishedAt": "2026-10-01T12:00:00Z",
            "categoryId": "20",
        },
        "statistics": {"viewCount": "1000"},
        "contentDetails": {"duration": "PT2M"},
    }
    video = YouTubeClient.parse_video(item, "US")
    assert video.views == 1000 and video.likes is None and video.duration_seconds == 120


def test_keyword_extraction_skips_generic_and_channel_tags():
    v = make_video(
        "a",
        "GTA 6 Gameplay | NEW Leak Explained! #GTA6",
        1000,
        5,
        tags=["Chan", "gameplay", "GTA 6", "Rockstar Games"],
    )
    kws = aggregator.extract_keywords(v)
    assert "gta 6" in kws and "gta6" in kws and "rockstar games" in kws
    assert "gameplay" not in kws and "chan" not in kws
    assert kws.index("gta 6") < kws.index("leak")  # tags come first


def test_build_topics_clusters_shared_keywords():
    videos = [
        make_video("1", "GTA 6 leak breakdown", 500_000, 5, tags=["gta 6"]),
        make_video("2", "GTA 6 map size compared", 300_000, 8, tags=["gta 6"]),
        make_video("3", "Minecraft hardcore day 100", 50_000, 20),
    ]
    flag_outliers(videos, 2.0)
    topics = aggregator.build_topics(videos)
    top = topics[0]
    assert top.topic == "gta 6"
    assert top.video_count == 2
    assert {"1", "2"} == {v.video_id for v in top.videos}
    # Phrases that cover the same video set collapse into one topic.
    assert sum(1 for t in topics if {v.video_id for v in t.videos} == {"1", "2"}) == 1


def test_rank_candidates_orders_by_score():
    videos = [
        make_video("1", "Minecraft hardcore", 10_000, 50, tags=["minecraft"]),
        make_video("2", "Minecraft speedrun", 10_000, 50, tags=["minecraft"]),
        make_video("3", "GTA 6 leak", 900_000, 3, tags=["gta 6"]),
        make_video("4", "GTA 6 map", 800_000, 4, tags=["gta 6"]),
    ]
    ranked = aggregator.rank_candidates(aggregator.build_topics(videos), limit=1)
    assert len(ranked) == 1 and ranked[0].topic == "gta 6"


def test_settings_validation(monkeypatch, tmp_path):
    get_settings.cache_clear()
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        get_settings(str(tmp_path / "missing.env"))
    get_settings.cache_clear()

    from trendclip import config

    root = tmp_path / "shared.env"
    root.write_text("YOUTUBE_API_KEY=shared\nCHANNEL=othercast\nCHANNEL_HANDLE=@Shared\nTTS_RATE=+0%\n")
    monkeypatch.setattr(config, "CHANNELS_DIR", tmp_path / "channels")
    (tmp_path / "channels" / "othercast").mkdir(parents=True)
    (tmp_path / "channels" / "othercast" / "channel.env").write_text(
        "CHANNEL_HANDLE=@OtherCast\nSTATS_TIKTOK=https://www.tiktok.com/@othercast\n")
    for name in ("CHANNEL", "CHANNEL_HANDLE", "TTS_RATE", "STATS_TIKTOK"):
        monkeypatch.delenv(name, raising=False)
    s = get_settings(str(root))
    assert (s.channel, s.channel_handle, s.stats_tiktok, s.tts_rate) == ("othercast", "@OtherCast", "othercast", "+0%")
    assert s.channel_dir == tmp_path / "channels" / "othercast"
    assert config.channel_file("token_youtube.json", "othercast") == s.channel_dir / "token_youtube.json"
    assert config.channel_file("client_secret.json", "othercast", shared=True) == config.PROJECT_ROOT / "client_secret.json"
    get_settings.cache_clear()
    monkeypatch.setenv("CHANNEL_HANDLE", "@FromShell")  # a real environment variable wins over channel.env
    assert get_settings(str(root)).channel_handle == "@FromShell"
    get_settings.cache_clear()
    for name in ("YOUTUBE_API_KEY", "CHANNEL", "TTS_RATE"):
        os.environ.pop(name, None)  # load_dotenv put the shared file's values in the environment

    s = Settings(youtube_api_key="k", yt_regions="us, gb")
    assert s.yt_regions == ["US", "GB"]
    with pytest.raises(ValueError):
        Settings(youtube_api_key="k", yt_regions="USA")
