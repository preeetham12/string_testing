"""
Part 2 - unseen test cases.

These recordings were NOT used when choosing the thresholds, so they show
whether the quality gate works in general or was only fitted to its own tests.
Includes 'false alarm' cases: good recordings that must NOT be rejected.

Usage: python -m tests.test_unseen
"""
import os
import warnings

import numpy as np
import librosa
import soundfile as sf

warnings.filterwarnings("ignore")
from engine.synth import render, STUDENT, TEACHER, SR
from engine.quality import check_recording
from engine.clean import clean_recording
from engine.pitch import analyse

OUT = "data/unseen"
REF = f"{OUT}/teacher_reference.wav"
rng = np.random.default_rng(42)


def coloured_noise(n, kind):
    """Fan noise is 'pink' (even), traffic is 'brown' (mostly low rumble)."""
    white = rng.normal(0, 1, n)
    spec = np.fft.rfft(white)
    f = np.maximum(np.fft.rfftfreq(n, 1 / SR), 1.0)
    spec /= np.sqrt(f) if kind == "pink" else f
    x = np.fft.irfft(spec, n)
    return x / np.std(x)


def add_noise(y, noise, db):
    """Add noise at a level `db` relative to the music's loudness."""
    return y + noise * np.sqrt(np.mean(y ** 2)) * 10 ** (db / 20)


def hum(n, base, level):
    t = np.arange(n) / SR
    return level * sum(np.sin(2 * np.pi * base * k * t) / k for k in (1, 2, 3))


def with_cents(notes, c):
    return [{**n, "cents": n.get("cents", 0) + c} for n in notes]


def scaled(notes, factor):
    return [{**n, "dur": n["dur"] * factor} for n in notes]


good = render(STUDENT, noise_db=-35, seed=11)
n = len(good)

# name: (audio, sample rate, channels, instrument, expected)
CASES = {
    "phone_48k_stereo":  (good, 48000, 2, "violin", "PASS"),
    "loud_not_clipped":  (good / np.max(np.abs(good)) * 0.97, SR, 1, "violin", "PASS"),
    "single_click":      (None, SR, 1, "violin", "PASS"),
    "slightly_out_10c":  (render(with_cents(STUDENT, -10), noise_db=-35, seed=12), SR, 1, "violin", "PASS"),
    "played_fast_1_6x":  (render(scaled(STUDENT, 1 / 1.45), noise_db=-35, seed=13), SR, 1, "violin", "PASS"),
    # measured 20.3 dB: just above the 20 dB warn line, so PASS is correct
    "fan_noise":         (add_noise(good, coloured_noise(n, "pink"), -18), SR, 1, "violin", "PASS"),
    # borderline: measures 19-21 dB against the 20 dB warning line, so PASS or
    # WARN are both acceptable; the clearly noisy case below must be flagged
    "traffic_rumble":    (add_noise(good, coloured_noise(n, "brown"), -12), SR, 1, "violin", ("WARN", "PASS")),
    "heavy_traffic":     (add_noise(good, coloured_noise(n, "brown"), -6), SR, 1, "violin", ("WARN", "FAIL")),
    # real rooms: echo after every note, and bow noise - must not look "noisy"
    "echoey_quiet_room": (None, SR, 1, "violin", "PASS"),
    "us_60hz_hum":       (np.clip(good + hum(n, 60, 0.09), -1, 1), SR, 1, "violin", "WARN"),
    "very_short_take":   (render(STUDENT[:1], noise_db=-35, seed=14), SR, 1, "violin", "WARN"),
    "c_major_arpeggio":  (render([{"midi": m, "dur": 0.5} for m in [72, 76, 79, 84, 79, 76, 72]],
                                 noise_db=-35, seed=15), SR, 1, "violin", "FAIL"),
    "mic_unplugged":     (np.zeros(SR * 5), SR, 1, "violin", "FAIL"),
    "cello_low_range":   (None, SR, 1, "cello", "PASS"),
    # long takes (28 notes, ~19 s): very little silence - found by the Part 5 tests
    "long_quiet_room":   (render(STUDENT * 4, noise_db=-40, seed=17), SR, 1, "violin", "PASS"),
    "long_very_noisy":   (render(STUDENT * 4, noise_db=-4, seed=18), SR, 1, "violin", "FAIL"),
}

# A single click: one loud sample (a bump of the mic stand) - must not count as clipping
y = good.copy()
y[int(2.0 * SR)] = 1.0
CASES["single_click"] = (y, SR, 1, "violin", "PASS")

# A quiet room with real-world echo and bow noise (the case user testing found)
from tests.test_web import realistic
CASES["echoey_quiet_room"] = (realistic(render(STUDENT, noise_db=-40, seed=19)) * 1.4, SR, 1, "violin", "PASS")

# Cello: the same melody two octaves lower (A2 ... F#3), compared with a cello teacher
cello_student = [{**x, "midi": x["midi"] - 24} for x in STUDENT]
cello_teacher = [{**x, "midi": x["midi"] - 24} for x in TEACHER]
CASES["cello_low_range"] = (render(cello_student, noise_db=-35, seed=16), SR, 1, "cello", "PASS")


def write(path, audio, sr, channels):
    if sr != SR:
        audio = librosa.resample(audio, orig_sr=SR, target_sr=sr)
    if channels == 2:
        audio = np.stack([audio, audio * 0.9], axis=1)  # slightly different L/R
    sf.write(path, audio, sr, subtype="PCM_16" if sr == 48000 else "FLOAT")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    sf.write(REF, render(TEACHER, noise_db=-40), SR, subtype="FLOAT")
    sf.write(f"{OUT}/cello_reference.wav", render(cello_teacher, noise_db=-40), SR, subtype="FLOAT")
    sf.write(f"{OUT}/long_reference.wav", render(TEACHER * 4, noise_db=-40), SR, subtype="FLOAT")

    print("Quality gate on unseen recordings\n")
    ok = 0
    for name, (audio, sr, ch, inst, expected) in CASES.items():
        path = f"{OUT}/{name}.wav"
        write(path, audio, sr, ch)
        ref = (f"{OUT}/cello_reference.wav" if inst == "cello"
               else f"{OUT}/long_reference.wav" if name.startswith("long_") else REF)
        r = check_recording(path, reference_path=ref, instrument=inst)
        hit = r["overall"] in (expected if isinstance(expected, tuple) else (expected,))
        ok += hit
        issues = "; ".join(f"{c['check']} {c['status'].lower()} ({c['value']})"
                           for c in r["checks"] if c["status"] != "PASS")
        print(f"  [{'PASS' if hit else 'FAIL'}] {name:18s} expected {'/'.join(expected) if isinstance(expected, tuple) else expected:9s} got {r['overall']:4s}  "
              f"{issues or 'all checks pass'}")
    print(f"\n  {ok}/{len(CASES)} judged correctly")

    print("\nCleaning the noisy ones, then detecting notes\n")
    TRUE = ["A4", "A4", "E5", "E5", "G5", "F#5", "E5"]
    for name in ["fan_noise", "traffic_rumble", "us_60hz_hum"]:
        path = f"{OUT}/{name}.wav"
        y, _ = clean_recording(path)
        cpath = f"{OUT}/{name}_cleaned.wav"
        sf.write(cpath, y, SR, subtype="FLOAT")
        for label, p in [("before", path), ("after ", cpath)]:
            notes = analyse(p)["notes"]
            names = [x["note"] for x in notes]
            flat = [x["cents"] for x in notes if x["note"] == "E5" and x["cents"] < -20]
            res = "all 7 notes correct" if names == TRUE else "WRONG: " + " ".join(names)
            print(f"  {name:15s} {label}  {res}" + (f", flat E5 at {flat[0]:+.1f} cents" if flat else ""))
