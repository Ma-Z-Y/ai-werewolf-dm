import type { BadgeCue, Game, GameState, NightStep, RoleCue, SceneCue } from '../game/game';
import { canSee } from '../game/game';
import {
  EXPLODE_CHOICE,
  ROLE_NAME,
  YES_NO_ACTIONS,
  isWolf,
  seat,
  teamOf,
  type Agent,
  type GameEvent,
  type PlayerView,
  type Role,
  type SpeechRequest,
  type SpeechResult,
  type TargetRequest,
  type WolfChatRequest,
} from '../game/types';
import { characterCanvas, type Look } from '../render/pixel';
import type { ScreenPos, Stage } from '../render/stage';
import { audio } from '../audio/audio';
import { getConfig, onConfigChange, updateConfig } from '../settings';
import { h } from './dom';
import { showRules } from './rules';
import { AwardCeremony, type CeremonyEntry } from './ceremony';
import type { Reviewer } from '../game/ceremony';

const ROLE_DESC: Record<Role, string> = {
  werewolf: '每晚与狼队商量并投票杀人；白天伪装成好人。屠边（神职或村民全灭）即胜。',
  wolfKing: '狼人阵营，与狼队一起刀人；出局时可开枪带走一人（被毒死、自爆不能开枪）。',
  villager: '没有技能，靠发言与投票找出狼人。',
  seer: '每晚查验一人是好人还是狼人。',
  witch: '金水救人、银水毒人，各一瓶；同一晚只能用一瓶，仅首夜可自救。',
  hunter: '出局时可开枪带走一人；被毒死不能开枪。',
  guard: '每晚守护一人免受狼刀，可守自己，不可连守。',
};

const ACTION_TITLE: Record<TargetRequest['action'], string> = {
  vote: '放逐投票',
  revote: '平票再投',
  seer: '预言家 · 查验',
  guard: '守卫 · 守护',
  wolfKill: '狼队 · 投票刀人',
  hunterShot: '猎人 · 开枪',
  wolfKingShot: '狼王 · 开枪',
  witchSave: '女巫 · 金水',
  witchPoison: '女巫 · 银水',
  runForSheriff: '警长竞选 · 是否上警',
  withdraw: '警长竞选 · 是否退水',
  sheriffVote: '警长投票',
  sheriffRevote: '警长 PK 再投',
  badge: '警长 · 移交警徽',
  speakOrder: '警长 · 决定发言顺序',
};

const SPEECH_KIND: Record<string, string> = {
  discussion: '', summary: '警长归票', lastWords: '遗言', defense: 'PK', campaign: '警上', campaignPk: '警长PK',
};

type Tab = 'round' | 'election' | 'all' | 'wolf' | 'private' | 'award';

/** Phone-sized screens (portrait, or a landscape phone): the HUD folds into a bottom dock + sheets. */
const MOBILE = window.matchMedia('(max-width: 760px), (max-height: 520px)');

/** Mobile: which sheet is pulled up over the scene. */
type Sheet = 'none' | 'players' | 'chat';

/** Mobile: eye-level close-up of one seat (default) or the desktop's overview. */
const VIEW_KEY = 'ai-werewolf:mobile-view';
function readClosePref(): boolean {
  try {
    return localStorage.getItem(VIEW_KEY) !== 'overview';
  } catch {
    return true;
  }
}
function writeClosePref(close: boolean) {
  try {
    localStorage.setItem(VIEW_KEY, close ? 'close' : 'overview');
  } catch {
    /* ignore */
  }
}

/**
 * One clearly different bubble colour per seat (12 distinct colour families,
 * ordered so neighbouring seats never look alike), reused in the chat log.
 */
const BUBBLE_COLORS: { bg: string; edge: string }[] = [
  { bg: '#ffd95e', edge: '#8a6a00' }, // 1 yellow
  { bg: '#7ab8ff', edge: '#1f4f8a' }, // 2 blue
  { bg: '#ff8f85', edge: '#9a2a22' }, // 3 red
  { bg: '#7fdc98', edge: '#1f6b36' }, // 4 green
  { bg: '#d4a4ff', edge: '#5e2a8f' }, // 5 purple
  { bg: '#ffb066', edge: '#8f4c0a' }, // 6 orange
  { bg: '#74e0e0', edge: '#0f6666' }, // 7 cyan
  { bg: '#ff9fd0', edge: '#8f2a5f' }, // 8 pink
  { bg: '#c8e67a', edge: '#4f6a10' }, // 9 lime
  { bg: '#aeb4ff', edge: '#2e3490' }, // 10 periwinkle
  { bg: '#e2c49c', edge: '#6a4a24' }, // 11 tan
  { bg: '#eeeeea', edge: '#55554f' }, // 12 light grey
];

/** Roster / label colour of a known role: wolves red, the gods (神职) green, villagers plain. */
/** The human's own guess at a player they have no system info on. */
export type Mark = 'villager' | 'god' | 'wolf';
const MARK_ORDER: (Mark | null)[] = [null, 'villager', 'god', 'wolf'];
const MARK_NAME: Record<Mark, string> = { villager: '民', god: '神', wolf: '狼' };
const MARK_CLS: Record<Mark, string> = { villager: 'good', god: 'god', wolf: 'wolf' };

function roleCls(role: Role): 'wolf' | 'god' | 'good' {
  return teamOf(role) === 'wolf' ? 'wolf' : role === 'villager' ? 'good' : 'god';
}

export function bubbleColor(id: number) {
  return BUBBLE_COLORS[id % BUBBLE_COLORS.length];
}

function bubbleStyle(id: number) {
  const c = bubbleColor(id);
  return `background:${c.bg};border-color:${c.edge};box-shadow:2px 2px 0 ${c.edge}`;
}

/** Public night-step names (GM announces the turn, never what the role does). */
const NIGHT_STEP_NAME: Record<NightStep, string> = {
  guard: '守卫轮',
  seer: '预言家轮',
  wolves: '狼人轮',
  hunter: '猎人轮',
  witch: '女巫轮',
};

export interface GameUIOptions {
  /** Resuming a save: the replay rebuilds the log silently, then `restored()` sets the scene. */
  restoring: boolean;
  /** Game time already played (resumed games). */
  elapsedMs: number;
  /** The human's 民/神/狼 marks (resumed games). */
  marks?: (Mark | null)[];
  onPause: () => void;
}

export class GameUI {
  private hud!: HTMLElement;
  private roleCard!: HTMLElement;
  private roster!: HTMLElement;
  private banner!: HTMLElement;
  private chatLog!: HTMLElement;
  private chatTabs!: HTMLElement;
  private chatFilter!: HTMLElement;
  private action!: HTMLElement;
  private labelEls: HTMLElement[] = [];
  /** Rendered width of each label, measured whenever its contents change. */
  private labelW: number[] = [];
  /** `spot`: a defense or last words after the vote, the only bubble on the plaza. */
  private bubbles = new Map<number, { text: string; seq: number; spot: boolean }>();
  private bubbleSeq = 0;
  private tab: Tab = 'round';
  private playerFilter: number | null = null;
  /** Per seat: the human's own 民/神/狼 guess (only shown where the system tells them nothing). */
  private marks: (Mark | null)[];
  private pendingTarget: { req: TargetRequest; select: (id: number) => void } | null = null;
  private lastPhase = '';
  private clock!: HTMLElement;
  private stepEl!: HTMLElement;
  private lastStep: NightStep | null = null;
  private clockTimer = 0;
  /** Played time before `runningSince` (the clock stops while paused). */
  private playedMs = 0;
  private runningSince: number | null = null;
  private restoring: boolean;
  private actorKey = '';
  private actorSince = 0;
  private actorTimer = 0;
  readonly agent: Agent;
  private engineChip = h('div', { class: 'engine panel', title: 'AI 引擎状态' });
  private engineStats = { ok: 0, fail: 0, fallback: 0, lastMs: 0 };
  private dock!: HTMLElement;
  private sheet: Sheet = 'none';
  /** Mobile: the 12 seats at a glance under the top bar (tap one to look at them). */
  private strip!: HTMLElement;
  private stripKey = '';
  /** Mobile close-up: the card with the viewed player's name, identity and latest words. */
  private card!: HTMLElement;
  private cardKey = '';
  /** When the strip / card last checked for changes (a few times a second is plenty). */
  private panelsAt = 0;
  /** Mobile: close-up (eye level, one seat at a time) rather than the overview. */
  private closePref = readClosePref();
  /** The seat the close-up stands before. */
  private viewSeat: number;
  /** The last seat the close-up moved to by itself (the speaker / actor); a swipe overrides until the next one. */
  private autoSeat: number | null = null;
  private unread = 0;
  /** Label tapped last (touch has no hover to bring a buried bubble to the front). */
  private raised: number | null = null;
  /** An AI's day speech is on screen that the human hasn't moved past yet. */
  private unreadSpeech = false;
  /** Who said the unread speech: the camera stays on them until the human moves on. */
  private lastSpeaker: number | null = null;
  /** The banner label (e.g. 警上发言) the last speaker spoke under. */
  private lastSpeakerLabel = '';
  /** The actor's label while they were asked to speak, to carry over to their speech. */
  private speakingLabel: { id: number; label: string } | null = null;
  /** The next AI is still thinking: the action panel shows a disabled 下一位 button. */
  private waitingFor: number | null = null;
  /** A ready speech held back until the human asks for it (see holdSpeech). */
  private held: { speaker: number; release: () => void } | null = null;
  /** The exile's last words are over: waiting for the human to send them off (see confirmExile). */
  private exileWait: (() => void) | null = null;
  private cleanups: (() => void)[] = [];
  /** 颁奖典礼: who takes part (null = the human), and the AIs' own notes. */
  private reviewers: { list: (Reviewer | null)[]; notes: () => (string[] | null)[] } | null = null;
  /** The ceremony has started: its log, and everyone is standing again. */
  private ceremonyLog: CeremonyEntry[] | null = null;
  private revived = false;
  private ceremonyThinking: number | null = null;

  constructor(
    private root: HTMLElement,
    private labelsRoot: HTMLElement,
    private stage: Stage,
    private game: Game,
    private me: number,
    private godView: boolean,
    private onRestart: () => void,
    private looks: Look[],
    private opts: GameUIOptions,
  ) {
    this.restoring = opts.restoring;
    this.viewSeat = me;
    this.playedMs = opts.elapsedMs;
    this.marks = game.players.map((_, i) => opts.marks?.[i] ?? null);
    this.build();
    this.agent = {
      speak: (req, view) => this.askSpeech(req, view),
      choose: (req, view) => this.askTarget(req, view),
    };
    stage.onFrame = (p) => this.placeLabels(p);
    stage.onPick = (id) => this.pick(id);
    stage.onSwipe = (d) => this.setView(this.viewSeat + d);
    stage.view(me);
    stage.highlightSelf(me);
  }

  private view(): PlayerView {
    return this.game.viewFor(this.me);
  }

  private canSee(e: GameEvent) {
    return this.godView || canSee(e.visibility, this.me);
  }

  /** What the human knows about player `id`'s role/alignment. */
  private knownOf(id: number): { text: string; cls: string; ring?: 'good' | 'wolf' } | null {
    const p = this.game.players[id];
    if (this.godView || this.game.state.phase === 'ended') {
      return { text: ROLE_NAME[p.role], cls: roleCls(p.role), ring: this.ringOf(id) };
    }
    const k = this.view().known[id];
    const ring = this.ringOf(id);
    if (id === this.me) return { text: ROLE_NAME[p.role], cls: 'me', ring };
    // a wolf you checked as the seer is a check result, not a teammate
    if (isWolf(k) && isWolf(this.game.players[this.me].role)) return { text: k === 'wolfKing' ? '狼王队友' : '狼队友', cls: 'wolf' };
    if (ring) return { text: `查验：${ring === 'wolf' ? '狼人' : '好人'}`, cls: ring, ring };
    return null;
  }

  /** For the save. */
  get playerMarks(): (Mark | null)[] {
    return this.marks.slice();
  }

  /** What to show next to a player's name: the system's info, else the human's own mark. */
  private identityOf(id: number): { text: string; cls: string; mark: boolean } | null {
    const k = this.knownOf(id);
    if (k) return { text: k.text, cls: k.cls, mark: false };
    const m = this.marks[id];
    return m ? { text: MARK_NAME[m], cls: MARK_CLS[m], mark: true } : null;
  }

  /** 民 → 神 → 狼 → none. */
  private cycleMark(id: number) {
    const i = MARK_ORDER.indexOf(this.marks[id]);
    this.marks[id] = MARK_ORDER[(i + 1) % MARK_ORDER.length];
    this.renderRoster();
    this.renderChat();
  }

  private ringOf(id: number): 'good' | 'wolf' | undefined {
    const me = this.game.players[this.me];
    if (me.role !== 'seer' && !this.godView) return undefined;
    const r = this.game.state.seerChecks[id];
    return r;
  }

  // ───────────────────────── build ─────────────────────────

  private build() {
    const me = this.game.players[this.me];
    const portrait = characterCanvas(this.looks[this.me]);
    this.roleCard = h('div', { class: 'role-card panel' });
    this.roleCard.append(
      portrait,
      h(
        'div',
        {},
        h('div', { class: 'seat' }, `SEAT ${this.me + 1} · ${me.name}`),
        h('div', { class: `role ${isWolf(me.role) ? 'wolf' : 'good'}` }, ROLE_NAME[me.role]),
        h('div', { class: 'desc' }, ROLE_DESC[me.role]),
        h('div', { class: 'items' }),
      ),
    );
    this.roster = h('div', { class: 'roster panel' });
    this.hud = h('div', { id: 'hud' }, this.roleCard, this.roster);

    this.clock = h('div', { class: 'clock', title: '本局已进行时间' }, '00:00');
    this.banner = h('div', { id: 'banner', class: 'panel' }, this.clock, h('div', { class: 'phase' }, '准备中'), h('div', { class: 'actor' }));
    this.runningSince = performance.now();
    this.clockTimer = window.setInterval(() => this.renderClock(), 1000);
    this.renderClock();

    const topright = h(
      'div',
      { id: 'topright' },
      this.engineChip,
      this.muteBtn(),
      h('button', { class: 'btn', title: '规则说明', 'aria-label': '规则说明', onclick: () => showRules(this.root) }, h('span', { class: 'long' }, '规则说明'), h('span', { class: 'short' }, '?')),
      h('button', { class: 'btn', title: '暂停（Esc）', 'aria-label': '暂停', onclick: () => this.opts.onPause() }, h('span', { class: 'long' }, '暂停'), h('span', { class: 'short' }, 'Ⅱ')),
    );

    this.chatTabs = h('div', { class: 'tabs', role: 'tablist' });
    this.chatFilter = h('div', { class: 'filter' });
    this.chatLog = h('div', { class: 'log', 'aria-live': 'polite' });
    const chat = h('div', { id: 'chat', class: 'panel' }, this.chatTabs, this.chatFilter, this.chatLog);

    this.action = h('div', { id: 'action', class: 'panel' });

    this.stepEl = h('div', { id: 'nightstep', 'aria-live': 'polite' });
    this.dock = h('nav', { id: 'dock', class: 'panel', 'aria-label': '面板' });
    this.strip = h('nav', { id: 'seats', 'aria-label': '座位' });
    this.card = h('section', { id: 'closeup', class: 'panel', 'aria-live': 'polite' });
    this.root.append(this.hud, this.banner, topright, this.strip, chat, this.card, this.action, this.stepEl, this.dock);
    this.bindMobile();

    for (let i = 0; i < 12; i++) {
      const el = h('div', { class: 'label' });
      this.labelsRoot.appendChild(el);
      this.labelEls.push(el);
    }
    this.renderTabs();
    this.renderRoster();
    this.bindNextSpeech();
  }

  /** Space / Enter / → reveals a held speech; switching 逐条查看 off lets it through. */
  private bindNextSpeech() {
    const onKey = (e: KeyboardEvent) => {
      const go = this.held?.release ?? this.exileWait;
      if (!go || e.repeat || !['Enter', ' ', 'ArrowRight'].includes(e.key)) return;
      if ((e.target as HTMLElement).closest?.('input, textarea, select, button, .modal-back')) return;
      e.preventDefault();
      go();
    };
    document.addEventListener('keydown', onKey);
    this.cleanups.push(() => document.removeEventListener('keydown', onKey));
    this.cleanups.push(onConfigChange((c) => {
      if (!c.stepSpeech) this.held?.release();
      this.renderActor();
      this.renderRoster();
      this.renderWaiting();
    }));
  }

  /**
   * Hold a ready AI day speech until the human asks for it, so the cloud models'
   * lines don't pile up faster than they can be read. The first speech after a
   * break (the human's own turn, a vote) comes through at once.
   */
  holdSpeech(speaker: number): Promise<void> {
    if (speaker === this.me || !this.unreadSpeech || !getConfig().stepSpeech) return Promise.resolve();
    return new Promise((resolve) => {
      const release = () => {
        if (this.held?.release !== release) return;
        this.held = null;
        this.unreadSpeech = false;
        this.close();
        // only now does the camera leave the last speech for the next speaker
        this.stage.focus(speaker);
        resolve();
      };
      this.held = { speaker, release };
      this.waitingFor = null;
      const p = this.game.players[speaker];
      // no chime: this is a reading aid, not a decision
      this.action.replaceChildren(
        h(
          'div',
          { class: 'next-speech' },
          h('div', { class: 'hint' }, `${seat(speaker)} ${p.name} 已准备好发言`),
          h('button', { class: 'btn primary', onclick: release }, '查看下一位发言 ▶'),
        ),
      );
      this.action.classList.add('show');
    });
  }

  /**
   * The exile has said their last words: they stay on the plaza with the words
   * overhead until the human confirms, then walk out of town and night falls.
   */
  confirmExile(id: number): Promise<void> {
    return new Promise((resolve) => {
      const go = () => {
        if (this.exileWait !== go) return;
        this.exileWait = null;
        this.close();
        this.bubbles.clear();
        this.pendingExile.delete(id);
        this.stage.exiled(id);
        resolve();
      };
      this.exileWait = go;
      const p = this.game.players[id];
      this.open(
        h(
          'div',
          { class: 'next-speech' },
          h('div', { class: 'hint' }, `${seat(id)} ${p.name} 的遗言已结束`),
          h('button', { class: 'btn primary', onclick: go }, '确定放逐 ▶'),
        ),
      );
    });
  }

  /**
   * Mobile plumbing: the sheets sit above the dock and the action panel, and the
   * action panel above the on-screen keyboard (CSS reads these as variables).
   */
  private bindMobile() {
    const root = this.root;
    const actionH = new ResizeObserver(() => root.style.setProperty('--action-h', `${this.action.offsetHeight}px`));
    actionH.observe(this.action);
    this.cleanups.push(() => actionH.disconnect());
    const vv = window.visualViewport;
    if (vv) {
      const onViewport = () => root.style.setProperty('--kb', `${Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop))}px`);
      vv.addEventListener('resize', onViewport);
      vv.addEventListener('scroll', onViewport);
      this.cleanups.push(() => {
        vv.removeEventListener('resize', onViewport);
        vv.removeEventListener('scroll', onViewport);
      });
    }
    const onMedia = () => {
      if (!MOBILE.matches) this.setSheet('none');
      this.applyView();
    };
    MOBILE.addEventListener('change', onMedia);
    this.cleanups.push(() => MOBILE.removeEventListener('change', onMedia));
    // a tapped bubble comes to the front (the desktop gets this from :hover)
    const onLabelTap = (e: PointerEvent) => {
      const i = this.labelEls.indexOf((e.target as HTMLElement).closest('.label') as HTMLElement);
      if (i >= 0) this.raised = i;
    };
    this.labelsRoot.addEventListener('pointerdown', onLabelTap);
    this.cleanups.push(() => this.labelsRoot.removeEventListener('pointerdown', onLabelTap));
    // the card swipes like the scene: left for the next seat, right for the one before
    let start: { x: number; y: number } | null = null;
    this.card.addEventListener('pointerdown', (e) => (start = { x: e.clientX, y: e.clientY }));
    this.card.addEventListener('pointerup', (e) => {
      if (!start) return;
      const dx = e.clientX - start.x;
      if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(e.clientY - start.y) * 1.2) this.setView(this.viewSeat + (dx < 0 ? 1 : -1));
      start = null;
    });
    this.card.addEventListener('pointercancel', () => (start = null));
    this.applyView();
    this.renderDock();
  }

  private setSheet(sheet: Sheet) {
    this.sheet = sheet;
    if (sheet === 'none') delete this.root.dataset.sheet;
    else this.root.dataset.sheet = sheet;
    if (sheet === 'chat') {
      this.unread = 0;
      this.chatLog.scrollTop = this.chatLog.scrollHeight;
    }
    this.renderDock();
  }

  private renderDock() {
    const me = this.game.players[this.me];
    const tab = (sheet: Sheet, ...label: (Node | string | null)[]) =>
      h(
        'button',
        {
          class: `dock-tab ${this.sheet === sheet ? 'on' : ''}`,
          'aria-pressed': String(this.sheet === sheet),
          onclick: () => this.setSheet(this.sheet === sheet ? 'none' : sheet),
        },
        ...label,
      );
    this.dock.replaceChildren(
      tab('players', h('span', { class: `dock-role ${isWolf(me.role) ? 'wolf' : 'good'}` }, `${this.me + 1}号 ${ROLE_NAME[me.role]}`), '玩家'),
      tab('chat', '记录', this.unread ? h('span', { class: 'badge' }, this.unread > 99 ? '99+' : String(this.unread)) : null),
      h(
        'button',
        {
          class: 'dock-tab view',
          title: this.closePref ? '切换到俯瞰全景' : '切换到人物近景',
          onclick: () => {
            this.closePref = !this.closePref;
            writeClosePref(this.closePref);
            this.applyView();
            this.renderDock();
          },
        },
        this.closePref ? '全景' : '近景',
      ),
    );
  }

  // ───────────────────────── mobile close-up ─────────────────────────

  /** Close-up on a phone (unless the player picked the overview); the desktop keeps the overview. */
  private applyView() {
    this.stage.closeUp = MOBILE.matches && this.closePref;
    this.cardKey = '';
  }

  /** Look at seat `id` (wraps round the ring). */
  private setView(id: number) {
    const n = this.game.players.length;
    this.viewSeat = ((id % n) + n) % n;
    this.stage.view(this.viewSeat);
    this.cardKey = '';
    this.stripKey = '';
    if (MOBILE.matches) {
      this.renderStrip();
      if (this.stage.inCloseUp) this.renderCard();
    }
  }

  /** The close-up moves on to whoever is now speaking / acting (a swipe holds only until then). */
  private follow(id: number | null) {
    if (id === null || id === this.autoSeat) return;
    this.autoSeat = id;
    this.setView(id);
  }

  /**
   * The part of the screen the HUD leaves uncovered: below the top bar, above the
   * panels spanning the width (portrait), left of the ones on the right (landscape).
   */
  private freeArea() {
    const W = innerWidth;
    const H = innerHeight;
    const r = { left: 0, top: 0, right: W, bottom: H };
    const shown = (el: Element | null) => {
      const b = el?.getBoundingClientRect();
      return b && b.width > 0 && b.height > 0 ? b : null;
    };
    for (const sel of ['#banner', '#seats', '#topright']) {
      const b = shown(this.root.querySelector(sel));
      if (b && b.top < H / 3) r.top = Math.max(r.top, b.bottom);
    }
    for (const sel of ['#closeup', '#action.show', '#dock', '#hud', '#chat']) {
      const b = shown(this.root.querySelector(sel));
      if (!b || b.top < r.top) continue;
      if (b.width > W * 0.6 || b.right < W * 0.6) r.bottom = Math.min(r.bottom, b.top);
      else r.right = Math.min(r.right, b.left);
    }
    return r;
  }

  /** Short identity for a seat chip: 狼 / 神 / 民 / 好, from the system's info or the human's mark. */
  private shortIdentity(id: number): { text: string; cls: string; mark: boolean } | null {
    if (id === this.me) return { text: '我', cls: 'me', mark: false };
    const k = this.knownOf(id);
    if (k) {
      const text = k.cls === 'wolf' ? '狼' : k.cls === 'god' ? '神' : k.ring === 'good' ? '好' : '民';
      return { text, cls: k.cls === 'good' && k.ring === 'good' ? 'good' : k.cls, mark: false };
    }
    const m = this.marks[id];
    return m ? { text: MARK_NAME[m], cls: MARK_CLS[m], mark: true } : null;
  }

  /** Who is on (speaking / acting / being read), for the seat strip and the card. */
  private onSeat(): number | null {
    return this.readingSpeaker() ?? this.visibleActor();
  }

  private renderStrip() {
    const on = this.onSeat();
    const sheriff = this.game.state.sheriff;
    const key = this.game.players
      .map((p, i) => `${p.alive || this.revived}${this.shortIdentity(i)?.text}${this.marks[i]}${this.bubbles.has(i)}`)
      .join('|') + `|${on}|${this.viewSeat}|${sheriff}|${this.stage.inCloseUp}`;
    if (key === this.stripKey) return;
    this.stripKey = key;
    this.strip.replaceChildren(
      ...this.game.players.map((p, i) => {
        const id = this.shortIdentity(i);
        const alive = p.alive || this.revived;
        return h(
          'button',
          {
            class: `seat-chip ${alive ? '' : 'dead'} ${on === i ? 'on' : ''} ${this.stage.inCloseUp && this.viewSeat === i ? 'viewed' : ''}`,
            style: `--seat:${bubbleColor(i).bg}`,
            title: `${seat(i)} ${p.name}`,
            'aria-label': `查看 ${seat(i)} ${p.name}`,
            onclick: () => {
              if (!this.closePref) {
                this.closePref = true;
                writeClosePref(true);
                this.applyView();
                this.renderDock();
              }
              this.setView(i);
            },
          },
          String(i + 1),
          id ? h('span', { class: `id ${id.cls} ${id.mark ? 'mark' : ''}` }, id.text) : null,
          sheriff === i ? h('span', { class: 'star' }, '★') : null,
          this.bubbles.has(i) ? h('span', { class: 'said' }) : null,
        );
      }),
    );
  }

  /** The viewed player's latest words: the bubble over their head, else the last thing they said that you saw. */
  private latestWords(id: number): { text: string; when: string } | null {
    const b = this.bubbles.get(id);
    if (b) return { text: b.text, when: '' };
    for (let k = this.game.events.length - 1; k >= 0; k--) {
      const e = this.game.events[k];
      if (e.speaker !== id || (e.type !== 'speech' && e.type !== 'wolfChat') || !this.canSee(e)) continue;
      const when = e.type === 'wolfChat' ? `第 ${e.day} 夜 · 狼队频道` : `第 ${e.day} 天${e.phase === 'election' ? ' · 警上' : ''}`;
      return { text: e.text, when };
    }
    return null;
  }

  private renderCard() {
    const i = this.viewSeat;
    const p = this.game.players[i];
    const on = this.onSeat();
    const actor = this.visibleActor();
    // the human's own turn isn't "thinking": their words stay up and the card says it's them
    const mine = i === this.me && actor === i;
    const thinking = ((actor === i && this.held?.speaker !== i) || this.ceremonyThinking === i) && !mine;
    const words = thinking ? null : this.latestWords(i);
    const alive = p.alive || this.revived;
    const key = `${i}|${alive}|${on}|${thinking}|${this.waitingFor}|${words?.text}|${this.identityOf(i)?.text}|${this.marks[i]}|${this.game.state.sheriff}|${this.stage.inCloseUp}`;
    if (key === this.cardKey) return;
    this.cardKey = key;
    const k = this.knownOf(i);
    const c = bubbleColor(i);
    const status = !alive ? '已出局' : mine ? '轮到你' : thinking ? (this.waitingFor === i ? '正在思考中…' : '思考中…') : on === i ? '发言中' : '';
    const text = thinking
      ? h('div', { class: 'words thinking' }, '…')
      : words
        ? h('div', { class: 'words', style: bubbleStyle(i) }, words.when ? h('div', { class: 'when' }, words.when) : null, words.text)
        : h('div', { class: 'words empty' }, '暂无发言');
    this.card.replaceChildren(
      h('button', { class: 'nav prev', 'aria-label': '上一位', onclick: () => this.setView(i - 1) }, '‹'),
      h(
        'div',
        { class: 'body' },
        h(
          'div',
          { class: 'head' },
          h('span', { class: 'n', style: `background:${c.bg};border-color:${c.edge}` }, String(i + 1)),
          h('span', { class: 'name' }, p.name, i === this.me ? '（你）' : ''),
          this.game.state.sheriff === i ? h('span', { class: 'star' }, '★警长') : null,
          k ? h('span', { class: `tag ${k.cls}` }, k.text) : i === this.me ? null : this.markCell(i, false),
          status ? h('span', { class: `status ${alive ? '' : 'out'}` }, status) : null,
        ),
        text,
        h(
          'button',
          {
            class: 'link history',
            onclick: () => {
              this.playerFilter = i;
              this.renderRoster();
              this.renderChat();
              this.setSheet('chat');
            },
          },
          'TA 的全部发言 ›',
        ),
      ),
      h('button', { class: 'nav next', 'aria-label': '下一位', onclick: () => this.setView(i + 1) }, '›'),
    );
  }

  /** Game time played so far, excluding pauses. */
  get elapsedMs() {
    return this.playedMs + (this.runningSince === null ? 0 : performance.now() - this.runningSince);
  }

  setPaused(paused: boolean) {
    if (paused && this.runningSince !== null) {
      this.playedMs = this.elapsedMs;
      this.runningSince = null;
    } else if (!paused && this.runningSince === null && this.game.state.phase !== 'ended') {
      this.runningSince = performance.now();
    }
  }

  private renderClock() {
    this.clock.textContent = formatClock(this.elapsedMs);
  }

  /** Quick mute; the same switch as 配置 → 声音. */
  private muteBtn() {
    const label = () => (getConfig().audio.muted ? '♪ 关' : '♪ 开');
    const b = h('button', { class: 'btn', title: '声音开关' }, label());
    b.onclick = () => updateConfig({ audio: { muted: !getConfig().audio.muted } });
    this.unsubConfig = onConfigChange(() => (b.textContent = label()));
    return b;
  }
  private unsubConfig = () => {};

  setEngineMode(mode: 'llm' | 'offline') {
    this.engineMode = mode;
    this.renderEngine();
  }
  private engineMode: 'llm' | 'offline' = 'llm';

  /** Telemetry from LLM agents. */
  recordCall(ok: boolean, ms: number) {
    if (ok) {
      this.engineStats.ok++;
      this.engineStats.lastMs = ms;
    } else this.engineStats.fail++;
    this.renderEngine();
  }

  private renderEngine() {
    const st = this.engineStats;
    if (this.engineMode === 'offline') {
      this.engineChip.className = 'engine panel warn';
      this.engineChip.textContent = '离线规则 AI（非 LLM）';
      return;
    }
    const bad = st.fallback > 0;
    this.engineChip.className = `engine panel ${bad ? 'warn' : ''}`;
    this.engineChip.textContent = `LLM ● ${st.ok} 次${st.lastMs ? ` · 上次 ${(st.lastMs / 1000).toFixed(1)}s` : ''}${st.fail ? ` · 失败 ${st.fail}` : ''}${bad ? ` · 代打 ${st.fallback}` : ''}`;
  }

  /** The model failed for an AI player: pause and let the human decide. */
  askFailure(info: { player: number; kind: string; error: string }): Promise<'retry' | 'fallback'> {
    return new Promise((resolve) => {
      const p = this.game.players[info.player];
      const KIND: Record<string, string> = {
        discussion: '发言', summary: '警长归票', lastWords: '遗言', defense: 'PK发言', vote: '投票', revote: 'PK再投',
        wolfChat: '狼队沟通', wolfKill: '狼队投票', seer: '查验', guard: '守护', hunterShot: '开枪', wolfKingShot: '狼王开枪', witchSave: '救人', witchPoison: '用毒',
        campaign: '警上发言', campaignPk: '警长PK发言', runForSheriff: '上警', withdraw: '退水', sheriffVote: '警长投票', sheriffRevote: '警长PK再投', badge: '移交警徽', speakOrder: '决定发言顺序',
      };
      // never reveal who acts at night (would leak roles)
      const secret = this.game.state.phase === 'night' && !this.godView;
      const who = secret ? '夜间某位 AI 玩家行动' : `${seat(p.id)} ${p.name} 的「${KIND[info.kind] ?? info.kind}」`;
      const done = (v: 'retry' | 'fallback') => {
        back.remove();
        if (v === 'fallback') {
          this.engineStats.fallback++;
          this.renderEngine();
        }
        resolve(v);
      };
      const back = h(
        'div',
        { class: 'modal-back' },
        h(
          'div',
          { class: 'modal panel', role: 'alertdialog' },
          h('h2', {}, 'AI 调用失败，游戏已暂停'),
          h('p', {}, `${who}的请求没有拿到可用结果（已自动重试）。`),
          h('p', { class: 'sub' }, info.error),
          h('p', { class: 'sub' }, '可能原因：模型服务不可达、显存不足（HTTP 507）、另一个程序正在占用本地模型导致排队超时。'),
          h(
            'div',
            { class: 'actions' },
            h('button', { class: 'btn', onclick: () => done('fallback') }, '本次由规则 AI 代打'),
            h('button', { class: 'btn primary', onclick: () => done('retry') }, '重试'),
          ),
        ),
      );
      this.root.appendChild(back);
    });
  }

  private destroyed = false;
  destroy() {
    this.destroyed = true;
    this.unsubConfig();
    for (const off of this.cleanups) off();
    delete this.root.dataset.sheet;
    document.body.classList.remove('closeup');
    this.stage.closeUp = false;
    this.stage.onSwipe = undefined;
    for (const v of ['--action-h', '--kb']) this.root.style.removeProperty(v);
    clearInterval(this.actorTimer);
    clearInterval(this.clockTimer);
    for (const el of [...this.root.children]) el.remove();
    this.labelsRoot.replaceChildren();
    this.stage.onFrame = undefined;
    this.stage.onPick = undefined;
  }

  // ───────────────────────── game hooks ─────────────────────────

  onEvent(e: GameEvent) {
    if (e.type === 'wolfChat' && typeof e.data?.target === 'number' && this.canSee(e)) this.wolfTarget = e.data.target;
    if (this.restoring) {
      // replaying a save: only keep the bubbles current; restored() rebuilds the rest at once
      if (this.canSee(e)) this.trackBubble(e);
      return;
    }
    if (e.type === 'death') {
      const id = e.data!.id as number;
      // exiles walk home after their last words; everyone else leaves a grave now
      if (e.data!.cause === 'vote') this.pendingExile.add(id);
      else if (e.data!.cause === 'explode') this.blast = this.stage.exploded(id);
      else this.stage.killed(id);
    }
    if (e.type === 'gm' && e.text.includes('天亮了')) audio.rooster();
    // the GM's verdict sting on the night's news (after the election on day 1)
    if (e.type === 'gm' && Array.isArray(e.data?.nightDeaths)) {
      if ((e.data.nightDeaths as number[]).length) audio.death();
      else audio.peaceful();
    }
    if (!this.canSee(e)) return;
    if (e.type === 'speech') {
      this.unreadSpeech = e.speaker !== this.me;
      this.lastSpeaker = e.speaker ?? null;
      const said = this.speakingLabel;
      this.lastSpeakerLabel = said && said.id === e.speaker ? said.label : '';
      // not held back (逐条查看 off): the new speech replaces the waiting panel
      if (this.waitingFor !== null) {
        this.waitingFor = null;
        this.close();
      }
    }
    else if (e.type === 'vote') this.unreadSpeech = false;
    this.trackBubble(e);
    if ((e.type === 'speech' || e.type === 'wolfChat') && e.speaker !== undefined) this.follow(e.speaker);
    if (e.type === 'speech' || e.type === 'vote') {
      this.renderActor();
      this.renderRoster();
    }
    if (this.matches(e)) this.appendMsg(e);
    if (e.type === 'private' || e.type === 'gm' || e.type === 'death') this.renderRoster();
  }

  private trackBubble(e: GameEvent) {
    // the day's talk stays up through the vote; the result clears the plaza so the
    // tied players' defenses or the exile's last words stand alone
    if (e.type === 'vote') this.bubbles.clear();
    if ((e.type === 'speech' || e.type === 'wolfChat') && e.speaker !== undefined) {
      // every speaker keeps their latest words over their head (day: until everyone
      // goes home at nightfall; wolf chat: until the pack is back indoors)
      const spot = e.speechKind === 'defense' || e.speechKind === 'lastWords' || e.speechKind === 'campaignPk';
      this.bubbles.set(e.speaker, { text: e.text, seq: ++this.bubbleSeq, spot });
    }
  }

  /**
   * The replay of a save has caught up: put the scene where the game stands
   * (graves, sealed houses, who is indoors, wolves out) without animation or sound.
   */
  restored() {
    this.restoring = false;
    const s = this.game.state;
    const night = s.phase === 'night';
    const dead: number[] = [];
    const exiled: number[] = [];
    const exploded: number[] = [];
    for (const e of this.game.events) {
      if (e.type !== 'death') continue;
      const id = e.data!.id as number;
      // today's exile still stands on the plaza for the vote result / last words
      if (e.data!.cause === 'explode') exploded.push(id);
      else if (e.data!.cause !== 'vote') dead.push(id);
      else if (e.day === s.day && (s.phase === 'vote' || s.phase === 'lastWords')) this.pendingExile.add(id);
      else exiled.push(id);
    }
    const iSeeWolves = isWolf(this.game.players[this.me].role) || this.godView;
    this.wolvesShown = night && s.nightStep === 'wolves' && iSeeWolves ? this.game.wolves().filter((w) => w.alive).map((w) => w.id) : [];
    this.lastHowlStep = `${s.day}:${s.nightStep}`;
    // tonight's own actions that leave a mark until dawn: the guard's bell, houses gone dark
    let guarded: number | null = null;
    const darkened: number[] = [];
    if (night) {
      for (const e of this.game.events) {
        if (e.day !== s.day || e.phase !== 'night' || !this.canSee(e) || !e.data) continue;
        if (typeof e.data.guard === 'number') guarded = e.data.guard;
        for (const k of ['poison', 'shot'] as const) if (typeof e.data[k] === 'number') darkened.push(e.data[k] as number);
      }
    }
    this.stage.restore({ dead, exiled, exploded, indoors: night, wolves: this.wolvesShown, night, guarded, darkened });
    this.stage.setBadge(s.sheriff);
    this.onState(s);
    this.renderTabs();
  }

  onState(s: GameState) {
    if (this.restoring) {
      this.scopeBubbles(s);
      return;
    }
    const night = s.phase === 'night';
    if (night) this.unreadSpeech = false;
    this.stage.setNight(night);
    audio.setNight(night);
    this.stage.focus(this.cameraTarget());
    // nightfall: the close-up goes home to your own door (unless your role is up)
    if (night && !this.lastPhase.endsWith(':night')) {
      this.autoSeat = null;
      this.setView(this.me);
    }
    // only speeches move the close-up: votes and quick decisions come too fast to follow
    if (s.actorSpeaks || this.readingSpeaker() !== null) this.follow(this.cameraTarget());
    this.choreograph(s);
    const phaseName: Record<string, string> = {
      setup: '准备中',
      night: `第 ${s.day} 夜`,
      dawn: `第 ${s.day} 天 · 天亮`,
      election: `第 ${s.day} 天 · 警长竞选`,
      discussion: `第 ${s.day} 天 · 发言`,
      vote: `第 ${s.day} 天 · 投票`,
      lastWords: `第 ${s.day} 天 · 遗言`,
      ended: '游戏结束',
    };
    const phaseEl = this.banner.querySelector('.phase')!;
    phaseEl.textContent = phaseName[s.phase];
    phaseEl.className = `phase ${night ? 'night' : ''}`;
    if (s.actor !== null && s.actorSpeaks) this.speakingLabel = { id: s.actor, label: s.actorLabel };
    this.renderActor();
    this.renderWaiting();
    if (s.nightStep !== this.lastStep) {
      this.lastStep = s.nightStep;
      if (s.nightStep) {
        this.stepEl.textContent = NIGHT_STEP_NAME[s.nightStep];
        this.stepEl.className = 'show';
        const step = s.nightStep;
        setTimeout(() => {
          if (this.lastStep === step) this.stepEl.className = 'show dim';
        }, 3000);
      } else this.stepEl.className = '';
    }
    const phaseKey = `${s.day}:${s.phase}`;
    if (phaseKey !== this.lastPhase) {
      const wasElection = this.lastPhase.endsWith(':election');
      this.lastPhase = phaseKey;
      // the election has its own record: follow it while it runs, then back to the day's
      if (s.phase === 'election' && this.tab === 'round') this.tab = 'election';
      else if (wasElection && this.tab === 'election') this.tab = 'round';
      this.renderTabs();
    }
    this.renderRoster();
    this.renderItems();
    if (s.phase === 'ended') {
      this.setPaused(true);
      clearInterval(this.clockTimer);
      this.renderClock();
      this.showEnd(s);
    }
  }

  /**
   * The banner's "who is on": the acting player with a running timer — or, while the
   * human is still reading the last AI speech (逐条查看), that speaker, so the banner,
   * roster and bubbles agree; the next one's thinking shows over their head instead.
   */
  private renderActor() {
    const s = this.game.state;
    const actorEl = this.banner.querySelector('.actor')!;
    const reading = this.readingSpeaker();
    if (reading !== null) {
      const actorKey = `read:${reading}:${this.lastSpeakerLabel}`;
      if (actorKey === this.actorKey) return;
      this.actorKey = actorKey;
      clearInterval(this.actorTimer);
      actorEl.textContent = `${seat(reading)} ${this.game.players[reading].name}${this.lastSpeakerLabel ? ` · ${this.lastSpeakerLabel}` : ''}`;
      return;
    }
    const night = s.phase === 'night';
    const secret = night && !this.godView;
    const mine = s.actor === this.me;
    // At night the banner only shows the public turn and how long it has lasted —
    // the timer restarts per turn, never per actor (per-actor restarts would leak
    // e.g. whether the witch was asked to save someone). Your own action is shown.
    const actorKey = secret && !mine
      ? (s.nightStep ? `step:${s.day}:${s.nightStep}` : '')
      : s.actor !== null ? `${s.actor}:${s.actorLabel}` : '';
    if (actorKey !== this.actorKey) {
      this.actorKey = actorKey;
      clearInterval(this.actorTimer);
      if (!actorKey) actorEl.textContent = '';
      else {
        this.actorSince = this.elapsedMs;
        const timer = h('span', { class: 'timer' });
        actorEl.replaceChildren(
          secret && !mine ? '夜幕下' : `${seat(s.actor!)} ${this.game.players[s.actor!].name} · ${s.actorLabel}`,
          h('span', { class: 'dots' }),
          mine ? '' : timer,
        );
        this.actorTimer = window.setInterval(() => {
          const sec = Math.floor((this.elapsedMs - this.actorSince) / 1000);
          timer.textContent = sec >= 3 ? ` ${sec}s` : '';
        }, 500);
      }
    }
  }

  // ───────────────────────── scene choreography ─────────────────────────

  private pendingExile = new Set<number>();
  /** Tonight's kill, from the wolves' vote (only reaches wolf / god-view players). */
  private wolfTarget: number | null = null;
  /** A wolf's house going up; the GM waits for it before nightfall. */
  private blast: Promise<void> | null = null;
  private bubbleScope = '';
  private wolvesShown: number[] = [];
  private lastHowlStep: string | null = null;

  /** Map engine state changes onto scene animation (howls, exiles). */
  private choreograph(s: GameState) {
    this.scopeBubbles(s);
    const stepKey = `${s.day}:${s.nightStep}`;
    if (s.nightStep === 'wolves' && this.lastHowlStep !== stepKey) {
      this.lastHowlStep = stepKey;
      audio.howl(0.9, 0.4);
      audio.howl(0.6, 1.6, 1.12);
    }
    if (this.pendingExile.size && s.phase !== 'vote' && s.phase !== 'lastWords') {
      for (const id of this.pendingExile) this.stage.exiled(id);
      this.pendingExile.clear();
    }
  }

  /** night ↔ day: wipe leftover bubbles from the previous part of the round
   * (the wolf-chat bubbles are their own scope, gone once the wolf turn ends). */
  private scopeBubbles(s: GameState) {
    const bubbleScope = `${s.day}:${s.phase === 'night' ? `night:${s.nightStep === 'wolves' ? 'wolves' : ''}` : s.phase === 'election' ? 'election' : 'day'}`;
    if (bubbleScope !== this.bubbleScope) {
      this.bubbleScope = bubbleScope;
      this.bubbles.clear();
    }
  }

  /**
   * The GM waits on these before moving on (e.g. the wolf turn ends only once
   * the pack is back indoors). Capped so a hidden tab (paused rAF) never stalls the game.
   */
  cue(c: SceneCue): Promise<void> {
    if (typeof c === 'object') return c.kind === 'badge' ? this.badgeCue(c) : this.roleCue(c);
    const iSeeWolves = isWolf(this.game.players[this.me].role) || this.godView;
    let job: Promise<void> = Promise.resolve();
    switch (c) {
      case 'nightfall':
        job = this.stage.nightFall();
        break;
      case 'dawn':
        job = this.stage.dayBreak();
        break;
      case 'wolvesOut':
        if (iSeeWolves) {
          this.wolvesShown = this.game.wolves().filter((w) => w.alive).map((w) => w.id);
          job = this.stage.wolvesOut(this.wolvesShown);
        }
        break;
      case 'explode':
        job = this.blast ?? job;
        this.blast = null;
        break;
      case 'wolvesIn':
        // the pack stops talking once it moves on the house
        this.bubbles.clear();
        if (this.wolvesShown.length) {
          const shown = this.wolvesShown;
          const target = this.wolfTarget;
          this.wolvesShown = [];
          this.wolfTarget = null;
          // the kill plays out first (the guard's 金钟罩 turns it away), then the pack goes home
          const attack = target === null ? Promise.resolve() : this.stage.wolfAttack(shown, target, this.game.state.lastGuarded === target);
          job = attack.then(() => this.stage.wolvesIn(shown));
        }
        break;
    }
    // capped so a hidden tab (paused rAF) never stalls the game; the attack takes a while
    return Promise.race([job, new Promise<void>((r) => setTimeout(r, c === 'wolvesIn' ? 40000 : 12000))]);
  }

  /** The badge flies to the new sheriff / heir, or is torn up: everyone sees it. */
  private badgeCue(c: BadgeCue): Promise<void> {
    if (c.to !== null) this.stage.focus(c.to);
    return Promise.race([this.stage.moveBadge(c.from, c.to), new Promise<void>((r) => setTimeout(r, 12000))]);
  }

  /**
   * A night role's action plays out only for the player who took it (or god view);
   * for anyone else it resolves at once, so its length gives nothing away. The
   * day shot (猎人 / 狼王) is public.
   */
  private roleCue(c: RoleCue): Promise<void> {
    if (c.kind !== 'dayShot' && c.actor !== this.me && !this.godView) return Promise.resolve();
    const home = this.me;
    let job: Promise<void>;
    switch (c.kind) {
      case 'guard':
        job = this.stage.guardCover(c.target, home);
        break;
      case 'seer':
        job = this.stage.seerReveal(c.target, isWolf(this.game.players[c.target].role), home);
        break;
      case 'witchPoison':
        job = this.stage.witchPoison(c.target, home);
        break;
      case 'witchSave':
        job = this.stage.witchSave(c.target, home);
        break;
      case 'dayShot':
        job = this.stage.dayShot(c.target);
        break;
    }
    return Promise.race([job, new Promise<void>((r) => setTimeout(r, 20000))]);
  }

  // ───────────────────────── roster / role card ─────────────────────────

  private renderItems() {
    const items = this.roleCard.querySelector('.items')!;
    const s = this.game.state;
    const me = this.game.players[this.me];
    const chips: HTMLElement[] = [];
    if (me.role === 'witch') {
      chips.push(h('span', { class: `chip ${s.witch.hasAntidote ? '' : 'used'}` }, '金水'));
      chips.push(h('span', { class: `chip ${s.witch.hasPoison ? '' : 'used'}` }, '银水'));
    }
    if (me.role === 'hunter') chips.push(h('span', { class: `chip ${s.hunterShot ? 'used' : ''}` }, '猎枪 ×1'));
    if (me.role === 'wolfKing') chips.push(h('span', { class: `chip ${s.wolfKingShot ? 'used' : ''}` }, '狼王枪 ×1'));
    if (me.role === 'guard' && s.lastGuarded !== null) chips.push(h('span', { class: 'chip' }, `昨夜守护 ${seat(s.lastGuarded)}`));
    if (s.sheriff === this.me) chips.push(h('span', { class: 'chip sheriff' }, '★ 警长'));
    if (!me.alive && !this.revived) chips.push(h('span', { class: 'chip used' }, '已出局'));
    items.replaceChildren(...chips);
  }

  /**
   * Who the camera looks at: the acting player, except while the human is still
   * reading the last AI speech (逐条查看) — the next AI thinking doesn't pull it away.
   */
  private cameraTarget(): number | null {
    const actor = this.visibleActor();
    if (this.unreadSpeech && getConfig().stepSpeech && actor !== this.me && this.lastSpeaker !== null) return this.lastSpeaker;
    return actor;
  }

  /**
   * The AI whose speech the human is still reading (逐条查看) while the next AI
   * speaker thinks or waits to be let through; null once the human's own turn or
   * a vote takes over. The roster and banner point at them, not at the thinker.
   */
  private readingSpeaker(): number | null {
    if (!this.unreadSpeech || !getConfig().stepSpeech || this.lastSpeaker === null) return null;
    const s = this.game.state;
    if (s.actor !== null && (s.actor === this.me || !s.actorSpeaks)) return null;
    return this.lastSpeaker;
  }

  /** While the next AI thinks, the 下一位 button is already there, disabled. */
  private renderWaiting() {
    // holdSpeech owns the panel once the speech is ready
    if (this.held) return;
    const s = this.game.state;
    const reading = this.readingSpeaker() !== null;
    // the model just answered: keep the panel up until holdSpeech enables the button
    if (reading && s.actor === null && this.waitingFor !== null) return;
    const next = reading ? s.actor : null;
    if (next === this.waitingFor) return;
    if (this.waitingFor !== null) this.close();
    this.waitingFor = next;
    if (next === null) return;
    // no chime: nothing to decide yet
    this.action.replaceChildren(
      h(
        'div',
        { class: 'next-speech' },
        h('div', { class: 'hint' }, `${seat(next)} ${this.game.players[next].name} 正在思考中…`),
        h('button', { class: 'btn primary', disabled: true }, '下一位正在思考中…'),
      ),
    );
    this.action.classList.add('show');
  }

  /** Night actors stay secret unless it is you (or god view): never point the camera at one. */
  private visibleActor(): number | null {
    const s = this.game.state;
    if (s.actor === null) return null;
    if (s.phase === 'night' && s.actor !== this.me && !this.godView) return null;
    return s.actor;
  }

  private renderRoster() {
    const actor = this.readingSpeaker() ?? this.visibleActor();
    const rows = this.game.players.map((p) => {
      const k = this.knownOf(p.id);
      const num = h('span', { class: `num ${k?.ring ? `ring-${k.ring}` : ''}` }, String(p.id + 1));
      const row = h(
        'div',
        {
          class: `roster-row ${p.alive || this.revived ? '' : 'dead'} ${actor === p.id ? 'acting' : ''} ${this.playerFilter === p.id ? 'selected' : ''}`,
          role: 'button',
          tabindex: '0',
          title: '点击只看 TA 的发言；选择目标时点击即可选中',
          onclick: () => this.pick(p.id),
        },
        num,
        h('span', { class: 'name' }, p.name, p.id === this.me ? '（你）' : '', this.game.state.sheriff === p.id ? h('span', { class: 'star', title: '警长' }, ' ★警长') : ''),
        k ? h('span', { class: `tag ${k.cls}` }, k.text) : this.markCell(p.id, !p.alive && !this.revived),
      );
      return row;
    });
    this.roster.replaceChildren(...rows);
  }

  /** A player the system tells the human nothing about: a button to mark them 民/神/狼. */
  private markCell(id: number, dead: boolean) {
    const m = this.marks[id];
    const btn = h(
      'button',
      {
        class: `mark ${m ? `set ${MARK_CLS[m]}` : ''}`,
        title: '自定义身份标记：点击切换 民 → 神 → 狼 → 清除',
        'aria-label': `标记 ${seat(id)} 的身份`,
        onclick: (ev: Event) => {
          ev.stopPropagation();
          this.cycleMark(id);
        },
        onkeydown: (ev: KeyboardEvent) => ev.stopPropagation(),
      },
      m ? MARK_NAME[m] : '标记',
    );
    return h('span', { class: 'tag' }, dead ? h('span', { class: 'out' }, '出局') : null, btn);
  }

  private pick(id: number) {
    if (this.pendingTarget && this.pendingTarget.req.candidates.includes(id)) {
      this.pendingTarget.select(id);
      return;
    }
    this.playerFilter = this.playerFilter === id ? null : id;
    this.renderRoster();
    this.renderChat();
    // on a phone the log is folded away: show what the filter picked
    if (MOBILE.matches && this.playerFilter !== null) this.setSheet('chat');
  }

  // ───────────────────────── chat ─────────────────────────

  private renderTabs() {
    const me = this.game.players[this.me];
    const tabs: [Tab, string][] = [['round', '本轮']];
    // 警长竞选 keeps its own shared record, from the moment it starts
    if (this.game.state.phase === 'election' || this.game.events.some((e) => e.phase === 'election')) tabs.push(['election', '警长竞选']);
    tabs.push(['all', '全部']);
    if (isWolf(me.role) || this.godView) tabs.push(['wolf', '狼队频道']);
    tabs.push(['private', '私密信息']);
    if (this.ceremonyLog) tabs.unshift(['award', '颁奖典礼']);
    this.chatTabs.replaceChildren(
      ...tabs.map(([t, label]) =>
        h('button', { class: `tab ${t === 'wolf' ? 'wolf' : ''} ${this.tab === t ? 'active' : ''}`, role: 'tab', onclick: () => { this.tab = t; this.renderTabs(); this.renderChat(); } }, label),
      ),
    );
    this.renderChat();
  }

  private matches(e: GameEvent): boolean {
    if (!this.canSee(e)) return false;
    if (this.playerFilter !== null && e.speaker !== this.playerFilter) return false;
    switch (this.tab) {
      case 'round':
        return e.day === this.game.state.day && e.type !== 'system' && e.phase !== 'election';
      case 'election':
        return e.phase === 'election' && e.type !== 'system';
      case 'all':
        return e.type !== 'system';
      case 'wolf':
        return e.type === 'wolfChat';
      case 'private':
        return e.visibility.kind === 'private' && e.type !== 'wolfChat';
      case 'award':
        return false;
    }
  }

  private renderChat() {
    this.chatFilter.replaceChildren();
    if (this.playerFilter !== null) {
      this.chatFilter.append(`只看 ${seat(this.playerFilter)} ${this.game.players[this.playerFilter].name} 的发言 `, h('button', { onclick: () => { this.playerFilter = null; this.renderRoster(); this.renderChat(); } }, '清除'));
    }
    this.chatLog.replaceChildren();
    if (this.tab === 'award') {
      for (const e of this.ceremonyLog ?? []) this.appendCeremony(e, false);
      this.chatLog.scrollTop = this.chatLog.scrollHeight;
      return;
    }
    let lastDay = -1;
    for (const e of this.game.events) {
      if (!this.matches(e)) continue;
      if (e.day !== lastDay && this.tab !== 'round') {
        this.chatLog.append(h('div', { class: 'daydiv' }, e.day === 0 ? '— 开局 —' : `— 第 ${e.day} 天 —`));
        lastDay = e.day;
      }
      this.appendMsg(e, false);
    }
    this.chatLog.scrollTop = this.chatLog.scrollHeight;
  }

  /** A spoken line: a block in the speaker's bubble colour, name included (same as over their head). */
  private said(id: number, cls: string, ...rest: (Node | string | null)[]) {
    const P = this.game.players;
    return h(
      'div',
      { class: `msg said ${cls}`, style: bubbleStyle(id) },
      h('div', { class: 'who' }, h('span', { class: 'n' }, String(id + 1)), P[id].name, id === this.me ? h('span', { class: 'you' }, '（你）') : null, this.identityChip(id), ...rest),
    );
  }

  /** The speaker's identity in the log: the system's info, or the human's mark (dashed). */
  private identityChip(id: number) {
    const k = this.identityOf(id);
    if (!k) return null;
    return h('span', { class: `idtag ${k.cls} ${k.mark ? 'mark' : ''}`, title: k.mark ? '你的自定义标记' : '系统给出的身份信息' }, k.text);
  }

  private appendCeremony(e: CeremonyEntry, scroll = true) {
    let el: HTMLElement;
    if (e.kind === 'note') el = h('div', { class: 'msg gm' }, e.text);
    else {
      const fb = e.fallback ? h('span', { class: 'kind fallback', title: '模型调用失败，由规则 AI 代发' }, '规则AI代打') : null;
      el = this.said(e.id, '', h('span', { class: 'kind' }, e.tag), fb);
      el.append(h('div', { class: 'text' }, e.text));
    }
    this.chatLog.append(el);
    if (scroll) this.chatLog.scrollTop = this.chatLog.scrollHeight;
  }

  private appendMsg(e: GameEvent, scroll = true) {
    let el: HTMLElement;
    switch (e.type) {
      case 'speech': {
        const kind = e.data?.explode ? '自爆' : SPEECH_KIND[e.speechKind ?? 'discussion'];
        const fb = e.data?.fallback ? h('span', { class: 'kind fallback', title: '模型调用失败，由规则 AI 代发' }, '规则AI代打') : null;
        el = this.said(e.speaker!, '', kind ? h('span', { class: 'kind' }, kind) : null, fb);
        el.append(h('div', { class: 'text' }, e.text));
        break;
      }
      case 'wolfChat':
        if (e.speaker !== undefined) {
          el = this.said(e.speaker, 'wolf', h('span', { class: 'kind' }, '狼队'));
          el.append(h('div', { class: 'text' }, e.text));
        } else el = h('div', { class: 'msg wolf' }, e.text);
        break;
      default:
        el = h('div', { class: `msg ${e.type}` }, e.text);
    }
    this.chatLog.append(el);
    if (scroll) {
      this.chatLog.scrollTop = this.chatLog.scrollHeight;
      if (MOBILE.matches && this.sheet !== 'chat') {
        this.unread++;
        this.renderDock();
      }
    }
  }

  // ───────────────────────── labels ─────────────────────────

  private placeLabels(pos: ScreenPos[]) {
    if (MOBILE.matches) {
      // the close-up has the card for words; the name plates come back for the wide shots
      const close = this.stage.inCloseUp;
      const now = performance.now();
      if (close !== document.body.classList.contains('closeup') || now - this.panelsAt > 200) {
        this.panelsAt = now;
        document.body.classList.toggle('closeup', close);
        this.renderStrip();
        if (close) this.renderCard();
        this.stage.setFreeArea(this.freeArea());
      }
    } else document.body.classList.remove('closeup');
    const actor = this.visibleActor();
    pos.forEach((p, i) => {
      const el = this.labelEls[i];
      el.style.display = p.visible ? '' : 'none';
      if (!p.visible) return;
      // keep labels near the screen's edges (seats at the ring's sides on a phone) fully on screen
      const half = (this.labelW[i] ?? 0) / 2 + 4;
      el.style.left = `${half * 2 < innerWidth ? Math.min(Math.max(p.x, half), innerWidth - half) : p.x}px`;
      el.style.top = `${p.y}px`;
      const pl = this.game.players[i];
      const k = this.knownOf(i);
      const b = this.bubbles.get(i);
      const thinking = (actor === i && this.held?.speaker !== i) || this.ceremonyThinking === i;
      // spelled out while the last speech is still being read, so it's clear who is next
      const thinkingText = this.waitingFor === i ? '正在思考中…' : '...';
      // newest words on top; hovering a bubble brings it to the front (CSS)
      el.style.zIndex = this.raised === i ? '9999' : String(b ? 10 + b.seq : 1);
      const alive = pl.alive || this.revived;
      const key = `${alive}|${k?.text}|${k?.ring}|${b ? `${b.spot}:${b.text}` : ''}|${thinking && thinkingText}`;
      if (el.dataset.key === key) return;
      el.dataset.key = key;
      el.className = `label ${alive ? '' : 'dead'} ${i === this.me ? 'me' : ''}`;
      const parts = [
        b ? h('div', { class: `bubble ${b.spot ? 'spot' : ''}`, style: bubbleStyle(i) }, b.text) : null,
        thinking ? h('div', { class: 'thinking' }, thinkingText) : null,
        k && i !== this.me && !k.ring ? h('div', { class: `role-tag ${k.cls}` }, k.text) : null,
        k?.ring ? h('div', { class: `role-tag ${k.ring}` }, this.godView || this.game.state.phase === 'ended' ? ROLE_NAME[pl.role] : k.ring === 'wolf' ? '狼人' : '好人') : null,
        h('div', { class: 'plate' }, h('span', { class: `n ${k?.ring ? `ring-${k.ring}` : ''}` }, String(i + 1)), pl.name, alive ? '' : ' ✝'),
      ];
      el.replaceChildren(...parts.filter((x): x is HTMLDivElement => x !== null));
      this.labelW[i] = el.offsetWidth;
    });
  }

  // ───────────────────────── human decisions ─────────────────────────

  private open(...children: (Node | string | null)[]) {
    audio.chime();
    this.action.replaceChildren(...children.filter((c): c is Node | string => c !== null));
    this.action.classList.add('show');
  }

  private close() {
    this.action.classList.remove('show');
    this.action.replaceChildren();
    this.pendingTarget = null;
  }

  private askSpeech(req: SpeechRequest | WolfChatRequest, _view: PlayerView): Promise<SpeechResult> {
    return new Promise((resolve) => {
      const title =
        req.kind === 'wolfChat'
          ? `狼队频道 · 第 ${req.round}/${req.rounds} 轮`
          : { discussion: '轮到你发言', summary: '警长发言 · 归票', lastWords: '你的遗言', defense: '放逐 PK 发言', campaign: '警上竞选发言', campaignPk: '警长 PK 发言' }[req.purpose];
      const hint =
        req.kind === 'wolfChat'
          ? req.othersPassed
            ? '队友们都表示没有补充。你还想说什么就写下来，队友会在下一轮回应；否则直接开始投票。'
            : '只有狼队友能看到。和队友商量刀谁、白天怎么配合；没有补充可直接点「过」。'
          : req.purpose === 'summary'
            ? '你是警长，所有人都已发言。梳理大家的站边与矛盾，点出你建议大家放逐的号码（归票）。'
            : req.purpose === 'campaign'
              ? '说说你为什么要当警长。预言家一般在这里报查验，并留警徽流（今晚验谁、明晚验谁）。昨晚死讯还没公布。'
              : '在这里输入你想对全镇说的话。可以提到「N号」。';
      const ta = h('textarea', { placeholder: req.kind === 'wolfChat' ? '例：今晚刀 5 号，他像预言家…' : '例：我是好人，3 号发言前后矛盾…', maxlength: '400' }) as HTMLTextAreaElement;
      const count = h('span', { class: 'count' }, '0 / 400');
      ta.oninput = () => (count.textContent = `${ta.value.length} / 400`);
      const done = (text: string, explode = false) => {
        this.close();
        resolve(explode ? { text, explode } : text);
      };
      // two clicks: the first arms it, so a stray click can't throw the game away
      let armed = false;
      const explodeBtn =
        req.kind === 'speech' && req.canExplode
          ? (h('button', {
              class: 'btn danger',
              title: '公开狼人身份并立即出局（无遗言）；今天剩余发言和投票取消，直接天黑。输入框里的话会作为你的最后发言。',
              onclick: () => {
                if (armed) return done(ta.value.trim() || '过', true);
                armed = true;
                explodeBtn!.textContent = '确认自爆？';
              },
            }, '自爆') as HTMLButtonElement)
          : null;
      ta.onkeydown = (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && ta.value.trim()) done(ta.value.trim());
      };
      this.open(
        h('div', { class: 'title' }, title),
        h('div', { class: 'hint' }, hint),
        ta,
        h(
          'div',
          { class: 'row' },
          count,
          ...(req.kind === 'wolfChat' && req.othersPassed
            ? [
                h('button', { class: 'btn', onclick: () => ta.value.trim() && done(ta.value.trim()) }, '补充 (⌘↵)'),
                h('button', { class: 'btn primary', onclick: () => done('pass') }, '没有补充，开始投票'),
              ]
            : [
                explodeBtn,
                h('button', { class: 'btn', onclick: () => done(req.kind === 'wolfChat' ? 'pass' : '过。') }, '过'),
                h('button', { class: 'btn primary', onclick: () => ta.value.trim() && done(ta.value.trim()) }, '发言 (⌘↵)'),
              ]),
        ),
      );
      // a phone would throw up the keyboard over the scene before the prompt is read
      if (!MOBILE.matches) setTimeout(() => ta.focus(), 50);
    });
  }

  private askTarget(req: TargetRequest, _view: PlayerView): Promise<number | null> {
    return new Promise((resolve) => {
      let selected: number | null = null;
      const buttons = new Map<number, HTMLButtonElement>();
      const confirmBtn = h('button', { class: 'btn primary', disabled: true }, '确认') as HTMLButtonElement;
      const select = (id: number) => {
        selected = id;
        for (const [k, b] of buttons) b.classList.toggle('sel', k === id);
        confirmBtn.disabled = false;
        confirmBtn.textContent = `确认：${seat(id)} ${this.game.players[id].name}`;
      };
      const finish = (v: number | null) => {
        this.close();
        resolve(v);
      };
      confirmBtn.onclick = () => selected !== null && finish(selected);
      const targets = h(
        'div',
        { class: 'targets' },
        ...req.candidates.map((id) => {
          const b = h('button', { class: 'btn', title: this.game.players[id].name, onclick: () => select(id) }, String(id + 1)) as HTMLButtonElement;
          buttons.set(id, b);
          return b;
        }),
      );
      if (YES_NO_ACTIONS.includes(req.action)) return this.askYesNo(req, finish);
      const skipLabel: Partial<Record<TargetRequest['action'], string>> = {
        vote: '弃票', revote: '弃票', guard: '空守', hunterShot: '不开枪', wolfKingShot: '不开枪', witchSave: '不救', witchPoison: '不用毒',
        wolfKill: '空刀', sheriffVote: '弃票', sheriffRevote: '弃票', badge: '撕掉警徽',
      };
      this.open(
        h('div', { class: 'title' }, ACTION_TITLE[req.action]),
        h('div', { class: 'hint' }, req.prompt, MOBILE.matches ? ' 也可以在「玩家」列表或场景中点选角色。' : ' 也可以点击左侧列表或场景中的角色来选择。'),
        targets,
        h('div', { class: 'row' }, req.allowSkip ? h('button', { class: 'btn', onclick: () => finish(null) }, skipLabel[req.action] ?? '跳过') : null, confirmBtn),
      );
      this.pendingTarget = { req, select };
    });
  }

  /** 上警 / 退水: two buttons (and, for a wolf on the stage, 自爆 behind a confirm click). */
  private askYesNo(req: TargetRequest, finish: (v: number | null) => void) {
    const [yes, no] = req.action === 'runForSheriff' ? ['上警', '不上警'] : ['退水', '继续竞选'];
    let armed = false;
    const explodeBtn = req.canExplode
      ? (h('button', {
          class: 'btn danger',
          title: '公开狼人身份并立即出局（无遗言）；警长竞选推迟到明天，今天直接天黑。',
          onclick: () => {
            if (armed) return finish(EXPLODE_CHOICE);
            armed = true;
            explodeBtn!.textContent = '确认自爆？';
          },
        }, '自爆') as HTMLButtonElement)
      : null;
    this.open(
      h('div', { class: 'title' }, ACTION_TITLE[req.action]),
      h('div', { class: 'hint' }, req.prompt),
      h(
        'div',
        { class: 'row' },
        explodeBtn,
        h('button', { class: 'btn', onclick: () => finish(null) }, no),
        h('button', { class: 'btn primary', onclick: () => finish(this.me) }, yes),
      ),
    );
  }

  // ───────────────────────── end ─────────────────────────

  private endShown = false;
  /** The results panel, once it is up (「回看记录」 takes it down; 暂停 / Esc bring it back). */
  private endModal: HTMLElement | null = null;

  /** 暂停 after the game is over: bring the results (and 回到标题画面) back. Returns false while the finale plays. */
  reopenEnd(): boolean {
    if (!this.endModal) return false;
    if (!this.endModal.isConnected) this.root.appendChild(this.endModal);
    return true;
  }

  /** Who takes part in the 颁奖典礼 (the human's own seat is null: they are asked through the panel). */
  setReviewers(list: (Reviewer | null)[], notes: () => (string[] | null)[]) {
    this.reviewers = { list, notes };
  }

  private startCeremony() {
    if (!this.reviewers || this.ceremonyLog) return;
    this.ceremonyLog = [];
    // the results panel no longer offers the ceremony
    this.endModal?.querySelector('.btn.award')?.remove();
    this.tab = 'award';
    this.playerFilter = null;
    this.bubbles.clear();
    this.renderTabs();
    const ceremony = new AwardCeremony({
      game: this.game,
      stage: this.stage,
      me: this.me,
      reviewers: this.reviewers.list,
      notes: this.reviewers.notes,
      log: (e) => {
        this.ceremonyLog!.push(e);
        if (this.tab === 'award') this.appendCeremony(e);
        else if (MOBILE.matches) {
          this.unread++;
          this.renderDock();
        }
      },
      bubble: (id, text) => {
        if (text === null) this.bubbles.delete(id);
        else this.bubbles.set(id, { text, seq: ++this.bubbleSeq, spot: true });
      },
      clearBubbles: () => this.bubbles.clear(),
      thinking: (id) => (this.ceremonyThinking = id),
      status: (phase, actor = '') => {
        this.banner.querySelector('.phase')!.textContent = phase;
        clearInterval(this.actorTimer);
        this.actorKey = '';
        this.banner.querySelector('.actor')!.textContent = actor;
      },
      panel: (...children) => {
        this.action.replaceChildren(...children.filter((c): c is Node | string => c !== null));
        this.action.classList.add('show');
      },
      closePanel: () => this.close(),
      revive: () => {
        this.revived = true;
        this.renderRoster();
        this.renderItems();
      },
      finish: () => {
        this.action.replaceChildren(
          h('div', { class: 'title' }, '🎉 颁奖典礼结束'),
          h('div', { class: 'hint' }, '感谢参与！可以在「颁奖典礼」页回看所有赛后感言和投票明细。'),
          h('div', { class: 'row' }, h('button', { class: 'btn', onclick: () => this.close() }, '继续围观'), h('button', { class: 'btn primary', onclick: () => this.onRestart() }, '回到标题画面')),
        );
        this.action.classList.add('show');
      },
      leave: () => this.onRestart(),
      gone: () => this.destroyed,
    });
    void ceremony.run().catch((e) => console.error(e));
  }

  private showEnd(s: GameState) {
    if (this.endShown) return;
    this.endShown = true;
    this.close();
    const meTeam = teamOf(this.game.players[this.me].role);
    const won = s.winner === meTeam;
    // the ending plays out on the stage first, then the results come up
    let finale: Promise<void>;
    if (s.winner === 'wolf') {
      audio.setEnding('hush');
      const wolves = this.game.wolves().filter((w) => w.alive).map((w) => w.id);
      finale = this.stage.wolfFinale(wolves, () => audio.setEnding('wolf'));
    } else {
      audio.setEnding('good');
      finale = this.stage.goodFinale();
    }
    // capped in real time: at a low frame rate (the GPU busy with a local model) the scene crawls
    const cap = new Promise<void>((r) => setTimeout(r, 15000));
    void Promise.race([finale, cap]).then(() => setTimeout(() => {
      if (this.destroyed) return; // back at the title already
      const back = h(
        'div',
        { class: 'modal-back' },
        h(
          'div',
          { class: 'modal panel' },
          h('div', { class: `winner ${s.winner}` }, s.winner === 'good' ? '好人胜利' : '狼人胜利'),
          h('p', { style: 'text-align:center' }, won ? '你所在的阵营赢得了这座小镇。' : '你所在的阵营输掉了这一局。'),
          h('div', { class: 'reveal' }, ...this.game.players.map((p) => h('div', { class: teamOf(p.role) === 'wolf' ? 'wolf' : '' }, `${seat(p.id)} ${p.name}`, h('br'), `${ROLE_NAME[p.role]}${p.alive ? '' : ' ✝'}`))),
          h(
            'div',
            { class: 'actions' },
            h('button', { class: 'btn', onclick: () => { back.remove(); if (MOBILE.matches) this.setSheet('chat'); } }, '回看记录'),
            this.reviewers && !this.ceremonyLog
              ? h('button', { class: 'btn award', title: '全员复活，赛后测评、投票选出全场最佳与最差', onclick: () => { back.remove(); this.startCeremony(); } }, '🏆 颁奖典礼')
              : null,
            h('button', { class: 'btn primary', onclick: () => this.onRestart() }, '回到标题画面'),
          ),
        ),
      );
      this.endModal = back;
      this.root.appendChild(back);
      this.renderRoster();
      this.renderChat();
    }, s.winner === 'wolf' ? 5000 : 3000)); // hold on the moon and the howling / the dance a little
  }
}

export function formatClock(ms: number): string {
  const sec = Math.floor(ms / 1000);
  const hh = Math.floor(sec / 3600);
  const mm = String(Math.floor((sec % 3600) / 60)).padStart(2, '0');
  const ss = String(sec % 60).padStart(2, '0');
  return hh ? `${hh}:${mm}:${ss}` : `${mm}:${ss}`;
}
