"""Phase 1 entry point: fetch YouTube trending signals and write a ranked trend JSON for Phase 2.

Usage:
    python -m trendclip.main
    python -m trendclip.main --regions US,GB,CA --stdout
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import aggregator, games
from .config import ConfigError, Settings, get_settings
from .models import PipelineResult, RunStats
from .youtube_client import YouTubeClient, YouTubeFatalError

logger = logging.getLogger("trendclip")


def make_youtube_client(settings: Settings) -> YouTubeClient:
    return YouTubeClient(settings.youtube_api_key.get_secret_value(), timeout=settings.request_timeout)


def run_pipeline(settings: Settings, yt: YouTubeClient | None = None) -> PipelineResult:
    warnings: list[str] = []
    stats = RunStats()

    yt = yt or make_youtube_client(settings)
    videos, yt_warnings = yt.get_trending_videos(
        regions=settings.yt_regions,
        category_id=settings.yt_category_id,
        max_results_per_region=settings.yt_max_results_per_region,
        outlier_ratio_threshold=settings.yt_outlier_ratio,
    )
    warnings.extend(yt_warnings)
    stats.videos_fetched = len(videos)
    outliers = [v for v in videos if v.is_outlier]
    stats.outlier_videos = len(outliers)
    logger.info("YouTube: %d unique videos, %d outliers", len(videos), len(outliers))

    game_trends = games.summarize_games(videos)
    unmatched = [v for v in videos if v.game is None]
    stats.games_detected = len(game_trends)
    stats.videos_with_game = len(videos) - len(unmatched)
    logger.info("Games: %d detected across %d videos", len(game_trends), stats.videos_with_game)

    candidates = aggregator.rank_candidates(
        aggregator.build_topics(unmatched, max_topics=settings.top_topics * 2), settings.top_topics
    )

    return PipelineResult(
        regions=settings.yt_regions,
        category_id=settings.yt_category_id,
        stats=stats,
        games=game_trends,
        candidates=candidates,
        outlier_videos=outliers[:20],
        videos=videos,
        warnings=warnings,
    )


def write_result(result: PipelineResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = result.generated_at.strftime("%Y%m%dT%H%M%SZ")
    path = output_dir / f"trends_{stamp}.json"
    payload = result.model_dump_json(indent=2)
    path.write_text(payload, encoding="utf-8")
    (output_dir / "latest.json").write_text(payload, encoding="utf-8")
    return path


def format_summary(result: PipelineResult, max_games: int = 15, max_outliers: int = 5) -> str:
    s = result.stats
    lines = [
        f"\nRegions: {', '.join(result.regions)} | category: {result.category_id or 'all'} "
        f"| videos: {s.videos_fetched} | outliers: {s.outlier_videos} "
        f"| games: {s.games_detected} ({s.videos_with_game} videos matched)",
        "",
        "Top trending games:",
        f"{'#':>3}  {'score':>6}  {'vids':>4}  {'views/h':>9}  {'share':>6}  game",
    ]
    for i, g in enumerate(result.games[:max_games], 1):
        lines.append(
            f"{i:>3}  {g.score:>6.1f}  {g.video_count:>4}  {g.views_per_hour:>9,.0f}  {g.view_share:>6.1%}  {g.name}"
        )
    if result.candidates:
        lines += ["", "Emerging topics (videos not matched to a known game):"]
        for c in result.candidates:
            lines.append(f"       {c.score:>6.1f}  {c.video_count:>4}  {c.topic}")
    if result.outlier_videos:
        lines += ["", "Top outlier videos:"]
        for v in result.outlier_videos[:max_outliers]:
            ratio = f"{v.outlier_ratio:.1f}x subs" if v.outlier_ratio is not None else "subs hidden"
            game = f"[{v.game}] " if v.game else ""
            lines.append(f"  {v.views_per_hour:>9,.0f} views/h  {ratio:>12}  {game}{v.title[:60]}  {v.url}")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TrendClipper Phase 1: trend detection")
    parser.add_argument("--regions", help="Comma-separated region codes (overrides YT_REGIONS)")
    parser.add_argument("--category", help="YouTube category id, or 'all' (overrides YT_CATEGORY_ID)")
    parser.add_argument("--max-results", type=int, help="Trending videos per region (1-200)")
    parser.add_argument("--top", type=int, help="Number of topic candidates to output")
    parser.add_argument("--output-dir", type=Path, help="Where to write JSON results")
    parser.add_argument("--stdout", action="store_true", help="Also print the JSON to stdout")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        settings = get_settings().with_overrides(
            yt_regions=args.regions,
            yt_category_id=args.category,
            yt_max_results_per_region=args.max_results,
            top_topics=args.top,
            output_dir=args.output_dir,
        )
    except (ConfigError, ValueError) as err:
        logger.error("%s", err)
        return 2

    try:
        result = run_pipeline(settings)
    except YouTubeFatalError as err:
        logger.error("Aborting: %s", err)
        return 3

    path = write_result(result, settings.output_dir)
    logger.info("Wrote %d candidates to %s", len(result.candidates), path)
    for warning in result.warnings:
        logger.warning("%s", warning)
    if args.stdout:
        print(result.model_dump_json(indent=2))
    else:
        print(format_summary(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
