"""Borderline Why: map lookups, scene timing, frames, the script checks, topics and the channel's day."""

from __future__ import annotations

import json
from datetime import date as Day, datetime, timedelta

import numpy as np
import pytest

from modules import bw_channel, bw_geo, bw_render, bw_script, bw_topics
from trendclip.config import get_settings


def _square(x0, y0, x1, y1):
    ring = np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)], dtype=np.float32)
    return bw_geo.Part([ring], (x0, y0, x1, y1))


@pytest.fixture
def world():
    """A tiny hand-made world: two countries, a state, a lake, a river, a city."""
    land = bw_geo.Feature("country", "United States of America", iso="USA", parts=[_square(-100, 40, -90, 49)], rank=3)
    canada = bw_geo.Feature("country", "Canada", iso="CAN", parts=[_square(-100, 49, -90, 55)], rank=1)
    state = bw_geo.Feature("region", "Minnesota", country="United States of America", iso="USA", parts=[_square(-97, 44, -90, 49.4)])
    lake = bw_geo.Feature("lake", "Lake of the Woods", parts=[_square(-95.3, 48.8, -94.0, 49.9)])
    river = bw_geo.Feature("river", "Mississippi", parts=[bw_geo.Part([np.array([(-95.2, 47.2), (-91, 40)], dtype=np.float32)],
                                                                          (-95.2, 40, -91, 47.2))])
    state_ms = bw_geo.Feature("region", "Mississippi", country="United States of America", parts=[_square(-91, 30, -88, 35)])
    city = bw_geo.Feature("place", "Paris", country="France", point=(2.35, 48.85), rank=1e7)
    w = bw_geo.World({"countries": [land, canada], "regions": [state, state_ms], "lakes": [lake], "rivers": [river], "places": [city]})
    bw_geo.set_world(w)
    bw_render._layers.clear()
    yield w
    bw_geo.set_world(None)
    bw_render._layers.clear()


def _find(world):
    return lambda name, kinds=None: world.find(name, kinds)


# --------------------------------------------------------------------------- geo


def test_find_by_name_alias_and_hint(world):
    assert world.find("United States").name == "United States of America"
    assert world.find("the lake of the woods").kind == "lake"
    assert world.find("Mississippi").kind == "region"  # a clash goes to the state
    assert world.find("Mississippi River").kind == "river"  # unless the name says river
    assert world.find("Paris, France").point == (2.35, 48.85)
    assert world.find("Atlantis") is None
    assert world.find("Minnesota").bbox() == (-97, 44, -90, 49.4)


def test_bbox_ignores_far_islands():
    main, island = _square(-100, 40, -80, 50), _square(170, 50, 171, 51)
    f = bw_geo.Feature("country", "X", parts=[main, island])
    assert f.bbox() == (-100, 40, -80, 50)
    assert f.bbox(main_only=False)[2] == 171
    # France with French Guiana (as big as 5% of it, but an ocean away) frames only the mainland and Corsica
    france = bw_geo.Feature("country", "France", parts=[_square(-5, 42, 8, 51), _square(8.5, 41.4, 9.6, 43), _square(-54.6, 2.1, -51.6, 5.8)])
    assert france.bbox() == (-5, 41.4, 9.6, 51)
    # an archipelago chained by narrow straits stays whole
    chain = bw_geo.Feature("country", "Isles", parts=[_square(95, -6, 106, 6), _square(105.5, -9, 115, -6.5), _square(116, -9, 125, -8)])
    assert chain.bbox() == (95, -9, 125, 6)


# --------------------------------------------------------------------------- timing and views


def _words(text, start=0.0, step=0.4):
    return [bw_render.Word(w, start + i * step, start + i * step + 0.3) for i, w in enumerate(text.split())]


def test_align_scenes_to_spoken_words():
    scenes = [bw_render.Scene("map", "One two three."), bw_render.Scene("card", "In 1783 four five."), bw_render.Scene("map", "Six seven.")]
    spoken = _words("One two three.") + [bw_render.Word("In 1783", 1.2, 1.9)] + _words("four five. Six seven.", 2.0)
    spec = bw_render.Spec(scenes, spoken)
    scenes[1].elements = [bw_render.Element("stamp", text="X", cue="five")]
    bw_render.align(spec)
    assert scenes[0].start == 0
    # "In 1783" is one spoken token: the card starts right after "three", not at "four"
    assert scenes[1].start == pytest.approx(0.8 + 0.3 + 0.1)
    assert scenes[2].start == pytest.approx(2.8)
    assert scenes[2].end == pytest.approx(spec.duration)
    assert scenes[1].elements[0].at == pytest.approx(2.4)


def test_fit_view_frames_the_box():
    lon, lat, ppd = bw_render.fit_view([(-95.3, 48.8, -94.0, 49.9)])
    assert -95.3 < lon < -94.0 and 48.8 < lat < 49.9
    cam = bw_render.Cam(lon, lat, ppd)
    for x, y in [(-95.3, 48.8), (-94.0, 49.9)]:
        px, py = cam.pt(x, y)
        assert 0 < px < bw_render.W and bw_render.MAP_TOP - 50 < py < bw_render.MAP_BOTTOM + 50


def _spec_dict():
    return {"title": "Why?", "cover_text": "A notch on the map", "seed": 2, "theme": "atlas", "scenes": [
        {"kind": "map", "narration": "There is a notch on the map.", "focus": ["Lake of the Woods"], "elements": [
            {"kind": "highlight", "place": "Minnesota", "color": "a", "cue": "notch"},
            {"kind": "highlight", "place": "Canada", "color": "b"},
            {"kind": "highlight", "place": "Mississippi River"},
            {"kind": "pin", "place": "Paris", "text": "far away"},
            {"kind": "arrow", "place": "Minnesota", "to": "Canada", "text": "north"},
            {"kind": "parallel", "value": 49.0}]},
        {"kind": "card", "narration": "In 1783 a treaty drew it.", "kicker": "Treaty", "title": "1783", "subtitle": "drawn on a map",
         "elements": [{"kind": "stamp", "text": "Rejected", "cue": "drew"}]},
        {"kind": "map", "narration": "And it stayed.", "focus": ["Minnesota"], "headline": "It stayed", "elements": [
            {"kind": "ring", "place": "Paris", "text": "here", "color": "gold"}]},
    ]}


def test_frames_render_map_card_and_transitions(world):
    data = _spec_dict()
    narration = " ".join(s["narration"] for s in data["scenes"])
    spec = bw_render.spec_from_dict(data, _words(narration))
    assert bw_render.prepare(spec) == []
    r = bw_render.Renderer(spec)
    for t in (0.0, 1.0, 2.9, 3.2, 4.5, spec.duration - 0.1):
        img = r.frame(t)
        assert img.size == (bw_render.W, bw_render.H)
    first, card = np.asarray(r.frame(1.0)), np.asarray(r.frame(4.5))
    assert first.std() > 10 and card.std() > 5
    # the cover text is drawn in gold at the top of the first frame
    top = np.asarray(r.frame(0.0))[150:350]
    assert ((top[..., 0] > 200) & (top[..., 1] > 150) & (top[..., 2] < 120)).sum() > 500


def test_missing_places_are_reported(world):
    data = _spec_dict()
    data["scenes"][0]["elements"].append({"kind": "pin", "place": "Atlantis"})
    spec = bw_render.spec_from_dict(data, [])
    assert bw_render.resolve(spec, _find(world)) == ["Atlantis"]


# --------------------------------------------------------------------------- the script checks


SOURCE_1 = ("The Northwest Angle is a part of Minnesota that can only be reached by land through Canada. "
            "The treaty negotiators used the Mitchell Map, which showed the lake as an egg shape with a clear northwest point. "
            "In 1798 the explorer David Thompson showed that the Mississippi began south of the lake.")
SOURCE_2 = ("The Convention of 1818 drew a line due south from the northwest point of the lake to the 49th parallel. "
            "The Angle covers about 123 square miles and Britain and Canada offered to buy it in the 1870s.")


def _research():
    topic = bw_topics.Topic(question="Why does Minnesota have a notch?", article="Northwest Angle")
    return bw_script.Research(topic, "Northwest Angle", [bw_script.Source(1, "https://en.wikipedia.org/wiki/Northwest_Angle", "Wiki", SOURCE_1),
                                                         bw_script.Source(2, "https://www.mnhs.org/angle", "MNHS", SOURCE_2)])


def _draft(narration_words: int = 105, quote_ok: bool = True) -> bw_script.Draft:
    filler = " ".join(["The map shows the lake and the border clearly."] * ((narration_words - 20) // 9))
    scenes = [
        bw_script.SceneDraft(kind="map", narration="There is a notch on top of Minnesota.", focus=["Lake of the Woods"],
                             elements=[bw_script.ElementDraft(kind="highlight", place="Minnesota", cue="Minnesota")]),
        bw_script.SceneDraft(kind="card", narration="In 1783 a treaty used an old map.", title="1783"),
        bw_script.SceneDraft(kind="map", narration=filler, focus=["Minnesota"],
                             elements=[bw_script.ElementDraft(kind="highlight", place="Minnesota", cue="map"),
                                       bw_script.ElementDraft(kind="label", place="Lake of the Woods", cue="lake")]),
        bw_script.SceneDraft(kind="map", narration="A line due west never met the river.", focus=["Mississippi River"],
                             elements=[bw_script.ElementDraft(kind="parallel", value=49.4, color="red"),
                                       bw_script.ElementDraft(kind="highlight", place="Mississippi River", cue="river")]),
        bw_script.SceneDraft(kind="map", narration="So the border dropped south to the 49th parallel.", focus=["Canada"],
                             elements=[bw_script.ElementDraft(kind="stamp", text="Patched", color="gold"),
                                       bw_script.ElementDraft(kind="highlight", place="Canada", cue="south")]),
    ]
    claims = [
        bw_script.ClaimDraft(claim="Only by land through Canada", source_id=1, quote="can only be reached by land through Canada"),
        bw_script.ClaimDraft(claim="Egg-shaped lake", source_id=1, quote="showed the lake as an egg shape with a clear northwest point"),
        bw_script.ClaimDraft(claim="1798", source_id=1, quote="In 1798 the explorer David Thompson showed that the Mississippi began south"),
        bw_script.ClaimDraft(claim="1818 line", source_id=2, quote="drew a line due south from the northwest point of the lake to the 49th parallel"
                             if quote_ok else "drew a straight line from the lake to the sea in 1818"),
    ]
    return bw_script.Draft(usable=True, title="Why does Minnesota have a notch?", cover_text="Cut off by a bad map", scenes=scenes,
                           claims=claims, description="A map error.", hashtags=["maps"])


def test_check_passes_a_good_draft(world):
    draft = _draft()
    assert bw_script.check(draft, _research(), _find(world)) == []


def test_check_catches_quotes_length_places_and_hype(world):
    draft = _draft(quote_ok=False)
    draft.scenes[0].narration = "Did you know there is a notch on top of Minnesota?"
    draft.scenes[0].elements.append(bw_script.ElementDraft(kind="pin", place="Atlantis"))
    problems = " | ".join(bw_script.check(draft, _research(), _find(world)))
    assert "not word for word" in problems and "drew a straight line" in problems
    assert "Atlantis" in problems
    assert "did you know" in problems
    short = _draft(narration_words=40)
    assert any("words (wanted" in p for p in bw_script.check(short, _research(), _find(world)))
    # the one legend (the owner, 2026-11-06): a sentence of the narration, told as a legend
    told = _draft()
    told.scenes[1].narration = "Locals say a drunk surveyor drew the notch."
    told.legend = "Locals say a drunk surveyor drew the notch."
    assert not [p for p in bw_script.check(told, _research(), _find(world)) if "legend" in p]
    told.legend = "A drunk surveyor drew the whole notch."
    assert any("not a sentence of the narration" in p for p in bw_script.check(told, _research(), _find(world)))
    told.scenes[1].narration = told.legend
    assert any("told as fact" in p for p in bw_script.check(told, _research(), _find(world)))


def test_check_takes_boards_and_the_mascot_lines(world):
    draft = _draft()
    draft.scenes[1] = bw_script.SceneDraft(kind="board", narration="In 1783 a treaty used an old map.", ask="Wait, how did that happen?",
                                           board_write=["The 1783 map", "A lake shaped like an egg"], board_draw=["Minnesota"])
    draft.scenes[4].react = "So who messed up, Britain or America?"
    assert bw_script.check(draft, _research(), _find(world)) == []
    script = bw_script.to_spec_dict(draft)
    assert script["scenes"][1]["board"] == {"write": ["The 1783 map", "A lake shaped like an egg"], "draw": ["Minnesota"]}
    assert script["scenes"][1]["ask"] == "Wait, how did that happen?" and "board_write" not in script["scenes"][1]


def test_check_catches_bad_boards_and_mascot_lines(world):
    draft = _draft()
    draft.scenes[0].ask = "Why?"
    draft.scenes[1] = bw_script.SceneDraft(kind="board", narration="In 1783 a treaty used an old map.", ask="This is not a question",
                                           board_draw=["Atlantis"])
    draft.scenes[2].react = "Subscribe for more maps like this one, friends!"
    problems = " | ".join(bw_script.check(draft, _research(), _find(world)))
    assert "first scene can't have an ask" in problems
    assert "must be a question" in problems
    assert "no board_write" in problems and "Atlantis" in problems
    assert "banned phrases in the mascot's react" in problems


def test_quote_found_ignores_case_and_punctuation_but_not_changes():
    source = bw_script.Source(1, "u", "t", "The Angle covers about 123 square miles, and Britain offered to buy it.")
    assert bw_script.quote_found("the angle covers about 123 square miles and Britain offered", source)
    assert not bw_script.quote_found("The Angle covers about 200 square miles", source)
    assert not bw_script.quote_found("123 square", source)  # too short to prove anything


def test_a_quote_shortened_with_dots_passes_when_each_piece_is_in_the_source():
    source = bw_script.Source(1, "u", "t", "The Treaty of Paris (1783) stated that the boundary would run through the lake "
                                         "to its most northwestern point and then due west to the Mississippi River.")
    assert bw_script.quote_found("The Treaty of Paris (1783)... stated that the boundary... would run", source)
    assert bw_script.quote_found("through the lake to its most northwestern point … due west to the Mississippi River", source)
    assert not bw_script.quote_found("The Treaty of Paris (1783)... said the border followed the river north", source)
    assert not bw_script.quote_found("The Treaty... stated", source)


@pytest.fixture
def scripted(monkeypatch):
    """bw_script.write with the rule check and the fact checker answering from lists, and every prompt kept."""
    calls = {"prompts": [], "rules": [], "facts": []}

    def ask(client, model, prompt, schema, temperature=0.7, progress=None):
        calls["prompts"].append(prompt)
        return _draft()

    monkeypatch.setattr(bw_script, "gemini", lambda client=None: (None, "m"))
    monkeypatch.setattr(bw_script, "_ask", ask)
    monkeypatch.setattr(bw_script, "check", lambda draft, res: calls["rules"].pop(0) if calls["rules"] else [])
    monkeypatch.setattr(bw_script, "verify", lambda client, model, draft, res, progress=None: calls["facts"].pop(0) if calls["facts"] else [])
    return calls


def test_the_fact_checker_gets_its_own_fixes_after_the_rule_fixes(scripted):
    scripted["rules"] = [["the narration is 188 words"], ["a quote is not word for word"]]
    scripted["facts"] = [["The lake is 200 miles wide - the sources say 70"], ["OFF TOPIC: David Thompson's later life"]]
    steps = []

    script = bw_script.write(_research(), today="2026-10-28", progress=steps.append)

    assert script["title"] == "Why does Minnesota have a notch?"
    assert len(scripted["prompts"]) == 5  # the draft, 2 rule fixes, 2 fact fixes
    assert "The lake is 200 miles wide" in scripted["prompts"][3] and "Keep every other sentence exactly" in scripted["prompts"][3]
    assert "Fixing the facts (1 of 3): The lake is 200 miles wide - the sources say 70" in steps


def test_a_draft_the_checker_keeps_flagging_drops_the_topic_with_what_it_flagged(scripted):
    scripted["facts"] = [["Built in 1900 - no source gives a year"]] * (bw_script.CHECK_FIXES + 1)

    with pytest.raises(bw_script.ScriptError) as err:
        bw_script.write(_research(), today="2026-10-28")

    assert "The fact checker still flagged: Built in 1900" in str(err.value)
    assert len(scripted["prompts"]) == 1 + bw_script.CHECK_FIXES


def test_the_description_tells_the_story_with_emojis_and_hashtags_and_no_sources():
    # the owner, 2026-11-07: like the geography Shorts that do well, a story with emojis, no source list, many hashtags
    old = bw_script.description({"question": "Why?", "description": "Because.", "hashtags": ["Strait of Messina", "#borders"],
                                 "sources": [{"title": "MNHS", "url": "https://mnhs.org"}]}, "@BorderlineWhy")
    assert old.startswith("🌍 Why?\n\nBecause.")  # a script written before the story style: its question leads
    assert "Sources" not in old and "mnhs.org" not in old and "No AI" not in old
    assert "🔔 Follow @BorderlineWhy" in old and "#straitofmessina #borders #geography" in old
    tags = [w for w in old.split() if w.startswith("#")]
    assert len(tags) == len(set(tags)) <= bw_script.MAX_TAGS and "#shorts" in tags
    assert old.rstrip().endswith(bw_script.CREDITS)  # the licences' credits, last and short
    story = bw_script.description({"question": "Why?", "description": "🌍 Did you know a line splits the animals? 🦘🐅"})
    assert story.startswith("🌍 Did you know") and "Why?" not in story
    assert "NASA" not in bw_script.description({"look": "flat"})
    assert "THE DESCRIPTION" in bw_script.PROMPT and "No sources" in bw_script.DESCRIPTION_RULES


def test_page_text_keeps_paragraphs_and_drops_scripts():
    title, text = bw_script.page_text("<html><head><title>Angle &amp; Lake</title><script>var x = 'hidden words here now';</script></head>"
                                      "<body><nav>menu one two three four five six seven eight</nav>"
                                      "<p>The Northwest Angle is the northernmost part of the contiguous United States today.</p>"
                                      "<p>Short.</p></body></html>")
    assert title == "Angle & Lake"
    assert "northernmost part" in text and "hidden" not in text and "menu" not in text and "Short." not in text


# --------------------------------------------------------------------------- topics


def test_topics_next_mark_and_refill(tmp_path, monkeypatch):
    first = bw_topics.next_topic(tmp_path)
    assert first.article == bw_topics.SEEDS[0][1]
    bw_topics.mark(tmp_path, first.article, used=True)
    bw_topics.mark(tmp_path, bw_topics.SEEDS[1][1], used=False, note="no sources")
    assert bw_topics.next_topic(tmp_path).article == bw_topics.SEEDS[2][1]
    assert bw_topics.next_topic(tmp_path, {bw_topics.SEEDS[2][1]}).article == bw_topics.SEEDS[3][1]
    calls = []
    monkeypatch.setattr(bw_topics, "propose", lambda out, client=None: calls.append(out))
    assert bw_topics.ensure(tmp_path) == len(bw_topics.SEEDS) - 2
    assert calls == []  # plenty left: no Gemini call


# --------------------------------------------------------------------------- the channel


@pytest.fixture
def settings(monkeypatch):
    for name in ("BW_SLOTS", "BW_LEAD", "BW_AUTO_MAKE", "BW_AUTO_POST", "BW_REQUIRE_APPROVAL", "BW_UPLOAD_PRIVACY", "BW_MUSIC"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BW_MUSIC", "none")
    return get_settings()


def test_jobs_and_due_times(settings, tmp_path, monkeypatch):
    assert bw_channel.slots() == ["14:30", "23:00"]
    monkeypatch.setenv("BW_SLOTS", "19:00")
    day = Day(2026, 11, 2)
    job = bw_channel.jobs_for(settings, day)[0]
    assert job.slot == "19:00" and job.key == "2026-11-02_1900"
    assert job.start == job.post_at - timedelta(hours=8)
    early = job.start - timedelta(minutes=1)
    assert bw_channel.due_makes(settings, early, set(), tmp_path) == []
    assert bw_channel.due_makes(settings, job.start, set(), tmp_path) == [job]
    assert bw_channel.due_makes(settings, job.start, {job.make_key}, tmp_path) == []
    # an hour early: uploaded private, scheduled for the slot
    assert bw_channel.due_posts(settings, job.post_at - timedelta(minutes=61), set()) == []
    assert bw_channel.due_posts(settings, job.post_at - timedelta(minutes=60), set()) == [job]
    assert bw_channel.due_posts(settings, job.post_at + timedelta(minutes=5), set()) == [job]
    assert bw_channel.due_posts(settings, job.post_at + bw_channel.POST_WINDOW, set()) == []
    assert job.make_key.startswith("2026-11-02")  # the sent log prunes by the date prefix


SCRIPT = {"title": "Why does Minnesota have a notch?", "question": "Why does Minnesota have a notch?", "article": "Northwest Angle",
          "narration": "There is a notch on the map. In 1783 a treaty drew it. And it stayed.",
          "description": "A map error.", "hashtags": ["borders"],
          "sources": [{"id": 1, "url": "https://en.wikipedia.org/wiki/Northwest_Angle", "title": "Wiki"},
                      {"id": 2, "url": "https://mnhs.org", "title": "MNHS"}]}


def _make(settings, tmp_path, world, day=Day(2026, 11, 2), seconds=45.0, slot="14:30"):
    script = {**_spec_dict(), **SCRIPT}
    rendered = []

    def voice(text, out, progress):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp3")
        ws = _words(text)
        ws[-1] = bw_render.Word(ws[-1].text, seconds - bw_render.TAIL - 0.3, seconds - bw_render.TAIL)
        return ws

    def render(spec, voice_path, out, music, volume, progress):
        rendered.append(spec)
        out.write_bytes(b"\x00" * 64)
        return out

    job = bw_channel.job_for(settings, day, slot)
    info = bw_channel.make(settings, job, out_dir=tmp_path, script=script, voice=voice, render=render)
    return job, info, rendered


def test_make_records_the_video(settings, tmp_path, world):
    job, info, rendered = _make(settings, tmp_path, world)
    assert rendered and rendered[0].scenes[0].view is not None
    saved = bw_channel.load_info(job.key, tmp_path)
    assert saved["title"] == SCRIPT["title"] and saved["check"] == []
    assert "Sources:" not in saved["description"] and "#" in saved["description"] and saved["duration_seconds"] == pytest.approx(45.0 - bw_render.TAIL + bw_channel.LOOP_HOLD)  # no outro: it loops
    assert (tmp_path / job.key / "script.json").is_file()
    assert bw_channel.videos(tmp_path)[0]["key"] == job.key
    assert "Northwest Angle" in bw_topics.load(tmp_path).used  # the script's topic is never made again


def test_too_long_video_fails_its_check(settings, tmp_path, world):
    with pytest.raises(bw_channel.ChannelError, match="s long"):
        _make(settings, tmp_path, world, seconds=120.0)
    assert bw_channel.load_info("2026-11-02_1430", tmp_path)["check"]


class FakeYouTube:
    def __init__(self):
        self.updates = []

    def videos(self):
        return self

    def update(self, part, body):
        self.updates.append(body)
        return self

    def execute(self):
        return {}


def test_approval_gates_the_upload(settings, tmp_path, world, monkeypatch):
    monkeypatch.setenv("BW_REQUIRE_APPROVAL", "1")
    job, info, _ = _make(settings, tmp_path, world)
    uploads = []

    def fake_upload(path, req, as_short, progress, youtube):
        uploads.append(req)
        return {"video_id": "abc", "url": "https://youtu.be/abc", "privacy": req.privacy}

    monkeypatch.setattr(bw_channel.publish, "upload_file", fake_upload)
    monkeypatch.setattr(bw_channel, "announce_upload", lambda *a, **k: None)
    yt = FakeYouTube()
    assert bw_channel.publish_if_allowed(job.key, out_dir=tmp_path, youtube=yt) is None  # not approved yet
    result = bw_channel.approve(job.key, settings, tmp_path, now=job.post_at - timedelta(hours=2), youtube=yt)
    assert result["record"] is None and uploads == []  # before the slot's early upload: it waits
    record = bw_channel.publish_if_allowed(job.key, out_dir=tmp_path, youtube=yt)
    assert record["url"] == "https://youtu.be/abc" and uploads[0].category_id == "27" and uploads[0].privacy == "public"
    assert bw_channel.publish_if_allowed(job.key, out_dir=tmp_path, youtube=yt) == record  # never twice
    assert len(uploads) == 1
    hidden = bw_channel.veto(job.key, tmp_path, yt)
    assert hidden["hidden"] and yt.updates[0]["status"]["privacyStatus"] == "private"


def test_without_approval_it_goes_out_by_itself(settings, tmp_path, world, monkeypatch):
    job, info, _ = _make(settings, tmp_path, world)
    assert not bw_channel.require_approval() and "goes out by itself" in bw_channel.video_note(info)
    monkeypatch.setattr(bw_channel.publish, "upload_file",
                        lambda path, req, as_short, progress, youtube: {"video_id": "v", "url": "https://youtu.be/v", "privacy": req.privacy})
    monkeypatch.setattr(bw_channel, "announce_upload", lambda *a, **k: None)
    assert bw_channel.publish_if_allowed(job.key, out_dir=tmp_path, youtube=FakeYouTube())["url"] == "https://youtu.be/v"


def test_approving_after_the_slot_uploads_at_once(settings, tmp_path, world, monkeypatch):
    job, info, _ = _make(settings, tmp_path, world)
    monkeypatch.setattr(bw_channel.publish, "upload_file",
                        lambda path, req, as_short, progress, youtube: {"video_id": "v", "url": "https://youtu.be/v", "privacy": req.privacy})
    monkeypatch.setattr(bw_channel, "announce_upload", lambda *a, **k: None)
    result = bw_channel.approve(job.key, settings, tmp_path, now=job.post_at + timedelta(minutes=10), youtube=FakeYouTube())
    assert result["record"]["url"] == "https://youtu.be/v"


def test_veto_before_upload_and_set_aside(settings, tmp_path, world):
    job, info, _ = _make(settings, tmp_path, world)
    bw_channel.approve(job.key, None, tmp_path)
    result = bw_channel.veto(job.key, tmp_path)
    assert not result["uploaded"] and not bw_channel.ready(bw_channel.load_info(job.key, tmp_path))
    moved = bw_channel.set_aside(job.key, tmp_path)
    assert moved.name.startswith(job.key + ".old-") and bw_channel.load_info(job.key, tmp_path) is None
    assert bw_channel.videos(tmp_path) == []  # set-aside folders are not listed


def test_next_script_queue_then_topics(settings, tmp_path, monkeypatch):
    queue = bw_channel.queue_dir(tmp_path)
    queue.mkdir(parents=True)
    (queue / "001_angle.json").write_text(json.dumps(SCRIPT))
    script, origin = bw_channel.next_script(out_dir=tmp_path)
    assert origin == "queue:001_angle.json" and script["title"] == SCRIPT["title"]
    assert (queue / "used" / "001_angle.json").is_file()

    seen = []

    def research(topic, progress):
        seen.append(topic.article)
        if len(seen) == 1:
            raise bw_script.ScriptError("only Wikipedia")
        return topic

    monkeypatch.setattr(bw_topics, "ensure", lambda out, client=None: 99)
    script, origin = bw_channel.next_script(out_dir=tmp_path, research=research, write=lambda res, client, progress: {"ok": res.article})
    assert seen == [bw_topics.SEEDS[0][1], bw_topics.SEEDS[1][1]]
    assert origin == f"topic:{bw_topics.SEEDS[1][1]}"
    assert bw_topics.SEEDS[0][1] in bw_topics.load(tmp_path).rejected

    def busy(topic, progress):
        raise bw_script.TryLater("Gemini quota")

    with pytest.raises(bw_script.TryLater):
        bw_channel.next_script(out_dir=tmp_path, research=busy)
    assert bw_topics.SEEDS[2][1] not in bw_topics.load(tmp_path).rejected  # a busy day does not drop the topic


def test_manual_topic_goes_first_and_listing(tmp_path):
    bw_topics.mark(tmp_path, bw_topics.SEEDS[0][1], used=True)
    bw_topics.add(tmp_path, "Why is Alaska's capital not reachable by road?", "Juneau, Alaska")
    assert bw_topics.next_topic(tmp_path).article == "Juneau, Alaska"
    rows = bw_topics.listing(tmp_path)
    assert rows[0]["article"] == "Juneau, Alaska" and rows[0]["status"] == "next" and rows[0]["source"] == "manual"
    assert rows[-1]["status"] == "used" and rows[-1]["article"] == bw_topics.SEEDS[0][1]


def test_tab_endpoints(tmp_path, monkeypatch, settings, world):
    from fastapi.testclient import TestClient

    from trendclip.web.app import app

    monkeypatch.setattr(bw_channel, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(bw_channel, "auto_post", lambda: False)  # Approve after the slot would start a real upload
    job, info, _ = _make(settings, tmp_path, world)
    client = TestClient(app)
    body = client.get("/api/borderlinewhy/videos").json()
    assert body["videos"][0]["key"] == job.key and body["videos"][0]["state"] == "approved"
    assert "🔔 Follow" in body["videos"][0]["youtube"]["description"]
    assert client.get(f"/api/borderlinewhy/videos/{job.key}/video.mp4").status_code == 200
    assert client.get("/api/borderlinewhy/videos/nope/video.mp4").status_code == 404
    approved = client.post(f"/api/borderlinewhy/videos/{job.key}/approve").json()
    assert approved["approved"] is True
    assert client.get("/api/borderlinewhy/videos").json()["videos"][0]["state"] == "approved"
    client.post(f"/api/borderlinewhy/videos/{job.key}/veto")
    assert client.get("/api/borderlinewhy/videos").json()["videos"][0]["state"] == "vetoed"
    monkeypatch.setattr(bw_script, "article_exists", lambda title: None)
    assert client.post("/api/borderlinewhy/topics", json={"question": "Why is this a thing?", "article": "Nope"}).status_code == 400
    monkeypatch.setattr(bw_script, "article_exists", lambda title: "Juneau, Alaska")
    added = client.post("/api/borderlinewhy/topics", json={"question": "Why no road to Juneau?", "article": "juneau"}).json()
    assert added["article"] == "Juneau, Alaska"
    assert client.get("/api/borderlinewhy/topics").json()["topics"][0]["article"] == "Juneau, Alaska"
    status = client.get("/api/borderlinewhy/status").json()
    assert status["slots"] == ["14:30", "23:00"] and "connected" in status["youtube"]
    plan = client.get("/api/borderlinewhy/plan").json()
    assert plan["slots"] == ["14:30", "23:00"] and len(plan["days"]) == 8
    assert client.post("/api/borderlinewhy/make", json={"day": "2026-11-02", "slot": "12:00"}).status_code == 400


def test_plan_days_states_and_extra_videos(settings, tmp_path, world, monkeypatch):
    job, info, _ = _make(settings, tmp_path, world)  # 2026-11-02 14:30, waiting for Approve
    old = tmp_path / "2026-11-02_1900"  # made when the slot was 19:00, already on YouTube
    old.mkdir()
    (old / "video.mp4").write_bytes(b"x")
    (old / "info.json").write_text(json.dumps({"title": "Old", "job": {"post_at": "2026-11-02T19:00+02:00"},
                                               "uploads": {"youtube": {"url": "https://youtu.be/o"}}}))
    now = datetime(2026, 11, 2, 16, 0, tzinfo=settings.tz)
    days = bw_channel.plan_days(settings, now, before=0, after=1, out_dir=tmp_path)
    today = days[0]
    assert today["today"] and [r["slot"] for r in today["slots"]] == ["14:30", "19:00", "23:00"]
    by = {r["slot"]: r for r in today["slots"]}
    assert by["14:30"]["state"] == "approved" and by["14:30"]["video"]["title"] == SCRIPT["title"] + " (Weird Borders #1 next)"
    assert by["19:00"]["state"] == "posted" and by["19:00"]["extra"]
    assert by["23:00"]["state"] == "due"  # made 8 h before: 15:00, so it is being made
    tomorrow = {r["slot"]: r["state"] for r in days[1]["slots"]}
    assert tomorrow == {"14:30": "later", "23:00": "later"}
    later = datetime(2026, 11, 3, 1, 0, tzinfo=settings.tz)
    assert {r["slot"]: r["state"] for r in bw_channel.plan_days(settings, later, 1, 0, tmp_path)[0]["slots"]}["23:00"] == "missed"


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


ARTICLE_HTML = ("<html><body><p>Llívia is a Spanish town completely surrounded by French territory in the Pyrenees.<sup class=\"reference\">[1]</sup></p>"
                "<p>It stayed Spanish because the 1659 treaty gave France villages, and Llívia was a town.</p>"
                "<a rel=\"mw:ExtLink nofollow\" href=\"https://treaties.example.org/llivia\">x</a>"
                "<h2 id=\"References\">References</h2><p>Cited reference text that must not be part of the article body here.</p>"
                "<a rel=\"mw:ExtLink\" href=\"https://news.example.com/a?x=1&amp;y=2\">y</a></body></html>")


def test_wikipedia_falls_back_to_the_rest_api_when_the_action_api_fails(monkeypatch):
    import requests

    calls = []

    def fake_get(url, params=None, timeout=20, tries=4):
        calls.append(url)
        if "api.php" in url:
            raise requests.HTTPError("503 Service Unavailable")
        if "/summary/Ll%C3%ADvia" in url:
            return _Resp(200, {"title": "Llívia"})
        if "/html/Ll%C3%ADvia" in url:
            return _Resp(200, text=ARTICLE_HTML)
        _Resp(404).raise_for_status()  # like the real _get: a 404 raises

    monkeypatch.setattr(bw_script, "_get", fake_get)
    assert bw_script.article_exists("Llívia") == "Llívia"
    assert bw_script.article_exists("No such page") is None  # a 404 is "missing", not an error
    title, text = bw_script.wiki_text("Llívia")
    assert "completely surrounded by French territory" in text and "[1]" not in text and "Cited reference" not in text
    links = bw_script.cited_links("Llívia")
    assert links == ["https://treaties.example.org/llivia", "https://news.example.com/a?x=1&y=2"]


def test_research_says_try_later_when_both_wikipedia_routes_fail(monkeypatch):
    import requests

    monkeypatch.setattr(bw_script, "article_exists", lambda title: title)

    def down(url, params=None, timeout=20, tries=4):
        raise requests.HTTPError("503 Service Unavailable")

    monkeypatch.setattr(bw_script, "_get", down)
    with pytest.raises(bw_script.TryLater):
        bw_script.research(bw_topics.Topic(question="Why?", article="Llívia"))


# --------------------------------------------------------------------------- the satellite look


@pytest.fixture
def fx_offline(monkeypatch, tmp_path):
    """bw_fx without the network: a plain-color world picture, made-up flags and icons, none of the owner's."""
    from PIL import Image

    from modules import bw_fx

    monkeypatch.setattr(bw_fx, "CUSTOM_DIR", tmp_path / "no-custom-icons")

    class FakeSat:
        globe_tex = np.full((64, 128, 3), (40, 120, 60), np.uint8)

        def frame(self, cam):
            return Image.new("RGB", (bw_render.W, bw_render.H), (40, 120, 60))

    def flag(name):
        if "Atlantis" in name or not name.startswith("Flag of"):
            raise OSError("404")
        return Image.new("RGB", (300, 200), (200, 30, 30))

    def icon(code, size):
        return Image.new("RGBA", (size, size), (250, 200, 0, 255))

    monkeypatch.setattr(bw_fx, "satellite", lambda: FakeSat())
    monkeypatch.setattr(bw_fx, "flag_image", flag)
    monkeypatch.setattr(bw_fx, "icon_image", icon)
    monkeypatch.setattr(bw_fx, "anim_frames", lambda code, size: None)
    return bw_fx


def _fx_dict():
    data = _spec_dict()
    data["look"] = "satellite"
    data["scenes"][0]["elements"] += [
        {"kind": "highlight", "place": "Canada", "flag": "Flag of Canada.svg", "cue": "map"},
        {"kind": "icon", "icon": "money_bag", "cue": "notch"},
        {"kind": "icon", "icon": "car", "place": "Minnesota"},
        {"kind": "icon", "icon": "unicorn"},
        {"kind": "route", "points": ["Minnesota", "Lake of the Woods"], "icon": "car", "cue": "There", "flag": ""},
    ]
    data["scenes"][1]["elements"] += [{"kind": "pole", "flag": "Canada"}, {"kind": "pole", "flag": "Atlantis"}]
    return data


def test_satellite_look_dresses_and_draws_frames(world, fx_offline):
    data = _fx_dict()
    narration = " ".join(s["narration"] for s in data["scenes"])
    spec = bw_render.spec_from_dict(data, _words(narration))
    assert spec.look == "satellite"
    assert bw_render.prepare(spec) == []
    route = next(e for e in spec.scenes[0].elements if e.kind == "route")
    assert "flag" not in route.opts and len(route.opts["coords"]) == 2  # empty fields are not options
    r = fx_offline.FxRenderer(spec)
    kinds = [(e.kind, e.opts.get("icon") or e.opts.get("flag")) for s in spec.scenes for e in s.elements if e.kind in ("icon", "pole")]
    # the unknown icon and the flag Commons does not have are left out; known names become code points / files
    assert kinds == [("icon", "1f4b0"), ("icon", "1f697"), ("pole", "Flag of Canada.svg")]
    money = next(e for e in spec.scenes[0].elements if e.opts.get("icon") == "1f4b0")
    car = next(e for e in spec.scenes[0].elements if e.kind == "icon" and e.opts.get("icon") == "1f697")
    assert (money.opts["x"], money.opts["y"]) == fx_offline.ICON_SLOTS["map"][0] and car.opts["dy"] < 0
    assert "globe" not in spec.scenes[0].opts  # a Short opens tight on its place, not on a slow globe
    for t in (0.0, 1.0, 2.0, 2.9, 3.2, 4.5, spec.duration - 0.1):
        assert r.frame(t).size == (bw_render.W, bw_render.H)


def test_satellite_look_keeps_the_screen_moving(world, fx_offline):
    # three map scenes on the same place cut between close and wide shots
    scenes = [bw_render.Scene("map", "a", view=(-94.5, 49.0, 200.0)) for _ in range(3)]
    views = fx_offline.vary_views(scenes)
    assert len({v[2] for v in views}) == 3 and views[0] == (-94.5, 49.0, 200.0)
    # a card's number counts up: a year from 60 before it, others from 0
    assert fx_offline.FxRenderer.counting("1659", 0.0) == "1599" and fx_offline.FxRenderer.counting("1659", 1.0) == "1659"
    assert fx_offline.FxRenderer.counting("350 years", 0.0) == "0 years" and fx_offline.FxRenderer.counting("2 kings", 0.0) == "2 kings"
    # a flag on a place too small to fill is a badge; a scene on a small place with nothing on it gets a target
    data = {"title": "Why?", "cover_text": "Paris", "seed": 1, "look": "satellite", "scenes": [
        {"kind": "map", "narration": "One two three.", "focus": ["Minnesota"], "elements": [{"kind": "highlight", "place": "Minnesota"}]},
        {"kind": "map", "narration": "Four five six.", "focus": ["Paris"], "elements": [
            {"kind": "highlight", "place": "Paris", "flag": "Flag of France.svg", "cue": "four"}]},
        {"kind": "map", "narration": "Seven eight nine.", "focus": ["Paris"], "headline": "Here"},
        {"kind": "map", "narration": "Ten eleven twelve.", "focus": ["Minnesota"]}]}
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    assert r.main[1] == (world.find("Paris"), True) and r.main[2] == (world.find("Paris"), False)
    assert r.main[3] == (world.find("Minnesota"), False)
    calls = []
    for name in ("flag_badge", "target", "trace"):
        orig = getattr(r, name)
        setattr(r, name, lambda *a, _n=name, _o=orig, **k: (calls.append(_n), _o(*a, **k))[1])
    for scene in spec.scenes[1:]:
        r.frame(scene.end - 0.1)
    assert calls == ["flag_badge", "target", "trace"]


def test_a_satellite_tile_nasa_wont_send_comes_from_the_world_picture(monkeypatch, tmp_path):
    from PIL import Image

    from modules import bw_fx

    def slow(url, params=None, tries=3):
        raise OSError("read timed out")

    monkeypatch.setattr(bw_fx, "_get", slow)
    monkeypatch.setattr(bw_fx, "FX_DIR", tmp_path)
    sat = bw_fx.Satellite.__new__(bw_fx.Satellite)
    sat.world, sat.tiles, sat.failed = Image.new("RGB", (bw_fx.GLOBAL_PX, bw_fx.GLOBAL_PX // 2), (10, 90, 30)), {}, set()
    tile = sat.tile(0, 40, 6)
    px, deg = bw_fx.LEVELS[0]
    assert tile.size == (px * deg, px * deg) and tile.getpixel((5, 5)) == (10, 90, 30)
    assert not (tmp_path / "sat" / str(px) / "40_6.jpg").exists()  # not saved: the next render asks NASA again


def test_the_satellite_tiles_come_down_before_the_drawing_and_a_refused_one_is_not_asked_again(monkeypatch, tmp_path):
    import io

    from PIL import Image

    from modules import bw_fx

    asked = []

    def nasa(url, params=None, tries=3):
        asked.append(params["BBOX"])
        if len(asked) == 1:
            raise OSError("read timed out")
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (200, 10, 10)).save(buf, "JPEG")
        return buf.getvalue()

    monkeypatch.setattr(bw_fx, "_get", nasa)
    monkeypatch.setattr(bw_fx, "FX_DIR", tmp_path)
    sat = bw_fx.Satellite.__new__(bw_fx.Satellite)
    sat.world, sat.tiles, sat.failed = Image.new("RGB", (bw_fx.GLOBAL_PX, bw_fx.GLOBAL_PX // 2), (10, 90, 30)), {}, set()
    seen = []
    sat.fetch({(0, 1, 1)}, lambda f, m: seen.append(m))  # the first ask fails
    sat.fetch({(0, 1, 1), (0, 2, 1)}, lambda f, m: seen.append(m))
    assert len(asked) == 2 and sat.failed == {(0, 1, 1)}
    assert (tmp_path / "sat" / str(bw_fx.LEVELS[0][0]) / "2_1.jpg").is_file()
    assert seen[-1] == "Downloading the satellite map: 1/1 pictures"
    sat.tile(0, 1, 1)  # drawing: the world picture at once, NASA isn't asked a second time
    assert len(asked) == 2
    cam = bw_render.Cam(35.0, 31.5, 400.0)
    level, i0, i1, j0, j1 = bw_fx.Satellite.span(cam)
    assert level == 1 and i0 <= i1 and j0 <= j1
    assert bw_fx.Satellite.span(bw_render.Cam(35.0, 31.5, 10.0)) is None  # zoomed out: the world picture



def test_satellite_look_falls_back_to_flat_when_nasa_is_down(world, monkeypatch, tmp_path):
    from modules import bw_fx

    def down():
        raise OSError("GIBS unreachable")

    monkeypatch.setattr(bw_fx, "satellite", down)
    data = _fx_dict()
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    bw_render.prepare(spec)
    used = []
    from PIL import Image

    def frame(self, t):
        used.append(type(self).__name__)
        return Image.new("RGB", (bw_render.W, bw_render.H))

    monkeypatch.setattr(bw_render.Renderer, "frame", frame)
    monkeypatch.setattr(bw_render.subprocess, "Popen", lambda *a, **k: type("P", (), {"stdin": open(tmp_path / "raw", "wb"), "wait": lambda s: 0, "returncode": 0})())
    monkeypatch.setattr(bw_render.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    bw_render.render(spec, tmp_path / "v.mp3", tmp_path / "out.mp4")
    assert used and set(used) == {"Renderer"}


def test_check_wants_known_icons_and_flags_on_poles(world, monkeypatch, tmp_path):
    from modules import bw_fx

    monkeypatch.setattr(bw_fx, "CUSTOM_DIR", tmp_path / "no-custom-icons")
    assert bw_fx.icon_code("Money Bag") == "1f4b0" and bw_fx.icon_code("1f697") == "1f697" and bw_fx.icon_code("unicorn") == ""
    assert bw_fx.flag_name("Canada") == "Flag of Canada.svg" and bw_fx.flag_name("Flag_of_Canada.svg") == "Flag of Canada.svg"
    draft = _draft()
    draft.scenes[0].elements += [bw_script.ElementDraft(kind="icon", icon="unicorn"), bw_script.ElementDraft(kind="pole")]
    problems = " ".join(bw_script.check(draft, _research(), _find(world)))
    assert "unknown icon 'unicorn'" in problems and "a pole needs a flag" in problems
    on_place = bw_script.ElementDraft(kind="Flag", place="Canada", flag="Flag of Canada.svg")
    alone = bw_script.ElementDraft(kind="flag", flag="Flag of Canada.svg")
    assert (on_place.kind, alone.kind) == ("highlight", "pole")


# --------------------------------------------------------------------------- write it myself (any LLM)


def test_write_it_myself_goes_through_both_checks_with_pasted_answers(tmp_path, world, monkeypatch):
    from modules import bw_manual

    monkeypatch.setattr(bw_script, "research", lambda topic, progress=None: _research())
    s = bw_manual.start(article="Northwest Angle", question="Why does Minnesota have a notch?", out_dir=tmp_path)
    assert s["step"] == "write" and "SOURCES" in s["prompt"] and "ANSWER FORMAT" in s["prompt"] and len(s["sources"]) == 2

    with pytest.raises(bw_manual.ManualError):
        bw_manual.answer(s["id"], "Sorry, I can't help with that.", tmp_path)
    bad = _draft(narration_words=40)
    s = bw_manual.answer(s["id"], "Here you go:\n```json\n" + bad.model_dump_json() + "\n```", tmp_path, find=_find(world))
    assert s["step"] == "fix" and any("words" in p for p in s["problems"]) and "Your draft has problems" in s["prompt"]

    s = bw_manual.answer(s["id"], _draft().model_dump_json(), tmp_path, find=_find(world))
    assert s["step"] == "facts" and "Check a YouTube narration" in s["prompt"] and '{"unsupported": []}' in s["prompt"]
    with pytest.raises(bw_manual.ManualError):
        bw_manual.script(s["id"], tmp_path)

    s = bw_manual.facts(s["id"], '{"unsupported": ["The treaty was in 1784 - the sources say 1783"]}', tmp_path)
    assert s["step"] == "facts_fix" and "1784" in s["prompt"] and "fact checker" in s["prompt"]
    s = bw_manual.answer(s["id"], _draft().model_dump_json(), tmp_path, find=_find(world))
    s = bw_manual.facts(s["id"], '{"unsupported": []}', tmp_path)
    assert s["step"] == "ready" and s["prompt"] == ""

    script = bw_manual.script(s["id"], tmp_path)
    assert script["origin"] == "manual" and script["article"] == "Northwest Angle" and len(script["sources"]) == 2
    assert [x["id"] for x in bw_manual.sessions(tmp_path)] == [s["id"]]


def test_a_manual_script_needs_two_sources(tmp_path, monkeypatch):
    from modules import bw_manual

    only_wiki = _research()
    only_wiki.sources = only_wiki.sources[:1]
    monkeypatch.setattr(bw_script, "research", lambda topic, progress=None: only_wiki)
    with pytest.raises(bw_script.ScriptError, match="two sources"):
        bw_manual.start(article="Northwest Angle", out_dir=tmp_path)
    with pytest.raises(bw_manual.ManualError):
        bw_manual.view("../etc", tmp_path)


def test_your_own_sources_make_up_the_second_source(tmp_path, monkeypatch):
    from modules import bw_manual

    def only_wiki(topic, progress=None):
        res = _research()
        res.topic, res.sources = topic, res.sources[:1]
        return res

    read = []

    def own(entry, sid):
        read.append(entry)
        if "broken" in entry:
            raise bw_script.ScriptError("broken.example couldn't be read (403)")
        return bw_script.Source(sid, entry if entry.startswith("http") else f"https://en.wikipedia.org/wiki/{entry}", entry, "text " * 400)

    monkeypatch.setattr(bw_script, "research", only_wiki)
    monkeypatch.setattr(bw_script, "own_source", own)
    bw_topics.add(tmp_path, "Why is China shaped like this?", "Borders of China")

    # a link typed in the question box is read as a source; a topic on the list keeps its question and series
    view = bw_manual.start("https://mapgo.io/china", "borders of china", out_dir=tmp_path, more=["China", "China"])
    assert read == ["https://mapgo.io/china", "China"]
    assert [s["id"] for s in view["sources"]] == [1, 2, 3] and view["question"] == "Why is China shaped like this?"
    with pytest.raises(bw_script.ScriptError, match="broken.example couldn't be read.*More sources"):
        bw_manual.start(article="Pheasant Island", out_dir=tmp_path, more=["https://broken.example/x"])


def test_a_pasted_answer_with_unescaped_quotes_and_trailing_commas_is_read():
    from modules import bw_manual

    pasted = """Here it is:
{
"usable": false,
"reject_reason": "Source 2 is about King Wamba.",
"title": "Why does a Spanish town sit completely inside France?",
"claims": [
{
"claim": "Spain ceded northern Cerdanya to France.",
"source_id": 1,
"quote": "ceded the comarques of Roussillon, Conflent, Capcir, Vallespir, and northern Cerdanya ("Cerdagne") to the Kingdom of France"
},
],
"hashtags": ["geography", "history",]
}"""

    draft = bw_manual._parse(pasted, bw_script.Draft)

    assert draft.claims[0].quote.endswith('northern Cerdanya ("Cerdagne") to the Kingdom of France')
    assert draft.hashtags == ["geography", "history"] and not draft.usable
    assert bw_manual.repair_json('{"a": "x \\"y\\" z", "b": ["c"]}') == '{"a": "x \\"y\\" z", "b": ["c"]}'  # valid JSON stays
    with pytest.raises(bw_manual.ManualError):
        bw_manual._parse('{"title": }', bw_script.Draft)


def test_a_manual_script_is_made_with_its_origin(settings, tmp_path, world, monkeypatch):
    monkeypatch.setitem(SCRIPT, "origin", "manual")
    job, info, rendered = _make(settings, tmp_path, world)
    assert info["origin"] == "manual" and "origin" not in json.loads((tmp_path / job.key / "script.json").read_text())


def _custom_gif(path, frames=6, size=(80, 40), fade_out=0):
    """A wide transparent GIF: a square that moves right frame by frame; the last `fade_out` frames nearly empty."""
    from PIL import Image, ImageDraw

    ims = []
    for n in range(frames):
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        if n < frames - fade_out:
            ImageDraw.Draw(im).rectangle([10 + n * 4, 8, 30 + n * 4, 32], fill=(200, 30, 30, 255))
        else:
            ImageDraw.Draw(im).point((0, 0), fill=(200, 30, 30, 255))
        ims.append(im)
    ims[0].save(path, save_all=True, append_images=ims[1:], duration=500, loop=0, disposal=2)


def test_owner_icons_replace_noto_and_add_names(world, monkeypatch, tmp_path):
    """A GIF named like an icon in stickers/borderlinewhy is what the renderer draws for it, a new name becomes
    an icon the script may use, the Noto icons stay for the rest; frames keep their shape and end on the picture."""
    from modules import bw_fx, bw_script

    folder = tmp_path / "borderlinewhy"
    folder.mkdir()
    _custom_gif(folder / "lock.gif", fade_out=1)
    _custom_gif(folder / "War.gif", frames=1)
    (folder / "notes.txt").write_text("not an icon")
    monkeypatch.setattr(bw_fx, "CUSTOM_DIR", folder)
    monkeypatch.setattr(bw_fx, "_anims", {})
    monkeypatch.setattr(bw_fx, "_icons", {})
    assert bw_fx.icon_names()[-1] == "war" and "lock" in bw_fx.icon_names() and bw_fx.icon_names().count("lock") == 1
    assert bw_fx.icon_code("lock") == "lock" and bw_fx.icon_code("1f512") == "lock" and bw_fx.icon_code("WAR") == "war"
    assert bw_fx.icon_code("car") == "1f697" and bw_fx.icon_code("unicorn") == ""
    frames, ends = bw_fx.anim_frames("lock", 100)
    assert len(frames) == 5 and ends == pytest.approx([0.5, 1.0, 1.5, 2.0, 2.5])  # the near-empty last frame dropped
    assert all(f.size == frames[0].size for f in frames) and frames[0].height == 130 and 130 < frames[0].width <= 312
    im, animated = bw_fx.icon_frame("lock", 100, 1.2)
    assert animated and im is frames[2]
    assert bw_fx.anim_frames("war", 100) is None  # one frame: a still icon
    still, animated = bw_fx.icon_frame("war", 100, 0.5)
    assert not animated and still.height == 130
    draft = _draft()
    draft.scenes[0].elements += [bw_script.ElementDraft(kind="icon", icon="war"), bw_script.ElementDraft(kind="icon", icon="lock")]
    assert "unknown icon" not in " ".join(bw_script.check(draft, _research(), _find(world)))
    assert "war" in bw_script.write_prompt(_research())


def test_owner_icon_draws_wide_on_the_frame(monkeypatch, tmp_path):
    """A wide custom icon is drawn at its own shape (not squashed square), stacked copies and the pop-in included."""
    from PIL import Image

    from modules import bw_fx, bw_render

    folder = tmp_path / "borderlinewhy"
    folder.mkdir()
    _custom_gif(folder / "wave.gif")
    monkeypatch.setattr(bw_fx, "CUSTOM_DIR", folder)
    monkeypatch.setattr(bw_fx, "_anims", {})
    monkeypatch.setattr(bw_fx, "_icons", {})
    el = bw_render.Element(kind="icon", at=0.0, opts={"icon": bw_fx.icon_code("wave"), "x": 540, "y": 900, "count": 2})
    for t, wide in ((0.1, False), (2.0, True)):  # popping in it is tiny; grown, wider than tall
        ov = Image.new("RGBA", (bw_render.W, bw_render.H), (0, 0, 0, 0))
        bw_fx.FxRenderer.icon(bw_fx.FxRenderer.__new__(bw_fx.FxRenderer), ov, el, (540, 900), t)
        box = ov.getbbox()
        assert box and (box[2] - box[0] > 300) == wide and box[2] - box[0] > box[3] - box[1]


# --------------------------------------------------------------------------- long videos (any LLM)


def _chapter_json(words: int = 210) -> str:
    d = _draft(narration_words=words)
    scenes = []
    for s in d.scenes:  # a long video's scenes are short (bw_long.SCENE_WORDS): the long one split into several
        said = s.narration.split()
        for k in range(0, len(said), 30):
            part = s.model_dump() if k == 0 else {**s.model_dump(), "elements": [], "headline": ""}
            scenes.append({**part, "narration": " ".join(said[k:k + 30])})
    return json.dumps({"scenes": scenes, "claims": [c.model_dump() for c in d.claims]})


def _outline(n: int = 5) -> str:
    return json.dumps({"usable": True, "title": "Why does Minnesota have a notch?", "cover_text": "Cut off by a bad map",
                       "chapters": [{"title": f"Part {k}", "question": f"Question {k}?", "beats": ["a [1]", "b [2]"]} for k in range(1, n + 1)],
                       "description": "A map error.", "hashtags": ["maps"]})


@pytest.fixture
def portrait_after():
    yield
    bw_render.use_layout("portrait")


def test_long_video_goes_from_research_to_a_full_script(tmp_path, world, monkeypatch):
    from modules import bw_long

    monkeypatch.setattr(bw_script, "research", lambda topic, progress=None: _research())
    monkeypatch.setattr(bw_script, "wiki_text", lambda title: ("Lake of the Woods", SOURCE_1 * 20))
    s = bw_long.start("Why does Minnesota have a notch?", "Northwest Angle", ["Lake of the Woods"], out_dir=tmp_path)
    assert s["step"] == "research" and s["purpose"] == "research" and len(s["sources"]) == 3
    assert "fact - link" in s["prompt"]

    read = {"https://www.nps.gov/angle": bw_script.Source(0, "https://www.nps.gov/angle", "NPS", SOURCE_2 * 10)}
    monkeypatch.setattr(bw_long, "_read", lambda url: read.get(url))
    with pytest.raises(bw_long.LongError, match="no links"):
        bw_long.add_research(s["id"], "Some facts without links.", tmp_path)
    s = bw_long.add_research(s["id"], "The Angle is cut off - https://www.nps.gov/angle\nAnd - https://reddit.com/r/x\n"
                                      "Also - https://example.com/dead.", tmp_path)
    assert s["step"] == "outline" and s["research"] == {"links": 2, "read": 1, "wikipedia": 0} and s["sources"][-1]["id"] == 4

    s = bw_long.answer_outline(s["id"], _outline(2), tmp_path)
    assert s["step"] == "outline" and "2 chapters" in s["problems"][0] and "fix them" in s["prompt"]
    s = bw_long.answer_outline(s["id"], _outline(5), tmp_path)
    assert s["step"] == "chapter" and s["current"] == 0 and len(s["chapters"]) == 5
    assert "YOUR CHAPTER: 1. Part 1" in s["prompt"] and "hook" in s["prompt"] and "THE STORY SO FAR" not in s["prompt"]

    s = bw_long.answer_chapter(s["id"], 0, _chapter_json(40), tmp_path, find=_find(world))
    assert s["chapters"][0]["step"] == "fix" and "Your chapter has problems" in s["prompt"]
    for i in range(5):
        s = bw_long.answer_chapter(s["id"], i, _chapter_json(), tmp_path, find=_find(world))
        assert s["purpose"] == "facts" and s["chapters"][i]["step"] == "facts"
        if i == 1:
            s = bw_long.answer_facts(s["id"], i, '{"unsupported": ["1784 - the sources say 1783"]}', tmp_path)
            assert s["chapters"][i]["step"] == "facts_fix" and "1784" in s["prompt"]
            s = bw_long.answer_chapter(s["id"], i, _chapter_json(), tmp_path, find=_find(world))
        s = bw_long.answer_facts(s["id"], i, '{"unsupported": []}', tmp_path)
        if i < 4:
            assert s["current"] == i + 1 and "THE STORY SO FAR" in s["prompt"]
    assert s["step"] == "ready" and s["minutes"] > 4

    script = bw_long.full_script(s["id"], tmp_path)
    marks = [(k, sc["chapter"]["n"]) for k, sc in enumerate(script["scenes"]) if "chapter" in sc]
    assert marks == [(0, 1), (11, 2), (22, 3), (33, 4), (44, 5)] and script["layout"] == "landscape"
    s = bw_long.reopen(s["id"], 2, tmp_path)
    assert s["step"] == "chapter" and s["current"] == 2
    with pytest.raises(bw_long.LongError):
        bw_long.full_script(s["id"], tmp_path)
    assert [x["id"] for x in bw_long.sessions(tmp_path)] == [s["id"]]


def test_long_video_links_chapters_and_description():
    from modules import bw_long

    assert bw_long.links("a https://www.gov.uk/x). b https://youtube.com/watch?v=1 c https://www.gov.uk/x d <https://museum.org/y>") == [
        "https://www.gov.uk/x", "https://museum.org/y"]
    assert bw_long.stamp(65) == "1:05" and bw_long.stamp(3725) == "1:02:05"
    scenes = [bw_render.Scene("map", "a", start=0.4, opts={"chapter": {"n": 1, "title": "Hook"}}), bw_render.Scene("map", "b", start=40),
              bw_render.Scene("card", "c", start=95.5, opts={"chapter": {"n": 2, "title": "The treaty"}}),
              bw_render.Scene("map", "d", start=200, opts={"chapter": {"n": 3, "title": "Today"}})]
    times = bw_long.chapter_times(bw_render.Spec(scenes, []))
    assert times == [(0.0, "Hook"), (95.5, "The treaty"), (200, "Today")]
    text = bw_long.description({"question": "Why?", "description": "Because.", "sources": [{"title": "A", "url": "https://a"}]}, times)
    assert "Chapters:\n0:00 Hook\n1:35 The treaty\n3:20 Today\n\n🔔 Follow" in text


def test_long_video_draws_landscape_with_banners_and_a_thumbnail(world, fx_offline, tmp_path, portrait_after):
    from PIL import Image

    from modules import bw_long

    data = _fx_dict()
    data["scenes"][0]["chapter"] = {"n": 1, "title": "The notch"}
    data["scenes"][1]["chapter"] = {"n": 2, "title": "The treaty"}
    spec = bw_long.spec_for(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert (bw_render.W, bw_render.H) == (1920, 1080) and fx_offline.ICON_SLOTS == fx_offline.SLOTS["landscape"]["icon"]
    assert bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    card = spec.scenes[1]
    with_banner = np.asarray(r.frame(card.start + 1.0))
    assert with_banner.shape == (1080, 1920, 3)
    y = bw_render.CAPTION_Y - 120 - 100
    assert with_banner[y, 200].mean() < 60  # the dark banner over the paper
    assert "globe" not in spec.scenes[0].opts  # a long video opens tight too, not on a slow globe
    bar = np.asarray(r.frame(card.start + 0.5 * (card.end - card.start)))[20]
    gold = np.array(fx_offline.FIXED["gold"])
    assert np.abs(bar[60] - gold).max() < 30 and np.abs(bar[1880] - gold).max() > 60  # chapter 1 done, the last not yet
    out = bw_long.thumbnail(spec, tmp_path / "thumb.jpg")
    assert Image.open(out).size == (1280, 720)
    bw_render.use_layout("portrait")
    assert (bw_render.W, bw_render.H, fx_offline.W) == (1080, 1920, 1080)


def test_a_long_chapter_keeps_its_pictures_short_and_never_asks_twice():
    from modules import bw_long

    def chapter(*scenes):
        return bw_long.Chapter(scenes=[bw_script.SceneDraft(kind=k, narration=t) for k, t in scenes])

    words = lambda n: " ".join(["word"] * n) + "."  # noqa: E731
    good = chapter(("map", words(30)), ("photo", words(10)), ("board", words(20)), ("photo", words(12)))
    assert bw_long.pacing(good) == []
    bad = chapter(("photo", words(18)), ("photo", words(8)), ("board", words(30)), ("map", words(50)))
    found = bw_long.pacing(bad)
    assert len(found) == 4 and "both photos" in found[3] and "scene 1 (photo) has 18" in found[0]
    before = chapter(("map", "The line was impossible. So how did diplomats try to fix the impossible line?"))
    again = chapter(("map", "How did surveyors and diplomats try to correct the error? In 1814 they met."))
    assert "closing question again" in bw_long.pacing(again, before)[0]
    answer = chapter(("map", "In 1814 the Treaty of Ghent tried to fix it."))
    assert bw_long.pacing(answer, before) == []
    assert "opens on a photo" in bw_long.pacing(chapter(("photo", words(5))), chapter(("photo", words(5))))[0]


def test_long_video_make_runs_in_its_own_process(tmp_path, world, monkeypatch):
    from modules import bw_long

    started = []

    class Proc:
        pid = 999_999_999

    monkeypatch.setattr(bw_long.subprocess, "Popen", lambda cmd, **kw: started.append(cmd) or Proc())
    monkeypatch.setattr(bw_script, "research", lambda topic, progress=None: _research())
    s = bw_long.start("Why?", "Northwest Angle", out_dir=tmp_path)
    with pytest.raises(bw_long.LongError, match="Not every chapter"):
        bw_long.start_make(s["id"], tmp_path)
    s = bw_long.skip_research(s["id"], tmp_path)
    s = bw_long.answer_outline(s["id"], _outline(5), tmp_path)
    for i in range(5):
        bw_long.answer_chapter(s["id"], i, _chapter_json(), tmp_path, find=_find(world))
        s = bw_long.answer_facts(s["id"], i, '{"unsupported": []}', tmp_path)
    bw_long.start_make(s["id"], tmp_path)
    assert started[0][1:5] == ["-m", "modules.bw_long", "make", s["id"]]
    # that pid is no process: the tab says it stopped
    j = bw_long.job(s["id"], tmp_path)
    assert j["running"] is False and "stopped" in j["error"]


def test_long_video_research_with_only_wikipedia_markdown_links_adds_the_articles(tmp_path, monkeypatch):
    from modules import bw_long

    monkeypatch.setattr(bw_script, "research", lambda topic, progress=None: _research())
    read = []
    monkeypatch.setattr(bw_script, "wiki_text", lambda title: (read.append(title) or title, SOURCE_1 * 20))
    s = bw_long.start("Why?", "Northwest Angle", out_dir=tmp_path)
    answer = ("* The Angle is cut off. - [https://en.wikipedia.org/wiki/Northwest_Angle](https://en.wikipedia.org/wiki/Northwest_Angle)\n"
              "* 1783. - [https://en.wikipedia.org/wiki/Treaty_of_Paris_(1783](https://en.wikipedia.org/wiki/Treaty_of_Paris_(1783))\n"
              "* Border. - [https://en.wikipedia.org/wiki/Canada%E2%80%93United_States_border](https://en.wikipedia.org/wiki/Canada%E2%80%93United_States_border)")
    s = bw_long.add_research(s["id"], answer, tmp_path)
    assert s["step"] == "outline" and s["research"] == {"links": 0, "read": 0, "wikipedia": 2}
    assert read == ["Treaty of Paris (1783)", "Canada–United States border"]
    assert [x["title"] for x in s["sources"][-2:]] == ["Treaty of Paris (1783) - Wikipedia", "Canada–United States border - Wikipedia"]
    assert "Not Wikipedia" in bw_long.RESEARCH_PROMPT


def test_owner_icons_have_short_names(tmp_path, monkeypatch):
    from modules import bw_fx

    for name in ("king_waving.gif", "horse_trotting.gif", "settlers.gif"):
        (tmp_path / name).write_bytes(b"")
    monkeypatch.setattr(bw_fx, "CUSTOM_DIR", tmp_path)
    assert bw_fx.icon_code("King") == "king" and bw_fx.icon_code("king_waving") == "king_waving"
    assert bw_fx.icon_code("horse") == "horse"  # the owner's horse, not the Noto one
    extra = [n for n in bw_fx.icon_names() if n not in bw_fx.ICONS]
    assert extra == ["settlers", "king"]  # the short name only, in the prompt's list


def test_a_landscape_camera_never_runs_off_the_world_picture(portrait_after):
    from modules import bw_fx

    bw_render.use_layout("landscape")
    lon, lat, ppd = bw_fx.inside_world(-100.0, 50.0, 3.0)  # zoomed out on North America
    cam = bw_render.Cam(lon, lat, ppd)
    (x0, y0), (x1, y1) = cam.lonlat(0, bw_render.H), cam.lonlat(bw_render.W, 0)
    assert -180 <= x0 and x1 <= 180 and -88 <= y0 and y1 <= 88
    assert bw_fx.inside_world(-94.8, 49.3, 400.0) == (-94.8, 49.3, 400.0)  # a close view is left alone


def test_flags_follow_the_name_the_narration_uses(monkeypatch):
    """A period flag of a country that still exists is shown as today's flag (the German Empire's stripes next to
    "Germany" read as the wrong flag); vanished states named as such keep their own, and a dated flag Commons
    only has under its dates is still found."""
    from modules import bw_fx

    assert bw_fx.modern_flag("Flag of the German Empire.svg") == "Flag of Germany.svg"
    assert bw_fx.modern_flag("German Empire") == "Flag of Germany.svg"
    assert bw_fx.modern_flag("Flag of Great Britain (1707–1800).svg") == "Flag of the United Kingdom.svg"
    assert bw_fx.modern_flag("Flag of the United States (1777–1795).svg") == "Flag of the United States.svg"
    assert bw_fx.modern_flag("Flag of the Russian Empire.svg") == "Flag of Russia.svg"
    assert bw_fx.modern_flag("Flag of the Netherlands (1795–1806).svg") == "Flag of the Netherlands.svg"
    for own in ("Flag of the Ottoman Empire.svg", "Flag of the Soviet Union.svg", "Flag of Canada.svg", ""):
        assert bw_fx.modern_flag(own) == own
    have = {"Flag of Germany.svg", "Flag of Prussia (1892–1918).svg"}

    def flag(name):
        if name not in have:
            raise FileNotFoundError(name)
        from PIL import Image

        return Image.new("RGB", (4, 2))

    monkeypatch.setattr(bw_fx, "flag_image", flag)
    assert bw_fx.usable_flag("Flag of the German Empire.svg") == "Flag of Germany.svg"
    assert bw_fx.usable_flag("Flag of Prussia (1892–1918).svg") == "Flag of Prussia (1892–1918).svg"
    assert bw_fx.usable_flag("Flag of Atlantis.svg") == ""
    assert "Flag of Germany.svg" in bw_script.SCENE_RULES and "flag of that time" not in bw_script.SCENE_RULES


def test_a_cards_flags_and_icons_stand_in_a_row_above_its_text(world, fx_offline):
    """Two poles and two icons on a treaty card: one evenly spaced row on the paper above the kicker, in script
    order, none of them on the subtitle (the card slots put them there on 2026-10-30)."""
    data = _fx_dict()
    data["scenes"][1]["elements"] = [{"kind": "pole", "flag": "Canada"}, {"kind": "icon", "icon": "money_bag"},
                                     {"kind": "pole", "flag": "Canada"}, {"kind": "icon", "icon": "car"}]
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    bw_render.prepare(spec)
    fx_offline.FxRenderer(spec)  # dresses the spec
    card = spec.scenes[1]
    assert card.kind == "card"
    els = [e for e in card.elements if e.kind in ("pole", "icon")]
    assert [e.kind for e in els] == ["pole", "icon", "pole", "icon"]
    for e in els:  # everything ends above the kicker at CARD_Y, below a headline at HEAD_Y
        bottom = e.opts["y"] if e.kind == "pole" else e.opts["y"] + e.opts["size"] / 2
        top = e.opts["y"] - e.opts.get("height", 230) if e.kind == "pole" else e.opts["y"] - e.opts["size"] / 2
        assert bw_render.HEAD_Y + 60 < top and bottom < bw_render.CARD_Y - 40, (e.kind, e.opts)
    # the row is packed by what each one draws (a pole's flag width, an icon's picture width), a gap of 40 between
    # neighbours, and centered as a whole
    edges = [(e.opts["x"], e.opts["x"] + e.opts.get("width", 190)) if e.kind == "pole"
             else (e.opts["x"] - e.opts["size"] / 2, e.opts["x"] + e.opts["size"] / 2) for e in els]
    assert all(abs((b[0] - a[1]) - 40) <= 1 for a, b in zip(edges, edges[1:]))
    assert abs((edges[0][0] + edges[-1][1]) / 2 - bw_render.W / 2) <= 1
    assert all(e.opts["size"] <= 200 for e in els if e.kind == "icon")  # four in a row: smaller icons
    # one icon alone sits in the middle of the row
    data["scenes"][1]["elements"] = [{"kind": "icon", "icon": "money_bag"}]
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    bw_render.prepare(spec)
    fx_offline.FxRenderer(spec)
    one = next(e for e in spec.scenes[1].elements if e.kind == "icon")
    assert (one.opts["x"], one.opts["y"], one.opts["size"]) == (bw_render.W // 2, 500, 250)


def test_check_finds_the_photos(world):
    draft = _draft()
    draft.scenes[1] = bw_script.SceneDraft(kind="photo", narration="In 1783 a treaty used an old map.", photo="Mitchell Map",
                                           caption="The Mitchell Map")
    ok = bw_script.check(draft, _research(), _find(world), photo_find=lambda q: object())
    assert ok == []
    bad = " | ".join(bw_script.check(draft, _research(), _find(world), photo_find=lambda q: None))
    assert "no picture found for 'Mitchell Map'" in bad


# --------------------------------------------------------------------------- the numbered series and the comment

from modules import bw_series  # noqa: E402


def test_series_of_a_topic():
    assert bw_series.classify("Why is Gambia a thin strip inside Senegal?", "The Gambia") == "Weird Borders"  # a tagged seed
    assert bw_series.classify("Why is Canberra the capital?", "Canberra") == ""
    assert bw_series.classify("Why does a Dutch village sit inside Belgium?", "X") == "Weird Borders"  # added by hand: its words
    assert bw_series.classify("Why is Canberra the capital?", "Canberra", tag="weird borders") == "Weird Borders"  # its own tag
    assert bw_series.classify("Why is there an enclave here?", "Y", tag="") == ""  # said "none"
    seeds = {t.article: t.episode_of for t in bw_topics.all_topics(bw_topics.TopicState())}
    assert seeds["Northwest Angle"] == "Weird Borders" and seeds["Time in China"] == ""
    assert set(bw_series.SEED_ARTICLES) <= set(seeds)  # no typo in the hand-tagged list


def test_series_title_is_numbered_and_fits():
    assert bw_series.titled("Why is Gambia inside Senegal?", "Weird Borders", 7) == "Why is Gambia inside Senegal? | Weird Borders #7"
    assert bw_series.titled("Weird Borders #3: Why is Gambia inside Senegal?", "Weird Borders", 7).startswith("Why is Gambia")
    assert bw_series.strip("Why Is China Shaped Like This?") == "Why Is China Shaped Like This?"  # the series' own question stays
    assert bw_series.strip("Why Is China Shaped Like This? | Shaped Like This #4") == "Why Is China Shaped Like This?"
    assert bw_series.strip("Shaped Like This: Chile") == "Chile" and bw_series.strip("Chile - Weird Borders") == "Chile"
    long = bw_series.titled("Why " + "very " * 30 + "odd?", "Weird Borders", 12)
    assert len(long) <= bw_series.TITLE_ROOM and long.endswith(" | Weird Borders #12")
    assert bw_channel.publish._yt_title(long).endswith("| Weird Borders #12 #shorts")


def test_series_counter_reserves_confirms_and_gives_back(tmp_path):
    assert bw_series.peek(tmp_path, "Weird Borders") == 1
    assert bw_series.reserve(tmp_path, "Weird Borders", "a") == 1
    assert bw_series.reserve(tmp_path, "Weird Borders", "a") == 1  # a retry keeps its number
    bw_series.confirm(tmp_path, "Weird Borders", "a", "vid")
    assert bw_series.reserve(tmp_path, "Weird Borders", "b") == 2
    bw_series.release(tmp_path, "Weird Borders", "b")  # its upload failed: #2 is free again
    assert bw_series.peek(tmp_path, "Weird Borders") == 2
    bw_series.release(tmp_path, "Weird Borders", "a")  # an uploaded episode keeps its number
    assert bw_series.number_of(tmp_path, "Weird Borders", "a") == 1
    data = bw_series.load(tmp_path)
    data["Weird Borders"]["start"] = 10  # the owner continues an older count
    bw_series._save(data, tmp_path)
    assert bw_series.peek(tmp_path, "Weird Borders") == 11
    assert bw_series.summary(tmp_path)[0] == {"series": "Weird Borders", "last": 1, "next": 11, "episodes": 1}


class FakeComments(FakeYouTube):
    def __init__(self, fail=False):
        super().__init__()
        self.comments, self.fail = [], fail

    def commentThreads(self):
        return self

    def insert(self, part, body):
        if self.fail:
            raise RuntimeError("comments are off")
        self.comments.append(body)
        return self

    def execute(self):
        return {"id": "c1"} if self.comments else {}


def _fake_upload(monkeypatch, uploads, fail=False):
    def upload_file(path, req, as_short, progress, youtube):
        if fail:
            raise bw_channel.publish.PublishError("quota")
        uploads.append(req)
        return {"video_id": f"v{len(uploads)}", "url": f"https://youtu.be/v{len(uploads)}", "privacy": req.privacy}

    monkeypatch.setattr(bw_channel.publish, "upload_file", upload_file)


def test_series_episodes_are_numbered_at_the_upload(settings, tmp_path, world, monkeypatch):
    first, _, _ = _make(settings, tmp_path, world, slot="14:30")
    stopped, _, _ = _make(settings, tmp_path, world, slot="23:00")
    later, _, _ = _make(settings, tmp_path, world, day=Day(2026, 11, 3))
    assert bw_channel.load_info(first.key, tmp_path)["series"] == "Weird Borders"
    numbers = bw_channel.expected_numbers(bw_channel.videos(tmp_path), tmp_path)
    assert [numbers[k] for k in (first.key, stopped.key, later.key)] == [1, 2, 3]  # the Plan shows them in slot order
    announced = []
    monkeypatch.setattr(bw_channel, "announce_upload", lambda info, record, settings=None: announced.append((info["title"], record)))

    uploads = []
    _fake_upload(monkeypatch, [], fail=True)
    with pytest.raises(bw_channel.publish.PublishError):
        bw_channel.upload(first.key, youtube=FakeComments(), out_dir=tmp_path)
    assert bw_series.peek(tmp_path, "Weird Borders") == 1  # a failed upload uses no number

    _fake_upload(monkeypatch, uploads)
    yt = FakeComments()
    bw_channel.upload(first.key, youtube=yt, out_dir=tmp_path)
    assert uploads[0].title == SCRIPT["title"] + " | Weird Borders #1"
    saved = bw_channel.load_info(first.key, tmp_path)
    assert saved["title"] == uploads[0].title and saved["base_title"] == SCRIPT["title"]
    assert saved["episode"] == {"series": "Weird Borders", "number": 1}
    assert announced[-1][0] == uploads[0].title  # Discord shows the numbered title

    bw_channel.veto(stopped.key, tmp_path)  # stopped before its upload: it takes no number
    bw_channel.upload(later.key, youtube=yt, out_dir=tmp_path)
    assert uploads[1].title.endswith("| Weird Borders #2")
    assert bw_channel.display_title(bw_channel.load_info(later.key, tmp_path)) == uploads[1].title
    assert bw_channel.upload_request(bw_channel.load_info(first.key, tmp_path)).title == uploads[0].title  # never numbered twice


def test_the_comment_is_posted_after_the_upload(settings, tmp_path, world, monkeypatch):
    job, _, _ = _make(settings, tmp_path, world)
    monkeypatch.setattr(bw_channel, "announce_upload", lambda *a, **k: None)
    _fake_upload(monkeypatch, [])
    yt = FakeComments()
    record = bw_channel.upload(job.key, youtube=yt, out_dir=tmp_path)
    body = yt.comments[0]["snippet"]
    assert body["videoId"] == "v1" and body["topLevelComment"]["snippet"]["textOriginal"] == "Which weird border should I cover next?"
    saved = bw_channel.load_info(job.key, tmp_path)["uploads"]["youtube"]
    assert saved["comment"]["url"] == "https://www.youtube.com/watch?v=v1&lc=c1" and record["comment"] == saved["comment"]
    line = bw_channel.comment_line(saved)
    assert "Pin it" in line and "Which weird border should I cover next?" in line
    assert bw_channel.comment(bw_channel.load_info(job.key, tmp_path), yt, tmp_path) == saved["comment"] and len(yt.comments) == 1  # once


class FakeThumbs(FakeComments):
    def __init__(self, refuse=False):
        super().__init__()
        self.thumbs, self.refuse = [], refuse

    def thumbnails(self):
        return self

    def set(self, videoId, media_body):
        if self.refuse:
            raise RuntimeError("The authenticated user doesn't have permissions to upload and set custom video thumbnails")
        self.thumbs.append((videoId, media_body))
        return self


class FakeProcessing(FakeThumbs):
    """A YouTube that is still processing the video for the first `busy` asks, then done."""

    def __init__(self, busy=2):
        super().__init__()
        self.busy, self.asked, self.status = busy, 0, ""

    def videos(self):
        return self

    def list(self, part, id):
        self.asked += 1
        self.status = "processing" if self.asked <= self.busy else "succeeded"
        return self

    def execute(self):
        return {"items": [{"processingDetails": {"processingStatus": self.status}}]}


def test_the_thumbnail_is_set_again_once_youtube_has_processed_the_video(settings, tmp_path, world, monkeypatch):
    """YouTube accepts a thumbnail set seconds after the upload, then replaces it with its own frame when the
    processing ends (all three channels, 2026-11-02/03): so it is set again, in the background, once processed."""
    from trendclip import publish

    from trendclip import doctor

    monkeypatch.setattr(publish, "PROCESSING_POLL", 0.0)
    monkeypatch.setattr(doctor, "verify_thumbnail", lambda video_id, path, wait=None: "set")  # the doctor's own tests cover it
    job, _, _ = _make(settings, tmp_path, world)
    (tmp_path / job.key / "thumbnail.jpg").write_bytes(b"\xff\xd8 a jpeg")
    _fake_upload(monkeypatch, [])
    monkeypatch.setattr(bw_channel, "announce_text", lambda text, settings=None, components=None: None)
    started = []
    monkeypatch.setattr(publish, "keep_thumbnail", lambda client, vid, path, on_set=None: started.append((client, vid, path, on_set)))
    yt = FakeProcessing()
    record = bw_channel.upload(job.key, youtube=yt, out_dir=tmp_path)
    assert record["thumbnail"] == "set" and len(yt.thumbs) == 1 and len(started) == 1
    client, vid, path, on_set = started[0]
    assert client() is yt and vid == "v1" and path == tmp_path / job.key / "thumbnail.jpg"
    assert publish.keep_thumbnail_now(client, vid, path, on_set) == "set"
    assert yt.asked == 3 and len(yt.thumbs) == 2  # waited two asks, then set it again
    assert bw_channel.load_info(job.key, tmp_path)["uploads"]["youtube"]["thumbnail"] == "set after processing"
    assert publish.keep_thumbnail_now(lambda: FakeThumbs(), "v2", path) == "unknown"  # a client that can't say: left alone


def test_the_thumbnail_goes_up_with_the_video(settings, tmp_path, world, monkeypatch):
    """The dashboard's thumbnail is set on YouTube right after the upload; a refusal leaves the video up and says so."""
    job, _, _ = _make(settings, tmp_path, world)
    texts = []
    monkeypatch.setattr(bw_channel, "announce_text", lambda text, settings=None, components=None: texts.append(text))
    (tmp_path / job.key / "thumbnail.jpg").write_bytes(b"\xff\xd8 a jpeg")
    _fake_upload(monkeypatch, [])
    yt = FakeThumbs()
    record = bw_channel.upload(job.key, youtube=yt, out_dir=tmp_path)
    assert record["thumbnail"] == "set" and yt.thumbs[0][0] == "v1" and "thumbnail" not in texts[-1]
    assert bw_channel.load_info(job.key, tmp_path)["uploads"]["youtube"]["thumbnail"] == "set"

    again, _, _ = _make(settings, tmp_path, world, slot="23:00")
    (tmp_path / again.key / "thumbnail.jpg").write_bytes(b"\xff\xd8 a jpeg")
    record = bw_channel.upload(again.key, youtube=FakeThumbs(refuse=True), out_dir=tmp_path)
    assert record["video_id"] and record["thumbnail"].startswith("not set: The authenticated user")
    assert "The thumbnail didn't go up: set it in YouTube Studio" in texts[-1]


def test_a_failed_comment_is_reported_and_can_be_retried(settings, tmp_path, world, monkeypatch):
    job, _, _ = _make(settings, tmp_path, world)
    texts = []
    monkeypatch.setattr(bw_channel, "announce_text", lambda text, settings=None, components=None: texts.append((text, components)))
    _fake_upload(monkeypatch, [])
    bw_channel.upload(job.key, youtube=FakeComments(fail=True), out_dir=tmp_path)
    text, components = texts[-1]
    assert "comment didn't go through: comments are off" in text
    assert any(b.get("custom_id") == f"tc:bwcm:{job.key}" for row in components for b in row["components"])
    found = bw_channel.comment_again(job.key, tmp_path, FakeComments())
    assert found["text"] == "Which weird border should I cover next?"
    assert "comment_error" not in bw_channel.load_info(job.key, tmp_path)["uploads"]["youtube"]


def test_a_private_upload_gets_no_comment_and_a_plain_video_no_number(settings, tmp_path, world, monkeypatch):
    monkeypatch.setattr(bw_channel, "announce_upload", lambda *a, **k: None)
    uploads = []
    _fake_upload(monkeypatch, uploads)
    job, _, _ = _make(settings, tmp_path, world)
    info = bw_channel.load_info(job.key, tmp_path)
    bw_channel.save_info({**info, "series": ""}, tmp_path)  # outside any series
    yt = FakeComments()
    bw_channel.upload(job.key, "private", youtube=yt, out_dir=tmp_path)
    assert uploads[0].title == SCRIPT["title"] and yt.comments == []
    assert "public or unlisted" in bw_channel.load_info(job.key, tmp_path)["uploads"]["youtube"]["comment_error"]
    assert bw_series.load(tmp_path) == {}


def test_topic_picking_and_the_prompt_know_the_series(tmp_path, monkeypatch):
    monkeypatch.delenv("BW_SERIES_EVERY", raising=False)
    state = bw_topics.TopicState(used={a: "x" for q, a in bw_topics.SEEDS})
    state.proposed = [bw_topics.Topic(question="Why is Ottawa the capital?", article="Ottawa", source="gemini", series=""),
                      bw_topics.Topic(question="Why is Point X cut off?", article="Point X", source="gemini", series="Weird Borders")]
    bw_topics.save(state, tmp_path)
    assert bw_topics.next_topic(tmp_path).article == "Ottawa"
    assert bw_series.due([""]) == "Weird Borders" and bw_series.due(["Weird Borders"]) == ""
    assert bw_topics.next_topic(tmp_path, prefer="Weird Borders").article == "Point X"
    bw_topics.add(tmp_path, "Why is Paris the capital?", "Paris")  # added by hand: it still goes first
    assert bw_topics.next_topic(tmp_path, prefer="Weird Borders").article == "Paris"

    episode = _research()
    note = bw_script.write_prompt(episode)
    assert 'next episode of the channel\'s numbered series "Weird Borders"' in note
    plain = bw_script.Research(bw_topics.Topic(question="Why is Canberra the capital?", article="Canberra", series=""), "Canberra", episode.sources)
    assert "numbered series" not in bw_script.write_prompt(plain)
    assert bw_script.finish(_draft(), episode)["series"] == "Weird Borders"
    assert "Weird Borders" in bw_topics.propose_prompt([]) and '"series": "Weird Borders"' in bw_topics.LLM_FORMAT


def test_check_keeps_the_series_out_of_the_title(world):
    draft = _draft()
    draft.title = "Why does Minnesota have a notch? | Weird Borders #3"
    assert any("series name" in p for p in bw_script.check(draft, _research(), _find(world)))


# --------------------------------------------------------------------------- Shaped Like This, transfers, what-ifs

from modules import bw_whatif  # noqa: E402


def test_shaped_like_this_series_and_rotation(monkeypatch):
    monkeypatch.delenv("BW_SERIES_EVERY", raising=False)
    assert bw_series.classify("Why is Chile shaped like this?", "Geography of Chile") == "Shaped Like This"
    seeds = {t.article: t.episode_of for t in bw_topics.all_topics(bw_topics.TopicState())}
    assert seeds["Geography of Chile"] == "Shaped Like This" and seeds["Lesotho"] == "Weird Borders"
    assert bw_series.due(["Weird Borders"]) == ""  # the last one was an episode
    assert bw_series.due(["", "Weird Borders"]) == "Shaped Like This"  # the series made least lately takes its turn
    assert bw_series.due(["", "Shaped Like This", "Weird Borders"]) == "Weird Borders"
    assert bw_series.comment_text("Shaped Like This") == "Which country's shape should I explain next?"
    assert bw_series.comment_text("Weird Borders") == "Which weird border should I cover next?"
    assert "map next" in bw_series.comment_text("", "whatif")
    note = bw_script.series_note(bw_topics.Topic(question="Why is Chile shaped like this?", article="Geography of Chile"))
    assert '"Shaped Like This"' in note and "board_draw" in note


def test_add_a_country(tmp_path):
    exists = {"Geography of Chile"}
    topic = bw_topics.add_country(tmp_path, " chile ", check=lambda t: t if t in exists else None, on_map=lambda n: object())
    assert topic.question == "Why is Chile shaped like this?" and topic.article == "Geography of Chile"
    assert bw_topics.next_topic(tmp_path).article == "Geography of Chile"  # added by hand: made next
    assert bw_topics.next_topic(tmp_path).episode_of == "Shaped Like This"
    with pytest.raises(ValueError, match="not a country"):
        bw_topics.add_country(tmp_path, "Atlantis", check=lambda t: t, on_map=lambda n: None)


def test_a_transfer_is_the_old_owner_then_the_new():
    spec = bw_render.spec_from_dict({"title": "t", "scenes": [{"kind": "map", "narration": "Russia sold Alaska.", "elements": [
        {"kind": "transfer", "place": "Alaska", "from_flag": "Flag of Russia.svg", "flag": "Flag of the United States.svg",
         "text": "Sold 1867", "cue": "sold", "color": "a"}]}]}, _words("Russia sold Alaska."))
    old, new = spec.scenes[0].elements
    assert (old.kind, old.place, old.opts["flag"], old.opts["_side"], old.cue) == ("highlight", "Alaska", "Flag of Russia.svg", "from", "")
    assert (new.opts["flag"], new.opts["_side"], new.cue, new.text, new.color) == ("Flag of the United States.svg", "to", "sold", "Sold 1867", "a")
    assert old.color == "b"
    fills = {}
    feat = bw_geo.Feature("region", "Alaska")
    bw_render.add_fill(fills, feat, (200, 0, 0), 1.0)
    bw_render.add_fill(fills, feat, (0, 0, 200), 0.0)  # not there yet: the old owner stays
    assert fills[id(feat)] == ((200, 0, 0), 1.0)
    bw_render.add_fill(fills, feat, (0, 0, 200), 0.5)
    assert fills[id(feat)] == ((100, 0, 100), 1.0)


def test_check_wants_both_transfer_flags(world):
    draft = _draft()
    draft.scenes[0].elements.append(bw_script.ElementDraft(kind="transfer", place="Minnesota", flag="Flag of the United States.svg"))
    assert any("both flags" in p for p in bw_script.check(draft, _research(), _find(world)))
    draft.scenes[0].elements[-1].from_flag = "Flag of the United Kingdom.svg"
    assert bw_script.check(draft, _research(), _find(world)) == []


def _whatif_json(**changes):
    draft = _draft()
    draft.title = "What if Minnesota had no notch?"
    draft.claims = []
    draft.scenes[4].narration = "So the border might have run straight."
    draft.scenes[4].elements.append(bw_script.ElementDraft(kind="transfer", place="Minnesota", color="b", cue="border"))
    for k, v in changes.items():
        setattr(draft, k, v)
    return draft.model_dump_json()


def test_a_what_if_from_prompt_to_queue_to_video(settings, tmp_path, world, monkeypatch):
    s = bw_whatif.start("Alaska was still Russian", tmp_path)
    assert s["question"] == "What if Alaska was still Russian?" and s["step"] == "write"
    assert "WHAT IF episode" in s["prompt"] and "transfer" in s["prompt"] and '"claims": [],' in s["prompt"]
    bad = bw_whatif.answer(s["id"], _whatif_json(title="Why does Minnesota have a notch?"), tmp_path, find=_find(world))
    assert bad["step"] == "fix" and any('start with "What if"' in p for p in bad["problems"]) and "missed" not in bad["prompt"]
    good = bw_whatif.answer(s["id"], _whatif_json(), tmp_path, find=_find(world))
    assert good["step"] == "ready", good["problems"]
    queued = bw_whatif.queue(s["id"], tmp_path)
    assert queued["step"] == "queued"
    path = bw_channel.queued_scripts(tmp_path)[0]
    script = json.loads(path.read_text())
    assert script["kind"] == "whatif" and script["series"] == "" and script["sources"] == []
    assert any(e["kind"] == "transfer" for sc in script["scenes"] for e in sc["elements"])

    def voice(text, out, progress):
        out.write_bytes(b"mp3")
        ws = _words(text)
        ws[-1] = bw_render.Word(ws[-1].text, 45 - bw_render.TAIL - 0.3, 45 - bw_render.TAIL)
        return ws

    job = bw_channel.job_for(settings, Day(2026, 11, 2), "14:30")
    info = bw_channel.make(settings, job, out_dir=tmp_path, script={**_spec_dict(), **script}, voice=voice,
                           render=lambda spec, v, out, *a: out.write_bytes(b"\x00"))
    assert info["check"] == [] and info["kind"] == "whatif" and info["series"] == ""  # no sources needed, no number
    assert "Nobody knows for sure" in info["description"] and "Sources:" not in info["description"]
    monkeypatch.setattr(bw_channel, "announce_upload", lambda *a, **k: None)
    _fake_upload(monkeypatch, uploads := [])
    yt = FakeComments()
    bw_channel.upload(job.key, youtube=yt, out_dir=tmp_path)
    assert uploads[0].title == "What if Minnesota had no notch?"
    assert yt.comments[0]["snippet"]["topLevelComment"]["snippet"]["textOriginal"] == bw_series.COMMENTS["whatif"]


def test_the_owner_picks_the_next_video(tmp_path):
    root = tmp_path
    queue = bw_channel.queue_dir(root)
    queue.mkdir(parents=True)
    (queue / "001_a.json").write_text(json.dumps({**SCRIPT, "title": "Queued A"}))
    (queue / "002_b.json").write_text(json.dumps({**SCRIPT, "title": "Queued B"}))
    choices = bw_channel.next_choices(root)
    assert choices["choice"] == "" and [o["label"] for o in choices["options"][:2]] == ["📥 Queued A", "📥 Queued B"]
    assert choices["options"][2]["value"] == f"topic:{bw_topics.SEEDS[0][1]}"

    pick = "Geography of Chile"
    bw_topics.pick(root, f"topic:{pick}")  # a topic goes before the queue and before the series' turn
    assert bw_topics.next_topic(root, prefer="Weird Borders").article == pick
    assert bw_channel.next_choices(root)["choice"] == f"topic:{pick}"
    assert next(t for t in bw_topics.listing(root) if t["article"] == pick)["picked"]
    seen = []
    script, origin = bw_channel.next_script(out_dir=root, research=lambda topic, p: seen.append(topic.article) or topic,
                                            write=lambda res, client, progress: {"title": res.question})
    assert origin == f"topic:{pick}" and seen == [pick] and len(bw_channel.queued_scripts(root)) == 2
    bw_topics.mark(root, pick, used=True)  # make() marks it: the pick is used up
    assert bw_topics.picked(root) == ""

    bw_topics.pick(root, "queue:002_b.json")
    script, origin = bw_channel.next_script(out_dir=root)
    assert script["title"] == "Queued B" and bw_topics.picked(root) == ""
    script, origin = bw_channel.next_script(out_dir=root)
    assert script["title"] == "Queued A"  # then the usual order
    with pytest.raises(ValueError):
        bw_topics.pick(root, "topic:Nowhere")


def test_the_owner_removes_a_planned_video(tmp_path):
    root = tmp_path
    queue = bw_channel.queue_dir(root)
    queue.mkdir(parents=True)
    (queue / "001_a.json").write_text(json.dumps({**SCRIPT, "title": "Queued A"}))
    gaza = bw_topics.add(root, "Why is Gaza separated from the West Bank?", "Gaza Strip")
    bw_topics.pick(root, "topic:Gaza Strip")
    bw_channel.remove_next("topic:Gaza Strip", root)  # the pick and the topic both go
    values = [o["value"] for o in bw_channel.next_choices(root)["options"]]
    assert "topic:Gaza Strip" not in values and bw_topics.picked(root) == ""
    assert next(t for t in bw_topics.listing(root) if t["article"] == "Gaza Strip")["status"] == "dropped"
    bw_topics.add(root, gaza.question, gaza.article)  # added again: it is back
    assert "topic:Gaza Strip" in [o["value"] for o in bw_channel.next_choices(root)["options"]]

    bw_topics.pick(root, "queue:001_a.json")
    bw_channel.remove_next("queue:001_a.json", root)
    assert bw_channel.queued_scripts(root) == [] and (queue / "removed" / "001_a.json").is_file() and bw_topics.picked(root) == ""
    for bad in ("queue:001_a.json", "topic:Nowhere", "nothing"):
        with pytest.raises(ValueError):
            bw_channel.remove_next(bad, root)


# --------------------------------------------------------------------------- music per kind and the sound effects

from modules import bw_sfx  # noqa: E402


def test_music_comes_from_the_kind_s_folder(settings, tmp_path, monkeypatch):
    from trendclip import music

    lib = tmp_path / "music"
    for folder, names in {"curious": ["Short.mp3", "Long A.mp3", "Long B.mp3"], "whatif": ["Dream.mp3"]}.items():
        (lib / folder).mkdir(parents=True)
        for n in names:
            (lib / folder / n).write_bytes(b"x")
    monkeypatch.setattr(type(settings), "music_library_dir", property(lambda s: lib), raising=False)
    lengths = {"Short": 29.0, "Long A": 120.0, "Long B": 117.0, "Dream": 168.0}
    monkeypatch.setattr(music, "_local_track", lambda s, p, f: music.DownloadedTrack(
        video_id=p.name, title=p.stem, channel_title=f, url="", filename=f"{f}/{p.name}", duration_seconds=lengths[p.stem], credit="", local=True))
    monkeypatch.delenv("BW_MUSIC", raising=False)
    out = tmp_path / "out"
    picks = {bw_channel.pick_music(settings, kind="", seconds=60, out_dir=out).title for _ in range(30)}
    assert picks == {"Long A", "Long B"}  # long enough for the video
    assert bw_channel.pick_music(settings, kind="whatif", seconds=60, out_dir=out).title == "Dream"
    assert bw_channel.pick_music(settings, kind="long", seconds=600, out_dir=out).channel_title == "curious"  # no music/long: curious
    monkeypatch.setattr(bw_channel, "videos", lambda out_dir=None, limit=30: [{"music": {"title": "Long A"}}])
    assert {bw_channel.pick_music(settings, seconds=60, out_dir=out).title for _ in range(20)} == {"Long B"}  # not the last one again
    monkeypatch.setenv("BW_MUSIC", "none")
    assert bw_channel.pick_music(settings) is None


def test_sound_effects_follow_the_scenes(tmp_path):
    text = "In 1867 Russia sold Alaska. A stamp. A pin."
    spec = bw_render.spec_from_dict({"title": "t", "scenes": [
        {"kind": "map", "narration": "In 1867 Russia sold Alaska.", "elements": [
            {"kind": "transfer", "place": "Alaska", "from_flag": "Flag of Russia.svg", "flag": "Flag of the United States.svg", "cue": "sold"}]},
        {"kind": "card", "narration": "A stamp.", "title": "1867", "elements": [{"kind": "stamp", "text": "SOLD", "cue": "stamp"},
                                                                               {"kind": "pin", "place": "Paris", "cue": "stamp"}]},
        {"kind": "map", "narration": "A pin.", "elements": [{"kind": "pin", "place": "Paris", "cue": "pin"}]}]}, _words(text))
    bw_render.align(spec)
    found = bw_sfx.events(spec)
    names = [n for _, n, _ in found]
    assert names.count("whoosh") == 2 and "paper" in names and "stamp" in names  # both flags; the card; the stamp
    assert names.count("pop") == 1  # the pin on the stamp's word is thinned; the last scene's pin pops
    assert all(b[0] - a[0] >= bw_sfx.MIN_GAP for a, b in zip(found, found[1:]))
    out = bw_sfx.sfx_track(spec, tmp_path / "sfx.wav")
    import wave

    with wave.open(str(out)) as w:
        assert w.getframerate() == bw_sfx.RATE and w.getnframes() >= int(spec.duration * bw_sfx.RATE)
    for name in bw_sfx.GAIN:
        assert 0.5 <= float(np.max(np.abs(bw_sfx.sound(name, 1.0)))) <= 1.0


def test_check_wants_a_quick_opening_and_short_cards(world):
    draft = _draft()
    assert bw_script.check(draft, _research(), _find(world)) == []
    draft.scenes[0].narration = "There is a notch on top of Minnesota that nobody on the map can explain at all."
    draft.scenes[1] = bw_script.SceneDraft(kind="card", narration="In 1783 a treaty used an old map that was very wrong about the big lake.", title="1783")
    draft.scenes[2].narration = "This is the Northwest Angle. " + draft.scenes[2].narration
    problems = " | ".join(bw_script.check(draft, _research(), _find(world)))
    assert "scene 1 is 17 words" in problems
    assert "card scene 2 is 16 words" in problems
    draft.scenes[1] = bw_script.SceneDraft(kind="map", narration="It is called the Northwest Angle.", focus=["Minnesota"])
    assert "scene 2 opens on a setup line" in " | ".join(bw_script.check(draft, _research(), _find(world)))
    two = _draft()
    two.scenes[3] = bw_script.SceneDraft(kind="card", narration="A line due west never met the river.", title="1818")
    assert any("2 card scenes (at most 1)" in p for p in bw_script.check(two, _research(), _find(world)))
    from modules import bw_long

    assert bw_long.CHAPTER.first_words == 0 and bw_long.CHAPTER.card_words == 0 and not bw_long.CHAPTER.no_setup
    assert "THE FIRST 5 SECONDS" in bw_script.write_prompt(_research()) and "THE FIRST 5 SECONDS" in bw_whatif.write_prompt("What if?")


def test_a_make_shows_its_progress_in_the_plan(settings, tmp_path, world):
    job = bw_channel.job_for(settings, Day(2026, 11, 2), "14:30")
    work = tmp_path / job.key
    work.mkdir(parents=True)
    heard = []
    p = bw_channel.Progress(work / "progress.json", lambda f, m: heard.append((f, m)))
    p(0.0, "Choosing the topic")
    p.step("Reading Wikipedia")
    p.step("Writing the script")
    assert 0 < bw_channel.progress_of(job.key, tmp_path)["fraction"] < bw_channel.Progress.SCRIPT_SHARE
    p(0.01, "back?")  # never goes back
    assert bw_channel.progress_of(job.key, tmp_path)["fraction"] == pytest.approx(0.1 * (1 - 0.8 ** 2), abs=1e-3)
    now = datetime(2026, 11, 2, 7, 0, tzinfo=settings.tz)
    row = next(r for r in bw_channel.plan_days(settings, now, 0, 0, tmp_path)[0]["slots"] if r["key"] == job.key)
    assert row["progress"]["message"] == "back?" and heard[1] == (pytest.approx(0.02), "Reading Wikipedia")
    assert bw_channel.progress_of(job.key, tmp_path, datetime.now() + timedelta(hours=1)) is None  # a make that died
    _make(settings, tmp_path, world)  # a finished make leaves no progress behind
    assert not (work / "progress.json").exists() and bw_channel.progress_of(job.key, tmp_path) is None


def test_the_whole_short_stays_under_a_minute(world):
    draft = _draft()
    draft.scenes[2].ask = "Wait, how does a map make a notch like that?"
    draft.scenes[4].react = "Fair border or bad map, which one?"
    assert bw_script.check(draft, _research(), _find(world)) == []  # 2 lines, within the words
    draft.scenes[3].ask = "Hold on, why did nobody check the river first?"
    problems = " | ".join(bw_script.check(draft, _research(), _find(world)))
    assert "the mascot has 3 lines (at most 2)" in problems and f"words together (at most {bw_script.SPOKEN_WORDS}" in problems
    assert bw_channel.MAX_SECONDS == 60.0 and bw_channel.check_video({"duration_seconds": 63, "sources": [1, 2]})


def test_a_slightly_long_voice_is_sped_up_to_fit(tmp_path):
    import subprocess

    voice = tmp_path / "voice.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=60",
                    "-c:a", "libmp3lame", str(voice)], check=True)
    words = [bw_render.Word("w", i * 0.6, i * 0.6 + 0.4) for i in range(100)] + [bw_render.Word("end", 59.6, 60.0, "panda")]
    fitted = bw_channel.fit_voice(voice, words, 62.4)  # 2.4 s of end card after the last word
    factor = 60.0 / fitted[-1].end
    assert fitted[-1].end + 2.4 == pytest.approx(bw_channel.FIT_SECONDS, abs=0.05) and fitted[-1].who == "panda"
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(voice)],
                           capture_output=True, text=True).stdout
    assert float(probe) == pytest.approx(60.0 / factor, abs=0.2)
    assert bw_channel.fit_voice(voice, fitted, 50.0) == fitted  # short enough: untouched
    far = [bw_render.Word("w", 0, 80.0)]
    assert bw_channel.fit_voice(voice, far, 82.4)[0].end == pytest.approx(80.0 / bw_channel.MAX_SPEEDUP, abs=0.01)  # capped


def test_an_icon_cued_late_pops_in_early_enough_to_be_seen():
    text = "One two three four five six seven eight. Nine ten."
    spec = bw_render.spec_from_dict({"title": "t", "scenes": [
        {"kind": "map", "narration": "One two three four five six seven eight.", "elements": [
            {"kind": "icon", "icon": "mountain", "cue": "eight"}, {"kind": "stamp", "text": "NO", "cue": "eight"},
            {"kind": "icon", "icon": "ship", "cue": "two"}]},
        {"kind": "map", "narration": "Nine ten."}]}, _words(text))
    bw_render.align(spec)
    late, stamp, early = spec.scenes[0].elements
    assert spec.scenes[0].end - late.at == pytest.approx(bw_render.MIN_ON_SCREEN)  # moved earlier
    assert stamp.at == pytest.approx(2.8) and early.at == pytest.approx(0.4)  # a stamp keeps its word; an early icon too


def test_stop_ends_a_make_at_its_next_step(settings, tmp_path, world):
    job = bw_channel.job_for(settings, Day(2026, 11, 2), "14:30")
    with pytest.raises(bw_channel.ChannelError, match="No video is being made"):
        bw_channel.request_stop(job.key, tmp_path)
    work = tmp_path / job.key
    work.mkdir(parents=True)
    p = bw_channel.Progress(work / "progress.json")
    p(0.3, "Drawing the maps")
    assert bw_channel.request_stop(job.key, tmp_path)["stopped"]
    assert bw_channel.progress_of(job.key, tmp_path) is None  # the slot is free at once
    newer = bw_channel.Progress(work / "progress.json")  # made again before the stopped one has noticed
    newer(0.05, "Choosing the topic")
    with pytest.raises(bw_channel.Stopped):
        p(0.31, "Drawing the maps: frame 600")
    p.done()  # the stopped make's cleanup leaves the newer one alone
    assert bw_channel.progress_of(job.key, tmp_path)["message"] == "Choosing the topic" and not (work / "stop").exists()
    newer(0.1, "still going")  # the stop was not for it
    newer.done()
    assert bw_channel.progress_of(job.key, tmp_path) is None

    def render(spec, voice_path, out, music, volume, progress):
        bw_channel.request_stop(job.key, tmp_path)  # pressed while the maps are drawn
        progress(0.5, "Drawing the maps: frame 10")

    def voice(text, out, progress):
        out.write_bytes(b"mp3")
        return _words(text)

    with pytest.raises(bw_channel.Stopped):
        bw_channel.make(settings, job, out_dir=tmp_path, script={**_spec_dict(), **SCRIPT}, voice=voice, render=render)
    assert bw_channel.load_info(job.key, tmp_path) is None and not (work / "stop").exists()


def test_messages_say_which_day_a_video_goes_out(settings, tmp_path, world):
    now = datetime(2026, 11, 1, 13, 0, tzinfo=settings.tz)
    assert bw_channel.when_text("2026-11-01T14:30+02:00", now) == "today at 14:30"
    assert bw_channel.when_text("2026-11-02T23:00+02:00", now) == "tomorrow at 23:00"
    assert bw_channel.when_text("2026-11-03T14:30+02:00", now) == "on Tue 3 Nov at 14:30"
    job, info, _ = _make(settings, tmp_path, world, day=Day(2026, 11, 2))
    assert "It goes out by itself " in bw_channel.video_note(info) and " at 14:30 as public" in bw_channel.video_note(info)


def test_make_now_makes_the_video_its_row_shows(tmp_path):
    root = tmp_path
    queue = bw_channel.queue_dir(root)
    queue.mkdir(parents=True)
    (queue / "001_whatif.json").write_text(json.dumps({**SCRIPT, "title": "What if Israel was in Africa?"}))
    bw_topics.pick(root, "topic:Oklahoma Panhandle")  # the owner's pick, shown on an earlier open slot
    seen = []
    research = lambda topic, p: seen.append(topic.article) or topic  # noqa: E731
    write = lambda res, client, progress: {"title": res.question}  # noqa: E731

    script, origin = bw_channel.next_script(out_dir=root, research=research, write=write, choice="queue:001_whatif.json")
    assert script["title"] == "What if Israel was in Africa?" and origin == "queue:001_whatif.json"
    assert bw_topics.picked(root) == "topic:Oklahoma Panhandle"  # the pick still waits for its own slot

    script, origin = bw_channel.next_script(out_dir=root, research=research, write=write, choice="topic:Lake Constance")
    assert origin == "topic:Lake Constance" and seen == ["Lake Constance"]
    assert bw_topics.picked(root) == "topic:Oklahoma Panhandle"

    script, origin = bw_channel.next_script(out_dir=root, research=research, write=write)  # the slot's own time: the pick
    assert origin == "topic:Oklahoma Panhandle"


def test_an_icon_never_stands_on_words(world, fx_offline):
    """An icon on a place moves off the place's pin name and label and off a stamp (the guard stood on "ENCLAVE"
    and the town's name on 2026-11-02)."""
    data = _fx_dict()
    data["scenes"][0]["elements"] = [{"kind": "pin", "place": "Minnesota", "text": "Minnesota"},
                                     {"kind": "label", "place": "Minnesota", "text": "Enclave"},
                                     {"kind": "icon", "icon": "car", "place": "Minnesota"}]
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    bw_render.prepare(spec)
    r = fx_offline.FxRenderer(spec)
    scene = spec.scenes[0]
    r.frame(scene.end - 0.1)
    cam = r.camera(0, scene.end - 0.1)
    zones = r.text_zones(cam, 0, scene)
    car = next(e for e in scene.elements if e.kind == "icon")
    x, y = r.icon_spot(0, scene, car, r.anchor(cam, car))
    half = car.opts.get("size", 190) * 0.6
    assert zones  # the pin's name and the label are there to stay clear of
    assert not any(x + half > z[0] and x - half < z[2] and y + half > z[1] and y - half < z[3] for z in zones), ((x, y), zones)


def test_a_short_opens_tight_and_its_maps_never_sit_still(world, fx_offline):
    """The first frame is close on the place and pulls back to the scene's view in OPEN_SECONDS; a long quiet map
    scene gets beats (a ring, no zoom) at most BEAT_GAP apart, and one on a number the narration says."""
    data = {"title": "Why?", "cover_text": "Paris", "seed": 1, "look": "satellite", "scenes": [
        {"kind": "map", "narration": "Paris sits far from Minnesota.", "focus": ["Paris", "Minnesota"]},
        {"kind": "map", "narration": "In 1783 a treaty drew a line that nobody checked for many many years on the ground at all.",
         "focus": ["Minnesota"]}]}
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"]), step=0.5))
    bw_render.prepare(spec)
    r = fx_offline.FxRenderer(spec)
    first = spec.scenes[0]
    assert r.open_view is not None and r.camera(0, 0.0).ppd > 1.5 * r.camera(0, first.start + fx_offline.OPEN_SECONDS + 0.01).ppd
    second = spec.scenes[1]
    beats = r.beats[1]
    marks = sorted({second.start, *beats, second.end})
    assert all(b - a <= fx_offline.BEAT_GAP + 0.01 for a, b in zip(marks, marks[1:])), marks
    year = next(w.start for w in spec.words if w.text.startswith("1783"))
    assert year in beats
    late = max(beats)  # after the flight from the last scene: only the slow drift moves the camera
    assert late > second.start + bw_render.MOVE
    calm = r.camera(1, late + 0.25).ppd / r.camera(1, late - 0.05).ppd
    assert abs(calm - 1) < 0.01  # the camera doesn't jump in on the beat


def test_a_what_if_is_due_every_third_short(monkeypatch, tmp_path):
    made = [{"kind": ""}, {"kind": ""}]
    monkeypatch.setattr(bw_channel, "videos", lambda out_dir=None, limit=30: made[:limit])
    monkeypatch.setattr(bw_channel, "queued_scripts", lambda out_dir=None: [])
    assert bw_channel.whatif_due()
    made[1] = {"kind": "whatif"}
    assert not bw_channel.whatif_due()
    made[1] = {"kind": ""}
    monkeypatch.setattr(bw_channel, "queued_scripts", lambda out_dir=None: [tmp_path / "20261102-1200_whatif-x.json"])
    assert not bw_channel.whatif_due()  # one already waits in the queue


# --------------------------------------------------------------------------- counters and the flash-forward


def test_counter_counts_up_to_its_number():
    el = bw_render.Element("counter", value=85, text="Day {n}")
    assert bw_render.Renderer.counted(el, 0.0) == ("DAY 0", "") and bw_render.Renderer.counted(el, 1.0) == ("DAY 85", "")
    tally = bw_render.Element("counter", value=120000, text="soldiers")
    assert bw_render.Renderer.counted(tally, 1.0) == ("120,000", "SOLDIERS")
    assert bw_render.Renderer.counted(tally, 2.0) == ("120,000", "SOLDIERS")  # it stops at its number
    assert bw_render.Renderer.counted(bw_render.Element("counter", value=3, text="{n} months"), 0.0) == ("3 MONTHS", "")
    assert bw_render.Renderer.counter_y(1) > bw_render.Renderer.counter_y(0) > bw_render.HEAD_Y


def _hook_dict():
    data = _spec_dict()
    data["scenes"][0]["elements"].append({"kind": "counter", "value": 994, "text": "{n}+ ships", "cue": "notch"})
    data["scenes"][2]["elements"].append({"kind": "counter", "value": 500, "text": "ships"})
    data["hook"] = {"scene": 2, "before": "Day 0", "after": "Day 85"}
    return data


def test_flash_forward_opens_on_the_outcome_then_the_story(world):
    data = _hook_dict()
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert spec.hook == {"scene": 2, "before": "Day 0", "after": "Day 85"}
    assert bw_render.prepare(spec) == []
    counter = spec.scenes[0].elements[-1]
    assert counter.kind == "counter" and counter.value == 994
    r = bw_render.Renderer(spec)
    outcome = np.asarray(r.scene_frame(2, spec.scenes[2].end - 0.05).convert("RGB")).astype(int)
    flashed = np.asarray(r.frame(1.5)).astype(int)
    band = slice(bw_render.HEAD_Y + 120, bw_render.HEAD_Y + 340)
    assert np.abs(flashed[band] - outcome[band]).mean() > 5  # the "after" words are stamped on the outcome
    assert np.abs(flashed[900:1300] - outcome[900:1300]).mean() < 3  # the rest of the frame is the outcome
    assert r.hook_frame(0.3) is not None and r.hook_frame(bw_render.HOOK_END) is None
    # the marker hand never shows on the flash-forward's held frames (it would stand still: a stuck hand)
    from modules import bw_brand, bw_fx
    drawn = []
    monkeypatch_hand = bw_brand.draw_hand
    bw_brand.draw_hand = lambda *a, **k: drawn.append(a)
    try:
        hooked = {**_hook_dict(), "brand": True}  # the mascot's marker hand draws the outcome scene's highlight
        hooked["scenes"][2]["elements"].append({"kind": "highlight", "place": "Minnesota", "color": "gold", "cue": "And"})
        hooked_spec = bw_render.spec_from_dict(hooked, _words(" ".join(s["narration"] for s in hooked["scenes"])))
        assert bw_render.prepare(hooked_spec) == []
        hooked_spec.scenes[2].elements[-1].at = hooked_spec.scenes[2].start  # the pen starts with the scene
        fx = bw_fx.FxRenderer(hooked_spec)
        fx.hook_frame(0.3)
        assert fx._hand is not None  # the pen would be in the frame...
        fx.hook_frame(1.2)
        assert drawn == []  # ...but is not drawn on either held frame
        fx.scene_frame(2, fx.spec.scenes[2].start)
        assert drawn  # in the story itself it is
    finally:
        bw_brand.draw_hand = monkeypatch_hand
    gold = lambda a: ((a[..., 0] > 200) & (a[..., 1] > 150) & (a[..., 2] < 120)).sum()
    assert gold(np.asarray(r.frame(0.3))[band]) > 2000  # "DAY 0" on the first map
    # after the flash the story starts from scene 1 as before, and no script without a hook changes
    plain = bw_render.spec_from_dict(_spec_dict(), _words(" ".join(s["narration"] for s in data["scenes"])))
    assert plain.hook == {} and bw_render.Renderer(plain).hook_frame(0.3) is None
    from modules import bw_sfx

    assert (bw_render.HOOK_FLIP, "whoosh", 0.0) in bw_sfx.events(spec)


def test_counters_and_flash_forward_on_the_satellite_look(world, fx_offline):
    data = _hook_dict()
    data["look"] = "satellite"
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    for t in (0.0, 0.5, 1.2, 2.0, spec.scenes[2].start + 0.8):
        assert r.frame(t).size == (bw_render.W, bw_render.H)
    zones = r.text_zones(r.camera(0, 1.0), 0, spec.scenes[0])
    y = r.counter_y(0)
    assert any(z[1] < y < z[3] and z[0] < bw_render.W / 2 < z[2] for z in zones)  # icons keep off the counter


def test_check_counters_and_the_flash_forward(world):
    draft = _draft()
    draft.scenes[3].elements.append(bw_script.ElementDraft(kind="counter", value=123, text="square miles", cue="line"))
    draft.hook_scene, draft.hook_before, draft.hook_after = 5, "1783", "Patched"
    assert bw_script.check(draft, _research(), _find(world)) == []
    script = bw_script.to_spec_dict(draft)
    assert script["hook"] == {"scene": 4, "before": "1783", "after": "Patched"}
    assert script["scenes"][3]["elements"][-1]["value"] == 123
    draft.scenes[1].elements.append(bw_script.ElementDraft(kind="counter", value=1783))
    draft.hook_scene = 2
    problems = " | ".join(bw_script.check(draft, _research(), _find(world)))
    assert "a counter goes on a map scene" in problems and "looks like a year" in problems
    assert "hook_scene 2 must be the number of a later map scene" in problems
    draft.hook_scene, draft.hook_after = 4, ""
    assert "needs hook_after" in " | ".join(bw_script.check(draft, _research(), _find(world)))


# --------------------------------------------------------------------------- the attempts format on the war map


def _attempts_json(armies: bool = True) -> str:
    E = bw_script.ElementDraft
    sides = [E(kind="highlight", place="Minnesota", color="a"), E(kind="highlight", place="Canada", color="b")]
    line = "The army marched north across the lake and the border in the cold."
    scenes = [bw_script.SceneDraft(kind="map", narration="Minnesota tried to take Canada 3 times.", focus=["Minnesota"], elements=sides)]
    for year in ("1775", "1812", "1838"):
        scenes.append(bw_script.SceneDraft(
            kind="map", narration=f"In {year} the first plan began on the lake.", focus=["Lake of the Woods"], year=year,
            elements=sides + ([E(kind="army", place="Minnesota", to="Canada", flag="Flag of Canada.svg", cue="plan")] if armies else [])))
        scenes += [bw_script.SceneDraft(kind="map", narration=line, focus=["Minnesota"], elements=sides),
                   bw_script.SceneDraft(kind="map", narration=line + " " + line, focus=["Canada"],
                                        elements=sides + [E(kind="battle", place="Lake of the Woods", cue="border")])]
    scenes.append(bw_script.SceneDraft(kind="map", narration="3 tries, and the border never moved.", focus=["Canada"], elements=sides))
    return bw_script.Draft(usable=True, title="3 Times Minnesota Tried to Take Canada", cover_text="3 tries, 0 wins",
                           scenes=scenes, description="Three attempts.", hashtags=["maps"]).model_dump_json()


def test_an_attempts_short_goes_to_the_queue_on_the_war_map(settings, tmp_path, world):
    s = bw_whatif.start("3 times Minnesota tried to take Canada", tmp_path, fmt="attempts")
    assert s["question"] == "3 times Minnesota tried to take Canada" and s["format"] == "attempts"
    assert "ATTEMPTS episode" in s["prompt"] and '"army"' in s["prompt"] and "WHAT IF" not in s["prompt"]
    bad = bw_whatif.answer(s["id"], _attempts_json(armies=False), tmp_path, find=_find(world))
    assert bad["step"] == "fix" and any("army elements" in p for p in bad["problems"])
    good = bw_whatif.answer(s["id"], _attempts_json(), tmp_path, find=_find(world))
    assert good["step"] == "ready", good["problems"]
    assert good["preview"]["armies"] == 3 and good["preview"]["battles"] == 3
    bw_whatif.queue(s["id"], tmp_path)
    script = json.loads(bw_channel.queued_scripts(tmp_path)[0].read_text())
    assert script["kind"] == "attempts" and script["style"] == "war" and script["question"] == "3 Times Minnesota Tried to Take Canada"
    assert [sc.get("year") for sc in script["scenes"] if sc.get("year")] == ["1775", "1812", "1838"]
    assert bw_channel.check_video({"kind": "attempts", "duration_seconds": 84}) == []  # 70-90 s, no sources
    assert bw_channel.check_video({"kind": "whatif", "duration_seconds": 84})  # a what-if stays under 60
    assert "Sources:" not in bw_script.description(script)


def test_the_war_map_draws_sides_armies_battles_and_years(world, fx_offline):
    data = _fx_dict()
    data["style"] = "war"
    data["scenes"][0]["year"] = "1812"
    data["scenes"][0]["elements"] += [
        {"kind": "army", "place": "Minnesota", "to": "Lake of the Woods", "flag": "Flag of Canada.svg", "cue": "notch"},
        {"kind": "army", "place": "Minnesota", "to": "Canada", "color": "b", "count": 7},
        {"kind": "battle", "place": "Minnesota", "flag": "Flag of Canada.svg", "from_flag": "Flag of Canada.svg", "text": "Clash"}]
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert spec.style == "war" and bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    assert r.war and r.color("a") == fx_offline.WAR["a"]
    assert r.war_pen(spec.scenes[0]).kind == "arrow"  # the hand draws the front (never round the tinted sides)
    battle_only = bw_render.Scene(kind="map", narration="x", elements=[
        bw_render.Element(kind="highlight", place="Canada", at=0.5, feature=bw_geo.resolve("Canada") or spec.scenes[0].elements[0].feature),
        bw_render.Element(kind="battle", place="Minnesota", at=2.0, feature=spec.scenes[0].elements[0].feature)], start=0.0, end=4.0)
    circle = r.war_pen(battle_only)
    assert circle.kind == "circle" and circle.at == 1.1 and circle in battle_only.elements  # ringed just before the fight
    battle = next(e for e in spec.scenes[0].elements if e.kind == "battle")
    assert battle.opts["from_flag"] == "Flag of Canada.svg" and battle.feature is not None
    army = next(e for e in spec.scenes[0].elements if e.kind == "army")
    assert army.feature_to is not None
    for t in (0.1, 0.5, 1.5, 2.5, spec.scenes[0].end - 0.1):
        assert r.frame(t).size == (bw_render.W, bw_render.H)
    with_year = np.asarray(r.frame(0.5)).astype(int)
    spec.scenes[0].opts.pop("year")
    plain = np.asarray(r.frame(0.5)).astype(int)
    assert with_year[bw_render.CY - 400, 40].sum() < plain[bw_render.CY - 400, 40].sum()  # the map darkens under the big year
    spec.scenes[0].opts["year"] = "1812"
    sounds = [name for t, name, _ in bw_sfx.events(spec) if t < spec.scenes[0].end]
    assert "stamp" in sounds and "whoosh" in sounds  # the battle's thud, the army's whoosh


def test_a_sides_bubble_is_drawn_over_an_added_gif_and_an_icon_keeps_off_a_sides_badge(world, fx_offline, monkeypatch):
    """The owner (2026-11-06): the cannon GIF of "war" stood on the general's bubble, and the planes hid behind
    Egypt's flag badge. An added GIF goes under everything and the bubbles go last; a war side's badge (and its
    stem) is a zone an icon on that country keeps off."""
    from modules import bw_fx, bw_local

    data = _spec_dict()
    data["scenes"][2]["elements"] = [{"kind": "local", "place": "Paris", "cue": "stayed", "react": "stayed", "icon": "general",
                                       "flag": "Flag of France.svg", "say": "Still here."}]
    words = _words(" ".join(s["narration"] for s in data["scenes"]))
    spec = bw_render.spec_from_dict(data, words)
    assert bw_render.prepare(spec) == []
    scene = spec.scenes[2]
    local = next(e for e in scene.elements if e.kind == "local")
    r = fx_offline.FxRenderer(spec)
    scene.elements.append(bw_render.Element(kind="icon", at=local.at, text="", opts={"icon": "war", "auto": True, "n": 0, "size": 240}))
    order = []
    monkeypatch.setattr(bw_local, "say", lambda *a, **k: order.append("say"))
    monkeypatch.setattr(r, "icon", lambda ov, el, xy, t, flip=False: order.append("gif" if el.opts.get("auto") else "icon"))
    r.scene_frame(2, r.react_time(local, scene) + 0.5)
    assert order and order.index("gif") < order.index("say")  # the GIF under, the bubble over
    gif = scene.elements[-1]  # and never touching the bubble's words: the side took its place first (the owner, 2026-11-06)
    gx, gy = gif.opts["_xy"]
    bub = bw_local.bubble_box("Still here.", r.head_of(local, r._spots[id(local)], bw_fx.LOCAL_SIZE), bw_render.W, local.opts.get("_lean"))
    half = 240 * 0.62
    assert not (gx + half > bub[0] and gx - half < bub[2] and gy + half > bub[1] and gy - half < bub[3])
    # the war map: a side's badge waves over its country, so an icon there steps aside
    data = _fx_dict()
    data["style"] = "war"
    data["scenes"][0]["elements"] += [{"kind": "highlight", "place": "Canada", "color": "b"},  # a country: it gets a badge
                                      {"kind": "icon", "place": "Canada", "icon": "plane", "cue": "notch"}]
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    side = next(e for e in spec.scenes[0].elements if e.kind == "highlight" and e.place == "Canada")
    icon = next(e for e in spec.scenes[0].elements if e.kind == "icon")
    assert side.opts.get("_badge")
    r.scene_frame(0, icon.at + 0.5)
    cam = r.camera(0, icon.at + 0.5)
    zone = next(z for z in r.flag_zones(cam, spec.scenes[0]) if z[2] - z[0] < 200)  # the badge's box, stem included
    ix, iy = r._spots.get(id(icon)) or r.anchor(cam, icon)
    half = 190 * 0.65
    assert not (ix + half > zone[0] and ix - half < zone[2] and iy + half > zone[1] and iy - half < zone[3])


def test_the_war_map_counts_the_attempts_and_keeps_its_armies_on_screen(world, fx_offline):
    words = _words("Attempt 1 began. The second try failed. Attempt number three too. Then the third time again.")
    marks = bw_render.attempt_marks(words)
    assert [m[1] for m in marks] == ["ATTEMPT 1", "ATTEMPT 2", "ATTEMPT 3"]  # "the third time" repeats 3: no second mark
    assert marks[0][0] == words[0].start and marks[1][0] == words[5].start
    # a long war scene is cut at its sentences, each element with the sentence that says its cue, the hook following
    data = _fx_dict()
    data["style"] = "war"
    data["hook"] = {"scene": 1, "before": "A", "after": "B"}
    data["scenes"][0].update(narration="The army marched north on the lake in the spring of that year. Then it stopped at the border.",
                             year="1812", ask="Why?", react="Wow.", elements=data["scenes"][0]["elements"] + [
                                 {"kind": "army", "place": "Minnesota", "to": "Canada", "flag": "Flag of Canada.svg", "cue": "stopped"}])
    cut = bw_render.split_scenes(data)
    first, second = cut["scenes"][0], cut["scenes"][1]
    assert len(cut["scenes"]) == len(data["scenes"]) + 1 and cut["hook"]["scene"] == 2
    assert first["year"] == "1812" and first["ask"] == "Why?" and "year" not in second and "ask" not in second
    assert "react" not in first and second["react"] == "Wow." and second["narration"] == "Then it stopped at the border."
    assert [e["kind"] for e in second["elements"]] == ["army"] and not any(e["kind"] == "army" for e in first["elements"])
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert len(spec.scenes) == len(data["scenes"]) + 1  # cut on the way in
    assert bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    army = next(e for e in spec.scenes[1].elements if e.kind == "army")
    later = [(s, e) for s in spec.scenes[2:] if s.kind == "map" for e in s.elements if e.kind == "army"]
    assert later and all(e.opts.get("_held") and e.at < s.start and e.to == army.to for s, e in later)  # it stays where it arrived
    silent = {round(e.at + 0.3, 1) for _, e in later}
    assert not any(round(t, 1) in silent for t, n, _ in bw_sfx.events(spec) if n == "whoosh")  # the held copies make no sound
    sides = [e for s in spec.scenes for e in s.elements if e.kind == "highlight" and e.opts.get("_badge")]
    assert sides and all(e.feature.kind == "country" for e in sides)  # each side's flag waves over its country
    r.attempts = [(spec.scenes[1].start, "ATTEMPT 1")]
    big = np.asarray(r.frame(spec.scenes[1].start + 0.3)).astype(int)
    r.attempts = []
    plain = np.asarray(r.frame(spec.scenes[1].start + 0.3)).astype(int)
    assert (big != plain).any()  # the attempt slams in
    for t in (spec.scenes[2].start + 0.2, spec.scenes[-2].end - 0.2):
        assert r.frame(t).size == (bw_render.W, bw_render.H)


def test_any_war_story_goes_on_the_war_map_and_a_peaceful_one_does_not(world):
    draft = _draft()
    assert "style" not in bw_script.to_spec_dict(draft)  # a border "why": our usual flags, arrows and transfers
    draft.scenes[3].elements.append(bw_script.ElementDraft(kind="battle", place="Minnesota", flag="Flag of Canada.svg"))
    assert bw_script.to_spec_dict(draft)["style"] == "war"
    assert bw_script.finish(draft, _research())["style"] == "war"
    assert "war map" in bw_script.SCENE_RULES and "army" in bw_script.SCENE_RULES


def test_squads_wear_their_sides_colour_and_an_ancient_side_gets_no_flag(world, fx_offline):
    """The owner, 2026-11-04 (Hannibal): the red and the blue army looked the same, and Italy's and Tunisia's flags
    waved over Rome and Carthage. Squads are painted in their side's colour; a highlight with "flag": "none" gets no
    badge, on the scene it is in and on every scene it is held into."""
    data = _fx_dict()
    data["style"] = "war"
    data["scenes"][0]["elements"] = [{"kind": "highlight", "place": "Canada", "color": "b", "flag": "none"},
                                     {"kind": "highlight", "place": "United States", "color": "a"},
                                     {"kind": "army", "place": "Minnesota", "to": "Canada", "color": "a", "cue": "marched"},
                                     {"kind": "army", "place": "Canada", "to": "Minnesota", "color": "b", "cue": "back"}]
    data["scenes"][0]["narration"] = "They marched north. Then they came back."
    data["scenes"].append({"kind": "map", "narration": "Nothing changed.", "focus": ["Canada"], "elements": []})
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    sides = [e for s in spec.scenes for e in s.elements if e.kind == "highlight"]
    assert all(not e.opts.get("_badge") and e.opts["_noflag"] for e in sides if e.place == "Canada")  # no flag over Canada, held copies too
    assert len([e for e in sides if e.place == "Canada"]) >= 2
    assert all(e.opts.get("_badge") for e in sides if e.place == "United States")  # the other side keeps its flag
    from PIL import Image

    a = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    b = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    r.squad(a, r.color("a"), (150, 150), 120, 0.5)
    r.squad(b, r.color("b"), (150, 150), 120, 0.5)
    if a.getbbox():  # with the owner's squad GIF on this machine: the two sides tell apart
        pa, pb = np.asarray(a).astype(int), np.asarray(b).astype(int)
        on = pa[..., 3] > 0
        assert pa[on][:, 2].mean() > pa[on][:, 0].mean() and pb[on][:, 0].mean() > pb[on][:, 2].mean()  # blue-ish vs red-ish


def test_a_local_stands_on_the_map_and_changes_face_on_the_react_word(world, fx_offline):
    """One of the owner's characters (modules.bw_local) on a place: pops in on the cue, reacts from the react word on
    (its own variant GIF or an emote over its head), stands clear of an icon on the same place, and the rules check
    its mood and its name."""
    from modules import bw_fx, bw_script

    data = _spec_dict()
    data["scenes"][0]["elements"] += [{"kind": "icon", "place": "Minnesota", "icon": "question"},
                                      {"kind": "local", "place": "Minnesota", "cue": "notch", "react": "map", "mood": "grin", "text": "a farmer",
                                       "icon": "farmer"}]
    words = _words(" ".join(s["narration"] for s in data["scenes"]))
    spec = bw_render.spec_from_dict(data, words)
    assert bw_render.prepare(spec) == []
    scene = spec.scenes[0]
    local = next(e for e in scene.elements if e.kind == "local")
    assert local.opts["mood"] == "grin" and local.at <= next(w.start for w in words if w.text == "notch")  # a late cue pops in earlier (STAY_KINDS)
    r = bw_fx.FxRenderer(spec)
    react = r.react_time(local, scene)
    assert react == next(w.start for w in words if w.text == "map.")  # "map" matches the spoken "map."
    assert react > local.at
    before = r.scene_frame(0, local.at + 0.5)
    after = r.scene_frame(0, react + 0.5)
    assert before.size == after.size == (bw_render.W, bw_render.H) and before.tobytes() != after.tobytes()
    icon = next(e for e in scene.elements if e.kind == "icon")
    ix, iy = r._spots.get(id(icon)) or r.anchor(r.camera(0, react), icon)
    lx, ly = r._spots.get(id(local)) or (0, 0)
    assert (lx, ly) != (0, 0) and (abs(lx - ix) > 150 or abs(ly - iy) > 150)  # the local moved off the icon
    el = bw_script.ElementDraft(kind="local", place="Minnesota", mood="angry", react="map")
    assert el.mood == "angry" and "angry" not in bw_script.LOCAL_MOODS and "grin" in bw_script.LOCAL_MOODS  # the check names the moods
    # a side whose country lies outside the close-up (Egypt behind a Sinai view, 2026-11-06): it steps into the frame
    data = _spec_dict()
    data["scenes"][2]["elements"] = [{"kind": "local", "place": "Paris", "cue": "stayed", "react": "stayed", "icon": "general",
                                       "flag": "Flag of France.svg", "say": "Still here."}]
    spec = bw_render.spec_from_dict(data, words)
    assert bw_render.prepare(spec) == []
    far = next(e for e in spec.scenes[2].elements if e.kind == "local")
    r = bw_fx.FxRenderer(spec)
    r.scene_frame(2, far.at + 0.5)
    fx, fy = r._spots[id(far)]
    assert 100 <= fx <= bw_render.W - 100 and bw_fx.HEADLINE_BOTTOM <= fy <= bw_render.H - 100


def test_a_local_is_one_of_the_owners_characters():
    """The local's name resolves to the owner's GIF (stickers/borderlinewhy), and a mood keeps the character: its own
    variant GIF when the owner drew one, the professor's surprised head for his outfits, else none (an emote pops)."""
    from modules import bw_local

    assert bw_local.character("scholar") == "scholar_writing"  # a person without moods: its GIF
    # the side families (<name>_blue / <name>_red, 2026-11-05 night): the coat follows the side letter
    assert [bw_local.character(n, c) for n, c in (("merchant", "a"), ("ambassador", "b"), ("general", ""), ("woman", "b"))] == \
        ["@merchant:blue", "@diplomat:red", "@general:blue", "@village_woman:red"]
    from PIL import Image
    blue, red = (Image.new("RGBA", (600, 600)) for _ in range(2))
    bw_local.draw(blue, "@diplomat:blue", (300, 300), 250, 3.0, 0.0, "grin", 2.0)
    bw_local.draw(red, "@diplomat:red", (300, 300), 250, 3.0, 0.0, "grin", 2.0)
    assert blue.tobytes() != red.tobytes()
    # the guard's poses (character_pointing_<coat>, character_arguing_<coat>): "point" for a shouted line
    assert bw_local.character("guard", "b") == "@character:red"
    assert bw_local.character("merchant_arab", "b") == "@merchant_arab:red"  # the Middle Eastern look (2026-11-06)
    assert bw_local.character("merchants_arab", "a") == "@merchant_arab:blue"  # a plural keeps the look
    assert {"pointing_red", "arguing_blue"} <= set(bw_local.family_frames("character", 250))
    # 2026-11-06: the nomad and the priest (pointing poses only so far), and pointing poses for the Arab families
    assert [bw_local.character(n, "a") for n in ("nomad", "herders", "bedouin", "priest", "monk", "missionary")] == \
        ["@nomad:blue", "@nomad:blue", "@nomad:blue", "@priest:blue", "@priest:blue", "@priest:blue"]
    assert {"pointing_blue", "pointing_red"} <= set(bw_local.family_frames("emperor_arab", 250))
    assert "pointing_blue" not in bw_local.family_frames("general", 250)  # the European general has no pointing pose yet
    point, argue = (Image.new("RGBA", (600, 600)) for _ in range(2))
    bw_local.draw(point, "@character:red", (300, 300), 250, 3.0, 0.0, "point", 2.0)
    bw_local.draw(argue, "@character:red", (300, 300), 250, 3.0, 0.0, "shocked", 2.0)
    assert point.tobytes() != argue.tobytes()
    assert bw_local.character("") == bw_local.character("no such person") == "@citizen"
    # the mood families (<name>_happy/_surprised/_sad): plural and other words for the same person find them
    assert [bw_local.character(n) for n in ("farmer", "settlers", "colonist", "king", "army", "villager")] == \
        ["@farmer", "@settler", "@settler", "@king", "@soldier", "@citizen"]
    from PIL import Image
    fam = bw_local.family_frames("farmer", 250)
    assert set(fam) == {"happy", "surprised", "sad"}
    heights = [frames[0].getchannel("A").getbbox() for frames, _ in fam.values()]
    assert max(b[3] - b[1] for b in heights) - min(b[3] - b[1] for b in heights) <= 3  # the person keeps its size
    calm, played, rest = (Image.new("RGBA", (600, 600)) for _ in range(3))
    bw_local.draw(calm, "@farmer", (300, 300), 250, 1.0, 0.0, "grin", 2.0)
    bw_local.draw(played, "@farmer", (300, 300), 250, 2.6, 0.0, "grin", 2.0)
    bw_local.draw(rest, "@farmer", (300, 300), 250, 9.0, 0.0, "grin", 2.0)
    assert calm.tobytes() != played.tobytes() != rest.tobytes()  # the stance, the happy jump, its last frame
    assert bw_local.variant("scholar_writing", "shocked") == "surprised"  # the professor's surprised head
    assert bw_local.variant("sailor_waving", "shocked") is None  # the sailor has none yet: a burst pops over his head


def test_a_short_speaks_faster_and_loops_without_the_outro(monkeypatch, tmp_path):
    """The owner asked for higher energy (2026-11-05): a Short's voices run at SHORT_PACE (Kokoro's speed, or Edge's
    rate percentage), a long video's at 1.0; a Short has no subscribe outro unless BW_OUTRO is on, and ends LOOP_HOLD
    after its last word."""
    from trendclip import kokoro_voice, voiceover

    asked = {}
    values = {"BW_TTS_ENGINE": "edge", "BW_TTS_RATE": "+8%"}
    monkeypatch.setattr(bw_channel, "setting", lambda name, default="": values.get(name, default))
    monkeypatch.setattr(voiceover, "synthesize", lambda text, out, voice, rate="+0%", **k: asked.setdefault("edge", []).append(rate) or [])
    bw_channel.record_voice("Hello there.", tmp_path / "a.mp3", pace=1.12)
    bw_channel.record_voice("Hello there.", tmp_path / "b.mp3")
    assert asked["edge"] == ["+21%", "+8%"]
    values["BW_TTS_ENGINE"] = "kokoro"
    monkeypatch.setattr(kokoro_voice, "synthesize", lambda text, out, voice, speed=1.0: asked.setdefault("kokoro", []).append(speed) or [])
    monkeypatch.setattr(kokoro_voice, "unload", lambda: None)
    bw_channel.record_voice("Hello there.", tmp_path / "c.mp3", pace=bw_channel.SHORT_PACE)
    assert asked["kokoro"] == [pytest.approx(1.12)]
    assert not bw_channel.outro_on()
    values["BW_OUTRO"] = "on"
    assert bw_channel.outro_on()
    spec = bw_render.spec_from_dict({"title": "t", "hold": bw_channel.LOOP_HOLD, "scenes": [{"kind": "map", "narration": "Russia sold Alaska."}]},
                                    [bw_render.Word("Russia", 0.0, 0.4), bw_render.Word("sold", 0.4, 0.8), bw_render.Word("Alaska.", 0.8, 1.4)])
    assert spec.duration == pytest.approx(1.4 + bw_channel.LOOP_HOLD)


def test_a_short_never_shows_one_thing_alone_and_closes_on_a_ding(world):
    """One thing on the screen at a time looked poor (the owner, 2026-11-05): a Short's map scene after the first needs
    two elements (long chapters and attempts don't); a looping Short ends with no bell (the owner, 2026-11-07)."""
    from modules import bw_long, bw_sfx, bw_whatif

    draft = _draft()
    assert bw_script.check(draft, _research(), _find(world)) == []
    draft.scenes[3].elements = draft.scenes[3].elements[:1]
    problems = " | ".join(bw_script.check(draft, _research(), _find(world)))
    assert "map scene 4 shows too little" in problems
    assert bw_long.CHAPTER.map_elements == 0 and bw_whatif.ATTEMPT_LIMITS.map_elements == 0 and bw_whatif.LIMITS.map_elements == 2
    words = [bw_render.Word("Russia", 0.0, 0.4), bw_render.Word("sold", 0.4, 0.8), bw_render.Word("Alaska.", 0.8, 1.4),
             bw_render.Word("Wow!", 1.8, 2.3, "panda")]
    spec = bw_render.spec_from_dict({"title": "t", "hold": bw_channel.LOOP_HOLD, "brand": True,
                                     "scenes": [{"kind": "map", "narration": "Russia sold Alaska.", "react": "Wow!"}]}, words)
    assert "ding" not in [name for _, name, _ in bw_sfx.events(spec)]
    assert spec.duration == pytest.approx(2.3 + bw_channel.LOOP_HOLD)


def test_a_still_moment_gets_the_gif_of_a_spoken_word(world):
    """Viewers left where nothing moved (the owner, 2026-11-05: the eye needs something new all the time; use our
    GIFs, not written notes): a stretch of GIF_GAP seconds with nothing new gets the owner's GIF of a word said in
    it, on that word; never an emote for a word with a second meaning, never the same GIF twice in a scene."""
    from modules import bw_fx

    said = "In 1850 the king signed a treaty, sold the land, and sent his army to cross the river by ship."
    words = [bw_render.Word(w, k * 0.5, k * 0.5 + 0.4) for k, w in enumerate(said.split())]
    gifs = {code for _, code, _ in bw_fx.gif_words(words)}
    assert {"scroll", "money_bag", "ship"} <= gifs and "cross" not in gifs
    spec = bw_render.spec_from_dict({"title": "t", "scenes": [{"kind": "map", "narration": said, "focus": ["Minnesota"]}]}, words)
    assert bw_render.prepare(spec) == []
    added = bw_fx.plan_gifs(spec, 0)
    assert added and all(e.kind == "icon" and e.opts["auto"] for e in added) and len(added) <= bw_fx.GIF_MAX
    assert len({e.opts["icon"] for e in added}) == len(added)
    times = sorted([0.0, *[e.at for e in added], spec.scenes[0].end])
    assert max(b - a for a, b in zip(times, times[1:])) <= bw_fx.GIF_GAP + 1.5


def test_a_slot_upload_goes_up_an_hour_early_scheduled_and_comments_once_public(settings, tmp_path, world, monkeypatch):
    from datetime import datetime, timezone

    job, info, _ = _make(settings, tmp_path, world)
    soon = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=1)
    info = bw_channel.load_info(job.key, tmp_path)
    info["job"]["post_at"] = soon.isoformat()
    bw_channel.save_info(info, tmp_path)
    uploads, comments = [], []

    def fake_upload(path, req, as_short, progress, youtube):
        uploads.append(req)
        return {"video_id": "abc", "url": "https://youtu.be/abc", "privacy": "private",
                "scheduled_for": req.publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")}

    monkeypatch.setattr(bw_channel.publish, "upload_file", fake_upload)
    monkeypatch.setattr(bw_channel, "announce_upload", lambda *a, **k: None)
    monkeypatch.setattr(bw_channel, "comment", lambda info, youtube=None, out_dir=None: comments.append(info["key"]))
    record = bw_channel.publish_if_allowed(job.key, out_dir=tmp_path, youtube=FakeYouTube())
    assert uploads[0].publish_at == soon and record["privacy"] == "private"
    assert comments == []  # a private video can't take the comment yet
    assert bw_channel.publish.due_later(tmp_path, soon - timedelta(minutes=1)) == []
    due = bw_channel.publish.due_later(tmp_path, soon)
    assert [e["key"] for e in due] == [job.key] and bw_channel.publish.due_later(tmp_path, soon) == []
    monkeypatch.setattr(bw_channel.publish, "is_public", lambda yt, vid: False)
    assert bw_channel.after_publish(job.key, tmp_path, FakeYouTube()) is False and comments == []
    monkeypatch.setattr(bw_channel.publish, "is_public", lambda yt, vid: True)
    assert bw_channel.after_publish(job.key, tmp_path, FakeYouTube()) is True and comments == [job.key]
    assert bw_channel.load_info(job.key, tmp_path)["uploads"]["youtube"]["privacy"] == "public"


def test_the_countries_are_the_characters(world):
    """The owner (2026-11-05, after a 15M-view Short of a Canadian and an American arguing over the map): a local
    with a flag speaks for that country, its flag badge at its shoulder, and says 1-5 words in a bubble on its
    react word; two sides can face each other in a scene, never a third; the checker keeps a line short."""
    from PIL import Image
    from modules import bw_local

    plain, flagged, spoke = (Image.new("RGBA", (1080, 1920)) for _ in range(3))
    bw_local.draw(plain, "@king", (400, 900), 250, 1.0, 0.0, "grin", 2.0)
    bw_local.draw(flagged, "@king", (400, 900), 250, 1.0, 0.0, "grin", 2.0)
    bw_local.badge(flagged, "Flag of France.svg", (400, 900), 325, 1.0, 0.0)
    assert flagged.tobytes() != plain.tobytes()  # the badge is there before the king speaks
    bw_local.draw(spoke, "@king", (400, 900), 250, 3.0, 0.0, "grin", 2.0)
    bw_local.say(spoke, "Mine now.", (400, 740), 3.0, 2.0)
    box = bw_local.bubble_box("Mine now.", (400, 740), 1080)
    assert box[3] <= 740 - 40 and box[0] >= 30 and box[2] <= 1050  # above the head, on the screen
    assert spoke.crop(box).getchannel("A").getextrema()[1] == 255  # the bubble is drawn there
    far = bw_local.bubble_box("We never signed that!", (1040, 740), 1080)
    assert far[2] <= 1050  # a character at the edge: its bubble leans in
    draft = _draft()
    sides = [bw_script.ElementDraft(kind="local", place="Canada", icon="king", flag="Flag of the United Kingdom.svg", cue="border",
                                    react="south", mood="grin", say="Mine now."),
             bw_script.ElementDraft(kind="local", place="Minnesota", icon="settler", flag="Flag of the United States.svg", cue="border",
                                    react="parallel", mood="shocked", say="Wait, what?")]
    draft.scenes[4].elements += sides
    assert bw_script.check(draft, _research(), _find(world)) == []
    draft.scenes[4].elements[-1].say = "We never ever signed any of that"
    assert any("says 7 words" in p for p in bw_script.check(draft, _research(), _find(world)))
    draft.scenes[4].elements[-1].say = "Wait, what?"
    draft.scenes[4].elements[-1].flag = sides[0].flag
    assert any("same side" in p for p in bw_script.check(draft, _research(), _find(world)))
    draft.scenes[4].elements[-1].flag = "Flag of the United States.svg"
    draft.scenes[4].elements.append(bw_script.ElementDraft(kind="local", place="Canada", icon="farmer", cue="border"))
    assert any("3 locals" in p for p in bw_script.check(draft, _research(), _find(world)))
    assert "say" in bw_script.SCENE_RULES and "Team" in bw_script.SCENE_RULES


def test_a_trip_back_in_time_rewinds_into_the_old_year_and_plays_it_as_old_film_until_today(world):
    from modules import bw_era

    data = {"title": "Why?", "seed": 2, "theme": "atlas", "scenes": [
        {"kind": "map", "narration": "There is a notch on the map.", "focus": ["Minnesota"]},
        {"kind": "map", "narration": "Back then a surveyor came.", "focus": ["Minnesota"], "headline": "1783"},
        {"kind": "map", "narration": "He drew a line north.", "focus": ["Canada"]},
        {"kind": "map", "narration": "Today the notch is still there.", "focus": ["Minnesota"]},
    ]}
    narration = " ".join(s["narration"] for s in data["scenes"])
    spec = bw_render.spec_from_dict(data, _words(narration))
    assert bw_render.prepare(spec) == []
    assert bw_era.stretch(spec) == [1, 2]  # from the old year until the narration says today
    assert bw_render.Renderer(spec).era is None  # off unless the channel turns it on (BW_REWIND)
    assert all(e[1] != "rewind" for e in bw_sfx.events(spec))
    spec.rewind = True
    r = bw_render.Renderer(spec)
    start, end = spec.scenes[1].start, spec.scenes[2].end
    assert r.era.rewinding(start + 0.3) and not r.era.rewinding(start + bw_era.REWIND + 0.1)
    plain = bw_render.spec_from_dict(data, _words(narration))
    bw_render.prepare(plain)
    mid = (start + bw_era.REWIND + end) / 2
    old, then = (np.asarray(x.frame(mid), dtype=float)[300:1300] for x in (r, bw_render.Renderer(plain)))
    warmth = lambda a: a[..., 0].mean() - a[..., 2].mean()
    assert warmth(old) > warmth(then) + 15  # an old warm print
    assert (old.max(axis=2) - old.min(axis=2)).mean() > 0.4 * (then.max(axis=2) - then.min(axis=2)).mean()  # still in colour
    at = spec.scenes[3].end - 0.1
    now, before = (np.asarray(x.frame(at), dtype=float)[300:1300] for x in (r, bw_render.Renderer(plain)))
    assert abs(now - before).mean() < 1  # the colour is back
    assert (start, "rewind", 0.0) in bw_sfx.events(spec)
    assert bw_era.fits(spec, []) and bw_era.fits(spec, [False, False, True])
    assert not bw_era.fits(spec, [True]) and not bw_era.fits(spec, [False, True])  # spaced out
    spec.style = "war"
    assert not bw_era.fits(spec, [])  # a war map's whole story is the past
    one = bw_render.spec_from_dict({**data, "scenes": data["scenes"][:2] + data["scenes"][3:]}, _words(narration))
    assert bw_era.stretch(one) == [1] and not bw_era.fits(one, [])  # one old scene is a date, not a trip
    assert bw_era.stretch(bw_render.spec_from_dict({**data, "scenes": data["scenes"][:1] + [{**data["scenes"][1], "headline": "2015"}]},
                                                   _words(narration))) == []  # a recent year is not the past


def test_a_stage_scene_leaves_the_map_for_an_old_painting(world, fx_offline, tmp_path):
    from PIL import Image

    from modules import bw_channel, bw_photos

    art = tmp_path / "painting.jpg"
    Image.new("RGB", (900, 1300), (170, 60, 200)).save(art)
    painting = bw_photos.Photo(art, "File:Crossing the lake (1866).jpg", "https://commons.wikimedia.org/wiki/File:X", "A. Painter", "Public domain")
    data = {"title": "Why?", "cover_text": "A notch", "seed": 1, "look": "satellite", "scenes": [
        {"kind": "map", "narration": "There is a notch on the map.", "focus": ["Minnesota"],
         "elements": [{"kind": "highlight", "place": "Minnesota"}]},
        {"kind": "stage", "narration": "Then the surveyors crossed the frozen lake.", "focus": ["Lake of the Woods"],
         "painting": "Surveyors crossing the lake", "year": "1783", "elements": [
             {"kind": "local", "icon": "citizen", "cue": "surveyors", "react": "frozen", "say": "So cold.", "flag": "Flag of France.svg"}]},
        {"kind": "map", "narration": "And the line stayed.", "focus": ["Minnesota"]}]}
    words = _words(" ".join(s["narration"] for s in data["scenes"]))
    spec = bw_render.spec_from_dict(data, words)
    bw_render.fetch_photos(spec, find=lambda q: None, paint=lambda q: painting if "crossing" in q else None)
    assert bw_render.prepare(spec) == [] and spec.scenes[1].kind == "stage"
    r = fx_offline.FxRenderer(spec)
    stage = spec.scenes[1]
    assert not [e for e in stage.elements if e.opts.get("auto")]  # no added GIFs on a painting
    mid = np.asarray(r.frame((stage.start + stage.end) / 2), dtype=float)
    assert abs(mid[500:700, 100:300].mean(axis=(0, 1)) - (170, 60, 200)).max() < 40  # the painting fills the frame
    assert np.asarray(r.frame(stage.end + 1.0), dtype=float)[500:700, 100:300, 1].mean() > 100  # back on the (green) map
    assert bw_channel.with_credits("About it.", spec) == "About it."  # a public-domain painting needs no credit
    assert (stage.start, "whoosh", 0.0) in bw_sfx.events(spec)
    # no painting found: the moment shows on the map
    plain = bw_render.spec_from_dict(data, words)
    bw_render.fetch_photos(plain, find=lambda q: None, paint=lambda q: None)
    assert plain.scenes[1].kind == "map"


def test_a_stage_needs_a_painting_and_only_its_characters():
    draft = _draft()
    local = bw_script.ElementDraft(kind="local", icon="citizen", say="So cold.", cue="treaty", react="map")
    draft.scenes[1] = bw_script.SceneDraft(kind="stage", narration=draft.scenes[1].narration, painting="Treaty of Paris signing",
                                           elements=[local])
    found = lambda q: object()
    problems = lambda d, paint=found: [p for p in bw_script.check(d, _research(), lambda n: True, paint_find=paint) if "stage" in p]
    assert problems(draft) == []
    assert problems(draft, lambda q: None) and "no old painting found" in problems(draft, lambda q: None)[0]
    draft.scenes[1].elements.append(bw_script.ElementDraft(kind="icon", icon="money_bag"))
    assert "1-2 locals and nothing else" in problems(draft)[0]
    draft.scenes[1].elements.pop()
    draft.scenes[0] = draft.scenes[1]
    assert any("first scene can't be a stage" in p for p in problems(draft))
    assert "stage | " not in bw_script.SCENE_RULES and "a stage scene (at most one" in bw_script.SCENE_RULES


def test_a_paper_scene_lands_the_eras_newspaper_on_the_map(world, fx_offline):
    data = {"title": "Why?", "cover_text": "A notch", "seed": 1, "look": "satellite", "scenes": [
        {"kind": "map", "narration": "There is a notch on the map.", "focus": ["Minnesota"],
         "elements": [{"kind": "highlight", "place": "Minnesota"}]},
        {"kind": "paper", "narration": "In 1783 the papers said the line was drawn.", "focus": ["Minnesota"], "year": "1783",
         "title": "The Line Is Drawn", "subtitle": "A notch for the lake", "kicker": "Paris, September 3, 1783"},
        {"kind": "map", "narration": "And the line stayed.", "focus": ["Minnesota"]}]}
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == [] and spec.scenes[1].kind == "paper"
    r = fx_offline.FxRenderer(spec)
    paper = spec.scenes[1]
    mid = np.asarray(r.frame(paper.start + 2.0), dtype=float)
    assert abs(mid[900:1000, 400:700].mean(axis=(0, 1)) - (215, 200, 165)).max() < 45  # the yellowed page fills the middle
    assert (paper.start, "paper", 0.0) in bw_sfx.events(spec)
    from modules import bw_paper

    p = bw_paper.Paper.from_scene(paper)
    assert (p.name, p.date, p.headline, p.place) == (bw_paper.MASTHEAD, "PARIS, SEPTEMBER 3, 1783", "The Line Is Drawn", "Minnesota")


def test_a_paper_scene_needs_a_headline_a_year_and_its_place():
    draft = _draft()
    draft.scenes[1] = bw_script.SceneDraft(kind="paper", narration=draft.scenes[1].narration, title="Alaska Sold To America",
                                           subtitle="For $7.2 million", kicker="Washington, March 30, 1867", year="1867",
                                           focus=["Alaska"])
    problems = lambda d: [p for p in bw_script.check(d, _research(), lambda n: n != "Atlantis") if "paper" in p or "newspaper" in p]
    assert problems(draft) == []
    draft.scenes[1].title = "Sold"
    assert "headline, 3-7 words" in problems(draft)[0]
    draft.scenes[1].title, draft.scenes[1].kicker, draft.scenes[1].year = "Alaska Sold To America", "Washington", ""
    assert "give its year" in problems(draft)[0]
    draft.scenes[1].year, draft.scenes[1].focus = "1867", ["Atlantis"]
    assert "the one place" in problems(draft)[0]
    draft.scenes[1].focus = ["Alaska"]
    draft.scenes[1].elements = [bw_script.ElementDraft(kind="icon", icon="money_bag")]
    assert "has elements" in problems(draft)[0]
    draft.scenes[1].elements = []
    draft.scenes[0] = draft.scenes[1]
    assert any("first scene can't be a newspaper" in p for p in problems(draft))
    assert any("2 newspapers" in p for p in problems(draft))
    assert "a paper scene (at most one" in bw_script.SCENE_RULES


def test_special_transitions_play_at_the_big_jumps_each_a_different_one(world, fx_offline):
    from modules import bw_moves

    scene = lambda kind, focus, **kw: {"kind": kind, "narration": f"Words about {focus}.", "focus": [focus], **kw}
    data = {"title": "Why?", "cover_text": "A notch", "seed": 1, "look": "satellite", "scenes": [
        scene("map", "Minnesota", elements=[{"kind": "highlight", "place": "Minnesota"}]),
        scene("map", "Minnesota"), scene("map", "France"), scene("map", "France"), scene("map", "Minnesota"),
        scene("map", "Minnesota"), scene("map", "France"), scene("map", "France"),
        scene("paper", "France", title="The Line Is Drawn Today", year="1783", kicker="Paris, 1783"), scene("map", "France")]}
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == []
    assert bw_moves.planned(spec) == {}  # off unless the channel turns it on
    spec.moves = True
    moves = bw_moves.planned(spec)
    assert 1 <= len(moves) <= bw_moves.MAX_MOVES and 8 not in moves and 9 not in moves  # the newspaper lands on its own
    assert all(b - a > bw_moves.GAP for a, b in zip(sorted(moves), sorted(moves)[1:]))
    assert len(set(moves.values())) == len(moves)  # never the same one twice
    i = min(moves)
    r = fx_offline.FxRenderer(spec)
    s = spec.scenes[i]
    mid = r.frame(s.start + bw_moves.SECONDS[moves[i]] / 2)
    assert mid.size == (bw_render.W, bw_render.H)
    assert (s.start, bw_moves.SOUNDS[moves[i]], 0.0) in bw_sfx.events(spec) or any(abs(e[0] - s.start) < 0.01 for e in bw_sfx.events(spec))


def test_each_transition_starts_on_the_scene_before_and_ends_on_the_next():
    from PIL import Image

    from modules import bw_moves

    a, b = Image.new("RGB", (216, 384), (200, 30, 30)), Image.new("RGB", (216, 384), (30, 30, 200))
    for name in bw_moves.MOVES:
        start = np.asarray(bw_moves.frame(name, a, b, 0.0), dtype=float)
        end = np.asarray(bw_moves.frame(name, a, b, 1.0), dtype=float)
        assert abs(np.median(start.reshape(-1, 3), axis=0) - (200, 30, 30)).max() < 2, name
        assert abs(np.median(end.reshape(-1, 3), axis=0) - (30, 30, 200)).max() < 2, name


def test_a_moment_scene_shows_the_everyday_situation_off_the_map(world, fx_offline):
    data = {"title": "Why?", "cover_text": "One clock", "seed": 1, "look": "satellite", "scenes": [
        {"kind": "map", "narration": "There is a notch on the map.", "focus": ["Minnesota"],
         "elements": [{"kind": "highlight", "place": "Minnesota"}]},
        {"kind": "moment", "narration": "Shops there open at ten in the dark until the sun finally rises.", "focus": ["Minnesota"],
         "sky": "night", "clock": "10:00", "clock_label": "Beijing time", "shop": "Bazaar", "shutter": "open", "sunrise": "sun",
         "elements": [{"kind": "local", "icon": "citizen", "cue": "shops", "react": "dark", "say": "So dark!"}]},
        {"kind": "map", "narration": "And the line stayed.", "focus": ["Minnesota"]}]}
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == [] and spec.scenes[1].kind == "moment"
    r = fx_offline.FxRenderer(spec)
    m = spec.scenes[1]
    frame = np.asarray(r.frame(m.start + 0.5), dtype=float)
    assert frame[100:400, 100:1000].mean(axis=(0, 1))[2] > frame[100:400, 100:1000].mean(axis=(0, 1))[0]  # a night sky
    moment = r.moments[1]
    assert (moment.sky, moment.clock, moment.clock_label, moment.shop) == ("night", "10:00", "BEIJING TIME", "Bazaar")
    assert 0 < moment.open_at < moment.sunrise_at and len(moment.cast) == 1
    assert (m.start, "whoosh", 0.0) in bw_sfx.events(spec)


def test_a_moment_scene_is_checked():
    draft = _draft()
    local = bw_script.ElementDraft(kind="local", icon="merchant", say="So dark!", cue="treaty", react="map")
    draft.scenes[1] = bw_script.SceneDraft(kind="moment", narration=draft.scenes[1].narration, sky="night", clock="10:00",
                                           clock_label="Beijing time", elements=[local])
    problems = lambda d: [p for p in bw_script.check(d, _research(), lambda n: True) if "moment" in p]
    assert problems(draft) == []
    draft.scenes[1].sky, draft.scenes[1].clock = "midnight", "ten"
    assert any("night, dawn, day or sunset" in p for p in problems(draft)) and any("an hour like 10:00" in p for p in problems(draft))
    draft.scenes[1].sky, draft.scenes[1].clock, draft.scenes[1].shutter = "night", "10:00", "zebra"
    assert "shutter word 'zebra' is not in its narration" in problems(draft)[0]
    draft.scenes[1].shutter = ""
    draft.scenes[1].elements.append(bw_script.ElementDraft(kind="icon", icon="money_bag"))
    assert "0-2 locals and nothing else" in problems(draft)[0]
    draft.scenes[1].elements.pop()
    draft.scenes[0] = draft.scenes[1]
    assert any("first scene can't be a moment" in p for p in problems(draft))
    assert "a moment scene (at most two" in bw_script.SCENE_RULES


def test_the_painting_is_an_old_work_of_art_of_that_moment():
    from modules import bw_stage

    def info(title, made, cats="Paintings", w=1200, h=1600, licence="Public domain", desc=""):
        return {"title": title, "mime": "image/jpeg", "width": w, "height": h, "extmetadata": {
            "DateTimeOriginal": {"value": made}, "LicenseShortName": {"value": licence},
            "Categories": {"value": cats}, "ImageDescription": {"value": desc}}}

    infos = [
        info("File:Hannibal crossing the Alps map.png", "1900"),  # a map
        info("File:Hannibal crossing the Alps 2019.jpg", "2019", cats="Photographs"),  # a photo of today
        info("File:Hannibal letter.jpg", "1700", cats="Documents"),  # a page of text
        info("File:Comic History of Rome Hannibal crossing the Alps.jpg", "1850", cats="Engravings"),  # a caricature
        info("File:Hannibal crossing the Alps, engraving.jpg", "1881", cats="Engravings"),
        info("File:Leutemann - Hannibals Übergang über die Alpen.jpg", "1866", cats="Oil paintings",
             desc="Hannibal's crossing of the Alps"),
        info("File:Elephants in a zoo.jpg", "1890"),  # not the moment
    ]
    assert bw_stage._pick("Hannibal crossing the Alps", infos)["title"].startswith("File:Leutemann")  # in colour wins
    assert bw_stage._pick("Hannibal crossing the Alps", infos[:5])["title"] == "File:Hannibal crossing the Alps, engraving.jpg"
    assert bw_stage._pick("Hannibal crossing the Alps", infos[:4]) is None


def test_a_bridge_spans_the_closest_shores_and_a_measure_says_how_far():
    from modules import bw_build, bw_geo

    def square(name, x0, y0, x1, y1, *more):
        corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]], dtype=float)
        rings = [np.concatenate([np.linspace(a, b, 60, endpoint=False) for a, b in zip(corners, corners[1:])])]  # a dense outline
        parts = [bw_geo.Part([r], (float(r[:, 0].min()), float(r[:, 1].min()), float(r[:, 0].max()), float(r[:, 1].max())))
                 for r in rings + [np.array(m, dtype=float) for m in more]]
        return bw_geo.Feature("region", name, parts=parts)

    island = square("Island", 15.0, 38.0, 15.6, 38.3)
    main = square("Main", 15.627, 38.1, 16.2, 38.6)  # 0.027 degrees of sea between them: about 2.4 km
    p, q = bw_build.closest(island, main)
    assert p[0] == pytest.approx(15.6) and q[0] == pytest.approx(15.627) and 2.0 < bw_build.km(p, q) < 2.6
    # a part of one that touches the other over land (Ceuta on Morocco) is no shore: the crossing is over the sea
    exclave = [[16.2, 38.2], [16.3, 38.2], [16.3, 38.3], [16.2, 38.3], [16.2, 38.2]]
    p, q = bw_build.closest(square("Island", 15.0, 38.0, 15.6, 38.3, exclave), main)
    assert p[0] == pytest.approx(15.6)
    assert bw_build.km((-0.1276, 51.5072), (2.3522, 48.8566)) == pytest.approx(344, abs=5)  # London to Paris
    el = bw_render.Element("measure", place="Island", to="Main", feature=island, feature_to=main)
    big, small = bw_build.length_label(el)
    assert big.endswith(" KM") and small.startswith("(") and small.endswith(" MI)")
    assert bw_build.length_label(bw_render.Element("measure", text="3 km", feature=island, feature_to=main)) == ("3 KM", "")
    # on screen a narrow strait's bridge is drawn as a readable symbol, reaching onto both shores
    a, b = bw_build.stretch((100, 100), (110, 100), bw_build.MIN_BRIDGE)
    assert b[0] - a[0] == pytest.approx(bw_build.MIN_BRIDGE) and (a[0] + b[0]) / 2 == pytest.approx(105)


def test_structures_drop_and_build_on_the_map(world, fx_offline):
    data = {"title": "Why?", "cover_text": "A notch", "seed": 1, "look": "satellite", "scenes": [
        {"kind": "map", "narration": "There is a notch on the map.", "focus": ["Minnesota"],
         "elements": [{"kind": "highlight", "place": "Minnesota"}]},
        {"kind": "map", "narration": "They planned a bridge and measured the gap, then built a wall.", "focus": ["Minnesota"],
         "elements": [{"kind": "build", "icon": "bridge", "place": "Minnesota", "to": "Lake of the Woods", "cue": "bridge"},
                      {"kind": "measure", "place": "Minnesota", "to": "Paris", "cue": "measured"},
                      {"kind": "build", "icon": "wall", "points": ["Minnesota", "Paris"], "cue": "wall"}]}]}
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    bridge, measure, wall = spec.scenes[1].elements[:3]
    assert len(wall.opts["coords"]) == 2 and bridge.feature_to is not None and measure.feature_to is not None
    before = np.asarray(r.frame(bridge.at - 0.05), dtype=float)
    after = np.asarray(r.frame(wall.at + 2.0), dtype=float)
    assert abs(after - before).mean() > 1  # something was drawn
    found = bw_sfx.events(spec)
    assert (bridge.at + 0.45, "stamp", 0.0) in found and (wall.at, "whoosh", 0.0) in found


def test_a_build_needs_its_kind_and_its_ends():
    draft = _draft()
    scene = draft.scenes[2]
    scene.elements.append(bw_script.ElementDraft(kind="build", icon="tower", place="Minnesota", to="Canada", cue="map"))
    scene.elements.append(bw_script.ElementDraft(kind="build", icon="wall", place="Minnesota", cue="map"))
    scene.elements.append(bw_script.ElementDraft(kind="measure", place="Minnesota", cue="map"))
    problems = bw_script.check(draft, _research(), lambda n: True)
    assert any("bridge, wall, fence or canal" in p for p in problems)
    assert any("a build needs" in p for p in problems)
    assert any("a measure needs the two places" in p for p in problems)
    assert "build (a structure drawn on the map" in bw_script.SCENE_RULES


def test_a_bridge_crosses_from_land_to_land_not_along_the_shore():
    from modules import bw_build, bw_geo

    def land(name, corners, *more):
        def dense(c):
            c = np.array(c + [c[0]], dtype=float)
            return np.concatenate([np.linspace(a, b, 40, endpoint=False) for a, b in zip(c, c[1:])])
        rings = [dense(corners)] + [dense(m) for m in more]
        return bw_geo.Feature("region", name, parts=[bw_geo.Part([r], (float(r[:, 0].min()), float(r[:, 1].min()),
                                                                       float(r[:, 0].max()), float(r[:, 1].max()))) for r in rings])

    # an island to the west whose north-east tip sits just north of the mainland's west tip: the narrowest gap runs
    # north-south, but the lands lie west and east of each other
    west = land("West", [[14.0, 38.0], [15.5, 38.0], [15.6, 38.3], [14.0, 38.3]])
    east = land("East", [[15.62, 38.0], [16.5, 38.0], [16.5, 38.5], [15.62, 38.26]])
    el = bw_render.Element("build", place="West", to="East", feature=west, feature_to=east, opts={"icon": "bridge"})
    e, n = bw_build.heading(el)
    assert e > 0.8 and abs(n) < 0.6  # it runs west to east, from land to land
    cam = bw_render.Cam(15.6, 38.2, 900.0)
    a, b = bw_build.span(cam, el, bw_build.MIN_BRIDGE)
    assert bw_build.inside(west, *cam.lonlat(*a)) and bw_build.inside(east, *cam.lonlat(*b))
    # a speck of rock a country keeps off the other's coast is no shore (Spain's rocks off Morocco)
    rock = [[16.0, 38.29], [16.002, 38.29], [16.002, 38.292], [16.0, 38.292]]
    spain = land("West", [[14.0, 38.0], [15.5, 38.0], [15.6, 38.3], [14.0, 38.3]], rock)
    p, _ = bw_build.closest(spain, east)
    assert p[0] < 15.7


def test_a_split_screen_shows_places_side_by_side_and_a_jump_raises_flags(world, fx_offline):
    from modules import bw_compare

    data = {"title": "Why?", "cover_text": "A notch", "seed": 1, "look": "satellite", "scenes": [
        {"kind": "map", "narration": "There is a notch on the map.", "focus": ["Minnesota"],
         "elements": [{"kind": "highlight", "place": "Minnesota"}]},
        {"kind": "compare", "narration": "Compare the lake and the river and the state.", "elements": [
            {"kind": "measure", "place": "Minnesota", "to": "Lake of the Woods", "text": "3 KM", "title": "The notch", "cue": "Compare"},
            {"kind": "highlight", "place": "Mississippi River", "cue": "river"},
            {"kind": "highlight", "place": "Canada", "title": "Up north", "cue": "state"}]},
        {"kind": "map", "narration": "Far away in Paris nobody knew.", "focus": ["Paris"], "elements": [
            {"kind": "pin", "place": "Paris", "cue": "nobody"}]}]}
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    split = spec.scenes[1]
    c = r.compare_of(1)
    assert isinstance(c, bw_compare.Compare) and len(c.strips) == 3 and c.width == bw_render.W // 3
    assert c.strips[0].title == "The notch" and c.strips[2].names == ["Canada"]
    img = np.asarray(r.frame(split.start + 1.5), dtype=float)
    x = c.width  # the white line between the first two strips
    assert img[1500:1600, x - 2:x + 2].mean() > 200
    assert (split.start, "whoosh", 0.0) in bw_sfx.events(spec)
    # the camera flies from Minnesota to Paris and nothing shows at first: France's flag rises on a pole
    paris = spec.scenes[2]
    flags = [e for e in paris.elements if e.opts.get("_arrival")]
    assert [e.opts["flag"] for e in flags] == ["Flag of France.svg"] and flags[0].at < paris.start + 0.6


def test_a_split_screen_needs_two_or_three_strips():
    draft = _draft()
    strip = lambda **k: bw_script.ElementDraft(cue="map", **k)
    draft.scenes[2] = bw_script.SceneDraft(kind="compare", narration=draft.scenes[2].narration,
                                           elements=[strip(kind="measure", place="Minnesota", to="Canada", title="A"),
                                                     strip(kind="highlight", place="Canada")])
    problems = lambda: [p for p in bw_script.check(draft, _research(), lambda n: True) if "split screen" in p]
    assert problems() == []
    draft.scenes[2].elements = draft.scenes[2].elements[:1]
    assert "2-3 strips" in problems()[0]
    draft.scenes[2].elements = [strip(kind="build", icon="wall", place="Minnesota", to="Canada"), strip(kind="highlight", place="Canada")]
    assert any("a strip's build is a bridge" in p for p in problems())
    assert "a compare scene (a split screen" in bw_script.SCENE_RULES



def test_the_mascots_last_line_asks_the_viewer_a_two_way_question(world):
    draft = _draft()
    draft.scenes[4].react = "So it's a rooster carved by treaties."  # the China Short's summary: 0 comments
    problems = bw_script.check(draft, _research(), _find(world))
    assert any("last line must be a react on the last scene" in p for p in problems)
    draft.scenes[4].react = "Should China get its rooster head back, yes or no?"
    assert not any("last line" in p for p in bw_script.check(draft, _research(), _find(world)))
    assert "Never a landscape, a river" in bw_script.PROMPT and "in EVERY video" in bw_script.PROMPT


def test_the_storys_people_talk_on_a_photo(world, fx_offline, tmp_path):
    from PIL import Image

    from modules import bw_local, bw_photos

    art = tmp_path / "face.jpg"
    Image.new("RGB", (800, 1000), (90, 140, 200)).save(art)
    photo = bw_photos.Photo(art, "File:A face.jpg", "https://commons.wikimedia.org/wiki/File:A", "Someone", "CC BY-SA 4.0")
    data = {"title": "Why?", "cover_text": "A notch", "seed": 1, "look": "satellite", "scenes": [
        {"kind": "map", "narration": "There is a notch on the map.", "focus": ["Minnesota"], "elements": [{"kind": "highlight", "place": "Minnesota"}]},
        {"kind": "photo", "narration": "The king signed the deal and smiled.", "photo": "A face", "caption": "The king",
         "elements": [{"kind": "local", "icon": "citizen", "cue": "king", "react": "smiled", "say": "Deal!", "flag": "Flag of France.svg"}]}]}
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    bw_render.fetch_photos(spec, find=lambda q: photo, paint=lambda q: None)
    assert bw_render.prepare(spec) == []
    r = fx_offline.FxRenderer(spec)
    said = []
    real = bw_local.say
    bw_local.say = lambda ov, text, *a, **k: said.append(text) or real(ov, text, *a, **k)
    try:
        r.frame(spec.scenes[1].end - 0.1)
    finally:
        bw_local.say = real
    ps = r.photos[1]
    assert len(ps.cast) == 1 and ps.guest == "" and said == ["Deal!"]


def test_no_arrival_flag_where_todays_flag_names_the_wrong_owner(world, fx_offline):
    """Alaska "in the 1800s" was Russia's: a jump there raises no US flag (the owner, 2026-11-08), nor in a scene
    that already flies whose land it is."""
    data = {"title": "Why?", "cover_text": "A notch", "seed": 1, "look": "satellite", "scenes": [
        {"kind": "map", "narration": "There is a notch on the map.", "focus": ["Minnesota"],
         "elements": [{"kind": "highlight", "place": "Minnesota"}]},
        {"kind": "map", "narration": "In the 1800s Paris was far away.", "focus": ["Paris"], "headline": "1800s",
         "elements": [{"kind": "pin", "place": "Paris", "cue": "away"}]},
        {"kind": "map", "narration": "Back to the notch again.", "focus": ["Minnesota"],
         "elements": [{"kind": "pin", "place": "Minnesota", "cue": "notch"}]},
        {"kind": "map", "narration": "Paris flies its own flag.", "focus": ["Paris"],
         "elements": [{"kind": "highlight", "place": "France", "flag": "Flag of France.svg", "cue": "Paris"}]}]}
    spec = bw_render.spec_from_dict(data, _words(" ".join(s["narration"] for s in data["scenes"])))
    assert bw_render.prepare(spec) == []
    fx_offline.FxRenderer(spec)
    arrived = [i for i, s in enumerate(spec.scenes) if any(e.opts.get("_arrival") for e in s.elements)]
    assert arrived == [2]  # only the jump back to today's Minnesota
