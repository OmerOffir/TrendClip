"""Encore Story's music, made here in code: an original backing track in the band's style, never the band's song.

The owner found the first rock loop annoying (2026-11-12: raw sawtooth chords on every eighth note, the same loop
from start to end) and asked whether a home-made cover of the band's song would avoid paying. It would not: a cover
is still the band's composition, Content ID matches melodies, and the claim sends the money to the publisher. A style
is free to use, so each video gets an original track in the spirit of the band's era and genre:

    anthem   70s/80s arena rock (Queen, Bon Jovi): piano chords, a half-time beat with a big snare, a pad, and in
             the second half a two-guitar harmony line
    britpop  90s guitar pop (Oasis, Blur): strummed jangly guitars, tambourine, a straight beat
    heavy    metal and punk: palm-muted distorted power chords, a driving beat
    pop      pop, rap and the rest: piano plucks, a four-on-the-floor beat, claps

Every track builds: a quiet intro, then the band comes in, then everything in the last part, and a final chord.
The tempo is band_world.BPM, so the cartoon members keep playing in time. No samples or recordings are used.

    python -m modules.band_music anthem 45 out.mp3
"""

from __future__ import annotations

import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

RATE = 44100
STYLES = ("anthem", "britpop", "heavy", "pop")
PILLAR_STYLE = {"rock legends": "anthem", "90s and 2000s": "britpop", "metal and punk": "heavy",
                "pop, rap and the rest": "pop"}
NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8,
        "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
PROGRESSIONS = {
    "anthem": ((("C", 0), ("G", 0), ("A", 1), ("F", 0)), (("F", 0), ("C", 0), ("D", 1), ("Bb", 0)),
               (("D", 0), ("A", 0), ("B", 1), ("G", 0)), (("Eb", 0), ("Bb", 0), ("C", 1), ("Ab", 0))),
    "britpop": ((("G", 0), ("D", 0), ("C", 0), ("G", 0)), (("A", 0), ("E", 0), ("F#", 1), ("D", 0)),
                (("E", 1), ("G", 0), ("D", 0), ("A", 0)), (("C", 0), ("G", 0), ("A", 1), ("F", 0))),
    "heavy": ((("E", 1), ("C", 0), ("D", 0), ("E", 1)), (("A", 1), ("F", 0), ("G", 0), ("A", 1)),
              (("D", 1), ("Bb", 0), ("C", 0), ("D", 1))),
    "pop": ((("A", 1), ("F", 0), ("C", 0), ("G", 0)), (("C", 0), ("A", 1), ("F", 0), ("G", 0)),
            (("D", 1), ("Bb", 0), ("F", 0), ("C", 0))),
}


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def chord_notes(root: str, minor: int, base: int = 48) -> list[int]:
    r = base + NOTE[root]
    return [r, r + (3 if minor else 4), r + 7]


DEFAULT_STYLE = "anthem"  # the owner picked it out of the four for every video (2026-11-12)


def style_for(script: dict) -> str:
    """The script's "music" if someone set a style by hand, else anthem (PILLAR_STYLE is kept for a later test)."""
    asked = str(script.get("music") or "").lower()
    return asked if asked in STYLES else DEFAULT_STYLE


# --------------------------------------------------------------------------- the instruments


@lru_cache(maxsize=256)
def pluck(midi: float, seconds: float, bright: float = 0.6, decay: float = 0.996, seed: int = 0) -> np.ndarray:
    """A plucked string (Karplus-Strong): a burst of noise in a delay line that loses its highs as it rings."""
    rng = np.random.default_rng(seed + int(midi * 10))
    p = max(2, int(round(RATE / hz(midi))))
    n = int(seconds * RATE)
    out = np.zeros(n + p, np.float32)
    burst = rng.uniform(-1, 1, p).astype(np.float32)
    for _ in range(int((1 - bright) * 4)):  # a darker pick: smooth the burst
        burst = 0.5 * (burst + np.roll(burst, 1))
    out[:p] = burst
    for i in range(p, n + p, p):
        a = out[i - p:i]
        b = np.concatenate((out[i - p - 1:i - p] if i - p - 1 >= 0 else np.zeros(1, np.float32), out[i - p:i - 1]))
        m = min(p, n + p - i)
        out[i:i + m] = (decay * 0.5 * (a + b))[:m]
    sig = out[:n]
    return sig / max(1e-6, float(np.abs(sig).max()))


@lru_cache(maxsize=128)
def piano(midi: int, seconds: float) -> np.ndarray:
    """A soft piano note: harmonics that die away faster the higher they are."""
    t = np.arange(int(seconds * RATE)) / RATE
    f = hz(midi)
    sig = np.zeros_like(t)
    for k in range(1, 9):
        fk = f * k * (1 + 0.0004 * k * k)  # a little stretch, like real strings
        if fk > RATE / 2.2:
            break
        sig += np.sin(2 * np.pi * fk * t) / k ** 1.4 * np.exp(-t * (1.2 + 0.7 * k))
    attack = np.minimum(1.0, t / 0.004)
    return (sig * attack / max(1e-6, np.abs(sig).max())).astype(np.float32)


@lru_cache(maxsize=64)
def pad(midis: tuple[int, ...], seconds: float) -> np.ndarray:
    """A warm pad: detuned saws, filtered hard, swelling in and out."""
    t = np.arange(int(seconds * RATE)) / RATE
    sig = np.zeros_like(t)
    for m in midis:
        for det in (-0.08, 0.0, 0.08):
            sig += ((hz(m + det) * t) % 1.0) * 2 - 1
    sig = lowpass(sig.astype(np.float32), 0.04)
    env = np.minimum(1, t / 0.6) * np.minimum(1, (seconds - t) / 0.4).clip(0, 1)
    return (sig * env / max(1e-6, np.abs(sig).max())).astype(np.float32)


def lowpass(x: np.ndarray, alpha: float) -> np.ndarray:
    """A one-pole low-pass by its impulse response (numpy only)."""
    taps = int(min(4000, max(20, 6 / alpha)))
    kernel = (alpha * (1 - alpha) ** np.arange(taps)).astype(np.float32)
    return np.convolve(x, kernel)[: len(x)].astype(np.float32)


def bass_note(midi: int, seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    f = hz(midi)
    sig = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.12 * np.sin(2 * np.pi * 3 * f * t)
    env = np.minimum(1, t / 0.006) * np.exp(-t * 2.2) * np.minimum(1, (seconds - t) / 0.02).clip(0, 1)
    return (sig * env / 1.4).astype(np.float32)


def drums(rng) -> dict[str, np.ndarray]:
    k_t = np.arange(int(0.4 * RATE)) / RATE
    kick = np.sin(2 * np.pi * np.cumsum(48 + 70 * np.exp(-k_t * 35)) / RATE) * np.exp(-k_t * 7)
    s_t = np.arange(int(0.35 * RATE)) / RATE
    noise = lowpass(rng.uniform(-1, 1, len(s_t)).astype(np.float32), 0.45)
    snare = (0.75 * noise * np.exp(-s_t * 14) + 0.5 * np.sin(2 * np.pi * 185 * s_t) * np.exp(-s_t * 22))
    h_t = np.arange(int(0.05 * RATE)) / RATE
    hat = np.diff(rng.uniform(-1, 1, len(h_t) + 1)) * np.exp(-h_t * 90)
    c_t = np.arange(int(2.0 * RATE)) / RATE
    crash = lowpass(np.diff(rng.uniform(-1, 1, len(c_t) + 1)).astype(np.float32), 0.6) * np.exp(-c_t * 2.0)
    burst = noise[: int(0.12 * RATE)] * np.exp(-s_t[: int(0.12 * RATE)] * 30)
    clap = np.zeros(len(burst) + int(0.023 * RATE), np.float32)  # three hands a few ms apart
    for d in (0.0, 0.011, 0.023):
        clap[int(d * RATE):int(d * RATE) + len(burst)] += burst
    tamb_t = np.arange(int(0.09 * RATE)) / RATE
    tamb = np.diff(rng.uniform(-1, 1, len(tamb_t) + 1)) * np.exp(-tamb_t * 40) * (0.6 + 0.4 * np.sin(2 * np.pi * 7000 * tamb_t))
    tom_t = np.arange(int(0.45 * RATE)) / RATE
    tom = np.sin(2 * np.pi * np.cumsum(110 + 40 * np.exp(-tom_t * 20)) / RATE) * np.exp(-tom_t * 8)
    return {k: v.astype(np.float32) for k, v in dict(kick=kick, snare=snare, hat=hat, crash=crash, clap=clap,
                                                     tamb=tamb, tom=tom).items()}


def reverb(x: np.ndarray, seconds: float = 1.4, rng=None) -> np.ndarray:
    """A hall: the dry sound convolved with a decaying burst of noise (by FFT, so it's quick)."""
    rng = rng or np.random.default_rng(1)
    t = np.arange(int(seconds * RATE)) / RATE
    ir = lowpass(rng.uniform(-1, 1, len(t)).astype(np.float32), 0.25) * np.exp(-t * 4.5)
    ir /= np.sqrt(np.sum(ir ** 2))
    n = len(x) + len(ir)
    size = 1 << (n - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return wet.astype(np.float32)


# --------------------------------------------------------------------------- the track


def bed(seconds: float, out: Path, style: str = "anthem", seed: int = 0, bpm: int = 124) -> Path:
    """An original backing track of `seconds` in `style`, written to `out` (mp3, stereo)."""
    style = style if style in STYLES else "anthem"
    rng = np.random.default_rng(seed + 11)
    progs = PROGRESSIONS[style]
    prog = progs[seed % len(progs)]
    beat = 60.0 / bpm
    bar = beat * 4
    bars = int(np.ceil((seconds + 0.5) / bar)) + 1
    n = int((bars * bar + 2.5) * RATE)
    L = np.zeros(n, np.float32)
    R = np.zeros(n, np.float32)
    send = np.zeros(n, np.float32)  # what goes to the hall
    kit = drums(rng)

    def add(at: float, sig: np.ndarray, gain: float, pan: float = 0.0, wet: float = 0.15):
        a = int(at * RATE)
        if a >= n or gain <= 0:
            return
        b = min(n, a + len(sig))
        s = sig[: b - a] * gain
        L[a:b] += s * np.sqrt(0.5 * (1 - pan))
        R[a:b] += s * np.sqrt(0.5 * (1 + pan))
        send[a:b] += s * wet

    end_bar = int(seconds / bar)  # the bar the voice ends in: the final chord rings from there
    full_from = max(2, int(bars * 0.5))
    for bi in range(bars):
        t0 = bi * bar
        root, minor = prog[bi % len(prog)]
        notes = chord_notes(root, minor)
        part = "intro" if bi < 2 else "full" if bi >= full_from else "verse"
        last = bi >= end_bar
        if last:  # one big chord, a crash, and let it ring
            for m in notes:
                add(t0, piano(m + 12, 3.0), 0.3, -0.2, 0.4) if style in ("anthem", "pop") else None
                add(t0, pluck(m, 3.0, 0.6, 0.998, seed), 0.35, 0.3, 0.3)
            add(t0, bass_note(notes[0] - 12, 2.5), 0.5)
            add(t0, kit["crash"], 0.35, 0.3, 0.4)
            add(t0, kit["kick"], 0.8)
            break
        # drums
        for b4 in range(4):
            at = t0 + b4 * beat
            if part == "intro" and style != "heavy":
                if style != "anthem":
                    add(at, kit["hat"], 0.12, 0.4, 0.05)
                continue
            if style == "anthem":
                if b4 == 0 or (part == "full" and b4 == 1 and bi % 2):
                    add(at + (beat / 2 if b4 == 1 else 0), kit["kick"], 0.9)
                if b4 == 2:
                    add(at, kit["snare"], 0.55, 0.0, 0.6)
                add(at, kit["hat"], 0.1, 0.35, 0.05)
                if part == "full" and bi % 4 == 3 and b4 == 3:  # a tom fill into the next phrase
                    for k, d in enumerate((0, 0.25, 0.5, 0.75)):
                        add(at + d * beat, kit["tom"] * (1 - 0.15 * k), 0.4, -0.3 + 0.2 * k, 0.3)
            elif style == "britpop":
                if b4 in (0, 2):
                    add(at, kit["kick"], 0.85)
                if b4 == 1 and part == "full":
                    add(at + beat / 2, kit["kick"], 0.5)
                if b4 in (1, 3):
                    add(at, kit["snare"], 0.5, 0.0, 0.3)
                add(at, kit["hat"], 0.11, 0.35, 0.05)
                add(at + beat / 2, kit["tamb"], 0.12, -0.5, 0.1)
            elif style == "heavy":
                if part == "intro":
                    add(at, kit["hat"], 0.12, 0.3, 0.05)
                    continue
                add(at, kit["kick"], 0.85)
                add(at + beat / 2, kit["kick"], 0.6)
                if b4 in (1, 3):
                    add(at, kit["snare"], 0.6, 0.0, 0.25)
                add(at, kit["hat"], 0.12, 0.35, 0.05)
            else:  # pop
                add(at, kit["kick"], 0.8)
                if b4 in (1, 3):
                    add(at, kit["clap"], 0.35, 0.0, 0.3)
                add(at + beat / 2, kit["hat"], 0.13, 0.4, 0.05)
        if part != "intro" and bi % 8 == 0:
            add(t0, kit["crash"], 0.18, 0.4, 0.3)
        # harmony instruments
        if style == "anthem":
            hits = (0, 1, 2, 3) if part != "intro" else (0, 2)
            for b4 in hits:
                for k, m in enumerate(notes):
                    add(t0 + b4 * beat + 0.006 * k, piano(m + 12, beat * 1.6), 0.13 if b4 else 0.17, -0.25, 0.3)
            add(t0, pad(tuple(m + 12 for m in notes), bar * 1.02), 0.05 if part == "intro" else 0.08, 0.2, 0.3)
            if part != "intro":
                for b4 in range(4):
                    add(t0 + b4 * beat, bass_note(notes[0] - 12, beat * 0.95), 0.42)
            if part == "full":  # two guitars in harmony, a slow line over the chords (Queen-like, not a Queen tune)
                line = [notes[2] + 12, notes[1] + 12, notes[0] + 12, notes[1] + 12][(bi % 2) * 2:][:2] + [notes[2] + 12]
                for k, m in enumerate(line[:2]):
                    d = beat * 2 * k
                    for harm, pan in ((0, -0.45), (3 if minor else 4, 0.45)):
                        g = np.tanh(2.2 * pluck(m + harm, beat * 2.2, 0.7, 0.9985, seed + harm))
                        add(t0 + d, lowpass(g, 0.35), 0.085, pan, 0.45)
        elif style == "britpop":
            pattern = (0, 1, 1.5, 2.5, 3, 3.5) if part != "intro" else (0, 2)
            for k, pos in enumerate(pattern):
                down = int(pos * 2) % 2 == 0
                voicing = [notes[0] - 12, notes[2] - 12, notes[0], notes[1], notes[2], notes[0] + 12]
                for j, m in enumerate(voicing if down else voicing[::-1][:4]):
                    add(t0 + pos * beat + 0.009 * j, pluck(m, beat * 1.4, 0.75, 0.995, seed), 0.07 if down else 0.05,
                        -0.4 if k % 2 else 0.4, 0.2)
            if part != "intro":
                for b4 in range(4):
                    add(t0 + b4 * beat, bass_note(notes[0] - 12, beat * 0.9), 0.4)
            if part == "full":
                add(t0, pad(tuple(m + 12 for m in notes), bar * 1.02), 0.05, 0.0, 0.3)
        elif style == "heavy":
            power = [notes[0] - 12, notes[0] - 5, notes[0]]
            for e in range(8):
                muted = part == "intro" or e % 4 not in (0, 3)
                length = beat * (0.22 if muted else 0.48)
                chord = sum(pluck(m, length, 0.8, 0.997, seed) for m in power) / 3
                g = lowpass(np.tanh(6 * chord), 0.3 if muted else 0.45)
                for pan in (-0.6, 0.6):
                    add(t0 + e * beat / 2 + (0.004 if pan > 0 else 0), g, 0.09 if part != "intro" else 0.06, pan, 0.08)
            if part != "intro":
                for e in range(8):
                    add(t0 + e * beat / 2, bass_note(notes[0] - 12, beat * 0.45), 0.38)
        else:  # pop
            for e in (0, 1.5, 3, 4.5, 6):
                for k, m in enumerate(notes):
                    add(t0 + e * beat / 2 + 0.004 * k, pluck(m + 12, beat * 0.9, 0.85, 0.993, seed), 0.08, 0.3 * (k - 1), 0.25)
            add(t0, pad(tuple(m + 12 for m in notes), bar * 1.02), 0.07, 0.0, 0.35)
            if part != "intro":
                for b4 in (0, 1.5, 2, 3.5):
                    add(t0 + b4 * beat, bass_note(notes[0] - 12, beat * 0.7), 0.4)
    hall = reverb(send, 1.6 if style == "anthem" else 1.1, rng)
    L += 0.35 * hall
    R += 0.35 * np.roll(hall, int(0.011 * RATE))  # a touch of width
    mix = np.stack([L, R])
    mix = np.tanh(1.3 * mix / max(1e-6, float(np.abs(mix).max()))) / np.tanh(1.3)  # glue: a soft limiter
    total = min(n, int((seconds + 2.5) * RATE))
    mix = mix[:, :total]
    fade_in, fade_out = int(0.03 * RATE), int(1.2 * RATE)
    mix[:, :fade_in] *= np.linspace(0, 1, fade_in)
    mix[:, -fade_out:] *= np.linspace(1, 0, fade_out)
    pcm = (mix.T * 0.92 * 32767).astype("<i2").tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ac", "2", "-ar", str(RATE), "-i", "-",
                    "-c:a", "libmp3lame", "-b:a", "192k", str(out)], input=pcm, check=True)
    return out


if __name__ == "__main__":
    style, secs, path = sys.argv[1], float(sys.argv[2]), Path(sys.argv[3])
    print(bed(secs, path, style, int(sys.argv[4]) if len(sys.argv) > 4 else 0))
