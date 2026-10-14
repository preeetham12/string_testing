"""
Part 7 - teacher studio and dashboard, tested in a real browser.

Usage: python -m pytest tests/test_teacher_web.py -v
Same setup as tests/test_web.py (Chromium via Playwright; axe-core for
accessibility; the microphone is fed from a WAV file).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
from contextlib import contextmanager

warnings.filterwarnings("ignore")
if os.path.isdir("/opt/pw-browsers"):  # a shared browser install, if this machine has one
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
AXE = os.environ.get("AXE_PATH", "/tmp/axe/node_modules/axe-core/axe.min.js")

import httpx
import librosa
import numpy as np
import pytest
import soundfile as sf
from playwright.sync_api import expect, sync_playwright

from backend.client import auth, upload_file
from engine.synth import SR, TEACHER, render

PORT = 8802
BASE = f"http://127.0.0.1:{PORT}"
TMP = tempfile.mkdtemp(prefix="stringcoach_teacher_")
LESSON = TEACHER * 4
SHOTS = "output/part7"
F = {}


def wav(name, notes, noise=-40, seed=1):
    p = os.path.join(TMP, f"{name}.wav")
    sf.write(p, render(notes, noise_db=noise, seed=seed), SR, subtype="FLOAT")
    F[name] = p
    return p


def mic(name, notes):
    y = np.concatenate([np.zeros(int(0.8 * SR)), render(notes, noise_db=-40, seed=7), np.zeros(SR)])
    p = os.path.join(TMP, f"mic_{name}.wav")
    sf.write(p, np.clip(librosa.resample(y, orig_sr=SR, target_sr=44100), -1, 1), 44100, subtype="PCM_16")
    F[f"mic_{name}"] = (p, len(y) / SR)


def api():
    return httpx.Client(base_url=BASE, timeout=60)


def login(h, email):
    return h.post("/auth/login", json={"email": email, "password": "violin-2026"}).json()


def register(h, name, role):
    email = f"{name.lower()}@studio.in"
    h.post("/auth/register", json={"name": name, "email": email, "password": "violin-2026", "role": role})
    return email


@pytest.fixture(scope="session", autouse=True)
def server():
    wav("teacher", LESSON)
    slip = [dict(n) for n in LESSON]
    slip[4]["midi"] += 1                                   # teacher's slip: G5 for F#5
    wav("slip", slip, seed=2)
    wav("stray", LESSON[:5] + [{"midi": 64, "dur": 0.3}] + LESSON[5:], seed=3)
    wav("noisy", LESSON, noise=-3, seed=4)
    # a teacher recording in a real room where echo blends one repeated pair
    # (notes 5-6, F#5 F#5) into one note: the case the Split tool is for
    from tests.test_web import realistic
    p = os.path.join(TMP, "echo_ref.wav")
    sf.write(p, realistic(render(LESSON, noise_db=-40, seed=3), drr_db=6, rt60=0.6,
                          bow_snr_db=20, seed=3), SR, subtype="FLOAT")
    F["echo_ref"] = p
    wav("good", LESSON, noise=-35, seed=5)
    bad = [dict(n) for n in LESSON]
    bad[18]["midi"] += 1                                   # wrong note 19, section 3
    wav("mistake", bad, noise=-35, seed=6)
    mic("short", TEACHER)

    env = {**os.environ, "STRINGCOACH_DATA": os.path.join(TMP, "server"), "WORKERS": "2"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api:app", "--port", str(PORT),
                            "--log-level", "warning"], env=env)
    h = api()
    for _ in range(150):
        try:
            h.get("/health")
            break
        except httpx.ConnectError:
            time.sleep(0.1)
    for name, role in [("Priya", "teacher"), ("Ravi", "teacher"), ("Arjun", "student"), ("Meera", "student")]:
        register(h, name, role)
    os.makedirs(SHOTS, exist_ok=True)
    yield
    srv.terminate()
    srv.wait()
    shutil.rmtree(TMP, ignore_errors=True)


@contextmanager
def browser(who="priya", width=1280, mic_file="short"):
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                    f"--use-file-for-fake-audio-capture={F['mic_' + mic_file][0]}",
                                    "--autoplay-policy=no-user-gesture-required"])
        page = b.new_context(viewport={"width": width, "height": 900}).new_page()
        if who:
            r = login(api(), f"{who}@studio.in")
            page.goto(BASE + "/app/")
            page.evaluate("([t,u]) => { localStorage.setItem('sc_token', t); localStorage.setItem('sc_user', JSON.stringify(u)); }",
                          [r["token"], r["user"]])
        try:
            yield page
        finally:
            b.close()


def setup_class(name, students=("arjun",)):
    """Class + students through the API (the UI for this is tested separately)."""
    h, tt = api(), login(api(), "priya@studio.in")["token"]
    c = h.post("/classes", headers=auth(tt), json={"name": name, "instrument": "violin"}).json()
    for s in students:
        h.post(f"/classes/{c['id']}/students", headers=auth(tt), json={"email": f"{s}@studio.in"})
    return c["id"]


def lesson_via_ui(page, class_id, title, ref):
    page.goto(f"{BASE}/app/#/new/{class_id}")
    page.fill("#nl-title", title)
    page.set_input_files("#nl-file", F[ref])
    page.click("#lesson-form button[type=submit]")
    expect(page.locator("#st-title")).to_have_text(title, timeout=20_000)
    expect(page.locator("#st-status")).not_to_have_text("Analysing your recording…", timeout=60_000)
    return int(page.url.split("/")[-1])


def axe(page):
    page.add_script_tag(path=AXE)
    return page.evaluate("async () => (await axe.run()).violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id + ': ' + v.nodes.map(n => n.target).join(' '))")


# ---------------- building a lesson ----------------
def test_teacher_builds_and_publishes_a_lesson_in_the_ui():
    with browser(who=None) as page:
        page.goto(BASE + "/app/")
        page.click("summary")
        page.fill("#name", "Kavya")
        page.fill("#reg-email", "kavya@studio.in")
        page.fill("#new-password", "violin-2026")
        page.check("input[name=role][value=teacher]")
        page.click("#register-form button[type=submit]")
        expect(page.locator("h1")).to_have_text("Your classes")
        page.fill("#class-name", "Violin Grade 2")
        page.click("#new-class button[type=submit]")
        card = page.locator(".class-card", has_text="Violin Grade 2")
        card.locator("input[type=email]").fill("meera@studio.in")
        card.locator(".add-student button").click()
        expect(card.locator(".students")).to_contain_text("Meera")
        page.screenshot(path=f"{SHOTS}/1_classes.png")
        card.get_by_role("link", name="New lesson").click()
        page.fill("#nl-title", "Twinkle, 4 phrases")
        page.select_option("#nl-level", "beginner")
        page.fill("#nl-max", "3")
        page.set_input_files("#nl-file", F["teacher"])
        page.click("#lesson-form button[type=submit]")
        expect(page.locator("#st-status")).to_have_text("Ready to check and publish", timeout=60_000)
        expect(page.locator("#st-notes .note-chip")).to_have_count(28)
        expect(page.locator("#st-ranges")).to_have_value("1-7, 8-14, 15-21, 22-28")
        page.fill("#st-ranges", "1-14, 15-28")
        page.click("#st-sections button[type=submit]")
        expect(page.locator("#status")).to_have_text("Saved 2 sections")
        page.screenshot(path=f"{SHOTS}/2_studio_ready.png", full_page=True)
        page.click("#st-publish")
        expect(page.locator("#st-status")).to_have_text("Published", timeout=10_000)
        aid = int(page.url.split("/")[-1])
    # the student now sees it, with the teacher's 2 sections and settings
    h = api()
    st = login(h, "meera@studio.in")["token"]
    a = h.get(f"/assignments/{aid}", headers=auth(st)).json()
    assert a["status"] == "published" and len(a["sections"]) == 2
    assert a["level"] == "beginner" and a["max_attempts"] == 3


def test_teacher_records_the_reference_in_the_browser():
    cid = setup_class("Recorded in browser")
    with browser(mic_file="short") as page:
        page.goto(f"{BASE}/app/#/new/{cid}")
        page.fill("#nl-title", "Recorded here")
        page.check("input[name=source][value=record]")
        page.click("#nl-record")
        expect(page.locator("#nl-record")).to_have_attribute("aria-pressed", "true", timeout=10_000)
        # the live pitch graph draws the teacher's playing WHILE recording
        page.wait_for_timeout(2500)
        lane = page.evaluate("""() => {
            const c = document.querySelector('#lane');
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let ink = 0, you = 0;
            for (let i = 0; i < d.length; i += 4) {
                if (d[i + 3] > 0) ink++;
                if (d[i] > 150 && d[i + 1] > 80 && d[i + 1] < 170 && d[i + 2] < 90 && d[i + 3] > 200) you++;
            }
            return { ink: ink / (c.width * c.height), you, heard: stringcoach.history().filter(h => h.midi !== null).length };
        }""")
        assert lane["heard"] > 20 and lane["you"] > 200, lane   # orange pitch line is drawn
        page.screenshot(path=f"{SHOTS}/0_teacher_recording_graph.png")
        page.wait_for_timeout(int((F["mic_short"][1] - 0.6) * 1000) - 2500)
        page.click("#nl-record")
        expect(page.locator("#nl-recorded")).to_contain_text("Recorded")
        page.click("#lesson-form button[type=submit]")
        expect(page.locator("#st-status")).to_have_text("Ready to check and publish", timeout=60_000)
        names = page.locator("#st-notes .note-chip").all_inner_texts()
        assert [n.split()[1] for n in names] == ["A4", "A4", "E5", "E5", "F#5", "F#5", "E5"], names


# ---------------- correcting notes ----------------
def test_teacher_fixes_their_own_slip_and_students_are_marked_fairly():
    cid = setup_class("Slip fix")
    with browser() as page:
        aid = lesson_via_ui(page, cid, "Slip", "slip")
        chip5 = page.locator("#st-notes .note-chip").nth(4)
        expect(chip5).to_have_text("5 G5")
        chip5.click()
        expect(page.locator("#st-note-info")).to_contain_text("Note 5: G5")
        page.click("#st-down")
        expect(page.locator("#status")).to_have_text("Note 5 is now F#5")
        expect(page.locator("#st-notes .note-chip").nth(4)).to_have_text("5 F#5")
        expect(page.locator("#st-notes .note-chip.corrected")).to_have_count(1)
        page.screenshot(path=f"{SHOTS}/3_note_fixed.png", full_page=True)
        page.click("#st-publish")
        expect(page.locator("#st-status")).to_have_text("Published", timeout=10_000)
    h, st = api(), login(api(), "arjun@studio.in")["token"]
    _, rec = upload_file(h, st, F["good"])
    at = h.post(f"/assignments/{aid}/attempts", headers=auth(st), json={"recording_id": rec}).json()
    for _ in range(200):
        r = h.get(f"/attempts/{at['id']}", headers=auth(st)).json()
        if r["status"] not in ("queued", "analysing"):
            break
        time.sleep(0.3)
    assert r["score"] == 100 and r["result"]["counts"]["wrong note"] == 0


def test_teacher_deletes_a_stray_note():
    cid = setup_class("Stray")
    with browser() as page:
        lesson_via_ui(page, cid, "Stray", "stray")
        expect(page.locator("#st-notes .note-chip")).to_have_count(29)
        page.locator("#st-notes .note-chip").nth(5).click()
        expect(page.locator("#st-note-info")).to_contain_text("Note 6: E4")
        page.click("#st-delete")
        expect(page.locator("#st-notes .note-chip")).to_have_count(28)
        expect(page.locator("#st-ranges")).to_have_value("1-7, 8-14, 15-21, 22-28")


def test_teacher_splits_a_note_blended_by_room_echo():
    cid = setup_class("Echo room")
    with browser() as page:
        aid = lesson_via_ui(page, cid, "Echo", "echo_ref")
        chips = page.locator("#st-notes .note-chip")
        expect(chips).to_have_count(27)                       # one repeated pair blended
        expect(page.locator("#st-notes-hint")).to_contain_text("may be two notes")
        expect(page.locator("#st-notes .note-chip.suggest")).to_have_count(1)
        page.locator("#st-notes .note-chip.suggest").click()
        expect(page.locator("#st-split-hint")).to_be_visible()
        assert axe(page) == [], "studio with a suggested split selected"   # incl. contrast
        page.screenshot(path=f"{SHOTS}/7_split_suggestion.png", full_page=True)
        page.click("#st-split")
        expect(page.locator("#status")).to_contain_text("split into two")
        expect(chips).to_have_count(28)
        names = [c.split()[1] for c in chips.all_inner_texts()]
        assert names == ["A4", "A4", "E5", "E5", "F#5", "F#5", "E5"] * 4, names
        page.click("#st-publish")
        expect(page.locator("#st-status")).to_have_text("Published", timeout=10_000)
    # a student who plays it correctly now gets a clean review
    h, st = api(), login(api(), "arjun@studio.in")["token"]
    _, rec = upload_file(h, st, F["good"])
    at = h.post(f"/assignments/{aid}/attempts", headers=auth(st), json={"recording_id": rec}).json()
    for _ in range(200):
        r = h.get(f"/attempts/{at['id']}", headers=auth(st)).json()
        if r["status"] not in ("queued", "analysing"):
            break
        time.sleep(0.3)
    assert r["status"] == "done" and r["result"]["counts"]["missed"] == 0, r["result"]["counts"]
    assert r["score"] >= 95, r["score"]


def test_unusable_reference_explains_why():
    cid = setup_class("Noisy")
    with browser() as page:
        lesson_via_ui(page, cid, "Noisy", "noisy")
        expect(page.locator("#st-status")).to_have_text("Recording problem")
        expect(page.locator(".qc-fail")).to_contain_text("background noise")
        expect(page.locator("#st-publish")).to_have_count(0)


def test_playback_highlights_the_current_note():
    cid = setup_class("Playback")
    with browser() as page:
        aid = lesson_via_ui(page, cid, "Playback", "teacher")
        page.click("#st-play")
        page.wait_for_timeout(2500)
        state = page.evaluate("() => ({t: document.querySelector('#st-audio').currentTime, note: stringcoach.studioNote})")
        notes = api().get(f"/assignments/{aid}", headers=auth(login(api(), "priya@studio.in")["token"])).json()
        t = state["t"] - notes["ref_offset"]
        expected = next(k for k, n in enumerate(notes["notes"]) if n["start"] <= t < n["end"] + 0.05)
        assert abs(state["note"] - expected) <= 1, (state, expected)
        expect(page.locator("#st-now")).to_contain_text("Now: note")


# ---------------- dashboard and reviewing takes ----------------
def published_with_takes():
    cid = setup_class("Dashboard class", students=("arjun", "meera"))
    h = api()
    tt = login(h, "priya@studio.in")["token"]
    _, rec = upload_file(h, tt, F["teacher"])
    a = h.post("/assignments", headers=auth(tt), json={"class_id": cid, "title": "Dashboard", "recording_id": rec}).json()
    while h.get(f"/assignments/{a['id']}", headers=auth(tt)).json()["status"] == "processing":
        time.sleep(0.2)
    h.post(f"/assignments/{a['id']}/publish", headers=auth(tt))
    ids = []
    for who, take in [("arjun", "mistake"), ("meera", "mistake"), ("meera", "good")]:
        st = login(h, f"{who}@studio.in")["token"]
        _, rec = upload_file(h, st, F[take])
        ids.append(h.post(f"/assignments/{a['id']}/attempts", headers=auth(st), json={"recording_id": rec}).json()["id"])
    for i in ids:
        while h.get(f"/attempts/{i}", headers=auth(tt)).json()["status"] in ("queued", "analysing"):
            time.sleep(0.3)
    return a["id"], cid


def test_dashboard_heatmap_progress_and_comment():
    aid, _ = published_with_takes()
    with browser() as page:
        page.goto(f"{BASE}/app/#/t/{aid}")
        expect(page.locator("#st-dash")).to_be_visible(timeout=10_000)
        cells = page.locator(".heat-cell")
        expect(cells).to_have_count(4)
        # Arjun's latest take has the wrong note in section 3; Meera's latest is perfect
        expect(cells.nth(2)).to_contain_text("1 of 2 passed")
        expect(cells.nth(0)).to_contain_text("2 of 2 passed")
        expect(page.locator("#st-hard li").first).to_contain_text("Note 19")
        rows = page.locator("#st-progress tbody tr")
        expect(rows).to_have_count(2)
        expect(rows.filter(has_text="Meera")).to_contain_text("All ✓")
        expect(rows.filter(has_text="Meera")).to_contain_text("→")         # score trend
        # dashboard comes before the lesson details once published; sections locked
        assert page.evaluate("() => document.querySelector('#st-dash').compareDocumentPosition(document.querySelector('.player')) & Node.DOCUMENT_POSITION_FOLLOWING")
        expect(page.locator("#st-ranges")).to_be_disabled()
        page.screenshot(path=f"{SHOTS}/4_dashboard.png", full_page=True)
        rows.filter(has_text="Arjun").get_by_role("link").click()
        expect(page.locator("#tk-title")).to_contain_text("Arjun")
        expect(page.locator("#tk-report .feedback")).to_contain_text("instead of")
        page.wait_for_function("() => document.querySelector('#tk-audio').duration > 5", timeout=10_000)
        page.fill("#tk-body", "Watch the G in phrase 3; use your 2nd finger.")
        page.fill("#tk-override", "85")
        page.click("#tk-comment button[type=submit]")
        expect(page.locator("#tk-report .comment")).to_contain_text("2nd finger")
        expect(page.locator("#tk-report .score")).to_contain_text("85")
        expect(page.locator("#tk-report .score")).to_contain_text("automatic score")  # both shown
        page.screenshot(path=f"{SHOTS}/5_review_take.png", full_page=True)
    h = api()
    st = login(h, "arjun@studio.in")["token"]
    seen = h.get(f"/assignments/{aid}/attempts", headers=auth(st)).json()[-1]
    assert seen["teacher_score"] == 85 and "2nd finger" in seen["comments"][0]["body"]


# ---------------- privacy, accessibility, phones ----------------
def test_students_and_other_teachers_cannot_use_the_studio():
    cid = setup_class("Private")
    with browser() as page:
        aid = lesson_via_ui(page, cid, "Private", "teacher")
    with browser(who="arjun") as page:                      # student
        page.goto(f"{BASE}/app/#/t/{aid}")
        expect(page.locator("h1")).to_have_text("Your lessons")  # student pages only
        expect(page.locator("#st-publish")).to_have_count(0)
    with browser(who="ravi") as page:                       # a different teacher
        page.goto(f"{BASE}/app/#/t/{aid}")
        expect(page.locator("#view")).to_contain_text("Assignment not found")


def test_teacher_pages_have_no_serious_accessibility_issues():
    aid, cid = published_with_takes()
    with browser() as page:
        for url in ["#/home", f"#/new/{cid}", f"#/t/{aid}"]:
            page.goto(f"{BASE}/app/{url}")
            page.wait_for_timeout(1500)
            assert axe(page) == [], url
        page.goto(f"{BASE}/app/#/t/{aid}")
        page.locator("#st-progress tbody a").first.click()
        expect(page.locator("#tk-detail")).to_be_visible()
        assert axe(page) == [], "takes page"


def test_teacher_pages_fit_a_phone():
    aid, _ = published_with_takes()
    with browser(width=375) as page:
        for url in ["#/home", f"#/t/{aid}"]:
            page.goto(f"{BASE}/app/{url}")
            page.wait_for_timeout(1500)
            overflow = page.evaluate("() => document.scrollingElement.scrollWidth - window.innerWidth")
            assert overflow <= 0, f"{url} scrolls sideways by {overflow}px"
        page.screenshot(path=f"{SHOTS}/6_phone_dashboard.png", full_page=True)
