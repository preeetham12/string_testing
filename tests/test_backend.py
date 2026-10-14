"""
Part 5 - backend tests.

Usage: python -m pytest tests/test_backend.py -v
Runs the API in-process with its own throwaway database. Background jobs are
processed explicitly (drain()) so every test is deterministic; the concurrency
test starts real worker threads.
"""
import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
import warnings

TMP = tempfile.mkdtemp(prefix="stringcoach_test_")
os.environ["STRINGCOACH_DATA"] = TMP
os.environ["START_WORKERS"] = "0"
warnings.filterwarnings("ignore")

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from backend import worker
from backend.api import app
from backend.client import auth, resume_upload, upload_file
from backend.db import Job, Recording, SessionLocal
from engine.synth import SR, TEACHER, render

LESSON = TEACHER * 2  # 14 notes, 2 phrases
AUDIO = os.path.join(TMP, "audio")
os.makedirs(AUDIO, exist_ok=True)


def wav(name, notes, noise=-35, seed=1, post=None):
    p = os.path.join(AUDIO, f"{name}.wav")
    y = render(notes, noise_db=noise, seed=seed)
    if post:
        y = post(y)
    sf.write(p, y, SR, subtype="FLOAT")
    return p


FILES = {}


@pytest.fixture(scope="session", autouse=True)
def files():
    mistake = [dict(n) for n in LESSON]
    mistake[9]["midi"] += 1  # wrong note in section 2
    FILES.update({
        "teacher": wav("teacher", LESSON, noise=-40),
        "teacher_noisy": wav("teacher_noisy", LESSON, noise=-3, seed=9),
        "good": wav("good", LESSON, seed=2),
        "mistake_s2": wav("mistake_s2", mistake, seed=3),
        "clipped": wav("clipped", LESSON, seed=4, post=lambda y: np.clip(y * 6, -1, 1)),
        "section2": wav("section2", LESSON[7:14], seed=5),
    })
    yield
    shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(scope="session")
def http():
    with TestClient(app) as c:
        yield c


def drain():
    """Process every queued job (what the worker threads do in production)."""
    while worker.run_one():
        pass


_n = [0]


def user(http, role, name=None):
    _n[0] += 1
    email = f"{role}{_n[0]}@test.in"
    http.post("/auth/register", json={"name": name or f"{role}{_n[0]}", "email": email,
                                      "password": "secret-pass", "role": role})
    return http.post("/auth/login", json={"email": email, "password": "secret-pass"}).json()["token"], email


def lesson(http, max_attempts=10, ref="teacher"):
    """A teacher with a published 2-section assignment and one enrolled student."""
    tt, _ = user(http, "teacher")
    st, se = user(http, "student")
    c = http.post("/classes", headers=auth(tt), json={"name": "C", "instrument": "violin"}).json()
    http.post(f"/classes/{c['id']}/students", headers=auth(tt), json={"email": se})
    _, rec = upload_file(http, tt, FILES[ref])
    a = http.post("/assignments", headers=auth(tt), json={
        "class_id": c["id"], "title": "T", "recording_id": rec, "max_attempts": max_attempts}).json()
    drain()
    if http.get(f"/assignments/{a['id']}", headers=auth(tt)).json()["status"] == "ready":
        http.post(f"/assignments/{a['id']}/publish", headers=auth(tt))
    return {"tt": tt, "st": st, "class": c["id"], "a": a["id"]}


def submit(http, L, key, section=None):
    _, rec = upload_file(http, L["st"], FILES[key])
    r = http.post(f"/assignments/{L['a']}/attempts", headers=auth(L["st"]),
                  json={"recording_id": rec, "section_id": section})
    if r.status_code != 201:
        return r
    drain()
    return http.get(f"/attempts/{r.json()['id']}", headers=auth(L["st"]))


# ---------------- accounts and login ----------------
def test_register_validation(http):
    bad = [{"name": "x", "email": "not-an-email", "password": "secret-pass", "role": "student"},
           {"name": "x", "email": "a@b.in", "password": "short", "role": "student"},
           {"name": "x", "email": "a@b.in", "password": "secret-pass", "role": "admin"}]
    for body in bad:
        assert http.post("/auth/register", json=body).status_code == 422
    ok = {"name": "x", "email": "dup@b.in", "password": "secret-pass", "role": "student"}
    assert http.post("/auth/register", json=ok).status_code == 201
    assert http.post("/auth/register", json=ok).status_code == 409


def test_login_failures_give_same_message(http):
    _, email = user(http, "student")
    wrong = http.post("/auth/login", json={"email": email, "password": "nope-nope"})
    unknown = http.post("/auth/login", json={"email": "ghost@x.in", "password": "nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()  # doesn't reveal which emails exist


def test_requests_need_a_valid_token(http):
    assert http.get("/me").status_code == 401
    assert http.get("/me", headers=auth("made-up-token")).status_code == 401


def test_passwords_are_not_stored_in_plain_text(http):
    user(http, "student")
    rows = sqlite3.connect(os.path.join(TMP, "stringcoach.db")).execute(
        "select password_hash from users").fetchall()
    assert rows and all("secret-pass" not in r[0] and "$" in r[0] for r in rows)


# ---------------- who can see and do what ----------------
def test_role_rules(http):
    L = lesson(http)
    assert http.post("/classes", headers=auth(L["st"]),
                     json={"name": "x", "instrument": "violin"}).status_code == 403
    assert http.post(f"/attempts/1/comments", headers=auth(L["st"]),
                     json={"body": "hi"}).status_code == 403
    _, rec = upload_file(http, L["tt"], FILES["good"])
    assert http.post(f"/assignments/{L['a']}/attempts", headers=auth(L["tt"]),
                     json={"recording_id": rec}).status_code == 403


def test_teachers_cannot_see_other_teachers_classes(http):
    L = lesson(http)
    other, _ = user(http, "teacher")
    at = submit(http, L, "good").json()
    assert http.get(f"/assignments/{L['a']}", headers=auth(other)).status_code == 404
    assert http.get(f"/attempts/{at['id']}", headers=auth(other)).status_code == 404
    assert http.get(f"/assignments/{L['a']}/progress", headers=auth(other)).status_code == 404
    _, se = user(http, "student")
    assert http.post(f"/classes/{L['class']}/students", headers=auth(other),
                     json={"email": se}).status_code == 404


def test_students_only_see_their_own_work(http):
    L = lesson(http)
    mine = submit(http, L, "good").json()
    other_st, other_email = user(http, "student")
    # not enrolled: can't see the assignment at all
    assert http.get(f"/assignments/{L['a']}", headers=auth(other_st)).status_code == 404
    http.post(f"/classes/{L['class']}/students", headers=auth(L["tt"]), json={"email": other_email})
    # enrolled classmate: sees the assignment, but NOT someone else's attempt
    assert http.get(f"/assignments/{L['a']}", headers=auth(other_st)).status_code == 200
    assert http.get(f"/attempts/{mine['id']}", headers=auth(other_st)).status_code == 404
    assert http.get(f"/assignments/{L['a']}/attempts", headers=auth(other_st)).json() == []


def test_unpublished_assignment_is_hidden_from_students(http):
    tt, _ = user(http, "teacher")
    st, se = user(http, "student")
    c = http.post("/classes", headers=auth(tt), json={"name": "C", "instrument": "violin"}).json()
    http.post(f"/classes/{c['id']}/students", headers=auth(tt), json={"email": se})
    _, rec = upload_file(http, tt, FILES["teacher"])
    a = http.post("/assignments", headers=auth(tt), json={
        "class_id": c["id"], "title": "draft", "recording_id": rec}).json()
    drain()
    assert http.get(f"/assignments/{a['id']}", headers=auth(st)).status_code == 404
    assert http.get(f"/classes/{c['id']}/assignments", headers=auth(st)).json() == []


def test_cannot_use_someone_elses_recording(http):
    L = lesson(http)
    thief, thief_email = user(http, "student")
    http.post(f"/classes/{L['class']}/students", headers=auth(L["tt"]), json={"email": thief_email})
    _, rec = upload_file(http, L["st"], FILES["good"])  # belongs to L's student
    assert http.post(f"/assignments/{L['a']}/attempts", headers=auth(thief),
                     json={"recording_id": rec}).status_code == 404


# ---------------- chunked uploads ----------------
def test_upload_arrives_byte_for_byte(http):
    tt, _ = user(http, "teacher")
    _, rec = upload_file(http, tt, FILES["teacher"], chunk_size=100_000)
    path = SessionLocal().get(Recording, rec).path
    assert hashlib.sha256(open(path, "rb").read()).digest() == \
           hashlib.sha256(open(FILES["teacher"], "rb").read()).digest()


def test_upload_resumes_after_lost_chunks(http):
    tt, _ = user(http, "teacher")
    uid, rec = upload_file(http, tt, FILES["teacher"], chunk_size=100_000, skip={1, 4})
    assert rec is None  # can't complete with chunks missing
    assert http.post(f"/uploads/{uid}/complete", headers=auth(tt)).status_code == 409
    rec, missing = resume_upload(http, tt, uid, FILES["teacher"], chunk_size=100_000)
    assert missing == [1, 4] and rec is not None


def test_damaged_chunk_is_rejected(http):
    tt, _ = user(http, "teacher")
    data = open(FILES["teacher"], "rb").read()[:1000]
    uid = http.post("/uploads", headers=auth(tt), json={
        "total_chunks": 1, "total_bytes": 1000, "sha256": hashlib.sha256(data).hexdigest()}).json()["upload_id"]
    damaged = b"X" + data[1:]
    r = http.put(f"/uploads/{uid}/chunks/0", content=damaged,
                 headers={**auth(tt), "X-Chunk-SHA256": hashlib.sha256(data).hexdigest()})
    assert r.status_code == 422


def test_non_audio_file_is_rejected(http):
    tt, _ = user(http, "teacher")
    p = os.path.join(AUDIO, "notes.txt")
    open(p, "wb").write(b"this is not audio" * 100)
    _, rec = upload_file(http, tt, p)
    assert rec is None


def test_uploads_are_private_and_complete_is_safe_to_repeat(http):
    a, _ = user(http, "teacher")
    b, _ = user(http, "teacher")
    uid, rec = upload_file(http, a, FILES["teacher"])
    assert http.get(f"/uploads/{uid}", headers=auth(b)).status_code == 404
    again = http.post(f"/uploads/{uid}/complete", headers=auth(a)).json()
    assert again["recording_id"] == rec


# ---------------- assignments ----------------
def test_noisy_reference_is_rejected_and_cannot_be_published(http):
    L = lesson(http, ref="teacher_noisy")
    a = http.get(f"/assignments/{L['a']}", headers=auth(L["tt"])).json()
    assert a["status"] == "reference_failed"
    assert any(c["check"] == "noise" and c["status"] == "FAIL" for c in a["qc_report"]["checks"])
    assert http.post(f"/assignments/{L['a']}/publish", headers=auth(L["tt"])).status_code == 409


def test_teacher_edits_sections(http):
    L = _ready_lesson(http)
    url = f"/assignments/{L['a']}/sections"
    gap = {"sections": [{"first": 1, "last": 5}, {"first": 7, "last": 14}]}
    assert http.put(url, headers=auth(L["tt"]), json=gap).status_code == 422
    ok = {"sections": [{"first": 1, "last": 4}, {"first": 5, "last": 14}]}
    s = http.put(url, headers=auth(L["tt"]), json=ok).json()["sections"]
    assert [(x["first"], x["last"]) for x in s] == [(0, 3), (4, 13)]
    http.post(f"/assignments/{L['a']}/publish", headers=auth(L["tt"]))
    assert http.put(url, headers=auth(L["tt"]), json=ok).status_code == 409  # locked now


# ---------------- attempts ----------------
def test_attempt_is_scored_like_the_engine(http):
    L = lesson(http)
    r = submit(http, L, "mistake_s2").json()
    assert r["status"] == "done"
    secs = r["result"]["sections"]
    assert secs[0]["passed"] and not secs[1]["passed"]
    assert "instead of" in r["feedback"]


def test_failed_quality_check_does_not_use_an_attempt(http):
    L = lesson(http, max_attempts=2)
    bad = submit(http, L, "clipped").json()
    assert bad["status"] == "qc_failed" and not bad["counted"]
    assert "distorting" in bad["feedback"]
    assert submit(http, L, "good").json()["status"] == "done"
    assert submit(http, L, "good").json()["status"] == "done"
    third = submit(http, L, "good")
    assert third.status_code == 409  # max 2 counted attempts


def test_section_practice_is_unlimited_and_scored_on_that_section(http):
    L = lesson(http, max_attempts=1)
    for _ in range(2):
        r = submit(http, L, "section2", section=2).json()
        assert r["status"] == "done" and r["score"] >= 95 and r["counted"]
    assert submit(http, L, "good").json()["status"] == "done"  # full attempt still available


def test_teacher_comment_and_score_override(http):
    L = lesson(http)
    at = submit(http, L, "mistake_s2").json()
    http.post(f"/attempts/{at['id']}/comments", headers=auth(L["tt"]),
              json={"body": "Good effort; fix bar 10.", "score_override": 85})
    seen = http.get(f"/attempts/{at['id']}", headers=auth(L["st"])).json()
    assert seen["teacher_score"] == 85 and seen["comments"][0]["body"].startswith("Good effort")


def test_progress_for_teacher_dashboard(http):
    L = lesson(http)
    submit(http, L, "mistake_s2")
    submit(http, L, "section2", section=2)
    submit(http, L, "good")
    row = http.get(f"/assignments/{L['a']}/progress", headers=auth(L["tt"])).json()["students"][0]
    assert row["attempts_used"] == 2 and row["section_practice_takes"] == 1
    assert row["scores_over_time"][-1] == 100 and row["all_passed"]


# ---------------- queue: crashes, errors, load ----------------
def test_jobs_survive_a_server_crash(http):
    L = lesson(http)
    _, rec = upload_file(http, L["st"], FILES["good"])
    at = http.post(f"/assignments/{L['a']}/attempts", headers=auth(L["st"]),
                   json={"recording_id": rec}).json()
    db = SessionLocal()
    job = db.query(Job).filter_by(kind="process_attempt", target_id=at["id"]).one()
    job.status = "running"  # the server "crashed" mid-job
    db.commit()
    db.close()
    assert worker.recover() == 1  # on restart it goes back in the queue
    drain()
    assert http.get(f"/attempts/{at['id']}", headers=auth(L["st"])).json()["status"] == "done"


def test_a_broken_job_fails_cleanly_and_doesnt_count(http):
    L = lesson(http)
    _, rec = upload_file(http, L["st"], FILES["good"])
    db = SessionLocal()
    os.remove(db.get(Recording, rec).path)  # file vanished from storage
    db.close()
    at = http.post(f"/assignments/{L['a']}/attempts", headers=auth(L["st"]),
                   json={"recording_id": rec}).json()
    drain()
    r = http.get(f"/attempts/{at['id']}", headers=auth(L["st"])).json()
    assert r["status"] == "error" and not r["counted"]
    assert submit(http, L, "good").json()["status"] == "done"  # server still fine


def test_many_attempts_at_once(http):
    L = lesson(http)
    ids = []
    for _ in range(6):
        _, rec = upload_file(http, L["st"], FILES["mistake_s2"])
        ids.append(http.post(f"/assignments/{L['a']}/attempts", headers=auth(L["st"]),
                             json={"recording_id": rec}).json()["id"])
    threads = worker.start_workers(3)
    t0 = time.time()
    try:
        while time.time() - t0 < 120:
            st = [http.get(f"/attempts/{i}", headers=auth(L["st"])).json() for i in ids]
            if all(s["status"] == "done" for s in st):
                break
            time.sleep(0.3)
    finally:
        worker.stop_workers()
        for t in threads:
            t.join(timeout=10)
    assert all(s["status"] == "done" for s in st)
    assert len({s["score"] for s in st}) == 1  # same recording -> same score every time


def test_double_submit_race_on_a_real_server():
    """8 simultaneous submissions with 1 attempt left: exactly one may get in.
    Needs a real server (the in-process client sends requests one at a time)."""
    import subprocess, sys, threading
    import httpx
    port = 8790
    env = {**os.environ, "STRINGCOACH_DATA": os.path.join(TMP, "race"), "START_WORKERS": "0"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api:app", "--port",
                            str(port), "--log-level", "warning"], env=env)
    try:
        h = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60)
        for _ in range(100):
            try:
                h.get("/health")
                break
            except httpx.ConnectError:
                time.sleep(0.1)
        for n, r in [("t", "teacher"), ("s", "student")]:
            h.post("/auth/register", json={"name": n, "email": f"{n}@race.in",
                                           "password": "secret-pass", "role": r})
        tt = h.post("/auth/login", json={"email": "t@race.in", "password": "secret-pass"}).json()["token"]
        st = h.post("/auth/login", json={"email": "s@race.in", "password": "secret-pass"}).json()["token"]
        c = h.post("/classes", headers=auth(tt), json={"name": "C", "instrument": "violin"}).json()
        h.post(f"/classes/{c['id']}/students", headers=auth(tt), json={"email": "s@race.in"})
        _, ref = upload_file(h, tt, FILES["teacher"])
        a = h.post("/assignments", headers=auth(tt), json={"class_id": c["id"], "title": "T",
                                                            "recording_id": ref, "max_attempts": 1}).json()
        # this server has no workers; mark the assignment published directly
        con = sqlite3.connect(os.path.join(TMP, "race", "stringcoach.db"))
        con.execute("update assignments set status='published', sections='[]' where id=?", (a["id"],))
        con.commit()
        recs = [upload_file(h, st, FILES["good"])[1] for _ in range(8)]
        codes, go = [], threading.Barrier(8)

        def fire(rec):
            c2 = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60)
            go.wait()
            codes.append(c2.post(f"/assignments/{a['id']}/attempts", headers=auth(st),
                                 json={"recording_id": rec}).status_code)
        ts = [threading.Thread(target=fire, args=(r,)) for r in recs]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert codes.count(201) == 1 and codes.count(409) == 7, sorted(codes)
    finally:
        srv.terminate()
        srv.wait()


# ---------------- Part 7: teacher corrections and class summary ----------------
def _ready_lesson(http, ref_key="teacher"):
    """Like lesson(), but left at 'ready' (not yet published) so notes can be edited."""
    tt, _ = user(http, "teacher")
    st, se = user(http, "student")
    c = http.post("/classes", headers=auth(tt), json={"name": "C", "instrument": "violin"}).json()
    http.post(f"/classes/{c['id']}/students", headers=auth(tt), json={"email": se})
    _, rec = upload_file(http, tt, FILES[ref_key])
    a = http.post("/assignments", headers=auth(tt), json={
        "class_id": c["id"], "title": "T", "recording_id": rec}).json()
    drain()
    return {"tt": tt, "st": st, "class": c["id"], "a": a["id"]}


def test_teacher_fixes_a_slip_in_their_own_recording(http):
    slip = [dict(n) for n in LESSON]
    slip[4]["midi"] += 1                       # teacher played G5 instead of F#5
    FILES["teacher_slip"] = wav("teacher_slip", slip, noise=-40, seed=21)
    L = _ready_lesson(http, "teacher_slip")
    notes = http.get(f"/assignments/{L['a']}", headers=auth(L["tt"])).json()["notes"]
    assert notes[4]["note"] == "G5"            # the engine heard the slip correctly
    fixed = http.put(f"/assignments/{L['a']}/notes", headers=auth(L["tt"]),
                     json={"edits": [{"index": 5, "midi": 78}]}).json()
    assert fixed["notes"][4]["note"] == "F#5" and fixed["notes"][4]["corrected"]
    http.post(f"/assignments/{L['a']}/publish", headers=auth(L["tt"]))
    r = submit(http, L, "good").json()         # student plays the piece correctly
    assert r["status"] == "done" and r["score"] == 100 and r["result"]["counts"]["wrong note"] == 0


def test_teacher_deletes_a_stray_note(http):
    # an accidental E4 long enough to be detected (blips under ~0.2 s are ignored
    # by the note detector anyway)
    stray = LESSON[:5] + [{"midi": 64, "dur": 0.3}] + LESSON[5:]
    FILES["teacher_stray"] = wav("teacher_stray", stray, noise=-40, seed=22)
    L = _ready_lesson(http, "teacher_stray")
    notes = http.get(f"/assignments/{L['a']}", headers=auth(L["tt"])).json()["notes"]
    assert len(notes) == 15 and notes[5]["note"] == "E4"
    a = http.put(f"/assignments/{L['a']}/notes", headers=auth(L["tt"]),
                 json={"edits": [{"index": 6, "delete": True}]}).json()
    assert len(a["notes"]) == 14 and a["sections"][-1]["last"] == 13  # sections re-detected
    # the deleted note's empty slot must not split a phrase into a new section
    assert [(x["first"], x["last"]) for x in a["sections"]] == [(0, 6), (7, 13)]
    http.post(f"/assignments/{L['a']}/publish", headers=auth(L["tt"]))
    r = submit(http, L, "good").json()
    assert r["result"]["counts"]["missed"] == 0 and r["score"] == 100


def test_note_edit_rules(http):
    L = _ready_lesson(http)
    url = f"/assignments/{L['a']}/notes"
    assert http.put(url, headers=auth(L["st"]), json={"edits": [{"index": 1, "midi": 70}]}).status_code == 403
    assert http.put(url, headers=auth(L["tt"]), json={"edits": [{"index": 99, "midi": 70}]}).status_code == 422
    assert http.put(url, headers=auth(L["tt"]), json={"edits": [{"index": 1}]}).status_code == 422
    other, _ = user(http, "teacher")
    assert http.put(url, headers=auth(other), json={"edits": [{"index": 1, "midi": 70}]}).status_code == 404
    http.post(f"/assignments/{L['a']}/publish", headers=auth(L["tt"]))
    # once students can see it, the lesson can't change under them
    assert http.put(url, headers=auth(L["tt"]), json={"edits": [{"index": 1, "midi": 70}]}).status_code == 409


def test_class_summary_finds_the_hard_section_and_note(http):
    L = lesson(http)
    _, e2 = user(http, "student")
    s2, _ = http.post("/auth/login", json={"email": e2, "password": "secret-pass"}).json()["token"], None
    http.post(f"/classes/{L['class']}/students", headers=auth(L["tt"]), json={"email": e2})
    submit(http, L, "mistake_s2")              # student 1: wrong note 10, in section 2
    _, rec = upload_file(http, s2, FILES["mistake_s2"])
    http.post(f"/assignments/{L['a']}/attempts", headers=auth(s2), json={"recording_id": rec})
    drain()
    summary = http.get(f"/assignments/{L['a']}/class_summary", headers=auth(L["tt"])).json()
    assert summary["students_with_takes"] == 2
    s = {x["id"]: x for x in summary["sections"]}
    assert s[1]["passed"] == 2 and s[2]["passed"] == 0 and s[2]["average"] < s[1]["average"]
    top = summary["hardest_notes"][0]
    assert top["note_number"] == 10 and top["students"] == 2 and "wrong note" in top["problems"]
    assert http.get(f"/assignments/{L['a']}/class_summary", headers=auth(L["st"])).status_code == 403


def test_teacher_splits_a_note_merged_by_echo(http):
    """Room echo can merge two same notes (A4, A4) into one in the teacher's
    reference. The teacher splits it in the studio: at the app's suggested
    point if there is one, otherwise in the middle (and then that note's
    rhythm isn't judged, because the split point wasn't heard)."""
    L = _ready_lesson(http)
    url = f"/assignments/{L['a']}/notes"
    notes = http.get(f"/assignments/{L['a']}", headers=auth(L["tt"])).json()["notes"]
    n7 = notes[6]                                    # the long E5 ending phrase 1
    # explicit split point
    at = round((n7["start"] + n7["end"]) / 2 + 0.05, 3)
    a = http.put(url, headers=auth(L["tt"]), json={"edits": [{"index": 7, "split": True, "at": at}]}).json()
    assert len(a["notes"]) == 15
    assert a["notes"][6]["note"] == a["notes"][7]["note"] == "E5"
    assert a["notes"][7]["start"] == at and a["notes"][7]["split_by_teacher"]
    assert not a["notes"][7].get("skip_timing")      # split point was given: timing judged
    # no point given and no suggestion: middle, timing not judged
    a = http.put(url, headers=auth(L["tt"]), json={"edits": [{"index": 1, "split": True}]}).json()
    assert len(a["notes"]) == 16 and a["notes"][1]["skip_timing"]
    # rules
    assert http.put(url, headers=auth(L["tt"]), json={"edits": [
        {"index": 2, "split": True}, {"index": 3, "midi": 70}]}).status_code == 422
    assert http.put(url, headers=auth(L["tt"]), json={"edits": [
        {"index": 3, "split": True, "at": 0.0}]}).status_code == 422      # outside the note
    http.post(f"/assignments/{L['a']}/publish", headers=auth(L["tt"]))
    assert http.put(url, headers=auth(L["tt"]), json={"edits": [
        {"index": 3, "split": True}]}).status_code == 409                 # locked after publishing


# ---------------- talking is not playing ----------------
def _gate_passes(monkeypatch):
    """Simulate the real case that exposed the gap: talking near the
    microphone got through the older quality checks."""
    monkeypatch.setattr(worker, "check_recording",
                        lambda *a, **k: {"overall": "PASS", "checks": []})


def test_instrument_check_violin_yes_speech_no():
    from engine.pipeline import prepare
    from engine.quality import instrument_check
    from tests.speech_like import SR as SPEECH_SR, speech_like
    from tests.test_web import realistic
    fast = [{"midi": 67 + (k % 8), "dur": 0.13} for k in range(40)]   # fast passage
    for label, y, sr in [("violin", render(LESSON, noise_db=-35), SR),
                         ("violin in an echoey room", realistic(render(LESSON, noise_db=-38)), SR),
                         ("fast violin notes", render(fast, noise_db=-35), SR)]:
        p = os.path.join(AUDIO, "inst.wav"); sf.write(p, y, sr, subtype="FLOAT")
        assert instrument_check(prepare(p, student=True))["ok"], label
    for seed in range(4):
        p = os.path.join(AUDIO, "speech.wav"); sf.write(p, speech_like(15, seed), SPEECH_SR, subtype="FLOAT")
        assert not instrument_check(prepare(p, student=True))["ok"], f"speech seed {seed}"


def test_talking_is_not_scored(http, monkeypatch):
    from tests.speech_like import SR as SPEECH_SR, speech_like
    _gate_passes(monkeypatch)
    L = lesson(http, max_attempts=1)
    FILES["speech"] = os.path.join(AUDIO, "talking.wav")
    sf.write(FILES["speech"], speech_like(15, 1), SPEECH_SR, subtype="FLOAT")
    r = submit(http, L, "speech").json()
    assert r["status"] == "qc_failed" and not r["counted"] and r["score"] is None
    assert "doesn't sound like your instrument" in r["feedback"]
    assert any(c["check"] == "instrument" for c in r["qc_report"]["checks"])
    assert submit(http, L, "good").json()["status"] == "done"   # the attempt wasn't used up


def test_teacher_reference_that_is_talking_is_rejected(http, monkeypatch):
    from tests.speech_like import SR as SPEECH_SR, speech_like
    _gate_passes(monkeypatch)
    FILES["speech_ref"] = os.path.join(AUDIO, "talking_ref.wav")
    sf.write(FILES["speech_ref"], speech_like(15, 2), SPEECH_SR, subtype="FLOAT")
    L = lesson(http, ref="speech_ref")
    a = http.get(f"/assignments/{L['a']}", headers=auth(L["tt"])).json()
    assert a["status"] == "reference_failed"
    assert any(c["check"] == "instrument" and c["status"] == "FAIL" for c in a["qc_report"]["checks"])


def test_a_different_piece_is_not_scored(http, monkeypatch):
    _gate_passes(monkeypatch)
    L = lesson(http)
    other = [{"midi": m, "dur": 0.45} for m in [62, 64, 66, 67, 69, 71, 73, 74] * 2]  # D major scale
    FILES["other_piece"] = wav("other_piece", other, seed=31)
    r = submit(http, L, "other_piece").json()
    assert r["status"] == "qc_failed" and not r["counted"], r["status"]
    assert "couldn't recognise this lesson" in r["feedback"]
