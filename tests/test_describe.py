from types import SimpleNamespace

from trendclip import script_writer
from trendclip.config import Settings
from trendclip.popups import Popup
from trendclip.script_writer import GeminiExtras, GeminiSeriesExtras
from trendclip.stickers import Reaction

STORY = ("Part One: The Call\n"
         "At 2:17 a.m., Mara's phone rang. The caller said nothing, only breathing. "
         "Then a voice whispered her address. She checked the locks twice.\n"
         "Part Two: The Knock\n"
         "At 3 a.m. someone knocked on her door. It was the taxi driver from last night. "
         "He was holding her lost keys.")


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output")


def extras(**kw):
    base = dict(on_screen="A late-night call.", title="Who Called Me At 2AM?", description="It got weird.",
                hashtags=["#storytime", "#mystery"], title_card="WHO CALLED?",
                popups=[Popup(word="phone", emoji="📱", query="phone")],
                reactions=[Reaction(word="whispered", mood="scared")], music_mood="chill_lofi",
                question="Who was calling?", pinned_comment="Who do you think it was? 👀")
    return GeminiExtras(**{**base, **kw})


def fake(answer, seen, shorter=None):
    class Models:
        def generate_content(self, model, contents, config):
            if config.response_schema is script_writer.Shortened:
                seen["shorten"] = contents[0]
                return SimpleNamespace(parsed=script_writer.Shortened(script=shorter), text="")
            seen["prompt"], seen["schema"] = contents[0], config.response_schema
            seen["system"] = config.system_instruction
            return SimpleNamespace(parsed=answer, text="")
    return SimpleNamespace(models=Models())


def test_clean_own_script_drops_part_headings():
    text = script_writer.clean_own_script(STORY)
    assert "Part One" not in text and "Part Two" not in text and "The Knock" not in text
    assert text.startswith("At 2:17 a.m., Mara's phone rang.")


def test_describe_script_keeps_the_words(tmp_path):
    seen = {}
    short = script_writer.describe_script(make_settings(tmp_path), "GTA V", STORY,
                                          client=fake(extras(), seen))
    assert seen["schema"] is GeminiExtras and "do not change it" in seen["prompt"]
    assert "NEVER rewrite" in seen["system"]
    story = script_writer.clean_own_script(STORY)
    assert short.script.startswith(story)
    assert short.script.endswith("Who was calling? " + script_writer.follow_cta("@SideQuestLogic", story))
    assert short.question == "Who was calling?" and short.title == "Who Called Me At 2AM?"
    assert "#shorts" in short.hashtags and [p.word for p in short.popups] == ["phone"]
    assert [r.word for r in short.reactions] == ["whispered"]
    assert short.pinned_comment.startswith("Who do you think")


def test_describe_script_keeps_your_own_closing_question(tmp_path):
    seen = {}
    short = script_writer.describe_script(make_settings(tmp_path), "GTA V",
                                          "My cat opened the fridge. Then it ate my lunch. Would you forgive it?",
                                          client=fake(extras(question="Cat or dog?"), seen))
    assert short.question == "Would you forgive it?"
    assert short.script.count("?") == 1 and "Cat or dog" not in short.script


def test_describe_series_splits_your_story(tmp_path):
    seen = {}
    answer = GeminiSeriesExtras(story_name="the last passenger", part2_starts_with="At 3 a.m. someone knocked",
                                part1=extras(), part2=extras(title="It Was The Driver", title_card="THE KNOCK",
                                                             popups=[Popup(word="keys", emoji="🔑", query="keys")],
                                                             reactions=[]),
                                pinned_comment="Guess who knocked 👀", music_mood="chill_lofi")
    series = script_writer.describe_series(make_settings(tmp_path), "GTA V", STORY, "", handle="@SideQuestLogic",
                                           client=fake(answer, seen))
    assert seen["schema"] is GeminiSeriesExtras and "PART 2 is empty" in seen["prompt"]
    p1, p2 = series.parts
    assert p1.script.startswith("At 2:17 a.m.") and "She checked the locks twice." in p1.script
    assert "knocked" not in p1.script
    assert p1.script.endswith("Sub to Side Quest Logic for Part 2 dropping tomorrow!")
    assert p2.script.startswith("At 3 a.m. someone knocked") and "lost keys." in p2.script
    assert p1.title_card == "PART 1: WHO CALLED?" and p2.title == "It Was The Driver (Part 2)"
    assert series.story_name == "TheLastPassenger" and "@SideQuestLogic" in series.pinned_comment


def test_a_story_too_long_is_tightened_before_the_extras(tmp_path):
    seen = {}
    long_story = " ".join(f"Clue number {i} was on the floor." for i in range(40))  # 280 words
    shorter = "The phone rang at night. A voice whispered her address. She checked the locks twice."
    short = script_writer.describe_script(make_settings(tmp_path), "GTA V", long_story, target_seconds=30,
                                          client=fake(extras(), seen, shorter))
    assert "creator's own story" in seen["shorten"] and "AT MOST" in seen["shorten"]
    assert shorter in seen["prompt"] and "Clue number" not in seen["prompt"]
    assert short.script.startswith(shorter)


def test_a_story_that_fits_is_not_touched(tmp_path):
    seen = {}
    script_writer.describe_script(make_settings(tmp_path), "GTA V", STORY, target_seconds=45,
                                  client=fake(extras(), seen))
    assert "shorten" not in seen


def test_split_story_falls_back_to_the_middle():
    one, two = script_writer.split_story("One two. Three four. Five six. Seven eight.", "not in the story")
    assert one == "One two. Three four." and two == "Five six. Seven eight."
