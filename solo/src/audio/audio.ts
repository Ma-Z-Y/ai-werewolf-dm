/**
 * Procedural audio (Web Audio, no asset files): two BGM themes — 白天悬疑 /
 * 夜晚危险 — cross-faded with the day/night mix, ambience (wind, rain,
 * thunder) and one-shot SFX (doors, wolves, rats, bats, dawn stingers).
 * When the game is decided an ending takes over the music: a lament under
 * wolf howls, or a bright dance tune with birdsong. The 颁奖典礼 after it has
 * its own: birdsong under a gentle tune, a fanfare and march with applause and
 * whistles for the best player, a comic oom-pah with boos for the worst.
 */

/**
 * 'hush': the music drops out (the wolves are about to strike).
 * 'ceremony' / 'award' / 'shame': the awards — the speeches, the best player's podium, the worst's.
 */
export type Ending = 'hush' | 'wolf' | 'good' | 'ceremony' | 'award' | 'shame';

type Bus = GainNode;

/** Base mix of the three groups. */
const MIX = { music: 0.55, sfx: 0.9, ambience: 0.6 };

const midi = (n: number) => 440 * Math.pow(2, (n - 69) / 12);

export class AudioEngine {
  private ctx: AudioContext | null = null;
  private master!: GainNode;
  private music!: Bus;
  private dayBus!: Bus;
  private nightBus!: Bus;
  private sfx!: Bus;
  private amb!: Bus;
  private reverb!: ConvolverNode;
  private reverbSend!: GainNode;
  private noise!: AudioBuffer;
  private windGain!: GainNode;
  private windFilter!: BiquadFilterNode;
  private rainGain!: GainNode;
  private schedTimer = 0;
  private nextBeat = { day: 0, night: 0 };
  private beatIndex = { day: 0, night: 0 };
  private melodyIdx = 3;
  private night = 0;
  private muted = false;
  /** User volume per group (配置), 0..1, on top of the mix levels below. */
  private levels = { music: 1, sfx: 1, ambience: 1 };
  private ending: Ending | null = null;
  /** The ending's music; replaced (the old one fades) whenever the ending changes. */
  private endBus: GainNode | null = null;
  private endNext = 0;
  private endStep = 0;
  private nextCall = 0;
  private nextClap = 0;

  get started() {
    return this.ctx !== null;
  }

  /** Must be called from a user gesture (browser autoplay policy). */
  start() {
    if (this.ctx) {
      void this.ctx.resume();
      return;
    }
    const ctx = new AudioContext();
    this.ctx = ctx;
    this.master = ctx.createGain();
    this.master.gain.value = this.muted ? 0 : 0.8;
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -14;
    comp.ratio.value = 4;
    this.master.connect(comp).connect(ctx.destination);

    this.reverb = ctx.createConvolver();
    this.reverb.buffer = this.impulse(3.2, 2.4);
    this.reverbSend = ctx.createGain();
    this.reverbSend.gain.value = 0.9;
    this.reverbSend.connect(this.reverb).connect(this.master);

    this.music = this.bus(MIX.music * this.levels.music);
    this.dayBus = ctx.createGain();
    this.nightBus = ctx.createGain();
    this.dayBus.gain.value = 1;
    this.nightBus.gain.value = 0;
    this.dayBus.connect(this.music);
    this.nightBus.connect(this.music);
    this.sfx = this.bus(MIX.sfx * this.levels.sfx);
    this.amb = this.bus(MIX.ambience * this.levels.ambience);

    const len = ctx.sampleRate * 2;
    this.noise = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = this.noise.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;

    this.startAmbience();
    this.startDrones();
    const now = ctx.currentTime + 0.1;
    this.nextBeat = { day: now, night: now };
    this.schedTimer = window.setInterval(() => this.schedule(), 100);
    const ending = this.ending;
    this.ending = null;
    this.setNight(this.night === 1);
    this.setEnding(ending);
  }

  /** Freeze all sound (the game is paused); scheduled music waits with the clock. */
  pause() {
    void this.ctx?.suspend();
  }

  resume() {
    void this.ctx?.resume();
  }

  setLevels(levels: { music: number; sfx: number; ambience: number }) {
    this.levels = { ...levels };
    if (!this.ctx) return;
    const t = this.ctx.currentTime;
    this.music.gain.setTargetAtTime(MIX.music * levels.music, t, 0.05);
    this.sfx.gain.setTargetAtTime(MIX.sfx * levels.sfx, t, 0.05);
    this.amb.gain.setTargetAtTime(MIX.ambience * levels.ambience, t, 0.05);
  }

  setMuted(m: boolean) {
    this.muted = m;
    if (this.ctx) this.master.gain.setTargetAtTime(m ? 0 : 0.8, this.ctx.currentTime, 0.2);
  }

  /** 0 = day, 1 = night. Cross-fades music themes and ambience. */
  setNight(night: boolean) {
    this.night = night ? 1 : 0;
    if (!this.ctx || this.ending) return;
    const t = this.ctx.currentTime;
    this.dayBus.gain.setTargetAtTime(night ? 0 : 1, t, 1.5);
    this.nightBus.gain.setTargetAtTime(night ? 1 : 0, t, 1.5);
    this.rainGain.gain.setTargetAtTime(night ? 1 : 0, t, 2.5);
    this.windGain.gain.setTargetAtTime(night ? 0.2 : 0.12, t, 2);
  }

  /**
   * The game is over: silence the day/night themes and play the ending's own
   * music and ambience; `null` goes back to the usual themes (title screen).
   */
  setEnding(kind: Ending | null) {
    if (kind === this.ending) return;
    this.ending = kind;
    if (!this.ctx) return;
    const ctx = this.ctx;
    const t = ctx.currentTime;
    if (this.endBus) {
      const old = this.endBus;
      old.gain.setTargetAtTime(0, t, 0.6);
      setTimeout(() => old.disconnect(), 4000);
      this.endBus = null;
    }
    if (!kind) {
      this.setNight(this.night === 1);
      return;
    }
    this.dayBus.gain.setTargetAtTime(0, t, kind === 'hush' ? 0.4 : 0.8);
    this.nightBus.gain.setTargetAtTime(0, t, 0.4);
    // wolves: only their howls are left on the wind; the village: a light breeze
    this.rainGain.gain.setTargetAtTime(0, t, 0.8);
    this.windGain.gain.setTargetAtTime(kind === 'wolf' ? 0 : kind === 'hush' ? 0.12 : 0.04, t, 1);
    if (kind === 'hush') return;
    this.endBus = ctx.createGain();
    this.endBus.gain.setValueAtTime(0.0001, t);
    // the awards' fanfare / trombone come in at once; the rest swell in
    this.endBus.gain.exponentialRampToValueAtTime(1, t + (kind === 'award' || kind === 'shame' ? 0.05 : 1.5));
    this.endBus.connect(this.music);
    this.endNext = t + (kind === 'wolf' ? 1.2 : 0.8);
    this.endStep = 0;
    this.nextCall = t + (kind === 'wolf' ? 0.3 : 1.5);
    this.nextClap = t + 0.3;
    if (kind === 'award') {
      this.fanfare(t + 0.1);
      this.endNext = t + 2.6;
    } else if (kind === 'shame') {
      this.sadTrombone(t + 0.1);
      this.endNext = t + 3.4;
    }
  }

  dispose() {
    clearInterval(this.schedTimer);
    void this.ctx?.close();
    this.ctx = null;
  }

  // ───────────────────────── building blocks ─────────────────────────

  private bus(gain: number): GainNode {
    const g = this.ctx!.createGain();
    g.gain.value = gain;
    g.connect(this.master);
    return g;
  }

  private impulse(seconds: number, decay: number): AudioBuffer {
    const ctx = this.ctx!;
    const len = Math.floor(ctx.sampleRate * seconds);
    const buf = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay);
    }
    return buf;
  }

  private noiseSrc(loop = false): AudioBufferSourceNode {
    const s = this.ctx!.createBufferSource();
    s.buffer = this.noise;
    s.loop = loop;
    if (!loop) s.loopStart = 0;
    return s;
  }

  private filter(type: BiquadFilterType, freq: number, q = 1): BiquadFilterNode {
    const f = this.ctx!.createBiquadFilter();
    f.type = type;
    f.frequency.value = freq;
    f.Q.value = q;
    return f;
  }

  /** Attack/decay envelope on a fresh gain node. */
  private env(t: number, attack: number, peak: number, decay: number): GainNode {
    const g = this.ctx!.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(peak, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + attack + decay);
    return g;
  }

  private out(node: AudioNode, dest: AudioNode, wet = 0) {
    node.connect(dest);
    if (wet > 0) {
      const w = this.ctx!.createGain();
      w.gain.value = wet;
      node.connect(w).connect(this.reverbSend);
    }
  }

  private tone(dest: AudioNode, t: number, freq: number, type: OscillatorType, peak: number, attack: number, decay: number, wet = 0.3) {
    const o = this.ctx!.createOscillator();
    o.type = type;
    o.frequency.setValueAtTime(freq, t);
    const g = this.env(t, attack, peak, decay);
    o.connect(g);
    this.out(g, dest, wet);
    o.start(t);
    o.stop(t + attack + decay + 0.05);
    return o;
  }

  private burst(dest: AudioNode, t: number, type: BiquadFilterType, freq: number, q: number, peak: number, attack: number, decay: number, wet = 0) {
    const s = this.noiseSrc();
    const f = this.filter(type, freq, q);
    const g = this.env(t, attack, peak, decay);
    s.connect(f).connect(g);
    this.out(g, dest, wet);
    s.start(t, Math.random() * 1.5);
    s.stop(t + attack + decay + 0.05);
    return f;
  }

  // ───────────────────────── ambience ─────────────────────────

  private startAmbience() {
    const ctx = this.ctx!;
    // wind: band-passed noise with slow gusts
    const w = this.noiseSrc(true);
    this.windFilter = this.filter('bandpass', 420, 0.7);
    this.windGain = ctx.createGain();
    this.windGain.gain.value = 0.12;
    const gust = ctx.createGain();
    gust.gain.value = 0.6;
    w.connect(this.windFilter).connect(gust).connect(this.windGain).connect(this.amb);
    w.start();
    const gustLoop = () => {
      if (!this.ctx) return;
      const t = this.ctx.currentTime;
      gust.gain.setTargetAtTime(0.35 + Math.random() * 0.9, t, 1.2 + Math.random());
      this.windFilter.frequency.setTargetAtTime(300 + Math.random() * 500, t, 1.5);
      setTimeout(gustLoop, 1500 + Math.random() * 2500);
    };
    gustLoop();

    // rain: individual drops (scheduled in `rainDrops`) over a very soft low wash — no hiss
    const r = this.noiseSrc(true);
    this.rainGain = ctx.createGain();
    this.rainGain.gain.value = 0;
    const wash = this.filter('lowpass', 420, 0.6);
    const wg = ctx.createGain();
    wg.gain.value = 0.25;
    r.connect(wash).connect(wg).connect(this.rainGain);
    this.rainGain.connect(this.amb);
    r.start();
  }

  thunder(distance = 0.5) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime + 0.3 + distance * 1.4;
    const vol = 0.9 - distance * 0.4;
    // crack
    this.burst(this.amb, t, 'lowpass', 1400, 0.7, vol * 0.5, 0.01, 0.35);
    // rumble with a few swells
    const s = this.noiseSrc();
    const f = this.filter('lowpass', 160, 0.9);
    const g = this.ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(vol, t + 0.08);
    g.gain.linearRampToValueAtTime(vol * 0.5, t + 0.7);
    g.gain.linearRampToValueAtTime(vol * 0.8, t + 1.2);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 4.5);
    s.connect(f).connect(g);
    this.out(g, this.amb, 0.4);
    s.start(t);
    s.stop(t + 4.6);
  }

  // ───────────────────────── music ─────────────────────────

  private startDrones() {
    const ctx = this.ctx!;
    const drone = (bus: Bus, freqs: [number, OscillatorType, number][], cutoff: number, lfoRate: number) => {
      const f = this.filter('lowpass', cutoff, 0.8);
      const lfo = ctx.createOscillator();
      lfo.frequency.value = lfoRate;
      const lfoAmt = ctx.createGain();
      lfoAmt.gain.value = cutoff * 0.4;
      lfo.connect(lfoAmt).connect(f.frequency);
      lfo.start();
      const g = ctx.createGain();
      g.gain.value = 1;
      f.connect(g);
      this.out(g, bus, 0.25);
      for (const [hz, type, vol] of freqs) {
        for (const det of [-4, 4]) {
          const o = ctx.createOscillator();
          o.type = type;
          o.frequency.value = hz;
          o.detune.value = det;
          const og = ctx.createGain();
          og.gain.value = vol;
          o.connect(og).connect(f);
          o.start();
        }
      }
    };
    // 白天：D 小调五度持续音
    drone(this.dayBus, [[midi(38), 'sawtooth', 0.025], [midi(45), 'triangle', 0.035], [midi(50), 'sine', 0.02]], 420, 0.05);
    // 夜晚：C / F# 三全音 + 次低音
    drone(this.nightBus, [[midi(36), 'sawtooth', 0.03], [midi(42), 'sawtooth', 0.022], [midi(24), 'sine', 0.09]], 320, 0.08);
    // 夜晚高频颤音弦
    const trem = ctx.createGain();
    trem.gain.value = 0.006;
    const tl = ctx.createOscillator();
    tl.frequency.value = 7;
    const ta = ctx.createGain();
    ta.gain.value = 0.005;
    tl.connect(ta).connect(trem.gain);
    tl.start();
    for (const hz of [midi(90), midi(91)]) {
      const o = ctx.createOscillator();
      o.frequency.value = hz;
      o.connect(trem);
      o.start();
    }
    this.out(trem, this.nightBus, 0.6);
  }

  /** Lookahead scheduler for melodic / rhythmic events of both themes. */
  private nextDrop = 0;

  /** 滴滴答答: sparse, individually pitched drops at night (~6 per second). */
  private rainDrops(horizon: number) {
    const ctx = this.ctx!;
    if (this.night < 1) {
      this.nextDrop = horizon;
      return;
    }
    this.nextDrop = Math.max(this.nextDrop, ctx.currentTime);
    while (this.nextDrop < horizon) {
      const t = this.nextDrop;
      this.nextDrop += -Math.log(1 - Math.random()) / 6; // Poisson, mean 6 drops/s
      const pan = ctx.createStereoPanner();
      pan.pan.value = Math.random() * 1.6 - 0.8;
      pan.connect(this.rainGain);
      const eave = Math.random() < 0.18; // a heavier drip off the eaves: lower, rounder "plop"
      const f = eave ? 500 + Math.random() * 350 : 1500 + Math.random() * 2200;
      const o = ctx.createOscillator();
      o.frequency.setValueAtTime(f, t);
      o.frequency.exponentialRampToValueAtTime(f * (eave ? 1.6 : 0.55), t + (eave ? 0.07 : 0.035));
      const g = this.env(t, 0.002, (eave ? 0.07 : 0.035) * (0.4 + Math.random() * 0.6), eave ? 0.11 : 0.05);
      o.connect(g).connect(pan);
      o.start(t);
      o.stop(t + 0.2);
    }
  }

  private schedule() {
    const ctx = this.ctx;
    if (!ctx) return;
    const horizon = ctx.currentTime + 0.6;
    this.rainDrops(horizon);
    if (this.ending) {
      this.endingMusic(horizon);
      // the themes' clocks keep time, so they pick up cleanly afterwards
      this.nextBeat.day = Math.max(this.nextBeat.day, horizon);
      this.nextBeat.night = Math.max(this.nextBeat.night, horizon);
      return;
    }
    // day theme: 66 bpm, sparse music box over pizzicato pulse
    const dayBeat = 60 / 66;
    while (this.nextBeat.day < horizon) {
      const t = this.nextBeat.day;
      const i = this.beatIndex.day++;
      if (this.night < 1) this.dayEvent(t, i);
      this.nextBeat.day += dayBeat;
    }
    // night theme: 56 bpm heartbeat + stingers + bell
    const nightBeat = 60 / 56;
    while (this.nextBeat.night < horizon) {
      const t = this.nextBeat.night;
      const i = this.beatIndex.night++;
      if (this.night > 0) this.nightEvent(t, i);
      this.nextBeat.night += nightBeat;
    }
  }

  private dayEvent(t: number, i: number) {
    const bus = this.dayBus;
    // low pizzicato on 1 and 3 of 4
    if (i % 4 === 0) this.tone(bus, t, midi(38), 'sine', 0.16, 0.005, 0.7, 0.2);
    if (i % 4 === 2) this.tone(bus, t, midi(33), 'sine', 0.12, 0.005, 0.7, 0.2);
    // music box: D harmonic minor, random walk, often silent
    const scale = [62, 65, 67, 69, 70, 73, 74, 77, 79, 81];
    if (Math.random() < 0.42) {
      this.melodyIdx = Math.max(0, Math.min(scale.length - 1, this.melodyIdx + [-2, -1, -1, 1, 1, 2][Math.floor(Math.random() * 6)]));
      const n = scale[this.melodyIdx] + 12;
      this.tone(bus, t, midi(n), 'triangle', 0.045, 0.004, 1.6, 0.55);
      this.tone(bus, t, midi(n + 12), 'sine', 0.012, 0.004, 0.8, 0.55);
      if (Math.random() < 0.25) this.tone(bus, t + 60 / 66 / 2, midi(scale[Math.max(0, this.melodyIdx - 2)] + 12), 'triangle', 0.03, 0.004, 1.2, 0.55);
    }
    // an unresolved swell every 16 beats
    if (i % 16 === 8) this.burst(bus, t, 'bandpass', 900, 2, 0.05, 2.2, 2.5, 0.5);
  }

  private nightEvent(t: number, i: number) {
    const bus = this.nightBus;
    // heartbeat: lub-dub
    const thump = (at: number, vol: number) => {
      const o = this.ctx!.createOscillator();
      o.frequency.setValueAtTime(62, at);
      o.frequency.exponentialRampToValueAtTime(38, at + 0.16);
      const g = this.env(at, 0.008, vol, 0.2);
      o.connect(g);
      this.out(g, bus, 0.1);
      o.start(at);
      o.stop(at + 0.3);
    };
    thump(t, 0.35);
    thump(t + 0.24, 0.22);
    // dissonant string stinger
    if (i % 16 === 12 && Math.random() < 0.7) {
      for (const n of [60, 61, 66]) this.tone(bus, t, midi(n), 'sawtooth', 0.018, 0.9, 2.8, 0.7);
    }
    // distant bell toll
    if (i % 12 === 0) {
      for (const [ratio, vol] of [[1, 0.06], [2.76, 0.025], [5.4, 0.012]] as const) this.tone(bus, t, midi(48) * ratio, 'sine', vol, 0.01, 4.5, 0.8);
    }
    // creeping low glissando
    if (i % 24 === 18) {
      const o = this.ctx!.createOscillator();
      o.type = 'sawtooth';
      o.frequency.setValueAtTime(midi(47), t);
      o.frequency.linearRampToValueAtTime(midi(42), t + 4);
      const f = this.filter('lowpass', 700, 1);
      const g = this.env(t, 1.5, 0.03, 3);
      o.connect(f).connect(g);
      this.out(g, bus, 0.6);
      o.start(t);
      o.stop(t + 5);
    }
  }

  // ───────────────────────── endings ─────────────────────────

  private endingMusic(horizon: number) {
    const ctx = this.ctx!;
    if (this.ending === 'wolf') {
      // 悲歌: a lament in D minor, one step per beat at 58 bpm
      while (this.endNext < horizon) {
        this.lament(this.endNext, this.endStep++);
        this.endNext += 60 / 58;
      }
      // howls from every side, now near, now far
      while (this.nextCall < horizon) {
        const far = Math.random();
        this.howl(1 - far * 0.6, this.nextCall - ctx.currentTime, 0.88 + Math.random() * 0.3);
        if (Math.random() < 0.35) this.howl(0.5 - far * 0.2, this.nextCall - ctx.currentTime + 0.8 + Math.random(), 1.1 + Math.random() * 0.15);
        this.nextCall += 4 + Math.random() * 5;
      }
    } else if (this.ending === 'good') {
      // a bright C-major dance at 132 bpm, one step per eighth note
      while (this.endNext < horizon) {
        this.jig(this.endNext, this.endStep++);
        this.endNext += 60 / 132 / 2;
      }
      while (this.nextCall < horizon) {
        this.bird(this.nextCall);
        this.nextCall += 0.8 + Math.random() * 2.5;
      }
    } else if (this.ending === 'ceremony') {
      // a gentle pastoral at 84 bpm under the birds, quiet enough to read by
      while (this.endNext < horizon) {
        this.pastoral(this.endNext, this.endStep++);
        this.endNext += 60 / 84 / 2;
      }
      while (this.nextCall < horizon) {
        this.bird(this.nextCall);
        if (Math.random() < 0.3) this.bird(this.nextCall + 0.2 + Math.random() * 0.4);
        this.nextCall += 0.6 + Math.random() * 1.8;
      }
    } else if (this.ending === 'award') {
      while (this.endNext < horizon) {
        this.march(this.endNext, this.endStep++);
        this.endNext += 60 / 116 / 2;
      }
      // the crowd claps and whistles
      while (this.nextClap < horizon) {
        this.clap(this.nextClap);
        this.nextClap += 0.02 + Math.random() * 0.05;
      }
      while (this.nextCall < horizon) {
        this.whistle(this.nextCall);
        this.nextCall += 1.2 + Math.random() * 2.8;
      }
    } else if (this.ending === 'shame') {
      while (this.endNext < horizon) {
        this.oomPah(this.endNext, this.endStep++);
        this.endNext += 60 / 100 / 2;
      }
      while (this.nextCall < horizon) {
        this.boo(this.nextCall);
        this.nextCall += 1.5 + Math.random() * 3;
      }
      // scattered mocking claps
      while (this.nextClap < horizon) {
        if (Math.random() < 0.5) this.clap(this.nextClap, 0.6);
        this.nextClap += 0.12 + Math.random() * 0.3;
      }
    }
  }

  /** One eighth of the ceremony's pastoral: soft arpeggios over a warm pad (32-step loop). */
  private pastoral(t: number, i: number) {
    const bus = this.endBus!;
    const step = i % 32;
    // F | C | Dm | B♭
    const chords = [
      [53, 65, 69, 72],
      [48, 64, 67, 72],
      [50, 65, 69, 74],
      [46, 65, 70, 74],
    ];
    const [root, ...up] = chords[Math.floor(step / 8)];
    const beat = 60 / 84;
    if (step % 8 === 0) {
      this.tone(bus, t, midi(root - 12), 'sine', 0.08, 0.3, beat * 4 + 0.5, 0.4);
      for (const n of up) this.tone(bus, t, midi(n - 12), 'triangle', 0.012, 0.8, beat * 4, 0.6);
    }
    const arp = [0, 1, 2, 1, 2, 0, 1, 2];
    const n = up[arp[step % 8]] + (step % 16 >= 8 && step % 8 === 4 ? 12 : 0);
    this.tone(bus, t, midi(n), 'sine', 0.035, 0.01, 0.6, 0.5);
  }

  /** A brass note: bright detuned saws through a swelling low-pass. */
  private brass(bus: AudioNode, t: number, n: number, dur: number, vol: number, bend = 0) {
    const ctx = this.ctx!;
    const f = this.filter('lowpass', 900, 1.2);
    f.frequency.setValueAtTime(700, t);
    f.frequency.linearRampToValueAtTime(2600, t + 0.06);
    f.frequency.setTargetAtTime(1500, t + 0.08, 0.2);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(vol, t + 0.03);
    g.gain.setValueAtTime(vol * 0.8, t + Math.max(0.05, dur - 0.05));
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur + 0.15);
    f.connect(g);
    this.out(g, bus, 0.35);
    for (const det of [-6, 5]) {
      const o = ctx.createOscillator();
      o.type = 'sawtooth';
      o.frequency.setValueAtTime(midi(n), t);
      if (bend) o.frequency.linearRampToValueAtTime(midi(n + bend), t + dur);
      o.detune.value = det;
      o.connect(f);
      o.start(t);
      o.stop(t + dur + 0.2);
    }
  }

  /** 颁奖: ta-ta-ta-taaa! */
  private fanfare(t: number) {
    const bus = this.endBus!;
    const tr = 0.14;
    const line: [number, number, number][] = [
      [0, 60, tr], [tr, 64, tr], [tr * 2, 67, tr], [tr * 3, 72, 0.5],
      [tr * 3 + 0.55, 67, tr], [tr * 4 + 0.55, 72, 1.3],
    ];
    for (const [at, n, d] of line) {
      this.brass(bus, t + at, n, d, 0.07);
      this.brass(bus, t + at, n - 12, d, 0.04);
    }
    const hold = t + tr * 4 + 0.55;
    for (const n of [48, 55, 64, 67]) this.brass(bus, hold, n, 1.4, 0.03);
    // cymbal crash
    this.burst(bus, hold, 'highpass', 5000, 0.5, 0.12, 0.002, 1.6, 0.4);
  }

  /** One eighth of the award march in C: bass, snare and a brass tune (64-step loop). */
  private march(t: number, i: number) {
    const bus = this.endBus!;
    const step = i % 64;
    const bar = Math.floor(step / 8);
    const pos = step % 8;
    // C | G | C | F | C | G | F G | C
    const roots = [48, 43, 48, 41, 48, 43, pos < 4 ? 41 : 43, 48];
    const root = roots[bar];
    if (pos === 0 || pos === 4) this.tone(bus, t, midi(root - 12 + (pos === 4 ? 7 : 0)), 'triangle', 0.15, 0.005, 0.3, 0.1);
    if (pos === 2 || pos === 6) {
      this.burst(bus, t, 'bandpass', 1800, 0.8, 0.09, 0.001, 0.12, 0.3); // snare
      const triad = { 48: [64, 67, 72], 43: [62, 67, 71], 41: [65, 69, 72] }[root]!;
      for (const n of triad) this.tone(bus, t, midi(n - 12), 'square', 0.01, 0.004, 0.12, 0.2);
    }
    if (pos === 7) this.burst(bus, t, 'bandpass', 2200, 0.8, 0.04, 0.001, 0.06, 0.2);
    const tune = [
      67, 0, 72, 0, 72, 74, 76, 0,
      74, 0, 71, 0, 67, 0, 0, 0,
      72, 0, 76, 0, 79, 0, 76, 72,
      77, 0, 0, 0, 76, 0, 74, 0,
      72, 0, 76, 0, 79, 0, 84, 0,
      83, 0, 79, 0, 74, 0, 0, 0,
      77, 0, 76, 0, 74, 0, 71, 0,
      72, 0, 67, 0, 72, 0, 0, 0,
    ];
    const n = tune[step];
    if (n) this.brass(bus, t, n, 0.24, 0.045);
  }

  /** 最差: wah, wah, wah, waaaah. */
  private sadTrombone(t: number) {
    const bus = this.endBus!;
    const notes: [number, number, number][] = [[0, 55, 0.5], [0.6, 54, 0.5], [1.2, 53, 0.5], [1.8, 52, 1.4]];
    for (const [at, n, d] of notes) this.brass(bus, t + at, n - 12, d, 0.09, at === 1.8 ? -0.5 : 0);
  }

  /** One eighth of a clumsy oom-pah polka for the losers (32-step loop). */
  private oomPah(t: number, i: number) {
    const bus = this.endBus!;
    const step = i % 32;
    const bar = Math.floor(step / 4);
    const pos = step % 4;
    const roots = [45, 40, 45, 40, 41, 40, 45, 45];
    if (pos === 0) this.brass(bus, t, roots[bar] - 12, 0.22, 0.06);
    if (pos === 2) this.tone(bus, t, midi(roots[bar] + 7), 'square', 0.02, 0.004, 0.1, 0.1);
    const tune = [69, 0, 72, 71, 69, 0, 68, 0, 69, 0, 64, 0, 0, 0, 0, 0, 65, 0, 69, 68, 65, 0, 64, 0, 69, 0, 57, 0, 0, 0, 0, 0];
    const n = tune[step];
    if (n) this.tone(bus, t, midi(n), 'triangle', 0.05, 0.005, 0.2, 0.25);
  }

  /** One hand clap somewhere in the crowd. */
  private clap(t: number, vol = 1) {
    const ctx = this.ctx!;
    const pan = ctx.createStereoPanner();
    pan.pan.value = Math.random() * 1.8 - 0.9;
    pan.connect(this.sfx);
    this.burst(pan, t, 'bandpass', 900 + Math.random() * 1400, 1.1, (0.03 + Math.random() * 0.05) * vol, 0.001, 0.03 + Math.random() * 0.04, 0.25);
  }

  /** A two-finger whistle from the crowd: a rising shriek, or the up-down wolf whistle. */
  private whistle(t: number) {
    const ctx = this.ctx!;
    const pan = ctx.createStereoPanner();
    pan.pan.value = Math.random() * 1.6 - 0.8;
    pan.connect(this.sfx);
    const base = 1900 + Math.random() * 700;
    const o = ctx.createOscillator();
    const wolf = Math.random() < 0.5;
    const dur = wolf ? 0.95 : 0.6;
    o.frequency.setValueAtTime(base, t);
    if (wolf) {
      o.frequency.exponentialRampToValueAtTime(base * 1.6, t + 0.22);
      o.frequency.setValueAtTime(base * 1.1, t + 0.4);
      o.frequency.exponentialRampToValueAtTime(base * 1.55, t + 0.55);
      o.frequency.exponentialRampToValueAtTime(base * 0.8, t + dur);
    } else {
      o.frequency.exponentialRampToValueAtTime(base * 1.5, t + 0.12);
      o.frequency.setValueAtTime(base * 1.5, t + 0.45);
      o.frequency.exponentialRampToValueAtTime(base * 1.35, t + dur);
    }
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.05, t + 0.03);
    if (wolf) {
      g.gain.setValueAtTime(0.05, t + 0.28);
      g.gain.linearRampToValueAtTime(0.004, t + 0.36);
      g.gain.linearRampToValueAtTime(0.05, t + 0.44);
    }
    g.gain.setValueAtTime(0.05, t + dur - 0.08);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g);
    this.out(g, pan, 0.3);
    o.start(t);
    o.stop(t + dur + 0.05);
    // the breath in it
    this.burst(pan, t, 'bandpass', base * 1.4, 4, 0.012, 0.02, dur, 0.2);
  }

  /** A few voices booing ("boooo"), falling in pitch. */
  private boo(t: number) {
    const ctx = this.ctx!;
    const dur = 1.1 + Math.random() * 0.5;
    const f1 = this.filter('bandpass', 420, 3);
    const f2 = this.filter('lowpass', 900, 0.7);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(0.06, t + 0.15);
    g.gain.setValueAtTime(0.05, t + dur - 0.3);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    f1.connect(f2).connect(g);
    this.out(g, this.sfx, 0.4);
    for (let v = 0; v < 3; v++) {
      const o = ctx.createOscillator();
      o.type = 'sawtooth';
      const f0 = 150 + Math.random() * 80;
      o.frequency.setValueAtTime(f0, t);
      o.frequency.linearRampToValueAtTime(f0 * 0.75, t + dur);
      o.connect(f1);
      o.start(t + v * 0.05);
      o.stop(t + dur + 0.05);
    }
  }

  /** One beat of the lament: string pad per bar, bass, and a violin line (32-beat loop). */
  private lament(t: number, i: number) {
    const bus = this.endBus!;
    const beat = 60 / 58;
    const step = i % 32;
    // Dm | B♭ | Gm | A | Dm | Gm | A | Dm
    const chords = [
      [50, 57, 62, 65],
      [46, 53, 58, 62],
      [43, 55, 58, 62],
      [45, 52, 57, 61],
      [50, 57, 62, 65],
      [43, 55, 58, 62],
      [45, 52, 57, 61],
      [38, 50, 57, 62],
    ];
    if (step % 4 === 0) {
      const [root, ...upper] = chords[step / 4];
      const bar = beat * 4;
      this.tone(bus, t, midi(root - 12), 'sine', 0.13, 0.6, bar + 1, 0.3);
      for (const n of upper) this.strings(bus, t, n, bar, 0.022);
    }
    // violin melody: [beat in loop, midi, beats]
    const line: [number, number, number][] = [
      [0, 69, 2], [2, 65, 1], [3, 64, 1],
      [4, 62, 3], [7, 65, 1],
      [8, 67, 2], [10, 70, 1], [11, 69, 1],
      [12, 64, 2], [14, 61, 1], [15, 64, 1],
      [16, 65, 2], [18, 69, 2],
      [20, 70, 2], [22, 67, 1], [23, 70, 1],
      [24, 69, 2], [26, 73, 1], [27, 76, 1],
      [28, 74, 4],
    ];
    // every other time round the tune drops an octave, like a cello answering
    const low = Math.floor(i / 32) % 2 === 1 ? -12 : 0;
    for (const [at, n, len] of line) if (at === step) this.violin(bus, t, n + low, len * beat, low ? 0.06 : 0.045);
  }

  /** A bowed string: detuned saws through a soft low-pass, slow swell. */
  private strings(bus: AudioNode, t: number, n: number, dur: number, vol: number) {
    const ctx = this.ctx!;
    const f = this.filter('lowpass', 1100, 0.7);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(vol, t + 1.1);
    g.gain.setValueAtTime(vol, t + dur - 0.3);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur + 1.2);
    f.connect(g);
    this.out(g, bus, 0.7);
    for (const det of [-7, 6]) {
      const o = ctx.createOscillator();
      o.type = 'sawtooth';
      o.frequency.value = midi(n);
      o.detune.value = det;
      o.connect(f);
      o.start(t);
      o.stop(t + dur + 1.3);
    }
  }

  /** A solo violin note with delayed vibrato. */
  private violin(bus: AudioNode, t: number, n: number, dur: number, vol: number) {
    const ctx = this.ctx!;
    const o = ctx.createOscillator();
    o.type = 'sawtooth';
    o.frequency.value = midi(n);
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 5.4;
    const depth = ctx.createGain();
    depth.gain.setValueAtTime(0, t);
    depth.gain.linearRampToValueAtTime(14, t + Math.min(0.8, dur * 0.6));
    lfo.connect(depth).connect(o.detune);
    const body = this.filter('lowpass', 2400, 0.8);
    const formant = this.filter('peaking', 1300, 1.5);
    formant.gain.value = 6;
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t);
    g.gain.linearRampToValueAtTime(vol, t + 0.25);
    g.gain.setValueAtTime(vol, t + Math.max(0.3, dur - 0.15));
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur + 0.6);
    o.connect(body).connect(formant).connect(g);
    this.out(g, bus, 0.8);
    o.start(t);
    lfo.start(t);
    o.stop(t + dur + 0.7);
    lfo.stop(t + dur + 0.7);
  }

  /** One eighth of the dance tune: oom-pah bass, chord stabs, shaker and a whistle-bright melody (64-step loop). */
  private jig(t: number, i: number) {
    const bus = this.endBus!;
    const step = i % 64;
    const bar = Math.floor(step / 8);
    const pos = step % 8;
    // C | F | G | C | C | Am | F G | C
    const roots = [48, 53, 55, 48, 48, 45, pos < 4 ? 53 : 55, 48];
    const triads: Record<number, number[]> = { 48: [64, 67, 72], 53: [65, 69, 72], 55: [62, 67, 71], 45: [64, 69, 72] };
    const root = roots[bar];
    if (pos === 0 || pos === 4) {
      this.tone(bus, t, midi(root - 12 + (pos === 4 ? 7 : 0)), 'triangle', 0.16, 0.005, 0.28, 0.1);
      // a soft kick under the bass
      const k = this.ctx!.createOscillator();
      k.frequency.setValueAtTime(120, t);
      k.frequency.exponentialRampToValueAtTime(45, t + 0.1);
      const kg = this.env(t, 0.003, 0.25, 0.14);
      k.connect(kg);
      this.out(kg, bus, 0);
      k.start(t);
      k.stop(t + 0.2);
    }
    if (pos === 2 || pos === 6) for (const n of triads[root]) this.tone(bus, t, midi(n - 12), 'square', 0.012, 0.004, 0.14, 0.15);
    if (pos % 2 === 1) this.burst(bus, t, 'highpass', 7000, 0.7, 0.035, 0.002, 0.04);
    if (pos === 4) this.burst(bus, t, 'bandpass', 1600, 1.2, 0.07, 0.002, 0.09, 0.3);
    const tune = [
      72, 0, 76, 0, 79, 0, 76, 0,
      77, 0, 76, 0, 74, 0, 72, 0,
      74, 0, 71, 0, 74, 76, 77, 0,
      76, 0, 72, 0, 72, 0, 0, 0,
      79, 0, 79, 81, 79, 0, 76, 0,
      76, 0, 72, 0, 69, 0, 72, 0,
      77, 0, 76, 0, 74, 0, 71, 0,
      72, 0, 67, 0, 72, 0, 84, 0,
    ];
    const n = tune[step];
    if (n) {
      this.tone(bus, t, midi(n), 'triangle', 0.07, 0.006, 0.34, 0.35);
      this.tone(bus, t, midi(n + 12), 'sine', 0.018, 0.006, 0.2, 0.35);
    }
  }

  /** A songbird: a few quick rising and falling chirps. */
  private bird(t: number) {
    const ctx = this.ctx!;
    const pan = ctx.createStereoPanner();
    pan.pan.value = Math.random() * 1.6 - 0.8;
    pan.connect(this.amb);
    const base = 2600 + Math.random() * 1600;
    const notes = 2 + Math.floor(Math.random() * 4);
    for (let k = 0; k < notes; k++) {
      const at = t + k * (0.09 + Math.random() * 0.05);
      const o = ctx.createOscillator();
      const up = Math.random() < 0.6;
      o.frequency.setValueAtTime(base * (up ? 0.8 : 1.25), at);
      o.frequency.exponentialRampToValueAtTime(base * (up ? 1.3 : 0.85), at + 0.07);
      const g = this.env(at, 0.004, 0.05, 0.07);
      o.connect(g);
      this.out(g, pan, 0.3);
      o.start(at);
      o.stop(at + 0.12);
    }
  }

  // ───────────────────────── SFX ─────────────────────────

  private now(delay = 0) {
    return this.ctx ? this.ctx.currentTime + delay : -1;
  }

  doorOpen(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    // latch click
    this.burst(this.sfx, t, 'highpass', 2500, 1, 0.3 * vol, 0.002, 0.04);
    // hinge creak: wobbling sawtooth through resonant band-passes
    const o = this.ctx!.createOscillator();
    o.type = 'sawtooth';
    const curve = new Float32Array(24).map((_, k) => 70 + Math.sin(k * 1.7) * 18 + k * 2.2 + Math.random() * 12);
    o.frequency.setValueCurveAtTime(curve, t + 0.05, 0.75);
    const b1 = this.filter('bandpass', 950, 7);
    const b2 = this.filter('bandpass', 2100, 5);
    const g = this.env(t + 0.05, 0.08, 0.55 * vol, 0.7);
    o.connect(b1).connect(g);
    o.connect(b2).connect(g);
    this.out(g, this.sfx, 0.25);
    o.start(t + 0.05);
    o.stop(t + 0.95);
  }

  doorClose(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const o = this.ctx!.createOscillator();
    o.frequency.setValueAtTime(95, t);
    o.frequency.exponentialRampToValueAtTime(45, t + 0.22);
    const g = this.env(t, 0.004, 0.9 * vol, 0.3);
    o.connect(g);
    this.out(g, this.sfx, 0.3);
    o.start(t);
    o.stop(t + 0.4);
    this.burst(this.sfx, t, 'lowpass', 500, 0.8, 0.6 * vol, 0.002, 0.14);
    this.burst(this.sfx, t + 0.06, 'highpass', 3000, 1, 0.22 * vol, 0.002, 0.04);
  }

  doorBreak(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    this.burst(this.sfx, t, 'lowpass', 1600, 0.7, 0.6 * vol, 0.003, 0.5, 0.3);
    for (let k = 0; k < 6; k++) this.burst(this.sfx, t + Math.random() * 0.35, 'bandpass', 2000 + Math.random() * 2500, 3, 0.25 * vol, 0.001, 0.04);
    const o = this.ctx!.createOscillator();
    o.frequency.setValueAtTime(70, t);
    o.frequency.exponentialRampToValueAtTime(35, t + 0.4);
    const g = this.env(t, 0.005, 0.5 * vol, 0.5);
    o.connect(g);
    this.out(g, this.sfx, 0.3);
    o.start(t);
    o.stop(t + 0.6);
  }

  /** A wolf's self-destruct: `big` is the house going up (boom, long rumble, falling debris). */
  explosion(vol = 1, big = true, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const ctx = this.ctx!;
    // the crack
    this.burst(this.sfx, t, 'lowpass', big ? 2600 : 1800, 0.6, (big ? 0.95 : 0.6) * vol, 0.002, big ? 0.6 : 0.35, 0.4);
    // body: a pitch-dropping thump
    const o = ctx.createOscillator();
    o.type = 'sine';
    o.frequency.setValueAtTime(big ? 90 : 120, t);
    o.frequency.exponentialRampToValueAtTime(big ? 28 : 45, t + (big ? 1.2 : 0.5));
    const g = this.env(t, 0.005, (big ? 1 : 0.6) * vol, big ? 1.6 : 0.6);
    o.connect(g);
    this.out(g, this.sfx, 0.4);
    o.start(t);
    o.stop(t + 2);
    if (!big) return;
    // long rolling rumble
    const s = this.noiseSrc(true);
    const f = this.filter('lowpass', 220, 0.8);
    const rg = ctx.createGain();
    rg.gain.setValueAtTime(0.0001, t);
    rg.gain.linearRampToValueAtTime(0.9 * vol, t + 0.1);
    rg.gain.linearRampToValueAtTime(0.5 * vol, t + 1.5);
    rg.gain.exponentialRampToValueAtTime(0.0001, t + 6);
    f.frequency.setValueAtTime(420, t);
    f.frequency.exponentialRampToValueAtTime(90, t + 5);
    s.connect(f).connect(rg);
    this.out(rg, this.sfx, 0.5);
    s.start(t);
    s.stop(t + 6.1);
    // debris raining down
    for (let k = 0; k < 14; k++) {
      this.burst(this.sfx, t + 0.9 + Math.random() * 2.2, 'bandpass', 900 + Math.random() * 2600, 2.5, (0.12 + Math.random() * 0.16) * vol, 0.002, 0.05 + Math.random() * 0.08);
    }
  }

  /** Wolves throwing themselves at a door: a heavy wooden thud and a creak of strained planks. */
  doorBang(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const o = this.ctx!.createOscillator();
    o.frequency.setValueAtTime(110, t);
    o.frequency.exponentialRampToValueAtTime(40, t + 0.25);
    const g = this.env(t, 0.003, 1.1 * vol, 0.35);
    o.connect(g);
    this.out(g, this.sfx, 0.35);
    o.start(t);
    o.stop(t + 0.45);
    this.burst(this.sfx, t, 'lowpass', 900, 0.9, 0.8 * vol, 0.002, 0.18, 0.3);
    this.burst(this.sfx, t + 0.02, 'bandpass', 2400, 4, 0.18 * vol, 0.002, 0.12);
  }

  /** A low werewolf snarl. */
  growl(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const ctx = this.ctx!;
    const o = ctx.createOscillator();
    o.type = 'sawtooth';
    o.frequency.setValueAtTime(70, t);
    o.frequency.linearRampToValueAtTime(95, t + 0.4);
    o.frequency.linearRampToValueAtTime(60, t + 1.1);
    // rough amplitude flutter
    const am = ctx.createGain();
    am.gain.value = 0.5;
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 23;
    const la = ctx.createGain();
    la.gain.value = 0.5;
    lfo.connect(la).connect(am.gain);
    const f = this.filter('lowpass', 650, 2);
    const env = this.env(t, 0.08, 0.5 * vol, 1.1);
    o.connect(am).connect(f).connect(env);
    this.out(env, this.sfx, 0.4);
    o.start(t);
    o.stop(t + 1.3);
    lfo.start(t);
    lfo.stop(t + 1.3);
    this.burst(this.sfx, t, 'bandpass', 380, 1.5, 0.25 * vol, 0.1, 0.9);
  }

  /** The victim's scream, cut short. */
  scream(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const ctx = this.ctx!;
    const mix = ctx.createGain();
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 9;
    const la = ctx.createGain();
    la.gain.value = 40;
    lfo.connect(la);
    for (const det of [0, 14, -11]) {
      const o = ctx.createOscillator();
      o.type = 'sawtooth';
      o.frequency.setValueAtTime(520, t);
      o.frequency.linearRampToValueAtTime(1350, t + 0.18);
      o.frequency.linearRampToValueAtTime(1200, t + 0.9);
      o.frequency.exponentialRampToValueAtTime(380, t + 1.25);
      o.detune.value = det * 10;
      la.connect(o.frequency);
      o.connect(mix);
      o.start(t);
      o.stop(t + 1.3);
    }
    lfo.start(t);
    lfo.stop(t + 1.3);
    const env = ctx.createGain();
    env.gain.setValueAtTime(0.0001, t);
    env.gain.linearRampToValueAtTime(0.3 * vol, t + 0.05);
    env.gain.setValueAtTime(0.28 * vol, t + 1.0);
    env.gain.exponentialRampToValueAtTime(0.0001, t + 1.25);
    // vowel formants ("aah")
    mix.connect(this.filter('bandpass', 1000, 4)).connect(env);
    mix.connect(this.filter('bandpass', 2700, 6)).connect(env);
    mix.connect(this.filter('bandpass', 3600, 8)).connect(env);
    this.out(env, this.sfx, 0.8);
    this.burst(this.sfx, t, 'highpass', 3000, 0.6, 0.06 * vol, 0.03, 1.1);
    // the cut-off: a thump and a wet tearing hiss
    this.burst(this.sfx, t + 1.2, 'lowpass', 500, 1, 0.5 * vol, 0.004, 0.3, 0.4);
    this.burst(this.sfx, t + 1.22, 'bandpass', 1800, 1.2, 0.18 * vol, 0.01, 0.35);
  }

  /** 金钟罩: a struck temple bell (inharmonic partials, long shimmer). */
  bell(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    for (const [ratio, amp, decay] of [[1, 0.5, 3.2], [2.76, 0.3, 2.2], [5.4, 0.18, 1.4], [8.93, 0.1, 0.8], [0.5, 0.25, 3.6]] as const) {
      this.tone(this.sfx, t, 330 * ratio, 'sine', amp * 0.5 * vol, 0.004, decay, 0.9);
    }
    this.burst(this.sfx, t, 'highpass', 4000, 0.7, 0.2 * vol, 0.001, 0.08, 0.5);
  }

  /** 守卫: a rising shimmer as the bell comes down (it rings on landing: `bell`). */
  guardCast(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const s = this.noiseSrc();
    const f = this.filter('bandpass', 1200, 6);
    f.frequency.setValueAtTime(900, t);
    f.frequency.exponentialRampToValueAtTime(5200, t + 1.2);
    const g = this.env(t, 0.9, 0.22 * vol, 0.5);
    s.connect(f).connect(g);
    this.out(g, this.sfx, 0.7);
    s.start(t);
    s.stop(t + 1.5);
    [69, 73, 76, 81].forEach((n, k) => this.tone(this.sfx, t + k * 0.22, midi(n), 'sine', 0.05 * vol, 0.01, 1.2, 0.8));
  }

  /** 查验: a run of glockenspiel twinkles. */
  sparkle(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const notes = [88, 91, 95, 93, 96, 100, 98, 103];
    notes.forEach((n, k) => {
      const at = t + k * 0.13 + Math.random() * 0.04;
      this.tone(this.sfx, at, midi(n), 'sine', 0.05 * vol, 0.003, 0.7, 0.8);
      this.tone(this.sfx, at, midi(n) * 2.76, 'sine', 0.012 * vol, 0.003, 0.3, 0.8);
    });
    this.burst(this.sfx, t, 'highpass', 6000, 0.7, 0.05 * vol, 0.6, 1.2, 0.6);
  }

  /** The verdict: a bright open chord (good), or a low dissonant sting (wolf). */
  reveal(wolf: boolean, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const chord = wolf ? [36, 42, 43, 48] : [60, 67, 72, 76, 79];
    for (const n of chord) {
      this.tone(this.sfx, t, midi(n), wolf ? 'sawtooth' : 'triangle', wolf ? 0.05 : 0.06, 0.02, wolf ? 2.4 : 2.8, 0.8);
    }
    if (wolf) this.burst(this.sfx, t, 'lowpass', 300, 1, 0.5, 0.01, 1.2, 0.4);
    else this.tone(this.sfx, t, midi(96), 'sine', 0.04, 0.005, 1.6, 0.9);
  }

  /** 银水: a glass vial smashes, then the poison hisses and bubbles. */
  poison(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    for (let k = 0; k < 8; k++) {
      const at = t + Math.random() * 0.12;
      this.tone(this.sfx, at, 2500 + Math.random() * 3500, 'sine', 0.05 * vol, 0.001, 0.12 + Math.random() * 0.2, 0.4);
    }
    this.burst(this.sfx, t, 'highpass', 3500, 0.8, 0.3 * vol, 0.002, 0.15);
    // hiss (longer than the noise buffer: loop it)
    const hiss = this.noiseSrc(true);
    const hg = this.env(t + 0.2, 0.4, 0.14 * vol, 3.2);
    hiss.connect(this.filter('highpass', 2800, 0.6)).connect(hg);
    this.out(hg, this.sfx, 0.3);
    hiss.start(t + 0.2);
    hiss.stop(t + 3.9);
    // bubbles: little upward blips
    for (let k = 0; k < 26; k++) {
      const at = t + 0.3 + Math.random() * 3.5;
      const o = this.ctx!.createOscillator();
      const f0 = 250 + Math.random() * 500;
      o.frequency.setValueAtTime(f0, at);
      o.frequency.exponentialRampToValueAtTime(f0 * 2.4, at + 0.06);
      const g = this.env(at, 0.004, (0.06 + Math.random() * 0.06) * vol, 0.07);
      o.connect(g);
      this.out(g, this.sfx, 0.2);
      o.start(at);
      o.stop(at + 0.12);
    }
  }

  /** The poisoned victim moans (two long "ohh…"s and a choking cough). */
  groan(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const ctx = this.ctx!;
    const moan = (at: number, f0: number, dur: number) => {
      const o = ctx.createOscillator();
      o.type = 'sawtooth';
      o.frequency.setValueAtTime(f0, at);
      o.frequency.linearRampToValueAtTime(f0 * 1.12, at + dur * 0.25);
      o.frequency.exponentialRampToValueAtTime(f0 * 0.7, at + dur);
      const lfo = ctx.createOscillator();
      lfo.frequency.value = 5;
      const la = ctx.createGain();
      la.gain.value = f0 * 0.03;
      lfo.connect(la).connect(o.frequency);
      const env = ctx.createGain();
      env.gain.setValueAtTime(0.0001, at);
      env.gain.linearRampToValueAtTime(0.35 * vol, at + 0.25);
      env.gain.setValueAtTime(0.3 * vol, at + dur * 0.7);
      env.gain.exponentialRampToValueAtTime(0.0001, at + dur);
      // "oh" formants, muffled by the walls
      const mix = ctx.createGain();
      o.connect(this.filter('bandpass', 480, 5)).connect(mix);
      o.connect(this.filter('bandpass', 850, 6)).connect(mix);
      const muffle = this.filter('lowpass', 1400, 0.7);
      mix.connect(muffle).connect(env);
      this.out(env, this.sfx, 0.5);
      o.start(at);
      lfo.start(at);
      o.stop(at + dur + 0.05);
      lfo.stop(at + dur + 0.05);
    };
    moan(t, 150, 1.5);
    moan(t + 1.8, 130, 2);
    for (let k = 0; k < 3; k++) this.burst(this.sfx, t + 4 + k * 0.28, 'bandpass', 700, 1.5, 0.3 * vol, 0.01, 0.16, 0.3);
  }

  /** 金水: a choir sings a short hymn (I – IV – V – I), with a bell over the last chord. */
  hymn(delay = 0) {
    const t0 = this.now(delay);
    if (t0 < 0) return;
    const ctx = this.ctx!;
    const chords = [
      [48, 55, 64, 67],
      [53, 57, 65, 69],
      [55, 59, 62, 67],
      [48, 55, 64, 72],
    ];
    const len = 1.5;
    const voice = (at: number, n: number, dur: number) => {
      const env = ctx.createGain();
      env.gain.setValueAtTime(0.0001, at);
      env.gain.linearRampToValueAtTime(0.07, at + 0.35);
      env.gain.setValueAtTime(0.07, at + dur - 0.2);
      env.gain.exponentialRampToValueAtTime(0.0001, at + dur + 0.4);
      const mix = ctx.createGain();
      // "aah" formants
      mix.connect(this.filter('bandpass', 800, 4)).connect(env);
      mix.connect(this.filter('bandpass', 1150, 5)).connect(env);
      mix.connect(this.filter('bandpass', 2900, 8)).connect(env);
      for (const det of [-9, 0, 8]) {
        const o = ctx.createOscillator();
        o.type = 'sawtooth';
        o.frequency.value = midi(n);
        o.detune.value = det;
        const lfo = ctx.createOscillator();
        lfo.frequency.value = 5 + Math.random();
        const la = ctx.createGain();
        la.gain.value = 5;
        lfo.connect(la).connect(o.detune);
        o.connect(mix);
        o.start(at);
        lfo.start(at);
        o.stop(at + dur + 0.5);
        lfo.stop(at + dur + 0.5);
      }
      this.out(env, this.sfx, 1);
    };
    chords.forEach((c, k) => {
      const dur = k === chords.length - 1 ? len * 1.8 : len;
      for (const n of c) voice(t0 + k * len, n, dur);
    });
    const end = t0 + (chords.length - 1) * len;
    for (const [ratio, amp] of [[1, 0.08], [2.76, 0.03], [5.4, 0.015]] as const) this.tone(this.sfx, end, midi(84) * ratio, 'sine', amp, 0.005, 3, 0.9);
  }

  /** The crosshair locks on: two quick electronic ticks. */
  lock() {
    const t = this.now();
    if (t < 0) return;
    this.tone(this.sfx, t, 1760, 'square', 0.04, 0.002, 0.05, 0);
    this.tone(this.sfx, t + 0.08, 2350, 'square', 0.04, 0.002, 0.08, 0);
  }

  /** A shotgun blast echoing round the town. */
  gunshot(delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    this.burst(this.sfx, t, 'lowpass', 4000, 0.5, 1.2, 0.001, 0.25, 0.8);
    this.burst(this.sfx, t, 'highpass', 2000, 0.7, 0.5, 0.001, 0.06);
    const o = this.ctx!.createOscillator();
    o.frequency.setValueAtTime(150, t);
    o.frequency.exponentialRampToValueAtTime(40, t + 0.3);
    const g = this.env(t, 0.002, 1, 0.4);
    o.connect(g);
    this.out(g, this.sfx, 0.6);
    o.start(t);
    o.stop(t + 0.5);
    // echoes off the houses
    for (const [d, v] of [[0.28, 0.25], [0.55, 0.12], [0.9, 0.06]] as const) this.burst(this.sfx, t + d, 'lowpass', 1200, 0.6, v, 0.005, 0.3, 0.5);
  }

  /** Someone takes the shot: a short cry and a body hitting the floor. */
  hit(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const ctx = this.ctx!;
    const o = ctx.createOscillator();
    o.type = 'sawtooth';
    o.frequency.setValueAtTime(260, t);
    o.frequency.linearRampToValueAtTime(330, t + 0.06);
    o.frequency.exponentialRampToValueAtTime(140, t + 0.4);
    const env = this.env(t, 0.01, 0.35 * vol, 0.4);
    const mix = ctx.createGain();
    o.connect(this.filter('bandpass', 700, 4)).connect(mix);
    o.connect(this.filter('bandpass', 1200, 5)).connect(mix);
    mix.connect(env);
    this.out(env, this.sfx, 0.5);
    o.start(t);
    o.stop(t + 0.5);
    const b = t + 0.45;
    const th = ctx.createOscillator();
    th.frequency.setValueAtTime(90, b);
    th.frequency.exponentialRampToValueAtTime(40, b + 0.2);
    const g = this.env(b, 0.004, 0.7 * vol, 0.3);
    th.connect(g);
    this.out(g, this.sfx, 0.3);
    th.start(b);
    th.stop(b + 0.4);
    this.burst(this.sfx, b, 'lowpass', 600, 0.8, 0.4 * vol, 0.003, 0.2);
  }

  seal(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    // two paper slaps + a stamp
    this.burst(this.sfx, t, 'bandpass', 3200, 1.5, 0.25 * vol, 0.002, 0.07);
    this.burst(this.sfx, t + 0.35, 'bandpass', 2800, 1.5, 0.25 * vol, 0.002, 0.07);
    const o = this.ctx!.createOscillator();
    o.frequency.setValueAtTime(160, t + 0.8);
    o.frequency.exponentialRampToValueAtTime(70, t + 0.9);
    const g = this.env(t + 0.8, 0.003, 0.35 * vol, 0.18);
    o.connect(g);
    this.out(g, this.sfx, 0.3);
    o.start(t + 0.8);
    o.stop(t + 1.05);
  }

  howl(vol = 1, delay = 0, pitch = 1) {
    const t = this.now(delay);
    if (t < 0) return;
    const ctx = this.ctx!;
    const base = ctx.createGain();
    const env = this.env(t, 0.5, 0.18 * vol, 2.6);
    const f1 = this.filter('bandpass', 850 * pitch, 2.5);
    const f2 = this.filter('bandpass', 2300 * pitch, 4);
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 5.5;
    const la = ctx.createGain();
    la.gain.value = 9 * pitch;
    lfo.connect(la);
    for (const [type, mul, v] of [['sawtooth', 1, 0.6], ['sine', 1, 1], ['sine', 2, 0.25]] as const) {
      const o = ctx.createOscillator();
      o.type = type;
      const fr = o.frequency;
      fr.setValueAtTime(260 * pitch * mul, t);
      fr.linearRampToValueAtTime(540 * pitch * mul, t + 0.7);
      fr.linearRampToValueAtTime(500 * pitch * mul, t + 2.0);
      fr.linearRampToValueAtTime(330 * pitch * mul, t + 3.0);
      la.connect(o.frequency);
      const og = ctx.createGain();
      og.gain.value = v;
      o.connect(og).connect(base);
      o.start(t);
      o.stop(t + 3.3);
    }
    lfo.start(t);
    lfo.stop(t + 3.3);
    base.connect(f1).connect(env);
    base.connect(f2).connect(env);
    this.out(env, this.sfx, 0.9);
  }

  squeak(vol = 1) {
    const t = this.now();
    if (t < 0) return;
    const n = 2 + Math.floor(Math.random() * 2);
    for (let k = 0; k < n; k++) {
      const at = t + k * (0.09 + Math.random() * 0.04);
      const o = this.ctx!.createOscillator();
      o.frequency.setValueAtTime(3000 + Math.random() * 600, at);
      o.frequency.linearRampToValueAtTime(4300 + Math.random() * 500, at + 0.05);
      const g = this.env(at, 0.004, 0.05 * vol, 0.06);
      o.connect(g);
      this.out(g, this.sfx, 0.1);
      o.start(at);
      o.stop(at + 0.1);
    }
  }

  batFlap(vol = 1) {
    const t = this.now();
    if (t < 0) return;
    const flaps = 6 + Math.floor(Math.random() * 4);
    for (let k = 0; k < flaps; k++) this.burst(this.sfx, t + k * 0.075, 'bandpass', 500 + Math.random() * 300, 1.2, 0.14 * vol, 0.01, 0.05);
    // faint high chirp
    this.tone(this.sfx, t + 0.1, 7000 + Math.random() * 1500, 'sine', 0.012 * vol, 0.003, 0.04, 0);
  }

  caw(vol = 1) {
    const t = this.now();
    if (t < 0) return;
    for (let k = 0; k < 2; k++) {
      const at = t + k * 0.38;
      const o = this.ctx!.createOscillator();
      o.type = 'sawtooth';
      o.frequency.setValueAtTime(720, at);
      o.frequency.linearRampToValueAtTime(480, at + 0.22);
      const f = this.filter('bandpass', 1300, 2.5);
      const g = this.env(at, 0.01, 0.07 * vol, 0.24);
      o.connect(f).connect(g);
      this.out(g, this.sfx, 0.5);
      o.start(at);
      o.stop(at + 0.3);
    }
  }

  /** 平安夜: a short warm major arpeggio with soft bells. */
  peaceful() {
    const t = this.now(0.2);
    if (t < 0) return;
    [74, 78, 81, 86, 90].forEach((n, k) => {
      this.tone(this.sfx, t + k * 0.16, midi(n), 'sine', 0.09, 0.01, 1.8, 0.6);
      this.tone(this.sfx, t + k * 0.16, midi(n) * 2.01, 'sine', 0.02, 0.01, 0.8, 0.6);
    });
    for (const n of [62, 66, 69]) this.tone(this.sfx, t, midi(n), 'triangle', 0.035, 0.4, 2.4, 0.7);
  }

  /** Someone died in the night: a scream falling into a low boom. */
  death() {
    const t = this.now(0.2);
    if (t < 0) return;
    const ctx = this.ctx!;
    const env = this.env(t, 0.06, 0.16, 1.3);
    const f1 = this.filter('bandpass', 950, 5);
    const f2 = this.filter('bandpass', 2600, 6);
    const mix = ctx.createGain();
    const lfo = ctx.createOscillator();
    lfo.frequency.value = 7;
    const la = ctx.createGain();
    la.gain.value = 25;
    lfo.connect(la);
    for (const det of [0, 9, -7]) {
      const o = ctx.createOscillator();
      o.type = 'sawtooth';
      o.frequency.setValueAtTime(680, t);
      o.frequency.linearRampToValueAtTime(1150, t + 0.25);
      o.frequency.linearRampToValueAtTime(980, t + 0.8);
      o.frequency.exponentialRampToValueAtTime(420, t + 1.35);
      o.detune.value = det * 10;
      la.connect(o.frequency);
      o.connect(mix);
      o.start(t);
      o.stop(t + 1.45);
    }
    lfo.start(t);
    lfo.stop(t + 1.45);
    mix.connect(f1).connect(env);
    mix.connect(f2).connect(env);
    this.out(env, this.sfx, 0.7);
    this.burst(this.sfx, t, 'highpass', 2500, 0.5, 0.05, 0.05, 1.1);
    // boom + dissonant chord
    const b = t + 1.1;
    const o = ctx.createOscillator();
    o.frequency.setValueAtTime(70, b);
    o.frequency.exponentialRampToValueAtTime(30, b + 1.5);
    const g = this.env(b, 0.01, 0.6, 2.2);
    o.connect(g);
    this.out(g, this.sfx, 0.5);
    o.start(b);
    o.stop(b + 2.4);
    for (const n of [48, 49, 54]) this.tone(this.sfx, b, midi(n), 'sawtooth', 0.025, 0.05, 2.6, 0.8);
  }

  /** 天亮: a rooster crowing (ko-ke-ko-KOOO), two birds far apart. */
  rooster(delay = 0) {
    const t0 = this.now(delay);
    if (t0 < 0) return;
    const crow = (t: number, pitch: number, vol: number) => {
      const ctx = this.ctx!;
      // syllables: [start, dur, f0 start, f0 peak, f0 end]
      const syl: [number, number, number, number, number][] = [
        [0, 0.16, 520, 620, 560],
        [0.2, 0.14, 600, 700, 640],
        [0.38, 0.16, 640, 760, 700],
        [0.6, 0.95, 700, 880, 520],
      ];
      for (const [st, dur, a, b, c] of syl) {
        const at = t + st;
        const o = ctx.createOscillator();
        o.type = 'sawtooth';
        o.frequency.setValueAtTime(a * pitch, at);
        o.frequency.linearRampToValueAtTime(b * pitch, at + dur * 0.3);
        o.frequency.linearRampToValueAtTime(c * pitch, at + dur);
        const lfo = ctx.createOscillator();
        lfo.frequency.value = 28;
        const la = ctx.createGain();
        la.gain.value = 18 * pitch;
        lfo.connect(la).connect(o.frequency);
        const f1 = this.filter('bandpass', 1100 * pitch, 3);
        const f2 = this.filter('bandpass', 2600 * pitch, 5);
        const g = ctx.createGain();
        g.gain.setValueAtTime(0.0001, at);
        g.gain.linearRampToValueAtTime(0.16 * vol, at + 0.03);
        g.gain.setValueAtTime(0.16 * vol, at + dur * 0.7);
        g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
        o.connect(f1).connect(g);
        o.connect(f2).connect(g);
        this.out(g, this.sfx, 0.5);
        o.start(at);
        lfo.start(at);
        o.stop(at + dur + 0.05);
        lfo.stop(at + dur + 0.05);
      }
    };
    crow(t0, 1, 1);
    crow(t0 + 1.9, 0.92, 0.45);
  }

  /** 颁奖台 rising / landing: a low thump with a little dust. */
  thud(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    const o = this.ctx!.createOscillator();
    o.frequency.setValueAtTime(90, t);
    o.frequency.exponentialRampToValueAtTime(38, t + 0.35);
    const g = this.env(t, 0.005, 0.45 * vol, 0.45);
    o.connect(g);
    this.out(g, this.sfx, 0.3);
    o.start(t);
    o.stop(t + 0.5);
    this.burst(this.sfx, t, 'lowpass', 600, 0.7, 0.12 * vol, 0.005, 0.4, 0.2);
  }

  /** 💩 lands: a wet splat and a plop. */
  splat(vol = 1, delay = 0) {
    const t = this.now(delay);
    if (t < 0) return;
    this.burst(this.sfx, t, 'lowpass', 500 + Math.random() * 300, 1.5, 0.22 * vol, 0.002, 0.13, 0.15);
    this.burst(this.sfx, t, 'bandpass', 1400, 2, 0.05 * vol, 0.001, 0.05, 0);
    const o = this.ctx!.createOscillator();
    o.frequency.setValueAtTime(260 + Math.random() * 80, t);
    o.frequency.exponentialRampToValueAtTime(70, t + 0.12);
    const g = this.env(t, 0.002, 0.12 * vol, 0.12);
    o.connect(g);
    this.out(g, this.sfx, 0.1);
    o.start(t);
    o.stop(t + 0.16);
  }

  /** Short UI click for the human's own turn prompts. */
  chime() {
    const t = this.now();
    if (t < 0) return;
    this.tone(this.sfx, t, midi(81), 'sine', 0.05, 0.005, 0.5, 0.4);
    this.tone(this.sfx, t + 0.09, midi(86), 'sine', 0.04, 0.005, 0.6, 0.4);
  }
}

export const audio = new AudioEngine();
