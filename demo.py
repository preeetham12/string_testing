"""
StringCoach demo: one command starts everything.

    python demo.py            # start (creates demo data the first time)
    python demo.py --reset    # throw away demo data and start fresh
    python demo.py --reference my_teacher.wav   # use a real teacher recording

Then open http://localhost:8000 and log in with the accounts printed below.
The demo data lives in ./demo_data (separate from any real server_data).
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
import warnings

warnings.filterwarnings("ignore")
import httpx
import soundfile as sf

from backend.client import auth, upload_file
from engine.synth import SR, TEACHER, render

DATA = "demo_data"
PORT = 8000
PASSWORD = "violin-demo"
TEACHER_EMAIL, STUDENT_EMAIL = "priya@demo.in", "arjun@demo.in"


def wait_for(h, url, token, until, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = h.get(url, headers=auth(token)).json()
        if r.get("status") in until:
            return r
        time.sleep(0.3)
    raise TimeoutError(url)


def seed(h, reference):
    """Accounts, a class, a published lesson, a lesson to check, a sample take."""
    for name, email, role in [("Priya", TEACHER_EMAIL, "teacher"), ("Arjun", STUDENT_EMAIL, "student"),
                              ("Meera", "meera@demo.in", "student")]:
        h.post("/auth/register", json={"name": name, "email": email, "password": PASSWORD, "role": role})
    tt = h.post("/auth/login", json={"email": TEACHER_EMAIL, "password": PASSWORD}).json()["token"]
    c = h.post("/classes", headers=auth(tt), json={"name": "Violin Grade 2", "instrument": "violin"}).json()
    for email in (STUDENT_EMAIL, "meera@demo.in"):
        h.post(f"/classes/{c['id']}/students", headers=auth(tt), json={"email": email})

    os.makedirs(f"{DATA}/audio", exist_ok=True)
    lesson = TEACHER * 4  # "Twinkle", 4 phrases
    ref = reference or f"{DATA}/audio/twinkle_teacher.wav"
    if not reference:
        sf.write(ref, render(lesson, noise_db=-40), SR, subtype="PCM_16")
    _, rec = upload_file(h, tt, ref)
    a = h.post("/assignments", headers=auth(tt), json={
        "class_id": c["id"], "title": "Twinkle, 4 phrases", "recording_id": rec,
        "level": "intermediate", "pass_mark": 90, "max_attempts": 10}).json()
    a = wait_for(h, f"/assignments/{a['id']}", tt, ("ready", "reference_failed"))
    if a["status"] != "ready":
        sys.exit("The reference recording was rejected by the quality check: " +
                 "; ".join(x["message"] for x in a["qc_report"]["checks"] if x["status"] == "FAIL"))
    h.post(f"/assignments/{a['id']}/publish", headers=auth(tt))

    # a second lesson left at "ready to check", to show the studio
    scale = [{"midi": m, "dur": 0.45} for m in [67, 69, 71, 72, 74, 76, 78, 79]] + [{"midi": 79, "dur": 0.9}]
    sf.write(f"{DATA}/audio/scale_teacher.wav", render(scale, noise_db=-40), SR, subtype="PCM_16")
    _, rec2 = upload_file(h, tt, f"{DATA}/audio/scale_teacher.wav")
    b = h.post("/assignments", headers=auth(tt), json={
        "class_id": c["id"], "title": "G major scale", "recording_id": rec2}).json()
    wait_for(h, f"/assignments/{b['id']}", tt, ("ready", "reference_failed"))

    # Meera's take: wrong note, a flat note and a hesitation, all in section 3,
    # so the review, the class heatmap and the dashboard have data straight away
    if not reference:
        take = [dict(n) for n in lesson]
        take[15]["cents"] = -32
        take[18]["midi"] += 1
        take[19]["gap_before"] = 0.45
        sf.write(f"{DATA}/audio/meera_take1.wav", render(take, noise_db=-35, seed=4), SR, subtype="PCM_16")
        mt = h.post("/auth/login", json={"email": "meera@demo.in", "password": PASSWORD}).json()["token"]
        _, rec3 = upload_file(h, mt, f"{DATA}/audio/meera_take1.wav")
        at = h.post(f"/assignments/{a['id']}/attempts", headers=auth(mt), json={"recording_id": rec3}).json()
        wait_for(h, f"/attempts/{at['id']}", mt, ("done", "qc_failed", "error"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="delete demo data and start fresh")
    ap.add_argument("--reference", help="a WAV of a real teacher recording for the published lesson")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    if a.reset:
        shutil.rmtree(DATA, ignore_errors=True)
    fresh = not os.path.exists(f"{DATA}/stringcoach.db")
    env = {**os.environ, "STRINGCOACH_DATA": DATA, "WORKERS": "2"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api:app", "--port", str(a.port),
                            "--log-level", "warning"], env=env)
    try:
        h = httpx.Client(base_url=f"http://127.0.0.1:{a.port}", timeout=60)
        for _ in range(200):
            try:
                h.get("/health")
                break
            except httpx.ConnectError:
                time.sleep(0.1)
        if fresh:
            print("Creating demo data (about 30 seconds the first time)...", flush=True)
            seed(h, a.reference)
        print(f"""
StringCoach demo is running:  http://localhost:{a.port}

  Teacher:  {TEACHER_EMAIL}   password: {PASSWORD}
  Student:  {STUDENT_EMAIL}   password: {PASSWORD}
  (Meera, meera@demo.in, has already submitted one take with mistakes.)

Use one normal browser window for the teacher and one private/incognito
window for the student. Press Ctrl+C to stop.
""", flush=True)
        srv.wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.terminate()
        srv.wait()


if __name__ == "__main__":
    main()
