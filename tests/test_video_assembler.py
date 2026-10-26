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


def test_landscape_layout_moves_overlays_off_the_captions(tmp_path):
    popup = va.Overlay(tmp_path / "p.png", 1, 2)  # 9:16 pop-up spot (540, 800)
    cta = va.Overlay(tmp_path / "c.png", 1, 2, center=(540, 1490), box=400)  # below the 9:16 captions
    p, c = va.to_landscape(popup), va.to_landscape(cta)
    assert p.center == (960, 450) and p.box == round(va.POPUP_BOX * va.LANDSCAPE_OVERLAY_SCALE)
    assert c.center[0] > 1500 and c.center[1] < 700  # to the side, above the 16:9 captions
    assert "scale=1920:1080" in va.video_filter("crop", 30, va.LANDSCAPE)
    ass = va.create_karaoke_ass_file(PLAIN, tmp_path / "s.ass", size=va.LANDSCAPE, title_card="WHO CALLED ME AT 2AM",
                                     title_chars=26).read_text()
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass and "WHO CALLED ME AT 2AM" in ass


def test_landscape_render_for_real(tmp_path):
    import subprocess

    import pytest
    from PIL import Image

    try:
        ffmpeg = va.ffmpeg_with_libass()
    except va.AssemblyError:
        pytest.skip("no ffmpeg with libass")
    gen = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
    subprocess.run(gen + ["testsrc=size=640x360:rate=30:duration=2", "-pix_fmt", "yuv420p", str(tmp_path / "bg.mp4")], check=True)
    subprocess.run(gen + ["sine=frequency=300:duration=2", "-ac", "1", str(tmp_path / "voice.mp3")], check=True)
    Image.new("RGBA", (200, 200), (255, 0, 0, 255)).save(tmp_path / "p.png")
    out = va.assemble_video(tmp_path / "bg.mp4", tmp_path / "voice.mp3", [{"word": "hello", "start": 0, "end": 1.5}],
                            tmp_path / "out.mp4", preset="ultrafast", landscape=True, title_card="HELLO THERE",
                            overlays=[va.Overlay(tmp_path / "p.png", 0.2, 1.5),
                                      va.Overlay(tmp_path / "p.png", 0.5, 1.8, center=(540, 1490), box=400)])
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
    assert "1920x1080" in probe


def _render_with_voice(tmp_path, voice_seconds):
    import subprocess

    import pytest

    try:
        ffmpeg = va.ffmpeg_with_libass()
    except va.AssemblyError:
        pytest.skip("no ffmpeg with libass")
    gen = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
    subprocess.run(gen + ["testsrc=size=320x240:rate=30:duration=6", "-pix_fmt", "yuv420p", str(tmp_path / "bg.mp4")], check=True)
    subprocess.run(gen + [f"sine=frequency=300:duration={voice_seconds}", "-ac", "1", str(tmp_path / "voice.mp3")], check=True)
    return va.assemble_video(tmp_path / "bg.mp4", tmp_path / "voice.mp3", [{"word": "done", "start": 0.5, "end": 1.2}],
                             tmp_path / "out.mp4", preset="ultrafast")


def test_video_ends_a_beat_after_the_last_word(tmp_path):
    out = _render_with_voice(tmp_path, 5)
    assert abs(va.media_duration(out) - (1.2 + va.TAIL_SECONDS)) < 0.15  # not the 5 s of audio


def test_a_voice_that_stops_on_its_last_word_still_gets_the_tail(tmp_path):
    out = _render_with_voice(tmp_path, 1.25)
    assert abs(va.media_duration(out) - (1.2 + va.TAIL_SECONDS)) < 0.15


def test_video_filter_background_speed():
    assert "setpts" not in va.video_filter("crop", 30)
    assert va.video_filter("crop", 30, speed=1.25).startswith("[0:v]setpts=PTS/1.25,")
    assert "[0:v]setpts=PTS/1.5,split=2" in va.video_filter("blur", 30, speed=1.5)


def test_render_request_background_speed():
    from pydantic import ValidationError

    from trendclip.shorts import RenderRequest

    assert RenderRequest(clip="a.mp4", game="G", script="Hi.").background_speed == 1
    assert RenderRequest(clip="a.mp4", game="G", script="Hi.", background_speed=1.25).background_speed == 1.25
    with pytest.raises(ValidationError):
        RenderRequest(clip="a.mp4", game="G", script="Hi.", background_speed=2)


def test_assemble_rejects_an_audio_only_background(tmp_path):
    """The bot saved YouTube downloads that had only an AAC stream; ffmpeg then died on the whole
    filtergraph with "[0:v] ... matches no streams" and the 600-char tail of stderr hid the cause."""
    import subprocess

    try:
        ffmpeg = va.ffmpeg_with_libass()
    except va.AssemblyError:
        pytest.skip("no ffmpeg with libass")
    gen = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
    subprocess.run(gen + ["sine=frequency=300:duration=3", "-c:a", "aac", str(tmp_path / "bg.mp4")], check=True)
    subprocess.run(gen + ["sine=frequency=300:duration=2", "-ac", "1", str(tmp_path / "voice.mp3")], check=True)
    with pytest.raises(va.AssemblyError, match="no video stream"):
        va.assemble_video(tmp_path / "bg.mp4", tmp_path / "voice.mp3", [{"word": "hello", "start": 0, "end": 1.0}],
                          tmp_path / "out.mp4", preset="ultrafast")


def test_ffmpeg_error_excerpt_keeps_both_ends_of_a_long_line():
    graph = "[0:v]scale=1080:1920[base];" * 80
    stderr = f"Stream specifier ':v' in filtergraph description {graph} matches no streams.\n" \
             "Error initializing complex filters: Invalid argument\n"
    excerpt = va._ffmpeg_error_excerpt(stderr)
    assert excerpt.startswith("Stream specifier ':v'") and "matches no streams." in excerpt
    assert "Error initializing complex filters" in excerpt and len(excerpt) < 600


def test_flashes_never_run_off_the_frame(tmp_path):
    from trendclip.quiz import TimedFlash as Flash
    from trendclip.video_assembler import FLASH_MARGIN, FLASH_POP, fit_flash, text_width

    # Estimated widths (no font file): 0.62 × size per character.
    assert text_width("+18", 288, None) == 3 * 288 * 0.62
    limit = (1080 - 2 * FLASH_MARGIN) / FLASH_POP
    assert fit_flash("+18", 288, limit) == (288, ["+18"])  # fits: untouched
    size, lines = fit_flash("B) SURVIVAL", 288, limit)  # "SURVIVAL" alone is 1428 px at 288: shrink
    assert lines == ["B)", "SURVIVAL"] or lines == ["B) SURVIVAL"]
    assert size < 288 and text_width(max(lines, key=len), size, None) <= limit + 1
    size, lines = fit_flash("MOVIE LOGIC", 288, limit)  # two words, far too wide for one line: wrapped
    assert lines == ["MOVIE", "LOGIC"] and 250 < size <= 288  # "MOVIE" alone is a touch wide in the estimate
    size, lines = fit_flash("GOT IT?", 288, limit)  # nearly fits: a little smaller beats two lines
    assert lines == ["GOT IT?"] and size < 288 and text_width("GOT IT?", size, None) <= limit + 1

    size, lines = fit_flash("COMMENT YOUR ANSWER NOW", 288, limit)  # three or more words: two lines, shrunk if needed
    assert len(lines) == 2 and all(text_width(l, size, None) <= limit + 1 for l in lines)

    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("Which hero wins A or B comment".split())]
    ass = tmp_path / "s.ass"
    va.create_karaoke_ass_file(words, ass, title_card="99% FAIL THIS BATMAN MOVIE LOGIC", title_chars=16, title_size=148,
                               flash_y=0.39, flashes=[
                                   Flash(text="MOVIE LOGIC", start=0.2, end=1.5),
                                   Flash(text="B) SURVIVAL", start=6.0, end=7.0),
                                   Flash(text="?", start=7.0, end=8.0)])

    text = ass.read_text()
    first, second, third = [l for l in text.splitlines() if ",Flash,," in l]
    # During the title card (3 lines of 148 px) the first flash sits below the card, not over it.
    assert "MOVIE\\NLOGIC" in first and int(first.split("\\pos(540,")[1].split(")")[0]) > 749
    import re

    assert re.search(r"\\fs\d", second) and "SURVIVAL" in second and "\\pos(540,749)" in second  # shrunk, usual spot
    assert not re.search(r"\\fs\d", third) and third.endswith("?")

