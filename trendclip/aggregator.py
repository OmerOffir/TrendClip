"""Turns raw trending videos into ranked topic candidates."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .models import TrendCandidate, YouTubeVideo

MAX_KEYWORDS_PER_TOPIC = 6
MAX_VIDEOS_PER_TOPIC = 5
MERGE_JACCARD = 0.6

STOPWORDS = frozenset(
    """
    a an and are as at be but by for from has have i if in into is it its it's just me my no not
    of on or our so than that the their them then there these they this to too up us was we were
    what when where which who why will with you your vs ft feat x i'm we're you're don't can't
    how all out new more most best ever first last one two get got got can now day days
    """.split()
)

# Words that describe the format rather than the subject.
GENERIC_TERMS = frozenset(
    """
    game games gaming gameplay video videos live stream streaming official trailer teaser update
    part episode ep full walkthrough playthrough guide tips tricks funny moments highlights shorts
    short clip clips reaction review reviews pc console ps4 ps5 xbox switch mobile android ios
    let's lets play playing played series season chapter edition hd 4k 60fps free tutorial
    trailers teasers reels viral fyp trending
    """.split()
)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'+]*")
_HASHTAG_RE = re.compile(r"#(\w+)")
_SEGMENT_SPLIT = re.compile(r"[|:\u2013\u2014()\[\]{}!?,\"/\\]+|\s-\s|\.\s")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace("\u2019", "'")
    return re.sub(r"\s+", " ", text).strip()


def compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(text))


def _is_meaningful_token(token: str) -> bool:
    return token not in STOPWORDS and not token.isdigit() and len(token) >= 3


def _title_phrases(title: str) -> list[str]:
    title_norm = normalize(title)
    phrases = [
        h for h in (normalize(tag) for tag in _HASHTAG_RE.findall(title_norm))
        if h not in GENERIC_TERMS and h not in STOPWORDS
    ]
    text = _HASHTAG_RE.sub(" | ", title_norm)
    for segment in _SEGMENT_SPLIT.split(text):
        tokens = _TOKEN_RE.findall(segment)
        for i, token in enumerate(tokens):
            if _is_meaningful_token(token) and token not in GENERIC_TERMS:
                phrases.append(token)
            if i + 1 < len(tokens):
                nxt = tokens[i + 1]
                if token in STOPWORDS or nxt in STOPWORDS:
                    continue
                if token in GENERIC_TERMS and nxt in GENERIC_TERMS:
                    continue
                # Keep "gta 6"-style bigrams, but require at least one real word.
                if _is_meaningful_token(token) or _is_meaningful_token(nxt):
                    phrases.append(f"{token} {nxt}")
    return phrases


def _tag_phrases(tags: list[str], channel_title: str, max_tags: int) -> list[str]:
    channel = compact(channel_title)
    phrases = []
    for tag in tags[:max_tags]:
        tag_norm = normalize(tag.lstrip("#"))
        words = tag_norm.split()
        if not (3 <= len(tag_norm) <= 40 and len(words) <= 4):
            continue
        if tag_norm in GENERIC_TERMS or all(w in STOPWORDS or w in GENERIC_TERMS for w in words):
            continue
        if tag_norm.isdigit() or compact(tag_norm) == channel:
            continue
        phrases.append(tag_norm)
    return phrases


def extract_keywords(video: YouTubeVideo, max_tags: int = 10) -> list[str]:
    """Ordered, de-duplicated candidate topic phrases for a video (tags first, then title)."""
    phrases = _tag_phrases(video.tags, video.channel_title, max_tags) + _title_phrases(video.title)
    return list(dict.fromkeys(phrases))


@dataclass
class _Topic:
    phrase: str
    keywords: list[str]
    videos: dict[str, YouTubeVideo] = field(default_factory=dict)

    @property
    def score(self) -> float:
        return sum(v.velocity_score for v in self.videos.values())


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 0.0


def build_topics(
    videos: list[YouTubeVideo], max_topics: int = 20, min_videos: int = 2
) -> list[TrendCandidate]:
    """Cluster videos by shared keywords.

    A phrase becomes a topic if it appears in >= min_videos trending videos, or in any
    outlier video. Phrases covering nearly the same videos are merged as extra keywords.
    """
    phrase_videos: dict[str, dict[str, YouTubeVideo]] = {}
    for video in videos:
        for phrase in extract_keywords(video):
            phrase_videos.setdefault(phrase, {})[video.video_id] = video

    eligible = [
        (phrase, vids)
        for phrase, vids in phrase_videos.items()
        if len(vids) >= min_videos or any(v.is_outlier for v in vids.values())
    ]
    eligible.sort(
        key=lambda pv: (
            sum(v.velocity_score for v in pv[1].values()),
            len(pv[1]),
            len(pv[0].split()),
        ),
        reverse=True,
    )

    topics: list[_Topic] = []
    for phrase, vids in eligible:
        ids = set(vids)
        match = next((t for t in topics if _jaccard(ids, set(t.videos)) >= MERGE_JACCARD), None)
        if match:
            if len(match.keywords) < MAX_KEYWORDS_PER_TOPIC:
                match.keywords.append(phrase)
            match.videos.update(vids)
        elif len(topics) < max_topics:
            topics.append(_Topic(phrase=phrase, keywords=[phrase], videos=dict(vids)))

    return [_to_candidate(t) for t in topics]


def _to_candidate(topic: _Topic) -> TrendCandidate:
    vids = sorted(topic.videos.values(), key=lambda v: v.velocity_score, reverse=True)
    return TrendCandidate(
        topic=topic.phrase,
        keywords=topic.keywords,
        score=round(topic.score, 3),
        video_count=len(vids),
        total_views=sum(v.views for v in vids),
        max_views_per_hour=max((v.views_per_hour for v in vids), default=0.0),
        regions=sorted({r for v in vids for r in v.regions}),
        videos=vids[:MAX_VIDEOS_PER_TOPIC],
    )


def rank_candidates(candidates: list[TrendCandidate], limit: int) -> list[TrendCandidate]:
    return sorted(candidates, key=lambda c: c.score, reverse=True)[:limit]
