import * as THREE from 'three';
import { Rng } from '../game/rng';
import { graveCanvas, pixelTexture, surface, type Material } from './pixel';

export const RING_HOUSE_R = 16;
export const RING_PLAYER_R = 11.5;
export const CHURCH_POS = new THREE.Vector3(0, 0, -36);

/** Waypoints of the path exiles take out of town (south, away from the church). */
export const EXIT_PATH = [
  new THREE.Vector3(0, 0, 12.2),
  new THREE.Vector3(0, 0, 19),
  new THREE.Vector3(1.2, 0, 30),
  new THREE.Vector3(-0.8, 0, 44),
  new THREE.Vector3(0.6, 0, 62),
];

export function seatAngle(i: number) {
  // seat 1 at the south (towards the default camera), then clockwise from above
  return Math.PI / 2 + (i / 12) * Math.PI * 2 + Math.PI / 12;
}

export function seatPosition(i: number, r = RING_PLAYER_R): THREE.Vector3 {
  const a = seatAngle(i);
  return new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
}

function mat(kind: Material, repeat: [number, number], seed = 1, extra: THREE.MeshStandardMaterialParameters = {}) {
  const map = surface(kind, seed).clone();
  map.needsUpdate = true;
  map.repeat.set(...repeat);
  return new THREE.MeshStandardMaterial({ map, roughness: 0.95, metalness: 0, ...extra });
}

function shadowy<T extends THREE.Object3D>(o: T): T {
  o.traverse((c) => {
    if ((c as THREE.Mesh).isMesh) {
      c.castShadow = true;
      c.receiveShadow = true;
    }
  });
  return o;
}

/** A gable-roof prism: width along x, depth along z. */
function roofGeometry(w: number, d: number, h: number) {
  const shape = new THREE.Shape();
  shape.moveTo(-w / 2, 0);
  shape.lineTo(0, h);
  shape.lineTo(w / 2, 0);
  shape.lineTo(-w / 2, 0);
  const g = new THREE.ExtrudeGeometry(shape, { depth: d, bevelEnabled: false });
  g.translate(0, 0, -d / 2);
  return g;
}

export interface HouseRefs {
  group: THREE.Group;
  doorPivot: THREE.Group;
  windows: THREE.MeshStandardMaterial[];
  /** Window glow at night; false once the owner is gone. */
  lit: boolean;
  /** World position just outside / inside the door. */
  doorstep: THREE.Vector3;
  inside: THREE.Vector3;
  seal: THREE.Group;
  debris: THREE.Group;
  state: 'normal' | 'broken' | 'sealed' | 'ruined';
  /** Footprint and wall height, for the ruin left by a wolf's self-destruct. */
  size: { w: number; d: number; h: number };
}

export interface TownRefs {
  root: THREE.Group;
  houses: HouseRefs[];
  /** Church window glow (house windows live in `houses`). */
  windows: THREE.MeshStandardMaterial[];
  lanterns: THREE.PointLight[];
  lanternFlames: THREE.Mesh[];
  billboards: THREE.Object3D[];
  /** Positions where perched crows / flies can live. */
  perches: THREE.Vector3[];
  fliesSpots: THREE.Vector3[];
  /** The old dead tree by the well (it blossoms when the village wins). */
  plazaTree: { group: THREE.Group; height: number; bark: THREE.MeshStandardMaterial };
  well: THREE.Object3D;
}

export function buildTown(): TownRefs {
  const rng = new Rng(42);
  const root = new THREE.Group();
  const refs: Omit<TownRefs, 'plazaTree' | 'well'> = { root, houses: [], windows: [], lanterns: [], lanternFlames: [], billboards: [], perches: [], fliesSpots: [] };

  // ground
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(220, 220), mat('grass', [60, 60]));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  root.add(ground);

  // plaza (cobble disc) + ring road (dirt annulus)
  const plaza = new THREE.Mesh(new THREE.CircleGeometry(9, 48), mat('cobble', [6, 6]));
  plaza.rotation.x = -Math.PI / 2;
  plaza.position.y = 0.02;
  plaza.receiveShadow = true;
  root.add(plaza);
  const ring = new THREE.Mesh(new THREE.RingGeometry(9, 13.2, 64, 1), mat('dirt', [8, 8]));
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.015;
  ring.receiveShadow = true;
  root.add(ring);
  // road to the church (north, between seat 6 and 7 which face -z)
  const road = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 26), mat('dirt', [1, 8], 2));
  road.rotation.x = -Math.PI / 2;
  road.position.set(0, 0.012, -24);
  road.receiveShadow = true;
  root.add(road);

  // 出镇小路: a narrow dirt track south, between seat 12 and seat 1 (the exiles' way out)
  for (let k = 0; k < EXIT_PATH.length - 1; k++) {
    const a = EXIT_PATH[k], b = EXIT_PATH[k + 1];
    const len = a.distanceTo(b) + 0.6;
    const seg = new THREE.Mesh(new THREE.PlaneGeometry(2.2, len), mat('dirt', [0.6, len / 4], 3 + k));
    seg.rotation.x = -Math.PI / 2;
    seg.rotation.z = -Math.atan2(b.x - a.x, b.z - a.z);
    seg.position.set((a.x + b.x) / 2, 0.013, (a.z + b.z) / 2);
    seg.receiveShadow = true;
    root.add(seg);
  }

  // houses
  for (let i = 0; i < 12; i++) root.add(house(i, rng, refs));

  // fences between houses
  for (let i = 0; i < 12; i++) {
    const a = seatAngle(i) + Math.PI / 12;
    if (Math.abs(Math.abs(Math.sin(a)) - 1) < 0.02) continue; // leave the church road and the exit path open
    const p = new THREE.Vector3(Math.cos(a) * (RING_HOUSE_R + 0.5), 0, Math.sin(a) * (RING_HOUSE_R + 0.5));
    const f = fence(3.2, rng.next() < 0.5, rng);
    f.position.copy(p);
    f.rotation.y = -a;
    root.add(f);
  }

  // centre: well + dead tree + lanterns
  const well = wellMesh();
  root.add(well);
  const tree = deadTree(rng, 5.5);
  tree.position.set(4.6, 0, -3.6);
  root.add(tree);
  const plazaTree = { group: tree, height: 5.5, bark: (tree.children[0] as THREE.Mesh).material as THREE.MeshStandardMaterial };
  refs.perches.push(new THREE.Vector3(4.6 + 1.2, 4.3, -3.6));

  for (let k = 0; k < 6; k++) {
    const a = (k / 6) * Math.PI * 2 + 0.3;
    const lp = lantern(refs);
    lp.position.set(Math.cos(a) * 8.6, 0, Math.sin(a) * 8.6);
    root.add(lp);
  }

  // church + graveyard
  const church = churchMesh(refs);
  church.position.copy(CHURCH_POS);
  root.add(church);
  refs.perches.push(CHURCH_POS.clone().add(new THREE.Vector3(-2, 7.2, 3)));
  refs.perches.push(CHURCH_POS.clone().add(new THREE.Vector3(1.8, 7.2, 1)));

  const yard = new THREE.Group();
  yard.position.set(12, 0, -34);
  root.add(yard);
  for (let k = 0; k < 16; k++) {
    const g = billboardSprite(pixelTexture(graveCanvas(rng.next() < 0.5 ? 'cross' : 'stone', k)), 0.9, 1.2);
    g.position.set((k % 4) * 2.2 - 3.3 + rng.next() * 0.6, 0, Math.floor(k / 4) * 2.2 - 3.3 + rng.next() * 0.6);
    yard.add(g);
    refs.billboards.push(g);
  }
  for (let s = 0; s < 4; s++) {
    const f = fence(10.5, true, rng);
    const a = (s * Math.PI) / 2;
    f.position.set(Math.sin(a) * 5.4, 0, Math.cos(a) * 5.4);
    f.rotation.y = a;
    yard.add(f);
  }
  refs.fliesSpots.push(new THREE.Vector3(12, 0.8, -34), new THREE.Vector3(-2.5, 0.7, 2.5));
  refs.perches.push(new THREE.Vector3(12 + 5.4, 1.3, -34));

  // outskirts: dead trees, rocks, barrels
  for (let k = 0; k < 28; k++) {
    const a = rng.next() * Math.PI * 2;
    const r = 24 + rng.next() * 40;
    const p = new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
    if (Math.abs(p.x) < 4 && p.z < -12 && p.z > -42) continue;
    if (p.distanceTo(new THREE.Vector3(12, 0, -34)) < 8 || p.distanceTo(CHURCH_POS) < 9) continue;
    const t = deadTree(rng, 4 + rng.next() * 4);
    t.position.copy(p);
    root.add(t);
  }
  for (let k = 0; k < 14; k++) {
    const a = seatAngle(k % 12) + (rng.next() - 0.5) * 0.4;
    const r = RING_HOUSE_R - 2.6 + rng.next() * 0.6;
    const b = rng.next() < 0.6 ? barrel() : crate(rng);
    b.position.x = Math.cos(a + 0.2) * r;
    b.position.z = Math.sin(a + 0.2) * r;
    b.rotation.y = rng.next() * 6;
    root.add(b);
  }
  // distant hills
  for (let k = 0; k < 10; k++) {
    const a = (k / 10) * Math.PI * 2;
    const h = new THREE.Mesh(
      new THREE.SphereGeometry(18 + rng.next() * 10, 10, 6, 0, Math.PI * 2, 0, Math.PI / 2),
      new THREE.MeshStandardMaterial({ color: 0x2c3228, roughness: 1, flatShading: true }),
    );
    h.scale.y = 0.35 + rng.next() * 0.3;
    h.position.set(Math.cos(a) * 95, -1, Math.sin(a) * 95);
    root.add(h);
  }

  return { ...refs, plazaTree, well };
}

function house(i: number, rng: Rng, refs: Omit<TownRefs, 'plazaTree' | 'well'>): THREE.Group {
  const g = new THREE.Group();
  const w = 4.2 + rng.next() * 1.2;
  const d = 3.6 + rng.next() * 0.8;
  const h = 2.6 + rng.next() * 0.9;
  const wallMat = mat('plaster', [1, 1], i + 1);
  const walls = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), [wallMat, wallMat, wallMat, wallMat, wallMat, wallMat]);
  walls.position.y = h / 2;
  g.add(walls);
  // stone base
  const base = new THREE.Mesh(new THREE.BoxGeometry(w + 0.2, 0.5, d + 0.2), mat('stone', [2, 0.3], i));
  base.position.y = 0.25;
  g.add(base);
  // roof
  const roofH = 1.6 + rng.next() * 0.8;
  const roof = new THREE.Mesh(roofGeometry(w + 0.8, d + 0.6, roofH), mat(rng.next() < 0.5 ? 'roof' : 'thatch', [1.2, 1.2], i));
  roof.position.y = h;
  // 破败: some roofs sag
  roof.rotation.z = (rng.next() - 0.5) * 0.06;
  g.add(roof);
  // chimney
  if (rng.next() < 0.7) {
    const ch = new THREE.Mesh(new THREE.BoxGeometry(0.5, 1.6, 0.5), mat('stone', [0.5, 1], i + 3));
    ch.position.set(w * 0.25, h + roofH * 0.6, d * 0.2);
    g.add(ch);
  }
  // doorway (dark interior visible when the door swings open)
  const doorway = new THREE.Mesh(new THREE.PlaneGeometry(0.92, 1.62), new THREE.MeshBasicMaterial({ color: 0x070504 }));
  doorway.position.set(0, 0.8 + 0.25, d / 2 + 0.006);
  g.add(doorway);
  // door on a hinge at its left edge (faces +z locally; group rotated to face the plaza)
  const doorPivot = new THREE.Group();
  doorPivot.position.set(-0.45, 0.25, d / 2 + 0.035);
  const doorMat = mat('darkwood', [0.4, 0.6], i);
  const door = new THREE.Mesh(new THREE.BoxGeometry(0.9, 1.6, 0.06), doorMat);
  door.position.set(0.45, 0.8, 0);
  doorPivot.add(door);
  g.add(doorPivot);
  // 封条: two crossed paper strips (hidden until exile)
  const seal = new THREE.Group();
  const sealMat = new THREE.MeshStandardMaterial({ map: sealTexture(), roughness: 1, side: THREE.DoubleSide });
  for (const a of [0.9, -0.9]) {
    const strip = new THREE.Mesh(new THREE.PlaneGeometry(1.9, 0.24), sealMat);
    strip.rotation.z = a;
    strip.position.z = a > 0 ? 0 : 0.01;
    seal.add(strip);
  }
  seal.position.set(0, 1.05, d / 2 + 0.1);
  seal.visible = false;
  g.add(seal);
  // smashed door (hidden until killed): the door lies torn off on the ground,
  // splintered boards hang in the frame, claw marks gouge the wall, blood on the step
  const debris = new THREE.Group();
  const fallen = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.06, 1.6), doorMat);
  fallen.position.set(0.25, 0.04, d / 2 + 1.05);
  fallen.rotation.set(0, 0.5, 0.06);
  debris.add(fallen);
  for (const [x, y, rz] of [[-0.3, 1.5, 0.9], [0.25, 0.55, -0.7]] as const) {
    const shard = new THREE.Mesh(new THREE.BoxGeometry(0.75, 0.12, 0.05), doorMat);
    shard.position.set(x, y, d / 2 + 0.05);
    shard.rotation.z = rz;
    debris.add(shard);
  }
  for (let k = 0; k < 6; k++) {
    const plank = new THREE.Mesh(new THREE.BoxGeometry(0.35 + rng.next() * 0.5, 0.05, 0.12), doorMat);
    plank.position.set((rng.next() - 0.5) * 2.2, 0.03, d / 2 + 0.5 + rng.next() * 1.6);
    plank.rotation.y = rng.next() * Math.PI;
    debris.add(plank);
  }
  const clawMat = new THREE.MeshBasicMaterial({ map: clawTexture(), transparent: true, depthWrite: false });
  for (const x of [-0.95, 0.95]) {
    const claw = new THREE.Mesh(new THREE.PlaneGeometry(0.85, 1.05), clawMat);
    claw.position.set(x, 1.35, d / 2 + 0.02);
    claw.scale.x = x < 0 ? -1 : 1;
    debris.add(claw);
  }
  const blood = new THREE.Mesh(new THREE.CircleGeometry(0.7, 10), new THREE.MeshBasicMaterial({ color: 0x4a0a08, transparent: true, opacity: 0.8, depthWrite: false }));
  blood.rotation.x = -Math.PI / 2;
  blood.position.set(-0.2, 0.03, d / 2 + 0.6);
  blood.scale.set(1.3, 0.8, 1);
  debris.add(blood);
  debris.visible = false;
  g.add(debris);
  const houseWindows: THREE.MeshStandardMaterial[] = [];
  // windows (emissive at night; some boarded)
  for (const x of [-w / 3.2, w / 3.2]) {
    const boarded = rng.next() < 0.3;
    const wm = new THREE.MeshStandardMaterial({
      color: boarded ? 0x3a2a1e : 0x1a1612,
      emissive: new THREE.Color(0xffa54a),
      emissiveIntensity: 0,
      roughness: 0.6,
    });
    if (!boarded) houseWindows.push(wm);
    const win = new THREE.Mesh(new THREE.PlaneGeometry(0.7, 0.7), wm);
    win.position.set(x, h * 0.6, d / 2 + 0.02);
    g.add(win);
    const frame = new THREE.Mesh(new THREE.PlaneGeometry(0.8, 0.08), new THREE.MeshStandardMaterial({ color: 0x2a1c12 }));
    frame.position.set(x, h * 0.6, d / 2 + 0.03);
    g.add(frame);
    if (boarded) {
      const plank = new THREE.Mesh(new THREE.PlaneGeometry(0.9, 0.14), mat('wood', [0.3, 0.1], i));
      plank.position.set(x, h * 0.6, d / 2 + 0.04);
      plank.rotation.z = 0.5;
      g.add(plank);
    }
  }
  // house number sign
  const sign = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 0.45), new THREE.MeshStandardMaterial({ map: numberSign(i + 1), roughness: 1 }));
  sign.position.set(0.8, 1.9, d / 2 + 0.03);
  g.add(sign);

  const p = seatPosition(i, RING_HOUSE_R + d / 2 - 0.4);
  g.position.copy(p);
  // face the plaza: local +z should point at the centre
  g.rotation.y = Math.atan2(-p.x, -p.z);
  g.updateMatrixWorld(true);
  shadowy(g);
  refs.houses.push({
    group: g,
    doorPivot,
    windows: houseWindows,
    lit: true,
    doorstep: g.localToWorld(new THREE.Vector3(0.1, 0, d / 2 + 0.9)),
    inside: g.localToWorld(new THREE.Vector3(0.1, 0, d / 2 - 0.5)),
    seal,
    debris,
    state: 'normal',
    size: { w, d, h },
  });
  return g;
}

/** 封条 strip: aged paper with red stamp characters. */
function sealTexture() {
  const c = document.createElement('canvas');
  c.width = 64;
  c.height = 8;
  const ctx = c.getContext('2d')!;
  ctx.fillStyle = '#e8dcc0';
  ctx.fillRect(0, 0, 64, 8);
  ctx.fillStyle = 'rgba(120,100,70,0.35)';
  for (let x = 0; x < 64; x += 5) ctx.fillRect(x, (x * 7) % 8, 2, 1);
  ctx.fillStyle = '#b02a20';
  ctx.font = 'bold 7px serif';
  ctx.textBaseline = 'middle';
  ctx.fillText('封　　封　　封', 4, 4.5);
  return pixelTexture(c);
}

/** Claw marks gouged into the wall beside a broken door. */
function clawTexture() {
  const c = document.createElement('canvas');
  c.width = 16;
  c.height = 20;
  const ctx = c.getContext('2d')!;
  for (let k = 0; k < 4; k++) {
    for (let y = 0; y < 16; y++) {
      const x = 2 + k * 3 + Math.floor(y / 4);
      ctx.fillStyle = 'rgba(14,6,4,0.95)';
      ctx.fillRect(x, 2 + y, 1, 1);
      ctx.fillStyle = 'rgba(150,24,18,0.75)';
      ctx.fillRect(x + 1, 2 + y, 1, 1);
    }
  }
  ctx.fillStyle = 'rgba(140,18,14,0.8)';
  ctx.fillRect(5, 18, 2, 2);
  ctx.fillRect(10, 17, 1, 3);
  return pixelTexture(c);
}

function numberSign(n: number) {
  const c = document.createElement('canvas');
  c.width = 24;
  c.height = 18;
  const ctx = c.getContext('2d')!;
  ctx.fillStyle = '#5a4230';
  ctx.fillRect(0, 0, 24, 18);
  ctx.fillStyle = '#3a2a1e';
  ctx.fillRect(0, 16, 24, 2);
  ctx.fillStyle = '#e8d8b0';
  ctx.font = 'bold 13px monospace';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(String(n), 12, 9);
  return pixelTexture(c);
}

function fence(len: number, broken: boolean, rng: Rng): THREE.Group {
  const g = new THREE.Group();
  const m = mat('wood', [0.2, 1], 5);
  const posts = Math.max(2, Math.round(len / 1.1));
  for (let k = 0; k <= posts; k++) {
    if (broken && rng.next() < 0.15) continue;
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.14, 1.1 + rng.next() * 0.2, 0.14), m);
    post.position.set(-len / 2 + (k * len) / posts, 0.55, 0);
    post.rotation.z = (rng.next() - 0.5) * (broken ? 0.3 : 0.08);
    g.add(post);
  }
  for (const y of [0.4, 0.85]) {
    if (broken && rng.next() < 0.3) continue;
    const rail = new THREE.Mesh(new THREE.BoxGeometry(len, 0.1, 0.06), m);
    rail.position.set(0, y, 0);
    rail.rotation.z = (rng.next() - 0.5) * (broken ? 0.1 : 0.02);
    g.add(rail);
  }
  return shadowy(g);
}

function wellMesh(): THREE.Group {
  const g = new THREE.Group();
  const ring = new THREE.Mesh(new THREE.CylinderGeometry(1.2, 1.3, 0.9, 12, 1, true), mat('stone', [3, 0.5], 9, { side: THREE.DoubleSide }));
  ring.position.y = 0.45;
  g.add(ring);
  const water = new THREE.Mesh(new THREE.CircleGeometry(1.15, 12), new THREE.MeshStandardMaterial({ color: 0x0c1418, roughness: 0.1, metalness: 0.3 }));
  water.rotation.x = -Math.PI / 2;
  water.position.y = 0.3;
  g.add(water);
  const pm = mat('darkwood', [0.2, 1], 3);
  for (const x of [-1.1, 1.1]) {
    const post = new THREE.Mesh(new THREE.BoxGeometry(0.16, 2.2, 0.16), pm);
    post.position.set(x, 1.1, 0);
    g.add(post);
  }
  const roof = new THREE.Mesh(roofGeometry(2.8, 1.6, 0.7), mat('roof', [0.6, 0.4], 9));
  roof.position.y = 2.2;
  g.add(roof);
  const bar = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 2.3, 6), pm);
  bar.rotation.z = Math.PI / 2;
  bar.position.y = 1.7;
  g.add(bar);
  return shadowy(g);
}

function deadTree(rng: Rng, height: number): THREE.Group {
  const g = new THREE.Group();
  const m = new THREE.MeshStandardMaterial({ color: 0x2e2620, roughness: 1, flatShading: true });
  const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.32, height, 5), m);
  trunk.position.y = height / 2;
  trunk.rotation.z = (rng.next() - 0.5) * 0.15;
  g.add(trunk);
  for (let k = 0; k < 5; k++) {
    const len = 0.8 + rng.next() * 1.6;
    const br = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.09, len, 4), m);
    const y = height * (0.45 + rng.next() * 0.5);
    const a = rng.next() * Math.PI * 2;
    br.position.set(Math.cos(a) * len * 0.4, y + len * 0.3, Math.sin(a) * len * 0.4);
    br.rotation.set(Math.sin(a) * 0.9, 0, -Math.cos(a) * 0.9);
    g.add(br);
  }
  return shadowy(g);
}

function barrel(): THREE.Mesh {
  const b = new THREE.Mesh(new THREE.CylinderGeometry(0.38, 0.34, 0.9, 10), mat('wood', [1.5, 0.5], 8));
  b.position.y = 0.45;
  return shadowy(b);
}

function crate(rng: Rng): THREE.Mesh {
  const s = 0.6 + rng.next() * 0.3;
  const c = new THREE.Mesh(new THREE.BoxGeometry(s, s, s), mat('wood', [0.5, 0.5], 4));
  c.position.y = s / 2;
  return shadowy(c);
}

function lantern(refs: Omit<TownRefs, 'plazaTree' | 'well'>): THREE.Group {
  const g = new THREE.Group();
  const post = new THREE.Mesh(new THREE.BoxGeometry(0.14, 2.6, 0.14), new THREE.MeshStandardMaterial({ color: 0x1e1814 }));
  post.position.y = 1.3;
  g.add(post);
  const cage = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.42, 0.34), new THREE.MeshStandardMaterial({ color: 0x1a1512, transparent: true, opacity: 0.55 }));
  cage.position.y = 2.75;
  g.add(cage);
  const flame = new THREE.Mesh(
    new THREE.BoxGeometry(0.16, 0.24, 0.16),
    new THREE.MeshStandardMaterial({ color: 0x000000, emissive: 0xffb050, emissiveIntensity: 3 }),
  );
  flame.position.y = 2.72;
  g.add(flame);
  refs.lanternFlames.push(flame);
  const light = new THREE.PointLight(0xff9a40, 0, 11, 1.6);
  light.position.y = 2.75;
  g.add(light);
  refs.lanterns.push(light);
  post.castShadow = true;
  return g;
}

function churchMesh(refs: Omit<TownRefs, 'plazaTree' | 'well'>): THREE.Group {
  const g = new THREE.Group();
  const stone = mat('stone', [3, 2], 11);
  const nave = new THREE.Mesh(new THREE.BoxGeometry(8, 6, 13), stone);
  nave.position.set(0, 3, 0);
  g.add(nave);
  const roof = new THREE.Mesh(roofGeometry(9, 14, 3.6), mat('roof', [3, 2], 11));
  roof.position.y = 6;
  g.add(roof);
  const tower = new THREE.Mesh(new THREE.BoxGeometry(3.6, 11, 3.6), mat('stone', [1.5, 4], 12));
  tower.position.set(0, 5.5, 7.4);
  g.add(tower);
  const spire = new THREE.Mesh(new THREE.ConeGeometry(2.7, 6, 4), mat('roof', [1, 2], 12));
  spire.position.set(0, 14, 7.4);
  spire.rotation.y = Math.PI / 4;
  g.add(spire);
  // broken cross on top
  const cm = new THREE.MeshStandardMaterial({ color: 0x2a2420 });
  const cv = new THREE.Mesh(new THREE.BoxGeometry(0.2, 1.4, 0.2), cm);
  cv.position.set(0, 17.6, 7.4);
  cv.rotation.z = 0.2;
  g.add(cv);
  const chh = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.18, 0.18), cm);
  chh.position.set(-0.1, 17.8, 7.4);
  chh.rotation.z = 0.2;
  g.add(chh);
  // stained-glass window & door facing the town (+z)
  const glass = new THREE.MeshStandardMaterial({ color: 0x201826, emissive: new THREE.Color(0xc05a9a), emissiveIntensity: 0 });
  refs.windows.push(glass);
  const rose = new THREE.Mesh(new THREE.CircleGeometry(0.9, 12), glass);
  rose.position.set(0, 7.2, 9.22);
  g.add(rose);
  const door = new THREE.Mesh(new THREE.PlaneGeometry(1.6, 2.8), mat('darkwood', [0.5, 0.8], 12));
  door.position.set(0, 1.4, 9.22);
  g.add(door);
  for (const x of [-4.02, 4.02]) {
    for (const z of [-4, 0, 4]) {
      const wm = new THREE.MeshStandardMaterial({ color: 0x181420, emissive: new THREE.Color(0x6a8ad0), emissiveIntensity: 0 });
      refs.windows.push(wm);
      const w = new THREE.Mesh(new THREE.PlaneGeometry(1, 2.2), wm);
      w.position.set(x, 3.6, z);
      w.rotation.y = x < 0 ? -Math.PI / 2 : Math.PI / 2;
      g.add(w);
    }
  }
  return shadowy(g);
}

/** Vertical plane that `Stage` rotates to face the camera around Y. */
export function billboardSprite(tex: THREE.Texture, w: number, h: number): THREE.Mesh {
  const geo = new THREE.PlaneGeometry(w, h);
  geo.translate(0, h / 2, 0);
  const m = new THREE.MeshStandardMaterial({ map: tex, alphaTest: 0.5, transparent: false, roughness: 1, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(geo, m);
  mesh.castShadow = true;
  mesh.receiveShadow = false;
  mesh.customDepthMaterial = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking, map: tex, alphaTest: 0.5 });
  return mesh;
}
