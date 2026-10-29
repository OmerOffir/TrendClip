"""The Gemini free tier's daily budget, shared by every process on the machine (output/gemini_quota.json).

The free tier allows about 20 generate requests a day per model, and Google's day ends at midnight in
California (09:00 or 10:00 in Israel), so a morning job runs on what yesterday left. This ledger remembers,
per model, how many requests went through today and until when Google said "come back later" (the 429's
retryDelay, or the next reset when it gave none), so that:

- script_writer skips a model Google has already closed instead of asking it again (`open_models`);
- a job that cannot run tells the bot WHEN to try again (`QuotaError.retry_at`) instead of every 30 minutes;
- jobs that can wait a day (comment checks, bank refills, topic proposals) step aside when the budget is
  low (`low`), keeping what is left for the scheduled videos.

The counts are estimates (Google counts, we only watch), good enough to decide what to skip.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import PROJECT_ROOT

logger = logging.getLogger(__name__)

QUOTA_TZ = ZoneInfo("America/Los_Angeles")  # Google's quota day
FREE_DAILY = 20  # generate requests per model per day on the free tier (GEMINI_FREE_DAILY)
RESERVE = 6  # calls kept for the scheduled videos: optional jobs skip below this (GEMINI_RESERVE)
LEDGER = PROJECT_ROOT / "output" / "gemini_quota.json"
_RETRY = re.compile(r"retry(?:Delay)?['\":\s]*(?:in\s*)?'?(\d+(?:\.\d+)?)s", re.IGNORECASE)


def _setting(name: str, default: str) -> str:
    from dotenv import dotenv_values

    return (dotenv_values(PROJECT_ROOT / ".env").get(name) or os.getenv(name) or default).strip()


def free_daily() -> int:
    try:
        return max(1, int(_setting("GEMINI_FREE_DAILY", str(FREE_DAILY))))
    except ValueError:
        return FREE_DAILY


def reserve() -> int:
    try:
        return max(0, int(_setting("GEMINI_RESERVE", str(RESERVE))))
    except ValueError:
        return RESERVE


def _now(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def quota_day(now: datetime | None = None) -> str:
    """Google's calendar day (Pacific time) the request counts against."""
    return _now(now).astimezone(QUOTA_TZ).date().isoformat()


def next_reset(now: datetime | None = None) -> datetime:
    """The next midnight in California, when every model's daily count starts over (aware, UTC)."""
    local = _now(now).astimezone(QUOTA_TZ)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc)


def retry_seconds(error: object) -> float | None:
    """The wait Google asked for in a 429 ("Please retry in 2817s" / retryDelay '2817s'), if it said."""
    text = str(error)
    details = getattr(error, "details", None)
    if details:
        text += " " + json.dumps(details, default=str)
    found = _RETRY.search(text)
    return float(found.group(1)) if found else None


def is_daily(error: object) -> bool:
    """A 429 about the per-day limit (not the per-minute one)."""
    text = str(error) + " " + json.dumps(getattr(error, "details", None), default=str)
    return "PerDay" in text or "per day" in text.lower()


# --------------------------------------------------------------------------- the ledger


class Ledger:
    def __init__(self, path: Path | None = None):
        self.path = path or LEDGER
        self.data: dict[str, Any] = {"models": {}}
        self._load()

    def _load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.data.get("models"), dict):
                raise ValueError("no models")
        except (OSError, ValueError):
            self.data = {"models": {}}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as err:  # the ledger is a convenience: never fail a job over it
            logger.warning("Could not write %s: %s", self.path, err)

    def _model(self, name: str, now: datetime) -> dict[str, Any]:
        entry = self.data["models"].setdefault(name, {})
        if entry.get("day") != quota_day(now):
            entry.update(day=quota_day(now), used=0)
        return entry

    def closed_until(self, name: str, now: datetime | None = None) -> datetime | None:
        """When Google said this model can be asked again, if that is still in the future."""
        now = _now(now)
        raw = self.data["models"].get(name, {}).get("closed_until")
        if not raw:
            return None
        try:
            until = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return until if until > now else None

    def record_success(self, name: str, now: datetime | None = None) -> None:
        now = _now(now)
        self._load()
        entry = self._model(name, now)
        entry["used"] = int(entry.get("used") or 0) + 1
        entry["ever_ok"] = True
        entry.pop("closed_until", None)
        self.data["updated"] = now.isoformat(timespec="seconds")
        self._save()

    def record_closed(self, name: str, error: object = None, now: datetime | None = None) -> datetime:
        """Google answered 429 for this model: closed for the time it asked, else until the next reset.
        Returns when it opens again."""
        now = _now(now)
        self._load()
        entry = self._model(name, now)
        wait = retry_seconds(error) if error is not None else None
        if wait is not None:
            until = now + timedelta(seconds=min(wait, 36 * 3600))
        else:
            until = next_reset(now)
        if is_daily(error) if error is not None else True:
            entry["used"] = max(int(entry.get("used") or 0), free_daily())
        entry["closed_until"] = until.isoformat(timespec="seconds")
        self.data["updated"] = now.isoformat(timespec="seconds")
        self._save()
        return until

    def open_models(self, models: list[str], now: datetime | None = None) -> list[str]:
        self._load()
        return [m for m in models if self.closed_until(m, now) is None]

    def reopens_at(self, models: list[str], now: datetime | None = None) -> datetime:
        """When the first of these closed models opens again (now, if one is open)."""
        now = _now(now)
        times = [self.closed_until(m, now) for m in models]
        if any(t is None for t in times):
            return now
        return min(t for t in times if t is not None)

    def used_today(self, name: str, now: datetime | None = None) -> int:
        entry = self.data["models"].get(name, {})
        return int(entry.get("used") or 0) if entry.get("day") == quota_day(now) else 0

    def budget(self, models: list[str], now: datetime | None = None) -> dict[str, Any]:
        """What is left today across the models that are known to work (the main one always counts)."""
        now = _now(now)
        self._load()
        limit = free_daily()
        counted = [m for i, m in enumerate(models) if i == 0 or self.data["models"].get(m, {}).get("ever_ok")]
        used = sum(self.used_today(m, now) for m in counted)
        left = sum(max(0, limit - self.used_today(m, now)) for m in counted if self.closed_until(m, now) is None)
        closed = [m for m in models if self.closed_until(m, now) is not None]
        return {"used": used, "left": left, "limit": limit * len(counted), "models": counted, "closed": closed,
                "reopens_at": self.reopens_at(models, now).isoformat(timespec="minutes"),
                "reset_at": next_reset(now).isoformat(timespec="minutes"), "day": quota_day(now)}


def ledger() -> Ledger:
    return Ledger()


def low(models: list[str] | None = None, keep: int | None = None, now: datetime | None = None) -> bool:
    """True when an optional job should step aside: fewer than `keep` (GEMINI_RESERVE) calls are left today."""
    from . import script_writer

    models = models or script_writer.model_chain()
    return ledger().budget(models, now)["left"] < (reserve() if keep is None else keep)


def status(models: list[str] | None = None, tz=None, now: datetime | None = None) -> dict[str, Any]:
    """For the dashboard: today's use, what is left, and until when the models are closed (times in `tz`)."""
    from . import script_writer

    models = models or script_writer.model_chain()
    now = _now(now)
    info = ledger().budget(models, now)
    all_closed = len(info["closed"]) == len(models)
    reopen = datetime.fromisoformat(info["reopens_at"]).astimezone(tz) if tz else datetime.fromisoformat(info["reopens_at"])
    reset = datetime.fromisoformat(info["reset_at"]).astimezone(tz) if tz else datetime.fromisoformat(info["reset_at"])
    return {**info, "all_closed": all_closed, "low": info["left"] < reserve(), "reserve": reserve(),
            "reopens_at": reopen.isoformat(timespec="minutes") if all_closed else None,
            "reset_at": reset.isoformat(timespec="minutes"), "free_tier": True}
