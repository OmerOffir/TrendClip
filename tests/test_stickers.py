import json
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image

from trendclip import shorts, stickers
from trendclip import video_assembler as va
from trendclip.config import Settings
from trendclip.stickers import Reaction, StickerInfo
from trendclip.stickers import tag_with_gemini as real_tag_with_gemini


def make_settings(tmp_path):
    return Settings(youtube_api_key="k", assets_dir=tmp_path / "assets", output_dir=tmp_path / "output",
                    stickers_dir=tmp_path / "stickers")


def words_of(text, step=0.5):
    return [va.Word(w, i * step, i * step + step - 0.05) for i, w in enumerate(text.split())]


def add_files(folder):
    folder.mkdir(parents=True, exist_ok=True)
    cut = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    cut.paste((255, 0, 0, 255), (40, 40, 160, 160))
    cut.save(folder / "image copy.png")
    Image.new("RGB", (240, 200), (0, 120, 255)).save(folder / "oh_no.jpg")
    frames = [Image.new("RGB", (120, 120), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(folder / "sub_bell.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    (folder / "notes.txt").write_text("not a sticker")


def test_scan_inspects_tags_and_applies_overrides(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    add_files(settings.stickers_dir)
    by_name = {s.filename: s for s in stickers.scan(settings)}
    assert set(by_name) == {"image copy.png", "oh_no.jpg", "sub_bell.gif"}
    assert by_name["image copy.png"].transparent and not by_name["oh_no.jpg"].transparent
    assert by_name["oh_no.jpg"].moods == ["shocked"] and by_name["oh_no.jpg"].source == "filename"
    assert by_name["sub_bell.gif"].category == "cta" and by_name["sub_bell.gif"].cta == "subscribe"
    assert by_name["sub_bell.gif"].frames == 3

    seen = {}

    class Models:
        def generate_content(self, model, contents, config):
            seen["images"] = sum(1 for c in contents if not isinstance(c, str))
            return SimpleNamespace(parsed=stickers.StickerTags(stickers=[
                stickers.StickerTag(index=0, category="reaction", moods=["awkward", "made-up"], description="Fake smile"),
                stickers.StickerTag(index=1, category="reaction", moods=[], description="Man looking up"),
                stickers.StickerTag(index=2, category="cta", cta="Part2", description="Bell"),
            ]), text="")

    monkeypatch.setattr(stickers, "tag_with_gemini", real_tag_with_gemini)
    stickers._cache_file(settings).unlink()
    tagged = {s.filename: s for s in stickers.scan(settings, client=SimpleNamespace(models=Models()))}
    assert seen["images"] == 3
    assert tagged["image copy.png"].moods == ["awkward"] and tagged["image copy.png"].source == "gemini"
    assert tagged["oh_no.jpg"].moods == ["funny"]  # a reaction always gets a mood
    assert tagged["sub_bell.gif"].cta == "part2"

    (settings.stickers_dir / "stickers.json").write_text(json.dumps({
        "oh_no.jpg": {"moods": ["shocked", "crazy"], "description": "OH NO"},
        "image copy.png": {"category": "off"},
    }))
    final = {s.filename: s for s in stickers.scan(settings)}  # cached tags reused: no Gemini call
    assert final["oh_no.jpg"].moods == ["shocked", "crazy"] and final["oh_no.jpg"].source == "manual"
    assert final["image copy.png"].category == "off"
    assert stickers.library(settings) is stickers.library(settings)  # unchanged folder: no rescan


def test_keyword_cues_and_cta_detection():
    words = words_of("I walked in and there was total silence. Then he turned around. Oh no. "
                     "Sub to Side Quest Logic for Part 2 dropping tomorrow!")
    moods = {m for _, m in stickers.keyword_moments(words)}
    assert {"awkward", "shocked"} <= moods
    cta = stickers.find_cta_start(words)
    assert words[cta].text == "Sub"
    assert words[stickers.sentence_start(words, cta - 1)].text == "Oh"


def lib():
    return [
        StickerInfo(filename="drake_no.png", category="reaction", moods=["reject"]),
        StickerInfo(filename="harold.png", category="reaction", moods=["pain", "awkward"]),
        StickerInfo(filename="dafoe.png", category="reaction", moods=["shocked"]),
        StickerInfo(filename="hidden.png", category="cta", cta="part2", transparent=True),
    ]


def test_plan_places_reactions_and_cta_without_overlaps(tmp_path):
    settings = make_settings(tmp_path)
    text = ("So I go to the party and my ex is there with my boss. We all just stood there in total silence "
            "for like a minute. Then my boss starts laughing really hard and I have no idea why at all. "
            "That is when he turned around and I saw his face. Sub to Side Quest Logic for Part 2 dropping tomorrow!")
    words = words_of(text, 0.4)
    popup = va.Overlay(tmp_path / "p.png", 4.0, 6.0)  # a pop-up in the middle of the screen
    overlays, used = stickers.plan(settings, lib(), words, [Reaction(word="silence", mood="awkward")], cta="part2",
                                   popups=[popup], title_seconds=3.0, end_seconds=3.5, seed="x", pace=False)
    cta = next(o for o in overlays if o.pulse)
    sub = stickers.find_cta_start(words)
    assert cta.start == pytest.approx(words[sub].start - 0.05) and cta.center == stickers.CTA_CENTER
    assert cta.image.name == "hidden.png" and cta.style == "sticker"  # the folder's own Part 2 sticker
    reactions = [o for o in overlays if not o.pulse]
    assert len(reactions) == 2  # Gemini's beat + the cliffhanger before the CTA
    first, cliff = reactions
    assert first.image.name == "harold.png"  # awkward -> the sticker tagged awkward
    assert first.start == pytest.approx(words[[w.text for w in words].index("silence")].start - 0.05)
    assert cliff.image.name == "dafoe.png" and cliff.end <= cta.start
    for o in reactions:
        rect = stickers._rect(o.center, o.box, o.box)
        assert not stickers._hits(rect, stickers.CAPTIONS)
        assert not any(stickers._hits(rect, ui) for ui in stickers.SHORTS_UI)
        if o.start < popup.end and popup.start < o.end:
            assert not stickers._hits(rect, stickers._rect(popup.xy(), popup.box, popup.box))
    assert used == ["hidden.png", "harold.png", "dafoe.png"]


def test_plan_keeps_something_new_on_screen_every_3_to_5_seconds(tmp_path):
    settings = make_settings(tmp_path)
    words = words_of(" ".join(f"plain{i}" for i in range(100)) + " Follow for more!", 0.3)  # ~31 s, no cues
    popups = [va.Overlay(tmp_path / "p.png", s, s + 2.0) for s in (4.0, 8.0, 20.0)]
    overlays, _ = stickers.plan(settings, lib()[:3], words, [], cta="subscribe", popups=popups,
                                title_seconds=3.0, seed="pace")
    cta = next(o for o in overlays if o.pulse)
    starts = sorted([p.start for p in popups] + [o.start for o in overlays if not o.pulse] + [cta.start])
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert max(gaps) <= stickers.PACE[1] + 0.3 and min(gaps) >= 3.0
    fillers = [o for o in overlays if not o.pulse]
    assert len(fillers) >= 3 and all(o.end <= cta.start for o in fillers)
    off, _ = stickers.plan(settings, lib()[:3], words, [], cta="subscribe", popups=popups, title_seconds=3.0, pace=False)
    assert [o for o in off if not o.pulse] == []


def test_cta_sticker_starts_with_the_cta_sentence_after_the_question(tmp_path):
    settings = make_settings(tmp_path)
    words = words_of("We waited by the door for a long time. He slowly looked back at me. "
                     "Ever done this? Hit that subscribe button for daily side quests!")
    texts = [w.text for w in words]
    start = stickers.find_cta_start(words)
    assert words[start].text == "Hit"
    overlays, _ = stickers.plan(settings, lib(), words, [], cta="part2", seed="q", pace=False)
    cta = next(o for o in overlays if o.pulse)
    assert cta.start == pytest.approx(words[start].start - 0.05)
    cliff = next(o for o in overlays if not o.pulse)
    assert cliff.start == pytest.approx(words[texts.index("He")].start - 0.05)  # the cliffhanger, not the question


def test_plan_falls_back_to_keywords_and_builtin_cta(tmp_path):
    settings = make_settings(tmp_path)
    words = words_of("We waited. It was so awkward and weird. Then the dog screamed. Oh no. Follow for more!", 0.6)
    overlays, used = stickers.plan(settings, lib()[:3], words, [], cta="subscribe", seed="y")
    cta = next(o for o in overlays if o.pulse)
    assert cta.image.parent == settings.assets_dir / "stickers" and cta.style == "asis"
    assert Image.open(cta.image).size[0] > 500  # generated SUBSCRIBE button
    reactions = [o for o in overlays if not o.pulse]
    # awkward (2.4 s) -> "screamed" is skipped (only 3.6 s later) -> "oh no" (6.6 s) is shocked
    assert [o.image.name for o in reactions] == ["harold.png", "dafoe.png"]
    assert reactions[1].start - reactions[0].start >= stickers.MIN_GAP
    off, _ = stickers.plan(settings, lib(), words, [], cta=None, auto=False)
    assert off == []


def test_pop_frames_animate_gifs_pulse_and_cards(tmp_path):
    add_files(tmp_path)
    card = stickers.make_card(Image.open(tmp_path / "oh_no.jpg"), 300)
    assert card.getpixel((0, 0))[3] == 0 and max(card.size) > 300  # rounded, padded, shadowed
    gif = va.Overlay(tmp_path / "sub_bell.gif", 0, 2.0, center=(200, 800), box=200, style="card", animated=True)
    pattern = va.render_pop_frames(gif, tmp_path, "g", fps=10)
    frames = sorted(tmp_path.glob("g_*.png"))
    assert pattern == "g_%03d.png" and len(frames) == 20
    colours = {Image.open(f).convert("RGB").getpixel((Image.open(f).width // 2,) * 2) for f in frames[8:12]}
    assert len(colours) >= 2  # the GIF plays
    still = va.Overlay(tmp_path / "image copy.png", 0, 2.0, box=200)
    va.render_pop_frames(still, tmp_path, "s", fps=10)
    assert len(list(tmp_path.glob("s_*.png"))) == len(va.POP_SCALES)  # ffmpeg holds the last frame
    first = Image.open(tmp_path / "s_000.png").getchannel("A").getextrema()[1]
    assert first < 120  # fades in
    pulse = va.Overlay(tmp_path / "image copy.png", 0, 2.0, box=200, pulse=True)
    va.render_pop_frames(pulse, tmp_path, "p", fps=10)
    assert len(list(tmp_path.glob("p_*.png"))) == 20


def test_sticker_overlay_renders_at_its_slot(tmp_path):
    try:
        ffmpeg = va.ffmpeg_with_libass()
    except va.AssemblyError:
        pytest.skip("no ffmpeg with libass")
    gen = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i"]
    subprocess.run(gen + ["color=c=black:size=1080x1920:rate=30:duration=3", "-pix_fmt", "yuv420p",
                          str(tmp_path / "bg.mp4")], check=True)
    subprocess.run(gen + ["sine=frequency=300:duration=3", "-ac", "1", str(tmp_path / "voice.mp3")], check=True)
    Image.new("RGB", (240, 240), (0, 0, 255)).save(tmp_path / "meme.png")
    cx, cy = stickers.SLOTS["bottom-left"]
    ov = va.Overlay(tmp_path / "meme.png", 1.0, 2.6, center=(cx, cy), box=300, style="card")
    out = va.assemble_video(tmp_path / "bg.mp4", tmp_path / "voice.mp3", [{"word": "hi", "start": 0, "end": 2.8}],
                            tmp_path / "out.mp4", overlays=[ov], preset="ultrafast")

    def pixel(t):
        raw = subprocess.run([ffmpeg, "-loglevel", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1",
                              "-vf", f"crop=2:2:{cx}:{cy}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        return tuple(raw[:3])

    assert pixel(0.5)[2] < 40 and pixel(2.0)[2] > 200 and pixel(2.9)[2] < 40


def test_render_request_carries_sticker_options(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    req = shorts.RenderRequest(clip="c.mp4", game="g", script="one two three. Sub to us!", part=1, parts_total=2,
                               reactions=[{"word": "two", "mood": "nonsense"}], title_card="HI")
    assert req.reactions[0].mood == "funny"
    seen = {}
    monkeypatch.setattr(stickers, "library", lambda s: lib())
    monkeypatch.setattr(stickers, "plan", lambda *a, **kw: seen.update(kw) or ([], []))
    shorts.resolve_stickers(settings, req, words_of(req.script), [], lambda f, m: None)
    assert seen["cta"] == "part2" and seen["title_seconds"] == 3.0 and seen["auto"] is True
    shorts.resolve_stickers(settings, req.model_copy(update={"part": 2}), words_of(req.script), [], lambda f, m: None)
    assert seen["cta"] == "subscribe"
    assert shorts.resolve_stickers(settings, req.model_copy(update={"stickers": False, "cta_sticker": False}),
                                   words_of(req.script), [], lambda f, m: None) == ([], [])
