"""
Results dashboard for the NUERiQ String Testing page: API rules and the
dashboard in a real browser.

    python -m pytest tests/test_tester_results.py -v
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
import numpy as np
import pytest
import soundfile as sf

KEY = "test-dashboard-key"
PORT = 8811
BASE = f"http://127.0.0.1:{PORT}"


def report(name="Asha", percent=0.9, email="leak@example.com"):
    return {"app": "NUERiQ String Testing", "version": 1, "createdAt": "2026-10-12T10:00:00Z",
            "tester": {"name": name, "email": email, "piece": "G major scale"},
            "instrument": {"finalTruth": "violin", "detected": "violin"},
            "recording": {"seconds": 12.0}, "quality": {"recordAgainSuggested": False, "flags": []},
            "playing": {"notes": 10, "within15": 0.8, "lowest": "G3", "highest": "D5", "meanAbsCents": 7},
            "verification": {"right": 9, "corrected": 1, "missed": 0, "extra": 0, "played": 10, "percent": percent,
                             "strip": [{"t": 0.5 * i, "note": "A4", "kind": "right" if i else "corrected"} for i in range(10)]},
            "intonation": {"byString": True, "instrument": "violin",
                           "rows": [{"label": "String 2 (A)", "notes": 6, "meanAbsCents": 5, "within15": 1.0}]},
            "keys": [], "corrections": [], "correctionSummary": {}}


def wav_bytes(seconds=2.0):
    buf = io.BytesIO()
    t = np.arange(int(44100 * seconds)) / 44100
    sf.write(buf, 0.3 * np.sin(2 * np.pi * 440 * t), 44100, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture(scope="module")
def server():
    data = tempfile.mkdtemp(prefix="tester_results_")
    env = {**os.environ, "STRINGCOACH_DATA": data, "START_WORKERS": "0", "TESTER_DASHBOARD_KEY": KEY}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api:app", "--port", str(PORT),
                            "--log-level", "warning"], env=env)
    h = httpx.Client(base_url=BASE, timeout=30)
    for _ in range(150):
        try:
            h.get("/health")
            break
        except httpx.ConnectError:
            time.sleep(0.1)
    yield h
    srv.terminate()
    srv.wait()
    shutil.rmtree(data, ignore_errors=True)


def add(h, rep=None, audio=True):
    r = h.post("/tester-api/results", content=json.dumps(rep or report()))
    assert r.status_code == 200, r.text
    d = r.json()
    if audio:
        assert h.put(f"/tester-api/results/{d['id']}/audio", content=wav_bytes(),
                      headers={"X-Upload-Token": d["upload_token"]}).status_code == 200
    return d


def test_api_rules(server):
    h, k = server, {"X-Dashboard-Key": KEY}
    d = add(server, audio=False)
    tok, rid = d["upload_token"], d["id"]
    put = lambda body, token: h.put(f"/tester-api/results/{rid}/audio", content=body, headers={"X-Upload-Token": token})
    assert put(wav_bytes(), "wrong").status_code == 403
    assert put(b"not a wav file at all", tok).status_code == 422
    assert put(wav_bytes(), tok).status_code == 200
    assert put(wav_bytes(), tok).status_code == 403                    # one-time token
    assert h.get("/tester-api/results").status_code == 401             # key required
    assert h.get("/tester-api/results", headers={"X-Dashboard-Key": "nope"}).status_code == 401
    row = next(r for r in h.get("/tester-api/results", headers=k).json() if r["id"] == rid)
    assert row["has_audio"] and row["verified_pct"] == 0.9 and row["created"].endswith("Z")
    assert "leak@example.com" not in json.dumps(h.get(f"/tester-api/results/{rid}", headers=k).json())
    assert h.get(f"/tester-api/results/{rid}/audio?key={KEY}").content[:4] == b"RIFF"
    assert rid in h.get(f"/tester-api/results.csv?key={KEY}").text
    assert h.post("/tester-api/results", content=json.dumps({"something": "else"})).status_code == 422
    assert h.post("/tester-api/results", content=b"x" * (3 * 1024 * 1024)).status_code == 413
    assert h.delete(f"/tester-api/results/{rid}", headers=k).status_code == 200
    assert h.get(f"/tester-api/results/{rid}", headers=k).status_code == 404


def test_dashboard_in_a_real_browser(server):
    from playwright.sync_api import expect, sync_playwright
    if os.path.isdir("/opt/pw-browsers"):
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    add(server, report("Meera", 0.95))
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/tester/dashboard.html")
        page.fill("#key", "wrong-key")
        page.click("#loginForm button")
        expect(page.locator("#loginErr")).to_contain_text("isn't right")
        page.fill("#key", KEY)
        page.click("#loginForm button")
        rows = page.locator("#resultsTable tbody tr[data-id]")
        expect(rows.filter(has_text="Meera")).to_contain_text("95%")
        n = rows.count()
        # a test submitted while the dashboard is open appears by itself
        add(server, report("Kabir", 0.8))
        expect(rows).to_have_count(n + 1, timeout=25_000)
        expect(page.locator("#resultsTable tr.new")).to_contain_text("Kabir")
        rows.filter(has_text="Kabir").click()
        detail = page.locator("#detail")
        expect(detail).to_contain_text("App vs what was really played")
        expect(detail).to_contain_text("String 2 (A)")
        expect(detail.locator(".vstrip i")).to_have_count(10)
        page.wait_for_function("() => { const a = document.querySelector('#detail audio'); a.load(); return true; }")
        page.wait_for_function("() => document.querySelector('#detail audio').duration > 1.5", timeout=10_000)
        assert page.locator("#csv").get_attribute("href").startswith("/tester-api/results.csv?key=")
        assert not errors, errors
        b.close()
