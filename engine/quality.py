"""
Part 2 - Quality gate.

Runs on the RAW recording (before any cleaning or volume changes), because
cleaning would hide the very problems we want to catch.

Each check returns PASS, WARN or FAIL plus a message the student understands.
  FAIL -> recording is rejected, student re-records (attempt not counted)
  WARN -> analysed, but the warning is shown in the report
"""
import numpy as np
import librosa
import soundfile as sf

from engine.pitch import SR, HOP, track_pitch, hz_to_midi

# Lowest note of each instrument (Hz). Used to set the pitch range and filters.
LOWEST_HZ = {"violin": 196.0, "viola": 130.8, "cello": 65.4, "guitar": 82.4}

# Starting thresholds - to be tuned on real recordings.
T = {
    "clip_fail": 0.001,       # more than 0.1% of samples at full scale
    "peak_fail_db": -30.0,    # loudest point quieter than -30 dBFS
    "snr_warn_db": 20.0,      # music vs background noise
    "snr_fail_db": 10.0,
    "playing_fail": 0.40,     # less than 40% of the recording has playing
    "dropout_ms": 5.0,        # run of digital silence inside the recording
    "duration_warn": (0.5, 2.0),
    "tuning_warn_cents": 15.0,
    "match_fail": 0.25,       # chroma DTW cost: same piece scored 0.12-0.21 in tests
}


def load_raw(path):
    """Read samples exactly as recorded (no resampling, no normalising)."""
    y, sr = sf.read(path, always_2d=True)
    return y.mean(axis=1), sr


def _frame_db(y, sr):
    rms = librosa.feature.rms(y=y, frame_length=int(0.046 * sr),
                              hop_length=int(0.0116 * sr))[0]
    return 20 * np.log10(rms + 1e-10)


def _dropouts(y, sr):
    """Runs of exact zeros INSIDE the recording (audio glitches).
    Returns (number of gaps, longest gap in ms)."""
    nz = np.flatnonzero(y != 0)
    if len(nz) == 0:
        return 0, 0.0
    inner = y[nz[0]:nz[-1] + 1] == 0
    min_run = int(T["dropout_ms"] / 1000 * sr)
    edges = np.diff(np.concatenate([[0], inner.astype(int), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    lengths = (ends - starts)[(ends - starts) >= min_run]
    return int(len(lengths)), float(lengths.max() / sr * 1000) if len(lengths) else 0.0


def _chroma(y, fmin):
    """Musical fingerprint: energy of the 12 note names over time, built only
    from frequencies the instrument can play (so mains hum can't pollute it)."""
    y, _ = librosa.effects.trim(y, top_db=30)
    return librosa.feature.chroma_cqt(y=y, sr=SR, hop_length=HOP * 2,
                                      fmin=fmin, n_octaves=5)


def match_cost(y_ref, y, fmin):
    """How different the recording is from the lesson (0 = same, higher = different).
    Subsequence DTW: the recording may match ANY part of the lesson, so a
    slower take, or one that stopped early, still counts as the same piece."""
    X, Y = _chroma(y, fmin), _chroma(y_ref, fmin)
    D, wp = librosa.sequence.dtw(X=X, Y=Y, metric="cosine", subseq=True)
    end = int(np.argmin(D[-1, :]))
    return float(D[-1, end] / len(wp))


def check_recording(path, reference_path=None, instrument="violin", noise_floor_db=None):
    y, sr = load_raw(path)
    checks = []

    def add(name, status, value, message):
        checks.append({"check": name, "status": status, "value": value, "message": message})

    # 1. Clipping: the waveform hit the maximum and got flattened (distortion)
    clip = float(np.mean(np.abs(y) >= 0.999))
    add("clipping", "FAIL" if clip > T["clip_fail"] else "PASS", f"{clip:.2%}",
        "Your recording is distorting. Move about 30 cm further from the mic "
        "or lower the input volume." if clip > T["clip_fail"] else "No distortion")

    # 2. Level: is there enough signal at all?
    peak = float(np.max(np.abs(y)))
    peak_db = 20 * np.log10(peak + 1e-10)
    add("level", "FAIL" if peak_db < T["peak_fail_db"] else "PASS", f"{peak_db:.1f} dBFS",
        "Your recording is too quiet. Move closer to the mic or check the right "
        "mic is selected." if peak_db < T["peak_fail_db"] else "Good level")

    # 3. Dropouts: gaps of digital silence from a slow device
    # One tiny gap (a single dropped audio buffer, ~10 ms) barely affects the
    # analysis, so it only warns; repeated or long gaps damage the take.
    # (Browser testing found single 10 ms gaps when recording starts.)
    drops, longest = _dropouts(y, sr)
    status = ("FAIL" if drops >= 3 or longest > 50 else "WARN" if drops else "PASS")
    add("dropouts", status, f"{drops} gap(s), longest {longest:.0f} ms" if drops else 0, {
        "FAIL": "The audio had glitches (gaps). Close other apps and record again.",
        "WARN": "There was a tiny glitch in the audio; it shouldn't affect your score.",
        "PASS": "No glitches"}[status])

    # Pitch analysis on a resampled, volume-normalised copy (used by checks 5-7)
    y22 = librosa.resample(y, orig_sr=sr, target_sr=SR) if sr != SR else y
    y22 = y22 / (np.max(np.abs(y22)) + 1e-10)
    lowest = LOWEST_HZ[instrument]
    # pYIN at the SAME resolution as note detection (so the "is a note
    # playing" timeline - which the noise check depends on - is unchanged),
    # but without the second, fine YIN pass: this check doesn't need exact
    # pitch. (A coarser pYIN step was tried first and changed verdicts.)
    f0, voiced, prob = librosa.pyin(y22, fmin=lowest * 0.9, fmax=2200.0, sr=SR,
                                    frame_length=2048, hop_length=HOP * 2)
    f0, voiced, prob = (np.repeat(x, 2) for x in (f0, voiced, prob))

    # 4. Signal-to-noise: playing level vs background noise level.
    # Background = the moments where NO note is being played (the pitch
    # tracker knows exactly when that is). Loudness-only guesses failed in
    # tests: on long takes they mistook quiet music for noise, and with
    # traffic they picked brief lulls and missed the noise.
    db = _frame_db(y, sr)
    silent = np.interp(np.linspace(0, 1, len(db)), np.linspace(0, 1, len(voiced)),
                       (~voiced).astype(float)) > 0.5
    # exact digital silence (all-zero samples, e.g. while a device starts up) is
    # not room noise - counting it would make any room look perfectly quiet
    silent &= db > -150
    # Room echo rings on after every note, and in those moments no note is
    # "playing" - so counting them made quiet but echoey rooms look noisy and
    # rejected real takes (found by testing with a realistic recording). Echo
    # only follows notes, so skip the 0.4 s after each one when there's enough
    # other silence; otherwise fall back to all the non-playing moments.
    playing = ~silent & (db > -150)
    k = int(0.4 / 0.0116)
    echo = np.convolve(playing.astype(int), np.ones(k + 1), mode="full")[:len(db)] > 0
    if (silent & ~echo).sum() * 0.0116 >= 0.25:
        silent = silent & ~echo
    if noise_floor_db is None and silent.sum() * 0.0116 < 0.15:
        add("noise", "PASS", "unknown",
            "Couldn't measure background noise (no silent moment in the recording)")
    else:
        floor = noise_floor_db if noise_floor_db is not None else float(np.median(db[silent]))
        snr = float(np.percentile(db, 95) - floor)
        status = "FAIL" if snr < T["snr_fail_db"] else "WARN" if snr < T["snr_warn_db"] else "PASS"
        add("noise", status, f"{snr:.1f} dB", {
            "FAIL": "Too much background noise. Find a quieter room or turn off fans and TV.",
            "WARN": "Some background noise. Scores may be slightly less reliable.",
            "PASS": "Quiet room"}[status])

    # 5. Playing detected: how much of the recording has a clear note
    playing = float(np.mean(voiced))
    add("playing", "FAIL" if playing < T["playing_fail"] else "PASS", f"{playing:.0%}",
        "We could hardly hear any playing. Check the mic and play the piece again."
        if playing < T["playing_fail"] else "Playing detected")

    if playing >= T["playing_fail"]:
        # (A "pitch confidence" check used to be here. It warned on every
        # realistic violin take - bow noise lowers pYIN's probabilities - and
        # never caught anything the noise check didn't, so it was removed.)

        # 7. Tuning: is the whole instrument tuned sharp or flat?
        m = hz_to_midi(f0[voiced])
        offset = float(np.median((m - np.round(m)) * 100))
        bad = abs(offset) > T["tuning_warn_cents"]
        add("tuning", "WARN" if bad else "PASS", f"{offset:+.0f} cents",
            f"Your instrument seems {'flat' if offset < 0 else 'sharp'} by about "
            f"{abs(offset):.0f} cents. Tune it before your next take; we adjusted "
            "for it this time." if bad else "In tune")

    # 8-9. Compared with the teacher's reference recording
    if reference_path:
        y_ref, sr_ref = load_raw(reference_path)
        ratio = (len(y) / sr) / (len(y_ref) / sr_ref)
        lo, hi = T["duration_warn"]
        add("duration", "WARN" if not lo <= ratio <= hi else "PASS", f"{ratio:.2f}x",
            "Your recording is much shorter or longer than the lesson. Did it stop "
            "early?" if not lo <= ratio <= hi else "Expected length")

        if playing >= T["playing_fail"]:
            r22 = librosa.resample(y_ref, orig_sr=sr_ref, target_sr=SR) if sr_ref != SR else y_ref
            r22 = r22 / (np.max(np.abs(r22)) + 1e-10)
            cost = match_cost(r22, y22, fmin=librosa.note_to_hz(librosa.hz_to_note(lowest)))
            add("same piece", "FAIL" if cost > T["match_fail"] else "PASS", f"{cost:.2f}",
                "This doesn't sound like the assigned piece. Check you recorded the "
                "right exercise." if cost > T["match_fail"] else "Matches the lesson")

    statuses = [c["status"] for c in checks]
    overall = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    return {"overall": overall, "checks": checks}


def instrument_check(analysis, steady_min=0.95, note_share_min=0.5):
    """Is this an instrument being played, or speech / other sounds?

    Measured on 10 real violin recordings (31 takes incl. room echo and fan
    noise) against real speech (6 speakers): a violin note holds its pitch
    (steadiness 0.991-1.000; speech 0.66-0.92) and notes fill most of the
    recording (violin 94%+; speech typically 3-6%). Speech has pitch, so
    without this check talking near the microphone was scored as playing.

    analysis: output of pipeline.prepare / pitch.analyse_audio.
    Returns {"ok", "steady", "note_share", "message"}.
    """
    v = np.asarray(analysis["voiced"])
    f0 = np.asarray(analysis["f0"])
    m = np.where(v, hz_to_midi(np.nan_to_num(f0, nan=440.0)), np.nan)
    k, steady, total = 5, 0, 0
    for i in range(k, len(m) - k):
        if not v[i]:
            continue
        w = m[i - k: i + k + 1]
        w = w[~np.isnan(w)]
        if len(w) < 6:
            continue
        total += 1
        steady += abs(m[i] - np.median(w)) < 0.35
    steady_ratio = steady / total if total else 0.0
    secs = len(analysis["y"]) / SR
    note_time = sum(n["end"] - n["start"] for n in analysis["notes"])
    share = note_time / max(secs, 1e-6)
    ok = steady_ratio >= steady_min and share >= note_share_min
    return {"ok": bool(ok), "steady": round(float(steady_ratio), 3), "note_share": round(float(share), 3),
            "message": "Playing detected" if ok else
            "This doesn't sound like your instrument being played - we heard mostly speaking or other "
            "sounds. Please play the piece (and keep talking for after the recording)."}
