"""Kokoro: an open-source narration voice (hexgrad/Kokoro-82M) that runs on the GPU or CPU, reads where the
sentences stop, and gives a timestamp for every word, so the captions follow the voice exactly.

All three channels use it: SideQuestLogic (TTS_ENGINE / TTS_VOICE / TTS_RATE, the Edge "+5%" read as speed
1.05), Dark Outliers (DA_TTS_ENGINE, DA_TTS_VOICE, DA_TTS_SPEED) and Borderline Why (BW_TTS_VOICE, BW_TTS_SPEED). The script goes in as one
paragraph (Kokoro paces a paragraph naturally and splits it into chunks of a few sentences itself); the
silence at every chunk's edges is trimmed and a short pause put between chunks. Install on the machine that makes the videos:
    .venv/bin/pip install "kokoro>=0.9.4" soundfile    (requirements-gpu.txt); the weights download on first use.
Voices: am_michael, am_onyx, am_adam (American), bm_george, bm_lewis (British), af_heart, bf_emma (female)…
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .video_assembler import Word

logger = logging.getLogger(__name__)

REPO = "hexgrad/Kokoro-82M"
SAMPLE_RATE = 24000
DEFAULT_VOICE = "bm_george"
DEFAULT_SPEED = 0.95
PAUSE = 0.25  # seconds of silence between the chunks Kokoro splits the text into (a few sentences each)
# (name, label, gender): the English voices of Kokoro-82M, American first, the better-graded ones at the top.
VOICES_INFO = (
    ("am_michael", "Michael", "Male"), ("am_fenrir", "Fenrir", "Male"), ("am_puck", "Puck", "Male"),
    ("am_adam", "Adam", "Male"), ("am_onyx", "Onyx", "Male"), ("am_echo", "Echo", "Male"),
    ("am_eric", "Eric", "Male"), ("am_liam", "Liam", "Male"),
    ("af_heart", "Heart", "Female"), ("af_bella", "Bella", "Female"), ("af_nicole", "Nicole", "Female"),
    ("af_aoede", "Aoede", "Female"), ("af_kore", "Kore", "Female"), ("af_sarah", "Sarah", "Female"),
    ("af_nova", "Nova", "Female"), ("af_sky", "Sky", "Female"), ("af_alloy", "Alloy", "Female"),
    ("af_jessica", "Jessica", "Female"), ("af_river", "River", "Female"),
    ("bm_george", "George", "Male"), ("bm_fable", "Fable", "Male"), ("bm_lewis", "Lewis", "Male"),
    ("bm_daniel", "Daniel", "Male"),
    ("bf_emma", "Emma", "Female"), ("bf_isabella", "Isabella", "Female"), ("bf_alice", "Alice", "Female"),
    ("bf_lily", "Lily", "Female"),
)
VOICES = tuple(name for name, _, _ in VOICES_INFO)
QUIZ_VOICE = "am_michael"  # SideQuestLogic's default: an American male, like the Edge voice it replaces
INSTALL = 'Kokoro is not installed: .venv/bin/pip install "kokoro>=0.9.4" soundfile (see requirements-gpu.txt)'

_pipeline: Any = None


def available() -> bool:
    try:
        import kokoro  # noqa: F401
    except ImportError:
        return False
    return True


def is_voice(name: str) -> bool:
    """True for a Kokoro voice name (am_michael), false for an Edge one (en-US-AndrewNeural) or nothing."""
    return bool(re.fullmatch(r"[ab][fm]_[a-z]+", (name or "").strip().lower()))


def list_voices() -> list[dict]:
    """The English voices in the shape the Create tab's picker wants: [{name, label, gender, locale}]."""
    return [{"name": name, "label": f"{label} · {gender.lower()} · Kokoro", "gender": gender,
             "locale": "American" if name.startswith("a") else "British"} for name, label, gender in VOICES_INFO]


def speed_from_rate(rate: str, default: float = 1.0) -> float:
    """An Edge rate ("+5%") as a Kokoro speed (1.05), so TTS_RATE keeps its meaning across the engines."""
    m = re.fullmatch(r"\s*([+-]?\d{1,3})%\s*", rate or "")
    return round(default + int(m.group(1)) / 100, 2) if m else default


def _load():
    global _pipeline
    if _pipeline is None:
        try:
            from kokoro import KPipeline
        except ImportError as err:
            raise RuntimeError(INSTALL) from err
        logger.info("Loading Kokoro (%s)", REPO)
        _pipeline = KPipeline(lang_code="a", repo_id=REPO)
    return _pipeline


def unload() -> None:
    """Free the model (the scene pictures want the GPU); it loads again on the next voice."""
    global _pipeline
    if _pipeline is not None:
        _pipeline = None
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass


def paragraph(text: str) -> str:
    """The text as one paragraph: Kokoro reads a paragraph at a natural pace and splits it into chunks of a
    few sentences itself; fed one sentence at a time it slows down and pads every sentence with silence."""
    return " ".join(text.split())


def trim(audio, sample_rate: int = SAMPLE_RATE, lead: float = 0.06, tail: float = 0.12):
    """Cut the model's silence at both ends of a chunk, keeping a short breath; returns (audio, seconds cut
    at the start), so the chunk's word timestamps can be shifted."""
    import numpy as np

    window = max(int(sample_rate * 0.02), 1)
    frames = audio.size // window
    if frames == 0:
        return audio, 0.0
    rms = np.sqrt(np.mean(audio[: frames * window].reshape(frames, window) ** 2, axis=1))
    loud = np.flatnonzero(rms > max(0.01, float(rms.max()) * 0.05))
    if loud.size == 0:
        return audio, 0.0
    start = max(int(loud[0]) * window - int(lead * sample_rate), 0)
    end = min((int(loud[-1]) + 1) * window + int(tail * sample_rate), audio.size)
    return audio[start:end], start / sample_rate


def words_from_tokens(tokens: list[Any], offset: float, fallback: float = 0.25) -> list[Word]:
    """Kokoro's tokens (text, start_ts, end_ts within the chunk) as timed words from `offset`; punctuation
    joins the word before it, a token without a timestamp gets a short slot after the previous one."""
    out: list[Word] = []
    for tok in tokens:
        text = (getattr(tok, "text", "") or "").strip()
        if not text:
            continue
        if not re.search(r"[\w]", text):  # punctuation: part of the previous word, as in the script
            if out:
                out[-1] = Word(out[-1].text + text, out[-1].start, out[-1].end)
            continue
        start, end = getattr(tok, "start_ts", None), getattr(tok, "end_ts", None)
        if start is None or end is None:
            prev_end = out[-1].end if out else offset
            start, end = prev_end - offset, prev_end - offset + fallback
        out.append(Word(text, round(offset + float(start), 3), round(offset + float(end), 3)))
    return out


def speak(text: str, voice: str = DEFAULT_VOICE, speed: float = DEFAULT_SPEED, pause: float = PAUSE) -> tuple[bytes, list[Word]]:
    """MP3 bytes and the timed words of `text`."""
    import numpy as np

    from .local_voice import _mp3

    pipe = _load()
    chunks: list[np.ndarray] = []
    words: list[Word] = []
    offset = 0.0
    for result in pipe(paragraph(text), voice=voice, speed=speed):
        audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
        if audio.size == 0:
            continue
        audio, cut = trim(audio)
        tokens = getattr(result, "tokens", None) or []
        got = words_from_tokens(tokens, offset - cut)
        got = [Word(w.text, max(w.start, offset), max(w.end, offset + 0.05)) for w in got]
        if not got:  # no token timings for this chunk: spread its words over its audio
            from .video_assembler import estimate_word_timings

            got = [Word(w.text, round(offset + w.start, 3), round(offset + w.end, 3))
                   for w in estimate_word_timings(getattr(result, "graphemes", "") or text, audio.size / SAMPLE_RATE, lead_in=0.02)]
        if chunks:
            chunks.append(np.zeros(int(SAMPLE_RATE * pause), dtype=np.float32))
            offset += pause
            got = [Word(w.text, round(w.start + pause, 3), round(w.end + pause, 3)) for w in got]
        words.extend(got)
        chunks.append(audio)
        offset += audio.size / SAMPLE_RATE
    if not chunks or not words:
        raise RuntimeError("Kokoro returned no audio")
    wav = np.concatenate(chunks)
    pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16).tobytes()
    return _mp3(pcm, SAMPLE_RATE), words


def synthesize(text: str, output_mp3: Path, voice: str = DEFAULT_VOICE, speed: float = DEFAULT_SPEED) -> list[Word]:
    """Write the MP3 and a <name>.words.json next to it (the same files the Edge voice writes); returns the words."""
    import json

    text = text.strip()
    if not text:
        raise RuntimeError("The script is empty")
    audio, words = speak(text, voice, speed)
    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    output_mp3.write_bytes(audio)
    output_mp3.with_suffix(".words.json").write_text(
        json.dumps([{"word": w.text, "start": w.start, "end": w.end} for w in words], indent=1), encoding="utf-8")
    return words
