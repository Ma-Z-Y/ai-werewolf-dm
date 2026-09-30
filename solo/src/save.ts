import type { LLMSnapshot } from './ai/llmAgent';
import type { MockSnapshot } from './ai/mockAgent';
import type { Decision, Phase, Role } from './game/types';
import type { GamePrefs, Settings } from './settings';
import type { Mark } from './ui/hud';

/**
 * One save slot in localStorage. A game is rebuilt by dealing from the same
 * seeds and replaying the journal of answers (see `GameOptions.replay`), so a
 * save stays small and restores every detail: the log, bubbles, potions, the
 * seer's checks, what each AI noted to itself.
 */
export interface SaveGame {
  /** 3: 狼王守卫板子（狼王 + 3 狼）; older journals no longer replay. */
  v: 3;
  savedAt: number;
  /** Written by the background autosave rather than the pause menu. */
  auto?: true;
  /** That game's own choices (pace, sound and the LLM connection come from 配置). */
  prefs: GamePrefs & Pick<Settings, 'mode'>;
  /** Seats the human and shuffles the personas. */
  setupSeed: number;
  gameSeed: number;
  journal: Decision[];
  /** Per seat: the AI's own memory (null for the human). */
  agents: (LLMSnapshot | MockSnapshot | null)[];
  elapsedMs: number;
  /** The human's own 民/神/狼 marks per seat (older saves have none). */
  marks?: (Mark | null)[];
  /** Shown on the title screen. */
  meta: { seat: number; role: Role; day: number; phase: Phase; alive: number };
}

const KEY = 'ai-werewolf:save:v3';

const ROLES = new Set<Role>(['werewolf', 'wolfKing', 'villager', 'seer', 'witch', 'hunter', 'guard']);
const PHASES = new Set<Phase>(['setup', 'night', 'dawn', 'election', 'discussion', 'vote', 'lastWords', 'ended']);
const MARKS = new Set<Mark>(['villager', 'god', 'wolf']);

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function number(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value);
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function decision(value: unknown): value is Decision {
  if (!record(value)) return false;
  if ('t' in value) return value.t === null || number(value.t);
  return typeof value.s === 'string';
}

function role(value: unknown): value is Role {
  return typeof value === 'string' && ROLES.has(value as Role);
}

function phase(value: unknown): value is Phase {
  return typeof value === 'string' && PHASES.has(value as Phase);
}

function mark(value: unknown): value is Mark | null {
  return value === null || (typeof value === 'string' && MARKS.has(value as Mark));
}

function agentSnapshot(value: unknown): LLMSnapshot | MockSnapshot | null {
  return record(value) ? (value as unknown as LLMSnapshot | MockSnapshot) : null;
}

/**
 * Validate the parts the loader and title screen actually index into. Returning
 * null for a damaged save is much better than throwing halfway through boot.
 */
export function parseSave(raw: unknown): SaveGame | null {
  if (!record(raw) || raw.v !== 3) return null;
  if (!number(raw.savedAt) || !number(raw.setupSeed) || !number(raw.gameSeed)) return null;
  if (!finite(raw.elapsedMs) || raw.elapsedMs < 0) return null;
  if (!Array.isArray(raw.journal) || !raw.journal.every(decision)) return null;
  if (!record(raw.prefs)) return null;
  if (typeof raw.prefs.playerName !== 'string' || raw.prefs.playerName.trim().length === 0) return null;
  if (!(raw.prefs.role === 'random' || role(raw.prefs.role))) return null;
  if (!number(raw.prefs.wolfChatRounds)) return null;
  if (typeof raw.prefs.godView !== 'boolean') return null;
  if (raw.prefs.mode !== 'llm' && raw.prefs.mode !== 'offline') return null;
  if (!record(raw.meta) || !number(raw.meta.seat) || !role(raw.meta.role) || !phase(raw.meta.phase) || !number(raw.meta.alive)) return null;

  const agents = Array.from({ length: 12 }, (_, i) => agentSnapshot(Array.isArray(raw.agents) ? raw.agents[i] : null));
  const rawMarks = Array.isArray(raw.marks) ? raw.marks : [];
  const marks = Array.from({ length: 12 }, (_, i) => (mark(rawMarks[i]) ? rawMarks[i] : null)) as (Mark | null)[];
  return {
    v: 3,
    savedAt: raw.savedAt,
    ...(raw.auto === true ? { auto: true as const } : {}),
    prefs: { ...(raw.prefs as unknown as SaveGame['prefs']) },
    setupSeed: raw.setupSeed,
    gameSeed: raw.gameSeed,
    journal: raw.journal as Decision[],
    agents,
    elapsedMs: raw.elapsedMs,
    marks,
    meta: raw.meta as SaveGame['meta'],
  };
}

export function readSave(): SaveGame | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    return parseSave(JSON.parse(raw));
  } catch {
    return null;
  }
}

/** Returns an error message, or null when saved. */
export function writeSave(s: SaveGame): string | null {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
    return null;
  } catch (e) {
    return (e as Error).name === 'QuotaExceededError' ? '浏览器存储空间不足' : `无法写入浏览器存储：${(e as Error).message}`;
  }
}

export function clearSave() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
