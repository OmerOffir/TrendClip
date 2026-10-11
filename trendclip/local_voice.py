"""On-device voice used when Microsoft refuses the Edge voice (HTTP 403).

F5-TTS runs on the NVIDIA GPU and copies the short Edge recording in voices/ref.wav,
so the speaker stays close to the Edge voice without a VPN. The model weights download
on first use (about 1 GB). Captions are estimated from the audio length.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .video_assembler import Word, estimate_word_timings

logger = logging.getLogger(__name__)

VOICE_NAME = "F5-TTS"
REF_WAV = Path(__file__).parent / "voices" / "ref.wav"
REF_TEXT = (Path(__file__).parent / "voices" / "ref.txt").read_text(encoding="utf-8").strip()
INSTALL = (
    "On the Linux machine, in the project folder: "
    ".venv/bin/pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124 "
    "&& .venv/bin/pip install f5-tts"
)

_model = None


_DOTS = re.compile(r"\.{3,}|…+")


def speed(rate: str) -> float:
    """F5's own 1.0 is already a rush, so the baseline is slower. '+5%' still speeds up a little."""
    match = re.fullmatch(r"([+-]\d{1,3})%", (rate or "").strip())
    percent = int(match.group(1)) if match else 0
    return min(max(0.82 * (1 + percent / 100), 0.6), 1.15)


def paced_pieces(text: str) -> list[tuple[str, str | float]]:
    """Spoken sentences, with real silence after questions and wherever the script has '.....'."""
    out: list[tuple[str, str | float]] = []
    parts = _DOTS.split(text)  # placeholders filled below
    marks = _DOTS.findall(text)
    bits: list[str] = []
    for i, part in enumerate(parts):
        bits.append(part)
        if i < len(marks):
            bits.append(marks[i])
    for i, bit in enumerate(bits):
        if _DOTS.fullmatch(bit or ""):
            extra = max(0, len(bit) - 3)
            out.append(("silence", min(0.45 + extra * 0.16, 1.7)))
            continue
        nxt = bits[i + 1] if i + 1 < len(bits) else ""
        for sentence in re.split(r"(?<=[.!?])\s+", bit.strip()):
            sentence = sentence.strip()
            if not sentence:
                continue
            out.append(("text", sentence))
            if _DOTS.fullmatch(nxt or ""):
                continue
            pause = 0.5 if sentence.endswith("?") else 0.38 if sentence.endswith("!") else 0.22
            out.append(("silence", pause))
    return out


def _load():
    global _model
    if _model is None:
        try:
            from f5_tts.api import F5TTS
        except ImportError as err:
            raise RuntimeError(f"F5-TTS is not installed. {INSTALL}") from err
        if not REF_WAV.is_file():
            raise RuntimeError(f"Missing the reference voice {REF_WAV}")
        logger.warning("Loading F5-TTS (first run also downloads the model)")
        _model = F5TTS()
    return _model


def _mp3(pcm: bytes, sample_rate: int) -> bytes:
    import subprocess

    import imageio_ffmpeg

    proc = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
         "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
         "-f", "mp3", "-codec:a", "libmp3lame", "-qscale:a", "2", "pipe:1"],
        input=pcm, capture_output=True, timeout=180,
    )
    if proc.returncode != 0 or not proc.stdout:
        detail = proc.stderr.decode("utf-8", "replace").strip()[-200:]
        raise RuntimeError(f"Could not encode the local voice: {detail}")
    return proc.stdout


def speak(text: str, rate: str = "+0%") -> tuple[bytes, list[Word]]:
    """MP3 bytes and estimated word timings for `text`."""
    import numpy as np

    model = _load()
    pace = speed(rate)
    pcm = bytearray()
    words: list[Word] = []
    sample_rate = 24000
    clock = 0.0
    for kind, value in paced_pieces(text):
        if kind == "silence":
            pcm.extend(b"\x00\x00" * int(sample_rate * float(value)))
            clock += float(value)
            continue
        wav, sample_rate, _spec = model.infer(
            ref_file=str(REF_WAV),
            ref_text=REF_TEXT,
            gen_text=str(value),
            speed=pace,
            seed=42,
            show_info=lambda message: logger.info("%s", message),
        )
        sample_rate = int(sample_rate)
        audio = np.asarray(wav).squeeze()
        if audio.size == 0:
            raise RuntimeError("F5-TTS returned no audio")
        pcm.extend((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        duration = audio.shape[-1] / sample_rate
        words += [Word(w.text, round(w.start + clock, 3), round(w.end + clock, 3))
                  for w in estimate_word_timings(str(value), duration, lead_in=0.04)]
        clock += duration
    if not pcm:
        raise RuntimeError("F5-TTS returned no audio")
    return _mp3(bytes(pcm), sample_rate), words
