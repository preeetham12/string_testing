"""
Run all of Part 4 and check the results.

Usage (from inside the stringcoach folder):
    python -m demos.part4_scoring
"""
import os
import re
import warnings

import numpy as np
import soundfile as sf
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

warnings.filterwarnings("ignore")
from engine.synth import render, TEACHER, SR
from engine.pipeline import prepare
from engine.compare import compare
from engine.score import score, teacher_section, LEVELS
from engine.feedback import write_feedback

OUT = "data/part4"
os.makedirs(OUT, exist_ok=True)
os.makedirs("output", exist_ok=True)
LESSON = TEACHER * 4  # 28 notes = 4 phrases
LEAD = 0.3            # silence before the first note in every synth recording


def true_onset(notes, k):
    """Where note k really starts in a synth recording (for checking timestamps)."""
    return LEAD + sum(n.get("gap_before", 0) + n["dur"] for n in notes[:k]) + notes[k].get("gap_before", 0)


def variant(changes, base=None):
    S = [dict(n) for n in (base or LESSON)]
    for k, ch in changes.items():
        S[k].update(ch)
    return S


_cache, _seed = {}, [100]


def run(name, notes, teacher=None, level="intermediate", sections=None):
    teacher = teacher or T
    path = f"{OUT}/{name}.wav"
    _seed[0] += 1
    sf.write(path, render(notes, noise_db=-35, seed=_seed[0]), SR, subtype="FLOAT")
    c = compare(teacher, prepare(path))
    r = score(c, teacher["notes"], level=level, sections=sections)
    return c, r, write_feedback(r, c)


print("Step 1/4  Preparing the teacher's 28-note lesson (4 phrases) ...")
sf.write(f"{OUT}/teacher.wav", render(LESSON, noise_db=-40), SR, subtype="FLOAT")
T = prepare(f"{OUT}/teacher.wav")
from engine.score import find_sections
SECTIONS = find_sections(T["notes"])
print(f"  {len(T['notes'])} notes, sections found: "
      + ", ".join(f"S{s['id']}=notes {s['first'] + 1}-{s['last'] + 1}" for s in SECTIONS))

checks = []


def check(label, ok, detail=""):
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


print("\nStep 2/4  Scoring tests ...")
c, r, fb = run("perfect", LESSON)
check("perfect performance scores 100 and passes every section",
      r["overall"] >= 99 and r["all_passed"], f"{r['overall']}")
check("perfect performance: review lists no mistakes", "Practise this section" not in fb)

c, r, fb = run("perfect_slow", [{**n, "dur": n["dur"] * 1.2} for n in LESSON])
check("20% slower is not penalised", r["overall"] >= 98, f"{r['overall']}")
check("...but the review mentions the slower tempo", "slower than your teacher" in fb)

scores = []
for k, flats in enumerate([[3], [3, 10], [3, 10, 24]]):
    _, rr, _ = run(f"flats_{len(flats)}", variant({i: {"cents": -30} for i in flats}))
    scores.append(rr["overall"])
check("more mistakes -> strictly lower score (1, 2, 3 flat notes)",
      scores[0] > scores[1] > scores[2], f"{scores}")

LOCAL = variant({15: {"cents": -32}, 18: {"midi": LESSON[18]["midi"] + 1},
                 19: {"gap_before": 0.45}})
c_loc, r_loc, fb_loc = run("mistakes_in_section_3", LOCAL)
secs = {s["id"]: s for s in r_loc["sections"]}
check("mistakes only in section 3 -> only section 3 fails",
      not secs[3]["passed"] and all(secs[i]["passed"] for i in (1, 2, 4)),
      " ".join(f"S{i}={secs[i]['score']}" for i in secs))
check("review names the wrong note", "played G5 instead of F#5" in fb_loc)
check("review names the flat note and the hesitation",
      "cents flat" in fb_loc and "hesitated before the F#5" in fb_loc)

# timestamps in the review must point to the right moment in the student's recording
def stamp(keyword):
    """Time (seconds) written on the review line containing `keyword`."""
    line = next(l for l in fb_loc.splitlines() if keyword in l)
    m, s = re.search(r"at (\d+):(\d+\.\d)", line).groups()
    return int(m) * 60 + float(s)


errs = [abs(stamp("instead of") - true_onset(LOCAL, 18)),
        abs(stamp("cents flat") - true_onset(LOCAL, 15)),
        abs(stamp("hesitated") - true_onset(LOCAL, 19))]
check("review timestamps point to the right moment (within 0.15 s)",
      max(errs) <= 0.15, "errors " + ", ".join(f"{e:.2f}s" for e in errs))

moderate = variant({i: {"cents": -25} for i in (2, 9, 16, 23)})
lv = [run(f"level_{lvl}", moderate, level=lvl)[1]["overall"] for lvl in LEVELS]
check("same playing scores higher for beginners than advanced",
      lv[0] > lv[1] > lv[2], f"beginner {lv[0]}, intermediate {lv[1]}, advanced {lv[2]}")

_, r, fb = run("octave", [{**n, "midi": n["midi"] + 12} for n in LESSON])
check("whole piece an octave too high scores near zero", r["overall"] <= 10, f"{r['overall']}")
check("...and the review says 'octave', not just 'wrong note'", "octave too high" in fb)

rng = np.random.default_rng(5)
human = [{**n, "dur": n["dur"] + rng.uniform(-0.04, 0.04), "cents": rng.uniform(-6, 6)}
         for n in LESSON]
_, r, _ = run("human_variation", human)
check("natural small variations still pass every section",
      r["overall"] >= 90 and r["all_passed"], f"{r['overall']}")

_, r, fb = run("detuned", [{**n, "cents": -25} for n in LESSON])
check("detuned instrument isn't punished", r["overall"] >= 95, f"{r['overall']}")
check("...but the review says to tune", "tune it before the next session" in fb)

_, r, fb = run("false_start", LESSON[:3] + [{**LESSON[0], "gap_before": 0.8}] + LESSON[1:])
check("false start then a perfect take scores high", r["overall"] >= 95, f"{r['overall']}")
check("...and the review explains the restart", "restarted after 3 notes" in fb)

print("\nStep 3/4  Practice loop: re-recording only section 3 ...")
sec3 = SECTIONS[2]
t3 = teacher_section(T, sec3)
part = LESSON[sec3["first"]:sec3["last"] + 1]
_, r_bad, fb_bad = run("practice_s3_still_flat", variant({0: {"cents": -30}}, base=part),
                      teacher=t3, sections=[{**sec3, "first": 0, "last": len(part) - 1}])
check("section-only take with a flat note still fails", not r_bad["all_passed"],
      f"{r_bad['overall']}")
_, r_good, fb_good = run("practice_s3_fixed", part, teacher=t3,
                         sections=[{**sec3, "first": 0, "last": len(part) - 1}])
check("section-only take played well now passes", r_good["all_passed"], f"{r_good['overall']}")
print("\n  Review after the fixed take:\n    " + fb_good.replace("\n", "\n    "))

print(f"\n  {sum(checks)}/{len(checks)} checks passed")

print("\nStep 4/4  Drawing the student's report card ...")
r, c, fb = r_loc, c_loc, fb_loc
fig = plt.figure(figsize=(12, 9))
gs = fig.add_gridspec(3, 2, height_ratios=[1.1, 0.8, 2.2], width_ratios=[1, 2.2])
ax0 = fig.add_subplot(gs[0, 0]); ax0.axis("off")
# the piece is only "passed" when EVERY section passes, so colour by that
col = "#55A868" if r["all_passed"] else "#E8B04B"
todo = sum(not s["passed"] for s in r["sections"])
ax0.text(0.5, 0.58, f"{r['overall']}", fontsize=58, fontweight="bold", ha="center", color=col)
ax0.text(0.5, 0.30, "out of 100", fontsize=12, ha="center", color="#555")
ax0.text(0.5, 0.15, "all sections passed" if not todo else
         f"{todo} section{'s' if todo > 1 else ''} to practise", fontsize=12,
         ha="center", color=col, fontweight="bold")
ax0.text(0.5, 0.02, f"level: {r['level']}  |  pass mark {r['pass_mark']}", fontsize=9,
         ha="center", color="#777")

ax1 = fig.add_subplot(gs[0, 1])
names = ["notes", "intonation", "rhythm", "dynamics"]
vals = [r["components"].get(k, 0) for k in names]
bars = ax1.barh(["right notes", "in tune", "rhythm", "loud / soft"], vals,
                color=["#55A868" if k in r["strengths"] else "#E8B04B" for k in names])
for b, v in zip(bars, vals):
    ax1.text(v + 1, b.get_y() + b.get_height() / 2, f"{v}%", va="center", fontsize=9)
ax1.set_xlim(0, 112); ax1.invert_yaxis()
ax1.set_title("How each area went (green = no problems, amber = see review)",
              fontweight="bold", fontsize=11)
ax1.spines[["top", "right"]].set_visible(False)

ax2 = fig.add_subplot(gs[1, :])
for s in r["sections"]:
    w = s["last"] - s["first"] + 1
    colr = "#55A868" if s["passed"] else "#C44E52"
    ax2.add_patch(Rectangle((s["first"], 0), w - 0.15, 1, color=colr))
    ax2.text(s["first"] + w / 2, 0.5, f"Section {s['id']}\n{s['score']}/100"
             + ("" if s["passed"] else "  - practise"), ha="center", va="center",
             color="white", fontweight="bold", fontsize=11)
ax2.set_xlim(0, len(T["notes"])); ax2.set_ylim(0, 1); ax2.axis("off")
ax2.set_title("Sections (green passed, red practise)", fontweight="bold", fontsize=11)

ax3 = fig.add_subplot(gs[2, :]); ax3.axis("off")
ax3.text(0, 1, "Your review\n\n" + fb, va="top", fontsize=10.5, family="DejaVu Sans",
         linespacing=1.5)
fig.suptitle("Student report: Twinkle (4 phrases)", fontweight="bold", fontsize=14)
plt.tight_layout()
plt.savefig("output/part4_report_card.png", dpi=120, bbox_inches="tight")
print("Report card saved to output/part4_report_card.png")
