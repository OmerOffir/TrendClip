"""Write a week of Gamer IQ questions with any LLM at once (the dashboard's "Write a week"), then send them to the
Linux hub, whose production run makes the next levels from them without asking Gemini.

The steps are the manual flow's (trendclip.gamer_manual), for several games and questions in one prompt:

  1. start    you say which games and how many levels each ("Minecraft 7, GTA V 3"): the session
  2. answer   you paste the LLM's JSON {"candidates": [{"game": ..., "shape": ..., ...}]}; each candidate gets the
              same checks as the manual flow (three different options, a speakable question, not asked before)
  3. facts    one fact-check prompt for every usable candidate (better in a second LLM with web search); you paste
              its "1: PASS" / "2: FAIL: why" lines. Failed ones are never asked for again; a game still short of
              its count goes back in the write prompt
  4. send     the passed questions go to the hub's queue (trendclip.gamer_queue): its production run takes the
              oldest question of the slot's game before asking Gemini, so a week of questions is a week of levels

One session at a time: output/gamer_week/session.json (starting a new week replaces it).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import gamer_manual, gamer_queue, llm_json, quiz
from .config import Settings

DEFAULT_COUNT = 7
MAX_QUESTIONS = 40
SPARE = 2  # candidates asked for beyond each game's count: some fail the fact check
STEPS = ("write", "facts", "ready", "sent", "rejected", "dropped")


class WeekError(ValueError):
    pass


class Candidate(quiz.GeminiGamer):
    game: str = Field(description="The game, exactly as named in the prompt")
    shape: str = Field("", description="The question's shape: number, fake, first or lore")


class WeekAnswer(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)


def root(settings: Settings) -> Path:
    return settings.output_dir / "gamer_week"


def _path(settings: Settings) -> Path:
    return root(settings) / "session.json"


def _load(settings: Settings) -> dict[str, Any]:
    path = _path(settings)
    if not path.is_file():
        raise WeekError("No week started yet: say which games and how many levels each")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(settings: Settings, session: dict[str, Any]) -> None:
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def exists(settings: Settings) -> bool:
    return _path(settings).is_file()


def key(question: str, correct: str) -> str:
    return f"{question} ({correct})"


def _norm(asked: str) -> str:
    """A "question (answer)" line as the comparisons see it (gamer_queue.asked_key)."""
    question, _, correct = asked.rpartition(" (")
    return gamer_queue.asked_key(question or asked, correct.rstrip(")"))


# --------------------------------------------------------------------------- 1. start


def parse_games(text: str) -> list[tuple[str, int]]:
    """"Minecraft 7, GTA V / Online 3" -> [("Minecraft", 7), ("GTA V / Online", 3)]; a game without a number
    gets DEFAULT_COUNT; the same game twice adds up."""
    out: dict[str, int] = {}
    for part in re.split(r"[,;\n]+", text or ""):
        part = " ".join(part.split())
        if not part:
            continue
        m = re.fullmatch(r"(.*?)[\s:x×]*(\d+)", part)
        game, n = (m.group(1).strip(" :"), int(m.group(2))) if m and m.group(1).strip(" :") else (part, DEFAULT_COUNT)
        if n > 0:
            out[game] = out.get(game, 0) + n
    return list(out.items())


def start(settings: Settings, games: str | list[tuple[str, int]]) -> dict[str, Any]:
    """Open the session: a line per game with how many levels it gets this week."""
    wanted = parse_games(games) if isinstance(games, str) else [(g, int(n)) for g, n in games if int(n) > 0]
    if not wanted:
        raise WeekError('Say which games and how many questions each, like "Minecraft 7, GTA V 3"')
    if sum(n for _, n in wanted) > MAX_QUESTIONS:
        raise WeekError(f"At most {MAX_QUESTIONS} questions in one week")
    session = {"created_at": datetime.now().replace(microsecond=0).isoformat(),
               "games": [{"game": g, "count": n} for g, n in wanted], "questions": [], "rejected": [], "problems": []}
    _save(settings, session)
    return view(settings)


# --------------------------------------------------------------------------- the prompts


def _need(session: dict[str, Any]) -> list[tuple[str, int]]:
    """(game, questions still missing) for the games not yet covered by candidates that passed or wait for the check."""
    out = []
    for g in session["games"]:
        have = sum(1 for q in session["questions"] if q["game"] == g["game"] and q["step"] in ("facts", "ready", "sent"))
        if have < g["count"]:
            out.append((g["game"], g["count"] - have))
    return out


SHAPES_LINE = "Shapes: mix them across the questions, about equally, and say which in each candidate's \"shape\":\n" + "\n".join(
    f"- {name}: {text.format(game="the game")}" for name, text in quiz.GAMER_SHAPES.items())

WEEK_FORMAT = """ANSWER FORMAT
Answer with ONLY one JSON object (no text before or after it, no code fence), shaped like this example:
{
  "candidates": [
    {
      "game": "Minecraft",
      "shape": "number",
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
"game" is the game's name exactly as written above; "shape" one of number, fake, first, lore."""


def write_prompt(settings: Settings, session: dict[str, Any]) -> str:
    """One prompt for every game still short of its count (empty when none is)."""
    need = _need(session)
    if not need:
        return ""
    asked = quiz.gamer_asked(settings) + session["rejected"] + [key(q["question"]["question"], q["question"]["correct"])
                                                               for q in session["questions"] if q["step"] != "dropped"]
    blocks = []
    for game, n in need:
        count = n + SPARE
        text = quiz.GAMER_PROMPT.format(count=count, game=game, shape="SEE SHAPES", used="; ".join(asked[-60:]) or "none yet",
                                        notes=quiz._notes("") + quiz._variety(settings) + quiz._learned(settings, "gamer"))
        text = text.replace("Shape: SEE SHAPES.", "The shapes are listed above; mix them.")
        blocks.append(f"=== GAME: {game} ({count} candidates, {n} will be used) ===\n{text.strip()}")
    head = (quiz.GAMER_INTRO + "\n\nYou write the questions for the coming week: several games, several questions each, "
            "every question about ITS game only. Each game below says how many candidates to write; a few more than "
            "needed, because a fact check drops some.\n\n" + SHAPES_LINE)
    return head + "\n\n" + "\n\n".join(blocks) + "\n\n" + WEEK_FORMAT


def _checking(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [q for q in session["questions"] if q["step"] == "facts"]


def facts_prompt(session: dict[str, Any]) -> str:
    """One fact-check prompt for every candidate that passed the shape checks (empty when there is none)."""
    todo = _checking(session)
    if not todo:
        return ""
    listing = "\n\n".join(
        f"{i}. [{q['game']}] Q: {q['question']['question'].strip().rstrip('?')}?\n   Claimed correct: {q['question']['correct']}\n"
        f"   Wrong options: {q['question']['trap']}; {q['question']['distractor']}\n   Claimed proof: {q['question']['fact']}"
        for i, q in enumerate(todo, 1))
    return quiz.GAMER_CHECK.format(candidates=listing) + "\n" + gamer_manual.FACTS_FORMAT


# --------------------------------------------------------------------------- 2. answer, 3. facts


def answer(settings: Settings, text: str) -> dict[str, Any]:
    """The LLM's candidates: the usable ones (three different options, a speakable question, not asked before,
    a game of this week) wait for the fact check; the rest are listed as problems."""
    session = _load(settings)
    found = llm_json.parse(text, WeekAnswer, WeekError)
    if not found.candidates:
        raise WeekError('The answer has no "candidates": ask the LLM for the JSON described at the end of the prompt')
    games = {g["game"].lower(): g["game"] for g in session["games"]}
    asked = gamer_queue.asked_keys(settings) | {_norm(a) for a in session["rejected"]}
    asked |= {gamer_queue.asked_key(q["question"]["question"], q["question"]["correct"]) for q in session["questions"] if q["step"] != "dropped"}
    need = dict(_need(session))
    problems, added = [], 0
    for c in found.candidates:
        game = games.get(" ".join(c.game.split()).lower())
        options = quiz._trivia_options(c)
        if game is None:
            problems.append(f"{c.question!r}: {c.game!r} is not a game of this week")
        elif not options:
            problems.append(f"{c.question!r}: needs three short, different options")
        elif not quiz.gamer_question(c.question, options):
            problems.append(f"{c.question!r}: the question alone (without the options) must be at most {quiz.GAMER_MAX_WORDS} words")
        elif gamer_queue.asked_key(c.question, c.correct) in asked:
            problems.append(f"{c.question!r}: already asked")
        elif need.get(game, 0) <= 0:
            problems.append(f"{c.question!r}: {game} has enough questions already")
        else:
            asked.add(gamer_queue.asked_key(c.question, c.correct))
            need[game] -= 1
            added += 1
            shape = c.shape.strip().lower() if c.shape.strip().lower() in quiz.GAMER_SHAPES else "number"
            session["questions"].append({"i": len(session["questions"]) + 1, "game": game, "shape": shape, "step": "facts",
                                         "question": quiz.GeminiGamer.model_validate(c.model_dump(exclude={"game", "shape"})).model_dump(),
                                         "why": ""})
    if not added:
        raise WeekError("No usable question in the answer: " + "; ".join(problems[:4]))
    session["problems"] = problems
    _save(settings, session)
    return view(settings)


def facts(settings: Settings, text: str) -> dict[str, Any]:
    """The fact checker's verdicts, numbered as the facts prompt numbered the candidates: a pass makes the
    question ready, a fail rejects it for good."""
    session = _load(settings)
    todo = _checking(session)
    if not todo:
        raise WeekError("No question waits for its fact check")
    if not any(quiz._VERDICT.match(line.strip()) for line in (text or "").splitlines()):
        raise WeekError('No verdict lines found: the answer needs lines like "1: PASS" or "2: FAIL: reason"')
    verdicts = quiz.read_verdicts(text, len(todo))
    for q, (ok, why) in zip(todo, verdicts):
        if ok:
            q.update(step="ready", why="")
        else:
            q.update(step="rejected", why=why or "failed")
            session["rejected"].append(key(q["question"]["question"], q["question"]["correct"]))
    _save(settings, session)
    return view(settings)


def drop(settings: Settings, i: int) -> dict[str, Any]:
    session = _load(settings)
    q = next((q for q in session["questions"] if q["i"] == i), None)
    if q is None:
        raise WeekError(f"No question {i} in this week")
    if q["step"] == "sent":
        raise WeekError("That question is already on Linux")
    q.update(step="dropped")
    _save(settings, session)
    return view(settings)


# --------------------------------------------------------------------------- 4. send


def bundle(settings: Settings) -> dict[str, Any]:
    session = _load(settings)
    return {"questions": [{"game": q["game"], "shape": q["shape"], "question": q["question"], "week_i": q["i"]}
                          for q in session["questions"] if q["step"] == "ready"]}


def send(settings: Settings, deliver=None) -> dict[str, Any]:
    """The ready questions go to the hub's queue (through Discord on the sending machine, straight into the
    queue on the hub itself)."""
    from modules import channel_hub

    data = bundle(settings)
    if not data["questions"]:
        raise WeekError("No question has passed the fact check yet")
    deliver = deliver or channel_hub.queue_scripts
    reply = deliver(settings, "sidequestlogic", data)
    session = _load(settings)
    at = datetime.now().replace(microsecond=0).isoformat()
    took = {q.get("week_i"): q for q in reply.get("queued", [])}
    skipped = {s.get("week_i"): s.get("why", "") for s in reply.get("skipped", [])}
    for q in session["questions"]:
        if q["step"] != "ready":
            continue
        if q["i"] in took:
            q.update(step="sent", sent={"at": at, "name": took[q["i"]].get("name", "")})
        elif q["i"] in skipped:
            q.update(why=f"Linux did not take it: {skipped[q['i']]}")
    session["last_send"] = {"at": at, **reply}
    _save(settings, session)
    return {**view(settings), "reply": reply}


# --------------------------------------------------------------------------- what the panel shows


def view(settings: Settings) -> dict[str, Any]:
    session = _load(settings)
    questions = []
    for q in session["questions"]:
        c = quiz.GeminiGamer.model_validate(q["question"])
        options = quiz._trivia_options(c)
        questions.append({"i": q["i"], "game": q["game"], "shape": q["shape"], "step": q["step"], "question": c.question,
                          "spoken": quiz.gamer_question(c.question, options) if options else "", "options": [c.trap, c.distractor, c.correct],
                          "correct": c.correct, "fact": c.fact, "title": c.title, "why": q.get("why", ""), "sent": q.get("sent")})
    steps = [q["step"] for q in questions]
    need = _need(session)
    write = write_prompt(settings, session)
    check = facts_prompt(session)
    return {"created_at": session["created_at"], "games": session["games"], "need": [{"game": g, "missing": n} for g, n in need],
            "questions": questions, "problems": session.get("problems") or [],
            "to_write": sum(n for _, n in need), "to_check": steps.count("facts"), "ready": steps.count("ready"),
            "sent": steps.count("sent"), "write_prompt": write, "facts_prompt": check,
            "write_chars": len(write), "facts_chars": len(check), "last_send": session.get("last_send"),
            "level": quiz.gamer_level(settings)}
