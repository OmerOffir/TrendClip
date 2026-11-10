import json
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


SPOKEN = spin.SCRIPT.format(game="Minecraft")


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
