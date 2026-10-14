"""
Part 3 test students. Each is compared with the same teacher recording, and
each lists what the comparison MUST find.
"""
import numpy as np

from engine.synth import TEACHER, STUDENT

D5 = 74
rng = np.random.default_rng(3)


def slower(notes, f):
    return [{**n, "dur": n["dur"] * f} for n in notes]


def human(notes):
    """Natural small variations a real player makes: +/-40 ms timing, +/-6 cents."""
    return [{**n, "dur": n["dur"] + rng.uniform(-0.04, 0.04),
             "cents": rng.uniform(-6, 6)} for n in notes]


CASES = {
    "A_main_student": {
        "notes": STUDENT,
        "about": "flat E5, wrong G5, hesitation, 10% slower",
        "expect": {"counts": {"correct": 6, "wrong note": 1, "missed": 0, "extra": 0},
                   "flags": {4: ["flat"], 6: ["late"]}, "tempo": 1.10},
    },
    "B_missed_note": {
        "notes": [n for k, n in enumerate(TEACHER) if k != 2],
        "about": "skipped the third note",
        "expect": {"counts": {"correct": 6, "wrong note": 0, "missed": 1, "extra": 0},
                   "tempo": 1.0},
    },
    "C_extra_note": {
        "notes": TEACHER[:4] + [{"midi": D5, "dur": 0.5}] + TEACHER[4:],
        "about": "added a D5 that isn't in the lesson",
        "expect": {"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 1},
                   "tempo": 1.0},
    },
    "D_detuned_instrument": {
        "notes": [{**n, "cents": -25 + (-30 if k == 3 else 0)} for k, n in enumerate(TEACHER)],
        "about": "whole violin 25 cents flat, plus note 4 another 30 cents flat",
        "expect": {"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 0},
                   "flags": {4: ["flat"]}, "tuning": -25, "tempo": 1.0},
    },
    "E_perfect_but_slow": {
        "notes": slower(TEACHER, 1.2),
        "about": "every note right, 20% slower",
        "expect": {"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 0},
                   "flags": {}, "tempo": 1.2},
    },
    "F_human_variation": {
        "notes": human(TEACHER),
        "about": "every note right, with natural small timing and pitch variation",
        "expect": {"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 0},
                   "flags": {}, "tempo": 1.0},
    },
    "G_one_soft_note": {
        "notes": [{**n, "gain": 0.3 if k == 2 else 1.0} for k, n in enumerate(TEACHER)],
        "about": "third note played much softer than the teacher",
        "expect": {"counts": {"correct": 7, "wrong note": 0, "missed": 0, "extra": 0},
                   "flags": {3: ["too soft"]}, "tempo": 1.0},
    },
}
