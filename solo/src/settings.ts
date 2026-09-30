import { PROVIDERS, PROVIDER_IDS, clampEffort, effortsFor, providerAvailable, type ProviderId } from './ai/catalog';
import type { ProviderConfig } from './ai/provider';
import { envProvider } from './config';
import type { Role } from './game/types';

/**
 * Global configuration (配置 panel): sound, pace and the AI engine. Changes
 * apply at once — also to a game in progress — and are announced to listeners.
 *
 * Everything persists in the browser except API keys, which live encrypted in
 * the key vault (see keyVault.ts). The local server's address and model list
 * come from `.env` only: the panel picks a model from that list, nothing else.
 */
export interface AudioLevels {
  muted: boolean;
  /** 0..1 */
  music: number;
  sfx: number;
  ambience: number;
}

/** One provider's saved choices (the matrix keeps one per provider). */
export interface LlmProfile {
  /** Fixed: the official address, or `.env` LLM_BASE_URL for the local server. */
  baseUrl: string;
  model: string;
  reasoning: string;
  decisionReasoning: string;
  useProxy: boolean;
}

export interface Config {
  mode: 'llm' | 'offline';
  paceMs: number;
  /** Day: hold each AI speech until the human asks for the next one. */
  stepSpeech: boolean;
  audio: AudioLevels;
  llm: { active: ProviderId; profiles: Record<ProviderId, LlmProfile> };
}

/** Per-game choices made on the new-game screen. */
export interface GamePrefs {
  playerName: string;
  role: Role | 'random';
  wolfChatRounds: number;
  godView: boolean;
}

/** Everything a game is started with (`provider` = the active profile, resolved). */
export type Settings = GamePrefs & Pick<Config, 'mode' | 'paceMs'> & { provider: ProviderConfig };

const CONFIG_KEY = 'ai-werewolf:config:v1';
const GAME_KEY = 'ai-werewolf:settings:v1';
const OLD_MUTE_KEY = 'ai-werewolf:muted';

const DEFAULT_AUDIO: AudioLevels = { muted: false, music: 0.8, sfx: 0.8, ambience: 0.8 };
const DEFAULT_GAME: GamePrefs = { playerName: '旅人', role: 'random', wolfChatRounds: 3, godView: false };

function read<T>(key: string): Partial<T> {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as Partial<T>) : {};
  } catch {
    return {};
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* ignore */
  }
}

function defaultProfile(id: ProviderId): LlmProfile {
  const preset = PROVIDERS[id];
  if (id === 'local') {
    const env = envProvider().config;
    return { baseUrl: env.baseUrl || preset.baseUrl, model: env.model, reasoning: env.reasoning, decisionReasoning: env.decisionReasoning, useProxy: env.useProxy };
  }
  // official APIs go through the dev-server proxy too (no CORS surprises)
  return { baseUrl: preset.baseUrl, model: preset.defaultModel, ...preset.suggested, useProxy: true };
}

/** The models a provider may use: its official list, or `.env` LLM_MODELS for the local server. */
export function modelIds(id: ProviderId): string[] {
  return PROVIDERS[id].fromEnv ? envProvider().models : PROVIDERS[id].models.map((m) => m.id);
}

/** A profile that only holds values its provider accepts. */
function sane(id: ProviderId, p: Partial<LlmProfile>): LlmProfile {
  const d = defaultProfile(id);
  // a model no longer listed (e.g. removed from .env) falls back to the default
  const model = p.model && modelIds(id).includes(p.model) ? p.model : d.model;
  const efforts = effortsFor(id, model);
  return {
    baseUrl: d.baseUrl,
    model,
    reasoning: clampEffort(p.reasoning ?? d.reasoning, efforts),
    decisionReasoning: clampEffort(p.decisionReasoning ?? d.decisionReasoning, efforts),
    useProxy: p.useProxy ?? d.useProxy,
  };
}

export function localDefaults(): LlmProfile {
  return defaultProfile('local');
}

function loadConfig(): Config {
  const saved = read<Config>(CONFIG_KEY);
  // older builds kept mode/pace with the game prefs and the mute flag on its own
  const legacy = read<{ mode: Config['mode']; paceMs: number }>(GAME_KEY);
  let legacyMuted = false;
  try {
    legacyMuted = localStorage.getItem(OLD_MUTE_KEY) === '1';
  } catch {
    /* ignore */
  }
  return {
    mode: saved.mode ?? legacy.mode ?? 'llm',
    paceMs: saved.paceMs ?? legacy.paceMs ?? 900,
    stepSpeech: saved.stepSpeech ?? true,
    audio: { ...DEFAULT_AUDIO, muted: legacyMuted, ...saved.audio },
    llm: {
      active: PROVIDER_IDS.includes(saved.llm?.active as ProviderId) && providerAvailable(saved.llm!.active) ? saved.llm!.active : providerAvailable('local') ? 'local' : 'openai',
      profiles: Object.fromEntries(PROVIDER_IDS.map((id) => [id, sane(id, saved.llm?.profiles?.[id] ?? {})])) as Record<ProviderId, LlmProfile>,
    },
  };
}

function persist() {
  write(CONFIG_KEY, current);
}

let current = loadConfig();
// pin the migrated values before the new-game screen rewrites the old key
if (!Object.keys(read<Config>(CONFIG_KEY)).length) persist();
const listeners = new Set<(c: Config) => void>();

export function getConfig(): Config {
  return current;
}

function changed() {
  persist();
  for (const fn of listeners) fn(current);
}

export function updateConfig(patch: Partial<Pick<Config, 'mode' | 'paceMs' | 'stepSpeech'>> & { audio?: Partial<AudioLevels> }) {
  current = { ...current, ...patch, audio: { ...current.audio, ...patch.audio } };
  changed();
}

/** Pick which provider drives the AI. */
export function setActiveProvider(id: ProviderId) {
  current = { ...current, llm: { ...current.llm, active: id } };
  changed();
}

/** Edit one provider's profile; invalid values (e.g. an effort the new model lacks) are corrected. */
export function updateProfile(id: ProviderId, patch: Partial<LlmProfile>) {
  const profiles = { ...current.llm.profiles, [id]: sane(id, { ...current.llm.profiles[id], ...patch }) };
  current = { ...current, llm: { ...current.llm, profiles } };
  changed();
}

/** The request settings of the active (or given) provider. */
export function resolveProvider(c: Config = current, id: ProviderId = c.llm.active): ProviderConfig {
  return { provider: id, ...c.llm.profiles[id], timeoutMs: envProvider().config.timeoutMs };
}

/** Returns an unsubscribe function. */
export function onConfigChange(fn: (c: Config) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function loadGamePrefs(): GamePrefs {
  const { playerName, role, wolfChatRounds, godView } = { ...DEFAULT_GAME, ...read<GamePrefs>(GAME_KEY) };
  return { playerName, role, wolfChatRounds, godView };
}

export function saveGamePrefs(p: GamePrefs) {
  write(GAME_KEY, p);
}
