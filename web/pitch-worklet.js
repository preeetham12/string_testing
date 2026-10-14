// Runs on the browser's audio thread, so the live pitch never stutters.
//  1. Collects raw samples while recording (sent to the page in batches).
//  2. Every ~23 ms, runs YIN (the same pitch method as the server's Part 1)
//     on the last ~46 ms of sound and sends {f0, rms, conf} to the page.

function yin(buf, sr, fmin, fmax, threshold) {
  const W = buf.length >> 1;
  const maxLag = Math.min(W - 1, Math.floor(sr / fmin));
  const minLag = Math.max(2, Math.floor(sr / fmax));
  const d = new Float32Array(maxLag + 2);
  for (let tau = 1; tau <= maxLag + 1; tau++) {
    let s = 0;
    for (let i = 0; i < W; i++) {
      const x = buf[i] - buf[i + tau];
      s += x * x;
    }
    d[tau] = s;
  }
  // cumulative mean normalised difference
  let running = 0;
  d[0] = 1;
  for (let tau = 1; tau <= maxLag + 1; tau++) {
    running += d[tau];
    d[tau] = running > 0 ? (d[tau] * tau) / running : 1;
  }
  // First dip below the threshold. If none - real bowed strings add enough
  // bow noise that none often is - fall back to the deepest dip overall
  // (the YIN paper's "best local estimate") and let `conf` say how sure.
  let best = -1;
  for (let tau = minLag; tau <= maxLag; tau++) {
    if (d[tau] < threshold) {
      while (tau + 1 <= maxLag && d[tau + 1] < d[tau]) tau++;
      best = tau;
      break;
    }
  }
  if (best < 0) {
    let lo = Infinity;
    for (let tau = minLag; tau <= maxLag; tau++) if (d[tau] < lo) lo = d[tau];
    // The deepest dip is often at TWICE the true period (an octave too low)
    // when there's noise, so take the FIRST dip that is nearly as deep.
    for (let tau = minLag; tau <= maxLag; tau++) {
      if (d[tau] <= lo + 0.1 && d[tau] <= d[tau - 1] && d[tau] <= d[tau + 1]) { best = tau; break; }
    }
  }
  if (best < 0) return { f0: 0, conf: 0 };
  // parabolic interpolation for sub-sample (sub-cent) precision
  const tau = best;
  const a = d[tau - 1], b = d[tau], c = d[tau + 1];
  const den = a - 2 * b + c;
  const shift = den !== 0 ? (a - c) / (2 * den) : 0;
  return { f0: sr / (tau + shift), conf: Math.max(0, 1 - b) };
}

class StringCoachProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.win = 2048;
    this.hop = 1024;
    this.ring = new Float32Array(this.win);
    this.filled = 0;
    this.sinceHop = 0;
    this.recording = false;
    this.batch = [];
    this.batchLen = 0;
    this.fmin = 60;
    this.port.onmessage = (e) => {
      if (e.data.type === "record") {
        this.recording = e.data.on;
        if (!e.data.on) this.flush();
      }
      if (e.data.type === "fmin") this.fmin = e.data.value;
    };
  }

  flush() {
    if (!this.batchLen) return;
    const out = new Float32Array(this.batchLen);
    let o = 0;
    for (const b of this.batch) { out.set(b, o); o += b.length; }
    this.port.postMessage({ type: "samples", data: out }, [out.buffer]);
    this.batch = [];
    this.batchLen = 0;
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    if (this.recording) {
      this.batch.push(ch.slice());
      this.batchLen += ch.length;
      if (this.batchLen >= 8192) this.flush();
    }
    // keep the last `win` samples
    this.ring.copyWithin(0, ch.length);
    this.ring.set(ch, this.win - ch.length);
    this.filled = Math.min(this.win, this.filled + ch.length);
    this.sinceHop += ch.length;
    if (this.filled === this.win && this.sinceHop >= this.hop) {
      this.sinceHop = 0;
      let e = 0;
      for (let i = 0; i < this.win; i++) e += this.ring[i] * this.ring[i];
      const rms = Math.sqrt(e / this.win);
      const r = rms > 0.003 ? yin(this.ring, sampleRate, this.fmin, 2200, 0.15) : { f0: 0, conf: 0 };
      this.port.postMessage({ type: "pitch", f0: r.f0, conf: r.conf, rms, t: currentTime });
    }
    return true;
  }
}

registerProcessor("stringcoach", StringCoachProcessor);
