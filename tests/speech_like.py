"""A speech-like test signal (no recorded voices in the repository).

Glottal pulses whose pitch glides constantly (100-250 Hz) through vowel-like
formant filters, ~4 syllables per second, hissy consonants between them, and
short pauses between "words" - continuous enough to pass the older checks,
like the real talking that exposed the gap. On the instrument check it behaves like the
real speech it stands in for (FSDD recordings: steadiness 0.60-0.90, notes
covering 0-23% of the recording).
"""
import numpy as np
from scipy.signal import lfilter

SR = 22050


def _formant(x, f, bw):
    r = np.exp(-np.pi * bw / SR)
    return lfilter([1 - r], [1, -2 * r * np.cos(2 * np.pi * f / SR), r * r], x)


def speech_like(seconds=15.0, seed=0):
    rng = np.random.default_rng(seed)
    out, t = [], 0.0
    vowels = [(700, 1200), (400, 2000), (300, 2300), (500, 900), (600, 1700)]
    while t < seconds:
        for _ in range(int(rng.integers(2, 5))):          # a word: 2-4 syllables
            dur = rng.uniform(0.18, 0.4)
            n = int(dur * SR)
            f_start, f_end = rng.uniform(110, 240, 2)       # pitch glides through the syllable
            f0 = np.linspace(f_start, f_end, n) * (1 + 0.03 * np.sin(np.linspace(0, 6, n)))
            phase = np.cumsum(f0 / SR)
            pulses = (np.diff(np.floor(phase), prepend=0) > 0).astype(float)
            f1, f2 = vowels[int(rng.integers(len(vowels)))]
            v = _formant(pulses, f1, 90) + 0.6 * _formant(pulses, f2, 120)
            v *= np.hanning(n)
            hiss = rng.normal(0, 1, int(rng.uniform(0.02, 0.06) * SR)) * 0.05   # a consonant
            out += [hiss, v]
            t += (len(hiss) + n) / SR
        gap = np.zeros(int(rng.uniform(0.05, 0.2) * SR))   # between words
        out.append(gap)
        t += len(gap) / SR
    y = np.concatenate(out)
    y += rng.normal(0, 1e-3, len(y))
    return y / np.max(np.abs(y)) * 0.6
