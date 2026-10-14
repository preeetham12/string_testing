"""
Part 4 - Scoring.

Turns the Part 3 comparison into scores out of 100:
  each section starts at 100; every mistake costs points (penalty model)
  graded errors (cents, ms, dB) -> 0..1 via a tolerance curve (depends on level)
  -> section score -> overall score (sections weighted by number of notes)

Component percentages (notes, intonation, rhythm, dynamics) are also reported
for the report card. Tone is measured and shown, but costs no points until it
is validated on real recordings (Part 3 testing showed room noise distorts it).
"""
import numpy as np

# Error at which a note still gets full marks, and error at which it gets zero.
LEVELS = {
    "beginner":     {"cents": (20, 50), "ms": (100, 400), "db": (4, 14)},
    "intermediate": {"cents": (12, 35), "ms": (80, 350), "db": (3, 12)},
    "advanced":     {"cents": (8, 25), "ms": (60, 300), "db": (3, 10)},
}
# Each section starts at 100 and every mistake costs points, the way a teacher
# marks. Graded errors (cents, ms, dB) cost up to the maximum, scaled by the
# tolerance curve. Tone costs nothing until validated on real recordings.
PENALTY = {"wrong note": 15, "missed": 15, "extra": 10,
           "intonation": 15, "rhythm": 10, "dynamics": 5, "tone": 0}
# A badly out-of-tune note costs as much as a wrong note: that's how it sounds.
# Pass mark 90: any single clear mistake means the section needs practice;
# small natural variations still pass. Teachers can change it per assignment.
PASS_MARK = 90


def curve(err, full, zero):
    """Full marks up to `full`, zero from `zero`, smooth S-shaped drop between.
    Smooth, so a tiny change in error never causes a big jump in score."""
    e = abs(err)
    if e <= full:
        return 1.0
    if e >= zero:
        return 0.0
    return 0.5 * (1 + np.cos(np.pi * (e - full) / (zero - full)))


def find_sections(notes, rest_s=0.25, long_factor=1.5):
    """Split the teacher's notes into phrases: a phrase ends at a rest, or at a
    long note (typical phrase ending). Teachers can adjust these in the studio."""
    durs = [n["end"] - n["start"] for n in notes]
    med = float(np.median(durs))
    sections, start = [], 0
    for k in range(len(notes) - 1):
        gap = notes[k + 1]["start"] - notes[k]["end"]
        # a gap left by a note the teacher deleted is not a musical rest
        is_rest = gap > rest_s and not notes[k + 1].get("skip_timing")
        if is_rest or durs[k] >= long_factor * med:
            sections.append((start, k + 1))
            start = k + 1
    sections.append((start, len(notes)))
    return [{"id": s + 1, "first": a, "last": b - 1,
             "t_start": notes[a]["start"], "t_end": notes[b - 1]["end"]}
            for s, (a, b) in enumerate(sections)]


def _section_of_rows(rows, sections):
    """Which section each comparison row belongs to (extras join the section of
    the teacher note just before them)."""
    owner, current = [], 0
    for r in rows:
        if r["t_index"] is not None:
            current = next(s["id"] for s in sections if s["first"] <= r["t_index"] <= s["last"])
        owner.append(current or 1)
    return owner


def _components(rows, tol):
    teacher_notes = [r for r in rows if r["t_index"] is not None]
    extras = sum(r["status"] == "extra" for r in rows)
    correct = sum(r["status"] == "correct" for r in rows)
    comp = {}
    if teacher_notes:
        comp["notes"] = correct / (len(teacher_notes) + extras)
    cents = [r["cents_error"] for r in rows if r.get("cents_error") is not None]
    if cents:
        comp["intonation"] = float(np.mean([curve(c, *tol["cents"]) for c in cents]))
    ms = [r["timing_ms"] for r in rows if r.get("timing_ms") is not None]
    if ms:
        comp["rhythm"] = float(np.mean([curve(m, *tol["ms"]) for m in ms]))
    db = [r["dynamics_db"] for r in rows if r.get("dynamics_db") is not None]
    if db:
        comp["dynamics"] = float(np.mean([curve(d, *tol["db"]) for d in db]))
    return comp


def _penalties(rows, tol):
    """Points lost per area in these rows."""
    p = {"notes": 0.0, "intonation": 0.0, "rhythm": 0.0, "dynamics": 0.0}
    for r in rows:
        if r["status"] in ("wrong note", "missed", "extra"):
            p["notes"] += PENALTY[r["status"]]
        if r.get("cents_error") is not None:
            p["intonation"] += PENALTY["intonation"] * (1 - curve(r["cents_error"], *tol["cents"]))
        if r.get("timing_ms") is not None:
            p["rhythm"] += PENALTY["rhythm"] * (1 - curve(r["timing_ms"], *tol["ms"]))
        if r.get("dynamics_db") is not None:
            p["dynamics"] += PENALTY["dynamics"] * (1 - curve(r["dynamics_db"], *tol["db"]))
    return p


def score(comparison, teacher_notes, level="intermediate", pass_mark=PASS_MARK,
          sections=None):
    tol = LEVELS[level]
    sections = sections or find_sections(teacher_notes)
    rows = comparison["notes"]
    owner = _section_of_rows(rows, sections)

    out_sections = []
    for sec in sections:
        srows = [r for r, o in zip(rows, owner) if o == sec["id"]]
        comp = _components(srows, tol)
        pen = _penalties(srows, tol)
        sc = max(0, round(100 - sum(pen.values())))
        played = [r["s_start"] for r in srows if r["s_start"] is not None]
        orig = [r["s_orig"] for r in srows if r.get("s_orig") is not None]
        out_sections.append({
            **sec, "score": sc, "passed": sc >= pass_mark,
            "components": {k: round(100 * v) for k, v in comp.items()},
            "points_lost": {k: round(v, 1) for k, v in pen.items() if v >= 0.5},
            "s_start": min(played) if played else None,
            "s_orig_start": min(orig) if orig else None,  # in the ORIGINAL recording
            "rows": srows,
        })

    sizes = [s["last"] - s["first"] + 1 for s in out_sections]
    overall = round(float(np.average([s["score"] for s in out_sections], weights=sizes)))
    comp_all = _components(rows, tol)
    pen_all = _penalties(rows, tol)
    return {
        "overall": overall, "level": level, "pass_mark": pass_mark,
        "components": {k: round(100 * v) for k, v in comp_all.items()},
        # an area is a real strength only if it cost (almost) no points anywhere
        "strengths": [k for k, v in pen_all.items() if v < 2.0],
        "points_lost": {k: round(v, 1) for k, v in pen_all.items() if v >= 0.5},
        "sections": out_sections,
        "all_passed": all(s["passed"] for s in out_sections),
    }


def teacher_section(teacher, sec, pad=0.05):
    """Just one section of the teacher's prepared recording, re-timed to start
    at 0 - used to score a student practising only that section."""
    from engine.pitch import SR
    a = max(sec["t_start"] - pad, 0.0)
    b = sec["t_end"] + pad
    notes = [{**n, "start": round(n["start"] - a, 3), "end": round(n["end"] - a, 3)}
             for n in teacher["notes"][sec["first"]:sec["last"] + 1]]
    return {**teacher, "y": teacher["y"][int(a * SR):int(b * SR)], "notes": notes}
