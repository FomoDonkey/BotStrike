/**
 * Programmatic notification sounds using Web Audio API.
 * No external audio files needed — generates tones on the fly.
 */

let audioCtx: AudioContext | null = null;

function getCtx(): AudioContext {
  if (!audioCtx) {
    audioCtx = new AudioContext();
  }
  if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }
  return audioCtx;
}

function playTone(frequency: number, duration: number, type: OscillatorType = "sine", volume = 0.15) {
  try {
    const ctx = getCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();

    osc.type = type;
    osc.frequency.setValueAtTime(frequency, ctx.currentTime);
    gain.gain.setValueAtTime(volume, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);

    osc.connect(gain);
    gain.connect(ctx.destination);

    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + duration);
  } catch {
    // Audio not available
  }
}

export const sounds = {
  /** Trade executed — short neutral blip */
  trade: () => {
    playTone(880, 0.08, "sine", 0.1);
    setTimeout(() => playTone(1100, 0.08, "sine", 0.08), 80);
  },

  /** Profitable trade — ascending cheerful tone */
  profit: () => {
    playTone(523, 0.1, "sine", 0.12);
    setTimeout(() => playTone(659, 0.1, "sine", 0.12), 100);
    setTimeout(() => playTone(784, 0.15, "sine", 0.1), 200);
  },

  /** Loss — descending somber tone */
  loss: () => {
    playTone(440, 0.15, "triangle", 0.12);
    setTimeout(() => playTone(349, 0.2, "triangle", 0.1), 150);
  },

  /** Alert — attention-grabbing double beep */
  alert: () => {
    playTone(1000, 0.1, "square", 0.08);
    setTimeout(() => playTone(1000, 0.1, "square", 0.08), 200);
    setTimeout(() => playTone(1400, 0.15, "square", 0.06), 400);
  },

  /** Circuit breaker — urgent alarm */
  circuitBreaker: () => {
    for (let i = 0; i < 3; i++) {
      setTimeout(() => playTone(800, 0.12, "sawtooth", 0.1), i * 250);
      setTimeout(() => playTone(600, 0.12, "sawtooth", 0.1), i * 250 + 120);
    }
  },
};
