// Runs on the audio thread: collects microphone samples and hands them to the
// page in 2048-sample batches (the analysis itself runs on the page).
class NueriqCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(2048);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === this.buf.length) {
        const out = this.buf;
        this.port.postMessage(out, [out.buffer]);
        this.buf = new Float32Array(2048);
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("nueriq-capture", NueriqCapture);
