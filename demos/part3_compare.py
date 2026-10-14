"""
Run all of Part 3 and check the results.

Usage (from inside the stringcoach folder):
    python -m demos.part3_compare
"""
import os
import warnings

import numpy as np
import soundfile as sf
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

warnings.filterwarnings("ignore")
from engine.synth import render, TEACHER, SR
from engine.pipeline import prepare
from engine.compare import compare
from engine.pitch import midi_name
from tests.part3_cases import CASES

OUT = "data/part3"
os.makedirs(OUT, exist_ok=True)
os.makedirs("output", exist_ok=True)

print("Step 1/3  Preparing the teacher recording (clean -> detect notes) ...")
sf.write(f"{OUT}/teacher.wav", render(TEACHER, noise_db=-40), SR, subtype="FLOAT")
teacher = prepare(f"{OUT}/teacher.wav")
print(f"  teacher: {' '.join(n['note'] for n in teacher['notes'])}")

print("\nStep 2/3  Comparing each test student with the teacher ...")
total_ok, total, results = 0, 0, {}
for seed, (name, case) in enumerate(CASES.items(), start=20):
    path = f"{OUT}/{name}.wav"
    sf.write(path, render(case["notes"], noise_db=-35, seed=seed), SR, subtype="FLOAT")
    r = compare(teacher, prepare(path))
    results[name] = r
    exp = case["expect"]

    print(f"\n  {name}: {case['about']}")
    print(f"  tempo {r['tempo_ratio']}x | tuning offset {r['tuning_offset_cents']:+.0f} cents "
          f"({r['tuning_source']}) | {r['counts']}")
    print("    #  teacher  student  result       cents  timing  loud   tone  flags")
    for k, n in enumerate(r["notes"], 1):
        print(f"   {k:2d}  {n['teacher'] or '-':7s}  {n['student'] or '-':7s}  {n['status']:11s}"
              f"  {n.get('cents_error', ''):>5}  {n.get('timing_ms', ''):>6}"
              f"  {n.get('dynamics_db', ''):>5}  {n.get('tone_diff', ''):>4}  "
              f"{', '.join(n['flags'])}")

    checks = [("note counts", r["counts"] == exp["counts"]),
              ("tempo", abs(r["tempo_ratio"] - exp["tempo"]) <= 0.05)]
    if "flags" in exp:
        got = {k: n["flags"] for k, n in enumerate(r["notes"], 1) if n["flags"]}
        checks.append(("flags on the right notes", got == exp["flags"]))
    if "tuning" in exp:
        checks.append(("tuning offset", abs(r["tuning_offset_cents"] - exp["tuning"]) <= 5))
    for label, ok in checks:
        print(f"    [{'PASS' if ok else 'FAIL'}] {label}")
        total_ok += ok
        total += 1

print(f"\n  {total_ok}/{total} checks passed across {len(CASES)} test students")

print("\nStep 3/3  Drawing the comparison chart for the main student ...")
r = results["A_main_student"]
student = prepare(f"{OUT}/A_main_student.wav")
COL = {"correct": "#55A868", "wrong note": "#C44E52", "missed": "#8172B3", "extra": "#E8B04B"}
fig, (ax, ax2) = plt.subplots(2, 1, figsize=(12, 8.5), gridspec_kw={"height_ratios": [2.3, 1]})

T, S = teacher["notes"], r.get("student_notes", student["notes"])
for n in T:  # teacher notes on top
    ax.add_patch(Rectangle((n["start"], 1.6), n["end"] - n["start"] - 0.03, 0.8,
                           color="#4C72B0", alpha=0.85))
    ax.text((n["start"] + n["end"]) / 2, 2.0, n["note"], ha="center", va="center",
            color="white", fontsize=9, fontweight="bold")
s_iter = iter(S)
for row in r["notes"]:  # student notes below, coloured by result
    if row["student"] is None:
        continue
    n = next(s_iter)
    flagged = row["status"] == "correct" and row["flags"]
    col = "#E8B04B" if flagged else COL[row["status"]]
    ax.add_patch(Rectangle((n["start"], -0.4), n["end"] - n["start"] - 0.03, 0.8, color=col))
    ax.text((n["start"] + n["end"]) / 2, 0.0, n["note"], ha="center", va="center",
            color="white", fontsize=9, fontweight="bold")
    label = row["status"] if row["status"] != "correct" else (", ".join(row["flags"]) or "")
    if row.get("cents_error") is not None and "flat" in row["flags"]:
        label = f"flat {row['cents_error']:+.0f}c"
    if "late" in row["flags"]:
        label = f"late {row['timing_ms']} ms"
    if label:
        ax.text((n["start"] + n["end"]) / 2, -0.75, label, ha="center", fontsize=9,
                color="#333", fontweight="bold")
    if row["teacher"] is not None:  # line between matched notes
        tn = next(t for t in T if t["start"] == row["t_start"])
        ax.plot([(tn["start"] + tn["end"]) / 2, (n["start"] + n["end"]) / 2], [1.6, 0.4],
                color="#888", lw=1, ls="--")
ax.set_xlim(-0.1, max(S[-1]["end"], T[-1]["end"]) + 0.2)
ax.set_ylim(-1.1, 2.7)
ax.set_yticks([0, 2], ["Student", "Teacher"])
ax.set_xlabel("time (seconds, after trimming silence)")
ax.set_title(f"Note-by-note match: {r['counts']['correct']} correct, "
             f"{r['counts']['wrong note']} wrong, tempo {r['tempo_ratio']}x "
             f"(slower is allowed); dashed lines = matched pairs", fontweight="bold")
for side in ("top", "right", "left"):
    ax.spines[side].set_visible(False)
handles = [Rectangle((0, 0), 1, 1, color=c) for c in
           ["#4C72B0", "#55A868", "#E8B04B", "#C44E52"]]
ax.legend(handles, ["teacher", "correct", "right note, needs work", "wrong note"],
          loc="upper right", ncol=4, frameon=False, fontsize=9)

p = r["dtw_path"]
ax2.plot(p[:, 1], p[:, 0], color="#4C72B0", lw=2, label="DTW alignment path")
lim = max(p.max(axis=0))
ax2.plot([0, lim], [0, lim], color="#999", ls=":", label="same speed")
ax2.set_xlabel("student time (s)")
ax2.set_ylabel("teacher time (s)")
ax2.set_title("DTW maps student time to teacher time; it bends where the music differs\n(flat E5, then the wrong G5 and the pause); note matching above then makes exact decisions",
              fontweight="bold")
ax2.legend(frameon=False)
ax2.spines[["top", "right"]].set_visible(False)
plt.tight_layout()
plt.savefig("output/part3_comparison.png", dpi=130, bbox_inches="tight")
print("Chart saved to output/part3_comparison.png")
