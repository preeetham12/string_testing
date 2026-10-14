"""
Real-violin benchmark (Bach10 dataset: 10 real solo violin recordings with
note-level ground truth).

Get the data (violin parts + annotations only, ~40 MB):
    git clone --depth 1 --filter=blob:none --sparse https://github.com/flippy-fyp/Bach10_v1.1.git Bach10
    cd Bach10 && git sparse-checkout set --no-cone '*-violin.wav' '*-GTNotes.mat' && cd ..
Run:
    python -m tests.real_violin_benchmark /path/to/Bach10

Two tests:
 1. Note detection on real violin: the engine's notes for each recording
    against the annotated notes.
 2. The full system on real violin sound: each recording is the teacher's
    reference; student takes are made from the SAME real audio with mistakes
    planted at annotated notes (pitch-shifted wrong / out-of-tune notes,
    removed notes, inserted notes, hesitations, pauses), a different tempo and
    a recording condition. Limitation: the "student" is the same performer, so
    this tests real violin SOUND, not differences between two players.
"""
import glob
import json
import os
import sys
import time
import warnings
from collections import Counter, defaultdict

import numpy as np
import librosa
import soundfile as sf

warnings.filterwarnings("ignore")
from engine.compare import compare
from engine.feedback import write_feedback
from engine.pipeline import prepare
from engine.quality import check_recording
from engine.score import score
from tests.test_unseen import add_noise, coloured_noise

SR = 44100
OUT = "data/real_violin"
KINDS = ["wrong", "flat", "sharp", "missed", "extra", "late", "pause"]
CONDITIONS = ["as recorded", "home room", "far mic", "fan", "phone"]


def load_piece(folder):
    """The violin recording and its annotated notes, from GTNotes.mat: for each
    note, the 10 ms frames where it sounds and its pitch in each frame. (The
    .txt file's 2nd column is the note's time in the SCORE, not its end in the
    audio - reading it as an end time gave notes that end before they start.)"""
    import scipy.io as sio
    name = os.path.basename(folder.rstrip("/"))
    y, sr = sf.read(glob.glob(f"{folder}/*-violin.wav")[0])
    assert sr == SR
    g = sio.loadmat(glob.glob(f"{folder}/*-GTNotes.mat")[0])["GTNotes"][0, 0]  # part 1 = violin
    notes = []
    for k in range(g.shape[0]):
        frames, pitch = g[k, 0][0], g[k, 0][1]
        notes.append({"on": (frames[0] - 1) * 0.01 + 0.023, "off": (frames[-1] - 1) * 0.01 + 0.023,
                      "midi": int(round(float(np.median(pitch))))})
    return name, y, sorted(notes, key=lambda n: n["on"])


def match(detected, truth, tol=0.15):
    """Pair detected notes with annotated ones: same pitch, start within tol.
    Returns {truth index: detected index}."""
    pairs, used = {}, set()
    for k, g in enumerate(truth):
        best = None
        for j, d in enumerate(detected):
            if j in used or d["midi"] != g["midi"]:
                continue
            dt = abs(d["start"] - g["on"])
            if dt <= tol and (best is None or dt < best[0]):
                best = (dt, j)
        if best:
            pairs[k] = best[1]
            used.add(best[1])
    return pairs


def room(y, rng, drr_db, rt60):
    """Room echo only: the recording already has the real bow noise."""
    n = int(rt60 * SR)
    tail = rng.normal(0, 1, n) * np.exp(-6.9 * np.arange(n) / SR / rt60)
    tail[: int(0.005 * SR)] = 0
    tail *= np.sqrt(10 ** (-drr_db / 10) / np.sum(tail ** 2))
    tail[0] = 1.0
    w = np.convolve(y, tail)[:len(y)]
    return w / np.max(np.abs(w)) * 0.5


def shift(x, steps):
    """Pitch-shift a (possibly very short) segment: pad first, since the
    shifting method needs a minimum amount of audio, then trim back."""
    pad = 4096
    z = librosa.effects.pitch_shift(np.pad(x, pad), sr=SR, n_steps=steps)[pad: pad + len(x)]
    return np.nan_to_num(z)


def fade_in(x, ms=4):
    k = min(int(ms / 1000 * SR), len(x))
    x = x.copy(); x[:k] *= np.linspace(0, 1, k); return x


def fade_out(x, ms=4):
    k = min(int(ms / 1000 * SR), len(x))
    x = x.copy(); x[len(x) - k:] *= np.linspace(1, 0, k); return x


def fade(x, ms=8):
    k = min(int(ms / 1000 * SR), len(x) // 2)
    if k > 0:
        x = x.copy()
        x[:k] *= np.linspace(0, 1, k)
        x[-k:] *= np.linspace(1, 0, k)
    return x


def make_take(y, truth, judgeable, rng, n_mistakes):
    """Plant n_mistakes at annotated notes (only notes the engine found in the
    reference, so each one can be judged). The audio is only touched where a
    mistake is planted: everything else is the original, sample for sample.
    (A first version faded every note boundary, which put tiny artificial gaps
    into legato playing and caused false alarms.)"""
    # Room tone for inserted gaps: the QUIETEST 0.4 s of the recording, looped
    # with crossfades. (A first version used the moments before the first
    # note, which held the start of the first bow stroke: looping it inserted
    # bow attacks instead of silence, so pauses weren't recognised.)
    w = int(0.4 * SR)
    rms = np.sqrt(np.convolve(y ** 2, np.ones(w) / w, mode="valid"))
    q = int(np.argmin(rms[::256])) * 256
    quiet = y[q: q + w]

    def tone(secs):
        n, x = int(secs * SR), int(0.02 * SR)
        out = np.zeros(n + w)
        ramp = np.ones(w); ramp[:x] = np.linspace(0, 1, x); ramp[-x:] = np.linspace(1, 0, x)
        for a in range(0, n, w - x):
            out[a: a + w] += quiet * ramp
        return out[:n]
    cand = [k for k in judgeable if 1 <= k < len(truth) - 1]
    spots = []
    for k in rng.permutation(cand):
        if len(spots) == n_mistakes:
            break
        if all(abs(k - s) >= 3 for s in spots):
            spots.append(int(k))
    plan = {k: KINDS[int(rng.integers(len(KINDS)))] for k in spots}
    cut = lambda k: int(truth[k]["on"] * SR) if k < len(truth) else len(y)
    out, extras, prev = [y[: cut(0)]], 0, 0
    for k in sorted(plan):
        kind = plan[k]
        out.append(y[cut(prev): cut(k)])           # untouched audio up to this note
        a, b = cut(k), cut(k + 1)
        note_len = min(int((truth[k]["off"] - truth[k]["on"]) * SR), b - a)
        if kind == "missed":
            out[-1] = fade_out(out[-1])
            prev = k + 1
            out.append(fade_in(y[b: b + 1]))       # the next note starts cleanly
            continue
        if kind == "late":
            out[-1] = fade_out(out[-1]); out.append(tone(rng.uniform(0.5, 0.8)))
        if kind == "pause":
            out[-1] = fade_out(out[-1]); out.append(tone(rng.uniform(1.5, 3.0)))
        if kind == "extra":
            step = float(rng.choice([-5, 3, 4]))
            out[-1] = fade_out(out[-1]); out.append(fade(shift(y[a: a + int(0.35 * SR)], step)))
            extras += 1
        if kind in ("wrong", "flat", "sharp"):
            steps = (float(rng.choice([-2, -1, 1, 2])) if kind == "wrong"
                     else float(rng.uniform(0.3, 0.45)) * (-1 if kind == "flat" else 1))
            out.append(fade(shift(y[a: a + note_len], steps), 4))
            out.append(fade_in(y[a + note_len: b]))
        else:
            out.append(fade_in(y[a: b]) if kind in ("late", "pause", "extra") else y[a: b])
        prev = k + 1
    out.append(y[cut(prev):])
    truth_kinds = {k: v for k, v in plan.items() if v != "extra"}
    return np.concatenate(out), truth_kinds, extras


def caught(kind, r, by_t, i, T):
    """Was the problem flagged, even if with a related label? A flat note can
    cross half a semitone and be called a wrong note; a hesitation plus the
    music's own rest can exceed 1 s and be reported as a pause; with repeated
    pitches, it's ambiguous which identical note was skipped."""
    if kind in ("flat", "sharp") and r["status"] == "wrong note":
        return True
    if kind == "late" and r.get("pause_s", 0) > 0:
        return True
    if kind == "missed":
        for k in (i - 1, i + 1):
            n = by_t.get(k)
            if n and n["status"] == "missed" and T[k]["midi"] == T[i]["midi"]:
                return True
    return False


def judge(rows, planted, t_of, extras, T=None):
    """planted: {truth index: kind}; t_of: truth index -> teacher note index.
    Returns (strict found, caught found, false alarms)."""
    by_t = {r["t_index"]: r for r in rows if r.get("t_index") is not None}
    found, loose = defaultdict(list), defaultdict(list)
    planted_t = {}
    for k, kind in planted.items():
        i = t_of[k]
        planted_t[i] = kind
        r = by_t.get(i, {"status": "?", "flags": []})
        strict = {
            "wrong": r["status"] == "wrong note",
            "flat": r["status"] == "correct" and "flat" in r["flags"],
            "sharp": r["status"] == "correct" and "sharp" in r["flags"],
            "missed": r["status"] == "missed",
            "late": "late" in r["flags"],
            "pause": r["status"] == "correct" and "late" not in r["flags"] and r.get("pause_s", 0) > 0,
        }[kind]
        found[kind].append(strict)
        loose[kind].append(strict or (T is not None and caught(kind, r, by_t, i, T)))
    got_extra = sum(r["status"] == "extra" for r in rows)
    ex = [True] * min(extras, got_extra) + [False] * max(0, extras - got_extra)
    found["extra"] += ex
    loose["extra"] += ex
    false = [f"note {i + 1}: {r['status']} {','.join(r['flags'])}".strip()
             for i, r in by_t.items() if i not in planted_t and (r["status"] != "correct" or r["flags"])]
    if got_extra > extras:
        false.append(f"{got_extra - extras} extra")
    return found, loose, false


def run(root, takes_per_condition=3, seed=11):
    os.makedirs(OUT, exist_ok=True)
    rng = np.random.default_rng(seed)
    detection, results = [], []
    start = globals().get("START", 1) - 1
    for folder in sorted(glob.glob(f"{root}/[0-9][0-9]-*"))[start: start + globals().get("PIECES", 10)]:
        name = os.path.basename(folder.rstrip("/"))
        saved = f"{OUT}/piece_{name}.json"
        if os.path.exists(saved):  # resumable: a finished piece is not redone
            d = json.load(open(saved))
            detection.append(d["detection"]); results += d["results"]
            rng = np.random.default_rng(d["next_seed"])
            print(f"  {name}: loaded saved results", flush=True)
            continue
        name, y, truth = load_piece(folder)
        piece_results_from = len(results)
        ref = f"{OUT}/{name}_teacher.wav"
        sf.write(ref, y, SR, subtype="PCM_16")
        t0 = time.time()
        teacher = prepare(ref)
        teacher_secs = time.time() - t0
        for n in teacher["notes"]:  # times back in the original file
            n["start_orig"] = n["start"] + teacher["trim_offset"]
        det = [{"start": n["start_orig"], "midi": n["midi"]} for n in teacher["notes"]]
        pairs = match(det, truth)
        onset_err = [abs(det[j]["start"] - truth[k]["on"]) for k, j in pairs.items()]
        detection.append({"piece": name, "truth": len(truth), "detected": len(det),
                          "matched": len(pairs), "onset_ms": 1000 * float(np.median(onset_err)),
                          "teacher_secs": teacher_secs, "secs": len(y) / SR})
        for cond in CONDITIONS:
            for k in range(takes_per_condition):
                # take 0 of each condition is a CONTROL: no mistakes planted, so
                # any reported mistake is a false alarm. (Takes are not
                # time-stretched: that smears note starts as no player would.)
                n_mistakes = 0 if k == 0 else int(rng.integers(1, 5))
                take, planted, extras = make_take(y, truth, list(pairs), rng, n_mistakes)
                if cond == "home room":
                    take = room(take, rng, 6, 0.6)
                elif cond == "far mic":
                    take = room(take, rng, 0, 0.8)
                elif cond == "fan":
                    take = add_noise(take, coloured_noise(len(take), "pink"), -22)
                take = take / np.max(np.abs(take)) * 0.7
                p = f"{OUT}/{name}_{cond.replace(' ', '_')}_{k}.wav"
                if cond == "phone":
                    a = librosa.resample(take, orig_sr=SR, target_sr=48000)
                    sf.write(p, np.stack([a, a * 0.9], axis=1), 48000, subtype="PCM_16")
                else:
                    sf.write(p, take, SR, subtype="PCM_16")
                t0 = time.time()
                qc = check_recording(p, reference_path=ref)
                if qc["overall"] == "FAIL":
                    results.append({"piece": name, "cond": cond, "rejected": True,
                                    "why": [c["check"] for c in qc["checks"] if c["status"] == "FAIL"]})
                    continue
                c = compare(teacher, prepare(p, student=True))
                r = score(c, teacher["notes"])
                write_feedback(r, c)
                secs = time.time() - t0
                found, loose, false = judge(c["notes"], planted, pairs, extras, teacher["notes"])
                exact = not false and all(all(v) for v in found.values())
                caught_all = not false and all(all(v) for v in loose.values())
                results.append({"piece": name, "cond": cond, "rejected": False, "found": dict(found),
                                "caught": dict(loose), "caught_all": caught_all,
                                "control": n_mistakes == 0,
                                "false": false, "exact": exact, "secs": secs,
                                "take_secs": len(take) / SR, "planted": {str(k + 1): v for k, v in planted.items()},
                                "extras": extras})
        next_seed = int(rng.integers(1 << 31))
        rng = np.random.default_rng(next_seed)
        json.dump({"detection": detection[-1], "results": results[piece_results_from:],
                   "next_seed": next_seed}, open(saved, "w"), default=str)
        print(f"  {name}: {len(pairs)}/{len(truth)} notes found in the reference", flush=True)
    return detection, results


def report(detection, results):
    print("\n1. NOTE DETECTION on real violin (engine vs annotated notes)")
    tot_t = sum(d["truth"] for d in detection)
    tot_d = sum(d["detected"] for d in detection)
    tot_m = sum(d["matched"] for d in detection)
    for d in detection:
        print(f"  {d['piece']:32s} found {d['matched']:2d}/{d['truth']:2d}  "
              f"(engine reported {d['detected']:2d})  note-start error {d['onset_ms']:4.0f} ms")
    print(f"  ALL: found {tot_m}/{tot_t} annotated notes ({100 * tot_m / tot_t:.1f}%), "
          f"{100 * tot_m / tot_d:.1f}% of reported notes are real")

    done = [r for r in results if not r["rejected"]]
    rej = [r for r in results if r["rejected"]]
    print(f"\n2. FULL SYSTEM on real violin sound ({len(results)} takes, {len(rej)} rejected by the quality gate)")
    recall, loose = defaultdict(list), defaultdict(list)
    for r in done:
        for kind, oks in r["found"].items():
            recall[kind] += oks
        for kind, oks in r.get("caught", {}).items():
            loose[kind] += oks
    print(f"  {'mistake':8s}{'strict (exact label)':>24s}{'caught (problem flagged)':>28s}")
    for kind in KINDS:
        v, w = recall.get(kind, []), loose.get(kind, [])
        if v:
            print(f"  {kind:8s}{sum(v):>10d}/{len(v):<3d} {100 * sum(v) / len(v):5.1f}%"
                  f"{sum(w):>14d}/{len(w):<3d} {100 * sum(w) / len(w):5.1f}%")
    ctrl = [r for r in done if r["control"]]
    print(f"  CONTROL takes (no mistakes planted): {sum(r['exact'] for r in ctrl)}/{len(ctrl)} clean reviews, "
          f"{sum(len(r['false']) for r in ctrl) / max(len(ctrl), 1):.2f} false alarms per take")
    print("  Review exactly right:")
    for cond in CONDITIONS:
        rs = [r for r in done if r["cond"] == cond]
        if rs:
            ex = sum(r["exact"] for r in rs)
            fa = sum(len(r["false"]) for r in rs) / len(rs)
            print(f"    {cond:12s} {ex:2d}/{len(rs):<2d} {100 * ex / len(rs):5.1f}%   false alarms {fa:.2f} per take")
    ex = sum(r["exact"] for r in done)
    print(f"    {'ALL':12s} {ex:2d}/{len(done):<2d} {100 * ex / max(len(done), 1):5.1f}%")
    ca = sum(r.get("caught_all", r["exact"]) for r in done)
    print(f"  Every planted problem caught and no false alarms: {ca}/{len(done)} ({100 * ca / max(len(done), 1):.1f}%)")
    if rej:
        print("  Rejected:", Counter(tuple(r["why"]) for r in rej))
    per_min = [r["secs"] / r["take_secs"] * 60 for r in done]
    print(f"\n3. LATENCY: {np.median(per_min):.1f} s per minute of music (median), "
          f"{np.percentile(per_min, 95):.1f} s (95th pct); takes {min(r['take_secs'] for r in done):.0f}-"
          f"{max(r['take_secs'] for r in done):.0f} s took {min(r['secs'] for r in done):.1f}-{max(r['secs'] for r in done):.1f} s")
    bad = [r for r in done if not r["exact"]][:10]
    if bad:
        print("\nWrong reviews (examples):")
        for r in bad:
            miss = [k for k, v in r["found"].items() if not all(v)]
            print(f"  {r['piece']} / {r['cond']}: planted {r['planted']}{' +%d extra' % r['extras'] if r['extras'] else ''}"
                  f" | not found: {miss or '-'} | false alarms: {r['false'] or '-'}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="Bach10")
    ap.add_argument("--pieces", type=int, default=10, help="use the first N pieces (quick runs)")
    ap.add_argument("--start", type=int, default=1, help="first piece number (e.g. 5 = pieces 05-10)")
    ap.add_argument("--conditions", default=",".join(CONDITIONS), help="comma-separated subset")
    ap.add_argument("--out", default=OUT, help="results folder (separate runs don't mix)")
    a = ap.parse_args()
    OUT = a.out
    CONDITIONS[:] = [c for c in CONDITIONS if c in a.conditions.split(",")]
    PIECES = a.pieces
    START = a.start
    det, res = run(a.root)
    json.dump({"detection": det, "results": res}, open(f"{OUT}/results.json", "w"), indent=1, default=str)
    report(det, res)
