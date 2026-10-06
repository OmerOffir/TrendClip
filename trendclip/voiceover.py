"""Free voiceover via edge-tts (Microsoft Edge online voices) with exact word timestamps."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path

from .video_assembler import Word

VOICE_CACHE_SECONDS = 24 * 3600


class VoiceError(RuntimeError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower())


def restore_punctuation(script: str, spoken: list[Word]) -> list[Word]:
    """edge-tts word boundaries drop punctuation; take the matching tokens from the script so
    subtitles keep commas/full stops (which also drive the line breaks)."""
    tokens = script.split()
    out: list[Word] = []
    i = 0
    token, remaining = "", ""  # script token being voiced, and its not-yet-spoken letters
    for word in spoken:
        spoken_norm = _norm(word.text)
        if not remaining:
            for j in range(i, min(i + 4, len(tokens))):
                if spoken_norm and _norm(tokens[j]).startswith(spoken_norm):
                    token, remaining, i = tokens[j], _norm(tokens[j]), j + 1
                    break
        if spoken_norm and remaining.startswith(spoken_norm):
            remaining = remaining[len(spoken_norm):]
            if _norm(token) == spoken_norm:
                text = token
            elif not remaining:  # last piece of a token voiced in parts ("GTA-6" -> "GTA", "6")
                text = word.text + (m.group(0) if (m := re.search(r"[^\w]+$", token)) else "")
            else:
                text = word.text
            out.append(Word(text, word.start, word.end))
        else:
            remaining = ""
            out.append(word)
    return [w for w in out if w.text]


async def _synthesize(text: str, voice: str, rate: str) -> tuple[bytes, list[Word]]:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    audio = bytearray()
    words: list[Word] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            start = chunk["offset"] / 1e7
            words.append(Word(chunk["text"], round(start, 3), round(start + chunk["duration"] / 1e7, 3)))
    return bytes(audio), words


RETRY_WAITS = (2, 4, 8, 15)  # edge-tts needs the internet; ride out short Wi-Fi / DNS drops


def synthesize(text: str, output_mp3: Path, voice: str, rate: str = "+0%",
               waits: tuple[float, ...] = RETRY_WAITS) -> list[Word]:
    """Write the MP3 and a <name>.words.json next to it; returns the timed words."""
    text = text.strip()
    if not text:
        raise VoiceError("The script is empty")
    last_error: Exception | None = None
    for wait in (*waits, None):
        try:
            audio, words = asyncio.run(_synthesize(text, voice, rate))
            break
        except Exception as err:  # noqa: BLE001 - network / service errors from edge-tts
            last_error = err
            if wait is not None:
                time.sleep(wait)
    else:
        offline = re.search(r"connect|dns|nodename|servname|timeout|network", f"{type(last_error).__name__} {last_error}", re.I)
        hint = "Can't reach Microsoft's voice server: check your internet connection and try again. " if offline else ""
        raise VoiceError(f"Voice generation failed. {hint}({type(last_error).__name__}: {last_error})")
    if not audio or not words:
        raise VoiceError(f"The voice '{voice}' returned no audio; pick another voice")

    words = restore_punctuation(text, words)
    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    output_mp3.write_bytes(audio)
    output_mp3.with_suffix(".words.json").write_text(
        json.dumps([{"word": w.text, "start": w.start, "end": w.end} for w in words], indent=1),
        encoding="utf-8",
    )
    return words


_voices: tuple[float, list[dict]] | None = None
_voices_lock = threading.Lock()


def list_voices(locale_prefix: str = "en-") -> list[dict]:
    """[{name, label, gender, locale}] for the given language, cached for a day."""
    global _voices
    with _voices_lock:
        if _voices is None or time.time() - _voices[0] > VOICE_CACHE_SECONDS:
            import edge_tts

            try:
                raw = asyncio.run(edge_tts.list_voices())
            except Exception as err:  # noqa: BLE001
                raise VoiceError(f"Could not load the voice list: {err}") from err
            _voices = (time.time(), raw)
        raw = _voices[1]
    voices = []
    for v in raw:
        if not v["Locale"].startswith(locale_prefix):
            continue
        short = v["ShortName"].split("-", 2)[-1].replace("Neural", "")
        short = short.replace("Multilingual", " (multilingual)")
        voices.append({
            "name": v["ShortName"],
            "label": f"{short} · {v['Gender'].lower()} · {v['Locale']}",
            "gender": v["Gender"],
            "locale": v["Locale"],
        })
    order = {"en-US": 0, "en-GB": 1, "en-AU": 2, "en-CA": 3}
    return sorted(voices, key=lambda v: (order.get(v["locale"], 9), v["locale"], v["name"]))
