import * as THREE from 'three';
import { batFrames, crowFrames, perchedCrow, pixelTexture, ratFrames } from './pixel';
import { billboardSprite } from './town';

/** Ambient critters on fixed routes / fixed rhythms (no AI, per brief). */
export class Animals {
  readonly group = new THREE.Group();
  readonly billboards: THREE.Object3D[] = [];
  private rats: { mesh: THREE.Mesh; r: number; a0: number; a1: number; speed: number; phase: number; tex: THREE.Texture[] }[] = [];
  private crows: { mesh: THREE.Mesh; center: THREE.Vector3; r: number; h: number; speed: number; phase: number; tex: THREE.Texture[] }[] = [];
  private perched: { mesh: THREE.Mesh; base: THREE.Vector3; period: number; phase: number }[] = [];
  private flies: { pts: THREE.Points; center: THREE.Vector3; seeds: Float32Array }[] = [];
  private bats: { mesh: THREE.Mesh; seed: number; tex: THREE.Texture[]; nextFlap: number }[] = [];
  private ratMoving: boolean[] = [];
  private nextCaw = 8;
  /** Sound hooks: world position of the critter making noise. */
  onSqueak?: (p: THREE.Vector3) => void;
  onFlap?: (p: THREE.Vector3) => void;
  onCaw?: (p: THREE.Vector3) => void;

  constructor(perches: THREE.Vector3[], fliesSpots: THREE.Vector3[]) {
    const ratTex = ratFrames().map((c) => pixelTexture(c));
    const routes = [
      { r: 13.6, a0: 0.3, a1: 1.6 },
      { r: 12.9, a0: 2.2, a1: 3.4 },
      { r: 13.8, a0: 4.0, a1: 5.3 },
      { r: 8.8, a0: 0.0, a1: 2.0 },
    ];
    routes.forEach((rt, i) => {
      const mesh = billboardSprite(ratTex[0], 0.55, 0.28);
      this.group.add(mesh);
      this.rats.push({ mesh, ...rt, speed: 0.12 + i * 0.03, phase: i * 1.7, tex: ratTex });
    });

    const crowTex = crowFrames().map((c) => pixelTexture(c));
    const circles = [
      { center: new THREE.Vector3(0, 0, -36), r: 9, h: 16, speed: 0.35 },
      { center: new THREE.Vector3(12, 0, -34), r: 6, h: 10, speed: -0.45 },
      { center: new THREE.Vector3(0, 0, 0), r: 14, h: 13, speed: 0.25 },
    ];
    circles.forEach((c, i) => {
      const mesh = billboardSprite(crowTex[0], 0.9, 0.6);
      mesh.castShadow = false;
      this.group.add(mesh);
      this.crows.push({ mesh, ...c, phase: i * 2.1, tex: crowTex });
    });

    const pc = pixelTexture(perchedCrow());
    perches.forEach((p, i) => {
      const mesh = billboardSprite(pc, 0.5, 0.5);
      mesh.position.copy(p);
      this.group.add(mesh);
      this.perched.push({ mesh, base: p.clone(), period: 3 + i * 1.3, phase: i });
    });

    for (const c of fliesSpots) {
      const N = 14;
      const seeds = new Float32Array(N * 3).map(() => Math.random() * 10);
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(N * 3), 3));
      const pts = new THREE.Points(g, new THREE.PointsMaterial({ color: 0x0a0a0a, size: 0.06, sizeAttenuation: true }));
      pts.frustumCulled = false;
      this.group.add(pts);
      this.flies.push({ pts, center: c, seeds });
    }

    const batTex = batFrames().map((c) => pixelTexture(c));
    for (let i = 0; i < 4; i++) {
      const mesh = billboardSprite(batTex[0], 0.7, 0.4);
      mesh.castShadow = false;
      mesh.visible = false;
      this.group.add(mesh);
      this.bats.push({ mesh, seed: i * 1.9 + 0.5, tex: batTex, nextFlap: 2 + i * 1.7 });
      this.billboards.push(mesh);
    }

    for (const r of this.rats) this.billboards.push(r.mesh);
    for (const c of this.crows) this.billboards.push(c.mesh);
    for (const p of this.perched) this.billboards.push(p.mesh);
  }

  update(t: number, camera: THREE.Camera, night: number) {
    const isNight = night > 0.5;
    // rats: ping-pong along an arc of the ring road; face travel direction
    for (const r of this.rats) {
      const u = (Math.sin(t * r.speed * 2 + r.phase) + 1) / 2; // 0..1
      const a = r.a0 + (r.a1 - r.a0) * u;
      const dir = Math.cos(t * r.speed * 2 + r.phase); // derivative sign
      r.mesh.position.set(Math.cos(a) * r.r, 0.02, Math.sin(a) * r.r);
      const mat = r.mesh.material as THREE.MeshStandardMaterial;
      mat.map = r.tex[Math.floor(t * 10) % 2];
      // screen-space facing: flip so the nose leads the motion
      const tangent = new THREE.Vector3(-Math.sin(a), 0, Math.cos(a)).multiplyScalar(Math.sign(dir) || 1);
      const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
      r.mesh.scale.x = tangent.dot(right) >= 0 ? 1 : -1;
      // pause occasionally (sniffing); squeak whenever it scurries off again
      const moving = Math.abs(dir) >= 0.15;
      if (!moving) mat.map = r.tex[0];
      const k = this.rats.indexOf(r);
      if (moving && this.ratMoving[k] === false) this.onSqueak?.(r.mesh.position);
      this.ratMoving[k] = moving;
    }
    // bats only come out at night: erratic figure-eights between the houses
    for (const b of this.bats) {
      b.mesh.visible = isNight;
      if (!isNight) continue;
      const s = b.seed;
      const a = t * (0.5 + s * 0.08) + s * 3;
      const r = 9 + Math.sin(t * 0.3 + s) * 4;
      b.mesh.position.set(Math.cos(a) * r + Math.sin(t * 1.7 + s) * 1.5, 4.5 + Math.sin(a * 2 + s) * 1.8 + Math.sin(t * 5 + s) * 0.3, Math.sin(a * 2) * r * 0.6 + Math.cos(t * 1.3 + s) * 1.5);
      (b.mesh.material as THREE.MeshStandardMaterial).map = b.tex[Math.floor(t * 14 + s * 5) % 2];
      if (t > b.nextFlap) {
        b.nextFlap = t + 3 + Math.random() * 6;
        this.onFlap?.(b.mesh.position);
      }
    }
    if (!isNight && t > this.nextCaw && this.crows.length) {
      this.nextCaw = t + 9 + Math.random() * 14;
      this.onCaw?.(this.crows[Math.floor(Math.random() * this.crows.length)].mesh.position);
    }
    for (const c of this.crows) {
      c.mesh.visible = !isNight;
      const a = t * c.speed + c.phase;
      c.mesh.position.set(c.center.x + Math.cos(a) * c.r, c.h + Math.sin(t * 0.7 + c.phase) * 0.8, c.center.z + Math.sin(a) * c.r);
      (c.mesh.material as THREE.MeshStandardMaterial).map = c.tex[Math.floor(t * 6 + c.phase) % 2];
      const tangent = new THREE.Vector3(-Math.sin(a), 0, Math.cos(a)).multiplyScalar(Math.sign(c.speed));
      const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
      c.mesh.scale.x = tangent.dot(right) >= 0 ? 1 : -1;
    }
    for (const p of this.perched) {
      const k = ((t + p.phase) % p.period) / p.period;
      const hop = k < 0.08 ? Math.sin((k / 0.08) * Math.PI) * 0.18 : 0;
      p.mesh.position.y = p.base.y + hop;
      p.mesh.scale.x = Math.floor((t + p.phase) / p.period) % 2 ? -1 : 1;
    }
    for (const f of this.flies) {
      const arr = f.pts.geometry.attributes.position.array as Float32Array;
      const n = arr.length / 3;
      for (let i = 0; i < n; i++) {
        const s0 = f.seeds[i * 3], s1 = f.seeds[i * 3 + 1], s2 = f.seeds[i * 3 + 2];
        arr[i * 3] = f.center.x + Math.sin(t * (3 + s0) + s1) * 0.6 + Math.sin(t * 11 + s2) * 0.08;
        arr[i * 3 + 1] = f.center.y + Math.sin(t * (2 + s1) + s2) * 0.3;
        arr[i * 3 + 2] = f.center.z + Math.cos(t * (2.5 + s2) + s0) * 0.6 + Math.cos(t * 13 + s1) * 0.08;
      }
      f.pts.geometry.attributes.position.needsUpdate = true;
    }
  }
}
