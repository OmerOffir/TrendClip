import pytest

from db import repository
from modules import fetcher


def test_save_dedupes_and_next_story_is_top_rated_new(tmp_path):
    db = tmp_path / "bank.db"
    repository.init_db(db)
    repository.init_db(db)  # twice is fine
    assert repository.save_story("reddit_a", "reddit/UnresolvedMysteries", "A", "text a", "u", 60, db_path=db)
    assert not repository.save_story("reddit_a", "reddit/UnresolvedMysteries", "A again", "x", "u", 99, db_path=db)
    assert repository.save_story("wikipedia_1", "wikipedia/Ghost ships", "B", "text b", "u", 90, db_path=db)
    assert repository.save_story("gemini_c", "gemini", "C", "text c", "", 90, db_path=db)

    first = repository.get_next_story(db)
    assert first["source_id"] == "wikipedia_1" and first["status"] == "NEW" and first["created_at"]
    repository.set_status(first["id"], "USED", db_path=db)
    assert repository.get_next_story(db)["source_id"] == "gemini_c"  # same score: the older one
    repository.set_status(repository.get_next_story(db)["id"], "REJECTED", db_path=db)
    assert repository.get_next_story(db)["title"] == "A"
    # a used story is still a duplicate: it is never fetched (or told) again
    assert not repository.save_story("wikipedia_1", "wikipedia/Ghost ships", "B", "b", "u", 90, db_path=db)
    assert repository.counts(db) == {"NEW": 1, "USED": 1, "REJECTED": 1}
    assert repository.known_ids("wikipedia_", db) == {"wikipedia_1"}
    with pytest.raises(ValueError):
        repository.set_status(1, "DONE", db_path=db)


def test_empty_bank_has_no_next_story(tmp_path):
    repository.init_db(tmp_path / "e.db")
    assert repository.get_next_story(tmp_path / "e.db") is None


FEED = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>t3_abc123</id><title>The vanishing of X</title>
<link href="https://www.reddit.com/r/UnresolvedMysteries/comments/abc123/x/"/>
<content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;{body}&lt;/p&gt;&lt;p&gt;Second &amp;amp; last.&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt; submitted by /u/someone [link] [comments]</content></entry>
<entry><id>t3_short1</id><title>Link post</title><link href="https://x"/><content type="html">tiny</content></entry>
</feed>"""


def test_reddit_feed_parsing():
    posts = fetcher.parse_reddit_feed(FEED.format(body="It was 1987. " * 60))
    cfg = fetcher.SUBREDDITS["UnresolvedMysteries"]
    stories = [fetcher.reddit_story(p, "UnresolvedMysteries", cfg) for p in posts]
    assert stories[1] is None  # the short post is skipped
    s = stories[0]
    assert s["source_id"] == "reddit_abc123" and s["source_name"] == "reddit/UnresolvedMysteries"
    assert s["raw_text"].endswith("Second & last.") and "submitted by" not in s["raw_text"]
    assert s["score"] == 100 and s["url"].endswith("/abc123/x/") and s["pillar"] == "mystery"
    long_post = fetcher.parse_reddit_feed(FEED.format(body="It was 1987. " * 400))[0]
    assert fetcher.reddit_story(long_post, "UnresolvedMysteries", cfg) is None  # over 3,500 characters


def _post(**kw):
    return {"id": "p1", "title": "Case", "text": "It was a cold night. " * 20, "link": "", "url": "u", "rank": 0,
            "ups": None, **kw}


def test_reddit_upvotes_keywords_exclusions_and_wikipedia_links():
    cfg = fetcher.SUBREDDITS["UnresolvedMysteries"]
    assert fetcher.reddit_story(_post(ups=99), "UnresolvedMysteries", cfg) is None  # under 100 upvotes
    assert fetcher.reddit_story(_post(ups=1000), "UnresolvedMysteries", cfg)["score"] == 75
    assert fetcher.reddit_story(_post(title="New podcast episode"), "UnresolvedMysteries", cfg) is None
    assert fetcher.reddit_story(_post(title="The Hollow [Fiction]"), "UnresolvedMysteries", cfg) is None
    assert fetcher.reddit_story(_post(link="https://news.example/x"), "UnresolvedMysteries", cfg) is None

    til = fetcher.SUBREDDITS["todayilearned"]
    article = {"pageid": 42, "title": "Dancing plague of 1518", "text": "Hundreds danced for days. " * 30,
               "url": "https://en.wikipedia.org/wiki/Dancing_plague_of_1518"}
    asked = []
    fetcher_wiki = fetcher.wiki_article
    try:
        fetcher.wiki_article = lambda session, pageid=None, title=None: asked.append(title) or article
        link = _post(title="TIL in 1518 people danced for days", link="https://en.wikipedia.org/wiki/Dancing_plague_of_1518")
        assert fetcher.reddit_story(link, "todayilearned", til, session=object()) is None  # no keyword
        s = fetcher.reddit_story({**link, "title": "TIL of a bizarre plague of dancing"}, "todayilearned", til,
                                 session=object())
        assert asked[-1] == "Dancing plague of 1518"
        assert s["source_id"] == "wikipedia_42" and s["title"] == "Dancing plague of 1518" and s["pillar"] == "history"
        assert s["raw_text"].startswith("TIL of a bizarre plague") and s["source_name"] == "reddit/todayilearned"
        assert fetcher.reddit_story({**link, "title": "TIL bizarre"}, "todayilearned", til, session=object(),
                                    skip={"wikipedia_42"}) is None  # that article is already in the bank
    finally:
        fetcher.wiki_article = fetcher_wiki


def test_screen_and_fit():
    assert fetcher.screen("A", "x" * 199) == "too short (199 chars)"
    assert fetcher.screen("A", "x" * 3501).startswith("too long")
    assert fetcher.screen("A", "a quiet town " * 20, keywords=True) == "no DarkArchives keyword"
    assert fetcher.screen("A strange town", "a quiet town " * 20, keywords=True) == ""
    assert fetcher.screen("A flag", "a quiet town " * 20 + "a mystery", keywords=True) == "no DarkArchives keyword"
    assert fetcher.screen("A flag", "a quiet town " * 20 + "a bizarre mystery", keywords=True) == ""
    assert fetcher.screen("List of hoaxes", "a quiet town " * 20) == "a list, not a story"
    text = "\n\n".join(["p" * 1000] * 5)
    assert fetcher.fit(text) == "\n\n".join(["p" * 1000] * 3)
    assert fetcher.fit("One. " * 1000).endswith("One.") and len(fetcher.fit("One. " * 1000)) <= 3500


LISTVERSE = """<h1>10 Terrifying Medical Mysteries</h1><p>Intro.</p>
<h2><span>10</span> The Radium Girls&rsquo; Glow</h2><div class="lyte"></div><p><noscript>video</noscript></p>
<p>{a}</p><p>Related: 10 Other Things</p><p>{a}</p>
<h2><span>9</span> Tiny</h2><p>Too short.</p>
<h3>Who's Behind Listverse?</h3><p>About us.</p>"""
RSS = """<?xml version="1.0"?><rss><channel>
<item><title>10 Terrifying Medical Mysteries</title><link>https://listverse.com/2026/10/13/10-medical-mysteries/</link>
<description>Doctors were baffled.</description><category><![CDATA[Mysteries]]></category></item>
<item><title>10 Wild Facts About Bears</title><link>https://listverse.com/2026/10/14/bears/</link>
<description>Bears are big.</description><category><![CDATA[Animals]]></category></item>
</channel></rss>"""
ATLAS = """<?xml version="1.0"?><rss><channel>
<item><title>The Vanishing Village</title><link>https://www.atlasobscura.com/places/vanishing-village</link>
<description>&lt;p&gt;{a}&lt;/p&gt;</description></item>
<item><title>A Nice Cafe</title><link>https://www.atlasobscura.com/places/cafe</link>
<description>&lt;p&gt;{b}&lt;/p&gt;</description></item>
</channel></rss>"""


def test_rss_feeds_atlas_places_and_listverse_entries():
    items = fetcher.listverse_items(LISTVERSE.format(a="Factory workers painted dials. " * 10))
    assert [(n, t) for n, t, _ in items] == [(10, "The Radium Girls’ Glow"), (9, "Tiny")]
    assert "Related" not in items[0][2] and "video" not in items[0][2] and items[0][2].count("\n\n") == 1

    class R:
        def __init__(self, text):
            self.status_code, self.text, self.headers = 200, text, {}

    pages = {
        "https://www.atlasobscura.com/feeds/latest": ATLAS.format(a="A strange village sank. " * 12, b="Good coffee here. " * 15),
        "https://listverse.com/feed/": RSS,
        "https://listverse.com/2026/10/13/10-medical-mysteries/": LISTVERSE.format(a="Factory workers painted dials. " * 10),
    }

    class S:
        def get(self, url, **kw):
            return R(pages[url])

    fetcher_sleep = fetcher.time.sleep
    fetcher.time.sleep = lambda s: None
    try:
        stories = fetcher.fetch_rss(session=S())
    finally:
        fetcher.time.sleep = fetcher_sleep
    assert [s["source_id"] for s in stories] == ["rss_atlasobscura_vanishing_village", "rss_listverse_10_medical_mysteries_10"]
    assert all(s["pillar"] == "outrage" and s["score"] == fetcher.RSS_SCORE for s in stories)
    assert stories[1]["source_name"] == "listverse/10 Terrifying Medical Mysteries"


def test_pillar_mix_picks_the_pillar_furthest_below_its_share(tmp_path):
    db = tmp_path / "bank.db"
    repository.init_db(db)
    for i, (pillar, score) in enumerate([("mystery", 90), ("mystery", 80), ("history", 50), ("outrage", 40)]):
        repository.save_story(f"reddit_{i}", "reddit/x", f"S{i}", "t", "", score, pillar=pillar, db_path=db)
    assert repository.get_next_story(db)["title"] == "S0"  # nothing made yet: mystery (40%, listed first)
    repository.set_status(1, "SCRIPT_READY", db_path=db)
    assert repository.next_pillar(db) == "history" and repository.get_next_story(db)["title"] == "S2"
    repository.set_status(3, "USED", db_path=db)
    # 1 mystery + 1 history made: of 3 videos outrage should have 0.6, the others 1.2 (0.2 short each)
    assert repository.get_next_story(db)["title"] == "S3"
    repository.set_status(4, "USED", db_path=db)
    assert repository.get_next_story(db)["title"] == "S1"  # history is due too, but has nothing NEW left
    mix = repository.pillar_mix(db)
    assert mix["mystery"] == {"target": 0.4, "produced": 1, "new": 1} and mix["outrage"] == {
        "target": 0.2, "produced": 1, "new": 0}
    with pytest.raises(ValueError):
        repository.save_story("x", "x", "x", "x", pillar="comedy", db_path=db)


def test_old_bank_is_migrated_and_rechecked(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE raw_stories (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT UNIQUE NOT NULL, "
                     "source_name TEXT, title TEXT, raw_text TEXT, url TEXT, score INTEGER, status TEXT DEFAULT 'NEW', "
                     "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        rows = [("reddit_a", "reddit/TrueCrimeDiscussion", "Case A", "The fire. " * 40),
                ("reddit_b", "reddit/UnresolvedMysteries", "Huge post", "x " * 2000),
                ("wikipedia_1", "wikipedia/Urban legends", "Bloody Mary", "\n\n".join(["p" * 1000] * 6)),
                ("reddit_c", "reddit/UnsolvedMysteries", "New podcast!", "promo " * 50),
                ("rss_listverse_x_1", "listverse/10 Bizarre Legends", "Flamel's Book", "He wrote a book. " * 20),
                ("wikipedia_2", "wikipedia/Urban legends", "Urban legend", "Stories people tell. " * 20)]
        conn.executemany("INSERT INTO raw_stories (source_id, source_name, title, raw_text) VALUES (?, ?, ?, ?)", rows)
    assert fetcher.recheck(db) == {"pillars": 6, "trimmed": 1, "rejected": 3}
    a, b, wiki, c, lv, topic = (repository.get_story(i, db) for i in range(1, 7))
    assert lv["status"] == "NEW" and lv["pillar"] == "outrage"  # its list article is on topic
    assert topic["status"] == "REJECTED" and "topic itself" in topic["note"]
    assert (a["pillar"], a["status"]) == ("outrage", "NEW")
    assert b["status"] == "REJECTED" and b["note"] == "fetch filter: too long (4,000 chars)"
    assert wiki["pillar"] == "history" and len(wiki["raw_text"]) <= 3500 and wiki["status"] == "NEW"
    assert c["status"] == "REJECTED" and "promo" in c["note"]
    assert fetcher.recheck(db) == {"pillars": 0, "trimmed": 0, "rejected": 0}


def test_gemini_pillar_split():
    assert fetcher.pillar_split(5) == {"mystery": 2, "history": 2, "outrage": 1}
    assert fetcher.pillar_split(3) == {"mystery": 1, "history": 1, "outrage": 1}
    assert sum(fetcher.pillar_split(7).values()) == 7


def test_wikipedia_text_and_score():
    text = "Intro.\n\n== History ==\nSomething.\n\n== See also ==\nOther\n\n== References ==\n1."
    assert fetcher.clean_extract(text) == "Intro.\n\nHistory\nSomething."
    assert [fetcher.wiki_score(v) for v in (0, 300, 3000, 30000, 10**7)] == [0, 50, 70, 90, 100]


def test_retries_on_429_with_retry_after():
    class R:
        def __init__(self, code, headers=None):
            self.status_code, self.headers = code, headers or {}

    replies = [R(429, {"Retry-After": "3"}), R(200)]
    waits = []

    class S:
        def get(self, url, **kw):
            return replies.pop(0)

    assert fetcher.get(S(), "https://x", sleep=waits.append).status_code == 200 and waits == [3.0]


def test_run_saves_each_source_and_survives_a_failing_one(tmp_path, monkeypatch):
    db = tmp_path / "bank.db"
    story = {"source_id": "gemini_x", "source_name": "gemini", "title": "X", "raw_text": "t", "url": "", "score": 70}
    monkeypatch.setattr(fetcher, "fetch_gemini", lambda count, avoid: [story, story])
    monkeypatch.setattr(fetcher, "fetch_reddit", lambda limit, skip: (_ for _ in ()).throw(RuntimeError("blocked")))
    report = fetcher.run(("reddit", "gemini"), db_path=db)
    assert report["reddit"] == {"error": "blocked"}
    assert report["gemini"] == {"fetched": 2, "saved": 1, "duplicates": 1}


def test_darkarchives_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from trendclip.web import app as web

    monkeypatch.setattr(repository, "DB_PATH", tmp_path / "bank.db")
    repository.init_db()
    repository.save_story("reddit_a", "reddit/UnresolvedMysteries", "Lake case", "the lake text", "https://r", 80)
    repository.save_story("gemini_b", "gemini", "Ship case", "the ship text", "", 70)
    client = TestClient(web.app)

    body = client.get("/api/darkarchives/stories").json()
    assert [s["source_id"] for s in body["stories"]] == ["reddit_a", "gemini_b"] and body["next_id"] == 1
    assert body["sources"] == {"reddit": {"NEW": 1}, "gemini": {"NEW": 1}} and body["counts"] == {"NEW": 2}
    assert [s["title"] for s in client.get("/api/darkarchives/stories", params={"source": "gemini"}).json()["stories"]] == ["Ship case"]
    assert [s["title"] for s in client.get("/api/darkarchives/stories", params={"q": "lake"}).json()["stories"]] == ["Lake case"]
    assert client.get("/api/darkarchives/stories/1").json()["raw_text"] == "the lake text"
    assert client.post("/api/darkarchives/stories/1/status", json={"status": "USED"}).json()["status"] == "USED"
    assert client.get("/api/darkarchives/stories").json()["next_id"] == 2
    assert client.post("/api/darkarchives/stories/1/status", json={"status": "DONE"}).status_code == 422
    assert client.get("/api/darkarchives/stories/99").status_code == 404

    monkeypatch.setattr(fetcher, "run", lambda sources, *a, progress, **k: progress(None, {"gemini": {"saved": 1}}) or {})
    assert client.post("/api/darkarchives/fetch", json={"sources": ["gemini"]}).status_code == 202
    import time
    for _ in range(50):
        if not client.get("/api/darkarchives/fetch").json()["running"]:
            break
        time.sleep(0.05)
    assert client.get("/api/darkarchives/fetch").json()["finished_at"]


def _draft(n_words=125, usable=True):
    from modules import script_gen as sg

    def say(n):  # sentences of at most 10 words
        out = []
        while n > 0:
            out.append(" ".join(["word"] * min(10, n)) + ".")
            n -= 10
        return " ".join(out)

    per = max(1, (n_words - 5) // 8)
    scenes = [sg.SceneDraft(narration=say(per), visual_keywords=["dark forest", "old car"],
                            image_prompt="Cinematic dark photo", overlay_text="no trace") for _ in range(8)]
    return sg.ScriptDraft(usable=usable, title="The Vanishing", hook_text=say(n_words - per * 8), scenes=scenes,
                          thumbnail_text="no  trace left")


def test_script_gen_writes_json_and_marks_script_ready(tmp_path, monkeypatch):
    import json

    from modules import script_gen as sg

    db = tmp_path / "bank.db"
    repository.init_db(db)
    repository.save_story("wikipedia_9", "wikipedia/Ghost ships", "Ghost ship", "raw " * 300, "u", 90, db_path=db)
    replies = [_draft(150), _draft(125)]  # too long first: sent back with the count
    prompts = []
    monkeypatch.setattr(sg, "_gemini", lambda client=None: (None, "m"))
    monkeypatch.setattr(sg, "_ask", lambda client, model, prompt: prompts.append(prompt) or replies.pop(0))

    result = sg.process(out_dir=tmp_path / "scripts", db_path=db)
    assert result["status"] == "SCRIPT_READY" and repository.get_story(1, db)["status"] == "SCRIPT_READY"
    assert "150 words" in prompts[1] and "Cut words" in prompts[1]
    data = json.loads((tmp_path / "scripts" / "story_1.json").read_text())
    assert list(data) == ["story_id", "title", "hook_text", "estimated_duration_seconds", "total_word_count", "scenes", "thumbnail_text"]
    assert data["story_id"] == 1 and data["total_word_count"] == 125 and data["estimated_duration_seconds"] == 45
    assert [s["scene_id"] for s in data["scenes"]] == list(range(1, 9))
    assert list(data["scenes"][0]) == ["scene_id", "narration", "visual_keywords", "image_prompt", "overlay_text"]
    assert data["scenes"][0]["overlay_text"] == "NO TRACE" and data["thumbnail_text"] == "NO TRACE LEFT"
    assert repository.get_next_story(db) is None  # no NEW stories left


def test_script_gen_rejects_unusable_and_restores_status_on_failure(tmp_path, monkeypatch):
    from modules import script_gen as sg

    db = tmp_path / "bank.db"
    repository.init_db(db)
    repository.save_story("reddit_x", "reddit/UnsolvedMysteries", "ANNOUNCEMENT: podcast", "promo " * 50, "u", 99, db_path=db)
    repository.save_story("reddit_y", "reddit/UnresolvedMysteries", "Case", "case " * 50, "u", 50, db_path=db)
    monkeypatch.setattr(sg, "_gemini", lambda client=None: (None, "m"))
    monkeypatch.setattr(sg, "_ask", lambda *a: sg.ScriptDraft(usable=False, reject_reason="a podcast promo"))
    assert sg.process(out_dir=tmp_path, db_path=db)["status"] == "REJECTED"
    assert repository.get_story(1, db)["status"] == "REJECTED"
    assert repository.get_story(1, db)["note"] == "a podcast promo"

    monkeypatch.setattr(sg, "_ask", lambda *a: _draft(60))  # always too short
    with pytest.raises(sg.ScriptGenError, match="3 times"):
        sg.process(out_dir=tmp_path, db_path=db)
    assert repository.get_story(2, db)["status"] == "NEW"  # back in the queue, not stuck in PROCESSING
    assert sg.words("In 1972, Flight 513's crew—gone. U.S. radar") == 8


def test_script_rules_long_sentences_and_prompt():
    import datetime as dt

    from modules import script_gen as sg

    draft = _draft(125)
    assert sg.check(draft) == []
    draft.scenes[2].narration = "One two three four five six seven eight nine ten eleven twelve thirteen. Short one."
    problems = sg.check(draft)
    assert problems == ['1 sentence(s) over 12 words: "One two three four five six seven eight nine ten eleven '
                        'twelve thirteen."']
    prompt = sg.build_prompt({"source_name": "reddit/ColdCases", "title": "T", "raw_text": "x", "pillar": "history"},
                             today=dt.date(2026, 10, 24))
    assert "today is 2026-10-24" in prompt and "Creepy history & obscure anomalies" in prompt
    assert "never more than 12" in prompt and "creepypasta" in prompt and "ongoing" in prompt and "thumbnail_text" in prompt
