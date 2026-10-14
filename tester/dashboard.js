/* NUERiQ String Testing - results dashboard (needs the StringCoach server). */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (x) => (x === null || x === undefined ? "—" : Math.round(x * 100) + "%");
  const API = "/tester-api";
  const NAMES = { violin: "Violin", viola: "Viola", cello: "Cello", guitar: "Guitar", veena: "Veena", sitar: "Sitar",
                  ukulele: "Ukulele", mandolin: "Mandolin", other: "Other", auto: "Not sure" };
  const nameOf = (i) => NAMES[i] || i || "—";
  let key = sessionStorage.getItem("nueriq.dashKey") || "", rows = [], seen = new Set(), timer = 0;

  async function api(path, opts) {
    const res = await fetch(API + path, Object.assign({}, opts, { headers: Object.assign({ "X-Dashboard-Key": key }, (opts || {}).headers) }));
    if (res.status === 401) { signOut("That key isn't right."); throw new Error("401"); }
    if (!res.ok) throw new Error("The server answered " + res.status);
    return res.json();
  }
  function signOut(msg) {
    clearInterval(timer); key = ""; sessionStorage.removeItem("nueriq.dashKey");
    $("board").hidden = true; $("login").hidden = false;
    if (msg) { $("loginErr").textContent = msg; $("loginErr").hidden = false; }
  }
  $("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault(); key = $("key").value.trim(); $("loginErr").hidden = true;
    try { await load(true); sessionStorage.setItem("nueriq.dashKey", key); start(); } catch (err) { if (err.message !== "401") { $("loginErr").textContent = err.message; $("loginErr").hidden = false; } }
  });

  function start() {
    $("login").hidden = true; $("board").hidden = false;
    $("csv").href = API + "/results.csv?key=" + encodeURIComponent(key);
    clearInterval(timer); timer = setInterval(() => load(false).catch(() => {}), 15000);
  }
  async function load(first) {
    const list = await api("/results");
    const fresh = first ? new Set() : new Set(list.filter((r) => !seen.has(r.id)).map((r) => r.id));
    list.forEach((r) => seen.add(r.id));
    rows = list.map((r) => Object.assign(r, { isNew: fresh.has(r.id) }));
    const insts = [...new Set(rows.map((r) => r.instrument).filter(Boolean))].sort();
    const cur = $("inst").value;
    $("inst").innerHTML = '<option value="">All</option>' + insts.map((i) => '<option value="' + esc(i) + '">' + esc(nameOf(i)) + "</option>").join("");
    $("inst").value = insts.includes(cur) ? cur : "";
    render();
    $("status").textContent = rows.length + " test" + (rows.length === 1 ? "" : "s") + " · updated " + new Date().toLocaleTimeString() + (fresh.size ? " · " + fresh.size + " new" : "");
  }
  function render() {
    const q = $("q").value.trim().toLowerCase(), inst = $("inst").value;
    const shown = rows.filter((r) => (!q || (r.name || "").toLowerCase().includes(q)) && (!inst || r.instrument === inst));
    const avg = (k) => { const v = shown.map((r) => r[k]).filter((x) => x !== null && x !== undefined); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
    $("stats").innerHTML =
      '<div class="stat"><span class="muted">Tests</span><b>' + shown.length + "</b></div>" +
      '<div class="stat"><span class="muted">Testers</span><b>' + new Set(shown.map((r) => r.name)).size + "</b></div>" +
      '<div class="stat"><span class="muted">Notes heard correctly (average)</span><b>' + pct(avg("verified_pct")) + "</b></div>" +
      '<div class="stat"><span class="muted">Notes within ±15 cents (average)</span><b>' + pct(avg("within15_pct")) + "</b></div>";
    const tb = $("resultsTable").querySelector("tbody");
    tb.innerHTML = shown.length ? shown.map((r) =>
      '<tr data-id="' + esc(r.id) + '" tabindex="0" class="' + (r.isNew ? "new" : "") + '"><td>' + esc(new Date(r.created).toLocaleString()) + "</td><td>" + esc(r.name) +
      "</td><td>" + esc(nameOf(r.instrument)) + (r.detected && r.detected !== r.instrument ? ' <span class="muted small">(app: ' + esc(nameOf(r.detected)) + ")</span>" : "") +
      '</td><td class="num">' + (r.notes ?? "—") + '</td><td class="num">' + pct(r.verified_pct) + '</td><td class="num">' + pct(r.within15_pct) +
      '</td><td class="num">' + (r.seconds ? r.seconds.toFixed(0) + " s" : "—") + "</td><td>" + (r.has_audio ? "✓" : '<span class="muted">—</span>') + "</td></tr>").join("")
      : '<tr><td colspan="8" class="muted">No tests yet. They appear here as soon as someone submits a test.</td></tr>';
    tb.querySelectorAll("tr[data-id]").forEach((tr) => {
      tr.onclick = () => openDetail(tr.dataset.id);
      tr.onkeydown = (e) => { if (e.key === "Enter") openDetail(tr.dataset.id); };
    });
  }
  $("q").addEventListener("input", render);
  $("inst").addEventListener("change", render);
  $("refresh").addEventListener("click", () => load(false).catch(() => {}));

  const COL = { right: "Heard correctly", corrected: "Wrong note (corrected)", missed: "Missed (added)", extra: "Shown but not played (deleted)" };
  async function openDetail(id) {
    const d = await api("/results/" + encodeURIComponent(id)), R = d.report || {}, V = R.verification || {}, P = R.playing || {}, IN = R.intonation || { rows: [] };
    const kv = (a, b) => '<div class="kv"><span>' + a + "</span><span>" + b + "</span></div>";
    const box = $("detail"); box.hidden = false;
    box.innerHTML =
      '<div class="detail-head"><div><h2>' + esc(d.name) + " · " + esc(nameOf(d.instrument)) + '</h2><p class="muted">' + esc(new Date(d.created).toLocaleString()) + " · " + (d.seconds ? d.seconds.toFixed(0) + " s" : "") +
      ' · recording quality: <span class="pill ' + (d.quality === "good" ? "ok" : "warn") + '">' + esc(d.quality) + "</span></p></div>" +
      '<div><button class="btn small" id="dJson" type="button">Download report (JSON)</button> <button class="btn small" id="dDel" type="button">Delete</button> <button class="btn small" id="dClose" type="button">Close</button></div></div>' +
      (d.has_audio ? '<audio controls preload="none" src="' + API + "/results/" + encodeURIComponent(id) + "/audio?key=" + encodeURIComponent(key) + '"></audio>' : '<p class="muted">No recording was attached.</p>') +
      '<div class="rgrid">' +
      '<div class="rbox wide"><h3>App vs what was really played</h3><div class="big-num">' + pct(V.percent) + '</div><div class="muted">of the notes played were heard correctly, confirmed by the player</div>' +
      (V.strip && V.strip.length ? '<div class="vstrip">' + V.strip.map((x) => '<i class="' + esc(x.kind) + '" title="' + esc(x.note + " at " + x.t + " s: " + COL[x.kind]) + '"></i>').join("") + "</div>" +
        '<div class="vkey">' + Object.entries(COL).map(([k, v]) => '<span><i class="' + k + '"></i>' + v + "</span>").join("") + "</div>" : "") +
      kv("Heard correctly", V.right ?? "—") + kv("Wrong note, corrected", V.corrected ?? "—") + kv("Missed, added", V.missed ?? "—") + kv("Shown but not played", V.extra ?? "—") + "</div>" +
      '<div class="rbox"><h3>Intonation by ' + (IN.byString ? "string" : "note") + "</h3>" + (IN.rows.length ? '<div class="table-wrap"><table><thead><tr><th>' + (IN.byString ? "String" : "Note") + "</th><th>Notes</th><th>Avg error</th><th title='Notes within ±15 cents'>In tune</th></tr></thead><tbody>" +
        IN.rows.map((r) => "<tr><td>" + esc(r.label) + "</td><td>" + r.notes + "</td><td>" + r.meanAbsCents + " cents</td><td>" + pct(r.within15) + "</td></tr>").join("") + "</tbody></table></div>" : '<p class="muted">—</p>') + "</div>" +
      '<div class="rbox"><h3>Playing</h3>' + kv("Notes", P.notes ?? "—") + kv("Range", esc((P.lowest || "—") + " to " + (P.highest || "—"))) + kv("Average tuning error", P.meanAbsCents == null ? "—" : Math.round(P.meanAbsCents) + " cents") + kv("Within ±15 cents", pct(P.within15)) + "</div>" +
      '<div class="rbox"><h3>About this test</h3>' + kv("Instrument played", esc(nameOf(d.instrument))) + kv("Instrument the app detected", esc(nameOf(d.detected))) +
        kv("What was played", esc((R.tester || {}).piece || "—")) + kv("Device", esc((R.tester || {}).device || "—")) + kv("Room", esc((R.tester || {}).room || "—")) +
        kv("Changes made", (R.corrections || []).length) + (R.note ? kv("Note from the player", esc(R.note)) : "") + "</div>" +
      "</div>";
    $("dClose").onclick = () => { box.hidden = true; };
    $("dJson").onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(R, null, 2)], { type: "application/json" })); a.download = "string-test-" + id + ".json"; a.click(); };
    $("dDel").onclick = async () => {
      if (!confirm("Delete this test and its recording? This cannot be undone.")) return;
      await api("/results/" + encodeURIComponent(id), { method: "DELETE" }); box.hidden = true; await load(false);
    };
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  if (key) load(true).then(start).catch(() => {});
})();
