"""Kokoro: an open-source narration voice (hexgrad/Kokoro-82M) that runs on the GPU or CPU, reads where the
sentences stop, and gives a timestamp for every word, so the captions follow the voice exactly.

Dark Outliers uses it (DA_TTS_ENGINE=kokoro, DA_TTS_VOICE, DA_TTS_SPEED). Each sentence is spoken as its
own chunk with a short pause after it. Install on the machine that makes the videos:
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
PAUSE = 0.35  # seconds of silence after every sentence
VOICES = ("am_michael", "am_onyx", "am_adam", "am_echo", "bm_george", "bm_lewis", "bm_daniel", "af_heart", "af_bella",
          "bf_emma", "bf_isabella")
INSTALL = 'Kokoro is not installed: .venv/bin/pip install "kokoro>=0.9.4" soundfile (see requirements-gpu.txt)'

_pipeline: Any = None


def available() -> bool:
    try:
        import kokoro  # noqa: F401
    except ImportError:
        return False
    return True


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


def sentences(text: str) -> list[str]:
    """One sentence per line: Kokoro speaks each as its own chunk, with a pause after it."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text.strip()) if p.strip()]
    return parts or [text.strip()]


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
    for line in sentences(text):
        for result in pipe(line, voice=voice, speed=speed):
            audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
            if audio.size == 0:
                continue
            tokens = getattr(result, "tokens", None) or []
            got = words_from_tokens(tokens, offset)
            if not got:  # no token timings for this chunk: spread its words over its audio
                from .video_assembler import estimate_word_timings

                got = [Word(w.text, round(offset + w.start, 3), round(offset + w.end, 3))
                       for w in estimate_word_timings(result.graphemes or line, audio.size / SAMPLE_RATE, lead_in=0.02)]
            words.extend(got)
            chunks.append(audio)
            offset += audio.size / SAMPLE_RATE
        chunks.append(np.zeros(int(SAMPLE_RATE * pause), dtype=np.float32))
        offset += pause
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
