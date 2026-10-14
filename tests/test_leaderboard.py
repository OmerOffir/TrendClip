from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

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
    from trendclip import script_writer

    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s: None)  # never the real Gemini
    from trendclip import script_writer

    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s: None)  # never the real Gemini
    from trendclip import script_writer

    monkeypatch.setattr(script_writer, "gemini_api_key", lambda s: None)  # never the real Gemini
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


def test_card_renders_podium_and_list():
    import io

    from PIL import Image

    from trendclip import leaderboard_card

    people = [{"name": f"@fan{i}", "points": 20 - i} for i in range(15)] + [{"name": "@רונישקד-פ9צ", "points": 1}]
    img = Image.open(io.BytesIO(leaderboard_card.render(people)))
    assert img.size == (leaderboard_card.W, leaderboard_card.H)
    assert Image.open(io.BytesIO(leaderboard_card.render([]))).size == img.size  # empty: "YOU?" spots
    assert leaderboard_card._pts(1) == "1 PT" and leaderboard_card._pts(3) == "3 PTS"


def test_post_caption_tags_everyone_on_the_card():
    people = [{"name": "@kerrie", "points": 10}, {"name": "Calvin C", "points": 3}] + [
        {"name": f"@fan{i}", "points": 1} for i in range(20)]
    text = lb.post_caption(people, 13)
    assert text.splitlines()[:3] == [
        "🏆 DAILY LEADERBOARD UPDATE! 🏆", "Current Kings of Logic: @kerrie with 10 pts!",
        "Next trap drops in 1 hour! Who's taking the #1 spot today? 🧠🔥"]
    tags = text.splitlines()[-1].split()
    assert tags[:2] == ["@kerrie", "@CalvinC"] and len(tags) == 13
    assert lb.post_caption([]) == ""


def test_leaderboard_reminder_at_its_time():
    from datetime import datetime

    from trendclip import assistant

    s = Settings(youtube_api_key="k", discord_leaderboard_time="13:30", discord_reminder_minutes=0)
    at = datetime(2026, 10, 14, 13, 31, tzinfo=s.tz)
    assert [r.kind for r in assistant.due_reminders(s, at, set())] == ["morning", "leaderboard"]
    assert [r.kind for r in assistant.due_reminders(s, at, {"2026-10-14 morning"})] == ["leaderboard"]
    off = s.model_copy(update={"discord_leaderboard_time": ""})
    assert [r.kind for r in assistant.due_reminders(off, at, {"2026-10-14 morning"})] == []


class FakeReplies:
    def __init__(self, existing=()):
        self.existing, self.inserted = dict(existing), []

    def comments(self):
        return self

    def list(self, part, parentId, maxResults):  # noqa: N803
        return _Req({"items": [{"snippet": {"authorChannelId": {"value": a}}} for a in self.existing.get(parentId, [])]})

    def insert(self, part, body):
        self.inserted.append(body["snippet"])
        return _Req({"id": "r"})


NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
DAY = NOW.date()


def _quiz_board(video_published: str) -> lb.Board:
    videos = {"q": Video("q", "Quiz", published=video_published, kind="quiz", answer="63"),
              "s": Video("s", "Story", published=video_published)}
    comments = [
        Comment("a1", "q", "amy", "amy", "51", "2026-10-13T08:00:00Z"),
        Comment("a2", "q", "amy", "amy", "63!", "2026-10-13T08:05:00Z"),  # amy's scoring comment
        Comment("b1", "q", "bob", "bob", "63", "2026-10-13T07:00:00Z"),  # first correct
        Comment("c1", "q", "cat", "cat", "42", "2026-10-13T09:00:00Z"),  # wrong
        Comment("d1", "q", "dan", "dan", "7", "2026-10-13T09:30:00Z"),  # wrong by the rules, Gemini unsure
        Comment("e1", "s", "eve", "eve", "love it", "2026-10-13T10:00:00Z"),
    ]
    verdicts = {"a1": "wrong", "a2": "correct", "b1": "correct", "c1": "wrong", "d1": "comment", "e1": "comment"}
    board = lb.Board(videos=videos, comments=comments, channel_id="UCme", verdicts=verdicts)
    board.people = lb.score(videos, comments, set(), board.verdicts)
    return board


def test_ranks_share_ties():
    assert lb.ranks([{"id": "a", "points": 5}, {"id": "b", "points": 1}, {"id": "c", "points": 1}]) == \
        {"a": 1, "b": 2, "c": 2}


def test_one_reply_per_person_per_video_with_its_result(settings):
    board = _quiz_board("2026-10-13T06:00:00Z")  # 30 h old: answers may be revealed
    queued = lb.plan_replies(settings, board, DAY, NOW)
    got = {q["comment_id"]: q["result"] for q in queued}
    assert got == {"b1": "first", "a2": "correct", "c1": "wrong", "d1": "comment", "e1": "comment"}
    assert "a1" not in got  # amy's earlier wrong guess gets nothing: one reply per video
    assert [q["result"] for q in queued][:2] == ["first", "correct"]  # the best news goes out first
    texts = {q["comment_id"]: q["text"] for q in queued}
    assert "+10" in texts["b1"] and "+2" in texts["a2"] and "+1" in texts["c1"]
    assert texts["d1"] in [t.format(rank=3, total="1 pt") for t in lb.REPLY_TEXTS["comment"]]
    assert all("http" not in t for t in texts.values())
    assert lb.plan_replies(settings, board, DAY, NOW) == []  # never queued twice


def test_quiz_replies_wait_24h_but_story_replies_dont(settings):
    board = _quiz_board("2026-10-14T00:00:00Z")  # 12 h old
    queued = lb.plan_replies(settings, board, DAY, NOW)
    assert [q["comment_id"] for q in queued] == ["e1"]
    later = lb.plan_replies(settings, board, DAY, NOW + timedelta(hours=13))
    assert {q["comment_id"] for q in later} == {"b1", "a2", "c1", "d1"}


def test_send_skips_comments_the_channel_answered(settings):
    board = _quiz_board("2026-10-13T06:00:00Z")
    lb.plan_replies(settings, board, DAY, NOW)
    yt = FakeReplies(existing={"b1": ["UCme"]})  # answered by hand already
    sent = [lb.send_next_reply(settings, "UCme", DAY, yt) for _ in range(5)]
    assert sent[0] is None and [s["comment_id"] for s in sent[1:]] == ["a2", "c1", "d1", "e1"]
    assert [i["parentId"] for i in yt.inserted] == ["a2", "c1", "d1", "e1"]
    assert lb.send_next_reply(settings, "UCme", DAY, yt) is None


def test_daily_max(settings, monkeypatch):
    monkeypatch.setattr(lb, "REPLY_DAILY_MAX", 2)
    assert len(lb.plan_replies(settings, _quiz_board("2026-10-13T06:00:00Z"), DAY, NOW)) == 2


class FakeGemini:
    class models:  # noqa: N801
        @staticmethod
        def generate_content(model, contents, config):
            import re as _re

            ids = _re.findall(r"- id (\S+):", contents[0])
            verdict = {"b1": "correct", "c1": "wrong", "d1": "not_an_answer"}

            class R:
                parsed = lb._Verdicts(items=[lb._Verdict(id=i, verdict=verdict.get(i, "wrong")) for i in ids])
                text = ""
            return R()


def test_gemini_verdicts_win_and_are_kept(settings):
    board = _quiz_board("2026-10-13T06:00:00Z")
    board.verdicts = {}
    board.comments.append(Comment("t1", "q", "tom", "tom", "sixty-tree", "2026-10-13T11:00:00Z"))
    assert lb.verify(settings, board, client=FakeGemini()) == 7
    assert board.verdicts["d1"] == "comment" and board.verdicts["c1"] == "wrong"
    assert board.verdicts["e1"] == "comment"  # a story video has no right / wrong
    assert lb.verify(settings, board, client=FakeGemini()) == 0  # each comment once


def test_links_and_flagged_comments_get_no_reply_and_no_points(settings):
    board = _quiz_board("2026-10-13T06:00:00Z")
    board.comments += [
        Comment("x1", "s", "spammer", "spammer", "sub to me 👉 www.mychannel.com", "2026-10-13T10:00:00Z"),
        Comment("x2", "s", "troll", "troll", "this channel is trash", "2026-10-13T10:00:00Z"),
        Comment("x3", "s", "new", "new", "cool!", "2026-10-13T10:00:00Z"),  # not checked by Gemini yet
    ]
    board.verdicts["x2"] = "skip"
    board.people = lb.score(board.videos, board.comments, set(), board.verdicts)
    assert {"spammer", "troll"}.isdisjoint(p["id"] for p in board.people)
    queued = {q["comment_id"] for q in lb.plan_replies(settings, board, DAY, NOW)}
    assert {"x1", "x2", "x3"}.isdisjoint(queued) and "e1" in queued
    assert [c.id for c in lb.new_skips(settings, board)] == ["x1", "x2"]
    assert lb.new_skips(settings, board) == []  # told once


def test_same_titles_never_share_an_answer(settings, monkeypatch):
    def short(name, title, answer, video_id=None):
        uploads = {"youtube": {"video_id": video_id}} if video_id else {}
        return shorts.ShortVideo(filename=name, game="g", title=title, description="", hashtags=[], script="",
                                 voice="", background="", answer=answer, uploads=uploads)

    monkeypatch.setattr(shorts, "list_shorts", lambda s: [
        short("a.mp4", "Only 1% Solve This Riddle", "A zipper", video_id="v1"),
        short("b.mp4", "Only 1% Solve This Riddle", "An echo"),  # same title, not uploaded from here
        short("c.mp4", "Twin title", "A coin"), short("d.mp4", "Twin title", "A needle"),
        short("e.mp4", "One of a kind", "63"),
    ])
    videos = [Video("v1", "Only 1% Solve This Riddle #shorts"), Video("v2", "Only 1% Solve This Riddle"),
              Video("v3", "Twin title"), Video("v4", "One of a kind #shorts")]
    lb._apply_answers(settings, videos, {})
    assert [v.answer for v in videos] == ["A zipper", "", "", "63"]
    # v1 by its id; v2 shares its title with v1 on the channel; v3's title fits two Shorts; v4 is unique


def test_set_answer_unknown_video(settings):
    with pytest.raises(lb.LeaderboardError):
        lb.set_answer(settings, "nope", "x")
