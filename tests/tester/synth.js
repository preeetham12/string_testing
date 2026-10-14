// Synthetic string-instrument takes with KNOWN notes, for testing tester/dsp.js.
// Each instrument is modelled from how it really sounds (see tester/dsp.js):
//   bowed  - slow attack, level sustain, vibrato, bow noise
//   guitar - pluck: instant attack, two-stage decay, high partials fade fast
//   veena  - pluck + very long ring, buzzing bridge keeps high partials alive,
//            meend (glide while ringing), sympathetic drone strings
//   ukulele- pluck, high register, very fast decay
//   mandolin - doubled strings, tremolo (rapid repeated plucks)
const DSP = require("../../tester/dsp.js");

function rng(seed) { // mulberry32
  let a = seed >>> 0;
  return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
const hz = (m) => 440 * Math.pow(2, (m - 69) / 12);
const noiseGen = (r) => () => (r() + r() + r() - 1.5) * 1.15;

// ---- single notes: return Float32Array of the note's own sound (length = ring time)
function bowedNote(sr, f, dur, p, r) {
  const n = Math.round((dur + 0.05) * sr), out = new Float32Array(n);
  const attack = p.attack, vibHz = p.vibHz, vibC = p.vibCents, tilt = p.tilt, nz = noiseGen(r);
  let ph = new Float64Array(14).fill(0);
  let lp = 0;
  for (let i = 0; i < n; i++) {
    const t = i / sr;
    const vib = t > 0.25 ? vibC * Math.sin(2 * Math.PI * vibHz * t) : 0;
    const f0 = f * Math.pow(2, vib / 1200);
    let s = 0;
    for (let k = 1; k <= 12; k++) {
      ph[k] += (2 * Math.PI * f0 * k) / sr;
      s += Math.sin(ph[k]) / Math.pow(k, tilt);
    }
    let env = Math.min(1, t / attack);
    env *= Math.min(1, (dur + 0.05 - t) / 0.04);
    env *= 1 + 0.04 * Math.sin(2 * Math.PI * 0.7 * t + p.ph);
    lp += 0.2 * (nz() - lp); // bow noise
    out[i] = (0.2 * s + 0.03 * lp) * env;
  }
  return out;
}

function pluckedNote(sr, f, ringTime, p, r, glide) {
  const n = Math.round(ringTime * sr), out = new Float32Array(n);
  const K = p.partials, ph = new Float64Array(K + 1), nz = noiseGen(r);
  // frequency curve: optional glide (meend) from f to f*2^(semis/12) starting at g.t over g.dur
  for (let i = 0; i < n; i++) {
    const t = i / sr;
    let fr = f;
    if (glide && t > glide.t) {
      const x = Math.min(1, (t - glide.t) / glide.dur);
      fr = f * Math.pow(2, (glide.semis * (0.5 - 0.5 * Math.cos(Math.PI * x))) / 12);
    }
    let s = 0;
    for (let k = 1; k <= K; k++) {
      const fk = fr * k * Math.sqrt(1 + p.inharm * k * k);
      if (fk > sr * 0.45) continue;
      ph[k] += (2 * Math.PI * fk) / sr;
      const tau = p.tau / (1 + p.tauK * (k - 1));
      const dec = p.fastShare * Math.exp(-t / (p.fastTau / (1 + 0.3 * (k - 1)))) + (1 - p.fastShare) * Math.exp(-t / tau);
      s += (dec * Math.sin(ph[k])) / Math.pow(k, p.tilt);
    }
    if (p.buzz) s = Math.tanh(p.buzz * s) / p.buzz; // jivari: bridge buzz spreads energy into high partials
    const atk = Math.min(1, t / p.attack);
    const rel = Math.min(1, (ringTime - t) / 0.12);
    const click = t < 0.006 ? nz() * 0.3 * (1 - t / 0.006) : 0;
    out[i] = (0.35 * s + click * 0.2) * atk * rel;
  }
  return out;
}

function add(dst, src, at) { for (let i = 0; i < src.length && at + i < dst.length; i++) if (at + i >= 0) dst[at + i] += src[i]; }

// ---- instrument definitions
function params(inst, r) {
  const u = (a, b) => a + (b - a) * r();
  switch (inst) {
    case "violin": return { kind: "bowed", lo: 60, hi: 88, attack: u(0.04, 0.12), vibHz: u(5, 6.5), vibCents: r() < 0.3 ? 0 : u(10, 30), tilt: u(1.0, 1.5), ph: u(0, 6) };
    case "viola":  return { kind: "bowed", lo: 50, hi: 76, attack: u(0.05, 0.13), vibHz: u(4.8, 6), vibCents: r() < 0.3 ? 0 : u(10, 30), tilt: u(1.1, 1.6), ph: u(0, 6) };
    case "cello":  return { kind: "bowed", lo: 36, hi: 62, attack: u(0.06, 0.16), vibHz: u(4.5, 5.8), vibCents: r() < 0.3 ? 0 : u(10, 28), tilt: u(1.2, 1.8), ph: u(0, 6) };
    case "guitar": return { kind: "plucked", lo: 40, hi: 76, partials: 14, inharm: u(0.00003, 0.0002), tau: u(1.2, 2.6), tauK: u(0.25, 0.6), fastShare: u(0.45, 0.65), fastTau: u(0.12, 0.3), tilt: u(1.0, 1.4), attack: 0.002, buzz: 0 };
    case "veena":  return { kind: "plucked", lo: 48, hi: 80, partials: 22, inharm: 0.00002, tau: u(3.0, 5.0), tauK: u(0.06, 0.14), fastShare: u(0.35, 0.5), fastTau: u(0.15, 0.4), tilt: u(0.6, 0.9), attack: 0.003, buzz: u(2.2, 3.4), meend: true, drone: true };
    case "ukulele":return { kind: "plucked", lo: 60, hi: 81, partials: 8, inharm: 0.0001, tau: u(0.3, 0.5), tauK: 0.4, fastShare: u(0.3, 0.5), fastTau: u(0.08, 0.15), tilt: u(1.1, 1.6), attack: 0.002, buzz: 0 };
    case "mandolin":return { kind: "plucked", lo: 55, hi: 88, partials: 10, inharm: 0.00015, tau: u(0.4, 0.8), tauK: 0.5, fastShare: u(0.35, 0.5), fastTau: u(0.08, 0.15), tilt: u(1.0, 1.4), attack: 0.002, buzz: 0, tremolo: true, double: true };
  }
  throw new Error("unknown instrument " + inst);
}

// A short melody on a scale, as {midi, dur, gap}
function melody(inst, r, p, count) {
  const scale = [0, 2, 4, 5, 7, 9, 11], notes = [];
  const root = Math.round(p.lo + (p.hi - p.lo) * 0.25 * r() + 4);
  let deg = Math.floor(r() * 4);
  for (let i = 0; i < count; i++) {
    deg = Math.max(-3, Math.min(10, deg + Math.round((r() - 0.5) * 4)));
    const oct = Math.floor(deg / 7), s = ((deg % 7) + 7) % 7;
    let midi = root + 12 * oct + scale[s];
    midi = Math.max(p.lo, Math.min(p.hi, midi));
    const dur = 0.35 + r() * 0.9;
    notes.push({ midi, dur, gap: 0.04 + r() * 0.22 });
  }
  return notes;
}

/**
 * Render one take. Returns { samples, truth:[{midi,t0,dur,glide?}], inst }.
 * opts: { count, room:0..1 (echo), snrDb, seed }
 */
function renderTake(inst, sr, o) {
  o = o || {};
  const r = rng(o.seed || 1), p = params(inst, r);
  const count = o.count || 12;
  const mel = melody(inst, r, p, count);
  const lead = 0.8; let tcur = lead;
  const total = mel.reduce((s, n) => s + n.dur + n.gap, 0) + lead + 2.5;
  const out = new Float32Array(Math.round(total * sr)), truth = [];
  if (p.kind === "bowed") {
    for (const n of mel) {
      add(out, bowedNote(sr, hz(n.midi), n.dur, p, r), Math.round(tcur * sr));
      truth.push({ midi: n.midi, t0: tcur, dur: n.dur });
      tcur += n.dur + n.gap;
    }
  } else if (inst === "mandolin") {
    for (const n of mel) {
      const isTrem = n.dur > 0.7;
      const hits = isTrem ? Math.floor(n.dur / 0.075) : 1;
      for (let h = 0; h < hits; h++) {
        const f = hz(n.midi), ring = isTrem ? 0.35 : Math.min(1.2, n.dur + 0.3);
        const a = pluckedNote(sr, f, ring, p, r), b = pluckedNote(sr, f * Math.pow(2, 2.5 / 1200), ring, p, r);
        const mix = new Float32Array(a.length); for (let i = 0; i < a.length; i++) mix[i] = 0.5 * (a[i] + b[i]);
        add(out, mix, Math.round((tcur + h * 0.075) * sr));
      }
      truth.push({ midi: n.midi, t0: tcur, dur: n.dur, tremolo: isTrem });
      tcur += n.dur + n.gap;
    }
  } else {
    for (let idx = 0; idx < mel.length; idx++) {
      const n = mel[idx];
      let glide = null, ring = Math.min(p.tau * 1.2 + 0.4, n.dur + (inst === "veena" ? (r() < 0.25 ? 0.9 : 0.3) : 0.35));
      if (p.meend && r() < 0.35) { // veena: pull the string up 1-3 semitones while it rings
        const semis = (r() < 0.8 ? 1 : -1) * (1 + Math.floor(r() * 3));
        glide = { t: Math.min(0.3, n.dur * 0.4), dur: 0.16 + r() * 0.18, semis };
        ring = Math.max(ring, glide.t + glide.dur + 0.45);
      }
      add(out, pluckedNote(sr, hz(n.midi), ring, p, r, glide), Math.round(tcur * sr));
      truth.push({ midi: n.midi, t0: tcur, dur: n.dur, glideTo: glide ? n.midi + glide.semis : undefined });
      if (glide) truth.push({ midi: n.midi + glide.semis, t0: tcur + glide.t + glide.dur, dur: 0.4, glide: true });
      tcur += n.dur + n.gap;
    }
    if (p.drone) { // sympathetic strings: Sa, Pa and upper Sa ring quietly all the time
      const sa = p.lo + 7 - 12 * 0, ph = [0, 0, 0];
      const ds = [hz(sa - 12 + 12), hz(sa + 7 - 12 + 12), hz(sa + 12)];
      for (let i = 0; i < out.length; i++) {
        let s = 0;
        for (let j = 0; j < 3; j++) { ph[j] += (2 * Math.PI * ds[j]) / sr; s += Math.sin(ph[j]) + 0.4 * Math.sin(2 * ph[j]); }
        out[i] += 0.0035 * s * Math.min(1, i / (0.5 * sr));
      }
    }
  }
  let y = out;
  if (o.room) y = reverb(y, sr, 0.15 + 0.35 * o.room, 0.35 * o.room, r);
  y = normalise(y, 0.5 * (o.level || 1));
  const rms = Math.sqrt(y.reduce((s, v) => s + v * v, 0) / y.length);
  if (o.snrDb) {
    const nz = noiseGen(r); let lp = 0;
    for (let i = 0; i < y.length; i++) { lp += 0.05 * (nz() - lp); y[i] += lp * 3.2 * rms * Math.pow(10, -o.snrDb / 20) * 4 + (nz() * 0.3) * rms * Math.pow(10, -o.snrDb / 20); }
  }
  return { samples: y, truth, inst, sr };
}

function normalise(y, peak) {
  let m = 0; for (const v of y) if (Math.abs(v) > m) m = Math.abs(v);
  const g = m > 0 ? peak / m : 1, o = new Float32Array(y.length);
  for (let i = 0; i < y.length; i++) o[i] = y[i] * g;
  return o;
}

function reverb(x, sr, rt60, wet, r) {
  const n = Math.round(rt60 * sr), ir = new Float32Array(n), nz = noiseGen(r);
  for (let i = 0; i < n; i++) ir[i] = nz() * Math.exp((-6.9 * i) / n);
  const size = DSP_nextPow2(x.length + n);
  const ar = new Float64Array(size), ai = new Float64Array(size), br = new Float64Array(size), bi = new Float64Array(size);
  ar.set(x); br.set(ir);
  DSP.fft(ar, ai, false); DSP.fft(br, bi, false);
  for (let i = 0; i < size; i++) { const re = ar[i] * br[i] - ai[i] * bi[i], im = ar[i] * bi[i] + ai[i] * br[i]; ar[i] = re; ai[i] = im; }
  DSP.fft(ar, ai, true);
  let eIR = 0; for (const v of ir) eIR += v * v;
  const g = wet / Math.sqrt(eIR);
  const out = new Float32Array(x.length);
  for (let i = 0; i < x.length; i++) out[i] = x[i] + g * ar[i];
  return out;
}
const DSP_nextPow2 = (n) => { let p = 1; while (p < n) p <<= 1; return p; };

// ---- things that are NOT playing
function speech(sr, secs, seed) {
  const r = rng(seed), n = Math.round(secs * sr), out = new Float32Array(n);
  let t = 0.2;
  const formants = [[700, 1200, 2500], [300, 2200, 3000], [500, 1500, 2500], [350, 800, 2400], [600, 1900, 2700]];
  while (t < secs - 0.4) {
    const dur = 0.12 + r() * 0.2, f = formants[Math.floor(r() * formants.length)];
    const base = 100 + r() * 120, slope = (r() - 0.5) * 70, wob = (r() - 0.5) * 30;
    let ph = 0;
    const i0 = Math.round(t * sr), len = Math.round(dur * sr);
    for (let i = 0; i < len && i0 + i < n; i++) {
      const x = i / len, f0 = base + slope * x + wob * Math.sin(2 * Math.PI * 3 * x + seed);
      ph += (2 * Math.PI * f0) / sr;
      let s = 0;
      for (let k = 1; k <= 30; k++) {
        const fk = f0 * k; if (fk > 4500) break;
        let a = 0; for (const fm of f) a += Math.exp(-0.5 * Math.pow((fk - fm) / 180, 2));
        s += (a / k) * Math.sin(k * ph);
      }
      out[i0 + i] += 0.25 * s * Math.sin(Math.PI * x) ;
    }
    t += dur + (r() < 0.3 ? 0.25 + r() * 0.4 : 0.03 + r() * 0.08);
  }
  return out;
}
function knock(sr, r) { const n = Math.round(0.08 * sr), o = new Float32Array(n), nz = noiseGen(r); let lp = 0; for (let i = 0; i < n; i++) { lp += 0.3 * (nz() - lp); o[i] = lp * Math.exp((-i / sr) / 0.015) * 2; } return o; }
function clap(sr, r) { const n = Math.round(0.15 * sr), o = new Float32Array(n), nz = noiseGen(r); for (let i = 0; i < n; i++) o[i] = nz() * Math.exp((-i / sr) / 0.03) * 1.2; return o; }
function cough(sr, r) { const n = Math.round(0.3 * sr), o = new Float32Array(n), nz = noiseGen(r); let lp = 0; for (let i = 0; i < n; i++) { lp += 0.4 * (nz() - lp); o[i] = lp * Math.sin(Math.PI * i / n) * 1.5 * (1 + 0.5 * Math.sin(i / 90)); } return o; }

function mixAt(base, x, tSec, sr, gain) { const o = Math.round(tSec * sr); for (let i = 0; i < x.length && o + i < base.length; i++) base[o + i] += x[i] * gain; }

module.exports = { renderTake, speech, knock, clap, cough, mixAt, rng, hz, normalise, reverb };
