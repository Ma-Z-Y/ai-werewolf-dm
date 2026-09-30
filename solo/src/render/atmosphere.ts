import * as THREE from 'three';
import { cloudTexture } from './pixel';

/**
 * Weather & lighting. Day = heavy overcast (乌云密布); night = thunderstorm
 * (雷雨交加). `mix` goes 0 (day) → 1 (night) and is eased by Stage.
 */
export class Atmosphere {
  readonly hemi = new THREE.HemisphereLight(0x9aa3b0, 0x3a3026, 1.2);
  readonly key = new THREE.DirectionalLight(0xd8d4cc, 1.6);
  readonly flash = new THREE.DirectionalLight(0xc8d8ff, 0);
  private sky: THREE.Mesh;
  private skyMat: THREE.ShaderMaterial;
  private clouds: THREE.Mesh[] = [];
  private rain: THREE.LineSegments;
  private rainPos: Float32Array;
  private rainVel: Float32Array;
  private fog: THREE.FogExp2;
  private nextStrike = 3;
  private strikeT = -1;
  private strikePattern: number[] = [];
  /** 0..1 current lightning brightness (read by post-processing). */
  lightning = 0;
  /** Fired when a lightning strike starts; arg = 0 (close) … 1 (far), for thunder delay. */
  onStrike?: (distance: number) => void;
  mix = 0;
  /** 0..1 好人胜利: the fog lifts, the clouds part and the sun comes up. */
  clear = 0;
  /** 0..1 颁奖典礼: fair weather — white clouds drifting in the clear blue sky. */
  fair = 0;
  /** 0..1 狼人胜利: a still, blood-red night — no rain, no lightning, a red moon. */
  blood = 0;
  private sun: THREE.Group;
  private moon: THREE.Group;

  constructor(scene: THREE.Scene) {
    this.key.position.set(-30, 45, 25);
    this.key.castShadow = true;
    this.key.shadow.mapSize.set(2048, 2048);
    const sc = this.key.shadow.camera;
    sc.left = -45;
    sc.right = 45;
    sc.top = 45;
    sc.bottom = -55;
    sc.near = 1;
    sc.far = 140;
    this.key.shadow.bias = -0.0008;
    this.key.shadow.normalBias = 0.03;
    this.flash.position.set(20, 60, -10);
    scene.add(this.hemi, this.key, this.flash);

    this.fog = new THREE.FogExp2(0x5d6268, 0.014);
    scene.fog = this.fog;

    this.skyMat = new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      fog: false,
      uniforms: {
        top: { value: new THREE.Color() },
        bottom: { value: new THREE.Color() },
      },
      vertexShader: `varying vec3 vP; void main(){ vP = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
      fragmentShader: `uniform vec3 top; uniform vec3 bottom; varying vec3 vP;
        void main(){ float h = smoothstep(-0.05, 0.6, vP.y); gl_FragColor = vec4(mix(bottom, top, h), 1.0); }`,
    });
    this.sky = new THREE.Mesh(new THREE.SphereGeometry(180, 24, 12), this.skyMat);
    scene.add(this.sky);

    const ct = cloudTexture(3);
    for (let i = 0; i < 3; i++) {
      const t = ct.clone();
      t.needsUpdate = true;
      t.repeat.set(2 + i, 2 + i);
      const m = new THREE.MeshBasicMaterial({ map: t, transparent: true, depthWrite: false, fog: false, opacity: 0.9, color: 0x6a6e74 });
      const plane = new THREE.Mesh(new THREE.PlaneGeometry(400, 400), m);
      plane.rotation.x = Math.PI / 2;
      plane.position.y = 38 + i * 6;
      scene.add(plane);
      this.clouds.push(plane);
    }

    // rain streaks
    const N = 5000;
    this.rainPos = new Float32Array(N * 6);
    this.rainVel = new Float32Array(N);
    for (let i = 0; i < N; i++) this.resetDrop(i, true);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(this.rainPos, 3));
    this.rain = new THREE.LineSegments(
      g,
      new THREE.LineBasicMaterial({ color: 0x9ab0c8, transparent: true, opacity: 0, depthWrite: false }),
    );
    this.rain.frustumCulled = false;
    scene.add(this.rain);

    // north, low over the church, where the ending shots look
    this.sun = disc(0xfff2c0, 0xffd070, 9);
    this.moon = disc(0xff6a50, 0xa01818, 7);
    scene.add(this.sun, this.moon);
  }

  private resetDrop(i: number, anyHeight = false) {
    const x = (Math.random() - 0.5) * 70;
    const z = (Math.random() - 0.5) * 70 - 6;
    const y = anyHeight ? Math.random() * 30 : 26 + Math.random() * 6;
    const o = i * 6;
    this.rainPos[o] = x;
    this.rainPos[o + 1] = y;
    this.rainPos[o + 2] = z;
    this.rainPos[o + 3] = x + 0.12;
    this.rainPos[o + 4] = y + 0.9;
    this.rainPos[o + 5] = z;
    this.rainVel[i] = 26 + Math.random() * 10;
  }

  update(dt: number, t: number) {
    const n = this.mix;
    const lerpC = (a: number, b: number) => new THREE.Color(a).lerp(new THREE.Color(b), n);

    // lightning (night only): pattern of quick flashes
    if (n > 0.6 && this.blood < 0.05) {
      this.nextStrike -= dt;
      if (this.nextStrike <= 0) {
        this.strikeT = 0;
        this.strikePattern = Math.random() < 0.5 ? [0, 0.12, 0.26] : [0, 0.09];
        this.nextStrike = 5 + Math.random() * 9;
        this.onStrike?.(Math.random());
      }
    }
    this.lightning = 0;
    if (this.strikeT >= 0) {
      this.strikeT += dt;
      for (const s of this.strikePattern) {
        const d = this.strikeT - s;
        if (d >= 0 && d < 0.18) this.lightning = Math.max(this.lightning, 1 - d / 0.18);
      }
      if (this.strikeT > 0.6) this.strikeT = -1;
    }
    const L = this.lightning * n;

    this.hemi.color.copy(lerpC(0x8e97a4, 0x2a3450));
    this.hemi.groundColor.copy(lerpC(0x3a3026, 0x10121a));
    this.hemi.intensity = THREE.MathUtils.lerp(1.25, 0.8, n) + L * 2.5;
    this.key.color.copy(lerpC(0xcfd0d0, 0x6a7ab8));
    this.key.intensity = THREE.MathUtils.lerp(1.5, 0.35, n);
    this.flash.intensity = L * 5;

    this.skyMat.uniforms.top.value.copy(lerpC(0x464b52, 0x07090f)).lerp(new THREE.Color(0xb8c4e0), L * 0.6);
    this.skyMat.uniforms.bottom.value.copy(lerpC(0x7c8086, 0x141824)).lerp(new THREE.Color(0xc8d0e8), L * 0.5);
    this.fog.color.copy(lerpC(0x62676e, 0x0e1119)).lerp(new THREE.Color(0x8090b0), L * 0.4);
    this.fog.density = THREE.MathUtils.lerp(0.013, 0.02, n);

    this.clouds.forEach((c, i) => {
      const m = c.material as THREE.MeshBasicMaterial;
      m.map!.offset.x = t * (0.004 + i * 0.002);
      m.map!.offset.y = t * 0.0015 * (i + 1);
      m.color.copy(lerpC(0x5e636a, 0x151a26)).lerp(new THREE.Color(0xd0d8ff), L * 0.8);
    });

    this.ending(n);

    // rain
    const rm = this.rain.material as THREE.LineBasicMaterial;
    rm.opacity = (THREE.MathUtils.clamp((n - 0.3) / 0.7, 0, 1) * 0.22 + L * 0.15) * (1 - this.blood) * (1 - this.clear);
    this.rain.visible = rm.opacity > 0.01;
    if (this.rain.visible) {
      const N = this.rainVel.length;
      for (let i = 0; i < N; i++) {
        const o = i * 6;
        const dy = this.rainVel[i] * dt;
        this.rainPos[o + 1] -= dy;
        this.rainPos[o + 4] -= dy;
        this.rainPos[o] -= dy * 0.12;
        this.rainPos[o + 3] -= dy * 0.12;
        if (this.rainPos[o + 1] < 0) this.resetDrop(i);
      }
      this.rain.geometry.attributes.position.needsUpdate = true;
    }
  }

  /** Blend the ending skies over the usual weather. */
  private ending(n: number) {
    const c = this.clear;
    const b = this.blood;
    const toward = (col: THREE.Color, hex: number, k: number) => col.lerp(new THREE.Color(hex), k);
    const sk = this.skyMat.uniforms;
    if (c > 0) {
      toward(sk.top.value, 0x3f7fd0, c);
      toward(sk.bottom.value, 0x9cc8f0, c);
      toward(this.fog.color, 0xbcd6ec, c);
      this.fog.density = THREE.MathUtils.lerp(this.fog.density, 0.0045, c);
      toward(this.hemi.color, 0xcfe4ff, c);
      toward(this.hemi.groundColor, 0x6a5a3a, c);
      this.hemi.intensity = THREE.MathUtils.lerp(this.hemi.intensity, 2.1, c);
      toward(this.key.color, 0xffe2b0, c);
      this.key.intensity = THREE.MathUtils.lerp(this.key.intensity, 3.6, c);
    }
    if (b > 0) {
      toward(sk.top.value, 0x12040a, b);
      toward(sk.bottom.value, 0x4a1016, b);
      toward(this.fog.color, 0x2a0c12, b);
      toward(this.hemi.color, 0x8a3040, b * 0.8);
      this.hemi.intensity = THREE.MathUtils.lerp(this.hemi.intensity, 1.15, b);
      toward(this.key.color, 0xd05040, b * 0.8);
      this.key.intensity = THREE.MathUtils.lerp(this.key.intensity, 0.55, b);
    }
    this.clouds.forEach((cl) => {
      const m = cl.material as THREE.MeshBasicMaterial;
      m.opacity = 0.9 * (1 - c) * (1 - b * 0.6);
      if (b > 0) toward(m.color, 0x3a1016, b);
      if (this.fair > 0) {
        m.opacity = Math.max(m.opacity, 0.6 * this.fair);
        toward(m.color, 0xffffff, this.fair);
      }
      cl.visible = m.opacity > 0.01;
    });
    // the sun climbs out of the horizon as the fog lifts; the moon is simply there
    const rise = THREE.MathUtils.smoothstep(c, 0.1, 1);
    this.sun.visible = c > 0.01;
    this.sun.position.set(-26, THREE.MathUtils.lerp(-12, 27, rise), -150);
    setGlow(this.sun, c);
    this.moon.visible = b > 0.01;
    this.moon.position.set(22, 25, -150);
    setGlow(this.moon, b * n);
  }
}

/** A sky body: a bright disc with a soft halo, never fogged. */
function disc(core: number, halo: number, r: number): THREE.Group {
  const g = new THREE.Group();
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const ctx = c.getContext('2d')!;
  const grad = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, 'rgba(255,255,255,0.9)');
  grad.addColorStop(0.25, 'rgba(255,255,255,0.35)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 128, 128);
  const glow = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), color: halo, transparent: true, depthWrite: false, fog: false, blending: THREE.AdditiveBlending }),
  );
  glow.scale.setScalar(r * 5);
  const body = new THREE.Mesh(
    new THREE.CircleGeometry(r, 32),
    new THREE.MeshBasicMaterial({ color: core, transparent: true, fog: false, depthWrite: false }),
  );
  g.add(glow, body);
  g.visible = false;
  g.renderOrder = -1;
  return g;
}

function setGlow(g: THREE.Group, k: number) {
  const [glow, body] = g.children as [THREE.Sprite, THREE.Mesh];
  (glow.material as THREE.SpriteMaterial).opacity = k;
  (body.material as THREE.MeshBasicMaterial).opacity = k;
}
