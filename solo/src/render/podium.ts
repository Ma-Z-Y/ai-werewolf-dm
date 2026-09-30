import * as THREE from 'three';

/** 颁奖台 stands on the plaza between the well and seat 1 (towards the default camera). */
export const PODIUM_POS = new THREE.Vector3(0, 0, 5.2);
export const PODIUM_H = 0.9;
const DEPTH = 2.2;
/** Room per person on the podium. */
export const PODIUM_SPACING = 1.15;

const EMOJI_FONT = '"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji","Twemoji Mozilla",sans-serif';
const emojiCache = new Map<string, THREE.CanvasTexture>();

/** An emoji drawn onto a canvas texture (cached per character). */
export function emojiTexture(ch: string): THREE.CanvasTexture {
  let t = emojiCache.get(ch);
  if (t) return t;
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const ctx = c.getContext('2d')!;
  ctx.font = `104px ${EMOJI_FONT}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(ch, 64, 70);
  t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  emojiCache.set(ch, t);
  return t;
}

export function emojiSprite(ch: string, size: number): THREE.Sprite {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: emojiTexture(ch), transparent: true, alphaTest: 0.05, depthWrite: false }));
  s.scale.setScalar(size);
  return s;
}

/** Remove a sprite for good (the emoji texture itself is shared and kept). */
function drop(s: THREE.Sprite) {
  s.removeFromParent();
  s.material.dispose();
}

/** A sprite flying on its own: thrown (ballistic, lands on `to`) or drifting down (confetti). */
interface Prop {
  sprite: THREE.Sprite;
  from: THREE.Vector3;
  to: THREE.Vector3;
  arc: number;
  t: number;
  dur: number;
  spin: number;
  land?: (at: THREE.Vector3) => void;
}

interface Flake {
  sprite: THREE.Sprite;
  vel: THREE.Vector3;
  spin: number;
  floor: number;
}

/**
 * The awards set: a podium that rises out of the plaza (red carpet, gold trim,
 * a 🏆 / 💩 sign on the front), bouquets, falling petals and flying poop.
 */
export class Podium {
  readonly group = new THREE.Group();
  private stand = new THREE.Group();
  private sign: THREE.Mesh;
  private props: Prop[] = [];
  private flakes: Flake[] = [];
  /** Things left lying about (bouquets, stuck poop): cleared with the set. */
  private litter: THREE.Sprite[] = [];
  width = 4;

  constructor() {
    const white = new THREE.MeshStandardMaterial({ color: 0xcdbfa4, roughness: 0.9 });
    const gold = new THREE.MeshStandardMaterial({ color: 0xd0a038, roughness: 0.45, metalness: 0.5 });
    const red = new THREE.MeshStandardMaterial({ color: 0xc02a32, roughness: 0.9 });
    // a unit-wide block, stretched sideways to fit however many share the award
    const body = new THREE.Mesh(new THREE.BoxGeometry(1, PODIUM_H, DEPTH), white);
    body.position.y = PODIUM_H / 2;
    const carpet = new THREE.Mesh(new THREE.BoxGeometry(1.04, 0.05, DEPTH + 0.06), red);
    carpet.position.y = PODIUM_H + 0.02;
    const trim = new THREE.Mesh(new THREE.BoxGeometry(1.02, 0.1, DEPTH + 0.04), gold);
    trim.position.y = PODIUM_H - 0.08;
    const foot = new THREE.Mesh(new THREE.BoxGeometry(1.06, 0.12, DEPTH + 0.1), gold);
    foot.position.y = 0.06;
    for (const m of [body, carpet, trim, foot]) {
      m.castShadow = true;
      m.receiveShadow = true;
      this.stand.add(m);
    }
    // steps up the front
    const step = new THREE.Mesh(new THREE.BoxGeometry(1.4, PODIUM_H / 2, 0.5), white);
    step.position.set(0, PODIUM_H / 4, DEPTH / 2 + 0.25);
    step.castShadow = step.receiveShadow = true;
    this.group.add(step);
    this.sign = new THREE.Mesh(
      new THREE.PlaneGeometry(0.62, 0.62),
      new THREE.MeshBasicMaterial({ map: emojiTexture('🏆'), transparent: true, alphaTest: 0.05, depthWrite: false }),
    );
    this.sign.position.set(-1, PODIUM_H * 0.55, DEPTH / 2 + 0.01);
    this.group.add(this.stand, this.sign);
    this.group.position.copy(PODIUM_POS);
    this.group.visible = false;
  }

  /** Size the podium for `n` people and put `emoji` on its front. */
  setup(n: number, emoji: string) {
    this.width = Math.max(3.2, n * PODIUM_SPACING + 0.9);
    this.stand.scale.x = this.width;
    this.sign.position.x = -this.width / 2 + 0.55;
    (this.sign.material as THREE.MeshBasicMaterial).map = emojiTexture(emoji);
  }

  /** Where the k-th of n stands on top (world space). */
  spot(k: number, n: number): THREE.Vector3 {
    return new THREE.Vector3((k - (n - 1) / 2) * PODIUM_SPACING, PODIUM_H, 0).add(PODIUM_POS);
  }

  /** Where the k-th of n waits at the foot of the podium before hopping up. */
  foot(k: number, n: number): THREE.Vector3 {
    return this.spot(k, n).setY(0).add(new THREE.Vector3(0, 0, DEPTH / 2 + 0.9));
  }

  /** Rise out of the ground (0..1). */
  rise(k: number) {
    this.group.visible = k > 0;
    this.group.position.y = PODIUM_POS.y - (1 - k) * (PODIUM_H + 0.3);
  }

  /** A sprite that stays put (a bouquet, poop stuck on someone) until the set is cleared. */
  place(ch: string, at: THREE.Vector3, size: number): THREE.Sprite {
    const s = emojiSprite(ch, size);
    s.position.copy(at);
    this.group.parent?.add(s);
    this.litter.push(s);
    // keep the pile from growing without end
    if (this.litter.length > 70) drop(this.litter.shift()!);
    return s;
  }

  unplace(s: THREE.Sprite) {
    drop(s);
    this.litter = this.litter.filter((x) => x !== s);
  }

  /** Throw `ch` from `from` to `to` in an arc; `land` fires on arrival. */
  throw(ch: string, from: THREE.Vector3, to: THREE.Vector3, opts: { size?: number; dur?: number; arc?: number; land?: (at: THREE.Vector3) => void } = {}) {
    const sprite = emojiSprite(ch, opts.size ?? 0.55);
    sprite.position.copy(from);
    this.group.parent?.add(sprite);
    this.props.push({ sprite, from: from.clone(), to: to.clone(), arc: opts.arc ?? 2, t: 0, dur: opts.dur ?? 0.7, spin: (Math.random() - 0.5) * 16, land: opts.land });
  }

  /** Petals / confetti drifting down over the podium. */
  sprinkle(chars: string[], count: number) {
    for (let k = 0; k < count; k++) {
      const s = emojiSprite(chars[k % chars.length], 0.28 + Math.random() * 0.18);
      s.position.set(PODIUM_POS.x + (Math.random() - 0.5) * (this.width + 3), 5 + Math.random() * 4, PODIUM_POS.z + (Math.random() - 0.5) * 4);
      this.group.parent?.add(s);
      this.flakes.push({ sprite: s, vel: new THREE.Vector3((Math.random() - 0.5) * 0.6, -(0.9 + Math.random() * 0.8), (Math.random() - 0.5) * 0.6), spin: (Math.random() - 0.5) * 4, floor: 0.05 });
    }
  }

  update(dt: number, t: number) {
    const done: Prop[] = [];
    for (const p of this.props) {
      p.t += dt;
      const k = Math.min(1, p.t / p.dur);
      p.sprite.position.lerpVectors(p.from, p.to, k);
      p.sprite.position.y += Math.sin(k * Math.PI) * p.arc;
      p.sprite.material.rotation += p.spin * dt;
      if (k >= 1) done.push(p);
    }
    if (done.length) {
      this.props = this.props.filter((p) => !done.includes(p));
      for (const p of done) {
        drop(p.sprite);
        p.land?.(p.to);
      }
    }
    const landed: Flake[] = [];
    for (const f of this.flakes) {
      f.sprite.position.addScaledVector(f.vel, dt);
      f.sprite.position.x += Math.sin(t * 2 + f.spin * 3) * dt * 0.4;
      f.sprite.material.rotation += f.spin * dt;
      if (f.sprite.position.y <= f.floor) landed.push(f);
    }
    if (landed.length) {
      this.flakes = this.flakes.filter((f) => !landed.includes(f));
      for (const f of landed) drop(f.sprite);
    }
  }

  /** Take the whole set down (a new game, or back to the title). */
  clear() {
    for (const s of [...this.props, ...this.flakes].map((x) => x.sprite).concat(this.litter)) drop(s);
    this.props = [];
    this.flakes = [];
    this.litter = [];
    this.rise(0);
  }
}
