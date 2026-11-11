"""The long adventure "Can You Beat Minecraft?": its script, its hearts, its place in the dashboard."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trendclip import quest, shorts
from trendclip.config import Settings
from trendclip.web import app as web


def settings(tmp_path: Path) -> Settings:
    return Settings(output_dir=tmp_path / "out", assets_dir=tmp_path / "assets", stickers_dir=tmp_path / "stickers",
                    youtube_api_key="x", gemini_api_key="")


def test_the_narrator_starts_with_ten_hearts_and_reaches_the_end_alive():
    hearts = quest.run()
    assert hearts[0] == 10 and len(hearts) == len(quest.STOPS) + 1 == 8
    assert all(0 < h <= 10 for h in hearts)  # a dead narrator has no video to finish


def test_every_stop_has_words_and_the_narrators_own_option():
    for stop, lines in zip(quest.STOPS, quest.scripts()):
        assert lines and all(text.strip() for _, text in lines)
        assert stop.mine() in stop.options()
    assert [stop.N for stop in quest.STOPS] == list(range(1, 8))


def test_the_script_fits_a_short():
    words = sum(len(text.split()) for lines in quest.scripts() for _, text in lines)
    assert words < 470  # about 2.7 words a second: 470 words would pass the three minutes


def test_missing_pictures_stop_the_video_before_anything_is_drawn(tmp_path):
    s = settings(tmp_path)
    assert quest.missing(s) == sorted(quest.needed())
    with pytest.raises(ValueError, match="No picture of"):
        quest.create(s)


def test_the_video_waits_in_upload_with_its_texts(tmp_path, monkeypatch):
    s = settings(tmp_path)
    monkeypatch.setattr(quest, "missing", lambda settings: [])
    monkeypatch.setattr("trendclip.video_assembler.media_duration", lambda path: 154.66)

    def fake(settings, output, progress, only=0):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        quest.record(settings, output)

    monkeypatch.setattr(quest, "_make", fake)
    short = quest.create(s)
    assert short.filename.startswith(quest.PREFIX) and short.title == quest.TITLE and short.mode == "gamer"
    assert short.duration_seconds < shorts.SHORTS_MAX_SECONDS
    assert "Comment your hearts" in short.script and f"with {quest.run()[-1]}" in short.pinned_comment
    assert [v.filename for v in quest.videos(s)] == [short.filename]
    assert shorts.list_shorts(s)[0].filename == short.filename


def test_a_failed_drawing_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(quest.sys, "executable", "false")
    with pytest.raises(RuntimeError, match="could not be made"):
        quest._make(settings(tmp_path), tmp_path / "x.mp4", lambda frac, msg: None)


def test_the_dashboard_shows_the_quest_and_refuses_without_pictures(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "_base_settings", lambda: settings(tmp_path))
    client = TestClient(web.app)
    view = client.get("/api/gamer/quest").json()
    assert view["title"] == quest.TITLE and len(view["stops"]) == 7 and view["missing"] and view["videos"] == []
    assert client.post("/api/gamer/quest").status_code == 400
    assert [s["stop"] for s in view["solos"]] == [1, 2, 4, 5, 6, 7]
    assert client.post("/api/gamer/quest", json={"stop": 3}).status_code == 400  # the village is no Short of its own
    page = client.get("/").text
    assert "gqMake" in page and "gqMakeOne" in page


def test_a_stop_alone_dares_first_and_asks_for_the_hearts():
    for stop in quest.solos():
        lines = quest.scripts(stop.N)[0]
        roles = [role for role, _ in lines]
        assert roles[0] == "title" and {"threat", "pause", "mine", "next"} <= set(roles)
        assert lines[0][1] == stop.HOOK and lines[-1][1] == "Comment your hearts."
        words = " ".join(text for _, text in lines)
        assert "Next stop" not in words and "Last stop" not in words and "Stop " not in words
        assert len(words.split()) < 90  # about half a minute at most
        assert 0 < stop.after() <= 10
    with pytest.raises(ValueError, match="not a Short of its own"):
        quest.scripts(3)


def test_a_stop_alone_waits_in_upload_under_its_own_title(tmp_path, monkeypatch):
    s = settings(tmp_path)
    monkeypatch.setattr(quest, "missing", lambda settings: [])
    monkeypatch.setattr("trendclip.video_assembler.media_duration", lambda path: 19.88)
    seen = []

    def fake(settings, output, progress, only=0):
        seen.append(only)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        quest.record(settings, output, None, only)

    monkeypatch.setattr(quest, "_make", fake)
    short = quest.create(s, only=7)
    assert seen == [7] and short.filename.startswith("quest_end_") and short.title == quest.End.SOLO
    assert short.title_card == "PAUSE TO FIGHT THE ENDER DRAGON" and "Next stop" not in short.script
    assert f"I kept {quest.End.after()}" in short.pinned_comment and quest.TITLE in short.description


def test_pause_twice_is_a_chest_then_a_fight_on_five_hearts():
    from trendclip import arena, spin

    for foe in ("Creeper", "Warden", "Pig", "Ender Dragon", "Wither"):
        only = f"{quest.TWICE}{foe}"
        loot, fight = quest._stops(only)
        chest, battle = quest.scripts(only)
        assert chest[0][1] == f"Five hearts. Can you beat the {foe.lower()}?" and chest[-1][1] == "Now, the fight."
        assert battle[-1][1] == "Comment your hearts." and foe.lower() in battle[1][1].lower()
        left = quest.TWICE_HEARTS + loot.mine().delta + fight.mine().delta
        assert 1 <= left <= 7 and f"I have {quest._hearts(left)} left" in battle[4][1]
        assert fight.MINE != "Bow" and loot.mine().delta != 0
        for option in fight.options():  # the hearts a weapon costs are the pause Shorts' own rule
            assert option.delta == -arena.hearts_lost(foe, option.name)
        text = quest.texts(only)
        assert text["title"] == f"Pause Twice To Beat The {foe} 🎁⚔️" and f"I kept {left}" in text["pinned"]
        assert len(" ".join(t for lines in (chest, battle) for _, t in lines).split()) < 100
    assert quest._stops(f"{quest.TWICE}Creeper")[1].MINE == quest._stops(f"{quest.TWICE}Creeper")[1].MINE
    assert set(quest.twice_foes()) == set(spin.BOSSES) | set(spin.FOES)
    with pytest.raises(ValueError, match="No such mob"):
        quest.scripts(f"{quest.TWICE}Herobrine")


def test_pause_twice_waits_in_upload_and_the_dashboard_asks_for_it(tmp_path, monkeypatch):
    s = settings(tmp_path)
    monkeypatch.setattr(quest, "missing", lambda settings: [])
    monkeypatch.setattr("trendclip.dungeon.pictures", lambda settings: {m: {} for m in quest.twice_foes()})
    monkeypatch.setattr("trendclip.video_assembler.media_duration", lambda path: 31.7)
    seen = []

    def fake(settings, output, progress, only=0):
        seen.append(only)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        quest.record(settings, output, None, only)

    monkeypatch.setattr(quest, "_make", fake)
    short = quest.create(s, only="twice:Iron Golem")
    assert seen == ["twice:Iron Golem"] and short.filename.startswith("quest_twice_iron-golem_")
    assert short.title == "Pause Twice To Beat The Iron Golem 🎁⚔️" and short.end_card == "Comment your hearts"
    monkeypatch.setattr(web, "_base_settings", lambda: s)
    client = TestClient(web.app)
    assert "Creeper" in client.get("/api/gamer/quest").json()["foes"]
    assert client.post("/api/gamer/quest", json={"twice": True, "foe": "Herobrine"}).status_code == 400


def test_the_fight_alone_is_a_short_on_ten_hearts(tmp_path, monkeypatch):
    for foe in ("Creeper", "Warden", "Chicken"):
        only = f"{quest.FIGHT}{foe}"
        [fight] = quest._stops(only)
        [lines] = quest.scripts(only)
        assert lines[0][1] == f"Ten hearts. Can you beat the {foe.lower()}?" and lines[-1][1] == "Comment your hearts."
        left = quest.HEARTS + fight.mine().delta
        assert 7 <= left <= 9 and f"I have {quest._hearts(left)} left" in " ".join(t for _, t in lines)
        text = quest.texts(only)
        assert text["title"] == f"Pause To Beat The {foe} ⚔️" and f"I kept {left}" in text["pinned"]
    s = settings(tmp_path)
    monkeypatch.setattr(quest, "missing", lambda settings: [])
    monkeypatch.setattr("trendclip.dungeon.pictures", lambda settings: {m: {} for m in quest.twice_foes()})
    monkeypatch.setattr("trendclip.video_assembler.media_duration", lambda path: 18.0)

    def fake(settings, output, progress, only=0):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"mp4")
        quest.record(settings, output, None, only)

    monkeypatch.setattr(quest, "_make", fake)
    short = quest.create(s, only="fight:Creeper")
    assert short.filename.startswith("quest_fight_creeper_") and short.script.startswith("Ten hearts")


def test_the_wall_alone_is_the_axe_short_drawn():
    from trendclip import spin

    assert set(quest.WALLS) == {name for name, (_, _, axe) in spin.WALL.items() if axe}  # the walls an axe is for
    assert {b.lower().replace(" ", "-") for b in quest.WALLS} == set(quest.WALL_TILES)
    costs = {}
    for block in quest.WALLS:
        only = f"{quest.WALL}{block}"
        [wall] = quest._stops(only)
        [lines] = quest.scripts(only)
        assert lines[0][1] == "Ten hearts. Can you break this wall?" and lines[-1][1] == "Comment your hearts."
        assert quest.WALLS[block][0] in lines[1][1] and wall.MINE in spin.AXES
        costs[block] = {o.name: -o.delta for o in wall.options()}
        assert costs[block]["Golden Axe"] == min(costs[block].values())  # gold is the fastest on wood
        assert quest.texts(only)["title"] == f"Pause To Break The {block} Wall 🪓"
        assert quest._tile(block.lower().replace(" ", "-")).size == (16, 16)
    assert costs["Pumpkin"]["Iron Axe"] == 0 and costs["Oak Log"]["Iron Axe"] == 1 and costs["Chest"]["Iron Axe"] == 2
    assert min(costs["Chest"].values()) == 1 and costs["Oak Log"]["Wooden Axe"] == 3
    with pytest.raises(ValueError, match="No such wall"):
        quest.scripts(f"{quest.WALL}Bedrock")


def test_the_mob_short_drawn_counts_hearts_by_how_the_fight_ends():
    from trendclip import arena

    seen = set()
    for boss in quest.pet_bosses():
        only = f"{quest.PET}{boss}"
        [pet] = quest._stops(only)
        [lines] = quest.scripts(only)
        assert lines[0][1] == f"Ten hearts. Can your mob beat the {boss.lower()}?" and lines[-1][1] == "Comment your hearts."
        for option in pet.options():
            mine, theirs = arena.pet_fight(boss, option.name)
            won = mine is not None and mine <= theirs
            assert option.delta == (0 if won else -3 if mine is None else -2) and option.name != boss
            assert option.sub.startswith("WINS IN" if won else "CAN'T ATTACK" if mine is None else "FALLS AFTER")
        left = quest.HEARTS + pet.mine().delta
        seen.add(left)
        assert f"I have {quest._hearts(left)} left" in lines[4][1]
        assert quest.texts(only)["title"] == f"Can Your Mob Beat The {boss}? 🐺"
    assert seen == {7, 8, 10}  # the narrator wins some, loses some, and once his mob can't even attack
    with pytest.raises(ValueError, match="No such boss"):
        quest.scripts(f"{quest.PET}Warden")
