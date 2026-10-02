"""FastAPI dashboard: JSON API over the Phase 1 pipeline + a static single-page UI.

Run: python3 run.py --web   (or: uvicorn trendclip.web.app:app --reload)
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import ConfigError, Settings, get_settings
from ..main import make_youtube_client, run_pipeline
from ..youtube_client import YouTubeAPIError, YouTubeAuthError, YouTubeQuotaError

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# Each trends call costs ~2-5 quota units; caching keeps casual browsing nearly free.
TRENDS_TTL_SECONDS = 300
CATEGORIES_TTL_SECONDS = 24 * 3600

REGIONS: dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
    "AU": "Australia",
    "IN": "India",
    "IL": "Israel",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "IT": "Italy",
    "NL": "Netherlands",
    "SE": "Sweden",
    "PL": "Poland",
    "TR": "Turkey",
    "SA": "Saudi Arabia",
    "AE": "United Arab Emirates",
    "BR": "Brazil",
    "MX": "Mexico",
    "JP": "Japan",
    "KR": "South Korea",
}


class _TTLCache:
    def __init__(self) -> None:
        self._data: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple, ttl: float) -> Any | None:
        with self._lock:
            hit = self._data.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        return None

    def set(self, key: tuple, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)


app = FastAPI(title="TrendClipper", version=__version__)
_cache = _TTLCache()


def _base_settings() -> Settings:
    try:
        return get_settings()
    except ConfigError as err:
        raise HTTPException(status_code=500, detail=str(err)) from err


def _youtube_http_error(err: YouTubeAPIError) -> HTTPException:
    if isinstance(err, YouTubeQuotaError):
        return HTTPException(status_code=429, detail=f"YouTube quota exhausted: {err}")
    if isinstance(err, YouTubeAuthError):
        return HTTPException(status_code=502, detail=f"YouTube rejected the API key: {err}")
    return HTTPException(status_code=502, detail=str(err))


@app.get("/api/config")
def config() -> dict[str, Any]:
    settings = _base_settings()
    return {
        "version": __version__,
        "regions": REGIONS,
        "defaults": {
            "region": settings.yt_regions[0],
            "category": settings.yt_category_id or "all",
            "max_results": settings.yt_max_results_per_region,
            "top": settings.top_topics,
        },
        "cache_ttl_seconds": TRENDS_TTL_SECONDS,
    }


@app.get("/api/categories")
def categories(region: str = Query("US", pattern=r"^[A-Za-z]{2}$")) -> dict[str, Any]:
    region = region.upper()
    key = ("categories", region)
    cached = _cache.get(key, CATEGORIES_TTL_SECONDS)
    if cached is None:
        settings = _base_settings()
        try:
            cached = make_youtube_client(settings).get_categories(region)
        except YouTubeAPIError as err:
            raise _youtube_http_error(err) from err
        cached.sort(key=lambda c: c["title"])
        _cache.set(key, cached)
    return {"region": region, "categories": cached}


@app.get("/api/trends")
def trends(
    region: str = Query("US", description="Region code, or comma-separated codes"),
    category: str = Query("20", description="YouTube category id, or 'all'"),
    max_results: int = Query(50, ge=1, le=200),
    top: int = Query(10, ge=1, le=50),
    refresh: bool = Query(False, description="Bypass the cache"),
) -> Response:
    try:
        settings = _base_settings().with_overrides(
            yt_regions=region,
            yt_category_id=category,
            yt_max_results_per_region=max_results,
            top_topics=top,
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    key = ("trends", tuple(settings.yt_regions), settings.yt_category_id, max_results, top)
    cached = None if refresh else _cache.get(key, TRENDS_TTL_SECONDS)
    if cached is not None:
        return Response(cached, media_type="application/json", headers={"X-Cache": "HIT"})

    try:
        result = run_pipeline(settings)
    except YouTubeAPIError as err:
        raise _youtube_http_error(err) from err
    payload = result.model_dump_json()
    _cache.set(key, payload)
    return Response(payload, media_type="application/json", headers={"X-Cache": "MISS"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
