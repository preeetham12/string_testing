"""Upload helper: how the app sends a recording in checksummed chunks."""
import hashlib
import time


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def upload_file(http, token, path, chunk_size=256 * 1024, skip=()):
    """Upload `path` in chunks. `skip` = chunk numbers to 'lose' (to test resume).
    Returns (upload_id, recording_id or None if chunks are missing)."""
    data = open(path, "rb").read()
    chunks = [data[i:i + chunk_size] for i in range(0, len(data), chunk_size)]
    r = http.post("/uploads", headers=auth(token), json={
        "total_chunks": len(chunks), "total_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()})
    r.raise_for_status()
    uid = r.json()["upload_id"]
    for n, c in enumerate(chunks):
        if n in skip:
            continue
        h = {**auth(token), "X-Chunk-SHA256": hashlib.sha256(c).hexdigest()}
        http.put(f"/uploads/{uid}/chunks/{n}", headers=h, content=c).raise_for_status()
    done = http.post(f"/uploads/{uid}/complete", headers=auth(token))
    return uid, (done.json()["recording_id"] if done.status_code == 200 else None)


def resume_upload(http, token, uid, path, chunk_size=256 * 1024):
    """Ask the server what's missing and send only those chunks."""
    data = open(path, "rb").read()
    missing = http.get(f"/uploads/{uid}", headers=auth(token)).json()["missing"]
    for n in missing:
        c = data[n * chunk_size:(n + 1) * chunk_size]
        h = {**auth(token), "X-Chunk-SHA256": hashlib.sha256(c).hexdigest()}
        http.put(f"/uploads/{uid}/chunks/{n}", headers=h, content=c).raise_for_status()
    return http.post(f"/uploads/{uid}/complete", headers=auth(token)).json()["recording_id"], missing


def wait_for(http, token, url, field="status", until=("done", "qc_failed", "error", "ready",
                                                         "reference_failed", "published"),
             timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = http.get(url, headers=auth(token)).json()
        if r.get(field) in until:
            return r
        time.sleep(0.2)
    raise TimeoutError(url)
