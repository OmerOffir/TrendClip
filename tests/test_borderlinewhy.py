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


def _draft(narration_words: int = 120, quote_ok: bool = True) -> bw_script.Draft:
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

    def ask(client, model, prompt, schema, temperature=0.7):
        calls["prompts"].append(prompt)
        return _draft()

    monkeypatch.setattr(bw_script, "gemini", lambda client=None: (None, "m"))
    monkeypatch.setattr(bw_script, "_ask", ask)
    monkeypatch.setattr(bw_script, "check", lambda draft, res: calls["rules"].pop(0) if calls["rules"] else [])
    monkeypatch.setattr(bw_script, "verify", lambda client, model, draft, res: calls["facts"].pop(0) if calls["facts"] else [])
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
    assert "Sources:" in saved["description"] and saved["duration_seconds"] == pytest.approx(45.0)
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
    assert body["videos"][0]["key"] == job.key and body["videos"][0]["state"] == "waiting"
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
    assert by["14:30"]["state"] == "waiting" and by["14:30"]["video"]["title"] == SCRIPT["title"]
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
def fx_offline(monkeypatch):
    """bw_fx without the network: a plain-color world picture, made-up flags and icons."""
    from PIL import Image

    from modules import bw_fx

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


def test_check_wants_known_icons_and_flags_on_poles(world):
    from modules import bw_fx

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


def test_a_manual_script_is_made_with_its_origin(settings, tmp_path, world, monkeypatch):
    monkeypatch.setitem(SCRIPT, "origin", "manual")
    job, info, rendered = _make(settings, tmp_path, world)
    assert info["origin"] == "manual" and "origin" not in json.loads((tmp_path / job.key / "script.json").read_text())
