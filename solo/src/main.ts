import './ui/styles.css';
import { LLMAgent } from './ai/llmAgent';
import { MockAgent } from './ai/mockAgent';
import { PERSONAS, TRAVELLER_LOOK } from './personas';
import { OpenAICompatibleProvider, SerialQueue } from './ai/provider';
import { Game, GameAborted, ReplayMismatch } from './game/game';
import { Rng } from './game/rng';
import type { Agent } from './game/types';
import type { Reviewer } from './game/ceremony';
import { Stage } from './render/stage';
import { audio } from './audio/audio';
import { clearSave, readSave, writeSave, type SaveGame } from './save';
import { h } from './ui/dom';
import { GameUI, formatClock } from './ui/hud';
import { showPause } from './ui/pause';
import { showRules } from './ui/rules';
import { PROVIDERS } from './ai/catalog';
import { vault } from './keyVault';
import { getConfig, loadGamePrefs, onConfigChange, resolveProvider, type Settings } from './settings';
import { promptSetKey, promptUnlock } from './ui/keyDialogs';
import { showConfig } from './ui/config';
import { showSetup } from './ui/setup';
import { PHASE_NAME, showTitle } from './ui/title';

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('./sw.js').catch(() => {
      /* offline support is a bonus; the game still works online */
    });
  });
}

const app = document.getElementById('app')!;
const labels = document.getElementById('labels')!;
const stage = new Stage(document.getElementById('stage')!);
// no pinch-zooming the page: iOS Safari ignores `user-scalable=no`, so stop its gestures here
// (the town's own pinch-zoom runs on pointer events and is unaffected)
for (const type of ['gesturestart', 'gesturechange', 'gestureend']) document.addEventListener(type, (e) => e.preventDefault());
document.addEventListener('touchmove', (e) => { if (e.touches.length > 1) e.preventDefault(); }, { passive: false });
if (import.meta.env.DEV) (window as unknown as { __stage: Stage }).__stage = stage; // debugging hook
stage.sounds = {
  doorOpen: (v) => audio.doorOpen(v),
  doorClose: (v) => audio.doorClose(v),
  doorBreak: (v) => audio.doorBreak(v),
  seal: (v) => audio.seal(v),
  squeak: (v) => audio.squeak(v),
  flap: (v) => audio.batFlap(v),
  caw: (v) => audio.caw(v),
  thunder: (d) => audio.thunder(d),
  explosion: (v, big) => audio.explosion(v, big),
  doorBang: (v) => audio.doorBang(v),
  growl: (v) => audio.growl(v),
  scream: (v) => audio.scream(v),
  bell: (v) => audio.bell(v),
  guardCast: (v) => audio.guardCast(v),
  sparkle: (v) => audio.sparkle(v),
  reveal: (wolf) => audio.reveal(wolf),
  poison: (v) => audio.poison(v),
  groan: (v) => audio.groan(v),
  hymn: () => audio.hymn(),
  lock: () => audio.lock(),
  gunshot: () => audio.gunshot(),
  hit: (v) => audio.hit(v),
  howl: (v, pitch) => audio.howl(v, 0, pitch),
  thud: (v) => audio.thud(v),
  splat: (v) => audio.splat(v),
};

// sound settings apply everywhere (title screen included)
const applyAudio = () => {
  const a = getConfig().audio;
  audio.setMuted(a.muted);
  audio.setLevels(a);
};
applyAudio();
onConfigChange(applyAudio);

function toast(text: string) {
  // on <body>, so it outlives the game UI (e.g. a save that fails to load)
  const el = h('div', { class: 'toast panel' }, text);
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 6000);
}

/** Play one game (new, or resumed from `save`) until the player goes back to the title. */
async function play(settings: Settings, save?: SaveGame) {
  const { playerName, role, wolfChatRounds, godView, mode } = settings;
  const prefs = { playerName, role, wolfChatRounds, godView, mode };
  const setupSeed = save?.setupSeed ?? (Math.random() * 2 ** 32) >>> 0;
  const rng = new Rng(setupSeed);
  const humanSeat = rng.int(12);
  const personas = rng.shuffle(PERSONAS);
  const names = personas.map((p, i) => (i === humanSeat ? settings.playerName : p.name));

  let ui: GameUI | null = null;
  let requestAutoSave = () => {};
  const game = new Game(
    {
      names,
      humanSeat,
      humanRole: settings.role === 'random' ? undefined : settings.role,
      paceMs: settings.paceMs,
      wolfChatRounds: settings.wolfChatRounds,
      seed: save?.gameSeed,
      replay: save?.journal,
    },
    {
      onEvent: (e) => {
        ui?.onEvent(e);
        requestAutoSave();
      },
      onState: (s) => {
        ui?.onState(s);
        requestAutoSave();
      },
      cue: (c) => ui?.cue(c) ?? Promise.resolve(),
      restored: () => ui?.restored(),
      beforeSpeech: (id) => ui?.holdSpeech(id) ?? Promise.resolve(),
      confirmExile: (id) => ui?.confirmExile(id) ?? Promise.resolve(),
    },
  );

  if (import.meta.env.DEV) (window as unknown as { __game: Game }).__game = game; // debugging hook
  let leave!: () => void;
  const left = new Promise<void>((r) => (leave = r));
  stage.resetAll();
  // everyone looks like their trade; the human is the hooded traveller
  const looks = personas.map((p, i) => (i === humanSeat ? TRAVELLER_LOOK : p.look));
  stage.setLooks(looks);
  ui = new GameUI(app, labels, stage, game, humanSeat, settings.godView, leave, looks, {
    restoring: !!save,
    elapsedMs: save?.elapsedMs ?? 0,
    marks: save?.marks,
    onPause: () => void pause(),
  });

  const provider = new OpenAICompatibleProvider(settings.provider, (id) => vault.getKey(id));
  const queue = new SerialQueue();
  ui.setEngineMode(settings.mode);
  const agents: (Agent & Partial<Snapshotting>)[] = names.map((_, i) => {
    if (i === humanSeat) return ui!.agent;
    if (settings.mode === 'offline') return new MockAgent(rng.int(1e9), 600);
    return new LLMAgent(i, personas[i], provider, queue, {
      onCall: (c) => ui?.recordCall(c.ok, c.ms),
      onFailure: (info) => ui!.askFailure(info),
    });
  });
  save?.agents.forEach((snap, i) => snap && agents[i].restore?.(snap as never));
  game.setAgents(agents);
  // 颁奖典礼: every AI reviews the game (the human answers through the panel)
  ui.setReviewers(
    agents.map((a, i) => (i === humanSeat ? null : (a as Agent & Reviewer))),
    () => agents.map((a) => (a instanceof LLMAgent ? a.notes : null)),
  );
  // 配置 changes made from the pause menu reach the running game
  const unsubConfig = onConfigChange((c) => {
    game.setPace(c.paceMs);
    provider.config = resolveProvider(c);
  });

  // ── save / pause ──
  let savedLen = save ? save.journal.length : -1;
  let savedMarks = JSON.stringify(save?.marks ?? null);
  let autoSaveTimer: number | null = null;
  let suppressAutoSave = false;
  const snapshot = (auto = false): SaveGame => {
    const s = game.state;
    return {
      v: 3,
      savedAt: Date.now(),
      ...(auto ? { auto: true as const } : {}),
      prefs,
      setupSeed,
      gameSeed: game.seed,
      journal: game.journal.slice(),
      agents: agents.map((a) => a.snapshot?.() ?? null),
      elapsedMs: ui!.elapsedMs,
      marks: ui!.playerMarks,
      meta: { seat: humanSeat, role: game.players[humanSeat].role, day: s.day, phase: s.phase, alive: game.alive().length },
    };
  };
  const flushAutoSave = () => {
    if (suppressAutoSave || game.replaying) return;
    if (game.state.phase === 'ended') {
      clearSave();
      savedLen = -1;
      return;
    }
    const snap = snapshot(true);
    if (writeSave(snap) === null) {
      savedLen = snap.journal.length;
      savedMarks = JSON.stringify(snap.marks);
    }
  };
  requestAutoSave = () => {
    if (suppressAutoSave || game.replaying) return;
    if (game.state.phase === 'ended') {
      clearSave();
      savedLen = -1;
      return;
    }
    if (autoSaveTimer !== null) window.clearTimeout(autoSaveTimer);
    autoSaveTimer = window.setTimeout(() => {
      autoSaveTimer = null;
      flushAutoSave();
    }, 1200);
  };
  const flushOnHide = () => {
    if (document.visibilityState === 'hidden') flushAutoSave();
  };
  const flushOnPageHide = () => flushAutoSave();
  document.addEventListener('visibilitychange', flushOnHide);
  window.addEventListener('pagehide', flushOnPageHide);

  let pausing = false;
  const pause = async () => {
    if (pausing) return;
    if (game.state.phase === 'ended') {
      ui!.reopenEnd();
      return;
    }
    pausing = true;
    game.pause();
    stage.paused = true;
    audio.pause();
    ui!.setPaused(true);
    const s = game.state;
    const choice = await showPause(app, {
      status: `${PHASE_NAME[s.phase](s.day)} · 用时 ${formatClock(ui!.elapsedMs)}`,
      cannotSave: null,
      save: () => {
        const snap = snapshot();
        const err = writeSave(snap);
        if (!err) {
          savedLen = snap.journal.length;
          savedMarks = JSON.stringify(snap.marks);
        }
        return err;
      },
      dirty: () => game.journal.length !== savedLen || JSON.stringify(ui!.playerMarks) !== savedMarks,
      config: () => showConfig(app, { inGame: settings.mode }),
    });
    pausing = false;
    if (choice === 'title') {
      suppressAutoSave = true;
      leave();
      return;
    }
    audio.resume();
    stage.paused = false;
    ui!.setPaused(false);
    game.resume();
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && !document.querySelector('.modal-back')) void pause();
  };
  document.addEventListener('keydown', onKey);

  const done = game.run().catch((e) => {
    if (e instanceof GameAborted) return;
    console.error(e);
    if (e instanceof ReplayMismatch) {
      toast(`存档无法载入：${e.message}（可能是旧版本的存档）`);
      leave();
    } else toast(`游戏出错：${(e as Error).message}`);
  });
  await Promise.race([left, done.then(() => left)]);
  if (autoSaveTimer !== null) window.clearTimeout(autoSaveTimer);
  document.removeEventListener('visibilitychange', flushOnHide);
  window.removeEventListener('pagehide', flushOnPageHide);
  unsubConfig();
  document.removeEventListener('keydown', onKey);
  game.abort();
  stage.paused = false;
  audio.resume();
  ui.destroy();
  audio.setEnding(null);
  stage.resetAll();
}

interface Snapshotting {
  snapshot(): NonNullable<SaveGame['agents'][number]>;
  restore(s: never): void;
}

/** An official API needs its key available (set, and the vault unlocked) before the game starts. */
async function keyReady(s: Settings): Promise<boolean> {
  if (s.mode !== 'llm' || !PROVIDERS[s.provider.provider].needsKey) return true;
  const id = s.provider.provider;
  if (!vault.hints()[id] && !(await promptSetKey(app, id))) return false;
  return promptUnlock(app, `开始对局需要使用已保存的 ${PROVIDERS[id].label} API Key。`);
}

async function main() {
  while (true) {
    const choice = await showTitle(app, readSave(), {
      onRules: () => showRules(app),
      onConfig: () => {
        audio.start(); // so the volume sliders can be heard
        void showConfig(app, { inGame: null });
      },
      onDelete: clearSave,
    });
    audio.start(); // the title click is the user gesture that unlocks audio
    if (choice === 'load') {
      const save = readSave();
      if (!save) continue;
      // the game's own choices from the save; pace and connection from 配置
      const { playerName, role, wolfChatRounds, godView, mode } = save.prefs;
      const settings: Settings = { playerName, role, wolfChatRounds, godView, mode, paceMs: getConfig().paceMs, provider: resolveProvider() };
      if (await keyReady(settings)) await play(settings, save);
      continue;
    }
    if (choice === 'quick') {
      const prefs = loadGamePrefs();
      const settings: Settings = {
        ...prefs,
        mode: 'offline',
        paceMs: getConfig().paceMs,
        provider: resolveProvider(),
      };
      await play(settings);
      continue;
    }
    const settings = await showSetup(app, () => showRules(app));
    if (settings && (await keyReady(settings))) await play(settings);
  }
}

main();
