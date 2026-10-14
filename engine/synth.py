"""
Part 1 - Synthetic test recordings.

We don't have real recordings yet, so we generate a violin-like teacher
recording and a student recording with KNOWN mistakes. If the engine later
finds exactly these mistakes, we know it works.
"""
import json
import numpy as np
import soundfile as sf

SR = 22050  # samples per second used for all analysis


def note_to_hz(midi):
    """MIDI note number -> frequency in Hz (A4 = 69 = 440 Hz)."""
    return 440.0 * 2 ** ((midi - 69) / 12)


def bowed_note(freq, dur, sr=SR, cents_offset=0.0, vibrato=True, vib_cents=15.0):
    """One violin-like note: harmonics + soft bow attack + vibrato."""
    t = np.arange(int(dur * sr)) / sr
    f = freq * 2 ** (cents_offset / 1200)
    # Vibrato starts after 0.25 s: 5.5 Hz wobble of +/-15 cents (typical violin)
    vib_depth = np.where(t > 0.25, vib_cents, 0.0) if vibrato else 0.0
    inst_f = f * 2 ** ((vib_depth * np.sin(2 * np.pi * 5.5 * t)) / 1200)
    phase = 2 * np.pi * np.cumsum(inst_f) / sr
    # Violin tone = fundamental + many harmonics with falling strength
    tone = sum((1 / k) * np.sin(k * phase) for k in range(1, 9))
    # Bowed envelope: soft 60 ms attack, 40 ms release (the bow change)
    env = np.ones_like(t)
    a, r = int(0.06 * sr), int(0.04 * sr)
    env[:a] = np.linspace(0, 1, a)
    env[-r:] = np.linspace(1, 0, r)
    return 0.3 * tone * env


def render(notes, sr=SR, noise_db=None, seed=0):
    """notes: list of dicts {midi, dur, cents, gap_before}. Returns audio."""
    parts = [np.zeros(int(0.3 * sr))]  # short silence before playing
    for n in notes:
        parts.append(np.zeros(int(n.get("gap_before", 0.0) * sr)))
        parts.append(n.get("gain", 1.0) *  # gain < 1 = played softer
                     bowed_note(note_to_hz(n["midi"]), n["dur"], sr, n.get("cents", 0.0),
                                vib_cents=n.get("vib", 15.0)))
    parts.append(np.zeros(int(0.3 * sr)))
    y = np.concatenate(parts)
    if noise_db is not None:  # room noise at a given level below the music
        rng = np.random.default_rng(seed)
        y = y + rng.normal(0, 1, len(y)) * np.sqrt(np.mean(y ** 2)) * 10 ** (noise_db / 20)
    return y / np.max(np.abs(y)) * 0.8


# "Twinkle Twinkle" opening in A major (public domain), as violin notes
A4, E5, FS5, G5 = 69, 76, 78, 79
TEACHER = [
    {"midi": A4, "dur": 0.5}, {"midi": A4, "dur": 0.5},
    {"midi": E5, "dur": 0.5}, {"midi": E5, "dur": 0.5},
    {"midi": FS5, "dur": 0.5}, {"midi": FS5, "dur": 0.5},
    {"midi": E5, "dur": 1.0},
]

# Student: 10% slower overall, plus four deliberate mistakes
SLOW = 1.1
STUDENT = [
    {"midi": A4, "dur": 0.5 * SLOW},
    {"midi": A4, "dur": 0.5 * SLOW},
    {"midi": E5, "dur": 0.5 * SLOW},
    {"midi": E5, "dur": 0.5 * SLOW, "cents": -35},  # mistake 1: 35 cents flat
    {"midi": G5, "dur": 0.5 * SLOW},                 # mistake 2: wrong note (G5, not F#5)
    {"midi": FS5, "dur": 0.5 * SLOW, "gap_before": 0.45},  # mistake 3: hesitation pause
    {"midi": E5, "dur": 1.0 * SLOW},
]
KNOWN_MISTAKES = [
    {"note": 4, "type": "intonation", "detail": "E5 played 35 cents flat"},
    {"note": 5, "type": "wrong_note", "detail": "G5 played instead of F#5"},
    {"note": 6, "type": "timing", "detail": "0.45 s hesitation before F#5"},
    {"note": "all", "type": "tempo", "detail": "whole piece 10% slower (not a mistake)"},
]

if __name__ == "__main__":
    import os
    out = "data"
    os.makedirs(out, exist_ok=True)
    sf.write(f"{out}/teacher.wav", render(TEACHER, noise_db=-40), SR)
    sf.write(f"{out}/student.wav", render(STUDENT, noise_db=-28, seed=1), SR)
    json.dump(KNOWN_MISTAKES, open(f"{out}/known_mistakes.json", "w"), indent=2)
    print("Wrote teacher.wav, student.wav and known_mistakes.json")
