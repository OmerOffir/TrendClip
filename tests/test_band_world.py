"""Encore Story's band stories (modules.band_world and the band format in pw_channel / pw_write)."""

import json
from pathlib import Path

import pytest

from modules import band_world, bw_render, pw_channel, pw_write

EXAMPLE = Path(__file__).resolve().parents[1] / "content" / "bandstory" / "examples" / "001-oasis.json"


def example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def fake_words(script: dict) -> list[bw_render.Word]:
    words, t = [], 0.0
    for scene in script["scenes"]:
        for w in scene["narration"].split():
            words.append(bw_render.Word(w, t, t + 0.25))
            t += 0.28
    return words


def test_the_oasis_example_passes_every_rule():
    s = example()
    assert pw_channel.is_band(s)
    assert pw_channel.check_script(s) == []
    assert pw_write.format_problems(s) == []
    assert pw_channel.kind_of(EXAMPLE) == "band"


def test_band_rules_catch_what_the_drawing_cannot_do():
    s = example()
    s["band"]["members"]["Liam"]["hair"] = "beehive"
    s["scenes"][1]["members"].append("Ringo")
    s["scenes"][-1]["narration"] = "They are back."
    s["scenes"][3]["big"]["cue"] = "nowhere"
    found = " | ".join(pw_channel.check_script(s) + pw_write.format_problems(s))
    assert "Ringo" in found and "hair=beehive" in found
    assert "question" in found and "'nowhere'" in found


def test_the_title_and_the_card_say_the_real_length():
    s = band_world.titled(example(), 41.2)
    assert s["title"] == "Oasis in 40 Seconds" and s["band"]["sub"] == "in 40 seconds"
    assert band_world.titled({"band": {"name": "QUEEN"}, "title": "Queen's story"}, 52)["title"] == "Queen in 50 Seconds"


@pytest.mark.parametrize("play", ["mic", "guitar", "bass", "drums", "none"])
def test_a_member_draws_with_every_instrument(play):
    look = {"hair": "mop", "glasses": "round", "top": "parka", "brows": "mono", "beard": "stubble", "guitar": "flag"}
    img = band_world.member(look, 1.3, play, "angry" if play == "none" else "smile", "cross", seed=3)
    assert img.size == (band_world.SW, band_world.SH) and img.getbbox()


def test_every_scene_draws_and_the_tags_show_once():
    s = example()
    spec = bw_render.spec_from_dict({**s, "look": "satellite"}, fake_words(s))
    bw_render.align(spec)
    r = band_world.BandRenderer(spec, s["band"])
    for scene in spec.scenes:
        assert r.frame((scene.start + scene.end) / 2).size == (bw_render.W, bw_render.H)
    tagged = [it["name"] for items in r.items for it in items if it["type"] == "member" and it["tag"]]
    assert sorted(tagged) == sorted(set(tagged)) and "Liam" in tagged
    assert any(name == "stamp" for _, name, _ in spec.sfx_extra)  # the year slams


def test_the_rock_bed_is_made_in_code(tmp_path):
    out = band_world.rock_bed(3.0, tmp_path / "bed.mp3", seed=2)
    assert out.is_file() and out.stat().st_size > 10_000


def test_the_description_comment_and_category_are_the_bands(tmp_path, monkeypatch):
    s = example()
    text = pw_channel.description(s)
    assert "Sources:" in text and "#music" in text and "financial" not in text.lower()
    assert pw_channel.comment_text(s).endswith(pw_channel.BAND_COMMENT)

    def record(text, out, progress=lambda m: None, voice="", speed=0.0, engine="", rate="", pause=0.0):
        assert voice == pw_channel.BAND_VOICE and engine == "edge" and pause == pw_channel.BAND_PAUSE
        out.write_bytes(b"")
        return fake_words(s)

    def render(spec, band, voice, out, music, volume, progress=lambda f, m: None):
        assert band["sub"].startswith("in ") and music.is_file()
        out.write_bytes(b"video")
        return out

    monkeypatch.setattr(pw_channel, "record", record)
    monkeypatch.setattr(band_world, "render", render)
    from modules import band_music

    monkeypatch.setattr(band_music, "bed", lambda seconds, out, style, seed=0: (out.write_bytes(b"m"), out)[1])
    info = pw_channel.make(s, "k1", tmp_path)
    assert info["category"] == pw_channel.BAND_CATEGORY and "music" in info["hashtags"]
    assert info["title"].startswith("Oasis in ") and info["title"].endswith("Seconds")
    assert (tmp_path / "k1" / "thumb.jpg").is_file()
    request = pw_channel.upload_request(info, "private")
    assert request.category_id == "10"


def test_the_band_prompts(tmp_path):
    prompt = pw_write.llm_prompt(5, found=[], format="band")
    assert "Encore Story" in prompt and '"format": "band"' in prompt
    session = {"kind": "one", "format": "band", "topics": [{"question": "Queen: how did a dentist's student become a rock god?"}]}
    text = pw_write.write_prompt(session)
    assert '"format": "band"' in text and "Queen" in text and "OASIS" in text
    item = pw_write.add_topic("Nirvana: how did a garage band from Aberdeen change rock?", "90s and 2000s", tmp_path, "band")
    assert item["format"] == "band"
    assert pw_write.view("week", tmp_path)["bands"][0]["question"].startswith("Nirvana")


def test_the_thumbnail_is_a_picture_of_the_whole_band(tmp_path):
    s = example()
    s["thumbnail"] = {"text": "BROTHERS AT WAR"}
    out = band_world.thumbnail(s, tmp_path / "thumb.jpg")
    from PIL import Image

    assert Image.open(out).size == (bw_render.W, bw_render.H)
    s["thumbnail"]["text"] = "X" * 40
    assert any("thumbnail" in p for p in pw_write.format_problems(s))


@pytest.mark.parametrize("key", ["hair", "face", "nose", "glasses", "beard", "hat", "top", "guitar"])
def test_every_look_value_draws(key):
    for value in band_world.LOOK_VALUES[key]:
        look = {key: value, "extras": list(band_world.EXTRAS)}
        img = band_world.member(look, 0.7, "guitar", "grin", "hang", seed=1)
        assert img.size == (band_world.SW, band_world.SH) and img.getbbox()


def test_the_blush_is_painted_over_the_skin_not_through_it():
    img = band_world.member({"hair": "short"}, 0.3, "none", "smile", "hang", 1)
    assert img.getpixel((180 - 76, 188 + 70 - 18 + band_world.OY))[3] == 255


def test_shots_props_poll_and_the_empty_mic_draw_and_pass_the_rules():
    s = example()
    sc = s["scenes"]
    sc[1]["shot"] = "close"
    sc[3]["props"] = [{"kind": "vinyl", "text": "OASIS", "cue": "fastest"}, {"kind": "trophy", "who": "Liam", "cue": "debut"}]
    sc[5]["empty_mic"] = True
    sc[6]["shot"] = "duo"
    sc[-1]["poll"] = {"a": "LIAM", "b": "NOEL", "cue": "Liam"}
    assert pw_write.format_problems(s) == []
    spec = bw_render.spec_from_dict({**s, "look": "satellite"}, fake_words(s))
    bw_render.align(spec)
    r = band_world.BandRenderer(spec, s["band"])
    for scene in spec.scenes:
        assert r.frame(scene.end - 0.05).size == (bw_render.W, bw_render.H)
    assert {it["type"] for items in r.items for it in items} >= {"prop", "poll", "emptymic"}
    for kind in band_world.PROPS:
        assert band_world.prop_sprite(kind, "TEXT", 0.5).getbbox()
    sc[2]["props"] = [{"kind": "piano", "cue": "Glasgow"}]
    sc[2]["poll"] = {"a": "X", "b": "Y"}
    found = " | ".join(pw_write.format_problems(s))
    assert "kind is one of" in found and "only the last scene" in found



@pytest.mark.parametrize("style", ["anthem", "britpop", "heavy", "pop"])
def test_every_music_style_is_made_in_code(style, tmp_path):
    from modules import band_music

    out = band_music.bed(6.0, tmp_path / "bed.mp3", style, seed=1)
    assert out.is_file() and out.stat().st_size > 20_000


def test_every_video_gets_the_anthem_unless_a_script_asks():
    from modules import band_music

    assert band_music.style_for({"music": "heavy", "pillar": "rock legends"}) == "heavy"
    assert band_music.style_for({"pillar": "90s and 2000s"}) == "anthem"
    assert band_music.style_for({}) == "anthem"



def test_a_band_story_takes_the_owners_track_when_there_is_one(tmp_path, monkeypatch):
    import subprocess

    folder = tmp_path / "music" / "encore"
    folder.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=8", str(folder / "Arena One.mp3")], check=True)
    monkeypatch.setattr(pw_channel, "PROJECT_ROOT", tmp_path)
    out = pw_channel.band_track({"seed": 3}, 5.0, tmp_path / "music.mp3")
    assert out.is_file() and out.stat().st_size > 10_000
    (folder / "Arena One.mp3").unlink()
    from modules import band_music

    monkeypatch.setattr(band_music, "bed", lambda seconds, out, style, seed=0: (out.write_bytes(b"made"), out)[1])
    assert pw_channel.band_track({"seed": 3}, 5.0, tmp_path / "bed.mp3").read_bytes() == b"made"
