"""
Part 2 test set: one good recording plus recordings with typical real problems.
Each case says what the quality gate SHOULD decide, so we can check it.
"""
import os
import numpy as np
import soundfile as sf

from engine.synth import render, TEACHER, STUDENT, SR, A4, E5, FS5

OUT = "data/qc_cases"
rng = np.random.default_rng(7)


def hum(n, level):
    """Electrical mains hum: 50 Hz in India, plus 100 and 150 Hz echoes."""
    t = np.arange(n) / SR
    return level * (np.sin(2 * np.pi * 50 * t) + 0.5 * np.sin(2 * np.pi * 100 * t)
                    + 0.3 * np.sin(2 * np.pi * 150 * t))


def with_cents(notes, c):
    return [{**n, "cents": n.get("cents", 0) + c} for n in notes]


good = render(STUDENT, noise_db=-35, seed=1)

CASES = {
    "01_good":           (good, "PASS"),
    "02_clipping":       (np.clip(good * 6, -1, 1), "FAIL"),
    "03_too_quiet":      (good * 0.02, "FAIL"),
    "04_some_noise":     (render(STUDENT, noise_db=-17, seed=2), "WARN"),
    "05_very_noisy":     (render(STUDENT, noise_db=-4, seed=3), "FAIL"),
    "06_mains_hum":      (None, "WARN"),
    "07_silence_only":   (rng.normal(0, 0.003, SR * 5), "FAIL"),
    "08_dropouts":       (None, "FAIL"),
    "09_wrong_piece":    (render([{"midi": m, "dur": 0.5} for m in
                                  [E5, 78, 76, 74, 73, 71, 69]], noise_db=-35), "FAIL"),
    "10_out_of_tune":    (render(with_cents(STUDENT, -25), noise_db=-35, seed=4), "WARN"),
    "11_stopped_early":  (render(STUDENT[:2], noise_db=-35, seed=5), "WARN"),
}

# Mains hum: music plus strong 50 Hz hum
y = render(STUDENT, noise_db=-40, seed=6)
CASES["06_mains_hum"] = (np.clip(y + hum(len(y), 0.08), -1, 1), "WARN")

# Dropouts: three 20 ms gaps of digital silence in the middle of the playing
y = good.copy()
for at in (1.2, 2.2, 3.8):
    y[int(at * SR): int((at + 0.02) * SR)] = 0.0
CASES["08_dropouts"] = (y, "FAIL")

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    sf.write(f"{OUT}/teacher_reference.wav", render(TEACHER, noise_db=-40), SR,
             subtype="FLOAT")
    for name, (audio, _) in CASES.items():
        # FLOAT keeps exact zeros and exact full-scale samples, like a real capture
        sf.write(f"{OUT}/{name}.wav", audio, SR, subtype="FLOAT")
    print(f"Wrote {len(CASES)} test recordings + teacher reference to {OUT}/")
