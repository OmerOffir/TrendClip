"""Replies to the comments on the account's Instagram Reels, to thank fans and bring them to YouTube.

The same rules as the YouTube replies (leaderboard.py): one reply per fan per Reel, under their best
comment; spam, links and rude comments get none (Gemini checks them); quiz / paradox Reels wait 24 h so
the reply doesn't give the answer away; "wrong" only when Gemini agrees; never a second reply, nor one
the account already answered by hand. The Instagram leaderboard doesn't exist (Instagram and YouTube
accounts can't be linked), so the replies claim no points: about half invite the fan to the YouTube
leaderboard by the channel's name (no links or @: Instagram flags those as spam), the others just thank.

Quiz answers come from the Shorts this machine posted (Short.uploads["instagram"]["media_id"]); other
Reels are thanked like stories. Stored in output/instagram_comments.json (Reels, comments, Gemini's
verdicts) and output/instagram_replies.json (what was replied, the queue).
"""

from __future__ import annotations

import json
import logging
import random
import threading
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import instagram, leaderboard, shorts
from .config import Settings
from .leaderboard import Board, Comment, Video
from .models import utcnow

logger = logging.getLogger(__name__)

MAX_REELS = 60  # the newest Reels of the last REPLY_MAX_AGE_DAYS; one API call each per read
MAX_PAGES = 10  # of 50 comments per Reel
LIMIT_PAUSE_MINUTES = 30  # after Instagram's rate limit, no replies for this long
INVITE_SHARE = 0.5  # the share of replies that invite the fan to YouTube

TEXTS = {
    "first": (
        "FIRST and correct! ⚡ Sharp one.",
        "Fastest correct answer here! 🧠🔥",
    ),
    "correct": (
        "BOOM, correct! 🧠 Sharp one.",
        "Nailed it! ✅ You saw through the trap.",
        "Correct! 🔥 Not many get that one.",
    ),
    "wrong": (
        "Ouch, the trap got you 🪤 Try the next one!",
        "So close! 😅 This trap was a sneaky one.",
        "Not quite 🪤 but nice try! A new trap drops daily.",
    ),
    "voted": (
        "🗳️ Vote counted! Which side wins?",
        "Good pick! 🗳️ The comments are split on this one.",
    ),
    "comment": (
        "🙌 Thanks for watching!",
        "🔥 Appreciate you!",
        "🧠 Thanks for the comment! New traps drop daily.",
    ),
}
INVITES = {
    "first": (
        "FIRST and correct! ⚡ On YouTube that's +10 pts on our leaderboard. Find {name} on YouTube 👑",
        "Fastest correct answer! 🧠 That would put you on our daily leaderboard. It lives on YouTube: {name} 🏆",
    ),
    "correct": (
        "Correct! 🧠 On YouTube that's points on the SideQuest Leaderboard. Come claim your spot: {name} 👑",
        "BOOM, you got it! ✅ Answers like that climb our daily leaderboard on YouTube ({name}) 🏆",
    ),
    "wrong": (
        "Ouch, the trap got you 🪤 On YouTube you'd still get points for trying. Find {name} there 🏆",
        "So close! 😅 Our YouTube leaderboard gives points even for trying: {name} on YouTube 🏆",
    ),
    "voted": (
        "🗳️ Vote counted! Votes earn points on our YouTube leaderboard too: {name} on YouTube 🏆",
        "Good pick! 🗳️ On YouTube every vote climbs the daily leaderboard. Find {name} there 👑",
    ),
    "comment": (
        "🔥 Love it! Our daily leaderboard lives on YouTube: {name} 🏆",
        "🙌 Thanks for watching! Comment on {name} on YouTube to join the daily leaderboard 👑",
        "🧠 Appreciate you! The daily trap leaderboard is on our YouTube: {name} 🏆",
    ),
}
_lock = threading.Lock()


def _board_path(settings: Settings) -> Path:
    return settings.output_dir / "instagram_comments.json"


def _replies_path(settings: Settings) -> Path:
    return settings.output_dir / "instagram_replies.json"


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _load_replies(settings: Settings) -> dict[str, Any]:
    data = _read(_replies_path(settings))
    for key, empty in (("replied", {}), ("done", {}), ("pending", []), ("failed", {}), ("flagged", {}),
                       ("sent", {"date": "", "count": 0}), ("pause_until", "")):
        data.setdefault(key, empty)
    return data


def enabled(settings: Settings) -> bool:
    return settings.instagram_auto_replies and instagram.configured(settings)


# --------------------------------------------------------------------------- the stored Reels / comments


def load(settings: Settings) -> Board:
    data = _read(_board_path(settings))
    board = Board(videos={v["id"]: Video(**{**v, "choices": [tuple(c) for c in v.get("choices", [])]})
                          for v in data.get("videos", [])},
                  comments=[Comment(**c) for c in data.get("comments", [])],
                  verdicts=data.get("verdicts", {}), updated_at=data.get("updated_at", ""),
                  channel_id=data.get("username", ""))
    board.people = leaderboard.score(board.videos, board.comments, set(), board.verdicts)
    return board


def links(settings: Settings) -> dict[str, str]:
    """Reel id -> its permalink."""
    return _read(_board_path(settings)).get("links", {})


def _save(settings: Settings, board: Board, permalinks: dict[str, str]) -> None:
    _write(_board_path(settings), {
        "updated_at": board.updated_at, "username": board.channel_id, "links": permalinks,
        "videos": [asdict(v) for v in board.videos.values()], "comments": [asdict(c) for c in board.comments],
        "verdicts": board.verdicts})


def _time(text: str) -> str:
    """Instagram's "2026-10-17T12:00:00+0000" as the "2026-10-17T12:00:00Z" the YouTube code compares."""
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return text or ""


def _pages(url: str, access_token: str, params: dict[str, Any], max_pages: int):
    for _ in range(max_pages):
        data = instagram._call("GET", url, access_token, params=params)
        yield from data.get("data", [])
        url = (data.get("paging") or {}).get("next", "")
        if not url:
            return
        params = {}  # the next link carries them (and the token: _call adds it again, harmlessly)


def _reels(settings: Settings, access_token: str, user_id: str) -> list[dict[str, Any]]:
    oldest = (datetime.now(timezone.utc) - timedelta(days=leaderboard.REPLY_MAX_AGE_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    out = []
    for item in _pages(f"{instagram.GRAPH}/{user_id}/media", access_token,
                       {"fields": "id,caption,timestamp,permalink,media_type,comments_count", "limit": 50}, 5):
        if _time(item.get("timestamp", "")) < oldest or len(out) >= MAX_REELS:
            break  # newest first
        out.append(item)
    return out


def _kinds(settings: Settings) -> dict[str, shorts.ShortVideo]:
    return {mid: s for s in shorts.list_shorts(settings) if (mid := (s.uploads.get("instagram") or {}).get("media_id"))}


def refresh(settings: Settings, gemini=None) -> Board:
    """Read the comments on the recent Reels again; Gemini checks the new ones."""
    access_token = instagram.token(settings)
    me = instagram.account(settings)
    username = me.get("username", "")
    old = load(settings)
    by_media = _kinds(settings)
    videos: dict[str, Video] = {}
    permalinks: dict[str, str] = {}
    comments: list[Comment] = []
    for reel in _reels(settings, access_token, me.get("user_id") or "me"):
        title = (reel.get("caption") or "").split("\n", 1)[0][:120] or "Reel"
        v = Video(id=reel["id"], title=title, published=_time(reel.get("timestamp", "")))
        if short := by_media.get(v.id):
            v.kind, v.answer, v.choices = leaderboard._from_short(short)
        videos[v.id] = v
        permalinks[v.id] = reel.get("permalink", "")
        if not reel.get("comments_count"):
            continue
        for c in _pages(f"{instagram.GRAPH}/{v.id}/comments", access_token,
                        {"fields": "id,text,timestamp,username,from", "limit": 50}, MAX_PAGES):
            author = c.get("username") or (c.get("from") or {}).get("username", "")
            author_id = (c.get("from") or {}).get("id") or author
            if not author_id or author.lower() == username.lower():
                continue  # the account's own comments (the first comment under each Reel)
            comments.append(Comment(id=c["id"], video_id=v.id, author=author, author_id=author_id,
                                    text=c.get("text", ""), published=_time(c.get("timestamp", ""))))
    board = Board(videos=videos, comments=comments, verdicts=old.verdicts, updated_at=utcnow().isoformat(),
                  channel_id=username)
    leaderboard.verify(settings, board, gemini)
    board.people = leaderboard.score(board.videos, board.comments, set(), board.verdicts)
    _save(settings, board, permalinks)
    return board


# --------------------------------------------------------------------------- replies


def reply_text(settings: Settings, result: str, seed: str) -> str:
    rng = random.Random(seed)
    kind = result if result in TEXTS else "comment"
    if rng.random() < INVITE_SHARE:
        return rng.choice(INVITES[kind]).format(name=settings.instagram_youtube_name)
    return rng.choice(TEXTS[kind])


def plan_replies(settings: Settings, board: Board, today: date, now: datetime | None = None,
                 wait: bool = True) -> list[dict[str, Any]]:
    """Queue one reply per fan per Reel (their best comment first); returns what was queued.
    wait=False: quiz / paradox Reels too, without the spoiler wait."""
    now = now or datetime.now(timezone.utc)
    order = {"first": 0, "correct": 1, "voted": 2, "wrong": 3, "comment": 4}
    with _lock:
        data = _load_replies(settings)
        day = today.isoformat()
        used = (data["sent"]["count"] if data["sent"]["date"] == day else 0) + len(data["pending"])
        ready = [c for state, c in leaderboard._reply_state(settings, board, data, now).values()
                 if state == "ready" or (state == "quiz_wait" and not wait)]
        queued = []
        for c in sorted(ready, key=lambda c: (c.points == 0, order.get(c.result, 9), c.published)):
            if used >= settings.instagram_reply_daily_max:
                break
            result = c.result if c.points > 0 else "comment"
            if result == "wrong" and board.verdicts.get(c.id) != "wrong":
                result = "comment"  # only Gemini's "wrong" is said out loud
            item = {"comment_id": c.id, "author_id": c.author_id, "name": c.author, "video_id": c.video_id,
                    "result": result, "text": reply_text(settings, result, c.id)}
            data["pending"].append(item)
            data["done"][f"{c.author_id}|{c.video_id}"] = now.isoformat()
            queued.append(item)
            used += 1
        _write(_replies_path(settings), data)
    return queued


def send_next_reply(settings: Settings, today: date, now: datetime | None = None) -> dict[str, Any] | None:
    """Post the oldest queued reply (one per call). None when nothing was posted."""
    now = now or datetime.now(timezone.utc)
    with _lock:
        data = _load_replies(settings)
        day = today.isoformat()
        if data["sent"]["date"] != day:
            data["sent"] = {"date": day, "count": 0}
        if data["pause_until"] and now.isoformat() < data["pause_until"]:
            return None
        if not data["pending"] or data["sent"]["count"] >= settings.instagram_reply_daily_max:
            return None
        item = data["pending"].pop(0)
        _write(_replies_path(settings), data)  # off the queue first: a crash can't make it post twice
    if item["comment_id"] in data["replied"]:
        return None
    posted = None
    try:
        access_token = instagram.token(settings)
        me = instagram.account(settings).get("username", "").lower()
        answers = instagram._call("GET", f"{instagram.GRAPH}/{item['comment_id']}/replies", access_token,
                                  params={"fields": "username"}).get("data", [])
        if not any((r.get("username") or "").lower() == me for r in answers):
            posted = instagram._call("POST", f"{instagram.GRAPH}/{item['comment_id']}/replies", access_token,
                                     data={"message": item["text"]})
    except instagram.InstagramError as err:
        tries = item.get("tries", 0) + 1
        logger.warning("Instagram reply to %s's comment %s failed (try %d): %s", item["name"], item["comment_id"],
                       tries, err)
        limited = err.code in instagram.LIMIT_CODES
        if limited or ((not err.status or err.status >= 500) and tries < leaderboard.REPLY_TRIES):
            with _lock:
                data = _load_replies(settings)
                data["pending"].insert(0, {**item, "tries": tries if not limited else item.get("tries", 0)})
                if limited:
                    data["pause_until"] = (now + timedelta(minutes=LIMIT_PAUSE_MINUTES)).isoformat()
                _write(_replies_path(settings), data)
            return None
        with _lock:
            data = _load_replies(settings)
            data["failed"][item["comment_id"]] = str(err)[:200]
            data["done"].pop(f"{item['author_id']}|{item['video_id']}", None)  # their other comment, if any
            _write(_replies_path(settings), data)
    with _lock:
        data = _load_replies(settings)
        data["replied"][item["comment_id"]] = utcnow().isoformat()  # never again, whatever happened
        if posted is not None:
            if data["sent"]["date"] != day:
                data["sent"] = {"date": day, "count": 0}
            data["sent"]["count"] += 1
        _write(_replies_path(settings), data)
    return item if posted is not None else None


def new_skips(settings: Settings, board: Board) -> list[Comment]:
    """Comments flagged as spam / rude / links that Discord hasn't been told about yet (each told once)."""
    with _lock:
        data = _load_replies(settings)
        out = [c for c in board.comments if c.result == "skip" and c.id not in data["flagged"]]
        for c in out:
            data["flagged"][c.id] = utcnow().isoformat()
        if out:
            _write(_replies_path(settings), data)
    return out


def reply_overview(settings: Settings, board: Board, today: date, now: datetime | None = None) -> dict[str, Any]:
    """Where every fan on every Reel stands, for /replies (the same keys as leaderboard.reply_overview)."""
    now = now or datetime.now(timezone.utc)
    with _lock:
        data = _load_replies(settings)
    out = {"replied": 0, "queued": len(data["pending"]), "ready": 0, "quiz_wait": 0, "unchecked": 0, "skipped": 0,
           "failed": len(data["failed"]), "next_quiz": "", "paused_until": data["pause_until"]
           if data["pause_until"] > now.isoformat() else "",
           "sent_today": data["sent"]["count"] if data["sent"]["date"] == today.isoformat() else 0,
           "daily_max": settings.instagram_reply_daily_max}
    for state, c in leaderboard._reply_state(settings, board, data, now).values():
        if state not in ("queued", "gone"):
            out[state] += 1
        if state == "quiz_wait" and (v := board.videos[c.video_id]).published:
            ready = datetime.fromisoformat(v.published.replace("Z", "+00:00")) + timedelta(
                hours=leaderboard.REPLY_AFTER_HOURS)
            out["next_quiz"] = min(out["next_quiz"] or ready.isoformat(), ready.isoformat())
    return out
