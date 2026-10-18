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
    assert result.mode == "math" and result.script.startswith("Start with ")  # the puzzle from the first word
    assert "5%" not in result.script and result.hook == "Only 5% of people finish this without pausing."
    assert "Don't pause" not in result.script and "made a mistake" not in result.script
    assert "Start with" in result.script and "Hint: it's " in result.script
    assert result.script.endswith("Comment your answer right now!")
    assert result.end_card == quiz.END_CARD and result.title_card == "ONLY 5% OF PEOPLE FINISH THIS"
    assert len(result.flashes) == 13 and [f.text for f in result.flashes[-5:]] == ["?", "3", "2", "1", "HINT"]
    assert "Got your number? Three. Two. One. Hint:" in result.script
    assert result.flashes[1].text[0] in "+-×÷"
    assert result.answer and result.answer not in result.pinned_comment and "→" not in result.pinned_comment
    assert result.pinned_comment.startswith("What number did you get? 👇") and "Hint:" in result.pinned_comment
    assert f"answer {result.answer}" in result.on_screen and "#shorts" in result.hashtags
    assert result.music_mood == quiz.MUSIC_MOOD and not result.popups and not result.reactions


def test_math_quiz_works_when_gemini_is_busy(tmp_path):
    result = quiz.write_quiz(make_settings(tmp_path), "GTA V", "math",
                             client=fake_client(script_writer.ScriptError("Gemini is overloaded")),
                             rng=random.Random(3))
    assert result.model == "built-in" and result.hook in quiz.MATH_HOOKS
    assert result.answer and result.flashes


def test_riddle_opens_on_the_paradox_and_counts_down(tmp_path):
    seen = {}
    riddle = quiz.GeminiRiddle(
        hook="What weighs nothing, but you can't hold it for ten minutes?",
        clues=["A strong man can't keep me in.", "You can see me on freezing days."],
        question="What am I", answer="Your breath.", explanation="Breath weighs nothing and fogs in the cold.",
        title="Can You Solve This? 🧩", description="Comment your guess!", hashtags=["#riddle"],
        title_card="WEIGHS NOTHING. CAN'T HOLD IT?", hint="you do it all day", bonus="What can you catch but never throw?")
    result = script_writer.write_script(make_settings(tmp_path), "Minecraft", mode="riddle",
                                        client=fake_client(riddle, seen))
    assert "only 3% solve this" in seen["prompt"]  # told not to open on a stat
    assert result.script.startswith("What weighs nothing, but you can't hold it for ten minutes? A strong man")
    assert result.script.endswith("What am I? Three. Two. One. Hint: you do it all day. Comment your answer right now!")
    assert result.title_card == "WEIGHS NOTHING. CAN'T HOLD IT?"
    assert [f.text for f in result.flashes] == ["RIDDLE", "CLUE 1", "CLUE 2", "?", "3", "2", "1", "HINT"]
    assert result.answer == "Your breath" and result.pinned_comment.startswith("What am I? 👇")
    assert "🔥 Bonus round: What can you catch but never throw? Reply with both answers!" in result.pinned_comment
    assert "breath" not in result.pinned_comment.lower()


def test_riddles_get_a_topic_and_avoid_used_answers(tmp_path):
    settings = make_settings(tmp_path)
    anchor = quiz.GeminiRiddle(
        hook="What do you throw away only when you need it?", clues=["Ships carry me.", "I sink on purpose."],
        question="What am I", answer="An anchor.", explanation="Thrown overboard to stop.", title="Riddle 🧩",
        description="Guess!", hashtags=["#riddle"], title_card="THROWN AWAY WHEN NEEDED?")
    prompts = []

    class Models:
        def generate_content(self, model, contents, config):
            prompts.append(contents[0])
            return SimpleNamespace(parsed=anchor, text="")

    client = SimpleNamespace(models=Models())
    quiz.write_quiz(settings, "Minecraft", "riddle", client=client, rng=random.Random(1))
    assert len(prompts) == 1 and "none yet" in prompts[0] and "This one's answer or setting: " in prompts[0]
    assert "throw away only when you need it" in prompts[0]  # named as an overused classic

    quiz.write_quiz(settings, "Minecraft", "riddle", client=client, rng=random.Random(2))
    assert len(prompts) == 3  # "the anchor" again: asked once more, then accepted
    assert "don't reuse these answers: An anchor." in prompts[1]
    assert quiz.used_riddles(settings) == ["An anchor."]
    topics = [d["topic"] for d in quiz.history(settings, "riddle")]
    assert len(topics) == 2 and topics[0] != topics[1] and all(t in quiz.RIDDLE_TOPICS for t in topics)


def test_used_quizzes_are_told_apart_and_titles_are_shown(tmp_path, monkeypatch):
    from trendclip import shorts

    def short(name, answer, script, flashes):
        return shorts.ShortVideo(filename=name, game="gta", title=f"Only 3% {name} 🧠", description="", hashtags=[],
                                 script=script, voice="", background="", answer=answer,
                                 render_settings={"flashes": [{"word": "x", "text": t} for t in flashes]})

    made = [short("math", "27", "Only 3% keep up. Start with 9. Got your number? Three. Two. One.", ["9", "?", "3"]),
            short("riddle", "A zipper", "I have teeth. What am I? Three. Two. One.", ["CLUE 1", "CLUE 2", "?"]),
            short("trivia", "a carpenter", "Gamers always miss this one. Was he a carpenter?", ["?", "A) Carpenter"]),
            short("paradox", "No single answer: it's the liar.", "Think fast!", ["PARADOX", "A) KEEP", "B) GO"])]
    monkeypatch.setattr(shorts, "list_shorts", lambda s: made)
    settings = make_settings(tmp_path)
    assert quiz.used_riddles(settings) == ["A zipper"]
    assert quiz.used_trivia(settings) == ["a carpenter"]
    assert quiz.used_paradoxes(settings) == ["the liar"]

    seen = {}
    quiz.write_quiz(settings, "GTA V", "math", client=fake_client(quiz.GeminiMathPack(
        hook="Bet you can't keep up.", title="Bet You Can't 🧠", description="d", hashtags=["#math"],
        title_card="BET YOU CAN'T"), seen))
    assert '"Only 3% math 🧠" / "Only 3% keep up."' in seen["prompt"] and "another kind of dare" in seen["prompt"]


def test_a_stat_dare_riddle_hook_is_replaced_by_the_first_clue(tmp_path):
    riddle = quiz.GeminiRiddle(
        hook="Only 5% solve this riddle on the first try.",
        clues=["I have a face, but I never smile.", "I have hands, but I can't hold anything."],
        question="What am I", answer="A clock.", explanation="A clock has a face and hands.",
        title="Only 5% Solve This 🧩", description="Comment your guess!", hashtags=["#riddle"],
        title_card="ONLY 5% SOLVE THIS", bonus="What is a clock's favourite food?")
    result = script_writer.write_script(make_settings(tmp_path), "Minecraft", mode="riddle", client=fake_client(riddle))
    assert result.script.startswith("I have a face, but I never smile. I have hands") and "5%" not in result.script
    assert result.title_card == "I HAVE A FACE, BUT I NEVER SMILE?"
    assert [f.text for f in result.flashes][:3] == ["RIDDLE", "CLUE 1", "?"]
    assert "clock" not in result.script.lower().split("hint:")[-1]
    assert "Bonus" not in result.pinned_comment  # it named the answer
    assert quiz.riddle_hook("What has a face but never smiles?", "A clock") == "What has a face but never smiles?"
    assert quiz.riddle_hook("What ticks but is not a clock?", "A clock") == ""


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
    assert "100 → − 50% = 50 → + 50% = 75 → × 2 = 150 → − 30 = 120 → ÷ 3 = 40" in result.on_screen
    assert result.pinned_comment.startswith("What number did you get? 👇") and "40" not in result.pinned_comment
    assert result.end_card == quiz.END_CARD and result.mode == "math"


def test_own_quiz_keeps_its_own_comment_line_and_asks_gemini_when_unsolvable(tmp_path):
    riddle = "Only 5% get this. I speak without a mouth. What am I? Comment your guess below!"
    pack = quiz.GeminiOwnQuiz(answer="An echo.", working="An echo repeats sound.", title="t", description="d",
                              hashtags=[], title_card="RIDDLE")
    result = script_writer.describe_script(make_settings(tmp_path), "Minecraft", riddle, mode="riddle",
                                           client=fake_client(pack))
    assert result.script == riddle and result.answer == "An echo"
    assert [f.text for f in result.flashes] == ["?"]
    assert result.pinned_comment.startswith("What do you think? 👇") and "echo" not in result.pinned_comment.lower()


def trivia_client(rounds, checks, seen=None):
    """Gemini that writes `rounds` (lists of candidates) and answers fact-checks with `checks` (texts)."""
    rounds, checks = list(rounds), list(checks)

    class Models:
        def generate_content(self, model, contents, config):
            if seen is not None:
                seen.setdefault("prompts", []).append(contents[0])
            if config.response_schema is None:
                assert config.tools  # the fact-check searches Google
                return SimpleNamespace(parsed=None, text=checks.pop(0))
            assert config.response_schema is quiz.GeminiTriviaSet
            return SimpleNamespace(parsed=quiz.GeminiTriviaSet(candidates=rounds.pop(0)), text="")
    return SimpleNamespace(models=Models())


def trivia(question="Which planet in our solar system is the hottest", trap="Mercury.", distractor="Mars",
           correct="Venus", **kw):
    return quiz.GeminiTrivia(
        category="space", question=question, trap=trap, distractor=distractor, correct=correct,
        fact=kw.pop("fact", "Venus averages about 465°C; Mercury about 167°C."), title="90% Get This Wrong 🌍",
        description="Comment the real answer!", hashtags=["#trivia"], title_card="90% get this trivia trap wrong! 🧠",
        **kw)


def test_trivia_trap(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(quiz, "TRIVIA_HOOKS", ("Only 5% get this right.",))
    result = script_writer.write_script(make_settings(tmp_path), "GTA V", mode="trivia",
                                        client=trivia_client([[trivia()]], ["1: PASS"], seen))
    assert "video games" in seen["prompts"][0] and "Venus" in seen["prompts"][1]
    assert result.script.startswith("Which planet in our solar system is the hottest? Is it ")  # no spoken dare
    assert result.hook == "Only 5% get this right."
    assert "Or is it " in result.script
    assert "Think fast! Three. Two. One. Hint:" in result.script and "you failed" not in result.script
    assert "Venus" not in result.script.split("One.")[-1]  # named as an option, never as the reveal
    assert result.script.endswith("Comment the real answer right now!")
    assert result.title_card == "90% GET THIS TRIVIA TRAP WRONG!"  # no emoji: the subtitle font can't draw it
    assert result.answer == "Venus" and result.pinned_comment.startswith("Which planet in our solar system is the hottest? 👇")
    assert "Venus" not in result.pinned_comment and "Hint:" in result.pinned_comment
    assert [r.word for r in result.reactions] == ["Hint"] and result.reactions[0].mood == "suspicious"
    labels = [f.text for f in result.flashes]
    assert labels[0] == "?" and [l[:3] for l in labels[1:4]] == ["A) ", "B) ", "C) "]
    assert sorted(l[3:] for l in labels[1:4]) == ["Mars", "Mercury", "Venus"]
    assert labels[4:] == ["THINK FAST!", "3", "2", "1", "HINT", "?"]

    words = va.estimate_word_timings(result.script.replace("hottest?", "hottest Venus?"), 18)
    timed = quiz.schedule_flashes(result.flashes, words)
    assert [t.text for t in timed] == labels
    question_end = next(w.end for w in words if w.text.endswith("?"))
    assert timed[1].start >= question_end - 0.1  # an option named in the question can't trigger option A


def test_paradox_says_think_fast_once(tmp_path):
    twice = CROCODILE.model_copy(update={"dilemma": "Think fast! What should the crocodile do?"})
    result = quiz.write_quiz(make_settings(tmp_path), "Minecraft", "paradox", client=fake_client(twice))
    assert result.script.count("Think fast!") == 1 and "Think fast! What should the crocodile do?" in result.script


def test_fit_quiz_drops_the_optional_lines_in_order_until_it_fits():
    long = "word " * 22
    lines = [long, long, "Hint: x.", "Extra line."]  # 48 words
    assert quiz.fit_quiz(lines, "Extra line.", "Hint: x.") == [long, long, "Hint: x."]  # 46: fits
    assert quiz.fit_quiz([long, long, long], long) == [long, long]
    assert quiz.fit_quiz(["short"], "short") == ["short"]  # nothing to drop when it fits


def test_trivia_says_the_with_country_names(tmp_path):
    islands = trivia("Which country has the most islands", "Indonesia", "the Philippines", "Sweden",
                     fact="Sweden: 267,570.", hook="90% fall for this.")
    result = quiz.write_quiz(make_settings(tmp_path), "GTA V", "trivia", client=trivia_client([[islands]], ["1: PASS"]),
                             rng=random.Random(2))
    assert "Is it the Philippines?" in result.script or "Or is it the Philippines?" in result.script
    assert any(f.text.endswith(") Philippines") and f.word == "it the" for f in result.flashes)


def test_trivia_flash_labels_drop_the_article(tmp_path):
    organ = trivia("Which organ can regrow", "the brain", "the heart", "the liver")
    result = quiz.write_quiz(make_settings(tmp_path), "GTA V", "trivia",
                             client=trivia_client([[organ]], ["1: PASS"]), rng=random.Random(3))
    assert sorted(f.text[3:] for f in result.flashes[1:4]) == ["Brain", "Heart", "Liver"]
    assert "Is it the liver?" in result.script or "Or is it the liver?" in result.script


def test_trivia_uses_only_a_fact_checked_question(tmp_path):
    venom = trivia("Which creature has the deadliest venom", "Inland taipan", "Black mamba", "Box jellyfish",
                   fact="Box jellyfish kill fast.", hint="It can kill in minutes!")
    result = quiz.write_quiz(make_settings(tmp_path), "GTA V", "trivia", rng=random.Random(1),
                             client=trivia_client([[venom, trivia()]],
                                                  ["**1: FAIL**: 'deadliest' is ambiguous\n2: PASS: Venus is hottest"]))
    assert result.answer == "Venus"


def test_trivia_writes_again_when_every_candidate_fails(tmp_path):
    seen = {}
    good = trivia("Which country has the most islands", "Indonesia", "the Philippines", "Sweden",
                  hint="over 220,000 of them!")
    result = quiz.write_quiz(make_settings(tmp_path), "GTA V", "trivia", rng=random.Random(1),
                             client=trivia_client([[trivia()], [good]], ["1: FAIL: myth", "1: PASS"], seen))
    assert result.answer == "Sweden" and "Venus" in seen["prompts"][2]  # the rejected answer isn't asked for again
    assert "Hint: over 220,000 of them." in result.script and "!." not in result.pinned_comment


def test_trivia_gives_up_after_three_failed_rounds(tmp_path):
    import pytest

    client = trivia_client([[trivia()], [trivia(correct="Pluto")], [trivia(correct="Earth")]],
                           ["1: FAIL: x", "nothing useful", "1: FAIL: y"])
    with pytest.raises(script_writer.ScriptError, match="fact-check"):
        quiz.write_quiz(make_settings(tmp_path), "GTA V", "trivia", client=client, rng=random.Random(1))


CROCODILE = quiz.GeminiParadox(
    name="the crocodile dilemma", hook="Only 2% can escape this paradox.",
    setup=["A crocodile steals a child and promises to return him if the father can guess what it will do."],
    twist="The father guesses: 'You will NOT return my child!'", dilemma="What should the crocodile do",
    side_a="If he keeps the child, the father was right, so he must return him.",
    side_b="But if he returns the child, the father was wrong!", vote_a="keep", vote_b="Return!",
    debate="Did the father just outsmart the crocodile, or is the child trapped forever",
    resolution="Any choice contradicts the promise. It is a self-reference paradox like the liar.",
    title="This Paradox Breaks 98% of Brains", description="Pick a side!", hashtags=["#shorts #paradox #logic"],
    title_card="only 2% can solve this paradox")


def test_paradox_trap(tmp_path):
    seen = {}
    result = script_writer.write_script(make_settings(tmp_path), "Minecraft", mode="paradox",
                                        client=fake_client(CROCODILE, seen))
    assert seen["schema"] is quiz.GeminiParadox and "PARADOX TRAP" in seen["prompt"]
    assert result.mode == "paradox" and result.script.startswith("A crocodile steals")  # the story first
    assert "Think fast! What should the crocodile do? If he keeps" in result.script
    assert "return my child!' Think fast!" in result.script  # no "!'." after the quote
    # too long for 20 s with the debate: it stays in the pinned comment only
    assert result.script.endswith("the father was wrong! Comment keep or return right now!")
    assert result.title_card == "ONLY 2% CAN SOLVE THIS PARADOX" and result.title.endswith("🧠")
    assert result.hashtags[:3] == ["#shorts", "#paradox", "#logic"]
    assert result.pinned_comment.startswith("Did the father just outsmart the crocodile") and "Team KEEP or Team RETURN" in result.pinned_comment
    assert "crocodile dilemma" in result.on_screen and "self-reference" in result.on_screen
    assert [r.word for r in result.reactions] == ["wrong"] and result.reactions[0].mood == "shocked"
    labels = [f.text for f in result.flashes]
    assert labels == ["PARADOX", "THINK FAST!", "A) KEEP", "B) RETURN", "KEEP OR RETURN?"]

    timed = quiz.schedule_flashes(result.flashes, va.estimate_word_timings(result.script, 25))
    assert [t.text for t in timed] == labels
    assert timed[0].start < 0.5  # something moves at once
    assert all(a.start < b.start for a, b in zip(timed, timed[1:]))


def test_paradoxes_dont_repeat(tmp_path):
    settings = make_settings(tmp_path)
    prompts = []
    for _ in range(len(quiz.PARADOX_IDEAS) - 15):
        seen = {}
        quiz.write_quiz(settings, "Minecraft", "paradox", client=fake_client(CROCODILE, seen))
        prompts.append(seen["prompt"])
    ideas = [d["idea"] for d in quiz.paradox_history(settings)]
    assert len(set(ideas)) == len(ideas) == len(quiz.PARADOX_IDEAS) - 15  # no repeats in a long run
    assert all(f"on this paradox or dilemma: {i}." in p for i, p in zip(ideas, prompts))
    assert "none yet" in prompts[0] and "don't retell these: the crocodile dilemma." in prompts[1]
    assert quiz.used_paradoxes(settings) == ["the crocodile dilemma"]  # names are listed once


def test_paradox_with_the_same_word_for_both_sides_falls_back_to_a_and_b(tmp_path):
    same = CROCODILE.model_copy(update={"vote_a": "yes", "vote_b": "YES"})
    result = quiz.write_quiz(make_settings(tmp_path), "Minecraft", "paradox", client=fake_client(same))
    assert result.script.endswith("Comment a or b right now!") and result.flashes[-1].text == "A OR B?"


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
    style = quiz.render_style(False)
    assert style.pop("music_fade_in") == 0.0  # assemble_video's, not the subtitles'
    path = va.create_karaoke_ass_file(words, tmp_path / "s.ass", title_card="NO PAUSING",
                                      flashes=[quiz.TimedFlash("+18", 0.25, 1.6)], **style)
    text = path.read_text(encoding="utf-8")
    assert rf"\pos(540,{round(1920 * quiz.FLASH_Y)})" in text and text.count(r"\3c") > 4
    assert f",{quiz.CAPTION_MARGIN},1" in text  # karaoke style MarginV
    assert quiz.render_style(True) == {"title_pulse": True, "title_chars": 16, "title_size": 148, "music_fade_in": 0.0}
    assert "afade=t=in" not in va.music_filter(20, 0.15, 0.0) and "afade=t=in:d=0.80" in va.music_filter(20, 0.15)


def test_flashes_are_drawn_in_the_subtitles(tmp_path):
    words = [va.Word("Add", 0.0, 0.3), va.Word("18.", 0.3, 0.7)]
    path = va.create_karaoke_ass_file(words, tmp_path / "s.ass", flashes=[quiz.TimedFlash("+18", 0.25, 1.6)])
    text = path.read_text(encoding="utf-8")
    assert "Style: Flash," in text and ",Flash,," in text and text.count("+18") == 1


# --------------------------------------------------------------------------- movie quizzes


VADER = quiz.GeminiMovieQuote(
    movie="The Empire Strikes Back (1980)", hook="Almost everyone misquotes this.",
    question="What did Darth Vader actually say to Luke in Star Wars", trap="'Luke, I am your father.'",
    distractor="I am your father, Luke.", correct="No, I am your father.",
    fact='Vader says "No, I am your father"; he never says "Luke".', title="90% Misquote This Line 🎬",
    description="Comment A, B or C!", hashtags=["#shorts", "#movies", "#starwars"], title_card="you've been saying it wrong")

UP = quiz.GeminiMovieEmoji(
    movie="Up", year="2009",
    emojis=[quiz.GeminiMovieEmoji.Pic(emoji="🎈", line="Balloons, hundreds of colorful balloons.", word="Balloons"),
            quiz.GeminiMovieEmoji.Pic(emoji="🏠", line="A little wooden house floating away.", word="house"),
            quiz.GeminiMovieEmoji.Pic(emoji="👴", line="A grumpy old man with a cane.", word="man")],
    hint="It starts with a flying house!", title="Guess The Movie From 3 Emojis", description="Comment the movie!",
    hashtags=["#shorts", "#guessthemovie"], title_card="guess the movie in 5 seconds")

MARTY = CROCODILE.model_copy(update={
    "name": "Back to the Future (1985)", "hook": "Movie fans, this one breaks brains.",
    "setup": ["Marty McFly goes back to 1955 and stops his parents from meeting."],
    "twist": "His photo starts to fade.", "dilemma": "Does he vanish right away",
    "side_a": "If he vanishes at once, nobody is left to fix it.",
    "side_b": "But if he has time, the past isn't really changed yet!", "vote_a": "vanish", "vote_b": "time",
    "debate": "Does Marty disappear instantly, or does he get time to fix it"})


def movie_client(rounds=(), checks=(), emoji=None, logic=None, seen=None):
    rounds, checks = list(rounds), list(checks)

    class Models:
        def generate_content(self, model, contents, config):
            if seen is not None:
                seen.setdefault("prompts", []).append(contents[0])
            if config.response_schema is None:
                assert config.tools  # the line is checked on Google
                return SimpleNamespace(parsed=None, text=checks.pop(0))
            schema = config.response_schema
            parsed = {quiz.GeminiMovieQuoteSet: lambda: quiz.GeminiMovieQuoteSet(candidates=rounds.pop(0)),
                      quiz.GeminiMovieEmoji: lambda: emoji, quiz.GeminiParadox: lambda: logic}[schema]()
            return SimpleNamespace(parsed=parsed, text="")
    return SimpleNamespace(models=Models())


def test_movie_misquote_trap_hides_the_answer_and_keeps_the_full_options(tmp_path):
    seen = {}
    result = quiz.write_quiz(make_settings(tmp_path), "GTA V", "movie", notes="a quote one", rng=random.Random(4),
                             client=movie_client([[VADER]], ["1: PASS"], seen=seen))
    assert result.mode == "movie" and "MISQUOTE" in seen["prompts"][0] and "No, I am your father" in seen["prompts"][1]
    assert result.script.startswith("What did Darth Vader actually say to Luke in Star Wars? Option A:")
    assert result.script.endswith("Three. Two. One. Comment A, B or C right now!")  # "Only one is..." didn't fit
    assert "real answer is" not in result.script.lower()
    assert sorted(c[3:] for c in result.choices) == ["I am your father, Luke.", "Luke, I am your father.",
                                                     "No, I am your father."]
    letter = result.answer[0]
    assert result.answer == f"{letter}) No, I am your father." and f"{letter}) No, I am your father." in result.choices
    assert "father" not in result.pinned_comment
    assert [f.text for f in result.flashes] == ["?", "A", "B", "C", "THINK FAST!", "3", "2", "1", "A, B OR C?"]
    assert quiz.used_movies(make_settings(tmp_path)) == ["The Empire Strikes Back (1980)"]


def test_movie_lines_that_fail_the_google_check_are_not_used(tmp_path):
    import pytest

    client = movie_client([[VADER], [VADER], [VADER]], ["1: FAIL: x", "1: FAIL: y", "1: FAIL: z"])
    with pytest.raises(script_writer.ScriptError, match="Google"):
        quiz.write_quiz(make_settings(tmp_path), "GTA V", "movie", notes="quote", client=client, rng=random.Random(1))


def test_guess_the_movie_from_emojis_pops_them_up_and_hides_the_title(tmp_path):
    result = quiz.write_quiz(make_settings(tmp_path), "Minecraft", "movie", notes="emoji please",
                             client=movie_client(emoji=UP), rng=random.Random(1))
    assert result.script.split("!")[0] + "!" in quiz.EMOJI_HOOKS
    assert "seconds! Emoji one: Balloons" in result.script  # "Three emojis, one famous movie." didn't fit
    assert [f.text for f in result.flashes][0] == "5 SECONDS!"
    assert "Hint: It starts with a flying house! Three. Two. One. Comment the movie right now!" in result.script
    assert [(p.word, p.emoji) for p in result.popups] == [("Balloons", "🎈"), ("house", "🏠"), ("man", "👴")]
    assert result.answer == "Up" and result.title_card == "GUESS THE MOVIE IN 5 SECONDS"
    assert "Up" not in result.pinned_comment.split()

    words = va.estimate_word_timings(result.script, 20)
    from trendclip import popups
    assert len(popups.schedule(result.popups, words)) == 3  # far enough apart that all three pop up


def test_an_emoji_line_that_names_the_movie_is_refused(tmp_path):
    import pytest

    bad = UP.model_copy(update={"movie": "Toy Story", "emojis": [
        quiz.GeminiMovieEmoji.Pic(emoji="🤠", line="A toy cowboy named Woody.", word="cowboy"), *UP.emojis[1:]]})
    with pytest.raises(script_writer.ScriptError, match="gave the movie away"):
        quiz.write_quiz(make_settings(tmp_path), "Minecraft", "movie", notes="emoji",
                        client=movie_client(emoji=bad), rng=random.Random(1))


def test_movie_logic_debate_is_a_two_sided_vote(tmp_path):
    seen = {}
    result = quiz.write_quiz(make_settings(tmp_path), "Minecraft", "movie", notes="movie logic",
                             client=movie_client(logic=MARTY, seen=seen), rng=random.Random(1))
    assert "MOVIE LOGIC DEBATE" in seen["prompts"][0] and result.mode == "movie"
    assert result.answer == "No single answer: it's movie logic in Back to the Future (1985)."
    assert result.flashes[0].text == "MOVIE LOGIC" and result.script.endswith("Comment vanish or time right now!")
    assert quiz.paradox_history(make_settings(tmp_path)) == []  # a movie, not a paradox

    long = MARTY.model_copy(update={"name": "Batman Begins (2005) - The Doing vs. Allowing Harm Dilemma"})
    result = quiz.write_quiz(make_settings(tmp_path), "Minecraft", "movie", notes="movie logic",
                             client=movie_client(logic=long), rng=random.Random(1))
    assert result.answer == "No single answer: it's movie logic in Batman Begins (2005)."


def test_movie_shapes_take_turns(tmp_path):
    settings = make_settings(tmp_path)
    quiz._remember(settings, "movie", shape="quote", movie="A")
    quiz._remember(settings, "movie", shape="emoji", movie="B")
    assert quiz.pick_movie_shape(settings, random.Random(1)) == "logic"
    assert quiz.pick_movie_shape(settings, random.Random(1), "do an EMOJI one") == "emoji"


def test_the_leaderboard_reads_the_full_options_of_a_movie_quiz():
    from trendclip import leaderboard, shorts

    short = shorts.ShortVideo(filename="m.mp4", game="g", title="t", description="", hashtags=[], script="", voice="",
                              background="", answer="B) No, I am your father.",
                              render_settings={"flashes": [{"word": "Option A", "text": "A"}],
                                               "choices": ["A) Luke, I am your father.", "B) No, I am your father.",
                                                           "C) I am your father, Luke."]})
    kind, answer, choices = leaderboard._from_short(short)
    video = leaderboard.Video("v", "t", kind=kind, answer=answer, choices=choices)
    assert kind == "quiz" and len(choices) == 3
    assert leaderboard.judge(video, "B") == "correct"
    assert leaderboard.judge(video, "it's 'No, I am your father'") == "correct"
    assert leaderboard.judge(video, "A for sure") == "wrong"
