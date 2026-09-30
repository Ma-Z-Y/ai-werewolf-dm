import * as THREE from 'three';
import { Rng } from '../game/rng';

/** Procedurally drawn pixel-art textures (no external assets). */

type Ctx = CanvasRenderingContext2D;

function canvas(w: number, h: number): [HTMLCanvasElement, Ctx] {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const ctx = c.getContext('2d')!;
  ctx.imageSmoothingEnabled = false;
  return [c, ctx];
}

export function pixelTexture(c: HTMLCanvasElement, repeat = false): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c);
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestFilter;
  t.generateMipmaps = false;
  t.colorSpace = THREE.SRGBColorSpace;
  if (repeat) t.wrapS = t.wrapT = THREE.RepeatWrapping;
  return t;
}

const hex = (r: number, g: number, b: number) => `rgb(${r | 0},${g | 0},${b | 0})`;

function jitter(base: [number, number, number], amt: number, rng: Rng): string {
  const k = (rng.next() - 0.5) * amt;
  return hex(base[0] + k, base[1] + k, base[2] + k);
}

function noiseFill(ctx: Ctx, w: number, h: number, base: [number, number, number], amt: number, rng: Rng) {
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    ctx.fillStyle = jitter(base, amt, rng);
    ctx.fillRect(x, y, 1, 1);
  }
}

export type Material = 'grass' | 'dirt' | 'cobble' | 'plaster' | 'wood' | 'roof' | 'stone' | 'thatch' | 'darkwood';

const cache = new Map<string, THREE.CanvasTexture>();

export function surface(kind: Material, seed = 1): THREE.CanvasTexture {
  const key = `${kind}:${seed}`;
  if (cache.has(key)) return cache.get(key)!;
  const rng = new Rng(seed * 977 + kind.length * 13);
  const S = 32;
  const [c, ctx] = canvas(S, S);
  switch (kind) {
    case 'grass': {
      noiseFill(ctx, S, S, [58, 66, 44], 18, rng);
      for (let i = 0; i < 40; i++) {
        ctx.fillStyle = rng.next() < 0.5 ? '#4a5536' : '#6b6a45';
        ctx.fillRect(rng.int(S), rng.int(S), 1, 2);
      }
      for (let i = 0; i < 6; i++) {
        ctx.fillStyle = '#5b4d3a';
        ctx.fillRect(rng.int(S), rng.int(S), 2, 1);
      }
      break;
    }
    case 'dirt': {
      noiseFill(ctx, S, S, [92, 76, 58], 16, rng);
      for (let i = 0; i < 14; i++) {
        ctx.fillStyle = rng.next() < 0.5 ? '#6f6254' : '#4c3e31';
        ctx.fillRect(rng.int(S), rng.int(S), 1 + rng.int(2), 1);
      }
      for (let i = 0; i < 6; i++) {
        ctx.fillStyle = 'rgba(40,48,58,0.45)'; // puddles
        const x = rng.int(S), y = rng.int(S);
        ctx.fillRect(x, y, 3 + rng.int(3), 1);
      }
      break;
    }
    case 'cobble':
    case 'stone': {
      const big = kind === 'stone';
      ctx.fillStyle = big ? '#3b3a3c' : '#3a3834';
      ctx.fillRect(0, 0, S, S);
      const bh = big ? 8 : 6;
      for (let y = 0; y < S; y += bh) {
        const off = (y / bh) % 2 ? (big ? 8 : 4) : 0;
        const bw = big ? 16 : 8;
        for (let x = -off; x < S; x += bw) {
          const base: [number, number, number] = big ? [104, 102, 104] : [96, 92, 84];
          const k = (rng.next() - 0.5) * 30;
          for (let yy = 1; yy < bh - 1; yy++) for (let xx = 1; xx < bw - 1; xx++) {
            const px = x + xx;
            if (px < 0 || px >= S) continue;
            const kk = k + (rng.next() - 0.5) * 12 - (yy === bh - 2 ? 14 : 0) + (yy === 1 ? 10 : 0);
            ctx.fillStyle = hex(base[0] + kk, base[1] + kk, base[2] + kk);
            ctx.fillRect(px, y + yy, 1, 1);
          }
          if (rng.next() < 0.25) {
            ctx.fillStyle = '#4d5a3a'; // moss
            ctx.fillRect(Math.max(0, x + 1 + rng.int(bw - 3)), y + bh - 2, 2, 1);
          }
        }
      }
      break;
    }
    case 'plaster': {
      noiseFill(ctx, S, S, [156, 144, 122], 14, rng);
      // stains
      for (let i = 0; i < 10; i++) {
        ctx.fillStyle = 'rgba(70,60,50,0.25)';
        ctx.fillRect(rng.int(S), rng.int(S), 2 + rng.int(4), 1 + rng.int(3));
      }
      // half-timber frame
      ctx.fillStyle = '#3e2c20';
      ctx.fillRect(0, 0, S, 2);
      ctx.fillRect(0, S - 2, S, 2);
      ctx.fillRect(0, 0, 2, S);
      ctx.fillRect(S / 2 - 1, 0, 2, S);
      for (let i = 0; i < S / 2; i++) {
        ctx.fillRect(2 + i, S - 2 - i * 1.8, 2, 2);
      }
      break;
    }
    case 'wood':
    case 'darkwood': {
      const base: [number, number, number] = kind === 'wood' ? [104, 76, 52] : [62, 46, 36];
      for (let y = 0; y < S; y++) {
        const plank = Math.floor(y / 6);
        const pk = ((plank * 37) % 11) - 5;
        for (let x = 0; x < S; x++) {
          const k = pk * 2 + (rng.next() - 0.5) * 10 + (Math.sin(x * 0.7 + plank) > 0.8 ? -10 : 0);
          ctx.fillStyle = hex(base[0] + k, base[1] + k, base[2] + k);
          ctx.fillRect(x, y, 1, 1);
        }
        if (y % 6 === 5) {
          ctx.fillStyle = '#22170f';
          ctx.fillRect(0, y, S, 1);
        }
      }
      for (let i = 0; i < 4; i++) {
        ctx.fillStyle = '#1c130c';
        ctx.fillRect(rng.int(S), rng.int(S), 1, 1);
      }
      break;
    }
    case 'roof': {
      ctx.fillStyle = '#231a18';
      ctx.fillRect(0, 0, S, S);
      for (let y = 0; y < S; y += 4) {
        const off = (y / 4) % 2 ? 3 : 0;
        for (let x = -off; x < S; x += 6) {
          const k = (rng.next() - 0.5) * 22;
          const missing = rng.next() < 0.05;
          for (let yy = 0; yy < 3; yy++) for (let xx = 0; xx < 5; xx++) {
            if (x + xx < 0) continue;
            ctx.fillStyle = missing ? '#15100e' : hex(86 + k - yy * 8, 50 + k - yy * 6, 42 + k - yy * 6);
            ctx.fillRect(x + xx, y + yy, 1, 1);
          }
        }
      }
      break;
    }
    case 'thatch': {
      for (let y = 0; y < S; y++) for (let x = 0; x < S; x++) {
        const k = (rng.next() - 0.5) * 30 + (y % 5 === 4 ? -24 : 0);
        ctx.fillStyle = hex(112 + k, 94 + k, 58 + k);
        ctx.fillRect(x, y, 1, 1);
      }
      break;
    }
  }
  const t = pixelTexture(c, true);
  cache.set(key, t);
  return t;
}

// ───────────────────────── characters ─────────────────────────

/** A character's outfit. Each persona has a fixed look that matches their trade. */
export interface Look {
  skin: string;
  hair: string;
  hairStyle: 'short' | 'long' | 'bun' | 'braid' | 'bald';
  beard: 'none' | 'full' | 'mustache' | 'stubble';
  beardColor?: string;
  head: 'none' | 'hood' | 'wimple' | 'strawHat' | 'featherCap' | 'helmet' | 'cap';
  headColor?: string;
  body: 'tunic' | 'dress' | 'habit' | 'apron' | 'armor' | 'robe' | 'cloak';
  main: string;
  dark: string;
  accent: string;
  pants: string;
}

/** Frames in a walk sheet: standing, left foot up, right foot up. */
export const WALK_FRAMES = 3;

/** A texture showing one frame of a horizontal sheet; `setFrame` flips between them. */
export function sheetTexture(c: HTMLCanvasElement, frames = WALK_FRAMES): THREE.CanvasTexture {
  const t = pixelTexture(c);
  t.repeat.set(1 / frames, 1);
  return t;
}

export function setFrame(t: THREE.Texture, frame: number, frames = WALK_FRAMES) {
  t.offset.x = frame / frames;
}

function sheet(frames: HTMLCanvasElement[]): HTMLCanvasElement {
  const [c, ctx] = canvas(frames[0].width * frames.length, frames[0].height);
  frames.forEach((f, k) => ctx.drawImage(f, k * f.width, 0));
  return c;
}

/** The standing frame and two stepping frames of a villager, side by side. */
export function characterSheet(l: Look): HTMLCanvasElement {
  return sheet([0, 1, 2].map((f) => characterCanvas(l, f)));
}

/**
 * 16×24 villager sprite built from a Look, with a 1px dark outline.
 * `frame` 1 / 2: mid-stride, that side's foot lifted and the opposite arm swung forward.
 */
export function characterCanvas(l: Look, frame = 0): HTMLCanvasElement {
  const W = 16, H = 24;
  const [c, ctx] = canvas(W, H);
  // lift of the left / right foot (2px), arm swing (hand 1px up = swung forward)
  const liftL = frame === 1 ? 2 : 0;
  const liftR = frame === 2 ? 2 : 0;
  const r = (x: number, y: number, w: number, h: number, col: string) => {
    ctx.fillStyle = col;
    ctx.fillRect(x, y, w, h);
  };
  const long = l.body === 'dress' || l.body === 'habit' || l.body === 'robe';

  // legs & boots (hidden under long garments)
  if (!long) {
    r(5, 18, 2, 4 - liftL, l.pants);
    r(9, 18, 2, 4 - liftR, l.pants);
  }
  r(4, 22 - liftL, 3, 2, '#1e1712');
  r(9, 22 - liftR, 3, 2, '#1e1712');

  // body
  if (long) {
    r(4, 10, 8, 12, l.main);
    r(10, 10, 2, 12, l.dark);
    // flared hem, swinging with the stride
    r(3 - (liftR >> 1), 18, 1, 4, l.main);
    r(12 + (liftL >> 1), 18, 1, 4, l.dark);
  } else if (l.body === 'cloak') {
    r(3, 10, 10, 10, l.main);
    r(10, 10, 3, 10, l.dark);
    r(7, 10, 2, 9, l.accent); // tunic peeking through
  } else {
    r(4, 10, 8, 9, l.main);
    r(10, 10, 2, 9, l.dark);
  }
  if (l.body === 'armor') {
    r(4, 10, 8, 9, '#8a8e96');
    r(10, 10, 2, 9, '#5e626a');
    r(5, 11, 1, 3, '#c4c8d0'); // highlight
    r(6, 12, 4, 7, l.accent); // tabard
    r(7, 13, 2, 1, '#e8d8a0'); // emblem
  }
  if (l.body === 'apron') r(5, 12, 6, 7, l.accent);
  if (l.body === 'habit') {
    r(5, 10, 6, 2, '#e8e4dc'); // white collar
    r(7, 12, 2, 5, '#c8a24a'); // cross pendant
    r(6, 13, 4, 1, '#c8a24a');
  }
  if (l.body === 'dress') r(5, 15, 6, 6, l.accent); // apron over the dress
  if (l.body === 'robe') r(7, 10, 2, 12, l.accent); // sash
  if (!long && l.body !== 'armor') {
    r(4, 15, 8, 1, '#2a1d14'); // belt
    r(7, 15, 1, 1, '#c8a24a');
  }

  // arms
  const sleeve = l.body === 'armor' ? '#5e626a' : l.dark;
  // the arm opposite the lifted foot swings forward (its hand rides 1px higher)
  const swingL = liftR >> 1;
  const swingR = liftL >> 1;
  r(3, 11, 1, 6 - swingL, sleeve);
  r(12, 11, 1, 6 - swingR, sleeve);
  r(3, 17 - swingL, 1, 1, l.skin);
  r(12, 17 - swingR, 1, 1, l.skin);

  // head
  r(5, 3, 6, 7, l.skin);
  r(10, 4, 1, 6, 'rgba(0,0,0,0.12)');
  r(6, 6, 1, 1, '#1a1210');
  r(9, 6, 1, 1, '#1a1210');

  // hair
  const hc = l.hair;
  if (l.hairStyle !== 'bald' && l.head !== 'wimple' && l.head !== 'helmet' && l.head !== 'hood') {
    r(5, 2, 6, 2, hc);
    r(4, 3, 1, 3, hc);
    r(11, 3, 1, 3, hc);
    if (l.hairStyle === 'long') {
      r(4, 3, 1, 8, hc);
      r(11, 3, 1, 8, hc);
    }
    if (l.hairStyle === 'bun') r(6, 0, 4, 2, hc);
    if (l.hairStyle === 'braid') {
      r(11, 3, 1, 5, hc);
      r(12, 7, 1, 5, hc);
      r(12, 12, 1, 1, '#c8a24a');
    }
  }
  if (l.hairStyle === 'bald' && l.head === 'none') {
    r(5, 2, 6, 1, l.skin);
    r(4, 4, 1, 2, hc); // fringe above the ears
    r(11, 4, 1, 2, hc);
  }

  // facial hair
  const bc = l.beardColor ?? hc;
  if (l.beard === 'full') {
    r(5, 7, 6, 3, bc);
    r(6, 10, 4, 1, bc);
    r(7, 8, 2, 1, '#6a3a30'); // mouth
  } else if (l.beard === 'mustache') {
    r(6, 8, 4, 1, bc);
  } else if (l.beard === 'stubble') {
    r(5, 8, 6, 2, 'rgba(40,30,25,0.35)');
  } else {
    r(7, 8, 2, 1, 'rgba(120,50,40,0.6)');
  }

  // headwear
  const hw = l.headColor ?? l.main;
  switch (l.head) {
    case 'hood':
      r(4, 1, 8, 3, hw);
      r(4, 1, 1, 9, hw);
      r(11, 1, 1, 9, l.dark);
      r(5, 0, 6, 1, hw);
      break;
    case 'wimple':
      r(4, 2, 8, 1, '#e8e4dc');
      r(4, 3, 1, 7, '#e8e4dc');
      r(11, 3, 1, 7, '#e8e4dc');
      r(5, 9, 6, 1, '#e8e4dc');
      r(3, 1, 10, 2, hw); // black veil
      r(3, 3, 1, 9, hw);
      r(12, 3, 1, 9, hw);
      r(4, 0, 8, 1, hw);
      break;
    case 'strawHat':
      r(2, 3, 12, 1, '#c8a860');
      r(5, 0, 6, 3, '#d8b870');
      r(5, 2, 6, 1, '#8a5a30');
      break;
    case 'featherCap':
      r(4, 1, 8, 2, hw);
      r(11, 0, 1, 1, '#e8e0c8');
      r(12, 0, 2, 1, '#e8e0c8');
      r(12, 0, 1, 3, '#c83a2a'); // feather
      r(13, 0, 1, 2, '#c83a2a');
      break;
    case 'helmet':
      r(4, 1, 8, 3, '#8a8e96');
      r(3, 4, 10, 1, '#6e727a');
      r(5, 1, 2, 1, '#c4c8d0');
      r(7, 0, 2, 1, '#6e727a');
      break;
    case 'cap':
      r(5, 1, 6, 2, hw);
      r(4, 3, 8, 1, hw);
      break;
  }
  outline(ctx, W, H, '#120d0b');
  return c;
}

function outline(ctx: Ctx, W: number, H: number, col: string) {
  const img = ctx.getImageData(0, 0, W, H);
  const a = (x: number, y: number) => (x < 0 || y < 0 || x >= W || y >= H ? 0 : img.data[(y * W + x) * 4 + 3]);
  const pts: [number, number][] = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    if (a(x, y)) continue;
    if (a(x - 1, y) || a(x + 1, y) || a(x, y - 1) || a(x, y + 1)) pts.push([x, y]);
  }
  ctx.fillStyle = col;
  for (const [x, y] of pts) ctx.fillRect(x, y, 1, 1);
}

// ───────────────────────── small sprites ─────────────────────────

export function crowFrames(): HTMLCanvasElement[] {
  const frames: HTMLCanvasElement[] = [];
  for (let f = 0; f < 2; f++) {
    const [c, ctx] = canvas(12, 8);
    ctx.fillStyle = '#121216';
    ctx.fillRect(4, 3, 5, 2); // body
    ctx.fillRect(9, 2, 2, 2); // head
    ctx.fillStyle = '#6b5a2a';
    ctx.fillRect(11, 3, 1, 1); // beak
    ctx.fillStyle = '#121216';
    ctx.fillRect(2, 4, 2, 1); // tail
    if (f === 0) {
      ctx.fillRect(5, 0, 2, 3);
      ctx.fillRect(4, 0, 1, 1);
    } else {
      ctx.fillRect(5, 5, 2, 3);
      ctx.fillRect(4, 7, 1, 1);
    }
    frames.push(c);
  }
  return frames;
}

export function perchedCrow(): HTMLCanvasElement {
  const [c, ctx] = canvas(8, 8);
  ctx.fillStyle = '#121216';
  ctx.fillRect(2, 2, 4, 4);
  ctx.fillRect(5, 1, 2, 2);
  ctx.fillRect(1, 5, 2, 1);
  ctx.fillStyle = '#6b5a2a';
  ctx.fillRect(7, 2, 1, 1);
  ctx.fillRect(3, 6, 1, 2);
  ctx.fillRect(5, 6, 1, 2);
  ctx.fillStyle = '#c0a040';
  ctx.fillRect(6, 1, 1, 1);
  return c;
}

export function ratFrames(): HTMLCanvasElement[] {
  const frames: HTMLCanvasElement[] = [];
  for (let f = 0; f < 2; f++) {
    const [c, ctx] = canvas(10, 5);
    ctx.fillStyle = '#3a3230';
    ctx.fillRect(2, 1, 5, 3);
    ctx.fillRect(7, 2, 2, 2);
    ctx.fillStyle = '#a07070';
    ctx.fillRect(9, 3, 1, 1); // nose
    ctx.fillRect(0, f ? 2 : 3, 2, 1); // tail
    ctx.fillStyle = '#2a2220';
    ctx.fillRect(f ? 3 : 2, 4, 1, 1);
    ctx.fillRect(f ? 5 : 6, 4, 1, 1);
    ctx.fillStyle = '#e0d0a0';
    ctx.fillRect(8, 2, 1, 1); // eye
    frames.push(c);
  }
  return frames;
}

export function graveCanvas(kind: 'cross' | 'stone', seed = 0): HTMLCanvasElement {
  const [c, ctx] = canvas(12, 16);
  const rng = new Rng(seed + 5);
  if (kind === 'cross') {
    ctx.fillStyle = '#4a3a2c';
    ctx.fillRect(5, 1, 2, 15);
    ctx.fillRect(2, 4, 8, 2);
    ctx.fillStyle = '#2e241b';
    ctx.fillRect(6, 1, 1, 15);
  } else {
    for (let y = 2; y < 16; y++) for (let x = 2; x < 10; x++) {
      if (y < 4 && (x < 3 || x > 8)) continue;
      const k = (rng.next() - 0.5) * 16;
      ctx.fillStyle = hex(92 + k, 92 + k, 96 + k);
      ctx.fillRect(x, y, 1, 1);
    }
    ctx.fillStyle = '#3a3a40';
    ctx.fillRect(4, 6, 4, 1);
    ctx.fillRect(5, 5, 2, 4);
    ctx.fillStyle = '#4d5a3a';
    ctx.fillRect(2, 14, 3, 2);
  }
  outline(ctx, 12, 16, '#141112');
  return c;
}

/** Soft cloud noise (not pixel) for the overcast sky layer. */
export function cloudTexture(seed = 3): THREE.CanvasTexture {
  const S = 256;
  const [c, ctx] = canvas(S, S);
  ctx.imageSmoothingEnabled = true;
  const rng = new Rng(seed);
  ctx.clearRect(0, 0, S, S);
  for (let i = 0; i < 90; i++) {
    const x = rng.next() * S, y = rng.next() * S, r = 20 + rng.next() * 50;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    const shade = 60 + rng.int(50);
    g.addColorStop(0, `rgba(${shade},${shade},${shade + 8},0.35)`);
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g;
    for (const dx of [-S, 0, S]) for (const dy of [-S, 0, S]) {
      ctx.save();
      ctx.translate(dx, dy);
      ctx.fillRect(x - r, y - r, r * 2, r * 2);
      ctx.restore();
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/** The werewolf's legs (rows 21–28): standing, full stride, legs passing under the body. */
const WOLF_LEGS = [
  [
    '....DFFFD.DFFD..........',
    '....DFFD...DFFD.........',
    '...DFFD.....DFFD........',
    '...DFD.......DFD........',
    '...DFFD.......DFD.......',
    '....DFFD.......DFFD.....',
    '.....DDD........DDD.....',
    '....CCCC........CCCC....',
  ],
  [
    '....DFFFD..DFFD.........',
    '...DFFD.....DFFD........',
    '..DFFD.......DFFD.......',
    '..DFD.........DFFD......',
    '.DFFD..........DFFD.....',
    '.DFD.............DFFD...',
    '.DDD..............DDD...',
    'CCCC..............CCCC..',
  ],
  [
    '....DFFFDDFFD...........',
    '.....DFFDDFFD...........',
    '.....DFFDDFFD...........',
    '......DFDDFD............',
    '......DFFDFFD...........',
    '.......DFFDFFD..........',
    '.......DDD.DDD..........',
    '......CCCCCCCC..........',
  ],
];

/** Standing and two running frames of a werewolf, side by side. */
export function werewolfSheet(seed: number): HTMLCanvasElement {
  return sheet([0, 1, 2].map((f) => werewolfCanvas(seed, f)));
}

/**
 * 24×30 werewolf in three-quarter profile facing right: long snout, tall
 * pointed ears, shaggy mane, hunched back, bushy tail, digitigrade legs.
 * Drawn from a character map so the silhouette stays clean.
 */
export function werewolfCanvas(seed: number, frame = 0): HTMLCanvasElement {
  const furs: [string, string, string][] = [
    ['#7a6a5a', '#4e4238', '#a08e78'],
    ['#6e6258', '#443a34', '#948676'],
    ['#6a6a72', '#40404a', '#90909a'],
    ['#806650', '#523f30', '#a88a6c'],
  ];
  const [fur, dark, light] = furs[seed % furs.length];
  // . empty  F fur  D dark fur  L light fur  E eye  N nose  T teeth  C claw  M mouth
  const map = [
    '........................',
    '.......D.....D..........',
    '.......DD...DF..........',
    '.......DFD.DFF..........',
    '.......DFFDFFF..........',
    '......DFFFFFFFF.........',
    '.....DFFFFFEFFFFF.......',
    '.....DFFFFFFFFLLLLL.....',
    '....DDFFFFFFFLLLLLLN....',
    '....DFFFFFFFFMMMMMM.....',
    '...DDFFFFFFFFTLTLTL.....',
    '..DDFFFFFFFFFFLLLL......',
    '.DDFFFFFFFFFFFLL........',
    '.DFFFFFFFFFFFFFLF.......',
    'DDFFFFFFFFFFFFFFFF......',
    'DFFFFFLLLLFFFFFFFFD.....',
    'DFFFFLLLLLLFFFDFFFFD....',
    '.DFFFLLLLLLFFFD.DFFFD...',
    '..DFFFLLLLFFFFD..DFFD...',
    '...DFFFFFFFFFD....DFD...',
    '....DFFFFFFFFD.....CCC..',
    ...WOLF_LEGS[frame],
    '........................',
  ];
  const H = map.length;
  const W = map[0].length;
  const [c, ctx] = canvas(W, H);
  const col: Record<string, string> = { F: fur, D: dark, L: light, E: '#ffd23a', N: '#141010', T: '#f0e8d8', C: '#e0d6c4', M: '#3a1414' };
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const ch = map[y][x];
    if (ch === '.') continue;
    ctx.fillStyle = col[ch];
    ctx.fillRect(x, y, 1, 1);
  }
  // bushy tail curling behind the back
  ctx.fillStyle = dark;
  for (const [x, y] of [[0, 17], [0, 18], [1, 19], [1, 20], [2, 21], [0, 19], [0, 20], [1, 21]]) ctx.fillRect(x, y, 1, 1);
  // a few fur tufts for texture (deterministic per wolf)
  const rng = new Rng(seed * 31 + 7);
  ctx.fillStyle = dark;
  for (let k = 0; k < 8; k++) {
    const x = 4 + rng.int(12), y = 12 + rng.int(8);
    if (map[y][x] === 'F') ctx.fillRect(x, y, 1, 1);
  }
  outline(ctx, W, H, '#0c0808');
  // glowing eye stays bright over the outline
  ctx.fillStyle = '#ffd23a';
  ctx.fillRect(11, 6, 1, 1);
  return c;
}

export function batFrames(): HTMLCanvasElement[] {
  const frames: HTMLCanvasElement[] = [];
  for (let f = 0; f < 2; f++) {
    const [c, ctx] = canvas(14, 8);
    ctx.fillStyle = '#18141c';
    ctx.fillRect(6, 3, 2, 3); // body
    ctx.fillRect(6, 2, 1, 1);
    ctx.fillRect(7, 2, 1, 1);
    if (f === 0) {
      ctx.fillRect(1, 1, 5, 2);
      ctx.fillRect(8, 1, 5, 2);
      ctx.fillRect(0, 0, 2, 1);
      ctx.fillRect(12, 0, 2, 1);
    } else {
      ctx.fillRect(2, 4, 4, 2);
      ctx.fillRect(8, 4, 4, 2);
      ctx.fillRect(1, 6, 2, 1);
      ctx.fillRect(11, 6, 2, 1);
    }
    ctx.fillStyle = '#c03030';
    ctx.fillRect(6, 3, 1, 1);
    ctx.fillRect(7, 3, 1, 1);
    frames.push(c);
  }
  return frames;
}

/**
 * 32×32 archangel, wings spread, halo overhead, a trumpet raised; two frames
 * (wings up / wings down) for a slow flap.
 */
export function angelSheet(): HTMLCanvasElement {
  const frames = [0, 1].map((f) => {
    const [c, ctx] = canvas(32, 32);
    const r = (x: number, y: number, w: number, h: number, col: string) => {
      ctx.fillStyle = col;
      ctx.fillRect(x, y, w, h);
    };
    // wings: stacked feather rows fanning out from the shoulders
    const wing = (side: 1 | -1) => {
      for (let k = 0; k < 7; k++) {
        const len = 11 - Math.abs(k - 2) * 1.4;
        const y = (f === 0 ? 5 : 9) + k * 2 - (f === 0 ? Math.max(0, 3 - k) : 0);
        const x0 = side > 0 ? 18 : 14 - Math.round(len);
        r(x0, y, Math.round(len), 2, k % 2 ? '#dfe6f2' : '#ffffff');
        r(side > 0 ? x0 + Math.round(len) - 1 : x0, y + 1, 1, 1, '#b8c4d8');
      }
    };
    wing(-1);
    wing(1);
    // robe
    r(13, 12, 6, 14, '#f4f0e6');
    r(12, 18, 8, 8, '#f4f0e6');
    r(11, 23, 10, 4, '#ece6d6');
    r(17, 12, 2, 15, '#d8d0bc');
    r(13, 16, 6, 1, '#e0b040'); // golden sash
    r(15, 17, 1, 4, '#e0b040');
    // head, golden hair
    r(14, 6, 4, 5, '#f2d0b0');
    r(13, 5, 6, 2, '#f0c040');
    r(13, 7, 1, 4, '#f0c040');
    r(18, 7, 1, 4, '#f0c040');
    r(15, 8, 1, 1, '#3a2a20');
    r(17, 8, 1, 1, '#3a2a20');
    // arms raised to a golden trumpet
    r(19, 11, 2, 2, '#f4f0e6');
    r(21, 9, 1, 3, '#f2d0b0');
    r(21, 6, 6, 1, '#f0c040');
    r(26, 4, 2, 5, '#ffd860');
    r(12, 13, 1, 5, '#f4f0e6');
    r(12, 18, 1, 1, '#f2d0b0');
    outline(ctx, 32, 32, '#6a5a3a');
    // halo floats free of the outline
    r(13, 1, 6, 1, '#fff2a0');
    r(12, 2, 1, 1, '#fff2a0');
    r(19, 2, 1, 1, '#fff2a0');
    r(13, 3, 6, 1, '#ffe070');
    return c;
  });
  return sheet(frames);
}

/**
 * 警徽: a round gold badge with a five-pointed star, dark rim, a glint top-left.
 * Drawn per pixel on a 16×16 grid.
 */
export function badgeCanvas(): HTMLCanvasElement {
  const [c, ctx] = canvas(16, 16);
  const star: [number, number][] = [];
  for (let k = 0; k < 10; k++) {
    const a = -Math.PI / 2 + (k * Math.PI) / 5;
    const r = k % 2 ? 2.4 : 5.6;
    star.push([7.5 + Math.cos(a) * r, 7.5 + Math.sin(a) * r]);
  }
  const inStar = (x: number, y: number) => {
    let inside = false;
    for (let i = 0, j = star.length - 1; i < star.length; j = i++) {
      const [xi, yi] = star[i];
      const [xj, yj] = star[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  };
  for (let y = 0; y < 16; y++) {
    for (let x = 0; x < 16; x++) {
      const d = Math.hypot(x - 7.5, y - 7.5);
      if (d > 7.6) continue;
      let col = d > 6.6 ? '#6a4208' : d > 5.9 ? '#f2c14e' : '#b8801c';
      if (d <= 5.9 && inStar(x + 0.5, y + 0.5)) col = x + y < 14 ? '#fff2b0' : '#ffd24a';
      ctx.fillStyle = col;
      ctx.fillRect(x, y, 1, 1);
    }
  }
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(4, 3, 2, 1);
  ctx.fillRect(3, 4, 1, 2);
  return c;
}
