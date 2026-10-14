"""
Part 6 - end-to-end browser tests.

Real Chromium (via Playwright) uses the web app exactly as a student would.
The browser's microphone is fed from a WAV file, so a "student" plays into the
app: live pitch, recording, upload, analysis and the report are all real.

Usage: python -m pytest tests/test_web.py -v
Needs Chromium for Playwright (PLAYWRIGHT_BROWSERS_PATH) and axe-core
(AXE_PATH) for the accessibility checks.
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

PORT = 8801
BASE = f"http://127.0.0.1:{PORT}"
TMP = tempfile.mkdtemp(prefix="stringcoach_web_")
LESSON = TEACHER * 4
MIC = {}
SHOTS = "output/part6"


def realistic(y, drr_db=6.0, rt60=0.6, bow_snr_db=20.0, seed=11):
    """A violin in a real room, as a phone or laptop hears it.

    drr_db:     direct-to-reverberant ratio. About +6 dB for a mic ~1 m away in a
                furnished room; 0 dB is a phone across the room. (A first version
                of this was -14 dB, like recording from the back of a hall, which
                made the tests unrealistically harsh.)
    rt60:       how long the room's echo takes to die away by 60 dB.
    bow_snr_db: tone-to-bow-noise ratio; real bowed strings are about 15-25 dB.
    """
    rng = np.random.default_rng(seed)
    # bow noise is made by the instrument, so it is added BEFORE the room
    env = np.sqrt(np.convolve(y ** 2, np.ones(400) / 400, mode="same"))
    dry = y + rng.normal(0, 1, len(y)) * env * 10 ** (-bow_snr_db / 20)
    n = int(rt60 * SR)
    tail = rng.normal(0, 1, n) * np.exp(-6.9 * np.arange(n) / SR / rt60)
    tail[: int(0.005 * SR)] = 0                        # first reflections after ~5 ms
    tail *= np.sqrt(10 ** (-drr_db / 10) / np.sum(tail ** 2))
    ir = tail
    ir[0] = 1.0                                       # the direct sound
    wet = np.convolve(dry, ir)[:len(y)]
    return wet / np.max(np.abs(wet)) * 0.5


def mic_wav(name, notes=None, post=None, lead=0.8):
    """A WAV the fake microphone plays: silence, the performance, silence."""
    y = render(notes, noise_db=-38, seed=len(MIC) + 3) if notes else np.zeros(SR * 6)
    if post:
        y = post(y)
    y = np.concatenate([np.zeros(int(lead * SR)), y, np.zeros(SR)])
    y44 = librosa.resample(y, orig_sr=SR, target_sr=44100)
    p = os.path.join(TMP, f"{name}.wav")
    sf.write(p, np.clip(y44, -1, 1), 44100, subtype="PCM_16")
    MIC[name] = (p, len(y) / SR)
    return p


@pytest.fixture(scope="session", autouse=True)
def server():
    mistakes = [dict(n) for n in LESSON]
    mistakes[15]["cents"] = -32          # section 3: flat
    mistakes[18]["midi"] += 1            # section 3: wrong note
    mic_wav("mistakes", mistakes)
    mic_wav("section3", LESSON[14:21])
    mic_wav("perfect", LESSON)
    mic_wav("clipped", LESSON[:7], post=lambda y: np.clip(y * 8, -1, 1))
    mic_wav("silent", None, post=lambda y: y + np.random.default_rng(0).normal(0, 0.002, len(y)))
    # loud sound but no notes - like a TV in the room before the student plays
    mic_wav("quiet_then_play", TEACHER, lead=2.5)   # room is quiet, then a note
    from tests.speech_like import speech_like
    mic_wav("talking", None, post=lambda y: speech_like(12, 5))  # someone talking, no violin
    mic_wav("noise_only", None, post=lambda y: y + np.random.default_rng(1).normal(0, 0.08, len(y)))
    # a REAL-WORLD style take: room echo and bow noise (the scratchy texture of a
    # bowed string). The first browser detector heard 0% of this as notes and
    # blocked it before upload - found when the app was tested with a real violin.
    mic_wav("realistic", mistakes, post=realistic)

    env = {**os.environ, "STRINGCOACH_DATA": os.path.join(TMP, "server"), "WORKERS": "2",
           "START_WORKERS": "1"}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api:app", "--port", str(PORT),
                            "--log-level", "warning"], env=env)
    h = httpx.Client(base_url=BASE, timeout=60)
    for _ in range(150):
        try:
            h.get("/health")
            break
        except httpx.ConnectError:
            time.sleep(0.1)
    # teacher sets up the lesson through the API (the teacher UI is Part 7)
    for name, role in [("Priya", "teacher"), ("Arjun", "student"), ("Meera", "student")]:
        h.post("/auth/register", json={"name": name, "email": f"{name.lower()}@web.in",
                                       "password": "violin-2026", "role": role})
    tt = token(h, "priya")
    c = h.post("/classes", headers=auth(tt), json={"name": "Violin Grade 2", "instrument": "violin"}).json()
    h.post(f"/classes/{c['id']}/students", headers=auth(tt), json={"email": "arjun@web.in"})
    ref = os.path.join(TMP, "teacher.wav")
    sf.write(ref, render(LESSON, noise_db=-40), SR, subtype="FLOAT")
    _, rec = upload_file(h, tt, ref)
    a = h.post("/assignments", headers=auth(tt), json={"class_id": c["id"], "title": "Twinkle, 4 phrases",
                                                       "recording_id": rec, "max_attempts": 5}).json()
    while h.get(f"/assignments/{a['id']}", headers=auth(tt)).json()["status"] == "processing":
        time.sleep(0.2)
    h.post(f"/assignments/{a['id']}/publish", headers=auth(tt))
    MIC["assignment"] = h.get(f"/assignments/{a['id']}", headers=auth(tt)).json()
    os.makedirs(SHOTS, exist_ok=True)
    yield
    srv.terminate()
    srv.wait()
    shutil.rmtree(TMP, ignore_errors=True)


def token(h, who):
    return h.post("/auth/login", json={"email": f"{who}@web.in", "password": "violin-2026"}).json()["token"]


@contextmanager
def browser(mic="perfect", who="arjun", width=1280):
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                    f"--use-file-for-fake-audio-capture={MIC[mic][0]}",
                                    "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": width, "height": 900})
        page = ctx.new_page()
        if who:  # already logged in (login itself is tested separately)
            h = httpx.Client(base_url=BASE)
            r = h.post("/auth/login", json={"email": f"{who}@web.in", "password": "violin-2026"}).json()
            page.goto(BASE + "/app/")
            page.evaluate("([t,u]) => { localStorage.setItem('sc_token', t); localStorage.setItem('sc_user', JSON.stringify(u)); }",
                          [r["token"], r["user"]])
        try:
            yield page
        finally:
            b.close()


def open_lesson(page):
    page.goto(f"{BASE}/app/#/a/{MIC['assignment']['id']}")
    expect(page.locator("#a-title")).to_have_text("Twinkle, 4 phrases")


def record(page, mic, use_space=True):
    """Start recording, let the 'student' play the whole mic file, stop."""
    if use_space:
        page.locator("body").press("Space")
    else:
        page.click("#record")
    expect(page.locator("#record")).to_have_attribute("aria-pressed", "true", timeout=10_000)
    page.wait_for_timeout(int((MIC[mic][1] - 0.6) * 1000))
    page.locator("body").press("Space") if use_space else page.click("#record")


def attempts_via_api():
    h = httpx.Client(base_url=BASE)
    return h.get(f"/assignments/{MIC['assignment']['id']}/attempts",
                 headers=auth(token(h, "arjun"))).json()


# ---------------- login ----------------
def test_login_screen(server):
    with browser(who=None) as page:
        page.goto(BASE + "/")
        expect(page.locator("h1")).to_have_text("Practise with your teacher's recording")
        page.fill("#email", "not-an-email")
        page.locator("#current-password").focus()  # leaving the field shows the error
        expect(page.locator("#email + .error-msg")).to_be_visible()
        assert page.get_attribute("#email", "aria-invalid") == "true"
        page.fill("#email", "arjun@web.in")
        page.fill("#current-password", "wrong-password")
        page.click("#login-form button[type=submit]")
        expect(page.locator("#status")).to_have_text("Wrong email or password")
        page.fill("#current-password", "violin-2026")
        page.click("#login-form button[type=submit]")
        expect(page.locator(".lesson-list a")).to_contain_text("Twinkle, 4 phrases")
        page.screenshot(path=f"{SHOTS}/1_lessons.png")


def test_accessibility_no_serious_issues(server):
    with browser(who=None) as page:
        page.goto(BASE + "/app/")
        page.add_script_tag(path=AXE)
        bad = page.evaluate("async () => (await axe.run()).violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id)")
        assert bad == [], f"login page: {bad}"
    with browser() as page:
        open_lesson(page)
        page.add_script_tag(path=AXE)
        bad = page.evaluate("async () => (await axe.run()).violations.filter(v => ['serious','critical'].includes(v.impact)).map(v => v.id)")
        assert bad == [], f"practice page: {bad}"


# ---------------- the pitch lane ----------------
def test_lane_shows_the_lesson_before_recording(server):
    """Before the mic starts, the student can already see the notes to play."""
    with browser() as page:
        open_lesson(page)
        page.wait_for_timeout(500)
        inked = page.evaluate("""() => {
            const c = document.querySelector('#lane');
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++;
            return n / (c.width * c.height); }""")
        assert page.evaluate("() => stringcoach.ghostDrawn") and inked > 0.01, inked


# ---------------- listening ----------------
def test_listen_to_teacher_and_jump_to_a_section(server):
    with browser() as page:
        open_lesson(page)
        page.click("#listen")
        page.wait_for_timeout(1200)
        st = page.evaluate("() => { const a = document.querySelector('#ref-audio'); return {paused: a.paused, t: a.currentTime, pitch: a.preservesPitch}; }")
        assert not st["paused"] and st["t"] > 0.5 and st["pitch"]
        page.click("#listen")  # stop
        page.get_by_role("radio", name="Section 3").click()
        page.select_option("#speed", "0.5")
        page.click("#listen")
        page.wait_for_timeout(600)
        a = MIC["assignment"]
        s3 = a["sections"][2]["t_start"] + a["ref_offset"]
        st = page.evaluate("() => { const a = document.querySelector('#ref-audio'); return {t: a.currentTime, rate: a.playbackRate}; }")
        assert st["rate"] == 0.5 and s3 - 0.3 <= st["t"] <= s3 + 0.8, (st, s3)


# ---------------- recording a full take ----------------
def test_full_take_live_pitch_and_report(server):
    with browser(mic="mistakes") as page:
        open_lesson(page)
        record(page, "mistakes")
        expect(page.locator("#status")).not_to_have_text("Recording the whole piece")  # status moves on
        # the live pitch the student saw while playing (browser YIN, on the audio thread)
        hist = page.evaluate("() => stringcoach.history()")
        notes = {round(h["midi"]) for h in hist if h["midi"] is not None}
        assert {69, 76, 78, 79} <= notes, notes          # A4, E5, F#5 and the wrong G5
        steady = [h["midi"] for h in hist if h["midi"] is not None]
        cents = np.array([(m - round(m)) * 100 for m in steady])
        assert np.median(np.abs(cents)) < 8, np.median(np.abs(cents))  # in-tune notes read in tune
        assert page.evaluate("() => stringcoach.ghostAligned()")
        page.screenshot(path=f"{SHOTS}/2_recording_lane.png")
        # the review arrives
        report = page.locator("#report")
        expect(report).to_be_visible(timeout=120_000)
        strip = report.locator(".strip .seg")
        expect(strip).to_have_count(4)
        assert [("fail" in strip.nth(k).get_attribute("class")) for k in range(4)] == [False, False, True, False]
        expect(report.locator(".feedback")).to_contain_text("instead of F#5")
        expect(report.locator(".feedback")).to_contain_text("cents flat")
        expect(page.locator("#a-attempts")).to_contain_text("4 of 5 full takes left")
        expect(report.get_by_role("button", name="Practise section 3")).to_be_visible()
        report.screenshot(path=f"{SHOTS}/3_report.png")


def test_realistic_bowed_take_is_compared(server):
    """Echo + bow noise: must be uploaded, compared and reviewed (not blocked)."""
    with browser(mic="realistic") as page:
        open_lesson(page)
        record(page, "realistic")
        hist = page.evaluate("() => stringcoach.history()")
        heard = [round(h["midi"]) for h in hist if h["midi"] is not None]
        assert len(heard) > 0.5 * len(hist), f"live tuner heard only {len(heard)} of {len(hist)}"
        assert sum(m in (69, 76, 78, 79) for m in heard) > 0.9 * len(heard)  # right notes, not octaves
        report = page.locator("#report")
        expect(report).to_be_visible(timeout=120_000)
        expect(report.locator(".feedback")).to_contain_text("instead of F#5")
        strip = report.locator(".strip .seg")
        assert "fail" in strip.nth(2).get_attribute("class")


def test_section_practice_take(server):
    with browser(mic="section3") as page:
        open_lesson(page)
        page.get_by_role("radio", name="Section 3").click()
        before = len(attempts_via_api())
        record(page, "section3", use_space=False)
        report = page.locator("#report")
        expect(report).to_be_visible(timeout=120_000)
        expect(report.locator("h2")).to_have_text("Section 3 practice")
        score = int(report.locator(".score").inner_text().split()[0])
        assert score >= 95, score
        expect(page.get_by_role("radio", name="Section 3, passed")).to_be_visible()
        after = attempts_via_api()
        assert len(after) == before + 1 and after[-1]["section_id"] == 3


# ---------------- problems caught on the device ----------------
def test_distorted_take_is_stopped_before_upload(server):
    before = len(attempts_via_api())
    with browser(mic="clipped") as page:
        open_lesson(page)
        record(page, "clipped")
        expect(page.locator("#status")).to_contain_text("distorting", timeout=10_000)
        expect(page.locator("#report")).to_be_hidden()
    assert len(attempts_via_api()) == before  # nothing uploaded, no attempt used


def test_mic_check_listens_to_the_room_then_a_note(server):
    """Stay quiet, then play: a quiet room and a good level -> 'All set'."""
    with browser(mic="quiet_then_play") as page:
        open_lesson(page)
        page.click("#mic-check")
        expect(page.locator("#status")).to_contain_text("Stay quiet", timeout=10_000)
        expect(page.locator("#status")).to_contain_text("All set", timeout=15_000)


def test_talking_is_not_scored_as_playing(server):
    """Found in user testing: talking near the microphone got a score."""
    before = len(attempts_via_api())
    with browser(mic="talking") as page:
        open_lesson(page)
        record(page, "talking")
        expect(page.locator("#status")).to_contain_text("wasn't counted", timeout=60_000)
        expect(page.locator(".qc-fail")).to_contain_text("doesn't sound like your instrument")
        expect(page.locator("#report .score")).to_have_count(0)
        expect(page.locator("#takes")).to_contain_text("no playing heard")
    after = attempts_via_api()
    assert len(after) == before + 1 and not after[-1]["counted"]


def test_silent_microphone_is_caught(server):
    """A muted or wrong microphone (-40 dBFS): told to check the mic."""
    with browser(mic="silent") as page:
        open_lesson(page)
        record(page, "silent")
        expect(page.locator("#status")).to_contain_text("too quiet", timeout=10_000)
        expect(page.locator("#status")).to_contain_text("right mic is selected")


def test_sound_but_no_playing_is_caught(server):
    """Plenty of sound but no notes (e.g. a TV): told no playing was heard."""
    before = len(attempts_via_api())
    with browser(mic="noise_only") as page:
        open_lesson(page)
        record(page, "noise_only")
        expect(page.locator("#status")).to_contain_text("hardly hear any playing", timeout=10_000)
    assert len(attempts_via_api()) == before


# ---------------- unreliable connection ----------------
def test_lost_chunk_is_resent(server):
    with browser(mic="perfect") as page:
        open_lesson(page)
        page.evaluate("() => { stringcoach.dropChunk = 1; }")
        record(page, "perfect")
        report = page.locator("#report")
        expect(report).to_be_visible(timeout=120_000)
        up = page.evaluate("() => stringcoach.uploads.at(-1)")
        assert up["resent"] == [1] and up["chunks"] > 2
        assert int(report.locator(".score").inner_text().split()[0]) >= 95


# ---------------- privacy and teacher feedback ----------------
def test_student_not_in_class_cannot_open_lesson(server):
    with browser(who="meera") as page:
        page.goto(f"{BASE}/app/#/a/{MIC['assignment']['id']}")
        expect(page.locator("#view")).to_contain_text("Assignment not found")
        page.goto(f"{BASE}/app/#/home")
        expect(page.locator("#view")).to_contain_text("not in a class yet")


def test_teacher_comment_shows_on_the_take(server):
    h = httpx.Client(base_url=BASE)
    full = [a for a in attempts_via_api() if a["section_id"] is None and a["status"] == "done"][0]
    h.post(f"/attempts/{full['id']}/comments", headers=auth(token(h, "priya")),
           json={"body": "Good bowing. Watch the G in bar 19.", "score_override": 88})
    with browser() as page:
        open_lesson(page)
        page.locator("#takes button").first.click()
        expect(page.locator("#report .comment")).to_contain_text("Watch the G in bar 19")
        expect(page.locator("#report .score")).to_contain_text("88")
        expect(page.locator("#report .score")).to_contain_text("set by your teacher")
        expect(page.locator("#report .score")).to_contain_text("automatic score")


def test_phone_width_layout(server):
    with browser(width=375) as page:
        open_lesson(page)
        overflow = page.evaluate("() => document.scrollingElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, f"page scrolls sideways by {overflow}px"
        page.screenshot(path=f"{SHOTS}/4_phone.png", full_page=True)
