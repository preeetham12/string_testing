"""
Part 4 - Written feedback.

Rule-based: every sentence comes from a measured finding, so the review can
never claim something the analysis didn't find. (A language model could later
re-word these sentences more naturally, but would not add new claims.)
"""

PRIORITY = {"wrong note": 0, "missed": 1, "extra": 2, "flat": 3, "sharp": 3,
            "late": 4, "rushed": 4, "too soft": 5, "too loud": 5}
NAMES = {"notes": "right notes", "intonation": "intonation (in tune)",
         "rhythm": "steady rhythm", "dynamics": "loud and soft"}


def _t(sec):
    """Seconds -> m:ss.s, so the student can jump straight to the moment."""
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def _when(r, offset):
    """Time of a note in the student's ORIGINAL recording (pauses that were cut
    out and silence trimmed from the start are added back)."""
    if r.get("s_orig") is not None:
        return r["s_orig"]
    return r["s_start"] + offset if r.get("s_start") is not None else None


def _issues(rows, offset):
    found = []
    for k, r in enumerate(rows):
        at = f" at {_t(_when(r, offset))}" if r["s_start"] is not None else ""
        if r["status"] == "wrong note":
            if any(f.startswith("octave") for f in r["flags"]):
                txt = (f"{r['student']}{at} is the right note but an "
                       f"{'octave too high' if r['semitones_off'] > 0 else 'octave too low'} "
                       f"(should be {r['teacher']}).")
            else:
                txt = f"You played {r['student']} instead of {r['teacher']}{at}."
            found.append((PRIORITY["wrong note"], txt))
        elif r["status"] == "missed":
            before = next((p for p in reversed(rows[:k]) if p["s_start"] is not None), None)
            if before is not None:
                where = f" after the {before['student']} at {_t(_when(before, offset))}"
            else:
                where = " at the start of the section"
            found.append((PRIORITY["missed"], f"The {r['teacher']}{where} was skipped."))
        elif r["status"] == "extra":
            found.append((PRIORITY["extra"], f"An extra {r['student']} was played{at}."))
        for f in r["flags"]:
            if f in ("flat", "sharp"):
                c = abs(r["cents_error"])
                fix = "a little higher" if f == "flat" else "a little lower"
                found.append((PRIORITY[f], f"Your {r['teacher']}{at} was {c:.0f} cents {f}; "
                                            f"aim {fix}."))
            elif f == "late":
                found.append((PRIORITY[f], f"You hesitated before the {r['teacher']}{at} "
                                            f"(about {r['timing_ms'] / 1000:.1f} s late)."))
            elif f == "rushed":
                found.append((PRIORITY[f], f"You came in early on the {r['teacher']}{at}; "
                                            f"give the note before it its full length."))
            elif f in ("too soft", "too loud"):
                found.append((PRIORITY[f], f"The {r['teacher']}{at} was {f} compared with "
                                            f"your teacher."))
    return [txt for _, txt in sorted(found, key=lambda x: x[0])]


# One practical tip per section, for its most important kind of problem.
TIPS = {
    "wrong note": "Listen to your teacher's version of this section at half speed, then play it slowly note by note.",
    "missed": "Play this section slowly and say each note name as you play it, so none gets skipped.",
    "extra": "Play this section slowly and stop on each note, so no extra notes slip in.",
    "flat": "Check your finger placement: play the note against a drone or tuner until it settles.",
    "sharp": "Check your finger placement: play the note against a drone or tuner until it settles.",
    "late": "Count steadily through the section (or use a metronome), so each note arrives on time.",
    "rushed": "Count steadily through the section (or use a metronome) and hold each note its full length.",
    "too soft": "Listen to how your teacher shapes the loud and soft notes, then match it.",
    "too loud": "Listen to how your teacher shapes the loud and soft notes, then match it.",
}


def _problems(rows):
    """{(teacher note index, kind)} for every mistake in these rows."""
    out = set()
    for r in rows:
        if r.get("t_index") is None:
            continue
        if r["status"] != "correct":
            out.add((r["t_index"], r["status"], r.get("teacher")))
        for f in r.get("flags", []):
            if f in PRIORITY:
                out.add((r["t_index"], f, r.get("teacher")))
    return out


FIXED_WORDS = {"wrong note": "the wrong note at note {n}", "missed": "the skipped note {n}",
               "extra": "the extra note", "flat": "the out-of-tune note {n}",
               "sharp": "the out-of-tune note {n}", "late": "the hesitation before note {n}",
               "rushed": "the rush into note {n}", "too soft": "the soft note {n}",
               "too loud": "the loud note {n}"}


def _since_last_time(result, previous):
    """One line comparing with the student's previous take of the same thing:
    the biggest section improvements, and which mistakes are now fixed."""
    if not previous or not previous.get("sections"):
        return []
    before = {s["id"]: s for s in previous["sections"]}
    ups = sorted(((s["score"] - before[s["id"]]["score"], s["id"], before[s["id"]]["score"], s["score"])
                  for s in result["sections"]
                  if s["id"] in before and s["score"] - before[s["id"]]["score"] >= 5), reverse=True)
    old = set().union(*[_problems(s["rows"]) for s in previous["sections"]])
    new = set().union(*[_problems(s["rows"]) for s in result["sections"]])
    fixed = sorted(old - new)
    if not ups and not fixed:
        return []
    parts = [f"section {sid} went from {a} to {b}" for _, sid, a, b in ups[:2]]
    if fixed:
        words = []
        for i, kind, _ in fixed:
            w = FIXED_WORDS[kind].format(n=i + 1)
            if w not in words:
                words.append(w)
        shown = words[:3] + (["more"] if len(words) > 3 else [])
        parts.append("you fixed " + (", ".join(shown[:-1]) + " and " + shown[-1] if len(shown) > 1 else shown[0]))
    return ["Since your last take: " + "; ".join(parts) + "."]


def write_feedback(result, comparison, max_per_section=3, previous=None):
    """previous: the result of this student's last take of the same thing, so
    the review can say what improved."""
    off = comparison.get("s_offset", 0.0)
    lines = []
    todo = [s for s in result["sections"] if not s["passed"]]

    # Headline
    if not todo:
        lines.append(f"Score {result['overall']}/100. Excellent - every section passed!")
    else:
        word = "Good work" if result["overall"] >= 70 else "A good start"
        lines.append(f"Score {result['overall']}/100. {word} - "
                     f"{len(todo)} section{'s' if len(todo) > 1 else ''} to practise.")

    # Strengths (only real ones)
    good = [NAMES[k] for k in result["strengths"] if k in NAMES]
    if good:
        lines.append("Strong points: " + ", ".join(good) + ".")

    lines += _since_last_time(result, previous)

    # Context: false start, tuning, tempo
    if comparison.get("false_start_notes"):
        lines.append(f"You restarted after {len(comparison['false_start_notes'])} notes - "
                     "that's fine, we scored your second attempt.")
    tune = comparison.get("tuning_offset_cents", 0)
    if abs(tune) > 5:
        lines.append(f"Your instrument was about {abs(tune):.0f} cents "
                     f"{'flat' if tune < 0 else 'sharp'}; tune it before the next session "
                     "(we adjusted for it this time).")
    # Long pauses were cut out before checking: say so kindly, no penalty
    paused = [r for r in comparison["notes"] if r.get("pause_s")]
    if paused:
        longest = max(r["pause_s"] for r in paused)
        first = paused[0]
        lines.append(
            (f"You paused before the {first['student']} at {_t(_when(first, off))} ({first['pause_s']:.1f} s). "
             if len(paused) == 1 else
             f"You paused {len(paused)} times (longest {longest:.1f} s). ")
            + "Pauses were cut out before checking, so they didn't affect your score. "
              "In your final take, try to play straight through.")
    tempo = comparison.get("tempo_ratio", 1.0)
    if tempo > 1.15:
        lines.append(f"You played about {round((tempo - 1) * 100)}% slower than your teacher. "
                     "That's fine while learning; speed up once the notes feel secure.")
    elif tempo < 0.87:
        lines.append(f"You played about {round((1 - tempo) * 100)}% faster than your teacher. "
                     "Slowing down will help you keep every note accurate.")

    # Sections with nothing played: one clear sentence instead of a list of skips
    unplayed = [s for s in result["sections"] if s["s_start"] is None]
    if unplayed:
        ids = [s["id"] for s in unplayed]
        last = result["sections"][-1]["id"]
        trailing = ids == list(range(ids[0], last + 1))
        names = (f"Section {ids[0]}" if len(ids) == 1
                 else f"Sections {ids[0]}-{ids[-1]}" if trailing
                 else "Sections " + ", ".join(map(str, ids)))
        lines.append(f"\n{names} {'was' if len(ids) == 1 else 'were'} not played"
                     + (" - it looks like the recording stopped early." if trailing else "."))
    played_todo = [s for s in todo if s["s_start"] is not None]

    # Sections to practise, weakest first - full detail for the 2 weakest only,
    # so a rough take gets a focused review rather than a wall of text
    ranked = sorted(played_todo, key=lambda x: x["score"])
    first_tip = None
    for s in ranked[:2]:
        start = s.get("s_orig_start") if s.get("s_orig_start") is not None else (
            s["s_start"] + off if s["s_start"] is not None else None)
        where = f", from {_t(start)} in your recording" if start is not None else ""
        lines.append(f"\nSection {s['id']} - {s['score']}/100{where}. Practise this section:")
        issues = _issues(s["rows"], off)
        for txt in issues[:max_per_section]:
            lines.append(f"  - {txt}")
        if len(issues) > max_per_section:
            lines.append(f"  - ...and {len(issues) - max_per_section} smaller point(s).")
        # one tip per kind of advice: the first section's tip goes with the
        # "Next step"; another section gets its own only if it needs DIFFERENT
        # advice (identical tips twice made rough reviews long and repetitive)
        kinds = sorted({k for _, k, _ in _problems(s["rows"])}, key=lambda k: PRIORITY[k])
        tip = TIPS[kinds[0]] if kinds else None
        if s is ranked[0]:
            first_tip = tip
        elif tip and tip != first_tip:
            lines.append(f"  Practice tip: {tip}")

    if len(ranked) > 2:
        rest = ", ".join(str(s["id"]) for s in sorted(ranked[2:], key=lambda x: x["id"]))
        lines.append(f"\nAlso below the pass mark: section{'s' if len(ranked) > 3 else ''} "
                     f"{rest}. Work on the two above first.")

    passed = [str(s["id"]) for s in result["sections"] if s["passed"]]
    if todo and passed:
        lines.append(f"\nSections passed: {', '.join(passed)}.")
    # Small points in sections that passed - worth knowing, not worth a re-record
    small = [(s["id"], txt) for s in result["sections"] if s["passed"] and len(todo) <= 1
             for txt in _issues(s["rows"], off)]  # only when there's room to mention them
    if small:
        lines.append("Small points in passed sections:")
        for sid, txt in small[:3]:
            lines.append(f"  - Section {sid}: {txt}")
    if ranked:
        lines.append(f"Next step: practise Section {ranked[0]['id']} slowly with the teacher's "
                     "recording, then record just that section again."
                     + (f" Tip: {first_tip}" if first_tip else ""))
    elif unplayed:
        lines.append("Next step: record the whole piece through to the end.")
    else:
        lines.append("Next step: record a full run-through for your teacher.")
    return "\n".join(lines)
