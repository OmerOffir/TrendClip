from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trendclip import ig_replies, instagram, script_writer, shorts
from trendclip.config import Settings

NOW = datetime.now(timezone.utc).replace(microsecond=0)
DAY = NOW.date()


def _ig(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S+0000")


class FakeGraph:
    """The Instagram calls the replies use: Reels, their comments, replies (read and post)."""

    def __init__(self, quiz_age_hours=30):
        self.posted, self.answered, self.fail = [], {}, None
        self.reels = [
            {"id": "m1", "caption": "Only 3% get this\n#quiz", "timestamp": _ig(NOW - timedelta(hours=quiz_age_hours)),
             "permalink": "https://instagram.com/reel/m1", "comments_count": 4},
            {"id": "m2", "caption": "A story", "timestamp": _ig(NOW - timedelta(hours=5)),
             "permalink": "https://instagram.com/reel/m2", "comments_count": 1},
            {"id": "old", "caption": "Old", "timestamp": _ig(NOW - timedelta(days=40)), "comments_count": 9},
        ]
        self.comments = {
            "m1": [
                {"id": "c1", "text": "51", "timestamp": _ig(NOW - timedelta(hours=20)), "username": "amy",
                 "from": {"id": "1", "username": "amy"}},
                {"id": "c2", "text": "63!", "timestamp": _ig(NOW - timedelta(hours=19)), "username": "amy",
                 "from": {"id": "1", "username": "amy"}},
                {"id": "c3", "text": "follow me www.spam.com", "timestamp": _ig(NOW - timedelta(hours=18)),
                 "username": "spam", "from": {"id": "2", "username": "spam"}},
                {"id": "c4", "text": "Comment your answer 👇", "timestamp": _ig(NOW - timedelta(hours=29)),
                 "username": "SideQuestLogic", "from": {"id": "9", "username": "sidequestlogic"}},
            ],
            "m2": [{"id": "c5", "text": "love it", "timestamp": _ig(NOW - timedelta(hours=4)), "username": "bob",
                    "from": {"id": "3", "username": "bob"}}],
        }

    def __call__(self, method, url, access_token, **kw):
        path = url.split(".com/", 1)[1].split("/", 1)[1]
        if self.fail and method == "POST":
            raise self.fail
        if path == "me-id/media":
            return {"data": self.reels}
        if path.endswith("/comments"):
            return {"data": self.comments.get(path.split("/")[0], [])}
        if path.endswith("/replies") and method == "GET":
            return {"data": [{"username": u} for u in self.answered.get(path.split("/")[0], [])]}
        if path.endswith("/replies") and method == "POST":
            self.posted.append((path.split("/")[0], kw["data"]["message"]))
            return {"id": "r"}
        raise AssertionError(path)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(instagram, "token", lambda s: "tok")
    monkeypatch.setattr(instagram, "account", lambda s: {"user_id": "me-id", "username": "sidequestlogic"})
    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s: "")  # no Gemini: the word rules decide
    s = Settings(youtube_api_key="k", output_dir=tmp_path / "output", assets_dir=tmp_path / "assets",
                 instagram_access_token="tok")
    s.shorts_dir.mkdir(parents=True, exist_ok=True)
    short = shorts.ShortVideo(filename="q.mp4", game="g", title="Only 3% get this", description="", hashtags=[],
                              script="", voice="", background="", answer="63",
                              uploads={"instagram": {"media_id": "m1"}})
    (s.shorts_dir / "q.json").write_text(short.model_dump_json())
    (s.shorts_dir / "q.mp4").write_bytes(b"\x00" * 64)
    return s


def test_reads_recent_reels_and_ties_quizzes_to_their_shorts(settings, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(instagram, "_call", graph)
    board = ig_replies.refresh(settings)
    assert set(board.videos) == {"m1", "m2"}  # the 40-day-old Reel is left out
    assert board.videos["m1"].kind == "quiz" and board.videos["m1"].answer == "63"
    assert board.videos["m1"].published.endswith("Z")
    assert {c.id for c in board.comments} == {"c1", "c2", "c3", "c5"}  # not the account's own comment
    assert {c.id: c.result for c in board.comments}["c2"] == "first"
    assert ig_replies.load(settings).comments[0].id == board.comments[0].id
    assert ig_replies.links(settings)["m1"] == "https://instagram.com/reel/m1"
    assert [c.id for c in ig_replies.new_skips(settings, board)] == ["c3"]


def test_one_reply_per_fan_per_reel_no_points_and_some_invite_to_youtube(settings, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(instagram, "_call", graph)
    board = ig_replies.refresh(settings)
    queued = ig_replies.plan_replies(settings, board, DAY, NOW)
    assert {q["comment_id"]: q["result"] for q in queued} == {"c2": "first", "c5": "comment"}  # not amy's guess
    assert all("pts" not in t or "On YouTube" in t for t in (q["text"] for q in queued))
    assert all("http" not in q["text"] and "@" not in q["text"] for q in queued)
    assert ig_replies.plan_replies(settings, board, DAY, NOW) == []
    assert [ig_replies.send_next_reply(settings, DAY, NOW)["comment_id"] for _ in range(2)] == ["c2", "c5"]
    assert [c for c, _ in graph.posted] == ["c2", "c5"]
    o = ig_replies.reply_overview(settings, board, DAY, NOW)
    assert (o["replied"], o["queued"], o["skipped"], o["sent_today"]) == (2, 0, 1, 2)


def test_every_reply_invites_to_youtube(settings):
    texts = [ig_replies.reply_text(settings, kind, f"id{i}") for i in range(100)
             for kind in ("first", "correct", "wrong", "voted", "comment", "unknown")]
    assert all("SideQuest Logic" in t and "YouTube" in t for t in texts)
    assert all("http" not in t and "@" not in t for t in texts)
    assert len(set(texts)) >= 12  # several wordings, not one reply over and over
    assert all("{name}" not in t for t in texts)
    custom = settings.model_copy(update={"instagram_youtube_name": "Brain Traps"})
    assert any("Brain Traps on YouTube" in ig_replies.reply_text(custom, "comment", f"id{i}") for i in range(20))


def test_quiz_reels_wait_24h(settings, monkeypatch):
    monkeypatch.setattr(instagram, "_call", FakeGraph(quiz_age_hours=10))
    board = ig_replies.refresh(settings)
    assert [q["comment_id"] for q in ig_replies.plan_replies(settings, board, DAY, NOW)] == ["c5"]
    assert ig_replies.reply_overview(settings, board, DAY, NOW)["quiz_wait"] == 1


def test_skips_comments_answered_by_hand(settings, monkeypatch):
    graph = FakeGraph()
    graph.answered = {"c2": ["SideQuestLogic"]}
    monkeypatch.setattr(instagram, "_call", graph)
    board = ig_replies.refresh(settings)
    ig_replies.plan_replies(settings, board, DAY, NOW)
    assert ig_replies.send_next_reply(settings, DAY, NOW) is None
    assert ig_replies.send_next_reply(settings, DAY, NOW)["comment_id"] == "c5"
    assert [c for c, _ in graph.posted] == ["c5"]
    assert ig_replies.plan_replies(settings, board, DAY, NOW) == []  # amy was answered on that Reel


def test_rate_limit_pauses_and_keeps_the_reply(settings, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(instagram, "_call", graph)
    board = ig_replies.refresh(settings)
    ig_replies.plan_replies(settings, board, DAY, NOW)
    graph.fail = instagram.InstagramError("Instagram: too many calls", status=400, code=4)
    assert ig_replies.send_next_reply(settings, DAY, NOW) is None
    o = ig_replies.reply_overview(settings, board, DAY, NOW)
    assert o["queued"] == 2 and o["paused_until"]
    graph.fail = None
    assert ig_replies.send_next_reply(settings, DAY, NOW + timedelta(minutes=5)) is None  # still paused
    later = NOW + timedelta(minutes=ig_replies.LIMIT_PAUSE_MINUTES + 1)
    assert ig_replies.send_next_reply(settings, DAY, later)["comment_id"] == "c2"


def test_a_deleted_comment_gives_the_fans_other_comment_the_reply(settings, monkeypatch):
    graph = FakeGraph()
    monkeypatch.setattr(instagram, "_call", graph)
    board = ig_replies.refresh(settings)
    ig_replies.plan_replies(settings, board, DAY, NOW)
    graph.fail = instagram.InstagramError("Instagram: unknown comment", status=400, code=100)
    ig_replies.send_next_reply(settings, DAY, NOW)  # c2 is gone: no retry
    graph.fail = None
    again = ig_replies.plan_replies(settings, board, DAY, NOW)
    assert [q["comment_id"] for q in again] == ["c1"]
    assert ig_replies.reply_overview(settings, board, DAY, NOW)["failed"] == 1


def test_daily_max(settings, monkeypatch):
    monkeypatch.setattr(instagram, "_call", FakeGraph())
    board = ig_replies.refresh(settings)
    capped = settings.model_copy(update={"instagram_reply_daily_max": 1})
    assert len(ig_replies.plan_replies(capped, board, DAY, NOW)) == 1
    assert ig_replies.send_next_reply(capped, DAY, NOW) is not None
    assert ig_replies.plan_replies(capped, board, DAY, NOW) == []  # 1 sent today
