# TrendClipper — Phase 1: Trend Detection

Fetches trending Gaming videos from YouTube, scores them for view velocity and channel-relative
"outlier" reach, clusters them into topics, and writes a ranked JSON file for the Phase 2 (Claude) step.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add YOUTUBE_API_KEY
```

## Run

`run.py` is the main runner; it uses `.venv` automatically (no activation needed):

```bash
python3 run.py --web                   # web dashboard at http://127.0.0.1:8000
python3 run.py                         # run the pipeline, print a summary table
python3 run.py --test                  # unit tests first, then the pipeline
python3 run.py --test-only             # unit tests only
python3 run.py --regions US,GB --top 5 # pipeline options are passed through
python3 run.py --category 28           # 28 = Science & Technology; "all" = every category
```

### Web dashboard

`python3 run.py --web` (add `--port 8080` or `--reload` as needed), then open http://127.0.0.1:8000.
Pick a region, category (loaded live from YouTube), batch size and topic count; results are cached
for 5 minutes per selection to save quota, and **Refresh** forces a new fetch. Selections are kept in
the URL, so a view like `/?region=GB&category=28` can be bookmarked.

API (also browsable at `/docs`):

- `GET /api/categories?region=US`
- `GET /api/trends?region=US&category=20&max_results=50&top=10&refresh=false`

Or call the module directly:

```bash
python -m trendclip.main                      # uses .env settings
python -m trendclip.main --regions US,GB,CA --top 15
python -m trendclip.main --stdout             # also print JSON
pytest -q
```

Output: `output/trends_<UTC timestamp>.json` and `output/latest.json`.

Exit codes: `0` ok, `2` config error, `3` YouTube quota/credential error.

## Layout

| File | Role |
| --- | --- |
| `trendclip/config.py` | `.env` loading + validated `Settings` |
| `trendclip/models.py` | Pydantic schemas; velocity/outlier metrics; output contract |
| `trendclip/youtube_client.py` | `videos.list(chart=mostPopular)`, channel sizes, outlier flags |
| `trendclip/aggregator.py` | Keyword extraction, topic clustering, ranking |
| `trendclip/main.py` | Orchestration + CLI |
| `trendclip/web/app.py` | FastAPI app: JSON API + cached results |
| `trendclip/web/static/` | Dashboard UI (plain HTML/CSS/JS, no build step) |

## Scoring

- `views_per_hour = views / max(age_hours, 1)`
- `outlier_ratio = views / max(subscribers, 1000)` (null if subscribers are hidden)
- `velocity_score = log10(1 + views_per_hour) * (1 + 0.5*log10(1 + outlier_ratio)) * (1 + 5*min(engagement_rate, 0.2))`
- A video `is_outlier` if `outlier_ratio >= YT_OUTLIER_RATIO` or it is in the top 10% of views/hour in the batch.
- Topic score = sum of member videos' velocity scores.

## API notes

- **YouTube quota:** every call used costs 1 unit (`videos.list`, `channels.list`); `search.list` (100 units) is avoided.
  If a region has no Gaming chart, the client falls back to the overall chart filtered by `categoryId`.
