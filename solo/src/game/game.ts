import { Rng } from './rng';
import {
  EXPLODE_CHOICE,
  GOD_ROLES,
  GUN_ROLES,
  ROLE_NAME,
  STANDARD_BOARD,
  isWolf,
  seat,
  type Agent,
  type DeathCause,
  type Decision,
  type DecisionRequest,
  type EventType,
  type GameEvent,
  type Phase,
  type Player,
  type PlayerView,
  type Role,
  type SpeechKind,
  type SpeechOrder,
  type TargetAction,
  type TargetRequest,
  type Visibility,
  type Winner,
} from './types';

export interface GameOptions {
  names: string[];
  /** Index of the human seat, or -1 for all-AI (tests / spectating). */
  humanSeat: number;
  /** Force the human's role (dev); otherwise random. */
  humanRole?: Role;
  seed?: number;
  /** Max wolf-channel chat rounds per night (brief: 5). */
  wolfChatRounds?: number;
  /** Pause between GM steps, ms (0 in tests). */
  paceMs?: number;
  /**
   * Resume a saved game: these recorded answers are fed back instantly (no agent
   * calls, no pacing, no scene cues) before live play continues. Needs the same `seed`.
   */
  replay?: Decision[];
}

export interface GameState {
  day: number;
  phase: Phase;
  players: Player[];
  /** Player currently being asked to act/speak, for highlighting. */
  actor: number | null;
  actorLabel: string;
  /** The actor is making a speech (not a pick): the HUD keeps the last one readable meanwhile. */
  actorSpeaks: boolean;
  winner: Winner;
  witch: { hasAntidote: boolean; hasPoison: boolean };
  /** The hunter's one shot is spent (fired, or lost to poison). */
  hunterShot: boolean;
  /** Same for the 狼王's gun (also lost to a 自爆). */
  wolfKingShot: boolean;
  lastGuarded: number | null;
  /** Seer knowledge: target -> 好人 / 狼人 (told at once, during the seer's turn). */
  seerChecks: Record<number, 'good' | 'wolf'>;
  /** Which night role is currently awake (public knowledge, drives scene/audio). */
  nightStep: NightStep | null;
  /** 警长 (public): wears the badge until they die; null before the election or once the badge is gone. */
  sheriff: number | null;
}

/** Wake order: 守卫 → 狼人 → 女巫 → 预言家 → 猎人 (the 狼王 has no turn of its own: it wakes with the pack). */
export type NightStep = 'guard' | 'wolves' | 'witch' | 'seer' | 'hunter';

/**
 * A night role's action (or a 猎人 / 狼王 shot), staged for whoever may see it:
 * night ones only for the acting player (or god view), the day shot for everyone.
 */
export interface RoleCue {
  kind: 'seer' | 'guard' | 'witchSave' | 'witchPoison' | 'dayShot';
  actor: number;
  target: number;
}

/**
 * The sheriff's badge moves (public): `from` null = just elected (it flies in),
 * `to` null = torn up / lost with its wearer.
 */
export interface BadgeCue {
  kind: 'badge';
  from: number | null;
  to: number | null;
}

/** Moments where the GM waits for the scene to finish animating. */
export type SceneCue = 'nightfall' | 'wolvesOut' | 'wolvesIn' | 'dawn' | 'explode' | RoleCue | BadgeCue;

export interface GameHooks {
  onEvent?(e: GameEvent): void;
  onState?(s: GameState): void;
  /** Resolve when the scene has played the cue (e.g. every wolf is back indoors). */
  cue?(c: SceneCue): Promise<void>;
  /** Resumed game only: the replay has caught up, live play starts now. */
  restored?(): void;
  /** A day speech is ready: resolve to let it be heard (the host may hold it until the human has read the last one). */
  beforeSpeech?(speaker: number): Promise<void>;
  /** The exile has had their last words (and the hunter's shot): resolve to send them off and start the night. */
  confirmExile?(id: number): Promise<void>;
}

export class GameAborted extends Error {}
/** A save's journal no longer fits the engine (e.g. saved by an older version). */
export class ReplayMismatch extends Error {}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export class Game {
  readonly rng: Rng;
  /** Deal/flow seed; with `journal` it is everything needed to rebuild the game. */
  readonly seed: number;
  readonly state: GameState;
  readonly events: GameEvent[] = [];
  /** Every answer given so far (replayed ones included). */
  readonly journal: Decision[] = [];
  private agents: Agent[] = [];
  private aborted = false;
  private paused = false;
  private pauseWaiters: (() => void)[] = [];
  private readonly replay: Decision[];
  private cursor = 0;
  private restoring: boolean;
  private readonly wolfChatRounds: number;
  private paceMs: number;

  constructor(private opts: GameOptions, private hooks: GameHooks = {}) {
    this.seed = opts.seed ?? (Math.random() * 2 ** 32) >>> 0;
    this.rng = new Rng(this.seed);
    this.replay = opts.replay ?? [];
    this.restoring = opts.replay !== undefined;
    this.wolfChatRounds = opts.wolfChatRounds ?? 3;
    this.paceMs = opts.paceMs ?? 0;
    const roles = this.dealRoles();
    this.state = {
      day: 0,
      phase: 'setup',
      players: roles.map((role, id) => ({
        id,
        name: opts.names[id] ?? `玩家${id + 1}`,
        role,
        alive: true,
        isHuman: id === opts.humanSeat,
      })),
      actor: null,
      actorLabel: '',
      actorSpeaks: false,
      winner: null,
      witch: { hasAntidote: true, hasPoison: true },
      hunterShot: false,
      wolfKingShot: false,
      lastGuarded: null,
      seerChecks: {},
      nightStep: null,
      sheriff: null,
    };
  }

  private dealRoles(): Role[] {
    const { humanSeat, humanRole } = this.opts;
    if (humanSeat >= 0 && humanRole) {
      const rest = STANDARD_BOARD.slice();
      rest.splice(rest.indexOf(humanRole), 1);
      const shuffled = this.rng.shuffle(rest);
      shuffled.splice(humanSeat, 0, humanRole);
      return shuffled;
    }
    return this.rng.shuffle(STANDARD_BOARD);
  }

  setAgents(agents: Agent[]) {
    this.agents = agents;
  }

  abort() {
    this.aborted = true;
    this.release();
  }

  /** 配置 changed the pace mid-game. */
  setPace(ms: number) {
    this.paceMs = ms;
  }

  /** Freeze the GM: it stops at its next step (an answer arriving meanwhile waits too). */
  pause() {
    this.paused = true;
  }

  resume() {
    this.paused = false;
    this.release();
  }

  get isPaused() {
    return this.paused;
  }

  /** Still fast-forwarding through a save's journal. */
  get replaying() {
    return this.cursor < this.replay.length;
  }

  private release() {
    const waiters = this.pauseWaiters;
    this.pauseWaiters = [];
    for (const r of waiters) r();
  }

  private async gate() {
    this.guard();
    while (this.paused) {
      await new Promise<void>((r) => this.pauseWaiters.push(r));
      this.guard();
    }
  }

  /** Tell the host once the replay has caught up (at the first live step: pace, cue or question). */
  private checkRestored() {
    if (!this.restoring || this.replaying) return;
    this.restoring = false;
    this.hooks.restored?.();
  }

  // ───────────────────────── queries ─────────────────────────

  get players() {
    return this.state.players;
  }
  alive(): Player[] {
    return this.players.filter((p) => p.alive);
  }
  aliveIds(): number[] {
    return this.alive().map((p) => p.id);
  }
  byRole(role: Role): Player | undefined {
    return this.players.find((p) => p.role === role);
  }
  wolves(): Player[] {
    return this.players.filter((p) => isWolf(p.role));
  }

  /** How everyone who is out went out (god view: for the post-game ceremony). */
  deathCauses(): Record<number, DeathCause> {
    return Object.fromEntries(this.causes);
  }

  /**
   * 屠边: wolves win once every god (神职) or every villager is out. Good wins only
   * once every wolf is out. Both at once (the same night) goes to the wolves (狼刀在先).
   */
  checkWinner(): Winner {
    const alive = this.alive();
    const gods = alive.filter((p) => GOD_ROLES.includes(p.role)).length;
    const villagers = alive.filter((p) => p.role === 'villager').length;
    if (gods === 0 || villagers === 0) return 'wolf';
    if (!alive.some((p) => isWolf(p.role))) return 'good';
    return null;
  }

  /** What `id` is allowed to know. Used by both AI prompts and the human UI. */
  viewFor(id: number): PlayerView {
    const self = this.players[id];
    const known: PlayerView['known'] = { [id]: self.role };
    // the pack knows each other, the 狼王 included
    if (isWolf(self.role)) for (const w of this.wolves()) known[w.id] = w.role;
    if (self.role === 'seer') Object.assign(known, this.state.seerChecks, { [id]: 'seer' });
    if (this.state.phase === 'ended') for (const p of this.players) known[p.id] = p.role;
    return {
      self: { ...self },
      day: this.state.day,
      phase: this.state.phase,
      players: this.players.map((p) => ({ id: p.id, name: p.name, alive: p.alive })),
      known,
      events: this.events.filter((e) => canSee(e.visibility, id)),
      sheriff: this.state.sheriff,
      witch: self.role === 'witch' ? { ...this.state.witch } : undefined,
      hunter: self.role === 'hunter' ? { hasShot: this.state.hunterShot } : undefined,
      wolfKing: self.role === 'wolfKing' ? { hasShot: this.state.wolfKingShot } : undefined,
      guard: self.role === 'guard' ? { lastGuarded: this.state.lastGuarded } : undefined,
    };
  }

  // ───────────────────────── plumbing ─────────────────────────

  private emit(
    type: EventType,
    text: string,
    visibility: Visibility = { kind: 'public' },
    extra: Partial<GameEvent> = {},
  ): GameEvent {
    const e: GameEvent = {
      seq: this.events.length,
      day: this.state.day,
      phase: this.state.phase,
      type,
      text,
      visibility,
      ...extra,
    };
    this.events.push(e);
    this.hooks.onEvent?.(e);
    return e;
  }

  private gm(text: string, to?: number[]) {
    return this.emit(to ? 'private' : 'gm', text, to ? { kind: 'private', to } : { kind: 'public' });
  }

  private publish() {
    this.hooks.onState?.(this.state);
  }

  private setPhase(phase: Phase) {
    this.state.phase = phase;
    this.publish();
  }

  private announceStep(step: NightStep, text: string) {
    this.setNightStep(step);
    this.gm(text);
  }

  /** A turn whose role is dead / has nothing to do: wait a plausible while. */
  private async idleTurn() {
    await this.pace(2 + this.rng.next() * 3);
  }

  private setNightStep(step: NightStep | null) {
    this.state.nightStep = step;
    this.publish();
  }

  private setActor(id: number | null, label = '', speaks = false) {
    this.state.actor = id;
    this.state.actorLabel = label;
    this.state.actorSpeaks = speaks;
    this.publish();
  }

  private async pace(mult = 1) {
    await this.gate();
    if (this.replaying) return;
    this.checkRestored();
    if (this.paceMs > 0) await sleep(this.paceMs * mult);
    await this.gate();
  }

  private async cue(c: SceneCue) {
    await this.gate();
    if (this.replaying) return;
    this.checkRestored();
    if (this.hooks.cue) await this.hooks.cue(c);
    await this.gate();
  }

  private guard() {
    if (this.aborted) throw new GameAborted();
  }

  /** The agent's answer, or the recorded one while replaying a save. Journaled either way. */
  private async answer(id: number, req: DecisionRequest): Promise<Decision> {
    if (this.replaying) {
      const rec = this.replay[this.cursor++];
      if ((req.kind === 'target') !== 't' in rec) throw new ReplayMismatch(`存档第 ${this.cursor} 步与当前规则不符`);
      this.journal.push(rec);
      return rec;
    }
    this.checkRestored();
    await this.gate();
    const agent = this.agents[id];
    const view = this.viewFor(id);
    let rec: Decision;
    if (req.kind === 'target') rec = { t: await agent.choose(req, view) };
    else {
      const res = await agent.speak(req, view);
      if (typeof res === 'string') rec = { s: res };
      else {
        rec = { s: res.text };
        if (res.fallback) rec.fb = true;
        if (res.explode && req.kind === 'speech' && req.canExplode) rec.x = true;
      }
    }
    this.guard();
    // journal before waiting out a pause, so a save made meanwhile keeps this answer
    this.journal.push(rec);
    await this.gate();
    return rec;
  }

  private async ask(id: number, req: DecisionRequest, label: string): Promise<string | number | null> {
    this.guard();
    this.setActor(id, label, req.kind === 'speech');
    try {
      const rec = await this.answer(id, req);
      if ('t' in rec) {
        const choice = rec.t;
        const { candidates, allowSkip } = req as TargetRequest;
        if (choice === null) {
          if (allowSkip) return null;
          return this.rng.pick(candidates);
        }
        if (choice === EXPLODE_CHOICE && (req as TargetRequest).canExplode) return choice;
        if (!candidates.includes(choice)) {
          this.emit('system', `${seat(id)}给出非法目标 ${choice + 1}，已随机替换`, { kind: 'private', to: [id] });
          return allowSkip ? null : this.rng.pick(candidates);
        }
        return choice;
      }
      this.lastSpeechFallback = !!rec.fb;
      this.lastSpeechExplode = !!rec.x;
      return rec.s.trim() || '（沉默）';
    } finally {
      this.setActor(null);
    }
  }

  private askTarget(id: number, action: TargetAction, candidates: number[], allowSkip: boolean, prompt: string, label: string, canExplode = false) {
    const req: TargetRequest = { kind: 'target', action, day: this.state.day, candidates, allowSkip, prompt, ...(canExplode ? { canExplode } : {}) };
    return this.ask(id, req, label) as Promise<number | null>;
  }

  private kill(id: number, cause: DeathCause) {
    const p = this.players[id];
    if (!p.alive) return;
    p.alive = false;
    this.causes.set(id, cause);
    const shown = cause === 'vote' || cause === 'explode' ? cause : 'hidden';
    this.emit('death', `${seat(id)} ${p.name} 出局`, { kind: 'public' }, { data: { id, cause: shown } });
  }

  /** Returns true if the game ended. */
  private endIfWon(): boolean {
    if (this.state.phase === 'ended') return true; // already announced (e.g. by the exile, then again by the main loop)
    const w = this.checkWinner();
    if (!w) return false;
    this.state.winner = w;
    this.setPhase('ended');
    const roster = this.players.map((p) => `${seat(p.id)}${p.name}：${ROLE_NAME[p.role]}`).join('，');
    const godsLeft = this.alive().some((p) => GOD_ROLES.includes(p.role));
    this.gm(
      w === 'good'
        ? '所有狼人均已出局，好人阵营获胜！'
        : `${godsLeft ? '所有村民' : '所有神职'}均已出局，狼人屠边成功，狼人阵营获胜！`,
    );
    this.gm(`身份公开：${roster}`);
    return true;
  }

  // ───────────────────────── main loop ─────────────────────────

  async run(): Promise<Winner> {
    if (this.agents.length !== this.players.length) throw new Error('agents not set');
    for (const p of this.players) {
      let text = `你是 ${seat(p.id)}，身份：${ROLE_NAME[p.role]}。`;
      if (isWolf(p.role)) {
        text += `狼队友：${this.wolves().filter((w) => w.id !== p.id).map((w) => `${seat(w.id)}${w.role === 'wolfKing' ? '（狼王）' : ''}`).join('、')}。`;
      }
      this.gm(text, [p.id]);
    }
    this.gm('游戏开始。12 人局（狼王守卫）：狼王 + 3 狼人、4 村民、预言家、女巫、猎人、守卫。狼人屠边（神职全灭或村民全灭）获胜，好人放逐全部狼人获胜。');
    while (true) {
      this.state.day += 1;
      const deaths = await this.night();
      await this.dayPhase(deaths);
      if (this.over) break;
    }
    this.checkRestored();
    return this.state.winner;
  }

  private get over() {
    return this.state.phase === 'ended';
  }

  // ───────────────────────── night ─────────────────────────

  /** Plays the night; returns who died (announced at daybreak, after the election on day 1). */
  private async night(): Promise<{ id: number; cause: DeathCause }[]> {
    this.setPhase('night');
    const day = this.state.day;
    this.gm(`第 ${day} 夜，天黑请闭眼。`);
    await this.cue('nightfall');
    await this.pace(2);

    // every turn is announced every night, even if that role is gone (no information leak)

    // 1. 守卫：可以守自己，不能连续两晚守同一人，可以空守
    const guardP = this.byRole('guard');
    let guarded: number | null = null;
    this.announceStep('guard', '守卫请睁眼，请选择要守护的玩家。');
    if (!guardP?.alive) await this.idleTurn();
    else {
      const last = this.state.lastGuarded;
      const cands = this.aliveIds().filter((i) => i !== last);
      guarded = await this.askTarget(guardP.id, 'guard', cands, true, `可以守护自己，不能连续两晚守护同一人（上一晚守护：${last === null ? '无' : seat(last)}）。可以空守。`, '守卫守护中');
      if (guarded === null) this.gm('你今晚选择空守。', [guardP.id]);
      else {
        this.emit('private', `你今晚守护了 ${seat(guarded)}。`, { kind: 'private', to: [guardP.id] }, { data: { guard: guarded } });
        await this.cue({ kind: 'guard', actor: guardP.id, target: guarded });
      }
      await this.pace();
    }
    this.state.lastGuarded = guarded;

    // 2. 狼人
    const knife = await this.wolfTurn();

    // 3. 女巫：只知道刀口（金水用掉后不再知道）；首夜之外不能自救；同一晚只能用一瓶药
    const witch = this.byRole('witch');
    let saved: number | null = null;
    let poisoned: number | null = null;
    this.announceStep('witch', '女巫请睁眼。');
    if (!witch?.alive) await this.idleTurn();
    else {
      const w = this.state.witch;
      if (w.hasAntidote) {
        if (knife === null) this.gm('今晚没有人被狼人杀害。', [witch.id]);
        else if (knife === witch.id && day > 1) this.gm(`今晚被狼人杀害的是 ${seat(knife)}（你自己）。除第一夜外女巫不能自救。`, [witch.id]);
        else {
          saved = await this.askTarget(witch.id, 'witchSave', [knife], true, `今晚被狼人杀害的是 ${seat(knife)}${knife === witch.id ? '（你自己，第一夜可以自救）' : ''}。是否使用金水救他？`, '女巫决定救人');
          if (saved !== null) {
            w.hasAntidote = false;
            this.emit('private', `你用金水救了 ${seat(saved)}。`, { kind: 'private', to: [witch.id] }, { data: { saved } });
            await this.cue({ kind: 'witchSave', actor: witch.id, target: saved });
          }
        }
      }
      if (saved === null && w.hasPoison) {
        const cands = this.aliveIds().filter((i) => i !== witch.id);
        poisoned = await this.askTarget(witch.id, 'witchPoison', cands, true, '你有一瓶银水（毒药），是否要毒杀一名玩家？（同一晚只能使用一瓶药）', '女巫决定用毒');
        if (poisoned !== null) {
          w.hasPoison = false;
          this.emit('private', `你向 ${seat(poisoned)} 使用了银水。`, { kind: 'private', to: [witch.id] }, { data: { poison: poisoned } });
          await this.cue({ kind: 'witchPoison', actor: witch.id, target: poisoned });
        }
      }
      await this.pace();
    }

    // 4. 预言家：查验好人 / 狼人
    const seer = this.byRole('seer');
    this.announceStep('seer', '预言家请睁眼，请选择要查验的玩家。');
    if (!seer?.alive) await this.idleTurn();
    else {
      const others = this.aliveIds().filter((i) => i !== seer.id);
      const fresh = others.filter((i) => !(i in this.state.seerChecks));
      const target = await this.askTarget(seer.id, 'seer', fresh.length ? fresh : others, false, '选择今晚要查验的玩家（结果为好人或狼人）。', '预言家查验中');
      if (target !== null) {
        // the GM tells the seer at once
        const result = isWolf(this.players[target].role) ? 'wolf' : 'good';
        this.state.seerChecks[target] = result;
        this.emit('private', `查验结果：${seat(target)} 是【${result === 'wolf' ? '狼人' : '好人'}】。`, { kind: 'private', to: [seer.id] }, { data: { check: target, result } });
        this.publish();
        await this.cue({ kind: 'seer', actor: seer.id, target });
      }
      await this.pace();
    }

    // 5. 猎人：只确认开枪状态（被毒不能开枪），夜里不能主动开枪
    const hunter = this.byRole('hunter');
    this.announceStep('hunter', '猎人请睁眼，确认你的开枪状态。');
    if (!(hunter?.alive && !this.state.hunterShot)) await this.idleTurn();
    else {
      this.gm(`你的开枪状态：${poisoned === hunter.id ? '不能开枪' : '可以开枪'}。`, [hunter.id]);
      await this.pace();
    }

    // 结算：守卫与金水同时作用在刀口上 = 同守同救，仍然死亡；毒药不受守护影响
    this.setNightStep(null);
    const deaths = new Map<number, DeathCause>();
    if (knife !== null && (knife === guarded) === (knife === saved)) deaths.set(knife, 'wolf');
    if (poisoned !== null) deaths.set(poisoned, 'poison');
    return [...deaths.entries()].sort((a, b) => a[0] - b[0]).map(([id, cause]) => ({ id, cause }));
  }

  private lastSpeechFallback = false;
  private lastSpeechExplode = false;
  private causes = new Map<number, DeathCause>();
  /** 警长竞选: not held yet, suspended by a 自爆 (resumes next day at 退水), or settled. */
  private election: { status: 'pending' | 'suspended' | 'done'; candidates: number[]; voters: number[] } = { status: 'pending', candidates: [], voters: [] };

  /** The pack's kill for tonight, or null for 空刀. */
  private async wolfTurn(): Promise<number | null> {
    const wolves = this.wolves().filter((w) => w.alive);
    this.announceStep('wolves', '狼人请睁眼，请商量今晚的目标。');
    if (!wolves.length) {
      await this.idleTurn();
      return null;
    }
    // dead wolves (e.g. a voted-out human) keep watching their pack's channel
    const channel: Visibility = { kind: 'private', to: this.wolves().map((w) => w.id) };
    await this.cue('wolvesOut');
    const day = this.state.day;
    if (wolves.length > 1) {
      let rounds = this.wolfChatRounds;
      for (let round = 1; round <= rounds; round++) {
        // from round 2 the human (if a wolf) speaks last, after seeing what the AI wolves added;
        // if they all passed the human gets a one-click "no addition, vote now"
        const extra = round > this.wolfChatRounds;
        const order = round === 1 ? wolves : [...wolves.filter((w) => !w.isHuman), ...(extra ? [] : wolves.filter((w) => w.isHuman))];
        let passes = 0;
        let spoken = 0;
        let humanSpoke = false;
        for (const w of order) {
          const othersPassed = round > 1 && spoken > 0 && passes === spoken;
          const text = (await this.ask(w.id, { kind: 'wolfChat', round, rounds, day, othersPassed }, `狼队沟通 ${round}/${rounds}`)) as string;
          const bare = isPass(text);
          const passed = bare || isWolfPass(text);
          spoken++;
          if (passed) passes++;
          else if (w.isHuman) humanSpoke = true;
          this.emit('wolfChat', bare ? '（没有补充）' : text, channel, { speaker: w.id, data: this.lastSpeechFallback ? { fallback: true } : undefined });
        }
        if (passes === spoken) break; // nobody had anything to add: go straight to the vote
        // the human had the last word of the last round: give the AI wolves one more turn to answer it
        if (round === rounds && humanSpoke && !extra) rounds++;
      }
    }
    const cands = this.aliveIds();
    const votes = new Map<number, number | null>();
    const prompt = '投票选择今晚要杀的玩家（可以选择狼队友，也可以空刀）。';
    const name = (t: number | null) => (t === null ? '空刀' : seat(t));
    // AI wolves vote first; a living human wolf sees their picks (like wolves pointing at night) and votes last
    const ai = wolves.filter((w) => !w.isHuman);
    const human = wolves.filter((w) => w.isHuman);
    for (const w of ai) votes.set(w.id, await this.askTarget(w.id, 'wolfKill', cands, true, prompt, '狼队投票中'));
    const aiVotes = ai.map((w) => `${seat(w.id)}→${name(votes.get(w.id)!)}`).join('，');
    if (human.length && ai.length) this.emit('wolfChat', `队友已投：${aiVotes}。`, channel);
    for (const w of human) {
      votes.set(w.id, await this.askTarget(w.id, 'wolfKill', cands, true, ai.length ? `队友已投：${aiVotes}。${prompt}` : prompt, '狼队投票中'));
    }
    const tally = new Map<number | null, number>();
    for (const t of votes.values()) tally.set(t, (tally.get(t) ?? 0) + 1);
    const [top, topCount] = [...tally.entries()].sort((a, b) => b[1] - a[1])[0];
    const detail = [...votes.entries()].map(([w, t]) => `${seat(w)}→${name(t)}`).join('，');
    let target: number | null;
    if (topCount * 2 > wolves.length) {
      target = top;
      this.emit('wolfChat', `狼队投票：${detail}。${target === null ? '今晚空刀。' : `今晚击杀 ${seat(target)}。`}`, channel, { data: { target } });
    } else {
      target = this.rng.pick([...tally.keys()]);
      this.emit('wolfChat', `狼队投票：${detail}。未过半数，系统随机选定${target === null ? '空刀' : ` ${seat(target)}`}。`, channel, { data: { target } });
    }
    // the wolf turn only ends once the pack is fully back indoors
    await this.cue('wolvesIn');
    await this.pace();
    return target;
  }

  // ───────────────────────── day ─────────────────────────

  private async dayPhase(deaths: { id: number; cause: DeathCause }[]) {
    const day = this.state.day;
    this.setPhase('dawn');
    this.gm(`第 ${day} 天，天亮了。`);
    await this.cue('dawn');
    await this.pace();

    // 警长竞选 comes before the night's news: last night's dead still stand for (and vote in) it
    let exploded = false;
    if (this.election.status !== 'done') exploded = (await this.runElection()) === 'exploded';
    if (this.over) return;

    // 公布死讯
    this.setPhase('dawn');
    const ids = deaths.map((d) => d.id);
    this.emit('gm', ids.length ? `昨晚死亡的玩家：${ids.map((i) => `${seat(i)} ${this.players[i].name}`).join('、')}。` : '昨晚是平安夜。', { kind: 'public' }, { data: { nightDeaths: ids } });
    for (const d of deaths) this.kill(d.id, d.cause);
    await this.pace();
    if (this.endIfWon()) return;
    // only the first night's dead leave last words; a wolf who blew up has none either way
    for (const id of ids) {
      if (this.causes.get(id) === 'explode') continue;
      await this.aftermath(id, day === 1);
      if (this.over) return;
    }
    if (exploded) {
      this.gm('因狼人自爆，今天不再发言和投票，直接进入黑夜。');
      await this.pace();
      return;
    }

    if (await this.discussion(ids)) return;
    await this.exileVote();
  }

  /**
   * After a death: the sheriff hands on (or tears up) the badge, the dead speaks
   * (if entitled to last words), and a hunter or 狼王 not poisoned may fire. The
   * victim of a shot dies by day, so they get last words too.
   */
  private async aftermath(id: number, lastWords: boolean) {
    if (this.state.sheriff === id) await this.passBadge(id);
    if (lastWords) await this.speech(id, 'lastWords', '遗言');
    const p = this.players[id];
    if (!GUN_ROLES.includes(p.role)) return;
    const king = p.role === 'wolfKing';
    const spent = king ? 'wolfKingShot' : 'hunterShot';
    if (this.state[spent]) return;
    if (this.causes.get(id) === 'poison') {
      this.state[spent] = true; // poisoned: cannot shoot (the GM says nothing)
      return;
    }
    this.gm(`${seat(id)} 出局，若有技能可以发动。`);
    const shot = await this.askTarget(id, king ? 'wolfKingShot' : 'hunterShot', this.aliveIds(), true, '你已出局，可以开枪带走一名存活玩家（整局仅一发），也可以不开枪。', '出局技能');
    this.state[spent] = true; // the chance is spent either way
    if (shot === null) {
      await this.pace();
      return;
    }
    this.gm(`${seat(id)} 是${king ? '狼王' : '猎人'}，开枪带走了 ${seat(shot)} ${this.players[shot].name}。`);
    await this.cue({ kind: 'dayShot', actor: id, target: shot });
    this.kill(shot, king ? 'wolfKing' : 'hunter');
    await this.pace(2);
    if (this.endIfWon()) return;
    await this.aftermath(shot, true);
  }

  /** A dying sheriff picks an heir (警徽流) or tears the badge up. */
  private async passBadge(id: number) {
    const cands = this.aliveIds();
    const heir = cands.length
      ? await this.askTarget(id, 'badge', cands, true, '你是警长，请移交警徽：选择继承警徽的玩家，或撕掉警徽（本局从此没有警长）。', '移交警徽')
      : null;
    this.state.sheriff = heir;
    this.publish();
    if (heir === null) this.gm(`${seat(id)} 撕毁了警徽，本局不再有警长。`);
    else this.gm(`${seat(id)} 将警徽移交给 ${seat(heir)} ${this.players[heir].name}，${seat(heir)} 成为新的警长。`);
    await this.cue({ kind: 'badge', from: id, to: heir });
    await this.pace();
  }

  // ───────────────────────── 警长竞选 ─────────────────────────

  /**
   * 上警 → 警上发言 → 退水 → 警下投票 (→ PK → 再投). A wolf blowing up on the stage
   * suspends it until the next day, where it picks up at 退水; a second 自爆 loses the badge.
   */
  private async runElection(): Promise<'done' | 'exploded'> {
    this.setPhase('election');
    const resumed = this.election.status === 'suspended';
    let cands: number[];
    let voters: number[];
    if (!resumed) {
      this.gm('进入警长竞选环节：想竞选警长的玩家请上警。警长的放逐票计 1.5 票，决定每天的发言顺序并最后发言归票。');
      await this.pace();
      const alive = this.aliveIds();
      const up: number[] = [];
      // everyone decides at once: nobody sees the others' choice until all have made theirs
      for (const id of alive) {
        const r = await this.askTarget(id, 'runForSheriff', [id], true, '是否上警竞选警长？上警的玩家依次发言，警下的玩家投票；上警后可以退水，但退水的人不能投票。', '考虑是否上警');
        if (r === id) up.push(id);
      }
      voters = alive.filter((i) => !up.includes(i));
      if (!up.length) return this.noSheriff('无人上警，本局没有警长。');
      this.gm(`上警玩家：${up.map(seat).join('、')}。警下玩家：${voters.length ? voters.map(seat).join('、') : '无'}。`);
      if (!voters.length) return this.noSheriff('所有人都上警了，没有警下玩家投票，本局没有警长。');
      const first = this.rng.pick(up);
      const clockwise = this.rng.next() < 0.5;
      const order = circularOrder(up, first, clockwise);
      if (up.length > 1) this.gm(`警上发言：由 ${seat(first)} 开始，${clockwise ? '顺时针' : '逆时针'}依次发言。`);
      await this.pace();
      const spoken: number[] = [];
      for (const id of order) {
        if (await this.speech(id, 'campaign', '警上发言', { order, spoken: [...spoken], first, clockwise })) return this.suspendElection(up, voters);
        spoken.push(id);
      }
      cands = up;
    } else {
      cands = this.election.candidates.filter((i) => this.players[i].alive);
      voters = this.election.voters.filter((i) => this.players[i].alive);
      if (!cands.length) return this.noSheriff('昨天的警上玩家都已出局，本局没有警长。');
      this.gm(`继续昨天被自爆打断的警长竞选：警上玩家 ${cands.map(seat).join('、')}，直接进入退水环节。`);
      await this.pace();
    }

    // 退水: decided at once, announced together; a wolf on the stage may blow up instead
    this.gm('请警上玩家决定是否退水。');
    const stay: number[] = [];
    const out: number[] = [];
    for (const id of cands) {
      const r = await this.askTarget(id, 'withdraw', [id], true, '是否退水（放弃竞选警长）？退水后不能再当选，也不能投警长票。', '考虑是否退水', isWolf(this.players[id].role));
      if (r === EXPLODE_CHOICE) {
        await this.selfDestruct(id);
        return this.suspendElection(cands, voters);
      }
      (r === id ? out : stay).push(id);
    }
    if (out.length) this.gm(`${out.map(seat).join('、')} 退水。`);
    if (!stay.length) return this.noSheriff('所有警上玩家都退水了，本局没有警长。');
    if (stay.length === 1) return this.elect(stay[0], `警上只剩 ${seat(stay[0])}，自动当选警长。`);
    if (!voters.length) return this.noSheriff('没有警下玩家可以投票，本局没有警长。');

    this.gm(`请警下玩家在 ${stay.map(seat).join('、')} 中投票选出警长。`);
    let top = await this.voteRound(voters, stay, 'sheriffVote', `在警上玩家 ${stay.map(seat).join('、')} 中投票选出警长，也可以弃票。`, false);
    if (top.length > 1) {
      this.gm(`${top.map(seat).join('、')} 平票，进入警长 PK：平票玩家依次发言，然后警下玩家再投一次。`);
      const said: number[] = [];
      for (const id of top) {
        if (await this.speech(id, 'campaignPk', '警长PK发言', { order: top, spoken: [...said] })) return this.suspendElection(top, voters);
        said.push(id);
      }
      top = await this.voteRound(voters, top, 'sheriffRevote', `只能在 PK 玩家 ${top.map(seat).join('、')} 中投票选出警长，也可以弃票。`, false);
      if (top.length > 1) return this.noSheriff('再次平票，警徽流失，本局没有警长。');
    }
    if (!top.length) return this.noSheriff('警下玩家全部弃票，警徽流失，本局没有警长。');
    return this.elect(top[0], `${seat(top[0])} ${this.players[top[0]].name} 当选警长！`);
  }

  private async elect(id: number, text: string): Promise<'done'> {
    this.election.status = 'done';
    this.state.sheriff = id;
    this.publish();
    this.gm(text);
    await this.cue({ kind: 'badge', from: null, to: id });
    await this.pace();
    return 'done';
  }

  private async noSheriff(text: string): Promise<'done'> {
    this.election.status = 'done';
    this.gm(text);
    await this.pace();
    return 'done';
  }

  /** A 自爆 on the stage: first time the election waits for tomorrow, the second time the badge is lost (吞警徽). */
  private suspendElection(cands: number[], voters: number[]): 'exploded' {
    if (this.election.status === 'suspended') {
      this.election.status = 'done';
      this.gm('第二次自爆打断竞选，警徽流失，本局没有警长。');
    } else {
      this.election = { status: 'suspended', candidates: cands.filter((i) => this.players[i].alive), voters };
      this.gm('警长竞选中断，推迟到明天从退水环节继续。');
    }
    return 'exploded';
  }

  // ───────────────────────── 发言 / 放逐 ─────────────────────────

  /** A wolf blows up: public reveal, out at once with no last words and no shot (狼王); a sheriff's badge goes with them. */
  private async selfDestruct(id: number) {
    const inElection = this.state.phase === 'election';
    if (this.players[id].role === 'wolfKing') this.state.wolfKingShot = true;
    this.gm(`${seat(id)} ${this.players[id].name} 自爆，身份是狼人！${inElection ? '' : '本轮剩余发言与投票取消，直接进入黑夜。'}`);
    this.kill(id, 'explode');
    await this.cue('explode');
    if (this.state.sheriff === id) {
      this.state.sheriff = null;
      this.publish();
      this.gm('警长自爆，警徽随之流失，本局不再有警长。');
      await this.cue({ kind: 'badge', from: id, to: null });
    }
  }

  /** Returns true if the speaker (a wolf) self-destructed: the day ends at once. */
  private async speech(id: number, purpose: SpeechKind, label: string, seq: SpeechOrder = {}): Promise<boolean> {
    const canExplode = purpose !== 'lastWords' && isWolf(this.players[id].role);
    const text = (await this.ask(id, { kind: 'speech', purpose, day: this.state.day, ...seq, ...(canExplode ? { canExplode } : {}) }, label)) as string;
    const explode = canExplode && this.lastSpeechExplode;
    const data = { ...(this.lastSpeechFallback ? { fallback: true } : {}), ...(explode ? { explode: true } : {}) };
    await this.gate();
    if (!this.replaying && this.hooks.beforeSpeech) {
      this.checkRestored();
      await this.hooks.beforeSpeech(id);
      await this.gate();
    }
    if (!(explode && isPass(text))) this.emit('speech', text, { kind: 'public' }, { speaker: id, speechKind: purpose, data: Object.keys(data).length ? data : undefined });
    if (explode) await this.selfDestruct(id);
    await this.pace();
    return explode;
  }

  /**
   * The day's speeches. With a sheriff: they pick 死左/死右 (one death last night) or
   * 警左/警右 (none or several) and speak last, closing with 归票. Without: the GM
   * starts next to the single dead, or anywhere, in a random direction.
   */
  private async discussion(nightDeaths: number[]): Promise<boolean> {
    const day = this.state.day;
    this.setPhase('discussion');
    const alive = this.aliveIds();
    const sheriff = this.state.sheriff !== null && this.players[this.state.sheriff].alive ? this.state.sheriff : null;
    const pivot = nightDeaths.length === 1 ? nightDeaths[0] : sheriff;
    let first: number;
    let clockwise: boolean;
    let order: number[];
    if (sheriff !== null) {
      const others = alive.filter((i) => i !== sheriff);
      if (!others.length) {
        first = sheriff;
        clockwise = true;
        order = [];
      } else {
        const cw = nextSeat(others, pivot!, true);
        const ccw = nextSeat(others, pivot!, false);
        const anchor = nightDeaths.length === 1 ? `死者 ${seat(pivot!)}` : '警长';
        const pick = cw === ccw ? cw : await this.askTarget(sheriff, 'speakOrder', [cw, ccw], false, `请决定今天的发言顺序（从${anchor}的左右两侧选一边开始）：${seat(cw)} 开始为顺时针（号码从小到大），${seat(ccw)} 开始为逆时针。你最后一个发言并归票。`, '决定发言顺序');
        first = pick!;
        clockwise = cw === ccw || pick === cw;
        order = circularOrder(others, first, clockwise);
      }
      this.gm(`第 ${day} 天发言开始：警长 ${seat(sheriff)} 决定${order.length ? `由 ${seat(first)} 开始，${clockwise ? '顺时针' : '逆时针'}发言，` : ''}警长最后发言并归票。`);
    } else {
      clockwise = this.rng.next() < 0.5;
      first = pivot !== null ? nextSeat(alive, pivot, clockwise) : this.rng.pick(alive);
      order = circularOrder(alive, first, clockwise);
      this.gm(`第 ${day} 天发言开始：由 ${seat(first)} 开始，${clockwise ? '顺时针' : '逆时针'}发言。`);
    }
    await this.pace();
    const spoken: number[] = [];
    const full = sheriff !== null ? [...order, sheriff] : order;
    for (const id of order) {
      if (!this.players[id].alive) continue;
      if (await this.speech(id, 'discussion', '发言中', { order: full, spoken: [...spoken], first, clockwise })) return true;
      spoken.push(id);
    }
    if (sheriff !== null && this.players[sheriff].alive) {
      this.gm(`请警长 ${seat(sheriff)} 发言并归票。`);
      if (await this.speech(sheriff, 'summary', '警长归票', { order: full, spoken, first, clockwise })) return true;
    }
    return false;
  }

  /** 放逐投票 (警长 1.5 票) → PK（台上的人不投票）→ 再平票无人出局. */
  private async exileVote() {
    this.setPhase('vote');
    this.gm(`发言结束，开始放逐投票${this.state.sheriff !== null ? '（警长的一票计 1.5 票）' : ''}。`);
    const alive = this.aliveIds();
    let out = await this.voteRound(alive, alive, 'vote', '投票放逐一名玩家，也可以弃票。', true);
    if (out.length > 1) {
      this.gm(`${out.map(seat).join('、')} 平票，进入 PK：平票玩家依次发言，然后由台下玩家在他们之中再投一次。`);
      const defended: number[] = [];
      for (const id of out) {
        if (await this.speech(id, 'defense', 'PK发言', { order: out, spoken: [...defended] })) return;
        defended.push(id);
      }
      const voters = this.aliveIds().filter((i) => !out.includes(i));
      if (!voters.length) {
        this.gm('台下没有可以投票的玩家，今天无人出局。');
        await this.pace();
        return;
      }
      this.gm('请台下玩家在 PK 玩家中投票。');
      out = await this.voteRound(voters, out, 'revote', `只能在 PK 玩家 ${out.map(seat).join('、')} 中投票，也可以弃票。`, true);
      if (out.length > 1) {
        this.gm('再次平票，今天无人出局。');
        await this.pace();
        return;
      }
    }
    if (out.length === 0) {
      this.gm('全员弃票，今天无人出局。');
      await this.pace();
      return;
    }
    const exiled = out[0];
    this.gm(`${seat(exiled)} ${this.players[exiled].name} 被放逐。`);
    this.kill(exiled, 'vote');
    if (this.endIfWon()) return;
    this.setPhase('lastWords');
    await this.aftermath(exiled, true);
    if (this.over) return;
    // the exile stays on the plaza, last words overhead, until the human sends them off
    await this.gate();
    if (!this.replaying && this.hooks.confirmExile) {
      this.checkRestored();
      await this.hooks.confirmExile(exiled);
      await this.gate();
    }
  }

  /**
   * One round of voting among `candidates` (never for yourself). With `weighted`
   * the sheriff's vote counts 1.5. Returns the ids with the top score (empty if everyone abstained).
   */
  private async voteRound(voters: number[], candidates: number[], action: TargetAction, prompt: string, weighted: boolean): Promise<number[]> {
    const votes: [number, number | null][] = [];
    for (const voter of voters) {
      const cands = candidates.filter((c) => c !== voter);
      if (!cands.length) {
        votes.push([voter, null]);
        continue;
      }
      votes.push([voter, await this.askTarget(voter, action, cands, true, prompt, '投票中')]);
    }
    const weight = (v: number) => (weighted && v === this.state.sheriff ? 1.5 : 1);
    const tally = new Map<number, number[]>();
    for (const [v, t] of votes) if (t !== null) tally.set(t, [...(tally.get(t) ?? []), v]);
    const score = (vs: number[]) => vs.reduce((n, v) => n + weight(v), 0);
    const lines = [...tally.entries()]
      .sort((a, b) => score(b[1]) - score(a[1]))
      .map(([t, vs]) => `${seat(t)}（${score(vs)}票）← ${vs.map((v) => `${v + 1}${weight(v) > 1 ? '(警长)' : ''}`).join(',')}`);
    const abstain = votes.filter(([, t]) => t === null).map(([v]) => v + 1);
    const title = action === 'sheriffVote' || action === 'sheriffRevote' ? '警长投票结果' : '投票结果';
    this.emit('vote', `${title}：${lines.join('；') || '无'}${abstain.length ? `；弃票：${abstain.join(',')}` : ''}`, { kind: 'public' }, { data: { votes: Object.fromEntries(votes), action } });
    await this.pace();
    if (!tally.size) return [];
    const max = Math.max(...[...tally.values()].map(score));
    return [...tally.entries()].filter(([, v]) => score(v) === max).map(([t]) => t);
  }
}

/** The first of `ids` after `pivot` going clockwise (ascending seats, wrapping) or counter-clockwise. */
export function nextSeat(ids: number[], pivot: number, clockwise: boolean): number {
  const sorted = ids.slice().sort((a, b) => a - b);
  if (clockwise) return sorted.find((i) => i > pivot) ?? sorted[0];
  return [...sorted].reverse().find((i) => i < pivot) ?? sorted[sorted.length - 1];
}

export function isPass(text: string): boolean {
  return /^\s*(pass|过|结束)\s*[。.!！]?\s*$/i.test(text);
}

/** A wolf-chat line that adds nothing: "pass", "过…", or a short "没有补充 / 没意见". */
export function isWolfPass(text: string): boolean {
  return /^\s*(pass|过)/i.test(text) || (text.length <= 30 && /没有?补充|没意见|pass/i.test(text));
}

export function canSee(v: Visibility, id: number): boolean {
  return v.kind === 'public' || v.to.includes(id);
}

/** Alive ids starting from `first`, going clockwise (ascending seats) or counter-clockwise. */
export function circularOrder(alive: number[], first: number, clockwise: boolean): number[] {
  const sorted = alive.slice().sort((a, b) => a - b);
  if (!clockwise) sorted.reverse();
  const i = sorted.indexOf(first);
  return [...sorted.slice(i), ...sorted.slice(0, i)];
}
