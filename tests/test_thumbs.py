import json
import os
import subprocess

import pytest
from PIL import Image

from modules import thumbs


def _video(path, color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=320x568:d=1",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def test_paths_per_channel(tmp_path):
    assert thumbs.paths("borderlinewhy", "2026-10-31_1430", tmp_path) == (
        tmp_path / "borderlinewhy" / "2026-10-31_1430" / "video.mp4", tmp_path / "borderlinewhy" / "2026-10-31_1430" / "thumbnail.jpg")
    assert thumbs.paths("darkarchives", "30", tmp_path)[1] == tmp_path / "darkarchives" / "story_30.thumbnail.jpg"
    assert thumbs.paths("sidequestlogic", "gta_1.mp4", tmp_path)[1] == tmp_path / "shorts" / "gta_1.thumbnail.jpg"


@pytest.mark.parametrize("ref", ["../secret", "a/b", ".hidden", ""])
def test_bad_refs_are_refused(tmp_path, ref):
    with pytest.raises(thumbs.ThumbError):
        thumbs.paths("sidequestlogic", ref, tmp_path)


def test_unknown_channel_and_missing_video(tmp_path):
    with pytest.raises(thumbs.ThumbError):
        thumbs.paths("aftermarket", "1", tmp_path)
    with pytest.raises(thumbs.ThumbError):
        thumbs.ensure("darkarchives", "9", tmp_path)


def test_dark_outliers_thumbnail(tmp_path):
    _video(tmp_path / "darkarchives" / "story_7.mp4")
    (tmp_path / "darkarchives" / "story_7.json").write_text(json.dumps({"cover_text": "Infected on purpose?"}))
    out = thumbs.draw("darkarchives", "7", tmp_path)
    assert Image.open(out).size == (1080, 1920)
    assert thumbs.fresh("darkarchives", "7", tmp_path) == out


def test_sidequest_thumbnail_without_its_clip(tmp_path):
    _video(tmp_path / "shorts" / "gta_1.mp4", "blue")
    (tmp_path / "shorts" / "gta_1.json").write_text(json.dumps({"title_card": "LEVEL 12 - GTA FANS ONLY", "background": "gone.mp4"}))
    out = thumbs.draw("sidequestlogic", "gta_1.mp4", tmp_path)
    assert Image.open(out).size == (1080, 1920)


def test_a_catch_level_thumbnail_has_the_zombie_holding_the_mark(tmp_path):
    _video(tmp_path / "shorts" / "mc_1.mp4", "blue")
    (tmp_path / "shorts" / "mc_1.json").write_text(json.dumps({"title_card": "LEVEL 12 - CATCH THE ZOMBIE",
                                                              "render_settings": {"catch": ["🍕", "🚀", "👺"]}}))
    out = thumbs.draw("sidequestlogic", "mc_1.mp4", tmp_path)
    img = Image.open(out)
    assert img.size == (1080, 1920)
    shirt = (0, 150, 150)
    near = lambda px: all(abs(a - b) <= 4 for a, b in zip(px[:3], shirt))  # JPEG rounding
    assert near(img.getpixel((540, 1320)))  # the zombie's shirt, centre of the lower half
    assert not near(img.getpixel((540, 900)))  # and nothing of it up in the title


def test_a_newer_video_makes_the_thumbnail_stale(tmp_path):
    video, thumb = thumbs.paths("darkarchives", "3", tmp_path)
    _video(video)
    thumb.write_bytes(b"x")
    os.utime(thumb, (1, 1))
    assert thumbs.fresh("darkarchives", "3", tmp_path) is None


@pytest.mark.parametrize("title, script, kind, what", [
    ("Pause To Get Your Weapon vs The Warden ⚔️", "", "fight", "Warden"),
    ("Pause To Beat The Skeleton | Dungeon Run 3/8 ⚔️", "", "fight", "Skeleton"),
    ("Pause To Beat The Final Boss | Dungeon Run 8/8 💀", "The final boss. A Wither is waiting.", "fight", "Wither"),
    ("Pause To Fight The Ender Dragon 🐉", "Ten hearts. Can you beat the Ender Dragon?", "fight", "Ender Dragon"),
    ("Can You Beat Minecraft? Pause Edition", "Ten hearts.", "fight", "Ender Dragon"),
    ("Pause To Get Your Minecraft Axe 🪓", "", "wall", "obsidian"),
    ("Pause To Break The Wall | Dungeon Run 4/8 ⛏️", "A Cobblestone wall blocks the way.", "wall", "cobble"),
    ("Pause To Dig Out Of The Cave ⛏️", "Ten hearts.", "wall", "stone"),
    ("Pause To Open The Chest | Dungeon Run 2/8 🎁", "", "chest", None),
    ("Pause To Get Your Minecraft Block 🎮", "", "blocks", None),
    ("Pause To Cross The Lava Lake 🔥", "Ten hearts.", "lava", None),
])
def test_the_scene_comes_from_the_title(title, script, kind, what):
    from modules import sql_thumb

    scene = sql_thumb.plan({"title": title, "script": script})
    assert scene["kind"] == kind
    if what:
        assert what in (scene.get("mob"), scene.get("block"))


def test_question_levels_keep_the_title_card():
    from modules import sql_thumb

    assert sql_thumb.plan({"title": "Real Minecraft Players Know This | Level 29 🎮"}) is None


def test_a_weapon_short_draws_the_fight(tmp_path):
    from modules import sql_thumb

    info = {"title": "Pause To Get Your Weapon vs The Zombie ⚔️", "title_card": "PAUSE NOW!"}
    if not sql_thumb.available(sql_thumb.plan(info)):
        pytest.skip("no mob pictures on this machine")
    _video(tmp_path / "shorts" / "mc_2.mp4", "blue")
    (tmp_path / "shorts" / "mc_2.json").write_text(json.dumps(info))
    img = Image.open(thumbs.draw("sidequestlogic", "mc_2.mp4", tmp_path))
    assert img.size == (1080, 1920)
    r, g, b = img.getpixel((540, 140))[:3]  # the yellow PAUSE NOW! box
    assert r > 200 and g > 170 and b < 80


def test_a_drawn_shorts_rule_goes_on_top_of_its_thumbnail():
    from modules import sql_thumb

    scene = sql_thumb.plan({"title": "Pause To Beat The Stray ⚔️ (Hardcore)", "script": "Three hearts, one stray. Can you win?"})
    assert (scene["kind"], scene["mob"], scene["top"], scene["hearts"]) == ("fight", "Stray", "HARDCORE!", 3)
    scene = sql_thumb.plan({"title": "Pause To Break The Bookshelf Wall 🪓 (Half Health)", "script": "Five hearts and a wall."})
    assert (scene["kind"], scene["block"], scene["top"], scene["hearts"]) == ("wall", "bookshelf", "HALF HEALTH!", 5)
    scene = sql_thumb.plan({"title": "Can Your Mob Beat The Spider? 🐺 (With A Shield)", "script": "Ten hearts, and a mob"})
    assert scene["mob"] == "Spider" and scene["top"] == "WITH A SHIELD!" and scene["hearts"] == 10
    assert sql_thumb.plan({"title": "Pause To Beat The Creeper ⚔️", "script": "Ten hearts."})["top"] == "PAUSE NOW!"


def test_balls_thumbnail_cast():
    from modules import bw_balls

    script = {"scenes": [
        {"who": "host", "with": ["Germany", "Brazil", "Russia", "China"], "narration": "Who has the most neighbors?"},
        {"who": "Germany", "expr": "happy", "badge": {"text": "9"}, "narration": "Nine."},
        {"who": "Brazil", "expr": "smug", "badge": {"text": "10"}, "narration": "Ten."},
        {"who": "Russia", "badge": {"text": "14"}, "narration": "Fourteen."},
        {"who": "China", "badge": {"text": "14"}, "narration": "Me too!"},
        {"who": "host", "with": ["Russia", "China"], "narration": "A tie!"}]}
    others, win = bw_balls.thumb_cast(script)
    assert [n for n, _, _ in others] == ["Germany", "Brazil"] and others[0][2] == "9"
    assert [n for n, _, _ in win] == ["Russia", "China"]
    script["scenes"][-1]["with"] = ["Germany", "Brazil", "Russia", "China"]  # everyone wins: all of them, no others
    others, win = bw_balls.thumb_cast(script)
    assert others == [] and len(win) == 3


def test_balls_badge_values_and_bars():
    from modules import bw_balls

    assert bw_balls.badge_value("1,100") == 1100 and bw_balls.badge_value("18K") == 18000
    assert bw_balls.badge_value("1.45B") == 1.45e9 and bw_balls.badge_value("100+") == 100
    assert bw_balls.badge_value("?") is None

    def turn(who, text, sub="km"):
        return {"who": who, "badge": {"text": text, "sub": sub, "at": 1.0}, "tower": None}

    turns = [{"who": "host", "badge": None, "tower": None}, turn("A", "25K"), turn("B", "202K")]
    bars = bw_balls.auto_bars(turns)
    assert [b["tower"]["height"] for b in bars] == [25000, 202000] and bars[0]["tower"]["shape"] == "bar"
    assert bw_balls.auto_bars([turn("A", "2002", ""), turn("B", "2011", "")]) == []  # years: no bars
    assert bw_balls.auto_bars([turn("A", "12")]) == []  # one number is no race
