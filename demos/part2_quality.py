"""
Run all of Part 2 and check the results.

Usage (from inside the stringcoach folder):
    python -m demos.part2_quality
"""
import os
import subprocess
import sys
import warnings

import numpy as np
import librosa
import librosa.display
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import soundfile as sf

warnings.filterwarnings("ignore")
from engine.quality import check_recording
from engine.clean import clean_recording
from engine.pitch import analyse, SR
from tests.make_bad_recordings import CASES

os.makedirs("output", exist_ok=True)
REF = "data/qc_cases/teacher_reference.wav"

print("Step 1/4  Creating test recordings ...")
subprocess.run([sys.executable, "-m", "engine.synth"], check=True, capture_output=True)
subprocess.run([sys.executable, "-m", "tests.make_bad_recordings"], check=True)

print("\nStep 2/4  Running the quality gate on every recording ...\n")
results, ok = {}, 0
for name, (_, expected) in CASES.items():
    r = check_recording(f"data/qc_cases/{name}.wav", reference_path=REF)
    results[name] = r
    hit = r["overall"] == expected
    ok += hit
    print(f"  [{'PASS' if hit else 'FAIL'}] {name:18s} expected {expected:4s}  got {r['overall']}")
    for c in r["checks"]:
        if c["status"] != "PASS":
            print(f"          {c['status']} {c['check']} ({c['value']}): {c['message']}")
print(f"\n  {ok}/{len(CASES)} recordings judged correctly")

print("\nStep 3/4  Cleaning noisy recordings, then re-detecting notes ...\n")
TRUE = ["A4", "A4", "E5", "E5", "G5", "F#5", "E5"]
for case in ["04_some_noise", "06_mains_hum", "05_very_noisy"]:
    raw = f"data/qc_cases/{case}.wav"
    y, _ = clean_recording(raw)
    out = f"data/qc_cases/{case}_cleaned.wav"
    sf.write(out, y, SR, subtype="FLOAT")
    for label, p in [("before cleaning", raw), ("after cleaning ", out)]:
        names = [n["note"] for n in analyse(p)["notes"]]
        snr = [c["value"] for c in check_recording(p)["checks"] if c["check"] == "noise"][0]
        if names == TRUE:
            verdict = "all 7 notes correct"
        elif not names:
            verdict = "no notes trusted (too noisy to measure)"
        else:
            verdict = f"WRONG: {' '.join(names)}"
        print(f"  {case:15s} {label}  noise gap {snr:>8s}   {verdict}")
    print()

print("Step 4/4  Drawing charts ...")
# Chart 1: quality gate results grid
check_names = ["clipping", "level", "dropouts", "noise", "playing",
               "tuning", "duration", "same piece"]
code = {"PASS": 0, "WARN": 1, "FAIL": 2}
grid = np.full((len(CASES), len(check_names)), np.nan)
for i, name in enumerate(CASES):
    for c in results[name]["checks"]:
        grid[i, check_names.index(c["check"])] = code[c["status"]]
fig, ax = plt.subplots(figsize=(11, 6.2))
ax.imshow(np.ma.masked_invalid(grid), cmap=ListedColormap(["#55A868", "#E8B04B", "#C44E52"]),
          vmin=0, vmax=2, aspect="auto")
for i in range(grid.shape[0]):
    for j in range(grid.shape[1]):
        txt = "-" if np.isnan(grid[i, j]) else ["pass", "warn", "FAIL"][int(grid[i, j])]
        ax.text(j, i, txt, ha="center", va="center", fontsize=9,
                color="white" if txt in ("pass", "FAIL") else "black")
ax.set_xticks(range(len(check_names)), check_names, rotation=0, fontsize=9)
labels = [f"{n[3:].replace('_', ' ')}  ->  {results[n]['overall']}" for n in CASES]
ax.set_yticks(range(len(CASES)), labels, fontsize=9.5)
ax.set_title("Quality gate: every bad recording is caught by the right check\n"
             "(green pass, amber warn, red fail; '-' = skipped because no playing was heard)",
             fontweight="bold", fontsize=11)
plt.tight_layout()
plt.savefig("output/part2_quality_gate.png", dpi=130, bbox_inches="tight")
plt.close()

# Chart 2: mains hum before vs after cleaning
fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), sharey=True)
for ax, p, title in [(axes[0], "data/qc_cases/06_mains_hum.wav", "Before cleaning"),
                     (axes[1], "data/qc_cases/06_mains_hum_cleaned.wav", "After cleaning")]:
    y, _ = librosa.load(p, sr=SR)
    S = librosa.amplitude_to_db(np.abs(librosa.stft(y, n_fft=4096, hop_length=256)), ref=np.max)
    librosa.display.specshow(S, sr=SR, hop_length=256, x_axis="time", y_axis="log",
                             ax=ax, vmin=-70, vmax=0, cmap="magma")
    ax.set_ylim(30, 4000)
    ax.set_title(title, fontweight="bold")
axes[0].annotate("50 / 100 / 150 Hz hum", xy=(2.5, 70), xytext=(0.3, 32),
                 color="white", fontsize=9, arrowprops=dict(arrowstyle="->", color="white"))
fig.suptitle("Mains hum automatically detected (50 Hz) and removed; violin notes untouched\n(After is shorter because silence at the start and end is trimmed)",
             fontweight="bold", fontsize=11)
plt.tight_layout()
plt.savefig("output/part2_cleaning_hum.png", dpi=130, bbox_inches="tight")
print("Charts saved to output/part2_quality_gate.png and output/part2_cleaning_hum.png")
