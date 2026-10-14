// StringCoach web app (Part 6). Plain JavaScript modules, no build step.

const $ = (sel, root = document) => root.querySelector(sel);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const NOTE = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const midiName = (m) => `${NOTE[((Math.round(m) % 12) + 12) % 12]}${Math.floor(Math.round(m) / 12) - 1}`;
const hzToMidi = (f) => 69 + 12 * Math.log2(f / 440);
const LOWEST_HZ = { violin: 196, viola: 130.8, cello: 65.4, guitar: 82.4 };

// Exposed for automated browser tests only (read-only use).
const debug = (window.stringcoach = { pitchFrames: 0, lastNote: null, uploads: [], events: [],
                                     history: () => audio.history.slice(),
                                     ghostAligned: () => audio.align !== null });

// ---------------- API ----------------
const auth = {
  get token() { return localStorage.getItem("sc_token"); },
  get user() { return JSON.parse(localStorage.getItem("sc_user") || "null"); },
  save(t, u) { localStorage.setItem("sc_token", t); localStorage.setItem("sc_user", JSON.stringify(u)); },
  clear() { localStorage.removeItem("sc_token"); localStorage.removeItem("sc_user"); },
};

async function api(path, { method = "GET", json, body, headers = {} } = {}) {
  const h = { ...headers };
  if (auth.token) h.Authorization = `Bearer ${auth.token}`;
  if (json !== undefined) { h["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  const r = await fetch(path, { method, headers: h, body });
  if (r.status === 401 && !path.startsWith("/auth/")) { auth.clear(); location.hash = "#/login"; }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) {
    const d = data?.detail;
    const msg = typeof d === "string" ? d : d?.message || (Array.isArray(d) ? "Please check the form" : `Request failed (${r.status})`);
    throw Object.assign(new Error(msg), { status: r.status, data });
  }
  return data;
}

function say(msg) { $("#status").textContent = msg; debug.events.push(msg); }

// Go to a page. (Changing the hash triggers route(); if it's unchanged, call it.)
function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

// ---------------- routing ----------------
function render(templateId) {
  const view = $("#view");
  view.replaceChildren($(templateId).content.cloneNode(true));
  view.focus();
  return view;
}

const leaving = [];  // things to stop when the page changes (timers, playback)
const onLeave = (fn) => leaving.push(fn);

// Why a take wasn't counted, in a few words for the takes list
function notCountedWhy(at) {
  const failed = (at.qc_report?.checks || []).filter((c) => c.status === "FAIL").map((c) => c.check);
  if (failed.includes("instrument")) return "no playing heard";
  if (failed.includes("lesson") || failed.includes("same piece")) return "lesson not recognised";
  return "recording problem";
}

async function route() {
  stopAudio();
  while (leaving.length) leaving.pop()();
  say("");
  const u = auth.user;
  $("#who").textContent = u ? `${u.name} (${u.role})` : "";
  $("#logout").hidden = !u;
  const [, page, id, id2] = (location.hash || "#/home").split("/");
  if (!u) return loginView();
  if (u.role === "teacher") {
    if (page === "new" && id) return newLessonView(Number(id));
    if (page === "t" && id) return studioView(Number(id));
    if (page === "r" && id && id2) return takesView(Number(id), Number(id2));
    if (page === "a" && id) return assignmentView(Number(id));
    return teacherHome();
  }
  if (page === "a" && id) return assignmentView(Number(id));
  return homeView();
}
window.addEventListener("hashchange", route);
$("#logout").addEventListener("click", () => { auth.clear(); go("#/login"); });

// keep aria-invalid in step with the :user-invalid styling
document.addEventListener("blur", (e) => {
  if (e.target.matches?.("input")) e.target.setAttribute("aria-invalid", e.target.matches(":user-invalid"));
}, true);

// ---------------- login / register ----------------
function loginView() {
  const v = render("#t-login");
  $("#login-form", v).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (!f.checkValidity()) return f.reportValidity();
    try {
      const r = await api("/auth/login", { method: "POST", json: { email: f.email.value, password: f.password.value } });
      auth.save(r.token, r.user);
      go("#/home");
    } catch (err) { say(err.message); }
  });
  $("#register-form", v).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (!f.checkValidity()) return f.reportValidity();
    const body = { name: f.name.value, email: f.email.value, password: f.password.value, role: f.role.value };
    try {
      await api("/auth/register", { method: "POST", json: body });
      const r = await api("/auth/login", { method: "POST", json: { email: body.email, password: body.password } });
      auth.save(r.token, r.user);
      go("#/home");
    } catch (err) { say(err.message); }
  });
}

// ---------------- lesson list ----------------
async function homeView() {
  const v = render("#t-home");
  const box = $("#classes", v);
  const classes = await api("/classes");
  if (!classes.length) {
    box.innerHTML = auth.user.role === "student"
      ? "<p>You're not in a class yet. Ask your teacher to add you using your email address.</p>"
      : "<p>Create classes and assignments in the teacher studio (Part 7). For now, use the API at <a href='/docs'>/docs</a>.</p>";
    return;
  }
  for (const c of classes) {
    const h = document.createElement("h2");
    h.textContent = `${c.name} (${c.instrument})`;
    const ul = document.createElement("ul");
    ul.className = "lesson-list";
    const items = await api(`/classes/${c.id}/assignments`);
    for (const a of items) {
      const li = document.createElement("li");
      li.innerHTML = `<a href="#/a/${a.id}"><span class="t"></span><span class="meta"></span></a>`;
      $(".t", li).textContent = a.title;
      $(".meta", li).textContent = a.status === "published" ? `${a.max_attempts} attempts` : a.status;
      ul.append(li);
    }
    if (!items.length) ul.innerHTML = "<li>No lessons published yet.</li>";
    box.append(h, ul);
  }
}

// ---------------- audio engine ----------------
const audio = { ctx: null, node: null, stream: null, samples: [], recording: false,
                history: [], align: null, raf: 0 };

async function startAudio(instrument) {
  if (audio.ctx) return;
  // Music needs the raw microphone: speech processing would cut sustained
  // notes and flatten loud/soft playing.
  audio.stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
  });
  audio.ctx = new AudioContext();
  await audio.ctx.audioWorklet.addModule("pitch-worklet.js");
  const src = audio.ctx.createMediaStreamSource(audio.stream);
  audio.node = new AudioWorkletNode(audio.ctx, "stringcoach");
  audio.node.port.postMessage({ type: "fmin", value: (LOWEST_HZ[instrument] || 60) * 0.85 });
  audio.node.port.onmessage = (e) => {
    const m = e.data;
    if (m.type === "samples") audio.samples.push(m.data);
    if (m.type === "pitch") onPitch(m);
  };
  src.connect(audio.node);
}

function stopAudio() {
  cancelAnimationFrame(audio.raf);
  audio.stream?.getTracks().forEach((t) => t.stop());
  audio.ctx?.close();
  Object.assign(audio, { ctx: null, node: null, stream: null, recording: false, samples: [], history: [] });
}

function onPitch(m) {
  const voiced = m.f0 > 0 && m.conf > 0.5;
  const midi = voiced ? hzToMidi(m.f0) : null;
  audio.history.push({ t: m.t, midi, rms: m.rms });
  if (audio.history.length > 2000) audio.history.shift();
  debug.pitchFrames++;
  if (audio.recording && audio.align === null && voiced) {
    // line the teacher's ghost up with the student's first steady note
    const last = audio.history.slice(-3);
    if (last.length === 3 && last.every((h) => h.midi !== null && Math.abs(h.midi - midi) < 0.5)) audio.align = last[0].t;
  }
  const name = $("#note-name"), needle = $("#needle"), cents = $("#cents");
  if (!name) return;
  if (!voiced) { name.textContent = "–"; cents.innerHTML = "&nbsp;"; needle.style.left = "50%"; needle.className = "needle"; return; }
  const c = Math.round((midi - Math.round(midi)) * 100);
  name.textContent = midiName(midi);
  debug.lastNote = midiName(midi);
  cents.textContent = c === 0 ? "in tune" : `${Math.abs(c)} cents ${c < 0 ? "flat" : "sharp"}`;
  needle.style.left = `${50 + c}%`;
  needle.className = `needle ${Math.abs(c) <= 10 ? "in-tune" : "off"}`;
}

// ---------------- the pitch lane ----------------
function drawLane(state) {
  const cv = $("#lane");
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const css = getComputedStyle(document.documentElement);
  const notes = state.ghost || [];
  const now = audio.ctx ? audio.ctx.currentTime : 0;
  const span = 6, left = now - 4;           // 4 s of history, 2 s ahead
  const x = (t) => ((t - left) / span) * W;
  const start = audio.align !== null ? audio.align : now + 0.3;
  let lo, hi, guides;
  if (notes.length) {
    const pitches = notes.map((n) => n.pitch);
    lo = Math.floor(Math.min(...pitches)) - 3; hi = Math.ceil(Math.max(...pitches)) + 3;
    // guide lines only for the lesson notes now on screen: a wide-ranging
    // piece used to draw every note name on top of each other
    const visible = notes.filter((n) => start + n.end >= left && start + n.start <= left + span);
    guides = [...new Set((visible.length ? visible : notes.slice(0, 8)).map((n) => Math.round(n.pitch)))];
  } else {
    // free mode (the teacher recording a new lesson): no lesson notes yet, so
    // the range follows the playing, drifting smoothly instead of jumping
    const recent = audio.history.filter((h) => h.midi !== null && h.t > now - 3).map((h) => h.midi).sort((a, b) => a - b);
    const target = recent.length ? recent[recent.length >> 1] : (state.center ?? 69);
    state.center = state.center == null ? target : state.center + (target - state.center) * 0.04;
    lo = Math.round(state.center) - 8; hi = lo + 16;
    guides = [];
    for (let m = lo + 1; m < hi; m++) if (!midiName(m).includes("#")) guides.push(m);  // natural notes only
  }
  const y = (m) => H - 12 - ((m - lo) / (hi - lo)) * (H - 24);

  g.font = `13px ${css.getPropertyValue("--font")}`;
  let lastLabel = -Infinity;
  for (const m of guides.sort((a, b) => a - b)) {
    g.strokeStyle = css.getPropertyValue("--line");
    g.lineWidth = 1;
    g.beginPath(); g.moveTo(0, y(m)); g.lineTo(W, y(m)); g.stroke();
    if (Math.abs(y(m) - lastLabel) >= 15) {  // never draw labels on top of each other
      g.fillStyle = css.getPropertyValue("--muted");
      g.fillText(midiName(m), 6, y(m) - 4);
      lastLabel = y(m);
    }
  }
  // teacher ghost: steps, starting where the student's first note started.
  // Before that, the lesson waits just ahead of the playhead, so the student
  // can see what's coming before they play.
  g.strokeStyle = css.getPropertyValue("--teacher");
  g.lineWidth = 8; g.lineCap = "round"; g.globalAlpha = 0.45;
  for (const n of notes) {
    g.beginPath(); g.moveTo(x(start + n.start), y(n.pitch)); g.lineTo(x(start + n.end - 0.03), y(n.pitch)); g.stroke();
  }
  g.globalAlpha = 1;
  debug.ghostDrawn = true;
  // the student's pitch
  g.strokeStyle = css.getPropertyValue("--you");
  g.lineWidth = 3; g.lineCap = "round";
  g.beginPath();
  let pen = false;
  for (const h of audio.history) {
    if (h.t < left || h.midi === null || h.midi < lo - 1 || h.midi > hi + 1) { pen = false; continue; }
    if (!pen) { g.moveTo(x(h.t), y(h.midi)); pen = true; } else g.lineTo(x(h.t), y(h.midi));
  }
  g.stroke();
  // playhead
  g.strokeStyle = css.getPropertyValue("--ink"); g.lineWidth = 1;
  g.beginPath(); g.moveTo(x(now), 0); g.lineTo(x(now), H); g.stroke();
  audio.raf = requestAnimationFrame(() => drawLane(state));
}

// ---------------- recording helpers ----------------
function concat(chunks) {
  const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}

function encodeWav(samples, sr) {
  // 16-bit PCM mono WAV: lossless for our purposes, half the size of float
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o, s) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, samples.length * 2, true);
  let o = 44;
  for (const s of samples) { const c = Math.max(-1, Math.min(1, s)); v.setInt16(o, c < 0 ? c * 0x8000 : c * 0x7fff, true); o += 2; }
  return buf;
}

// Instant check before uploading (the server repeats it in full).
function quickCheck(samples, frames) {
  let peak = 0, clipped = 0;
  for (const s of samples) { const a = Math.abs(s); if (a > peak) peak = a; if (a >= 0.999) clipped++; }
  const playing = frames.length ? frames.filter((h) => h.midi !== null).length / frames.length : 0;
  if (samples.length < audio.ctx.sampleRate) return "That was very short. Play the passage, then press Stop.";
  if (clipped / samples.length > 0.001) return "Your recording is distorting. Move about 30 cm further from the mic or lower the input volume.";
  if (20 * Math.log10(peak + 1e-9) < -30) return "Your recording is too quiet. Move closer to the mic or check the right mic is selected.";
  // Only block when essentially nothing was played: the server's detector is
  // stronger and makes the real decision, so borderline takes are sent there.
  // (Blocking at 40% here rejected real violin takes - found by user testing.)
  if (playing < 0.1) return "We could hardly hear any playing. Check the mic and play the piece again.";
  return null;
}

async function sha256hex(bytes) {
  const d = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Chunked, checksummed, resumable upload (matches the Part 5 API).
async function uploadRecording(buf, onProgress) {
  const bytes = new Uint8Array(buf);
  const CH = 256 * 1024;
  const n = Math.ceil(bytes.length / CH);
  const { upload_id } = await api("/uploads", {
    method: "POST", json: { total_chunks: n, total_bytes: bytes.length, sha256: await sha256hex(bytes) } });
  const send = async (k) => {
    const chunk = bytes.subarray(k * CH, (k + 1) * CH);
    const sum = await sha256hex(chunk);
    for (let tries = 0; ; tries++) {
      try {
        await api(`/uploads/${upload_id}/chunks/${k}`, { method: "PUT", body: chunk, headers: { "X-Chunk-SHA256": sum } });
        return;
      } catch (e) {
        if (tries >= 2) throw e;
        await sleep(400 * (tries + 1));   // brief pause, then try again
      }
    }
  };
  for (let k = 0; k < n; k++) {
    if (debug.dropChunk === k) { debug.dropChunk = null; continue; }  // test hook: simulate a lost chunk
    await send(k);
    onProgress(((k + 1) / n) * 100);
  }
  const st = await api(`/uploads/${upload_id}`);          // anything lost on the way?
  for (const k of st.missing) await send(k);
  debug.uploads.push({ upload_id, chunks: n, resent: st.missing });
  return (await api(`/uploads/${upload_id}/complete`, { method: "POST" })).recording_id;
}

// ---------------- the practice page ----------------
async function assignmentView(id) {
  const v = render("#t-assignment");
  let a;
  try { a = await api(`/assignments/${id}`); } catch (e) { v.innerHTML = `<p>${e.message}. <a href="#/home">Back to your lessons</a></p>`; return; }
  const cls = (await api("/classes")).find((c) => c.id === a.class_id) || { instrument: "violin" };
  const isStudent = auth.user.role === "student";
  $("#a-title", v).textContent = a.title;
  let mode = null;  // null = whole piece, else a section id
  let attempts = [];

  const ghostFor = () => {
    const notes = mode ? a.notes.slice(a.sections[mode - 1].first, a.sections[mode - 1].last + 1) : a.notes;
    const t0 = notes[0].start;
    return notes.map((n) => ({ start: n.start - t0, end: n.end - t0, pitch: n.pitch }));
  };
  const lane = { ghost: ghostFor() };

  // sections
  const secBox = $("#sections", v);
  const passedSections = () => {
    const set = new Set();
    for (const at of attempts.filter((x) => x.status === "done")) {
      for (const s of at.result.sections) if (s.passed) set.add(at.section_id || s.id);
    }
    return set;
  };
  const drawSections = () => {
    secBox.replaceChildren();
    const passed = passedSections();
    const opts = [{ id: null, label: "Whole piece" }, ...a.sections.map((s) => ({ id: s.id, label: `Section ${s.id}` }))];
    for (const o of opts) {
      const b = document.createElement("button");
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(o.id === mode));
      b.textContent = o.label;
      if (o.id && passed.has(o.id)) { b.classList.add("passed"); b.setAttribute("aria-label", `${o.label}, passed`); }
      b.addEventListener("click", () => { mode = o.id; lane.ghost = ghostFor(); drawSections(); });
      secBox.append(b);
    }
  };

  // listening to the teacher (slow-down keeps the pitch: preservesPitch)
  const ref = $("#ref-audio", v);
  ref.src = `/recordings/${a.reference_recording_id}/audio?token=${encodeURIComponent(auth.token)}`;
  ref.preservesPitch = true;
  const span = () => mode
    ? [a.sections[mode - 1].t_start + a.ref_offset - 0.15, a.sections[mode - 1].t_end + a.ref_offset + 0.15]
    : [0, Infinity];
  $("#listen", v).addEventListener("click", (e) => {
    if (!ref.paused) { ref.pause(); e.target.setAttribute("aria-pressed", "false"); e.target.textContent = "Listen to teacher"; return; }
    ref.playbackRate = Number($("#speed", v).value);
    ref.currentTime = Math.max(0, span()[0]);
    ref.play();
    e.target.setAttribute("aria-pressed", "true");
    e.target.textContent = "Stop listening";
  });
  ref.addEventListener("timeupdate", () => { if (ref.currentTime > span()[1]) ref.currentTime = Math.max(0, span()[0]); });

  // microphone
  const recBtn = $("#record", v);
  const ensureMic = async () => {
    try { await startAudio(cls.instrument); } catch { say("Allow microphone access in your browser to record."); return false; }
    recBtn.disabled = false;
    drawLane(lane);
    return true;
  };
  // Mic check: first the room (stay quiet), then a long note. Real-violin
  // testing showed background noise and distance from the microphone matter
  // most for accuracy, so the check advises on both.
  $("#mic-check", v).addEventListener("click", async () => {
    if (!(await ensureMic())) return;
    const db = (from) => {
      const rms = audio.history.slice(from).map((h) => h.rms).sort((a, b) => a - b);
      return (q) => 20 * Math.log10(Math.max(rms[Math.floor(q * (rms.length - 1))] || 0, 1e-9));
    };
    say("Stay quiet for a moment: listening to your room…");
    let from = audio.history.length;
    await sleep(1500);
    const room = db(from)(0.5);
    say("Now play a long note…");
    await sleep(500);
    from = audio.history.length;
    await sleep(2000);
    const note = db(from)(0.9);
    const gap = note - room;
    say(note < -40 ? "Very quiet. Move closer to the microphone, or check the right one is selected."
      : note > -3 ? "Very loud. Move a little further from the microphone."
      : gap < 20 ? "Your room is a bit noisy, which can make the review less accurate. Turn off fans or music, or find a quieter spot."
      : "All set: good level and a quiet room. Press Record when you're ready.");
  });
  recBtn.disabled = !isStudent;
  if (!isStudent) recBtn.title = "Only students record attempts";

  const toggle = async () => {
    if (recBtn.disabled) return;
    if (!audio.ctx) {
      if (!(await ensureMic())) return;
      // let the microphone settle first: audio drivers often drop a buffer or
      // two while starting up (found by browser testing)
      say("Getting the microphone ready…");
      await sleep(500);
    }
    if (!audio.recording) {
      audio.samples = []; audio.align = null;
      audio.recStart = audio.history.length;
      audio.recording = true;
      audio.node.port.postMessage({ type: "record", on: true });
      recBtn.textContent = "Stop"; recBtn.setAttribute("aria-pressed", "true");
      say(mode ? `Recording section ${mode}` : "Recording the whole piece");
      return;
    }
    audio.recording = false;
    audio.node.port.postMessage({ type: "record", on: false });
    recBtn.textContent = "Record"; recBtn.setAttribute("aria-pressed", "false");
    say("Checking your recording…");
    await sleep(250);  // let the last samples arrive
    const samples = concat(audio.samples);
    const problem = quickCheck(samples, audio.history.slice(audio.recStart));
    if (problem) { say(problem); return; }
    await submit(samples);
  };
  recBtn.addEventListener("click", toggle);
  const onKey = (e) => {
    if (e.code === "Space" && !e.target.matches("input, select, textarea, button")) { e.preventDefault(); toggle(); }
  };
  document.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", () => document.removeEventListener("keydown", onKey), { once: true });

  async function submit(samples) {
    const up = $("#upload", v), bar = $("#upload-progress", v);
    up.hidden = false; bar.value = 0;
    recBtn.disabled = true;
    say("Sending your recording…");
    try {
      const rec = await uploadRecording(encodeWav(samples, audio.ctx.sampleRate), (p) => { bar.value = p; });
      const at = await api(`/assignments/${a.id}/attempts`, { method: "POST", json: { recording_id: rec, section_id: mode } });
      say("Analysing your take…");
      let r;
      for (let i = 0; i < 300; i++) {
        r = await api(`/attempts/${at.id}`);
        if (!["queued", "analysing"].includes(r.status)) break;
        await sleep(700);
      }
      say(r.status === "done" ? `Your score: ${r.score}` : r.status === "qc_failed" ? "This take wasn't counted. See why below." : "");
      await loadAttempts();
      showReport(r);
    } catch (e) { say(e.message); }
    finally { up.hidden = true; recBtn.disabled = false; }
  }

  function showReport(r) {
    const box = $("#report", v);
    box.hidden = false;
    box.replaceChildren();
    const h = document.createElement("h2");
    h.id = "report-title";
    h.textContent = r.section_id ? `Section ${r.section_id} practice` : "Your review";
    box.append(h);
    if (r.status === "qc_failed" || r.status === "error") {
      const p = document.createElement("p");
      p.className = "qc-fail";
      p.textContent = (r.status === "error" ? "Something went wrong while analysing this take." : r.feedback)
        + " This take wasn't counted. Record again when you're ready.";
      box.append(p);
      return;
    }
    const res = r.result;
    const shown = r.teacher_score ?? r.score;
    const top = document.createElement("div");
    top.className = "report-top";
    top.innerHTML = `<div class="score"></div><div class="verdict"></div>`;
    // when the teacher overrides, say so - the review below still quotes the automatic score
    $(".score", top).innerHTML = `${shown}<small> / 100${r.teacher_score != null ? ` (set by your teacher · automatic score ${r.score})` : ""}</small>`;
    const todo = res.sections.filter((s) => !s.passed);
    $(".verdict", top).textContent = todo.length ? `${todo.length} section${todo.length > 1 ? "s" : ""} to practise` : "Every section passed";
    $(".verdict", top).classList.add(todo.length ? "work" : "pass");
    box.append(top);
    const strip = document.createElement("div");
    strip.className = "strip";
    for (const s of res.sections) {
      const seg = document.createElement("div");
      seg.className = `seg ${s.passed ? "pass" : "fail"}`;
      seg.style.flex = String(s.last - s.first + 1);
      seg.textContent = `${r.section_id ? `Section ${r.section_id}` : `S${s.id}`} · ${s.score}`;
      strip.append(seg);
    }
    box.append(strip);
    const fb = document.createElement("p");
    fb.className = "feedback";
    fb.textContent = r.feedback;
    box.append(fb);
    for (const c of r.comments) {
      const q = document.createElement("blockquote");
      q.className = "comment";
      q.textContent = `Your teacher: ${c.body}`;
      box.append(q);
    }
    const worst = !r.section_id && todo.sort((x, y) => x.score - y.score)[0];
    if (worst) {
      const b = document.createElement("button");
      b.className = "primary";
      b.textContent = `Practise section ${worst.id}`;
      b.addEventListener("click", () => { mode = worst.id; lane.ghost = ghostFor(); drawSections(); $("#listen", v).focus(); });
      box.append(b);
    }
    box.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }

  async function loadAttempts() {
    attempts = isStudent ? await api(`/assignments/${a.id}/attempts`) : [];
    const used = attempts.filter((x) => x.section_id === null && x.counted).length;
    $("#a-attempts", v).textContent = isStudent
      ? `${Math.max(0, a.max_attempts - used)} of ${a.max_attempts} full takes left · section practice is unlimited`
      : "Teacher preview: students record here.";
    const list = $("#takes", v);
    list.replaceChildren();
    for (const at of attempts) {
      const li = document.createElement("li");
      const what = at.section_id ? `Section ${at.section_id} practice` : "Whole piece";
      const b = document.createElement("button");
      b.className = "link";
      b.textContent = at.status === "done" ? `${what}: ${at.teacher_score ?? at.score}`
        : at.status === "qc_failed" ? `${what}: not counted (${notCountedWhy(at)})` : `${what}: ${at.status}`;
      b.addEventListener("click", () => showReport(at));
      li.append(b);
      list.append(li);
    }
    if (!attempts.length) list.innerHTML = "<li>No takes yet. Listen to your teacher, then record.</li>";
    drawSections();
  }

  drawSections();
  drawLane(lane);  // show the lesson's notes straight away, before the mic starts
  await loadAttempts();
}

// =====================================================================
// Part 7: teacher studio and dashboard
// =====================================================================
const STATUS_TEXT = { processing: "Analysing your recording…", ready: "Ready to check and publish",
                      published: "Published", reference_failed: "Recording problem" };
const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function teacherHome() {
  const v = render("#t-teacher-home");
  const box = $("#t-classes", v);
  const classes = await api("/classes");
  if (!classes.length) box.innerHTML = "<p>No classes yet. Create your first one below.</p>";
  for (const c of classes) {
    const card = document.createElement("section");
    card.className = "card class-card";
    card.innerHTML = `<h2>${esc(c.name)} <span class="hint">(${esc(c.instrument)})</span></h2>
      <div class="cols"><div><h3>Lessons</h3><ul class="lesson-list"></ul>
        <a class="button-link" href="#/new/${c.id}">New lesson</a></div>
      <div><h3>Students</h3><ul class="students"></ul>
        <form class="add-student inline-form"><label for="add-${c.id}">Add a student by email</label>
          <input id="add-${c.id}" type="email" name="email" required autocomplete="off">
          <button class="secondary" type="submit">Add</button></form></div></div>`;
    const lessons = await api(`/classes/${c.id}/assignments`);
    const ul = $(".lesson-list", card);
    for (const a of lessons) {
      const li = document.createElement("li");
      li.innerHTML = `<a href="#/t/${a.id}"><span class="t">${esc(a.title)}</span><span class="chip ${a.status}">${STATUS_TEXT[a.status] || a.status}</span></a>`;
      ul.append(li);
    }
    if (!lessons.length) ul.innerHTML = "<li>No lessons yet.</li>";
    const drawStudents = async () => {
      const st = await api(`/classes/${c.id}/students`);
      $(".students", card).innerHTML = st.length ? st.map((x) => `<li>${esc(x.name)}</li>`).join("") : "<li>No students yet.</li>";
    };
    await drawStudents();
    $(".add-student", card).addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api(`/classes/${c.id}/students`, { method: "POST", json: { email: e.target.email.value } });
        say(`Added ${e.target.email.value} to ${c.name}`);
        e.target.reset();
        await drawStudents();
      } catch (err) { say(err.message); }
    });
    box.append(card);
  }
  $("#new-class", v).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (!f.checkValidity()) return f.reportValidity();
    try {
      await api("/classes", { method: "POST", json: { name: f.name.value, instrument: f.instrument.value } });
      say(`Class "${f.name.value}" created`);
      teacherHome();
    } catch (err) { say(err.message); }
  });
}

async function newLessonView(classId) {
  const v = render("#t-new-lesson");
  const cls = (await api("/classes")).find((c) => c.id === classId);
  if (!cls) { v.innerHTML = "<p>Class not found. <a href='#/home'>Back</a></p>"; return; }
  $("#nl-class", v).textContent = `For ${cls.name} (${cls.instrument})`;
  const form = $("#lesson-form", v);
  let recorded = null;
  form.addEventListener("change", (e) => {
    if (e.target.name !== "source") return;
    $("#src-file", v).hidden = e.target.value !== "file";
    $("#src-record", v).hidden = e.target.value !== "record";
  });
  const recBtn = $("#nl-record", v);
  recBtn.addEventListener("click", async () => {
    if (!audio.ctx) {
      try { await startAudio(cls.instrument); } catch { say("Allow microphone access in your browser to record."); return; }
      drawLane({ ghost: [] });  // live pitch graph while the teacher plays
      say("Getting the microphone ready…");
      await sleep(500);
    }
    if (!audio.recording) {
      audio.samples = [];
      audio.recording = true;
      audio.node.port.postMessage({ type: "record", on: true });
      recBtn.textContent = "Stop"; recBtn.setAttribute("aria-pressed", "true");
      say("Recording your reference");
      return;
    }
    audio.recording = false;
    audio.node.port.postMessage({ type: "record", on: false });
    recBtn.textContent = "Record again"; recBtn.setAttribute("aria-pressed", "false");
    await sleep(250);
    const samples = concat(audio.samples);
    recorded = encodeWav(samples, audio.ctx.sampleRate);
    $("#nl-recorded", v).textContent = `Recorded ${(samples.length / audio.ctx.sampleRate).toFixed(1)} seconds.`;
    say("Recording kept. Fill in the details and press Create lesson.");
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!form.checkValidity()) return form.reportValidity();
    let buf = null;
    if (form.source.value === "file") {
      const file = form.file.files[0];
      if (!file) return say("Choose your WAV recording first.");
      buf = await file.arrayBuffer();
    } else {
      if (!recorded) return say("Record your reference first.");
      buf = recorded;
    }
    const up = $("#upload", v), bar = $("#upload-progress", v);
    up.hidden = false;
    try {
      say("Sending your recording…");
      const rec = await uploadRecording(buf, (p) => { bar.value = p; });
      const a = await api("/assignments", { method: "POST", json: {
        class_id: classId, title: form.title.value, recording_id: rec, level: form.level.value,
        pass_mark: Number(form.pass_mark.value), max_attempts: Number(form.max_attempts.value) } });
      go(`#/t/${a.id}`);
    } catch (err) { say(err.message); up.hidden = true; }
  });
}

async function studioView(id) {
  const v = render("#t-studio");
  let a;
  try { a = await api(`/assignments/${id}`); } catch (e) { v.innerHTML = `<p>${esc(e.message)}. <a href="#/home">Back</a></p>`; return; }
  $("#st-title", v).textContent = a.title;
  const chip = $("#st-status", v);
  chip.textContent = STATUS_TEXT[a.status] || a.status;
  chip.className = `chip ${a.status}`;
  $("#st-settings", v).textContent = `Level: ${a.level} · pass mark ${a.pass_mark} per section · ${a.max_attempts} full takes per student`;
  const body = $("#st-body", v);

  if (a.status === "processing") {
    body.innerHTML = "<p>Finding the notes in your recording. This usually takes a few seconds.</p>";
    const t = setTimeout(route, 1500);  // check again (route() redraws this page)
    onLeave(() => clearTimeout(t));
    return;
  }
  if (a.status === "reference_failed") {
    const msgs = (a.qc_report?.checks || []).filter((c) => c.status === "FAIL").map((c) => `<li>${esc(c.message)}</li>`).join("");
    body.innerHTML = `<div class="card qc-fail"><p>This recording can't be used as a lesson:</p><ul>${msgs || "<li>It couldn't be analysed.</li>"}</ul>
      <p><a href="#/new/${a.class_id}">Record or upload it again</a></p></div>`;
    return;
  }

  body.replaceChildren($("#t-studio-lesson").content.cloneNode(true));
  const editable = a.status === "ready";
  $("#st-preview", v).href = `#/a/${a.id}`;
  $("#st-publish-box", v).hidden = !editable;
  const suggested = a.notes.filter((n) => n.suggest_split_at != null).length;
  $("#st-notes-hint", v).textContent = editable && suggested
    ? `Select a note to fix it. ${suggested} note${suggested > 1 ? "s" : ""} marked "?" may be two notes blended by room echo: listen and split if so.`
    : editable
    ? "Select a note to fix it: a note the app misheard, a slip in your own playing, or a stray sound."
    : "This lesson is published, so its notes are locked.";

  // ---- synced playback: the roll's playhead and the current note follow the audio
  const audioEl = $("#st-audio", v);
  audioEl.src = `/recordings/${a.reference_recording_id}/audio?token=${encodeURIComponent(auth.token)}`;
  let selected = null, raf = 0;
  onLeave(() => { cancelAnimationFrame(raf); audioEl.pause(); });
  const current = () => {
    const t = audioEl.currentTime - (a.ref_offset || 0);
    return a.notes.findIndex((n) => t >= n.start && t < n.end);
  };
  const drawRoll = () => {
    const cv = $("#roll", v);
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
    if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
    const g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const css = getComputedStyle(document.documentElement);
    const end = a.notes[a.notes.length - 1].end, p = a.notes.map((n) => n.pitch);
    const lo = Math.min(...p) - 2, hi = Math.max(...p) + 2;
    const x = (t) => 8 + (t / end) * (W - 16), y = (m) => H - 24 - ((m - lo) / (hi - lo)) * (H - 40);
    a.sections.forEach((s, k) => {
      g.fillStyle = k % 2 ? "rgba(79,116,160,0.06)" : "rgba(79,116,160,0.13)";
      g.fillRect(x(s.t_start), 0, x(s.t_end) - x(s.t_start), H);
      g.fillStyle = css.getPropertyValue("--muted");
      g.font = `13px ${css.getPropertyValue("--font")}`;
      g.fillText(`Section ${s.id}`, x(s.t_start) + 4, H - 6);
    });
    const now = current();
    a.notes.forEach((n, k) => {
      g.fillStyle = k === selected ? css.getPropertyValue("--you")
        : k === now ? css.getPropertyValue("--ink") : css.getPropertyValue("--teacher");
      g.fillRect(x(n.start), y(n.pitch) - 5, Math.max(3, x(n.end) - x(n.start) - 2), 10);
    });
    if (!audioEl.paused) {
      const t = audioEl.currentTime - (a.ref_offset || 0);
      g.strokeStyle = css.getPropertyValue("--ink"); g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(x(t), 0); g.lineTo(x(t), H); g.stroke();
      $("#st-now", v).textContent = now >= 0 ? `Now: note ${now + 1}, ${a.notes[now].note}` : "";
      debug.studioNote = now;
    }
    raf = requestAnimationFrame(drawRoll);
  };
  $("#st-play", v).addEventListener("click", (e) => {
    if (audioEl.paused) { audioEl.play(); e.target.textContent = "Pause"; e.target.setAttribute("aria-pressed", "true"); }
    else { audioEl.pause(); e.target.textContent = "Play your recording"; e.target.setAttribute("aria-pressed", "false"); }
  });

  // ---- notes list + correction
  const list = $("#st-notes", v);
  const drawNotes = () => {
    list.replaceChildren();
    a.notes.forEach((n, k) => {
      const b = document.createElement("button");
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", String(k === selected));
      b.className = "note-chip" + (n.corrected ? " corrected" : "") + (n.suggest_split_at != null && editable ? " suggest" : "");
      b.textContent = `${k + 1} ${n.note}` + (n.suggest_split_at != null && editable ? " ?" : "");
      if (n.suggest_split_at != null && editable) b.setAttribute("aria-description", "may be two notes");
      b.addEventListener("click", () => select(k));
      list.append(b);
    });
  };
  const select = (k) => {
    selected = k;
    drawNotes();
    const n = a.notes[k];
    $("#st-note-edit", v).hidden = !editable;
    $("#st-note-info", v).textContent = `Note ${k + 1}: ${n.note}, ${(n.end - n.start).toFixed(2)} s long` +
      (n.corrected ? " (corrected by you)" : "") + (n.split_by_teacher ? " (split by you)" : "");
    // Room echo can merge two same notes (A4, A4) into one. The app suggests
    // where it heard a bow change; the teacher listens and decides.
    const hint = $("#st-split-hint", v);
    hint.hidden = n.suggest_split_at == null;
    hint.textContent = n.suggest_split_at == null ? "" :
      "This may be two notes played as one (room echo can blend a repeated note). " +
      "Listen: if you played it twice, split it where the app heard a new bow stroke.";
    $("#st-split", v).textContent = n.suggest_split_at != null ? "Split where the new bow stroke is" : "Split into two notes";
    audioEl.currentTime = n.start + (a.ref_offset || 0);  // jump there to listen
  };
  const edit = async (change, done) => {
    try {
      a = await api(`/assignments/${a.id}/notes`, { method: "PUT", json: { edits: [{ index: selected + 1, ...change }] } });
      say(done);
      if (change.delete) { selected = null; $("#st-note-edit", v).hidden = true; }
      drawNotes();
      $("#st-ranges", v).value = rangesText();
      if (selected !== null) select(selected);
    } catch (err) { say(err.message); }
  };
  $("#st-up", v).addEventListener("click", () => edit({ midi: a.notes[selected].midi + 1 }, `Note ${selected + 1} is now ${midiName(a.notes[selected].midi + 1)}`));
  $("#st-down", v).addEventListener("click", () => edit({ midi: a.notes[selected].midi - 1 }, `Note ${selected + 1} is now ${midiName(a.notes[selected].midi - 1)}`));
  $("#st-delete", v).addEventListener("click", () => edit({ delete: true }, `Note ${selected + 1} deleted`));
  // the server splits at the app's suggested point if there is one, else in the middle
  $("#st-split", v).addEventListener("click", () => edit({ split: true }, `Note ${selected + 1} split into two`));

  // ---- sections
  const rangesText = () => a.sections.map((s) => `${s.first + 1}-${s.last + 1}`).join(", ");
  $("#st-ranges", v).value = rangesText();
  if (!editable) {  // published: sections are locked, like the notes
    $("#st-ranges", v).disabled = true;
    $("#st-sections button", v).hidden = true;
    $("#st-ranges-hint", v).textContent = "This lesson is published, so its sections are locked.";
  }
  $("#st-sections", v).addEventListener("submit", async (e) => {
    e.preventDefault();
    const parts = e.target.ranges.value.split(",").map((r) => r.trim().split(/\s*[-–]\s*/).map(Number));
    if (parts.some((p) => p.length !== 2 || p.some((n) => !Number.isInteger(n)))) return say("Write sections like 1-7, 8-14");
    try {
      a = await api(`/assignments/${a.id}/sections`, { method: "PUT", json: { sections: parts.map(([first, last]) => ({ first, last })) } });
      say(`Saved ${a.sections.length} section${a.sections.length > 1 ? "s" : ""}`);
    } catch (err) { say(err.message); }
  });
  $("#st-publish", v).addEventListener("click", async () => {
    try { await api(`/assignments/${a.id}/publish`, { method: "POST" }); say("Published: your students can see this lesson now."); go(`#/t/${a.id}`); }
    catch (err) { say(err.message); }
  });

  drawNotes();
  drawRoll();
  if (a.status === "published") await drawDashboard(a, v);
}

async function drawDashboard(a, v) {
  const dash = $("#st-dash", v);
  dash.hidden = false;
  $(".player", v).before(dash);  // once published, "how is my class doing?" comes first
  const [prog, sum] = await Promise.all([api(`/assignments/${a.id}/progress`), api(`/assignments/${a.id}/class_summary`)]);
  // class heatmap: one block per section, coloured by how many students passed it
  const heat = $("#st-heat", v);
  if (!sum.students_with_takes) heat.innerHTML = "<p>No full takes yet.</p>";
  else {
    heat.className = "heat";
    heat.innerHTML = sum.sections.map((s) => {
      const share = s.students ? s.passed / s.students : 0;
      const cls = share >= 0.8 ? "good" : share >= 0.5 ? "mid" : "low";
      return `<div class="heat-cell ${cls}" style="flex:${s.notes.split("-").reduce((x, y) => y - x + 1)}">
        <strong>Section ${s.id}</strong><span>${s.passed} of ${s.students} passed</span><span>average ${s.average}</span></div>`;
    }).join("");
  }
  const PROB = { "wrong note": "wrong note", missed: "skipped", flat: "flat", sharp: "sharp", late: "late", rushed: "rushed", "too soft": "too soft", "too loud": "too loud" };
  $("#st-hard", v).innerHTML = sum.hardest_notes.length
    ? sum.hardest_notes.map((h) => `<li>Note ${h.note_number} (${esc(h.note)}): ${h.students} student${h.students > 1 ? "s" : ""}, ${Object.entries(h.problems).map(([k, n]) => `${PROB[k] || k} ×${n}`).join(", ")}</li>`).join("")
    : "<li>No problem notes yet.</li>";
  const tbody = $("#st-progress tbody", v);
  for (const r of prog.students) {
    const tr = document.createElement("tr");
    const trend = r.scores_over_time.length ? r.scores_over_time.join(" → ") : "–";
    tr.innerHTML = `<th scope="row">${esc(r.student)}</th><td>${r.attempts_used} of ${prog.max_attempts}</td>
      <td>${r.best_score ?? "–"}</td><td>${r.all_passed ? "All ✓" : r.sections_passed.length ? r.sections_passed.join(", ") : "–"}</td>
      <td>${trend}</td><td><a href="#/r/${a.id}/${r.student_id}">Takes<span class="visually-hidden"> by ${esc(r.student)}</span></a></td>`;
    tbody.append(tr);
  }
}

async function takesView(assignmentId, studentId) {
  const v = render("#t-takes");
  $("#tk-back", v).href = `#/t/${assignmentId}`;
  const a = await api(`/assignments/${assignmentId}`);
  const name = (await api(`/assignments/${assignmentId}/progress`)).students.find((s) => s.student_id === studentId)?.student || "Student";
  $("#tk-title", v).textContent = `${name}: ${a.title}`;
  const takes = (await api(`/assignments/${assignmentId}/attempts`)).filter((t) => t.student_id === studentId);
  const list = $("#tk-list", v);
  if (!takes.length) list.innerHTML = "<li>No takes yet.</li>";
  let current = null;
  const show = (t) => {
    current = t;
    $("#tk-detail", v).hidden = false;
    $("#tk-detail-title", v).textContent = `${t.section_id ? `Section ${t.section_id} practice` : "Whole piece"}, ${new Date(t.created).toLocaleString()}`;
    $("#tk-audio", v).src = `/recordings/${t.recording_id}/audio?token=${encodeURIComponent(auth.token)}`;
    const rep = $("#tk-report", v);
    if (t.status !== "done") { rep.innerHTML = `<p class="qc-fail">Not counted: ${esc(t.feedback || t.status)}</p>`; return; }
    const strip = t.result.sections.map((s) => `<div class="seg ${s.passed ? "pass" : "fail"}" style="flex:${s.last - s.first + 1}">${t.section_id ? `Section ${t.section_id}` : `S${s.id}`} · ${s.score}</div>`).join("");
    rep.innerHTML = `<div class="report-top"><div class="score">${t.teacher_score ?? t.score}<small> / 100${t.teacher_score != null ? ` (your score · automatic score ${t.score})` : ""}</small></div></div>
      <div class="strip">${strip}</div><p class="feedback">${esc(t.feedback)}</p>` +
      t.comments.map((c) => `<blockquote class="comment">You wrote: ${esc(c.body)}</blockquote>`).join("");
  };
  for (const t of takes) {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.className = "link";
    b.textContent = `${t.section_id ? `Section ${t.section_id} practice` : "Whole piece"}: ${t.status === "done" ? (t.teacher_score ?? t.score) : "not counted"}`;
    b.addEventListener("click", () => show(t));
    li.append(b);
    list.append(li);
  }
  $("#tk-comment", v).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (!f.checkValidity()) return f.reportValidity();
    const json = { body: f.body.value };
    if (f.score_override.value !== "") json.score_override = Number(f.score_override.value);
    try {
      const t = await api(`/attempts/${current.id}/comments`, { method: "POST", json });
      say("Comment sent");
      f.reset();
      show(t);
    } catch (err) { say(err.message); }
  });
  if (takes.length) show(takes[takes.length - 1]);
}

route();
