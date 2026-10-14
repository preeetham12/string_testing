"""
Part 3 - Compare a student's recording with the teacher's, note by note.

  1. DTW time alignment     - map every moment of the student's playing to the
                              matching moment of the teacher's, even if slower
                              or with pauses
  2. Note matching          - Needleman-Wunsch (the DNA-matching algorithm)
                              pairs teacher and student notes and labels each:
                              correct / wrong note / missed / extra
  3. Tuning compensation    - a whole instrument tuned a bit flat is reported
                              once, not counted as every note being wrong
  4. Rhythm                 - overall tempo is measured and allowed; only
                              hesitations and rushing are flagged
  5. Per-note checks        - intonation (cents), dynamics (dB), tone (MFCC)

Output is plain data. Turning it into a score and written feedback is Part 4.
"""
import numpy as np
import librosa

from engine.pitch import SR, HOP, hz_to_midi, midi_name

# Flag limits used to label notes here (Part 4 turns errors into scores)
FLAG = {
    "intonation_cents": 15.0,   # beyond this a note counts as out of tune
    "timing_ms": 120.0,         # hesitation / rush beyond this is flagged
    "dynamics_db": 6.0,         # much louder/softer than the teacher's shape
}
GAP_COST = 1.0       # cost of a missed or extra note
WRONG_COST = 1.0     # cost of a wrong pitch (cheaper than missed + extra)
TIME_COST = 0.6      # max extra cost when notes are far apart in time


# ---------- 1. DTW time alignment ----------
def _mean_by(x, y):
    """For each distinct value of x, the mean of y (sorted by x). Vectorised:
    the earlier per-value loop was O(n^2) and slowed every comparison."""
    ux, inv = np.unique(x, return_inverse=True)
    return ux, np.bincount(inv, weights=y) / np.bincount(inv)


def _chroma(y):
    return librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP * 2, fmin=150.0, n_octaves=5)


def dtw_time_map(y_t, y_s, ct=None):
    """Returns (path_times, map_fn): map_fn(student_time) -> teacher_time."""
    hop = HOP * 2
    ct = _chroma(y_t) if ct is None else ct  # the teacher's can be cached
    cs = _chroma(y_s)
    # Open-end DTW: both performances start together, but the student may stop
    # early, so the path may end at ANY point of the teacher's recording. (A
    # normal DTW would stretch a half-played piece over the whole lesson.)
    D, steps = librosa.sequence.dtw(X=ct, Y=cs, metric="cosine", backtrack=False,
                                    return_steps=True)
    n, m = D.shape
    end = int(np.argmin(D[:, -1] / (np.arange(n) + m)))  # best average cost per step
    sigma = np.array([[1, 1], [0, 1], [1, 0]])  # librosa's default step sizes
    i, j, wp = end, m - 1, [(end, m - 1)]
    while i > 0 or j > 0:
        if i == 0:
            j -= 1
        elif j == 0:
            i -= 1
        else:
            di, dj = sigma[steps[i, j]]
            i, j = i - di, j - dj
        wp.append((i, j))
    wp = np.array(wp[::-1])  # path runs from the start
    t_times = wp[:, 0] * hop / SR
    s_times = wp[:, 1] * hop / SR
    # average teacher time for each student time -> monotonic mapping
    us, ut = _mean_by(s_times, t_times)
    ut = np.maximum.accumulate(ut)
    return np.c_[t_times, s_times], (lambda s: float(np.interp(s, us, ut)))


# ---------- 2. Note matching (Needleman-Wunsch) ----------
def match_notes(T, S, s_to_t):
    """Align teacher notes T with student notes S. Returns list of (i, j)
    pairs where i or j is None for a missed or extra note."""
    n, m = len(T), len(S)
    D = np.zeros((n + 1, m + 1))
    D[:, 0] = np.arange(n + 1) * GAP_COST
    D[0, :] = np.arange(m + 1) * GAP_COST
    move = np.zeros((n + 1, m + 1), dtype=int)  # 0 diag, 1 up (missed), 2 left (extra)
    move[1:, 0], move[0, 1:] = 1, 2

    s_mapped = [s_to_t(x["start"]) for x in S]  # once per note, not per cell

    def cost(i, j):
        pitch = 0.0 if T[i]["midi"] == S[j]["midi"] else WRONG_COST
        dt = abs(T[i]["start"] - s_mapped[j])
        return pitch + TIME_COST * min(dt / 0.5, 1.0)

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = (D[i - 1, j - 1] + cost(i - 1, j - 1),
                       D[i - 1, j] + GAP_COST,
                       D[i, j - 1] + GAP_COST)
            move[i, j] = int(np.argmin(options))
            D[i, j] = options[move[i, j]]

    pairs, i, j = [], n, m
    while i > 0 or j > 0:
        if move[i, j] == 0:
            pairs.append((i - 1, j - 1)); i, j = i - 1, j - 1
        elif move[i, j] == 1:
            pairs.append((i - 1, None)); i -= 1
        else:
            pairs.append((None, j - 1)); j -= 1
    return pairs[::-1]


# ---------- repeated notes merged by room echo ----------
def _joined_pitch(p, n):
    """Pitch of two joined pieces of one note: length-weighted, not just the
    longer piece's (that misreported in-tune held notes as flat on real violin)."""
    a, b = p["end"] - p["start"], n["end"] - n["start"]
    pitch = (p["pitch"] * a + n["pitch"] * b) / max(a + b, 1e-6)
    cents = (p["cents"] * a + n["cents"] * b) / max(a + b, 1e-6)
    return {"pitch": round(pitch, 3), "cents": round(cents, 1)}


def merge_fragments(S, max_frag=0.25, max_gap=0.05):
    """Rejoin a short piece that directly continues the same pitch: bow noise
    can break the end off a note. split_merged_repeats then decides, from the
    lesson, where genuinely repeated notes are."""
    out = []
    for n in S:
        p = out[-1] if out else None
        if (p is not None and p["midi"] == n["midi"] and n["start"] - p["end"] < max_gap
                and min(p["end"] - p["start"], n["end"] - n["start"]) < max_frag):
            keep = p if p["end"] - p["start"] >= n["end"] - n["start"] else n
            out[-1] = {**keep, **_joined_pitch(p, n), "start": p["start"], "end": n["end"]}
        else:
            out.append(n)
    return out


def merge_against_lesson(T, S, s_to_t, max_gap=0.12, tempo=1.0):
    """Sensitive note detection can split one note in two at a vibrato wobble.
    If two neighbouring same-pitch pieces both line up with ONE lesson note,
    they are one note: join them. (Genuinely repeated notes line up with two
    lesson notes and stay separate.)"""
    def owner(t, midi):
        tt = s_to_t(t)
        best = [k for k, n in enumerate(T) if n["midi"] == midi and n["start"] - 0.1 <= tt <= n["end"] + 0.1]
        return min(best, key=lambda k: abs((T[k]["start"] + T[k]["end"]) / 2 - tt)) if best else None
    out = []
    for n in S:
        p = out[-1] if out else None
        if p is not None and p["midi"] == n["midi"] and n["start"] - p["end"] < max_gap:
            a = owner((p["start"] + p["end"]) / 2, p["midi"])
            b = owner((n["start"] + n["end"]) / 2, n["midi"])
            # ...and only if, together, they last about as long as ONE lesson
            # note: two real notes (e.g. a wrong note that happens to repeat
            # the previous pitch) last twice as long and must stay separate
            # (found by the benchmark: such wrong notes vanished as "missed")
            one_note = (T[a]["end"] - T[a]["start"]) * tempo if a is not None else 0
            # else: two notes, one out of tune. 30 cents: a real violinist's
            # pitch drifts ~20 cents within one long note (attack, vibrato) -
            # 20 split held notes on real recordings; an out-of-tune repeat is 30+
            same_pitch = abs(p["cents"] - n["cents"]) <= 30
            if (a is not None and a == b and same_pitch
                    and (n["end"] - p["start"]) <= 1.25 * one_note):
                keep = p if p["end"] - p["start"] >= n["end"] - n["start"] else n
                out[-1] = {**keep, **_joined_pitch(p, n), "start": p["start"], "end": n["end"],
                           "orig_start": p.get("orig_start", p["start"]),
                           **({"pause_s": p["pause_s"], "skip_timing": True} if p.get("pause_s") else {})}
                continue
        out.append(n)
    return out


def split_merged_repeats(T, S, path, student=None, tuning=0.0, cut=0.0):
    """In an echoey room the bow change between two SAME notes (A4, A4) can be
    smoothed over, so the student's take shows one long A4. Where the lesson
    repeats a pitch inside such a note, split it there (score-informed
    segmentation). The split time is inferred, not heard, so the second part's
    rhythm isn't judged. (Found by testing with a realistic recording.)"""
    t_times, s_times = path[:, 0], path[:, 1]
    ut, us = _mean_by(t_times, s_times)
    us = np.maximum.accumulate(us)
    sx, sy = _mean_by(s_times, t_times)
    to_t = lambda x: float(np.interp(x, sx, sy))
    to_s = lambda x: float(np.interp(x, ut, us))
    # overall tempo from the alignment (student seconds per teacher second)
    tempo = (s_times[-1] - s_times[0]) / max(t_times[-1] - t_times[0], 1e-6)
    out = []
    for n in S:
        a, b = to_t(n["start"]), to_t(n["end"])
        # Only where the note genuinely COVERS most of both repeated lesson
        # notes. (A first version split whenever a note merely touched the
        # next one's start, and broke correct, separately played notes.)
        def covers(k):
            p, q = T[k - 1], T[k]
            # ...and it must really LAST about as long as both notes together:
            # around a mistake the alignment stretches, so a single note can
            # appear to cover two (found by testing: a wrong 2nd note)
            long_enough = (n["end"] - n["start"]) >= 0.75 * (q["end"] - p["start"]) * tempo
            return (long_enough and a <= p["start"] + 0.3 * (p["end"] - p["start"])
                    and b >= q["end"] - 0.3 * (q["end"] - q["start"]))
        cuts = [to_s(T[k]["start"]) for k in range(1, len(T))
                if T[k]["midi"] == n["midi"] and T[k - 1]["midi"] == n["midi"]
                and a + 0.1 < T[k]["start"] < b - 0.1 and covers(k)]
        cuts = [c for c in cuts if n["start"] + 0.08 < c < n["end"] - 0.08]
        if not cuts:
            out.append(n)
            continue
        # each piece gets its OWN pitch measurement (stable middle, tuning
        # corrected): one of two repeated notes may be out of tune
        edges = [n["start"]] + cuts + [n["end"]]
        for k, (lo, hi) in enumerate(zip(edges, edges[1:])):
            piece = {**n, "start": round(lo, 3), "end": round(hi, 3), "split": True,
                     "skip_timing": k > 0, "inferred_start": k > 0,
                     "orig_start": round(n.get("orig_start", n["start"]) + (lo - n["start"]), 3)}
            if k > 0:
                piece.pop("pause_s", None)  # a pause belongs to the first piece only
            if student is not None:
                tm = np.asarray(student["times"]) - cut
                sel = (tm >= lo) & (tm < hi) & np.asarray(student["voiced"])
                if sel.sum() >= 5:
                    m = np.sort(hz_to_midi(np.asarray(student["f0"])[sel]))
                    core = m[int(len(m) * 0.2): max(int(len(m) * 0.8), int(len(m) * 0.2) + 1)]
                    pc = float(np.median(core)) - tuning / 100
                    if round(pc) == n["midi"]:
                        piece.update(cents=round((pc - round(pc)) * 100, 1), pitch=round(pc + tuning / 100, 3))
            out.append(piece)
    return out


def _blurred(T, S, i, pairs, tempo, max_len=0.25):
    """Teacher note i (short) has no student note, but both neighbours were
    played and the time between them matches the teacher's: the note was
    played and blurred (room echo), not skipped. (Found on real violin.)"""
    dur = T[i]["end"] - T[i]["start"]
    if dur >= max_len or not 0 < i < len(T) - 1:
        return False
    m = {a: b for a, b in pairs if a is not None and b is not None}
    if i - 1 not in m or i + 1 not in m:
        return False
    t_span = T[i + 1]["start"] - T[i - 1]["start"]
    s_span = (S[m[i + 1]]["start"] - S[m[i - 1]]["start"]) / max(tempo, 1e-6)
    # played: s_span ~ t_span; skipped: s_span ~ t_span - dur. Decide by the midpoint.
    return s_span > t_span - 0.5 * dur


def _sliver(T, i, played_t, max_len=0.3):
    """Teacher note i is a short same-pitch sliver next to a note that was played."""
    if T[i]["end"] - T[i]["start"] >= max_len:
        return False
    return any(0 <= k < len(T) and k in played_t and T[k]["midi"] == T[i]["midi"]
               for k in (i - 1, i + 1))


# ---------- false-start detection ----------
def find_restart(T, S, min_match=2, min_gap=0.25):
    """If the student played the opening, stopped, and started again from the
    beginning, return the index of the restart note, else 0."""
    opening = [n["midi"] for n in T[:3]]
    for r in range(min_match, len(S) // 2 + 1):
        before = [n["midi"] for n in S[:r]]
        again = [n["midi"] for n in S[r:r + len(opening)]]
        gap = S[r]["start"] - S[r - 1]["end"]
        # if the LESSON itself repeats its opening here, it's music, not a restart
        lesson_repeats = [n["midi"] for n in T[r:r + len(opening)]] == opening
        if (gap >= min_gap and again == opening and not lesson_repeats
                and before == [n["midi"] for n in T[:r]]):
            return r
    return 0


# ---------- 5. helpers for per-note loudness and tone ----------
def _note_db(y, note):
    seg = y[int(note["start"] * SR): int(note["end"] * SR)]
    return 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-10) if len(seg) else -100.0


def _note_tone(y, note):
    """Average 'tone colour' of a note (MFCCs 1-12, skipping overall loudness)."""
    seg = y[int(note["start"] * SR): int(note["end"] * SR)]
    if len(seg) < 2048:
        return None
    return librosa.feature.mfcc(y=seg, sr=SR, n_mfcc=13)[1:].mean(axis=1)


def compare(teacher, student, tuning_offset_cents=None):
    """teacher / student: output of pitch.analyse_audio (needs 'y' and 'notes')."""
    T = teacher["notes"]

    # 3. Tuning FIRST: from the session tuning check if given, else estimated as
    #    the typical sharpness/flatness of the student's notes vs the teacher's.
    #    Then re-name the student's notes as if the instrument were in tune, so a
    #    detuned violin can't turn an E5 into a D#5.
    if tuning_offset_cents is None:
        frac = lambda notes: [(n["pitch"] - round(n["pitch"])) * 100 for n in notes]
        est = float(np.median(frac(student["notes"])) - np.median(frac(T)))
        tuning = est if abs(est) > 5 else 0.0
        tuning_source = "estimated from this recording"
    else:
        tuning, tuning_source = float(tuning_offset_cents), "from tuning check"
    S = []
    for n in student["notes"]:
        p = n["pitch"] - tuning / 100
        S.append({**n, "midi": int(round(p)), "note": midi_name(p),
                  "cents": round((p - round(p)) * 100, 1)})

    # False start: set the abandoned attempt aside and compare from the restart
    restart = find_restart(T, S)
    false_start = [n["note"] for n in S[:restart]]
    y_s = student["y"]
    cut = 0.0
    if restart:
        cut = max(S[restart]["start"] - 0.05, 0.0)
        y_s = y_s[int(cut * SR):]
        S = [{**n, "start": round(n["start"] - cut, 3), "end": round(n["end"] - cut, 3)}
             for n in S[restart:]]
        student = {**student, "y": y_s}

    # The teacher's features never change between takes: cache them on the
    # teacher's dict. Each cache records the audio length it was made from, so
    # a section slice (shorter audio) never reuses the whole piece's cache.
    if teacher.get("_chroma_len") != len(teacher["y"]):
        teacher["chroma"], teacher["_chroma_len"] = _chroma(teacher["y"]), len(teacher["y"])
        teacher["_tones"] = {}
    path, s_to_t = dtw_time_map(teacher["y"], y_s, ct=teacher["chroma"])
    S = merge_fragments(S)
    tempo_est = (path[-1, 1] - path[0, 1]) / max(path[-1, 0] - path[0, 0], 1e-6)
    S = merge_against_lesson(T, S, s_to_t, tempo=tempo_est)
    S = split_merged_repeats(T, S, path, student, tuning, cut)
    pairs = match_notes(T, S, s_to_t)

    # 4. Tempo: median ratio of time between neighbouring notes (only pairs
    #    with no missed/extra note between them; one hesitation can't distort it)
    neighbours = [(a, b) for a, b in zip(pairs, pairs[1:])
                  if None not in a and None not in b]
    played = [(i, j) for i, j in pairs if i is not None and j is not None]
    iois = [(T[i2]["start"] - T[i1]["start"], S[j2]["start"] - S[j1]["start"])
            for (i1, j1), (i2, j2) in neighbours]
    tempo = float(np.median([s / t for t, s in iois if t > 0])) if iois else 1.0

    # Dynamics are compared as a SHAPE: each note relative to that player's average
    t_db = {i: _note_db(teacher["y"], T[i]) for i, _ in played}
    s_db = {j: _note_db(student["y"], S[j]) for _, j in played}
    t_mean, s_mean = np.mean(list(t_db.values())), np.mean(list(s_db.values()))

    # Tone, like dynamics, is compared RELATIVE to each player's average sound:
    # a brighter or darker violin shifts every note equally and cancels out;
    # what's left is how each note's colour differs from the player's usual sound
    def centred(y, notes, idx, cache=None):
        if cache is None:
            v = {k: _note_tone(y, notes[k]) for k in idx}
        else:  # the teacher's per-note tone, keyed by the note's exact times
            v = {}
            for k in idx:
                key = (notes[k]["start"], notes[k]["end"])
                if key not in cache:
                    cache[key] = _note_tone(y, notes[k])
                v[k] = cache[key]
        v = {k: x for k, x in v.items() if x is not None}
        mean = np.mean(list(v.values()), axis=0) if v else 0
        return {k: x - mean for k, x in v.items()}
    t_tone = centred(teacher["y"], T, [i for i, _ in played], cache=teacher["_tones"])
    s_tone = centred(student["y"], S, [j for _, j in played])

    played_t = {i for i, j in pairs if i is not None and j is not None}
    rows, prev = [], None
    for i, j in pairs:
        row = {"t_index": i, "s_index": j,
               "s_orig": S[j].get("orig_start") if j is not None else None,
               "pause_s": S[j].get("pause_s", 0) if j is not None else 0,
               "teacher": T[i]["note"] if i is not None else None,
               "student": S[j]["note"] if j is not None else None,
               "t_start": T[i]["start"] if i is not None else None,
               "s_start": S[j]["start"] if j is not None else None,
               "flags": []}
        if i is None:
            row["status"] = "extra"
            prev = None  # don't time the next note across an inserted note
        elif j is None and _sliver(T, i, played_t):
            # a very short teacher note with the same pitch as a neighbour the
            # student played: usually the ring or bow lift at the end of a held
            # note, split off in the teacher's recording. Ambiguous, so not
            # called "missed" (found with real violin recordings).
            row["status"] = "correct"
            row["sliver"] = True
        elif j is None and _blurred(T, S, i, pairs, tempo):
            # a fast passing note blurred into its neighbours by room echo: the
            # time between its neighbours is the teacher's, so it was played;
            # had it been skipped, that time would have shrunk by its length
            row["status"] = "correct"
            row["unclear"] = True
        elif j is None:
            row["status"] = "missed"
            prev = None  # ...or across a skipped one
        else:
            if T[i]["midi"] != S[j]["midi"]:
                row["status"] = "wrong note"
                row["semitones_off"] = S[j]["midi"] - T[i]["midi"]
                if row["semitones_off"] % 12 == 0:  # right note, wrong octave
                    row["flags"].append("octave too high" if row["semitones_off"] > 0
                                        else "octave too low")
            else:
                row["status"] = "correct"
                # A teacher note under 0.2 s is mostly a glide between notes (or
                # a sliver of one): its "pitch" isn't meaningful to compare with.
                # (Found on real violin: false "sharp" against a 0.15 s sliver.)
                if T[i]["end"] - T[i]["start"] >= 0.2:
                    err = S[j]["cents"] - T[i]["cents"]  # tuning already removed
                    row["cents_error"] = round(err, 1)
                    if abs(err) > FLAG["intonation_cents"]:
                        row["flags"].append("flat" if err < 0 else "sharp")
            if prev is not None and not T[i].get("skip_timing") and not S[j].get("skip_timing"):
                pi, pj = prev
                t_ioi, s_ioi = T[i]["start"] - T[pi]["start"], S[j]["start"] - S[pj]["start"]
                ms = (s_ioi / tempo - t_ioi) * 1000
                row["timing_ms"] = round(ms)
                if abs(ms) > FLAG["timing_ms"]:
                    row["flags"].append("late" if ms > 0 else "rushed")
            if T[i]["end"] - T[i]["start"] >= 0.2:  # same reason as intonation
                dyn = (s_db[j] - s_mean) - (t_db[i] - t_mean)
                row["dynamics_db"] = round(float(dyn), 1)
                if abs(dyn) > FLAG["dynamics_db"]:
                    row["flags"].append("too loud" if dyn > 0 else "too soft")
            a, b = t_tone.get(i), s_tone.get(j)
            if a is not None and b is not None:
                # 0 = same tone change as the teacher; bigger = more different
                row["tone_diff"] = round(float(np.linalg.norm(a - b)), 1)
            # time the next note from the last start that was actually HEARD;
            # a split piece's start is inferred, so it can't be a reference
            if not S[j].get("inferred_start"):
                prev = (i, j)
        rows.append(row)

    # A "late" right next to an equal "rushed" (or the reverse) is one note
    # start detected in the wrong place - common with room echo - not a
    # rhythm mistake: one misplaced start makes the gap before it too long and
    # the gap after it too short by the same amount. A real hesitation makes
    # only one gap long. So such pairs aren't flagged. (Found by the benchmark.)
    timed = [r for r in rows if r.get("timing_ms") is not None]
    unsure = set()  # decide every pair first, then change rows (changing them
    for k in range(len(timed) - 1):  # while looping broke 3 notes in a row)
        a, b = timed[k]["timing_ms"], timed[k + 1]["timing_ms"]
        if a * b < 0 and abs(a + b) < 0.4 * max(abs(a), abs(b)):
            unsure.update({k, k + 1})
    for k in unsure:
        r = timed[k]
        r["flags"] = [f for f in r["flags"] if f not in ("late", "rushed")]
        # ...and not scored either: if we can't tell, we don't penalise
        r["timing_ms_unreliable"] = r.pop("timing_ms")

    counts = {k: sum(r["status"] == k for r in rows)
              for k in ("correct", "wrong note", "missed", "extra")}
    return {"tempo_ratio": round(tempo, 2), "tuning_offset_cents": round(tuning, 1),
            "false_start_notes": false_start,
            "student_notes": S,  # after tuning correction, rejoining and splitting
            # add to a student time to get the time in their ORIGINAL recording
            "s_offset": round(cut + student.get("trim_offset", 0.0), 3),
            "tuning_source": tuning_source, "counts": counts, "notes": rows,
            "dtw_path": path}
