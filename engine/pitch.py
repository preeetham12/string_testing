"""
Part 1 - Pitch tracking and note detection (bowed strings, one note at a time).

Steps:
  1. Load audio as mono at 22,050 Hz and normalise the volume.
  2. pYIN pitch tracker -> one pitch value every ~11.6 ms, plus how sure it is.
  3. Convert Hz -> note name + cents (how sharp/flat).
  4. Group frames into notes: a new note starts when the pitch moves to a
     different semitone OR when the bow re-attacks (onset detection).
"""
import numpy as np
import librosa

SR = 22050
HOP = 256  # frame step: 256 samples = 11.6 ms
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def load_audio(path):
    y, _ = librosa.load(path, sr=SR, mono=True)
    return y / (np.max(np.abs(y)) + 1e-9)


def track_pitch(y, fmin=180.0, fmax=2200.0):
    """Two trackers working together:
    - pYIN decides WHETHER a note is sounding and roughly which (robust, but
      its pitch is rounded to 10-cent steps);
    - YIN gives the EXACT pitch (continuous, sub-cent), used only where it
      agrees with pYIN within 50 cents. This is the "trackers must agree" rule.
    """
    # pYIN is the slow part, and it only decides IF a note is sounding, so it
    # runs every 2nd frame (23 ms); YIN, which gives the exact pitch, runs on
    # every frame (11.6 ms). Halves the analysis time with the same accuracy.
    f0_p, voiced, prob = librosa.pyin(y, fmin=fmin, fmax=fmax, sr=SR,
                                      frame_length=2048, hop_length=HOP * 2)
    f0_p, voiced, prob = (np.repeat(a, 2) for a in (f0_p, voiced, prob))
    f0_y = librosa.yin(y, fmin=fmin, fmax=fmax, sr=SR,
                       frame_length=2048, hop_length=HOP)
    n = min(len(f0_p), len(f0_y))
    f0_p, f0_y, voiced, prob = f0_p[:n], f0_y[:n], voiced[:n], prob[:n]
    agree = np.abs(1200 * np.log2(f0_y / np.nan_to_num(f0_p, nan=1.0))) < 50
    f0 = np.where(voiced & agree, f0_y, f0_p)
    times = librosa.times_like(f0, sr=SR, hop_length=HOP)
    return times, f0, voiced, prob


def hz_to_midi(f):
    return 69 + 12 * np.log2(f / 440.0)


def midi_name(m):
    m = int(round(m))
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def detect_onsets(y, min_dip_db=6.0, min_gap=0.12):
    """Times where a new note is attacked (bow change, pluck).

    Spectral onset detectors mistake violin vibrato for new notes, so we use
    loudness instead: a bow change makes the volume dip and rise again.
    """
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=HOP)[0]
    db = librosa.amplitude_to_db(rms + 1e-9, ref=np.max)
    times = librosa.times_like(db, sr=SR, hop_length=HOP)
    win = int(0.08 * SR / HOP)  # look 80 ms either side
    onsets = []
    for i in range(win, len(db) - win):
        if db[i] == db[i - win:i + win].min():  # local loudness minimum
            depth = min(db[i - win:i].max(), db[i:i + win].max()) - db[i]
            if depth >= min_dip_db and (not onsets or times[i] - onsets[-1] > min_gap):
                onsets.append(float(times[i]))
    return np.array(onsets)


def segment_notes(times, f0, voiced, onsets, prob=None, min_dur=0.08, min_conf=0.3,
                  level_db=None, quiet_db=20.0):
    """Turn the frame-by-frame pitch curve into a list of notes."""
    midi = np.where(voiced, hz_to_midi(np.nan_to_num(f0, nan=440.0)), np.nan)
    onset_frames = set(np.searchsorted(times, onsets))
    notes, start = [], None
    for i in range(len(times) + 1):
        is_v = i < len(times) and voiced[i]
        boundary = start is not None and (
            not is_v
            or i in onset_frames
            # a real pitch change, not vibrato wobbling across a boundary
            or abs(midi[i] - np.nanmedian(midi[start:i])) >= 0.6
        )
        if boundary:
            notes.append((start, i))
            start = None
        if is_v and start is None:
            start = i
    notes = [(s, e) for s, e in notes if times[min(e, len(times) - 1)] - times[s] >= min_dur]
    # A "note" the tracker is unsure of AND that is much quieter than the
    # playing is background noise (e.g. a fan). Uncertainty alone isn't enough:
    # bow noise makes the tracker unsure of perfectly real, loud notes (found
    # by testing with a realistic violin recording).
    if level_db is not None and notes:
        loud = [float(np.median(level_db[s:e])) for s, e in notes]
        top = max(loud)
    out = []
    if level_db is not None and notes:
        typical = float(np.median(loud))
    for k_note, (s, e) in enumerate(notes):
        conf = float(np.median(prob[s:e])) if prob is not None else 1.0
        if prob is not None and conf < min_conf:
            if level_db is None or loud[k_note] < top - quiet_db:
                continue  # unsure and quiet: noise, not a note
        # Measured on real violin: false "extra notes" were ~0.1 s long with
        # 4-6% tracker confidence (bow noise, transitions), or 40+ dB below
        # the music. Real notes - even fast ones - are confident and audible.
        if times[min(e, len(times) - 1)] - times[s] < 0.12 and conf < 0.4:
            continue
        if level_db is not None and loud[k_note] < typical - 30:
            continue
        seg = midi[s:e]
        # Use the stable middle 60% of the note (skip the attack and release)
        k = len(seg)
        core = seg[int(k * 0.2): max(int(k * 0.8), int(k * 0.2) + 1)]
        m = float(np.nanmedian(core))
        nearest = round(m)
        out.append({
            "start": round(float(times[s]), 3),
            "end": round(float(times[min(e, len(times) - 1)]), 3),
            "note": midi_name(nearest),
            "midi": nearest,
            "cents": round((m - nearest) * 100, 1),  # + sharp, - flat
            "hz": round(float(440 * 2 ** ((m - 69) / 12)), 1),
            "pitch": round(m, 3),  # continuous pitch, used for tuning correction
        })
    return out


def analyse(path):
    return analyse_audio(load_audio(path))


def analyse_audio(y, fmin=180.0, onset_db=6.0):
    """Same as analyse(), for audio already in memory (e.g. after cleaning).

    onset_db: how deep a loudness dip must be to count as a new bow stroke.
    6 dB (careful) for teachers: it never invents notes. 3 dB (sensitive) for
    students: catches bow changes blurred by room echo; the extra splits it
    makes are corrected against the lesson in compare.py.
    """
    y = y / (np.max(np.abs(y)) + 1e-9)
    times, f0, voiced, prob = track_pitch(y, fmin=fmin)
    onsets = detect_onsets(y, min_dip_db=onset_db)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    level_db = 20 * np.log10(np.pad(rms, (0, max(0, len(times) - len(rms))))[:len(times)] + 1e-10)
    notes = segment_notes(times, f0, voiced, onsets, prob, level_db=level_db)
    return {"y": y, "times": times, "f0": f0, "voiced": voiced,
            "prob": prob, "onsets": onsets, "notes": notes}
