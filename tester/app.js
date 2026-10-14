/* NUERiQ String Testing page. Analysis runs in the browser; a submitted test
   (report + recording) is saved to the results dashboard when the page is served
   by the StringCoach server. */
(function () {
  "use strict";
  const D = window.NueriqDSP;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtT = D.fmtT;
  const NATURALS = [0, 2, 4, 5, 7, 9, 11];
  const NAMES = Object.assign({ sitar: "Sitar", other: "Other string instrument", auto: "Not sure" },
    Object.fromEntries(Object.entries(D.INSTRUMENTS).map(([k, v]) => [k, v.label])));
  // sitar is judged against the veena family: both are plucked, long-ringing, gliding, buzzing-bridge instruments
  const familyOf = (id) => (id === "sitar" ? "veena" : id);
  // pairs that overlap in register and sound; telling them apart from a recording alone is not reliable
  const GROUPS = [["violin", "viola"], ["ukulele", "mandolin"]];
  const sameGroup = (a, b) => a === b || GROUPS.some((g) => g.includes(a) && g.includes(b));
  const nameWithAlt = (c) => NAMES[c.best] + (c.alsoPossible && c.alsoPossible.length ? " or " + c.alsoPossible.map((k) => NAMES[k].toLowerCase()).join(" or ") : "");
  const dbText = (db) => (db < -90 ? "silent" : db.toFixed(0) + " dB");
  // Internal reference note for the analysis only (the Sa field was removed:
  // nothing on screen depends on it any more).
  const REF_NOTE = 60;
  // Which instrument's tuning numbers the strings: the one confirmed in the
  // form, else the one chosen at the start, else the one the app detected.
  function stringInst() {
    const pick = (i) => (i && D.STRING_TUNINGS[i] ? i : null);
    return pick(S.instChosen) || pick(S.user && S.user.instrument)
      || (lastClass && lastClass.ready ? pick(lastClass.best) : null)
      || (S.result && S.result.instrument && S.result.instrument.ready ? pick(S.result.instrument.best) : null);
  }
  // "String 2" - or the note name where the instrument has no standard tuning
  function stringText(m) { const st = D.stringOf(m, stringInst()); return st ? "String " + st.num : D.noteName(m); }
  function stringShort(m) { const st = D.stringOf(m, stringInst()); return st ? "S" + st.num : D.noteName(m); }
  function stringSub(m) { const st = D.stringOf(m, stringInst()); return st ? st.name + " string · " + D.noteName(m) : ""; }

  const S = {
    step: 1, user: null, room: null, phase: "idle", sr: 44100, samples: null, result: null, quality: null,
    liveKeys: [], keys: [], corrections: [], problems: [], mode: "live", sel: null, nextId: 1,
    instChosen: null, instWhy: null, submitted: null, wavUrl: null, wavBlob: null,
  };

  // --------------------------------------------------------------- helpers
  function store(k, v) { try { if (v === undefined) return JSON.parse(localStorage.getItem("nueriq." + k)); localStorage.setItem("nueriq." + k, JSON.stringify(v)); } catch (e) { return null; } }
  function go(n) {
    S.step = n;
    for (let i = 1; i <= 5; i++) $("s" + i).hidden = i !== n;
    document.querySelectorAll("#steps li").forEach((li) => {
      const k = +li.dataset.step;
      li.className = k === n ? "cur" : k < n ? "done" : "";
      li.querySelector(".dot").textContent = k < n ? "✓" : k === n ? "…" : String(k);
      if (k === n) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    const h = $("s" + n).querySelector("h2"); if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }
    window.scrollTo(0, 0);
  }
  function setWho() {
    $("who").hidden = !S.user; $("topActions").hidden = !S.user;
    if (S.user) $("who").textContent = "Tester: " + S.user.name;
    $("net").textContent = "Your test is analysed on this device and saved privately for the NUERiQ team when you submit.";
  }

  // ----------------------------------------------------------- step 1: form
  const saved = store("user");
  if (saved) { $("fName").value = saved.name || ""; $("fEmail").value = saved.email || ""; $("fInst").value = saved.instrument || "auto"; $("fPiece").value = saved.piece || ""; $("fDevice").value = saved.device || ""; $("fRoom").value = saved.room || ""; }
  $("detailsForm").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = $("fName").value.trim(), email = $("fEmail").value.trim(), err = $("detailsErr");
    err.hidden = true;
    if (!name) { err.textContent = "Please enter your name."; err.hidden = false; $("fName").focus(); return; }
    if (!/^\S+@\S+\.\S+$/.test(email)) { err.textContent = "Please enter a valid e-mail address."; err.hidden = false; $("fEmail").focus(); return; }
    if (!$("fConsent").checked) { err.textContent = "Please tick the box to agree before starting."; err.hidden = false; $("fConsent").focus(); return; }
    S.user = { name, email, instrument: $("fInst").value, sa: REF_NOTE, piece: $("fPiece").value.trim(), device: $("fDevice").value.trim(), room: $("fRoom").value.trim() };
    store("user", S.user);
    setWho(); resetTake(); go(2);
  });
  $("btnNotYou").addEventListener("click", () => { store("user", {}); S.user = null; $("fName").value = ""; $("fEmail").value = ""; $("fConsent").checked = false; setWho(); go(1); });
  $("btnProblem").addEventListener("click", () => { $("problemText").value = ""; $("dlgProblem").showModal(); });
  $("dlgProblem").addEventListener("close", () => { if ($("dlgProblem").returnValue === "ok" && $("problemText").value.trim()) S.problems.push({ at: new Date().toISOString(), step: S.step, text: $("problemText").value.trim() }); });

  // ----------------------------------------------------- step 2: recording
  let ctx = null, stream = null, node = null, analyzer = null, chunks = [], roomChunks = [], roomLen = 0, recLen = 0, raf = 0, lastInstAt = 0, lastClass = null, levelHist = [];
  // The live graph has a FIXED pitch range for the instrument, so only time
  // moves (left to right); it used to re-centre up and down with the playing.
  const RANGES = { violin: [53, 90], mandolin: [53, 90], viola: [46, 84], cello: [34, 72], guitar: [38, 78],
                   ukulele: [58, 83], veena: [38, 79], sitar: [45, 82] };
  const rangeFor = (inst) => RANGES[inst] || [38, 88];
  const live = { label: null, labelMidi: NaN, yLo: 48, yHi: 76, drawn: 0 };

  function resetTake() {
    document.body.classList.remove("recording");
    S.phase = "idle"; S.samples = null; S.result = null; S.quality = null; S.liveKeys = []; S.keys = []; S.corrections = []; S.sel = null; S.instChosen = null; S.instWhy = null; S.submitted = null;
    if (S.wavUrl) URL.revokeObjectURL(S.wavUrl); S.wavUrl = null; S.wavBlob = null;
    $("ready").hidden = false; $("roomcheck").hidden = true; $("live").hidden = true; $("busy").hidden = true; $("micErr").hidden = true;
    analyzer = null; chunks = []; roomChunks = []; roomLen = 0; recLen = 0; levelHist = []; lastClass = null;
    live.label = null; live.labelMidi = NaN;
    [live.yLo, live.yHi] = rangeFor(S.user ? S.user.instrument : null);
  }
  const micMsg = (e) => e && e.name === "NotAllowedError" ? "The microphone is blocked. Allow microphone access in the browser (the icon near the address bar) and press Record again."
    : e && e.name === "NotFoundError" ? "No microphone was found. Connect one and try again."
    : e && e.name === "NotReadableError" ? "The microphone is being used by another app. Close it and try again."
    : "The microphone could not be started: " + ((e && e.message) || e);

  async function startRecording() {
    $("micErr").hidden = true;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { $("micErr").textContent = "This browser cannot record. Use a recent Chrome, Edge, Firefox or Safari, over https or localhost."; $("micErr").hidden = false; return; }
    try {
      // voice processing OFF: it would cut or reshape instrument sound
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
      const AC = window.AudioContext || window.webkitAudioContext;
      ctx = new AC(); await ctx.resume();
      await ctx.audioWorklet.addModule("worklet.js");
      const src = ctx.createMediaStreamSource(stream);
      node = new AudioWorkletNode(ctx, "nueriq-capture");
      const mute = ctx.createGain(); mute.gain.value = 0; // keeps the graph running without echoing the mic
      src.connect(node); node.connect(mute); mute.connect(ctx.destination);
    } catch (e) { cleanupAudio(); $("micErr").textContent = micMsg(e); $("micErr").hidden = false; return; }
    S.sr = ctx.sampleRate; S.phase = "room"; roomChunks = []; roomLen = 0; chunks = []; recLen = 0;
    $("ready").hidden = true; $("roomcheck").hidden = false; $("roomCount").textContent = "3";
    node.port.onmessage = (e) => onSamples(e.data);
  }
  function cleanupAudio() {
    try { if (node) { node.port.onmessage = null; node.disconnect(); } } catch (e) {}
    try { if (stream) stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
    try { if (ctx) ctx.close(); } catch (e) {}
    node = null; stream = null; ctx = null; cancelAnimationFrame(raf);
  }
  function rmsOf(a) { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * a[i]; return Math.sqrt(s / a.length); }

  function onSamples(chunk) {
    if (S.phase === "room") {
      roomChunks.push(chunk); roomLen += chunk.length;
      const secs = roomLen / S.sr;
      $("roomCount").textContent = String(Math.max(1, Math.ceil(3 - secs)));
      $("roomBar").style.width = Math.min(100, rmsOf(chunk) * 800) + "%";
      if (secs >= 3) finishRoomCheck();
      return;
    }
    if (S.phase !== "live") return;
    chunks.push(chunk); recLen += chunk.length;
    analyzer.push(chunk);
    levelHist.push(rmsOf(chunk)); if (levelHist.length > 44) levelHist.shift();
    if (recLen / S.sr >= 300) stopRecording(); // 5 minute limit
  }
  function finishRoomCheck() {
    const all = new Float32Array(roomLen); let o = 0; for (const c of roomChunks) { all.set(c, o); o += c.length; }
    const m = D.measureRoom(all.subarray(Math.round(0.4 * S.sr)), S.sr); // skip the microphone's start-up
    S.room = { noiseDb: m.noiseDb, peakDb: m.peakDb };
    analyzer = new D.StreamAnalyzer(S.sr, { gateDb: D.gateFrom(m.noiseDb), live: true, hop: 0.02 });
    S.phase = "live";
    $("roomcheck").hidden = true; $("live").hidden = false; buildLevels();
    document.body.classList.add("recording");               // compact: everything in one window
    $("s2").scrollIntoView({ block: "start" });
    lastInstAt = 0; $("liveTalk").hidden = true; raf = requestAnimationFrame(drawLive);
  }
  function buildLevels() { const L = $("levels"); L.innerHTML = ""; for (let i = 0; i < 44; i++) L.appendChild(document.createElement("b")); }

  // live label with hysteresis so it does not flicker between neighbouring notes
  function updateLabel() {
    const fr = analyzer.frames, now = analyzer.duration, v = [];
    for (let i = fr.length - 1; i >= 0 && now - fr[i].t < 0.3; i--) if (fr[i].voiced && now - fr[i].t < 0.18) v.push(fr[i].midi);
    if (v.length < 2) { if (!v.length) { live.label = null; live.labelMidi = NaN; } return; }
    const m = D.median(v);
    if (isNaN(live.labelMidi) || Math.abs(m - live.labelMidi) > 0.65) live.labelMidi = Math.round(m);
    live.cents = Math.round((m - live.labelMidi) * 100);
    live.label = live.labelMidi;
  }

  function drawLive() {
    if (S.phase !== "live") return;
    const t = analyzer.duration;
    $("timer").textContent = fmtT(t);
    const bars = $("levels").children;
    for (let i = 0; i < bars.length; i++) { const v = levelHist[levelHist.length - bars.length + i] || 0; bars[i].style.height = Math.min(30, 4 + Math.sqrt(v) * 80) + "px"; }
    updateLabel();
    if (live.label != null) {
      $("nowNote").textContent = stringText(live.label); $("nowWest").textContent = stringSub(live.label);
      $("nowCents").textContent = (live.cents > 0 ? "+" : "") + live.cents + " cents";
    } else { $("nowNote").textContent = "—"; $("nowWest").textContent = ""; $("nowCents").textContent = ""; }
    const fr = analyzer.frames;
    const all = liveClosed(), notes = all.filter((n) => !isVoice(n)), cur = analyzer.current;
    const talkHeard = all.some((n) => isVoice(n) && t - n.t1 < 4) || analyzer.bursts.some((b) => t - b.t < 4);
    $("liveTalk").hidden = !talkHeard;
    drawGraph($("graph"), { frames: fr, notes: notes.map(liveNoteView), cur: cur && cur.frames.length > 2 ? liveCurView(cur) : null, tMin: Math.max(0, t - 8), tMax: Math.max(8, t), yLo: live.yLo, yHi: live.yHi, strings: D.STRING_TUNINGS[stringInst()], now: true });
    $("liveCount").textContent = notes.length + " notes heard";
    if (performance.now() - lastInstAt > 800) { lastInstAt = performance.now(); updateInstChip(); }
    raf = requestAnimationFrame(drawLive);
  }
  // closed notes get their features once; a note that wobbles like a voice is not drawn or counted
  function liveClosed() { const fresh = analyzer.notes.filter((n) => n.attackMs === undefined); if (fresh.length) D.noteFeatures(fresh, analyzer.env, analyzer.dt); return analyzer.notes; }
  const isVoice = (n) => n.wobble !== undefined && D.voiceScore(n) >= 3;
  function liveNoteView(n) { return { t0: n.t0, t1: n.t1, midi: Math.round(n.midi), label: stringShort(n.midi) }; }
  function liveCurView(c) { const m = D.median(c.frames.map((f) => f.midi)); return { t0: c.t0, t1: c.frames[c.frames.length - 1].t, midi: Math.round(m), label: stringShort(m), active: true }; }

  function classifyNow(a) { return D.classifyInstrument(liveClosed().filter((n) => !isVoice(n)), a.tracker.glides, {}); }
  function probBars(c, n) {
    const ids = D.INSTRUMENT_IDS.slice().sort((a, b) => c.probs[b] - c.probs[a]).slice(0, n || 3);
    return ids.map((id, i) => '<div class="pbar' + (i === 0 ? " top" : "") + '"><span>' + esc(NAMES[id]) + '</span><i><b style="width:' + Math.round(c.probs[id] * 100) + '%"></b></i><span>' + Math.round(c.probs[id] * 100) + "%</span></div>").join("");
  }
  // The instrument guess is no longer shown while recording; it still runs,
  // because with "Not sure" it decides which tuning numbers the strings.
  function updateInstChip() { lastClass = classifyNow(analyzer); }
  function stopRecording() {
    if (S.phase !== "live") return;
    S.phase = "busy"; cancelAnimationFrame(raf); document.body.classList.remove("recording");
    const n = recLen, samples = new Float32Array(n); let o = 0; for (const c of chunks) { samples.set(c, o); o += c.length; }
    analyzer.finish();
    const liveNotes = liveClosed().filter((x) => x.t1 - x.t0 >= 0.07 && !isVoice(x));
    S.liveKeys = D.describeNotes(liveNotes, S.user.sa, 0).map((k) => Object.assign(k, { live: true }));
    cleanupAudio();
    S.samples = samples; $("live").hidden = true; $("busy").hidden = false;
    setTimeout(() => {
      try {
        S.result = D.analyzeOffline(samples, S.sr, { noiseDb: S.room.noiseDb, saMidi: S.user.sa });
        S.quality = D.analyzeQuality(S.result, { noiseDb: S.room.noiseDb });
        S.wavBlob = new Blob([D.encodeWav(samples, S.sr)], { type: "audio/wav" });
        S.wavUrl = URL.createObjectURL(S.wavBlob);
        showReview3();
      } catch (e) { console.error(e); $("busy").hidden = true; $("ready").hidden = false; S.phase = "idle"; $("micErr").textContent = "Sorry, checking the recording failed: " + e.message; $("micErr").hidden = false; }
    }, 60);
  }
  $("btnRec").addEventListener("click", startRecording);
  $("btnStop").addEventListener("click", stopRecording);
  document.addEventListener("keydown", (e) => {
    if (e.code !== "Space" || /INPUT|TEXTAREA|SELECT|BUTTON/.test((e.target.tagName || "")) ) return;
    if (S.step === 2 && S.phase === "live") { e.preventDefault(); stopRecording(); }
    else if (S.step === 2 && S.phase === "idle") { e.preventDefault(); startRecording(); }
  });

  // ---------------------------------------------------------- step 3: listen
  // What testers are told: only problems that make the recording unusable, one
  // sentence per kind. Individual events (knocks, short pauses, bursts of
  // noise) are kept in the report data for the team but not listed to testers.
  const SHOWN = ["short", "quiet", "clipping", "no_playing", "talking"];
  function testerMessages(q) {
    const out = new Map();
    for (const f of q.flags) if (SHOWN.includes(f.type) && !out.has(f.type))
      out.set(f.type, f.type === "talking" ? "Talking was heard during the recording. Please play without talking." : f.text);
    return [...out.values()];
  }
  function showReview3() {
    $("busy").hidden = true; S.phase = "done";
    const q = S.quality, r = S.result;
    $("s3sum").innerHTML = "You recorded <b>" + fmtT(r.duration) + "</b>. <b>" + S.liveKeys.length + "</b> notes were heard while you played. Listen if you like, then choose.";
    $("aud3").src = S.wavUrl;
    const box = $("s3flags"), msgs = testerMessages(q);
    if (msgs.length) {
      box.innerHTML = '<div class="flagbox" role="status"><b>Please record again:</b><ul>' + msgs.map((m) => "<li>" + esc(m) + "</li>").join("") + "</ul></div>";
    } else box.innerHTML = '<div class="flagbox good" role="status"><b>The recording looks good.</b> Room noise ' + dbText(S.room.noiseDb) + ", " + r.notes.length + " notes found.</div>";
    $("btnUse").className = "btn" + (q.recordAgain ? "" : " primary"); $("btnAgain").className = "btn" + (q.recordAgain ? " primary" : "");
    go(3);
  }
  $("btnAgain").addEventListener("click", () => { resetTake(); go(2); });
  $("btnUse").addEventListener("click", () => { initReview(); go(4); });

  // -------------------------------------------------------- step 4: the form
  const aud = $("aud4");
  let rv = { lo: 40, hi: 80, playKey: null };

  function initReview() {
    const r = S.result;
    S.keys = r.keys.map((k) => Object.assign({}, k, { id: S.nextId++, state: "auto", deleted: false, orig: { midi: k.midi, t0: k.t0 } }));
    S.sel = null; S.mode = "live"; document.querySelector('input[name="mode"][value="live"]').checked = true;
    aud.src = S.wavUrl; aud.playbackRate = 1; aud.preservesPitch = true; $("speed").value = "1"; $("loop").value = "off";
    const mids = S.keys.concat(S.liveKeys).map((k) => k.midi);
    rv.lo = (mids.length ? Math.min(...mids) : S.user.sa) - 4; rv.hi = (mids.length ? Math.max(...mids) : S.user.sa + 12) + 4;
    if (rv.hi - rv.lo < 14) rv.hi = rv.lo + 14;
    const c = S.result.instrument;
    $("instLine").innerHTML = c.ready ? "The app thinks this is a <b>" + esc(nameWithAlt(c)) + "</b> (" + Math.round(c.confidence * 100) + "% sure)" + (S.user.instrument !== "auto" ? ", you said <b>" + esc(NAMES[S.user.instrument]) + "</b>." : ".") : "The app could not tell (too few clear notes). Please say what you played.";
    const sel = $("instFinal"); sel.innerHTML = '<option value="">Choose…</option>' + ["violin", "viola", "cello", "guitar", "veena", "sitar", "ukulele", "mandolin", "other"].map((i) => '<option value="' + i + '">' + NAMES[i] + "</option>").join("");
    sel.value = S.user.instrument !== "auto" ? S.user.instrument : ""; S.instChosen = sel.value || null;
    $("instEvidence").innerHTML = c.evidence.map((e) => "<li>" + esc(e) + "</li>").join("") + (c.ready ? "<li>Chances: " + D.INSTRUMENT_IDS.map((i) => NAMES[i] + " " + Math.round(c.probs[i] * 100) + "%").join(", ") + "</li>" : "");
    $("why").hidden = true; $("instWhy").hidden = true;
    renderTable(); redraw4(); updateModeNote(); checkInstMismatch();
  }
  function viewKeys() { return S.mode === "live" ? S.liveKeys : S.keys.filter((k) => !k.deleted); }
  function updateModeNote() { $("modeNote").textContent = S.mode === "live" ? "Showing what you saw while playing (read-only)" : "Showing the checked keys (editable below)"; }
  document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", () => { S.mode = r.value; updateModeNote(); renderTable(); redraw4(); }));
  $("speed").addEventListener("change", () => { aud.playbackRate = +$("speed").value; });

  function keyNoteOptions(sel, midi) {
    let h = ""; for (let m = rv.lo - 6; m <= rv.hi + 6; m++) h += '<option value="' + m + '"' + (m === midi ? " selected" : "") + ">" + D.noteName(m) + (D.stringOf(m, stringInst()) ? " · String " + D.stringOf(m, stringInst()).num : "") + "</option>";
    return h;
  }
  function renderTable() {
    const tb = $("keysTable").querySelector("tbody"), keys = S.mode === "live" ? S.liveKeys : S.keys, editable = S.mode === "checked";
    tb.innerHTML = "";
    keys.slice().sort((a, b) => a.t0 - b.t0).forEach((k, i) => {
      const tr = document.createElement("tr");
      tr.className = [S.sel === k.id ? "sel" : "", k.deleted ? "deleted" : "", k.state === "edited" ? "edited" : "", k.state === "added" ? "added" : ""].join(" ");
      tr.dataset.id = k.id;
      const cents = k.centsOff === undefined ? "" : (k.centsOff > 0 ? "+" : "") + k.centsOff + "¢";
      tr.innerHTML = "<td>" + (i + 1) + "</td><td>" + k.t0.toFixed(2) + " s</td><td>" + esc(stringText(k.midi)) + "</td><td>" + (editable && !k.deleted ? '<select aria-label="Note for key ' + (i + 1) + '">' + keyNoteOptions(null, k.midi) + "</select>" : esc(D.noteName(k.midi))) + "</td><td>" + cents + (k.glide ? " glide" : "") + "</td><td>" + k.dur.toFixed(2) + " s</td><td></td>";
      const act = tr.lastChild;
      const play = document.createElement("button"); play.className = "btn"; play.type = "button"; play.textContent = "▶"; play.setAttribute("aria-label", "Play key " + (i + 1));
      play.onclick = (ev) => { ev.stopPropagation(); S.sel = k.id; aud.currentTime = Math.max(0, k.t0 - 0.05); aud.play(); renderTable(); };
      act.appendChild(play);
      if (editable) {
        const del = document.createElement("button"); del.className = "btn"; del.type = "button"; del.textContent = k.deleted ? "Undo" : "Delete";
        del.onclick = (ev) => { ev.stopPropagation(); toggleDelete(k); };
        act.appendChild(del);
        const s = tr.querySelector("select"); if (s) s.onchange = () => editKey(k, +s.value);
      }
      tr.onclick = () => { S.sel = k.id; renderTable(); redraw4(); };
      tb.appendChild(tr);
    });
    if (!keys.length) tb.innerHTML = '<tr><td colspan="7" class="muted">No keys were found.</td></tr>';
  }
  function logCorr(c) { c.at = Date.now(); S.corrections.push(c); return c; }
  function askWhy(corr, options) {
    const box = $("why"); box.hidden = false; box.innerHTML = '<span class="q">Why?</span>';
    options.forEach((o) => { const b = document.createElement("button"); b.className = "btn"; b.type = "button"; b.textContent = o[1]; b.onclick = () => { corr.why = o[0]; box.hidden = true; }; box.appendChild(b); });
  }
  function editKey(k, midi) {
    const before = k.midi; if (midi === before) return;
    const c = logCorr({ type: "change_note", keyId: k.id, t: k.t0, before, after: midi, origMidi: k.orig ? k.orig.midi : null, why: null });
    k.midi = midi; if (k.state !== "added") k.state = "edited";
    renderTable(); redraw4();
    const octave = Math.abs(midi - before) % 12 === 0;
    askWhy(c, [["wrong_note", "It showed the wrong note"], octave ? ["wrong_octave", "Right note, wrong octave"] : ["wrong_octave_alt", "Heard a harmonic / a neighbouring string"], ["out_of_tune", "I played it out of tune"], ["other", "Other"]]);
  }
  function toggleDelete(k) {
    k.deleted = !k.deleted;
    const c = logCorr({ type: k.deleted ? "delete_key" : "undo_delete", keyId: k.id, t: k.t0, midi: k.midi, why: null });
    renderTable(); redraw4();
    if (k.deleted) askWhy(c, [["not_a_key", "That wasn't a note (talking / noise)"], ["duplicate", "The same note was shown twice"], ["echo", "It was the echo / ringing of the last note"], ["other", "Other"]]); else $("why").hidden = true;
  }
  $("btnAddKey").addEventListener("click", () => {
    if (S.mode !== "checked") { S.mode = "checked"; document.querySelector('input[name="mode"][value="checked"]').checked = true; updateModeNote(); }
    const t = aud.currentTime || 0, last = S.keys.filter((k) => !k.deleted && k.t0 <= t).pop();
    const k = { id: S.nextId++, t0: t, t1: t + 0.4, dur: 0.4, midi: last ? last.midi : S.user.sa, centsOff: 0, state: "added", deleted: false };
    S.keys.push(k); S.sel = k.id; renderTable(); redraw4();
    const c = logCorr({ type: "add_key", keyId: k.id, t, midi: k.midi, why: null });
    askWhy(c, [["missed", "It missed a note I played"], ["missed_soft", "It missed a very soft note"], ["merged", "Two notes were shown as one"], ["other", "Other"]]);
  });
  function checkInstMismatch() {
    const c = S.result.instrument, ch = S.instChosen, box = $("instWhy");
    if (!ch || !c.ready || familyOf(ch) === c.best || ch === "other") { box.hidden = true; S.instWhy = null; return; }
    box.hidden = false; box.innerHTML = '<span class="q">The app said ' + esc(NAMES[c.best]) + ". Which is true?</span>";
    [["app_wrong", "I played a " + (NAMES[ch] || ch).toLowerCase() + " — the app got it wrong"], ["user_mistake", "I chose the wrong instrument by mistake"], ["unsure", "Not sure"]].forEach((o) => { const b = document.createElement("button"); b.className = "btn"; b.type = "button"; b.textContent = o[1]; b.onclick = () => { S.instWhy = o[0]; box.querySelectorAll(".btn").forEach((x) => x.style.borderColor = ""); b.style.borderColor = "var(--accent)"; }; box.appendChild(b); });
  }
  $("instFinal").addEventListener("change", () => { S.instChosen = $("instFinal").value || null; S.instWhy = null; checkInstMismatch(); });

  // playback sync
  let playRaf = 0;
  aud.addEventListener("play", () => { cancelAnimationFrame(playRaf); const tick = () => { onTick(); if (!aud.paused) playRaf = requestAnimationFrame(tick); }; tick(); });
  aud.addEventListener("seeked", onTick); aud.addEventListener("pause", onTick);
  function keyAt(t) { const ks = viewKeys(); let best = null; for (const k of ks) if (t >= k.t0 - 0.02 && t <= Math.min(k.t1, k.t0 + k.dur) + 0.02) best = k; return best; }
  function onTick() {
    const t = aud.currentTime;
    if ($("loop").value === "key" && S.sel != null && !aud.paused) { const k = S.keys.concat(S.liveKeys).find((x) => x.id === S.sel); if (k && t > k.t0 + Math.max(k.dur, 0.3) + 0.1) aud.currentTime = Math.max(0, k.t0 - 0.05); }
    const k = keyAt(t); rv.playKey = k;
    $("now4Note").textContent = k ? stringText(k.midi) : "—"; $("now4West").textContent = k ? stringSub(k.midi) : "";
    document.querySelectorAll("#keysTable tbody tr").forEach((tr) => tr.classList.toggle("playing", !!k && +tr.dataset.id === k.id));
    redraw4();
  }
  function redraw4() {
    if (S.step !== 4) return;
    const t = aud.currentTime || 0, r = S.result;
    drawGraph($("graph4"), { frames: r.frames, notes: viewKeys().map((k) => ({ t0: k.t0, t1: k.t0 + Math.max(k.dur, 0.12), midi: k.midi, label: stringShort(k.midi), active: rv.playKey && rv.playKey.id === k.id, selected: S.sel === k.id })), tMin: 0, tMax: Math.max(1, r.duration), yLo: rv.lo, yHi: rv.hi, strings: D.STRING_TUNINGS[stringInst()], playhead: t, onsets: r.onsets });
  }
  $("graph4").addEventListener("click", (e) => { const b = e.target.getBoundingClientRect(); aud.currentTime = ((e.clientX - b.left - 44) / (b.width - 52)) * S.result.duration; });
  window.addEventListener("resize", () => { if (S.step === 4) redraw4(); });

  // --------------------------------------------------------------- drawing
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  function fit(canvas) {
    if (!canvas.dataset.h) { canvas.dataset.h = canvas.getAttribute("height"); canvas.style.height = canvas.dataset.h + "px"; }
    const dpr = window.devicePixelRatio || 1, w = Math.max(280, canvas.clientWidth), h = +canvas.dataset.h;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
    const g = canvas.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); return { g, w, h };
  }
  function drawGraph(canvas, o) {
    const { g, w, h } = fit(canvas), L = 44, R = 8, T = 8, B = 20, pw = w - L - R, ph = h - T - B;
    const X = (t) => L + ((t - o.tMin) / (o.tMax - o.tMin)) * pw, Y = (m) => T + (1 - (m - o.yLo) / (o.yHi - o.yLo)) * ph;
    g.clearRect(0, 0, w, h); g.fillStyle = "#101816"; g.fillRect(0, 0, w, h);
    g.font = "11px system-ui"; g.textBaseline = "middle";
    // faint line for every semitone; the instrument's open strings as labelled
    // guide lines (string 1 = highest); without a standard tuning, C lines
    for (let m = Math.ceil(o.yLo); m <= o.yHi; m++) {
      const y = Y(m); if (y < T || y > T + ph) continue;
      g.strokeStyle = "#18221f"; g.lineWidth = 1; g.beginPath(); g.moveTo(L, y); g.lineTo(L + pw, y); g.stroke();
    }
    const guides = o.strings ? o.strings.map((open, i) => [open, (i + 1) + " · " + D.noteName(open).replace(/-?\d+$/, "")])
      : Array.from({ length: 9 }, (_, k) => 24 + 12 * k).map((c) => [c, D.noteName(c)]);
    for (const [m, txt] of guides) {
      const y = Y(m); if (y < T || y > T + ph) continue;
      g.strokeStyle = "#2f6b6e"; g.lineWidth = 1.5; g.beginPath(); g.moveTo(L, y); g.lineTo(L + pw, y); g.stroke();
      g.fillStyle = "#40c0c8"; g.textAlign = "right"; g.fillText(txt, L - 5, y);
    }
    g.textAlign = "center"; g.textBaseline = "top"; g.fillStyle = "#7f918b";
    const span = o.tMax - o.tMin, stepT = span > 60 ? 10 : span > 20 ? 5 : span > 8.1 ? 2 : 1;
    for (let t = Math.ceil(o.tMin / stepT) * stepT; t <= o.tMax; t += stepT) { g.fillText(fmtT(t), X(t), h - B + 4); g.strokeStyle = "#18221f"; g.beginPath(); g.moveTo(X(t), T); g.lineTo(X(t), T + ph); g.stroke(); }
    g.save(); g.beginPath(); g.rect(L, T, pw, ph); g.clip();
    if (o.onsets) { g.strokeStyle = "#2b3f3b"; for (const on of o.onsets) { const x = X(on.t); if (x >= L && x <= L + pw) { g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + ph); g.stroke(); } } }
    const bar = (n, col, txt) => {
      const x0 = X(n.t0), x1 = Math.max(x0 + 3, X(n.t1)), y = Y(n.midi), bh = Math.max(6, ph / (o.yHi - o.yLo) * 0.8);
      g.fillStyle = col; g.beginPath(); (g.roundRect ? g.roundRect(x0, y - bh / 2, x1 - x0, bh, 4) : g.rect(x0, y - bh / 2, x1 - x0, bh)); g.fill();
      if (txt && x1 - x0 > 26) { g.fillStyle = n.active ? "#f6c48f" : "#b9e6e9"; g.font = "bold 11px system-ui"; g.textAlign = "left"; g.textBaseline = "bottom"; g.fillText(n.label, x0 + 2, y - bh / 2 - 1); }
    };
    for (const n of o.notes) bar(n, n.active ? "#f0903a" : n.selected ? "#f7c36b" : "rgba(64,192,200,.55)", true);
    if (o.cur) bar(o.cur, "rgba(240,144,58,.8)", true);
    // pitch trace
    g.lineWidth = 2; let prev = null;
    for (const f of o.frames) {
      if (f.t < o.tMin - 0.1) continue; if (f.t > o.tMax + 0.1) break;
      if (!f.voiced) { prev = null; continue; }
      const x = X(f.t), y = Y(f.midi);
      if (prev && f.t - prev.t < 0.07 && Math.abs(f.midi - prev.midi) < 3) { g.strokeStyle = "rgba(232,239,237," + (0.35 + 0.6 * f.conf).toFixed(2) + ")"; g.beginPath(); g.moveTo(X(prev.t), Y(prev.midi)); g.lineTo(x, y); g.stroke(); }
      prev = f;
    }
    if (o.playhead !== undefined) { const x = X(o.playhead); g.strokeStyle = "#f0903a"; g.lineWidth = 2; g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + ph); g.stroke(); }
    g.restore();
  }
  // ------------------------------------------------------------- submitting
  function matchKeys(shown, truth) {
    const used = new Set(); let tp = 0;
    for (const t of truth) {
      let best = -1, bd = 1e9;
      shown.forEach((s, i) => { if (used.has(i)) return; const d = Math.abs(s.t0 - t.t0); if (d <= 0.25 && s.midi === t.midi && d < bd) { bd = d; best = i; } });
      if (best >= 0) { used.add(best); tp++; }
    }
    return { shown: shown.length, truth: truth.length, correct: tp, wrong: shown.length - tp, missed: truth.length - tp, precision: shown.length ? tp / shown.length : null, recall: truth.length ? tp / truth.length : null };
  }
  function buildReport() {
    const r = S.result, sa = S.user.sa, finalKeys = S.keys.filter((k) => !k.deleted).sort((a, b) => a.t0 - b.t0);
    const c = r.instrument, chosen = S.instChosen, initial = S.user.instrument;
    const cents = finalKeys.filter((k) => k.centsOff !== undefined && !k.glide).map((k) => Math.abs(k.centsOff));
    const mids = finalKeys.map((k) => k.midi);
    const byWhy = {}; S.corrections.forEach((x) => { const w = x.type + ":" + (x.why || "no reason"); byWhy[w] = (byWhy[w] || 0) + 1; });
    const f = c.features || {};
    const strInst = stringInst();
    // What the player confirmed: each note the app showed was kept (right),
    // changed (wrong note), deleted (not played); notes the player added were missed.
    const live = (k) => !k.deleted;
    const right = S.keys.filter((k) => live(k) && k.state === "auto").length;
    const corrected = S.keys.filter((k) => live(k) && k.state === "edited").length;
    const missed = S.keys.filter((k) => live(k) && k.state === "added").length;
    const extra = S.keys.filter((k) => k.deleted && k.state !== "added").length;
    const played = right + corrected + missed;
    const strip = S.keys.filter((k) => !(k.deleted && k.state === "added")).sort((a, b) => a.t0 - b.t0)
      .map((k) => ({ t: +k.t0.toFixed(2), note: D.noteName(k.midi), kind: k.deleted ? "extra" : k.state === "auto" ? "right" : k.state === "edited" ? "corrected" : "missed" }));
    const groups = {};
    for (const k of finalKeys) {
      if (k.centsOff === undefined || k.glide) continue;
      const st = D.stringOf(k.midi, strInst), key = st ? "String " + st.num + " (" + st.name + ")" : D.noteName(k.midi);
      (groups[key] = groups[key] || { label: key, order: st ? st.num : k.midi, cents: [] }).cents.push(Math.abs(k.centsOff));
    }
    const strRows = Object.values(groups).sort((a, b) => a.order - b.order).map((g) => ({
      label: g.label, notes: g.cents.length, meanAbsCents: Math.round(g.cents.reduce((a, b) => a + b, 0) / g.cents.length),
      within15: g.cents.filter((x) => x <= 15).length / g.cents.length }));
    return {
      app: "NUERiQ String Testing", version: 1, createdAt: new Date().toISOString(),
      tester: { name: S.user.name, piece: S.user.piece, device: S.user.device, room: S.user.room }, userAgent: navigator.userAgent, sampleRate: S.sr,
      recording: { seconds: r.duration, roomNoiseDb: S.room.noiseDb, peakDb: S.quality.peakDb, clipped: r.clippedFrac },
      quality: { flags: S.quality.flags.map((x) => ({ type: x.type, t: x.t, text: x.text })), recordAgainSuggested: S.quality.recordAgain },
      instrument: { chosenBefore: initial, finalTruth: chosen, detected: c.ready ? c.best : null, second: c.second || null, confidence: c.ready ? c.confidence : null, probs: c.probs, family: c.family || null,
        correct: c.ready && chosen ? familyOf(chosen) === c.best : null, closeMatch: c.ready && chosen ? sameGroup(familyOf(chosen), c.best) : null, alsoPossible: c.alsoPossible || [], whyIfWrong: S.instWhy, evidence: c.evidence, features: f },
      keys: finalKeys.map((k) => ({ t: +k.t0.toFixed(3), dur: +k.dur.toFixed(3), note: D.noteName(k.midi), string: D.stringOf(k.midi, strInst) ? D.stringOf(k.midi, strInst).num : null, midi: k.midi, centsOff: k.centsOff, glide: !!k.glide, state: k.state })),
      accuracy: { live: matchKeys(S.liveKeys, finalKeys), checked: matchKeys(r.keys, finalKeys) },
      verification: { right, corrected, missed, extra, played, percent: played ? right / played : null, strip },
      intonation: { byString: !!D.STRING_TUNINGS[strInst], instrument: strInst, rows: strRows },
      playing: { notes: finalKeys.length, lowest: mids.length ? D.noteName(Math.min(...mids)) : null, highest: mids.length ? D.noteName(Math.max(...mids)) : null,
        meanAbsCents: cents.length ? cents.reduce((a, b) => a + b, 0) / cents.length : null, within15: cents.length ? cents.filter((x) => x <= 15).length / cents.length : null, tuningOffsetCents: r.tuningOffsetCents, glides: r.glides.length },
      corrections: S.corrections.map((x) => ({ type: x.type, t: x.t, before: x.before, after: x.after, midi: x.midi, why: x.why })), correctionSummary: byWhy,
      problems: S.problems, note: $("finalNote").value.trim(),
    };
  }
  $("btnSubmit").addEventListener("click", () => {
    if (!S.instChosen) { $("instFinal").focus(); $("instFinal").setCustomValidity("x"); $("instLine").insertAdjacentHTML("beforeend", ' <span class="err">← please choose the instrument you played</span>'); return; }
    S.submitted = buildReport(); aud.pause(); renderReport(S.submitted); go(5); saveResult(S.submitted);
  });
  const pct = (x) => (x === null || x === undefined ? "—" : Math.round(x * 100) + "%");
  function renderReport(R) {
    const V = R.verification, A = R.accuracy, P = R.playing, I = R.instrument, el = $("report");
    const kv = (a, b) => '<div class="kv"><span>' + a + "</span><span>" + b + "</span></div>";
    const inst = NAMES[I.finalTruth] || (I.detected ? NAMES[I.detected] : "Instrument not set");
    const corrRows = Object.entries(R.correctionSummary).map(([k, v]) => kv(esc(k.replace(":", " — ").replace(/_/g, " ")), v)).join("") || kv("No changes needed", "");
    const COL = { right: "Heard correctly", corrected: "Wrong note (you corrected it)", missed: "Missed (you added it)", extra: "Shown but not played (you deleted it)" };
    const strip = V.strip.length ? '<div class="vstrip" role="img" aria-label="Each note in order: ' + V.right + ' heard correctly, ' + V.corrected + ' corrected, ' + V.missed + ' missed, ' + V.extra + ' extra">' +
      V.strip.map((x) => '<i class="' + x.kind + '" title="' + esc(x.note + " at " + x.t.toFixed(1) + " s: " + COL[x.kind]) + '"></i>').join("") + "</div>" +
      '<div class="vkey">' + Object.entries(COL).map(([k, v]) => '<span><i class="' + k + '"></i>' + v + "</span>").join("") + "</div>" : "";
    const IN = R.intonation;
    const intoRows = IN.rows.length ? '<div class="table-wrap"><table><thead><tr><th>' + (IN.byString ? "String" : "Note") + '</th><th>Notes</th><th>Avg error</th><th title="Notes within ±15 cents">In tune</th></tr></thead><tbody>' +
      IN.rows.map((r) => "<tr><td>" + esc(r.label) + "</td><td>" + r.notes + "</td><td>" + r.meanAbsCents + " cents</td><td>" + pct(r.within15) + "</td></tr>").join("") + "</tbody></table></div>" : '<p class="muted">Not enough steady notes to measure.</p>';
    el.innerHTML =
      '<p class="center big-line">' + esc(inst) + (V.played ? " · the app heard <b>" + V.right + " of " + V.played + "</b> notes correctly (" + pct(V.percent) + "), checked by the player." : ".") + "</p>" +
      '<p class="center muted">' + esc(R.tester.name) + " · " + new Date(R.createdAt).toLocaleString() + " · " + fmtT(R.recording.seconds) + " recorded</p>" +
      '<p class="center save" id="saveState" role="status"></p>' +
      '<div class="rgrid">' +
      '<div class="rbox wide"><h3>App vs what you really played</h3><div class="big-num">' + pct(V.percent) + '</div><div class="muted">of the notes played were heard correctly, note by note, as confirmed by the player</div>' + strip +
      kv("Heard correctly", V.right) + kv("Wrong note, corrected by the player", V.corrected) + kv("Missed, added by the player", V.missed) + kv("Shown but not played", V.extra) +
      kv("While playing (live view)", A.live.truth ? A.live.correct + " of " + A.live.truth + " right" : "—") + "</div>" +
      '<div class="rbox"><h3>Intonation by ' + (IN.byString ? "string" : "note") + "</h3>" + intoRows + '<p class="muted small">Average distance from the exact pitch, after allowing for the instrument\'s overall tuning. "In tune" = within ±15 cents, which is generally heard as in tune.</p></div>' +
      '<div class="rbox"><h3>Your playing</h3><div class="big-num">' + P.notes + " notes</div>" + kv("Range", esc((P.lowest || "—") + " to " + (P.highest || "—"))) + kv("Average tuning error", P.meanAbsCents === null ? "—" : P.meanAbsCents.toFixed(0) + " cents") + kv("Notes within ±15 cents", pct(P.within15)) + kv("Instrument's overall tuning", (P.tuningOffsetCents > 0 ? "+" : "") + Math.round(P.tuningOffsetCents) + " cents") + kv("Glides", P.glides) + "</div>" +
      '<div class="rbox"><h3>Recording quality</h3><div class="big-num ' + (R.quality.recordAgainSuggested ? "warn-t" : "ok-t") + '">' + (R.quality.recordAgainSuggested ? "Could be better" : "Good") + "</div>" + kv("Room noise", dbText(R.recording.roomNoiseDb)) + kv("Loudest sound", dbText(R.recording.peakDb)) + testerMessages(R.quality).map((m) => '<p class="warn-t small">' + esc(m) + "</p>").join("") + "</div>" +
      '<div class="rbox"><h3>Changes made (' + R.corrections.length + ')</h3>' + corrRows + "</div>" +
      "</div>" +
      '<div class="rbox"><h3>All notes</h3><div class="table-wrap"><table><thead><tr><th>#</th><th>Time</th><th>' + (IN.byString ? "String" : "Note") + "</th><th>Note</th><th>Tuning</th><th>Length</th></tr></thead><tbody>" +
      R.keys.map((k, i) => "<tr><td>" + (i + 1) + "</td><td>" + k.t.toFixed(2) + " s</td><td>" + (k.string ? "String " + k.string : "—") + "</td><td>" + esc(k.note) + "</td><td>" + (k.centsOff === undefined ? "" : (k.centsOff > 0 ? "+" : "") + k.centsOff + "¢") + (k.glide ? " glide" : "") + "</td><td>" + k.dur.toFixed(2) + " s</td></tr>").join("") + "</tbody></table></div></div>" +
      (R.note ? '<div class="rbox"><h3>Note from the player</h3><p>' + esc(R.note) + "</p></div>" : "");
  }
  // Save the finished test to the results dashboard (report, then recording).
  // The recording is sent with a one-time token, so only this page can attach it.
  async function saveResult(R) {
    const st = $("saveState"); st.className = "center save"; st.textContent = "Saving your test…";
    try {
      const res = await fetch("/tester-api/results", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(R) });
      if (!res.ok) throw new Error("the server answered " + res.status);
      const saved = await res.json();
      if (S.wavBlob) {
        const a = await fetch("/tester-api/results/" + saved.id + "/audio", { method: "PUT", headers: { "Content-Type": "audio/wav", "X-Upload-Token": saved.upload_token }, body: S.wavBlob });
        if (!a.ok) throw new Error("the recording upload was refused (" + a.status + ")");
      }
      R.savedId = saved.id;
      st.className = "center save ok-t"; st.textContent = "✓ Saved to the results dashboard";
    } catch (e) {
      st.className = "center save warn-t";
      st.textContent = "Couldn't save to the results dashboard (" + e.message + "). Please download the report and the recording below and send them to us.";
    }
  }
  function download(name, blob) { const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500); }
  $("btnJson").addEventListener("click", () => download("nueriq-report-" + Date.now() + ".json", new Blob([JSON.stringify(S.submitted, null, 2)], { type: "application/json" })));
  $("btnWav").addEventListener("click", () => download("nueriq-recording-" + Date.now() + ".wav", S.wavBlob));
  $("btnPrint").addEventListener("click", () => window.print());
  $("btnNew").addEventListener("click", () => { resetTake(); go(2); });

  setWho(); go(1);
  window.__nueriq = { S, D, live };
})();
