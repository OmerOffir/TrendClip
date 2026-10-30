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
