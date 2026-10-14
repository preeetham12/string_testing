"""Glue: recording file -> cleaned audio (Part 2) -> notes (Part 1).

Teachers' references and students' takes are prepared slightly differently:
  teacher: careful note detection (never invents notes); pauses are kept,
           because rests are part of the music
  student: sensitive note detection (corrected against the lesson later), and
           long pauses (stopping to think, turning a page) are CUT OUT before
           checking, so they don't throw the comparison off
"""
import numpy as np
import librosa

from engine.clean import clean_recording
from engine.pitch import HOP, SR, analyse_audio
from engine.quality import LOWEST_HZ

import os
# How deep a loudness dip must be to start a new note in a STUDENT's take
# (teachers always use the careful 6 dB). Overridable for benchmarking.
STUDENT_ONSET_DB = float(os.environ.get("STUDENT_ONSET_DB", "3.0"))
PAUSE_MIN_S = 1.0     # quiet for at least this long = a pause, not a hesitation
PAUSE_KEEP_S = 0.3    # how much of a pause to keep, so notes don't collide
QUIET_DB = 25.0       # "quiet" = this far below the playing level


def find_pauses(y):
    """Stretches inside the take that are much quieter than the playing for
    at least PAUSE_MIN_S. Returns [(start_s, end_s)]."""
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    db = 20 * np.log10(rms + 1e-10)
    loud = np.percentile(db, 90)
    quiet = db < loud - QUIET_DB
    t = librosa.frames_to_time(np.arange(len(db)), sr=SR, hop_length=HOP)
    out, start = [], None
    for i, q in enumerate(np.append(quiet, False)):
        if q and start is None:
            start = i
        elif not q and start is not None:
            a, b = t[start], t[min(i, len(t) - 1)]
            if start > 0 and i < len(quiet) and b - a >= PAUSE_MIN_S:  # inside, not at the ends
                out.append((float(a), float(b)))
            start = None
    return out


def prepare(path, instrument="violin", student=False):
    y, info = clean_recording(path, instrument=instrument)
    trim = float(info["trimmed_start_s"])
    pauses = find_pauses(y) if student else []
    # cut the pauses; remember where each cut is so times can be mapped back
    segments, cuts, last, removed = [], [], 0, 0.0
    for a, b in pauses:
        i0, i1 = int((a + PAUSE_KEEP_S / 2) * SR), int((b - PAUSE_KEEP_S / 2) * SR)
        if i1 <= i0:
            continue
        segments.append(y[last:i0])
        new_t = i0 / SR - removed                 # where the cut sits in the new timeline
        removed += (i1 - i0) / SR
        cuts.append({"new_t": new_t, "removed": (i1 - i0) / SR, "pause_s": b - a})
        last = i1
    segments.append(y[last:])
    y2 = np.concatenate(segments)

    out = analyse_audio(y2, fmin=LOWEST_HZ[instrument] * 0.9, onset_db=STUDENT_ONSET_DB if student else 6.0)

    def to_original(t):
        """Time in the shortened audio -> time in the student's ORIGINAL file."""
        return t + trim + sum(c["removed"] for c in cuts if c["new_t"] <= t)

    for n in out["notes"]:
        n["orig_start"] = round(to_original(n["start"]), 3)
    for c in cuts:  # the first note after each pause: noted, rhythm not judged
        after = [n for n in out["notes"] if c["new_t"] - 0.05 <= n["start"] <= c["new_t"] + 0.6]
        if after:
            after[0]["pause_s"] = round(c["pause_s"], 1)
            after[0]["skip_timing"] = True
    out["trim_offset"] = trim
    out["pauses"] = [{"at": round(to_original(c["new_t"]), 2), "length_s": round(c["pause_s"], 1)}
                     for c in cuts]
    return out


def suggest_splits(teacher, min_share=0.3):
    """Notes in the teacher's reference that look like TWO notes merged by room
    echo (A4, A4 heard as one long A4). Re-listens with the sensitive detector:
    where one careful note contains a bow change and two same-pitch halves that
    are each a fair share of it, suggest splitting there. The teacher decides
    in the studio; nothing is changed automatically."""
    sensitive = analyse_audio(teacher["y"], onset_db=3.0)["notes"]
    typical = float(np.median([n["end"] - n["start"] for n in teacher["notes"]]))
    out = []
    for i, n in enumerate(teacher["notes"]):
        length = n["end"] - n["start"]
        # two merged notes last about twice as long as a normal note; a small
        # loudness dip inside an ordinary note is just vibrato (found by
        # testing: without this, 7 of 9 suggestions were wrong)
        if length < 1.6 * typical:
            continue
        inside = [m for m in sensitive if m["midi"] == n["midi"]
                  and m["start"] >= n["start"] - 0.05 and m["end"] <= n["end"] + 0.05]
        best = None
        for m in inside[1:]:  # a boundary = where a later piece starts
            a, b = m["start"] - n["start"], n["end"] - m["start"]
            if min(a, b) >= min_share * length:
                if best is None or abs(a - b) < abs((best - n["start"]) - (n["end"] - best)):
                    best = m["start"]
        if best is not None:
            out.append({"index": i, "at": round(best, 3)})
    return out


def split_note(notes, index, at=None):
    """Split note `index` at time `at` (seconds); without `at`, in the middle.
    The second half's timing is only judged if the split point was HEARD."""
    n = notes[index]
    heard = at is not None
    at = at if heard else (n["start"] + n["end"]) / 2
    if not n["start"] + 0.05 < at < n["end"] - 0.05:
        raise ValueError("The split point must be inside the note")
    first = {**n, "end": round(at - 0.02, 3)}
    second = {**n, "start": round(at, 3), "split_by_teacher": True}
    first.pop("suggest_split_at", None)
    second.pop("suggest_split_at", None)
    if not heard:
        second["skip_timing"] = True
    return notes[:index] + [first, second] + notes[index + 1:]
