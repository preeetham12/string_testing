"""
Results dashboard for the NUERiQ String Testing page (tester/).

The testing page saves each finished test here: the report (JSON), then the
recording (WAV). The dashboard (tester/dashboard.html) lists them.

Who can do what:
  - anyone using the testing page can ADD a result; the reply carries a
    one-time token, the only way to attach that result's recording
  - viewing, exporting and deleting need the dashboard key: the
    TESTER_DASHBOARD_KEY setting, or one generated on first start and saved
    to <data folder>/tester_dashboard_key.txt (printed in the server log)
"""
import csv
import io
import json
import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from backend.auth import get_db
from backend.db import DATA_DIR, TesterResult

router = APIRouter(prefix="/tester-api", tags=["string tester"])

MAX_REPORT = 2 * 1024 * 1024     # bytes
MAX_AUDIO = 60 * 1024 * 1024     # about 5 minutes of 48 kHz audio
AUDIO_DIR = os.path.join(DATA_DIR, "tester_audio")


def dashboard_key():
    key = os.environ.get("TESTER_DASHBOARD_KEY")
    if key:
        return key
    path = os.path.join(DATA_DIR, "tester_dashboard_key.txt")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_urlsafe(18))
        print(f"String tester dashboard key created: see {path}", flush=True)
    return open(path).read().strip()


def require_key(x_dashboard_key: str = Header(None), key: str = Query(None)):
    """The key comes in a header (dashboard page) or ?key= (audio player, CSV link)."""
    given = x_dashboard_key or key or ""
    if not secrets.compare_digest(given.encode(), dashboard_key().encode()):
        raise HTTPException(401, "Dashboard key required")


def _summary(r):
    # stored in UTC; SQLite drops the zone, so mark it (browsers then show local time)
    created = r.created.isoformat() + ("Z" if r.created and r.created.tzinfo is None else "") if r.created else None
    return {"id": r.id, "created": created, "name": r.name,
            "instrument": r.instrument, "detected": r.detected, "notes": r.notes,
            "verified_pct": r.verified_pct, "within15_pct": r.within15_pct, "seconds": r.seconds,
            "quality": r.quality, "has_audio": bool(r.audio_received)}


@router.post("/results")
async def add_result(request: Request, db=Depends(get_db)):
    body = await request.body()
    if len(body) > MAX_REPORT:
        raise HTTPException(413, "Report too large")
    try:
        rep = json.loads(body)
    except ValueError:
        raise HTTPException(422, "Not a JSON report")
    if not isinstance(rep, dict) or rep.get("app") != "NUERiQ String Testing":
        raise HTTPException(422, "Not a NUERiQ String Testing report")
    rep.pop("email", None)                      # never stored, even if sent
    (rep.get("tester") or {}).pop("email", None)
    v, p, ins = rep.get("verification") or {}, rep.get("playing") or {}, rep.get("instrument") or {}
    r = TesterResult(
        id=secrets.token_hex(12), name=str((rep.get("tester") or {}).get("name") or "")[:120],
        instrument=ins.get("finalTruth"), detected=ins.get("detected"),
        notes=p.get("notes"), verified_pct=v.get("percent"), within15_pct=p.get("within15"),
        seconds=(rep.get("recording") or {}).get("seconds"),
        quality="could be better" if (rep.get("quality") or {}).get("recordAgainSuggested") else "good",
        report=rep, upload_token=secrets.token_urlsafe(24))
    db.add(r)
    db.commit()
    return {"id": r.id, "upload_token": r.upload_token}


@router.put("/results/{rid}/audio")
async def add_audio(rid: str, request: Request, x_upload_token: str = Header(None), db=Depends(get_db)):
    r = db.get(TesterResult, rid)
    if r is None or r.audio_received or not x_upload_token or \
            not secrets.compare_digest(x_upload_token.encode(), (r.upload_token or "").encode()):
        raise HTTPException(403, "Not allowed")  # same answer for every refusal: reveals nothing
    body = await request.body()
    if len(body) > MAX_AUDIO:
        raise HTTPException(413, "Recording too large")
    if body[:4] != b"RIFF" or body[8:12] != b"WAVE":
        raise HTTPException(422, "Not a WAV recording")
    os.makedirs(AUDIO_DIR, exist_ok=True)
    path = os.path.join(AUDIO_DIR, f"{rid}.wav")
    with open(path, "wb") as f:
        f.write(body)
    r.audio_path, r.audio_received, r.upload_token = path, True, None   # token used up
    db.commit()
    return {"ok": True}


@router.get("/results", dependencies=[Depends(require_key)])
def list_results(db=Depends(get_db)):
    return [_summary(r) for r in db.query(TesterResult).order_by(TesterResult.created.desc())]


@router.get("/results.csv", dependencies=[Depends(require_key)])
def export_csv(db=Depends(get_db)):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", "tester", "instrument played", "instrument detected", "notes",
                "notes heard correctly (%)", "notes within 15 cents (%)", "seconds", "recording quality",
                "notes corrected", "notes missed", "extra notes", "id"])
    for r in db.query(TesterResult).order_by(TesterResult.created.desc()):
        v = (r.report or {}).get("verification") or {}
        pct = lambda x: "" if x is None else round(100 * x)
        w.writerow([r.created.strftime("%Y-%m-%d %H:%M") if r.created else "", r.name, r.instrument, r.detected,
                    r.notes, pct(r.verified_pct), pct(r.within15_pct), r.seconds and round(r.seconds, 1),
                    r.quality, v.get("corrected"), v.get("missed"), v.get("extra"), r.id])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="string-tests.csv"'})


@router.get("/results/{rid}", dependencies=[Depends(require_key)])
def get_result(rid: str, db=Depends(get_db)):
    r = db.get(TesterResult, rid)
    if r is None:
        raise HTTPException(404, "Not found")
    return {**_summary(r), "report": r.report}


@router.get("/results/{rid}/audio", dependencies=[Depends(require_key)])
def get_audio(rid: str, db=Depends(get_db)):
    r = db.get(TesterResult, rid)
    if r is None or not r.audio_received:
        raise HTTPException(404, "No recording")
    return FileResponse(r.audio_path, media_type="audio/wav", filename=f"string-test-{rid}.wav")


@router.delete("/results/{rid}", dependencies=[Depends(require_key)])
def delete_result(rid: str, db=Depends(get_db)):
    """Testers can ask for their recordings to be deleted (consent text)."""
    r = db.get(TesterResult, rid)
    if r is None:
        raise HTTPException(404, "Not found")
    if r.audio_path and os.path.exists(r.audio_path):
        os.remove(r.audio_path)
    db.delete(r)
    db.commit()
    return {"deleted": rid}
