"""
Part 5 - The API.

Run:  uvicorn backend.api:app --port 8000
Docs: http://localhost:8000/docs  (interactive, generated automatically)

Every endpoint checks WHO is asking:
  - teachers manage only their own classes and assignments
  - students see only published assignments of classes they're in, and only
    their own attempts
"""
import hashlib
import os
import shutil
import threading
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import List, Optional

import soundfile as sf
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import worker
from backend.auth import (current_user, get_db, hash_password, new_token,
                          teacher_only, verify_password)
from backend.db import Token
from backend.db import (DATA_DIR, Assignment, Attempt, Class, Comment, Enrollment,
                        Recording, Upload, User, init_db)

CHUNK_MAX = 5 * 1024 * 1024        # 5 MB per chunk
FILE_MAX = 200 * 1024 * 1024       # 200 MB per recording (~10 min lossless)
INSTRUMENTS = {"violin", "viola", "cello", "guitar"}
LEVELS = {"beginner", "intermediate", "advanced"}

# One lock per (assignment, student): "count attempts, then save" must happen as
# one step, or two taps on Submit at the same moment both get through (found by
# testing). Production with several server processes: use a PostgreSQL row lock
# (SELECT ... FOR UPDATE on the enrollment row) for the same guarantee.
_attempt_locks = defaultdict(threading.Lock)
_attempt_locks_guard = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    init_db()
    if os.environ.get("START_WORKERS", "1") == "1":
        worker.start_workers(int(os.environ.get("WORKERS", "2")))
    yield
    worker.stop_workers()


app = FastAPI(title="StringCoach API", version="0.5", lifespan=lifespan)


# ---------- request bodies ----------
class Register(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=8)
    role: str = Field(pattern="^(teacher|student)$")


class Login(BaseModel):
    email: str
    password: str


class NewClass(BaseModel):
    name: str
    instrument: str


class Enroll(BaseModel):
    email: str


class NewUpload(BaseModel):
    total_chunks: int = Field(ge=1, le=1000)
    total_bytes: int = Field(ge=1, le=FILE_MAX)
    sha256: str = Field(min_length=64, max_length=64)


class NewAssignment(BaseModel):
    class_id: int
    title: str
    recording_id: int
    level: str = "intermediate"
    pass_mark: int = Field(90, ge=50, le=100)
    max_attempts: int = Field(10, ge=1, le=100)


class SectionEdit(BaseModel):
    first: int  # first note number (1-based, as teachers see it)
    last: int


class Sections(BaseModel):
    sections: List[SectionEdit]


class NoteEdit(BaseModel):
    index: int                     # note number, 1-based as teachers see it
    midi: Optional[int] = Field(None, ge=24, le=108)   # corrected pitch
    delete: bool = False           # remove a stray note (a bump, an open string)
    split: bool = False            # two notes heard as one (room echo merged them)
    at: Optional[float] = None     # where to split, in seconds (default: the
                                   # app's suggestion, else the middle)


class NoteEdits(BaseModel):
    edits: List[NoteEdit] = Field(min_length=1)


class NewAttempt(BaseModel):
    recording_id: int
    section_id: Optional[int] = None  # None = full piece


class NewComment(BaseModel):
    body: str = Field(min_length=1)
    score_override: Optional[int] = Field(None, ge=0, le=100)


# ---------- helpers ----------
def _own_class(db, class_id, user):
    c = db.get(Class, class_id)
    if not c or c.teacher_id != user.id:
        raise HTTPException(404, "Class not found")
    return c


def _enrolled(db, class_id, user):
    return db.query(Enrollment).filter_by(class_id=class_id, student_id=user.id).first()


def _visible_assignment(db, assignment_id, user):
    """Teacher of the class, or an enrolled student once it's published.
    Anyone else gets 404 (not 403), so IDs of other classes aren't revealed."""
    a = db.get(Assignment, assignment_id)
    if a:
        c = db.get(Class, a.class_id)
        if user.role == "teacher" and c.teacher_id == user.id:
            return a, c
        if user.role == "student" and a.status == "published" and _enrolled(db, c.id, user):
            return a, c
    raise HTTPException(404, "Assignment not found")


def _visible_attempt(db, attempt_id, user):
    at = db.get(Attempt, attempt_id)
    if at:
        a = db.get(Assignment, at.assignment_id)
        c = db.get(Class, a.class_id)
        if (user.role == "student" and at.student_id == user.id) or \
           (user.role == "teacher" and c.teacher_id == user.id):
            return at, a, c
    raise HTTPException(404, "Attempt not found")


def _assignment_out(a, teacher_view):
    out = {"id": a.id, "class_id": a.class_id, "title": a.title, "status": a.status,
           "level": a.level, "pass_mark": a.pass_mark, "max_attempts": a.max_attempts,
           "sections": a.sections, "reference_recording_id": a.reference_id,
           # the lesson's notes: students need them for the ghost line
           "notes": a.notes, "ref_offset": a.ref_offset or 0.0}
    if teacher_view:
        out["qc_report"] = a.qc_report
    return out


def _attempt_out(db, at):
    comments = db.query(Comment).filter_by(attempt_id=at.id).order_by(Comment.id).all()
    override = next((c.score_override for c in reversed(comments)
                     if c.score_override is not None), None)
    return {"id": at.id, "assignment_id": at.assignment_id, "student_id": at.student_id,
            "recording_id": at.recording_id,
            "section_id": at.section_id, "status": at.status, "counted": at.counted,
            "score": at.score, "teacher_score": override, "feedback": at.feedback,
            "result": at.result, "qc_report": at.qc_report, "seconds": at.seconds,
            "created": at.created.isoformat(),
            "comments": [{"body": c.body, "score_override": c.score_override,
                          "created": c.created.isoformat()} for c in comments]}


# ---------- the web app (Part 6) ----------
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
if os.path.isdir(WEB_DIR):
    app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")


# NUERiQ String Testing: results dashboard API (/tester-api/...)
from backend.tester_results import router as tester_router  # noqa: E402
app.include_router(tester_router)

# NUERiQ String Testing page: a self-contained browser app (tester/), served at /tester/
TESTER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tester")
if os.path.isdir(TESTER_DIR):
    app.mount("/tester", StaticFiles(directory=TESTER_DIR, html=True), name="tester")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


# ---------- audio playback ----------
@app.get("/recordings/{recording_id}/audio")
def recording_audio(recording_id: int, token: Optional[str] = None,
                    authorization: str = Header(None), db=Depends(get_db)):
    """Play a recording. An <audio> tag can't send headers, so the token may also
    come as ?token=. Allowed: the owner; the teacher of the class it belongs to;
    an enrolled student, for the reference of a published assignment."""
    raw = token or (authorization or "").removeprefix("Bearer ")
    tok = db.get(Token, raw) if raw else None
    if not tok:
        raise HTTPException(401, "Please log in")
    user = db.get(User, tok.user_id)
    rec = db.get(Recording, recording_id)
    allowed = False
    if rec:
        if rec.owner_id == user.id:
            allowed = True
        for a in db.query(Assignment).filter_by(reference_id=rec.id):
            c = db.get(Class, a.class_id)
            if (user.role == "teacher" and c.teacher_id == user.id) or (
                    user.role == "student" and a.status == "published" and _enrolled(db, c.id, user)):
                allowed = True
        at = db.query(Attempt).filter_by(recording_id=rec.id).first()
        if at and user.role == "teacher":
            c = db.get(Class, db.get(Assignment, at.assignment_id).class_id)
            allowed = allowed or c.teacher_id == user.id
    if not allowed:
        raise HTTPException(404, "Recording not found")
    return FileResponse(rec.path, media_type="audio/wav")


# ---------- health ----------
@app.get("/health")
def health():
    return {"ok": True}


# ---------- auth ----------
@app.post("/auth/register", status_code=201)
def register(body: Register, db=Depends(get_db)):
    if db.query(User).filter_by(email=body.email.lower()).first():
        raise HTTPException(409, "That email is already registered")
    u = User(name=body.name, email=body.email.lower(), role=body.role,
             password_hash=hash_password(body.password))
    db.add(u)
    db.commit()
    return {"id": u.id, "name": u.name, "role": u.role}


@app.post("/auth/login")
def login(body: Login, db=Depends(get_db)):
    u = db.query(User).filter_by(email=body.email.lower()).first()
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Wrong email or password")  # same message either way
    return {"token": new_token(db, u.id), "user": {"id": u.id, "name": u.name, "role": u.role}}


@app.get("/me")
def me(user=Depends(current_user)):
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role}


# ---------- classes ----------
@app.post("/classes", status_code=201)
def create_class(body: NewClass, user=Depends(teacher_only), db=Depends(get_db)):
    if body.instrument not in INSTRUMENTS:
        raise HTTPException(422, f"Instrument must be one of {sorted(INSTRUMENTS)}")
    c = Class(teacher_id=user.id, name=body.name, instrument=body.instrument)
    db.add(c)
    db.commit()
    return {"id": c.id, "name": c.name, "instrument": c.instrument}


@app.get("/classes")
def list_classes(user=Depends(current_user), db=Depends(get_db)):
    if user.role == "teacher":
        cs = db.query(Class).filter_by(teacher_id=user.id).all()
    else:
        ids = [e.class_id for e in db.query(Enrollment).filter_by(student_id=user.id)]
        cs = db.query(Class).filter(Class.id.in_(ids)).all() if ids else []
    return [{"id": c.id, "name": c.name, "instrument": c.instrument} for c in cs]


@app.post("/classes/{class_id}/students", status_code=201)
def enroll(class_id: int, body: Enroll, user=Depends(teacher_only), db=Depends(get_db)):
    c = _own_class(db, class_id, user)
    s = db.query(User).filter_by(email=body.email.lower(), role="student").first()
    if not s:
        raise HTTPException(404, "No student account with that email")
    if not _enrolled(db, c.id, s):
        db.add(Enrollment(class_id=c.id, student_id=s.id))
        db.commit()
    return {"class_id": c.id, "student_id": s.id}


@app.get("/classes/{class_id}/students")
def class_students(class_id: int, user=Depends(teacher_only), db=Depends(get_db)):
    c = _own_class(db, class_id, user)
    ids = [e.student_id for e in db.query(Enrollment).filter_by(class_id=c.id)]
    return [{"id": u.id, "name": u.name} for u in db.query(User).filter(User.id.in_(ids))]


@app.get("/classes/{class_id}/assignments")
def class_assignments(class_id: int, user=Depends(current_user), db=Depends(get_db)):
    c = db.get(Class, class_id)
    if c and user.role == "teacher" and c.teacher_id == user.id:
        rows = db.query(Assignment).filter_by(class_id=c.id).all()
        return [_assignment_out(a, True) for a in rows]
    if c and user.role == "student" and _enrolled(db, c.id, user):
        rows = db.query(Assignment).filter_by(class_id=c.id, status="published").all()
        return [_assignment_out(a, False) for a in rows]
    raise HTTPException(404, "Class not found")


# ---------- chunked uploads (resumable; production: S3 multipart) ----------
def _upload_dir(uid):
    return os.path.join(DATA_DIR, "uploads", uid)


@app.post("/uploads", status_code=201)
def start_upload(body: NewUpload, user=Depends(current_user), db=Depends(get_db)):
    up = Upload(id=uuid.uuid4().hex, owner_id=user.id, total_chunks=body.total_chunks,
                total_bytes=body.total_bytes, sha256=body.sha256.lower(), received=[])
    db.add(up)
    db.commit()
    os.makedirs(_upload_dir(up.id), exist_ok=True)
    return {"upload_id": up.id, "chunk_max_bytes": CHUNK_MAX}


def _my_upload(db, upload_id, user):
    up = db.get(Upload, upload_id)
    if not up or up.owner_id != user.id:
        raise HTTPException(404, "Upload not found")
    return up


@app.put("/uploads/{upload_id}/chunks/{n}")
async def put_chunk(upload_id: str, n: int, request: Request,
                    x_chunk_sha256: str = Header(...), user=Depends(current_user),
                    db=Depends(get_db)):
    up = _my_upload(db, upload_id, user)
    if up.status != "uploading":
        raise HTTPException(409, "Upload already completed")
    if not 0 <= n < up.total_chunks:
        raise HTTPException(422, "Chunk number out of range")
    data = await request.body()
    if len(data) > CHUNK_MAX:
        raise HTTPException(413, "Chunk too large")
    if hashlib.sha256(data).hexdigest() != x_chunk_sha256.lower():
        raise HTTPException(422, "Chunk damaged in transit (checksum mismatch) - send it again")
    with open(os.path.join(_upload_dir(up.id), f"{n:05d}"), "wb") as f:
        f.write(data)
    up.received = sorted(set(up.received) | {n})
    db.commit()
    return {"received": len(up.received), "total": up.total_chunks}


@app.get("/uploads/{upload_id}")
def upload_status(upload_id: str, user=Depends(current_user), db=Depends(get_db)):
    """What's still missing - lets the app resume after a dropped connection."""
    up = _my_upload(db, upload_id, user)
    missing = [k for k in range(up.total_chunks) if k not in up.received]
    return {"status": up.status, "received": up.received, "missing": missing,
            "recording_id": up.recording_id}


@app.post("/uploads/{upload_id}/complete")
def complete_upload(upload_id: str, user=Depends(current_user), db=Depends(get_db)):
    up = _my_upload(db, upload_id, user)
    if up.status == "complete":
        return {"recording_id": up.recording_id}  # safe to call twice
    missing = [k for k in range(up.total_chunks) if k not in up.received]
    if missing:
        raise HTTPException(409, {"message": "Some chunks are missing", "missing": missing})
    os.makedirs(os.path.join(DATA_DIR, "recordings"), exist_ok=True)
    path = os.path.join(DATA_DIR, "recordings", f"{up.id}.wav")
    h = hashlib.sha256()
    with open(path, "wb") as out:
        for k in range(up.total_chunks):
            with open(os.path.join(_upload_dir(up.id), f"{k:05d}"), "rb") as f:
                data = f.read()
            h.update(data)
            out.write(data)
    size = os.path.getsize(path)
    if size != up.total_bytes or h.hexdigest() != up.sha256:
        os.remove(path)
        raise HTTPException(422, "The assembled file doesn't match its checksum - upload again")
    try:
        sf.info(path)  # is it really an audio file?
    except Exception:
        os.remove(path)
        raise HTTPException(422, "That file isn't a readable audio recording")
    rec = Recording(owner_id=user.id, path=path, bytes=size)
    db.add(rec)
    db.commit()
    up.status, up.recording_id = "complete", rec.id
    db.commit()
    shutil.rmtree(_upload_dir(up.id), ignore_errors=True)
    return {"recording_id": rec.id}


# ---------- assignments ----------
@app.post("/assignments", status_code=201)
def create_assignment(body: NewAssignment, user=Depends(teacher_only), db=Depends(get_db)):
    c = _own_class(db, body.class_id, user)
    rec = db.get(Recording, body.recording_id)
    if not rec or rec.owner_id != user.id:
        raise HTTPException(404, "Recording not found")
    if body.level not in LEVELS:
        raise HTTPException(422, f"Level must be one of {sorted(LEVELS)}")
    a = Assignment(class_id=c.id, title=body.title, reference_id=rec.id, level=body.level,
                   pass_mark=body.pass_mark, max_attempts=body.max_attempts)
    db.add(a)
    db.commit()
    worker.enqueue(db, "process_reference", a.id)
    return _assignment_out(a, True)


@app.get("/assignments/{assignment_id}")
def get_assignment(assignment_id: int, user=Depends(current_user), db=Depends(get_db)):
    a, c = _visible_assignment(db, assignment_id, user)
    return _assignment_out(a, user.role == "teacher")


@app.put("/assignments/{assignment_id}/sections")
def edit_sections(assignment_id: int, body: Sections, user=Depends(teacher_only),
                  db=Depends(get_db)):
    """Teacher adjusts phrase boundaries. Sections must cover every note, in order."""
    a, _ = _visible_assignment(db, assignment_id, user)
    if a.status == "published":
        # like notes: everyone must be marked on the same sections
        raise HTTPException(409, "Sections can only be changed before the lesson is published")
    if a.status != "ready":
        raise HTTPException(409, "The reference recording is still being analysed")
    n, expect, out = len(a.notes), 1, []
    for k, s in enumerate(body.sections, 1):
        if s.first != expect or s.last < s.first or s.last > n:
            raise HTTPException(422, f"Sections must cover notes 1-{n} in order, without gaps")
        out.append({"id": k, "first": s.first - 1, "last": s.last - 1,
                    "t_start": a.notes[s.first - 1]["start"], "t_end": a.notes[s.last - 1]["end"]})
        expect = s.last + 1
    if expect != n + 1:
        raise HTTPException(422, f"Sections must cover notes 1-{n} in order, without gaps")
    a.sections = out
    db.commit()
    return _assignment_out(a, True)


@app.put("/assignments/{assignment_id}/notes")
def edit_notes(assignment_id: int, body: NoteEdits, user=Depends(teacher_only),
               db=Depends(get_db)):
    """Teacher corrects the detected notes before publishing: fix a note's pitch
    (detection error, or a slip in the teacher's own take) or delete a stray
    one. Locked after publishing, so students are never scored against a lesson
    that changed under them. Sections are re-detected afterwards."""
    from engine.pitch import midi_name
    from engine.pipeline import split_note
    from engine.score import find_sections
    a, _ = _visible_assignment(db, assignment_id, user)
    if a.status != "ready":
        raise HTTPException(409, "Notes can only be changed before the lesson is published")
    notes = [dict(n) for n in a.notes]
    splits = [e for e in body.edits if e.split]
    if splits:
        # splitting renumbers the notes after it, so it is done on its own
        if len(body.edits) != 1:
            raise HTTPException(422, "Split one note at a time")
        e = splits[0]
        if not 1 <= e.index <= len(notes):
            raise HTTPException(422, f"There is no note {e.index}")
        at = e.at if e.at is not None else notes[e.index - 1].get("suggest_split_at")
        try:
            notes = split_note(notes, e.index - 1, at)
        except ValueError as err:
            raise HTTPException(422, str(err))
        a.notes = worker.jsonable(notes)
        a.sections = worker.jsonable(find_sections(notes))
        db.commit()
        worker.forget_teacher(a.id)
        return _assignment_out(a, True)
    deleted = set()
    for e in body.edits:
        if not 1 <= e.index <= len(notes):
            raise HTTPException(422, f"There is no note {e.index}")
        n = notes[e.index - 1]
        if e.delete:
            deleted.add(e.index - 1)
        elif e.midi is not None:
            # keep the teacher's played intonation, move it to the right note
            n.update(midi=e.midi, note=midi_name(e.midi), pitch=round(e.midi + n["cents"] / 100, 3),
                     hz=round(440 * 2 ** ((e.midi + n["cents"] / 100 - 69) / 12), 1), corrected=True)
        else:
            raise HTTPException(422, "Each edit needs a new pitch, delete: true or split: true")
    # The note after a deleted one: the teacher's timing there includes the
    # accident, so that note's rhythm isn't judged (found by testing: students
    # playing it correctly were marked as rushing).
    for k in deleted:
        if k + 1 < len(notes) and k + 1 not in deleted:
            notes[k + 1]["skip_timing"] = True
    notes = [n for k, n in enumerate(notes) if k not in deleted]
    if len(notes) < 2:
        raise HTTPException(422, "A lesson needs at least 2 notes")
    a.notes = notes
    a.sections = worker.jsonable(find_sections(notes))
    db.commit()
    worker.forget_teacher(a.id)
    return _assignment_out(a, True)


@app.get("/assignments/{assignment_id}/class_summary")
def class_summary(assignment_id: int, user=Depends(teacher_only), db=Depends(get_db)):
    """Class heatmap data, from each student's LATEST finished full take:
    how each section is going, and which notes the class gets wrong most."""
    a, c = _visible_assignment(db, assignment_id, user)
    latest = {}
    for at in db.query(Attempt).filter_by(assignment_id=a.id, section_id=None, status="done") \
                                .order_by(Attempt.id):
        latest[at.student_id] = at
    takes = list(latest.values())
    sections = []
    for s in a.sections or []:
        scores = [next(x["score"] for x in t.result["sections"] if x["id"] == s["id"]) for t in takes]
        sections.append({"id": s["id"], "notes": f"{s['first'] + 1}-{s['last'] + 1}",
                         "students": len(scores),
                         "average": round(sum(scores) / len(scores)) if scores else None,
                         "passed": sum(sc >= a.pass_mark for sc in scores)})
    trouble = {}
    for t in takes:
        for s in t.result["sections"]:
            for r in s["rows"]:
                if r.get("t_index") is None:
                    continue
                problem = (r["status"] if r["status"] != "correct"
                           else r["flags"][0] if r["flags"] else None)
                if problem:
                    k = r["t_index"]
                    trouble.setdefault(k, {"note_number": k + 1, "note": a.notes[k]["note"],
                                           "students": set(), "problems": {}})
                    trouble[k]["students"].add(t.student_id)
                    trouble[k]["problems"][problem] = trouble[k]["problems"].get(problem, 0) + 1
    hardest = sorted(({**v, "students": len(v["students"])} for v in trouble.values()),
                     key=lambda v: (-v["students"], v["note_number"]))
    return {"students_with_takes": len(takes), "sections": sections, "hardest_notes": hardest[:5]}


@app.post("/assignments/{assignment_id}/publish")
def publish(assignment_id: int, user=Depends(teacher_only), db=Depends(get_db)):
    a, _ = _visible_assignment(db, assignment_id, user)
    if a.status != "ready":
        raise HTTPException(409, f"Can't publish while the assignment is '{a.status}'")
    a.status = "published"
    db.commit()
    return _assignment_out(a, True)


# ---------- attempts ----------
@app.post("/assignments/{assignment_id}/attempts", status_code=201)
def submit_attempt(assignment_id: int, body: NewAttempt, user=Depends(current_user),
                   db=Depends(get_db)):
    if user.role != "student":
        raise HTTPException(403, "Only students submit attempts")
    a, c = _visible_assignment(db, assignment_id, user)
    rec = db.get(Recording, body.recording_id)
    if not rec or rec.owner_id != user.id:
        raise HTTPException(404, "Recording not found")
    if body.section_id is not None and body.section_id not in [s["id"] for s in a.sections]:
        raise HTTPException(422, "No such section")
    with _attempt_locks_guard:
        lock = _attempt_locks[(a.id, user.id)]
    with lock:
        if body.section_id is None:  # only full-piece takes count; practice is unlimited
            used = db.query(Attempt).filter_by(assignment_id=a.id, student_id=user.id,
                                               section_id=None, counted=True).count()
            if used >= a.max_attempts:
                raise HTTPException(409, f"All {a.max_attempts} attempts have been used")
        at = Attempt(assignment_id=a.id, student_id=user.id, recording_id=rec.id,
                     section_id=body.section_id)
        db.add(at)
        db.commit()
    worker.enqueue(db, "process_attempt", at.id)
    return {"id": at.id, "status": at.status}


@app.get("/attempts/{attempt_id}")
def get_attempt(attempt_id: int, user=Depends(current_user), db=Depends(get_db)):
    at, _, _ = _visible_attempt(db, attempt_id, user)
    return _attempt_out(db, at)


@app.get("/assignments/{assignment_id}/attempts")
def list_attempts(assignment_id: int, user=Depends(current_user), db=Depends(get_db)):
    a, c = _visible_assignment(db, assignment_id, user)
    q = db.query(Attempt).filter_by(assignment_id=a.id)
    if user.role == "student":
        q = q.filter_by(student_id=user.id)
    return [_attempt_out(db, at) for at in q.order_by(Attempt.id)]


@app.post("/attempts/{attempt_id}/comments", status_code=201)
def comment(attempt_id: int, body: NewComment, user=Depends(teacher_only), db=Depends(get_db)):
    at, _, _ = _visible_attempt(db, attempt_id, user)
    db.add(Comment(attempt_id=at.id, teacher_id=user.id, body=body.body,
                   score_override=body.score_override))
    db.commit()
    return _attempt_out(db, at)


@app.get("/assignments/{assignment_id}/progress")
def progress(assignment_id: int, user=Depends(teacher_only), db=Depends(get_db)):
    """Teacher dashboard data: one row per student."""
    a, c = _visible_assignment(db, assignment_id, user)
    ids = [e.student_id for e in db.query(Enrollment).filter_by(class_id=c.id)]
    rows = []
    for s in db.query(User).filter(User.id.in_(ids)).order_by(User.name):
        ats = db.query(Attempt).filter_by(assignment_id=a.id, student_id=s.id).all()
        full = [x for x in ats if x.section_id is None and x.status == "done"]
        best = max(full, key=lambda x: x.score, default=None)
        rows.append({
            "student_id": s.id, "student": s.name,
            "attempts_used": sum(x.section_id is None and x.counted for x in ats),
            "section_practice_takes": sum(x.section_id is not None for x in ats),
            "best_score": best.score if best else None,
            "sections_passed": ([x["id"] for x in best.result["sections"] if x["passed"]]
                                if best else []),
            "all_passed": bool(best and best.result["all_passed"]),
            "scores_over_time": [x.score for x in sorted(full, key=lambda x: x.id)],
        })
    return {"assignment": a.title, "max_attempts": a.max_attempts, "students": rows}
