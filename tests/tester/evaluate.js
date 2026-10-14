// Accuracy report for tester/dsp.js on synthetic takes with known notes.
//   node tests/tester/evaluate.js            full run (about 2 minutes)
//   node tests/tester/evaluate.js --quick    1 seed per cell
// Exits 1 when a threshold in THRESHOLDS is missed.
const D = require("../../tester/dsp.js");
const S = require("./synth.js");
const SR = 44100;
const quick = process.argv.includes("--quick");
const SEEDS = quick ? [1] : [1, 2, 3, 4];
const CONDS = {
  clean: { },
  room: { room: 0.7 },
  noisy: { snrDb: 24 },
  phone: { room: 0.3, snrDb: 30, level: 0.2 },
};
// violin/viola and ukulele/mandolin overlap in register and sound; telling them apart
// from pitch and tone alone is not reliable, so they also count as a "group" hit.
const GROUPS = [["violin", "viola"], ["ukulele", "mandolin"]];
const sameGroup = (a, b) => a === b || GROUPS.some((g) => g.includes(a) && g.includes(b));
const THRESHOLDS = { noteRecall: 0.9, notePrecision: 0.9, instrumentTop1: 0.7, instrumentGroup: 0.9, familyAcc: 0.95 };

function matchNotes(truth, found) {
  const used = new Set(); let tp = 0, optional = 0;
  for (const t of truth) {
    let best = -1, bd = 1e9;
    found.forEach((f, i) => {
      if (used.has(i)) return;
      const dt = Math.abs(f.t0 - t.t0);
      const ok = Math.abs(f.midi - t.midi) <= 0.5 || (t.glideTo !== undefined && Math.abs(f.midi - t.glideTo) <= 0.5);
      if (dt <= 0.18 && ok && dt < bd) { bd = dt; best = i; }
    });
    if (best >= 0) { used.add(best); if (t.glide) optional++; else tp++; }
  }
  // a veena glide target is optional: not counted missing when absent, not counted extra when found
  const required = truth.filter((t) => !t.glide).length;
  const extraGlides = found.filter((f, i) => !used.has(i) && f.glide).length;
  return { tp, fn: required - tp, fp: found.length - used.size - extraGlides };
}

const rows = [], confusion = {};
let falseTalk = 0, falseNonKey = 0, takesWithFalse = 0;
let totals = { tp: 0, fn: 0, fp: 0 }, top1 = 0, grp = 0, fam = 0, n = 0, confCorrect = [], confWrong = [];
const byInst = {}, byCond = {};
const t0 = Date.now();
for (const inst of D.INSTRUMENT_IDS) {
  for (const [cname, copt] of Object.entries(CONDS)) {
    for (const seed of SEEDS) {
      const tk = S.renderTake(inst, SR, Object.assign({ seed: seed * 101 + inst.length, count: 12 }, copt));
      const r = D.analyzeOffline(tk.samples, SR, { saMidi: 60 });
      const m = matchNotes(tk.truth, r.keys);
      const ok = r.instrument.best === inst;
      const famOk = D.INSTRUMENTS[r.instrument.best] && D.INSTRUMENTS[r.instrument.best].family === D.INSTRUMENTS[inst].family;
      for (const k of ["tp", "fn", "fp"]) totals[k] += m[k];
      top1 += ok; grp += sameGroup(r.instrument.best, inst); fam += !!famOk; n++;
      (ok ? confCorrect : confWrong).push(r.instrument.confidence);
      confusion[inst] = confusion[inst] || {}; confusion[inst][r.instrument.best] = (confusion[inst][r.instrument.best] || 0) + 1;
      const bi = (byInst[inst] = byInst[inst] || { tp: 0, fn: 0, fp: 0, ok: 0, n: 0 });
      const bc = (byCond[cname] = byCond[cname] || { tp: 0, fn: 0, fp: 0, ok: 0, n: 0 });
      for (const b of [bi, bc]) { b.tp += m.tp; b.fn += m.fn; b.fp += m.fp; b.ok += ok; b.n++; }
      const ft = r.events.filter((x) => x.type === "talking").length, fn_ = r.events.filter((x) => x.type === "non_key").length;
      falseTalk += ft; falseNonKey += fn_; if (ft || fn_) takesWithFalse++;
      rows.push({ inst, cname, seed, ok, got: r.instrument.best, conf: r.instrument.confidence, m, ev: r.events.length });
    }
  }
}
const pct = (x) => (100 * x).toFixed(0) + "%";
const rec = (b) => b.tp / Math.max(1, b.tp + b.fn), pre = (b) => b.tp / Math.max(1, b.tp + b.fp);
console.log("\nNOTE DETECTION (found within 0.18 s and 50 cents of the true note)");
console.log("instrument  recall  precision  instrument-ID");
for (const [k, b] of Object.entries(byInst)) console.log(k.padEnd(10), pct(rec(b)).padStart(6), pct(pre(b)).padStart(9), pct(b.ok / b.n).padStart(13));
console.log("\ncondition   recall  precision  instrument-ID");
for (const [k, b] of Object.entries(byCond)) console.log(k.padEnd(10), pct(rec(b)).padStart(6), pct(pre(b)).padStart(9), pct(b.ok / b.n).padStart(13));
console.log("\nCONFUSION (rows = real instrument, columns = detected)");
const ids = D.INSTRUMENT_IDS;
console.log("".padEnd(10) + ids.map((i) => i.slice(0, 5).padStart(6)).join(""));
for (const a of ids) console.log(a.padEnd(10) + ids.map((b) => String((confusion[a] || {})[b] || 0).padStart(6)).join(""));
const avg = (a) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
console.log("\nmean confidence when right " + pct(avg(confCorrect)) + ", when wrong " + pct(avg(confWrong)));
const summary = { noteRecall: rec(totals), notePrecision: pre(totals), instrumentTop1: top1 / n, instrumentGroup: grp / n, familyAcc: fam / n };
console.log("\nTOTAL", Object.entries(summary).map(([k, v]) => k + " " + pct(v)).join("  "), " takes:", n, " time", ((Date.now() - t0) / 1000).toFixed(0) + "s");
const wrong = rows.filter((r) => !r.ok);
if (wrong.length) console.log("\nmisidentified:", wrong.map((r) => `${r.inst}/${r.cname}/${r.seed}->${r.got}(${pct(r.conf)})`).join("  "));
console.log("\nFALSE ALARMS on clean instrument takes: talking " + falseTalk + ", non-key sounds " + falseNonKey + " (" + takesWithFalse + " of " + n + " takes had one)");

// ---- sounds that are NOT playing must be flagged, and must not disturb the notes
const neg = { talkTakes: 0, talkFlagged: 0, speechNotes: 0, speechSecs: 0, knocks: 0, knocksFlagged: 0, mixNotesOK: 0, mixTakes: 0 };
for (const seed of [1, 2, 3, 4, 5]) { // speech alone
  const z = new Float32Array(Math.round(9 * SR)); S.mixAt(z, S.speech(SR, 8, seed), 0.5, SR, 1);
  const r = D.analyzeOffline(S.normalise(z, 0.4), SR, { noiseDb: -70 });
  neg.talkTakes++; neg.talkFlagged += r.events.some((x) => x.type === "talking") ? 1 : 0; neg.speechNotes += r.notes.length; neg.speechSecs += 8;
}
for (const inst of ["violin", "cello", "guitar", "veena", "ukulele"]) for (const seed of [1, 2]) { // talking + knocks over playing
  const tk = S.renderTake(inst, SR, { seed: seed * 7 + 3, count: 10 });
  const clean = D.analyzeOffline(tk.samples, SR, { noiseDb: -70 });
  const x = Float32Array.from(tk.samples);
  // talking in the pause after the last note (talking over a ringing note is not detectable), knock in the gap after note 6
  const last = tk.truth[tk.truth.length - 1];
  S.mixAt(x, S.speech(SR, 2.2, seed + 40), last.t0 + last.dur + 0.8, SR, 0.5);
  const kt = tk.truth[6].t0 + tk.truth[6].dur + 0.05; S.mixAt(x, S.knock(SR, S.rng(seed)), kt, SR, 0.9);
  const r = D.analyzeOffline(x, SR, { noiseDb: -70 });
  neg.mixTakes++; neg.knocks++;
  neg.knocksFlagged += r.events.some((q) => q.type === "non_key" && Math.abs(q.t - kt) < 0.4) ? 1 : 0;
  neg.mixNotesOK += Math.abs(r.keys.length - clean.keys.length) <= 2 ? 1 : 0;
  neg.talkFlagged += 0;
  neg.talkTakes += 0;
  rows.mixTalk = (rows.mixTalk || 0) + (r.events.some((q) => q.type === "talking") ? 1 : 0);
}
console.log("\nTALKING / KNOCKS");
console.log("speech alone flagged as talking: " + neg.talkFlagged + " of 5 takes; notes wrongly found in 40 s of speech: " + neg.speechNotes);
console.log("talking over playing flagged: " + (rows.mixTalk || 0) + " of " + neg.mixTakes + "; knocks flagged: " + neg.knocksFlagged + " of " + neg.knocks + "; note count within 2 of the clean take: " + neg.mixNotesOK + " of " + neg.mixTakes);
summary.talkingFlagged = neg.talkFlagged / 5; summary.mixTalkingFlagged = (rows.mixTalk || 0) / neg.mixTakes; summary.knockFlagged = neg.knocksFlagged / neg.knocks; summary.notesKept = neg.mixNotesOK / neg.mixTakes;
THRESHOLDS.talkingFlagged = 1; THRESHOLDS.mixTalkingFlagged = 0.9; THRESHOLDS.knockFlagged = 0.9; THRESHOLDS.notesKept = 0.8;
const bad = Object.entries(THRESHOLDS).filter(([k, v]) => summary[k] < v);
if (bad.length) { console.log("\nBELOW TARGET:", bad.map(([k, v]) => `${k} ${pct(summary[k])} < ${pct(v)}`).join(", ")); process.exit(1); }
console.log("\nall thresholds met");
