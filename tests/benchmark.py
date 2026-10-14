"""
Accuracy + latency benchmark.

Many student takes with KNOWN planted mistakes, in varied conditions, scored
note by note against what the engine reports. Measures how often each kind of
mistake is found, how many false alarms there are, and how often the whole
review is exactly right.

Usage:  python -m tests.benchmark            (about 2 minutes)
        python -m tests.benchmark --quick    (a smaller run)
"""
import argparse
import os
import time
import warnings
from collections import Counter, defaultdict

import numpy as np
import librosa
import soundfile as sf

warnings.filterwarnings("ignore")
from engine.synth import SR, TEACHER, render
from engine.pipeline import prepare
from engine.compare import compare
from engine.score import score
from tests.test_web import realistic
from tests.test_unseen import add_noise, coloured_noise

OUT = "data/benchmark"
LESSON_NOW = [None]  # the lesson being judged (for the repeated-note rule)

# Two lessons so nothing is tuned to one tune: Twinkle (repeated notes) and a
# melody with short and long notes and a rest between phrases.
MELODY = [(67, .5), (71, .25), (74, .25), (79, .75), (78, .25), (76, .5), (74, .5), (72, .25),
          (71, .25), (69, .5), (67, .75), (74, .5, .5), (76, .25), (78, .25), (79, .5), (74, .5),
          (71, .25), (72, .25), (74, .5), (69, .5), (71, .25), (69, .25), (67, 1.0)]
LESSONS = {
    "twinkle": TEACHER * 4,
    "melody": [{"midi": m[0], "dur": m[1], **({"gap_before": m[2]} if len(m) > 2 else {})} for m in MELODY],
}
# home room = mic ~1 m away (typical); far mic = phone across the room (hard)
CONDITIONS = ["clean", "home room", "far mic", "fan", "phone"]
KINDS = ["wrong", "flat", "sharp", "missed", "extra", "late", "pause"]


def make_take(lesson, rng):
    """A student take with 0-4 planted mistakes and natural human variation.
    Returns (notes, truth): truth maps teacher note index -> mistake kind,
    plus the number of extra notes."""
    n = len(lesson)
    k = int(rng.integers(0, 5))
    spots = []
    for idx in (rng.permutation(np.arange(2, n - 1)) if k else []):
        if all(abs(idx - s) >= 3 for s in spots):
            spots.append(int(idx))
        if len(spots) == k:
            break
    kinds = {s: KINDS[int(rng.integers(0, len(KINDS)))] for s in spots}
    tempo = float(rng.uniform(0.85, 1.2))
    notes, truth, extras = [], {}, 0
    for i, base in enumerate(lesson):
        nt = {**base, "dur": base["dur"] * tempo + rng.uniform(-0.03, 0.03),
              "cents": float(rng.uniform(-6, 6)), "vib": float(rng.uniform(10, 25))}
        if "gap_before" in base:
            nt["gap_before"] = base["gap_before"] * tempo
        kind = kinds.get(i)
        if kind == "missed":
            truth[i] = "missed"
            continue
        if kind == "wrong":
            nt["midi"] += int(rng.choice([-2, -1, 1, 2]))
        if kind == "flat":
            nt["cents"] = float(rng.uniform(-45, -30))
        if kind == "sharp":
            nt["cents"] = float(rng.uniform(30, 45))
        if kind == "late":            # hesitation: a short stall
            nt["gap_before"] = nt.get("gap_before", 0) + float(rng.uniform(0.5, 0.8))
        if kind == "pause":           # a long stop (page turn, thinking)
            nt["gap_before"] = nt.get("gap_before", 0) + float(rng.uniform(1.5, 3.0))
        if kind == "extra":           # an inserted note before this one
            others = {lesson[i - 1]["midi"], base["midi"]}
            em = int(rng.choice([m for m in (64, 66, 71, 73, 81) if m not in others]))
            notes.append({"midi": em, "dur": 0.35 * tempo})
            extras += 1
        else:
            truth.setdefault(i, kind) if kind else None
        notes.append(nt)
    return notes, truth, extras


def apply_condition(y, cond, rng):
    if cond == "home room":
        return realistic(y, drr_db=6, rt60=0.6, bow_snr_db=20, seed=int(rng.integers(1e6)))
    if cond == "far mic":
        return realistic(y, drr_db=0, rt60=0.8, bow_snr_db=15, seed=int(rng.integers(1e6)))
    if cond == "fan":
        y = add_noise(y, coloured_noise(len(y), "pink"), -22)
        return y / np.max(np.abs(y)) * 0.8
    return y


def write(path, y, cond):
    if cond == "phone":
        a = librosa.resample(y, orig_sr=SR, target_sr=48000)
        sf.write(path, np.stack([a, a * 0.9], axis=1), 48000, subtype="PCM_16")
    else:
        sf.write(path, y, SR, subtype="FLOAT")


def judge(rows, truth, extras):
    """Compare the engine's per-note verdicts with the planted truth."""
    by_t = {r["t_index"]: r for r in rows if r.get("t_index") is not None}
    # Missing one of two identical neighbouring notes (A4, A4) is ambiguous:
    # either can fairly be called "missed", so accept the neighbour's label.
    for i, kind in list(truth.items()):
        if kind == "missed" and by_t.get(i, {}).get("status") != "missed":
            for j in (i - 1, i + 1):
                if (j not in truth and by_t.get(j, {}).get("status") == "missed"
                        and LESSON_NOW[0][j]["midi"] == LESSON_NOW[0][i]["midi"]):
                    by_t[i], by_t[j] = by_t[j], by_t[i]
    found = {}
    for i, kind in truth.items():
        r = by_t.get(i, {"status": "?", "flags": []})
        found[kind] = found.get(kind, []) + [{
            "wrong": r["status"] == "wrong note",
            "flat": r["status"] == "correct" and "flat" in r["flags"],
            "sharp": r["status"] == "correct" and "sharp" in r["flags"],
            "missed": r["status"] == "missed",
            "late": "late" in r["flags"],
            # a long pause should be NOTED, not counted as a rhythm mistake
            "pause": r["status"] == "correct" and "late" not in r["flags"] and r.get("pause_s", 0) > 0,
        }[kind]]
    got_extra = sum(r["status"] == "extra" for r in rows)
    found["extra"] = [True] * min(extras, got_extra) + [False] * max(0, extras - got_extra)
    false_alarms = []
    for i, r in by_t.items():
        if i in truth:
            continue
        if r["status"] != "correct" or r["flags"]:
            false_alarms.append(f"note {i + 1}: {r['status']} {','.join(r['flags'])}".strip())
    if got_extra > extras:
        false_alarms.append(f"{got_extra - extras} extra note(s) reported")
    return found, false_alarms


SUGGESTIONS = Counter()


def teacher_checked(t, lesson):
    """What the teacher does in the studio: the app suggests notes that look
    like two notes merged by room echo; the teacher listens and accepts only
    the right ones (a teacher hears the difference at once). Also counts how
    good the suggestions are."""
    from engine.pipeline import split_note, suggest_splits
    # where each lesson note really starts in the teacher's recording
    starts, tm = [], 0.3
    for n in lesson:
        tm += n.get("gap_before", 0)
        starts.append(tm)
        tm += n["dur"]
    true_cuts = [starts[k] for k in range(1, len(lesson)) if lesson[k]["midi"] == lesson[k - 1]["midi"]]
    notes = t["notes"]
    merged = sum(1 for c in true_cuts
                 if any(n["start"] + t["trim_offset"] + 0.1 < c < n["end"] + t["trim_offset"] - 0.1 for n in notes))
    sug = suggest_splits(t)
    right = [g for g in sug if any(abs(g["at"] + t["trim_offset"] - c) < 0.12 for c in true_cuts)]
    SUGGESTIONS["suggested"] += len(sug)
    SUGGESTIONS["right"] += len(right)
    SUGGESTIONS["merged"] += merged
    for g in sorted(right, key=lambda x: -x["index"]):
        notes = split_note(notes, g["index"], g["at"])
    return {**t, "notes": notes}


def run(per_cell, seed=7, verbose=False, checked=False):
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(seed)
    teachers = {}
    for name, lesson in LESSONS.items():
        for cond in ("clean", "home room"):  # teachers record in real rooms too
            p = f"{OUT}/teacher_{name}_{cond}.wav"
            y = apply_condition(render(lesson, noise_db=-40, seed=3), cond, rng)
            sf.write(p, y, SR, subtype="FLOAT")
            teachers[(name, cond)] = prepare(p)
            if checked:
                teachers[(name, cond)] = teacher_checked(teachers[(name, cond)], lesson)
    hits, alarms, exact, total = defaultdict(list), Counter(), Counter(), Counter()
    latency, examples = [], []
    for name, lesson in LESSONS.items():
        for cond in CONDITIONS:
            t = teachers[(name, "home room" if cond in ("home room", "far mic") else "clean")]
            for k in range(per_cell):
                notes, truth, extras = make_take(lesson, rng)
                y = apply_condition(render(notes, noise_db=-38, seed=int(rng.integers(1e6))), cond, rng)
                p = f"{OUT}/take_{name}_{cond}_{k}.wav"
                write(p, y, cond)
                t0 = time.time()
                s = prepare(p, student=True)
                c = compare(t, s)
                score(c, t["notes"])
                secs = time.time() - t0
                latency.append(secs / (len(y) / SR / 60))
                LESSON_NOW[0] = lesson
                found, fa = judge(c["notes"], truth, extras)
                for kind, oks in found.items():
                    hits[kind] += oks
                    hits[(kind, cond)] += oks
                alarms[cond] += len(fa)
                ok = not fa and all(all(v) for v in found.values())
                exact[cond] += ok
                total[cond] += 1
                if not ok and len(examples) < 12:
                    miss = [f"{kd}@{i + 1}" for i, kd in truth.items() if kd in found and not found[kd][0]]
                    examples.append(f"{name}/{cond}/{k}: planted {dict((i + 1, v) for i, v in truth.items())}"
                                    f"{' +%d extra' % extras if extras else ''} | false alarms {fa}")
    return hits, alarms, exact, total, latency, examples


def report(hits, alarms, exact, total, latency, examples):
    print("Mistakes found (recall):")
    for kind in KINDS:
        v = hits.get(kind, [])
        if v:
            print(f"  {kind:7s} {sum(v):3d}/{len(v):<3d} {100 * sum(v) / len(v):5.1f}%")
    print("\nBy condition:      " + "".join(f"{c:>11s}" for c in CONDITIONS))
    for kind in KINDS:
        cells = []
        for c in CONDITIONS:
            v = hits.get((kind, c), [])
            cells.append(f"{sum(v)}/{len(v)}".rjust(11) if v else "-".rjust(11))
        print(f"  {kind:16s}" + "".join(cells))
    print("\nReview exactly right (every planted mistake found, no false alarms):")
    for cond in CONDITIONS:
        print(f"  {cond:9s} {exact[cond]:3d}/{total[cond]:<3d} {100 * exact[cond] / total[cond]:5.1f}%"
              f"   false alarms: {alarms[cond] / total[cond]:.2f} per take")
    all_ok, all_n = sum(exact.values()), sum(total.values())
    print(f"  {'ALL':9s} {all_ok:3d}/{all_n:<3d} {100 * all_ok / all_n:5.1f}%")
    print(f"\nLatency: {np.median(latency):.1f} s per minute of music (median), "
          f"{np.percentile(latency, 95):.1f} s (95th percentile)")
    if SUGGESTIONS["suggested"] or SUGGESTIONS["merged"]:
        print(f"\nStudio split suggestions: {SUGGESTIONS['right']} of {SUGGESTIONS['suggested']} right; "
              f"found {SUGGESTIONS['right']} of {SUGGESTIONS['merged']} notes really merged by echo")
    if examples:
        print("\nExamples of wrong reviews:")
        for e in examples:
            print("  " + e)
    return 100 * all_ok / all_n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--checked", action="store_true",
                    help="the teacher fixed merged notes in the studio before publishing")
    a = ap.parse_args()
    report(*run(per_cell=3 if a.quick else 6, checked=a.checked))
