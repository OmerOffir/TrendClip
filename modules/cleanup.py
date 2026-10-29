"""Old videos off the disk: once a day the bot deletes the generated media in output/ (videos, voices, pictures,
subtitles) older than CLEANUP_KEEP_DAYS (default 30; 0 turns it off) that nothing still needs.

Still needed, whatever its age:
  SideQuestLogic  a Short (output/shorts/<name>.*, and its copies in output/discord/ and output/instagram/) not on
                  YouTube yet, or planned (planner.json) for today or later
  Dark Outliers   story_<id>.mp4, its story_<id>/ folder and Discord copy while the video is not on YouTube yet, or
                  is planned (darkarchives/plan.json) for today or later and not posted
  Borderline Why  a slot's folder (<date>_<HHMM>/) whose video is not on YouTube yet, unless vetoed; a folder set
                  aside (<key>.old-<time>) is a leftover
A channel whose state can't be read keeps all its files. JSON, logs, databases and output/samples/ are never
touched. Files nothing claims (test renders, a removed channel's folder, failed renders) are leftovers.

    python -m modules.cleanup              # dry run: what would go, with sizes
    python -m modules.cleanup --delete     # delete it
    python -m modules.cleanup --days 7     # another age (dry run unless --delete)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date as Day
from pathlib import Path
from typing import Any

from modules.scene_images import setting

logger = logging.getLogger(__name__)

DEFAULT_KEEP_DAYS = 30
MEDIA = {".mp4", ".mov", ".webm", ".mkv", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".jpg", ".jpeg", ".png", ".webp",
         ".ass", ".srt"}
KEEP_DIRS = {"samples"}  # top-level output folders the owner keeps by hand
BW_FOLDER = re.compile(r"\d{4}-\d{2}-\d{2}_\d{4}(\.old-\d+)?")
DA_STORY = re.compile(r"story_(\d+)(\..+)?")


def keep_days() -> int:
    try:
        return max(0, int(setting("CLEANUP_KEEP_DAYS", str(DEFAULT_KEEP_DAYS))))
    except ValueError:
        return DEFAULT_KEEP_DAYS


@dataclass
class Report:
    days: int
    deleted: bool
    files: list[dict[str, Any]] = field(default_factory=list)  # {"path" (relative to output/), "bytes", "date"}
    kept: int = 0  # old media files kept because something still needs them
    errors: list[str] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(f["bytes"] for f in self.files)

    def summary(self) -> str:
        verb = "Deleted" if self.deleted else "Would delete"
        text = f"{verb} {len(self.files)} files older than {self.days} days ({human(self.bytes)}); kept {self.kept} still needed"
        return text + (f"; {len(self.errors)} could not be deleted" if self.errors else "")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _on_youtube(data: dict) -> bool:
    return bool((data.get("uploads") or {}).get("youtube") or (data.get("posted") or {}).get("youtube"))


def _shorts_needed(settings, root: Path, today: Day) -> set[str]:
    from trendclip import planner

    needed = set()
    for sidecar in (root / "shorts").glob("*.json"):
        if not sidecar.name.endswith(".words.json") and not _on_youtube(_json(sidecar)):
            needed.add(sidecar.stem)
    for item in planner.load(settings).items:
        if item.short and item.date >= today:
            needed.add(Path(item.short).stem)
    return needed


def _dark_needed(root: Path, today: Day) -> set[str]:
    from modules import da_plan

    out = root / "darkarchives"
    plan = da_plan.load(out)
    posted = {i.story_id for i in plan.items if i.posted.get("youtube")}
    needed = set()
    for info in out.glob("story_*.json"):
        if (m := DA_STORY.fullmatch(info.stem)) and int(m.group(1)) not in posted and not _on_youtube(_json(info)):
            needed.add(m.group(1))
    for video in out.glob("story_*.mp4"):  # a video without its info file: not known to be on YouTube
        if (m := DA_STORY.fullmatch(video.stem)) and not video.with_suffix(".json").is_file():
            needed.add(m.group(1))
    for item in plan.items:
        if item.date >= today and not item.posted.get("youtube"):
            needed.add(str(item.story_id))
    return needed


def _borderline_needed(root: Path) -> set[str]:
    needed = set()
    for folder in (root / "borderlinewhy").glob("*_*"):
        if not folder.is_dir() or not BW_FOLDER.fullmatch(folder.name) or ".old-" in folder.name:
            continue
        info = folder / "info.json"
        data = _json(info)
        if info.is_file() and not _on_youtube(data) and not data.get("veto"):
            needed.add(folder.name)
    return needed


def owner(rel: Path) -> tuple[str, str] | None:
    """Which video a file under output/ belongs to: ("shorts", stem), ("darkarchives", story id),
    ("borderlinewhy", folder) or None for a leftover nothing claims."""
    parts = rel.parts
    if len(parts) < 2:
        return None
    if parts[0] in ("shorts", "discord", "instagram") and len(parts) == 2:
        return "shorts", parts[1].split(".", 1)[0]
    if parts[0] == "darkarchives":
        name = parts[2] if parts[1] == "discord" and len(parts) == 3 else parts[1]
        if m := DA_STORY.fullmatch(name):
            return "darkarchives", m.group(1)
    if parts[0] == "borderlinewhy" and len(parts) > 2 and BW_FOLDER.fullmatch(parts[1]):
        return "borderlinewhy", parts[1]
    return None


def needed(settings, root: Path, today: Day) -> dict[str, set[str] | None]:
    """Per channel the videos still needed; None: its state couldn't be read, so all its files stay."""
    out: dict[str, set[str] | None] = {}
    for name, source in (("shorts", lambda: _shorts_needed(settings, root, today)),
                         ("darkarchives", lambda: _dark_needed(root, today)),
                         ("borderlinewhy", lambda: _borderline_needed(root))):
        try:
            out[name] = source()
        except Exception:  # noqa: BLE001 - unreadable state keeps the channel's files, never deletes them
            logger.exception("Cleanup: can't read what %s still needs; keeping all its files", name)
            out[name] = None
    return out


def run(settings, days: int | None = None, delete: bool = False, now: float | None = None) -> Report:
    """Find (and with `delete`, remove) the old media nothing needs under settings.output_dir."""
    days = keep_days() if days is None else days
    report = Report(days=days, deleted=delete)
    root = settings.output_dir
    if days <= 0 or not root.is_dir():
        return report
    now = time.time() if now is None else now
    cutoff = now - days * 86400
    keep = needed(settings, root, Day.fromtimestamp(now))
    touched: set[Path] = set()
    for folder, dirs, files in os.walk(root):  # never follows links into other folders
        here = Path(folder)
        if here == root:
            dirs[:] = [d for d in dirs if d not in KEEP_DIRS]
        for name in files:
            path = here / name
            if path.suffix.lower() not in MEDIA or path.is_symlink():
                continue
            stat = path.stat()
            if stat.st_mtime >= cutoff:
                continue
            rel = path.relative_to(root)
            who = owner(rel)
            if who is not None and (keep[who[0]] is None or who[1] in keep[who[0]]):
                report.kept += 1
                continue
            entry = {"path": rel.as_posix(), "bytes": stat.st_size, "date": Day.fromtimestamp(stat.st_mtime).isoformat()}
            if delete:
                try:
                    path.unlink()
                except OSError as err:
                    report.errors.append(f"{rel}: {err}")
                    continue
                touched.add(here)
            report.files.append(entry)
    for folder in sorted(touched, key=lambda p: len(p.parts), reverse=True):
        while len(folder.relative_to(root).parts) >= 3 and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()  # a video's pictures/ or clips/ folder left empty; the channels' own folders stay
            folder = folder.parent
    report.files.sort(key=lambda f: f["path"])
    logger.info("Cleanup: %s", report.summary())
    return report


def main(argv: list[str] | None = None) -> None:
    from trendclip.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=None, help=f"age in days (default CLEANUP_KEEP_DAYS, {DEFAULT_KEEP_DAYS})")
    parser.add_argument("--delete", action="store_true", help="delete; without it, only list")
    args = parser.parse_args(argv)
    report = run(get_settings(), args.days, args.delete)
    for f in report.files:
        print(f"{human(f['bytes']):>9}  {f['date']}  {f['path']}")
    print(report.summary())
    for err in report.errors:
        print("  !", err)


if __name__ == "__main__":
    main()
