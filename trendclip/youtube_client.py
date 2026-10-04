"""YouTube Data API v3 client: trending charts, channel sizes, and velocity/outlier flags.

Quota cost: videos.list and channels.list are 1 unit per call (vs. 100 for search.list),
so a full multi-region run typically costs < 20 units of the 10,000/day free quota.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import YouTubeVideo, utcnow

logger = logging.getLogger(__name__)

_PAGE_SIZE = 50
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


class YouTubeAPIError(RuntimeError):
    def __init__(self, message: str, reason: str | None = None, status: int | None = None):
        super().__init__(message)
        self.reason = reason
        self.status = status


class YouTubeFatalError(YouTubeAPIError):
    """Errors that will fail every subsequent call, so the run should stop."""


class YouTubeQuotaError(YouTubeFatalError):
    pass


class YouTubeAuthError(YouTubeFatalError):
    pass


_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}
_AUTH_REASONS = {"keyInvalid", "keyExpired", "accessNotConfigured", "ipRefererBlocked", "forbidden"}


class YouTubeChartUnavailable(YouTubeAPIError):
    """The mostPopular chart is not offered for this region/category combination."""


def parse_iso8601_duration(value: str | None) -> int | None:
    if not value:
        return None
    match = _DURATION_RE.match(value)
    if not match:
        return None
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _opt_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _http_error_reason(err: HttpError) -> str | None:
    try:
        payload = json.loads(err.content.decode("utf-8"))
        return payload["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, AttributeError):
        return None


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[idx]


class YouTubeClient:
    def __init__(self, api_key: str, timeout: float = 15.0):
        self._yt = build(
            "youtube",
            "v3",
            developerKey=api_key,
            http=httplib2.Http(timeout=timeout),
            cache_discovery=False,
        )

    def _execute(self, request):
        try:
            return request.execute(num_retries=3)
        except HttpError as err:
            reason = _http_error_reason(err)
            status = err.resp.status if err.resp is not None else None
            # str(err) embeds the request URL, which contains the API key.
            detail = getattr(err, "reason", None) or "unknown error"
            message = f"YouTube API error {status} ({reason}): {detail}"
            if reason in _QUOTA_REASONS:
                raise YouTubeQuotaError(message, reason, status) from err
            if reason in _AUTH_REASONS or "api key" in str(detail).lower():
                raise YouTubeAuthError(message, reason, status) from err
            if reason == "videoChartNotFound":
                raise YouTubeChartUnavailable(message, reason, status) from err
            raise YouTubeAPIError(message, reason, status) from err

    def fetch_most_popular(
        self, region: str, category_id: str | None, max_results: int
    ) -> list[dict]:
        """Raw `videos.list(chart='mostPopular')` items, paginated up to max_results."""
        items: list[dict] = []
        page_token: str | None = None
        while len(items) < max_results:
            params = {
                "part": "snippet,statistics,contentDetails",
                "chart": "mostPopular",
                "regionCode": region,
                "maxResults": min(_PAGE_SIZE, max_results - len(items)),
            }
            if category_id:
                params["videoCategoryId"] = category_id
            if page_token:
                params["pageToken"] = page_token
            response = self._execute(self._yt.videos().list(**params))
            items.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items

    def fetch_channel_subscribers(self, channel_ids: Iterable[str]) -> dict[str, int | None]:
        """channel_id -> subscriber count (None if hidden)."""
        ids = list(dict.fromkeys(channel_ids))
        result: dict[str, int | None] = {}
        for start in range(0, len(ids), _PAGE_SIZE):
            batch = ids[start : start + _PAGE_SIZE]
            response = self._execute(
                self._yt.channels().list(part="statistics", id=",".join(batch), maxResults=_PAGE_SIZE)
            )
            for item in response.get("items", []):
                stats = item.get("statistics", {})
                hidden = stats.get("hiddenSubscriberCount", False)
                result[item["id"]] = None if hidden else _opt_int(stats.get("subscriberCount"))
        return result

    @staticmethod
    def parse_video(item: dict, region: str) -> YouTubeVideo:
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        details = item.get("contentDetails", {})
        return YouTubeVideo(
            video_id=item["id"],
            title=snippet.get("title", ""),
            description=(snippet.get("description") or "")[:500],
            channel_id=snippet.get("channelId", ""),
            channel_title=snippet.get("channelTitle", ""),
            published_at=snippet["publishedAt"],
            fetched_at=utcnow(),
            category_id=snippet.get("categoryId"),
            regions=[region],
            tags=snippet.get("tags", []) or [],
            duration_seconds=parse_iso8601_duration(details.get("duration")),
            live_status=snippet.get("liveBroadcastContent", "none"),
            views=int(stats.get("viewCount", 0)),
            likes=_opt_int(stats.get("likeCount")),
            comments=_opt_int(stats.get("commentCount")),
        )

    def _trending_items_for_region(
        self, region: str, category_id: str | None, max_results: int, warnings: list[str]
    ) -> list[dict]:
        try:
            return self.fetch_most_popular(region, category_id, max_results)
        except YouTubeChartUnavailable:
            if category_id is None:
                raise
            # Some regions don't expose per-category charts; filter the overall chart instead.
            msg = (
                f"YouTube: category {category_id} chart unavailable for {region}; "
                "falling back to the overall chart filtered by category."
            )
            logger.warning(msg)
            warnings.append(msg)
            items = self.fetch_most_popular(region, None, 200)
            matching = [i for i in items if i.get("snippet", {}).get("categoryId") == category_id]
            if not matching:
                warnings.append(
                    f"YouTube: no category {category_id} videos in {region}'s overall trending chart."
                )
            return matching[:max_results]

    def resolve_channel(self, ref: str) -> dict | None:
        """Channel by id (UC...), handle (@name) or plain name -> {id, title, uploads, video_count}.

        Plain names first try the matching handle (1 unit) before search.list (100 units).
        """
        ref = ref.strip()
        explicit = ref.startswith("@") or (ref.startswith("UC") and len(ref) == 24)
        if explicit:
            params = {"forHandle": ref} if ref.startswith("@") else {"id": ref}
        else:
            # A guessed handle can belong to an unrelated empty channel, so require uploads.
            params = {"forHandle": "@" + re.sub(r"\s+", "", ref)}

        response = self._execute(
            self._yt.channels().list(part="snippet,contentDetails,statistics", **params)
        )
        for item in response.get("items", []):
            info = self._channel_info(item)
            if explicit or info["video_count"] > 0:
                return info
        if explicit:
            return None

        response = self._execute(self._yt.search().list(part="snippet", q=ref, type="channel", maxResults=1))
        items = response.get("items", [])
        return self.resolve_channel(items[0]["snippet"]["channelId"]) if items else None

    @staticmethod
    def _channel_info(item: dict) -> dict:
        return {
            "id": item["id"],
            "title": item["snippet"]["title"],
            "uploads": item["contentDetails"]["relatedPlaylists"]["uploads"],
            "video_count": int(item.get("statistics", {}).get("videoCount", 0)),
        }

    def list_uploads(self, playlist_id: str, max_items: int = 1000) -> list[dict]:
        """Newest-first uploads of a playlist: [{video_id, title, published_at}] (1 unit / 50 items)."""
        videos: list[dict] = []
        page_token: str | None = None
        while len(videos) < max_items:
            params = {"part": "snippet", "playlistId": playlist_id, "maxResults": _PAGE_SIZE}
            if page_token:
                params["pageToken"] = page_token
            response = self._execute(self._yt.playlistItems().list(**params))
            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                video_id = snippet.get("resourceId", {}).get("videoId")
                if video_id and snippet.get("title") not in ("Private video", "Deleted video"):
                    videos.append(
                        {
                            "video_id": video_id,
                            "title": snippet.get("title", ""),
                            "published_at": snippet.get("publishedAt"),
                        }
                    )
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return videos[:max_items]

    def get_categories(self, region: str) -> list[dict[str, str]]:
        """Assignable video categories for a region: [{"id": "20", "title": "Gaming"}, ...]."""
        response = self._execute(self._yt.videoCategories().list(part="snippet", regionCode=region))
        return [
            {"id": item["id"], "title": item["snippet"]["title"]}
            for item in response.get("items", [])
            if item.get("snippet", {}).get("assignable")
        ]

    def get_trending_videos(
        self,
        regions: list[str],
        category_id: str | None,
        max_results_per_region: int,
        outlier_ratio_threshold: float,
    ) -> tuple[list[YouTubeVideo], list[str]]:
        """Trending videos across regions, deduped, enriched with channel size, sorted by velocity."""
        warnings: list[str] = []
        by_id: dict[str, YouTubeVideo] = {}

        for region in regions:
            try:
                items = self._trending_items_for_region(
                    region, category_id, max_results_per_region, warnings
                )
            except YouTubeFatalError:
                raise
            except YouTubeAPIError as err:
                logger.warning("Skipping region %s: %s", region, err)
                warnings.append(f"YouTube: region {region} skipped ({err.reason or err.status})")
                continue

            logger.info("YouTube: %d trending items for region %s", len(items), region)
            for item in items:
                video = self.parse_video(item, region)
                existing = by_id.get(video.video_id)
                if existing:
                    existing.regions.append(region)
                else:
                    by_id[video.video_id] = video

        videos = list(by_id.values())
        if not videos:
            return [], warnings

        subscribers = self.fetch_channel_subscribers(v.channel_id for v in videos)
        for video in videos:
            video.channel_subscribers = subscribers.get(video.channel_id)

        flag_outliers(videos, outlier_ratio_threshold)
        videos.sort(key=lambda v: v.velocity_score, reverse=True)
        return videos, warnings


def flag_outliers(videos: list[YouTubeVideo], ratio_threshold: float, vph_percentile: float = 0.9) -> None:
    """Mark videos that out-reach their channel size or are in the top views/hour band of the batch."""
    vph_cutoff = _percentile([v.views_per_hour for v in videos], vph_percentile)
    for video in videos:
        big_vs_channel = video.outlier_ratio is not None and video.outlier_ratio >= ratio_threshold
        fast = len(videos) >= 5 and video.views_per_hour >= vph_cutoff
        video.is_outlier = big_vs_channel or fast
