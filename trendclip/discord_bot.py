"""Discord bot: today's videos, random Shorts, the upload plan and YouTube uploads from slash commands.

Run: ./run.sh bot   (or: python3 run.py --bot). Needs DISCORD_BOT_TOKEN and DISCORD_GUILD_ID in .env;
reminders go to DISCORD_CHANNEL_ID. Heavy work (download, Gemini, render, upload) runs on threads;
buttons keep working after a restart (their custom ids carry the action).
"""

import asyncio
import io
import logging
import random
import sys
import uuid
from datetime import date as Day, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import discord
from discord import app_commands
from discord.ext import tasks

from . import (analytics, assistant, channel_stats, hub, ig_replies, instagram, leaderboard, leaderboard_card, planner, publish,
               script_writer, shorts, video_downloader)
from .config import ConfigError, Settings, get_settings

logger = logging.getLogger(__name__)

MESSAGE_MAX = 2000
CUSTOM_ID_MAX = 100
OWN_TEXT_MAX = 3000  # the longest voiceover a render takes
PENDING_TEXTS = 30
NOUNS = {"math": "math challenge", "riddle": "riddle", "trivia": "trivia trap", "paradox": "paradox trap",
         "movie": "movie quiz", "gamer": "Gamer IQ question"}
EMERGENCY_GAMES = ("GTA V / Online", "Minecraft")  # names from games.py
CREATE_ATTEMPTS = 3      # a random video that fails (ffmpeg, a bad clip, Gemini busy) is made again from scratch
EMERGENCY_ATTEMPTS = 10  # the emergency video keeps trying until it has one (a cap so a broken setup can't loop forever)
PRODUCE_ATTEMPTS = 5     # the morning production run has all day: a failed video is made again from scratch
BW_ATTEMPTS = 2          # a Borderline Why video: one more try (Gemini busy, a topic without sources)
BW_RETRY_MINUTES = 30    # Wikipedia or Gemini down / out of quota: the video is tried again this much later...
BW_RETRY_UNTIL = timedelta(hours=1)  # ...until this long before its slot
RETRY_WAIT = 10.0        # seconds between attempts
PROGRESS_EVERY = 4.0  # seconds between progress message edits (Discord rate limits edits)
REMINDER_ORDER = ("post", "emergency", "slot", "produce", "morning", "leaderboard")  # most time-critical first

SlotChoice = Literal["14:30", "23:00"]
REPLY_EVERY_MINUTES = 2  # YouTube auto replies: never a burst
NO_PINGS = discord.AllowedMentions.none()  # comment text from YouTube is shown as is


PLATFORM_NAMES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}


class JobFailed(Exception):
    pass


# --------------------------------------------------------------------------- helpers


def _bot(interaction: discord.Interaction) -> "TrendClipBot":
    return interaction.client  # type: ignore[return-value]


def allowed(settings: Settings, interaction: discord.Interaction) -> bool:
    if settings.discord_guild_id and interaction.guild_id != settings.discord_guild_id:
        return False
    return not settings.discord_allowed_users or interaction.user.id in settings.discord_allowed_users


def clip_text(text: str, limit: int = MESSAGE_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def champions_note(final: list[dict]) -> str:
    """For you, above the champions post: who to check before giving a prize (must be subscribed)."""
    text = "👑 Last month's final podium: post the next message on YouTube (Community) and Instagram."
    if missing := leaderboard.unverified(final):
        names = ", ".join(f"**{leaderboard.safe(p['name'], 40)}**" for p in missing)
        text += (f"\n⚠️ YouTube doesn't show {names} as subscribed (their subscriptions may be private). "
                 "Must be subscribed to win: check before giving the prize.")
    return text


def already_on(item: planner.PlanItem, short: shorts.ShortVideo, platform: str) -> bool:
    """Uploaded by TrendClip, or marked posted by hand (Upload tab or plan) after posting it in the app."""
    return bool(short.uploads.get(platform) or short.posted.get(platform) or platform in item.posted)


def chunks(lines: list[str], limit: int = MESSAGE_MAX - 50) -> list[str]:
    """Lines packed into as few messages as Discord's length limit allows."""
    out, cur = [], ""
    for line in lines:
        line = clip_text(line, limit)
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    return out + [cur] if cur else out


async def reply_lines(interaction: discord.Interaction, lines: list[str], ephemeral: bool = False) -> None:
    for text in chunks(lines) or ["Nothing to show."]:
        await reply(interaction, text, suppress_embeds=True, allowed_mentions=NO_PINGS, ephemeral=ephemeral)


async def reply(interaction: discord.Interaction, content: str, **kwargs: Any) -> None:
    content = clip_text(content)
    if kwargs.get("view", discord.utils.MISSING) is None:
        del kwargs["view"]
    if interaction.response.is_done():
        await interaction.followup.send(content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


def day_label(settings: Settings, day: Day) -> str:
    delta = (day - assistant.today(settings)).days
    name = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get(delta)
    pretty = day.strftime("%a %d %b")
    return f"{name} ({pretty})" if name else pretty


def youtube_line(short: shorts.ShortVideo | None) -> str:
    if short is None:
        return "video missing"
    yt = short.uploads.get("youtube") or {}
    if not yt:
        return "not uploaded yet"
    return f"on YouTube ({yt.get('privacy', '?')}): <{yt.get('url', '')}>"


def short_meta(short: shorts.ShortVideo) -> str:
    bits = [short.game]
    if short.duration_seconds:
        bits.append(f"{round(short.duration_seconds)}s")
    if short.part:
        bits.append(f"Part {short.part}/{short.parts_total or 2}")
    return " · ".join(bits)


def _fence(text: str) -> str:
    """A copy-paste block. Hashtags inside it don't ping Discord search."""
    return "```\n" + text.replace("```", "'''").strip() + "\n```"


CAPTION_LABELS = {"youtube": "▶️ YouTube", "instagram": "📸 Instagram", "tiktok": "🎵 TikTok"}


def platform_names(settings: Settings) -> str:
    """"YouTube and Instagram" / "YouTube, Instagram and TikTok" (PLATFORMS)."""
    names = [PLATFORM_NAMES[p] for p in settings.platforms]
    return " and ".join([", ".join(names[:-1]), names[-1]]) if len(names) > 1 else names[0]


def caption_message(settings: Settings, short: shorts.ShortVideo, heading: str) -> str:
    """Ready-to-paste caption (text + hashtags) for each active platform (PLATFORMS)."""
    stored = publish.texts_for(settings, short.filename)
    texts = publish.PlatformTexts.model_validate(stored.texts)
    links = (publish.series_info(settings, stored) or {}).get("links", "")
    bodies = {"youtube": publish.youtube_description(texts.youtube, texts.credit, links),
              "instagram": publish.full_text(texts.instagram, texts.credit),
              "tiktok": publish.full_text(texts.tiktok, texts.credit)}
    blocks = [(CAPTION_LABELS[p], bodies[p]) for p in settings.platforms]
    lines = [heading, f"**{texts.youtube.title}**"]
    for label, body in blocks:
        lines.append(f"{label}\n{_fence(body)}")
    text = "\n".join(lines)
    if len(text) <= MESSAGE_MAX:
        return text
    # Keep every fence closed: trim the longest caption until the message fits.
    bodies = [b for _, b in blocks]
    while len(text) > MESSAGE_MAX:
        i = max(range(len(bodies)), key=lambda n: len(bodies[n]))
        bodies[i] = bodies[i][: max(len(bodies[i]) - (len(text) - MESSAGE_MAX) - 2, 40)] + "…"
        lines = [heading, f"**{texts.youtube.title}**"]
        for (label, _), body in zip(blocks, bodies):
            lines.append(f"{label}\n{_fence(body)}")
        text = "\n".join(lines)
    return text


def item_line(settings: Settings, item: planner.PlanItem, with_day: bool = False) -> str:
    when = f"`{item.slot or '--:--'}`"
    if with_day:
        when = f"{item.date.strftime('%a %d %b')} {when}"
    short = None
    if item.short:
        try:
            short = shorts.get_short(settings, item.short)
        except (ValueError, FileNotFoundError):
            pass
    status = youtube_line(short) if item.short else "not made here"
    return f"{when} **{item.title}** · {status}"


def slot_code(slot: str) -> str:
    return slot.replace(":", "")


def code_slot(code: str) -> planner.Slot:
    slot = f"{code[:2]}:{code[2:]}"
    if slot not in planner.SLOTS:
        raise ValueError(f"Unknown slot {slot}")
    return slot  # type: ignore[return-value]


# --------------------------------------------------------------------------- buttons


class ActionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"tc:(?P<action>[a-z]+):(?P<arg>.*)"):
    """A button whose custom id is `tc:<action>:<arg>`, so it works on old messages after restarts."""

    def __init__(self, action: str, arg: str, label: str = "", style: discord.ButtonStyle = discord.ButtonStyle.secondary):
        super().__init__(discord.ui.Button(label=label[:80] or action, style=style, custom_id=f"tc:{action}:{arg}"))
        self.action, self.arg = action, arg

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):  # noqa: ANN001
        return cls(match["action"], match["arg"], item.label or "", item.style)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if allowed(_bot(interaction).settings, interaction):
            return True
        await reply(interaction, "You can't use this bot.", ephemeral=True)
        return False

    async def callback(self, interaction: discord.Interaction) -> None:
        handler = ACTIONS.get(self.action)
        if handler is None:
            return await reply(interaction, "This button is from an older version of the bot.", ephemeral=True)
        try:
            await handler(_bot(interaction), interaction, self.arg)
        except JobFailed:
            pass
        except (ValueError, KeyError, FileNotFoundError, publish.PublishError) as err:
            await reply(interaction, f"Couldn't do that: {err}", ephemeral=True)
        except Exception as err:  # noqa: BLE001 - always answer the click
            logger.exception("Button %s failed", self.action)
            await reply(interaction, f"Something went wrong: {err}", ephemeral=True)


def button(action: str, arg: str, label: str,
           style: discord.ButtonStyle = discord.ButtonStyle.secondary) -> ActionButton | None:
    if len(f"tc:{action}:{arg}") > CUSTOM_ID_MAX:
        return None
    return ActionButton(action, arg, label, style)


def view_of(*buttons: ActionButton | None) -> discord.ui.View | None:
    items = [b for b in buttons if b is not None][:25]
    if not items:
        return None
    view = discord.ui.View(timeout=None)
    for item in items:
        view.add_item(item)
    return view


def video_buttons(bot: "TrendClipBot", short: shorts.ShortVideo) -> list[ActionButton | None]:
    """Upload / comment / plan buttons that fit where this Short is right now."""
    settings = bot.settings
    out: list[ActionButton | None] = []
    yt = short.uploads.get("youtube") or {}
    if not yt:
        out.append(button("up", short.filename, "Upload to YouTube now", discord.ButtonStyle.danger))
    elif assistant.pinned_comment(settings, short.filename):
        out.append(button("cm", short.filename, "Post the pinned comment"))
    if instagram.configured(settings) and not short.uploads.get("instagram"):
        out.append(button("ig", short.filename, "Post on Instagram"))
    planned = [i for i in planner.load(settings).items if i.short == short.filename]
    if planned:
        for item in planned[:2]:
            out.append(button("rm", item.id, f"Remove from plan ({item.date.strftime('%d %b')} {item.slot or ''})".strip()))
    elif not yt:
        now = assistant.now(settings)
        for slot in planner.free_slots(settings, now.date()):
            if planner.slot_time(settings, now.date(), slot) > now:
                out.append(button("pt", f"{slot_code(slot)}:{short.filename}", f"Plan today {slot}", discord.ButtonStyle.primary))
        out.append(button("pl", short.filename, "Plan next free slot", discord.ButtonStyle.primary))
    return out


# --------------------------------------------------------------------------- the bot


class Tree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if allowed(_bot(interaction).settings, interaction):
            return True
        await reply(interaction, "You can't use this bot.", ephemeral=True)
        return False

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        err = getattr(error, "original", error)
        if isinstance(err, JobFailed):
            return
        if not isinstance(err, (ValueError, KeyError, FileNotFoundError, publish.PublishError)):
            logger.exception("Command failed", exc_info=err)
        try:
            await reply(interaction, f"Couldn't do that: {err}", ephemeral=True)
        except discord.HTTPException:
            pass


class TrendClipBot(discord.Client):
    def __init__(self, settings: Settings):
        super().__init__(intents=discord.Intents.default())
        self.settings = settings
        self.tree = Tree(self)
        self.render_lock = asyncio.Lock()  # renders are CPU heavy: one at a time
        self.upload_lock = asyncio.Lock()
        self.comments_lock = asyncio.Lock()  # one YouTube comments read at a time
        self.ig_lock = asyncio.Lock()  # one Instagram comments read at a time
        self.jobs: set[asyncio.Task] = set()  # background posts (a task nobody holds can be dropped)
        self.emergency: set[str] = set()  # "day slot" whose emergency video is being made
        self.sent = assistant.SentLog(settings)
        self._games: list[str] | None = None
        self.texts: dict[str, tuple[str, str | None]] = {}  # remember_text

    async def setup_hook(self) -> None:
        if n := planner.assign_slots(self.settings, assistant.today(self.settings)):
            logger.info("Gave %d planned reels an upload time (14:30 / 23:00)", n)
        self.add_dynamic_items(ActionButton)
        for command in COMMANDS:
            self.tree.add_command(command)
        try:
            if self.settings.discord_guild_id:
                guild = discord.Object(id=self.settings.discord_guild_id)
                self.tree.copy_global_to(guild=guild)
                self.tree.clear_commands(guild=None)
                synced = await self.tree.sync(guild=guild)
                await self.tree.sync()  # drop global copies from earlier runs
            else:
                synced = await self.tree.sync()
            logger.info("Registered %d slash commands", len(synced))
        except app_commands.CommandSyncFailure:
            # A bad command definition (a description over 100 characters…) must not take the whole bot
            # down: the reminders, posts and production run need no slash commands. Discord keeps the
            # commands it registered last time.
            logger.exception("Registering the slash commands failed; running with the ones Discord already has")

        self.reminders.start()
        self.reply_loop.start()
        self.comment_check.start()

    async def on_ready(self) -> None:
        logger.info("Discord bot ready as %s", self.user)
        if hub.is_hub(self.settings):
            logger.info("Hub: taking videos from the other machine in channel %s", hub.channel_id(self.settings))

    async def on_message(self, message: discord.Message) -> None:
        """The hub: answer the sending machine (it posts with this bot's token; nobody else counts)."""
        if (not hub.is_hub(self.settings) or message.author != self.user
                or message.channel.id != hub.channel_id(self.settings) or not hub.is_request(message.content)):
            return
        reply, item, attach = await hub.handle(self.settings, message, message.channel)
        if attach is None:
            await message.channel.send(reply)
        else:
            await message.channel.send(reply, file=discord.File(io.BytesIO(attach[1]), filename=attach[0]))
        if item is None or not self.settings.discord_channel_id:
            return
        channel = self.get_channel(self.settings.discord_channel_id) or await self.fetch_channel(
            self.settings.discord_channel_id)
        await channel.send(f"📥 From the other machine: {item_line(self.settings, item, with_day=True)}")

    # ---- shared work ------------------------------------------------------------------

    async def run_job(self, channel: discord.abc.Messageable, title: str, fn: Callable[[assistant.ProgressFn], Any],
                      lock: asyncio.Lock, attempts: int = 1, give_up: Callable[[], bool] = lambda: False,
                      stop: Callable[[Exception], bool] = lambda err: False) -> Any:
        """Run `fn(progress)` on a thread, keeping one message up to date with its progress. A failure is
        tried again from scratch, up to `attempts` times in all, unless `give_up()` says it is no longer needed
        or `stop(err)` says another try now cannot help (Gemini's quota is used up for the day)."""
        msg = await channel.send(f"⏳ {title}: " + ("waiting for the current job" if lock.locked() else "starting"))
        state: dict[str, Any] = {"text": "Starting", "frac": None, "dirty": False}

        def progress(frac: float | None, text: str) -> None:
            state.update(frac=frac, text=text, dirty=True)

        async def ticker() -> None:
            while True:
                await asyncio.sleep(PROGRESS_EVERY)
                if state["dirty"]:
                    state["dirty"] = False
                    pct = f" ({state['frac']:.0%})" if state["frac"] is not None else ""
                    try:
                        await msg.edit(content=clip_text(f"⏳ {title}: {state['text']}{pct}"))
                    except discord.HTTPException:
                        pass

        async with lock:
            task = asyncio.create_task(ticker())
            try:
                for attempt in range(1, attempts + 1):
                    try:
                        result = await asyncio.to_thread(fn, progress)
                        break
                    except Exception as err:  # noqa: BLE001 - shown in the channel
                        logger.exception("%s failed (attempt %d/%d)", title, attempt, attempts)
                        if attempt == attempts or stop(err) or await asyncio.to_thread(give_up):
                            await msg.edit(content=clip_text(f"❌ {title} failed: {err}"
                                                             + (f" ({attempt} attempts)" if attempt > 1 else "")))
                            raise JobFailed(str(err)) from err
                        state["dirty"] = False
                        await msg.edit(content=clip_text(f"⚠️ {title}: attempt {attempt} failed ({err}). "
                                                         f"Trying again ({attempt + 1}/{attempts})…"))
                        await asyncio.sleep(RETRY_WAIT)
            finally:
                task.cancel()
        await msg.edit(content=f"✅ {title}: done" + (f" (on attempt {attempt})" if attempt > 1 else ""))
        return result

    async def send_video(self, channel: discord.abc.Messageable, filename: str, content: str,
                         view: discord.ui.View | None = None, followup: discord.Interaction | None = None) -> None:
        guild = getattr(channel, "guild", None)
        limit = guild.filesize_limit if guild else assistant.DISCORD_FILE_LIMIT
        try:
            path = await asyncio.to_thread(assistant.discord_copy, self.settings, filename, limit)
        except Exception as err:  # noqa: BLE001 - still say what the video is
            logger.warning("Could not prepare %s for Discord: %s", filename, err)
            content += f"\n(Couldn't attach the video: {err})"
            path = None
        kwargs: dict[str, Any] = {"content": clip_text(content)}
        if path is not None:
            kwargs["file"] = discord.File(path, filename=filename)
        if view is not None:
            kwargs["view"] = view
        if followup is not None:
            await followup.followup.send(**kwargs)
        else:
            await channel.send(**kwargs)

    async def show_short(self, interaction: discord.Interaction, filename: str, heading: str = "") -> None:
        short = shorts.get_short(self.settings, filename)
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)
        text = f"{heading}\n" if heading else ""
        text += f"🎬 **{short.title}**\n{short_meta(short)} · {youtube_line(short)}"
        await self.send_video(interaction.channel, filename, text, view_of(*video_buttons(self, short)),
                              followup=interaction)

    def remember_text(self, text: str, game: str | None) -> str:
        """Your own text waits here (custom ids are too short for it) until you pick story / quiz."""
        key = uuid.uuid4().hex[:10]
        self.texts[key] = (text, game)
        while len(self.texts) > PENDING_TEXTS:
            self.texts.pop(next(iter(self.texts)))
        return key

    async def create_random(self, interaction: discord.Interaction, game: str | None, kind: str = "story",
                            text: str | None = None, pick: analytics.Pick | None = None) -> None:
        """A random video, or (`text`) one with your own words as the voiceover. `pick`: the numbers-based
        choice behind `kind` / `game` ("🎲 Pick for me"), explained in the channel."""
        if script_writer.gemini_api_key(self.settings) is None:
            return await reply(interaction, "Add GEMINI_API_KEY to .env first (it writes the story).", ephemeral=True)
        noun = NOUNS.get(kind, "story" if text else "video")
        if text:
            what = f"your {noun}" + (f" on {game} gameplay" if game else "")
        else:
            what = f"a random {game} {noun}" if game else f"a random {noun}"
        note = f"\n{analytics.describe_pick(pick)}" if pick else ""
        await reply(interaction, f"On it: creating {what}. This takes a few minutes; I'll post it here.{note}")
        seconds = pick.target_seconds if pick else None

        def job(p: assistant.ProgressFn) -> shorts.ShortVideo:
            if text:
                return assistant.create_from_text(self.settings, text, game, p, kind)
            return assistant.create_random_short(self.settings, game, p, kind, target_seconds=seconds)

        short = await self.run_job(interaction.channel, f"Creating {what}", job, self.render_lock,
                                   attempts=CREATE_ATTEMPTS)
        answer = f"\nAnswer (spoiler): ||{short.answer}||" if short.answer else ""
        await self.send_video(interaction.channel, short.filename,
                              f"🎬 New video: **{short.title}**\n{short_meta(short)}{answer}\nWhat should I do with it?",
                              view_of(*video_buttons(self, short),
                                      button("del", short.filename, "Delete it", discord.ButtonStyle.secondary)))

    async def upload(self, channel: discord.abc.Messageable, filename: str) -> None:
        if not publish.TOKEN_FILE.is_file():
            await channel.send("YouTube isn't connected. Open the dashboard → Upload → Connect YouTube, then try again.")
            return
        short = shorts.get_short(self.settings, filename)
        record = await self.run_job(channel, f"Uploading \"{short.title}\" to YouTube",
                                    lambda p: assistant.upload_now(self.settings, filename, p), self.upload_lock)
        text = f"✅ **{short.title}** is on YouTube: {record['url']}"
        if record.get("privacy") != "public":
            text += (f"\nYouTube set it to **{record.get('privacy')}** (unverified API projects can only upload "
                     f"private videos). Make it public in Studio: <{record['studio_url']}>")
        comment = assistant.pinned_comment(self.settings, filename)
        ig_too = instagram.configured(self.settings) and not short.uploads.get("instagram")
        view = view_of(button("cm", filename, "Post the pinned comment") if comment else None,
                       button("ig", filename, "Post on Instagram too") if ig_too else None)
        await channel.send(clip_text(text), view=view)

    async def post_instagram(self, channel: discord.abc.Messageable, filename: str) -> None:
        short = shorts.get_short(self.settings, filename)
        record = await self.run_job(channel, f"Posting \"{short.title}\" on Instagram",
                                    lambda p: instagram.post_reel(self.settings, filename,
                                                                  first_comment=short.pinned_comment, progress=p),
                                    self.upload_lock)
        text = f"✅ **{short.title}** is on Instagram: {record['url']}"
        if record.get("comment_error"):
            text += f"\nThe comment didn't go through: {record['comment_error']}"
        await channel.send(clip_text(text))


    def today_text(self, day: Day) -> tuple[str, list[ActionButton | None], bool]:
        """The day's slots as text, Show / Upload buttons, and whether a slot is still empty."""
        settings = self.settings
        lines = [f"📅 **{day_label(settings, day)}**"]
        buttons: list[ActionButton | None] = []
        empty = False
        for entry in assistant.day_slots(settings, day):
            if entry.item is None:
                lines.append(f"`{entry.slot}` nothing planned")
                empty = True
                continue
            lines.append(item_line(settings, entry.item))
            if entry.short:
                buttons.append(button("show", entry.short.filename, f"Show the {entry.slot} video", discord.ButtonStyle.primary))
                if not entry.youtube:
                    buttons.append(button("up", entry.short.filename, f"Upload {entry.slot} now", discord.ButtonStyle.danger))
        for item in assistant.unslotted(settings, day):
            lines.append(item_line(settings, item))
            if item.short:
                buttons.append(button("show", item.short, f"Show \"{clip_text(item.title, 40)}\""))
        return "\n".join(lines), buttons, empty

    # ---- reminders ----------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def reminders(self) -> None:
        at = assistant.now(self.settings)
        self.darkarchives_posts(at)
        self.borderlinewhy_jobs(at)
        if not self.settings.discord_channel_id:
            return
        # The time-critical work first (a slot's post, the emergency video), then the rest. After a restart the
        # whole backlog is due at once, and the morning summary (stats, insights, comments, Gemini) takes
        # minutes: it must never hold up a post. Everything slow runs as its own task.
        due = sorted(assistant.due_reminders(self.settings, at, self.sent.load()),
                     key=lambda r: REMINDER_ORDER.index(r.kind))
        for reminder in due:
            try:
                channel = self.get_channel(self.settings.discord_channel_id) or await self.fetch_channel(
                    self.settings.discord_channel_id)
                if reminder.kind == "slot":
                    await self.slot_reminder(channel, reminder.day, reminder.slot)
                    self.sent.add(reminder.key, at.date())
                    continue
                if reminder.kind == "leaderboard" and not publish.TOKEN_FILE.is_file():
                    self.sent.add(reminder.key, at.date())
                    continue
                # Marked sent first: an upload or a render takes minutes and must never start twice.
                self.sent.add(reminder.key, at.date())
                if reminder.kind == "post":
                    work = self.auto_post(channel, reminder.day, reminder.slot)
                elif reminder.kind == "emergency":
                    work = self.emergency_video(channel, reminder.day, reminder.slot)
                elif reminder.kind == "produce":
                    work = self.produce_videos(channel)
                elif reminder.kind == "morning":
                    work = self.morning_summary(channel, reminder.day)
                else:
                    work = self.leaderboard_post(channel)
                task = asyncio.create_task(work)
                self.jobs.add(task)
                task.add_done_callback(self._job_done)
            except discord.HTTPException:
                logger.exception("Reminder %s failed; retrying next minute", reminder.key)
                continue

    def darkarchives_posts(self, at: datetime) -> None:
        """Dark Outliers (its own plan, channel and token): the slot's planned video goes to its YouTube
        channel at its time, reported in its Discord channel; and once a day (DA_AUTO_PRODUCE_TIME) the
        production run fills the empty slots."""
        from modules import da_plan, da_produce

        try:
            sent = self.sent.load()
            due = da_plan.due_posts(self.settings, at, sent)
            local = at.astimezone(self.settings.tz)
            hour, minute = map(int, da_produce.produce_time().split(":"))
            start = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            key = da_produce.produce_key(local.date())
            produce = (da_produce.auto_produce() and key not in sent and start <= local < start + assistant.MORNING_WINDOW)
        except Exception:  # noqa: BLE001 - never let the second channel stop the first one's reminders
            logger.exception("Dark Outliers plan check failed")
            return
        for key_post, day, slot in due:
            self.sent.add(key_post, at.date())  # marked first: an upload takes minutes and must never start twice
            task = asyncio.create_task(self.darkarchives_post(day, slot))
            self.jobs.add(task)
            task.add_done_callback(self._job_done)
        if produce:
            self.sent.add(key, local.date())
            task = asyncio.create_task(self.darkarchives_produce())
            self.jobs.add(task)
            task.add_done_callback(self._job_done)

    async def darkarchives_channel(self) -> discord.abc.Messageable | None:
        from modules import da_channel

        channel_id = da_channel.discord_channel_id()
        if not channel_id:
            return None
        try:
            return self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        except discord.HTTPException:
            logger.exception("Dark Outliers Discord channel %s is not reachable", channel_id)
            return None

    async def darkarchives_produce(self, only: tuple[Day, str] | None = None) -> None:
        """DA_AUTO_PRODUCE: refresh the bank, then a video for every empty slot of tomorrow (and today's
        still 3 h away), each planned and shown with a "Don't post this" button. `only`: one slot."""
        from modules import da_produce

        settings = self.settings
        channel = await self.darkarchives_channel()
        if channel is None:
            logger.warning("Dark Outliers production: no Discord channel (DISCORD_CHANNEL_ID in channels/darkarchives/channel.env)")
            return
        wanted = [only] if only else await asyncio.to_thread(da_produce.targets, settings, assistant.now(settings))
        if not wanted:
            await channel.send("🏭 Every Dark Outliers slot of today and tomorrow is planned; nothing to make.")
            return
        if script_writer.gemini_api_key(settings) is None:
            await channel.send("🏭 Can't write the scripts without GEMINI_API_KEY.")
            return
        labels = ", ".join(f"{day_label(settings, d)} {s}" for d, s in wanted)
        await channel.send(clip_text(f"🏭 **Making the Dark Outliers videos for {labels}**: the best story of the pillar "
                                     "the mix needs, its script, the video, a check, then the slot. Each goes out at "
                                     "its time by itself; press **Don't post this** under any you don't like."))
        if not only:
            lesson = await self.darkarchives_lesson()
            if lesson:
                await channel.send(clip_text(f"📈 {lesson} — /da insights for the whole picture"), suppress_embeds=True)
        if not only and da_produce.daily_fetch():
            try:
                report = await self.run_job(channel, "Refreshing the story bank", da_produce.refresh_bank, self.render_lock)
                saved = sum(r.get("saved", 0) for r in report.values())
                await channel.send(f"📚 {saved} new stories in the bank.")
            except JobFailed:
                pass
        for day, slot in wanted:
            await self.darkarchives_produce_one(channel, day, slot)

    async def darkarchives_lesson(self) -> str:
        """The best lesson of the channel's own numbers (read again when the cache is old), "" when there is
        none or they cannot be read: the morning run never waits on it."""
        from modules import da_insights

        try:
            data = await asyncio.to_thread(da_insights.refresh, self.settings)
            return da_insights.best_lesson(data)
        except Exception as err:  # noqa: BLE001
            logger.warning("Dark Outliers lesson unavailable: %s", err)
            return ""

    async def darkarchives_produce_one(self, channel: discord.abc.Messageable, day: Day, slot: str,
                                       ranking: bool | None = None) -> None:
        """`ranking`: a ranking video for the slot (None: one when it is due)."""
        from modules import da_plan, da_produce

        settings = self.settings
        if await asyncio.to_thread(da_plan.slot_item, day, slot):
            return
        label = f"{day_label(settings, day)} {slot}"
        try:
            result = await self.run_job(channel, f"Making the {'ranking ' if ranking else ''}video for {label}",
                                        lambda p: da_produce.produce_one(settings, day, slot, p, ranking=ranking), self.render_lock)
        except JobFailed:
            await channel.send(f"🏭 Couldn't make a video for {label}. If Gemini is out of quota, write the script with any other "
                               "LLM: dashboard → Dark Outliers → ✍️ Write it myself.",
                               view=view_of(button("daprod", f"{day.isoformat()} {slot}", "🎬 Try again now",
                                                   discord.ButtonStyle.success)))
            return
        if result["status"] != "planned":
            return
        info, item = result["info"], result["item"]
        skipped = [t for t in result["tried"] if not t.get("ok")]
        note = ("\nSkipped: " + "; ".join(f"{t['title'][:30]} ({', '.join(t['problems'])})" for t in skipped)) if skipped else ""
        ranked = ("\n" + " · ".join(f"{e['rank']}. {e['label']}" for e in info["ranking"])) if info.get("ranking") else ""
        text = (f"🏭 **{info['title']}** is planned for {label} and goes out by itself as {da_plan.upload_privacy()}.{ranked}\n"
                f"{info['duration_seconds']:.0f} s · pictures: {info['pictures']}"
                + (f" · {len(info['photos'])} photo(s)" if info.get("photos") else "")
                + (f" · 🎵 {info['music']['title']}" if info.get("music") else "") + note)
        await self.darkarchives_send_video(channel, result["story_id"], text,
                                           view_of(button("daveto", item["id"], "🚫 Don't post this", discord.ButtonStyle.danger),
                                                   button("daup", str(result["story_id"]), "⬆️ Upload to YouTube now", discord.ButtonStyle.primary),
                                                   button("dadel", str(result["story_id"]), "🗑 Delete the video")))

    async def darkarchives_send_video(self, channel: discord.abc.Messageable, story_id: int, content: str,
                                      view: discord.ui.View | None = None) -> None:
        from modules import video_gen

        await self.send_file_video(channel, video_gen.video_path(story_id), content, view)

    async def send_file_video(self, channel: discord.abc.Messageable, path: Path, content: str,
                              view: discord.ui.View | None = None) -> None:
        """Post any channel's finished video (shrunk to the server's limit when needed) with its text and buttons."""
        guild = getattr(channel, "guild", None)
        limit = guild.filesize_limit if guild else assistant.DISCORD_FILE_LIMIT
        kwargs: dict[str, Any] = {"content": clip_text(content)}
        try:
            small = await asyncio.to_thread(assistant.fit_for_discord, path, path.parent / "discord" / path.name, limit)
            kwargs["file"] = discord.File(small, filename=path.name)
        except Exception as err:  # noqa: BLE001
            kwargs["content"] = clip_text(content + f"\n(Couldn't attach the video: {err})")
        if view is not None:
            kwargs["view"] = view
        await channel.send(**kwargs)

    async def darkarchives_post(self, day: Day, slot: str) -> None:
        from modules import da_channel, da_plan

        item = await asyncio.to_thread(da_plan.slot_item, day, slot)
        if item is None:
            return
        rest = da_channel._rest(self.settings)
        try:
            record = await asyncio.to_thread(da_plan.post_now, item)
            logger.info("Dark Outliers %s %s: %s is on YouTube (%s)", day, slot, item.title, record.get("url"))
        except Exception as err:  # noqa: BLE001 - reported in the channel
            logger.exception("Dark Outliers post for %s %s failed", day, slot)
            if rest is not None:
                try:
                    await asyncio.to_thread(rest.post, clip_text(
                        f"❌ {slot}: **{item.title}** did not go out: {err}\nUpload it from the Dark Outliers tab."))
                except Exception:  # noqa: BLE001
                    logger.exception("Could not report the failure in the Dark Outliers channel")

    # ---- Borderline Why --------------------------------------------------------------------

    def borderlinewhy_jobs(self, at: datetime) -> None:
        """Borderline Why (its own channel and token): BW_LEAD hours before the slot the bot makes the day's video
        and posts it with Approve; at the slot the approved video goes to YouTube."""
        from modules import bw_channel

        try:
            sent = self.sent.load()
            makes = bw_channel.due_makes(self.settings, at, sent)
            posts = bw_channel.due_posts(self.settings, at, sent)
        except Exception:  # noqa: BLE001 - never let the third channel stop the others' reminders
            logger.exception("Borderline Why job check failed")
            return
        for job in makes:
            self.sent.add(job.make_key, at.date())  # marked first: a render takes minutes and must never start twice
            self._spawn(self.borderlinewhy_make(job))
        for job in posts:
            self.sent.add(job.post_key, at.date())
            self._spawn(self.borderlinewhy_post(job))

    def _spawn(self, work) -> None:
        task = asyncio.create_task(work)
        self.jobs.add(task)
        task.add_done_callback(self._job_done)

    async def borderlinewhy_channel(self) -> discord.abc.Messageable | None:
        from modules import bw_channel

        channel_id = bw_channel.discord_channel_id()
        if not channel_id:
            return None
        try:
            return self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        except discord.HTTPException:
            logger.exception("Borderline Why Discord channel %s is not reachable", channel_id)
            return None

    async def borderlinewhy_make(self, job) -> None:
        """Make the job's video (BW_ATTEMPTS tries) and show it with Approve / Don't post / Upload now."""
        from modules import bw_channel

        settings = self.settings
        label = f"{day_label(settings, job.day)} {job.slot}"
        channel = await self.borderlinewhy_channel()
        if channel is None:
            logger.warning("Borderline Why: no Discord channel (DISCORD_CHANNEL_ID in channels/borderlinewhy/channel.env); "
                           "nothing made for %s", label)
            return
        late = lambda: assistant.now(settings) > job.post_at  # noqa: E731
        out_of_quota = lambda err: getattr(err, "retry_at", None) is not None  # noqa: E731 - a second try now can't help
        try:
            info = await self.run_job(channel, f"🗺️ Borderline Why video for {label}", lambda p: bw_channel.make(settings, job, p),
                                      self.render_lock, attempts=BW_ATTEMPTS, give_up=late, stop=out_of_quota)
        except JobFailed as failed:
            from modules import bw_script

            cause = failed.__cause__
            try_again = view_of(button("bwrun", f"{job.day.isoformat()} {job.slot}", "🎬 Try again now", discord.ButtonStyle.success))
            now = assistant.now(settings)
            quota_back = getattr(cause, "retry_at", None)
            # Out of quota: wait for the time Google gave (plus a little), not every 30 minutes; one message.
            retry_at = (quota_back.astimezone(settings.tz) + timedelta(minutes=3) if quota_back
                        else now + timedelta(minutes=BW_RETRY_MINUTES))
            if isinstance(cause, bw_script.TryLater) and retry_at <= job.post_at - BW_RETRY_UNTIL:
                # Wikipedia down, Gemini busy or out of quota: not the topic's fault, so wait and try again by itself
                what = (f"Gemini's free quota is used up until {quota_back.astimezone(settings.tz):%H:%M}" if quota_back
                        else f"Wikipedia or Gemini is not answering ({clip_text(str(failed), 200)})")
                await channel.send(f"⏳ {what}. I'll try the {label} video again at {retry_at:%H:%M} by myself"
                                   + (", carrying on from the draft I kept" if quota_back else "")
                                   + ". Or write the script with any other LLM: dashboard → Borderline Why → ✍️ Write it myself.",
                                   view=try_again)
                await asyncio.sleep(max(0.0, (retry_at - assistant.now(settings)).total_seconds()))
                if await asyncio.to_thread(bw_channel.load_info, job.key) is None:
                    await self.borderlinewhy_make(job)
                return
            hint = ((f" Gemini's free quota comes back at {quota_back.astimezone(settings.tz):%H:%M}, too late for this slot: "
                     if quota_back else " Gemini is out of quota: ")
                    + "write the script with any other LLM on the dashboard → Borderline Why → ✍️ Write it myself."
                    if isinstance(cause, bw_script.TryLater) else "")
            await channel.send(f"🗺️ No Borderline Why video for {label}: nothing will be posted.{hint}", view=try_again)
            return
        await self.send_file_video(channel, bw_channel.video_path(info["key"]),
                                   bw_channel.video_line(info) + "\n" + bw_channel.video_note(info), bw_view(info["key"]))

    async def borderlinewhy_post(self, job) -> None:
        """The slot's time: upload the approved video; say so when it is still waiting for Approve."""
        from modules import bw_channel

        info = await asyncio.to_thread(bw_channel.load_info, job.key)
        if info is None or (info.get("uploads") or {}).get("youtube") or bw_channel.is_vetoed(info) or info.get("check"):
            return
        channel = await self.borderlinewhy_channel()
        if not bw_channel.ready(info):
            if channel is not None:
                await channel.send(f"⏸ {job.slot}: **{info['title']}** is waiting for **Approve**; it goes out as soon as you press it.",
                                   view=bw_view(info["key"]))
            return
        try:
            record = await asyncio.to_thread(bw_channel.publish_if_allowed, job.key, settings=self.settings)
            logger.info("Borderline Why %s: %s", job.key, (record or {}).get("url"))
        except Exception as err:  # noqa: BLE001 - reported in the channel
            logger.exception("Borderline Why upload of %s failed", job.key)
            if channel is not None:
                await channel.send(clip_text(f"❌ **{info['title']}** did not go out: {err}"),
                                   view=view_of(button("bwup", info["key"], "⬆️ Try the upload again", discord.ButtonStyle.primary)))

    def _job_done(self, task: asyncio.Task) -> None:
        self.jobs.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("Background job failed", exc_info=task.exception())

    @reminders.before_loop
    async def _before_reminders(self) -> None:
        await self.wait_until_ready()

    async def leaderboard_post(self, channel: discord.abc.Messageable) -> None:
        """The fresh leaderboard picture and a caption to copy into a YouTube post (the API can't post there)."""
        settings = self.settings
        fresh = True
        try:
            board, _ = await asyncio.to_thread(leaderboard.refresh, settings)
        except Exception as err:  # noqa: BLE001 - fall back to the last read
            logger.exception("Comments leaderboard failed")
            fresh = False
            board = await asyncio.to_thread(leaderboard.load, settings)
            if not board.updated_at:
                await channel.send(clip_text(f"🏆 Couldn't read the YouTube comments for the leaderboard: {err}"))
                return
        if assistant.today(settings).day == 1:
            month, final = leaderboard.last_month(settings, board)
            if text := leaderboard.champions_text(month, final):
                await channel.send(clip_text(champions_note(final)), allowed_mentions=NO_PINGS)
                await channel.send(text, allowed_mentions=NO_PINGS, suppress_embeds=True)
        if not board.people:
            return
        if fresh:
            await self.queue_replies(channel, board)
        await self.send_post(channel.send, board)

    async def send_post(self, send, board: leaderboard.Board) -> None:
        """The podium picture, then the YouTube post text as its own message."""
        settings = self.settings
        handle = settings.stats_youtube if settings.stats_youtube.startswith("@") else settings.channel_handle
        png = await asyncio.to_thread(leaderboard_card.render, board.people, handle, day=assistant.today(settings),
                                      logo=settings.channel_dir / "logo.png")
        caption = leaderboard.post_caption(board.people, 3 + leaderboard_card.LIST_SIZE,
                                         leaderboard.next_drop(settings),
                                         leaderboard.month_name(leaderboard.month_start(settings)))
        await send("🏆 Today's leaderboard post: save the picture, copy the next message (long-press → "
                   "Copy Text), and post both on YouTube (Create → Post). Pick each @name from YouTube's "
                   "list as you type it, or it may not become a tag.",
                   file=discord.File(io.BytesIO(png), filename="leaderboard.png"))
        # Its own plain message: "Copy Text" then gives exactly the caption, with no ``` around it.
        await send(caption, allowed_mentions=NO_PINGS, suppress_embeds=True)

    async def morning_summary(self, channel: discord.abc.Messageable, day: Day) -> None:
        text, buttons, empty = self.today_text(day)
        waiting = len(assistant.backlog(self.settings))
        text = "☀️ Good morning! Here's today's upload plan.\n" + text
        if waiting:
            text += f"\n{waiting} video(s) are made but not planned yet (/videos)."
        if empty:
            text += "\nSome slots are empty: plan a video with /plan add, or let me make one."
            buttons.append(button("mk", "-", "Create a random video", discord.ButtonStyle.success))
        if self.settings.auto_produce:
            text += (f"\n🏭 At {self.settings.auto_produce_time} I make the videos for every slot still empty "
                     f"(tomorrow's, and today's if 3+ hours away) and post them here; press **Don't post this** "
                     f"on any you don't like.")
        await channel.send(clip_text(text), view=view_of(*buttons))
        await self.send_stats(channel)
        if publish.TOKEN_FILE.is_file():
            await self.send_insights(channel, brief=True)
            await self.send_comments(channel)

    async def send_insights(self, channel: discord.abc.Messageable, days: int = 30, brief: bool = False) -> None:
        """Read today's numbers of every published video (the daily snapshot), then what works best: a few
        lines in the morning summary (`brief`), the whole report for /insights. A failure is only reported."""
        try:
            collected = await asyncio.to_thread(analytics.collect, self.settings)
            report = await asyncio.to_thread(analytics.build_report, self.settings, days)
        except Exception as err:  # noqa: BLE001 - shown in the channel
            logger.exception("Insights failed")
            await channel.send(clip_text(f"📈 Couldn't read the video insights: {err}"))
            return
        if brief:
            lines = analytics.insight_lines(report)
            if not lines:
                return
            head = f"📈 **What works (last {days} days, {report.ranked} videos)** — /insights for the whole picture"
            await channel.send(clip_text("\n".join([head, *lines])), suppress_embeds=True)
            return
        messages = analytics.format_report(report)
        if collected.warnings:
            messages.append("\n".join(f"⚠️ {w}" for w in collected.warnings))
        for text in messages:
            await channel.send(clip_text(text), suppress_embeds=True)

    async def send_comments(self, channel: discord.abc.Messageable) -> None:
        """Yesterday's new YouTube comments with their points, and the top 10; a failure is only reported."""
        try:
            async with self.comments_lock:
                board, _ = await asyncio.to_thread(leaderboard.refresh, self.settings)
        except Exception as err:  # noqa: BLE001 - shown in the channel
            logger.exception("Comments leaderboard failed")
            await channel.send(clip_text(f"💬 Couldn't read the YouTube comments: {err}"))
            return
        # By time, not "since the last read": comment_check reads them every few hours too.
        since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        new = [c for c in board.comments if c.published >= since]
        head = f"💬 **{len(new)} new comment{'s' * (len(new) != 1)}** since yesterday"
        lines = [head + (":" if new else "."), *leaderboard.new_lines(board, new), "",
                 f"🏆 **Leaderboard, {leaderboard.month_name(leaderboard.month_start(self.settings))}** "
                 "(/leaderboard · /comments · /points)", *leaderboard.top_lines(board)]
        if not board.people:
            lines.append("Nobody has commented this month yet.")
        for text in chunks(lines):
            await channel.send(text, suppress_embeds=True, allowed_mentions=NO_PINGS)
        await self.queue_replies(channel, board)

    async def queue_replies(self, channel: discord.abc.Messageable, board: leaderboard.Board,
                            wait: bool = True) -> None:
        """A YouTube reply to every comment, sent by reply_loop a few minutes apart; flagged comments are
        listed so they can be removed. wait=False skips the spoiler wait on quiz videos."""
        skipped = await asyncio.to_thread(leaderboard.new_skips, self.settings, board)
        if skipped:
            lines = [f"🚫 **{len(skipped)} comment(s) look like spam, links or rude**: no reply, no points. "
                     "Remove them in YouTube Studio → Comments if you agree:"]
            lines += [f"• **{leaderboard.safe(c.author, 32)}**: {leaderboard.safe(c.text, 100)} <{c.url}>"
                      for c in skipped[:15]]
            for text in chunks(lines):
                await channel.send(text, suppress_embeds=True, allowed_mentions=NO_PINGS)
        if not self.settings.youtube_auto_replies or not publish.can_comment():
            return
        queued = await asyncio.to_thread(leaderboard.plan_replies, self.settings, board,
                                         assistant.today(self.settings), None, wait)
        if queued:
            kinds = {"first": "🥇 first correct", "correct": "✅ correct", "voted": "🗳️ votes",
                     "wrong": "🪤 trapped", "comment": "💬 comments", "again": "🔁 extra comments"}
            counts = ", ".join(f"{sum(q['result'] == k for q in queued)} {label}" for k, label in kinds.items()
                               if any(q["result"] == k for q in queued))
            minutes = len(queued) * REPLY_EVERY_MINUTES
            await channel.send(clip_text(f"💬 Replying on YouTube to {len(queued)} comment(s) ({counts}), one every "
                                         f"{REPLY_EVERY_MINUTES} min (~{minutes} min)."), allowed_mentions=NO_PINGS)

    @tasks.loop(minutes=REPLY_EVERY_MINUTES)
    async def reply_loop(self) -> None:
        settings = self.settings
        if settings.youtube_auto_replies and publish.TOKEN_FILE.is_file():
            board = await asyncio.to_thread(leaderboard.load, settings)
            try:
                item = board.channel_id and await asyncio.to_thread(
                    leaderboard.send_next_reply, settings, board.channel_id, assistant.today(settings))
            except Exception:  # noqa: BLE001 - logged; the next round tries the next one
                logger.exception("YouTube auto reply failed")
                item = None
            if item:
                logger.info("Replied on YouTube to %s: %s", item["name"], item["text"])
        if ig_replies.enabled(settings):
            try:
                item = await asyncio.to_thread(ig_replies.send_next_reply, settings, assistant.today(settings))
            except Exception:  # noqa: BLE001 - logged; the next round tries the next one
                logger.exception("Instagram auto reply failed")
                item = None
            if item:
                logger.info("Replied on Instagram to %s: %s", item["name"], item["text"])

    @reply_loop.before_loop
    async def _before_reply_loop(self) -> None:
        await self.wait_until_ready()

    def replies_on(self) -> str:
        """Why YouTube replies can't go out right now ("" when they can)."""
        if not self.settings.youtube_auto_replies:
            return "Auto replies are off: set YOUTUBE_AUTO_REPLIES=true in .env and restart."
        if not publish.TOKEN_FILE.is_file():
            return "YouTube isn't connected. Open the dashboard → Upload → Connect YouTube."
        if not publish.can_comment():
            return "Reconnect YouTube once (dashboard → Upload → Disconnect → Connect YouTube) to allow replies."
        return ""

    async def check_comments(self, channel: discord.abc.Messageable, wait: bool = True) -> leaderboard.Board:
        """Read the comments now and queue a reply to every new one."""
        async with self.comments_lock:
            board, _ = await asyncio.to_thread(leaderboard.refresh, self.settings)
        await self.queue_replies(channel, board, wait)
        return board

    async def check_instagram(self, channel: discord.abc.Messageable, wait: bool = True) -> leaderboard.Board:
        """Read the Instagram comments now, list the flagged ones and queue a reply to every new fan."""
        settings = self.settings
        async with self.ig_lock:
            board = await asyncio.to_thread(ig_replies.refresh, settings)
        skipped = await asyncio.to_thread(ig_replies.new_skips, settings, board)
        if skipped:
            reels = await asyncio.to_thread(ig_replies.links, settings)
            lines = [f"🚫 **{len(skipped)} Instagram comment(s) look like spam, links or rude**: no reply. "
                     "Remove them on the Reel if you agree:"]
            lines += [f"• **{leaderboard.safe(c.author, 32)}**: {leaderboard.safe(c.text, 100)}"
                      + (f" <{reels[c.video_id]}>" if reels.get(c.video_id) else "") for c in skipped[:15]]
            for text in chunks(lines):
                await channel.send(text, suppress_embeds=True, allowed_mentions=NO_PINGS)
        queued = await asyncio.to_thread(ig_replies.plan_replies, settings, board, assistant.today(settings), None,
                                         wait)
        if queued:
            invites = sum(settings.instagram_youtube_name in q["text"] for q in queued)
            await channel.send(clip_text(
                f"📸 Replying on Instagram to {len(queued)} fan(s), {invites} with the YouTube invite, one every "
                f"{REPLY_EVERY_MINUTES} min (~{len(queued) * REPLY_EVERY_MINUTES} min)."), allowed_mentions=NO_PINGS)
        return board

    @staticmethod
    def _due(updated_at: str, hours: float, first: bool) -> bool:
        try:
            last = datetime.fromisoformat(updated_at)
        except ValueError:
            return first
        return datetime.now(timezone.utc) - last >= timedelta(hours=hours)

    @tasks.loop(minutes=30)
    async def comment_check(self) -> None:
        settings = self.settings
        hours = settings.youtube_reply_check_hours
        if not hours or not settings.discord_channel_id:
            return
        try:
            channel = self.get_channel(settings.discord_channel_id) or await self.fetch_channel(
                settings.discord_channel_id)
        except Exception:  # noqa: BLE001 - logged; the next round tries again
            logger.exception("The Discord channel for comment replies is unreachable")
            return
        if not self.replies_on() and not self.comments_lock.locked():
            board = await asyncio.to_thread(leaderboard.load, settings)
            # never read yet: the morning read or /replies starts it
            if self._due(board.updated_at, hours, first=False):
                try:
                    await self.check_comments(channel)
                except Exception:  # noqa: BLE001 - logged; the next round tries again
                    logger.exception("Reading the YouTube comments for replies failed")
        if ig_replies.enabled(settings) and not self.ig_lock.locked():
            board = await asyncio.to_thread(ig_replies.load, settings)
            if self._due(board.updated_at, hours, first=True):
                try:
                    await self.check_instagram(channel)
                except Exception:  # noqa: BLE001 - logged; the next round tries again
                    logger.exception("Reading the Instagram comments for replies failed")

    @comment_check.before_loop
    async def _before_comment_check(self) -> None:
        await self.wait_until_ready()

    async def send_stats(self, channel: discord.abc.Messageable) -> None:
        """The channel report (YouTube / TikTok / Instagram, last 24 h); a failure here never blocks the plan."""
        try:
            report = await asyncio.to_thread(channel_stats.build_report, self.settings)
        except Exception as err:  # noqa: BLE001 - shown in the channel
            logger.exception("Channel report failed")
            await channel.send(clip_text(f"📊 Couldn't build the channel report: {err}"))
            return
        for message in channel_stats.format_report(report, self.settings.tz):
            await channel.send(clip_text(message), suppress_embeds=True)

    def auto_targets(self, item: planner.PlanItem, short: shorts.ShortVideo) -> tuple[list[str], list[str]]:
        """The platforms the slot's Short goes to by itself, and notes on the ones it can't."""
        go, notes = [], []
        if "youtube" in item.platforms and not already_on(item, short, "youtube"):
            if publish.TOKEN_FILE.is_file():
                go.append("youtube")
            else:
                notes.append("YouTube isn't connected (dashboard → Upload → Connect YouTube), so it wasn't posted there.")
        if "instagram" in item.platforms and not already_on(item, short, "instagram"):
            if instagram.configured(self.settings):
                go.append("instagram")
            else:
                notes.append("Instagram isn't set up (INSTAGRAM_ACCESS_TOKEN), so it wasn't posted there.")
        if "tiktok" in item.platforms and not short.posted.get("tiktok"):
            notes.append("TikTok has no upload API: post it there yourself (the caption is in the Upload tab).")
        return go, notes

    async def auto_post(self, channel: discord.abc.Messageable, day: Day, slot: str) -> None:
        """AUTO_POST: the slot's planned Short goes out on YouTube and Instagram, with a report here."""
        settings = self.settings
        item = await asyncio.to_thread(planner.slot_item, settings, day, slot)
        if item is None:
            return  # the reminder before the slot already said nothing is planned
        if not item.short:
            await channel.send(clip_text(f"⏰ {slot}: **{item.title}** was made outside TrendClip, so post it yourself."))
            return
        try:
            short = await asyncio.to_thread(shorts.get_short, settings, item.short)
            if not shorts.short_path(settings, item.short).is_file():
                raise FileNotFoundError(item.short)
        except (ValueError, FileNotFoundError, OSError):
            await channel.send(clip_text(f"⚠️ {slot}: the video for **{item.title}** isn't on this machine "
                                         "anymore, so nothing was posted."))
            return
        go, notes = self.auto_targets(item, short)
        if not go:
            done = [p for p in ("youtube", "instagram") if already_on(item, short, p)]
            if done:
                notes.insert(0, f"**{short.title}** is already on {' and '.join(PLATFORM_NAMES[p] for p in done)}.")
            if notes:
                await channel.send(clip_text(f"⏰ {slot}: " + "\n".join(notes)))
            return
        names = " and ".join(PLATFORM_NAMES[p] for p in go)
        await channel.send(clip_text(f"🚀 **{slot}: posting \"{short.title}\" on {names}** (planned for now)."))
        lines, retry = [], []
        if "youtube" in go:
            try:
                record = await self.run_job(channel, "YouTube upload",
                                            lambda p: assistant.upload_now(settings, item.short, p), self.upload_lock)
                lines.append(f"▶️ YouTube: {record['url']}")
                if record.get("privacy") != "public":
                    lines.append(f"   YouTube set it to **{record.get('privacy')}**: make it public in Studio "
                                 f"<{record['studio_url']}>")
                comment = assistant.pinned_comment(settings, item.short)
                if comment and record.get("privacy") == "public" and publish.can_comment():
                    try:
                        await asyncio.to_thread(publish.post_comment, settings, item.short, comment)
                        lines.append("   💬 Comment posted. Pin it on YouTube (⋮ → Pin); the API can't.")
                    except Exception as err:  # noqa: BLE001 - the video is up; only the comment failed
                        lines.append(f"   The comment didn't go through: {err}")
            except JobFailed as err:
                lines.append(f"❌ YouTube failed: {err}")
                retry.append(button("up", item.short, "Retry YouTube", discord.ButtonStyle.danger))
        if "instagram" in go:
            try:
                record = await self.run_job(channel, "Instagram post", lambda p: instagram.post_reel(
                    settings, item.short, first_comment=short.pinned_comment, progress=p), self.upload_lock)
                lines.append(f"📸 Instagram: {record['url']}")
                if record.get("comment_error"):
                    lines.append(f"   The comment didn't go through: {record['comment_error']}")
            except JobFailed as err:
                lines.append(f"❌ Instagram failed: {err}")
                retry.append(button("ig", item.short, "Retry Instagram", discord.ButtonStyle.danger))
        head = "✅" if not retry else "⚠️"
        await channel.send(clip_text(f"{head} **{short.title}** ({slot})\n" + "\n".join(lines + notes)),
                           view=view_of(*retry), suppress_embeds=True)

    # ---- the production run (full automation) -------------------------------------------

    async def produce_videos(self, channel: discord.abc.Messageable) -> None:
        """AUTO_PRODUCE: a video for every empty slot of tomorrow (and today's still 3+ h away), made by the
        channel's numbers, planned there and shown here with a "Don't post this" button."""
        settings = self.settings
        targets = await asyncio.to_thread(assistant.produce_targets, settings, assistant.now(settings))
        if not targets:
            await channel.send("🏭 Every slot of today and tomorrow is planned; nothing to make.")
            return
        if script_writer.gemini_api_key(settings) is None:
            await channel.send("🏭 Can't make the videos for the empty slots without GEMINI_API_KEY.")
            return
        labels = ", ".join(f"{day_label(settings, d)} {s}" for d, s in targets)
        await channel.send(clip_text(
            f"🏭 **Making the videos for {labels}**, one at a time, by the channel's numbers. Each goes out at "
            f"its time by itself; press **Don't post this** under any you don't like."))
        games = await asyncio.to_thread(library_games, settings)
        for day, slot in targets:
            await self.produce_one(channel, day, slot, games)

    async def produce_one(self, channel: discord.abc.Messageable, day: Day, slot: str,
                          games: list[str] | None = None) -> None:
        """One slot's video: picked, made (PRODUCE_ATTEMPTS tries), planned, shown with the veto button."""
        settings = self.settings
        if await asyncio.to_thread(planner.slot_item, settings, day, slot):
            return
        label = f"{day_label(settings, day)} {slot}"
        pool = games or list(EMERGENCY_GAMES)
        must, avoid = await asyncio.to_thread(analytics.daily_game, settings, day, slot)
        if must:
            pool = [must]
        elif avoid:
            pool = [g for g in pool if g != avoid] or pool
        pick = await asyncio.to_thread(plan_pick, settings, pool, None, day)
        kind, game = pick.mode, pick.game or random.choice(EMERGENCY_GAMES)
        noun = NOUNS.get(kind, "story")
        daily = f" (today's {must} video)" if must else ""
        await channel.send(clip_text(f"🏭 {label}: a {noun} on {game} gameplay{daily}.\n{analytics.describe_pick(pick)}"))
        try:
            short = await self.run_job(channel, f"Making the {noun} for {label}",
                                       lambda p: assistant.create_auto_short(settings, game, p, kind, EMERGENCY_GAMES,
                                                                             target_seconds=pick.target_seconds),
                                       self.render_lock, attempts=PRODUCE_ATTEMPTS,
                                       give_up=lambda: planner.slot_item(settings, day, slot) is not None)
        except JobFailed:
            manual = (" Or write a Gamer IQ with any LLM: dashboard → SideQuestLogic → ✍️ Write it myself."
                      if kind == "gamer" else "")
            await channel.send(f"🏭 Couldn't make the video for {label} after {PRODUCE_ATTEMPTS} attempts. The "
                               f"emergency video will try again an hour before the slot.{manual} Or:",
                               view=view_of(button("prod", f"{day.isoformat()} {slot}", "🎬 Try again now",
                                                   discord.ButtonStyle.success)))
            return
        if await asyncio.to_thread(planner.slot_item, settings, day, slot):
            await self.send_video(channel, short.filename,
                                  f"🎬 **{short.title}** is ready, but {label} got a video meanwhile, so it waits "
                                  f"in /videos.\n{short_meta(short)}", view_of(*video_buttons(self, short)))
            return
        item = await asyncio.to_thread(assistant.plan_short, settings, short.filename, day, slot)
        answer = f"\nAnswer (spoiler): ||{short.answer}||" if short.answer else ""
        await self.send_video(channel, short.filename,
                              f"🏭 **{short.title}** is planned for {label} and goes out by itself.\n"
                              f"{short_meta(short)}{answer}",
                              view_of(button("veto", item.id, "🚫 Don't post this", discord.ButtonStyle.danger),
                                      button("cm", short.filename, "Captions")))

    async def emergency_video(self, channel: discord.abc.Messageable, day: Day, slot: str) -> None:
        """EMERGENCY_VIDEO: a slot still empty an hour before it gets a random video (a random type over
        GTA V or Minecraft gameplay), planned there so it goes out at its time."""
        settings = self.settings
        if await asyncio.to_thread(planner.slot_item, settings, day, slot):
            return
        if script_writer.gemini_api_key(settings) is None:
            await channel.send(f"🚨 Nothing is planned for {slot}, and I can't make a video without GEMINI_API_KEY.")
            return
        # No AUTO_PICK_DAILY lineup here: an hour before the slot Gemini's quota is often gone, and the
        # picker's kinds include math, which has built-in texts.
        pick = await asyncio.to_thread(plan_pick, settings, list(EMERGENCY_GAMES))
        kind, game = pick.mode, pick.game or random.choice(EMERGENCY_GAMES)
        noun = NOUNS.get(kind, "story")
        busy = f"{day.isoformat()} {slot}"
        self.emergency.add(busy)
        try:
            await channel.send(f"🚨 **Emergency:** nothing is planned for {slot}. Making a random {noun} "
                               f"with {game} gameplay for it now.\n{analytics.describe_pick(pick)}")
            try:
                short = await self.run_job(channel, f"Emergency {noun} for {slot}",
                                           lambda p: assistant.create_auto_short(settings, game, p, kind,
                                                                                 EMERGENCY_GAMES,
                                                                                 target_seconds=pick.target_seconds),
                                           self.render_lock, attempts=EMERGENCY_ATTEMPTS,
                                           give_up=lambda: planner.slot_item(settings, day, slot) is not None)
            except JobFailed:
                await channel.send(f"🚨 The emergency video for {slot} couldn't be made, so {slot} has nothing planned.",
                                   view=view_of(button("mk", "-", "Create a random video", discord.ButtonStyle.success)))
                return
            if await asyncio.to_thread(planner.slot_item, settings, day, slot):
                await self.send_video(channel, short.filename,
                                      f"🎬 **{short.title}** is ready, but {slot} got a video meanwhile, so it "
                                      f"waits in /videos.\n{short_meta(short)}", view_of(*video_buttons(self, short)))
                return
            await asyncio.to_thread(assistant.plan_short, settings, short.filename, day, slot)
        finally:
            self.emergency.discard(busy)
        when = planner.slot_time(settings, day, slot)
        late = assistant.now(settings) >= when
        if not settings.auto_post:
            going = f"upload it at {slot}"
        elif late:
            going = "it's past the slot time, so it goes out now"
        else:
            going = f"it goes out by itself at {slot}"
        await self.send_video(channel, short.filename,
                              f"🚨 Planned for {slot}: **{short.title}** ({going}).\n{short_meta(short)}",
                              view_of(*video_buttons(self, short)))
        if settings.auto_post and late and assistant.now(settings) < when + assistant.POST_WINDOW:
            # The slot's own post already found it empty (or would post it a second time): post it from here.
            self.sent.add(assistant.post_key(day, slot), day)
            await self.auto_post(channel, day, slot)

    async def slot_reminder(self, channel: discord.abc.Messageable, day: Day, slot: str) -> None:
        minutes = self.settings.discord_reminder_minutes
        entry = next(e for e in assistant.day_slots(self.settings, day) if e.slot == slot)
        if entry.item is None and f"{day.isoformat()} {slot}" in self.emergency:
            await channel.send(f"⏰ {slot} is in {minutes} minutes: its emergency video is still being made.")
            return
        if entry.item is None:
            await channel.send(f"⏰ {slot} is in {minutes} minutes and nothing is planned for it.",
                               view=view_of(button("mk", "-", "Create a random video", discord.ButtonStyle.success)))
            return
        if entry.short is None:
            await channel.send(f"⏰ {slot} in {minutes} minutes: **{entry.item.title}** (made outside TrendClip).")
            return
        if entry.youtube:
            await channel.send(f"⏰ {slot}: **{entry.short.title}** is already {youtube_line(entry.short)}")
            return
        go, _ = self.auto_targets(entry.item, entry.short) if self.settings.auto_post else ([], [])
        when = (f"goes out by itself on {' and '.join(PLATFORM_NAMES[p] for p in go)} in {minutes} minutes" if go
                else f"upload in {minutes} minutes")
        await self.send_video(channel, entry.short.filename,
                              f"⏰ {slot} {when}: **{entry.short.title}**\n{short_meta(entry.short)}",
                              view_of(*video_buttons(self, entry.short)))


# --------------------------------------------------------------------------- button actions


async def act_show(bot: TrendClipBot, interaction: discord.Interaction, filename: str) -> None:
    await bot.show_short(interaction, filename)


async def act_upload_confirm(bot: TrendClipBot, interaction: discord.Interaction, filename: str) -> None:
    short = shorts.get_short(bot.settings, filename)
    yt = short.uploads.get("youtube")
    text = f"Publish **{short.title}** publicly on YouTube now?"
    if yt:
        text = f"**{short.title}** is already on YouTube (<{yt.get('url')}>). Upload it again as a new video?"
    await reply(interaction, text, view=view_of(
        button("upgo", filename, "Yes, publish now", discord.ButtonStyle.danger),
        button("no", "-", "Cancel")))


async def act_upload(bot: TrendClipBot, interaction: discord.Interaction, filename: str) -> None:
    await interaction.response.edit_message(content="Publishing…", view=None)
    await bot.upload(interaction.channel, filename)


async def act_instagram_confirm(bot: TrendClipBot, interaction: discord.Interaction, filename: str) -> None:
    short = shorts.get_short(bot.settings, filename)
    up = short.uploads.get("instagram")
    text = (f"**{short.title}** is already on Instagram (<{up.get('url')}>). Post it again?" if up
            else f"Post **{short.title}** on Instagram now as a Reel (with the Upload tab's caption"
                 f"{' and the pinned comment' if short.pinned_comment else ''})?")
    await reply(interaction, text, view=view_of(
        button("iggo", filename, "Yes, post on Instagram", discord.ButtonStyle.danger),
        button("no", "-", "Cancel")))


async def act_instagram(bot: TrendClipBot, interaction: discord.Interaction, filename: str) -> None:
    await interaction.response.edit_message(content="Posting on Instagram…", view=None)
    await bot.post_instagram(interaction.channel, filename)


async def act_cancel(bot: TrendClipBot, interaction: discord.Interaction, _arg: str) -> None:
    await interaction.response.edit_message(content="Cancelled.", view=None)


async def act_comment(bot: TrendClipBot, interaction: discord.Interaction, filename: str) -> None:
    text = assistant.pinned_comment(bot.settings, filename)
    if not text:
        return await reply(interaction, "This video has no pinned comment.", ephemeral=True)
    await interaction.response.defer(thinking=True)
    record = await asyncio.to_thread(publish.post_comment, bot.settings, filename, text)
    await reply(interaction, f"💬 Comment posted: <{record['url']}>\nThe API can't pin it: on YouTube open the "
                             "comment's ⋮ menu → Pin.")


async def act_plan_next(bot: TrendClipBot, interaction: discord.Interaction, filename: str) -> None:
    item = assistant.plan_short(bot.settings, filename)
    await reply(interaction, f"📅 Planned **{item.title}** for {day_label(bot.settings, item.date)} at {item.slot}.")


async def act_plan_today(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    code, filename = arg.split(":", 1)
    item = assistant.plan_short(bot.settings, filename, assistant.today(bot.settings), code_slot(code))
    await reply(interaction, f"📅 Planned **{item.title}** for today at {item.slot}.")


async def act_veto(bot: TrendClipBot, interaction: discord.Interaction, item_id: str) -> None:
    """The production run's "Don't post this": off the plan; the video is kept; the slot can be refilled."""
    settings = bot.settings
    try:
        item = await asyncio.to_thread(planner.find_item, settings, item_id)
    except KeyError:
        return await reply(interaction, "That video is no longer on the plan.", ephemeral=True)
    await asyncio.to_thread(planner.delete_item, settings, item_id)
    label = f"{day_label(settings, item.date)} {item.slot or ''}".strip()
    arg = f"{item.date.isoformat()} {item.slot}" if item.slot else ""
    await reply(interaction, f"🚫 **{item.title}** won't be posted: it's off {label} (the video stays in /videos). "
                             f"The slot is empty again: make another now, or the emergency video fills it an hour "
                             f"before.",
                view=view_of(button("prod", arg, "🎬 Make another now", discord.ButtonStyle.success) if arg else None,
                             *kind_buttons()))


async def act_produce(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    """"Make another now" / "Try again now" for one slot: the production run for just that slot."""
    try:
        date_text, slot = arg.split()
        day = Day.fromisoformat(date_text)
    except ValueError as err:
        raise ValueError(f"bad slot {arg!r}") from err
    if slot not in planner.SLOTS:
        raise ValueError(f"unknown slot {slot}")
    await reply(interaction, f"On it: making a video for {day_label(bot.settings, day)} {slot}.")
    await bot.produce_one(interaction.channel, day, slot)


async def act_unplan(bot: TrendClipBot, interaction: discord.Interaction, item_id: str) -> None:
    item = assistant.unplan(bot.settings, item_id)
    await reply(interaction, f"🗑️ Took **{item.title}** off {day_label(bot.settings, item.date)} "
                             f"{item.slot or ''}. The video itself is kept.")


async def act_delete_confirm(bot: TrendClipBot, interaction: discord.Interaction, filename: str) -> None:
    short = shorts.get_short(bot.settings, filename)
    if any(i.short == filename for i in planner.load(bot.settings).items):
        return await reply(interaction, "It's on the plan; remove it from the plan first.", ephemeral=True)
    await reply(interaction, f"Delete **{short.title}** for good?", view=view_of(
        button("delgo", filename, "Yes, delete", discord.ButtonStyle.danger), button("no", "-", "Keep it")))


async def act_delete(bot: TrendClipBot, interaction: discord.Interaction, filename: str) -> None:
    shorts.delete_short(bot.settings, filename)
    await interaction.response.edit_message(content="🗑️ Deleted.", view=None)


# Math first: its Shorts get the most comments (13-18 against 2-8 for the other quizzes).
KINDS = {"math": ("🧮 Math challenge 🔥", discord.ButtonStyle.success),
         "gamer": ("🎮 Gamer IQ", discord.ButtonStyle.success),
         "trivia": ("🌍 Trivia trap", discord.ButtonStyle.primary), "riddle": ("🧩 Riddle", discord.ButtonStyle.primary),
         "movie": ("🎬 Movie quiz", discord.ButtonStyle.primary),
         "paradox": ("🌀 Paradox trap", discord.ButtonStyle.primary),
         "story": ("📖 Story", discord.ButtonStyle.secondary)}
# "🎲 Pick for me" and the emergency video without AUTO_PICK: a quiz kind, math most often.
PICK_WEIGHTS = {"math": 4, "gamer": 3, "trivia": 2, "riddle": 2, "movie": 2, "paradox": 1}


def plan_pick(settings: Settings, games: list[str] | None = None, rng: random.Random | None = None,
              day: Day | None = None) -> analytics.Pick:
    """What "🎲 Pick for me" and the emergency video make: drawn by the channel's own numbers (AUTO_PICK,
    analytics.plan_video), else by the fixed weights above. `games`: the gameplay to choose from. `day`: the
    slot's day, whose AUTO_PICK_DAILY lineup fixes the kind until it is complete."""
    lineup = analytics.lineup_kind(settings, day) if day else None
    if settings.auto_pick:
        try:
            trending = analytics.trending_games(settings) if games and settings.auto_pick_trends else None
            return analytics.plan_video(settings, games=games, rng=rng, trending=trending,
                                        kinds=(lineup,) if lineup else None)

        except Exception as err:  # noqa: BLE001 - a broken analytics file must not stop the video
            logger.warning("Numbers-based pick failed (%s); using the fixed weights", err)
    rng = rng or random.Random()
    if lineup:
        return analytics.Pick(mode=lineup, game=rng.choice(games) if games else None)
    allowed = {k: w for k, w in PICK_WEIGHTS.items() if k in settings.auto_pick_kinds} or {settings.auto_pick_kinds[0]: 1}
    kind = rng.choices(list(allowed), weights=list(allowed.values()))[0]
    return analytics.Pick(mode=kind, game=rng.choice(games) if games else None)



def library_games(settings: Settings) -> list[str]:
    """The games the No-Copyright channels have gameplay of (empty when the library can't be read)."""
    try:
        return [name for name, n in video_downloader.get_library(settings).counts().items() if n]
    except Exception as err:  # noqa: BLE001 - no library: the game is picked while downloading
        logger.warning("Game library unavailable (%s)", err)
        return []


def kind_buttons(game: str | None = None) -> list[ActionButton | None]:
    """The kind choice (🎲 first); `mk` args are 'kind:game' (just 'kind' when the game doesn't fit)."""
    out = [(button("mk", f"pick:{game}", "🎲 Pick for me") if game else None)
           or button("mk", "pick", "🎲 Pick for me")]
    for kind, (label, style) in KINDS.items():
        with_game = button("mk", f"{kind}:{game}", label, style) if game else None
        out.append(with_game or button("mk", kind, label, style))
    return out


async def ask_kind(interaction: discord.Interaction, game: str | None) -> None:
    what = f" with {game} gameplay" if game else ""
    own = button("own", game or "-", "✍️ Use my own text") or button("own", "-", "✍️ Use my own text")
    await reply(interaction, f"What kind of video should I make{what}?", view=view_of(*kind_buttons(game), own))


async def act_make(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    kind, _, game = arg.partition(":")
    game = game.strip() or None
    pick = None
    if kind == "pick":
        games = None if game else await asyncio.to_thread(library_games, bot.settings)
        pick = await asyncio.to_thread(plan_pick, bot.settings, games)
        kind, game = pick.mode, game or pick.game
    if kind not in KINDS:  # the old "Create a random video" buttons
        return await ask_kind(interaction, None)
    await bot.create_random(interaction, game, kind, pick=pick)


class OwnTextModal(discord.ui.Modal, title="Your own text"):
    """A multi-line box for a story or quiz you wrote (the slash command's `text:` is one line)."""

    text = discord.ui.TextInput(
        label="Story or quiz (spoken word for word)", style=discord.TextStyle.paragraph, min_length=20,
        max_length=OWN_TEXT_MAX,
        placeholder="I walked into the wrong wedding... / Start with 24. Add 18. Divide by 2... / Which country...")

    def __init__(self, game: str | None):
        super().__init__(timeout=900)
        self.game = game

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await ask_text_kind(interaction, _bot(interaction).remember_text(str(self.text.value), self.game))


async def ask_text_kind(interaction: discord.Interaction, key: str) -> None:
    buttons = [button("mkt", f"{kind}:{key}", label.replace("Math challenge", "Math"), style)
               for kind, (label, style) in KINDS.items()]
    await reply(interaction, "Got your text. What is it? I keep your words; Gemini writes the title, hashtags "
                             "and the rest (quizzes get their numbers / options flashed on screen).",
                view=view_of(*buttons))


async def act_own_text(bot: TrendClipBot, interaction: discord.Interaction, game: str) -> None:
    await interaction.response.send_modal(OwnTextModal(None if game in ("", "-") else game))


async def act_make_from_text(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    kind, _, key = arg.partition(":")
    if key not in bot.texts or kind not in KINDS:
        return await reply(interaction, "I don't have that text anymore (I restarted). Send it again with "
                                        "`/create text:` or the ✍️ button.", ephemeral=True)
    text, game = bot.texts.pop(key)
    await bot.create_random(interaction, game, kind, text=text)


async def act_da_veto(bot: TrendClipBot, interaction: discord.Interaction, item_id: str) -> None:
    """Dark Outliers "Don't post this": off its plan; the video stays in the tab; the slot can be refilled."""
    from modules import da_plan

    try:
        item = await asyncio.to_thread(da_plan.update_item, item_id, da_plan.ItemUpdate())
    except KeyError:
        return await reply(interaction, "That video is no longer on the Dark Outliers plan.", ephemeral=True)
    await asyncio.to_thread(da_plan.delete_item, item_id)
    label = f"{day_label(bot.settings, item.date)} {item.slot}"
    await reply(interaction, f"🚫 **{item.title}** won't be posted: it's off {label} (the video stays in the "
                             f"Dark Outliers tab). The slot is empty again.",
                view=view_of(button("daprod", f"{item.date.isoformat()} {item.slot}", "🎬 Make another now",
                                    discord.ButtonStyle.success)))


async def act_da_produce(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    """Dark Outliers "Make another now" / "Try again now": the production run for that one slot."""
    try:
        date_text, slot = arg.split()
        day = Day.fromisoformat(date_text)
    except ValueError as err:
        return await reply(interaction, f"Couldn't read the slot: {err}", ephemeral=True)
    await reply(interaction, f"🏭 Making a Dark Outliers video for {day_label(bot.settings, day)} {slot}…", ephemeral=True)
    task = asyncio.create_task(bot.darkarchives_produce(only=(day, slot)))
    bot.jobs.add(task)
    task.add_done_callback(bot._job_done)


def _da_info(story_id: int) -> dict | None:
    from modules import da_plan

    return da_plan.video_info(story_id)


async def act_da_upload_confirm(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    from modules import da_channel, da_plan

    info = _da_info(int(arg))
    if info is None:
        return await reply(interaction, "That Dark Outliers video is gone.", ephemeral=True)
    if (info.get("uploads") or {}).get("youtube"):
        return await reply(interaction, f"**{info['title']}** is already on YouTube: {info['uploads']['youtube'].get('url', '')}", ephemeral=True)
    if not da_channel.connected():
        return await reply(interaction, "The Dark Outliers YouTube channel is not connected (Dark Outliers tab → Videos → Connect YouTube).", ephemeral=True)
    privacy = da_plan.upload_privacy()
    await reply(interaction, f"Upload **{info['title']}** to the Dark Outliers channel now, as **{privacy}**?",
                view=view_of(button("daupgo", arg, f"Yes, upload as {privacy}", discord.ButtonStyle.danger),
                             button("no", "-", "Cancel")), ephemeral=True)


async def act_da_upload(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    from modules import da_channel, da_plan

    story_id = int(arg)
    info = _da_info(story_id)
    if info is None:
        return await reply(interaction, "That Dark Outliers video is gone.", ephemeral=True)
    await interaction.response.defer(thinking=True)
    try:
        record = await asyncio.to_thread(da_channel.upload, story_id, da_plan.upload_privacy())
    except Exception as err:  # noqa: BLE001
        return await reply(interaction, f"❌ Upload failed: {err}")
    for item in await asyncio.to_thread(lambda: [i for i in da_plan.load().items if i.story_id == story_id]):
        await asyncio.to_thread(da_plan.set_posted, item.id, True)
    await reply(interaction, f"▶️ **{info['title']}** is on YouTube ({record.get('privacy')}): {record.get('url')}")


async def act_da_plan(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    from modules import da_plan

    story_id = int(arg)
    info = _da_info(story_id)
    if info is None:
        return await reply(interaction, "That Dark Outliers video is gone.", ephemeral=True)
    settings = bot.settings
    nxt = await asyncio.to_thread(da_plan.next_free_slot, settings, assistant.now(settings))
    if nxt is None:
        return await reply(interaction, "No free Dark Outliers slot in the next 30 days.", ephemeral=True)
    item = await asyncio.to_thread(da_plan.add_item, da_plan.ItemRequest(story_id=story_id, date=nxt[0], slot=nxt[1]))
    await reply(interaction, f"📅 **{info['title']}** is planned for {day_label(settings, item.date)} at {item.slot} and goes "
                             f"out by itself as {da_plan.upload_privacy()}.",
                view=view_of(button("daveto", item.id, "🚫 Don't post this", discord.ButtonStyle.danger)))


async def act_da_delete_confirm(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    info = _da_info(int(arg))
    if info is None:
        return await reply(interaction, "That Dark Outliers video is already gone.", ephemeral=True)
    await reply(interaction, f"Delete **{info['title']}** (the file, its pictures and its plan entries; the story can be "
                             f"made again)?" + (" It stays on YouTube." if (info.get("uploads") or {}).get("youtube") else ""),
                view=view_of(button("dadelgo", arg, "Yes, delete it", discord.ButtonStyle.danger), button("no", "-", "Cancel")),
                ephemeral=True)


async def act_da_delete(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    from modules import video_gen

    info = _da_info(int(arg))
    if info is None:
        return await reply(interaction, "That Dark Outliers video is already gone.", ephemeral=True)
    result = await asyncio.to_thread(video_gen.delete_video, int(arg))
    await reply(interaction, f"🗑 **{info['title']}** is deleted ({result['plan_items']} plan entr{'y' if result['plan_items'] == 1 else 'ies'} "
                             f"removed; the story is {result['status'] or 'kept'}).")


def bw_view(key: str) -> discord.ui.View | None:
    return view_of(button("bwok", key, "✅ Approve", discord.ButtonStyle.success),
                   button("bwveto", key, "🚫 Don't post this", discord.ButtonStyle.danger),
                   button("bwup", key, "⬆️ Upload now", discord.ButtonStyle.primary))


async def act_bw_approve(bot: TrendClipBot, interaction: discord.Interaction, key: str) -> None:
    """Borderline Why "Approve": it goes out at its slot, or now when the slot has passed."""
    from modules import bw_channel

    await interaction.response.defer(thinking=True)
    try:
        result = await asyncio.to_thread(bw_channel.approve, key, bot.settings)
    except KeyError:
        return await reply(interaction, "That Borderline Why video is gone.")
    if result["record"]:
        return await reply(interaction, f"✅ **{result['title']}** is on YouTube: {result['record'].get('url', '')}")
    when = result["post_at"].strftime("%H:%M") if result["post_at"] else "its slot"
    await reply(interaction, f"✅ **{result['title']}** is approved: it goes out at {when} as {bw_channel.upload_privacy()}.")


async def act_bw_veto(bot: TrendClipBot, interaction: discord.Interaction, key: str) -> None:
    from modules import bw_channel

    try:
        result = await asyncio.to_thread(bw_channel.veto, key)
    except KeyError:
        return await reply(interaction, "That Borderline Why video is gone.", ephemeral=True)
    text = (f"🚫 **{result['title']}** is private on YouTube now." if result["hidden"] else
            f"🚫 **{result['title']}** was already private." if result["uploaded"] else f"🚫 **{result['title']}** won't be posted.")
    day, slot = key[:10], f"{key[11:13]}:{key[13:15]}"
    await reply(interaction, text, view=view_of(button("bwrun", f"{day} {slot}", "🎬 Make another now", discord.ButtonStyle.success)))


async def act_bw_run(bot: TrendClipBot, interaction: discord.Interaction, arg: str) -> None:
    """Borderline Why "Try again now" / "Make another now": a new video for that slot (the old folder is kept aside)."""
    from modules import bw_channel

    try:
        date_text, slot = arg.split()
        day = Day.fromisoformat(date_text)
    except ValueError as err:
        return await reply(interaction, f"Couldn't read the slot: {err}", ephemeral=True)
    job = bw_channel.job_for(bot.settings, day, slot)
    await asyncio.to_thread(bw_channel.set_aside, job.key)
    await reply(interaction, f"🗺️ Making a new Borderline Why video for {day_label(bot.settings, day)} {job.slot}…", ephemeral=True)
    bot._spawn(bot.borderlinewhy_make(job))


async def act_bw_upload_confirm(bot: TrendClipBot, interaction: discord.Interaction, key: str) -> None:
    from modules import bw_channel

    info = bw_channel.load_info(key)
    if info is None:
        return await reply(interaction, "That Borderline Why video is gone.", ephemeral=True)
    if (info.get("uploads") or {}).get("youtube"):
        return await reply(interaction, f"**{info['title']}** is already on YouTube: {info['uploads']['youtube'].get('url', '')}", ephemeral=True)
    if info.get("check"):
        return await reply(interaction, f"**{info['title']}** failed its check ({'; '.join(info['check'])}) and can't go out.", ephemeral=True)
    if not bw_channel.connected():
        return await reply(interaction, "The Borderline Why YouTube channel is not connected: `/bw connect`.", ephemeral=True)
    privacy = bw_channel.upload_privacy()
    await reply(interaction, f"Upload **{info['title']}** to Borderline Why now, as **{privacy}**?",
                view=view_of(button("bwupgo", key, f"Yes, upload as {privacy}", discord.ButtonStyle.danger), button("no", "-", "Cancel")),
                ephemeral=True)


async def act_bw_upload(bot: TrendClipBot, interaction: discord.Interaction, key: str) -> None:
    from modules import bw_channel

    info = bw_channel.load_info(key)
    if info is None:
        return await reply(interaction, "That Borderline Why video is gone.", ephemeral=True)
    await interaction.response.defer(thinking=True)
    try:
        record = await asyncio.to_thread(bw_channel.upload, key, settings=bot.settings)
    except Exception as err:  # noqa: BLE001
        return await reply(interaction, f"❌ Upload failed: {err}")
    await reply(interaction, f"▶️ **{info['title']}** is on YouTube ({record.get('privacy')}): {record.get('url')}")


ACTIONS: dict[str, Callable[[TrendClipBot, discord.Interaction, str], Any]] = {
    "show": act_show, "up": act_upload_confirm, "upgo": act_upload, "no": act_cancel, "cm": act_comment,
    "pl": act_plan_next, "pt": act_plan_today, "rm": act_unplan, "del": act_delete_confirm, "delgo": act_delete,
    "mk": act_make, "own": act_own_text, "mkt": act_make_from_text, "ig": act_instagram_confirm,
    "iggo": act_instagram, "veto": act_veto, "prod": act_produce, "daveto": act_da_veto, "daprod": act_da_produce,
    "daup": act_da_upload_confirm, "daupgo": act_da_upload, "daplan": act_da_plan, "dadel": act_da_delete_confirm,
    "dadelgo": act_da_delete, "bwok": act_bw_approve, "bwveto": act_bw_veto, "bwrun": act_bw_run,
    "bwup": act_bw_upload_confirm, "bwupgo": act_bw_upload,
}


# --------------------------------------------------------------------------- autocomplete


async def video_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    needle = current.lower().strip()
    out = []
    for s in shorts.list_shorts(_bot(interaction).settings):
        if needle and needle not in s.title.lower() and needle not in s.filename.lower():
            continue
        tag = " · on YouTube" if s.uploads.get("youtube") else ""
        if len(s.filename) <= 100:
            out.append(app_commands.Choice(name=clip_text(f"{s.title} ({s.game}){tag}", 100), value=s.filename))
        if len(out) == 25:
            break
    return out


async def item_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    settings = _bot(interaction).settings
    needle = current.lower().strip()
    start = assistant.today(settings) - timedelta(days=7)
    out = []
    for item in assistant.planned_items(settings, start, 60):
        label = f"{item.date.strftime('%a %d %b')} {item.slot or '--:--'} · {item.title}"
        if not needle or needle in label.lower():
            out.append(app_commands.Choice(name=clip_text(label, 100), value=item.id))
        if len(out) == 25:
            break
    return out


async def day_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    settings = _bot(interaction).settings
    base = assistant.today(settings)
    out = []
    for n in range(14):
        day = base + timedelta(days=n)
        label = day_label(settings, day)
        if not current or current.lower() in label.lower() or current in day.isoformat():
            out.append(app_commands.Choice(name=label, value=day.isoformat()))
    return out[:25]


async def game_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    bot = _bot(interaction)
    if bot._games is None:
        def load() -> list[str]:
            counts = video_downloader.get_library(bot.settings).counts()
            return sorted(counts, key=lambda g: -counts[g])

        try:
            bot._games = await asyncio.wait_for(asyncio.to_thread(load), timeout=2.0)
        except Exception:  # noqa: BLE001 - autocomplete has 3 seconds; try again on the next keystroke
            return []
    needle = current.lower().strip()
    return [app_commands.Choice(name=g[:100], value=g[:100]) for g in bot._games if needle in g.lower()][:25]


# --------------------------------------------------------------------------- slash commands


@app_commands.command(name="today", description="Which videos go out today at 14:30 and 23:00")
async def today_cmd(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    day = assistant.today(bot.settings)
    text, buttons, empty = bot.today_text(day)
    videos = [(e.slot, e.short) for e in assistant.day_slots(bot.settings, day) if e.short]
    for item in assistant.unslotted(bot.settings, day):
        try:
            extra = shorts.get_short(bot.settings, item.short) if item.short else None
        except (ValueError, FileNotFoundError):
            extra = None
        if extra:
            videos.append((item.slot, extra))
    if videos:
        text += "\nCaptions for each one are below, ready to copy."
    if any(b and b.action == "show" for b in buttons):
        text += "\nWant to see them?"
    if empty:
        buttons.append(button("mk", "-", "Create a random video", discord.ButtonStyle.success))
    await reply(interaction, text, view=view_of(*buttons))
    for slot, short in videos:
        await interaction.followup.send(caption_message(bot.settings, short, f"📝 `{slot or 'no time'}` **{short.title}**"))


@app_commands.command(name="slot", description="Send me the video planned for 14:30 or 23:00")
@app_commands.describe(time="Upload time", day="Which day (default: today)")
@app_commands.autocomplete(day=day_choices)
async def slot_cmd(interaction: discord.Interaction, time: SlotChoice, day: str | None = None) -> None:
    bot = _bot(interaction)
    when = assistant.parse_day(bot.settings, day)
    item = planner.slot_item(bot.settings, when, time)
    if item is None:
        return await reply(interaction, f"Nothing is planned for {day_label(bot.settings, when)} at {time}. "
                                        "Use /plan add or /create.")
    if not item.short:
        return await reply(interaction, f"{time}: **{item.title}** was made outside TrendClip, so I have no file.")
    await bot.show_short(interaction, item.short, f"📅 {day_label(bot.settings, when)} · {time}")


@app_commands.command(name="create", description="Create a video: a story, math, riddle, trivia, paradox or movie quiz")
@app_commands.describe(game="Optional: the gameplay to use (default: a random game)",
                       type="Story, math challenge, riddle, trivia trap, paradox trap or movie quiz (I ask with buttons when you leave it out)",
                       text="Optional: your own story / quiz, spoken word for word (like Fill the rest in the dashboard)")
@app_commands.choices(type=[app_commands.Choice(name=label, value=kind) for kind, (label, _) in KINDS.items()])
@app_commands.autocomplete(game=game_choices)
async def create_cmd(interaction: discord.Interaction, game: str | None = None,
                     type: app_commands.Choice[str] | None = None,  # noqa: A002 - the option's name in Discord
                     text: app_commands.Range[str, 1, OWN_TEXT_MAX] | None = None) -> None:
    bot = _bot(interaction)
    game = (game or "").strip() or None
    text = (text or "").strip() or None
    if text and len(text.split()) < 3:
        return await reply(interaction, "Send at least a few sentences as `text:`.", ephemeral=True)
    if text and type is None:
        return await ask_text_kind(interaction, bot.remember_text(text, game))
    if type is None:
        return await ask_kind(interaction, game)
    await bot.create_random(interaction, game, type.value, text=text)


@app_commands.command(name="upload", description="Publish a video on YouTube now (public)")
@app_commands.describe(video="The video (default: the next one planned today)")
@app_commands.autocomplete(video=video_choices)
async def upload_cmd(interaction: discord.Interaction, video: str | None = None) -> None:
    bot = _bot(interaction)
    if video is None:
        now = assistant.now(bot.settings)
        pending = [e for e in assistant.day_slots(bot.settings, now.date()) if e.short and not e.youtube]
        if not pending:
            return await reply(interaction, "Nothing planned today is waiting for upload. Pick one with `video:`.")
        upcoming = [e for e in pending if planner.slot_time(bot.settings, e.day, e.slot) > now]
        video = (upcoming or pending)[0].short.filename
    await act_upload_confirm(bot, interaction, video)


@app_commands.command(name="videos", description="Videos that are made but not planned or uploaded yet")
async def videos_cmd(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    waiting = assistant.backlog(bot.settings)
    if not waiting:
        return await reply(interaction, "Every video is planned or uploaded. Make a new one with /create.")
    lines = [f"🎞️ **{len(waiting)} video(s) waiting**"]
    lines += [f"• **{s.title}** · {short_meta(s)}" for s in waiting[:15]]
    if len(waiting) > 15:
        lines.append(f"…and {len(waiting) - 15} more (see the dashboard).")
    buttons = [button("show", s.filename, f"Show: {clip_text(s.title, 60)}") for s in waiting[:5]]
    await reply(interaction, "\n".join(lines), view=view_of(*buttons))


@app_commands.command(name="status", description="YouTube connection, today's plan and what's waiting")
async def status_cmd(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    await interaction.response.defer(thinking=True)
    yt = await asyncio.to_thread(publish.youtube_status)
    if yt.get("connected"):
        yt_line = f"✅ YouTube connected ({(yt.get('channel') or {}).get('title', 'channel')})"
    else:
        yt_line = f"❌ YouTube not connected{': ' + yt['error'] if yt.get('error') else ''} (dashboard → Upload)"
    gemini = "✅ Gemini key set" if script_writer.gemini_api_key(bot.settings) else "❌ GEMINI_API_KEY missing"
    nxt = planner.next_free_slot(bot.settings, assistant.now(bot.settings))
    free = f"{day_label(bot.settings, nxt[0])} {nxt[1]}" if nxt else "none in the next 30 days"
    text, _, _ = bot.today_text(assistant.today(bot.settings))
    await reply(interaction, "\n".join([
        yt_line, gemini, f"🎞️ {len(assistant.backlog(bot.settings))} video(s) not planned yet",
        f"🕒 Next free slot: {free}", "", text,
    ]))


@app_commands.command(name="stats", description="Followers, and views / likes / comments of the last 24 h videos")
async def stats_cmd(interaction: discord.Interaction) -> None:
    await reply(interaction, f"📊 Reading {platform_names(_bot(interaction).settings)}…")
    await _bot(interaction).send_stats(interaction.channel)


@app_commands.command(name="insights", description="Which formats, games and hooks get the most views")
@app_commands.describe(days="How far back to look (default 30 days)")
async def insights_cmd(interaction: discord.Interaction, days: app_commands.Range[int, 7, 730] = 30) -> None:
    await reply(interaction, "📈 Reading every video's numbers from YouTube…")
    await _bot(interaction).send_insights(interaction.channel, days)


async def _board(interaction: discord.Interaction, fresh: bool = False) -> leaderboard.Board | None:
    """The stored comments (read from YouTube when asked, or the first time)."""
    settings = _bot(interaction).settings
    await interaction.response.defer(thinking=True)
    board = await asyncio.to_thread(leaderboard.load, settings)
    if fresh or not board.updated_at:
        if not publish.TOKEN_FILE.is_file():
            await reply(interaction, "YouTube isn't connected. Open the dashboard → Upload → Connect YouTube.")
            return None
        try:
            board, _ = await asyncio.to_thread(leaderboard.refresh, settings)
        except Exception as err:  # noqa: BLE001 - shown in the channel
            logger.exception("Comments leaderboard failed")
            await reply(interaction, f"💬 Couldn't read the YouTube comments: {err}")
            return None
    return board


def _updated(settings: Settings, board: leaderboard.Board) -> str:
    try:
        at = datetime.fromisoformat(board.updated_at).astimezone(settings.tz)
    except ValueError:
        return ""
    return f"_Comments read {at.strftime('%a %d %b %H:%M')} · `/leaderboard refresh:True` reads them again_"


@app_commands.command(name="leaderboard", description="Fans ranked by points from their YouTube comments")
@app_commands.describe(refresh="Read the comments from YouTube now and queue the replies (default: this morning's)",
                       image="The podium picture of the top 13 plus the text for your YouTube post",
                       post="Just the text for your YouTube post (tags the top 13)",
                       last_month="Last month's final standings (the board starts from 0 on the 1st)")
async def leaderboard_cmd(interaction: discord.Interaction, refresh: bool = False, image: bool = False,
                          post: bool = False, last_month: bool = False) -> None:
    board = await _board(interaction, refresh)
    if board is None:
        return
    settings = _bot(interaction).settings
    if refresh:
        await _bot(interaction).queue_replies(interaction.channel, board)
    if last_month:
        month, final = leaderboard.last_month(settings, board)
        lines = [f"👑 **{month}: final standings** ({len(final)} people)",
                 *[leaderboard.person_line(i, p) for i, p in enumerate(final[:30], 1)]]
        if final:
            lines += ["", champions_note(final), leaderboard.champions_text(month, final)]
        else:
            lines.append("Nobody commented that month.")
        await reply_lines(interaction, lines)
        return
    if (image or post) and not board.people:
        await reply(interaction, "Nobody has commented this month yet, so there's no leaderboard to post.")
        return
    if image:
        await _bot(interaction).send_post(interaction.followup.send, board)
        return
    if post:
        await reply(interaction, "📝 Your YouTube post text is the next message: long-press → Copy Text, then "
                                 "YouTube → Create → Post. Pick each @name from YouTube's list as you type it.")
        caption = leaderboard.post_caption(board.people, 3 + leaderboard_card.LIST_SIZE, leaderboard.next_drop(settings),
                                         leaderboard.month_name(leaderboard.month_start(settings)))
        await interaction.followup.send(caption, allowed_mentions=NO_PINGS, suppress_embeds=True)
        return
    month = leaderboard.month_name(leaderboard.month_start(settings))
    lines = [f"🏆 **Leaderboard, {month}**: {len(board.people)} people (resets on the 1st; "
             f"`last_month:True` for the last one)", *leaderboard.top_lines(board, 30)]
    if not board.people:
        lines.append("Nobody has commented this month yet.")
    lines += ["", f"Points: first correct answer {leaderboard.FIRST_POINTS}, correct / paradox vote "
              f"{leaderboard.CORRECT_POINTS}, any comment / try {leaderboard.COMMENT_POINTS} (best comment per video). "
              f"Skip {leaderboard.MISS_LIMIT} videos in a row: −50%. Must be subscribed (⭐) to win.",
              _updated(_bot(interaction).settings, board)]
    await reply_lines(interaction, lines)


async def youtube_video_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    board = leaderboard.load(_bot(interaction).settings)
    count: dict[str, int] = {}
    for c in board.comments:
        count[c.video_id] = count.get(c.video_id, 0) + 1
    needle = current.lower().strip()
    out = []
    for v in sorted(board.videos.values(), key=lambda v: v.published, reverse=True):
        if needle and needle not in v.name.lower():
            continue
        out.append(app_commands.Choice(name=clip_text(f"{v.name} · {count.get(v.id, 0)} comments", 100), value=v.id))
        if len(out) == 25:
            break
    return out


async def person_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    needle = current.lower().strip()
    people = leaderboard.load(_bot(interaction).settings).people
    return [app_commands.Choice(name=clip_text(f"{p['name']} · {p['points']} pts", 100), value=p["id"])
            for p in people if not needle or needle in p["name"].lower()][:25]


@app_commands.command(name="comments", description="Every comment on one YouTube video, with its points")
@app_commands.describe(video="The video (newest first)")
@app_commands.autocomplete(video=youtube_video_choices)
async def comments_cmd(interaction: discord.Interaction, video: str) -> None:
    board = await _board(interaction)
    if board is not None:
        await reply_lines(interaction, leaderboard.video_lines(board, video))


def reply_overview_lines(settings: Settings, platform: str, o: dict[str, Any], sent: dict[str, Any] | None,
                         setting: str) -> list[str]:
    lines = [f"✅ Replied: {o['replied']} fans",
             f"⏳ In the queue: {o['queued']} (one every {REPLY_EVERY_MINUTES} min, "
             f"~{o['queued'] * REPLY_EVERY_MINUTES} min)"
             + (f" · just sent one to {leaderboard.safe(sent['name'], 32)}" if sent else "")]
    if o.get("paused_until"):
        when = datetime.fromisoformat(o["paused_until"]).astimezone(settings.tz).strftime("%H:%M")
        lines.append(f"🧊 {platform}'s rate limit was hit: replies start again at {when}")
    if o["quiz_wait"]:
        when = datetime.fromisoformat(o["next_quiz"]).astimezone(settings.tz).strftime("%a %H:%M") \
            if o["next_quiz"] else "soon"
        lines.append(f"🤫 Waiting so they don't spoil the answer: {o['quiz_wait']} on quiz / paradox videos "
                     f"(replies start {leaderboard.REPLY_AFTER_HOURS} h after the video; next at {when})")
    if o["unchecked"]:
        lines.append(f"🔎 Not checked for spam yet: {o['unchecked']} (Gemini was busy; the next read tries again)")
    if o["skipped"]:
        lines.append(f"🚫 No reply (spam, links or rude): {o['skipped']}")
    if o["failed"]:
        lines.append(f"⚠️ {platform} refused: {o['failed']} (comment deleted, or comments turned off)")
    lines.append(f"📅 Sent today: {o['sent_today']} of {o['daily_max']} ({setting})"
                 + (" · the rest go out tomorrow" if o["sent_today"] >= o["daily_max"] else ""))
    return lines


@app_commands.command(name="replies", description="Read the YouTube and Instagram comments now and reply to new fans")
@app_commands.describe(now="True: reply on quiz videos too, without the 10 h wait (the replies show the answer)")
async def replies_cmd(interaction: discord.Interaction, now: bool = False) -> None:
    bot = _bot(interaction)
    settings = bot.settings
    youtube_off, ig_on = bot.replies_on(), ig_replies.enabled(settings)
    if youtube_off and not ig_on:
        return await reply(interaction, youtube_off)
    await interaction.response.defer(thinking=True)
    today = assistant.today(settings)
    lines: list[str] = []
    if youtube_off:
        lines.append(f"▶️ **YouTube replies**: {youtube_off}")
    else:
        lines.append(f"▶️ **YouTube replies** (comments from the last {leaderboard.REPLY_MAX_AGE_DAYS} days)")
        try:
            board = await bot.check_comments(interaction.channel, wait=not now)
            sent = await asyncio.to_thread(leaderboard.send_next_reply, settings, board.channel_id, today)
            o = await asyncio.to_thread(leaderboard.reply_overview, settings, board, today)
            lines += reply_overview_lines(settings, "YouTube", o, sent, "YOUTUBE_REPLY_DAILY_MAX")
        except Exception as err:  # noqa: BLE001 - shown in the channel
            logger.exception("Reading the YouTube comments for replies failed")
            lines.append(clip_text(f"Couldn't read the YouTube comments: {err}", 500))
    if ig_on:
        lines.append("")
        lines.append(f"📸 **Instagram replies** (Reels from the last {leaderboard.REPLY_MAX_AGE_DAYS} days)")
        try:
            board = await bot.check_instagram(interaction.channel, wait=not now)
            sent = await asyncio.to_thread(ig_replies.send_next_reply, settings, today)
            o = await asyncio.to_thread(ig_replies.reply_overview, settings, board, today)
            if not board.comments:
                lines.append("No fan comments visible. If your Reels have comments, the Meta app is still in "
                             "Development mode: switch it to Live (README → Instagram replies).")
            lines += reply_overview_lines(settings, "Instagram", o, sent, "INSTAGRAM_REPLY_DAILY_MAX")
        except Exception as err:  # noqa: BLE001 - shown in the channel
            logger.exception("Reading the Instagram comments for replies failed")
            lines.append(clip_text(f"Couldn't read the Instagram comments: {err}", 500))
    if settings.youtube_reply_check_hours:
        lines.append(f"_New comments are read every {settings.youtube_reply_check_hours:g} h by themselves._")
    await reply_lines(interaction, lines)


@app_commands.command(name="points", description="One fan's points and comments")
@app_commands.describe(name="Who (type part of the name)")
@app_commands.autocomplete(name=person_choices)
async def points_cmd(interaction: discord.Interaction, name: str) -> None:
    board = await _board(interaction)
    if board is not None:
        await reply_lines(interaction, leaderboard.person_lines(board, name))


@app_commands.command(name="answer", description="Set a video's quiz answer (for videos this machine didn't make)")
@app_commands.describe(video="The video", answer="The correct answer (empty for a paradox)",
                       choices="Multiple choice or paradox sides, e.g. A) Rome | B) Paris")
@app_commands.autocomplete(video=youtube_video_choices)
async def answer_cmd(interaction: discord.Interaction, video: str, answer: str = "", choices: str = "") -> None:
    settings = _bot(interaction).settings
    if not answer.strip() and len(leaderboard.parse_choices(choices)) < 2:
        return await reply(interaction, "Give the answer, or at least two choices like `A) Rome | B) Paris`.",
                           ephemeral=True)
    try:
        v = await asyncio.to_thread(leaderboard.set_answer, settings, video, answer, choices)
    except leaderboard.LeaderboardError as err:
        return await reply(interaction, str(err), ephemeral=True)
    what = f"answer ||{leaderboard.safe(v.answer, 60)}||" if v.answer else "paradox sides"
    extra = f" · choices {' | '.join(f'{l}) {t}' for l, t in v.choices)}" if v.choices else ""
    await reply(interaction, f"✅ **{leaderboard.safe(v.name, 80)}**: {what}{extra}. Points are counted again.",
                allowed_mentions=NO_PINGS)


plan_group = app_commands.Group(name="plan", description="See and change the upload plan")


@plan_group.command(name="show", description="The plan for the next days")
@app_commands.describe(days="How many days (default 3)")
async def plan_show(interaction: discord.Interaction, days: app_commands.Range[int, 1, 14] = 3) -> None:
    settings = _bot(interaction).settings
    start = assistant.today(settings)
    lines = []
    for n in range(days):
        day = start + timedelta(days=n)
        lines.append(f"**{day_label(settings, day)}**")
        items = {i.slot: i for i in assistant.planned_items(settings, day, 1)}
        for slot in planner.SLOTS:
            lines.append(item_line(settings, items[slot]) if slot in items else f"`{slot}` —")
        lines += [item_line(settings, i) for i in assistant.unslotted(settings, day)]
    await reply(interaction, "\n".join(lines))


@plan_group.command(name="add", description="Put a video on the plan")
@app_commands.describe(video="The video", day="Which day (default: the next free slot)", time="Upload time")
@app_commands.autocomplete(video=video_choices, day=day_choices)
async def plan_add(interaction: discord.Interaction, video: str, day: str | None = None,
                   time: SlotChoice | None = None) -> None:
    settings = _bot(interaction).settings
    when = assistant.parse_day(settings, day) if day else None
    if when is None and time is not None:
        when = assistant.today(settings)
    item = assistant.plan_short(settings, video, when, time)
    await reply(interaction, f"📅 Planned **{item.title}** for {day_label(settings, item.date)} at {item.slot}.")


@plan_group.command(name="remove", description="Take a video off the plan (the video itself is kept)")
@app_commands.describe(item="The planned video")
@app_commands.autocomplete(item=item_choices)
async def plan_remove(interaction: discord.Interaction, item: str) -> None:
    await act_unplan(_bot(interaction), interaction, item)


@plan_group.command(name="move", description="Move a planned video to another day or time")
@app_commands.describe(item="The planned video", day="New day", time="New upload time")
@app_commands.autocomplete(item=item_choices, day=day_choices)
async def plan_move(interaction: discord.Interaction, item: str, day: str, time: SlotChoice) -> None:
    settings = _bot(interaction).settings
    moved = assistant.move(settings, item, assistant.parse_day(settings, day), time)
    await reply(interaction, f"📅 Moved **{moved.title}** to {day_label(settings, moved.date)} at {moved.slot}.")


@app_commands.command(name="help", description="What I can do")
async def help_cmd(interaction: discord.Interaction) -> None:
    await reply(interaction, "\n".join([
        "**TrendClip bot**",
        "`/today` today's 14:30 and 23:00 videos, plus a copy-ready caption per platform",

        "`/slot time:14:30` send me the video for a slot (`day:` for another day)",
        "`/create` make a video: I ask story, math challenge, riddle, trivia trap, paradox trap or movie quiz (`game:` picks the gameplay, "
        "`text:` or ✍️ uses your own words); 🎲 **Pick for me** and the emergency videos choose the kind, game and length "
        "by what got views and retention lately (`/insights`), trying something else now and then",

        "`/plan show` · `/plan add` · `/plan remove` · `/plan move` the upload plan",
        "🏭 Every morning I make a video for each empty slot (tomorrow's, and today's if 3+ h away), plan it and "
        "post it here; it goes out by itself unless you press **Don't post this**. An hour before a slot that is "
        "still empty, the emergency video fills it.",

        "`/upload` publish on YouTube now (default: the next video planned today) · **Post on Instagram** "
        "button under a video posts it as a Reel",
        "`/videos` made but not planned yet · `/status` YouTube, Gemini, free slots",
        "`/stats` followers + views / likes / comments of the last 24 h (also in the morning summary)",
        "`/insights` which kinds of video, games, hook styles, lengths and upload times get the most views, "
        "with retention when YouTube Analytics is allowed (`days:` how far back; the best of each in the morning summary)",
        "`/leaderboard` fans ranked by points from their YouTube comments (new comments + top 10 each morning; "
        "`image:True` the podium picture + YouTube post text, `post:True` just the post text)",
        "`/comments` every comment on one video · `/points` one fan · `/answer` set a quiz answer",
        "`/replies` read the YouTube and Instagram comments now and reply to new fans (also every few hours); "
        "`now:True` replies on quiz videos too, without the 10 h spoiler wait",
        "",
        "**Dark Outliers** (its own plan, bank and YouTube channel): `/da plan` · `/da videos` · `/da status` · "
        "`/da make` a video for the next empty slot now (`slots:` more, `fetch:True` refresh the bank first) · "
        "`/da rank` a ranking video (the 5 best stories counted down; every 4th video is one by itself) · "
        "`/da fetch` · `/da upload` · `/da insights` what the channel's own numbers say. Every morning the bank is refreshed and each empty slot gets a video, posted "
        "in its channel with **Don't post this**, **Upload now** and **Delete** buttons.",
        "",
        "**Borderline Why** (map Shorts, its own YouTube channel): `/bw status` · `/bw make` the next video now · "
        "`/bw connect` the YouTube login. Every day the bot makes one video hours before its slot and posts it in its "
        "channel; press **Approve** and it goes out at the slot.",
    ]), ephemeral=True)


# --------------------------------------------------------------------------- Dark Outliers


da_group = app_commands.Group(name="da", description="Dark Outliers: the dark-stories channel (its own plan, bank and YouTube)")


def _da_video_line(v: dict, planned: dict[int, list[str]]) -> str:
    up = (v.get("uploads") or {}).get("youtube")
    where = f"▶️ {up.get('url', '')}" if up else (f"📅 {', '.join(planned.get(v['story_id'], []))}" if planned.get(v["story_id"]) else "not planned")
    return f"• **{v['title']}** · {v.get('duration_seconds', 0):.0f} s · story {v['story_id']} · {where}"


async def da_video_choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    from modules import da_plan

    out = []
    for v in await asyncio.to_thread(da_plan.videos):
        label = f"{v['title']} ({v.get('duration_seconds', 0):.0f} s)"
        if not current or current.lower() in label.lower():
            out.append(app_commands.Choice(name=label[:100], value=str(v["story_id"])))
    return out[:25]


@da_group.command(name="plan", description="The Dark Outliers plan for the next days")
@app_commands.describe(days="How many days (default 3)")
async def da_plan_cmd(interaction: discord.Interaction, days: app_commands.Range[int, 1, 14] = 3) -> None:
    from modules import da_plan, da_produce

    settings = _bot(interaction).settings
    view = await asyncio.to_thread(da_plan.view)
    by_key = {f"{i['date']} {i['slot']}": i for i in view["items"]}
    lines = []
    start = assistant.today(settings)
    for n in range(days):
        day = start + timedelta(days=n)
        lines.append(f"**{day_label(settings, day)}**")
        for slot in view["slots"]:
            item = by_key.get(f"{day.isoformat()} {slot}")
            if item is None:
                lines.append(f"`{slot}` —")
            else:
                yt = item["youtube"]
                state = f"▶️ on YouTube ({yt.get('privacy') or '?'}) {yt.get('url', '')}" if yt["done"] else "⏳ waits for its time"
                lines.append(f"`{slot}` **{item['title']}** · {state}")
    automation = f"every day at {da_produce.produce_time()}" if da_produce.auto_produce() else "off"
    lines.append(f"\n🏭 Automation: {automation} · posts: {'on' if view['auto_post'] else 'off'} as {view['privacy']}")
    await reply(interaction, "\n".join(lines))


@da_group.command(name="videos", description="Dark Outliers videos: made, planned, on YouTube")
async def da_videos_cmd(interaction: discord.Interaction) -> None:
    from modules import da_plan

    videos = await asyncio.to_thread(da_plan.videos)
    planned = await asyncio.to_thread(da_plan.planned_dates)
    if not videos:
        return await reply(interaction, "No Dark Outliers videos yet: `/da make` makes one for the next empty slot.")
    lines = [f"🎞️ **{len(videos)} Dark Outliers video(s)**"] + [_da_video_line(v, planned) for v in videos[:15]]
    waiting = [v for v in videos if not (v.get("uploads") or {}).get("youtube") and not planned.get(v["story_id"])][:4]
    buttons = [button("daplan", str(v["story_id"]), f"📅 Plan: {clip_text(v['title'], 50)}", discord.ButtonStyle.success) for v in waiting]
    await reply(interaction, "\n".join(lines), view=view_of(*buttons), suppress_embeds=True)


@da_group.command(name="status", description="Dark Outliers: YouTube connection, the story bank, the next empty slot")
async def da_status_cmd(interaction: discord.Interaction) -> None:
    from db import repository
    from modules import da_channel, da_plan, da_produce

    settings = _bot(interaction).settings
    await interaction.response.defer(thinking=True)
    yt = await asyncio.to_thread(da_channel.youtube_status)
    yt_line = (f"✅ YouTube connected ({(yt.get('channel') or {}).get('title', 'channel')} {yt.get('handle', '')})" if yt.get("connected")
               else f"❌ YouTube not connected{': ' + yt['error'] if yt.get('error') else ''} (Dark Outliers tab → Videos → Connect YouTube)")
    counts = await asyncio.to_thread(repository.counts)
    mix = await asyncio.to_thread(repository.pillar_mix)
    nxt = await asyncio.to_thread(da_plan.next_free_slot, settings, assistant.now(settings))
    targets = await asyncio.to_thread(da_produce.targets, settings, assistant.now(settings))
    await reply(interaction, "\n".join([
        yt_line,
        f"📚 Bank: {counts.get('NEW', 0)} new · {counts.get('SCRIPT_READY', 0)} scripted · {counts.get('USED', 0)} used · {counts.get('REJECTED', 0)} rejected",
        "🧭 Mix: " + " · ".join(f"{p} {m['produced']} made / {m['new']} new" for p, m in mix.items()),
        f"🕒 Next free slot: {day_label(settings, nxt[0]) + ' ' + nxt[1] if nxt else 'none in 30 days'} · "
        f"the morning run would fill: {', '.join(day_label(settings, d) + ' ' + s for d, s in targets) or 'nothing'}",
        f"🏭 Automation: {'every day at ' + da_produce.produce_time() if da_produce.auto_produce() else 'off'} · "
        f"posts {'on' if da_plan.auto_post() else 'off'} as {da_plan.upload_privacy()} at {', '.join(da_plan.slots())}",
    ]))


@da_group.command(name="make", description="Make a Dark Outliers video now for the next empty slot (story → script → video → plan)")
@app_commands.describe(slots="How many empty slots to fill (default 1)", fetch="Refresh the story bank first (default no)")
async def da_make_cmd(interaction: discord.Interaction, slots: app_commands.Range[int, 1, 4] = 1, fetch: bool = False) -> None:
    from modules import da_produce

    bot = _bot(interaction)
    settings = bot.settings
    targets = await asyncio.to_thread(da_produce.targets, settings, assistant.now(settings))
    if not targets:
        return await reply(interaction, "Every Dark Outliers slot of today and tomorrow is planned; nothing to make.", ephemeral=True)
    wanted = targets[:slots]
    await reply(interaction, f"🏭 Making {len(wanted)} Dark Outliers video(s): {', '.join(day_label(settings, d) + ' ' + s for d, s in wanted)}. "
                             f"Progress in the Dark Outliers channel.", ephemeral=True)

    async def go() -> None:
        if fetch:
            channel = await bot.darkarchives_channel()
            if channel is not None:
                try:
                    await bot.run_job(channel, "Refreshing the story bank", da_produce.refresh_bank, bot.render_lock)
                except JobFailed:
                    pass
        for day, slot in wanted:
            await bot.darkarchives_produce(only=(day, slot))

    task = asyncio.create_task(go())
    bot.jobs.add(task)
    task.add_done_callback(bot._job_done)


@da_group.command(name="rank", description="Make a Dark Outliers ranking video now: the bank's 5 best stories counted down, for the next slot")

async def da_rank_cmd(interaction: discord.Interaction) -> None:
    from modules import da_produce

    bot = _bot(interaction)
    settings = bot.settings
    targets = await asyncio.to_thread(da_produce.targets, settings, assistant.now(settings))
    if not targets:
        return await reply(interaction, "Every Dark Outliers slot of today and tomorrow is planned; nothing to make.", ephemeral=True)
    day, slot = targets[0]
    await reply(interaction, f"🏆 Making a ranking video for {day_label(settings, day)} {slot}. Progress in the Dark Outliers channel.",
                ephemeral=True)

    async def go() -> None:
        channel = await bot.darkarchives_channel()
        if channel is not None:
            await bot.darkarchives_produce_one(channel, day, slot, ranking=True)

    task = asyncio.create_task(go())
    bot.jobs.add(task)
    task.add_done_callback(bot._job_done)


@da_group.command(name="fetch", description="Refresh the Dark Outliers story bank now (Reddit, Wikipedia, Gemini, RSS)")
async def da_fetch_cmd(interaction: discord.Interaction) -> None:
    from modules import da_produce

    await interaction.response.defer(thinking=True)
    report = await asyncio.to_thread(da_produce.refresh_bank)
    lines = [f"• {name}: " + (f"❌ {r['error']}" if "error" in r else f"{r['saved']} new ({r['fetched']} fetched, {r['duplicates']} known)")
             for name, r in report.items()]
    await reply(interaction, "📚 **Story bank refreshed**\n" + "\n".join(lines))


@da_group.command(name="insights", description="Dark Outliers: which videos, pillars and hooks get the views, and what the scripts learn")
@app_commands.describe(refresh="Read YouTube now instead of the last few hours' numbers (default no)")
async def da_insights_cmd(interaction: discord.Interaction, refresh: bool = False) -> None:
    from modules import da_insights

    await interaction.response.defer(thinking=True)
    try:
        data = await asyncio.to_thread(da_insights.refresh, _bot(interaction).settings, force=refresh)
    except Exception as err:  # noqa: BLE001 - shown to you
        return await reply(interaction, clip_text(f"📈 Couldn't read the Dark Outliers insights: {err}"))
    await reply(interaction, "\n".join(da_insights.format_report(da_insights.report(data))), suppress_embeds=True)


@da_group.command(name="upload", description="Upload a Dark Outliers video to YouTube now")
@app_commands.describe(video="The video")
@app_commands.autocomplete(video=da_video_choices)
async def da_upload_cmd(interaction: discord.Interaction, video: str) -> None:
    await act_da_upload_confirm(_bot(interaction), interaction, video)


# --------------------------------------------------------------------------- Borderline Why


bw_group = app_commands.Group(name="bw", description="Borderline Why: map Shorts answering geography and history questions")


@bw_group.command(name="status", description="Borderline Why: YouTube login, the next video, the topics left")
async def bw_status_cmd(interaction: discord.Interaction) -> None:
    from modules import bw_channel, bw_topics

    settings = _bot(interaction).settings
    await interaction.response.defer(thinking=True)
    yt = await asyncio.to_thread(bw_channel.youtube_status)
    yt_line = (f"✅ YouTube connected ({(yt.get('channel') or {}).get('title', 'channel')})" if yt.get("connected")
               else f"❌ YouTube not connected{': ' + yt['error'] if yt.get('error') else ''} (`/bw connect`)")
    nxt = bw_channel.next_job(settings, assistant.now(settings))
    last = (await asyncio.to_thread(bw_channel.videos, None, 1) or [None])[0]
    up = ((last or {}).get("uploads") or {}).get("youtube") or {}
    topics = await asyncio.to_thread(bw_topics.summary, bw_channel.out_root())
    queued = len(bw_channel.queued_scripts())
    await reply(interaction, "\n".join([
        yt_line,
        f"🕒 Next: {day_label(settings, nxt.day) + ' ' + nxt.slot + ' (made at ' + nxt.start.strftime('%H:%M') + ')' if nxt else 'none'}",
        (f"🎬 Last: {last['title']} ({last['key']}, {last.get('duration_seconds', 0):.0f} s) · "
         + (f"▶️ {up.get('url')}" if up else "approved" if last.get("approved") else "waiting for Approve")) if last else "🎬 No video yet",
        f"🧭 Topics: {topics['left']} left, {topics['used']} used, {topics['rejected']} dropped · {queued} hand-checked script(s) queued"
        + (f" · next: {topics['next']}" if topics["next"] else ""),
        f"🏭 Automation: making {'on' if bw_channel.auto_make() else 'off'} · posting {'on' if bw_channel.auto_post() else 'off'} · "
        f"slot {', '.join(bw_channel.slots())} · approval {'required' if bw_channel.require_approval() else 'not required'} · "
        f"as {bw_channel.upload_privacy()}",
    ]))


@bw_group.command(name="make", description="Make the next Borderline Why video now (topic → research → script → maps)")
async def bw_make_cmd(interaction: discord.Interaction) -> None:
    from modules import bw_channel

    bot = _bot(interaction)
    now = assistant.now(bot.settings)
    job = bw_channel.next_job(bot.settings, now) or bw_channel.job_for(bot.settings, now.date())
    try:
        await asyncio.to_thread(bw_channel.set_aside, job.key)
    except bw_channel.ChannelError as err:
        return await reply(interaction, f"🗺️ {err}", ephemeral=True)
    await reply(interaction, f"🗺️ Making the Borderline Why video for {day_label(bot.settings, job.day)} {job.slot}. "
                             "Progress in the Borderline Why channel.", ephemeral=True)
    bot._spawn(bot.borderlinewhy_make(job))


@bw_group.command(name="connect", description="How to connect the Borderline Why YouTube channel")
async def bw_connect_cmd(interaction: discord.Interaction) -> None:
    port = 8000  # the dashboard's port on the hub (trendclip.service)
    await reply(interaction, "\n".join([
        "Connect the **Borderline Why** YouTube channel (sign in with the Google account that owns @BorderlineWhy):",
        f"1. On your Mac: `ssh -L {port}:localhost:{port} Bot` (keeps a tunnel to the hub open)",
        f"2. Open http://localhost:{port}/api/borderlinewhy/youtube/connect and pick the Borderline Why channel",
        "3. `/bw status` should then say ✅ YouTube connected.",
    ]), ephemeral=True)


COMMANDS = [today_cmd, slot_cmd, create_cmd, upload_cmd, videos_cmd, status_cmd, stats_cmd, insights_cmd, leaderboard_cmd,
            comments_cmd, replies_cmd, points_cmd, answer_cmd, plan_group, da_group, bw_group, help_cmd]



def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        settings = get_settings()
    except ConfigError as err:
        print(err, file=sys.stderr)
        return 1
    if not settings.discord_bot_token:
        print("Set DISCORD_BOT_TOKEN in .env (see README: Discord bot).", file=sys.stderr)
        return 1
    if not settings.discord_guild_id:
        logger.warning("DISCORD_GUILD_ID is not set: commands are registered globally and anyone who adds "
                       "the bot could use it")
    TrendClipBot(settings).run(settings.discord_bot_token.get_secret_value(), log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
