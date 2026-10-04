from datetime import datetime, timedelta, timezone

from trendclip.games import detect_games, normalize, summarize_games
from trendclip.models import YouTubeVideo

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def video(vid, title, tags=None, views=10_000, hours=10, subs=100_000):
    return YouTubeVideo(
        video_id=vid,
        title=title,
        channel_id=f"c{vid}",
        channel_title=f"Channel {vid}",
        published_at=NOW - timedelta(hours=hours),
        fetched_at=NOW,
        views=views,
        tags=tags or [],
        channel_subscribers=subs,
        regions=["US"],
    )


def test_normalize_strips_accents_and_punctuation():
    assert normalize("Pokémon: Legends Z-A!") == " pokemon legends z a "
    assert normalize("Baldur's Gate 3") == " baldurs gate 3 "


def test_title_matching_basics():
    assert detect_games(video("1", "I survived 100 days in Minecraft Hardcore")) == ["Minecraft"]
    assert detect_games(video("2", "WOW this is insane lol")) == []
    assert detect_games(video("3", "Rust is the best language")) == ["Rust"]  # known trade-off


def test_specific_entry_beats_franchise():
    assert detect_games(video("1", "GTA 6 Trailer 3 Breakdown")) == ["GTA VI"]
    assert detect_games(video("2", "GTA V vs GTA 6 graphics")) == ["GTA V / Online", "GTA VI"]
    assert detect_games(video("3", "Mario Kart World 200cc")) == ["Mario Kart"]
    assert detect_games(video("4", "Pokémon Legends Z-A first look")) == ["Pokemon Legends: Z-A"]
    assert detect_games(video("5", "Grow a Garden update", tags=["roblox"])) == ["Grow a Garden"]


def test_hashtag_and_compact_forms():
    assert detect_games(video("1", "Insane clutch #MarvelRivals")) == ["Marvel Rivals"]
    assert detect_games(video("2", "#GTA6 leaks")) == ["GTA VI"]
    assert detect_games(video("3", "BO7 zombies Easter egg")) == ["Black Ops 7"]


def test_primary_game_is_first_in_title_then_tags():
    assert detect_games(video("1", "Fortnite vs Minecraft"))[0] == "Fortnite"
    tag_only = video("2", "My craziest win ever", tags=["valorant", "valorant clips", "fps"])
    assert detect_games(tag_only) == ["Valorant"]
    title_and_tags = video("3", "Apex Legends ranked", tags=["fortnite"])
    assert detect_games(title_and_tags) == ["Apex Legends", "Fortnite"]


def test_summarize_games_ranks_and_tags_videos():
    vids = [
        video("1", "Minecraft but...", views=500_000, hours=5),
        video("2", "Minecraft speedrun", views=300_000, hours=5),
        video("3", "Fortnite new season", views=900_000, hours=5),
        video("4", "Daily vlog"),
    ]
    games = summarize_games(vids)
    assert [g.name for g in games] == ["Minecraft", "Fortnite"]
    mc = games[0]
    assert mc.video_count == 2 and mc.total_views == 800_000
    assert mc.videos[0].video_id == "1"
    assert 0 < mc.view_share < 1
    assert vids[3].game is None and vids[0].game == "Minecraft"
