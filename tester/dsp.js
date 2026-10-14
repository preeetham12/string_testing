/*
 * NUERiQ string-instrument analysis (pure JavaScript, no dependencies).
 * Works in the browser (window.NueriqDSP) and in Node (require) so the same
 * code that runs live is the code the tests exercise.
 *
 *   StreamAnalyzer   - feed audio chunks, get pitch frames + notes while playing
 *   analyzeOffline   - the careful second pass over a finished recording
 *   classifyInstrument - violin / viola / cello / guitar / veena / ukulele / mandolin
 *   analyzeQuality   - talking, knocks, long pauses, noise, clipping, too quiet
 *
 * Instrument identification uses physical differences between the families
 * (see INSTRUMENTS below and docs/TESTER.md). The decision weights are
 * hand-set from those differences and checked on synthetic audio only; the
 * tester page collects real labelled takes so they can be tuned.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.NueriqDSP = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------------------------------------------------------------- basics
  const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
  const SWARA = ["Sa", "re", "Re", "ga", "Ga", "Ma", "ma", "Pa", "dha", "Dha", "ni", "Ni"];
  const midiToHz = (m) => 440 * Math.pow(2, (m - 69) / 12);
  const hzToMidi = (f) => 69 + 12 * Math.log2(f / 440);
  const noteName = (m) => {
    m = Math.round(m);
    return NOTE_NAMES[((m % 12) + 12) % 12] + (Math.floor(m / 12) - 1);
  };
  // Swara relative to Sa. ".Re" = lower octave, "Re." = upper octave.
  const swaraName = (m, sa) => {
    const d = Math.round(m) - sa;
    const o = Math.floor(d / 12);
    const s = SWARA[((d % 12) + 12) % 12];
    if (o < 0) return ".".repeat(Math.min(-o, 2)) + s;
    if (o > 0) return s + ".".repeat(Math.min(o, 2));
    return s;
  };
  // Standard tunings, string 1 = highest-pitched (how players number them).
  // Instruments without one standard tuning (veena, sitar) have none here.
  const STRING_TUNINGS = {
    violin: [76, 69, 62, 55],      // E5 A4 D4 G3
    mandolin: [76, 69, 62, 55],    // same as violin
    viola: [69, 62, 55, 48],       // A4 D4 G3 C3
    cello: [57, 50, 43, 36],       // A3 D3 G2 C2
    guitar: [64, 59, 55, 50, 45, 40], // E4 B3 G3 D3 A2 E2
    ukulele: [69, 64, 60, 67],     // A4 E4 C4 G4 (re-entrant)
  };
  // The string a note is most likely played on (first position): the
  // highest-pitched open string at or below the note. null if unknown.
  const stringOf = (m, inst) => {
    const t = STRING_TUNINGS[inst];
    if (!t || !isFinite(m)) return null;
    m = Math.round(m);
    let best = -1;
    t.forEach((open, i) => { if (open <= m && (best < 0 || open > t[best])) best = i; });
    return best < 0 ? null : { num: best + 1, open: t[best], name: NOTE_NAMES[((t[best] % 12) + 12) % 12] };
  };
  const noteNameToMidi = (name) => {
    const m = /^([A-G]#?)(-?\d)$/.exec(name);
    return m ? NOTE_NAMES.indexOf(m[1]) + 12 * (parseInt(m[2], 10) + 1) : null;
  };
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const sigmoid = (x) => 1 / (1 + Math.exp(-x));
  function median(a) {
    if (!a.length) return NaN;
    const s = Array.from(a).sort((x, y) => x - y);
    const h = s.length >> 1;
    return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
  }
  function percentile(a, p) {
    if (!a.length) return NaN;
    const s = Array.from(a).sort((x, y) => x - y);
    return s[clamp(Math.floor((p / 100) * (s.length - 1) + 0.5), 0, s.length - 1)];
  }
  const nextPow2 = (n) => { let p = 1; while (p < n) p <<= 1; return p; };
  const mean = (a) => { let s = 0; for (const x of a) s += x; return a.length ? s / a.length : NaN; };

  // ------------------------------------------------------------------- FFT
  const fftCache = {};
  function fftTables(n) {
    if (fftCache[n]) return fftCache[n];
    const cos = new Float64Array(n / 2), sin = new Float64Array(n / 2), rev = new Uint32Array(n);
    for (let i = 0; i < n / 2; i++) { cos[i] = Math.cos((2 * Math.PI * i) / n); sin[i] = -Math.sin((2 * Math.PI * i) / n); }
    const bits = Math.log2(n);
    for (let i = 0; i < n; i++) rev[i] = (rev[i >> 1] >> 1) | ((i & 1) << (bits - 1));
    return (fftCache[n] = { cos, sin, rev });
  }
  function fft(re, im, inverse) {
    const n = re.length, t = fftTables(n);
    for (let i = 0; i < n; i++) {
      const j = t.rev[i];
      if (j > i) { let x = re[i]; re[i] = re[j]; re[j] = x; x = im[i]; im[i] = im[j]; im[j] = x; }
    }
    for (let size = 2; size <= n; size <<= 1) {
      const half = size >> 1, step = n / size;
      for (let i = 0; i < n; i += size) {
        for (let j = 0, k = 0; j < half; j++, k += step) {
          const wr = t.cos[k], wi = inverse ? -t.sin[k] : t.sin[k];
          const a = i + j, b = a + half;
          const tr = re[b] * wr - im[b] * wi, ti = re[b] * wi + im[b] * wr;
          re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
        }
      }
    }
    if (inverse) for (let i = 0; i < n; i++) { re[i] /= n; im[i] /= n; }
  }

  // ------------------------------------------------------------------- YIN
  // Pitch by YIN (de Cheveigne & Kawahara 2002), difference function by FFT.
  // Window length N (power of two); lags up to N/2.
  function yin(x, sr, fmin, fmax, thr) {
    const N = x.length, W = N >> 1;
    const maxLag = Math.min(W - 2, Math.floor(sr / fmin)), minLag = Math.max(2, Math.floor(sr / fmax));
    const ar = new Float64Array(N), ai = new Float64Array(N), br = new Float64Array(N), bi = new Float64Array(N);
    for (let i = 0; i < W; i++) ar[i] = x[i];
    for (let i = 0; i < N; i++) br[i] = x[i];
    fft(ar, ai, false); fft(br, bi, false);
    for (let i = 0; i < N; i++) { // conj(A) * B
      const re = ar[i] * br[i] + ai[i] * bi[i], im = ar[i] * bi[i] - ai[i] * br[i];
      ar[i] = re; ai[i] = im;
    }
    fft(ar, ai, true);
    const p = new Float64Array(N + 1);
    for (let i = 0; i < N; i++) p[i + 1] = p[i] + x[i] * x[i];
    const e0 = p[W];
    const d = new Float64Array(maxLag + 2);
    for (let tau = 1; tau <= maxLag + 1; tau++) d[tau] = Math.max(0, e0 + (p[tau + W] - p[tau]) - 2 * ar[tau]);
    let running = 0;
    d[0] = 1;
    for (let tau = 1; tau <= maxLag + 1; tau++) { running += d[tau]; d[tau] = running > 0 ? (d[tau] * tau) / running : 1; }
    let best = -1;
    for (let tau = minLag; tau <= maxLag; tau++) {
      if (d[tau] < thr) { while (tau + 1 <= maxLag && d[tau + 1] < d[tau]) tau++; best = tau; break; }
    }
    if (best < 0) { // noisy: first dip nearly as deep as the deepest (avoids octave-low errors)
      let lo = Infinity;
      for (let tau = minLag; tau <= maxLag; tau++) if (d[tau] < lo) lo = d[tau];
      for (let tau = minLag; tau <= maxLag; tau++) {
        if (d[tau] <= lo + 0.1 && d[tau] <= d[tau - 1] && d[tau] <= d[tau + 1]) { best = tau; break; }
      }
    }
    if (best < 0) return { f0: 0, conf: 0 };
    const a = d[best - 1], b = d[best], c = d[best + 1], den = a - 2 * b + c;
    const shift = den !== 0 ? clamp((a - c) / (2 * den), -1, 1) : 0;
    return { f0: sr / (best + shift), conf: clamp(1 - b, 0, 1) };
  }

  // ------------------------------------------------------ spectral features
  const hannCache = {};
  function hann(n) {
    if (hannCache[n]) return hannCache[n];
    const w = new Float64Array(n);
    for (let i = 0; i < n; i++) w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1));
    return (hannCache[n] = w);
  }
  // centroid (Hz), flatness 0..1, share of power above 3 kHz.
  function spectralFeatures(x, sr) {
    const n = x.length, w = hann(n), re = new Float64Array(n), im = new Float64Array(n);
    for (let i = 0; i < n; i++) re[i] = x[i] * w[i];
    fft(re, im, false);
    const binHz = sr / n, lo = Math.max(1, Math.floor(60 / binHz)), hi = Math.min(n / 2 - 1, Math.floor(8000 / binHz));
    let sp = 0, sf = 0, hf = 0, logSum = 0, cnt = 0;
    for (let k = lo; k <= hi; k++) {
      const pw = re[k] * re[k] + im[k] * im[k] + 1e-18;
      sp += pw; sf += pw * k * binHz; logSum += Math.log(pw); cnt++;
      if (k * binHz >= 3000) hf += pw;
    }
    const power = new Float64Array(n / 2);
    for (let k = 0; k < n / 2; k++) power[k] = re[k] * re[k] + im[k] * im[k];
    return { centroid: sf / sp, flatness: Math.exp(logSum / cnt) / (sp / cnt), hf: hf / sp, power, binHz };
  }
  // Is there real energy at f0 itself? A pitch tracker can lock onto a
  // sub-multiple (two notes sounding together); strings always have a
  // fundamental, so move up an octave while it is missing.
  function fixSubharmonic(f0, sp, fmax) {
    const P = (f) => {
      const c = Math.round(f / sp.binHz), w = Math.max(1, Math.round(0.03 * f / sp.binHz));
      let m = 0; for (let k = Math.max(1, c - w); k <= Math.min(sp.power.length - 1, c + w); k++) if (sp.power[k] > m) m = sp.power[k];
      return m;
    };
    for (let guard = 0; guard < 3 && f0 * 2 <= fmax; guard++) {
      let strongest = 0;
      for (let k = 1; k <= 6; k++) if (f0 * k < sp.binHz * sp.power.length) strongest = Math.max(strongest, P(f0 * k));
      if (P(f0) < strongest * 0.003) f0 *= 2; else break;
    }
    return f0;
  }

  // ------------------------------------------------------------- envelope
  // Loudness in dB (full scale = 0) every 3 ms over 12 ms (long enough to
  // smooth the 82 Hz ripple of a low guitar string).
  class EnvelopeBuilder {
    constructor(sr) {
      this.sr = sr; this.hop = Math.max(1, Math.round(sr * 0.003)); this.win = this.hop * 4;
      this.dt = this.hop / sr; this.db = []; this.nextStart = 0;
    }
    update(buf, len) {
      const out = [];
      while (this.nextStart + this.win <= len) {
        let s = 0;
        for (let i = this.nextStart; i < this.nextStart + this.win; i++) s += buf[i] * buf[i];
        const v = 10 * Math.log10(s / this.win + 1e-12);
        this.db.push(v); out.push(v);
        this.nextStart += this.hop;
      }
      return out;
    }
    timeOf(i) { return (i * this.hop + this.win / 2) / this.sr; }
  }

  // Energy onsets. A pluck rises 5+ dB within ~15 ms; a bow change dips 6+ dB
  // and then climbs 8+ dB. Slow beating between ringing strings (a few dB over
  // 50+ ms) is neither, so it is not counted.
  class OnsetDetector {
    constructor(env, gateDb) {
      this.env = env; this.gate = gateDb; this.sm = []; this.last = -1;
      this.fast = Math.round(0.018 / env.dt); this.look = Math.round(0.06 / env.dt); this.back = Math.round(0.15 / env.dt);
      this.refract = Math.round(0.07 / env.dt); this.active = null;
    }
    push(db) {
      const sm = this.sm, i = sm.length;
      const s = i ? (db + sm[i - 1]) / 2 : db;
      sm.push(s);
      const a = this.active;
      if (a) {
        if (s > a.peakDb) { a.peakDb = s; a.peakI = i; }
        if (i - a.i0 > Math.round(0.35 / this.env.dt) || s < a.peakDb - 4) this.active = null;
      }
      if (i < 8 || s < this.gate + 3 || i - this.last <= this.refract) return null;
      let mnF = Infinity, mnS = Infinity, mxP = -Infinity;
      for (let j = Math.max(0, i - this.fast); j < i - 1; j++) if (sm[j] < mnF) mnF = sm[j];
      for (let j = Math.max(0, i - this.look); j < i - 1; j++) if (sm[j] < mnS) mnS = sm[j];
      for (let j = Math.max(0, i - this.look - this.back); j < i - this.look; j++) if (sm[j] > mxP) mxP = sm[j];
      const fastHit = s - mnF >= 5, slowHit = s - mnS >= 8 && mxP - mnS >= 6;
      if (!fastHit && !slowHit) return null;
      const mn = fastHit ? mnF : mnS;
      let i0 = i;
      while (i0 > 0 && sm[i0 - 1] > mn + 2) i0--;
      const on = { i0, i, t: this.env.timeOf(i0), riseDb: s - mn, peakDb: s, peakI: i };
      this.last = i; this.active = on;
      return on;
    }
  }

  // ---------------------------------------------------------- note tracker
  const DEFAULTS = {
    fmin: 50, fmax: 2500, hop: 0.01, gateDb: -58, confMin: 0.6, live: false,
    minNote: 0.06, // shortest note kept (s)
  };

  // Turns a stream of pitch frames (+ energy onsets) into notes.
  class NoteTracker {
    constructor(opts, dt, skip) {
      this.o = opts; this.dt = dt; this.skip = skip;
      this.cur = null; this.pend = []; this.notes = []; this.fragments = []; this.glides = [];
      this.unvoiced = 0; this.quiet = 0;
    }
    _open(t0, onset, viaGlide) {
      this.cur = { t0, t1: t0, frames: [], onset: onset || null, viaGlide: !!viaGlide, glideFrom: null };
      this.pend = []; this.unvoiced = 0; this.quiet = 0;
    }
    _runMedian() {
      const f = this.cur.frames, k = f.slice(-15), v = [];
      for (const x of k) if (x.t >= this.cur.t0 + this.skip) v.push(x.midi);
      return v.length >= 3 ? median(v) : NaN;
    }
    close(tEnd) {
      const c = this.cur;
      if (!c) return;
      this.cur = null; this.pend = [];
      c.t1 = Math.max(c.t0, tEnd);
      const usable = c.frames.filter((f) => f.t >= c.t0 + this.skip);
      const need = Math.max(3, Math.round(this.o.minNote / this.dt) - 2);
      if (usable.length < need) { this.fragments.push(c); return; }
      const m = median(usable.map((f) => f.midi));
      const plateau = usable.filter((f) => Math.abs(f.midi - m) <= 0.7).length / usable.length;
      if (plateau < 0.55) { this.fragments.push(c); return; }
      c.usable = usable; c.midi = m; c.plateau = plateau;
      this.notes.push(c);
      this.onNote && this.onNote(c);
    }
    push(f, onsets) {
      const dt = this.dt;
      for (const on of onsets) { // new energy onset: always a new note
        this.close(on.t);
        this._open(on.t, on, false);
      }
      const loud = f.db >= this.o.gateDb;
      if (!loud) {
        this.quiet++;
        if (this.cur && this.quiet * dt > 0.1) this.close(f.t - this.quiet * dt);
        return;
      }
      this.quiet = 0;
      if (!f.voiced) {
        this.unvoiced++;
        if (this.cur && this.unvoiced * dt > 0.25) this.close(f.t - this.unvoiced * dt);
        return;
      }
      this.unvoiced = 0;
      if (!this.cur) this._open(f.t - dt, null, false);
      const c = this.cur;
      if (f.t < c.t0 + this.skip) { c.frames.push(f); c.t1 = f.t; return; } // attack frames: mixed content
      const ref = this._runMedian();
      if (isNaN(ref) || Math.abs(f.midi - ref) <= 0.7) { // same note (vibrato stays inside 0.7 st)
        if (this.pend.length) this.pend = [];
        c.frames.push(f); c.t1 = f.t; return;
      }
      // pitch moved with no new energy onset: a slur or a glide (meend)
      this.pend.push(f);
      const p = this.pend, nStable = this.o.live ? 4 : 5;
      if (p.length >= nStable) {
        const tail = p.slice(-nStable), vals = tail.map((x) => x.midi);
        if (Math.max(...vals) - Math.min(...vals) <= 0.45) { // settled on a new pitch
          const from = ref, to = median(vals);
          const steps = [];
          for (let i = 1; i < p.length; i++) steps.push(Math.abs(p[i].midi - p[i - 1].midi));
          const smooth = p.length >= 6 && Math.max(...steps) < 1.2 && Math.abs(to - from) >= 1.2;
          const tStart = p[0].t;
          this.close(tStart);
          this._open(tail[0].t - this.skip, null, smooth);
          this.cur.glideFrom = from; this.cur.split = true;
          this.cur.frames.push(...tail); this.cur.t1 = tail[tail.length - 1].t;
          if (smooth) this.glides.push({ t: tStart, dur: tail[0].t - tStart, from, to });
          this.pend = [];
        }
      }
      if (p.length > Math.round(1.2 / dt)) this.pend = p.slice(-3); // never stabilised: give up
    }
  }

  // --------------------------------------------------------- stream analyser
  class StreamAnalyzer {
    constructor(sr, opts) {
      const o = (this.o = Object.assign({}, DEFAULTS, opts || {}));
      this.sr = sr;
      this.win = nextPow2(Math.round(sr * 0.075));
      this.hopS = Math.max(1, Math.round(sr * o.hop));
      this.buf = new Float32Array(sr * 20);
      this.len = 0;
      this.env = new EnvelopeBuilder(sr);
      this.onsetDet = new OnsetDetector(this.env, o.gateDb);
      this.onsets = []; this.pendingOnsets = []; this.bursts = [];
      this.frames = [];
      this.nextCenter = 0;
      this.dt = this.hopS / sr;
      this.skip = this.win / 2 / sr + 0.003;
      this.tracker = new NoteTracker(o, this.dt, this.skip);
      this.peak = 0; this.clipped = 0;
    }
    get notes() { return this.tracker.notes; }
    get current() { return this.tracker.cur; }
    get duration() { return this.len / this.sr; }
    push(chunk) {
      if (this.len + chunk.length > this.buf.length) {
        const nb = new Float32Array(Math.max(this.buf.length * 2, this.len + chunk.length));
        nb.set(this.buf.subarray(0, this.len)); this.buf = nb;
      }
      this.buf.set(chunk, this.len);
      for (let i = 0; i < chunk.length; i++) {
        const a = Math.abs(chunk[i]);
        if (a > this.peak) this.peak = a;
        if (a >= 0.98) this.clipped++;
      }
      this.len += chunk.length;
      const newDb = this.env.update(this.buf, this.len);
      for (const db of newDb) {
        const on = this.onsetDet.push(db);
        if (on) { this.onsets.push(on); this.pendingOnsets.push(on); }
      }
      return this._frames(false);
    }
    _frames(flush) {
      const out = [], half = this.win / 2, o = this.o;
      while (this.nextCenter + (flush ? 0 : half) < this.len) {
        const c = this.nextCenter, t = c / this.sr;
        // loudness at the frame centre: loudest 3 ms slot within +-15 ms
        const ei = Math.round((c - this.env.win / 2) / this.env.hop);
        let db = -120;
        for (let k = ei - 5; k <= ei + 5; k++) if (this.env.db[k] > db) db = this.env.db[k];
        const f = { t, db, f0: 0, midi: NaN, conf: 0, voiced: false, centroid: 0, flat: 0, hf: 0 };
        if (db >= o.gateDb - 3) {
          const seg = new Float32Array(this.win);
          const s0 = c - half;
          for (let i = 0; i < this.win; i++) { const j = s0 + i; seg[i] = j >= 0 && j < this.len ? this.buf[j] : 0; }
          const r = yin(seg, this.sr, o.fmin, o.fmax, 0.15);
          f.f0 = r.f0; f.conf = r.conf;
          const sp = spectralFeatures(seg, this.sr);
          f.centroid = sp.centroid; f.flat = sp.flatness; f.hf = sp.hf;
          if (r.conf >= o.confMin && r.f0 > 0 && db >= o.gateDb) {
            f.f0 = fixSubharmonic(r.f0, sp, o.fmax);
            f.midi = hzToMidi(f.f0); f.voiced = true;
          }
        }
        // octave-slip repair (causal in live mode): a single frame an octave away from its neighbours
        this._octaveFix(f);
        this.frames.push(f); out.push(f);
        const ons = [];
        while (this.pendingOnsets.length && this.pendingOnsets[0].t <= t + this.dt / 2) {
          const on = this.pendingOnsets.shift(), i0 = Math.round(on.t * this.sr);
          // A knock, clap or cough starts like a note but is noise, not a tuned string.
          on.flat = i0 + 1024 <= this.len ? spectralFeatures(this.buf.subarray(i0, i0 + 1024), this.sr).flatness : 0;
          if (on.flat > 0.1) { on.burst = true; this.bursts.push({ t: on.t, flat: on.flat }); } else ons.push(on);
        }
        this.tracker.push(f, ons);
        this.nextCenter += this.hopS;
      }
      return out;
    }
    _octaveFix(f) {
      if (!f.voiced) return;
      const k = [];
      for (let i = this.frames.length - 1; i >= 0 && k.length < 5; i--) if (this.frames[i].voiced && f.t - this.frames[i].t < 0.1) k.push(this.frames[i].midi);
      if (k.length < 3) return;
      const m = median(k), d = f.midi - m;
      if (Math.abs(Math.abs(d) - 12) < 0.8) { f.midi -= Math.sign(d) * 12; f.f0 = midiToHz(f.midi); f.fixed = true; }
    }
    finish() {
      this._frames(true);
      this.tracker.close(this.len / this.sr);
      return this;
    }
  }

  // ----------------------------------------------------------- note features
  function linreg(xs, ys) {
    const n = xs.length; if (n < 3) return null;
    const mx = mean(xs), my = mean(ys);
    let sxy = 0, sxx = 0;
    for (let i = 0; i < n; i++) { sxy += (xs[i] - mx) * (ys[i] - my); sxx += (xs[i] - mx) * (xs[i] - mx); }
    return sxx > 0 ? sxy / sxx : null;
  }
  // attack, decay, sustain from the loudness curve around a note
  function envelopeFeatures(note, env) {
    const dt = env.dt, db = env.db;
    const i0 = Math.max(0, Math.round((note.t0 - 0.06) / dt));
    const iPeakMax = Math.min(db.length - 1, Math.round((note.t0 + 0.35) / dt));
    let ip = i0;
    for (let i = i0; i <= iPeakMax; i++) if (db[i] > db[ip]) ip = i;
    const peak = db[ip];
    let ib = i0; // quietest point before the peak = where this note's rise begins (ring of the last note excluded)
    for (let i = i0; i <= ip; i++) if (db[i] <= db[ib]) ib = i;
    const lower = Math.max(peak - 20, db[ib] + 3);
    let a = ib; while (a < ip && db[a] < lower) a++;
    let b = a; while (b < ip && db[b] < peak - 3) b++;
    const attackMs = (b - a) * dt * 1000;
    const peakPos = clamp((ip * dt - (note.t0 - 0.06)) / Math.max(0.2, note.t1 - note.t0 + 0.06), 0, 1);
    // Level trend after the start, up to the point it has fallen 15 dB (or 0.7 s).
    // Stopping there keeps the bow lifting off at the end of a bowed note from
    // looking like a pluck's decay.
    let decay = null, sustain = null;
    const iEnd = Math.min(db.length - 1, Math.round(note.t1 / dt)), iS = ip + Math.round(0.03 / dt);
    let iE = Math.min(iEnd, ip + Math.round(0.7 / dt));
    for (let i = iS; i <= iE; i++) if (db[i] < peak - 15) { iE = i; break; }
    if ((iE - iS) * dt >= 0.2) {
      const xs = [], ys = [];
      for (let i = iS; i <= iE; i++) { xs.push(i * dt); ys.push(db[i]); }
      const sl = linreg(xs, ys);
      if (sl !== null) decay = sl;
      sustain = db[iE] - peak;
    }
    return { attackMs, peakPos, decay, sustain, peakDb: peak };
  }
  // vibrato: 4-8 Hz oscillation of pitch inside a long note
  function vibratoOf(note, dt) {
    const f = note.usable || [];
    if (f.length * dt < 0.5) return null;
    const c = f.map((x) => x.midi * 100), w = Math.round(0.25 / dt), d = [];
    for (let i = 0; i < c.length; i++) {
      let s = 0, n = 0;
      for (let j = Math.max(0, i - (w >> 1)); j <= Math.min(c.length - 1, i + (w >> 1)); j++) { s += c[j]; n++; }
      d.push(c[i] - s / n);
    }
    let tot = 0; for (const v of d) tot += v * v;
    if (tot / d.length < 1) return { present: false, extent: 0, rate: 0 };
    let bestAmp = 0, bestF = 0;
    for (let fr = 3.5; fr <= 8.5; fr += 0.25) {
      let re = 0, im = 0;
      for (let i = 0; i < d.length; i++) { re += d[i] * Math.cos(2 * Math.PI * fr * i * dt); im += d[i] * Math.sin(2 * Math.PI * fr * i * dt); }
      const amp = (2 * Math.sqrt(re * re + im * im)) / d.length; // cents, zero to peak
      if (amp > bestAmp) { bestAmp = amp; bestF = fr; }
    }
    const share = (bestAmp * bestAmp / 2) / (tot / d.length);
    const extent = bestAmp;
    return { present: extent >= 8 && share >= 0.35, extent, rate: bestF };
  }

  // Mandolin tremolo: repeated plucks make the loudness rise sharply and fall
  // slowly 8-22 times a second. Beating between strings is a plain sine, so
  // requires an asymmetric (sawtooth) shape as well as a periodic one.
  function tremoloOf(note, env) {
    const dt = env.dt, i0 = Math.round((note.t0 + 0.08) / dt), i1 = Math.round((note.t1 - 0.04) / dt);
    if ((i1 - i0) * dt < 0.4) return null;
    const y = env.db.slice(i0, i1), h = Math.round(0.075 / dt), d = [];
    for (let i = 0; i < y.length; i++) { // remove the slow decay with a 0.15 s moving average
      let sum = 0, c = 0;
      for (let j = Math.max(0, i - h); j <= Math.min(y.length - 1, i + h); j++) { sum += y[j]; c++; }
      d.push(y[i] - sum / c);
    }
    let tot = 0; for (const v of d) tot += v * v;
    tot /= d.length;
    if (tot < 0.02) return { present: false };
    let bestAmp = 0, bestF = 0;
    for (let fr = 8; fr <= 22; fr += 0.5) {
      let re = 0, im = 0;
      for (let i = 0; i < d.length; i++) { re += d[i] * Math.cos(2 * Math.PI * fr * i * dt); im += d[i] * Math.sin(2 * Math.PI * fr * i * dt); }
      const amp = (2 * Math.sqrt(re * re + im * im)) / d.length;
      if (amp > bestAmp) { bestAmp = amp; bestF = fr; }
    }
    const ups = [], downs = [];
    for (let i = 1; i < d.length; i++) { const v = d[i] - d[i - 1]; if (v > 0) ups.push(v); else downs.push(-v); }
    const asym = ups.length && downs.length ? percentile(ups, 95) / (percentile(downs, 95) + 1e-9) : 1;
    const share = (bestAmp * bestAmp / 2) / tot;
    return { present: bestAmp >= 0.3 && share >= 0.2 && asym >= 1.3, depth: bestAmp, rate: bestF, asym, share };
  }

  function noteFeatures(notes, env, frameDt) {
    for (const n of notes) {
      const ef = envelopeFeatures(n, env);
      n.attackMs = ef.attackMs; n.peakPos = ef.peakPos; n.decay = ef.decay; n.sustain = ef.sustain; n.peakDb = ef.peakDb;
      n.vibrato = vibratoOf(n, frameDt);
      const uAll = n.usable || [], c0 = Math.floor(uAll.length * 0.25), c1 = Math.max(c0 + 3, Math.ceil(uAll.length * 0.9));
      const u0 = uAll.length >= 8 ? uAll.slice(c0, c1) : uAll; // middle of the note: the changeover from the last note is left out
      if (u0.length >= 3) { // pitch wobble (cents, straight-line trend removed) and how sure the tracker was
        const xs = u0.map((f) => f.t), ys = u0.map((f) => f.midi * 100), sl = linreg(xs, ys) || 0, mx = mean(xs), my = mean(ys);
        let ss = 0; for (let i = 0; i < ys.length; i++) { const r = ys[i] - (my + sl * (xs[i] - mx)); ss += r * r; }
        n.wobble = Math.sqrt(ss / ys.length); n.meanConf = mean(u0.map((f) => f.conf)); n.meanFlat = mean(u0.map((f) => f.flat));
      }
      n.tremolo = tremoloOf(n, env);
      const u = n.usable;
      if (u && u.length >= 8) { // brightness early vs late (high partials die faster on a guitar)
        const k = Math.min(4, u.length >> 1);
        const e = mean(u.slice(0, k).map((x) => x.centroid / x.f0)), l = mean(u.slice(-k).map((x) => x.centroid / x.f0));
        n.brightEarly = e; n.brightLate = l; n.hfLate = mean(u.slice(-k).map((x) => x.hf));
      }
    }
  }

  // --------------------------------------------------------- instruments
  const INSTRUMENTS = {
    violin:  { label: "Violin",  family: "bowed",   hint: "Bowed, G3 to E7" },
    viola:   { label: "Viola",   family: "bowed",   hint: "Bowed, C3 to E6" },
    cello:   { label: "Cello",   family: "bowed",   hint: "Bowed, C2 to A5" },
    guitar:  { label: "Guitar",  family: "plucked", hint: "Plucked, E2 to E6, fast decay" },
    veena:   { label: "Veena / sitar", family: "plucked", hint: "Plucked, long sustain, buzzing bridge, glides (meend)" },
    ukulele: { label: "Ukulele", family: "plucked", hint: "Plucked, C4 and above, very short decay" },
    mandolin:{ label: "Mandolin",family: "plucked", hint: "Plucked, G3 and above, repeated-note tremolo" },
  };
  const INSTRUMENT_IDS = Object.keys(INSTRUMENTS);

  function softmax(obj, temp) {
    const ks = Object.keys(obj), m = Math.max(...ks.map((k) => obj[k]));
    let z = 0; const e = {};
    for (const k of ks) { e[k] = Math.exp((obj[k] - m) / temp); z += e[k]; }
    for (const k of ks) e[k] /= z;
    return e;
  }
  const gaussLL = (x, mu, sd) => -0.5 * Math.pow((x - mu) / sd, 2);

  /*
   * How the families differ (the evidence used):
   *  - Attack: a plucked string reaches full loudness in a few ms; a bow
   *    needs ~30-150 ms to set the string moving.
   *  - Decay: after a pluck the sound dies away on its own; a bowed note stays
   *    level (or swells) as long as the bow moves.
   *  - Vibrato: bowed notes (violin family) carry a 4-8 Hz pitch wobble.
   *  - Glides (meend): the veena/sitar pull the string sideways to glide
   *    continuously between notes while the sound is still ringing.
   *  - Brightness over time: the veena's curved bridge (jivari) keeps the high
   *    harmonics alive as the note rings; on a guitar they fade first.
   *  - Register: violin > viola > cello; guitar reaches lower than a ukulele.
   *  - Tremolo: a mandolin repeats one pitch very rapidly.
   */
  function classifyInstrument(notes, glides, info) {
    info = info || {};
    const evidence = [];
    const used = notes.filter((n) => n.attackMs !== undefined);
    const probsZero = () => { const p = {}; for (const k of INSTRUMENT_IDS) p[k] = 0; return p; };
    if (used.length < 4) {
      return { ready: false, probs: probsZero(), best: null, confidence: 0, family: null, notesUsed: used.length,
        evidence: ["Need at least 4 clear notes to identify the instrument (heard " + used.length + ")."] };
    }
    const attacks = used.map((n) => n.attackMs);
    // notes that are being re-plucked over and over (tremolo) never decay: leave them out
    const decays = used.filter((n) => n.decay !== null && n.decay !== undefined && !(n.tremolo && n.tremolo.present) && !n.tremoloRun).map((n) => n.decay);
    const peakPos = used.filter((n) => n.sustain !== null).map((n) => n.peakPos);
    const attackMed = median(attacks);
    const decayMed = decays.length >= 2 ? median(decays) : null;
    const long = used.filter((n) => n.vibrato);
    const vibFrac = long.length ? long.filter((n) => n.vibrato.present).length / long.length : 0;
    const playing = used.reduce((s, n) => s + (n.t1 - n.t0), 0);
    const glideTime = glides.reduce((s, g) => s + g.dur, 0);
    const glideFrac = playing > 0 ? glideTime / playing : 0;
    const bright = used.filter((n) => n.brightEarly && n.brightLate && n.t1 - n.t0 >= 0.45);
    const brightRatio = bright.length >= 2 ? median(bright.map((n) => n.brightLate / n.brightEarly)) : null;
    const hfLate = bright.length >= 2 ? median(bright.map((n) => n.hfLate)) : null;
    const freqs = used.map((n) => midiToHz(n.midi));
    const fMed = median(freqs), fLow = percentile(freqs, 10), fMin = Math.min(...freqs);

    // tremolo: loudness pulses 8-22 times a second inside long notes
    const trLong = used.filter((n) => n.tremolo);
    const trFrac = trLong.length ? trLong.filter((n) => n.tremolo.present).length / trLong.length : 0;
    const trCount = trLong.filter((n) => n.tremolo.present).length;
    const runs = used.filter((n) => n.tremoloRun).length;
    const tremolo = (trCount >= 2 && trFrac >= 0.25) || runs >= 1 ? 1 : 0;
    const maxRun = 0;

    // ---- bowed vs plucked
    const sAttack = clamp((attackMed - 22) / 18, -2, 2);
    const sDecay = decayMed === null ? 0 : clamp((decayMed + 6) / 6, -2, 2);
    const sPeak = peakPos.length ? clamp((median(peakPos) - 0.15) / 0.2, -1.5, 1.5) : 0;
    const sVib = vibFrac >= 0.3 ? 1.5 : vibFrac > 0 ? 0.5 : (long.length >= 2 ? -0.3 : 0);
    const zBow = 1.6 * sAttack + 0.9 * sDecay + 0.7 * sPeak + sVib;
    const pBow = sigmoid(zBow);
    evidence.push("Attack " + (attackMed < 3 ? "under 3" : attackMed.toFixed(0)) + " ms (" + (attackMed < 25 ? "sharp, like a pluck" : "soft, like a bow") + ")");
    if (decayMed !== null) evidence.push("Level changes " + decayMed.toFixed(1) + " dB/s after the start (" + (decayMed < -4 ? "dies away by itself: plucked" : "stays nearly level: a bow, or a long-ringing string") + ")");
    if (long.length) evidence.push("Vibrato in " + Math.round(vibFrac * 100) + "% of long notes");

    // ---- within bowed: register
    const bowedLL = {
      violin: gaussLL(Math.log2(fMed), Math.log2(520), 0.6) + gaussLL(Math.log2(fLow), Math.log2(330), 0.45),
      viola:  gaussLL(Math.log2(fMed), Math.log2(300), 0.5) + gaussLL(Math.log2(fLow), Math.log2(190), 0.4),
      cello:  gaussLL(Math.log2(fMed), Math.log2(170), 0.55) + gaussLL(Math.log2(fLow), Math.log2(105), 0.4),
    };
    if (fMin < 185) { bowedLL.violin -= 3; evidence.push("A note below G3 (" + fMin.toFixed(0) + " Hz): too low for a violin"); }
    if (fMin < 120) bowedLL.viola -= 3;
    const bowedP = softmax(bowedLL, 1.0);

    // ---- within plucked: sum of log-likelihood terms, one per physical cue.
    // Typical values: guitar loses ~14 dB/s after the pluck, veena/sitar only ~5,
    // ukulele and mandolin ~30; high partials fade on a guitar but persist on a
    // veena's buzzing bridge; lowest playable notes limit the small instruments.
    const sc = { guitar: 0, veena: 0, ukulele: 0, mandolin: 0 };
    const DEC = { guitar: [-14, 7], veena: [-5, 4], ukulele: [-32, 10], mandolin: [-32, 10] };
    const BRI = { guitar: [0.75, 0.15], veena: [1.0, 0.12], ukulele: [0.8, 0.2], mandolin: [0.8, 0.2] };
    const MED = { guitar: [200, 0.9], veena: [230, 0.7], ukulele: [400, 0.5], mandolin: [500, 0.6] };
    for (const k of Object.keys(sc)) {
      if (decayMed !== null) sc[k] += gaussLL(decayMed, DEC[k][0], DEC[k][1]);
      if (brightRatio !== null) sc[k] += gaussLL(brightRatio, BRI[k][0], BRI[k][1]);
      sc[k] += gaussLL(Math.log2(fMed), Math.log2(MED[k][0]), MED[k][1]);
    }
    if (decayMed !== null) evidence.push("Plucked notes lose " + (-decayMed).toFixed(0) + " dB/s (guitar ≈ 14, veena ≈ 5, ukulele / mandolin ≈ 30)");
    if (brightRatio !== null) evidence.push(brightRatio < 0.85 ? "High harmonics fade as notes ring (guitar-like)" : brightRatio > 0.95 ? "High harmonics stay alive as notes ring (buzzing bridge)" : "High harmonics fade slightly as notes ring");
    if (fMin < 245) { sc.ukulele -= 3; }                // lowest ukulele note is C4 (G3 with low-G tuning)
    if (fMin < 185) { sc.mandolin -= 3; }               // lowest mandolin note is G3
    if (fMin < 100) { sc.veena -= 1.0; sc.guitar += 0.8; evidence.push("Notes below 100 Hz"); }
    const meend = clamp(glideFrac * 14, 0, 2.5);
    sc.veena += 1.6 * meend;
    if (glides.length) evidence.push(glides.length + " glide(s) between notes (" + (glideFrac * 100).toFixed(0) + "% of playing time)");
    if (tremolo) { sc.mandolin += 4.5; evidence.push("Rapid repeated plucks on one note (tremolo)"); }
    const pluckedP = softmax(sc, 1.0);

    const probs = {};
    for (const k of ["violin", "viola", "cello"]) probs[k] = pBow * bowedP[k];
    for (const k of ["guitar", "veena", "ukulele", "mandolin"]) probs[k] = (1 - pBow) * pluckedP[k];
    const ranked = INSTRUMENT_IDS.slice().sort((a, b) => probs[b] - probs[a]);
    const best = ranked[0];
    const confidence = probs[best];
    evidence.push("Based on " + used.length + " notes" + (used.length < 8 ? " (play more notes for a firmer answer)" : ""));
    const close = ranked.filter((k, i) => i > 0 && probs[k] >= 0.6 * probs[best] && probs[k] >= 0.15);
    if (close.length) evidence.push("Also possible: " + close.map((k) => INSTRUMENTS[k].label).join(", ") + " (the sound and register overlap)");
    return { ready: true, probs, best, second: ranked[1], alsoPossible: close, confidence, family: pBow >= 0.5 ? "bowed" : "plucked", pBow,
      evidence, notesUsed: used.length, features: { attackMs: attackMed, decay: decayMed, vibFrac, glideFrac, glides: glides.length,
        brightRatio, hfLate, fMed, fLow, tremolo, maxRun } };
  }

  // ----------------------------------------------------------- quality gate
  function fmtT(t) { const s = Math.max(0, Math.round(t)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }

  // Everything that is loud but not an accepted note: sort it into talking / knock / noise.
  function nonKeyEvents(frames, notes, fragments, dt, gateDb, voiceNotes, bursts) {
    const inNote = (t) => { for (const n of notes) if (t >= n.t0 - 0.02 && t <= n.t1 + 0.05) return true; return false; };
    const runs = []; let cur = null;
    for (const f of frames) {
      const act = f.db >= gateDb && !inNote(f.t);
      if (act) {
        if (cur && f.t - cur.last <= 0.15) { cur.frames.push(f); cur.last = f.t; }
        else { cur = { frames: [f], last: f.t }; runs.push(cur); }
      }
    }
    const ev = [];
    for (const r of runs) {
      const dur = r.frames.length * dt;
      if (dur < 0.05) continue;
      const v = r.frames.filter((f) => f.voiced);
      const voicedFrac = v.length / r.frames.length;
      const t = r.frames[0].t;
      if (voicedFrac >= 0.35 && v.length >= 3) {
        const m = median(v.map((f) => f.midi)), fHz = midiToHz(m);
        let moving = 0;
        for (let i = 0; i < v.length; i++) {
          const dev = Math.abs(v[i].midi - m) > 0.7;
          const step = i && Math.abs(v[i].midi - v[i - 1].midi) > 0.4;
          if (dev || step) moving++;
        }
        const wander = moving / v.length;
        if (fHz >= 70 && fHz <= 450 && wander >= 0.3 && dur >= 0.08) { ev.push({ type: "talking", t, dur }); continue; }
        if (wander >= 0.3) { ev.push({ type: "non_key", t, dur }); continue; }
        continue; // steady blip: ignored, it is too short to be a note
      }
      if (dur <= 2.0) ev.push({ type: "non_key", t, dur });
      else ev.push({ type: "noise", t, dur });
    }
    for (const n of voiceNotes || []) ev.push({ type: "talking", t: n.t0, dur: n.t1 - n.t0 });
    for (const b of bursts || []) if (!ev.some((e) => e.type === "non_key" && Math.abs(e.t - b.t) < 0.3)) ev.push({ type: "non_key", t: b.t, dur: 0.1 });
    ev.sort((a, b) => a.t - b.t);
    // merge talking within 1.2 s into one stretch
    const merged = [];
    for (const e of ev) {
      const last = merged[merged.length - 1];
      if (last && last.type === e.type && e.t - (last.t + last.dur) <= 1.2 && e.type === "talking") last.dur = e.t + e.dur - last.t;
      else merged.push(Object.assign({}, e));
    }
    return merged;
  }

  function analyzeQuality(res, opts) {
    opts = opts || {};
    const flags = [];
    const dur = res.duration, notes = res.notes;
    const peakDb = 20 * Math.log10(res.peak + 1e-9);
    if (dur < 3) flags.push({ type: "short", severity: "fail", t: 0, text: "The recording is very short (" + dur.toFixed(1) + " s). Play for at least 10 seconds." });
    if (res.peak < 0.02) flags.push({ type: "quiet", severity: "fail", t: 0, text: "Too quiet (loudest sound " + peakDb.toFixed(0) + " dB). Move the phone or microphone closer to the instrument." });
    if (res.clippedFrac > 0.001) flags.push({ type: "clipping", severity: "fail", t: 0, text: "The sound is distorted (too loud for the microphone). Move back a little." });
    if (opts.noiseDb !== undefined && opts.noiseDb !== null) {
      if (opts.noiseDb > -42) flags.push({ type: "noisy_room", severity: "warn", t: 0, text: "The room is noisy (" + opts.noiseDb.toFixed(0) + " dB). Turn off fans or move somewhere quieter." });
      else if (notes.length >= 3) {
        const snr = median(notes.map((n) => n.peakDb !== undefined ? n.peakDb : -30)) - opts.noiseDb;
        if (snr < 20) flags.push({ type: "low_snr", severity: "warn", t: 0, text: "Background noise is quite loud compared with your playing (" + snr.toFixed(0) + " dB difference)." });
      }
    }
    if (notes.length < 3) flags.push({ type: "no_playing", severity: "fail", t: 0, text: "Hardly any playing was recognised (" + notes.length + " notes). Play closer to the microphone." });
    for (const e of res.events) {
      if (e.type === "talking") flags.push({ type: "talking", severity: "fail", t: e.t, text: "Talking was heard at " + fmtT(e.t) + (e.dur > 1.5 ? "–" + fmtT(e.t + e.dur) : "") + "." });
      else if (e.type === "non_key") flags.push({ type: "non_key", severity: "warn", t: e.t, text: "A sound that isn't a note (a cough, a knock or a clap?) at " + fmtT(e.t) + "." });
      else if (e.type === "noise") flags.push({ type: "noise", severity: "warn", t: e.t, text: "Steady background noise at " + fmtT(e.t) + " for " + e.dur.toFixed(0) + " s." });
    }
    // long pauses between activity (notes and events)
    const marks = notes.map((n) => [n.t0, n.t1]).sort((a, b) => a[0] - b[0]);
    for (let i = 1; i < marks.length; i++) {
      const gap = marks[i][0] - marks[i - 1][1];
      if (gap >= 3) flags.push({ type: "pause", severity: "warn", t: marks[i - 1][1], text: "The playing stopped for " + gap.toFixed(0) + " seconds at " + fmtT(marks[i - 1][1]) + "." });
    }
    flags.sort((a, b) => a.t - b.t);
    const talkTime = res.events.filter((e) => e.type === "talking").reduce((s, e) => s + e.dur, 0);
    const bad = flags.filter((f) => f.severity === "fail").length;
    // "record again" only for problems that make the take unusable (fail);
    // many small events (knocks, short pauses) no longer trigger it
    return { flags, recordAgain: bad > 0, talkTime, peakDb };
  }

  // -------------------------------------------------------------- offline
  function noiseFloorDb(samples, sr) {
    const env = new EnvelopeBuilder(sr); env.update(samples, samples.length);
    return percentile(env.db, 10);
  }
  // Measure room noise from a short stretch of silence.
  function measureRoom(samples, sr) {
    const env = new EnvelopeBuilder(sr); env.update(samples, samples.length);
    if (!env.db.length) return { noiseDb: -90, peakDb: -90 };
    return { noiseDb: median(env.db), peakDb: Math.max(...env.db) };
  }
  function gateFrom(noiseDb) { return clamp(noiseDb + 10, -62, -28); }

  // A voice can look like a note: stable vowel, clear pitch. But a spoken syllable
  // wobbles in pitch (about 20 cents; a string under 8), is short, and the tracker
  // is less sure of it. Score those cues; strings that glide, vibrato or are plucked
  // are protected.
  function voiceScore(n) {
    const dur = n.t1 - n.t0;
    let sc = 0;
    if (n.wobble >= 12) sc += 2; else if (n.wobble >= 8) sc += 1;
    if (n.meanConf < 0.95) sc += 1; else if (n.meanConf < 0.985) sc += 0.5;
    if (dur < 0.4) sc += 1;
    if (dur < 0.25) sc += 0.5;
    if (dur < 0.3 && n.attackMs >= 25) sc += 1; // short AND soft start: a syllable, not a pluck
    if (n.vibrato && n.vibrato.present) sc -= 2;
    if (n.viaGlide) sc -= 2;
    if (n.attackMs < 15 && n.peakPos < 0.2 && n.decay !== null && n.decay < -6) sc -= 1.5; // clear pluck
    return sc;
  }
  function splitVoice(notes) {
    const voice = new Set();
    notes.forEach((n, i) => { if (n.wobble !== undefined && voiceScore(n) >= 3) voice.add(i); });
    // a weaker syllable right next to clear speech is speech too
    notes.forEach((n, i) => {
      if (voice.has(i) || n.wobble === undefined || voiceScore(n) < 2) return;
      for (const j of voice) if (Math.abs(notes[j].t0 - n.t0) < 0.4 || Math.abs(notes[j].t1 - n.t0) < 0.3) { voice.add(i); break; }
    });
    return { notes: notes.filter((n, i) => !voice.has(i)), voice: notes.filter((n, i) => voice.has(i)) };
  }

  // Two clean-ups that need hindsight:
  //  1. A new pluck over a ringing string: for its first moments the tracker
  //     still hears the LAST note, then switches. That short head is the old
  //     note's ring, not a note, so it is joined to the real note after it.
  //  2. Mandolin tremolo: 4+ very short repeats of one pitch are ONE held note.
  function repairNotes(notes) {
    const out = [];
    for (let i = 0; i < notes.length; i++) {
      const n = notes[i], prev = out[out.length - 1], next = notes[i + 1];
      if (n.onset && n.t1 - n.t0 < 0.3 && prev && Math.abs(prev.midi - n.midi) < 0.7 && next && next.split && !next.onset && next.t0 - n.t1 < 0.06) {
        next.t0 = n.t0; next.onset = n.onset; next.headDropped = true; continue;
      }
      out.push(n);
    }
    const res = [];
    for (let i = 0; i < out.length;) {
      let j = i;
      while (j + 1 < out.length && out[j].t1 - out[j].t0 < 0.16 && out[j + 1].t0 - out[j].t0 <= 0.14 && Math.abs(out[j + 1].midi - out[i].midi) < 0.6) j++;
      if (j - i >= 3) { // run of 4+ repeats
        const last = out[j], m = out[i];
        const merged = Object.assign({}, m, { t1: last.t1, usable: [].concat(...out.slice(i, j + 1).map((x) => x.usable)), tremoloRun: j - i + 1 });
        res.push(merged); i = j + 1;
      } else { res.push(out[i]); i++; }
    }
    return res;
  }

  function tuningOffset(notes) {
    const d = notes.filter((n) => n.plateau >= 0.8).map((n) => (n.midi - Math.round(n.midi)) * 100);
    if (d.length < 4) return 0;
    // circular-ish: all deviations are within +-50 by construction
    const m = median(d), mad = median(d.map((x) => Math.abs(x - m)));
    return mad <= 20 ? m : 0;
  }

  function describeNotes(notes, saMidi, offsetCents) {
    return notes.map((n, i) => {
      const adj = n.midi - offsetCents / 100, r = Math.round(adj);
      return {
        id: i, t0: n.t0, t1: n.t1, dur: n.t1 - n.t0, midi: r, centsOff: Math.round((adj - r) * 100),
        name: noteName(r), swara: saMidi != null ? swaraName(r, saMidi) : "", hz: midiToHz(n.midi),
        attackMs: n.attackMs, glide: !!n.viaGlide, strength: n.peakDb,
      };
    });
  }

  /**
   * Careful pass over a whole recording.
   * opts: { noiseDb, saMidi, fmin, fmax }
   */
  function analyzeOffline(samples, sr, opts) {
    opts = opts || {};
    const noiseDb = opts.noiseDb !== undefined && opts.noiseDb !== null ? opts.noiseDb : noiseFloorDb(samples, sr);
    const gateDb = gateFrom(noiseDb);
    const sa = new StreamAnalyzer(sr, { gateDb, fmin: opts.fmin || 50, fmax: opts.fmax || 2500, hop: 0.01 });
    const step = 4096;
    for (let i = 0; i < samples.length; i += step) sa.push(samples.subarray(i, Math.min(samples.length, i + step)));
    sa.finish();
    return finalize(sa, noiseDb, gateDb, opts.saMidi == null ? 60 : opts.saMidi);
  }

  function finalize(sa, noiseDb, gateDb, saMidi) {
    const tr = sa.tracker;
    // drop notes that are clearly not instrument notes (a very short, unplucked, unstable blip)
    let notes = tr.notes.filter((n) => n.t1 - n.t0 >= 0.07);
    notes = repairNotes(notes);
    noteFeatures(notes, sa.env, sa.dt);
    const sv = splitVoice(notes);
    notes = sv.notes;
    const events = nonKeyEvents(sa.frames, notes, tr.fragments, sa.dt, gateDb, sv.voice, sa.bursts);
    const offset = tuningOffset(notes);
    const keys = describeNotes(notes, saMidi, offset);
    const instrument = classifyInstrument(notes, tr.glides, {});
    return {
      sr: sa.sr, duration: sa.duration, peak: sa.peak, clippedFrac: sa.clipped / Math.max(1, sa.len),
      noiseDb, gateDb, frames: sa.frames, notes, keys, events, glides: tr.glides, onsets: sa.onsets,
      tuningOffsetCents: offset, instrument, saMidi, env: sa.env,
    };
  }

  // Wav encoding for downloads / playback
  function encodeWav(samples, sr) {
    const n = samples.length, buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const wr = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    wr(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); wr(8, "WAVE"); wr(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    wr(36, "data"); v.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) v.setInt16(44 + i * 2, Math.round(clamp(samples[i], -1, 1) * 32767), true);
    return buf;
  }

  return {
    midiToHz, hzToMidi, noteName, swaraName, noteNameToMidi, median, percentile, fmtT, STRING_TUNINGS, stringOf,
    yin, fft, spectralFeatures, EnvelopeBuilder, OnsetDetector, StreamAnalyzer,
    noteFeatures, classifyInstrument, INSTRUMENTS, INSTRUMENT_IDS,
    voiceScore, splitVoice, analyzeQuality, analyzeOffline, finalize, describeNotes, measureRoom, gateFrom, tuningOffset, encodeWav,
  };
});
