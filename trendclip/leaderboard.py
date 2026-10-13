"""Every YouTube comment of the channel, scored into a fan leaderboard that the Discord bot shows.

Stored in output/comments.json on the machine that runs the bot; refreshed each morning and by
/leaderboard refresh. Points per person and video (only their best comment on a video counts, so
guessing every option or spamming doesn't pay): the first correct quiz answer 10, any other correct
answer or a paradox vote 2, any other comment 1. A subscriber gets 10 once (YouTube only shows
subscribers who made their subscriptions public). The channel's own comments are skipped.

Quiz answers come from the Shorts on this machine (matched by YouTube id, else by title); /answer sets
them for videos made elsewhere.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import publish, shorts
from .config import Settings
from .models import utcnow

logger = logging.getLogger(__name__)

FIRST_POINTS, CORRECT_POINTS, COMMENT_POINTS, SUBSCRIBER_POINTS = 10, 2, 1, 10
MAX_COMMENT_PAGES = 200  # 100 threads a page, 1 quota unit each
RESULT_POINTS = {"first": FIRST_POINTS, "correct": CORRECT_POINTS, "voted": CORRECT_POINTS,
                 "wrong": COMMENT_POINTS, "comment": COMMENT_POINTS}
RESULT_ICON = {"first": "🥇", "correct": "✅", "voted": "🗳️", "wrong": "❌", "comment": "💬"}
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
    updated_at: str = ""
    people: list[dict[str, Any]] = field(default_factory=list)  # best first


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
        right = next((l for l, c in video.choices if _words(c) == _words(video.answer)), "")
        if not picks:
            return "comment"
        return "correct" if picks == {right} else "wrong"
    if _mentions(words, video.answer):
        return "correct"
    numeric = all(w.isdigit() for w in _words(video.answer))
    return "wrong" if numeric and any(w.isdigit() for w in words) else "comment"


def score(videos: dict[str, Video], comments: list[Comment], subscribers: set[str]) -> list[dict[str, Any]]:
    """Fill each comment's result / points and return the people, best first."""
    for c in comments:
        video = videos.get(c.video_id)
        c.result = judge(video, c.text) if video else "comment"
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
    for c in sorted(comments, key=lambda c: c.published):
        p = people.setdefault(c.author_id, {"name": c.author, "id": c.author_id, "points": 0, "comments": 0,
                                            "videos": set(), "correct": 0, "first": 0})
        p["name"] = c.author  # the newest display name
        p["comments"] += 1
        p["videos"].add(c.video_id)
        p["points"] += c.points
        p["correct"] += c.points > 0 and c.result in ("first", "correct")
        p["first"] += c.result == "first"
    for p in people.values():
        p["subscriber"] = p["id"] in subscribers
        p["points"] += SUBSCRIBER_POINTS if p["subscriber"] else 0
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
    choices = [(m.group(1), m.group(2)) for t in flashes if (m := _CHOICE.match(t))]
    if short.answer.startswith(NO_ANSWER):
        return "paradox", "", choices
    return ("quiz" if short.answer else "story"), short.answer, choices if short.answer else []


def _apply_answers(settings: Settings, videos: list[Video], typed: dict[str, dict[str, Any]]) -> None:
    """What /answer set wins; else the quiz Short on this machine (by YouTube id, else by title)."""
    local = shorts.list_shorts(settings)
    by_id = {(s.uploads.get("youtube") or {}).get("video_id"): s for s in local}
    by_title: dict[str, shorts.ShortVideo] = {}
    for s in local:
        for title in (s.title, (s.texts.get("youtube") or {}).get("title", ""),
                      (s.uploads.get("youtube") or {}).get("title", "")):
            if title:
                by_title.setdefault(_norm_title(title), s)
    for v in videos:
        if v.id in typed:
            v.answer = typed[v.id].get("answer", "").strip()
            v.choices = [tuple(c) for c in typed[v.id].get("choices", [])]
            v.kind = "quiz" if v.answer else "paradox" if len(v.choices) >= 2 else "story"
        elif short := by_id.get(v.id) or by_title.get(_norm_title(v.title)):
            v.kind, v.answer, v.choices = _from_short(short)


# --------------------------------------------------------------------------- the stored board


def _path(settings: Settings) -> Path:
    return settings.output_dir / "comments.json"


def _rescore(settings: Settings, board: Board) -> Board:
    _apply_answers(settings, list(board.videos.values()), board.answers)
    board.people = score(board.videos, board.comments, board.subscribers)
    return board


def load(settings: Settings) -> Board:
    try:
        data = json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Board()
    board = Board(videos={v["id"]: Video(**{**v, "choices": [tuple(c) for c in v.get("choices", [])]})
                          for v in data.get("videos", [])},
                  comments=[Comment(**c) for c in data.get("comments", [])],
                  subscribers=set(data.get("subscribers", [])), answers=data.get("answers", {}),
                  updated_at=data.get("updated_at", ""))
    return _rescore(settings, board)


def _save(settings: Settings, board: Board) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"updated_at": board.updated_at, "videos": [asdict(v) for v in board.videos.values()],
            "comments": [asdict(c) for c in board.comments], "subscribers": sorted(board.subscribers),
            "answers": board.answers}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def refresh(settings: Settings, youtube=None) -> tuple[Board, list[Comment]]:
    """Read every comment again; returns the board and the comments that are new since the last time."""
    old = load(settings)
    youtube = youtube or publish._youtube()
    channel_id, videos = channel_videos(youtube)
    comments = channel_comments(youtube, channel_id)
    subscribers = channel_subscribers(youtube)
    board = Board(videos={v.id: v for v in videos}, comments=comments,
                  subscribers=old.subscribers if subscribers is None else subscribers,
                  answers=old.answers, updated_at=utcnow().isoformat())
    _rescore(settings, board)
    _save(settings, board)
    seen = {c.id for c in old.comments}
    return board, [c for c in comments if c.id not in seen] if old.updated_at else []


def set_answer(settings: Settings, video_id: str, answer: str, choices: str = "") -> Video:
    board = load(settings)
    if video_id not in board.videos:
        raise LeaderboardError("Unknown video: run /leaderboard refresh first")
    board.answers[video_id] = {"answer": answer.strip(), "choices": parse_choices(choices)}
    _rescore(settings, board)
    _save(settings, board)
    return board.videos[video_id]


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
        lines.append(f"⭐ Subscriber bonus: +{SUBSCRIBER_POINTS}")
    for c in sorted((c for c in board.comments if c.author_id == author_id), key=lambda c: c.published, reverse=True):
        title = board.videos[c.video_id].name if c.video_id in board.videos else c.video_id
        lines.append(f"{RESULT_ICON[c.result]} `+{c.points}` {safe(title, 40)}: {safe(c.text, 100)}")
    return lines


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
