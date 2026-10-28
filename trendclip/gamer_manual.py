"""Write a Gamer IQ question with any LLM, by hand (the dashboard's "Write it myself"), for when Gemini is out of
quota or you'd rather use another model. The steps are Gemini's own, with you carrying the prompts:

  1. write   the code gives the writing prompt (the game, a question shape, the questions already asked); you
             paste the LLM's JSON with a few candidate questions
  2. facts   the fact-check prompt (better in a second LLM with web search); you paste its "1: PASS" lines.
             None passed: back to 1 with the reasons, and the rejected questions are never asked for again
  3. ready   the first question that passed becomes the next level's Short over no-copyright gameplay of its
             own game (assistant.create_gamer_short), planned in the slot you pick

Each session is a folder output/gamer_manual/<id>/ with state.json, so it survives a restart.
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import llm_json, quiz
from .config import Settings

SESSIONS_SHOWN = 10

JSON_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence), shaped like this example:
{
  "candidates": [
    {
      "question": "How many ... in Minecraft?",
      "trap": "the wrong answer most players pick",
      "distractor": "another wrong answer",
      "correct": "the right answer",
      "fact": "One or two sentences proving it, with the real numbers or names.",
      "short_name": "MINECRAFT",
      "title": "Real Minecraft Players Know This",
      "description": "One or two sentences daring fans to comment the answer.",
      "hashtags": ["#shorts", "#minecraft", "#gaming", "#quiz"],
      "hint": "a spoiler-free hint"
    }
  ]
}
Write {count} candidates in the list."""

FACTS_FORMAT = """Search the web for each one (the game's wiki first). Answer with only the verdict lines."""


class ManualError(ValueError):
    pass


def root(settings: Settings) -> Path:
    return settings.output_dir / "gamer_manual"


def _folder(settings: Settings, sid: str) -> Path:
    folder = root(settings) / (sid or "")
    if not re.fullmatch(r"[a-z0-9-]{1,80}", sid or "") or not (folder / "state.json").is_file():
        raise ManualError(f"No manual question {sid!r}")
    return folder


def _state(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "state.json").read_text(encoding="utf-8"))


def _save(folder: Path, state: dict[str, Any]) -> None:
    (folder / "state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _candidates(state: dict[str, Any]) -> list[quiz.GeminiGamer]:
    return [quiz.GeminiGamer.model_validate(c) for c in state.get("candidates") or []]


# --------------------------------------------------------------------------- the steps


def start(settings: Settings, game: str, rng: random.Random | None = None) -> dict[str, Any]:
    """Open a session for a question about `game` (a question shape the last levels didn't use)."""
    game = " ".join((game or "").split())
    if not game:
        raise ManualError("Pick the game the question is about")
    now = datetime.now().replace(microsecond=0)
    sid = f"{re.sub(r'[^a-z0-9]+', '-', game.lower()).strip('-')[:40] or 'game'}-{now:%Y%m%d-%H%M%S}"
    folder = root(settings) / sid
    folder.mkdir(parents=True, exist_ok=True)
    _save(folder, {"game": game, "shape": quiz.pick_gamer_shape(settings, rng or random.Random()), "step": "write",
                   "created_at": now.isoformat(), "candidates": [], "verdicts": [], "rejected": [], "problems": []})
    return view(settings, sid)


def _parse_set(text: str) -> quiz.GeminiGamerSet:
    """The candidates, also when the LLM answered one question without the list around it."""
    try:
        return llm_json.parse(text, quiz.GeminiGamerSet, ManualError)
    except ManualError as err:
        try:
            return quiz.GeminiGamerSet(candidates=[llm_json.parse(text, quiz.GeminiGamer, ManualError)])
        except (ManualError, ValidationError):
            raise err from None


def answer(settings: Settings, sid: str, text: str) -> dict[str, Any]:
    """Step 1: the writer's candidates. The usable ones (three different options, not asked before) go to the
    fact check."""
    folder = _folder(settings, sid)
    state = _state(folder)
    batch = _parse_set(text)
    asked = {a.lower() for a in quiz.gamer_asked(settings) + state["rejected"]}
    usable, problems = [], []
    for c in batch.candidates:
        if not quiz._trivia_options(c):
            problems.append(f"{c.question!r}: needs three short, different options")
        elif f"{c.question} ({c.correct})".lower() in asked:
            problems.append(f"{c.question!r}: already asked")
        else:
            usable.append(c)
    if not usable:
        state.update(step="write", problems=problems or ["The answer has no questions"])
    else:
        state.update(step="facts", candidates=[c.model_dump() for c in usable], verdicts=[], problems=problems)
    _save(folder, state)
    return view(settings, sid)


def facts(settings: Settings, sid: str, text: str) -> dict[str, Any]:
    """Step 2: the fact checker's verdicts. A question that passed: ready; none: write new ones."""
    folder = _folder(settings, sid)
    state = _state(folder)
    if state["step"] not in ("facts", "ready"):
        raise ManualError("Paste the writer's questions first")
    candidates = _candidates(state)
    if not any(quiz._VERDICT.match(line.strip()) for line in (text or "").splitlines()):
        raise ManualError('No verdict lines found: the answer needs lines like "1: PASS" or "2: FAIL: reason"')
    verdicts = quiz.read_verdicts(text, len(candidates))
    passed = [i for i, (ok, _) in enumerate(verdicts) if ok]
    if passed:
        state.update(step="ready", verdicts=verdicts, chosen=passed[0], problems=[])
    else:
        state.update(step="write", verdicts=verdicts, candidates=[],
                     rejected=state["rejected"] + [f"{c.question} ({c.correct})" for c in candidates],
                     problems=[f"{c.question!r}: {why or 'failed'}" for c, (_, why) in zip(candidates, verdicts)])
    _save(folder, state)
    return view(settings, sid)


def choose(settings: Settings, sid: str, index: int) -> dict[str, Any]:
    """Make another question that passed the fact check the one for the video."""
    folder = _folder(settings, sid)
    state = _state(folder)
    if state["step"] != "ready" or not (0 <= index < len(state["verdicts"])) or not state["verdicts"][index][0]:
        raise ManualError("Only a question that passed the fact check can be chosen")
    state["chosen"] = index
    _save(folder, state)
    return view(settings, sid)


def question(settings: Settings, sid: str) -> tuple[str, quiz.GeminiGamer, str]:
    """(game, the chosen question, its shape) of a ready session."""
    state = _state(_folder(settings, sid))
    if state["step"] != "ready":
        raise ManualError("No question has passed the fact check yet")
    return state["game"], _candidates(state)[state["chosen"]], state["shape"]


def mark_made(settings: Settings, sid: str, filename: str) -> None:
    folder = _folder(settings, sid)
    state = _state(folder)
    state.update(step="made", video=filename)
    _save(folder, state)


# --------------------------------------------------------------------------- what the panel shows


def _prompt(settings: Settings, state: dict[str, Any]) -> str:
    """The prompt to copy for this step (each is whole, so a fresh chat or another LLM works too)."""
    if state["step"] == "write":
        asked = quiz.gamer_asked(settings) + state["rejected"]
        return "\n\n".join([quiz.QUIZ_INSTRUCTION, quiz.gamer_prompt(settings, state["game"], state["shape"], asked),
                            JSON_FORMAT.replace("{count}", str(quiz.GAMER_CANDIDATES))])
    if state["step"] == "facts":
        return quiz.GAMER_CHECK.format(candidates=quiz.check_listing(_candidates(state))) + "\n" + FACTS_FORMAT
    return ""


def view(settings: Settings, sid: str) -> dict[str, Any]:
    state = _state(_folder(settings, sid))
    verdicts = state.get("verdicts") or []
    questions = []
    for i, c in enumerate(_candidates(state)):
        ok, why = verdicts[i] if i < len(verdicts) else (None, "")
        questions.append({"question": c.question, "options": [c.trap, c.distractor, c.correct], "correct": c.correct,
                          "fact": c.fact, "title": c.title, "passed": ok, "why": why})
    return {"id": sid, "game": state["game"], "shape": state["shape"], "step": state["step"],
            "level": quiz.gamer_level(settings), "prompt": _prompt(settings, state), "problems": state.get("problems") or [],
            "questions": questions, "chosen": state.get("chosen"), "video": state.get("video"),
            "created_at": state.get("created_at", "")}


def sessions(settings: Settings) -> list[dict[str, Any]]:
    """The newest sessions, for picking one up again."""
    folder = root(settings)
    if not folder.is_dir():
        return []
    out = []
    for f in folder.iterdir():
        if (f / "state.json").is_file():
            state = _state(f)
            out.append({"id": f.name, "game": state["game"], "step": state["step"], "created_at": state.get("created_at", "")})
    return sorted(out, key=lambda s: s["created_at"], reverse=True)[:SESSIONS_SHOWN]
