"""On-device voice used when Microsoft refuses the Edge voice (HTTP 403).

The first use downloads one Piper voice into assets/voices/. After that it needs no
internet and no VPN. Captions are estimated from the audio length, not from Microsoft's
word timings.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import PROJECT_ROOT
from .video_assembler import estimate_word_timings
from .video_assembler import Word

logger = logging.getLogger(__name__)

VOICE_NAME = "en_US-ryan-medium"  # male US narrator, closest to the default Edge voice
VOICE_DIR = PROJECT_ROOT / "assets" / "voices"

_voice = None


def length_scale(rate: str) -> float:
    """Piper's length scale from an edge-tts rate like '+5%' (under 1 is faster)."""
    import re

    match = re.fullmatch(r"([+-]\d{1,3})%", (rate or "").strip())
    percent = int(match.group(1)) if match else 0
    return 100 / (100 + percent) if percent > -90 else 1.0


def model_path() -> Path:
    return VOICE_DIR / f"{VOICE_NAME}.onnx"


def ensure_model() -> Path:
    """Download the voice model once. The .onnx.json config is saved beside it."""
    path = model_path()
    if path.is_file() and path.with_name(path.name + ".json").is_file() and path.stat().st_size > 0:
        return path
    from piper.download_voices import download_voice

    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    logger.warning("Downloading the local voice %s (once, about 60 MB)", VOICE_NAME)
    download_voice(VOICE_NAME, VOICE_DIR)
    if not path.is_file():
        raise RuntimeError(f"The local voice {VOICE_NAME} did not download")
    return path


def _load():
    global _voice
    if _voice is None:
        from piper import PiperVoice

        _voice = PiperVoice.load(ensure_model())
    return _voice


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
    from piper import SynthesisConfig

    voice = _load()
    config = SynthesisConfig(length_scale=length_scale(rate), normalize_audio=True)
    pcm = bytearray()
    sample_rate = 22050
    for chunk in voice.synthesize(text, syn_config=config):
        pcm.extend(chunk.audio_int16_bytes)
        sample_rate = chunk.sample_rate or sample_rate
    if not pcm:
        raise RuntimeError("The local voice returned no audio")
    duration = len(pcm) / 2 / sample_rate
    return _mp3(bytes(pcm), sample_rate), estimate_word_timings(text, duration)
