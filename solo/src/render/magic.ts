import * as THREE from 'three';
import { angelSheet, pixelTexture, setFrame } from './pixel';
import type { HouseRefs } from './town';

/**
 * Night-role set pieces that are not particles: the archangel and ring of holy
 * light over a house the witch saved (金水), the seer's green / red verdict ring,
 * a house turning see-through (查验) or corroding (银水), and the hunter's
 * crosshair (a DOM overlay tracking a point in the scene).
 */

const holyVert = `
  varying vec2 vUv;
  void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

// a wall of light rising from the ground, bright at the foot, streaked with slow rays
const holyFrag = `
  uniform float power;
  uniform float time;
  varying vec2 vUv;
  void main(){
    float rays = pow(0.5 + 0.5 * sin(vUv.x * 6.2832 * 14.0 + time * 1.3), 3.0)
               + 0.6 * pow(0.5 + 0.5 * sin(vUv.x * 6.2832 * 23.0 - time * 0.9), 4.0);
    float fall = pow(1.0 - vUv.y, 1.8);
    float a = 0.5 * power * fall * (0.3 + 0.7 * rays);
    gl_FragColor = vec4(vec3(1.5, 1.25, 0.7) * a, 1.0);
  }`;

export class Magic {
  readonly group = new THREE.Group();
  /** Billboards the stage turns to face the camera. */
  readonly billboards: THREE.Object3D[] = [];
  /** Created up front: adding a light later recompiles every material. */
  readonly light = new THREE.PointLight(0xffffff, 0, 18, 1.6);
  private angel: THREE.Mesh;
  private angelTex: THREE.Texture;
  private angelMat: THREE.MeshBasicMaterial;
  private holy: THREE.Mesh;
  private holyMat: THREE.ShaderMaterial;
  private holyGround: THREE.Mesh;
  private verdict: THREE.Group;
  private verdictMats: THREE.MeshBasicMaterial[];
  private angelOn = 0;
  private angelGoal = 0;
  private angelBase = new THREE.Vector3();
  private holyOn = 0;
  private holyGoal = 0;
  private verdictOn = 0;
  private verdictGoal = 0;
  private lightGoal = 0;

  constructor(scene: THREE.Scene) {
    this.angelTex = pixelTexture(angelSheet());
    this.angelTex.repeat.set(0.5, 1);
    // exact sprite colours (not tone-mapped); its whites still catch a little bloom
    this.angelMat = new THREE.MeshBasicMaterial({ map: this.angelTex, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide, fog: false, toneMapped: false });
    this.angelMat.color.setScalar(0.92);
    const ag = new THREE.PlaneGeometry(4.2, 4.2);
    ag.translate(0, 2.1, 0);
    this.angel = new THREE.Mesh(ag, this.angelMat);
    this.angel.visible = false;
    this.angel.renderOrder = 7;
    this.billboards.push(this.angel);

    const cyl = new THREE.CylinderGeometry(1, 1, 1, 48, 1, true);
    cyl.translate(0, 0.5, 0);
    this.holyMat = new THREE.ShaderMaterial({
      uniforms: { power: { value: 0 }, time: { value: 0 } },
      vertexShader: holyVert,
      fragmentShader: holyFrag,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    });
    this.holy = new THREE.Mesh(cyl, this.holyMat);
    this.holy.visible = false;
    this.holy.renderOrder = 5;
    this.holyGround = new THREE.Mesh(
      new THREE.RingGeometry(0.9, 1.05, 64),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(2.4, 2, 1.1), transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending, fog: false }),
    );
    this.holyGround.rotation.x = -Math.PI / 2;
    this.holyGround.visible = false;

    this.verdict = new THREE.Group();
    this.verdictMats = [0, 1].map(
      // solid and not tone-mapped, so the red / green stays saturated instead of washing out to pink
      () => new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, depthWrite: false, fog: false, toneMapped: false }),
    );
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.6, 0.85, 40), this.verdictMats[0]);
    const disc = new THREE.Mesh(new THREE.CircleGeometry(0.6, 40), this.verdictMats[1]);
    for (const m of [ring, disc]) {
      m.rotation.x = -Math.PI / 2;
      this.verdict.add(m);
    }
    this.verdict.position.y = 0.06;
    this.verdict.visible = false;

    this.group.add(this.angel, this.holy, this.holyGround, this.verdict, this.light);
    scene.add(this.group);
  }

  /** 金水: the archangel comes down over `house`, a ring of holy light rises round it. */
  bless(house: HouseRefs) {
    const { w, d, h } = house.size;
    const R = Math.hypot(w, d) / 2 + 0.9;
    const p = house.group.position;
    this.holy.position.copy(p);
    this.holy.scale.set(R, h + 3, R);
    this.holyGround.position.copy(p).setY(0.05);
    this.holyGround.scale.setScalar(R);
    this.angelBase.copy(p).setY(h + 3.6);
    this.angel.position.copy(this.angelBase).setY(this.angelBase.y + 5);
    this.holy.visible = this.holyGround.visible = this.angel.visible = true;
    this.angelGoal = this.holyGoal = 1;
    this.shine(p.clone().setY(h + 1.5), 0xffd890, 30);
  }

  unbless() {
    this.angelGoal = this.holyGoal = 0;
    this.lightGoal = 0;
  }

  /** 查验: a ring under the revealed player, green (good) or red (wolf). */
  showVerdict(at: THREE.Vector3, wolf: boolean) {
    const c = wolf ? new THREE.Color(1, 0.08, 0.05) : new THREE.Color(0.15, 1, 0.3);
    for (const m of this.verdictMats) m.color.copy(c);
    this.verdict.position.set(at.x, 0.06, at.z);
    this.verdict.visible = true;
    this.verdictGoal = 1;
  }

  hideVerdict() {
    this.verdictGoal = 0;
  }

  /** Light the scene round `at` (fades in; `dark()` fades it out). */
  shine(at: THREE.Vector3, color: THREE.ColorRepresentation, intensity: number) {
    this.light.position.copy(at);
    this.light.color.set(color);
    this.lightGoal = intensity;
  }

  dark() {
    this.lightGoal = 0;
  }

  reset() {
    this.angelOn = this.angelGoal = this.holyOn = this.holyGoal = this.verdictOn = this.verdictGoal = this.lightGoal = 0;
    this.light.intensity = 0;
    this.angel.visible = this.holy.visible = this.holyGround.visible = this.verdict.visible = false;
  }

  update(dt: number, t: number) {
    const ease = (v: number, goal: number, rate: number) => v + (goal - v) * Math.min(1, dt * rate);
    this.light.intensity = ease(this.light.intensity, this.lightGoal, 2);

    if (this.angel.visible) {
      this.angelOn = ease(this.angelOn, this.angelGoal, this.angelGoal ? 0.9 : 1.6);
      this.angelMat.opacity = Math.min(1, this.angelOn * 1.2);
      // floats down, then hovers with a slow bob; wings beat every ~0.7 s
      const target = this.angelBase.y + Math.sin(t * 1.6) * 0.25;
      this.angel.position.y = ease(this.angel.position.y, this.angelGoal ? target : this.angelBase.y + 4, 1.1);
      setFrame(this.angelTex, Math.floor(t / 0.35) % 2, 2);
      if (!this.angelGoal && this.angelOn < 0.01) this.angel.visible = false;
    }
    if (this.holy.visible) {
      this.holyOn = ease(this.holyOn, this.holyGoal, this.holyGoal ? 0.8 : 1.4);
      this.holyMat.uniforms.power.value = this.holyOn * (0.85 + Math.sin(t * 2.2) * 0.15);
      this.holyMat.uniforms.time.value = t;
      (this.holyGround.material as THREE.MeshBasicMaterial).opacity = this.holyOn;
      if (!this.holyGoal && this.holyOn < 0.01) this.holy.visible = this.holyGround.visible = false;
    }
    if (this.verdict.visible) {
      this.verdictOn = ease(this.verdictOn, this.verdictGoal, 3);
      const pulse = 1 + Math.sin(t * 4) * 0.08;
      this.verdict.scale.setScalar(pulse * (0.6 + 0.4 * this.verdictOn));
      this.verdictMats[0].opacity = this.verdictOn;
      this.verdictMats[1].opacity = this.verdictOn * 0.35;
      if (!this.verdictGoal && this.verdictOn < 0.01) this.verdict.visible = false;
    }
  }
}

interface Snap {
  mat: THREE.MeshStandardMaterial | THREE.MeshBasicMaterial;
  color: THREE.Color;
  opacity: number;
  transparent: boolean;
  depthWrite: boolean;
  emissive?: THREE.Color;
  emissiveIntensity?: number;
  window: boolean;
}

/**
 * Temporary changes to a house's look (see-through, poisoned green), undone by
 * `restore`. Every house has its own materials, so nothing else is touched.
 */
export class HouseSkin {
  private snaps: Snap[] = [];
  private seeThrough = false;

  constructor(readonly house: HouseRefs) {
    const seen = new Set<THREE.Material>();
    house.group.traverse((o) => {
      const m = (o as THREE.Mesh).material;
      if (!m) return;
      for (const mat of Array.isArray(m) ? m : [m]) {
        if (seen.has(mat) || !(mat instanceof THREE.MeshStandardMaterial || mat instanceof THREE.MeshBasicMaterial)) continue;
        seen.add(mat);
        const std = mat instanceof THREE.MeshStandardMaterial ? mat : null;
        this.snaps.push({
          mat,
          color: mat.color.clone(),
          opacity: mat.opacity,
          transparent: mat.transparent,
          depthWrite: mat.depthWrite,
          emissive: std?.emissive.clone(),
          emissiveIntensity: std?.emissiveIntensity,
          window: !!std && house.windows.includes(std),
        });
      }
    });
  }

  /** 1 = solid, towards 0 = see-through (the insides show). */
  opacity(o: number) {
    if (!this.seeThrough) {
      this.seeThrough = true;
      for (const s of this.snaps) {
        s.mat.transparent = true;
        s.mat.depthWrite = false;
        s.mat.needsUpdate = true;
      }
    }
    for (const s of this.snaps) s.mat.opacity = s.opacity * o;
  }

  /** 0..1 poison: walls go a sickly green-black and glow faintly. */
  corrode(k: number, t: number) {
    const sick = new THREE.Color(0.2, 0.34, 0.12);
    const glow = new THREE.Color(0.2, 1, 0.15);
    for (const s of this.snaps) {
      if (s.window) continue;
      s.mat.color.copy(s.color).lerp(s.color.clone().multiply(sick), k);
      if (s.mat instanceof THREE.MeshStandardMaterial) {
        s.mat.emissive.copy(s.emissive!).lerp(glow, k);
        s.mat.emissiveIntensity = THREE.MathUtils.lerp(s.emissiveIntensity!, 0.035 + Math.sin(t * 5) * 0.02, k);
      }
    }
  }

  restore() {
    for (const s of this.snaps) {
      s.mat.color.copy(s.color);
      s.mat.opacity = s.opacity;
      if (s.mat instanceof THREE.MeshStandardMaterial) {
        s.mat.emissive.copy(s.emissive!);
        if (!s.window) s.mat.emissiveIntensity = s.emissiveIntensity!;
      }
      if (this.seeThrough) {
        s.mat.transparent = s.transparent;
        s.mat.depthWrite = s.depthWrite;
        s.mat.needsUpdate = true;
      }
    }
    this.seeThrough = false;
  }
}

const RETICLE_SVG = `
<svg viewBox="-50 -50 100 100" width="100%" height="100%" fill="none" stroke="currentColor" stroke-linecap="square">
  <circle r="30" stroke-width="3"/>
  <circle r="42" stroke-width="1.5" stroke-dasharray="6 8" class="spin"/>
  <path d="M0 -46 V-18 M0 18 V46 M-46 0 H-18 M18 0 H46" stroke-width="3"/>
  <circle r="2.5" fill="currentColor" stroke="none"/>
</svg>`;

/**
 * 猎人的准星: sweeps in across the screen, overshoots, settles on the target
 * and locks (shrinks, turns red); `fire` kicks it out.
 */
export class Reticle {
  readonly el: HTMLDivElement;
  private t = 0;
  private active = false;
  private lockedAt = -1;
  private firedAt = -1;
  private from = { x: 0, y: 0 };
  private over = { x: 0, y: 0 };
  /** Seconds from the start until it rests on the target. */
  readonly sweep = 1.9;

  constructor(host: HTMLElement) {
    this.el = document.createElement('div');
    this.el.innerHTML = RETICLE_SVG;
    Object.assign(this.el.style, {
      position: 'absolute',
      left: '0',
      top: '0',
      width: '120px',
      height: '120px',
      marginLeft: '-60px',
      marginTop: '-60px',
      pointerEvents: 'none',
      display: 'none',
      zIndex: '1',
      filter: 'drop-shadow(0 0 4px rgba(0,0,0,0.9))',
    });
    host.appendChild(this.el);
  }

  start(w: number, h: number) {
    const side = Math.random() < 0.5 ? -1 : 1;
    this.from = { x: side * w * 0.42, y: -h * (0.18 + Math.random() * 0.15) };
    this.over = { x: -side * w * 0.12, y: h * 0.07 };
    this.t = 0;
    this.lockedAt = this.firedAt = -1;
    this.active = true;
  }

  lock() {
    this.lockedAt = this.t;
  }

  fire() {
    this.firedAt = this.t;
  }

  stop() {
    this.active = false;
    this.el.style.display = 'none';
  }

  update(dt: number, x: number, y: number) {
    if (!this.active) return;
    this.t += dt;
    const t = this.t;
    // from the edge, across and past the target, then back onto it
    const k = Math.min(1, t / this.sweep);
    const smooth = (v: number) => v * v * (3 - 2 * v);
    let ox: number;
    let oy: number;
    if (k < 0.6) {
      const e = smooth(k / 0.6);
      ox = this.from.x + (this.over.x - this.from.x) * e;
      oy = this.from.y + (this.over.y - this.from.y) * e;
    } else {
      const e = smooth((k - 0.6) / 0.4);
      ox = this.over.x * (1 - e);
      oy = this.over.y * (1 - e);
    }
    // a little hand tremor until it locks
    const shake = this.lockedAt < 0 ? 3 * (1 - k * 0.7) : 0;
    ox += Math.sin(t * 23) * shake;
    oy += Math.cos(t * 19) * shake;
    let scale = 1.35 - 0.2 * k;
    let opacity = Math.min(1, t * 4);
    let color = '#f4ead0';
    if (this.lockedAt >= 0) {
      const lt = t - this.lockedAt;
      scale = 0.8 + 0.35 * Math.exp(-lt * 9);
      color = '#ff3a2a';
    }
    if (this.firedAt >= 0) {
      const ft = t - this.firedAt;
      scale = 0.8 + ft * 2.5;
      opacity = Math.max(0, 1 - ft * 2.5);
      if (opacity === 0) return this.stop();
    }
    const s = this.el.style;
    s.display = '';
    s.transform = `translate(${x + ox}px, ${y + oy}px) scale(${scale}) rotate(${(this.lockedAt < 0 ? t * 40 : 0).toFixed(1)}deg)`;
    s.opacity = String(opacity);
    s.color = color;
  }
}
