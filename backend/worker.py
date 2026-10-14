"""
Part 5 - Background worker.

Takes jobs from the database queue and runs the Parts 1-4 engine:
  process_reference: quality check (teacher) -> clean -> notes -> sections
  process_attempt:   quality check vs lesson -> clean -> notes -> compare
                     -> score -> written review
The API never waits for this; the app polls the attempt status.
Jobs survive a server restart: anything left 'running' is re-queued at start.
"""
import json
import threading
import time
import traceback
import warnings

import numpy as np
import soundfile as sf

warnings.filterwarnings("ignore")
from backend.db import Assignment, Attempt, Class, Job, Recording, SessionLocal
from engine.compare import compare
from engine.feedback import write_feedback
from engine.pipeline import prepare, suggest_splits
from engine.pitch import SR, load_audio
from engine.quality import check_recording, instrument_check
from engine.score import find_sections, score, teacher_section

_claim_lock = threading.Lock()
_stop = threading.Event()
_teacher_cache = {}


def jsonable(x):
    """numpy values -> plain Python so they can be stored as JSON."""
    return json.loads(json.dumps(x, default=lambda o: o.item() if hasattr(o, "item")
                                 else o.tolist() if hasattr(o, "tolist") else str(o)))


def enqueue(db, kind, target_id):
    db.add(Job(kind=kind, target_id=target_id))
    db.commit()


def _teacher(a: Assignment):
    """The teacher's prepared recording (cleaned audio + notes), cached."""
    if a.id not in _teacher_cache:
        y = load_audio(a.analysis_path)
        _teacher_cache[a.id] = {"y": y, "notes": a.notes}
    _teacher_cache[a.id]["notes"] = a.notes  # always the latest (teacher may edit)
    return _teacher_cache[a.id]


def forget_teacher(assignment_id):
    """Drop the cached reference after the teacher edits its notes."""
    _teacher_cache.pop(assignment_id, None)


def process_reference(db, assignment_id):
    a = db.get(Assignment, assignment_id)
    cls = db.get(Class, a.class_id)
    path = db.get(Recording, a.reference_id).path
    qc = check_recording(path, instrument=cls.instrument)
    a.qc_report = jsonable(qc)
    if qc["overall"] == "FAIL":
        a.status = "reference_failed"
        db.commit()
        return
    t = prepare(path, cls.instrument)
    inst = instrument_check(t)
    if not inst["ok"]:  # a reference must be the piece being played
        qc["checks"].append({"check": "instrument", "status": "FAIL", "value": inst["steady"],
                             "message": inst["message"]})
        qc["overall"] = "FAIL"
        a.qc_report = jsonable(qc)
        a.status = "reference_failed"
        db.commit()
        return
    a.analysis_path = path.replace(".wav", "_clean.wav")
    sf.write(a.analysis_path, t["y"], SR, subtype="FLOAT")
    notes = t["notes"]
    for sg in suggest_splits(t):  # notes that may be two notes merged by room echo
        notes[sg["index"]]["suggest_split_at"] = sg["at"]
    a.notes = jsonable(notes)
    a.ref_offset = float(t.get("trim_offset", 0.0))
    a.sections = jsonable(find_sections(t["notes"]))
    a.status = "ready"  # teacher reviews sections, then publishes
    db.commit()


def _not_counted(db, at, t0, check, message):
    """Reject a take without using up an attempt; record why."""
    qc = at.qc_report or {"overall": "PASS", "checks": []}
    qc["checks"] = qc["checks"] + [{"check": check, "status": "FAIL", "value": None, "message": message}]
    qc["overall"] = "FAIL"
    at.qc_report = jsonable(qc)
    at.status, at.counted = "qc_failed", False
    at.feedback = message
    at.seconds = round(time.time() - t0, 2)
    db.commit()


def process_attempt(db, attempt_id):
    t0 = time.time()
    at = db.get(Attempt, attempt_id)
    a = db.get(Assignment, at.assignment_id)
    cls = db.get(Class, a.class_id)
    path = db.get(Recording, at.recording_id).path
    at.status = "analysing"
    db.commit()

    # Section practice: compare with just that slice of the teacher's recording
    teacher = _teacher(a)
    sections = a.sections
    ref_path = db.get(Recording, a.reference_id).path
    if at.section_id:
        sec = next(s for s in a.sections if s["id"] == at.section_id)
        teacher = teacher_section(teacher, sec)
        sections = [{**sec, "first": 0, "last": sec["last"] - sec["first"]}]
        ref_path = None  # duration/same-piece checks only make sense for full takes

    qc = check_recording(path, reference_path=ref_path, instrument=cls.instrument)
    at.qc_report = jsonable(qc)
    if qc["overall"] == "FAIL":
        # If it isn't the instrument at all (e.g. talking), say THAT: the older
        # checks would otherwise blame "background noise" or the wrong piece.
        try:
            inst = instrument_check(prepare(path, cls.instrument, student=True))
        except Exception:
            inst = {"ok": True}
        if not inst["ok"]:
            _not_counted(db, at, t0, "instrument", inst["message"])
            return
        at.status, at.counted = "qc_failed", False  # re-record, attempt not used up
        at.feedback = "\n".join(c["message"] for c in qc["checks"] if c["status"] == "FAIL")
        at.seconds = round(time.time() - t0, 2)
        db.commit()
        return

    student = prepare(path, cls.instrument, student=True)  # sensitive notes, pauses cut
    # Layer 1: is this the instrument being played at all? (Speech has pitch:
    # without this, talking near the microphone was scored as playing.)
    inst = instrument_check(student)
    if not inst["ok"]:
        _not_counted(db, at, t0, "instrument", inst["message"])
        return
    comp = compare(teacher, student)
    # Layer 2: is it recognisably this lesson? If under a third of the
    # lesson's notes are right (octave slips count as right), a score would
    # be meaningless: ask the student to check the piece instead. (A quarter
    # let a well-played different piece through: it shared one pitch.)
    t_notes = [r for r in comp["notes"] if r.get("t_index") is not None]
    right = sum(r["status"] == "correct" or any(f.startswith("octave") for f in r.get("flags", []))
                for r in t_notes)
    if t_notes and right < len(t_notes) / 3:
        _not_counted(db, at, t0, "lesson", "We couldn't recognise this lesson in your recording. "
                     "Check you're playing the right piece (or section), then record again.")
        return
    res = score(comp, teacher["notes"], level=a.level, pass_mark=a.pass_mark,
                sections=sections)
    prev = (db.query(Attempt)
            .filter(Attempt.assignment_id == a.id, Attempt.student_id == at.student_id,
                    Attempt.section_id.is_(None) if at.section_id is None else Attempt.section_id == at.section_id,
                    Attempt.status == "done", Attempt.id < at.id)
            .order_by(Attempt.id.desc()).first())
    fb = write_feedback(res, comp, previous=prev.result if prev else None)
    warns = [c["message"] for c in qc["checks"] if c["status"] == "WARN"]
    if warns:
        fb += "\n\nRecording notes: " + " ".join(warns)

    for s in res["sections"]:  # keep the per-note rows, drop nothing the app needs
        s["rows"] = [{k: v for k, v in r.items()} for r in s["rows"]]
    comp.pop("dtw_path", None)
    at.result = jsonable({**res, "tempo_ratio": comp["tempo_ratio"],
                          "tuning_offset_cents": comp["tuning_offset_cents"],
                          "counts": comp["counts"], "s_offset": comp["s_offset"],
                          "false_start_notes": comp["false_start_notes"],
                          "pauses": student.get("pauses", [])})
    at.score, at.feedback, at.status = res["overall"], fb, "done"
    at.seconds = round(time.time() - t0, 2)
    db.commit()


HANDLERS = {"process_reference": process_reference, "process_attempt": process_attempt}


def _claim(db):
    with _claim_lock:
        job = db.query(Job).filter(Job.status == "queued").order_by(Job.id).first()
        if job:
            job.status = "running"
            db.commit()
        return job


def run_one():
    """Process one queued job if there is one. Returns True if it did work."""
    db = SessionLocal()
    try:
        job = _claim(db)
        if not job:
            return False
        try:
            HANDLERS[job.kind](db, job.target_id)
            job.status = "done"
        except Exception:
            db.rollback()
            job = db.get(Job, job.id)
            job.status, job.error = "failed", traceback.format_exc()[-2000:]
            if job.kind == "process_attempt":
                at = db.get(Attempt, job.target_id)
                at.status, at.counted = "error", False
            else:
                db.get(Assignment, job.target_id).status = "reference_failed"
        db.commit()
        return True
    finally:
        db.close()


def recover():
    """After a restart: jobs that were 'running' when the server stopped go back
    in the queue, so no recording is ever silently lost."""
    db = SessionLocal()
    n = db.query(Job).filter(Job.status == "running").update({"status": "queued"})
    db.commit()
    db.close()
    return n


def _loop():
    while not _stop.is_set():
        if not run_one():
            time.sleep(0.1)


def start_workers(n=2):
    recover()
    _stop.clear()
    threads = [threading.Thread(target=_loop, daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
    return threads


def stop_workers():
    _stop.set()
