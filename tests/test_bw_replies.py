import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from modules import bw_replies

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
ME = "UCbw"


class _Req:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeYouTube:
    def __init__(self, threads, answered=()):
        self.threads, self.answered, self.posted = threads, set(answered), []

    def channels(self):
        return SimpleNamespace(list=lambda **k: _Req({"items": [{"id": ME}]}))

    def commentThreads(self):
        return SimpleNamespace(list=lambda **k: _Req({"items": self.threads}))

    def comments(self):
        def list_(parentId, **k):
            mine = [{"snippet": {"authorChannelId": {"value": ME}}}] if parentId in self.answered else []
            return _Req({"items": mine})

        def insert(part, body):
            self.posted.append(body["snippet"])
            return _Req({"id": "r" + body["snippet"]["parentId"]})

        return SimpleNamespace(list=list_, insert=insert)


def thread(cid, video, author, text, hours_ago=1):
    when = (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"snippet": {"videoId": video, "topLevelComment": {"id": cid, "snippet": {
        "authorChannelId": {"value": author}, "authorDisplayName": author, "textOriginal": text,
        "publishedAt": when, "likeCount": 0}}}}


def made(tmp_path, key, vid, question):
    folder = tmp_path / key
    folder.mkdir()
    (folder / "info.json").write_text(json.dumps({"title": "Which Country Has the Most Neighbors?", "comment": question,
                                                  "description": "China and Russia have 14 each.\n\nSources:\n- x",
                                                  "uploads": {"youtube": {"video_id": vid}}}))


def fake_check(verdicts):
    def check(data, videos, settings, client=None):
        assert videos["v1"]["question"].startswith("How many") and "Sources" not in videos["v1"]["about"]
        for cid, (verdict, text) in verdicts.items():
            if cid in data["comments"]:
                data["verdicts"][cid], data["texts"][cid] = verdict, text
        return len(verdicts)
    return check


def test_each_viewer_gets_one_reply_per_video_and_spam_is_flagged(tmp_path, monkeypatch):
    made(tmp_path, "2026-11-12_1430", "v1", "How many neighbors does YOUR country have? 👇")
    settings = SimpleNamespace(youtube_reply_daily_max=100)
    yt = FakeYouTube([
        thread("c1", "v1", "ann", "12 🇿🇦"),
        thread("c2", "v1", "ann", "also I love the panda"),  # her second comment on the same video: no second reply
        thread("c3", "v1", "bob", "sub to my channel!!"),
        thread("c4", "v1", "cat", "see www.spam.com"),
        thread("c5", "v1", "dan", "two", hours_ago=30),  # Gemini never checked it: the plain thank-you
        thread("c6", "v1", "eve", "17 km", hours_ago=2),  # not checked yet, too new for the fallback: waits
        thread("c0", "v1", ME, "How many neighbors does YOUR country have? 👇"),  # our own question
        thread("c7", "v1", "UCsql", "4 😅"),  # the owner's other channel
        thread("c8", "v1", "@Owner", "3"),  # the owner's personal account, named in BW_REPLY_SKIP
    ], answered={"c5"})
    monkeypatch.setenv("BW_REPLY_SKIP", " @owner , UCnobody")
    monkeypatch.setattr(bw_replies, "own_channel_ids", lambda: {"UCsql"})
    monkeypatch.setattr(bw_replies, "check", fake_check({"c1": ("reply", "12 neighbors? South Africa keeps busy! 🇿🇦"),
                                                         "c2": ("reply", "Thanks!"), "c3": ("skip", ""),
                                                         "c7": ("reply", "x"), "c8": ("reply", "x")}))

    got = bw_replies.refresh(settings, youtube=yt, now=NOW, out_dir=tmp_path)

    assert got["new"] == 6
    assert [q["comment_id"] for q in got["queued"]] == ["c5", "c1"]
    assert got["queued"][1]["text"] == "12 neighbors? South Africa keeps busy! 🇿🇦"
    assert got["queued"][0]["text"] in bw_replies.FALLBACK
    assert sorted(c["id"] for c in got["flagged"]) == ["c3", "c4"]

    # dan was already answered by hand: skipped, nothing posted; ann's reply goes up
    assert bw_replies.send_next(settings, date(2026, 11, 14), youtube=yt, out_dir=tmp_path) is None
    sent = bw_replies.send_next(settings, date(2026, 11, 14), youtube=yt, out_dir=tmp_path)
    assert sent["comment_id"] == "c1" and yt.posted == [{"parentId": "c1", "textOriginal": sent["text"]}]
    assert bw_replies.send_next(settings, date(2026, 11, 14), youtube=yt, out_dir=tmp_path) is None

    # the next read queues nothing again and flags nobody twice
    again = bw_replies.refresh(settings, youtube=yt, now=NOW, out_dir=tmp_path)
    assert again["new"] == 0 and again["queued"] == [] and again["flagged"] == []
    assert not bw_replies.due(3, tmp_path, now=NOW) and bw_replies.due(3, tmp_path, now=NOW + timedelta(hours=3))


def test_the_fallback_keeps_their_flag():
    assert bw_replies.fallback_text({"id": "x", "text": "12 🇿🇦 ❤"}).startswith("🇿🇦 ")
    assert bw_replies.fallback_text({"id": "x", "text": "nice"}) in bw_replies.FALLBACK
