// Capture worklet: no rate conversion. The AudioContext runs at 16 kHz, so a 100 ms block is 1600 samples.
// Emits int16 blocks plus RMS / peak in dBFS for the meter.
class Cap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(1600); this.n = 0; }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === 1600) this.flush();
    }
    return true;
  }
  flush() {
    const pcm = new Int16Array(1600);
    let s = 0, p = 0;
    for (let i = 0; i < 1600; i++) {
      const v = Math.max(-1, Math.min(1, this.buf[i]));
      pcm[i] = Math.round(v * 32767);
      s += v * v; p = Math.max(p, Math.abs(v));
    }
    this.n = 0;
    this.port.postMessage({ pcm: pcm.buffer, rms: 10 * Math.log10(s / 1600 + 1e-12), peak: 20 * Math.log10(p + 1e-9) }, [pcm.buffer]);
  }
}
registerProcessor("cap", Cap);
