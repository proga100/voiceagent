// AudioWorklet: capture mic audio as Int16 PCM frames at the context's sample rate.
//
// The capture AudioContext is created at 16 kHz on the main thread, so the
// browser's *high-quality* resampler does the 48k->16k conversion. This worklet
// therefore does NO manual downsampling (the previous naive decimation caused
// aliasing, which made the recognizer drift to neighbouring languages like
// Turkish). It just converts Float32 -> Int16 and emits ~100 ms frames.
//
// MediaRecorder is still avoided — it produces WebM/Opus, not raw PCM.

class PcmFramerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._frameSamples = Math.round(sampleRate * 0.1); // 100 ms at context rate (16k)
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const chan = input[0];
    if (!chan) return true;

    for (let i = 0; i < chan.length; i++) this._buf.push(chan[i]);

    while (this._buf.length >= this._frameSamples) {
      const frame = this._buf.splice(0, this._frameSamples);
      const pcm = new Int16Array(frame.length);
      for (let j = 0; j < frame.length; j++) {
        const s = Math.max(-1, Math.min(1, frame[j]));
        pcm[j] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor('pcm-framer', PcmFramerProcessor);
