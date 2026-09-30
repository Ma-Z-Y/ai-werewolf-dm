import * as THREE from 'three';
import { Rng } from '../game/rng';
import type { HouseRefs } from './town';

/**
 * A wolf's self-destruct (自爆): a blast where the wolf stood, then the house
 * goes up in a fireball and a mushroom cloud, and its ruin keeps burning and
 * smoking for the rest of the game.
 */

const Kind = { Fire: 0, Smoke: 1, Dust: 2, Ember: 3, Blood: 4, Toxic: 5, Drip: 6, Bubble: 7, Star: 8, Mote: 9 } as const;
type Kind = (typeof Kind)[keyof typeof Kind];

interface Spawn {
  kind: Kind;
  pos: THREE.Vector3;
  vel?: THREE.Vector3;
  life: number;
  size: [number, number];
  alpha?: number;
  drag?: number;
  /** Upward acceleration (hot air). */
  lift?: number;
  gravity?: number;
  /** How strongly the wind carries it. */
  wind?: number;
  /** Smoke only: seconds it still glows with the fire under it. */
  heat?: number;
  /** Position is written by its owner each frame instead of integrated. */
  manual?: boolean;
}

const FIRE_HOT = new THREE.Color(2.2, 1.6, 0.75);
const FIRE_MID = new THREE.Color(1.7, 0.55, 0.1);
const FIRE_END = new THREE.Color(0.45, 0.07, 0.02);
const SMOKE = new THREE.Color(0.042, 0.039, 0.036);
const SMOKE_HOT = new THREE.Color(0.9, 0.28, 0.05);
const DUST = new THREE.Color(0.06, 0.05, 0.04);
const BLOOD = new THREE.Color(0.2, 0.003, 0.002);
const TOXIC = new THREE.Color(0.16, 0.42, 0.05);
const TOXIC_DARK = new THREE.Color(0.03, 0.07, 0.02);
const DRIP = new THREE.Color(0.25, 0.75, 0.08);

/** A continuous source (poison fumes, sparkles, holy motes) that runs for a while. */
interface Emitter {
  left: number;
  rate: number;
  acc: number;
  spawn: () => void;
}

const vert = `
  attribute float size;
  attribute vec4 tint;
  uniform float scale;
  varying vec4 vTint;
  void main(){
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = size * scale / max(-mv.z, 0.1);
    vTint = tint;
  }`;
// chunky pixel puffs, to sit with the pixel-art sprites
const frag = (grid: number) => `
  varying vec4 vTint;
  void main(){
    vec2 q = (floor(gl_PointCoord * ${grid}.0) + 0.5) / ${grid}.0 * 2.0 - 1.0;
    float r = dot(q, q);
    if (r > 1.0) discard;
    float a = 1.0 - r;
    gl_FragColor = vec4(vTint.rgb, vTint.a * a * a);
  }`;
// a four-pointed pixel twinkle (✦) on a 9×9 grid
const starFrag = `
  varying vec4 vTint;
  void main(){
    vec2 q = floor(gl_PointCoord * 9.0) - 4.0;
    vec2 aq = abs(q);
    float a = 0.0;
    if (aq.x == 0.0 || aq.y == 0.0) a = 1.0 - (aq.x + aq.y) / 5.0;
    else if (aq.x == 1.0 && aq.y == 1.0) a = 0.45;
    if (a <= 0.0) discard;
    gl_FragColor = vec4(vTint.rgb, vTint.a * a);
  }`;

/** A fixed-size pool of point particles simulated on the CPU. */
class Layer {
  readonly points: THREE.Points;
  private readonly cap: number;
  private pos: Float32Array;
  private vel: Float32Array;
  private age: Float32Array;
  private life: Float32Array;
  private s0: Float32Array;
  private s1: Float32Array;
  private a0: Float32Array;
  private drag: Float32Array;
  private lift: Float32Array;
  private grav: Float32Array;
  private windK: Float32Array;
  private heat: Float32Array;
  private kind: Uint8Array;
  private live: Uint8Array;
  private manual: Uint8Array;
  private size: Float32Array;
  private tint: Float32Array;
  private cursor = 0;
  /** Particles alive after the last update (skip the upload while the layer is idle). */
  private active = 0;
  readonly uniforms = { scale: { value: 600 } };

  constructor(cap: number, additive: boolean, order: number, star = false) {
    this.cap = cap;
    this.pos = new Float32Array(cap * 3);
    this.vel = new Float32Array(cap * 3);
    this.age = new Float32Array(cap);
    this.life = new Float32Array(cap);
    this.s0 = new Float32Array(cap);
    this.s1 = new Float32Array(cap);
    this.a0 = new Float32Array(cap);
    this.drag = new Float32Array(cap);
    this.lift = new Float32Array(cap);
    this.grav = new Float32Array(cap);
    this.windK = new Float32Array(cap);
    this.heat = new Float32Array(cap);
    this.kind = new Uint8Array(cap);
    this.live = new Uint8Array(cap);
    this.manual = new Uint8Array(cap);
    this.size = new Float32Array(cap);
    this.tint = new Float32Array(cap * 4);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.pos, 3).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('size', new THREE.BufferAttribute(this.size, 1).setUsage(THREE.DynamicDrawUsage));
    g.setAttribute('tint', new THREE.BufferAttribute(this.tint, 4).setUsage(THREE.DynamicDrawUsage));
    this.points = new THREE.Points(
      g,
      new THREE.ShaderMaterial({
        uniforms: this.uniforms,
        vertexShader: vert,
        fragmentShader: star ? starFrag : frag(additive ? 6 : 8),
        transparent: true,
        depthWrite: false,
        blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending,
      }),
    );
    this.points.frustumCulled = false;
    this.points.renderOrder = order;
  }

  spawn(o: Spawn): number {
    for (let n = 0; n < this.cap; n++) {
      const i = (this.cursor + n) % this.cap;
      if (this.live[i]) continue;
      this.cursor = (i + 1) % this.cap;
      this.live[i] = 1;
      this.active++;
      this.manual[i] = o.manual ? 1 : 0;
      this.kind[i] = o.kind;
      this.pos.set([o.pos.x, o.pos.y, o.pos.z], i * 3);
      this.vel.set(o.vel ? [o.vel.x, o.vel.y, o.vel.z] : [0, 0, 0], i * 3);
      this.age[i] = 0;
      this.life[i] = o.life;
      this.s0[i] = o.size[0];
      this.s1[i] = o.size[1];
      this.a0[i] = o.alpha ?? 1;
      this.drag[i] = o.drag ?? 0;
      this.lift[i] = o.lift ?? 0;
      this.grav[i] = o.gravity ?? 0;
      this.windK[i] = o.wind ?? 0;
      this.heat[i] = o.heat ?? 0;
      return i;
    }
    return -1;
  }

  alive(i: number) {
    return i >= 0 && this.live[i] === 1;
  }

  place(i: number, x: number, y: number, z: number) {
    if (!this.alive(i)) return;
    this.pos[i * 3] = x;
    this.pos[i * 3 + 1] = y;
    this.pos[i * 3 + 2] = z;
  }

  clear() {
    this.live.fill(0);
    this.active = 1; // one more pass to blank the buffers
  }

  update(dt: number, t: number, wind: THREE.Vector3) {
    if (!this.active) return;
    this.points.visible = true;
    let active = 0;
    const c = new THREE.Color();
    for (let i = 0; i < this.cap; i++) {
      if (!this.live[i]) {
        this.tint[i * 4 + 3] = 0;
        this.size[i] = 0;
        continue;
      }
      const age = (this.age[i] += dt);
      const life = this.life[i];
      if (age >= life) {
        this.live[i] = 0;
        this.tint[i * 4 + 3] = 0;
        this.size[i] = 0;
        continue;
      }
      active++;
      const o = i * 3;
      if (!this.manual[i]) {
        const damp = Math.exp(-this.drag[i] * dt);
        const w = this.windK[i];
        this.vel[o] = this.vel[o] * damp + wind.x * w * dt;
        this.vel[o + 1] = this.vel[o + 1] * damp + (this.lift[i] - this.grav[i]) * dt;
        this.vel[o + 2] = this.vel[o + 2] * damp + wind.z * w * dt;
        this.pos[o] += this.vel[o] * dt;
        this.pos[o + 1] += this.vel[o + 1] * dt;
        this.pos[o + 2] += this.vel[o + 2] * dt;
        if (this.pos[o + 1] < 0.05) {
          this.pos[o + 1] = 0.05;
          this.vel[o + 1] = Math.abs(this.vel[o + 1]) * 0.2;
        }
      }
      const k = age / life;
      let alpha = this.a0[i];
      switch (this.kind[i]) {
        case Kind.Fire:
          if (k < 0.25) c.copy(FIRE_HOT).lerp(FIRE_MID, k / 0.25);
          else c.copy(FIRE_MID).lerp(FIRE_END, (k - 0.25) / 0.75);
          alpha *= Math.min(1, age / 0.04) * Math.pow(1 - k, 1.3);
          break;
        case Kind.Smoke: {
          const hk = this.heat[i] > 0 ? Math.max(0, 1 - age / this.heat[i]) : 0;
          c.copy(SMOKE).lerp(SMOKE_HOT, Math.pow(hk, 1.3));
          alpha *= Math.min(1, age / 0.25) * Math.pow(1 - k, 1.4);
          break;
        }
        case Kind.Dust:
          c.copy(DUST);
          alpha *= Math.min(1, age / 0.1) * Math.pow(1 - k, 1.6);
          break;
        case Kind.Blood:
          c.copy(BLOOD);
          alpha *= k < 0.7 ? 1 : 1 - (k - 0.7) / 0.3;
          break;
        case Kind.Toxic:
          c.copy(TOXIC).lerp(TOXIC_DARK, k);
          alpha *= Math.min(1, age / 0.3) * Math.pow(1 - k, 1.2);
          break;
        case Kind.Drip:
          c.copy(DRIP);
          alpha *= 1 - k * k;
          break;
        case Kind.Bubble: {
          // swell, then pop
          c.setRGB(0.5, 1.9, 0.3);
          alpha *= k < 0.85 ? 0.8 : 0;
          break;
        }
        case Kind.Star: {
          const tw = 0.55 + 0.45 * Math.sin(t * 17 + i * 2.3);
          c.setRGB(2.6 * tw, 2.4 * tw, 1.6 + 1.4 * tw);
          alpha *= Math.sin(k * Math.PI);
          break;
        }
        case Kind.Mote: {
          const tw = 0.7 + 0.3 * Math.sin(t * 9 + i);
          c.setRGB(2.4 * tw, 1.8 * tw, 0.7 * tw);
          alpha *= Math.sin(k * Math.PI);
          break;
        }
        case Kind.Ember: {
          const flick = 0.55 + 0.45 * Math.sin(t * 31 + i * 1.7);
          c.setRGB(3.4 * flick, 1.3 * flick, 0.3 * flick);
          alpha *= 1 - k;
          break;
        }
      }
      this.tint[i * 4] = c.r;
      this.tint[i * 4 + 1] = c.g;
      this.tint[i * 4 + 2] = c.b;
      this.tint[i * 4 + 3] = alpha;
      const e = 1 - (1 - k) * (1 - k);
      this.size[i] = this.s0[i] + (this.s1[i] - this.s0[i]) * e;
    }
    this.active = active;
    if (!active) this.points.visible = false;
    const g = this.points.geometry;
    g.attributes.position.needsUpdate = true;
    g.attributes.size.needsUpdate = true;
    g.attributes.tint.needsUpdate = true;
  }
}

/** A particle of the mushroom cloud, placed on a rolling torus / rising stem each frame. */
interface CloudBit {
  layer: Layer;
  i: number;
  /** Cap: angle round the vertical axis, angle round the tube, spread. Stem: angle, height share, spread. */
  a: number;
  b: number;
  s: number;
  cap: boolean;
  tube: number;
}

interface Chunk {
  mesh: THREE.Mesh;
  vel: THREE.Vector3;
  spin: THREE.Vector3;
  resting: boolean;
}

interface Blast {
  t: number;
  origin: THREE.Vector3;
  bits: CloudBit[];
  chunks: Chunk[];
  ring: THREE.Mesh;
  done: () => void;
  resolved: boolean;
}

interface Ruin {
  id: number;
  group: THREE.Group;
  /** World-space fire spots in the rubble. */
  hot: THREE.Vector3[];
  light: THREE.PointLight;
  glow: THREE.MeshStandardMaterial;
  acc: { fire: number; smoke: number; ember: number };
  /** Emitters wait until the blast has cleared. */
  delay: number;
}

const rand = (a: number, b: number) => a + Math.random() * (b - a);

function randomDir(up = 0): THREE.Vector3 {
  const v = new THREE.Vector3(rand(-1, 1), rand(-1, 1) + up, rand(-1, 1));
  return v.lengthSq() < 1e-4 ? new THREE.Vector3(0, 1, 0) : v.normalize();
}

export class Explosions {
  readonly group = new THREE.Group();
  // smoke, then flames over it (normal blending: overlapping puffs must not sum
  // into a white-out), then embers (additive sparks)
  private smoke = new Layer(2400, false, 2);
  private fire = new Layer(1800, false, 3);
  private sparks = new Layer(900, true, 4);
  private stars = new Layer(400, true, 6, true);
  private emitters: Emitter[] = [];
  private blasts: Blast[] = [];
  private ruins: Ruin[] = [];
  /** Blown-out chunks lying about the plaza. */
  private rubble: THREE.Mesh[] = [];
  private flashLight = new THREE.PointLight(0xffb070, 0, 45, 1.6);
  /** Lights for burning ruins, created up front: adding lights later recompiles every material. */
  private firePool: THREE.PointLight[] = [];
  private wind = new THREE.Vector3(1.1, 0, 0.35);
  private chunkMats = [0x2a1a10, 0x1c1714, 0x4a4540, 0x5a4a38].map((c) => new THREE.MeshStandardMaterial({ color: c, roughness: 1, flatShading: true }));
  /** 0..1 screen flash, read by the grade pass. */
  flash = 0;
  /** Camera shake amplitude in world units. */
  shake = 0;

  constructor(scene: THREE.Scene) {
    this.group.add(this.smoke.points, this.fire.points, this.sparks.points, this.stars.points, this.flashLight);
    for (let k = 0; k < 4; k++) {
      const l = new THREE.PointLight(0xff7a30, 0, 16, 1.6);
      this.firePool.push(l);
      this.group.add(l);
    }
    scene.add(this.group);
  }

  /** Pixels per world unit at distance 1 (point sprites scale by it). */
  setViewport(heightPx: number, fovDeg: number) {
    const scale = heightPx / (2 * Math.tan(THREE.MathUtils.degToRad(fovDeg) / 2));
    for (const l of this.layers) l.uniforms.scale.value = scale;
  }

  /** A spray of blood from `at`, thrown mostly along `dir` (a wolf kill through the doorway). */
  blood(at: THREE.Vector3, dir: THREE.Vector3, amount = 1) {
    const d = dir.clone().normalize();
    for (let k = 0; k < 140 * amount; k++) {
      const v = d.clone().multiplyScalar(rand(2, 7)).add(randomDir(0.8).multiplyScalar(rand(0.5, 2.5)));
      v.y = Math.abs(v.y) + rand(0.5, 3);
      this.smoke.spawn({ kind: Kind.Blood, pos: at, vel: v, life: rand(0.9, 1.8), size: [rand(0.12, 0.3), 0.1], gravity: 13, drag: 0.8 });
    }
    // a heavier mist hanging in the doorway
    for (let k = 0; k < 18 * amount; k++) {
      this.smoke.spawn({ kind: Kind.Blood, pos: at, vel: d.clone().multiplyScalar(rand(0.5, 1.5)).add(randomDir().multiplyScalar(0.5)), life: rand(0.6, 1.2), size: [0.5, 1.1], alpha: 0.55, drag: 3 });
    }
  }

  private get layers() {
    return [this.smoke, this.fire, this.sparks, this.stars];
  }

  /** Run `spawn` `rate` times a second for `seconds`. */
  private emit(seconds: number, rate: number, spawn: () => void) {
    this.emitters.push({ left: seconds, rate, acc: 0, spawn });
  }

  /** A random point on the walls / roof of `house` (world space), pushed `out` beyond the surface. */
  private onHouse(house: HouseRefs, out = 0.1, top = 1): THREE.Vector3 {
    const { w, d, h } = house.size;
    const side = Math.random();
    const y = rand(0.4, h * top + (top > 1 ? 1.5 : 0));
    const p =
      side < 0.5
        ? new THREE.Vector3(rand(-w / 2, w / 2), y, (d / 2 + out) * (side < 0.35 ? 1 : -1))
        : new THREE.Vector3((w / 2 + out) * (side < 0.75 ? 1 : -1), y, rand(-d / 2, d / 2));
    return house.group.localToWorld(p);
  }

  /** 预言家: ✦ twinkles winking on and off all round `house`. */
  sparkles(house: HouseRefs, seconds: number) {
    this.emit(seconds, 38, () => {
      const p = this.onHouse(house, rand(0.3, 1.6), 1.6);
      this.stars.spawn({ kind: Kind.Star, pos: p, vel: new THREE.Vector3(0, rand(0.1, 0.5), 0), life: rand(0.5, 1.1), size: [rand(0.5, 1.1), rand(0.2, 0.5)] });
    });
  }

  /** 银水: green fumes seeping out of `house`, poison running down its walls, bubbles bursting. */
  toxic(house: HouseRefs, seconds: number) {
    const { h } = house.size;
    this.emit(seconds, 26, () => {
      const p = this.onHouse(house, 0.2, 1.2);
      this.smoke.spawn({ kind: Kind.Toxic, pos: p, vel: new THREE.Vector3(rand(-0.3, 0.3), rand(0.3, 0.9), rand(-0.3, 0.3)), life: rand(2, 3.5), size: [0.8, 2.6], alpha: 0.75, drag: 0.8, lift: 0.4, wind: 0.4 });
    });
    this.emit(seconds, 30, () => {
      const p = this.onHouse(house, 0.06).setY(rand(h * 0.5, h));
      this.smoke.spawn({ kind: Kind.Drip, pos: p, vel: new THREE.Vector3(0, -rand(0.1, 0.4), 0), life: rand(1, 2), size: [0.16, 0.12], gravity: 1.2, drag: 1.5 });
    });
    this.emit(seconds, 14, () => {
      const p = this.onHouse(house, 0.15).setY(rand(0.2, 0.7));
      this.sparks.spawn({ kind: Kind.Bubble, pos: p, vel: new THREE.Vector3(rand(-0.2, 0.2), rand(0.3, 0.8), rand(-0.2, 0.2)), life: rand(0.5, 1), size: [0.1, 0.45], drag: 1 });
    });
  }

  /** 金水: golden motes rising round `center` in a ring of radius `r`. */
  motes(center: THREE.Vector3, r: number, seconds: number) {
    this.emit(seconds, 40, () => {
      const a = rand(0, Math.PI * 2);
      const rr = r * rand(0.85, 1.05);
      const p = center.clone().add(new THREE.Vector3(Math.cos(a) * rr, rand(0.1, 1), Math.sin(a) * rr));
      this.sparks.spawn({ kind: Kind.Mote, pos: p, vel: new THREE.Vector3(0, rand(1, 2.4), 0), life: rand(1.5, 3), size: [0.22, 0.12], drag: 0.3 });
    });
  }

  /** 警徽 in flight: golden twinkles trailing behind it. */
  glint(at: THREE.Vector3) {
    this.stars.spawn({ kind: Kind.Star, pos: at.clone().add(randomDir().multiplyScalar(0.18)), vel: randomDir().multiplyScalar(rand(0.2, 0.8)), life: rand(0.4, 0.9), size: [rand(0.35, 0.8), rand(0.1, 0.3)] });
  }

  /** 警徽 lands on its wearer: a burst of stars. */
  starBurst(at: THREE.Vector3) {
    for (let k = 0; k < 46; k++) {
      this.stars.spawn({ kind: Kind.Star, pos: at, vel: randomDir().multiplyScalar(rand(1.5, 4.5)), life: rand(0.6, 1.3), size: [rand(0.4, 1), rand(0.15, 0.4)], drag: 2.2 });
    }
  }

  /** 撕警徽: gold scraps scattering and falling. */
  shards(at: THREE.Vector3) {
    for (let k = 0; k < 60; k++) {
      this.sparks.spawn({ kind: Kind.Ember, pos: at, vel: randomDir(1).multiplyScalar(rand(1.5, 5)), life: rand(0.8, 1.6), size: [0.2, 0.1], gravity: 8, drag: 0.8 });
    }
  }

  /** A puff of dust (someone drops dead where they stood). */
  puff(at: THREE.Vector3) {
    for (let k = 0; k < 40; k++) {
      const a = rand(0, Math.PI * 2);
      const v = new THREE.Vector3(Math.cos(a), rand(0.1, 0.5), Math.sin(a)).multiplyScalar(rand(1, 3));
      this.smoke.spawn({ kind: Kind.Dust, pos: at.clone().setY(0.3), vel: v, life: rand(0.8, 1.6), size: [0.6, 1.8], alpha: 0.6, drag: 2.5, lift: 0.3 });
    }
  }

  /** The small blast where the wolf stood. */
  pop(at: THREE.Vector3) {
    const c = at.clone().setY(1);
    for (let k = 0; k < 90; k++) {
      this.fire.spawn({ kind: Kind.Fire, pos: c, vel: randomDir(0.4).multiplyScalar(rand(3, 8)), life: rand(0.4, 0.9), size: [0.8, 2.2], drag: 3.5, lift: 3 });
    }
    for (let k = 0; k < 40; k++) {
      this.smoke.spawn({ kind: Kind.Smoke, pos: c, vel: randomDir(0.6).multiplyScalar(rand(1.5, 4)), life: rand(2.5, 4), size: [1, 3], alpha: 0.8, drag: 2, lift: 1.2, wind: 0.6, heat: 0.4 });
    }
    for (let k = 0; k < 30; k++) {
      this.sparks.spawn({ kind: Kind.Ember, pos: c, vel: randomDir(1.2).multiplyScalar(rand(4, 9)), life: rand(0.8, 1.8), size: [0.18, 0.12], gravity: 9, drag: 0.6 });
    }
    this.flash = Math.max(this.flash, 0.5);
    this.shake = Math.max(this.shake, 0.25);
  }

  /** The house blows up. Resolves once the cloud has towered up (the GM may move on). */
  blast(house: HouseRefs, id: number): Promise<void> {
    const origin = house.group.position.clone();
    return new Promise((done) => {
      const b: Blast = { t: 0, origin, bits: [], chunks: [], ring: this.shockRing(origin), done, resolved: false };
      const up = origin.clone().setY(2);
      // fireball
      for (let k = 0; k < 200; k++) {
        this.fire.spawn({ kind: Kind.Fire, pos: up, vel: randomDir(0.5).multiplyScalar(rand(5, 15)), life: rand(0.7, 1.7), size: [1.6, 4.5], alpha: 0.9, drag: 3.2, lift: 4 });
      }
      // dust skirt racing out along the ground
      for (let k = 0; k < 140; k++) {
        const a = rand(0, Math.PI * 2);
        const v = new THREE.Vector3(Math.cos(a), rand(0.02, 0.18), Math.sin(a)).multiplyScalar(rand(7, 15));
        this.smoke.spawn({ kind: Kind.Dust, pos: origin.clone().setY(0.6), vel: v, life: rand(2.5, 4.5), size: [1.6, 5.5], alpha: 0.75, drag: 2.2, lift: 0.4, wind: 0.5 });
      }
      // embers
      for (let k = 0; k < 160; k++) {
        this.sparks.spawn({ kind: Kind.Ember, pos: up, vel: randomDir(1.6).multiplyScalar(rand(7, 19)), life: rand(1.6, 3.6), size: [0.28, 0.18], gravity: 7, drag: 0.5, wind: 0.4 });
      }
      // the mushroom: a rolling cap on a stem
      const cloud = (layer: Layer, kind: Kind, cap: boolean, n: number, life: [number, number], size: [number, number], alpha: number, heat: number) => {
        for (let k = 0; k < n; k++) {
          const i = layer.spawn({ kind, pos: up, life: rand(...life), size: [size[0] * rand(0.7, 1.2), size[1] * rand(0.7, 1.2)], alpha, heat: heat * rand(0.7, 1.3), manual: true });
          if (i >= 0) b.bits.push({ layer, i, cap, a: rand(0, Math.PI * 2), b: cap ? rand(0, Math.PI * 2) : Math.pow(Math.random(), 0.8), s: Math.sqrt(Math.random()), tube: rand(0.75, 1.1) });
        }
      };
      cloud(this.smoke, Kind.Smoke, true, 300, [8, 12], [3, 6.5], 0.95, 5);
      cloud(this.fire, Kind.Fire, true, 150, [1.6, 3], [2.5, 4], 0.85, 0);
      cloud(this.smoke, Kind.Smoke, false, 150, [7, 10], [2, 4], 0.9, 3.5);
      cloud(this.fire, Kind.Fire, false, 70, [1.2, 2.4], [1.5, 3], 0.85, 0);
      // flying rubble
      for (let k = 0; k < 34; k++) {
        const s = rand(0.15, 0.5);
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(s * rand(0.8, 2.2), s, s * rand(0.8, 1.6)), this.chunkMats[k % this.chunkMats.length]);
        mesh.position.copy(origin).add(new THREE.Vector3(rand(-1.5, 1.5), rand(1, 3), rand(-1.5, 1.5)));
        mesh.castShadow = true;
        const dir = new THREE.Vector3(rand(-1, 1), 0, rand(-1, 1)).normalize();
        const vel = dir.multiplyScalar(rand(2.5, 9)).setY(rand(6, 16));
        const spin = new THREE.Vector3(rand(-9, 9), rand(-9, 9), rand(-9, 9));
        this.group.add(mesh);
        this.rubble.push(mesh);
        b.chunks.push({ mesh, vel, spin, resting: false });
      }
      this.flashLight.position.copy(origin).setY(4);
      this.flashLight.intensity = 140;
      this.flash = 1;
      this.shake = 1.1;
      this.blasts.push(b);
      this.ruin(house, id, 1.6);
    });
  }

  private shockRing(at: THREE.Vector3): THREE.Mesh {
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.82, 1, 48),
      new THREE.MeshBasicMaterial({ color: 0xffb070, transparent: true, opacity: 0.5, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide }),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.copy(at).setY(0.12);
    this.group.add(ring);
    return ring;
  }

  /** Swap the house for a burning ruin (also used, without the blast, when a save is loaded). */
  ruin(house: HouseRefs, id: number, delay = 0) {
    if (this.ruins.some((r) => r.id === id)) return;
    const rng = new Rng(id * 7907 + 13);
    const r = (a: number, b: number) => a + rng.next() * (b - a);
    const { w, d, h } = house.size;
    house.group.visible = false;
    house.state = 'ruined';
    house.lit = false;

    const g = new THREE.Group();
    g.position.copy(house.group.position);
    g.rotation.copy(house.group.rotation);
    const [wood, char, stone, plaster] = this.chunkMats;
    const box = (m: THREE.Material, sx: number, sy: number, sz: number, x: number, y: number, z: number, rx = 0, ry = 0, rz = 0) => {
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), m);
      mesh.position.set(x, y, z);
      mesh.rotation.set(rx, ry, rz);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      g.add(mesh);
      return mesh;
    };
    // scorched ground
    const scorch = new THREE.Mesh(new THREE.CircleGeometry(Math.max(w, d) * 0.95, 18), new THREE.MeshBasicMaterial({ color: 0x0c0907, transparent: true, opacity: 0.85, depthWrite: false }));
    scorch.rotation.x = -Math.PI / 2;
    scorch.position.y = 0.035;
    scorch.scale.set(1, 0.85, 1);
    g.add(scorch);
    // stone base, cracked open
    box(stone, w + 0.2, 0.35, 0.3, 0, 0.18, -d / 2);
    box(stone, 0.3, 0.3, d * 0.7, -w / 2, 0.15, -d * 0.1);
    box(stone, w * 0.45, 0.28, 0.3, w * 0.25, 0.14, d / 2);
    // jagged wall stubs, blackened
    const stub = (x: number, z: number, len: number, alongX: boolean) => {
      const n = Math.max(2, Math.round(len / 0.7));
      for (let k = 0; k < n; k++) {
        const hh = r(0.4, h * 0.75) * (k % 2 ? 0.6 : 1);
        const off = -len / 2 + (k + 0.5) * (len / n);
        box(k % 3 ? plaster : char, alongX ? len / n : 0.28, hh, alongX ? 0.28 : len / n, alongX ? x + off : x, hh / 2, alongX ? z : z + off);
      }
    };
    stub(0, -d / 2 + 0.15, w * 0.9, true);
    stub(-w / 2 + 0.15, -d * 0.1, d * 0.7, false);
    stub(w / 2 - 0.15, -d * 0.25, d * 0.45, false);
    // chimney stump
    box(stone, 0.5, r(1.2, 1.9), 0.5, w * 0.25, 0.8, d * 0.2 - 0.3, 0, 0, 0.12);
    // charred beams fallen across the rubble
    for (let k = 0; k < 5; k++) {
      box(k % 2 ? wood : char, r(2, w * 0.9), 0.18, 0.2, r(-w / 3, w / 3), r(0.3, 1), r(-d / 3, d / 3), r(-0.4, 0.4), r(0, Math.PI), r(-0.5, 0.5));
    }
    // rubble heaps
    for (let k = 0; k < 30; k++) {
      const s = r(0.25, 0.7);
      box([char, stone, plaster, wood][k % 4], s * r(1, 2), s * r(0.4, 0.9), s * r(1, 1.8), r(-w / 2, w / 2), s * 0.3, r(-d / 2, d / 2 + 0.6), r(-0.4, 0.4), r(0, Math.PI), r(-0.4, 0.4));
    }
    // glowing embers in the rubble
    const glow = new THREE.MeshStandardMaterial({ color: 0x1a0a04, emissive: 0xff5a18, emissiveIntensity: 2, roughness: 1 });
    for (let k = 0; k < 14; k++) {
      const s = r(0.12, 0.3);
      const m = box(glow, s, s * 0.5, s, r(-w / 2.4, w / 2.4), s * 0.2, r(-d / 2.4, d / 2.4));
      m.castShadow = false;
    }
    this.group.add(g);
    g.updateMatrixWorld(true);

    const hot = [
      new THREE.Vector3(r(-w / 4, w / 4), 0.6, r(-d / 4, d / 4)),
      new THREE.Vector3(-w / 3, 0.5, -d / 4),
      new THREE.Vector3(w / 4, 0.8, 0),
      new THREE.Vector3(r(-w / 5, w / 5), 0.4, d / 4),
    ].map((p) => g.localToWorld(p));
    const light = this.firePool[this.ruins.length % this.firePool.length];
    light.position.copy(g.position).setY(2);
    this.ruins.push({ id, group: g, hot, light, glow, acc: { fire: 0, smoke: 0, ember: 0 }, delay });
  }

  /** Back to a fresh town (new game). */
  reset() {
    for (const r of this.ruins) {
      this.group.remove(r.group);
      r.group.traverse((o) => (o as THREE.Mesh).geometry?.dispose());
      r.light.intensity = 0;
    }
    this.ruins = [];
    for (const m of this.rubble) {
      this.group.remove(m);
      m.geometry.dispose();
    }
    this.rubble = [];
    for (const b of this.blasts) {
      this.group.remove(b.ring);
      if (!b.resolved) b.done();
    }
    this.blasts = [];
    for (const l of this.layers) l.clear();
    this.emitters = [];
    this.flashLight.intensity = 0;
    this.flash = 0;
    this.shake = 0;
  }

  update(dt: number, t: number) {
    this.flash *= Math.exp(-dt * 3.5);
    this.shake *= Math.exp(-dt * 2.2);
    this.flashLight.intensity *= Math.exp(-dt * 2.4);

    for (const e of this.emitters) {
      e.left -= dt;
      for (e.acc += dt * e.rate; e.acc >= 1; e.acc--) e.spawn();
    }
    this.emitters = this.emitters.filter((e) => e.left > 0);
    for (const b of this.blasts) this.stepBlast(b, dt);
    for (const b of this.blasts) if (b.t >= 14) this.group.remove(b.ring);
    this.blasts = this.blasts.filter((b) => b.t < 14);

    for (const r of this.ruins) {
      if (r.delay > 0) {
        r.delay -= dt;
        continue;
      }
      const flick = 0.75 + Math.sin(t * 13 + r.id) * 0.12 + Math.sin(t * 29 + r.id * 2) * 0.1 + Math.random() * 0.08;
      r.light.intensity = 38 * flick;
      r.glow.emissiveIntensity = 1.6 + flick * 1.2;
      r.acc.fire += dt * 55;
      r.acc.smoke += dt * 14;
      r.acc.ember += dt * 6;
      for (; r.acc.fire >= 1; r.acc.fire--) {
        const p = r.hot[Math.floor(Math.random() * r.hot.length)].clone().add(new THREE.Vector3(rand(-0.6, 0.6), 0, rand(-0.6, 0.6)));
        this.fire.spawn({ kind: Kind.Fire, pos: p, vel: new THREE.Vector3(rand(-0.3, 0.3), rand(1.2, 2.6), rand(-0.3, 0.3)), life: rand(0.5, 1.1), size: [1.3, 0.5], alpha: 0.9, lift: 1.8, wind: 0.3 });
      }
      for (; r.acc.smoke >= 1; r.acc.smoke--) {
        const p = r.hot[Math.floor(Math.random() * r.hot.length)].clone().setY(1.8);
        this.smoke.spawn({ kind: Kind.Smoke, pos: p, vel: new THREE.Vector3(rand(-0.3, 0.3), rand(1.8, 3), rand(-0.3, 0.3)), life: rand(6, 9), size: [1.4, 6.5], alpha: 0.85, drag: 0.25, lift: 0.5, wind: 0.9, heat: 1.4 });
      }
      for (; r.acc.ember >= 1; r.acc.ember--) {
        const p = r.hot[Math.floor(Math.random() * r.hot.length)];
        this.sparks.spawn({ kind: Kind.Ember, pos: p, vel: new THREE.Vector3(rand(-1, 1), rand(2.5, 5), rand(-1, 1)), life: rand(1.2, 2.6), size: [0.16, 0.1], lift: 0.6, gravity: 1.2, wind: 0.8 });
      }
    }

    for (const l of this.layers) l.update(dt, t, this.wind);
  }

  private stepBlast(b: Blast, dt: number) {
    b.t += dt;
    const t = b.t;
    // cap rises and spreads, the tube keeps rolling (slower as it cools)
    const capY = 3 + 13 * (1 - Math.exp(-t / 1.6));
    const R = 1 + 3.4 * (1 - Math.exp(-t / 1.3));
    const tube = 0.8 + 2.3 * (1 - Math.exp(-t / 1.5));
    const roll = 2.4 * Math.exp(-t / 4);
    const drift = 0.18 * t * t * Math.exp(-t / 9);
    const cx = b.origin.x + this.wind.x * drift;
    const cz = b.origin.z + this.wind.z * drift;
    for (const bit of b.bits) {
      if (!bit.layer.alive(bit.i)) continue;
      if (bit.cap) {
        bit.b -= roll * dt;
        const rt = tube * bit.tube * (0.5 + 0.5 * bit.s);
        const rr = R + rt * Math.cos(bit.b);
        bit.layer.place(bit.i, cx + Math.cos(bit.a) * rr, capY + rt * Math.sin(bit.b) * 0.8, cz + Math.sin(bit.a) * rr);
      } else {
        bit.a += dt * 0.5;
        const y = bit.b * capY * 0.92;
        const width = (0.5 + 1.2 * Math.pow(1 - bit.b, 2) + 0.35 * bit.b) * (0.6 + 0.4 * Math.min(1, t / 2)) * bit.s;
        const k = y / Math.max(capY, 1);
        bit.layer.place(bit.i, b.origin.x + (cx - b.origin.x) * k + Math.cos(bit.a) * width, y, b.origin.z + (cz - b.origin.z) * k + Math.sin(bit.a) * width);
      }
    }
    // shock ring
    const rk = Math.min(1, t / 0.9);
    const radius = 1 + 19 * (1 - Math.pow(1 - rk, 3));
    b.ring.scale.setScalar(radius);
    (b.ring.material as THREE.MeshBasicMaterial).opacity = 0.5 * (1 - rk) * (1 - rk);
    b.ring.visible = rk < 1;
    // rubble: fly, bounce once or twice, then lie where it landed
    for (const c of b.chunks) {
      if (c.resting) continue;
      c.vel.y -= 20 * dt;
      c.mesh.position.addScaledVector(c.vel, dt);
      c.mesh.rotation.x += c.spin.x * dt;
      c.mesh.rotation.y += c.spin.y * dt;
      c.mesh.rotation.z += c.spin.z * dt;
      const floor = 0.12;
      if (c.mesh.position.y < floor) {
        c.mesh.position.y = floor;
        if (Math.abs(c.vel.y) < 2.5) {
          c.resting = true;
          c.mesh.rotation.x = Math.round(c.mesh.rotation.x / (Math.PI / 2)) * (Math.PI / 2);
          c.mesh.rotation.z = Math.round(c.mesh.rotation.z / (Math.PI / 2)) * (Math.PI / 2);
        } else {
          c.vel.set(c.vel.x * 0.4, -c.vel.y * 0.3, c.vel.z * 0.4);
          c.spin.multiplyScalar(0.4);
        }
      }
    }
    if (!b.resolved && t > 5) {
      b.resolved = true;
      b.done();
    }
  }
}
