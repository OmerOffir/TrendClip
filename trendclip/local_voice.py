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


def speed(rate: str) -> float:
    """Map the dashboard Speed menu onto F5. F5 at 1.0 already rushes, so Normal is 0.85
    and the menu still speeds up or slows down from there."""
    match = re.fullmatch(r"([+-]\d{1,3})%", (rate or "").strip())
    percent = int(match.group(1)) if match else 0
    return round(min(max(0.85 * (1 + percent / 100), 0.55), 1.2), 3)


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

    wav, sample_rate, _spec = _load().infer(
        ref_file=str(REF_WAV),
        ref_text=REF_TEXT,
        gen_text=text,
        speed=speed(rate),
        seed=42,
        show_info=lambda message: logger.info("%s", message),
    )
    audio = np.asarray(wav).squeeze()
    if audio.size == 0:
        raise RuntimeError("F5-TTS returned no audio")
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
    duration = audio.shape[-1] / sample_rate
    return _mp3(pcm, int(sample_rate)), estimate_word_timings(text, duration)
