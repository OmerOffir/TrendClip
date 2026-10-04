import re

import pytest

from trendclip import video_assembler as va

PLAIN = [
    {"word": "This", "start": 0.0, "end": 0.3},
    {"word": "trick", "start": 0.3, "end": 0.7},
    {"word": "is", "start": 0.7, "end": 0.8},
    {"word": "insane.", "start": 0.8, "end": 1.4},
    {"word": "Watch", "start": 1.8, "end": 2.1},
    {"word": "this!", "start": 2.1, "end": 2.6},
]


def test_load_word_timings_formats():
    assert va.load_word_timings(PLAIN)[3] == va.Word("insane.", 0.8, 1.4)

    whisper = {"segments": [{"words": [{"word": " Hi", "start": 0.5, "end": 0.9}]}]}
    assert va.load_word_timings(whisper) == [va.Word("Hi", 0.5, 0.9)]

    edge = [{"text": "Hey", "offset": 5_000_000, "duration": 3_000_000}]
    assert va.load_word_timings(edge) == [va.Word("Hey", 0.5, 0.8)]

    eleven = {"alignment": {
        "characters": list("Go now"),
        "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
    }}
    assert va.load_word_timings(eleven) == [va.Word("Go", 0.0, 0.2), va.Word("now", 0.3, 0.6)]

    with pytest.raises(va.AssemblyError):
        va.load_word_timings({"nope": 1})


def test_chunk_words_breaks_on_size_punctuation_and_pauses():
    chunks = va.chunk_words(va.load_word_timings(PLAIN))
    assert [[w.text for w in c] for c in chunks] == [["This", "trick", "is", "insane."], ["Watch", "this!"]]

    many = [va.Word(f"w{i}", i * 0.2, i * 0.2 + 0.15) for i in range(9)]
    sizes = [len(c) for c in va.chunk_words(many)]
    assert all(2 <= s <= 4 for s in sizes) and sum(sizes) == 9


def test_karaoke_ass_highlights_each_word_for_its_duration(tmp_path):
    path = va.create_karaoke_ass_file(PLAIN, tmp_path / "s.ass", highlight="cyan")
    text = path.read_text()
    assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
    style = next(line for line in text.splitlines() if line.startswith("Style:")).split(",")
    assert style[3] == "&H00FFFFFF" and style[5] == "&H00000000" and style[18] == "2"  # white, black outline, Alignment=2

    events = [line for line in text.splitlines() if line.startswith("Dialogue:")]
    assert len(events) == len(PLAIN)
    first = events[0].split(",", 9)
    assert first[1:3] == ["0:00:00.00", "0:00:00.30"]
    assert first[9].startswith("{\\c&H00FFFF00&") and "THIS{\\r} TRICK IS INSANE." in first[9]
    # Last word of a line is held a little, but never past the next line's start.
    assert events[3].split(",")[2] == "0:00:01.65"
    assert all(len(re.findall(r"\\c&H", e)) == 1 for e in events)


def test_colors_and_estimates():
    assert va._resolve_color("#FF8800") == "&H000088FF"
    with pytest.raises(va.AssemblyError):
        va._resolve_color("purple-ish")
    words = va.estimate_word_timings("Hello there, friend. Bye", 4.0)
    assert len(words) == 4 and words[0].start > 0 and words[-1].end <= 4.0
    assert all(a.end <= b.start for a, b in zip(words, words[1:]))


def test_assemble_reports_missing_inputs(tmp_path):
    with pytest.raises(va.AssemblyError, match="Background video not found"):
        va.assemble_video(tmp_path / "none.mp4", tmp_path / "v.mp3", PLAIN, tmp_path / "out.mp4")
