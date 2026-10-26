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

### Channels and secrets

TrendClip is organised per channel, so more channels can be added later. Each one gets its own
folder under `channels/`. The whole folder is gitignored and never committed.

```
.env                                # shared by every channel: YOUTUBE_API_KEY, GEMINI_API_KEY,
                                    # PEXELS_API_KEY, DISCORD_BOT_TOKEN, … and CHANNEL=sidequestlogic
client_secret.json                  # the shared Google OAuth client (any channel can connect with it)
channels/
  channel.env.example               # template (the only tracked file here)
  sidequestlogic/                   # SideQuestLogic
    channel.env                     # CHANNEL_HANDLE, STATS_* links, INSTAGRAM_ACCESS_TOKEN, DISCORD_CHANNEL_ID
    token_youtube.json              # its YouTube login (Upload → Connect YouTube)
    token_instagram.json            # its refreshed Instagram token
```

- `CHANNEL` in `.env` picks the active channel (default `sidequestlogic`).
- `PLATFORMS` (default `youtube,instagram`) is where the videos go: the plan's platforms and goals,
  the captions the bot posts, the morning channel report and the Upload tab's tabs. TikTok has no
  upload API, so it is off by default; `PLATFORMS=youtube,instagram,tiktok` brings its captions, tab,
  stats and plan buttons back. A platform switched off disappears from existing plans on load.

- Settings in `channel.env` win over `.env`, so any setting (`TTS_VOICE`, `MUSIC_VOLUME`, …) can
  differ per channel. Real environment variables win over both.
- A channel may also have its own `client_secret.json` in its folder.
- New channel: create `channels/<name>/`, copy `channels/channel.env.example` there as
  `channel.env`, fill it in, set `CHANNEL=<name>` and connect YouTube again for that channel.
- Videos, the plan and stickers are still shared for now.

## Run

Quickest: `./run.sh` creates `.venv`, installs dependencies (again only when `requirements.txt`
changes), creates `.env` if missing, and starts the dashboard:

```bash
./run.sh                    # web dashboard at http://127.0.0.1:8000 + the Discord bot (if configured)
./run.sh web --port 8080    # NO_BOT=1 ./run.sh web skips the bot
./run.sh bot                # only the Discord bot (see "Discord bot" below)
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
| `trendclip/analytics.py` | Insights tab: every published video's numbers (YouTube Data + Analytics API) grouped by format, kind, game, hook style, length and slot |
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

   **Questions → 🧮 Math challenge / 🧩 Riddle** (also under Script type) make a ~20–25 s interactive
   quiz that viewers answer in the comments. Frame 0 is a large pulsing dare ("ONLY 5% OF PEOPLE FINISH
   THIS") and the voice starts on that same line, with no soft intro. A math challenge then reads a fast
   chain built to be hard ("Start with 18. Add 16. Subtract 26. Times 4. Add half of it. Divide by half.
   Divide by 6. Multiply by 4.") and ends with "Got your number? Three. Two. One. Most people get this
   wrong. Comment yours!". The answer is never spoken or written on screen. TrendClip picks the numbers
   itself: awkward numbers (no multiples of 5 or 10), ×3–×9 and ÷3–÷9, never a step that undoes the one
   before, and two trick steps per chain ("Divide by half" is ×2 and most people halve; "Add half of it",
   "Take away a third of it", "Square it", "Double it"). Whole numbers only, so the answer is always
   right; the creator view shows the working and the number most people get. There is no spoken hint
   (the digit count is in the pinned comment). Gemini writes the hook, title (with an emoji) and hashtags; if Gemini is busy, built-in
   texts are used. A riddle is a hook, 2–3 clues, the question, then a hint that is not the answer.
   Each number or clue (`24`, `+18`, `÷2`, `CLUE 1`) flashes big in the middle while it is spoken, and
   the end shows **COMMENT YOUR ANSWER** (riddles also flash **HINT**). The pinned comment is the question plus a hint
   ("What number did you get? 👇 / Hint: it's two digits."). The real answer stays in the creator view
   and in Discord as a spoiler. Reaction stickers are skipped on math so nothing covers the numbers.

   **🌍 Trivia trap** (gamified fact trap, 12–15 s) builds a speed quiz on a counter-intuitive but
   true fact from science, geography, history, animals, space, food or pop culture. The banner is a
   challenge like "90% GET THIS TRIVIA TRAP WRONG!". The voiceover goes: "Which country has the most
   islands? Is it Indonesia? The Philippines? Or Sweden? Three. Two. One. Did you guess it right? Drop
   your answer below!". The last line flows back into the question when YouTube replays the Short. The
   correct option is never named after the choices. Each option (`A) Sweden`) and the countdown flash on
   screen, then a green **GOT IT?** with **DROP YOUR ANSWER BELOW!** on top (never the answer, so the
   comments and the leaderboard stay a real game). Gemini picks the trap most people would choose, a
   distractor and the surprising answer, and only a question that passes a Google fact-check is used.
   The pinned comment is the question plus a hint, not the answer. **Fill the rest for my quiz** works for your own math, riddle or trivia text too: it
   keeps your words, removes any subscribe line and ends on the comment line.

   **🎬 Movie quiz** (15–25 s) is built on a famous movie from any year. It takes turns between three
   shapes; notes like "emoji", "quote" or "movie logic" pick one.
   - **Misquote trap:** "What did Darth Vader ACTUALLY say to Luke? Option A: Luke, I am your father.
     Option B: No, I am your father. Option C: I am your father, Luke. Think fast! Three. Two. One. Only
     one is the real line. Comment A, B or C right now!" Gemini writes 3 candidates and checks each line
     on Google, and only a line that passes is used. **A**, **B** and **C** flash on screen.
   - **Guess the movie from 3 emojis** (12–15 s): "Name this movie in five seconds! Balloons. Flying
     house. Grumpy old man. Hint: it won two Oscars! Three. Two. One. Did you guess it right? Drop your
     answer below!" The three emojis pop up back to back (all on screen by about 4 s) and stay up side by
     side; the countdown flashes above them; the ending is the same green **GOT IT?** as the trivia trap.
     The hint is dropped when it would push the Short past 15 s. The title is never spoken or shown.
   - **Movie logic debate**, shaped like the paradox trap: "If Marty McFly stops his parents from
     meeting, does he vanish at once or get time to fix it?" Viewers comment one word for their side.

   As with the other quizzes, the answer stays off the video and goes into the pinned comment and Discord
   (as a spoiler), so comments score on the leaderboard. Movies already used are listed in
   `output/movie_history.json` and are not picked again.

   **Background that fits:** when the bot makes a quiz on random gameplay (`/create` without `game:`,
   the automatic videos), it writes the quiz first, then Gemini picks the game whose world matches it
   (a war movie over a shooter, space trivia over a space game) or, when nothing matches, a neutral,
   fast, high-contrast one; that game's gameplay is downloaded. A quiz about the gameplay's own game
   keeps it.

   **Viral Shorts (length up to 30 s, spoken as 18–22 s):** one story in four beats. 0–3 s is a shock
   hook already in the middle of the action; 3–12 s the problem gets about twice as bad; 12–18 s is
   the twist; 18–22 s is an unfinished sentence that flows back into the first line, so the replay
   feels seamless. Tone is conversational, fast and sensory. The comment question ("Was I wrong?")
   goes in the pinned comment, not the voiceover, and there is no spoken subscribe line: the subscribe
   sticker pops up for the last 3 seconds. 45 s and 60 s Shorts keep the spoken question and the
   "Follow …" ending.
   **Long videos** use the same four beats stretched across the chosen length (three escalations, a
   twist, then the replay loop) instead of a spoken question and follow line.
3. **Voice & style:** (edge-tts needs the internet; a failed request is retried for about 30 s
   before the "Voice generation failed" error, which then says when the voice server can't be
   reached. A 403 from Microsoft, which is what Europe and Israel get from the old Edge voice
   call, switches to F5-TTS on the NVIDIA GPU instead — no VPN. Install it once with
   `requirements-gpu.txt`. The first video downloads the model. It copies the Edge recording in
   `trendclip/voices/ref.wav`, so the speaker stays close to the Edge voice. Machines Microsoft
   still accepts keep the Edge voice.) a free edge-tts
   voice (47 English voices) with exact word timings, speed,
   highlight colour, words per line, crop/blur layout and **Gameplay speed** (×1 / ×1.25 / ×1.5: only the
   background footage plays faster, the voice and music keep their speed). Trend tab downloads are 1.5× the chosen clip length (30 s → 45 s),
   so a clip still covers the full video at ×1.5. **Create Short** renders in the background.
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
  Set your handle with `CHANNEL_HANDLE` in the channel's `channel.env` or the **Channel** field.

Pop-up images are free and need no key: the matching emoji from Microsoft's Fluent Emoji set (MIT)
on Wikimedia Commons, else Noto Emoji / Twemoji, else a Commons search that keeps only files with a
real transparent background. Each becomes a sticker (white outline, soft shadow) cached in
`assets/popups/`; credits are added to the description. A pop-up with no usable image is skipped.

**Reaction stickers** (`stickers/` folder, PNG / WebP / JPG / animated GIF):

- **Scan:** the folder is scanned when the server starts and again whenever a file changes. Gemini
  looks at each new image once and tags it as a *reaction* (mood: funny, awkward, shocked, approve,
  reject, proud, pain, nope, innocent, crazy, embarrassed, suspicious, thinking) or a *CTA* (subscribe / follow /
  part 2 / comment). If Gemini is unavailable, the filename is used (`oh_no.png` → shocked,
  `subscribe_*.gif` → subscribe CTA) and the next scan tries again. For an animated GIF, Gemini sees
  4 frames from start to end, so it tags what the whole animation shows. Tags are cached in
  `assets/cache/stickers.json`. See them under **Sticker library** in the Create tab, with
  **Rescan** and **Re-tag with Gemini** buttons.
- **Quizzes** (every kind): their reactions come only from the *thinking* stickers, at least 3 per
  video and bigger (420 px instead of 300), in the upper corners or beside the flashes, never over them:
  one on the "Three. Two. One." countdown when it fits, the rest every 3-5 s after the title banner. To add one, drop the GIF in
  `stickers/` and tag it `{"moods": ["thinking"]}` (files named think… / calculating… get the tag on their own).
- **Fix a tag:** add it to `stickers/stickers.json`, which always wins, e.g.
  `{"oh_no_2.png": {"moods": ["embarrassed"]}, "image.png": {"category": "off"}}`.
- **When they appear:** Gemini marks 2-5 reaction beats in the script (a word + mood, shown as chips
  you can remove). Without them, keywords in the voiceover are used ("awkward", "oh no", "no way",
  …). Each sticker pops up for 1.9 s when its word is spoken, at least 3 s apart. An animated GIF
  stays until its animation has played once (up to 3.2 s), unless the next sticker or the CTA starts
  first. Extra stickers
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
  (your local time zone, quick picks like "Tomorrow 14:30"): the video uploads now as private and
  YouTube publishes it at that time (at least 15 minutes ahead).
- **Instagram**: caption, hashtags (at most 5) and @mentions, and the ready-to-paste text with the
  credits. **Post on Instagram** posts the Short as a Reel (also shown in the feed) with that text,
  and can post the pinned comment under it as the first comment (Instagram's API can't pin it:
  pin it in the app). The Reel link is saved on the Short and the Plan tab counts it as posted. In
  Discord, a video's buttons include **Post on Instagram** (it asks you to confirm first), and after a
  YouTube upload there is a **Post on Instagram too** button.
  It uses the same `INSTAGRAM_ACCESS_TOKEN` as the channel report (see *Channel report* below). The
  token needs the `instagram_business_content_publish` permission, plus
  `instagram_business_manage_comments` for the first comment. Limits: Reels from 3 s to 15 min,
  up to 300 MB, and 100 API posts a day. Copy caption / Download video still work for posting by hand.
  This API only takes videos from a web link, so while posting, TrendClip serves a lighter copy of the
  video (about 3.5 Mbit/s, in `output/instagram/`) from your machine at a secret temporary link through
  a free Cloudflare quick tunnel, and closes it as soon as Instagram has the video (about 2 minutes in
  all). Nothing else on the machine is reachable through it. Install `cloudflared` once on each
  machine that posts: macOS `brew install cloudflared`; Debian/Ubuntu:
  `curl -LO https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i cloudflared-linux-amd64.deb`
  (`-arm64.deb` on ARM).
- **TikTok** (no upload API here): caption, hashtags and @mentions in TikTok's style, a
  ready-to-paste text with the credits, **Copy caption**, **Download video**, a link to the upload
  page and a **Posted** checkbox to keep track.
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
   folder (git-ignored, shared by all channels), or in `channels/<channel>/` for one channel only.
2. On the OAuth consent screen, add your Google account as a **test user**.
3. In the Upload tab press **Connect YouTube** and allow access. The token is saved to the channel's
   folder as `channels/<channel>/token_youtube.json` (git-ignored). **Disconnect** deletes it.

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
- **Upload times:** a day has two upload slots, **14:30** and **23:00** (in `TIMEZONE`, default
  Asia/Jerusalem). The empty slots of a day are labelled **+ 14:30 reel** / **+ 23:00 reel**, and the
  dialog has an **Upload time** field. Each slot holds one item; an item can also have no fixed time.
- **Moving and editing:** drag an item to another day, or use **Edit** to change the day, time,
  platforms or notes, or to remove it. If you drag an item onto a day where its time is already taken,
  it keeps no fixed time. **Upload tab** opens the Short there with its captions.
- **Counters:** the top cards show:
  - today's reels posted, out of the goal;
  - how many reels are planned for tomorrow and the day after;
  - long videos posted this week and next week (Monday to Sunday);
  - anything **late** (an earlier day not posted everywhere).

  Late items are listed above the days. The number on the Plan tab shows how many uploads are left
  for today.

The plan is saved in `output/planner.json`.

### Two machines, one plan

When Shorts are made on two computers, one of them is the **hub**: the always-on Linux box. It
keeps the plan and runs the Discord bot. The other machine sends its finished Shorts there
through Discord, so the two don't need to be on the same network.

- Make a private Discord channel (e.g. `#hub`) the bot can read and write, and copy its id.
- Both `.env` files: the same `DISCORD_BOT_TOKEN` and `HUB_CHANNEL_ID=<that id>`.
- The hub: `HUB_ROLE=hub`. The other machine: `HUB_ROLE=sender`.

Each Short on the sender then gets a **📤 Send to Linux** button. It asks the hub (a message in
the hub channel) for the upload times still open on its plan. Sending posts the video in 8 MB
pieces (Discord's file limit is 10 MB) plus its details; the hub joins them, refuses a time that
is already taken (also if it was taken in the meantime) with the free ones, or saves the Short,
marks it ready, plans it at that time and says so in the reminders channel. The transfer
messages are deleted afterwards.

The sender only uses Discord's REST API with the bot token, never logs in as the bot, and
`run.sh` doesn't start the bot there, so NPC_Steve answers from the hub only.

## Discord bot

`./run.sh` (the dashboard) starts the bot along with it whenever `DISCORD_BOT_TOKEN` is set; it logs to
`output/discord_bot.log` and stops with the dashboard. `./run.sh bot` runs only the bot. Only one bot
runs at a time. Control TrendClip from your Discord server with slash commands.

| Command | What it does |
|---------|--------------|
| `/today` | Today's 14:30 and 23:00 videos, with **Show** and **Upload** buttons, plus a copy-ready caption (text and hashtags) for YouTube, TikTok and Instagram |
| `/slot time:14:30` | Sends the video planned for that slot (`day:` for another day) |
| `/insights days:30` | Which kinds of video, games, hook styles, lengths and upload times get the most views (and retention), see **Insights** below |

| `/create` | Makes a random video: fresh random no-copyright gameplay, a random Gemini story, voice, music by mood, pop-ups and stickers. Then it posts the video with **Plan today 14:30 / 23:00**, **Plan next free slot**, **Upload to YouTube now** and **Delete** buttons. It first asks with buttons: **📖 Story**, **🧮 Math challenge**, **🧩 Riddle**, **🌍 Trivia trap**, **🌀 Paradox trap** or **🎬 Movie quiz** (quizzes show the answer as a spoiler); `type:` skips the question and `game:` picks the gameplay. The **Create a random video** buttons in reminders ask the same. **Your own text:** paste it as `text:` (one line, up to 3,000 characters) or press **✍️ Use my own text** for a multi-line box. Then pick story, math, riddle or trivia. Like **Fill the rest** in the dashboard, your words are the voiceover and Gemini writes the title, hashtags, banner and pinned comment; quizzes get their numbers or options flashed on screen and no subscribe line. |
| `/plan show` · `/plan add` · `/plan remove` · `/plan move` | The upload plan. `add` without a day takes the next free slot |
| `/upload` | Publishes a video **publicly** on YouTube now, after a confirmation button (default: the next video planned today). Afterwards, **Post the pinned comment** posts the comment (pin it in YouTube) |
| `/videos` | Videos that are made but not planned or uploaded |
| `/status` | YouTube connection, Gemini key, next free slot, today's plan |
| `/stats` | The channel report (below) right now |

On its own the bot posts to `DISCORD_CHANNEL_ID`:

- a morning summary of the day's plan at `DISCORD_MORNING_TIME`, followed by the channel report;
- each slot's video `DISCORD_REMINDER_MINUTES` before 14:30 and 23:00, with an **Upload now**
  button. An empty slot gets a **Create a random video** button instead;
- **at 14:30 and 23:00, the planned Short goes out by itself** (`AUTO_POST=true`) on the platforms
  of its plan item: YouTube (public, with the pinned comment posted; pin it yourself, the API can't)
  and Instagram (as a Reel, with its first comment). The channel gets a "posting now" message, live
  progress, then the links. If a platform fails, you get the error and a **Retry** button. A
  platform it's already on is skipped. TikTok has no upload API, so the message reminds you to post
  it there. If the bot was off at the slot time, it still posts when it's back, up to 2 h late.
  Items made outside TrendClip, and videos no longer on the machine, are only reported;
- **the production run** (`AUTO_PRODUCE=true`, at `AUTO_PRODUCE_TIME`, default 10:15): every morning
  the bot makes a video for each empty slot of tomorrow (and of today when the slot is still 3+ hours
  away), one at a time. The kind, game and length come from the channel's numbers (**Insights**, the
  same picker as 🎲 Pick for me) and the script gets the learning brief; the bot says what it picked and
  why. Each video is planned in its slot and posted to Discord with **🚫 Don't post this**: press nothing
  and it goes out at its time by itself (`AUTO_POST`); press it and the video comes off the plan (it stays
  in /videos) with a **🎬 Make another now** button, or the emergency video below fills the slot an hour
  before. A video that fails 5 times is left to the emergency video, with a **Try again now** button.
  This is the whole channel on autopilot: your daily effort is a glance at two videos, and only if you
  want to.
- **an emergency video for an empty slot** (`EMERGENCY_VIDEO=true`): if 14:30 or 23:00 still has

  nothing planned an hour before, the bot makes a video over GTA V or Minecraft gameplay (never
  another game, even when the download fails and it falls back to a downloaded clip) with a random
  type (math, trivia, riddle, movie, paradox or story), plans it in that slot, and it goes out at its
  time. If it finishes after the slot time, it's posted right away. If you fill the slot yourself
  meanwhile, the emergency video waits in `/videos` instead. A failed attempt (an ffmpeg error, a bad
  clip, Gemini busy) is made again from scratch until it works (up to 10 attempts), unless the slot gets
  a video meanwhile. "Create a random video" tries up to 3 times the same way.

Setup:

1. https://discord.com/developers/applications → **New Application** → **Bot** → **Reset Token**,
   and put the token in `.env` as `DISCORD_BOT_TOKEN`.
2. **OAuth2 → URL Generator**: tick the `bot` and `applications.commands` scopes, then the bot
   permissions *Send Messages*, *Attach Files*, *Embed Links* and *Read Message History*. Open the
   generated link and add the bot to your server.
3. In Discord, go to **Settings → Advanced** and turn on **Developer Mode**. Right-click your server →
   **Copy Server ID** and put it in `DISCORD_GUILD_ID`. Right-click the reminder channel → **Copy
   Channel ID** and put it in `channels/<channel>/channel.env` as `DISCORD_CHANNEL_ID`. Optional:
   `DISCORD_ALLOWED_USERS=<your user id>` in `.env` lets only you use the bot.
4. Restart `./run.sh` (or run `./run.sh bot`). The commands show up on the server right away.

Uploading needs YouTube connected once from the dashboard (**Upload → Connect YouTube**). The bot
uses the same `channels/<channel>/token_youtube.json`.

Videos bigger than the server's upload limit (10 MB without boosts) are sent as a smaller copy,
cached in `output/discord/`. Your original file is never changed.

Which reminders have already gone out is stored in `output/discord_state.json`, so restarting the bot
doesn't send them again.

### Insights (what works)

The channel only grows from what the numbers say, so every published video is tracked in
`output/analytics.db` together with how it was made, and the numbers are read from YouTube once a day
(the morning summary) or on demand (**Insights** tab → **Read YouTube now**, `/insights`,
`python -m trendclip.analytics --refresh`).

- **What is tracked:** for every Short uploaded from here, its format (Short / Multi-part / Long 16:9),
  kind of script (story, math challenge, riddle, trivia trap, paradox trap, movie quiz — recorded on new
  Shorts, read from the rendered flashes and title for older ones), game, the **hook** (the first spoken
  sentence) and its style (dare "Only 5% finish this", question, number, quote, command, first person,
  bold claim), length bucket, upload slot (14:30 / 23:00 or the hour) and music mood. Videos the channel
  published without TrendClip (by hand, or from the other machine before its sidecar arrived) are tracked
  too, with their title and time only, so the channel totals are complete.
- **Numbers:** views, likes and comments from the Data API every day (1 quota unit per 50 videos), kept
  as a daily snapshot per video, so the report can show **day 1 → day 7** views once a video has been
  tracked from its first day. With the **YouTube Analytics** permission, each video also gets its watch
  time, **average view duration / percentage** (retention: the number Shorts live and die by), shares
  and subscribers gained. Analytics numbers run about two days behind. Logins made before this existed
  lack the permission: Upload tab → **Disconnect** → **Connect YouTube** once more (the Upload tab says
  so), and enable **YouTube Analytics API** in the Google Cloud project if the refresh says it is off.
- **The report** (`/insights days:30`, the Insights tab with 30 / 90 / 365 days): the videos of the
  window, grouped by each attribute and ranked by **median views** (so one viral hit doesn't crown a
  group), each with its views per day since upload, average view %, likes and comments per 1,000 views,
  how it compares to the channel median (1.4× / 0.6×) and its best video. Then the top videos, the top
  retention, and the **hooks that pulled the most views** with their style, to reuse. Videos younger
  than 2 days are listed but not ranked. The morning summary posts the best kind, game and hook style of
  the last 30 days; `/insights` posts the whole report.
- **The bot learns from it** (`AUTO_PICK=true`): 🎲 **Pick for me** and the emergency videos choose the
  kind of video, the game and (stories) the length by these numbers instead of fixed weights. Each
  choice's chance is its median views against the channel median, moved up to ±30% by its retention and
  shrunk toward average by 3 pseudo-videos, so one lucky video doesn't take over; no choice drops below
  15% of an average one. `AUTO_PICK_EXPLORE` (default 0.2) of the time a dimension is drawn at random,
  so untried kinds and games get their turn; `AUTO_PICK_DAYS` (default 60) is the window. For the game,
  the channel's proven games are the main pool (and get up to +50% when they are trending on YouTube
  right now), while the exploration share goes to games never published, weighted by their current
  momentum from the trend pipeline (`AUTO_PICK_TRENDS`, `YT_REGIONS`; `output/latest.json` is reused
  for 12 hours), so a new game is tried when people are watching it, not at random. `AUTO_PICK_KINDS`
  limits the kinds it may choose (a quiz-only channel: `math,riddle,trivia,paradox,movie`). The bot says


  what it picked and why ("Math challenge (1.6× the channel median over 9 videos, 93% watched) on
  Minecraft (trying it out)"), and the Insights tab shows the current chances under **What the bot
  picks next**.
- **The scripts learn from it too** (`SCRIPT_LEARNING=true`): every story and quiz prompt ends with a
  brief built from the same numbers for that kind of video (all kinds when fewer than 3 of it have
  retention): the five hooks that kept viewers watching, the hooks that lost them, how each hook style
  does ("Question hooks: 61% watched … loses viewers: a question works only when its answer lands within
  the first 5 seconds"), the length that gets the views, the closing lines that drew the most comments
  per 1,000 views, and the titles published in the last 30 days so Gemini writes something new. It is
  evidence of what the audience responds to, not text to copy. See it under **What Gemini is told** in
  the Insights tab or with `python -m trendclip.analytics --brief math`. Creator's notes still win.



### Channel report

Every morning, after the plan, and whenever you run `/stats`, the bot posts one message per platform:

- **Followers / subscribers**, plus total views (and TikTok's total likes). From the second day on,
  each shows its change since yesterday's report.
- **Every video posted in the last 24 hours**, with its link, posting time, views, likes and comments
  (TikTok also shares). It also gives the sum for those videos, and a ⭐ on the best one.

The accounts are `STATS_YOUTUBE`, `STATS_TIKTOK` and `STATS_INSTAGRAM` in the channel's
`channel.env`. Links or handles both work. They default to the SideQuestLogic accounts. Snapshots for the changes are kept in
`assets/cache/channel_stats.json`.

- **YouTube:** the Data API. When YouTube is connected in the dashboard, private and scheduled
  videos count too. Otherwise only public ones (the API key).
- **TikTok:** the public profile and video list. No login is needed.
- **Instagram** blocks logged-out requests most of the time, so it needs an access token from the
  official Instagram API:
  1. Switch the account to **Professional** (Creator or Business): Instagram app → Settings →
     Account type and tools.
  2. https://developers.facebook.com/apps → **Create app** → use case *Manage messaging & content on
     Instagram* (the "Instagram API with Instagram login" product).
  3. In the app: **Instagram → API setup with Instagram login → Generate access tokens** → **Add
     account** and log in as `sidequestlogic`. It needs the `instagram_business_basic` and
     `instagram_business_manage_insights` permissions (and `instagram_business_content_publish` +
     `instagram_business_manage_comments` to post Reels from the Upload tab). Then copy the token.
  4. Put it in `channels/sidequestlogic/channel.env` as `INSTAGRAM_ACCESS_TOKEN=` and restart the
     bot. The 60-day token is refreshed every week by itself and kept next to it in
     `token_instagram.json` (gitignored, never committed).

  Without a token the report still tries Instagram's public profile data. When Instagram refuses,
  it says so and the other platforms are reported as usual.

### Comments leaderboard

Every morning after the report, the bot reads every top-level comment on the channel's videos (the
channel's own comments are skipped). It posts the new ones since yesterday with their points, and the
top 10. Everything is kept in `output/comments.json` on the bot's machine. Commands:

- `/leaderboard` everyone ranked this month (`refresh:True` reads YouTube again right now,
  `last_month:True` last month's final standings plus a champions post to copy).
- `/comments video:` every comment on one video, with its result and points.
- `/points name:` one fan's total and comments.
- `/answer video: answer: choices:` the quiz answer for a video this machine didn't make (choices like
  `A) Rome | B) Paris`; for a paradox, only the two sides). Quizzes made here are known by themselves.

Points per person and video (only their best comment on a video counts, so spamming or guessing every
option doesn't pay): the first correct answer 10, another correct answer or a paradox vote 3, any other
comment or try 1. There are no subscriber points: you must be subscribed to win a prize (⭐ in Discord).
YouTube only shows subscribers who keep their subscriptions public, so the bot warns you about podium fans
it can't see as subscribed.

**The 3-video rule:** a fan who doesn't comment on 3 videos in a row loses 50% of their points (an odd
half is rounded in their favour). A video counts as missed once it is 24 hours old without their comment,
and a late comment still counts. It only applies once they have points this month. `/points` shows what
was lost.
It uses the YouTube login from the Upload tab, so there is nothing else to set up.

**The leaderboard resets every month.** Only comments posted since midnight on the 1st (`TIMEZONE`)
count, so everyone starts from 0. Older comments stay stored. On the 1st, the daily leaderboard post
starts with last month's final podium ("👑 SEPTEMBER 2026 CHAMPIONS 👑 …", with the prizes and how to
claim them) to copy into a YouTube and Instagram post.
The picture says "OCTOBER LEADERBOARD", and the caption says the board resets on the 1st. The bot's
replies give the fan's rank and points for this month.

Every day at `DISCORD_LEADERBOARD_TIME` (default 13:30, empty = off) the bot sends a podium picture of
the top 13 and a caption that tags all of them. Any time, `/leaderboard image:True` sends both and
`/leaderboard post:True` just the caption; its "Next trap drops…" line follows the next upload time. YouTube's
API can't create posts, so post it yourself: save the picture, copy the text, YouTube → Create → Post.
Put the channel logo in `channels/<channel>/logo.png` and it's drawn on the picture.

Every fan gets exactly one YouTube reply per video (YouTube always notifies about replies, unlike tags
in posts), under the comment that scored: "FIRST & CORRECT! ⚡ +10 pts…", "BOOM! Correct answer! +3 pts…",
"Ouch! You fell for the trap! 🪤 +1 pt for trying…", "🗳️ Vote counted!…" or "🙌 Thanks for commenting!…",
with the current rank. Their other comments on that video get nothing more, unless the scoring one
can't be replied to (deleted): then another of theirs gets the one reply. Scoring comments go out first. Quiz and paradox videos wait until they're 10 h old, so the replies don't
give the answer away. Gemini double-checks quiz answers (typos, synonyms, other languages); "wrong" is only
said when Gemini agrees, otherwise the plain +1 pt text. Never a second reply to a fan on a video, nor to a comment the
channel already answered. Replies go out one every 2 minutes, with varied wording and no links, up to
`YOUTUBE_REPLY_DAILY_MAX` a day (default 100). Each costs 50 of the 10,000 daily YouTube API units, and an
upload ~1,600; the rest wait for the next day. A reply YouTube refuses for a moment is tried 3 times. If
the comment was deleted or comments are off, it's dropped. Comments older than 30 days are left alone.

Comments with links, and those Gemini flags as spam, scams, self-promotion or rude, get no reply and no
points; the bot lists them in Discord once, with a link, so you can remove them. With a Gemini key, a
comment Gemini hasn't checked yet (Gemini busy) waits for the next read. Without a key, everything except
links is replied to.

The bot reads the comments in the morning, at 13:30, and every `YOUTUBE_REPLY_CHECK_HOURS` (default 3; 0
turns it off), and says in Discord how many replies it queued. **`/replies`** does it now: it reads the
comments, queues a reply to every unanswered one, and sends the first right away. Then it shows the
counts: replied, in the queue, waiting for the quiz 10 h (and when the next one opens), not checked yet,
spam, refused, and sent today. **`/replies now:True`** also replies on quiz and paradox videos younger
than 10 h, on YouTube and Instagram (those replies say who was right, so they show the answer).
`YOUTUBE_AUTO_REPLIES=false` turns replies off; the record is
`output/comment_replies.json`.

### Instagram replies

The bot replies to fans on your Instagram Reels too (`ig_replies.py`), with the same rules as on
YouTube:
- one reply per fan per Reel, under their best comment;
- no reply to spam, links or rude comments (listed in Discord with the Reel's link);
- quiz and paradox Reels wait 10 h;
- never a second reply, and nothing for a comment you already answered by hand.

Instagram and YouTube accounts can't be linked, so the replies claim no points. Every reply sends the
fan to the leaderboard on YouTube by the channel's name, e.g. "Correct! 🧠 On YouTube that's points on the
SideQuest Leaderboard. Come claim your spot: SideQuest Logic 👑" or "The trap got you 🪤 Get your revenge on
the next one: search SideQuest Logic on YouTube 🧠" (several wordings, so they don't all look alike).

There are no links or @ in the replies, because Instagram flags those as spam. Put the YouTube link in
your Instagram bio instead.

The comments on the Reels of the last 30 days are read with the YouTube ones (every
`YOUTUBE_REPLY_CHECK_HOURS`, and `/replies`). Replies go out one every 2 minutes, up to
`INSTAGRAM_REPLY_DAILY_MAX` a day (default 50). If Instagram's rate limit is hit, replies pause for 30
minutes. Quiz answers are known for the Reels this machine posted; other Reels get thank-you replies.
Settings: `INSTAGRAM_AUTO_REPLIES`, `INSTAGRAM_REPLY_DAILY_MAX` and `INSTAGRAM_YOUTUBE_NAME` (how the
replies name the channel). The record is `output/instagram_replies.json`.

**The Meta app must be Live.** In Development mode, Instagram hides your fans' comments from the API: the
call succeeds but the list is empty, and `/replies` says "No fan comments visible". Your own account
already has the permission, so no App Review is needed. To switch:
1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps) → your app → **App settings
   → Basic**.
2. Fill in **Privacy Policy URL** with a page that really opens (a 404 doesn't work; a public Google Doc
   or Notion page is enough). Also pick a **Category**, then click **Save changes**.
3. At the top of the dashboard, switch **App Mode** from Development to **Live**.

The token stays the same; the next read sees the comments.

## Dark Outliers (story bank, scripts and videos)

The second channel tells real dark stories: obscure history, eerie true events, unsolved mysteries.
Open it with the **DA** workspace button (or `?workspace=darkarchives`). The bank and the videos live
on the Linux machine (the hub), where the dashboard runs; `db/content_bank.db` is git-ignored.

```bash
python -m modules.fetcher            # fetch stories into db/content_bank.db
python -m modules.fetcher --recheck  # apply the current filters to the NEW stories already saved
python -m modules.script_gen         # Gemini writes the next story's Short: assets/generated_scripts/story_<id>.json
python -m modules.video_gen --id 17  # the finished Short: output/darkarchives/story_17.mp4 (+ .json, story_17/pictures/)
```

The tab does the same: pick a story → **Write the Shorts script** → **Make the video** (a few minutes;
the tab shows the progress, then the video, its pictures and a description to copy). The story becomes
USED.

**Its YouTube channel** (`@DarkOutliersOfficial`, a second channel on the same Google account) has its own
folder, `channels/darkarchives/`, with `channel.env` (`CHANNEL_HANDLE`, `STATS_*`) and its own
`token_youtube.json`; the OAuth client (`client_secret.json` in the project folder) is shared. Under a
finished video the tab has **Connect YouTube** (Google asks which channel: pick Dark Outliers) and
**Upload to YouTube** with the privacy (private until the API audit, like the gameplay channel); the
title, description (handle, source link, photo credits, hashtags) and tags come from `story_<id>.json`,
where the upload (video id, url, Studio link) is recorded too. Category: Entertainment. After a public
upload the channel posts its own first comment, the script's closing question plus the follow line
(`DA_FIRST_COMMENT`): comments are an engagement signal and the question invites them. The handle
also goes into the end banner (`@handle · Follow for more`) and the descriptions. The dashboard's
Upload and Plan tabs stay the gameplay channel's. No TikTok or Instagram for this channel yet.

**Its Discord channel** (`DISCORD_CHANNEL_ID` in `channels/darkarchives/channel.env`, same bot token):
every finished video is posted there (shrunk under 10 MB when needed) and every upload with its YouTube
link. `python -m modules.video_gen --id 17 --quiet` skips the post.

**Videos and Plan** (the tab's other two sections). *Videos* lists every finished video with a player,
the exact YouTube title, description and tags, where it is planned and whether it is on YouTube, with
Plan, Story, Upload now (with the privacy) and Delete buttons (Delete removes the file, its pictures and
plan entries; the story goes back to script-ready and can be made again; a copy already on YouTube stays
there). The YouTube bar above the cards is where the channel is connected (Connect YouTube) or disconnected. *Plan* is the
next two weeks: every day has the slots of `DA_SLOTS` (default `14:30,23:00`, in `TIMEZONE`), each
holding one video; the plan lives in `output/darkarchives/plan.json`, apart from the gameplay channel's.
With `DA_AUTO_POST=1` the Discord bot uploads the slot's video to the Dark Outliers channel at its time as
`DA_UPLOAD_PRIVACY` (default public) and reports the link, or the failure, in its Discord channel; a slot
missed by more than two hours (bot off) is skipped. A video already on YouTube counts as posted.

**Full automation** (`modules/da_produce.py`, `DA_AUTO_PRODUCE=1`): every day at `DA_AUTO_PRODUCE_TIME`
(default 09:30) the bot refreshes the bank from every source (`DA_DAILY_FETCH`) and then, for every empty
slot of tomorrow and of today still 3 h away: takes the best NEW story of the pillar the mix needs most,
has Gemini write its script (a rejected story is marked and the next one tried), makes the video, checks
it (between `DA_MIN_SECONDS` and `DA_MAX_SECONDS` long, real pictures rather than placeholder cards, enough
words; a video that fails is deleted and the next story tried, four stories per slot at most) and puts it
on the plan. Each video is posted in the Dark Outliers Discord channel with **Don't post this** (off the
plan; the video stays in the tab; **Make another now** refills the slot), **Upload to YouTube now** and
**Delete the video** buttons, and goes out at its time; a video made from the tab gets **Plan the next
free slot** instead of the veto. The Plan section shows the automation line with **Run it now**; the command
line is `python -m modules.da_produce` (`--no-fetch`, `--slots N`).

**Discord commands** for this channel: `/da plan` (the next days), `/da videos`, `/da status` (YouTube,
the bank, the mix, the next empty slot, the automation), `/da make` (a video for the next empty slot now;
`slots:`, `fetch:True`), `/da fetch` (refresh the bank), `/da upload video:`.

**How a video is made** (`modules/video_gen.py`): the beats are the hook and every scene.
1. **Narration**: hook + narrations through the usual voice (`trendclip.voiceover`: Edge, or F5-TTS on the
   GPU when Microsoft refuses), `DA_TTS_VOICE` / `DA_TTS_RATE` (default: `TTS_VOICE`, +10%). The word
   timings give every beat its start.
2. **Pictures** (`modules/scene_images.py`): one 9:16 picture per beat from the scene's `image_prompt`.
   `DA_IMAGE_BACKEND`: `auto` (default) = `local` when an NVIDIA GPU and diffusers are there, else `gemini`
   when the key has image quota, else `placeholder`; a backend that fails hands over to the next.
   - `local`: Stable Diffusion XL through diffusers, `DA_IMAGE_MODEL` (default RealVisXL V4.0 Lightning:
     photoreal, 6 steps, 10-20 s a picture on the 8 GB card; the weights, ~7 GB, download on the first
     video). Install with `requirements-gpu.txt`. The GPU voice is unloaded first: both don't fit at once.
   - `gemini`: `DA_GEMINI_IMAGE_MODEL` (default `gemini-2.5-flash-image`). The free tier answers 429:
     it needs a key with billing.
   - A **Wikipedia story** also gets the article's real photos (`DA_ARCHIVE_PHOTOS=1`): the two best
     Commons pictures (no maps, flags or icons) on scene 1 and the middle scene, laid over a blurred copy
     of themselves, with the author and licence in the description.
3. **Motion**: ffmpeg moves over every picture (a slow push-in or pull-out, alternating, with a slight
   drift) and dissolves 0.6 s between beats; each still was graded first (less saturation, a cold tint,
   vignette). Moving grain is `DA_GRAIN=1` (it makes the file about ten times bigger).
4. **Assembly**: frame 0 is the thumbnail (YouTube takes it for a video uploaded through the API), so the
   title card is at full size there and a big headline sits over the middle for the first second:
   Gemini's `thumbnail_text` (2-4 words, the strongest curiosity gap), or the hook's first words for
   scripts written before it existed (`DA_COVER`: `auto`, your own words, or `none`). Then the karaoke
   assembler adds the captions (3 words, `DA_HIGHLIGHT`, default orange), the
   scene's `overlay_text` flashing in above them, the title for 3 s (`DA_TITLE_CARD`), the "Follow for more"
   banner over the last 3.5 s (`DA_END_CARD`, `·` breaks the line, `none` = off), the spoken closing line
   (`DA_CTA_LINE`, default "Follow for more dark stories.") with the red SUBSCRIBE button from that line to
   the end (`DA_CTA_STICKER`: subscribe / follow / none), and a track from
   `DA_MUSIC` (default `folder:dramatic` = `music/dramatic/`, `none` for silence) at `DA_MUSIC_VOLUME`.

`output/darkarchives/story_<id>.json` keeps the title, description (source link, photo and music credits,
hashtags), beats with their timing and picture, who drew the pictures and how long it took.

**Content pillars**: every story belongs to one, and the next story to script comes from the pillar furthest below its share of the videos made so far:

| Pillar | Target | Sources |
|---|---|---|
| Unresolved mysteries & cold cases | 40% | r/UnresolvedMysteries, r/ColdCases, Wikipedia (disappearances, unsolved deaths, ghost ships…) |
| Creepy history & obscure anomalies | 40% | r/CreepyWikipedia, r/DarkHistory, r/todayilearned (filtered), Wikipedia (urban legends, mass psychogenic illness, human experiments) |
| Disturbing true events & outrage cases | 20% | r/TrueCrime, Atlas Obscura and Listverse RSS, Wikipedia (medical scandals) |

Gemini also adds documented cases, split by the same mix, and at least half of them are **anniversaries**:
events whose date falls within the next 10 days (the story starts with `Anniversary: <date>`). Those score
100, so the production run takes them first while the date is near (timely Shorts get shown more), and the
script opens with "N years ago this week"; once the date has passed they become ordinary stories again.

**Filters when fetching**:
- **Length:** stories must be 200–3,500 characters. Wikipedia articles are cut to their opening paragraphs.
- **Link posts:** a Reddit link post that points to Wikipedia becomes that article. The article is saved once, whichever source finds it first.
- **Promos, meta posts and fiction** are rejected by their title.
- **Mixed-topic sources** (r/todayilearned, Atlas Obscura, Listverse) need a keyword: mystery, unexplained, vanished, strange, bizarre, creepy, classified, incident or cold case. It must be in the title, or two different keywords must appear in the text. A Listverse entry counts as on topic when its list article is.
- **Upvotes:** posts need at least 100, but only when Reddit shows them, which takes a free app login. Put `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET` from a "script" app at reddit.com/prefs/apps in `.env`. Without them the public RSS feed is used, which has no votes and is rate-limited (HTTP 429 for some subreddits on a run).

**Script rules** (Gemini, checked in code and sent back up to 3 times):
- **Length:** 95–120 words, about 40–50 s (Shorts live on retention).
- **Hook:** a suspense statement or paradox of at most 12 words.
- **Sentences:** at most 12 words each.
- **Pacing:** tension escalates through scenes 1–5. The last scene is an open question, an eerie twist or a provoking thought.
- **Tone:** documentary and eerie, using only facts from the source. A living person is never called guilty of a crime they weren't convicted of.

Gemini rejects a story (status REJECTED, with the reason shown in the tab) if it is any of these:
- gore without a story;
- fiction (unless it is framed as an internet urban legend);
- a recent political controversy or ongoing news;
- not a story at all.

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
