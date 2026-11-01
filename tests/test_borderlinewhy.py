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


def _draft(narration_words: int = 90, quote_ok: bool = True) -> bw_script.Draft:
    filler = " ".join(["The map shows the lake and the border clearly."] * ((narration_words - 20) // 9))
    scenes = [
        bw_script.SceneDraft(kind="map", narration="There is a notch on top of Minnesota.", focus=["Lake of the Woods"],
                             elements=[bw_script.ElementDraft(kind="highlight", place="Minnesota", cue="Minnesota")]),
        bw_script.SceneDraft(kind="card", narration="In 1783 a treaty used an old map.", title="1783"),
        bw_script.SceneDraft(kind="map", narration=filler, focus=["Minnesota"]),
        bw_script.SceneDraft(kind="map", narration="A line due west never met the river.", focus=["Mississippi River"],
                             elements=[bw_script.ElementDraft(kind="parallel", value=49.4, color="red")]),
        bw_script.SceneDraft(kind="map", narration="So the border dropped south to the 49th parallel.", focus=["Canada"],
                             elements=[bw_script.ElementDraft(kind="stamp", text="Patched", color="gold")]),
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


def test_check_takes_boards_and_the_mascot_lines(world):
    draft = _draft()
    draft.scenes[1] = bw_script.SceneDraft(kind="board", narration="In 1783 a treaty used an old map.", ask="Wait, how did that happen?",
                                           board_write=["The 1783 map", "A lake shaped like an egg"], board_draw=["Minnesota"])
    draft.scenes[4].react = "So a bad map made the notch?"
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


def test_description_lists_sources_and_credits():
    text = bw_script.description({"question": "Why?", "description": "Because.", "hashtags": ["borders"],
                                  "sources": [{"title": "MNHS", "url": "https://mnhs.org"}]}, "@BorderlineWhy")
    assert "Sources:\n- MNHS: https://mnhs.org" in text
    assert "Natural Earth" in text and "#borders" in text and "@BorderlineWhy" in text
    assert "NASA" in text and "CC BY 4.0" in text  # the satellite look's pictures are credited
    assert "NASA" not in bw_script.description({"look": "flat"})


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
    assert bw_channel.due_posts(settings, job.post_at - timedelta(minutes=1), set()) == []
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
    assert "Sources:" in saved["description"] and saved["duration_seconds"] == pytest.approx(45.0 + 1.2)  # + the outro's end card
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
    result = bw_channel.approve(job.key, settings, tmp_path, now=job.post_at - timedelta(hours=1), youtube=yt)
    assert result["record"] is None and uploads == []  # before the slot: it waits
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
    job, info, _ = _make(settings, tmp_path, world)
    client = TestClient(app)
    body = client.get("/api/borderlinewhy/videos").json()
    assert body["videos"][0]["key"] == job.key and body["videos"][0]["state"] == "approved"
    assert "Sources:" in body["videos"][0]["youtube"]["description"]
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
    assert spec.scenes[0].opts["globe"] is True and spec.scenes[0].view[2] <= fx_offline.GLOBE_MAX_PPD
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
    return json.dumps({"scenes": [s.model_dump() for s in d.scenes], "claims": [c.model_dump() for c in d.claims]})


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
    assert marks == [(0, 1), (5, 2), (10, 3), (15, 4), (20, 5)] and script["layout"] == "landscape"
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
    assert "Chapters:\n0:00 Hook\n1:35 The treaty\n3:20 Today\n\nSources:" in text


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
    out = bw_long.thumbnail(spec, tmp_path / "thumb.jpg")
    assert Image.open(out).size == (1280, 720)
    bw_render.use_layout("portrait")
    assert (bw_render.W, bw_render.H, fx_offline.W) == (1080, 1920, 1080)


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
    draft.scenes[4].react = "So one wrong lake shape gave Minnesota its notch?"
    assert bw_script.check(draft, _research(), _find(world)) == []  # 2 lines, within the words
    draft.scenes[3].ask = "Hold on, why did nobody check the river first?"
    problems = " | ".join(bw_script.check(draft, _research(), _find(world)))
    assert "the mascot has 3 lines (at most 2)" in problems and "words together (at most 120" in problems
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
