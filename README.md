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
| `trendclip/planner.py` | Plan tab: upload calendar, goals, per-platform "posted" status |
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

- **Dashboard:** click **Get gameplay** next to any game. A window asks where the clip should come
  from:
  - **🎲 Random:** a random video of that game from any of your channels, or a YouTube search when
    none of them has it.
  - **One channel:** each of your channels is listed with how many videos of that game it has (and
    how many are vertical); channels without the game are greyed out.
  - **🔎 Search YouTube…:** opens *Find more gameplay* with the game filled in, so you pick a video
    yourself.
  - **Pexels:** only available with `PEXELS_API_KEY`.

  The window also sets the clip length and orientation, and it remembers your last choice.
  Downloads run in the background and show up in the *Background gameplay* panel with a preview, a
  download link and a credit link.
- **From a link:** paste a YouTube video link (a random part of it is cut) or a channel link such as
  `https://www.youtube.com/@NoCopyrightGameplays` (a random one of its latest 300 videos, read with
  yt-dlp, no API quota) and press **Get clip**. The optional **Game** keeps only that channel's videos
  of the game and names the clip; otherwise the game is guessed from the video title. Clip length and
  orientation come from the controls above.
- **Find more gameplay on YouTube** (the card under *Background gameplay*): type a game or keywords
  (or click a trending game) and press **Search YouTube**.
  - **How it searches:** it runs three yt-dlp searches at once, so it uses no API quota: "<words>
    no copyright gameplay", "... copyright free gameplay" and "... free to use gameplay no
    commentary".
  - **What it keeps:** only videos whose title, description or channel name says no copyright /
    copyright free / free to use / royalty free. Tick **Show all results** to see everything.
  - **Get clip:** takes a clip from that video, using the length and orientation above.
  - **+ Add as source:** the channel suggestions above the results show which channels had the most
    matching videos. Adding one saves it to `assets/ncg_channels.json`, and from then on **Get
    gameplay** also picks random clips from that channel's uploads, together with `NCG_CHANNELS`.
  - **Add a channel by link:** in *Your gameplay channels*, paste a channel link
    (`https://www.youtube.com/@Name`, `/channel/UC…`, `/c/Name`), an `@handle` or a channel id and
    press **+ Add channel** (1 API unit; a plain name costs a search, 100 units). Video links are
    rejected: paste the channel's link.
  - **Removing channels:** open *Your gameplay channels* to see and remove them.
  - **Licences:** "no copyright" is the uploader's own claim. Read the description before
    publishing and credit the channel (the clip's licence note reminds you).
- **Python:** `get_background_video("Minecraft")` returns the path of `assets/backgrounds/latest_gameplay.mp4`.
  It tries Pexels first, then No-Copyright YouTube. `get_background_video_async()` returns a `Future`.
- **CLI:** `.venv/bin/python -m trendclip.video_downloader "Minecraft" --source youtube --seconds 60`

Sources:

- **Pexels** (`PEXELS_API_KEY`): searches `"<game> gameplay"` and picks a random HD/4K MP4 that
  matches the orientation. Pexels mostly has generic footage, so few results are game-specific.
- **No-Copyright YouTube** (`NCG_CHANNELS`): the uploads of these channels are listed once a day
  (1 quota unit per 50 videos, cached in `assets/cache/`) and matched to games with the same catalog.
  A random video not downloaded before is picked, and yt-dlp cuts a random segment, skipping the intro.
  Games with no channel videos fall back to the same YouTube search as *Find more gameplay*,
  keeping only results of that game that say no copyright.
  Portrait mode prefers uploads labelled *Vertical*/*9:16* (mostly Minecraft parkour). For other
  games it takes a landscape upload and crops the centre to 9:16, so every *Vertical 9:16* download
  is really vertical.

Every YouTube clip (from a game or a pasted link) starts at a random point after the **first
minute**, for example 1:45 to 2:45 for a 60 s clip, and stops at least 20 s before the end. Videos
too short for that skip as much of the start as they can, and channel picks prefer videos long
enough to skip the full minute. Clips are **not mirrored**; set `MIRROR_CLIPS = True` in
`trendclip/video_downloader.py` to flip them left to right again (the sidecar records `"mirrored"`).

Every clip gets a `.json` sidecar with its source URL, author and license note. These channels let you
use their footage, usually with credit, so check the license note before publishing and give the credit
it asks for. `assets/` is git-ignored. Needs ffmpeg (system or the bundled `imageio-ffmpeg`).

## Create tab (Gemini script → voice → Short)

The dashboard's **Create** tab turns a downloaded clip into a finished Short:

1. **Pick a background clip** (or press **Create Short** on a clip in the Trends tab).
2. **Write script with Gemini:** Gemini watches the part of the clip the Short uses (a small 480p
   copy is uploaded and deleted afterwards), reads the game's current trending video titles, and
   returns a hook + voiceover sized to the chosen length, a title, a description, hashtags and a
   **pinned comment**. Every field stays editable, and you can also type a script without Gemini.
   - **Hook (first 3 seconds):** the first sentence is always a high-stakes, surprising line or a bold
     claim with a curiosity gap ("I accidentally committed a crime at my office."). Slow openers
     ("So…", "I once…", "One day…", "Hey guys") are banned in the prompt. If Gemini still puts one
     before its hook, the app drops it, and if the hook isn't spoken at all, the app puts it first.
   - **Closing question:** the story ends with a punchy question of 2-5 words that invites viewers
     to comment their own story ("Ever done this?", "Worst boss story?"). The app enforces it: a
     longer final question is swapped for Gemini's short one, and *"What would you do?"* is the
     fallback.
   - **Follow CTA (last ~3 s):** after the question, the app adds one quick follow line as the very
     last sentence. It picks from *"Hit that subscribe button for daily side quest stories!"*,
     *"Follow Side Quest Logic so you never miss a single story!"*, *"Subscribe to see if your story
     gets featured in the next one!"* and *"Subscribe for a brand new side quest story every single
     day!"*. Each takes about 3-3.5 s with the edge-tts voice (`FOLLOW_CTAS` in
     `trendclip/script_writer.py`), the same
     script always gets the same line, and the handle comes from `CHANNEL_HANDLE`, spoken without the
     `@`. Gemini is told not to write its own, and any "follow for more" it still adds is removed. The
     script is tightened to leave room for the question and the CTA, and the subscribe sticker pops
     up when the CTA sentence starts.
   - **Pinned comment:** Gemini writes one for every Short (no links or hashtags). It is saved with the
     Short and shown in the Upload tab with **Copy comment** / **Post comment on YouTube**, so you
     can pin it right after uploading (YouTube's API can't pin, so use ⋮ → Pin).
   - **✨ Fill the rest for my story:** paste your own story in the voiceover box (or in Part 1 /
     Part 2 for Multi-part) and press this instead. Gemini keeps your words exactly and writes
     everything else: title, description, hashtags, title card, pop-ups, reaction stickers, music
     mood, closing question and pinned comment (story name too for Multi-part). Heading lines like
     *"Part One: The Call"* are removed from the voiceover. A story that fits the chosen length is
     kept word for word; a longer one is tightened by Gemini first (per part for Multi-part, capped by
     the clip length), keeping your sentences and the clues the ending needs, so the pop-ups and
     stickers match the final text. The closing question + follow CTA are added at the end (a
     question of 2-5 words you already end on is kept). If Part 2 is empty, Gemini picks the cliffhanger sentence
     where Part 2 starts (otherwise the story is split in the middle). Endpoint:
     `POST /api/create/describe`.
   **Script type → Random story** makes Gemini write a self-contained first-person storytime that is
   *not* about the game (the gameplay is only the background); the clip is not uploaded in that mode.
3. **Voice & style:** (edge-tts needs the internet; a failed request is retried for about 30 s
   before the "Voice generation failed" error, which then says when the voice server can't be
   reached) a free edge-tts voice (47 English voices) with exact word timings, speed,
   highlight colour, words per line and crop/blur layout. **Create Short** renders in the background.
4. **Background music (optional):** pick it in the **Background music** menu:
   - **By story mood** (default): automatic, strictly **chill lo-fi / quirky**. Gemini sorts every
     script into `funny_quirky` (awkward, weird, chaotic stories) or `chill_lofi` (everything else,
     including mysteries), and a random track comes from `music/funny/` or `music/chill/`. If the
     folder is empty, a free Chillhop lo-fi track is used.
   - **Your music folders**: **😱 Dramatic / horror** (`music/dramatic/`), **😂 Funny / quirky**,
     **☕ Chill lo-fi** (a random track from that folder, with its track count shown), or
     **🎲 My music** (a random track from any of your folders).
   - **Your tracks**: every file in your music folders by name, to use exactly that track.
   - **No music**, **Random** (any YouTube music channel) or one channel.

   Drop `.mp3`, `.m4a`, `.wav`, `.ogg`, `.flac` or `.aac` files into `music/funny/`,
   `music/dramatic/` or `music/chill/`; new files show up after a page reload. An optional
   `<song>.txt` next to a file holds its credit line for the description. Preview the track and press **Shuffle** for another one. Every track is first
   levelled to the same loudness and then played at 12%, 14% or 15% (Quiet/Normal/Loud), so it never
   drowns out the voice. It also loops if it is short, fades in and out, and ducks under the voice.
   Set `MUSIC_LIBRARY_DIR` to use another folder.
5. **Title card and pop-ups:** Gemini also suggests a 2-5 word ALL-CAPS title card (shown at the top
   for the first 3 seconds) and pop-up images for things the script mentions: about one every 4 s
   (11 for a 45 s Short, at most 15). Each pop-up appears for 1.5-2.5 s exactly when its word is
   spoken (edge-tts word timings), at least 2.5 s after the previous one, between the title area and
   the subtitles. Edit the title, remove pop-ups (×) or add your own word + emoji.
   **Visual pacing:** something new pops up every 3-5 s. Whenever more than 5 s would pass without a
   new pop-up or sticker, a reaction sticker from your library fills the gap, about 4 s after the
   last visual, on a spoken word.

The voiceover never outlasts the selected clip. Lengths longer than the clip are greyed out, and the
target is capped at the clip length (for Multi-part, half the clip per part). If Gemini still writes
too much, it is asked once to tighten the script; failing that, it is cut at a sentence end.

**Format** (top of the Script card):

- **Short** (15-60 s): one video, as above.
- **Long story** (60 / 75 / 90 s ≈ 200 / 250 / 300 words): a more detailed, witty script with
  escalating beats, a callback and a mini-hook every ~15 s. Works with both script types. The edge-tts
  voice speaks about 3.3 words per second, so 250-320 words needs 75-95 s. Long stories render as
  **landscape 16:9 (1920x1080)**, so YouTube treats them as regular videos, not Shorts: the title
  card, pop-ups and stickers are moved to the matching spots of the wide frame (things that sit below
  the captions on 9:16, like the subscribe sticker, go to the right side), and the captions are sized
  for 16:9. The Create preview turns wide too. Short and Multi-part stay vertical 9:16.
- **Multi-part** (40-50 s per part): one Gemini call writes a story split into **Part 1**, which ends on
  a cliffhanger, and **Part 2**, which resolves it with a twist. The app appends the calls to action
  itself (spoken and in the karaoke captions), each after the part's short closing question:
  Part 1 ends with *"What would you do? Sub to Side Quest Logic for Part 2 dropping tomorrow!"*, and
  Part 2 with *"Ever happened to you?"* plus one of the follow lines above (Gemini's own 2-5 word
  question replaces the defaults). A question that ends Part 1's story is kept as the cliffhanger. Each part also gets a yellow end banner (e.g. `PART 2 TOMORROW · SUB
  @SideQuestLogic`), "(Part 1)" / "(Part 2)" in the title, `#part1` / `#part2` hashtags, a
  "PART 1: …" title card, a description line, and a pinned comment for Part 1. Switch parts with the
  tabs to edit them. **Create Part 1 + Part 2** renders both in one job:
  `output/shorts/<StoryName>_Part1.mp4` and `_Part2.mp4`. Both parts use the same voice, music and
  clip, and Part 2 continues the gameplay and the music where Part 1 stopped. The clip loops if it
  is shorter than the voice; for long formats, download longer clips (Trends → length "whole video").
  Set your handle with `CHANNEL_HANDLE` in `.env` or the **Channel** field.

Pop-up images are free and need no key: the matching emoji from Microsoft's Fluent Emoji set (MIT)
on Wikimedia Commons, else Noto Emoji / Twemoji, else a Commons search that keeps only files with a
real transparent background. Each becomes a sticker (white outline, soft shadow) cached in
`assets/popups/`; credits are added to the description. A pop-up with no usable image is skipped.

**Reaction stickers** (`stickers/` folder, PNG / WebP / JPG / animated GIF):

- **Scan:** the folder is scanned when the server starts and again whenever a file changes. Gemini
  looks at each new image once and tags it as a *reaction* (mood: funny, awkward, shocked, approve,
  reject, proud, pain, nope, innocent, crazy, embarrassed, suspicious) or a *CTA* (subscribe / follow /
  part 2 / comment). If Gemini is unavailable, the filename is used (`oh_no.png` → shocked,
  `subscribe_*.gif` → subscribe CTA) and the next scan tries again. Tags are cached in
  `assets/cache/stickers.json`. See them under **Sticker library** in the Create tab, with
  **Rescan** and **Re-tag with Gemini** buttons.
- **Fix a tag:** add it to `stickers/stickers.json`, which always wins, e.g.
  `{"oh_no_2.png": {"moods": ["embarrassed"]}, "image.png": {"category": "off"}}`.
- **When they appear:** Gemini marks 2-5 reaction beats in the script (a word + mood, shown as chips
  you can remove). Without them, keywords in the voiceover are used ("awkward", "oh no", "no way",
  …). Each sticker pops up for 1.9 s when its word is spoken, at least 3 s apart. Extra stickers
  fill any stretch longer than 5 s without a new pop-up or sticker, using the nearby keyword's mood
  or a rotating one (funny, suspicious, shocked…). In Part 1 of a
  series, a *shocked* sticker also lands on the cliffhanger line. The CTA sticker (the folder's
  subscribe / part 2 sticker, or a built-in red **SUBSCRIBE** / **PART 2 TOMORROW** button) pulses
  above the captions from the moment the spoken CTA starts.
- **Look:** a bounce scale-in with fade-in and a 0.25 s fade-out. Animated GIFs play, and images with
  no transparency become rounded white-bordered cards. Slots: bottom-left, top-right, center-left or
  center-right. A slot is used only if it does not cover the captions, a pop-up at that moment, or the
  Shorts buttons on the right and bottom.
- **Toggles:** **Reaction stickers** and **CTA sticker**, per short or series.

Meme images are usually copyrighted. Short reaction clips are common on Shorts, but YouTube can
still flag them, so prefer stickers you made or that have a free licence.

**Edit a Short:** press **✏️ Edit** on it in *Your Shorts*. Its settings load back into the form, so you
can change the script, title, description, title card, end banner, pop-ups, stickers, voice, caption
style, music track or volume, or even the gameplay clip. Then press **Save changes**. The video is
re-rendered under the same file name and keeps its Ready status and upload history; **Cancel** brings
back the draft you had before. If the script, voice and speed are unchanged (e.g. only the music
changed), the old voiceover is reused, which is faster. Shorts made before editing existed are rebuilt
from what was saved (pop-up emojis, reaction moments and caption style fall back to defaults). An
edited Short that is already on YouTube must be uploaded again to publish the new version.

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
  description with the credits. **Upload as** picks *YouTube Short* or *Regular video*: landscape
  or longer than 3 minutes is always a regular video (no `#shorts` in the title, hashtags, tags or
  description, a normal `watch?v=` link), and you can also send a vertical video without `#shorts`
  (YouTube still decides by shape, so it may show it as a Short). Texts written for a landscape video
  (template or Gemini) leave `#shorts` out. **Upload to YouTube as a Short / as a video** uploads it (resumable, with
  progress) and shows the Shorts and Studio links. **Visibility → Schedule…** picks a date and time
  (your local time zone, quick picks like "Tomorrow 18:00"): the video uploads now as private and
  YouTube publishes it at that time (at least 15 minutes ahead).
- **Instagram** and **TikTok** (no upload API here): caption, hashtags (Instagram: at most 5) and
  @mentions in each platform's style, a ready-to-paste text with the credits, **Copy caption**,
  **Download video**, a link to the upload page and a **Posted** checkbox to keep track.
  The YouTube sub-tab has the same kind of box (**Already on YouTube**) for Shorts you uploaded in
  YouTube Studio.

**Multi-part stories:** the YouTube sub-tab shows both parts and their links. Once a part is
uploaded, the other part's description gets a "Part 1: <link>" / "Part 2: <link>" line. The pinned
comment updates too: Part 1's comment teases Part 2, then links to it once Part 2 is uploaded.
**Post comment on YouTube** posts it as your channel. The API can't pin comments, so pin it on
YouTube (⋮ → Pin). The **24 h after Part 1** quick pick schedules Part 2 for the next day. Posting
comments needs one extra permission: if you connected YouTube before this feature existed, press
Disconnect and then Connect again.

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

## Plan tab (what goes out when)

The **Plan** tab is your upload calendar. The default goals are **2 reels a day** on YouTube,
Instagram and TikTok and **2 long videos a week** on YouTube. You can change the numbers and the
platforms at the top of the tab.

- **Days:** the board shows 7 days starting with **Today**, **Tomorrow** and **Day after
  tomorrow**; **Earlier** and **Later** move it a week. Each day has an empty slot for every reel it
  still needs, plus **+ Long video**. Today's card is outlined.
- **Not planned yet:** your Shorts that aren't on a day yet. **Tomorrow** or **Day after** puts a
  Short on that day in one click. **Pick a day…** lets you choose any date. You can also drag a
  Short onto a day. Planning a Short also marks it **Ready to upload**, so it appears in the Upload
  tab.
- **Long videos and other uploads:** add them with **+ Long video**, or with an empty slot and
  "Something else". They only need a title and an optional note (time, thumbnail idea, …), because
  long videos are made outside TrendClip.
- **Marking as uploaded:** every item has a button per platform (YouTube, Instagram, TikTok).
  Click it once the video is up there and it turns coloured. When every platform is ticked, the item
  is done. For a Short, the tick is the same as the **Posted** box in the Upload tab, so ticking
  either one updates both. A YouTube upload made from the Upload tab ticks YouTube by itself; that
  button then opens the video.
- **Moving and editing:** drag an item to another day, or use **Edit** to change the day, platforms
  or notes, or to remove it. **Upload tab** opens the Short there with its captions.
- **Counters:** the top cards show:
  - today's reels posted, out of the goal;
  - how many reels are planned for tomorrow and the day after;
  - long videos posted this week and next week (Monday to Sunday);
  - anything **late** (an earlier day not posted everywhere).

  Late items are listed above the days. The number on the Plan tab shows how many uploads are left
  for today.

The plan is saved in `output/planner.json`.

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
