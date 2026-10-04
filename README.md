# TrendClipper — Phase 1: Trend Detection

Fetches trending Gaming videos from YouTube, scores them for view velocity and channel-relative
"outlier" reach, works out **which game each video is about**, ranks the trending games, and writes
a JSON file for the Phase 2 (Claude) step. Videos that match no known game are clustered into
"emerging topics" so new releases and events still surface.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add YOUTUBE_API_KEY
```

## Run

Quickest: `./run.sh` creates `.venv`, installs dependencies (again only when `requirements.txt`
changes), creates `.env` if missing, and starts the dashboard:

```bash
./run.sh                    # web dashboard at http://127.0.0.1:8000
./run.sh web --port 8080
./run.sh cli --category 28  # one terminal run
./run.sh test
./run.sh setup              # environment only
```

`run.py` is the underlying runner; it uses `.venv` automatically (no activation needed):

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
The main section is the **Top trending games** leaderboard (sortable by momentum, views/hour, video
count or total views); it defaults to the Worldwide view (10 regions merged). Pick a region or region
group, category (loaded live from YouTube), batch size and topic count; results are cached
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
| `trendclip/games.py` | Game catalog + title/tag matching, trending-games leaderboard |
| `trendclip/aggregator.py` | Keyword extraction, emerging-topic clustering, ranking |
| `trendclip/main.py` | Orchestration + CLI |
| `trendclip/web/app.py` | FastAPI app: JSON API + cached results |
| `trendclip/web/static/` | Dashboard UI (plain HTML/CSS/JS, no build step) |

## Scoring

- `views_per_hour = views / max(age_hours, 1)`
- `outlier_ratio = views / max(subscribers, 1000)` (null if subscribers are hidden)
- `velocity_score = log10(1 + views_per_hour) * (1 + 0.5*log10(1 + outlier_ratio)) * (1 + 5*min(engagement_rate, 0.2))`
- A video `is_outlier` if `outlier_ratio >= YT_OUTLIER_RATIO` or it is in the top 10% of views/hour in the batch.
- Game / topic score ("momentum") = sum of member videos' velocity scores, so a game with many
  fast-rising videos outranks one viral clip.
- `view_share` = a game's combined views/hour as a share of all fetched videos' views/hour.

## Game detection

The YouTube API does not say which game a video is about, so `trendclip/games.py` matches titles
and the first 15 tags against a curated catalog (~150 games with nicknames, e.g. `gta 6`/`gta vi`/
`#GTA6`, `bo7`, `fortnitemares`). Title matches beat tag-only matches; a specific entry beats its
franchise (`GTA VI` over `Grand Theft Auto`, `Grow a Garden` over `Roblox`).

To add a game, append a `_g("Name", "alias", ...)` line to `CATALOG`. Avoid aliases that are common
words (`peak`, `wow`, `lol`). If a game keeps showing up under **Emerging topics**, it's missing
from the catalog.

## Background gameplay downloads

`trendclip/video_downloader.py` fetches a random gameplay clip for a game, to use as a Shorts background.

- **Dashboard:** click **Get gameplay** next to any game. Pick the source, clip length and
  orientation in the *Background gameplay* panel; downloads run in the background and show up there
  with a preview, a download link and a credit link.
- **Python:** `get_background_video("Minecraft")` returns the path of `assets/backgrounds/latest_gameplay.mp4`.
  It tries Pexels first, then No-Copyright YouTube. `get_background_video_async()` returns a `Future`.
- **CLI:** `.venv/bin/python -m trendclip.video_downloader "Minecraft" --source youtube --seconds 60`

Sources:

- **Pexels** (`PEXELS_API_KEY`): searches `"<game> gameplay"` and picks a random HD/4K MP4 that
  matches the orientation. Pexels mostly has generic footage, so few results are game-specific.
- **No-Copyright YouTube** (`NCG_CHANNELS`): the uploads of these channels are listed once a day
  (1 quota unit per 50 videos, cached in `assets/cache/`) and matched to games with the same catalog.
  A random video not downloaded before is picked, and yt-dlp cuts a random segment, skipping the intro.
  Games with no channel videos fall back to a yt-dlp search for "no copyright gameplay <game>".
  Portrait mode prefers uploads labelled *Vertical*/*9:16*.

Every clip gets a `.json` sidecar with its source URL, author and license note. These channels let you
use their footage, usually with credit, so check the license note before publishing and give the credit
it asks for. `assets/` is git-ignored. Needs ffmpeg (system or the bundled `imageio-ffmpeg`).

## Create tab (Gemini script → voice → Short)

The dashboard's **Create** tab turns a downloaded clip into a finished Short:

1. **Pick a background clip** (or press **Create Short** on a clip in the Trends tab).
2. **Write script with Gemini:** Gemini watches the part of the clip the Short uses (a small 480p
   copy is uploaded and deleted afterwards), reads the game's current trending video titles, and
   returns a hook + voiceover sized to the chosen length, a title, a description and hashtags. Every
   field stays editable, and you can also type a script without Gemini.
   **Script type → Random story** makes Gemini write a self-contained first-person storytime that is
   *not* about the game (the gameplay is only the background); the clip is not uploaded in that mode.
3. **Voice & style:** a free edge-tts voice (47 English voices) with exact word timings, speed,
   highlight colour, words per line and crop/blur layout. **Create Short** renders in the background.
4. **Background music (optional):** a random track from NoCopyrightSounds (EDM), the Audio Library
   (mixed genres) or Chillhop (lo-fi). Preview it, press **Shuffle** for another one, pick
   Quiet/Normal/Loud (Normal = 14%, about -17 dB). The music loops if it is short, fades in/out and
   ducks under the voice.
5. **Title card and pop-ups:** Gemini also suggests a 2-5 word ALL-CAPS title card (shown at the top
   for the first 3 seconds) and 3-6 pop-up images for things the script mentions. Each pop-up appears
   for 1.5-2.5 s exactly when its word is spoken (edge-tts word timings), between the title area and
   the subtitles. Edit the title, remove pop-ups (×) or add your own word + emoji.

Pop-up images are free and need no key: the matching emoji from Microsoft's Fluent Emoji set (MIT)
on Wikimedia Commons, else Noto Emoji / Twemoji, else a Commons search that keeps only files with a
real transparent background. Each becomes a sticker (white outline, soft shadow) cached in
`assets/popups/`; credits are added to the description. A pop-up with no usable image is skipped.

Shorts are saved in `output/shorts/` (MP4 + voiceover MP3 + subtitles + JSON with title,
description and hashtags); the newest is also copied to `output/final_short.mp4`. The description
gets credit lines for the gameplay channel and the music track automatically.

Music: only single songs are used (mixes, streams and compilations are filtered out); the upload
lists are cached for a day in `assets/cache/music_library.json` and tracks are downloaded once to
`assets/music/`. These channels allow use with credit, but YouTube can still show a Content ID
notice for some tracks; it is usually released after disputing with the credit link. Change the
channels with `MUSIC_CHANNELS`.

Setup: create a key at [Google AI Studio](https://aistudio.google.com/apikey) and add
`GEMINI_API_KEY=...` to `.env` (no restart needed). Optional: `GEMINI_MODEL` (default
`gemini-3.8-flash`), `TTS_VOICE`, `TTS_RATE`, `SHORT_TARGET_SECONDS`. On the free tier Google may use
prompts to improve its products and applies daily limits.

## Upload tab (YouTube / Instagram / TikTok)

In **Create → Your Shorts**, press **Ready to upload** on the videos you want to publish; they move to
the **Upload** tab (the counter on the tab shows how many). Pick one and switch between three sub-tabs:

- **YouTube:** title (`#shorts` is added), description, hashtags, search tags (450-character budget),
  visibility, "made for kids" and "altered / synthetic content", and a live preview of the final
  description with the credits. **Upload to YouTube as a Short** uploads it (resumable, with
  progress) and shows the Shorts and Studio links. **Visibility → Schedule…** picks a date and time
  (your local time zone, quick picks like "Tomorrow 18:00"): the video uploads now as private and
  YouTube publishes it at that time (at least 15 minutes ahead).
- **Instagram** and **TikTok** (no upload API here): caption, hashtags (Instagram: at most 5) and
  @mentions in each platform's style, a ready-to-paste text with the credits, **Copy caption**,
  **Download video**, a link to the upload page and a **Posted** checkbox to keep track.

Texts start from a template built from the Short; **Improve texts with Gemini** rewrites all three
for their platform (mentions only for official accounts Gemini is sure of). Edits are saved on the
Short (`output/shorts/<name>.json`).

YouTube setup (once):

1. In [Google Cloud Console](https://console.cloud.google.com/) enable **YouTube Data API v3**, create
   an OAuth client of type **Desktop app**, and save its JSON as `client_secret.json` in the project
   folder (git-ignored).
2. On the OAuth consent screen, add your Google account as a **test user**.
3. In the Upload tab press **Connect YouTube** and allow access. The token is saved to
   `token_youtube.json` (git-ignored); **Disconnect** deletes it.

Notes: an upload costs about 100 of the 10,000 daily API quota units. Projects that haven't passed
YouTube's API audit can only upload **private** videos; make them public in YouTube Studio.
Vertical videos up to 3 minutes become Shorts.

## Video assembly (karaoke Shorts)

`trendclip/video_assembler.py` turns a background clip, a voiceover and word timestamps into a
1080x1920 Short with burned-in subtitles. Lines show 2-4 words in uppercase Impact (or Montserrat /
Arial Black), white with a black outline, and the word being spoken turns yellow for its duration.

```bash
./run.sh assemble --demo                                     # test voice via macOS `say`
./run.sh assemble --voice voice.mp3 --timestamps words.json  # real input
./run.sh assemble --voice voice.mp3 --text "the script..."   # no timestamps: estimated timing
```

- Background defaults to `assets/backgrounds/latest_gameplay.mp4`; output is `output/final_short.mp4`
  plus the `.ass` file next to it.
- `--fit crop` fills the 9:16 frame and cuts the sides; `--fit blur` keeps the whole 16:9 frame over a
  blurred copy. `--highlight cyan|#RRGGBB`, `--max-words`, `--font-size`, `--position` (px from the bottom).
- The background loops if it is shorter than the voiceover; the video always ends with the voice.
- Timestamps: a plain `[{"word","start","end"}]` list, Whisper `segments[].words`, edge-tts
  `offset`/`duration`, or ElevenLabs character `alignment`.
- `--music song.mp3` (default `assets/music/background.mp3` if present, `--no-music` to skip),
  `--music-volume 0.14`, `--title-card "CAT LOGIC 101"`, `--popups popups.json`
  (`[{"word": "cat", "emoji": "🐈", "query": "cat"}]`).
- Python: `create_karaoke_ass_file(timestamps, "subs.ass", title_card=...)`,
  `assemble_video(bg, voice, timestamps, music=..., overlays=[Overlay(png, start, end)], title_card=...)`.
- Needs an ffmpeg with libass. Homebrew's default `ffmpeg` has none; the bundled `imageio-ffmpeg` has
  it and is picked automatically (or set `FFMPEG_BINARY`). Put extra fonts in `assets/fonts/`.

## API notes

- **YouTube quota:** every call used costs 1 unit (`videos.list`, `channels.list`); `search.list` (100 units) is avoided.
  If a region has no Gaming chart, the client falls back to the overall chart filtered by `categoryId`.
