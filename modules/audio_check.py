"""Listen to a recorded narration before it goes out: Whisper writes down what the voice really says, sentence by
sentence, and the words that came out as something else are reported ("Freddie dies" heard as "Freddie dices").

The owner caught a mispronounced word by ear on 2026-11-13; this is the ear that checks every video. It runs on the
hub's GPU (or the CPU) with openai/whisper-small.en through transformers, with no audio decoder of its own: ffmpeg
reads the mp3 (torchcodec's CUDA libraries are missing on the hub).
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODEL = "openai/whisper-small.en"
RATE = 16000
MAX_PIECE = 25.0  # seconds Whisper hears at once (it takes 30)
NUMBERS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
           "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
           "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million", "billion",
           "first", "second", "third", "fourth", "fifth", "tenth", "twentieth", "and", "a", "half", "quarter"}
FILLERS = {"the", "a", "an", "this", "that", "its", "it's", "and", "of", "to", "in", "on", "s"}


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower().replace("’", "'"))


def sound(word: str) -> str:
    """A rough key for how a word sounds, so a spelling of the same name is not a mistake (Freddie / Freddy,
    Noel / Noelle)."""
    w = re.sub(r"(ie|y|ey)$", "i", _norm(word).replace("'", ""))
    w = re.sub(r"(.)\1+", r"\1", w)
    return re.sub(r"e$", "", w) if len(w) > 3 else w


def _same(e: str, g: str) -> bool:
    """Two words that are the same thing said: a number, a filler, a spelling, a plural."""
    if (_numberish(e) or e in FILLERS) and (_numberish(g) or g in FILLERS):
        return True
    if e.replace("'", "") == g.replace("'", "") or sound(e) == sound(g):
        return True
    e_, g_ = e.replace("'", ""), g.replace("'", "")
    if e_ + "s" == g_ or g_ + "s" == e_:  # a plural or a possessive: "queens" for "queen"
        return True
    return len(e) >= 7 and difflib.SequenceMatcher(None, e, g).ratio() >= 0.9


def _numberish(word: str) -> bool:
    w = _norm(word)
    return not w or w in NUMBERS or bool(re.fullmatch(r"\d+(st|nd|rd|th|s)?", w))


def pieces(words: list[dict[str, Any]]) -> list[tuple[float, float, list[str]]]:
    """The narration cut into sentences (at most MAX_PIECE s each), from the voice's own word timings."""
    out: list[tuple[float, float, list[str]]] = []
    cur: list[dict[str, Any]] = []
    for w in words:
        if cur and float(w["end"]) - float(cur[0]["start"]) > MAX_PIECE:
            out.append((float(cur[0]["start"]), float(cur[-1]["end"]), [c["word"] for c in cur]))
            cur = []
        cur.append(w)
        if re.search(r"[.?!]$", str(w["word"])):
            out.append((float(cur[0]["start"]), float(cur[-1]["end"]), [c["word"] for c in cur]))
            cur = []
    if cur:
        out.append((float(cur[0]["start"]), float(cur[-1]["end"]), [c["word"] for c in cur]))
    return out


def misheard(said: list[str], heard: str) -> list[tuple[str, str]]:
    """The words of `said` that came out as something else: (what the script says, what was heard). Numbers ("1970",
    "twentieth" / "20th"), a dropped "the" and spelling of the same sound are not reported."""
    a = [_norm(w) for w in said]
    b = [_norm(w) for w in heard.split()]
    a_, b_ = [w for w in a if w], [w for w in b if w]
    found = []
    for op, i0, i1, j0, j1 in difflib.SequenceMatcher(None, a_, b_, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        exp, got = a_[i0:i1], b_[j0:j1]
        if len(exp) == len(got) > 1:  # word for word: keep only the pairs that really differ ("freddie dies" / "freddy dices")
            keep = [(e, g) for e, g in zip(exp, got) if not _same(e, g)]
            if keep:
                found.append((" ".join(e for e, _ in keep), " ".join(g for _, g in keep)))
            continue
        if all(_numberish(w) or w in FILLERS for w in exp + got):
            continue
        joined_e, joined_g = "".join(exp), "".join(got)
        if joined_e.replace("'", "") == joined_g.replace("'", ""):  # "rock n roll" / "rocknroll"
            continue
        if "".join(map(sound, exp)) == "".join(map(sound, got)):  # the same name, spelt another way
            continue
        if len(exp) == len(got) == 1 and _same(exp[0], got[0]):
            continue
        if not exp and all(w in FILLERS for w in got):
            continue
        found.append((" ".join(exp) or "(nothing)", " ".join(got) or "(nothing)"))
    return found


_model: Any = None


def _load():
    global _model
    if _model is None:
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        dev = "cuda" if torch.cuda.is_available() else "cpu"
        proc = WhisperProcessor.from_pretrained(MODEL)
        model = WhisperForConditionalGeneration.from_pretrained(MODEL, dtype=torch.float16 if dev == "cuda" else torch.float32).to(dev)
        _model = (proc, model, dev)
    return _model


def unload() -> None:
    global _model
    _model = None
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def transcribe(audio, start: float, end: float) -> str:
    import numpy as np

    proc, model, dev = _load()
    clip = audio[max(0, int((start - 0.15) * RATE)): int((end + 0.25) * RATE)]
    if len(clip) < RATE // 4:
        clip = np.pad(clip, (0, RATE // 4 - len(clip)))
    feats = proc(clip, sampling_rate=RATE, return_tensors="pt").input_features.to(dev, model.dtype)
    ids = model.generate(feats, max_new_tokens=200, max_length=None)
    return proc.batch_decode(ids, skip_special_tokens=True)[0].strip()


def check(voice: Path, words: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What the voice gets wrong: [{"at": seconds into the video, "said": the sentence, "heard": what Whisper heard, "word": "dies", "as": "dices"}]."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(voice), "-ac", "1", "-ar", str(RATE), "-f", "f32le", "-"],
                         capture_output=True, check=True, timeout=120).stdout
    audio = np.frombuffer(raw, np.float32)
    found = []
    for start, end, said in pieces(words):
        heard = transcribe(audio, start, end)
        for exp, got in misheard(said, heard):
            found.append({"at": round(start, 1), "said": " ".join(said), "heard": heard, "word": exp, "as": got})
    return found


def check_quietly(voice: Path, words: list[dict[str, Any]]) -> list[dict[str, str]] | None:
    """check(), or None when it could not run (no model, no GPU memory): a video is never held up by the ear."""
    try:
        return check(voice, words)
    except Exception as err:  # noqa: BLE001
        logger.warning("The narration could not be checked by ear (%s)", err)
        return None
    finally:
        unload()


if __name__ == "__main__":
    import sys

    folder = Path(sys.argv[1])
    got = check(folder / "voice.mp3", json.loads((folder / "voice.words.json").read_text()))
    print(json.dumps(got, indent=1))
