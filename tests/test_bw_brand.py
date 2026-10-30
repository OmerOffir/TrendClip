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
    assert spec.duration == pytest.approx(3.6 + bw_render.TAIL + bw_brand.END_HOLD["short"])


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
    assert text.count("Walleye (Sander vitreus) by Some One (CC BY-SA 4.0)") == 1 and text.startswith("Pictures:")
    assert bw_photos.credits([]) == ""
