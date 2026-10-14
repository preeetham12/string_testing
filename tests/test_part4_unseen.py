"""
Part 4 - unseen scoring and feedback tests.

Not used while building Part 4. Checks consistency (same playing -> same score),
speed (architecture target: result in under 10 s per 1-minute attempt), unusual
piece structures, and whether the written review makes sense to a student.

Usage: python -m tests.test_part4_unseen
"""
import os
import time
import warnings

import soundfile as sf

warnings.filterwarnings("ignore")
from engine.synth import render, TEACHER, SR
from engine.pipeline import prepare
from engine.compare import compare
from engine.score import score, find_sections
from engine.feedback import write_feedback

OUT = "data/part4_unseen"
os.makedirs(OUT, exist_ok=True)
LESSON = TEACHER * 4
results, _seed, _teachers = [], [300], {}


def check(label, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))


def teacher_for(notes, inst="violin"):
    key = (inst, tuple((n["midi"], n["dur"], n.get("gap_before", 0)) for n in notes))
    if key not in _teachers:
        p = f"{OUT}/teacher_{len(_teachers)}.wav"
        sf.write(p, render(notes, noise_db=-40), SR, subtype="FLOAT")
        _teachers[key] = prepare(p, inst)
    return _teachers[key]


def attempt(name, notes, lesson=LESSON, inst="violin", seed=None, **kw):
    t = teacher_for(lesson, inst)
    _seed[0] += 1
    p = f"{OUT}/{name}.wav"
    sf.write(p, render(notes, noise_db=-35, seed=seed or _seed[0]), SR, subtype="FLOAT")
    t0 = time.time()
    c = compare(t, prepare(p, inst))
    r = score(c, t["notes"], **kw)
    fb = write_feedback(r, c)
    return r, fb, time.time() - t0


def variant(changes, base=None):
    S = [dict(n) for n in (base or LESSON)]
    for k, ch in changes.items():
        S[k].update(ch)
    return S


print("Consistency and fairness")
same = variant({5: {"cents": -28}, 17: {"midi": LESSON[17]["midi"] + 2}})
runs = [attempt(f"repeat_{k}", same, seed=500 + k)[0]["overall"] for k in range(3)]
check("same playing, 3 recordings with different room noise -> scores within 2 points",
      max(runs) - min(runs) <= 2, f"{runs}")

r80 = attempt("pass80", variant({3: {"cents": -30}}), pass_mark=80)[0]
r90 = attempt("pass90", variant({3: {"cents": -30}}), pass_mark=90)[0]
check("teacher's pass mark is respected (one flat note: passes at 80, not at 90)",
      r80["all_passed"] and not r90["all_passed"], f"{r80['overall']} vs {r90['overall']}")

flat30 = variant({3: {"cents": -30}})
rb = attempt("beginner_flat", flat30, level="beginner")[0]
ra = attempt("advanced_flat", flat30, level="advanced")[0]
check("one 30-cent flat note: passes for a beginner, practise for advanced",
      rb["all_passed"] and not ra["all_passed"])

print("\nUnusual piece structures")
SCALE = [{"midi": m, "dur": 0.4} for m in [69, 71, 73, 74, 76, 78, 80, 81, 80, 78, 76, 74]]
secs = find_sections(teacher_for(SCALE)["notes"])
check("a piece with no rests or long notes stays one section", len(secs) == 1, f"{len(secs)}")

RESTS = (TEACHER[:4] + [{**TEACHER[4], "gap_before": 0.6}] + TEACHER[5:6]
         + [{**TEACHER[6], "gap_before": 0.6}])
secs = find_sections(teacher_for(RESTS)["notes"])
check("phrases separated by rests are split at the rests",
      [(s["first"], s["last"]) for s in secs] == [(0, 3), (4, 5), (6, 6)],
      f"{[(s['first'] + 1, s['last'] + 1) for s in secs]}")

custom = [{"id": 1, "first": 0, "last": 13, "t_start": 0, "t_end": 0},
          {"id": 2, "first": 14, "last": 27, "t_start": 0, "t_end": 0}]
rc = attempt("custom_sections", variant({20: {"midi": LESSON[20]["midi"] + 1}}), sections=custom)[0]
check("teacher-defined sections are used (2 sections, mistake lands in section 2)",
      len(rc["sections"]) == 2 and rc["sections"][0]["passed"] and not rc["sections"][1]["passed"])

print("\nIs the written review useful to a student?")
r, fb, _ = attempt("missed_note", [n for k, n in enumerate(LESSON) if k != 10])
line = next((l for l in fb.splitlines() if "skipped" in l), "")
check("a skipped note says WHERE it was skipped (a time or neighbouring note)",
      " at " in line or "after" in line, line.strip())

r, fb, _ = attempt("stopped_halfway", LESSON[:14])
check("stopped halfway: sections 3-4 fail with 0", [s["score"] for s in r["sections"]][2:] == [0, 0],
      f"{[s['score'] for s in r['sections']]}")
check("stopped halfway: review says sections weren't played (not a list of skipped notes)",
      "not played" in fb and fb.count("skipped") <= 1,
      f"{fb.count('skipped')} 'skipped' lines")

bad = variant({k: {"midi": LESSON[k]["midi"] + (1 if k % 3 == 0 else 0),
                   "cents": -25 if k % 4 == 1 else 0} for k in range(28)})
r, fb, _ = attempt("very_rough_take", bad)
lines = [l for l in fb.splitlines() if l.strip()]
check("a very rough take gets a short, focused review (at most 14 lines)",
      len(lines) <= 14, f"{len(lines)} lines, score {r['overall']}")

r, fb, _ = attempt("rushed_and_loud", variant({9: {"dur": 0.3}, 22: {"gain": 2.5}}))
check("rushed and too-loud notes are explained in words",
      "came in early" in fb and "too loud" in fb)

r, fb, _ = attempt("cello_section_mistake",
                   [{**n, "midi": n["midi"] - 24} for n in variant({16: {"midi": LESSON[16]["midi"] + 2}})],
                   lesson=[{**n, "midi": n["midi"] - 24} for n in LESSON], inst="cello")
check("cello: mistake found in the right section",
      [s["passed"] for s in r["sections"]] == [True, True, False, True],
      f"{[s['score'] for s in r['sections']]}")

print("\nSpeed (target: under 10 s per 1-minute attempt)")
LONG = TEACHER * 14  # ~63 s of music, 98 notes
r, fb, secs_taken = attempt("one_minute_piece", LONG, lesson=LONG)
check("1-minute attempt analysed, compared and scored in under 10 s",
      secs_taken < 10, f"{secs_taken:.1f} s for {len(r['sections'])} sections, score {r['overall']}")

print(f"\n  {sum(results)}/{len(results)} checks passed")
