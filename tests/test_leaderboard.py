from __future__ import annotations

import pytest

from trendclip import leaderboard as lb
from trendclip import shorts
from trendclip.config import Settings
from trendclip.leaderboard import Comment, Video


def test_judge_riddle_and_math():
    riddle = Video("v", "t", kind="quiz", answer="A zipper")
    assert lb.judge(riddle, "It's a ZIPPER!!") == "correct"
    assert lb.judge(riddle, "zippers lol") == "correct"
    assert lb.judge(riddle, "a coat") == "comment"
    math = Video("v", "t", kind="quiz", answer="63")
    assert lb.judge(math, "I got 63") == "correct"
    assert lb.judge(math, "sixty three") == "correct"
    assert lb.judge(math, "51?") == "wrong"
    assert lb.judge(math, "great video") == "comment"


def test_judge_multiple_choice():
    v = Video("v", "t", kind="quiz", answer="Alaska",
              choices=[("A", "Florida"), ("B", "Alaska"), ("C", "California")])
    assert lb.judge(v, "B") == "correct"
    assert lb.judge(v, "b)") == "correct"
    assert lb.judge(v, "Easy, Alaska") == "correct"
    assert lb.judge(v, "it's C") == "wrong"
    assert lb.judge(v, "A or B") == "wrong"  # guessing two options doesn't count
    assert lb.judge(v, "this is a cool video") == "comment"


def test_judge_paradox_vote():
    v = Video("v", "t", kind="paradox", choices=[("A", "SHAVE"), ("B", "PASS")])
    assert lb.judge(v, "Team shave, obviously") == "voted"
    assert lb.judge(v, "shave or pass, I can't decide") == "comment"
    assert lb.judge(v, "my brain hurts") == "comment"


def _c(cid, video, author, text, t):
    return Comment(cid, video, author.title(), author, text, f"2026-10-1{t}T10:00:00Z")


def test_score_first_correct_best_comment_and_subscriber():
    videos = {"q": Video("q", "Quiz", kind="quiz", answer="63"), "s": Video("s", "Story")}
    comments = [
        _c("1", "q", "bob", "51", 1),
        _c("2", "q", "amy", "63", 2),  # first correct: 10
        _c("3", "q", "bob", "63!", 3),  # correct: 2, his "51" then gets 0
        _c("4", "q", "bob", "63 63 63", 4),  # still 0: one best comment per video
        _c("5", "s", "bob", "nice", 5),  # 1
    ]
    people = lb.score(videos, comments, subscribers={"bob"})
    by = {p["id"]: p for p in people}
    assert by["amy"]["points"] == 10 and by["amy"]["first"] == 1
    assert by["bob"]["points"] == 2 + 1 + 10 and by["bob"]["subscriber"]
    assert [c.points for c in comments] == [0, 10, 2, 0, 1]
    assert comments[1].result == "first"
    assert people[0]["id"] == "bob"


def test_safe_text_cannot_ping_or_format():
    assert "@\u200beveryone" in lb.safe("hi @everyone")
    assert lb.safe("**bold** `x`") == r"\*\*bold\*\* \`x\`"
    assert len(lb.safe("x" * 500)) == 160


class _Req:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeYouTube:
    def __init__(self, comments, subs=()):
        self.comment_items, self.subs = comments, subs

    def channels(self):
        return self

    def playlistItems(self):
        return self

    def commentThreads(self):
        return self

    def subscriptions(self):
        return self

    def list(self, **kw):
        if kw.get("mine"):
            return _Req({"items": [{"id": "UCme", "contentDetails": {"relatedPlaylists": {"uploads": "UUme"}}}]})
        if "playlistId" in kw:
            return _Req({"items": [
                {"snippet": {"title": "Quiz #shorts"}, "contentDetails": {"videoId": "vq", "videoPublishedAt": "2026-10-11T10:00:00Z"}},
                {"snippet": {"title": "Elsewhere"}, "contentDetails": {"videoId": "vx", "videoPublishedAt": "2026-10-12T10:00:00Z"}},
            ]})
        if "allThreadsRelatedToChannelId" in kw:
            return _Req({"items": self.comment_items})
        return _Req({"items": [{"subscriberSnippet": {"channelId": s}} for s in self.subs]})


def _thread(cid, video, author, text, when):
    return {"snippet": {"videoId": video, "topLevelComment": {"id": cid, "snippet": {
        "authorDisplayName": author, "authorChannelId": {"value": author}, "textOriginal": text,
        "publishedAt": when, "likeCount": 1}}}}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    local = shorts.ShortVideo(filename="q.mp4", game="gta", title="Quiz", description="", hashtags=[], script="",
                              voice="", background="", answer="63")
    monkeypatch.setattr(shorts, "list_shorts", lambda s: [local])
    return Settings(youtube_api_key="k", output_dir=tmp_path / "output")


def test_refresh_stores_scores_and_reports_new(settings):
    threads = [
        _thread("c1", "vq", "amy", "63", "2026-10-11T11:00:00Z"),
        _thread("c2", "vq", "UCme", "Answer below 👇", "2026-10-11T10:30:00Z"),  # the channel itself
        _thread("c3", "vx", "bob", "C", "2026-10-12T11:00:00Z"),
    ]
    board, new = lb.refresh(settings, FakeYouTube(threads, subs=["bob"]))
    assert new == []  # the first read is the starting point, not "new"
    assert [(p["name"], p["points"]) for p in board.people] == [("bob", 11), ("amy", 10)]  # amy: by title

    lb.set_answer(settings, "vx", "Paris", "A) Rome | C) Paris")
    board = lb.load(settings)
    assert board.videos["vx"].choices == [("A", "Rome"), ("C", "Paris")]
    assert board.people[0]["name"] == "bob" and board.people[0]["points"] == 20

    threads.append(_thread("c4", "vq", "cat", "sixty three", "2026-10-13T09:00:00Z"))
    board, new = lb.refresh(settings, FakeYouTube(threads, subs=["bob"]))
    assert [c.id for c in new] == ["c4"] and new[0].points == 2
    assert board.videos["vx"].answer == "Paris"  # /answer survives a refresh
    assert any("sixty three" in line for line in lb.new_lines(board, new))
    assert lb.video_lines(board, "vq")[0].startswith("🎬 **Quiz** · 2 comments")
    assert "20 pts" in lb.person_lines(board, "bob")[0]


def test_set_answer_unknown_video(settings):
    with pytest.raises(lb.LeaderboardError):
        lb.set_answer(settings, "nope", "x")
