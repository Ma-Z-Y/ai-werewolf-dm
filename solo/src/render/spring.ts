import * as THREE from 'three';
import { Rng } from '../game/rng';
import { EXIT_PATH, RING_HOUSE_R, seatAngle, type TownRefs } from './town';

const LEAF = [0x5f9e3c, 0x74b248, 0x4f8a34, 0x86c25a];
const PETAL = [0xffb3cf, 0xffc8dc, 0xff9ec0, 0xfff0f4];
const FLOWER = [0xff6f91, 0xffd23f, 0xffffff, 0xb07cff, 0xff8a3d, 0x6fb7ff];

interface Bud {
  mesh: THREE.Object3D;
  /** When it opens, 0..1 along the grow-in. */
  at: number;
  size: number;
}

/**
 * 好人胜利's spring: the old tree by the well leafs out and blossoms (petals
 * drifting down), and little flowers open along the roads. Hidden until `grow`
 * goes above 0; `grow` 0..1 opens everything in a staggered wave.
 */
export class Spring {
  readonly group = new THREE.Group();
  grow = 0;
  private buds: Bud[] = [];
  private flowers: THREE.InstancedMesh;
  private stems: THREE.InstancedMesh;
  private spots: { pos: THREE.Vector3; at: number; size: number; tilt: number }[] = [];
  private petals: THREE.Points;
  private petalPos: Float32Array;
  private petalVel: Float32Array;
  private crown: THREE.Vector3;
  private bark: THREE.MeshStandardMaterial;
  private barkDead: THREE.Color;
  private m = new THREE.Matrix4();
  private q = new THREE.Quaternion();
  private e = new THREE.Euler();

  constructor(town: TownRefs) {
    const rng = new Rng(7);
    const { group: tree, height } = town.plazaTree;
    this.bark = town.plazaTree.bark;
    this.barkDead = this.bark.color.clone();
    this.crown = tree.position.clone().setY(height * 0.95);
    this.group.visible = false;

    // canopy: leafy clumps, then blossom over them
    const canopy = new THREE.Group();
    canopy.position.copy(this.crown);
    this.group.add(canopy);
    for (let k = 0; k < 16; k++) {
      const r = 0.6 + rng.next() * 0.6;
      const leaf = new THREE.Mesh(
        new THREE.IcosahedronGeometry(r, 0),
        new THREE.MeshStandardMaterial({ color: LEAF[k % LEAF.length], roughness: 0.9, flatShading: true }),
      );
      const a = rng.next() * Math.PI * 2;
      const d = rng.next() * 1.8;
      leaf.position.set(Math.cos(a) * d, (rng.next() - 0.35) * 1.6, Math.sin(a) * d);
      leaf.castShadow = true;
      canopy.add(leaf);
      this.buds.push({ mesh: leaf, at: 0.05 + (d / 1.8) * 0.3 + rng.next() * 0.1, size: 1 });
    }
    const blossomGeo = new THREE.OctahedronGeometry(0.16, 0);
    for (let k = 0; k < 70; k++) {
      const col = PETAL[k % PETAL.length];
      const b = new THREE.Mesh(blossomGeo, new THREE.MeshStandardMaterial({ color: col, emissive: col, emissiveIntensity: 0.25, roughness: 0.8, flatShading: true }));
      const a = rng.next() * Math.PI * 2;
      const up = rng.next();
      const d = 1.2 + rng.next() * 1.3;
      b.position.set(Math.cos(a) * d, (up - 0.3) * 2.2, Math.sin(a) * d);
      b.rotation.set(rng.next() * 3, rng.next() * 3, 0);
      canopy.add(b);
      this.buds.push({ mesh: b, at: 0.4 + rng.next() * 0.4, size: 0.8 + rng.next() * 0.7 });
    }
    for (const b of this.buds) b.mesh.scale.setScalar(0);

    // falling petals
    const P = 90;
    this.petalPos = new Float32Array(P * 3);
    this.petalVel = new Float32Array(P);
    for (let i = 0; i < P; i++) this.dropPetal(i, true);
    const pg = new THREE.BufferGeometry();
    pg.setAttribute('position', new THREE.BufferAttribute(this.petalPos, 3));
    this.petals = new THREE.Points(pg, new THREE.PointsMaterial({ color: 0xffc0d8, size: 0.14, transparent: true, opacity: 0, depthWrite: false }));
    this.petals.frustumCulled = false;
    this.group.add(this.petals);

    // roadside flowers: the plaza ring's outer verge, the church road and the path out of town
    const at = (p: THREE.Vector3, wave: number) => this.spots.push({ pos: p, at: wave, size: 0.8 + rng.next() * 0.6, tilt: (rng.next() - 0.5) * 0.4 });
    for (let k = 0; k < 260; k++) {
      const a = rng.next() * Math.PI * 2;
      // keep out of the houses' doorways
      const off = Math.abs(Math.atan2(Math.sin(a - seatAngle(0)), Math.cos(a - seatAngle(0))) % (Math.PI / 6));
      if (off < 0.09 || off > Math.PI / 6 - 0.09) continue;
      const r = 13.4 + rng.next() * (RING_HOUSE_R - 15.2);
      at(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r), 0.2 + rng.next() * 0.5);
    }
    for (let k = 0; k < 44; k++) {
      const side = k % 2 ? 1 : -1;
      const z = -14 - rng.next() * 22;
      at(new THREE.Vector3(side * (2 + rng.next() * 0.8), 0, z), 0.35 + ((-z - 14) / 22) * 0.45);
    }
    for (let k = 0; k < 48; k++) {
      const seg = Math.floor(rng.next() * (EXIT_PATH.length - 1));
      const a = EXIT_PATH[seg], b = EXIT_PATH[seg + 1];
      const p = a.clone().lerp(b, rng.next());
      const side = k % 2 ? 1 : -1;
      p.x += side * (1.4 + rng.next() * 0.7);
      at(p, 0.3 + (p.z / 62) * 0.55);
    }
    for (let k = 0; k < 40; k++) {
      // a few round the well
      const a = rng.next() * Math.PI * 2;
      const r = 1.7 + rng.next() * 0.9;
      at(new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r), 0.1 + rng.next() * 0.3);
    }
    const n = this.spots.length;
    this.flowers = new THREE.InstancedMesh(
      new THREE.OctahedronGeometry(0.2, 0),
      new THREE.MeshStandardMaterial({ roughness: 0.7, flatShading: true }),
      n,
    );
    const stemGeo = new THREE.BoxGeometry(0.05, 0.45, 0.05);
    stemGeo.translate(0, 0.225, 0);
    this.stems = new THREE.InstancedMesh(stemGeo, new THREE.MeshStandardMaterial({ color: 0x4f8a34, roughness: 1 }), n);
    const col = new THREE.Color();
    this.spots.forEach((_, i) => this.flowers.setColorAt(i, col.setHex(FLOWER[Math.floor(rng.next() * FLOWER.length)])));
    this.flowers.instanceColor!.needsUpdate = true;
    this.group.add(this.flowers, this.stems);
    this.place();
  }

  /** Back to the dead tree and bare verges. */
  reset() {
    this.grow = 0;
    this.group.visible = false;
    this.bark.color.copy(this.barkDead);
  }

  update(dt: number, t: number) {
    const g = this.grow;
    this.group.visible = g > 0;
    if (!this.group.visible) return;
    this.bark.color.copy(this.barkDead).lerp(new THREE.Color(0x5a4030), g);
    for (const b of this.buds) {
      const k = pop((g - b.at) / 0.25);
      b.mesh.scale.setScalar(k * b.size);
    }
    this.place(t);

    // petals, once the blossom is out
    const pm = this.petals.material as THREE.PointsMaterial;
    pm.opacity = THREE.MathUtils.clamp((g - 0.6) / 0.3, 0, 1) * 0.9;
    const P = this.petalVel.length;
    for (let i = 0; i < P; i++) {
      const o = i * 3;
      this.petalPos[o] += Math.sin(t * 1.3 + i) * dt * 0.5 + dt * 0.35;
      this.petalPos[o + 1] -= this.petalVel[i] * dt;
      this.petalPos[o + 2] += Math.cos(t * 1.1 + i * 0.7) * dt * 0.4;
      if (this.petalPos[o + 1] < 0.02) this.dropPetal(i);
    }
    this.petals.geometry.attributes.position.needsUpdate = true;
  }

  private place(t = 0) {
    this.spots.forEach((s, i) => {
      const k = pop((this.grow - s.at) / 0.2) * s.size;
      const sway = Math.sin(t * 1.6 + i) * 0.08;
      this.q.setFromEuler(this.e.set(s.tilt * 0.5, i, s.tilt + sway));
      this.m.compose(s.pos, this.q, new THREE.Vector3(k, k, k));
      this.stems.setMatrixAt(i, this.m);
      const head = new THREE.Vector3(0, 0.48 * k, 0).applyQuaternion(this.q).add(s.pos);
      this.m.compose(head, this.q, new THREE.Vector3(k, k * 0.7, k));
      this.flowers.setMatrixAt(i, this.m);
    });
    this.stems.instanceMatrix.needsUpdate = true;
    this.flowers.instanceMatrix.needsUpdate = true;
  }

  private dropPetal(i: number, anyHeight = false) {
    const a = Math.random() * Math.PI * 2;
    const d = Math.random() * 2.6;
    const o = i * 3;
    this.petalPos[o] = this.crown.x + Math.cos(a) * d;
    this.petalPos[o + 1] = anyHeight ? Math.random() * this.crown.y : this.crown.y + (Math.random() - 0.3) * 1.5;
    this.petalPos[o + 2] = this.crown.z + Math.sin(a) * d;
    this.petalVel[i] = 0.35 + Math.random() * 0.4;
  }
}

/** 0 → 1 with a little overshoot, like a bud popping open. */
function pop(x: number): number {
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  return 1 + 1.6 * Math.pow(x - 1, 3) + 0.6 * Math.pow(x - 1, 2);
}
