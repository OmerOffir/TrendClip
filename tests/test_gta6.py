import json
import random

from modules import gta6, pw_channel
from trendclip import spin


def test_kinds_read_off_the_narration():
    for kind in ["ride", "crew", "land-banshee", "land-lucia", "land-jason", "land-jet"]:
        words = gta6.words_for(kind, random.Random(1))
        assert gta6.kind_of(words["script"]) == "gta-" + kind
        assert spin.kind_of(words["script"]) == "gta-" + kind  # the spin engine hands it to gta6.plan
        assert "GTA 6" in words["title"] and words["title"].split()[-1] in ("🚁", "😈", "😤🔥")
    assert gta6.kind_of("Minecraft fans only. Pause this video on the diamond block.") == ""
    assert spin.kind_of("Minecraft fans only. Pause this video on the diamond block.") == "land-diamond"


def test_every_tier_and_target_is_there():
    for name, s in gta6.SETS.items():
        assert {t for t, _ in s["items"].values()} == set(gta6.TIERS), name
    for land in gta6.LANDS.values():
        assert land["target"] in gta6.SETS[land["set"]]["items"]


def test_queue_fills_in_rotation(tmp_path):
    made = [gta6.new_script(tmp_path) for _ in range(3)]
    sets = [json.loads(p.read_text())["set"] for p in made]
    assert sets == gta6.ROTATION[:3]
    script = json.loads(made[0].read_text())
    assert pw_channel.is_gta(script) and pw_channel.check_script(script) == []
    assert pw_channel.limits(script) == pw_channel.GTA_SECONDS
    assert pw_channel.check_script({"format": "gta", "set": "boats"}) != []


def test_card_sizes_stay_the_same_in_a_set(tmp_path):
    from PIL import Image

    pic = tmp_path / "a.jpg"
    Image.new("RGB", (300, 169), (200, 0, 0)).save(pic)
    cache: dict = {}
    for name, s in gta6.SETS.items():
        plain = gta6.card({"name": "X", "tier": "common", "path": str(pic)}, s["pic"], cache)
        noted = gta6.card({"name": "Y", "tier": "legendary", "path": str(pic)}, s["pic"], cache, notes=("A", "B"))
        assert plain.size == noted.size


def test_plan_shows_the_sets_to_come(tmp_path, monkeypatch):
    monkeypatch.setenv("PW_FORMAT", "gta")
    from datetime import datetime

    from trendclip.config import get_settings

    s = get_settings()
    queue = tmp_path / "queue"
    first = next(gta6.coming(queue))
    days = pw_channel.plan_days(s, datetime.now(s.tz), before=0, after=2, out_dir=tmp_path)
    rows = [r for d in days for r in d["slots"] if r["script"]]
    assert rows and rows[0]["script"]["title"] == first[1]
    made = json.loads(gta6.new_script(queue).read_text())
    assert (made["set"], made["title"]) == first  # the slot gets what the Plan showed
