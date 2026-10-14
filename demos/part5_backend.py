"""
Part 5 live demo: starts the real server and plays out a full teacher + student
session over HTTP, printing every step.

Usage (from inside the stringcoach folder):
    python -m demos.part5_backend
"""
import os
import shutil
import subprocess
import sys
import time
import warnings

import httpx
import soundfile as sf

warnings.filterwarnings("ignore")
from backend.client import auth, upload_file, wait_for
from engine.synth import render, SR, TEACHER

PORT = 8765
os.makedirs("data/part5", exist_ok=True)
shutil.rmtree("demo_server_data", ignore_errors=True)

LESSON = TEACHER * 4
STUDENT = [dict(n) for n in LESSON]
STUDENT[15]["cents"] = -32
STUDENT[18]["midi"] += 1
STUDENT[19]["gap_before"] = 0.45
sf.write("data/part5/teacher.wav", render(LESSON, noise_db=-40), SR, subtype="FLOAT")
sf.write("data/part5/student_take1.wav", render(STUDENT, noise_db=-35, seed=1), SR, subtype="FLOAT")
sf.write("data/part5/student_section3.wav", render(LESSON[14:21], noise_db=-35, seed=2), SR, subtype="FLOAT")
sf.write("data/part5/student_take2.wav", render(LESSON, noise_db=-35, seed=3), SR, subtype="FLOAT")


def step(msg):
    print(f"\n>> {msg}")


env = {**os.environ, "STRINGCOACH_DATA": "demo_server_data", "WORKERS": "2"}
server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api:app", "--port", str(PORT),
                           "--log-level", "warning"], env=env)
try:
    http = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=60)
    for _ in range(100):
        try:
            if http.get("/health").status_code == 200:
                break
        except httpx.ConnectError:
            time.sleep(0.1)
    print(f"Server running at http://127.0.0.1:{PORT}  (interactive docs at /docs)")

    step("Teacher and student create accounts and log in")
    for who, role in [("Priya", "teacher"), ("Arjun", "student")]:
        http.post("/auth/register", json={"name": who, "email": f"{who.lower()}@demo.in",
                                          "password": "violin-2026", "role": role})
    tt = http.post("/auth/login", json={"email": "priya@demo.in", "password": "violin-2026"}).json()["token"]
    st = http.post("/auth/login", json={"email": "arjun@demo.in", "password": "violin-2026"}).json()["token"]
    print("   teacher token ...", tt[-6:], "| student token ...", st[-6:])

    step("Teacher creates a violin class and adds the student")
    cls = http.post("/classes", headers=auth(tt), json={"name": "Violin Grade 2", "instrument": "violin"}).json()
    http.post(f"/classes/{cls['id']}/students", headers=auth(tt), json={"email": "arjun@demo.in"})
    print("   class:", cls, "| students:", http.get(f"/classes/{cls['id']}/students", headers=auth(tt)).json())

    step("Teacher uploads the reference recording in chunks")
    t0 = time.time()
    _, ref = upload_file(http, tt, "data/part5/teacher.wav")
    print(f"   uploaded {os.path.getsize('data/part5/teacher.wav') // 1024} KB in "
          f"{-(-os.path.getsize('data/part5/teacher.wav') // (256 * 1024))} chunks -> recording {ref} "
          f"({time.time() - t0:.2f}s)")

    step("Teacher creates the assignment; the server analyses the reference in the background")
    a = http.post("/assignments", headers=auth(tt), json={
        "class_id": cls["id"], "title": "Twinkle - 4 phrases", "recording_id": ref,
        "level": "intermediate", "pass_mark": 90, "max_attempts": 5}).json()
    print("   status right away:", a["status"], "(the API didn't wait)")
    a = wait_for(http, tt, f"/assignments/{a['id']}")
    print("   status now:", a["status"], "|", len(a["notes"]), "notes |",
          "sections:", [f"{s['first'] + 1}-{s['last'] + 1}" for s in a["sections"]])

    step("Student can't see it yet (not published)")
    print("   student GET ->", http.get(f"/assignments/{a['id']}", headers=auth(st)).status_code)

    step("Teacher publishes")
    http.post(f"/assignments/{a['id']}/publish", headers=auth(tt))
    print("   student GET ->", http.get(f"/assignments/{a['id']}", headers=auth(st)).status_code)

    step("Student uploads take 1 and submits it")
    _, rec = upload_file(http, st, "data/part5/student_take1.wav")
    at = http.post(f"/assignments/{a['id']}/attempts", headers=auth(st), json={"recording_id": rec}).json()
    print("   submitted -> status:", at["status"])
    at = wait_for(http, st, f"/attempts/{at['id']}")
    print(f"   analysed in {at['seconds']}s -> score {at['score']}, sections "
          f"{[(s['id'], s['score']) for s in at['result']['sections']]}")
    print("   review:\n      " + at["feedback"].replace("\n", "\n      "))

    step("Student practises section 3 only (doesn't use up an attempt)")
    _, rec = upload_file(http, st, "data/part5/student_section3.wav")
    p = http.post(f"/assignments/{a['id']}/attempts", headers=auth(st),
                  json={"recording_id": rec, "section_id": 3}).json()
    p = wait_for(http, st, f"/attempts/{p['id']}")
    print(f"   section 3 take -> score {p['score']}, passed {p['result']['all_passed']}")

    step("Student records take 2 of the whole piece")
    _, rec = upload_file(http, st, "data/part5/student_take2.wav")
    t2 = http.post(f"/assignments/{a['id']}/attempts", headers=auth(st), json={"recording_id": rec}).json()
    t2 = wait_for(http, st, f"/attempts/{t2['id']}")
    print(f"   take 2 -> score {t2['score']}: {t2['feedback'].splitlines()[0]}")

    step("Teacher comments on take 2")
    http.post(f"/attempts/{t2['id']}/comments", headers=auth(tt),
              json={"body": "Lovely - section 3 is secure now. Next: add vibrato on the long notes."})
    print("   student sees:", http.get(f"/attempts/{t2['id']}", headers=auth(st)).json()["comments"][0]["body"])

    step("Teacher's progress view")
    for row in http.get(f"/assignments/{a['id']}/progress", headers=auth(tt)).json()["students"]:
        print(f"   {row['student']}: attempts used {row['attempts_used']}/5, section practice "
              f"{row['section_practice_takes']}, scores over time {row['scores_over_time']}, "
              f"best {row['best_score']}, all sections passed: {row['all_passed']}")
finally:
    server.terminate()
    server.wait()
    print("\nServer stopped.")
