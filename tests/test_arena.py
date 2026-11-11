import json
import random

from PIL import Image

from trendclip import arena, dungeon, reveal, spin
from trendclip.config import Settings
from trendclip.video_assembler import Word


def settings(tmp_path) -> Settings:
    return Settings(youtube_api_key="x", output_dir=tmp_path / "out", assets_dir=tmp_path / "assets")


def library(s: Settings) -> None:
    """Every picture the three Shorts need, as 16 px squares."""
    mobs = list(arena.ALLIES) + ["Creeper"]
    for folder, names in ((reveal.library_dir(s), mobs), (spin.weapon_dir(s), list(spin.WEAPONS)),
                          (spin.axe_dir(s), ["Chest", "Bedrock"]), (dungeon.dungeon_dir(s), list(dungeon.LOOT))):
        folder.mkdir(parents=True)
        entries = []
        for i, name in enumerate(names):
            Image.new("RGBA", (16, 16), (3 * i, 90, 140, 255)).save(folder / f"p{i}.png")
            entries.append({"name": name, "kind": "x", "file": f"p{i}.png", "source": "x"})
        (folder / "catalog.json").write_text(json.dumps(entries))


def spoken(text: str) -> list[Word]:
    return [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(text.split())]


def test_the_create_tabs_choices():
    assert arena.wanted("pause to fight your friend") == "friend"
    assert arena.wanted("pause to get your mob") == "pet"
    assert arena.wanted("pause twice") == "double"
    assert arena.wanted("pause to get your weapon") == "" and arena.wanted("") == ""


def test_more_damage_wins_the_friend_fight():
    assert arena.friend_verdict("Netherite Axe", "Wooden Sword")[0] == "YOU WIN! 10 VS 4 DAMAGE"
    assert arena.friend_verdict("Wooden Spear", "Trident")[0] == "FRIEND WINS! 9 VS 1 DAMAGE"
    assert arena.friend_verdict("Iron Sword", "Mace")[0].startswith("DRAW! 6 VS 6")


def test_your_mob_fights_hit_for_hit_and_strikes_first():
    assert arena.pet_fight("Zombie", "Chicken") == (None, 2)  # a chicken never attacks; 4 health, 3 damage
    assert arena.pet_verdict("Zombie", "Chicken")[0] == "CAN'T ATTACK: YOU LOSE"
    mine, theirs = arena.pet_fight("Zombie", "Warden")
    assert mine == 1 and theirs > 100 and arena.pet_verdict("Zombie", "Warden")[0] == "YOU WIN IN 1 HIT!"
    # a wolf (8 health, 4 damage) against a zombie (20 health, 2 armour, 3 damage): it falls on the third hit
    mine, theirs = arena.pet_fight("Zombie", "Wolf")
    assert theirs == 3 and mine > theirs and arena.pet_verdict("Zombie", "Wolf")[0].startswith("YOU LOSE")
    assert "ABOUT" in arena.pet_verdict("Ravager", "Iron Golem")[0]
    for boss in arena.PET_BOSSES:  # a win is rare, never hopeless and never the rule
        assert 0.05 <= arena.win_share(boss) <= 0.5, boss
        assert boss in {**spin.FOES, **spin.BOSSES}
    assert set(arena.ALLIES) <= set(spin.BOSSES)


def test_two_pauses_leave_one_to_seven_hearts():
    assert arena.loot_verdict("Golden Apple") == ("+2 HEARTS", arena.GREEN)
    assert arena.loot_verdict("Rotten Flesh")[0].startswith("-1 HEART")
    assert arena.loot_verdict("Coal")[0] == "NOT FOOD: NO HEARTS"
    for boss in ("Zombie", "Warden", "Pig", "Wither"):
        lost = {arena.hearts_lost(boss, w) for w in spin.WEAPONS}
        assert lost <= {1, 2, 3} and 1 in lost
    assert arena.hearts_lost("Wither", "Bow") == 3 and "ARROWS BOUNCE OFF" in arena.fight_verdict("Wither", "Bow")[0]
    assert arena.fight_verdict("Zombie", "Diamond Sword")[0] == "3 HITS: LOSE 1 HEART"
    low = arena.START_HEARTS + min(arena.GIVES.values()) - 3
    high = arena.START_HEARTS + max(arena.GIVES.values()) - 1
    assert (low, high) == (1, 7) and "7 hearts" in arena.DOUBLE_PINNED


def test_each_short_has_its_script_and_cards(tmp_path):
    s = settings(tmp_path)
    library(s)
    for kind, title in (("friend", "Pause To Fight Your Friend ⚔️"), ("pet", "Pause To Get Your Mob vs The "),
                        ("double", "Pause Twice To Beat The ")):
        result = arena.script(s, "Minecraft", kind, rng=random.Random(5))
        assert result.spin and result.title.startswith(title) and spin.kind_of(result.script) == kind
        assert spin.MYTHIC_MARK in result.script and "MYTHIC" in result.pinned_comment
        assert "friend" in result.script and spin.tall(result.script) == (kind != "friend")
        boss = arena.boss_of(result.script)
        assert (boss == "") == (kind == "friend") and boss in result.title
        overlays = spin.plan(s, spoken(result.script), seed=f"t-{kind}", kind=kind, script_text=result.script)
        assert len(overlays) == (2 if kind == "double" else 1) and overlays[0].start == 0
        assert all(o.animated and o.cut for o in overlays)
        with Image.open(overlays[-1].image) as webp:
            assert webp.size == ((spin.AW, spin.AH) if kind == "friend" else (spin.BW, spin.BH))
        if kind == "double":  # the chest until "Now a ... attacks", then the fight
            words = spoken(result.script)
            now = next(w for w in words if w.text == "Now")
            assert overlays[0].end == overlays[1].start == now.start


def test_one_card_of_the_video_is_the_mythic(tmp_path):
    s = settings(tmp_path)
    library(s)
    plain = [Image.new("RGBA", (spin.BW, spin.BH), (10, 10, 10, 255)) for _ in range(100)]
    assert spin.add_mythic(s, list(plain), "No such card here.", "seed", set()) is None
    cards = list(plain)
    name, second = spin.add_mythic(s, cards, "One frame hides a mythic card.", "seed", {"Mace", "Trident"})
    changed = [i for i, (a, b) in enumerate(zip(cards, plain)) if a is not b]
    assert len(changed) == 1 and 35 <= changed[0] < 85 and cards[changed[0]].size == plain[0].size
    assert name in spin.MYTHICS and name not in ("Mace", "Trident")
    assert second == round(changed[0] * spin.HOLD / 30, 2)
    again = list(plain)
    assert spin.add_mythic(s, again, "One frame hides a mythic card.", "seed", {"Mace", "Trident"}) == (name, second)
