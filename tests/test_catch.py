"""Catch the Zombie: a Gamer IQ level where a drawn zombie pops up three times holding emoji."""

from __future__ import annotations

import random
from pathlib import Path

from trendclip import catch, popups, quiz
from trendclip.config import Settings
from trendclip.leaderboard import Video, judge
from trendclip.video_assembler import Overlay, Word, overlay_filter, render_pop_frames


def settings(tmp_path: Path, **kw) -> Settings:
    return Settings(output_dir=tmp_path / "out", assets_dir=tmp_path / "assets", stickers_dir=tmp_path / "stickers",
                    youtube_api_key="x", gemini_api_key="", **kw)


def words(text: str, start: float = 0.0, step: float = 0.3) -> list[Word]:
    out, t = [], start
    for w in text.split():
        out.append(Word(w, round(t, 2), round(t + step - 0.05, 2)))
        t += step
    return out


def test_a_catch_level_comes_up_on_the_game_once_the_last_levels_had_none(tmp_path):
    s = settings(tmp_path)
    assert catch.wanted(s, "Minecraft") and not catch.wanted(s, "GTA V / Online")
    assert catch.wanted(s, "GTA V / Online", notes="make a Catch the zombie one")
    quiz._remember(s, "gamer", shape="catch", level="1")
    assert not catch.wanted(s, "Minecraft")
    quiz._remember(s, "gamer", shape="number", level="2")
    assert not catch.wanted(s, "Minecraft")  # one level between is not enough with CATCH_EVERY=3
    quiz._remember(s, "gamer", shape="fake", level="3")
    assert catch.wanted(s, "Minecraft")
    assert not catch.wanted(settings(tmp_path, catch_every=0), "Minecraft")


def test_the_catch_script_holds_three_emoji_and_asks_for_them_at_the_end(tmp_path):
    s = settings(tmp_path)
    quiz._remember(s, "gamer", shape="number", level="1")
    result = catch.script(s, "Minecraft", rng=random.Random(3))
    assert len(result.catch) == 3 and len(set(result.catch)) == 3 and all(e in catch.EMOJI for e in result.catch)
    assert result.answer == " ".join(result.catch) and result.choices == []
    assert result.script.startswith("Minecraft fans only. A zombie is hiding in this video, three times,")
    assert result.script.endswith("What three emojis did the zombie hold? Comment all three below! Level 3 drops next. Subscribe so you don't miss it!")
    assert result.title == "Can You Catch The Zombie? | Level 2 🎮" and result.title_card == "LEVEL 2 - CATCH THE ZOMBIE"
    assert "All three = 10 leaderboard points. Level 3 drops next!" in result.pinned_comment
    assert not any(e in result.pinned_comment for e in result.catch)  # never the answer
    assert [f.text for f in result.flashes] == ["x3", "GOT ALL 3?"]
    assert len(result.script.split()) < 70  # about 23 s spoken: a level, not a lecture
    history = quiz.history(s, "gamer")
    assert history[-1]["shape"] == "catch" and history[-1]["answer"] == result.answer and quiz.gamer_level(s) == 3
    assert "Catch the zombie" not in " ".join(quiz.gamer_asked(s))  # not an "already asked" question for the LLM


def test_the_bot_makes_a_catch_level_without_gemini(tmp_path):
    s = settings(tmp_path)
    result = quiz.write_quiz(s, "Minecraft", "gamer", rng=random.Random(1), client=None)
    assert result.mode == "gamer" and result.catch and result.model == "builtin"


def test_the_zombie_pops_three_times_between_ready_and_the_question_shorter_each_time(tmp_path, monkeypatch):
    s = settings(tmp_path)
    monkeypatch.setattr(popups, "fetch_asset", lambda settings, popup, session=None: None)  # no network: no emoji
    spoken = words(catch.SCRIPT.format(game="Minecraft", next=2))
    pops = catch.plan(s, ["👺", "🍕", "🚀"], spoken, seed="x")
    ready = next(w.start for w in spoken if w.text == "Ready?")
    ask = next(w.start for w in spoken if w.text == "What")
    assert len(pops) == 3 and all(isinstance(p, Overlay) and p.cut for p in pops)
    assert [round(p.end - p.start, 3) for p in pops] == [round(h, 3) for h in catch.POPS]
    assert ready <= pops[0].start and pops[-1].end <= ask
    assert pops[0].end < pops[1].start < pops[1].end < pops[2].start  # apart, in order
    assert len({p.center for p in pops}) == 3  # three different places
    for p in pops:
        assert p.image.is_file() and catch.BOX // 2 <= p.center[0] and p.center[1] <= 1920 - catch.BOX // 2
    assert catch.plan(s, ["👺", "🍕", "🚀"], spoken, seed="x") == pops  # the same seed, the same moments
    assert catch.plan(s, [], spoken) == []


def test_a_cut_overlay_is_one_frame_held_with_no_fade(tmp_path):
    png = catch.draw_zombie(None, tmp_path / "z.png")
    ov = Overlay(png, 3.0, 3.13, center=(300, 500), box=440, cut=True)
    assert render_pop_frames(ov, tmp_path, "pop0") == "pop0_%03d.png"
    assert (tmp_path / "pop0_000.png").is_file() and not (tmp_path / "pop0_001.png").is_file()
    graph = overlay_filter(0, 3, ov, 30, "v0", "v1")
    assert "stop_duration=0.130" in graph and "fade=" not in graph and "x=300-w/2" in graph
    assert "fade=" in overlay_filter(0, 3, Overlay(png, 3.0, 5.0), 30, "v0", "v1")


def test_the_leaderboard_wants_all_three_emoji():
    v = Video("v", "t", kind="quiz", answer="👺 🍕 🚀")
    assert judge(v, "🚀🍕👺 easy") == "correct"
    assert judge(v, "👺 and 🍕, missed the last") == "wrong"
    assert judge(v, "🔥🔥 nice") == "comment"
    assert judge(v, "no idea") == "comment"
