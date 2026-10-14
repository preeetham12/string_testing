"""
Latency trend on real violin: the full server path (quality gate -> clean ->
notes -> compare -> score -> review) for takes of 15-120 s, built by joining
Bach10 violin recordings. Each length is timed twice; the faster run is kept
(the machine may be busy with other work).

    python -m tests.latency_trend /path/to/Bach10
"""
import glob
import json
import sys
import time
import warnings

import numpy as np
import soundfile as sf

warnings.filterwarnings("ignore")
from engine.compare import compare
from engine.feedback import write_feedback
from engine.pipeline import prepare
from engine.quality import check_recording
from engine.score import score

root = sys.argv[1] if len(sys.argv) > 1 else "Bach10"
audio = np.concatenate([sf.read(f)[0] for f in sorted(glob.glob(f"{root}/*/*-violin.wav"))])
SR = 44100
rows = []
for secs in (15, 30, 60, 90, 120):
    y = audio[: secs * SR]
    sf.write("/tmp/lat_teacher.wav", y, SR, subtype="PCM_16")
    sf.write("/tmp/lat_student.wav", y * 0.9, SR, subtype="PCM_16")
    teacher = prepare("/tmp/lat_teacher.wav")
    compare(teacher, prepare("/tmp/lat_student.wav", student=True))  # fill teacher caches, like a 2nd take
    best = None
    for _ in range(2):
        parts = {}
        t0 = time.time(); check_recording("/tmp/lat_student.wav", reference_path="/tmp/lat_teacher.wav"); parts["quality gate"] = time.time() - t0
        t0 = time.time(); s = prepare("/tmp/lat_student.wav", student=True); parts["clean + notes"] = time.time() - t0
        t0 = time.time(); c = compare(teacher, s); r = score(c, teacher["notes"]); write_feedback(r, c); parts["compare + review"] = time.time() - t0
        if best is None or sum(parts.values()) < sum(best.values()):
            best = parts
    total = sum(best.values())
    rows.append({"secs": secs, "total": total, **best})
    print(f"{secs:4d} s take: {total:5.2f} s total ({total / secs * 60:4.1f} s per minute) | "
          + " | ".join(f"{k} {v:.2f}" for k, v in best.items()), flush=True)
json.dump(rows, open("data/latency_trend.json", "w"), indent=1)
