"""
Run all of Part 1 and check the results.

Usage (from inside the stringcoach folder):
    python -m demos.part1_pitch
"""
import json
import os
import subprocess
import sys

from engine.pitch import analyse

print("Step 1/3  Creating test recordings ...")
subprocess.run([sys.executable, "-m", "engine.synth"], check=True)

print("\nStep 2/3  Detecting notes and pitch ...")
results = {}
for who in ["teacher", "student"]:
    notes = analyse(f"data/{who}.wav")["notes"]
    results[who] = notes
    json.dump(notes, open(f"data/{who}_notes.json", "w"), indent=1)
    print(f"\n  {who.upper()} - {len(notes)} notes found")
    print("   #  note   start    end      Hz     cents")
    for i, n in enumerate(notes, 1):
        print(f"  {i:2d}  {n['note']:5s} {n['start']:5.2f}s {n['end']:5.2f}s "
              f"{n['hz']:7.1f}  {n['cents']:+6.1f}")

print("\nStep 3/3  Checking against the mistakes we planted ...")
t, s = results["teacher"], results["student"]
checks = [
    ("Teacher: 7 notes found", len(t) == 7),
    ("Student: 7 notes found", len(s) == 7),
    ("Teacher notes are A4 A4 E5 E5 F#5 F#5 E5",
     [n["note"] for n in t] == ["A4", "A4", "E5", "E5", "F#5", "F#5", "E5"]),
    ("Teacher in tune (all within 5 cents)", all(abs(n["cents"]) <= 5 for n in t)),
    ("Student note 4 is E5 about 35 cents flat (within 5)",
     len(s) > 3 and s[3]["note"] == "E5" and abs(s[3]["cents"] + 35) <= 5),
    ("Student note 5 is the wrong note G5", len(s) > 4 and s[4]["note"] == "G5"),
    ("Pause before student note 6 (gap over 0.25 s)",
     len(s) > 5 and s[5]["start"] - s[4]["end"] > 0.25),
]
passed = 0
for name, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    passed += ok
print(f"\n  {passed}/{len(checks)} checks passed")

print("\nDrawing the chart ...")
subprocess.run([sys.executable, "-m", "demos.plot_pitch"], check=True)
print("\nDone. Open the output folder to see the chart, and the data folder "
      "to listen to teacher.wav and student.wav.")
