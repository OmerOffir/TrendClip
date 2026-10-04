"""Pydantic schemas shared across the pipeline. The JSON output is the contract for Phase 2."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from pydantic import BaseModel, Field, computed_field

# Floors that keep brand-new videos and tiny channels from producing absurd ratios.
MIN_AGE_HOURS = 1.0
MIN_SUBSCRIBERS = 1_000

SCHEMA_VERSION = "1.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class YouTubeVideo(BaseModel):
    video_id: str
    title: str
    description: str = ""
    channel_id: str
    channel_title: str
    published_at: datetime
    fetched_at: datetime = Field(default_factory=utcnow)
    category_id: str | None = None
    regions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    duration_seconds: int | None = None
    live_status: str = "none"
    views: int = 0
    likes: int | None = None  # None when the creator hides likes
    comments: int | None = None  # None when comments are disabled
    channel_subscribers: int | None = None  # None when the subscriber count is hidden
    is_outlier: bool = False
    game: str | None = None  # primary detected game
    games: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @computed_field
    @property
    def thumbnail_url(self) -> str:
        return f"https://i.ytimg.com/vi/{self.video_id}/mqdefault.jpg"

    @computed_field
    @property
    def age_hours(self) -> float:
        hours = (self.fetched_at - self.published_at).total_seconds() / 3600
        return round(max(hours, MIN_AGE_HOURS), 2)

    @computed_field
    @property
    def views_per_hour(self) -> float:
        return round(self.views / self.age_hours, 1)

    @computed_field
    @property
    def engagement_rate(self) -> float:
        if not self.views:
            return 0.0
        return round(((self.likes or 0) + (self.comments or 0)) / self.views, 4)

    @computed_field
    @property
    def outlier_ratio(self) -> float | None:
        """Views relative to channel size; >1 means the video out-reached the channel's audience."""
        if self.channel_subscribers is None:
            return None
        return round(self.views / max(self.channel_subscribers, MIN_SUBSCRIBERS), 3)

    @computed_field
    @property
    def velocity_score(self) -> float:
        """log10(views/hour), boosted by channel-relative reach and engagement.

        Roughly: 3.0 ~ 1k views/h with neutral reach/engagement; each +1 is 10x faster.
        """
        base = math.log10(1 + self.views_per_hour)
        reach = 1.0 if self.outlier_ratio is None else 1 + 0.5 * math.log10(1 + self.outlier_ratio)
        engagement = 1 + 5 * min(self.engagement_rate, 0.2)
        return round(base * reach * engagement, 3)


class TrendCandidate(BaseModel):
    topic: str
    keywords: list[str]
    score: float
    video_count: int = 0
    total_views: int = 0
    max_views_per_hour: float = 0.0
    regions: list[str] = Field(default_factory=list)
    videos: list[YouTubeVideo] = Field(default_factory=list)


class GameTrend(BaseModel):
    name: str
    franchise: str | None = None
    score: float  # sum of member videos' velocity scores
    video_count: int
    outlier_count: int
    total_views: int
    views_per_hour: float  # combined, across this game's trending videos
    view_share: float  # share of all fetched videos' combined views/hour
    regions: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    videos: list[YouTubeVideo] = Field(default_factory=list)


class RunStats(BaseModel):
    videos_fetched: int = 0
    outlier_videos: int = 0
    games_detected: int = 0
    videos_with_game: int = 0


class PipelineResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime = Field(default_factory=utcnow)
    regions: list[str]
    category_id: str | None  # None = all categories
    stats: RunStats
    games: list[GameTrend] = Field(default_factory=list)
    # Keyword topics from videos that matched no known game (new releases, events, memes).
    candidates: list[TrendCandidate]
    outlier_videos: list[YouTubeVideo]
    videos: list[YouTubeVideo] = Field(default_factory=list)  # every fetched video, by velocity
    warnings: list[str] = Field(default_factory=list)
