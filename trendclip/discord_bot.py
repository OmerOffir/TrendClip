"""Discord bot: today's videos, random Shorts, the upload plan and YouTube uploads from slash commands.

Run: ./run.sh bot   (or: python3 run.py --bot). Needs DISCORD_BOT_TOKEN and DISCORD_GUILD_ID in .env;
reminders go to DISCORD_CHANNEL_ID. Heavy work (download, Gemini, render, upload) runs on threads;
buttons keep working after a restart (their custom ids carry the action).
"""

import asyncio
import logging
import sys
from datetime import date as Day, timedelta
from typing import Any, Callable, Literal

import discord
from discord import app_commands
from discord.ext import tasks

from . import assistant, channel_stats, planner, publish, script_writer, shorts, video_downloader
from .config import ConfigError, Settings, get_settings

logger = logging.getLogger(__name__)

MESSAGE_MAX = 2000
CUSTOM_ID_MAX = 100
PROGRESS_EVERY = 4.0  # seconds between progress message edits (Discord rate limits edits)
SlotChoice = Literal["18:00", "23:00"]


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
        self.sent = assistant.SentLog(settings)
        self._games: list[str] | None = None

    async def setup_hook(self) -> None:
        if n := planner.assign_slots(self.settings, assistant.today(self.settings)):
            logger.info("Gave %d planned reels an upload time (18:00 / 23:00)", n)
        self.add_dynamic_items(ActionButton)
        for command in COMMANDS:
            self.tree.add_command(command)
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            self.tree.clear_commands(guild=None)
            synced = await self.tree.sync(guild=guild)
            await self.tree.sync()  # drop global copies from earlier runs
        else:
            synced = await self.tree.sync()
        logger.info("Registered %d slash commands", len(synced))
        self.reminders.start()

    async def on_ready(self) -> None:
        logger.info("Discord bot ready as %s", self.user)

    # ---- shared work ------------------------------------------------------------------

    async def run_job(self, channel: discord.abc.Messageable, title: str, fn: Callable[[assistant.ProgressFn], Any],
                      lock: asyncio.Lock) -> Any:
        """Run `fn(progress)` on a thread, keeping one message up to date with its progress."""
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
                result = await asyncio.to_thread(fn, progress)
            except Exception as err:  # noqa: BLE001 - shown in the channel
                logger.exception("%s failed", title)
                await msg.edit(content=clip_text(f"❌ {title} failed: {err}"))
                raise JobFailed(str(err)) from err
            finally:
                task.cancel()
        await msg.edit(content=f"✅ {title}: done")
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

    async def create_random(self, interaction: discord.Interaction, game: str | None, kind: str = "story") -> None:
        if script_writer.gemini_api_key(self.settings) is None:
            return await reply(interaction, "Add GEMINI_API_KEY to .env first (it writes the story).", ephemeral=True)
        noun = {"math": "math challenge", "riddle": "riddle"}.get(kind, "video")
        what = f"random {game} {noun}" if game else f"random {noun}"
        await reply(interaction, f"On it: creating a {what}. This takes a few minutes; I'll post it here.")
        short = await self.run_job(interaction.channel, f"Creating a {what}",
                                   lambda p: assistant.create_random_short(self.settings, game, p, kind),
                                   self.render_lock)
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
        view = view_of(button("cm", filename, "Post the pinned comment")) if comment else None
        await channel.send(clip_text(text), view=view)

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
        if not self.settings.discord_channel_id:
            return
        at = assistant.now(self.settings)
        for reminder in assistant.due_reminders(self.settings, at, self.sent.load()):
            try:
                channel = self.get_channel(self.settings.discord_channel_id) or await self.fetch_channel(
                    self.settings.discord_channel_id)
                if reminder.kind == "morning":
                    await self.morning_summary(channel, reminder.day)
                else:
                    await self.slot_reminder(channel, reminder.day, reminder.slot)
            except discord.HTTPException:
                logger.exception("Reminder %s failed; retrying next minute", reminder.key)
                continue
            self.sent.add(reminder.key, at.date())

    @reminders.before_loop
    async def _before_reminders(self) -> None:
        await self.wait_until_ready()

    async def morning_summary(self, channel: discord.abc.Messageable, day: Day) -> None:
        text, buttons, empty = self.today_text(day)
        waiting = len(assistant.backlog(self.settings))
        text = "☀️ Good morning! Here's today's upload plan.\n" + text
        if waiting:
            text += f"\n{waiting} video(s) are made but not planned yet (/videos)."
        if empty:
            text += "\nSome slots are empty: plan a video with /plan add, or let me make one."
            buttons.append(button("mk", "-", "Create a random video", discord.ButtonStyle.success))
        await channel.send(clip_text(text), view=view_of(*buttons))
        await self.send_stats(channel)

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

    async def slot_reminder(self, channel: discord.abc.Messageable, day: Day, slot: str) -> None:
        minutes = self.settings.discord_reminder_minutes
        entry = next(e for e in assistant.day_slots(self.settings, day) if e.slot == slot)
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
        await self.send_video(channel, entry.short.filename,
                              f"⏰ {slot} upload in {minutes} minutes: **{entry.short.title}**\n{short_meta(entry.short)}",
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


async def act_make(bot: TrendClipBot, interaction: discord.Interaction, _arg: str) -> None:
    await bot.create_random(interaction, None)


ACTIONS: dict[str, Callable[[TrendClipBot, discord.Interaction, str], Any]] = {
    "show": act_show, "up": act_upload_confirm, "upgo": act_upload, "no": act_cancel, "cm": act_comment,
    "pl": act_plan_next, "pt": act_plan_today, "rm": act_unplan, "del": act_delete_confirm, "delgo": act_delete,
    "mk": act_make,
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


@app_commands.command(name="today", description="Which videos go out today at 18:00 and 23:00")
async def today_cmd(interaction: discord.Interaction) -> None:
    bot = _bot(interaction)
    text, buttons, empty = bot.today_text(assistant.today(bot.settings))
    if any(b and b.action == "show" for b in buttons):
        text += "\nWant to see them?"
    if empty:
        buttons.append(button("mk", "-", "Create a random video", discord.ButtonStyle.success))
    await reply(interaction, text, view=view_of(*buttons))


@app_commands.command(name="slot", description="Send me the video planned for 18:00 or 23:00")
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


@app_commands.command(name="create", description="Create a random video (random gameplay + a story or a quiz)")
@app_commands.describe(game="Optional: the gameplay to use (default: a random game)",
                       type="Story (default), math challenge or riddle")
@app_commands.choices(type=[app_commands.Choice(name="📖 Story", value="story"),
                            app_commands.Choice(name="🧮 Math challenge", value="math"),
                            app_commands.Choice(name="🧩 Riddle", value="riddle")])
@app_commands.autocomplete(game=game_choices)
async def create_cmd(interaction: discord.Interaction, game: str | None = None,
                     type: app_commands.Choice[str] | None = None) -> None:  # noqa: A002 - the option's name in Discord
    await _bot(interaction).create_random(interaction, (game or "").strip() or None, type.value if type else "story")


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
    await reply(interaction, "📊 Reading YouTube, TikTok and Instagram…")
    await _bot(interaction).send_stats(interaction.channel)


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
        "`/today` today's 18:00 and 23:00 videos (with Show / Upload buttons)",
        "`/slot time:18:00` send me the video for a slot (`day:` for another day)",
        "`/create` make a random video (`game:` to pick the gameplay, `type:` story, math challenge or riddle)",
        "`/plan show` · `/plan add` · `/plan remove` · `/plan move` the upload plan",
        "`/upload` publish on YouTube now (default: the next video planned today)",
        "`/videos` made but not planned yet · `/status` YouTube, Gemini, free slots",
        "`/stats` followers + views / likes / comments of the last 24 h (also in the morning summary)",
    ]), ephemeral=True)


COMMANDS = [today_cmd, slot_cmd, create_cmd, upload_cmd, videos_cmd, status_cmd, stats_cmd, plan_group, help_cmd]


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
