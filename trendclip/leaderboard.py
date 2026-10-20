"""Every YouTube comment of the channel, scored into a fan leaderboard that the Discord bot shows.

Stored in output/comments.json on the machine that runs the bot; refreshed each morning and by
/leaderboard refresh. Points per person and video (only their best comment on a video counts, so
guessing every option or spamming doesn't pay): the first correct quiz answer 10, any other correct
answer or a paradox vote 3, any other comment 1. Skipping 3 videos in a row halves a fan's points.
Being subscribed earns no points but is needed to win a prize (YouTube only shows subscribers who made
their subscriptions public). The channel's own comments are skipped. The board is
monthly: only comments posted since the 1st (channel timezone) count, so everyone starts from 0 each month;
older comments stay stored for last month's final standings.

Quiz answers come from the Shorts on this machine (matched by YouTube id, else by title); /answer sets
them for videos made elsewhere. Word rules judge each answer; Gemini double-checks quiz answers (typos,
synonyms, other languages) when GEMINI_API_KEY is set. Every fan then gets one reply per video.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from . import publish, shorts
from .config import Settings
from .models import utcnow

logger = logging.getLogger(__name__)

FIRST_POINTS, CORRECT_POINTS, COMMENT_POINTS = 10, 3, 1
# The 3-video rule: a fan who doesn't comment on 3 videos in a row loses half their points. A video
# counts as missed once it is MISS_GRACE old without their comment (a late comment still saves it).
MISS_LIMIT, MISS_GRACE = 3, timedelta(hours=24)
PRIZES = ("🎁 1st place: a digital gift card (Steam / Amazon / Google Play) + a pinned comment!",
          "🏅 2nd & 3rd place: a Hall of Fame shoutout on YouTube & Instagram!",
          "📩 To claim: DM @sidequestlogic or email sidequestlogic@gmail.com (you must be subscribed to win).")
MAX_COMMENT_PAGES = 200  # 100 threads a page, 1 quota unit each
RESULT_POINTS = {"first": FIRST_POINTS, "correct": CORRECT_POINTS, "voted": CORRECT_POINTS,
                 "wrong": COMMENT_POINTS, "comment": COMMENT_POINTS, "skip": 0}
RESULT_ICON = {"first": "🥇", "correct": "✅", "voted": "🗳️", "wrong": "❌", "comment": "💬", "skip": "🚫"}
# Links in a comment: self-promotion / spam. Never replied to, never scored.
LINK = re.compile(r"https?://|www\.|\bt\.me/|\b[\w-]+\.(?:com|net|org|io|ly|gg|me|tv|xyz|co|link|shop|ru|info)\b",
                  re.IGNORECASE)
NO_ANSWER = "No single answer"  # paradox Shorts store this as their answer


class LeaderboardError(RuntimeError):
    pass


@dataclass
class Video:
    id: str
    title: str
    published: str = ""
    kind: str = "story"  # story / quiz / paradox
    answer: str = ""
    choices: list[tuple[str, str]] = field(default_factory=list)  # [("A", "Florida"), ...]

    @property
    def url(self) -> str:
        return f"https://youtube.com/shorts/{self.id}"

    @property
    def name(self) -> str:
        return re.sub(r"\s*#\w+", "", self.title).strip() or self.id


@dataclass
class Comment:
    id: str
    video_id: str
    author: str
    author_id: str
    text: str
    published: str
    likes: int = 0
    result: str = "comment"
    points: int = 0

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}&lc={self.id}"


@dataclass
class Board:
    videos: dict[str, Video] = field(default_factory=dict)
    comments: list[Comment] = field(default_factory=list)
    subscribers: set[str] = field(default_factory=set)
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)  # set with /answer
    verdicts: dict[str, str] = field(default_factory=dict)  # comment id -> Gemini's correct / wrong / comment
    updated_at: str = ""
    channel_id: str = ""
    people: list[dict[str, Any]] = field(default_factory=list)  # this month's, best first
    since: str = ""  # the month's start ("…Z"): older comments are kept but don't count


# --------------------------------------------------------------------------- judging answers

_NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i > 1}
_SKIP = {"a", "an", "the"}


def _words(text: str) -> list[str]:
    """Lowercase words, number words as digits ("sixty three" → "63"), plurals folded ("zippers" → "zipper")."""
    raw = re.findall(r"[a-z0-9]+", (text or "").lower().replace("'", ""))
    out: list[str] = []
    for word in raw:
        if word in _NUMBERS and out and out[-1].isdigit() and out[-1].endswith("0") and int(out[-1]) >= 20:
            out[-1] = str(int(out[-1]) + _NUMBERS[word])
        elif word in _NUMBERS or word in _TENS:
            out.append(str(_NUMBERS.get(word, _TENS.get(word))))
        else:
            out.append(word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word)
    return out


def _mentions(comment: list[str], phrase: str) -> bool:
    want = [w for w in _words(phrase) if w not in _SKIP]
    return bool(want) and all(w in comment for w in want)


def _letters(text: str, letters: set[str]) -> set[str]:
    """Option letters the comment picks: "B", "b)", "option b", or the whole comment being one letter.
    A lowercase "a" alone in a sentence is an article, not a pick."""
    found = set()
    bare = re.sub(r"[^A-Za-z]", "", text)
    if len(bare) == 1 and bare.upper() in letters:
        found.add(bare.upper())
    for m in re.finditer(r"\b([A-Za-z])\)|\b(?:option|answer|pick|choose)\s*\(?([A-Za-z])\b", text, re.IGNORECASE):
        letter = (m.group(1) or m.group(2)).upper()
        if letter in letters:
            found.add(letter)
    # "B!", "it's C", "A or B" but not "A coin"
    for m in re.finditer(r"\b([A-Z])\b(?!\s+(?!(?:or|and|is|was|for|because)\b)[a-z])", text):
        if m.group(1) in letters:
            found.add(m.group(1))
    return found


def judge(video: Video, text: str) -> str:
    """correct / voted (a paradox side) / wrong (answered something else) / comment."""
    words = _words(text)
    picks = (_letters(text, {l for l, _ in video.choices}) | {l for l, c in video.choices if _mentions(words, c)}
             if video.choices else set())
    if video.kind == "paradox":
        return "voted" if len(picks) == 1 else "comment"
    if not video.answer:
        return "comment"
    if video.choices:
        lettered = _CHOICE.match(video.answer)  # "B) No, I am your father."
        right = lettered.group(1) if lettered else next(
            (l for l, c in video.choices if _words(c) == _words(video.answer)), "")
        if not picks:
            return "comment"
        return "correct" if picks == {right} else "wrong"
    if _mentions(words, video.answer):
        return "correct"
    numeric = all(w.isdigit() for w in _words(video.answer))
    return "wrong" if numeric and any(w.isdigit() for w in words) else "comment"


def in_period(c: Comment, since: str = "", until: str = "") -> bool:
    return (not since or c.published >= since) and (not until or c.published < until)


def month_start(settings: Settings, now: datetime | None = None, back: int = 0) -> datetime:
    """Midnight on the 1st of this month (or `back` months earlier) in the channel's timezone."""
    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    year, month = now.year, now.month - back
    while month < 1:
        year, month = year - 1, month + 12
    return datetime(year, month, 1, tzinfo=settings.tz)


def utc_text(moment: datetime) -> str:
    """As YouTube / Instagram times are stored, so they compare as text: "2026-10-01T00:00:00Z"."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_name(moment: datetime) -> str:
    return moment.strftime("%B %Y")


def _parse(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _closings(videos: dict[str, Video], since: str, until: str, now: datetime) -> list[tuple[str, str]]:
    """(when it counts as missed, video id) for the period's videos that are old enough, in order."""
    limit = min(now, _parse(until) or now)
    out = []
    for v in videos.values():
        if (at := _parse(v.published)) and (not since or v.published >= since) and at + MISS_GRACE <= limit:
            out.append((utc_text(at + MISS_GRACE), v.id))
    return sorted(out)


def apply_misses(own: list[Comment], closings: list[tuple[str, str]], commented: set[str]) -> tuple[int, int, int]:
    """The 3-video rule over one fan's period: (points, points lost, times halved). Points come in as their
    comments are posted; each MISS_LIMIT videos in a row they never commented on halves what they have."""
    events = [(c.published, 0, c.points, "") for c in own] + [(at, 1, 0, vid) for at, vid in closings]
    points = lost = halved = streak = 0
    for _, is_closing, pts, vid in sorted(events):
        if not is_closing:
            points += pts
        elif vid in commented:
            streak = 0
        elif points > 0:
            streak += 1
            if streak == MISS_LIMIT:
                cut = points // 2  # an odd half is rounded in the fan's favour
                points, lost, halved, streak = points - cut, lost + cut, halved + 1, 0
    return points, lost, halved


def score(videos: dict[str, Video], comments: list[Comment], subscribers: set[str],
          verdicts: dict[str, str] | None = None, since: str = "", until: str = "",
          now: datetime | None = None) -> list[dict[str, Any]]:
    """Fill each comment's result / points and return the people, best first. Gemini's verdicts win over
    the word rules (quiz answers); links and what Gemini flags (spam, rude) are "skip": no points.
    `since` / `until` (UTC "…Z" times): only comments posted then count for the people (the month), and
    the 3-video rule halves the points of fans who skip MISS_LIMIT videos in a row."""
    verdicts = verdicts or {}
    for c in comments:
        video = videos.get(c.video_id)
        if LINK.search(c.text) or verdicts.get(c.id) == "skip":
            c.result = "skip"
        elif video is None:
            c.result = "comment"
        elif video.kind == "quiz" and c.id in verdicts:
            c.result = verdicts[c.id]
        else:
            c.result = judge(video, c.text)
    by_video: dict[str, list[Comment]] = defaultdict(list)
    for c in sorted(comments, key=lambda c: c.published):
        by_video[c.video_id].append(c)
    for items in by_video.values():
        if first := next((c for c in items if c.result == "correct"), None):
            first.result = "first"
        best: dict[str, Comment] = {}
        for c in items:
            if c.author_id not in best or RESULT_POINTS[c.result] > RESULT_POINTS[best[c.author_id].result]:
                best[c.author_id] = c
        for c in items:
            c.points = RESULT_POINTS[c.result] if best[c.author_id] is c else 0

    people: dict[str, dict[str, Any]] = {}
    own: dict[str, list[Comment]] = defaultdict(list)
    commented: dict[str, set[str]] = defaultdict(set)
    for c in sorted(comments, key=lambda c: c.published):
        if c.result == "skip":
            continue
        commented[c.author_id].add(c.video_id)
        if not in_period(c, since, until):
            continue
        own[c.author_id].append(c)
        p = people.setdefault(c.author_id, {"name": c.author, "id": c.author_id, "points": 0, "comments": 0,
                                            "videos": set(), "correct": 0, "first": 0})
        p["name"] = c.author  # the newest display name
        p["comments"] += 1
        p["videos"].add(c.video_id)
        p["correct"] += c.points > 0 and c.result in ("first", "correct")
        p["first"] += c.result == "first"
    closings = _closings(videos, since, until, now or datetime.now(timezone.utc))
    for p in people.values():
        p["points"], p["lost"], p["halved"] = apply_misses(own[p["id"]], closings, commented[p["id"]])
        p["subscriber"] = p["id"] in subscribers  # must be subscribed to win a prize
        p["videos"] = len(p["videos"])
    return sorted(people.values(), key=lambda p: (-p["points"], -p["first"], p["name"].lower()))


# --------------------------------------------------------------------------- YouTube


def _pages(request_fn, max_pages: int, **kwargs):
    token = None
    for _ in range(max_pages):
        response = request_fn(**kwargs, **({"pageToken": token} if token else {})).execute()
        yield from response.get("items", [])
        token = response.get("nextPageToken")
        if not token:
            return


def channel_videos(youtube) -> tuple[str, list[Video]]:
    items = youtube.channels().list(part="contentDetails", mine=True).execute().get("items", [])
    if not items:
        raise LeaderboardError("The connected Google account has no YouTube channel")
    channel_id = items[0]["id"]
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    videos = []
    for item in _pages(youtube.playlistItems().list, 40, part="snippet,contentDetails", playlistId=uploads,
                       maxResults=50):
        vid = item["contentDetails"].get("videoId")
        if vid:
            videos.append(Video(id=vid, title=item["snippet"].get("title", ""),
                                published=item["contentDetails"].get("videoPublishedAt")
                                or item["snippet"].get("publishedAt", "")))
    return channel_id, videos


def channel_comments(youtube, channel_id: str) -> list[Comment]:
    out = []
    for item in _pages(youtube.commentThreads().list, MAX_COMMENT_PAGES, part="snippet",
                       allThreadsRelatedToChannelId=channel_id, maxResults=100, textFormat="plainText"):
        top = item["snippet"]["topLevelComment"]
        snip = top["snippet"]
        author_id = (snip.get("authorChannelId") or {}).get("value", "")
        if not author_id or author_id == channel_id:
            continue
        out.append(Comment(id=top["id"], video_id=item["snippet"].get("videoId") or snip.get("videoId", ""),
                           author=snip.get("authorDisplayName", ""), author_id=author_id,
                           text=snip.get("textOriginal") or snip.get("textDisplay", ""),
                           published=snip.get("publishedAt", ""), likes=int(snip.get("likeCount") or 0)))
    return out


def channel_subscribers(youtube) -> set[str] | None:
    """Subscribers whose subscriptions are public (YouTube hides the others); None when unreadable."""
    try:
        return {s["subscriberSnippet"]["channelId"] for s in _pages(
            youtube.subscriptions().list, 100, part="subscriberSnippet", mySubscribers=True, maxResults=50)
            if s.get("subscriberSnippet", {}).get("channelId")}
    except Exception as err:  # noqa: BLE001 - the bonus is optional; the comments still count
        logger.warning("Could not read the subscriber list: %s", err)
        return None


# --------------------------------------------------------------------------- answers


_CHOICE = re.compile(r"^\s*([A-Z])\)\s*(.+?)\s*$")


def parse_choices(text: str) -> list[tuple[str, str]]:
    """"A) Rome | B) Paris" → [("A", "Rome"), ("B", "Paris")]."""
    return [(m.group(1), m.group(2)) for part in (text or "").split("|") if (m := _CHOICE.match(part))]


def _norm_title(title: str) -> str:
    return " ".join(_words(re.sub(r"#\w+", "", title)))


def _from_short(short: shorts.ShortVideo) -> tuple[str, str, list[tuple[str, str]]]:
    flashes = [f.get("text", "") for f in (short.render_settings.get("flashes") or []) if isinstance(f, dict)]
    full = [t for t in short.render_settings.get("choices") or [] if isinstance(t, str)]
    choices = [(m.group(1), m.group(2)) for t in (full or flashes) if (m := _CHOICE.match(t))]
    if short.answer.startswith(NO_ANSWER):
        return "paradox", "", choices
    return ("quiz" if short.answer else "story"), short.answer, choices if short.answer else []


def _apply_answers(settings: Settings, videos: list[Video], typed: dict[str, dict[str, Any]]) -> None:
    """What /answer set wins; else the quiz Short on this machine with this YouTube id. Only when no Short
    has the id: the Short with the same title, if exactly one Short and one channel video have that title
    (same titles never share an answer; /answer fixes those)."""
    local = shorts.list_shorts(settings)
    by_id = {vid: s for s in local if (vid := (s.uploads.get("youtube") or {}).get("video_id"))}
    by_title: dict[str, set[str]] = defaultdict(set)
    named: dict[str, shorts.ShortVideo] = {}
    for s in local:
        if (s.uploads.get("youtube") or {}).get("video_id"):
            continue  # already tied to its own YouTube video
        for title in (s.title, (s.texts.get("youtube") or {}).get("title", "")):
            if title:
                by_title[_norm_title(title)].add(s.filename)
                named[s.filename] = s
    on_channel = defaultdict(int)
    for v in videos:
        on_channel[_norm_title(v.title)] += 1
    for v in videos:
        if v.id in typed:
            v.answer = typed[v.id].get("answer", "").strip()
            v.choices = [tuple(c) for c in typed[v.id].get("choices", [])]
            v.kind = "quiz" if v.answer else "paradox" if len(v.choices) >= 2 else "story"
            continue
        short = by_id.get(v.id)
        title = _norm_title(v.title)
        if short is None and len(by_title.get(title, ())) == 1 and on_channel[title] == 1:
            short = named[next(iter(by_title[title]))]
        if short is not None:
            v.kind, v.answer, v.choices = _from_short(short)


# --------------------------------------------------------------------------- the stored board


def _path(settings: Settings) -> Path:
    return settings.output_dir / "comments.json"


def _rescore(settings: Settings, board: Board) -> Board:
    """Score the board for the current month: everyone starts from 0 on the 1st."""
    _apply_answers(settings, list(board.videos.values()), board.answers)
    board.since = utc_text(month_start(settings))
    board.people = score(board.videos, board.comments, board.subscribers, board.verdicts, since=board.since)
    return board


def last_month(settings: Settings, board: Board, now: datetime | None = None) -> tuple[str, list[dict[str, Any]]]:
    """(name, final people) of the month before this one, e.g. ("September 2026", [...])."""
    start, end = month_start(settings, now, back=1), month_start(settings, now)
    people = score(board.videos, board.comments, board.subscribers, board.verdicts,
                   since=utc_text(start), until=utc_text(end))
    return month_name(start), people


def champions_text(month: str, people: list[dict[str, Any]]) -> str:
    """The month-end announcement: the final podium, then the fresh start."""
    if not people:
        return ""
    medals = ("🥇", "🥈", "🥉")
    lines = [f"👑 {month.upper()} CHAMPIONS 👑"]
    lines += [f"{m} {_tag(p['name'])}: {p['points']} pts" for m, p in zip(medals, people)]
    lines += ["", *PRIZES, "",
              "The leaderboard just reset: everyone starts from 0 this month. First correct answer = 10 pts! 🧠🔥"]
    return "\n".join(lines)


def unverified(people: list[dict[str, Any]], top: int = 3) -> list[dict[str, Any]]:
    """Podium fans YouTube doesn't show as subscribed (must be subscribed to win; subscriptions can be private)."""
    return [p for p in people[:top] if not p.get("subscriber")]


def load(settings: Settings) -> Board:
    try:
        data = json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Board()
    board = Board(videos={v["id"]: Video(**{**v, "choices": [tuple(c) for c in v.get("choices", [])]})
                          for v in data.get("videos", [])},
                  comments=[Comment(**c) for c in data.get("comments", [])],
                  subscribers=set(data.get("subscribers", [])), answers=data.get("answers", {}),
                  verdicts=data.get("verdicts", {}), updated_at=data.get("updated_at", ""), channel_id=data.get("channel_id", ""))
    return _rescore(settings, board)


def _save(settings: Settings, board: Board) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"updated_at": board.updated_at, "channel_id": board.channel_id,
            "videos": [asdict(v) for v in board.videos.values()],
            "comments": [asdict(c) for c in board.comments], "subscribers": sorted(board.subscribers),
            "answers": board.answers, "verdicts": board.verdicts}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def refresh(settings: Settings, youtube=None, gemini=None) -> tuple[Board, list[Comment]]:
    """Read every comment again (Gemini checks the new quiz answers); returns the board and the comments
    that are new since the last time."""
    old = load(settings)
    youtube = youtube or publish._youtube()
    channel_id, videos = channel_videos(youtube)
    comments = channel_comments(youtube, channel_id)
    subscribers = channel_subscribers(youtube)
    board = Board(videos={v.id: v for v in videos}, comments=comments,
                  subscribers=old.subscribers if subscribers is None else subscribers,
                  answers=old.answers, verdicts=old.verdicts, updated_at=utcnow().isoformat(),
                  channel_id=channel_id)
    _apply_answers(settings, videos, board.answers)
    verify(settings, board, gemini)
    _rescore(settings, board)
    _save(settings, board)
    seen = {c.id for c in old.comments}
    return board, [c for c in comments if c.id not in seen] if old.updated_at else []


def set_answer(settings: Settings, video_id: str, answer: str, choices: str = "") -> Video:
    board = load(settings)
    if video_id not in board.videos:
        raise LeaderboardError("Unknown video: run /leaderboard refresh first")
    board.answers[video_id] = {"answer": answer.strip(), "choices": parse_choices(choices)}
    for c in board.comments:  # checked against the old answer: check again on the next refresh
        if c.video_id == video_id:
            board.verdicts.pop(c.id, None)
    _rescore(settings, board)
    _save(settings, board)
    return board.videos[video_id]


# --------------------------------------------------------------------------- replies to every fan
#
# A reply is the one thing YouTube always notifies about. Every fan gets exactly one per video, under
# the comment that earned their points for it, saying what it earned (spam, links and rude ones excepted).
# Quiz / paradox videos wait until they're REPLY_AFTER_HOURS old so "Correct!" doesn't spoil the answer
# for the people still guessing. "Wrong" is only said when Gemini agreed (else the plain "+1 pt" text).
# Never a second reply to a fan on a video, nor one the channel already answered; at most
# settings.youtube_reply_daily_max a day, one at a time (the bot waits between them), with varied wording
# and no links or tags. A reply YouTube refuses for a moment is tried REPLY_TRIES times; when it can't be
# posted at all (comment deleted), the fan's other comment on that video gets the one reply instead.

REPLY_AFTER_HOURS, REPLY_MAX_AGE_DAYS, REPLY_TRIES = 10, 30, 3
REPLY_TEXTS = {
    "first": (
        "FIRST & CORRECT! ⚡ You secured the +10 pts mega bonus! You're #{rank} on the SideQuest Leaderboard! 👑",
        "⚡ Fastest brain in the comments! First correct answer = +10 pts. Current rank: #{rank} 🏆",
        "🥇 You cracked it before anyone else! +10 pts bonus. You're now #{rank} on the leaderboard 🧠",
        "👑 First to solve it! That's the +10 pts mega bonus. Rank #{rank} with {total} total!",
    ),
    "correct": (
        "BOOM! Correct answer! 🧠 You just earned +3 pts on the SideQuest Leaderboard! [Current Rank: #{rank}] 🏆",
        "✅ Nailed it! +3 pts for you. You're #{rank} on the SideQuest Leaderboard 🏆",
        "🧠 Big brain! Correct answer, +3 pts. Current rank: #{rank} with {total} 🔥",
        "🎯 Spot on! +3 pts added. You're sitting at #{rank} on our leaderboard!",
    ),
    "wrong": (
        "Ouch! You fell for the trap! 🪤 But no worries, you still earned +1 pt for trying! Check the community "
        "tab for daily rankings! 🧠",
        "🪤 The trap got you this time! You still get +1 pt for playing. You're #{rank} on the leaderboard, "
        "so try the next one!",
        "Not quite! 😅 That one's sneaky. +1 pt for trying, and the next trap is your comeback 🧠",
        "🪤 So close! This trap fools a lot of people. +1 pt for jumping in! Current rank: #{rank}",
    ),
    "voted": (
        "🗳️ Vote counted! +3 pts on the SideQuest Leaderboard. Current rank: #{rank} 🧠",
        "🌀 Bold pick! Your vote earned +3 pts. You're #{rank} on our leaderboard!",
        "🗳️ Side chosen! +3 pts for taking a stand. Rank #{rank}, now defend your answer 👇",
    ),
    "comment": (
        "🙌 Thanks for commenting! +1 pt on the SideQuest Leaderboard. Current rank: #{rank} 🏆",
        "🧠 +1 pt for joining in! Answer the next trap to climb fast. You're #{rank} right now!",
        "🔥 Appreciate you! That's +1 pt on our leaderboard. Rank #{rank} so far!",
    ),
    "again": (  # their scoring comment is gone: no points claimed here
        "🙌 Thanks for commenting! Your points for this video are in. You're #{rank} with {total} 🏆",
        "🔥 Love the energy! You're #{rank} on the SideQuest Leaderboard. The next trap = more points 🧠",
        "🧠 Appreciate you! You're #{rank} right now, so catch the next video for more points!",
    ),
}
_replies_lock = threading.Lock()


def _replies_path(settings: Settings) -> Path:
    return settings.output_dir / "comment_replies.json"


def _load_replies(settings: Settings) -> dict[str, Any]:
    try:
        data = json.loads(_replies_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("replied", {})  # comment id -> when
    data.setdefault("done", {})  # "person|video" -> when it was queued
    data.setdefault("pending", [])
    data.setdefault("sent", {"date": "", "count": 0})
    return data


def _save_replies(settings: Settings, data: dict[str, Any]) -> None:
    path = _replies_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def ranks(people: list[dict[str, Any]]) -> dict[str, int]:
    """Tied points share a rank (1 + how many have more)."""
    return {p["id"]: 1 + sum(q["points"] > p["points"] for q in people) for p in people}


def reply_text(result: str, rank: int, total: int, seed: str) -> str:
    texts = REPLY_TEXTS.get(result, REPLY_TEXTS["comment"])
    return random.Random(seed).choice(texts).format(rank=rank, total=f"{total} pt" + "s" * (total != 1))


def _replyable(settings: Settings, board: Board, c: Comment) -> bool:
    """Fine to thank: not spam / links / rude, and checked by Gemini (when there is a Gemini key)."""
    from . import script_writer as sw

    if c.result == "skip" or c.video_id not in board.videos:
        return False
    return c.id in board.verdicts or not sw.gemini_api_key(settings)


def _fan_key(c: Comment) -> str:
    return f"{c.author_id}|{c.video_id}"


def _reply_state(settings: Settings, board: Board, data: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Each fan on each video (last REPLY_MAX_AGE_DAYS): the comment to reply to, or why not yet.
    key -> (state, comment); state is replied / queued / ready / quiz_wait / unchecked / skipped / gone
    (YouTube refused every comment of theirs)."""
    ready_before = (now - timedelta(hours=REPLY_AFTER_HOURS)).isoformat()
    oldest = (now - timedelta(days=REPLY_MAX_AGE_DAYS)).isoformat()
    waiting = {p["comment_id"] for p in data["pending"]}
    order = {"first": 0, "correct": 1, "voted": 2, "wrong": 3, "comment": 4}
    by_fan: dict[str, list[Comment]] = defaultdict(list)
    for c in sorted(board.comments, key=lambda c: (c.points == 0, order.get(c.result, 9), c.published)):
        if c.video_id in board.videos and c.published >= oldest:
            by_fan[_fan_key(c)].append(c)
    out: dict[str, Any] = {}
    for key, items in by_fan.items():
        if any(c.id in waiting for c in items):
            out[key] = ("queued", None)
            continue
        if key in data["done"]:
            out[key] = ("replied", None)
            continue
        # The scoring comment first; the others only once it can't get a reply (spam, deleted).
        left = [c for c in items if c.id not in data["replied"] and c.result != "skip"]
        if not left:
            failed = data.get("failed", {})
            gone = all(c.id in failed or c.result == "skip" for c in items) and any(c.id in failed for c in items)
            out[key] = ("gone" if gone else "skipped" if all(c.result == "skip" for c in items) else "replied",
                        None)
            continue
        c, v = left[0], board.videos[left[0].video_id]
        if not _replyable(settings, board, c):
            out[key] = ("unchecked", c)
        elif v.kind in ("quiz", "paradox") and (v.published or "") > ready_before:
            out[key] = ("quiz_wait", c)
        else:
            out[key] = ("ready", c)
    return out


def plan_replies(settings: Settings, board: Board, today: date, now: datetime | None = None,
                 wait: bool = True) -> list[dict[str, Any]]:
    """Queue one reply per fan per video (the comment that scored, scoring ones first); returns what was queued.
    wait=False: quiz / paradox videos too, without the REPLY_AFTER_HOURS spoiler wait."""
    now = now or datetime.now(timezone.utc)
    rank_of = ranks(board.people)
    total_of = {p["id"]: p["points"] for p in board.people}
    order = {"first": 0, "correct": 1, "voted": 2, "wrong": 3, "comment": 4}
    with _replies_lock:
        data = _load_replies(settings)
        day = today.isoformat()
        used = (data["sent"]["count"] if data["sent"]["date"] == day else 0) + len(data["pending"])
        ready = [c for state, c in _reply_state(settings, board, data, now).values()
                 if state == "ready" or (state == "quiz_wait" and not wait)]
        queued = []
        for c in sorted(ready, key=lambda c: (c.points == 0, order.get(c.result, 9), c.published)):
            if used >= settings.youtube_reply_daily_max:
                break
            result = c.result if c.points > 0 else "again"
            if result == "wrong" and board.verdicts.get(c.id) != "wrong":
                result = "comment"  # only Gemini's "wrong" is said out loud
            item = {"comment_id": c.id, "author_id": c.author_id, "name": c.author, "video_id": c.video_id,
                    "result": result,
                    "text": reply_text(result, rank_of.get(c.author_id, 0), total_of.get(c.author_id, 0), c.id)}
            data["pending"].append(item)
            data["done"][_fan_key(c)] = now.isoformat()
            queued.append(item)
            used += 1
        _save_replies(settings, data)
    return queued


# --------------------------------------------------------------------------- Gemini double-check


class _Verdict(BaseModel):
    id: str
    verdict: Literal["correct", "wrong", "not_an_answer", "skip"]


class _Verdicts(BaseModel):
    items: list[_Verdict]


VERIFY_PROMPT = """You check comments on a gaming YouTube Shorts channel before the channel replies to them.
For each comment decide:
- skip: spam, scams, self-promotion ("sub to me", "check my channel"), sexual content, insults, hate or
  harassment, or anything a family-friendly channel should not thank someone for;
- correct: (quiz videos only) the commenter's answer means the correct answer (allow typos, synonyms, other
  languages, number words, the right option letter);
- wrong: (quiz videos only) they answered something else, listed several options to cover their bets, or
  said "not X" where X is the answer;
- not_an_answer: anything else that is fine to thank (praise, a question, a joke, an emoji, off-topic chat,
  or every fine comment on a video without a quiz).
Return one item per comment id.

{videos}"""


def verify(settings: Settings, board: Board, client=None, batch: int = 80) -> int:
    """Gemini's verdict for every comment that has none yet (kept, so each comment is checked once):
    spam / rude ones become "skip"; on quiz videos also correct or wrong."""
    from . import script_writer as sw

    todo = [c for c in board.comments if c.id not in board.verdicts and c.video_id in board.videos
            and not LINK.search(c.text)]
    if not todo or (client is None and not sw.gemini_api_key(settings)):
        return 0
    from google.genai import errors, types

    client, model = sw._gemini(settings, client)
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=_Verdicts,
                                         temperature=0.0)
    done = 0
    for start in range(0, len(todo), batch):
        part = todo[start:start + batch]
        by_video: dict[str, list[Comment]] = defaultdict(list)
        for c in part:
            by_video[c.video_id].append(c)
        blocks = []
        for vid, items in by_video.items():
            v = board.videos[vid]
            if v.kind == "quiz" and v.answer:
                choices = " | ".join(f"{l}) {t}" for l, t in v.choices)
                about = f"Quiz. Correct answer: {v.answer}" + (f"\nChoices: {choices}" if choices else "")
            else:
                about = "No quiz on this video: only skip or not_an_answer."
            blocks.append(f"Video: {v.name}\n{about}\n"
                          + "\n".join(f"- id {c.id}: {json.dumps(c.text[:300], ensure_ascii=False)}" for c in items))
        try:
            response, model = sw._generate_with_fallback(client, model, [VERIFY_PROMPT.format(videos="\n\n".join(blocks))],
                                                         config, lambda f, m: None, errors.APIError)
            result = response.parsed if isinstance(response.parsed, _Verdicts) else _Verdicts.model_validate_json(
                response.text or "{}")
        except Exception as err:  # noqa: BLE001 - the word rules still score them; no replies until checked
            logger.warning("Gemini comment check failed: %s", err)
            break
        by_id = {c.id: c for c in part}
        for item in result.items:
            if c := by_id.get(item.id):
                quiz = board.videos[c.video_id].kind == "quiz"
                verdict = item.verdict
                if verdict == "not_an_answer" or (verdict in ("correct", "wrong") and not quiz):
                    verdict = "comment"
                board.verdicts[item.id] = verdict
                done += 1
    return done


def send_next_reply(settings: Settings, channel_id: str, today: date, youtube=None) -> dict[str, Any] | None:
    """Post the oldest queued reply (one per call). None when nothing was posted."""
    from googleapiclient.errors import HttpError

    with _replies_lock:
        data = _load_replies(settings)
        day = today.isoformat()
        if data["sent"]["date"] != day:
            data["sent"] = {"date": day, "count": 0}
        if not data["pending"] or data["sent"]["count"] >= settings.youtube_reply_daily_max:
            return None
        item = data["pending"].pop(0)
        _save_replies(settings, data)  # off the queue first: a crash can't make it post twice
    if item["comment_id"] in data["replied"]:
        return None
    youtube = youtube or publish._youtube()
    try:
        replies = youtube.comments().list(part="snippet", parentId=item["comment_id"], maxResults=100).execute()
        if any((r["snippet"].get("authorChannelId") or {}).get("value") == channel_id
               for r in replies.get("items", [])):
            posted = None  # the channel answered it already (by hand)
        else:
            body = {"snippet": {"parentId": item["comment_id"], "textOriginal": item["text"]}}
            posted = youtube.comments().insert(part="snippet", body=body).execute()
    except HttpError as err:
        status = getattr(err.resp, "status", 0)
        tries = item.get("tries", 0) + 1
        logger.warning("Reply to %s's comment %s failed (try %d): %s", item["name"], item["comment_id"], tries, err)
        if status not in (400, 403, 404) and tries < REPLY_TRIES:  # deleted / comments off: no point retrying
            with _replies_lock:
                data = _load_replies(settings)
                data["pending"].append({**item, "tries": tries})
                _save_replies(settings, data)
            return None
        posted = None
        with _replies_lock:
            data = _load_replies(settings)
            data.setdefault("failed", {})[item["comment_id"]] = f"HTTP {status}: {str(err)[:200]}"
            data["done"].pop(f"{item['author_id']}|{item['video_id']}", None)  # their other comment, if any
            _save_replies(settings, data)
    with _replies_lock:
        data = _load_replies(settings)
        data["replied"][item["comment_id"]] = utcnow().isoformat()  # never again, whatever happened
        if posted is not None:
            if data["sent"]["date"] != day:
                data["sent"] = {"date": day, "count": 0}
            data["sent"]["count"] += 1
        _save_replies(settings, data)
    return item if posted is not None else None


def new_skips(settings: Settings, board: Board) -> list[Comment]:
    """Comments flagged as spam / rude / links that Discord hasn't been told about yet (each told once)."""
    with _replies_lock:
        data = _load_replies(settings)
        told = data.setdefault("flagged", {})
        out = [c for c in board.comments if c.result == "skip" and c.id not in told]
        for c in out:
            told[c.id] = utcnow().isoformat()
        if out:
            _save_replies(settings, data)
    return out


def pending_replies(settings: Settings) -> int:
    with _replies_lock:
        return len(_load_replies(settings)["pending"])


def reply_overview(settings: Settings, board: Board, today: date, now: datetime | None = None) -> dict[str, Any]:
    """Where every fan on every video of the last REPLY_MAX_AGE_DAYS stands, for /replies."""
    now = now or datetime.now(timezone.utc)
    with _replies_lock:
        data = _load_replies(settings)
    out = {"replied": 0, "queued": len(data["pending"]), "ready": 0, "quiz_wait": 0, "unchecked": 0, "skipped": 0,
           "failed": len(data.get("failed", {})), "next_quiz": "",
           "sent_today": data["sent"]["count"] if data["sent"]["date"] == today.isoformat() else 0,
           "daily_max": settings.youtube_reply_daily_max}
    for state, c in _reply_state(settings, board, data, now).values():
        if state not in ("queued", "gone"):
            out[state] += 1
        if state == "quiz_wait" and (v := board.videos[c.video_id]).published:
            ready = datetime.fromisoformat(v.published.replace("Z", "+00:00")) + timedelta(hours=REPLY_AFTER_HOURS)
            out["next_quiz"] = min(out["next_quiz"] or ready.isoformat(), ready.isoformat())
    return out


# --------------------------------------------------------------------------- Discord text


def safe(text: str, limit: int = 160) -> str:
    """Comment text that can't ping (@everyone) or break the message's formatting."""
    text = " ".join((text or "").split())
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return re.sub(r"([*_~`|>\\])", r"\\\1", text).replace("@", "@\u200b")


def person_line(rank: int, p: dict[str, Any]) -> str:
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"`{rank}.`")
    bits = [f"{p['comments']} comment{'s' * (p['comments'] != 1)}"]
    if p["correct"]:
        bits.append(f"{p['correct']} correct")
    if p["first"]:
        bits.append(f"{p['first']}× first")
    if p["subscriber"]:
        bits.append("⭐ sub")
    if p.get("lost"):
        bits.append(f"🔥 −{p['lost']}")
    return f"{medal} **{safe(p['name'], 40)}** {p['points']} pts · " + " · ".join(bits)


def comment_line(c: Comment) -> str:
    pts = f"+{c.points}" if c.points else "0"
    return f"{RESULT_ICON[c.result]} `{pts:>3}` **{safe(c.author, 32)}**: {safe(c.text)}"


def top_lines(board: Board, limit: int = 10) -> list[str]:
    return [person_line(i, p) for i, p in enumerate(board.people[:limit], 1)]


def video_lines(board: Board, video_id: str) -> list[str]:
    v = board.videos.get(video_id)
    if v is None:
        return ["Unknown video: run /leaderboard refresh first"]
    items = sorted((c for c in board.comments if c.video_id == video_id), key=lambda c: c.published)
    if v.kind == "paradox":
        about = "Paradox: " + " or ".join(t for _, t in v.choices)
    elif v.answer:
        about = f"Answer: ||{safe(v.answer, 60)}||"
    else:
        about = "No quiz answer (set one with /answer)"
    head = [f"🎬 **{safe(v.name, 80)}** · {len(items)} comment{'s' * (len(items) != 1)} · {about}", f"<{v.url}>"]
    return head + ([comment_line(c) for c in items] or ["No comments yet."])


def person_lines(board: Board, author_id: str) -> list[str]:
    rank, p = next(((i, p) for i, p in enumerate(board.people, 1) if p["id"] == author_id), (0, None))
    if p is None:
        return ["Nobody by that name has commented."]
    lines = [person_line(rank, p), f"<https://www.youtube.com/channel/{p['id']}>"]
    if p["subscriber"]:
        lines.append("⭐ Subscribed: can win a prize")
    else:
        lines.append("Not shown as subscribed (maybe private subscriptions): check before giving a prize")
    if p["lost"]:
        lines.append(f"🔥 3-video rule: lost {p['lost']} pts ({p['halved']}× skipped {MISS_LIMIT} videos in a row)")
    for c in sorted((c for c in board.comments if c.author_id == author_id and in_period(c, board.since)),
                    key=lambda c: c.published, reverse=True):
        title = board.videos[c.video_id].name if c.video_id in board.videos else c.video_id
        lines.append(f"{RESULT_ICON[c.result]} `+{c.points}` {safe(title, 40)}: {safe(c.text, 100)}")
    return lines


def _tag(name: str) -> str:
    return name if name.startswith("@") else "@" + re.sub(r"\s+", "", name)


def next_drop(settings: Settings, now: datetime | None = None) -> str:
    """When the next video goes up, as the post says it: "in 1 hour", "at 23:00" or "tomorrow at 14:30"."""
    from . import planner

    now = (now or datetime.now(settings.tz)).astimezone(settings.tz)
    for days, word in ((0, "at"), (1, "tomorrow at")):
        day = now.date() + timedelta(days=days)
        for slot in planner.SLOTS:
            minutes = (planner.slot_time(settings, day, slot) - now).total_seconds() / 60
            if minutes > 0:
                return "in 1 hour" if 40 <= minutes <= 80 else f"{word} {slot}"
    return "soon"


def post_caption(people: list[dict[str, Any]], tags: int = 13, drop: str = "in 1 hour", month: str = "") -> str:
    """The daily YouTube post text, tagging everyone on the picture. `month`: "October 2026"."""
    if not people:
        return ""
    top = people[0]
    pts = "pt" if top["points"] == 1 else "pts"
    lines = ["🏆 DAILY LEADERBOARD UPDATE! 🏆",
             f"Current Kings of Logic{f' of {month.split()[0]}' if month else ''}: {_tag(top['name'])} with "
             f"{top['points']} {pts}!",
             f"Next trap drops {drop}! Who's taking the #1 spot today? 🧠🔥"]
    if month:
        lines.append("The leaderboard resets on the 1st of every month: everyone starts from 0.")
    lines.append(f"⚠️ 3-video rule: skip {MISS_LIMIT} videos in a row and lose 50% of your points! "
                 "Must be subscribed to win 🎁")
    tagged = " ".join(_tag(p["name"]) for p in people[:tags])
    return "\n".join(lines) + ("\n\n" + tagged if len(people) > 1 else "")


def new_lines(board: Board, new: list[Comment], limit: int = 20) -> list[str]:
    by_video: dict[str, list[Comment]] = defaultdict(list)
    for c in sorted(new, key=lambda c: c.published):
        by_video[c.video_id].append(c)
    lines: list[str] = []
    for vid, items in by_video.items():
        title = board.videos[vid].name if vid in board.videos else vid
        lines.append(f"🎬 **{safe(title, 60)}**")
        lines += [comment_line(c) for c in items]
    if len(lines) > limit:
        lines = lines[:limit] + [f"…and more: /comments shows each video"]
    return lines
