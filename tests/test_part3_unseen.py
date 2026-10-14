"""
Part 3 - unseen comparison tests.

Students the comparison engine was NOT developed against: real-world habits
(false start, stopping halfway, speeding up), awkward recordings (phone format,
fan noise, long silence), other instruments, and a long piece.

Usage: python -m tests.test_part3_unseen
"""
import os
import time
import warnings
from collections import Counter

import numpy as np
import librosa
import soundfile as sf

warnings.filterwarnings("ignore")
from engine.synth import render, TEACHER, STUDENT, SR
from engine.pipeline import prepare
from engine.compare import compare
from tests.test_unseen import coloured_noise, add_noise

OUT = "data/part3_unseen"


def shift(notes, semis):
    return [{**n, "midi": n["midi"] + semis} for n in notes]


def accelerando(notes, start=1.08, end=0.92):
    """Gradually speeding up through the piece, as nervous students do."""
    f = np.linspace(start, end, len(notes))
    return [{**n, "dur": n["dur"] * k} for n, k in zip(notes, f)]


LONG_TEACHER = TEACHER * 4  # 28 notes
LONG_STUDENT = []
for k, n in enumerate(LONG_TEACHER):
    if k == 24:
        continue                                      # missed
    if k == 9:
        n = {**n, "cents": -30}                       # flat
    if k == 19:
        n = {**n, "midi": n["midi"] + 2}              # wrong note
    LONG_STUDENT.append(n)

# name: student notes, teacher notes, instrument, post-processing, expectations
CASES = {
    "false_start": dict(
        notes=TEACHER[:3] + [{**TEACHER[0], "gap_before": 0.8}] + TEACHER[1:],
        about="plays 3 notes, stops, restarts from the beginning",
        expect={"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 0},
                "false_start": ["A4", "A4", "E5"], "flags": []}),
    "repeated_opening": dict(
        notes=TEACHER + [{**TEACHER[0], "gap_before": 0.6}] + TEACHER[1:],
        teacher=TEACHER + [{**TEACHER[0], "gap_before": 0.6}] + TEACHER[1:],
        about="piece itself repeats its opening after a rest; played correctly",
        expect={"counts": {"correct": 14, "wrong note": 0, "missed": 0, "extra": 0},
                "false_start": [], "flags": []}),
    "stopped_halfway": dict(
        notes=TEACHER[:4], about="plays only the first 4 notes",
        expect={"counts": {"correct": 4, "wrong note": 0, "missed": 3, "extra": 0}}),
    "speeds_up": dict(
        notes=accelerando(TEACHER), about="gradually speeds up (+8% to -8%)",
        expect={"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 0},
                "flags": []}),
    "much_faster": dict(
        notes=[{**n, "dur": n["dur"] / 1.5} for n in STUDENT],
        about="main mistakes, played 1.5x faster than teacher",
        expect={"counts": {"correct": 6, "wrong note": 1, "missed": 0, "extra": 0},
                "flags": ["flat", "late"], "tempo": 0.73}),
    "sharp_note": dict(
        notes=[{**n, "cents": 28 if k == 5 else 0} for k, n in enumerate(TEACHER)],
        about="note 6 played 28 cents sharp",
        expect={"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 0},
                "flags": ["sharp"]}),
    "wide_vibrato": dict(
        notes=[{**n, "vib": 35} for n in TEACHER],
        about="every note in tune but with wide +/-35 cent vibrato",
        expect={"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 0},
                "flags": []}),
    "octave_too_high": dict(
        notes=shift(TEACHER, 12), about="whole piece one octave too high",
        expect={"counts": {"correct": 0, "wrong note": 7, "missed": 0, "extra": 0},
                "flags": ["octave too high"] * 7}),
    "wrong_then_missed": dict(
        notes=TEACHER[:4] + [{**TEACHER[4], "midi": 79}] + TEACHER[6:],
        about="wrong note immediately followed by a skipped note",
        expect={"counts": {"correct": 5, "wrong note": 1, "missed": 1, "extra": 0}}),
    "long_silence_first": dict(
        notes=[{**STUDENT[0], "gap_before": 2.5}] + STUDENT[1:],
        about="main mistakes, 2.5 s of silence before playing",
        expect={"counts": {"correct": 6, "wrong note": 1, "missed": 0, "extra": 0},
                "flags": ["flat", "late"]}),
    "phone_48k_stereo": dict(
        notes=STUDENT, fmt="phone", about="main mistakes, recorded as 48 kHz stereo",
        expect={"counts": {"correct": 6, "wrong note": 1, "missed": 0, "extra": 0},
                "flags": ["flat", "late"]}),
    "fan_noise": dict(
        notes=STUDENT, fmt="fan", about="main mistakes, with fan noise in the room",
        expect={"counts": {"correct": 6, "wrong note": 1, "missed": 0, "extra": 0},
                "flags": ["flat", "late"]}),
    "brighter_violin": dict(
        notes=TEACHER, fmt="bright", about="perfect, but a brighter-sounding violin",
        expect={"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 0},
                "flags": [], "max_tone_diff": 15.0}),  # same-violin notes: 5-12
    "cello": dict(
        notes=shift(STUDENT, -24), teacher=shift(TEACHER, -24), instrument="cello",
        about="cello (two octaves lower), main mistakes",
        expect={"counts": {"correct": 6, "wrong note": 1, "missed": 0, "extra": 0},
                "flags": ["flat", "late"]}),
    "long_piece_28_notes": dict(
        notes=LONG_STUDENT, teacher=LONG_TEACHER,
        about="28-note piece: one flat, one wrong, one missed",
        expect={"counts": {"correct": 26, "wrong note": 1, "missed": 1, "extra": 0},
                "flags": ["flat"]}),
}


def write(path, audio, fmt):
    if fmt == "phone":
        a = librosa.resample(audio, orig_sr=SR, target_sr=48000)
        sf.write(path, np.stack([a, a * 0.9], axis=1), 48000, subtype="PCM_16")
        return
    if fmt == "fan":
        audio = add_noise(audio, coloured_noise(len(audio), "pink"), -20)
    if fmt == "bright":
        audio = librosa.effects.preemphasis(audio, coef=0.9)
        audio = audio / np.max(np.abs(audio)) * 0.8
    sf.write(path, audio, SR, subtype="FLOAT")


def run():
    os.makedirs(OUT, exist_ok=True)
    teachers, ok_cases, total_checks, ok_checks = {}, 0, 0, 0
    for seed, (name, c) in enumerate(CASES.items(), start=50):
        inst = c.get("instrument", "violin")
        t_notes = c.get("teacher", TEACHER)
        key = (inst, tuple((n["midi"], n.get("gap_before", 0)) for n in t_notes))
        if key not in teachers:
            tp = f"{OUT}/teacher_{inst}_{len(teachers)}.wav"
            sf.write(tp, render(t_notes, noise_db=-40), SR, subtype="FLOAT")
            teachers[key] = prepare(tp, inst)
        path = f"{OUT}/{name}.wav"
        write(path, render(c["notes"], noise_db=-35, seed=seed), c.get("fmt"))

        t0 = time.time()
        r = compare(teachers[key], prepare(path, inst))
        secs = time.time() - t0

        exp, checks = c["expect"], []
        checks.append(("counts", r["counts"] == exp["counts"],
                       f"{r['counts']}"))
        flags = Counter(f for n in r["notes"] for f in n["flags"])
        if "flags" in exp:
            checks.append(("flags", flags == Counter(exp["flags"]),
                           f"{dict(flags) or 'none'}"))
        if "false_start" in exp:
            checks.append(("false start", r["false_start_notes"] == exp["false_start"],
                           f"{r['false_start_notes']}"))
        if "max_tone_diff" in exp:
            worst = max(n.get("tone_diff", 0) for n in r["notes"])
            checks.append(("tone (instrument colour ignored)", worst <= exp["max_tone_diff"],
                           f"worst tone_diff {worst}"))
        if "tempo" in exp:
            checks.append(("tempo", abs(r["tempo_ratio"] - exp["tempo"]) <= 0.05,
                           f"{r['tempo_ratio']}x"))
        passed = all(ok for _, ok, _ in checks)
        ok_cases += passed
        total_checks += len(checks)
        ok_checks += sum(ok for _, ok, _ in checks)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:20s} {secs:4.1f}s  {c['about']}")
        for label, ok, got in checks:
            if not ok:
                print(f"         {label} wrong: got {got}")
        if not passed:
            print("         notes: " + "  ".join(
                f"{n['teacher'] or '-'}>{n['student'] or '-'}:{n['status'][:5]}"
                + (f"[{','.join(n['flags'])}]" if n["flags"] else "") for n in r["notes"]))
    print(f"\n  {ok_cases}/{len(CASES)} unseen students judged correctly "
          f"({ok_checks}/{total_checks} checks)")


if __name__ == "__main__":
    run()
