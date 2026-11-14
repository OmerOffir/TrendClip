"""Borderline Why answers its viewers' comments on YouTube (built 2026-11-14, when the balls Shorts' end questions
started bringing comments in).

Every few hours the bot reads the channel's comments. Gemini, in one call for all the new ones, flags spam and rude
comments ("skip") and writes a short reply about what each viewer wrote ("12 neighbors for South Africa? Busy
borders! 🇿🇦"). Each viewer gets one reply per video, sent one at a time a few minutes apart (the Discord bot's
reply loop). Without Gemini (no key, the day's budget low, an error) a comment waits; after FALLBACK_HOURS it gets a
plain thank-you instead, unless it holds a link. A comment the channel already answered by hand is left alone.

The state lives in output/borderlinewhy/replies.json.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from modules import bw_channel
from trendclip import leaderboard, publish
from trendclip.config import Settings

logger = logging.getLogger(__name__)

MAX_AGE_DAYS = 30  # older comments get no reply
FALLBACK_HOURS = 24  # unchecked by Gemini this long: a plain thank-you
TRIES = 3
BATCH = 80
_lock = threading.Lock()
FLAG = re.compile("[\U0001F1E6-\U0001F1FF]{2}")
FALLBACK = (
    "🌍 Love it, thanks for sharing!",
    "🙌 Thanks for playing along!",
    "🔥 Great answer, thanks for watching!",
    "🗺️ Love that, thanks for commenting!",
)

PROMPT = """You are the host of Borderline Why, a family-friendly YouTube Shorts channel about geography: countries
as talking flag balls race on a map (most neighbors, longest coastline...) and a red panda hosts. Each Short ends
with a question to the viewers, shown below with the video. Read the viewers' comments and decide for each one:
- skip: spam, scams, self-promotion ("sub to me", "check my channel"), sexual content, insults, hate, harassment,
  politics that attacks a people or a country, or anything a family-friendly channel should not answer;
- reply: anything else (an answer to the question, praise, a fact, a question, a joke, an emoji).

For every reply write `reply`: what the channel answers under it. Rules:
- about what THEY wrote: react to their number, their country, their fact, or answer their question;
- one short sentence, at most 15 words, warm and playful, at most one emoji (their country's flag is welcome);
- in English unless the comment is clearly in another language;
- never "subscribe", no links, no hashtags, no @names, and don't ask them to do anything;
- don't invent facts: use only what the video below says, else just react warmly.
Return one item per comment id.

{videos}"""


class _Verdict(BaseModel):
    id: str
    verdict: Literal["reply", "skip"]
    reply: str = ""


class _Verdicts(BaseModel):
    items: list[_Verdict]


def path(out_dir: Path | None = None) -> Path:
    return bw_channel.out_root(out_dir) / "replies.json"


def load(out_dir: Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(path(out_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    for key, empty in (("comments", {}), ("verdicts", {}), ("texts", {}), ("replied", {}), ("done", {}),
                       ("failed", {}), ("flagged", {}), ("pending", []), ("sent", {"date": "", "count": 0})):
        data.setdefault(key, empty)
    data.setdefault("updated_at", "")
    return data


def save(data: dict[str, Any], out_dir: Path | None = None) -> None:
    p = path(out_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def youtube_client():
    return publish._youtube(bw_channel.TOKEN)


def can_reply() -> bool:
    return bw_channel.TOKEN.is_file() and bw_channel.can_comment()


def our_videos(out_dir: Path | None = None) -> dict[str, dict[str, str]]:
    """video id -> what Gemini should know (title, the end question, the description), from every info.json
    (old videos whose mp4 the cleanup removed too)."""
    out = {}
    root = bw_channel.out_root(out_dir)
    if not root.is_dir():
        return out
    for info_file in root.glob("*/info.json"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{4}", info_file.parent.name):
            continue
        try:
            info = json.loads(info_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        vid = ((info.get("uploads") or {}).get("youtube") or {}).get("video_id")
        if vid:
            desc = str(info.get("description") or "").split("\n\nSources:")[0].split("\n\nMaps:")[0]
            out[vid] = {"title": str(info.get("title") or ""), "question": str(info.get("comment") or ""),
                        "about": desc[:600]}
    return out


def fallback_text(comment: dict[str, Any]) -> str:
    flag = FLAG.search(comment.get("text") or "")
    text = random.Random(comment["id"]).choice(FALLBACK)
    return f"{flag.group(0)} {text[2:]}" if flag else text


def check(data: dict[str, Any], videos: dict[str, dict[str, str]], settings: Settings, client=None) -> int:
    """Gemini's verdict and reply for each comment that has none yet (each comment is checked once)."""
    from trendclip import script_writer as sw

    todo = [c for c in data["comments"].values() if c["id"] not in data["verdicts"] and not leaderboard.LINK.search(c["text"])]
    if not todo or (client is None and not sw.gemini_api_key(settings)):
        return 0
    from trendclip import gemini_quota

    if client is None and gemini_quota.low():
        logger.info("Gemini's free budget is low today; %d Borderline Why comment(s) wait", len(todo))
        return 0
    from google.genai import errors, types

    client, model = sw._gemini(settings, client)
    config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=_Verdicts, temperature=0.4)
    done = 0
    for start in range(0, len(todo), BATCH):
        part = todo[start:start + BATCH]
        blocks = []
        for vid in dict.fromkeys(c["video_id"] for c in part):
            v = videos.get(vid) or {}
            head = f"Video: {v.get('title') or vid}\nIts end question: {v.get('question') or '(none)'}\nAbout it: {v.get('about') or '-'}"
            blocks.append(head + "\n" + "\n".join(f"- id {c['id']}: {json.dumps(c['text'][:300], ensure_ascii=False)}"
                                                  for c in part if c["video_id"] == vid))
        try:
            response, model = sw._generate_with_fallback(client, model, [PROMPT.format(videos="\n\n".join(blocks))],
                                                         config, lambda f, m: None, errors.APIError)
            result = response.parsed if isinstance(response.parsed, _Verdicts) else _Verdicts.model_validate_json(response.text or "{}")
        except Exception as err:  # noqa: BLE001 - they wait for the next check (or the fallback)
            logger.warning("Gemini's Borderline Why comment check failed: %s", err)
            break
        ids = {c["id"] for c in part}
        for item in result.items:
            if item.id in ids:
                data["verdicts"][item.id] = item.verdict
                if item.verdict == "reply" and (text := leaderboard.clean_reply(item.reply)):
                    data["texts"][item.id] = text
                done += 1
    return done


def plan(data: dict[str, Any], settings: Settings, now: datetime) -> list[dict[str, Any]]:
    """Queue one reply per viewer per video (their first comment that can get one)."""
    oldest = (now - timedelta(days=MAX_AGE_DAYS)).isoformat()
    fallback_before = (now - timedelta(hours=FALLBACK_HOURS)).isoformat()
    waiting = {p["comment_id"] for p in data["pending"]}
    queued = []
    for c in sorted(data["comments"].values(), key=lambda c: c["published"]):
        key = f"{c['author_id']}|{c['video_id']}"
        if (c["published"] < oldest or key in data["done"] or c["id"] in data["replied"] or c["id"] in waiting
                or c["id"] in data["failed"] or leaderboard.LINK.search(c["text"])):
            continue
        verdict = data["verdicts"].get(c["id"])
        if verdict == "skip":
            continue
        if verdict == "reply":
            text = data["texts"].get(c["id"]) or fallback_text(c)
        elif c["published"] < fallback_before:  # Gemini never got to it
            text = fallback_text(c)
        else:
            continue
        item = {"comment_id": c["id"], "author_id": c["author_id"], "name": c["author"], "video_id": c["video_id"],
                "comment": c["text"][:200], "text": text}
        data["pending"].append(item)
        data["done"][key] = now.isoformat()
        queued.append(item)
    return queued


def refresh(settings: Settings, youtube=None, gemini=None, now: datetime | None = None,
            out_dir: Path | None = None) -> dict[str, Any]:
    """Read the comments, check the new ones, queue the replies. Returns {"new", "queued", "flagged"}."""
    now = now or datetime.now(timezone.utc)
    youtube = youtube or youtube_client()
    channel_id = youtube.channels().list(part="id", mine=True).execute()["items"][0]["id"]
    comments = leaderboard.channel_comments(youtube, channel_id)
    videos = our_videos(out_dir)
    with _lock:
        data = load(out_dir)
        data["channel_id"] = channel_id
        new = 0
        for c in comments:
            if c.id not in data["comments"]:
                new += 1
            data["comments"][c.id] = {"id": c.id, "video_id": c.video_id, "author": c.author, "author_id": c.author_id,
                                      "text": c.text, "published": c.published}
    check(data, videos, settings, gemini)  # outside the lock: Gemini takes a while
    with _lock:
        fresh = load(out_dir)
        for key in ("verdicts", "texts"):
            fresh[key].update(data[key])
        fresh["comments"].update(data["comments"])
        fresh["channel_id"] = channel_id
        flagged = [c for c in fresh["comments"].values()
                   if c["id"] not in fresh["flagged"]
                   and (fresh["verdicts"].get(c["id"]) == "skip" or leaderboard.LINK.search(c["text"]))]
        for c in flagged:
            fresh["flagged"][c["id"]] = now.isoformat()
        queued = plan(fresh, settings, now)
        fresh["updated_at"] = now.isoformat()
        save(fresh, out_dir)
    return {"new": new, "queued": queued, "flagged": flagged}


def due(hours: float, out_dir: Path | None = None, now: datetime | None = None) -> bool:
    updated = load(out_dir)["updated_at"]
    try:
        return (now or datetime.now(timezone.utc)) - datetime.fromisoformat(updated) >= timedelta(hours=hours)
    except ValueError:
        return True


def send_next(settings: Settings, today: date, youtube=None, out_dir: Path | None = None) -> dict[str, Any] | None:
    """Post the oldest queued reply (one per call); None when nothing was posted."""
    from googleapiclient.errors import HttpError

    day = today.isoformat()
    with _lock:
        data = load(out_dir)
        if data["sent"]["date"] != day:
            data["sent"] = {"date": day, "count": 0}
        if not data["pending"] or data["sent"]["count"] >= settings.youtube_reply_daily_max:
            return None
        item = data["pending"].pop(0)
        save(data, out_dir)  # off the queue first: a crash can't make it post twice
        channel_id = data.get("channel_id", "")
    youtube = youtube or youtube_client()
    posted = None
    try:
        replies = youtube.comments().list(part="snippet", parentId=item["comment_id"], maxResults=100).execute()
        if not any((r["snippet"].get("authorChannelId") or {}).get("value") == channel_id for r in replies.get("items", [])):
            body = {"snippet": {"parentId": item["comment_id"], "textOriginal": item["text"]}}
            posted = youtube.comments().insert(part="snippet", body=body).execute()
    except HttpError as err:
        status = getattr(err.resp, "status", 0)
        tries = item.get("tries", 0) + 1
        logger.warning("Borderline Why reply to %s failed (try %d): %s", item["name"], tries, err)
        with _lock:
            data = load(out_dir)
            if status not in (400, 403, 404) and tries < TRIES:
                data["pending"].append({**item, "tries": tries})
            else:
                data["failed"][item["comment_id"]] = f"HTTP {status}: {str(err)[:200]}"
                data["done"].pop(f"{item['author_id']}|{item['video_id']}", None)  # their other comment, if any
            save(data, out_dir)
        return None
    with _lock:
        data = load(out_dir)
        data["replied"][item["comment_id"]] = datetime.now(timezone.utc).isoformat()
        if posted is not None:
            if data["sent"]["date"] != day:
                data["sent"] = {"date": day, "count": 0}
            data["sent"]["count"] += 1
        save(data, out_dir)
    return item if posted is not None else None


def pending(out_dir: Path | None = None) -> int:
    with _lock:
        return len(load(out_dir)["pending"])
