import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { Animals } from './animals';
import { Atmosphere } from './atmosphere';
import { Explosions } from './explosion';
import { Shield } from './shield';
import { Spring } from './spring';
import { HouseSkin, Magic, Reticle } from './magic';
import { PODIUM_H, PODIUM_POS, Podium } from './podium';
import { PERSONAS } from '../personas';
import { badgeCanvas, characterSheet, graveCanvas, pixelTexture, setFrame, sheetTexture, werewolfSheet, type Look } from './pixel';
import { EXIT_PATH, billboardSprite, buildTown, seatAngle, seatPosition, type HouseRefs, type TownRefs } from './town';

/** Separable tilt-shift blur: sharp band around `focus` (0..1 screen y), blur grows away from it. */
const TiltShift = (dir: [number, number]) => ({
  uniforms: {
    tDiffuse: { value: null },
    texel: { value: new THREE.Vector2(dir[0] / 1024, dir[1] / 1024) },
    focus: { value: 0.45 },
    band: { value: 0.16 },
    strength: { value: 2.2 },
  },
  vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
  fragmentShader: `
    uniform sampler2D tDiffuse; uniform vec2 texel; uniform float focus; uniform float band; uniform float strength;
    varying vec2 vUv;
    void main(){
      float d = max(abs(vUv.y - focus) - band, 0.0);
      float s = clamp(d * strength * 4.0, 0.0, 3.0);
      vec4 sum = vec4(0.0);
      sum += texture2D(tDiffuse, vUv - 4.0 * texel * s) * 0.051;
      sum += texture2D(tDiffuse, vUv - 3.0 * texel * s) * 0.0918;
      sum += texture2D(tDiffuse, vUv - 2.0 * texel * s) * 0.12245;
      sum += texture2D(tDiffuse, vUv - 1.0 * texel * s) * 0.1531;
      sum += texture2D(tDiffuse, vUv) * 0.1633;
      sum += texture2D(tDiffuse, vUv + 1.0 * texel * s) * 0.1531;
      sum += texture2D(tDiffuse, vUv + 2.0 * texel * s) * 0.12245;
      sum += texture2D(tDiffuse, vUv + 3.0 * texel * s) * 0.0918;
      sum += texture2D(tDiffuse, vUv + 4.0 * texel * s) * 0.051;
      gl_FragColor = sum;
    }`,
});

/** Final grade: vignette, split toning, film grain, lightning lift. */
const GradeShader = {
  uniforms: {
    tDiffuse: { value: null },
    time: { value: 0 },
    night: { value: 0 },
    flash: { value: 0 },
  },
  vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
  fragmentShader: `
    uniform sampler2D tDiffuse; uniform float time; uniform float night; uniform float flash;
    varying vec2 vUv;
    float rand(vec2 c){ return fract(sin(dot(c, vec2(12.9898,78.233))) * 43758.5453); }
    void main(){
      vec4 c = texture2D(tDiffuse, vUv);
      float lum = dot(c.rgb, vec3(0.299,0.587,0.114));
      vec3 shadowTint = mix(vec3(0.92,0.96,1.06), vec3(0.8,0.9,1.2), night);
      vec3 highTint = vec3(1.08,1.0,0.9);
      c.rgb *= mix(shadowTint, highTint, smoothstep(0.1, 0.7, lum));
      c.rgb = mix(vec3(lum), c.rgb, mix(0.85, 0.75, night));
      vec2 p = vUv - 0.5;
      float v = smoothstep(0.85, 0.25, length(p * vec2(1.0, 1.15)));
      c.rgb *= mix(0.55, 1.0, v);
      c.rgb += flash * 0.12;
      c.rgb += (rand(vUv * 800.0 + time) - 0.5) * 0.035;
      gl_FragColor = c;
    }`,
};

/** Height of the sheriff's badge over its wearer's feet (just above the head). */
const BADGE_Y = 2.35;

interface Actor {
  id: number;
  sprite: THREE.Mesh;
  /** Werewolf form, only ever shown to wolves (or god view) during the wolf turn. */
  wolf: THREE.Mesh;
  grave: THREE.Mesh;
  /** Standing spot on the plaza ring. */
  base: THREE.Vector3;
  /** Where the wolves gather in the plaza. */
  den: THREE.Vector3;
  status: 'alive' | 'dead' | 'exiled';
  /** Bumped by every new command; running sequences stop when it changes. */
  gen: number;
  phase: number;
  /** Extra vertical stretch on top of the idle bob (a wolf rearing up to howl). */
  pose: number;
}

/** Scene sounds, positioned by the stage (volume falls off with distance). */
export interface StageSounds {
  doorOpen(vol: number): void;
  doorClose(vol: number): void;
  doorBreak(vol: number): void;
  seal(vol: number): void;
  squeak(vol: number): void;
  flap(vol: number): void;
  caw(vol: number): void;
  thunder(distance: number): void;
  /** `big`: the house going up; otherwise the smaller blast where the wolf stood. */
  explosion(vol: number, big: boolean): void;
  doorBang(vol: number): void;
  growl(vol: number): void;
  scream(vol: number): void;
  bell(vol: number): void;
  /** 守卫 casts the bell over a house. */
  guardCast(vol: number): void;
  /** 查验: twinkling chimes, then the verdict (a bright chord, or a low sting for a wolf). */
  sparkle(vol: number): void;
  reveal(wolf: boolean): void;
  /** 银水: the bottle breaks and the poison hisses and bubbles; then the victim moans. */
  poison(vol: number): void;
  groan(vol: number): void;
  /** 金水: a choir sings a hymn. */
  hymn(): void;
  /** 猎人: the crosshair locks, the gun goes off, someone is hit. */
  lock(): void;
  gunshot(): void;
  hit(vol: number): void;
  /** 狼人胜利: a wolf howls at the moon. */
  howl(vol: number, pitch: number): void;
  /** 颁奖典礼: the podium thumps into place; poop lands with a splat. */
  thud(vol: number): void;
  splat(vol: number): void;
}

interface Tween {
  t: number;
  dur: number;
  fn: (k: number) => void;
  resolve: () => void;
}

const WALK_SPEED = 2.6;
/** Camera limits: look around a little, never orbit freely. */
/** Camera limits: free orbit; pitch from low over the rooftops to nearly top-down. */
const PITCH_MIN = 0.15;
const PITCH_MAX = 1.35;
const ZOOM_MIN = 0.35;
// farther than this the night fog (FogExp2 0.02) swallows the town
const ZOOM_MAX = 1.4;
/** Vertical field of view at FIT_ASPECT and wider. */
const FOV = 32;
/** Narrower viewports widen the lens (to at most FOV_MAX) to see as far sideways as this aspect. */
const FIT_ASPECT = 1.3;
/** Portrait fits the whole ring across: its side seats sit right at the screen's edges otherwise. */
const FIT_ASPECT_PORTRAIT = 1.55;
const FOV_MAX = 66;
/** Phone close-up: an eye-level shot of one seat. */
const CLOSE_FOV = 42;
/** Layer for whatever blocks the close-up's line of sight: the camera doesn't draw it. */
const HIDDEN_LAYER = 5;
const CLOSE_PITCH = 0.12;
const CLOSE_YAW = 0.2;
/** What the close-up frames (world units): a villager, the werewolf form, a house (its owner indoors). */
const CLOSE_SUBJECT = {
  person: { h: 2.3, aim: 1.05 },
  wolf: { h: 3, aim: 1.4 },
  house: { h: 7.5, aim: 2.6 },
};
/** Share of the free screen height the subject fills. */
const CLOSE_FILL = 0.62;
const CLOSE_DIST_MIN = 6;
const CLOSE_DIST_MAX = 26;
/** How far the view may be panned away from its framed target. */
const MAX_PAN = 26;

export interface ScreenPos {
  x: number;
  y: number;
  visible: boolean;
}

export class Stage {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera = new THREE.PerspectiveCamera(FOV, 1, 0.5, 400);
  /** Extra camera distance a tall viewport needs beyond what its wider lens covers. */
  private aspectFit = 1;
  private composer: EffectComposer;
  private bloom: UnrealBloomPass;
  private tiltH: ShaderPass;
  private tiltV: ShaderPass;
  private grade: ShaderPass;
  private atmo: Atmosphere;
  private fx: Explosions;
  /**
   * A set-piece shot overriding the speaker framing: a house blowing up (pulled
   * back for the whole cloud) or the wolves' attack on a door.
   */
  private shot: { target: THREE.Vector3; until: number; dist: number; band: number } | null = null;
  private shield: Shield;
  private magic: Magic;
  private reticle: Reticle;
  /** Where the crosshair is aimed (world space), while it is up. */
  private aimAt: THREE.Vector3 | null = null;
  /** Houses whose look is changed for a set piece, restored when it ends (or on reset). */
  private skins = new Map<number, HouseSkin>();
  /** Bumped by `resetAll`: set pieces of an old game stop where they are. */
  private epoch = 0;
  private town: TownRefs;
  private animals: Animals;
  private spring: Spring;
  /** The game is over: 'good' keeps it day and the villagers dance; 'wolf' keeps it night. */
  private ending: 'good' | 'wolf' | null = null;
  /** 0..1 how far the survivors have broken into their dance. */
  private dance = 0;
  private actors: Actor[] = [];
  /** 颁奖典礼's podium, bouquets and flying poop. */
  private podium = new Podium();
  /** Awards: who is hopping and clapping on the spot, facing the podium. */
  private cheering = new Set<number>();
  /** Bumped whenever the awards move on: the previous part's loops (throwing flowers / poop) stop. */
  private awardRun = 0;
  private bouquets: THREE.Sprite[] = [];
  private billboards: THREE.Object3D[] = [];
  private focusRing: THREE.Mesh;
  /** 警徽: worn over the sheriff's head by day. */
  private badge: THREE.Mesh;
  private badgeHolder: number | null = null;
  /** The badge is flying / being torn up: `frame` leaves its position alone. */
  private badgeBusy = false;
  private focusLight: THREE.SpotLight;
  private timer = new THREE.Timer();
  /** Scene time; stands still while paused. */
  private time = 0;
  /** Freeze every animation (walks, doors, weather, animals); the frame keeps rendering. */
  paused = false;

  private nightTarget = 0;
  private focusId: number | null = null;
  private camTarget = new THREE.Vector3(0, 1, 0);
  private camDist = 40;
  private yaw = 0;
  private pitch = 0.55;
  /** Auto-framed heading (eased towards `yawGoal`). */
  private baseYaw = 0;
  /** Player drag offset, limited to a small arc around the framed view. */
  private dragYaw = 0;
  private userZoom = 1;
  /** Player pan offset on the ground (right-drag / shift-drag), dropped when the next speaker is framed. */
  private pan = new THREE.Vector3();

  onFrame?: (positions: ScreenPos[]) => void;
  onPick?: (id: number) => void;
  /** Close-up: the player swiped sideways (+1: on to the next seat, -1: back). */
  onSwipe?: (dir: 1 | -1) => void;

  /**
   * Phone view: instead of the overview the camera stands at eye level in front of
   * one seat (`view`); the set pieces (wolves' attack, night roles, badge, endings)
   * still take the wide shot while they play.
   */
  closeUp = false;
  private closeSeat = 0;
  private wasClose = false;
  /** The overview's lens for this viewport (see `resize`). */
  private wideFov = FOV;
  private viewH = 1;
  private viewW = 1;
  private viewHcss = 1;
  /** Close-up: the picture's shift (CSS px) that centres the subject in the free part of the screen. */
  private viewShift = new THREE.Vector2();
  /** The part of the screen no panel covers (CSS px), from the HUD; null = all of it. */
  private free: { left: number; top: number; right: number; bottom: number } | null = null;
  sounds?: StageSounds;

  constructor(private host: HTMLElement) {
    this.renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    host.appendChild(this.renderer.domElement);

    this.atmo = new Atmosphere(this.scene);
    this.fx = new Explosions(this.scene);
    this.shield = new Shield(this.scene);
    this.magic = new Magic(this.scene);
    this.billboards.push(...this.magic.billboards);
    this.reticle = new Reticle(host);
    this.town = buildTown();
    this.scene.add(this.town.root);
    this.billboards.push(...this.town.billboards);

    this.spring = new Spring(this.town);
    this.scene.add(this.spring.group);
    this.scene.add(this.podium.group);

    this.animals = new Animals(this.town.perches, this.town.fliesSpots);
    this.scene.add(this.animals.group);
    this.billboards.push(...this.animals.billboards);

    for (let i = 0; i < 12; i++) {
      const tex = sheetTexture(characterSheet(PERSONAS[i].look));
      const sprite = this.selfLit(billboardSprite(tex, 1.25, 1.875), tex);
      const base = seatPosition(i);
      sprite.position.copy(base);
      const wtex = sheetTexture(werewolfSheet(i));
      const wolf = this.selfLit(billboardSprite(wtex, 1.9, 2.375), wtex);
      wolf.visible = false;
      const grave = billboardSprite(pixelTexture(graveCanvas('cross', i)), 0.8, 1.05);
      grave.position.copy(base);
      grave.visible = false;
      const da = seatAngle(i);
      const den = new THREE.Vector3(Math.cos(da) * 4.6, 0, Math.sin(da) * 4.6);
      this.scene.add(sprite, wolf, grave);
      this.billboards.push(sprite, wolf, grave);
      this.actors.push({ id: i, sprite, wolf, grave, base, den, status: 'alive', gen: 0, phase: i * 0.7, pose: 1 });
    }

    this.animals.onSqueak = (p) => this.sounds?.squeak(this.falloff(p));
    this.animals.onFlap = (p) => this.sounds?.flap(this.falloff(p));
    this.animals.onCaw = (p) => this.sounds?.caw(this.falloff(p) * 0.7);
    this.atmo.onStrike = (d) => this.sounds?.thunder(d);

    this.badge = new THREE.Mesh(
      new THREE.PlaneGeometry(0.8, 0.8),
      new THREE.MeshBasicMaterial({ map: pixelTexture(badgeCanvas()), alphaTest: 0.5, side: THREE.DoubleSide, fog: false }),
    );
    this.badge.visible = false;
    this.scene.add(this.badge);
    this.billboards.push(this.badge);

    this.focusRing = new THREE.Mesh(
      new THREE.RingGeometry(0.55, 0.75, 24),
      new THREE.MeshBasicMaterial({ color: 0xffc070, transparent: true, opacity: 0.0, depthWrite: false, fog: false }),
    );
    this.focusRing.rotation.x = -Math.PI / 2;
    this.focusRing.position.y = 0.05;
    this.scene.add(this.focusRing);
    this.focusLight = new THREE.SpotLight(0xffc890, 0, 14, 0.45, 0.6, 1.2);
    this.focusLight.position.set(0, 8, 0);
    this.scene.add(this.focusLight, this.focusLight.target);

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.55, 0.5, 0.82);
    this.composer.addPass(this.bloom);
    this.tiltH = new ShaderPass(TiltShift([1, 0]));
    this.tiltV = new ShaderPass(TiltShift([0, 1]));
    this.composer.addPass(this.tiltH);
    this.composer.addPass(this.tiltV);
    this.grade = new ShaderPass(GradeShader);
    this.composer.addPass(this.grade);
    this.composer.addPass(new OutputPass());

    this.bindInput();
    this.resize();
    window.addEventListener('resize', () => this.resize());
    this.renderer.setAnimationLoop(() => this.frame());
  }

  // ───────────────────────── public API ─────────────────────────

  setNight(night: boolean) {
    // once the game is decided its ending owns the sky
    if (this.ending) return;
    this.nightTarget = night ? 1 : 0;
  }

  /** The part of the screen the HUD leaves uncovered: the close-up centres its subject there. */
  setFreeArea(r: { left: number; top: number; right: number; bottom: number } | null) {
    this.free = r;
  }

  /** Close-up: whose seat the camera stands in front of. */
  view(id: number) {
    this.closeSeat = id;
  }

  /** The close-up is on screen right now (no set piece has taken the wide shot). */
  get inCloseUp() {
    return this.closeUp && !this.shot;
  }

  focus(id: number | null) {
    if (id !== null && id !== this.focusId) this.reframe(id);
    this.focusId = id;
  }

  /**
   * Swing the camera round to look at `id` from the plaza side, so only their own
   * house is behind them and no building blocks the view. Drops the player's
   * rotation / pan / pitch; keeps their zoom level.
   */
  private reframe(id: number) {
    const a = this.actors[id];
    const p = this.anchor(a) ?? a.base;
    // camera offset direction is (sin yaw, cos yaw): point it from the actor towards the centre
    this.yawGoal = Math.atan2(-p.x, -p.z);
    this.resetView(false);
  }

  /** Back to the framed view (double-click). */
  private resetView(zoom: boolean) {
    this.pitchGoal = 0.55;
    if (zoom) this.userZoom = 1;
    this.pan.set(0, 0, 0);
    // unwind the free orbit the short way round before easing it back to 0
    this.dragYaw = Math.atan2(Math.sin(this.dragYaw), Math.cos(this.dragYaw));
    this.dragReset = true;
  }

  private yawGoal = 0;
  private pitchGoal: number | null = null;
  private dragReset = false;

  // ── choreography ──

  /** 天黑请闭眼: everyone still standing walks home and shuts the door. */
  nightFall(): Promise<void> {
    const jobs: Promise<void>[] = [];
    for (const a of this.actors) {
      if (a.status !== 'alive') continue;
      const gen = ++a.gen;
      jobs.push(this.goInside(a, gen, a.sprite, Math.random() * 1.4));
    }
    return Promise.all(jobs).then(() => {});
  }

  /** Dawn: survivors open their doors and walk back to the plaza. */
  dayBreak(): Promise<void> {
    this.shield.uncover(); // the guard's bell stands until morning
    const jobs: Promise<void>[] = [];
    for (const a of this.actors) {
      if (a.status !== 'alive') continue;
      // wolves smashed in the door but the witch saved them: it has been patched up overnight
      const h = this.town.houses[a.id];
      if (h.state === 'broken') {
        h.state = 'normal';
        h.lit = true;
        h.debris.visible = false;
        h.doorPivot.visible = true;
        h.doorPivot.rotation.set(0, 0, 0);
      }
      const gen = ++a.gen;
      jobs.push(this.comeOutside(a, gen, a.sprite, a.base, 0.4 + Math.random() * 1.4));
    }
    return Promise.all(jobs).then(() => {});
  }

  /** Wolf turn, seen only by wolves: they step out in werewolf form. */
  wolvesOut(ids: number[]): Promise<void> {
    const jobs: Promise<void>[] = [];
    for (const id of ids) {
      const a = this.actors[id];
      if (a.status !== 'alive') continue;
      const gen = ++a.gen;
      a.sprite.visible = false;
      jobs.push(this.comeOutside(a, gen, a.wolf, a.den, Math.random() * 0.8));
    }
    return Promise.all(jobs).then(() => {});
  }

  wolvesIn(ids: number[]): Promise<void> {
    const jobs: Promise<void>[] = [];
    for (const id of ids) {
      const a = this.actors[id];
      if (!a.wolf.visible) continue;
      const gen = ++a.gen;
      jobs.push(this.goInside(a, gen, a.wolf, Math.random() * 0.6));
    }
    return Promise.all(jobs).then(() => {});
  }

  /**
   * Jump straight to a resumed game's scene: graves and smashed doors for the
   * dead, sealed dark houses for the exiled, everyone else indoors (night) or on
   * the plaza, and `wolves` (seen by wolf players) out in the den.
   */
  restore(r: {
    dead: number[];
    exiled: number[];
    exploded: number[];
    indoors: boolean;
    wolves: number[];
    night: boolean;
    /** Tonight's guarded house (seen by the guard): its bell is up. */
    guarded?: number | null;
    /** Houses whose light went out tonight (poisoned / shot, seen by the one who did it). */
    darkened?: number[];
  }) {
    this.resetAll();
    if (r.guarded != null) this.shield.cover(this.town.houses[r.guarded], true);
    for (const id of r.darkened ?? []) this.town.houses[id].lit = false;
    for (const id of r.dead) this.killed(id, true);
    for (const id of r.exploded) {
      this.killed(id, true);
      this.fx.ruin(this.town.houses[id], id);
    }
    for (const id of r.exiled) {
      const a = this.actors[id];
      a.status = 'exiled';
      a.sprite.visible = false;
      const h = this.town.houses[id];
      h.lit = false;
      h.state = 'sealed';
      h.seal.visible = true;
    }
    for (const a of this.actors) {
      if (a.status !== 'alive') continue;
      if (r.wolves.includes(a.id)) {
        a.sprite.visible = false;
        a.wolf.visible = true;
        a.wolf.position.copy(a.den);
      } else a.sprite.visible = !r.indoors;
    }
    this.nightTarget = r.night ? 1 : 0;
    this.atmo.mix = this.nightTarget;
  }

  /** Killed (night kill, poison, hunter shot): a grave takes their place, their door is smashed. */
  killed(id: number, silent = false) {
    const a = this.actors[id];
    if (a.status !== 'alive') return;
    a.status = 'dead';
    a.gen++;
    a.sprite.visible = false;
    a.wolf.visible = false;
    a.grave.visible = true;
    const h = this.town.houses[id];
    h.lit = false;
    if (h.state === 'broken') return; // already smashed in by the wolves tonight
    this.breakDoor(h, silent);
  }

  private breakDoor(h: HouseRefs, silent = false) {
    h.state = 'broken';
    h.debris.visible = true;
    h.doorPivot.visible = false; // torn off: it now lies on the ground (part of debris)
    if (!silent) this.sounds?.doorBreak(this.falloff(h.doorstep));
  }

  /**
   * The kill, seen by the wolves: the pack runs to the victim's door and throws
   * itself at it. Unguarded, the door gives, they rush in (a scream, blood out of
   * the doorway) and come back out; guarded, a golden bell (金钟罩) flares with
   * every blow and throws them back. Either way they end up by that door; wolvesIn
   * then takes them home.
   */
  async wolfAttack(ids: number[], target: number, blocked: boolean): Promise<void> {
    const h = this.town.houses[target];
    const g = h.group;
    // a wolf voted to kill a teammate: the victim slips home first
    const victim = this.actors[target];
    if (ids.includes(target) && victim.wolf.visible) await this.goInside(victim, ++victim.gen, victim.wolf, 0);
    const pack = ids.map((id) => this.actors[id]).filter((a) => a.id !== target && a.status === 'alive' && a.wolf.visible);
    if (!pack.length) return;
    const gens = new Map(pack.map((a) => [a.id, ++a.gen]));
    const live = (a: Actor) => () => a.gen === gens.get(a.id);
    const { d } = h.size;
    const n = pack.length;
    // guarded: they gather outside the bell's flared rim
    const back = blocked ? Math.hypot(h.size.w, d) / 2 + 1.9 : d / 2 + 1.8;
    const spots = pack.map((_, k) => g.localToWorld(new THREE.Vector3((k - (n - 1) / 2) * 1.25, 0, back + (k % 2) * 0.35)));
    const door = g.localToWorld(new THREE.Vector3(0, 1.1, d / 2 + 0.1));
    const outward = g.localToWorld(new THREE.Vector3(0, 0, 1)).sub(g.position).setY(0).normalize();

    this.frameHouse(target, { dist: 24, pitch: 0.42, yaw: 0.35 }); // from the plaza side, a little off-axis

    // charge
    await Promise.all(pack.map((a, k) => this.wait(k * 0.15).then(() => this.walk(a.wolf, spots[k], live(a), 6))));
    if (pack.some((a) => !live(a)())) return;
    this.sounds?.growl(this.falloff(door));
    await this.wait(0.5);

    // three blows
    for (let hit = 0; hit < 3; hit++) {
      await Promise.all(pack.map((a, k) => this.wait(k * 0.07).then(() => this.lunge(a.wolf, spots[k], h.doorstep, blocked ? 0.35 : 0.75))));
      if (blocked) {
        // struck where the leading wolf hit the bell
        this.shield.strike(h, spots[0].clone().lerp(h.doorstep, 0.35).setY(1.2), hit === 2 ? 1.6 : 1);
        this.sounds?.bell(this.falloff(door) * (hit === 2 ? 1 : 0.7));
      } else {
        this.sounds?.doorBang(this.falloff(door));
        void this.rattleDoor(h);
      }
      this.fx.shake = Math.max(this.fx.shake, 0.12);
      await this.wait(hit === 2 ? 0.1 : 0.35);
    }

    if (blocked) {
      // thrown back from the bell, then they slink off
      await Promise.all(pack.map((a, k) => this.walk(a.wolf, spots[k].clone().addScaledVector(outward, 2.2), live(a), 9)));
      await this.wait(1.2);
      this.shot = null;
      return;
    }

    // the door gives; they pour in
    this.breakDoor(h);
    await this.wait(0.35);
    await Promise.all(
      pack.map((a, k) =>
        this.wait(k * 0.18).then(async () => {
          await this.walk(a.wolf, h.doorstep, live(a), 7);
          await this.walk(a.wolf, h.inside, live(a), 7);
          if (live(a)()) a.wolf.visible = false;
        }),
      ),
    );
    await this.wait(0.3);
    this.sounds?.scream(this.falloff(door));
    for (const w of h.windows) w.emissiveIntensity = 0; // the light inside goes out
    h.lit = false;
    await this.wait(0.5);
    this.fx.blood(door.clone().setY(1), outward, 1);
    await this.wait(0.45);
    this.fx.blood(door.clone().setY(0.8), outward, 0.6);
    await this.wait(1.2);
    // back out, one by one
    await Promise.all(
      pack.map((a, k) =>
        this.wait(k * 0.25).then(async () => {
          if (!live(a)()) return;
          a.wolf.position.copy(h.inside);
          a.wolf.visible = true;
          await this.walk(a.wolf, h.doorstep, live(a), 3);
          await this.walk(a.wolf, spots[k], live(a), 3);
        }),
      ),
    );
    this.shot = null;
  }

  // ── night roles' set pieces (seen by the player who acts, or god view) ──

  /**
   * Point the camera at house `id` from the plaza side. `lift` raises the aim (e.g.
   * to take in the angel over the roof); the shot holds for `secs`.
   */
  private frameHouse(id: number, o: { dist: number; pitch: number; yaw?: number; lift?: number; secs?: number; inset?: number }) {
    const g = this.town.houses[id].group;
    this.shot = { target: g.position.clone().multiplyScalar(o.inset ?? 0.85).setY(o.lift ?? 1.4), until: this.time + (o.secs ?? 60), dist: o.dist, band: 0.3 };
    this.yawGoal = Math.atan2(-g.position.x, -g.position.z) + (o.yaw ?? 0);
    this.resetView(false);
    this.pitchGoal = o.pitch;
  }

  /** Back to the player's own house for a moment, then the usual framing. */
  private goHome(me: number) {
    this.frameHouse(me, { dist: 30, pitch: 0.5, secs: 2.5 });
    return this.wait(1);
  }

  private skin(id: number): HouseSkin {
    let s = this.skins.get(id);
    if (!s) this.skins.set(id, (s = new HouseSkin(this.town.houses[id])));
    return s;
  }

  private unskin(id: number) {
    this.skins.get(id)?.restore();
    this.skins.delete(id);
  }

  /** The lights inside stutter and go out. */
  private async lightsOut(h: HouseRefs) {
    await this.tween(0.8, (k) => {
      for (const w of h.windows) w.emissiveIntensity = Math.random() < 0.5 + k * 0.4 ? 0 : 2.4 * (1 - k);
    });
    h.lit = false;
    for (const w of h.windows) w.emissiveIntensity = 0;
  }

  /** 守卫: a golden bell comes down over the house they chose; it stands until dawn. */
  async guardCover(id: number, me: number): Promise<void> {
    const epoch = this.epoch;
    const h = this.town.houses[id];
    this.frameHouse(id, { dist: 26, pitch: 0.45, yaw: 0.3, lift: 2 });
    await this.wait(0.9);
    if (epoch !== this.epoch) return;
    this.shield.cover(h);
    this.sounds?.guardCast(this.falloff(h.doorstep));
    await this.wait(1.2);
    if (epoch !== this.epoch) return;
    this.sounds?.bell(0.8);
    this.fx.shake = Math.max(this.fx.shake, 0.08);
    await this.wait(1.8);
    if (epoch !== this.epoch) return;
    await this.goHome(me);
  }

  /**
   * 查验: sparkles gather round the house, it turns see-through and shows who is
   * inside — a werewolf if that is what they are — standing in a green (good) or
   * red (wolf) ring, with their role over their name. Then the house closes up.
   */
  async seerReveal(id: number, wolf: boolean, me: number): Promise<void> {
    const epoch = this.epoch;
    const a = this.actors[id];
    const h = this.town.houses[id];
    const gen = ++a.gen;
    const ok = () => epoch === this.epoch && a.gen === gen;
    this.frameHouse(id, { dist: 19, pitch: 0.62, yaw: 0.25, inset: 0.95, lift: 1 });
    await this.wait(1);
    if (!ok()) return;
    this.fx.sparkles(h, 4.5);
    this.sounds?.sparkle(this.falloff(h.doorstep));
    this.magic.shine(h.group.position.clone().setY(3.5), 0xb8d0ff, 40);
    await this.wait(0.9);
    if (!ok()) return;
    const skin = this.skin(id);
    const body = wolf ? a.wolf : a.sprite;
    await this.tween(1.6, (k) => {
      skin.opacity(1 - 0.88 * k * k);
      if (k > 0.45 && !body.visible && ok()) {
        body.position.copy(h.group.position).setY(0);
        body.scale.x = 1;
        body.visible = true;
      }
    });
    if (!ok()) return;
    this.magic.showVerdict(h.group.position, wolf);
    this.sounds?.reveal(wolf);
    if (wolf) this.sounds?.growl(this.falloff(h.doorstep) * 0.8);
    await this.wait(3.4);
    if (!ok()) return;
    this.magic.hideVerdict();
    this.magic.dark();
    body.visible = false;
    await this.tween(1.2, (k) => skin.opacity(0.12 + 0.88 * k));
    if (!ok()) return;
    this.unskin(id);
    await this.goHome(me);
  }

  /**
   * 银水: the house is eaten by poison (green fumes, dripping walls), someone moans
   * inside, the lights go out.
   */
  async witchPoison(id: number, me: number): Promise<void> {
    const epoch = this.epoch;
    const h = this.town.houses[id];
    const ok = () => epoch === this.epoch;
    this.frameHouse(id, { dist: 22, pitch: 0.42, yaw: 0.3 });
    await this.wait(1);
    if (!ok()) return;
    this.sounds?.poison(this.falloff(h.doorstep));
    this.fx.toxic(h, 5.5);
    this.magic.shine(h.group.position.clone().setY(2.5), 0x60ff50, 14);
    const skin = this.skin(id);
    let groaned = false;
    await this.tween(3.4, (k) => {
      if (!ok()) return;
      skin.corrode(Math.min(1, k * 1.6), this.time);
      if (k > 0.4 && !groaned) {
        groaned = true;
        this.sounds?.groan(this.falloff(h.doorstep));
      }
    });
    if (!ok()) return;
    await this.lightsOut(h);
    await this.wait(1.4);
    if (!ok()) return;
    this.magic.dark();
    await this.tween(1.6, (k) => ok() && skin.corrode(1 - k, this.time));
    if (!ok()) return;
    this.unskin(id);
    await this.goHome(me);
  }

  /** 金水: an archangel comes down over the house, a ring of holy light rises round it, a choir sings. */
  async witchSave(id: number, me: number): Promise<void> {
    const epoch = this.epoch;
    const h = this.town.houses[id];
    const ok = () => epoch === this.epoch;
    this.frameHouse(id, { dist: 32, pitch: 0.26, yaw: 0.25, lift: 3.6 });
    await this.wait(1);
    if (!ok()) return;
    this.magic.bless(h);
    this.sounds?.hymn();
    const { w, d } = h.size;
    this.fx.motes(h.group.position, Math.hypot(w, d) / 2 + 0.9, 6);
    await this.wait(6.5);
    if (!ok()) return;
    this.magic.unbless();
    await this.wait(1.5);
    if (!ok()) return;
    await this.goHome(me);
  }

  /** The crosshair sweeps in, locks on `at`, and the gun goes off. */
  private async takeAim(at: THREE.Vector3, ok: () => boolean): Promise<boolean> {
    this.aimAt = at;
    this.reticle.start(this.host.clientWidth, this.host.clientHeight);
    await this.wait(this.reticle.sweep);
    if (!ok()) return false;
    this.reticle.lock();
    this.sounds?.lock();
    await this.wait(0.6);
    if (!ok()) return false;
    this.reticle.fire();
    this.sounds?.gunshot();
    this.fx.flash = Math.max(this.fx.flash, 0.55);
    this.fx.shake = Math.max(this.fx.shake, 0.3);
    void this.wait(0.5).then(() => {
      if (this.aimAt === at) this.aimAt = null;
    });
    return true;
  }

  /** 猎人白天开枪 (seen by everyone): aim at the player, fire; they drop and a grave takes their place. */
  async dayShot(id: number): Promise<void> {
    const epoch = this.epoch;
    const a = this.actors[id];
    const ok = () => epoch === this.epoch && a.status === 'alive';
    const p = (this.anchor(a) ?? a.base).clone();
    this.shot = { target: p.clone().setY(1.1), until: this.time + 60, dist: 16, band: 0.3 };
    this.yawGoal = Math.atan2(-p.x, -p.z);
    this.resetView(false);
    this.pitchGoal = 0.32;
    await this.wait(1.1);
    if (!ok()) return;
    if (!(await this.takeAim(p.clone().setY(1.2), ok))) return;
    this.sounds?.hit(1);
    const body = a.wolf.visible ? a.wolf : a.sprite;
    const away = p.clone().sub(this.camera.position).setY(0).normalize();
    this.fx.blood(p.clone().setY(1.2), away, 0.35);
    const side = Math.random() < 0.5 ? 1 : -1;
    await this.tween(0.4, (k) => {
      body.rotation.z = side * (Math.PI / 2) * k * k;
    });
    await this.wait(0.5);
    if (epoch !== this.epoch) return;
    body.rotation.z = 0;
    this.fx.puff(p);
    this.killed(id);
    await this.wait(1.4);
    if (epoch === this.epoch) this.shot = null;
  }

  /** One leap at the door and back. */
  private async lunge(body: THREE.Mesh, from: THREE.Vector3, door: THREE.Vector3, reach: number) {
    const to = from.clone().lerp(door, reach);
    const tex = (body.material as THREE.MeshStandardMaterial).map!;
    setFrame(tex, 1);
    await this.tween(0.16, (k) => {
      body.position.lerpVectors(from, to, k * k);
      body.position.y = Math.sin(k * Math.PI) * 0.5;
    });
    setFrame(tex, 2);
    await this.tween(0.28, (k) => {
      body.position.lerpVectors(to, from, 1 - (1 - k) * (1 - k));
      body.position.y = 0;
    });
    setFrame(tex, 0);
  }

  /** The door jumps on its hinges under a blow. */
  private async rattleDoor(h: HouseRefs) {
    if (h.state !== 'normal') return;
    await this.tween(0.3, (k) => this.setDoor(h, Math.sin(k * Math.PI * 5) * 0.12 * (1 - k)));
  }

  /**
   * A wolf self-destructs: a blast where they stood, then their house goes up in a
   * mushroom cloud and is left a burning ruin. Resolves once the cloud has risen.
   */
  async exploded(id: number): Promise<void> {
    const a = this.actors[id];
    if (a.status !== 'alive') return;
    const at = (this.anchor(a) ?? a.base).clone();
    const house = this.town.houses[id];
    a.status = 'dead';
    const gen = ++a.gen;
    a.sprite.visible = false;
    a.wolf.visible = false;
    a.grave.visible = true;
    this.fx.pop(at);
    this.sounds?.explosion(this.falloff(at), false);
    // face the house from the plaza and back off so the whole cloud fits
    this.yawGoal = Math.atan2(-house.group.position.x, -house.group.position.z);
    this.resetView(false);
    this.pitchGoal = 0.3;
    this.shot = { target: house.group.position.clone().multiplyScalar(0.7).setY(6), until: this.time + 8, dist: 50, band: 0.5 };
    await this.wait(0.35);
    if (a.gen !== gen) return; // a new game started meanwhile
    this.sounds?.explosion(1, true);
    await this.fx.blast(house, id);
  }

  /** Exiled by vote: they walk out of town along the path; their house goes dark and is sealed. No grave. */
  exiled(id: number) {
    const a = this.actors[id];
    if (a.status !== 'alive') return;
    a.status = 'exiled';
    const gen = ++a.gen;
    const h = this.town.houses[id];
    void (async () => {
      h.lit = false;
      h.state = 'sealed';
      await this.wait(0.6);
      h.seal.visible = true;
      this.sounds?.seal(this.falloff(h.doorstep));
      if (!a.sprite.visible) return;
      // join the path at its nearest point, then follow it out
      const start = EXIT_PATH.reduce((best, p, k) => (p.distanceTo(a.sprite.position) < EXIT_PATH[best].distanceTo(a.sprite.position) ? k : best), 0);
      for (const p of EXIT_PATH.slice(start)) {
        await this.walk(a.sprite, p, () => a.gen === gen, 1.8);
        if (a.gen !== gen) return;
      }
      a.sprite.visible = false;
    })();
  }

  // ── 警徽 ──

  /** Where the badge sits on `id`: over the head, or over the grave once they are dead. */
  private badgeSpot(id: number): THREE.Vector3 {
    const a = this.actors[id];
    return (this.anchor(a) ?? a.base).clone().setY(a.status === 'dead' ? 1.5 : BADGE_Y);
  }

  /** Put the badge on `id` at once (a resumed game). */
  setBadge(id: number | null) {
    this.badgeHolder = id;
    this.badgeBusy = false;
  }

  /**
   * The badge moves. Elected (`from` null): it drops out of the sky over the well and
   * flies, spinning and trailing stars, onto the new sheriff's head. Handed on: it
   * flies from the dead sheriff's grave to the heir. Torn up (`to` null): it shakes
   * and bursts into falling gold scraps.
   */
  async moveBadge(from: number | null, to: number | null): Promise<void> {
    const epoch = this.epoch;
    const ok = () => epoch === this.epoch;
    const b = this.badge;
    if (to === null) {
      this.badgeHolder = null;
      if (from === null) return;
      const at = this.badgeSpot(from);
      this.badgeBusy = true;
      b.visible = true;
      b.position.copy(at);
      this.shot = { target: at.clone().setY(1.4), until: this.time + 3.5, dist: 16, band: 0.3 };
      await this.tween(1, (k) => {
        if (!ok()) return;
        b.position.set(at.x + Math.sin(k * 60) * 0.06 * k, at.y + k * 0.5, at.z);
        b.scale.setScalar(1 + k * 0.4);
      });
      if (!ok()) return;
      this.fx.shards(b.position.clone());
      this.fx.flash = Math.max(this.fx.flash, 0.2);
      this.sounds?.doorBreak(0.45);
      b.visible = false;
      b.scale.set(1, 1, 1);
      this.badgeBusy = false;
      await this.wait(1.2);
      return;
    }
    const start = from === null ? new THREE.Vector3(0, 12, 0) : this.badgeSpot(from);
    const end0 = this.badgeSpot(to);
    this.badgeHolder = null;
    this.badgeBusy = true;
    b.visible = true;
    b.position.copy(start);
    const dur = from === null ? 2.6 : 2.2;
    this.shot = { target: start.clone().lerp(end0, 0.6).setY(2.5), until: this.time + dur + 1.6, dist: from === null ? 26 : 22, band: 0.45 };
    this.sounds?.sparkle(1);
    const p = new THREE.Vector3();
    await this.tween(dur, (k) => {
      if (!ok()) return;
      const e = k * k * (3 - 2 * k);
      p.lerpVectors(start, this.badgeSpot(to), e);
      p.y += Math.sin(Math.PI * e) * (from === null ? 1.5 : 3);
      b.position.copy(p);
      // a coin spinning in the air, bigger while it is high up
      b.scale.set(Math.cos(k * Math.PI * 7) * (1 + (1 - k) * 0.7), 1 + (1 - k) * 0.7, 1);
      this.fx.glint(p);
    });
    if (!ok()) return;
    b.scale.set(1, 1, 1);
    this.fx.starBurst(this.badgeSpot(to));
    this.fx.flash = Math.max(this.fx.flash, 0.25);
    this.sounds?.bell(1);
    this.badgeHolder = to;
    this.badgeBusy = false;
    await this.wait(1.4);
  }

  // ── endings ──

  /** A wide, low shot of the plaza with the northern sky (sun / moon) over the church. */
  private skyShot(secs: number) {
    this.shot = { target: new THREE.Vector3(0, 4, -4), until: this.time + secs, dist: 44, band: 0.5 };
    this.yawGoal = 0;
    this.resetView(true);
    this.pitchGoal = 0.16;
  }

  /**
   * 狼人胜利: in broad daylight the surviving wolves turn, fall on everyone
   * left on the plaza, and the moment the last one drops night slams down under
   * a blood-red moon. Resolves on nightfall (`onNight` fires then too); the pack
   * then gathers by the well and howls.
   */
  async wolfFinale(wolfIds: number[], onNight: () => void): Promise<void> {
    const epoch = this.epoch;
    const ok = () => epoch === this.epoch;
    this.ending = 'wolf';
    this.nightTarget = 0;
    this.shot = { target: new THREE.Vector3(0, 1.2, 0), until: this.time + 600, dist: 36, band: 0.4 };
    this.yawGoal = 0;
    this.resetView(true);
    this.pitchGoal = 0.62;
    await this.wait(1.2);
    if (!ok()) return;

    // 兽化: one by one they drop the disguise
    const pack = wolfIds.map((id) => this.actors[id]).filter((a) => a.status === 'alive');
    for (const a of pack) {
      a.gen++;
      const at = (this.anchor(a) ?? a.base).clone().setY(0);
      a.sprite.visible = false;
      a.wolf.position.copy(at);
      a.wolf.visible = true;
      this.fx.puff(at);
      this.fx.shake = Math.max(this.fx.shake, 0.25);
      this.sounds?.growl(1);
      await this.tween(0.35, (k) => {
        a.wolf.scale.x = 0.55 + 0.45 * k;
        a.pose = 0.55 + 0.45 * k + Math.sin(k * Math.PI) * 0.25;
      });
      if (!ok()) return;
      await this.wait(0.35);
      if (!ok()) return;
    }
    this.sounds?.howl(1, 1);
    await this.wait(1.4);
    if (!ok()) return;

    // 屠杀: each wolf takes the nearest villager still standing, again and again
    const prey = this.actors.filter((a) => a.status === 'alive' && !pack.includes(a) && a.sprite.visible);
    const taken = new Set<Actor>();
    const hunt = async (w: Actor, k: number) => {
      await this.wait(k * 0.3);
      while (ok()) {
        const left = prey.filter((v) => !taken.has(v) && v.status === 'alive');
        if (!left.length) return;
        const v = left.reduce((best, x) => (x.sprite.position.distanceTo(w.wolf.position) < best.sprite.position.distanceTo(w.wolf.position) ? x : best));
        taken.add(v);
        v.gen++; // frozen with fear
        const vp = v.sprite.position.clone().setY(0);
        const from = w.wolf.position.clone().setY(0);
        const stand = vp.clone().add(from.clone().sub(vp).setY(0).normalize().multiplyScalar(1.1));
        await this.walk(w.wolf, stand, ok, 9);
        if (!ok()) return;
        await this.lunge(w.wolf, stand, vp, 0.7);
        if (!ok()) return;
        this.sounds?.scream(this.falloff(vp) * 0.8);
        this.fx.blood(vp.clone().setY(1), vp.clone().sub(stand).setY(0).normalize(), 0.8);
        this.fx.shake = Math.max(this.fx.shake, 0.15);
        // cut down where they stood: a grave there, their house dark (the door stays shut)
        v.status = 'dead';
        v.sprite.visible = false;
        v.grave.position.copy(vp);
        v.grave.visible = true;
        this.town.houses[v.id].lit = false;
        await this.wait(0.35);
      }
    };
    await Promise.all(pack.map(hunt));
    await this.wait(0.8);
    if (!ok()) return;

    // 天黑: at once
    this.nightTarget = 1;
    this.fx.flash = Math.max(this.fx.flash, 0.6);
    void this.tween(0.6, (k) => {
      this.atmo.mix = Math.max(this.atmo.mix, k);
      this.atmo.blood = k;
    });
    onNight();
    this.skyShot(600);

    // the pack gathers by the well and howls at the moon
    void (async () => {
      await Promise.all(pack.map((w) => this.walk(w.wolf, w.den, ok, 4)));
      while (ok()) {
        for (const w of pack) {
          if (!ok()) return;
          await this.tween(1.6, (k) => (w.pose = 1 + Math.sin(k * Math.PI) * 0.18));
          await this.wait(0.8 + Math.random() * 1.5);
        }
      }
    })();
  }

  /**
   * 好人胜利: the fog lifts and the sun climbs over the church, the old tree by the
   * well leafs out and blossoms, flowers open along the roads, and everyone still
   * standing dances round in circles. Resolves once the scene has bloomed.
   */
  async goodFinale(): Promise<void> {
    const epoch = this.epoch;
    const ok = () => epoch === this.epoch;
    this.ending = 'good';
    this.nightTarget = 0;
    for (const a of this.actors) {
      // any wolf still showing goes back to plain clothes (none should be left standing)
      if (a.wolf.visible) {
        a.wolf.visible = false;
        if (a.status === 'alive') a.sprite.visible = true;
      }
    }
    this.skyShot(600);
    await this.wait(0.8);
    if (!ok()) return;
    void this.tween(6, (k) => (this.atmo.clear = k * k * (3 - 2 * k)));
    await this.wait(2);
    if (!ok()) return;
    void this.tween(5, (k) => (this.spring.grow = k));
    await this.wait(1.5);
    if (!ok()) return;
    for (const a of this.actors) if (a.status === 'alive') a.gen++; // stop wherever they were headed
    await this.tween(1.2, (k) => (this.dance = k));
    await this.wait(2);
  }

  /** The survivors skip round in little circles, hopping. */
  private danceStep(t: number) {
    const d = this.dance;
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
    for (const a of this.actors) {
      if (a.status !== 'alive' || !a.sprite.visible) continue;
      const th = t * 2.6 + a.phase * 2;
      const r = 0.7 * d;
      a.sprite.position.set(a.base.x + Math.cos(th) * r - r, Math.abs(Math.sin(t * 7 + a.phase)) * 0.3 * d, a.base.z + Math.sin(th) * r);
      // face the way they're going round
      const vel = new THREE.Vector3(-Math.sin(th), 0, Math.cos(th));
      a.sprite.scale.x = vel.dot(right) >= 0 ? 1 : -1;
      const hop = Math.sin(t * 7 + a.phase);
      setFrame((a.sprite.material as THREE.MeshStandardMaterial).map!, hop > 0.3 ? 1 : hop < -0.3 ? 2 : 0);
    }
  }

  // ── 颁奖典礼 ──

  /**
   * The awards: everyone is back on the plaza — the dead risen, the exiled home,
   * every smashed door, sealed house and ruin put right — under a clear blue sky
   * with white clouds, the tree in blossom and flowers by the roads.
   */
  ceremonyScene() {
    this.resetAll();
    this.ending = 'good';
    this.nightTarget = 0;
    this.atmo.mix = 0;
    this.atmo.clear = 1;
    this.atmo.fair = 1;
    this.spring.grow = 1;
    this.yawGoal = 0;
    this.resetView(true);
  }

  /**
   * Frame the podium (and the ring round it) for the rest of the ceremony: from
   * high enough that seat 1's house, behind the camera, never blocks the view.
   */
  private awardShot(dist: number) {
    this.focus(null);
    this.shot = { target: PODIUM_POS.clone().setY(1.4), until: this.time + 3600, dist, band: 0.5 };
    this.yawGoal = 0;
    this.resetView(true);
    this.pitchGoal = 0.66;
  }

  /** The podium rises out of the middle of the plaza. */
  async raisePodium(n: number): Promise<void> {
    const epoch = this.epoch;
    this.awardShot(22);
    this.podium.setup(n, '🏆');
    this.fx.shake = Math.max(this.fx.shake, 0.12);
    this.sounds?.thud(0.7);
    await this.tween(1.6, (k) => epoch === this.epoch && this.podium.rise(k * k * (3 - 2 * k)));
    if (epoch !== this.epoch) return;
    this.fx.puff(PODIUM_POS.clone());
    this.sounds?.thud(1);
  }

  /** A path from `from` to `to` that steps round the well instead of through it. */
  private route(from: THREE.Vector3, to: THREE.Vector3): THREE.Vector3[] {
    const d = to.clone().sub(from).setY(0);
    const len2 = d.lengthSq();
    if (len2 < 1e-4) return [to];
    const k = THREE.MathUtils.clamp(-from.clone().setY(0).dot(d) / len2, 0, 1);
    const near = from.clone().setY(0).addScaledVector(d, k);
    if (near.length() > 2.6) return [to];
    const side = near.lengthSq() < 1e-4 ? new THREE.Vector3(-d.z, 0, d.x).normalize() : near.normalize();
    return [side.multiplyScalar(3.3), to];
  }

  private async walkTo(a: Actor, gen: number, to: THREE.Vector3, speed = WALK_SPEED): Promise<boolean> {
    for (const p of this.route(a.sprite.position, to)) {
      await this.walk(a.sprite, p, () => a.gen === gen, speed);
      if (a.gen !== gen) return false;
    }
    return true;
  }

  /** A little jump from where they stand to `to` (up onto / down off the podium). */
  private hop(a: Actor, gen: number, to: THREE.Vector3): Promise<void> {
    const from = a.sprite.position.clone();
    const tex = (a.sprite.material as THREE.MeshStandardMaterial).map!;
    setFrame(tex, 1);
    return this.tween(0.45, (k) => {
      if (a.gen !== gen) return;
      a.sprite.position.lerpVectors(from, to, k);
      a.sprite.position.y = THREE.MathUtils.lerp(from.y, to.y, k) + Math.sin(k * Math.PI) * 0.6;
    }).then(() => {
      if (a.gen === gen) setFrame(tex, 0);
    });
  }

  /** Everyone in `ids` walks up and onto the podium, side by side. */
  private async mount(ids: number[]): Promise<void> {
    const n = ids.length;
    await Promise.all(
      ids.map(async (id, k) => {
        const a = this.actors[id];
        const gen = ++a.gen;
        this.cheering.delete(id);
        await this.wait(k * 0.25);
        if (!(await this.walkTo(a, gen, this.podium.foot(k, n), 3.2))) return;
        await this.hop(a, gen, this.podium.spot(k, n));
      }),
    );
  }

  /** `ids` hop down and go back to their places in the ring. */
  async stepDown(ids: number[]): Promise<void> {
    this.awardRun++;
    for (const b of this.bouquets) this.podium.unplace(b);
    this.bouquets = [];
    const n = ids.length;
    await Promise.all(
      ids.map(async (id, k) => {
        const a = this.actors[id];
        const gen = ++a.gen;
        await this.hop(a, gen, this.podium.foot(k, n));
        if (a.gen !== gen) return;
        await this.walkTo(a, gen, a.base, 3.2);
      }),
    );
  }

  /** Throw `ch` from `a` at `aim` with a little wind-up lunge towards it. */
  private async lob(a: Actor, gen: number, ch: string, aim: THREE.Vector3, land: (at: THREE.Vector3) => void, size = 1) {
    const p0 = a.sprite.position.clone();
    const dir = aim.clone().sub(p0).setY(0).normalize().multiplyScalar(0.3);
    await this.tween(0.22, (k) => {
      if (a.gen !== gen) return;
      a.sprite.position.x = p0.x + dir.x * Math.sin(k * Math.PI);
      a.sprite.position.z = p0.z + dir.z * Math.sin(k * Math.PI);
    });
    if (a.gen !== gen) return;
    const from = a.sprite.position.clone().setY(1.3);
    this.podium.throw(ch, from, aim, { dur: 0.55 + Math.random() * 0.3, arc: 1.2 + Math.random() * 1.6, size: (ch === '💩' ? 0.6 : 0.45) * size, land });
  }

  /**
   * 全场最佳: the winners walk up onto the podium and get bouquets; everyone else
   * hops and claps, tossing roses up at them while petals drift down. Resolves once
   * they are up; the cheering goes on until `stepDown`.
   */
  async awardBest(ids: number[]): Promise<void> {
    const epoch = this.epoch;
    const run = ++this.awardRun;
    const live = () => epoch === this.epoch && run === this.awardRun;
    this.podium.setup(ids.length, '🏆');
    for (const a of this.actors) if (!ids.includes(a.id)) this.cheering.add(a.id);
    await this.mount(ids);
    if (!live()) return;
    ids.forEach((_, k) => {
      const at = this.podium.spot(k, ids.length).add(new THREE.Vector3(0.42, 0.95, 0.3));
      this.bouquets.push(this.podium.place('💐', at, 0.6));
    });
    this.podium.sprinkle(['🌸', '🌼', '🌷', '✨'], 60);
    // roses thrown up from the crowd, landing on the carpet at the winners' feet
    const fans = this.actors.filter((a) => !ids.includes(a.id));
    fans.forEach((a) => {
      void (async () => {
        await this.wait(Math.random() * 1.5);
        let flowers = 0;
        while (live() && flowers++ < 4) {
          const k = Math.floor(Math.random() * ids.length);
          const aim = this.podium.spot(k, ids.length).add(new THREE.Vector3((Math.random() - 0.5) * 0.9, 0.08, 0.3 + Math.random() * 0.5));
          await this.lob(a, a.gen, '🌹', aim, (at) => live() && this.bouquets.push(this.podium.place('🌹', at, 0.3)));
          await this.wait(1.5 + Math.random() * 3);
        }
      })();
    });
    void (async () => {
      while (live()) {
        await this.wait(2.2);
        if (live()) this.podium.sprinkle(['🌸', '🌼', '🌷', '✨'], 25);
      }
    })();
  }

  /** 全场最差: the podium turns its sign to 💩 and the losers walk up onto it. */
  async awardWorst(ids: number[]): Promise<void> {
    const epoch = this.epoch;
    ++this.awardRun;
    this.cheering.clear();
    this.podium.setup(ids.length, '💩');
    this.fx.puff(PODIUM_POS.clone().setY(0.4));
    this.sounds?.thud(0.6);
    await this.mount(ids);
    if (epoch !== this.epoch) return;
  }

  /** The poop storm under way: who is on the podium, and each thrower's command (for the human's own throws). */
  private storm: { targets: Actor[]; live: () => boolean; ready: Map<number, number> } | null = null;

  /**
   * Everyone else gathers round the podium in a ring and pelts the losers with
   * 💩, hopping and jeering — until the next game. `manual` (the human's seat)
   * only throws when told to (`throwPoop`). Resolves once the ring has formed.
   */
  async poopStorm(worst: number[], manual: number | null = null): Promise<void> {
    const epoch = this.epoch;
    const run = ++this.awardRun;
    const live = () => epoch === this.epoch && run === this.awardRun;
    this.awardShot(26);
    const throwers = this.actors.filter((a) => !worst.includes(a.id));
    const rx = Math.max(3.6, this.podium.width / 2 + 1.5);
    const rz = 3.2;
    const storm = { targets: worst.map((id) => this.actors[id]), live, ready: new Map<number, number>() };
    this.storm = storm;
    const arrived = throwers.map(async (a, k) => {
      const gen = ++a.gen;
      this.cheering.delete(a.id);
      // spread round the podium, starting at the front (towards the camera)
      const th = Math.PI / 2 + ((k + 0.5) / throwers.length) * Math.PI * 2;
      const spot = new THREE.Vector3(PODIUM_POS.x + Math.cos(th) * rx, 0, PODIUM_POS.z + Math.sin(th) * rz);
      await this.wait(Math.random() * 0.8);
      if (!(await this.walkTo(a, gen, spot, 3.4)) || !live()) return;
      this.cheering.add(a.id);
      storm.ready.set(a.id, gen);
      if (a.id === manual) return;
      void (async () => {
        while (live() && a.gen === gen) {
          await this.wait(0.2 + Math.random() * 0.7);
          if (live()) await this.pelt(a, gen);
        }
      })();
    });
    await Promise.all(arrived);
  }

  /** The human throws one 💩 from `id`'s place in the ring; false if they are not in the ring (yet). */
  throwPoop(id: number): boolean {
    const st = this.storm;
    const gen = st?.ready.get(id);
    if (!st || gen === undefined || !st.live() || this.actors[id].gen !== gen) return false;
    void this.pelt(this.actors[id], gen, 1.25);
    return true;
  }

  /** One 💩 from `a` at someone on the podium: half hit them, the rest splat on the carpet round their feet. */
  private async pelt(a: Actor, gen: number, size = 1) {
    const st = this.storm!;
    const live = st.live;
    const v = st.targets[Math.floor(Math.random() * st.targets.length)];
    const stand = v.sprite.position.clone();
    const aim = Math.random() < 0.5
      ? stand.clone().add(new THREE.Vector3((Math.random() - 0.5) * 0.7, 0.35 + Math.random() * 1.3, 0.2))
      : stand.clone().add(new THREE.Vector3((Math.random() - 0.5) * 1.6, 0.12, (Math.random() - 0.5) * 1.6));
    await this.lob(a, gen, '💩', aim, (at) => {
      if (!live()) return;
      this.podium.place('💩', at, (0.32 + Math.random() * 0.12) * size);
      this.sounds?.splat(this.falloff(at) * 0.8 * size);
      // the one hit flinches
      void this.tween(0.25, (k) => {
        if (!live()) return;
        v.sprite.position.x = stand.x + Math.sin(k * Math.PI * 4) * 0.08;
      });
    }, size);
  }

  /** Awards: the crowd hops and claps on the spot, facing the podium. */
  private cheerStep(t: number) {
    const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
    for (const id of this.cheering) {
      const a = this.actors[id];
      if (!a.sprite.visible) continue;
      const hop = Math.sin(t * 9 + a.phase * 3);
      a.sprite.position.y = Math.abs(hop) * 0.14;
      a.sprite.scale.x = PODIUM_POS.clone().sub(a.sprite.position).dot(right) >= 0 ? 1 : -1;
      setFrame((a.sprite.material as THREE.MeshStandardMaterial).map!, hop > 0.4 ? 1 : hop < -0.4 ? 2 : 0);
    }
  }

  resetAll() {
    this.epoch++;
    this.badgeHolder = null;
    this.badgeBusy = false;
    this.badge.visible = false;
    this.badge.scale.set(1, 1, 1);
    this.ending = null;
    this.dance = 0;
    this.atmo.clear = 0;
    this.atmo.blood = 0;
    this.atmo.fair = 0;
    this.spring.reset();
    this.podium.clear();
    this.cheering.clear();
    this.bouquets = [];
    this.storm = null;
    this.awardRun++;
    for (const a of this.actors) {
      a.status = 'alive';
      a.gen++;
      a.pose = 1;
      a.sprite.visible = true;
      a.sprite.position.copy(a.base);
      a.wolf.visible = false;
      a.grave.visible = false;
      a.grave.position.copy(a.base);
      for (const body of [a.sprite, a.wolf]) {
        body.rotation.z = 0;
        body.scale.set(1, 1, 1);
        setFrame((body.material as THREE.MeshStandardMaterial).map!, 0);
      }
    }
    this.fx.reset();
    this.shot = null;
    this.shield.hide();
    this.magic.reset();
    this.aimAt = null;
    this.reticle.stop();
    for (const skin of this.skins.values()) skin.restore();
    this.skins.clear();
    for (const h of this.town.houses) {
      h.group.visible = true;
      h.lit = true;
      h.state = 'normal';
      h.seal.visible = false;
      h.debris.visible = false;
      h.doorPivot.visible = true;
      h.doorPivot.rotation.set(0, 0, 0);
    }
    this.focus(null);
  }

  /** World position that labels / focus should follow, or null if hidden indoors. */
  private anchor(a: Actor): THREE.Vector3 | null {
    if (a.wolf.visible) return a.wolf.position;
    if (a.sprite.visible) return a.sprite.position;
    if (a.status === 'dead') return a.grave.position;
    return null;
  }

  private async goInside(a: Actor, gen: number, body: THREE.Mesh, delay: number) {
    const h = this.town.houses[a.id];
    await this.wait(delay);
    if (a.gen !== gen || !body.visible) return;
    await this.walk(body, h.doorstep, () => a.gen === gen);
    if (a.gen !== gen) return;
    await this.swingDoor(h, true);
    await this.walk(body, h.inside, () => a.gen === gen);
    body.visible = false;
    await this.swingDoor(h, false);
  }

  private async comeOutside(a: Actor, gen: number, body: THREE.Mesh, dest: THREE.Vector3, delay: number) {
    const h = this.town.houses[a.id];
    await this.wait(delay);
    if (a.gen !== gen || a.status !== 'alive') return;
    if (body.visible) {
      await this.walk(body, dest, () => a.gen === gen);
      return;
    }
    await this.swingDoor(h, true);
    if (a.gen !== gen) return;
    body.position.copy(h.inside);
    body.visible = true;
    await this.walk(body, h.doorstep, () => a.gen === gen);
    void this.swingDoor(h, false);
    await this.walk(body, dest, () => a.gen === gen);
  }

  private async swingDoor(h: HouseRefs, open: boolean) {
    if (h.state !== 'normal') return;
    const from = h.doorPivot.rotation.y;
    const to = open ? 1.45 : 0;
    if (open) this.sounds?.doorOpen(this.falloff(h.doorstep));
    await this.tween(open ? 0.55 : 0.4, (k) => this.setDoor(h, from + (to - from) * (open ? k : k * k)));
    if (!open) this.sounds?.doorClose(this.falloff(h.doorstep));
  }

  private setDoor(h: HouseRefs, angle: number) {
    h.doorPivot.rotation.y = angle;
  }

  /** Walk `body` to `to` with a stepping bob; stops early if `alive()` turns false. */
  private walk(body: THREE.Mesh, to: THREE.Vector3, alive: () => boolean, speed = WALK_SPEED): Promise<void> {
    const from = body.position.clone().setY(0);
    const dist = from.distanceTo(to);
    if (dist < 0.05) return Promise.resolve();
    const dir = to.clone().sub(from);
    const tex = (body.material as THREE.MeshStandardMaterial).map!;
    return this.tween(dist / speed, (k) => {
      if (!alive()) return;
      body.position.lerpVectors(from, to, k);
      // one step per half-period of the bob: the foot is up (frame 1 / 2, alternating) mid-step
      const steps = (k * dist * 3.2) / Math.PI;
      const mid = Math.abs((steps % 1) - 0.5) < 0.3;
      setFrame(tex, mid ? (Math.floor(steps) % 2 ? 2 : 1) : 0);
      body.position.y = Math.abs(Math.sin(k * dist * 3.2)) * 0.09;
      const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrixWorld, 0);
      body.scale.x = dir.dot(right) >= 0 ? 1 : -1;
    }).then(() => {
      if (!alive()) return;
      body.position.y = 0;
      setFrame(tex, 0);
    });
  }

  private tweens: Tween[] = [];

  private tween(dur: number, fn: (k: number) => void): Promise<void> {
    return new Promise((resolve) => this.tweens.push({ t: 0, dur: Math.max(dur, 0.001), fn, resolve }));
  }

  private wait(sec: number) {
    return this.tween(sec, () => {});
  }

  private stepTweens(dt: number) {
    const done: Tween[] = [];
    for (const tw of this.tweens) {
      tw.t += dt;
      const k = Math.min(1, tw.t / tw.dur);
      tw.fn(k);
      if (k >= 1) done.push(tw);
    }
    if (done.length) {
      this.tweens = this.tweens.filter((x) => !done.includes(x));
      for (const d of done) d.resolve();
    }
  }

  /** 1 near the camera focus, fading with distance. */
  private falloff(p: THREE.Vector3): number {
    return THREE.MathUtils.clamp(1.2 - p.distanceTo(this.camTarget) / 45, 0.45, 1);
  }

  private selfLit(m: THREE.Mesh, tex: THREE.Texture): THREE.Mesh {
    const sm = m.material as THREE.MeshStandardMaterial;
    sm.emissiveMap = tex;
    sm.emissive = new THREE.Color(0xffffff);
    sm.emissiveIntensity = 0.15;
    return m;
  }

  /** Dress each seat as its persona (called when a game starts). */
  setLooks(looks: Look[]) {
    looks.forEach((look, i) => {
      const tex = sheetTexture(characterSheet(look));
      const sprite = this.actors[i].sprite;
      const m = sprite.material as THREE.MeshStandardMaterial;
      m.map?.dispose();
      m.map = tex;
      m.emissiveMap = tex;
      m.needsUpdate = true;
      (sprite.customDepthMaterial as THREE.MeshDepthMaterial).map = tex;
    });
  }

  /** Tint the human's own character slightly so they can find themselves. */
  highlightSelf(id: number) {
    this.selfId = id;
  }
  private selfId = -1;

  // ───────────────────────── internals ─────────────────────────

  private bindInput() {
    const el = this.renderer.domElement;
    // touch: one finger orbits, two fingers pinch-zoom and pan (the page itself never scrolls or zooms)
    el.style.touchAction = 'none';
    const pointers = new Map<number, { x: number; y: number }>();
    let panning = false;
    let moved = 0;
    let lx = 0;
    let ly = 0;
    let pinch: { dist: number; mx: number; my: number } | null = null;
    /** Where a one-finger gesture started (a close-up swipe is judged on release). */
    let sx = 0;
    let sy = 0;
    const pinchOf = () => {
      const [a, b] = [...pointers.values()];
      return { dist: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
    };
    const panBy = (dx: number, dy: number) => {
      // grab the ground: it follows the pointer
      const k = this.camDist * 0.0016;
      const right = new THREE.Vector3(Math.cos(this.yaw), 0, -Math.sin(this.yaw));
      const forward = new THREE.Vector3(-Math.sin(this.yaw), 0, -Math.cos(this.yaw));
      this.pan.addScaledVector(right, -dx * k).addScaledVector(forward, dy * k);
      if (this.pan.length() > MAX_PAN) this.pan.setLength(MAX_PAN);
    };
    el.addEventListener('contextmenu', (e) => e.preventDefault());
    el.addEventListener('dblclick', () => this.resetView(true));
    el.addEventListener('pointerdown', (e) => {
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      el.setPointerCapture(e.pointerId);
      if (pointers.size === 2) {
        pinch = pinchOf();
        moved = Infinity; // a two-finger gesture is never a tap
        return;
      }
      if (pointers.size > 2) return;
      // left drag orbits; right drag (or shift + left) pans
      panning = e.button === 2 || e.shiftKey;
      moved = 0;
      sx = e.clientX;
      sy = e.clientY;
      lx = e.clientX;
      ly = e.clientY;
    });
    el.addEventListener('pointermove', (e) => {
      const p = pointers.get(e.pointerId);
      if (!p) return;
      p.x = e.clientX;
      p.y = e.clientY;
      this.dragReset = false;
      if (pointers.size >= 2) {
        if (!pinch || pointers.size > 2 || this.inCloseUp) return;
        const now = pinchOf();
        if (now.dist > 0 && pinch.dist > 0) {
          this.userZoom = THREE.MathUtils.clamp(this.userZoom * (pinch.dist / now.dist), ZOOM_MIN, ZOOM_MAX);
        }
        panBy(now.mx - pinch.mx, now.my - pinch.my);
        pinch = now;
        return;
      }
      const dx = e.clientX - lx;
      const dy = e.clientY - ly;
      moved += Math.abs(dx) + Math.abs(dy);
      lx = e.clientX;
      ly = e.clientY;
      // the close-up doesn't orbit: a sideways swipe moves on to the next seat (on release)
      if (this.inCloseUp) return;
      if (panning) return panBy(dx, dy);
      this.dragYaw -= dx * 0.005;
      this.pitch = THREE.MathUtils.clamp(this.pitch + dy * 0.003, PITCH_MIN, PITCH_MAX);
      this.pitchGoal = null;
    });
    const release = (e: PointerEvent) => {
      if (!pointers.delete(e.pointerId)) return;
      if (e.type === 'pointerup' && pointers.size === 0 && moved < 6 && e.button === 0) this.pick(e);
      else if (e.type === 'pointerup' && pointers.size === 0 && this.inCloseUp && moved !== Infinity) {
        const dx = e.clientX - sx;
        if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(e.clientY - sy) * 1.2) this.onSwipe?.(dx < 0 ? 1 : -1);
      }
      pinch = null;
      // the finger left on the glass carries on orbiting from where it is
      const rest = [...pointers.values()][0];
      if (rest) {
        lx = rest.x;
        ly = rest.y;
        panning = false;
      }
    };
    el.addEventListener('pointerup', release);
    el.addEventListener('pointercancel', release);
    el.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        if (this.inCloseUp) return;
        this.userZoom = THREE.MathUtils.clamp(this.userZoom * (1 + e.deltaY * 0.001), ZOOM_MIN, ZOOM_MAX);
      },
      { passive: false },
    );
  }

  private pick(e: PointerEvent) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(ndc, this.camera);
    const hits = ray.intersectObjects(this.actors.flatMap((a) => [a.sprite, a.wolf, a.grave]).filter((m) => m.visible), false);
    if (hits.length) {
      const id = this.actors.find((a) => [a.sprite, a.wolf, a.grave].includes(hits[0].object as THREE.Mesh))!.id;
      this.onPick?.(id);
    }
  }

  /**
   * The close-up looks across the plaza at eye level, where the well, the old tree,
   * the lantern posts and other players would stand in the way: whatever is on the
   * line of sight moves to a layer the camera doesn't draw (their `visible`, which
   * the game's own state rides on, is left alone; a lantern keeps its light).
   */
  private clearSightLine(close: boolean) {
    if (!this.occluders) {
      this.occluders = [
        { meshes: this.meshesOf(this.town.well), at: () => this.town.well.position, r: 1.8, hidden: false },
        { meshes: this.meshesOf(this.town.plazaTree.group), at: () => this.town.plazaTree.group.position, r: 2.2, hidden: false },
        ...this.town.lanterns.map((l) => ({ meshes: this.meshesOf(l.parent!), at: () => l.parent!.position, r: 1.3, hidden: false })),
        ...this.actors.flatMap((a) =>
          [a.sprite, a.wolf, a.grave].map((m) => ({ meshes: [m], at: () => m.position, r: 0.9, hidden: false, actor: a.id })),
        ),
      ];
    }
    const from = new THREE.Vector2(this.camera.position.x, this.camera.position.z);
    const to = new THREE.Vector2(this.camTarget.x, this.camTarget.z);
    const seg = to.clone().sub(from);
    const len2 = Math.max(seg.lengthSq(), 1e-6);
    for (const o of this.occluders) {
      let hide = false;
      if (close && o.actor !== this.closeSeat) {
        const at = o.at();
        const p = new THREE.Vector2(at.x, at.z);
        // strictly between the camera and the subject (the subject's neighbours behind it stay)
        const k = p.clone().sub(from).dot(seg) / len2;
        // players near the camera fill much of the frame: the nearer, the wider the berth
        const r = o.actor === undefined ? o.r : o.r + 2.5 * (1 - k);
        hide = k > 0 && k < 0.85 && p.distanceTo(from.clone().addScaledVector(seg, k)) < r;
      }
      if (hide === o.hidden) continue;
      o.hidden = hide;
      for (const m of o.meshes) {
        if (hide) m.layers.set(HIDDEN_LAYER);
        else m.layers.set(0);
      }
    }
  }

  private occluders: { meshes: THREE.Object3D[]; at: () => THREE.Vector3; r: number; hidden: boolean; actor?: number }[] | null = null;

  private meshesOf(root: THREE.Object3D) {
    const out: THREE.Object3D[] = [];
    root.traverse((o) => {
      if ((o as THREE.Mesh).isMesh) out.push(o);
    });
    return out;
  }

  private resize() {
    const w = this.host.clientWidth || window.innerWidth;
    const h = this.host.clientHeight || window.innerHeight;
    const aspect = w / h;
    this.camera.aspect = aspect;
    // a tall (phone portrait) viewport keeps the landscape view's sideways reach by
    // widening the lens (up to FOV_MAX), and only backs off for the rest: backing off
    // alone would put the camera so far out that the night fog swallows the town
    const fit = aspect < 1 ? FIT_ASPECT_PORTRAIT : FIT_ASPECT;
    const want = Math.tan(THREE.MathUtils.degToRad(FOV / 2)) * Math.max(1, fit / aspect);
    const fov = Math.min(FOV_MAX, THREE.MathUtils.radToDeg(2 * Math.atan(want)));
    this.wideFov = fov;
    this.camera.fov = this.inCloseUp ? CLOSE_FOV : fov;
    this.aspectFit = want / Math.tan(THREE.MathUtils.degToRad(fov / 2));
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h);
    this.composer.setSize(w, h);
    const pr = this.renderer.getPixelRatio();
    (this.tiltH.uniforms.texel.value as THREE.Vector2).set(1 / (w * pr), 0);
    (this.tiltV.uniforms.texel.value as THREE.Vector2).set(0, 1 / (h * pr));
    this.viewH = h * pr;
    this.viewW = w;
    this.viewHcss = h;
    if (this.viewShift.lengthSq()) this.camera.setViewOffset(w, h, this.viewShift.x, this.viewShift.y, w, h);
    this.fx.setViewport(this.viewH, this.camera.fov);
  }

  private frame() {
    this.timer.update();
    const dt = this.paused ? 0 : Math.min(this.timer.getDelta(), 0.1);
    this.time += dt;
    const t = this.time;

    // day/night easing
    this.atmo.mix += (this.nightTarget - this.atmo.mix) * Math.min(1, dt * 0.8);
    const n = this.atmo.mix;
    this.atmo.update(dt, t);
    // the fog is tuned for the landscape framing: thin it as far as a tall viewport backs off
    (this.scene.fog as THREE.FogExp2).density /= this.aspectFit;
    this.fx.update(dt, t);
    this.shield.update(dt, t);
    this.magic.update(dt, t);
    this.spring.update(dt, t);
    this.podium.update(dt, t);
    this.stepTweens(dt);
    if (this.dance > 0) this.danceStep(t);
    if (this.cheering.size) this.cheerStep(t);
    for (const w of this.town.windows) w.emissiveIntensity = THREE.MathUtils.lerp(0.15, 2.2, n);
    for (const h of this.town.houses) {
      const glow = h.lit ? THREE.MathUtils.lerp(0.15, 2.2, n) : 0;
      for (const w of h.windows) w.emissiveIntensity += (glow - w.emissiveIntensity) * Math.min(1, dt * 2);
    }
    this.town.lanterns.forEach((l, i) => {
      const flicker = 0.85 + Math.sin(t * 9 + i * 3) * 0.08 + Math.sin(t * 23 + i) * 0.06;
      l.intensity = THREE.MathUtils.lerp(4, 22, n) * flicker;
    });
    this.town.lanternFlames.forEach((f, i) => {
      (f.material as THREE.MeshStandardMaterial).emissiveIntensity = 2.5 + Math.sin(t * 11 + i) * 0.6;
    });

    // characters: idle breathing; sprites stay readable at night (HD-2D style self-lit sprites)
    for (const a of this.actors) {
      for (const body of [a.sprite, a.wolf]) {
        if (!body.visible) continue;
        const sm = body.material as THREE.MeshStandardMaterial;
        sm.emissiveIntensity = THREE.MathUtils.lerp(0.12, 0.4, n) + (a.id === this.selfId ? 0.12 : 0) + (body === a.wolf ? 0.1 : 0);
        const speaking = this.focusId === a.id;
        const bob = Math.sin(t * (speaking ? 7 : 2) + a.phase);
        body.scale.y = (1 + bob * (speaking ? 0.035 : body === a.wolf ? 0.03 : 0.015)) * (body === a.wolf ? a.pose : 1);
      }
    }

    // the sheriff wears the badge by day (at night everyone is indoors)
    if (!this.badgeBusy) {
      const a = this.badgeHolder !== null ? this.actors[this.badgeHolder] : null;
      const show = !!a && a.status === 'alive' && a.sprite.visible && n < 0.5 && this.ending !== 'wolf';
      this.badge.visible = show;
      if (show) this.badge.position.copy(a!.sprite.position).setY(BADGE_Y + Math.sin(t * 2.2 + a!.phase) * 0.05);
    }

    // camera
    const focusedActor = this.focusId !== null ? this.actors[this.focusId] : null;
    const focusPos = focusedActor ? this.anchor(focusedActor) : null;
    const focused = focusPos ? { base: focusPos.clone().setY(0) } : null;
    if (this.shot && t > this.shot.until) this.shot = null;
    const close = this.inCloseUp;
    let closeAt: THREE.Vector3 | null = null;
    let closeDist = 0;
    // the free part of the screen (below the top bar, above / beside the panels)
    const W = this.viewW;
    const H = this.viewHcss;
    const fr = this.free ?? { left: 0, top: 0, right: W, bottom: H };
    const freeH = Math.max(80, fr.bottom - fr.top);
    if (close) {
      // stand before the seat, looking out from the plaza: at them, their grave, or their door if indoors
      const a = this.actors[this.closeSeat];
      const at = this.anchor(a);
      // indoors: step back far enough to take in the whole house
      const subj = !at ? CLOSE_SUBJECT.house : a.wolf.visible ? CLOSE_SUBJECT.wolf : CLOSE_SUBJECT.person;
      closeAt = (at ?? this.town.houses[a.id].doorstep).clone().setY(subj.aim);
      // near enough that the subject fills most of the free height
      const tan = Math.tan(THREE.MathUtils.degToRad(CLOSE_FOV / 2));
      closeDist = THREE.MathUtils.clamp(subj.h / (CLOSE_FILL * (freeH / H) * 2 * tan), CLOSE_DIST_MIN, CLOSE_DIST_MAX);
      this.yawGoal = Math.atan2(-closeAt.x, -closeAt.z) + CLOSE_YAW;
      this.pitchGoal = CLOSE_PITCH;
      if (!this.wasClose) {
        this.pan.set(0, 0, 0);
        this.dragYaw = Math.atan2(Math.sin(this.dragYaw), Math.cos(this.dragYaw));
        this.dragReset = true;
      }
    } else if (this.wasClose && !this.shot) {
      // back to the overview: frame the speaker as usual
      if (this.focusId !== null) this.reframe(this.focusId);
      else this.resetView(false);
    }
    this.wasClose = close;
    // centre the subject in the free part of the screen, not behind the panels
    const shiftGoal = close ? new THREE.Vector2(W / 2 - (fr.left + fr.right) / 2, H / 2 - (fr.top + fr.bottom) / 2) : new THREE.Vector2();
    if (this.viewShift.distanceTo(shiftGoal) > 0.5) {
      this.viewShift.lerp(shiftGoal, Math.min(1, dt * 2.5));
      if (this.viewShift.length() < 0.5) {
        this.viewShift.set(0, 0);
        this.camera.clearViewOffset();
      } else this.camera.setViewOffset(W, H, this.viewShift.x, this.viewShift.y, W, H);
    }
    const fovGoal = close ? CLOSE_FOV : this.wideFov;
    if (Math.abs(this.camera.fov - fovGoal) > 0.01) {
      this.camera.fov += (fovGoal - this.camera.fov) * Math.min(1, dt * 2);
      this.camera.updateProjectionMatrix();
      this.fx.setViewport(this.viewH, this.camera.fov);
    }
    const tgt = closeAt
      ? closeAt
      : this.shot
        ? this.shot.target.clone().add(this.pan)
        : (focused ? focused.base.clone().setY(1.2).multiplyScalar(0.5) : new THREE.Vector3(0, 1, 0)).add(this.pan);
    this.camTarget.lerp(tgt, Math.min(1, dt * (close ? 2.4 : 1.6)));
    // stay wide enough to keep most of the ring (and their bubbles) in view (see `resize`)
    const dist = close ? closeDist : (this.shot ? this.shot.dist : focused ? 34 : 40) * this.userZoom * this.aspectFit;
    this.camDist += (dist - this.camDist) * Math.min(1, dt * 1.4);
    const sway = Math.sin(t * 0.05) * (close ? 0.03 : 0.12);
    {
      // ease the framed heading the shortest way round (the idle sway is part of the final angle)
      const want = this.yawGoal - sway;
      const diff = Math.atan2(Math.sin(want - this.baseYaw), Math.cos(want - this.baseYaw));
      this.baseYaw += diff * Math.min(1, dt * 2.2);
    }
    if (this.dragReset) {
      this.dragYaw += (0 - this.dragYaw) * Math.min(1, dt * 2.2);
      if (Math.abs(this.dragYaw) < 0.002) this.dragReset = false;
    }
    if (this.pitchGoal !== null) {
      this.pitch += (this.pitchGoal - this.pitch) * Math.min(1, dt * 2.2);
      if (Math.abs(this.pitchGoal - this.pitch) < 0.002) this.pitchGoal = null;
    }
    this.yaw = this.baseYaw + this.dragYaw + sway;
    const cp = this.pitch;
    this.camera.position.set(
      this.camTarget.x + Math.sin(this.yaw) * Math.cos(cp) * this.camDist,
      this.camTarget.y + Math.sin(cp) * this.camDist,
      this.camTarget.z + Math.cos(this.yaw) * Math.cos(cp) * this.camDist,
    );
    this.camera.lookAt(this.camTarget);
    this.clearSightLine(close);
    if (this.fx.shake > 0.01) {
      const s = this.fx.shake;
      this.camera.position.add(new THREE.Vector3((Math.random() - 0.5) * s, (Math.random() - 0.5) * s, (Math.random() - 0.5) * s));
      this.camera.rotateZ((Math.random() - 0.5) * s * 0.03);
    }
    this.camera.updateMatrixWorld();

    // focus ring/spot
    const ringMat = this.focusRing.material as THREE.MeshBasicMaterial;
    if (focused) {
      this.focusRing.position.set(focused.base.x, 0.05, focused.base.z);
      ringMat.opacity += (0.85 - ringMat.opacity) * Math.min(1, dt * 4);
      this.focusRing.scale.setScalar(1 + Math.sin(t * 4) * 0.06);
      this.focusLight.position.set(focused.base.x * 0.7, 9, focused.base.z * 0.7);
      this.focusLight.target.position.copy(focused.base);
      this.focusLight.intensity += (60 - this.focusLight.intensity) * Math.min(1, dt * 3);
    } else {
      ringMat.opacity += (0 - ringMat.opacity) * Math.min(1, dt * 4);
    }
    if (closeAt && !focused) {
      // the close-up's subject (or their house, at night) is never left in the dark
      const base = closeAt.clone().setY(0);
      this.focusLight.position.set(base.x * 0.7, 9, base.z * 0.7);
      this.focusLight.target.position.copy(base);
      this.focusLight.intensity += (45 - this.focusLight.intensity) * Math.min(1, dt * 3);
    } else if (!focused) this.focusLight.intensity += (0 - this.focusLight.intensity) * Math.min(1, dt * 3);

    // billboards face camera around Y
    for (const b of this.billboards) {
      const wp = b.getWorldPosition(new THREE.Vector3());
      const parentRot = b.parent && b.parent !== this.scene ? b.parent.rotation.y : 0;
      b.rotation.y = Math.atan2(this.camera.position.x - wp.x, this.camera.position.z - wp.z) - parentRot;
    }

    this.animals.update(t, this.camera, n);

    // post
    const ft = this.camTarget.clone().project(this.camera);
    const focusY = THREE.MathUtils.clamp((ft.y + 1) / 2, 0.2, 0.8);
    for (const p of [this.tiltH, this.tiltV]) {
      p.uniforms.focus.value += (focusY - p.uniforms.focus.value) * Math.min(1, dt * 3);
      // keep the towering cloud sharp instead of lost in the tilt-shift blur
      p.uniforms.band.value = close ? 0.3 : this.shot ? this.shot.band : focused ? 0.1 : 0.16;
    }
    this.bloom.strength = THREE.MathUtils.lerp(0.35, 0.9, n) + this.atmo.lightning * 0.4 + this.fx.flash * 0.4;
    this.grade.uniforms.time.value = t;
    this.grade.uniforms.night.value = n;
    this.grade.uniforms.flash.value = this.atmo.lightning * n + this.fx.flash * 0.8;
    this.renderer.toneMappingExposure = THREE.MathUtils.lerp(1.05, 1.25, n) + this.atmo.clear * 0.2;

    this.composer.render(dt);

    if (this.aimAt) {
      const p = this.aimAt.clone().project(this.camera);
      this.reticle.update(dt, ((p.x + 1) / 2) * this.host.clientWidth, ((1 - p.y) / 2) * this.host.clientHeight);
    }

    if (this.onFrame) {
      const rect = this.renderer.domElement.getBoundingClientRect();
      const out: ScreenPos[] = this.actors.map((a) => {
        const anchor = this.anchor(a);
        if (!anchor) return { x: 0, y: 0, visible: false };
        const badged = this.badge.visible && !this.badgeBusy && this.badgeHolder === a.id;
        const height = a.wolf.visible ? 2.7 : a.status === 'dead' ? 1.3 : badged ? 2.8 : 2.25;
        const p = anchor.clone().setY((anchor.y > PODIUM_H / 2 ? PODIUM_H : 0) + height).project(this.camera);
        return {
          x: rect.left + ((p.x + 1) / 2) * rect.width,
          y: rect.top + ((1 - p.y) / 2) * rect.height,
          visible: p.z < 1 && Math.abs(p.x) < 1.1 && Math.abs(p.y) < 1.1,
        };
      });
      this.onFrame(out);
    }
  }
}
