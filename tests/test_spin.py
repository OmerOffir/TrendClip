import json
from pathlib import Path
import random

from PIL import Image

from trendclip import spin
from trendclip.config import Settings
from trendclip.video_assembler import Overlay, Word, render_pop_frames


def settings(tmp_path) -> Settings:
    return Settings(youtube_api_key="x", output_dir=tmp_path / "out", assets_dir=tmp_path / "assets")


def library(s: Settings) -> None:
    folder = spin.library_dir(s)
    folder.mkdir(parents=True)
    entries = []
    for i, name in enumerate(["Dirt", "Stone", "Iron Block", "Diamond Ore", "Beacon"]):
        Image.new("RGBA", (64, 64), (40 * i, 100, 200, 255)).save(folder / f"{i}.png")
        entries.append({"name": name, "kind": "block", "file": f"{i}.png", "source": "x"})
    (folder / "catalog.json").write_text(json.dumps(entries))


SPOKEN = spin.SCRIPT.format(game="Minecraft", **spin.MYTHIC_FILL)


def words() -> list[Word]:
    return [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(SPOKEN.split())]


def test_the_deck_keeps_the_tiers_shares_and_never_repeats_a_block(tmp_path):
    s = settings(tmp_path)
    library(s)
    cards = spin.deck(spin.catalog(s), 2000, random.Random(3))
    assert all(a["name"] != b["name"] for a, b in zip(cards, cards[1:]))
    gold = sum(spin.BLOCKS[c["name"]][0] == "legendary" for c in cards) / len(cards)
    assert 0.04 < gold < 0.10


def test_one_card_every_two_frames_for_the_whole_video(tmp_path):
    s = settings(tmp_path)
    library(s)
    spoken = words()
    [overlay] = spin.plan(s, spoken, seed="t")
    assert overlay.start == 0 and overlay.end > spoken[-1].end + 2 and overlay.cut and overlay.animated
    with Image.open(overlay.image) as webp:
        assert webp.n_frames >= (spoken[-1].end + 2) * 30 / spin.HOLD
        assert webp.size == (spin.W, spin.H)
    short = Overlay(overlay.image, 0.0, 1.0, center=overlay.center, box=overlay.box, cut=True, animated=True)
    pattern = render_pop_frames(short, tmp_path, "pop0")
    frames = sorted(tmp_path.glob("pop0_*.png"))
    assert pattern == "pop0_%04d.png" and len(frames) == 30  # one PNG per video frame, no pop-in
    sizes = {Image.open(f).size for f in frames}
    assert len(sizes) == 1


def test_a_spin_short_has_no_answer_and_asks_to_share(tmp_path):
    s = settings(tmp_path)
    library(s)
    result = spin.script(s, "Minecraft")
    assert result.spin and result.answer == "" and result.flashes == []
    assert result.title == "Pause To Get Your Minecraft Block 🎮"
    assert "friend" in result.script and "friend" in result.pinned_comment
    assert spin.wanted("spin") and spin.wanted("Pause to get your block") and not spin.wanted("catch")


def test_the_create_tabs_choice_beats_the_rotation(tmp_path, monkeypatch):
    from trendclip import quiz, reveal

    s = settings(tmp_path).model_copy(update={"catch_every": 1})  # the zombie's turn every time
    library(s)
    folder = reveal.library_dir(s)
    folder.mkdir(parents=True)
    Image.new("RGBA", (64, 64), (60, 180, 60, 255)).save(folder / "creeper.png")
    (folder / "catalog.json").write_text(json.dumps([{"name": "Creeper", "kind": "mob", "file": "creeper.png",
                                                      "source": "x"}]))
    write = lambda notes: quiz._write_gamer(s, "Minecraft", notes=notes, format="short",  # noqa: E731
                                            progress=lambda *a: None, client=None, rng=random.Random(1))
    assert write("pause to get your block").spin
    assert write("guess the picture").reveal == "creeper.png"
    assert write("").catch  # Automatic: the rotation's zombie
    from trendclip import gamer_queue

    class Question(Exception):
        pass

    def reached(*a, **k):
        raise Question

    monkeypatch.setattr(gamer_queue, "take", reached)  # the question path's first step
    try:
        write(quiz.QUESTION_ONLY)
        raise AssertionError("not a question")
    except Question:
        pass


def axe_library(s: Settings) -> None:
    library(s)
    folder = spin.axe_dir(s)
    folder.mkdir(parents=True)
    entries = []
    for i, name in enumerate(list(spin.AXES) + ["Bedrock", "Chest"]):
        Image.new("RGBA", (16, 16), (30 * i, 80, 120, 255)).save(folder / f"a{i}.png")
        entries.append({"name": name, "kind": "axe", "file": f"a{i}.png", "source": "x"})
    (folder / "catalog.json").write_text(json.dumps(entries))


def test_break_times_follow_the_wikis_formula():
    assert spin.break_seconds("Oak Log", "Wooden Axe") == 1.5
    assert spin.break_seconds("Oak Log", "Netherite Axe") == 0.35
    assert spin.break_seconds("Oak Log", "Golden Axe") == 0.25  # gold is the fastest on wood
    assert spin.break_seconds("Obsidian", "Netherite Axe") == 250.0  # an axe isn't a pickaxe
    assert spin.break_seconds("Stone", "Diamond Axe") == 7.5
    assert spin.break_seconds("Dirt", "Wooden Axe") == 0.75
    assert spin.break_seconds("TNT", "Stone Axe") == 0.0
    assert spin.break_seconds("Bedrock", "Netherite Axe") is None
    assert spin.verdict(None)[0] == "NEVER BREAKS"
    assert spin.verdict(250.0)[0] == "TAKES 4 MIN 10 S"
    assert spin.verdict(0.35)[0] == "BREAKS IN 0.35 SECONDS"


def test_an_axe_short_flicks_axe_and_wall_cards(tmp_path):
    s = settings(tmp_path)
    axe_library(s)
    found = spin.axe_catalog(s)
    assert len(found["axes"]) == 6 and {w["name"] for w in found["walls"]} >= {"Bedrock", "Chest", "Dirt"}
    pairs = spin.axe_deck(found, 3000, random.Random(2))
    assert all(a[1]["name"] != b[1]["name"] for a, b in zip(pairs, pairs[1:]))
    rare = sum(p[0]["name"] == "Netherite Axe" for p in pairs) / len(pairs)
    assert 0.02 < rare < 0.07
    result = spin.script(s, "Minecraft", kind="axe")
    assert result.spin and result.title == "Pause To Get Your Minecraft Axe 🪓"
    assert spin.kind_of(result.script) == "axe" and spin.kind_of(SPOKEN) == "block"
    spoken = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(result.script.split())]
    [overlay] = spin.plan(s, spoken, seed="axe", kind="axe")
    with Image.open(overlay.image) as webp:
        assert webp.size == (spin.AW, spin.AH)
    assert spin.wanted("pause to get your axe") and spin.axe_wanted("pause to get your axe")


def test_hits_follow_the_wikis_damage_health_and_armour():
    assert spin.hits_to_kill("Warden", "Netherite Axe") == 50
    assert spin.hits_to_kill("Warden", "Wooden Spear") == 500
    assert spin.hits_to_kill("Wither", "Netherite Axe") == 31  # 10 less 4 armour's 3.2 %: 300 / 9.68
    assert spin.hits_to_kill("Iron Golem", "Diamond Pickaxe") == 20
    assert spin.hits_to_kill("Wither", "Bow") is None and spin.hits_to_kill("Ender Dragon", "Bow") == 34
    assert spin.weapon_verdict("Warden", "Netherite Axe") == ("50 HITS TO WIN", (40, 170, 70))
    assert spin.weapon_verdict("Warden", "Wooden Spear")[1] == (200, 40, 40)
    assert spin.weapon_verdict("Wither", "Bow")[0] == "ARROWS BOUNCE OFF"
    assert spin.weapon_verdict("Chicken", "Wooden Sword")[0] == "1 HIT TO WIN"  # any mob can come, even a chicken
    assert spin.weapon_verdict("Pig", "Bow")[0] == "2 SHOTS TO WIN"


def test_a_weapon_short_puts_one_boss_over_the_flicking_weapons(tmp_path):
    from trendclip import reveal

    s = settings(tmp_path)
    library(s)
    for folder, names in ((reveal.library_dir(s), ["Zombie", "Warden", "Ender Dragon", "Elder Guardian"]),
                          (spin.weapon_dir(s), list(spin.WEAPONS))):
        folder.mkdir(parents=True)
        entries = []
        for i, name in enumerate(names):
            Image.new("RGBA", (16, 16), (8 * i, 90, 140, 255)).save(folder / f"w{i}.png")
            entries.append({"name": name, "kind": "x", "file": f"w{i}.png", "source": "x"})
        (folder / "catalog.json").write_text(json.dumps(entries))
    found = spin.weapon_catalog(s)
    assert [b["name"] for b in found["bosses"]] == ["Warden", "Ender Dragon", "Elder Guardian", "Zombie"]
    picks = spin.weapon_deck(found, 4000, random.Random(4))
    legendary = sum(spin.WEAPONS[w["name"]][0] == "legendary" for w in picks) / len(picks)
    assert 0.04 < legendary < 0.10 and all(a["name"] != b["name"] for a, b in zip(picks, picks[1:]))
    seen = set()
    for seed in range(12):
        result = spin.script(s, "Minecraft", kind="weapon", rng=random.Random(seed))
        boss = spin.boss_of(result.script)
        seen.add(boss)
        assert result.title == f"Pause To Get Your Weapon vs The {boss} ⚔️" and spin.kind_of(result.script) == "weapon"
    assert "Elder Guardian" in seen and "An Elder Guardian is coming" in spin.WEAPON_SCRIPT.format(
        game="Minecraft", article=spin.article("Elder Guardian"), boss="Elder Guardian", **spin.MYTHIC_FILL)
    spoken = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(result.script.split())]
    [overlay] = spin.plan(s, spoken, seed="w", kind="weapon", script_text=result.script)
    assert overlay.center == spin.BOSS_CENTER
    with Image.open(overlay.image) as webp:
        assert webp.size == (spin.BW, spin.BH)
    assert spin.weapon_wanted("pause to get your weapon") and not spin.axe_wanted("pause to get your weapon")


def test_dungeon_numbers_and_parts():
    from trendclip import dungeon

    assert dungeon.break_seconds("Obsidian", "Diamond Pickaxe") == 9.4
    assert dungeon.break_seconds("Obsidian", "Iron Pickaxe") == 250.0  # too low a mining level
    assert dungeon.break_seconds("Obsidian", "Netherite Shovel") == 250.0
    assert dungeon.break_seconds("Cobblestone", "Golden Pickaxe") == 0.25
    assert dungeon.break_seconds("Cobblestone", "Copper Pickaxe") == 0.6
    assert dungeon.break_seconds("Cobblestone", "Diamond Axe") == 10.0
    assert spin.hits_to_kill("Enderman", "Bow") is None and spin.hits_to_kill("Zombie", "Iron Sword") == 4
    assert dungeon.loot_verdict("Totem of Undying")[0] == "LEGENDARY LOOT!!"
    assert dungeon.wanted("dungeon run part 3") == 3 and dungeon.wanted("Dungeon") == 1 and dungeon.wanted("") == 0
    assert dungeon.part_of("Minecraft dungeon run, part 7 of 8. An obsidian wall") == 7
    assert spin.kind_of("Minecraft dungeon run, part 7 of 8.") == "dungeon"


def test_every_dungeon_part_renders_its_map_and_room(tmp_path):
    from trendclip import dungeon, reveal

    s = settings(tmp_path)
    groups = {reveal.library_dir(s): ["Zombie", "Skeleton", "Enderman", "Wither"],
              spin.library_dir(s): ["Cobblestone", "Obsidian"], spin.axe_dir(s): ["Chest"],
              spin.weapon_dir(s): list(spin.WEAPONS),
              dungeon.dungeon_dir(s): list(dungeon.LOOT) + [t for t in dungeon.TOOLS if t.endswith("Shovel")]}
    for folder, names in groups.items():
        folder.mkdir(parents=True)
        entries = []
        for i, name in enumerate(names):
            Image.new("RGBA", (16, 16), (8 * i % 255, 90, 140, 255)).save(folder / f"d{i}.png")
            entries.append({"name": name, "kind": "x", "file": f"d{i}.png", "source": "x"})
        (folder / "catalog.json").write_text(json.dumps(entries))
    assert dungeon.ready(s)
    for part in range(1, dungeon.PARTS + 1):
        result = dungeon.script(s, "Minecraft", part)
        assert spin.kind_of(result.script) == "dungeon" and dungeon.part_of(result.script) == part
        assert f"{part}/8" in result.title and result.spin
        spoken = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(result.script.split())]
        the_map, cards = spin.plan(s, spoken, seed=f"p{part}", kind="dungeon", script_text=result.script)
        assert the_map.start == 0 and the_map.end == cards.start and cards.animated
        assert abs(the_map.end - (spoken[6].end + 0.25)) < 1e-6  # "...part 1 of 8." ends on the 7th word
    assert "is next" in dungeon.script(s, "Minecraft", 1).pinned_comment
    assert "Wither" in dungeon.script(s, "Minecraft", 8).pinned_comment


def test_every_dungeon_part_ends_on_share_and_follow():
    from trendclip import dungeon

    for part in range(1, dungeon.PARTS + 1):
        kind, what = dungeon.ROOMS[part - 1]
        text = dungeon.NARRATION[kind].format(game="Minecraft", n=part, total=8, what=what, article="A",
                                              next=part + 1, follow="Follow.", **spin.MYTHIC_FILL)
        assert "send this to a friend" in text and text.endswith("Follow.")


OPTIONS = (Path(__file__).parents[1] / "trendclip" / "web" / "static" / "index.html").read_text(encoding="utf-8")


def prizes(s: Settings, kind: str) -> None:
    folder = spin.prize_dir(s, kind)
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for i, (name, _) in enumerate(spin.PRIZES[kind]["items"].items()):
        Image.new("RGBA", (16, 16), (8 * i, 100, 200, 255)).save(folder / f"{i}.png")
        entries.append({"name": name, "kind": kind, "file": f"{i}.png", "source": "x"})
    (folder / "catalog.json").write_text(json.dumps(entries))


def test_a_prize_short_keeps_the_block_shorts_shape(tmp_path):
    s = settings(tmp_path)
    for kind in spin.PRIZES:
        prizes(s, kind)
        result = spin.script(s, "Minecraft", kind=spin.PRIZE + kind)
        words = spin.prize_words(kind)
        assert result.spin and result.answer == ""
        assert result.title == words["title"] and spin.prize_of_title(result.title) == kind
        assert words["line"] in result.script and words["comment"] in result.script
        assert spin.MYTHIC_MARK not in result.script  # the winner had none: one prize, one word to comment
        assert spin.kind_of(result.script) == spin.PRIZE + kind and not spin.tall(result.script)
        option = f"pause to {'get' if words['want'].startswith('get') else 'see'} {words['want'].removeprefix('get ')}"
        assert option in OPTIONS  # the Create tab's own choice
        assert spin.prize_wanted(option) == spin.PRIZE + kind and spin.wanted(option)
        spoken = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(result.script.split())]
        *strip, overlay = spin.plan(s, spoken, seed=kind, kind=spin.kind_of(result.script), script_text=result.script)
        assert overlay.start == 0 and overlay.center == spin.CENTER
        assert len(strip) == (kind in spin.STRIP)
        if strip:  # the hero strip under the captions, one picture per card
            from trendclip import prize_strip

            assert strip[0].center == prize_strip.CENTER and strip[0].animated
            with Image.open(strip[0].image) as a, Image.open(overlay.image) as b:
                assert a.n_frames == b.n_frames and a.size == prize_strip.SIZE
    assert spin.kind_of(SPOKEN) == "block" and spin.prize_wanted("pause to get your block") == ""
    assert spin.prize_wanted("pause to get your mob") == ""  # that is arena's mob-vs-boss


def test_every_prize_set_has_every_rarity_and_a_thumbnail_scene():
    from modules import sql_thumb

    for kind, prize in spin.PRIZES.items():
        assert set(prize["items"].values()) == set(spin.TIERS)
        assert prize["items"][prize["hero"]] == "legendary"  # the thumbnail says LEGENDARY under it
        assert set(prize["around"]) <= set(prize["items"]) and prize["look"]
        scene = sql_thumb.plan({"title": spin.prize_words(kind)["title"]})
        assert scene["kind"] == "blocks" and scene["hero"] == prize["hero"]
    assert spin.tier_of("Creeper", "pet") == "rare" and spin.tier_of("Creeper", "death") == "common"
    assert spin.prize_of_title("Pause To Get Your Minecraft Block 🎮") == ""


def lands(s: Settings, name: str) -> None:
    folder = spin.land_dir(s, name)
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for i, item in enumerate(spin.LANDS[name]["items"]):
        Image.new("RGBA", (16, 16), (8 * i, 100, 200, 255)).save(folder / f"l{i}.png")
        entries.append({"name": item, "kind": name, "file": f"l{i}.png", "source": "x"})
    (folder / "catalog.json").write_text(json.dumps(entries))


def test_a_land_short_is_one_target_ten_seconds_and_true_taunts(tmp_path):
    import random

    from modules import sql_thumb

    s = settings(tmp_path)
    for name, land in spin.LANDS.items():
        lands(s, name)
        result = spin.script(s, "Minecraft", kind=spin.LAND + name, rng=random.Random(1))
        assert result.spin and spin.kind_of(result.script) == spin.LAND + name and spin.has_strip(result.script)
        assert len(result.script.split()) <= 30  # about ten seconds with the tail
        assert spin.land_of_title(result.title) == name and spin.prize_of_title(result.title) == ""
        option = f"land the {land['target'].lower()}"
        assert option in OPTIONS and spin.land_wanted(option) == spin.LAND + name and spin.wanted(option)
        assert sql_thumb.plan({"title": result.title})["hero"] == land["target"]
        spoken = [Word(w, i * 0.3, i * 0.3 + 0.25) for i, w in enumerate(result.script.split())]
        strip, overlay = spin.plan(s, spoken, seed=name, kind=spin.kind_of(result.script), script_text=result.script)
        with Image.open(strip.image) as a, Image.open(overlay.image) as b:
            assert a.n_frames == b.n_frames
        dealt = spin.land_deck(spin.land_catalog(s, name), land["target"], 300, random.Random(name))
        hits = [i for i, (e, _, _) in enumerate(dealt) if e["name"] == land["target"]]
        assert 15 <= len(hits) and all(b - a >= 4 for a, b in zip(hits, hits[1:]))
        for i, (e, label, _) in enumerate(dealt):  # every taunt true to where the card is
            near = any(j in hits for j in (i - 1, i + 1))
            assert (label == "YOU LANDED IT!") == (i in hits) and (label == "ONE AWAY!") == (near and i not in hits)
