"""Borderline Why sound effects, made in code (no files to license): a pop when a pin, icon or label appears, a
thud for a stamp, a whoosh when a flag fills a place or a place changes hands, paper for a title card, a marker
scratch while the whiteboard is written, and a bell on the end card.

sfx_track(spec, out) writes one WAV the length of the video with every sound at its moment (the element's `at`,
set when the scenes are aligned to the voice); bw_render.render mixes it under the voice when spec.sfx is on
(bw_channel / bw_long turn it on; BW_SFX=off drops it). Sounds closer than MIN_GAP are thinned, so a scene that
shows five things at once makes one pop, not five.
"""

from __future__ import annotations

import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

RATE = 44100
MIN_GAP = 0.22  # seconds between two sounds
GAIN = {"pop": 0.30, "stamp": 0.55, "whoosh": 0.22, "paper": 0.28, "marker": 0.12, "ding": 0.20, "cash": 0.24}
POP_KINDS = ("pin", "icon", "label", "ring", "pole", "counter")
LINE_KINDS = ("arrow", "line", "route")


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(RATE * seconds)) / RATE


def _noise(seconds: float, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).uniform(-1, 1, int(RATE * seconds))


def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    """A plain low-pass: a moving average over n samples."""
    return np.convolve(x, np.ones(n) / n, mode="same")


def _norm(x: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(x))) or 1.0
    fade = min(len(x), int(RATE * 0.005))
    x = x / peak
    x[:fade] *= np.linspace(0, 1, fade)
    x[-fade:] *= np.linspace(1, 0, fade)
    return x


@lru_cache(maxsize=None)
def sound(name: str, seconds: float = 0.0) -> np.ndarray:
    """One sound, peak 1.0 (`seconds`: the marker's length)."""
    if name == "pop":  # a quick falling blip
        t = _t(0.09)
        freq = 900 * np.exp(-t * 18) + 280
        x = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.exp(-t * 38)
    elif name == "stamp":  # a low thud with a papery slap
        t = _t(0.22)
        x = np.sin(2 * np.pi * 70 * t) * np.exp(-t * 22) + 0.6 * _smooth(_noise(0.22, 1), 6) * np.exp(-t * 45)
    elif name == "whoosh":  # filtered noise swelling and fading
        t = _t(0.42)
        env = np.sin(np.pi * t / t[-1]) ** 2
        x = (_smooth(_noise(0.42, 2), 14) - 0.6 * _smooth(_noise(0.42, 2), 60)) * env
    elif name == "paper":  # a short rustle
        t = _t(0.24)
        x = (_noise(0.24, 3) - _smooth(_noise(0.24, 3), 5)) * np.exp(-t * 14) * (0.6 + 0.4 * np.sin(2 * np.pi * 31 * t))
    elif name == "marker":  # a felt tip squeaking in strokes
        length = max(0.3, min(seconds or 1.2, 2.0))
        t = _t(length)
        strokes = (np.sin(2 * np.pi * 7 * t) > -0.2).astype(float)
        x = (_noise(length, 4) - _smooth(_noise(length, 4), 3)) * _smooth(strokes, 400)
    elif name == "ding":  # a small bell
        t = _t(0.9)
        x = (np.sin(2 * np.pi * 1320 * t) + 0.4 * np.sin(2 * np.pi * 2640 * t) + 0.2 * np.sin(2 * np.pi * 3960 * t)) * np.exp(-t * 5)
    elif name == "cash":  # a register: a quick rattle, then two bright bell hits
        t = _t(0.7)
        bell = lambda f, at: np.where(t >= at, (np.sin(2 * np.pi * f * (t - at)) + 0.3 * np.sin(2 * np.pi * 2 * f * (t - at)))
                                      * np.exp(-np.clip(t - at, 0, None) * 7), 0.0)
        rattle = (_noise(0.7, 5) - _smooth(_noise(0.7, 5), 4)) * np.exp(-t * 30) * 0.5
        x = rattle + bell(2093, 0.03) + 0.8 * bell(2637, 0.11)
    else:
        raise KeyError(name)
    return _norm(x.astype(np.float64))


def events(spec) -> list[tuple[float, str, float]]:
    """(time, sound, seconds) for every moment worth a sound, thinned to one per MIN_GAP."""
    found: list[tuple[float, str, float]] = []
    for scene in spec.scenes:
        if scene.kind == "card":
            found.append((scene.start, "paper", 0.0))
        elif scene.kind == "board":
            found.append((scene.start + 0.3, "marker", min(1.8, max(0.4, (scene.end - scene.start) * 0.5))))
        elif scene.kind == "end":
            found.append((scene.start + 0.2, "ding", 0.0))
        elif scene.kind == "photo":  # the print popping in with its flash, then the character (bw_photos.PhotoScene)
            found += [(scene.start, "paper", 0.0), (scene.start + 1.6, "pop", 0.0)]
        if scene.opts.get("year"):  # the year slamming in (bw_fx.FxRenderer.year)
            found.append((scene.start + 0.05, "stamp", 0.0))
        for el in scene.elements:
            if el.opts.get("_held"):  # an army standing where it arrived earlier (bw_fx.FxRenderer.hold_armies)
                continue
            if el.kind in ("stamp", "battle"):
                found.append((el.at, "stamp", 0.0))
            elif el.kind == "army":
                found.append((el.at + 0.3, "whoosh", 0.0))
            elif el.kind in POP_KINDS:
                found.append((el.at, "pop", 0.0))
            elif el.kind == "highlight" and (el.opts.get("flag") or el.opts.get("_side") == "to"):
                found.append((el.at, "whoosh", 0.0))
            elif el.kind in LINE_KINDS:
                found.append((el.at, "whoosh", 0.0))
    if getattr(spec, "style", "") == "war":  # "Attempt 2" slamming in (bw_fx.FxRenderer.attempt_tag)
        from modules import bw_render

        found += [(at, "stamp", 0.0) for at, _ in bw_render.attempt_marks(spec.words)]
    hook = getattr(spec, "hook", None) or {}
    if isinstance(hook.get("scene"), int):  # the opening flash-forward's cut to the outcome comes before anything near it
        from modules import bw_render

        flip = bw_render.HOOK_FLIP if hook.get("before") else 0.0
        found = [e for e in found if abs(e[0] - flip) >= MIN_GAP] + [(flip, "whoosh", 0.0)]
    found += list(getattr(spec, "sfx_extra", None) or [])  # a channel's own moments (modules.pw_render.sounds)
    found.sort(key=lambda e: (e[0], e[1] not in ("stamp", "cash")))  # a stamp or a cash ring wins a tie
    kept: list[tuple[float, str, float]] = []
    for e in found:
        if e[0] >= 0 and (not kept or e[0] - kept[-1][0] >= MIN_GAP):
            kept.append(e)
    return kept


def sfx_track(spec, out: Path, volume: float = 1.0) -> Path | None:
    """The sound-effects track (a mono 16-bit WAV as long as the video), or None when there is nothing to play."""
    found = events(spec)
    if not found:
        return None
    mix = np.zeros(int(RATE * (spec.duration + 1.0)))
    for at, name, seconds in found:
        x = sound(name, round(seconds, 1)) * GAIN[name] * volume
        i = int(RATE * at)
        j = min(len(mix), i + len(x))
        if i < j:
            mix[i:j] += x[: j - i]
    pcm = (np.clip(mix, -1, 1) * 32767).astype("<i2")
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return out
