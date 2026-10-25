"""DarkArchives production run: the channel fills its own plan, like the gameplay channel's morning run.

    python -m modules.da_produce              # fetch stories, then a video for every empty slot (see below)
    python -m modules.da_produce --no-fetch --slots 1

Once a day (DA_AUTO_PRODUCE_TIME, default 09:30, by the Discord bot; DA_AUTO_PRODUCE=0 turns it off):
  1. the bank is refreshed (modules.fetcher, all sources; DA_DAILY_FETCH=0 skips it);
  2. every empty slot of tomorrow, and of today still PRODUCE_LEAD away, gets a video:
     - the best NEW story of the pillar the mix needs most (repository.get_next_story),
     - its Shorts script (modules.script_gen; a story Gemini rejects is marked and the next one is tried),
     - the video (modules.video_gen),
     - a check (check_video): length between DA_MIN_SECONDS and DA_MAX_SECONDS, real pictures (not the
       placeholder cards), enough words; a video that fails is deleted and the next story is tried,
       MAX_STORY_TRIES stories per slot in all;
     - the slot (modules.da_plan): it goes out by itself at its time (da_plan / the bot).
  The bot shows each video in the DarkArchives Discord channel with a "Don't post this" button.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date as Day, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from db import repository
from modules import da_plan, fetcher, script_gen, video_gen
from modules.scene_images import setting

logger = logging.getLogger(__name__)

PRODUCE_LEAD = timedelta(hours=3)  # today's slots at least this far away are filled too
MAX_STORY_TRIES = 4  # stories scripted / made for one slot before giving up on it
DEFAULT_TIME = "09:30"
ProgressFn = Callable[[float | None, str], None]


class ProduceError(RuntimeError):
    pass


def auto_produce() -> bool:
    return setting("DA_AUTO_PRODUCE", "1").strip().lower() not in ("0", "false", "off", "none")


def produce_time() -> str:
    value = setting("DA_AUTO_PRODUCE_TIME", DEFAULT_TIME).strip()
    return value if len(value) == 5 and value[2] == ":" and value.replace(":", "").isdigit() else DEFAULT_TIME


def daily_fetch() -> bool:
    return setting("DA_DAILY_FETCH", "1").strip().lower() not in ("0", "false", "off", "none")


def length_limits() -> tuple[float, float]:
    try:
        lo, hi = float(setting("DA_MIN_SECONDS", "25")), float(setting("DA_MAX_SECONDS", "90"))
    except ValueError:
        lo, hi = 25.0, 90.0
    return (lo, hi) if lo < hi else (25.0, 90.0)


def targets(settings, at: datetime, out_dir: Path | None = None) -> list[tuple[Day, str]]:
    """The slots the run fills: today's empty ones still PRODUCE_LEAD away, then tomorrow's empty ones."""
    at = at.astimezone(settings.tz)
    out = []
    for day in (at.date(), at.date() + timedelta(days=1)):
        for slot in da_plan.slots():
            if da_plan.slot_item(day, slot, out_dir) is not None:
                continue
            if da_plan.slot_time(settings, day, slot) - at >= PRODUCE_LEAD:
                out.append((day, slot))
    return out


def produce_key(day: Day) -> str:
    return f"da {day.isoformat()} produce"


def refresh_bank(progress: ProgressFn = lambda f, m: None, db_path=None) -> dict[str, dict]:
    """Fetch from every source into the bank (one failing source never stops the others)."""
    def each(name, report):
        progress(None, f"Fetching stories: {name}" if name else "Fetched")

    return fetcher.run(fetcher.SOURCES, db_path=db_path, progress=each)


def check_video(info: dict, min_words: int = 70) -> list[str]:
    """Why a finished video should not go out (empty = fine)."""
    lo, hi = length_limits()
    problems = []
    seconds = float(info.get("duration_seconds") or 0)
    if not lo <= seconds <= hi:
        problems.append(f"{seconds:.0f} s long (wanted {lo:.0f}-{hi:.0f})")
    pictures = str(info.get("pictures") or "")
    if "placeholder" in pictures and setting("DA_ALLOW_PLACEHOLDER", "0") not in ("1", "true", "on"):
        problems.append(f"placeholder pictures ({pictures})")
    if int(info.get("words") or 0) < min_words:
        problems.append(f"only {info.get('words')} words")
    return problems


def next_script(progress: ProgressFn = lambda f, m: None, db_path=None, scripts_dir: Path | None = None,
                skip: set[int] | None = None, tries: int = MAX_STORY_TRIES) -> dict:
    """Script the best NEW story; a story Gemini rejects is marked REJECTED and the next one is tried.
    Returns script_gen.process's result (status SCRIPT_READY)."""
    skip = set(skip or ())
    for _ in range(tries):
        story = repository.get_next_story(db_path)
        while story and story["id"] in skip:
            repository.set_status(story["id"], "REJECTED", db_path, note="production run: skipped")
            story = repository.get_next_story(db_path)
        if story is None:
            raise ProduceError("No NEW stories left in the bank (fetch more)")
        progress(None, f"Writing the script: {story['title'][:60]}")
        result = script_gen.process(story["id"], out_dir=scripts_dir, db_path=db_path)
        if result["status"] == "SCRIPT_READY":
            return result
        progress(None, f"Gemini rejected \"{story['title'][:40]}\": {result.get('reason', '')[:80]}")
    raise ProduceError(f"{tries} stories in a row were rejected")


def produce_one(settings, day: Day, slot: str, progress: ProgressFn = lambda f, m: None, *, db_path=None,
                scripts_dir: Path | None = None, out_dir: Path | None = None, make=None, tries: int = MAX_STORY_TRIES,
                announce: bool = False) -> dict:
    """One slot: story -> script -> video -> check -> plan. {"status": "planned" | "taken", "story_id",
    "item", "info", "tried": [...]} ; raises ProduceError when no story made a good video. `announce`:
    post it in the Discord channel (the bot's own run posts with a veto button instead)."""
    make = make or video_gen.make_video
    tried: list[dict[str, Any]] = []
    for attempt in range(tries):
        if da_plan.slot_item(day, slot, out_dir) is not None:
            return {"status": "taken", "day": day.isoformat(), "slot": slot, "tried": tried}
        scripted = next_script(progress, db_path, scripts_dir, skip={t["story_id"] for t in tried}, tries=tries - attempt)
        story_id = scripted["story"]["id"]
        progress(0.0, f"Making the video: {scripted['script'].title[:60]}")
        info = make(story_id, progress, out_dir=out_dir, scripts_dir=scripts_dir, db_path=db_path, settings=settings,
                    announce=False)
        problems = check_video(info)
        if not problems:
            item = da_plan.add_item(da_plan.ItemRequest(story_id=story_id, date=day, slot=slot), out_dir)
            tried.append({"story_id": story_id, "title": info.get("title", ""), "ok": True})
            if announce:
                from modules import da_channel

                da_channel.announce_video(story_id, info, settings, out_dir, note=(
                    f"Planned for {day.isoformat()} {slot}: it goes out by itself as {da_plan.upload_privacy()}. "
                    "Don't want it? Take it off the plan in the DarkArchives tab."))
            return {"status": "planned", "day": day.isoformat(), "slot": slot, "story_id": story_id,
                    "item": item.model_dump(mode="json"), "info": info, "tried": tried}
        progress(None, f"Not good enough ({'; '.join(problems)}): trying another story")
        logger.warning("Story %s video rejected by the check: %s", story_id, problems)
        video_gen.delete_video(story_id, out_dir, db_path)
        repository.set_status(story_id, "REJECTED", db_path, note="production check: " + "; ".join(problems)[:200])
        tried.append({"story_id": story_id, "title": info.get("title", ""), "ok": False, "problems": problems})
    raise ProduceError("No story made a good video: " + " | ".join(
        f"{t['title'][:30]}: {'; '.join(t.get('problems', []))}" for t in tried))


def run(settings, at: datetime | None = None, progress: ProgressFn = lambda f, m: None, *, fetch: bool | None = None,
        max_slots: int | None = None, db_path=None, scripts_dir: Path | None = None, out_dir: Path | None = None,
        make=None, announce: bool = False) -> dict:
    """The whole run: refresh the bank, then a video for every target slot. Never raises for one slot:
    its failure is in the result."""
    at = at or datetime.now(settings.tz)
    result: dict[str, Any] = {"fetch": None, "slots": []}
    if fetch if fetch is not None else daily_fetch():
        try:
            result["fetch"] = refresh_bank(progress, db_path)
        except Exception as err:  # noqa: BLE001 - the bank may still have stories
            logger.exception("Daily fetch failed")
            result["fetch"] = {"error": str(err)[:200]}
    wanted = targets(settings, at, out_dir)
    if max_slots is not None:
        wanted = wanted[:max_slots]
    for day, slot in wanted:
        try:
            result["slots"].append(produce_one(settings, day, slot, progress, db_path=db_path, scripts_dir=scripts_dir,
                                               out_dir=out_dir, make=make, announce=announce))
        except Exception as err:  # noqa: BLE001
            logger.exception("Production for %s %s failed", day, slot)
            result["slots"].append({"status": "failed", "day": day.isoformat(), "slot": slot, "error": str(err)[:300]})
    return result


def main() -> None:
    from trendclip.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-fetch", action="store_true", help="don't refresh the bank first")
    parser.add_argument("--slots", type=int, help="at most this many slots")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    def show(frac, msg):
        print(f"[{'' if frac is None else f'{frac:4.0%}'}] {msg}")

    settings = get_settings()
    print("targets:", [f"{d} {s}" for d, s in targets(settings, datetime.now(settings.tz))] or "none (every slot is planned)")
    result = run(settings, progress=show, fetch=not args.no_fetch, max_slots=args.slots, announce=True)
    if result["fetch"]:
        print("fetch:", {k: v.get("saved", v.get("error")) for k, v in result["fetch"].items()})
    for r in result["slots"]:
        if r["status"] == "planned":
            print(f"{r['day']} {r['slot']}: planned story {r['story_id']} \"{r['info']['title']}\" ({r['info']['duration_seconds']}s)")
        else:
            print(f"{r['day']} {r['slot']}: {r['status']} {r.get('error', '')}")


if __name__ == "__main__":
    main()
