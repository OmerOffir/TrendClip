"""The hub's queue of Gamer IQ questions written for later (the dashboard's "Write a week", trendclip.gamer_week):
one JSON file per checked question in output/gamer_queue/, named <stamp>-<nn>-<game>.json so the oldest comes first.
The production run (quiz._write_gamer, through assistant.create_auto_short) takes the oldest question of the slot's
game before asking Gemini, and the run prefers the games that have questions waiting (discord_bot.produce_one).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import quiz
from .config import Settings

logger = logging.getLogger(__name__)


def root(settings: Settings) -> Path:
    return settings.output_dir / "gamer_queue"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "game"


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).lower() == " ".join(b.split()).lower()


def asked_key(question: str, correct: str) -> str:
    """"question (answer)" the way the history and the queue compare questions: case, spacing and the final
    question mark don't count (the history keeps the spoken question, with its "?")."""
    return f"{' '.join((question or '').split()).rstrip('?.! ').lower()} ({' '.join((correct or '').split()).lower()})"


def asked_keys(settings: Settings) -> set[str]:
    """Every question asked on this machine or waiting in its queue."""
    keys = set()
    for a in quiz.gamer_asked(settings):
        question, _, correct = a.rpartition(" (")
        keys.add(asked_key(question or a, correct.rstrip(")")))
    return keys | {asked_key(e["question"].get("question", ""), e["question"].get("correct", "")) for e in entries(settings)}


def files(settings: Settings) -> list[Path]:
    folder = root(settings)
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def entries(settings: Settings) -> list[dict[str, Any]]:
    """Every queued question, oldest first, each with its file name under "name"."""
    out = []
    for path in files(settings):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("game") and isinstance(data.get("question"), dict):
            out.append({**data, "name": path.name})
    return out


def games(settings: Settings) -> dict[str, int]:
    """{game: questions waiting}, in the order the games first appear in the queue."""
    out: dict[str, int] = {}
    for e in entries(settings):
        out[e["game"]] = out.get(e["game"], 0) + 1
    return out


def put(settings: Settings, questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Questions from the dashboard into the queue. Each: {"game", "shape", "question": GeminiGamer fields,
    "week_i"}. A question already asked on this machine or already queued is skipped.
    {"queued": [{"week_i", "name", "game", "question"}], "skipped": [{"week_i", "game", "question", "why"}],
    "in_queue": {game: n}}."""
    asked = asked_keys(settings)
    folder = root(settings)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    queued, skipped = [], []
    for n, item in enumerate(questions, 1):
        game = " ".join(str(item.get("game") or "").split())
        label = {"week_i": item.get("week_i"), "game": game, "question": str((item.get("question") or {}).get("question", ""))}
        try:
            q = quiz.GeminiGamer.model_validate(item.get("question") or {})
        except ValueError:
            skipped.append({**label, "why": "not a complete question"})
            continue
        options = quiz._trivia_options(q)
        if not game or not options or not quiz.gamer_question(q.question, options):
            skipped.append({**label, "why": "no game, or the question can't be spoken with three different options"})
            continue
        if asked_key(q.question, q.correct) in asked:
            skipped.append({**label, "why": "already asked or queued on the hub"})
            continue
        asked.add(asked_key(q.question, q.correct))
        shape = str(item.get("shape") or "").lower()
        name = f"{stamp}-{n:02d}-{slug(game)}.json"
        (folder / name).write_text(json.dumps({
            "game": game, "shape": shape if shape in quiz.GAMER_SHAPES else "number", "question": q.model_dump(),
            "week_i": item.get("week_i"), "received_at": datetime.now().replace(microsecond=0).isoformat()},
            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        queued.append({**label, "name": name})
    return {"queued": queued, "skipped": skipped, "in_queue": games(settings)}


def take(settings: Settings, game: str) -> tuple[quiz.GeminiGamer, str] | None:
    """The oldest queued question about `game`, removed from the queue: (question, shape); None when none waits."""
    for e in entries(settings):
        if not _same(e["game"], game):
            continue
        path = root(settings) / e["name"]
        try:
            q = quiz.GeminiGamer.model_validate(e["question"])
        except ValueError:
            logger.warning("Queued question %s is not readable; leaving it", path.name)
            continue
        path.unlink(missing_ok=True)
        logger.info("Gamer IQ question for %s taken from the queue: %s", game, q.question)
        return q, str(e.get("shape") or "number")
    return None


def prefer(settings: Settings, pool: list[str]) -> list[str]:
    """The games of `pool` that have questions waiting (the oldest question's game first), or `pool` itself."""
    waiting = games(settings)
    picked = [g for w in waiting for g in pool if _same(g, w)]
    return list(dict.fromkeys(picked)) or pool
