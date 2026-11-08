import json
import random

from PIL import Image, ImageDraw

from trendclip import reveal
from trendclip.config import Settings
from trendclip.video_assembler import Word


def settings(tmp_path) -> Settings:
    return Settings(youtube_api_key="x", output_dir=tmp_path / "out", assets_dir=tmp_path / "assets")


def library(s: Settings) -> None:
    folder = reveal.library_dir(s)
    folder.mkdir(parents=True)
    img = Image.new("RGBA", (200, 400), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 20, 160, 380], fill=(60, 180, 60, 255))
    d.rectangle([70, 60, 90, 80], fill=(0, 0, 0, 255))  # the face: busy, breaks last
    d.rectangle([110, 60, 130, 80], fill=(0, 0, 0, 255))
    d.point([(2, 2), (197, 397)], fill=(200, 0, 200, 255))  # loose specks
    img.save(folder / "creeper.png")
    (folder / "catalog.json").write_text(json.dumps([{"name": "Creeper", "kind": "mob", "file": "creeper.png",
                                                       "source": "x"}]))


SPOKEN = ("Minecraft fans only. Something is hiding behind these blocks. One breaks every half second. "
          "Guess it before you see it all. Still no idea? It's a mob. Three. Two. One.")


def words() -> list[Word]:
    return [Word(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate(SPOKEN.split())]


def test_the_blocks_break_every_half_second_and_never_all(tmp_path):
    s = settings(tmp_path)
    library(s)
    spoken = words()
    overlays = reveal.plan(s, "creeper.png", spoken, seed="t")
    assert overlays[0].start == 0 and overlays[-1].end > spoken[-1].end + 1.9  # covers the tail too
    assert all(a.end == b.start for a, b in zip(overlays, overlays[1:]))  # one card at a time, no gap
    assert all(o.cut and o.center == reveal.CENTER for o in overlays)
    picture = reveal._fit(s, "creeper.png")
    boxes = reveal.tiles(picture)
    assert 20 <= len(boxes) <= 50
    broken = [b for b in reveal.order(picture, boxes, random.Random(1))]
    face = next(i for i, b in enumerate(boxes) if b[0] <= picture.width * 0.45 <= b[2] and b[1] <= picture.height * 0.18 <= b[3])
    assert broken.index(face) > len(boxes) // 3  # the busy part is not among the first to go
    # Every card is a fresh PNG; the last still has about LEFT of the blocks standing.
    first, last = Image.open(overlays[0].image), Image.open(overlays[-1].image)
    assert first.size == last.size == (reveal.SIDE + 2 * reveal.FRAME, reveal.SIDE + reveal.STRIP + 3 * reveal.FRAME)


def test_a_reveal_level_needs_no_gemini_and_names_the_kind(tmp_path):
    s = settings(tmp_path)
    library(s)
    assert reveal.wanted("guess the picture please") and reveal.wanted("reveal") and not reveal.wanted("catch")
    result = reveal.script(s, "Minecraft", rng=random.Random(1), level=31)
    assert result.reveal == "creeper.png" and result.answer == "Creeper" and result.flashes == []
    assert result.script.endswith("It's a mob. Three. Two. One.")
    assert result.title == "Guess The Minecraft Mob | Level 31 🎮"
    assert result.title_card == "LEVEL 31 - WHAT IS IT?" and result.end_card == reveal.END_CARD
    assert "Creeper" not in result.pinned_comment and "which second" in result.pinned_comment.lower()


def test_picture_levels_take_turns_with_catch_and_questions(tmp_path):
    from trendclip import quiz

    s = settings(tmp_path)
    assert not reveal.wanted("", s, "Minecraft")  # no pictures yet
    library(s)
    assert reveal.wanted("", s, "Minecraft") and not reveal.wanted("", s, "GTA V")
    quiz._remember(s, "gamer", answer="Creeper", question="Guess the picture", game="Minecraft", shape=reveal.SHAPE, level="1")
    assert not reveal.wanted("", s, "Minecraft")
    for n in range(2, 2 + s.reveal_every - 1):
        quiz._remember(s, "gamer", answer="x", question="q", game="Minecraft", shape="number", level=str(n))
    assert reveal.wanted("", s, "Minecraft")
    assert not reveal.wanted("", s.model_copy(update={"reveal_every": 0}), "Minecraft")
