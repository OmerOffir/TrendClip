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
