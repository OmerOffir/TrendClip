import pytest


from modules import bw_brand, bw_render
from modules.bw_render import Scene, Word


def script(**extra):
    return {"brand": True, "narration": "One. Two. Three.",
            "scenes": [{"narration": "One."}, {"narration": "Two.", **extra}, {"narration": "Three."}]}


def test_dialogue_without_lines_is_the_narration():
    assert bw_brand.dialogue(script()) == [("", "One. Two. Three.")]
    assert bw_brand.dialogue({**script(ask="Why?"), "brand": False}) == [("", "One. Two. Three.")]


def test_dialogue_puts_the_mascot_between_the_narrator_parts():
    assert bw_brand.dialogue(script(ask="Why?", react="Wow!")) == [
        ("", "One."), ("panda", "Why?"), ("", "Two."), ("panda", "Wow!"), ("", "Three.")]


def test_spec_keeps_the_mascot_words_out_of_the_captions():
    words = [Word("One.", 0, 0.5), Word("Why?", 0.9, 1.3, "panda"), Word("Two.", 1.7, 2.1),
             Word("Wow", 2.4, 2.7, "panda"), Word("now!", 2.7, 3.0, "panda")]
    spec = bw_render.spec_from_dict(script(ask="Why?"), words)
    assert [w.text for w in spec.words] == ["One.", "Two."]
    assert [[w.text for w in line] for line in spec.lines] == [["Why?"], ["Wow", "now!"]]
    assert spec.duration == 3.0 + bw_render.TAIL
    assert spec.line_at(1.0)[0].text == "Why?" and spec.line_at(2.2) is None


def test_moods_search_first_and_cheer_the_last_answer():
    scenes = [Scene("map", "Look here."), Scene("map", "It was found.", opts={"ask": "Why?"}),
              Scene("map", "But why?"), Scene("map", "Because.", opts={"ask": "How?"}), Scene("map", "x", opts={"mascot": "thinking"})]
    assert bw_brand.moods(scenes) == ["searching", "discovery", "thinking", "celebration", "thinking"]


def test_the_mascot_talks_while_it_says_a_line():
    words = [Word("One.", 0, 0.5), Word("Why?", 0.9, 1.3, "panda"), Word("Two.", 1.7, 2.1)]
    spec = bw_render.spec_from_dict(script(ask="Why?"), words)
    spec.scenes[0].start, spec.scenes[1].start = 0.0, 1.7
    assert bw_brand.mascot_mood(spec, ["searching", "discovery", "discovery"], 0, 1.0)[0] == "speaking"
    assert bw_brand.mascot_mood(spec, ["searching", "discovery", "discovery"], 0, 1.5) == ("searching", pytest.approx(1.4))


def test_wrap_keeps_rows_inside_the_width():
    f = bw_render.font(40)
    words = "Wait why would Germany want a skinny strip like that".split()
    rows = bw_brand.wrap(words, f, 300)
    assert sum(rows, []) == list(range(len(words)))
    assert all(f.getlength(" ".join(words[j] for j in r)) <= 300 or len(r) == 1 for r in rows)


def test_add_outro_once_and_only_with_the_brand():
    s = {**script(), "seed": 1}
    out = bw_brand.add_outro(s, long=True)
    assert out["scenes"][-1]["kind"] == "end" and out["scenes"][-1]["react"] == bw_brand.OUTROS[1]
    assert out["scenes"][-1]["hold"] == bw_brand.END_HOLD["long"]
    assert bw_brand.add_outro(out) is out
    assert bw_brand.add_outro({**s, "brand": False})["scenes"] == s["scenes"]
    assert bw_brand.dialogue(out)[-1] == ("panda", bw_brand.OUTROS[1])


def test_the_end_card_starts_with_the_subscribe_line_and_holds():
    s = bw_brand.add_outro(script(react="Wow!"))
    words = [Word("One.", 0, 0.5), Word("Two.", 0.7, 1.1), Word("Three.", 1.3, 1.8), Word("Wow!", 2.1, 2.5, "panda"),
             Word("Subscribe!", 3.1, 3.6, "panda")]
    spec = bw_render.spec_from_dict(s, words)
    assert len(spec.lines) == 2  # the reaction and the subscribe line are two bubbles
    bw_render.align(spec)
    assert spec.scenes[-1].start == pytest.approx(2.9)
    assert spec.duration == pytest.approx(3.6 + bw_brand.END_HOLD["short"])  # 0.5 s after its last word, no TAIL on top


def test_a_photo_scene_without_a_picture_falls_back():
    s = {"scenes": [{"narration": "a", "kind": "photo", "photo": "Nobody", "focus": ["Minnesota"]},
                    {"narration": "b", "kind": "photo", "photo": "Nobody", "caption": "A man"},
                    {"narration": "c", "kind": "photo", "photo": "Somebody"}]}
    spec = bw_render.spec_from_dict(s, [])
    bw_render.fetch_photos(spec, find=lambda q: "pic" if q == "Somebody" else None)
    assert [sc.kind for sc in spec.scenes] == ["map", "card", "photo"]
    assert spec.scenes[1].title == "A man" and spec.scenes[2].opts["_photo"] == "pic"


def test_photo_credits():
    from pathlib import Path

    from modules import bw_photos

    p = bw_photos.Photo(Path("x.jpg"), "File:Walleye (Sander vitreus).jpg", "https://commons.wikimedia.org/wiki/File:W.jpg",
                        "Some One", "CC BY-SA 4.0")
    text = bw_photos.credits([p, p])
    assert text.count("Walleye (Sander vitreus) by Some One (CC BY-SA 4.0)") == 1 and text.startswith("Photo: ")
    assert bw_photos.credits([]) == ""
    old = bw_photos.Photo(Path("y.jpg"), "File:Old map.jpg", "https://commons.wikimedia.org/wiki/File:Y.jpg", "Someone", "Public domain")
    assert bw_photos.credits([old]) == ""  # public domain needs no credit


@pytest.mark.parametrize("lines", [2, 3])
def test_a_portrait_board_keeps_clear_of_the_mascot(lines):
    """The writing stays above the mascot's head and the drawing right of it (the Kentucky Bend board, 2026-10-31)."""
    bw_render.use_layout("portrait")
    write = ["Border Rules", "West: Mississippi River", "South: Straight parallel", "East: Big Sandy"][:lines + 1]
    scene = Scene(kind="board", narration="a b c", start=0.0, end=6.0, opts={"board": {"write": write, "draw": ["Kentucky"]}})
    board = bw_brand.Board(scene)
    cx, feet = bw_brand.mascot_spot()
    head = feet - bw_brand.size("mascot")
    writes = [it for it in board.items if it["kind"] == "write"]
    assert len(writes) == lines + 1 and all(it["y"] + it["px"] / 2 <= head for it in writes)
    outline = [it for it in board.items if it["kind"] == "line" and len(it["pts"]) > 31]  # not the title's underline
    assert outline and all(it["pts"][:, 0].min() >= cx + bw_brand.size("mascot") / 2 for it in outline)
    assert all(it["pts"][:, 1].max() <= bw_render.CAPTION_Y - 200 for it in outline)  # above the bubble beside the mascot


def test_the_mascot_s_new_poses_follow_the_story():
    from modules import bw_brand, bw_fx, bw_render

    def scene(kind, narration, elements=()):
        return bw_render.Scene(kind, narration, elements=list(elements))

    scenes = [scene("map", "A village sits inside Switzerland."), scene("map", "Its border is close.", [bw_render.Element("highlight", "Italy")]),
              scene("board", "The deal of 1848."), scene("map", "Switzerland refused the offer."), scene("map", "So it stayed Italian."),
              scene("end", "")]
    assert bw_brand.moods(scenes) == ["searching", "pointing", "writing", "disagreement", "discovery", "greeting"]  # 4 % 3: in turn; bye on the end card
    assert set(bw_brand.MASCOT) >= {"approval", "laughing", "listening", "surprised", "thanks", "writing"}
    spec = bw_render.Spec(scenes, [])
    say = lambda text, at: [bw_render.Word(w, at + k * 0.3, at + k * 0.3 + 0.2, "panda") for k, w in enumerate(text.split())]
    scenes[-1].start = 20.0
    spec.lines = [say("Wait, it begged to join?", 3.0), say("Swiss neutrality strikes again!", 9.0), say("So that's the deal.", 12.0),
                  say("Subscribe for more borders!", 20.1)]
    assert [bw_brand.line_pose(spec, ln) for ln in spec.lines] == ["surprised", "laughing", "approval", "thanks"]
    names = bw_fx.icon_names()
    assert not {"explorer_nodding", "explorer_greeting"} & set(names) and "explorer" in names  # the panda is never a map icon
    assert {"astronomer", "librarian", "traveler", "route_planner", "politician", "sailor", "merchant", "queen"} <= set(names)
    assert "queen_curtsying" not in names and bw_fx.custom_icons()["queen_curtsying"].stem == "queen_waving"  # older scripts still draw
    assert all((bw_fx.CUSTOM_DIR / f"{f}.gif").is_file() for f in bw_brand.MASCOT.values())


def test_the_mascot_walks_along_a_route():
    from modules import bw_brand, bw_render

    scenes = [bw_render.Scene("map", "Look."), bw_render.Scene("map", "They sailed south.", elements=[bw_render.Element("arrow", "Spain")])]
    assert bw_brand.moods(scenes) == ["searching", "walking"]


def test_a_board_gets_a_character_while_the_words_are_written():
    """A board with room left gets one of the owner's characters (one its words call for) right after the title,
    and an arrow drawn to it after the words when there is time; a board without room or a short one has no arrow."""
    from modules import bw_fx

    bw_render.use_layout("portrait")
    scene = Scene("board", "The king signed a treaty.", start=0.0, end=9.0, opts={"board": {"write": ["The deal", "King signs"]}})
    board = bw_brand.Board(scene)
    g = board.guest_item
    assert g is not None and g["code"] == bw_fx.icon_code("scroll")
    title = board.items[0]
    assert g["at"] == pytest.approx(title["at"] + title["dur"] + 0.05)
    arrow = board.items[-1]
    assert arrow["kind"] == "line" and arrow.get("head") and arrow["at"] > g["at"]
    assert board.frame(g["at"] + 1.0).size == (bw_render.W, bw_render.H)
    short = Scene("board", "The king signed a treaty.", start=0.0, end=3.0, opts={"board": {"write": ["The deal", "King signs"]}})
    quick = bw_brand.Board(short)
    assert quick.guest_item is not None and not any(it.get("head") for it in quick.items)  # no time for the arrow
    assert bw_brand.Board(scene, guest=False).guest_item is None  # Price Tag Why's boards stay as they were
