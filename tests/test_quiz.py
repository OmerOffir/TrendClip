import random
from fractions import Fraction
from types import SimpleNamespace

from trendclip import quiz, script_writer, video_assembler as va
from trendclip.config import Settings
from trendclip.quiz import Flash, Step


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output")


def fake_client(answer, seen=None):
    class Models:
        def generate_content(self, model, contents, config):
            if seen is not None:
                seen["prompt"], seen["schema"] = contents[0], config.response_schema
            if isinstance(answer, Exception):
                raise answer
            return SimpleNamespace(parsed=answer, text="")
    return SimpleNamespace(models=Models())


def test_chains_are_whole_numbers_with_times_and_divide():
    for seed in range(300):
        start, chain = quiz.make_chain(random.Random(seed))
        assert len(chain) == 7
        assert {"×", "÷"} <= {s.op for s in chain}
        assert all(a.op != b.op for a, b in zip(chain, chain[1:]))
        exact = Fraction(start)
        for step in chain:
            exact = {"+": exact + step.value, "-": exact - step.value,
                     "×": exact * step.value, "÷": exact / step.value}[step.op]
            assert exact.denominator == 1 and exact > 0
        assert 5 <= exact <= 199 and quiz.solve(start, chain)[-1] == exact
        assert quiz.trap_answer(start, chain) != exact


def test_working_reads_like_the_pinned_comment():
    chain = [Step(op="+", value=18), Step(op="÷", value=2), Step(op="-", value=13)]
    assert quiz.working(24, chain) == "24 + 18 = 42 → ÷ 2 = 21 → − 13 = 8"


def test_math_quiz_script_flashes_and_answer(tmp_path):
    seen = {}
    pack = quiz.GeminiMathPack(hook="Only 5% of people finish this without pausing.", title="Only 5% Get This 🧠",
                               description="No pausing!", hashtags=["math", "#brainteaser"], title_card="don't pause")
    result = script_writer.write_script(make_settings(tmp_path), "Minecraft", mode="math",
                                        client=fake_client(pack, seen))
    assert seen["schema"] is quiz.GeminiMathPack and "answer" in seen["prompt"]
    assert result.mode == "math" and result.script.startswith("Only 5% of people finish this without pausing. Don't pause")
    assert "Start with" in result.script and result.script.endswith("Comment your answer right now!")
    assert result.end_card == quiz.END_CARD and result.title_card == "DON'T PAUSE"
    assert len(result.flashes) == 9 and result.flashes[-1].text == "?"
    assert result.flashes[1].text[0] in "+-×÷"
    assert result.answer and f"Answer: {result.answer}" in result.pinned_comment
    assert "→" in result.pinned_comment and "#shorts" in result.hashtags
    assert result.music_mood == quiz.MUSIC_MOOD and not result.popups and not result.reactions


def test_math_quiz_works_when_gemini_is_busy(tmp_path):
    result = quiz.write_quiz(make_settings(tmp_path), "GTA V", "math",
                             client=fake_client(script_writer.ScriptError("Gemini is overloaded")),
                             rng=random.Random(3))
    assert result.model == "built-in" and result.hook in quiz.MATH_HOOKS
    assert result.answer and result.flashes


def test_riddle_quiz(tmp_path):
    riddle = quiz.GeminiRiddle(
        hook="Only 5% solve this riddle on the first try.",
        clues=["I have a face, but I never smile.", "I have hands, but I can't hold anything."],
        question="What am I", answer="A clock.", explanation="A clock has a face and hands.",
        title="Only 5% Solve This 🧩", description="Comment your guess!", hashtags=["#riddle"], title_card="RIDDLE TIME")
    result = script_writer.write_script(make_settings(tmp_path), "Minecraft", mode="riddle", client=fake_client(riddle))
    assert "Listen carefully. I have a face" in result.script and "What am I?" in result.script
    assert [f.text for f in result.flashes] == ["RIDDLE", "CLUE 1", "CLUE 2", "?"]
    assert result.answer == "A clock" and result.pinned_comment.startswith("🧩 Answer: A clock.")


OWN_MATH = ("99% OF PEOPLE FAIL THIS PERCENTAGE TRAP! Start with 100. Take away 50%. Add 50% back. Multiply by 2. "
            "Minus 30. Divide by 3. Got your answer? If you got 100, you fell for the trap! What did you get? "
            "Hit that subscribe button for daily side quest stories!")


def test_fill_the_rest_for_a_math_quiz_keeps_it_a_quiz(tmp_path):
    seen = {}
    pack = quiz.GeminiOwnQuiz(answer="40", working="x", title="99% Fail This 🧠", description="Comment!",
                              hashtags=["#math"], title_card="PERCENT TRAP")
    result = script_writer.describe_script(make_settings(tmp_path), "Fortnite", OWN_MATH, mode="math",
                                           client=fake_client(pack, seen))
    assert "subscribe" not in result.script.lower() and "stories" not in result.script.lower()
    assert result.script.startswith("99% OF PEOPLE FAIL") and result.script.endswith(
        "What did you get? Comment your answer right now!")
    assert [f.text for f in result.flashes] == ["100", "-50%", "+50%", "×2", "-30", "÷3", "?"]
    assert result.answer == "40" and "The correct answer is 40" in seen["prompt"]
    assert "100 → − 50% = 50 → + 50% = 75 → × 2 = 150 → − 30 = 120 → ÷ 3 = 40" in result.pinned_comment
    assert result.end_card == quiz.END_CARD and result.mode == "math"


def test_own_quiz_keeps_its_own_comment_line_and_asks_gemini_when_unsolvable(tmp_path):
    riddle = "Only 5% get this. I speak without a mouth. What am I? Comment your guess below!"
    pack = quiz.GeminiOwnQuiz(answer="An echo.", working="An echo repeats sound.", title="t", description="d",
                              hashtags=[], title_card="RIDDLE")
    result = script_writer.describe_script(make_settings(tmp_path), "Minecraft", riddle, mode="riddle",
                                           client=fake_client(pack))
    assert result.script == riddle and result.answer == "An echo"
    assert [f.text for f in result.flashes] == ["?"] and result.pinned_comment.startswith("🧩 Answer: An echo.")


def test_flashes_follow_the_voice_and_repeated_numbers():
    words = [va.Word(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate(
        "Start with 12. Times 2. Plus 2. Divide by 2. Got your number? Comment your answer right now!".split())]
    flashes = [Flash(word="12", text="12"), Flash(word="2", text="×2"), Flash(word="2", text="+2"),
               Flash(word="2", text="÷2"), Flash(word="number", text="?")]
    timed = quiz.schedule_flashes(flashes, words)
    assert [t.text for t in timed] == ["12", "×2", "+2", "÷2", "?"]
    assert timed[1].start == round(words[4].start - 0.05, 3)  # "2." after "Times"
    assert all(a.end <= b.start for a, b in zip(timed, timed[1:]))
    assert timed[-1].end - timed[-1].start > quiz.FLASH_SECONDS


def test_quiz_voice_is_english_only_and_never_says_plus():
    assert quiz.english_voice("en-US-AndrewMultilingualNeural") == "en-US-AndrewNeural"
    assert quiz.english_voice("en-US-GuyNeural") == "en-US-GuyNeural"
    assert quiz.quiz_rate("+0%") == "+5%" and quiz.quiz_rate("+20%") == "+20%" and quiz.quiz_rate("") == "+5%"
    said = " ".join(t for options in quiz.SAY.values() for t in options).lower()
    assert "plus" not in said and "minus" not in said


def test_quiz_layout_pulses_the_hook_and_keeps_the_middle_band(tmp_path):
    words = [va.Word("Add", 0.0, 0.3), va.Word("18.", 0.3, 0.7)]
    path = va.create_karaoke_ass_file(words, tmp_path / "s.ass", title_card="NO PAUSING",
                                      flashes=[quiz.TimedFlash("+18", 0.25, 1.6)], **quiz.render_style(False))
    text = path.read_text(encoding="utf-8")
    assert r"\pos(540,826)" in text and text.count(r"\3c") > 4
    assert f",{quiz.CAPTION_MARGIN},1" in text  # karaoke style MarginV
    assert quiz.render_style(True) == {"title_pulse": True}


def test_flashes_are_drawn_in_the_subtitles(tmp_path):
    words = [va.Word("Add", 0.0, 0.3), va.Word("18.", 0.3, 0.7)]
    path = va.create_karaoke_ass_file(words, tmp_path / "s.ass", flashes=[quiz.TimedFlash("+18", 0.25, 1.6)])
    text = path.read_text(encoding="utf-8")
    assert "Style: Flash," in text and ",Flash,," in text and text.count("+18") == 1
