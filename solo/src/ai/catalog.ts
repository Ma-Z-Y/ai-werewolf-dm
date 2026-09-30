/**
 * The providers the game can talk to (all OpenAI-compatible Chat Completions),
 * their default endpoints, their models, and the reasoning values each model
 * officially accepts. Checked against the providers' docs on 2026-09-24:
 *
 * - OpenAI  https://developers.openai.com/api/docs/models (each model's page)
 *   `reasoning_effort` (top level); the allowed values differ per model.
 *   Reasoning models reject `temperature`; the output cap is `max_completion_tokens`.
 * - DeepSeek https://api-docs.deepseek.com/guides/thinking_mode
 *   `thinking: {type: "enabled" | "disabled"}` + top-level `reasoning_effort`
 *   (`low` | `high` | `max`); `temperature` has no effect while thinking.
 * - Local servers (MTPLX / Qwen-style): `reasoning_effort`, and
 *   `chat_template_kwargs.enable_thinking=false` to turn thinking off.
 */

export type ProviderId = 'openai' | 'deepseek' | 'local';

/** Request dialect, see `buildChatBody`. */
export type Dialect = 'openai' | 'deepseek' | 'local';

export interface ModelSpec {
  id: string;
  /** Short description shown in the model picker. */
  note: string;
  /**
   * Accepted reasoning values, in increasing effort; `none` means "no thinking".
   * Empty: not a reasoning model (no reasoning parameter is sent).
   */
  efforts: string[];
  /** The provider's default when the parameter is omitted. */
  defaultEffort?: string;
}

export interface ProviderPreset {
  id: ProviderId;
  label: string;
  dialect: Dialect;
  baseUrl: string;
  /**
   * The local server: address and model list come from `.env` (LLM_BASE_URL /
   * LLM_MODELS), not from this preset; the game only picks from that list.
   */
  fromEnv: boolean;
  /** Official APIs need a key; a local server usually does not. */
  needsKey: boolean;
  /** Where to get a key. */
  keyUrl?: string;
  /** Fixed list (official APIs); the local server's list is `.env` LLM_MODELS. */
  models: ModelSpec[];
  /** Model picked when this provider is first chosen. */
  defaultModel: string;
  /** Suggested levels for a werewolf table (many short calls): speech / decisions. */
  suggested: { reasoning: string; decisionReasoning: string };
}

const GPT6_EFFORTS = ['none', 'low', 'medium', 'high', 'xhigh', 'max'];
const GPT55_EFFORTS = ['none', 'low', 'medium', 'high', 'xhigh'];

export const PROVIDERS: Record<ProviderId, ProviderPreset> = {
  openai: {
    id: 'openai',
    label: 'OpenAI',
    dialect: 'openai',
    baseUrl: 'https://api.openai.com/v1',
    fromEnv: false,
    needsKey: true,
    keyUrl: 'https://platform.openai.com/api-keys',
    models: [
      { id: 'gpt-6-luna', note: 'GPT-6 Luna · 最快最省 · $0.1 / $0.5', efforts: GPT6_EFFORTS, defaultEffort: 'medium' },
      { id: 'gpt-6-sol', note: 'GPT-6 Sol · 均衡 · $2 / $10', efforts: GPT6_EFFORTS, defaultEffort: 'medium' },
      // Astra does not accept `none` (HTTP 400)
      { id: 'gpt-6-astra', note: 'GPT-6 Astra · 旗舰 · $10 / $50', efforts: ['low', 'medium', 'high', 'xhigh', 'max'] },
      { id: 'gpt-5.6-luna', note: 'GPT-5.6 Luna · $0.2 / $1.2', efforts: GPT6_EFFORTS, defaultEffort: 'medium' },
      { id: 'gpt-5.6-terra', note: 'GPT-5.6 Terra · $2 / $12', efforts: GPT6_EFFORTS, defaultEffort: 'medium' },
      { id: 'gpt-5.6-sol', note: 'GPT-5.6 Sol · $4 / $20', efforts: GPT6_EFFORTS, defaultEffort: 'medium' },
      { id: 'gpt-5.5', note: 'GPT-5.5 · $5 / $30', efforts: GPT55_EFFORTS, defaultEffort: 'medium' },
      { id: 'gpt-5.4-mini', note: 'GPT-5.4 mini · $0.75 / $4.5', efforts: GPT55_EFFORTS, defaultEffort: 'none' },
      { id: 'gpt-5.4-nano', note: 'GPT-5.4 nano · $0.2 / $1.25', efforts: GPT55_EFFORTS, defaultEffort: 'none' },
      { id: 'gpt-4.1', note: 'GPT-4.1 · 非推理模型 · $2 / $8', efforts: [] },
    ],
    defaultModel: 'gpt-6-luna',
    suggested: { reasoning: 'low', decisionReasoning: 'none' },
  },
  deepseek: {
    id: 'deepseek',
    label: 'DeepSeek',
    dialect: 'deepseek',
    baseUrl: 'https://api.deepseek.com',
    fromEnv: false,
    needsKey: true,
    keyUrl: 'https://platform.deepseek.com/api_keys',
    models: [
      // `none` = thinking disabled; low / high / max = thinking with that effort
      { id: 'deepseek-flash', note: 'DeepSeek-V4.1-Flash · 快', efforts: ['none', 'low', 'high', 'max'], defaultEffort: 'high' },
      { id: 'deepseek-v4-pro', note: 'DeepSeek-V4-Pro · 强', efforts: ['none', 'low', 'high', 'max'], defaultEffort: 'high' },
    ],
    defaultModel: 'deepseek-flash',
    suggested: { reasoning: 'low', decisionReasoning: 'none' },
  },
  local: {
    id: 'local',
    label: '本地 LLM',
    dialect: 'local',
    baseUrl: 'http://127.0.0.1:8001/v1',
    fromEnv: true,
    needsKey: false,
    models: [],
    defaultModel: '',
    suggested: { reasoning: 'medium', decisionReasoning: 'low' },
  },
};

export const PROVIDER_IDS: ProviderId[] = ['openai', 'deepseek', 'local'];

/**
 * A local server is only reachable when the page itself is served locally:
 * from a public origin (e.g. GitHub Pages) the browser's CORS / private-network
 * checks reject calls to 127.0.0.1. Outside a browser (tests, scripts) it is allowed.
 */
export function localReachable(): boolean {
  const host = (globalThis as { location?: { hostname: string } }).location?.hostname;
  return host === undefined || host === 'localhost' || host === '127.0.0.1' || host === '[::1]';
}

/** Providers that can be chosen from this page. */
export function providerAvailable(id: ProviderId): boolean {
  return id !== 'local' || localReachable();
}

/** Levels offered for a local server (it may or may not honour them). */
export const LOCAL_EFFORTS = ['none', 'low', 'medium', 'high'];

/** Reasoning values valid for this provider + model ([] = not a reasoning model). */
export function effortsFor(provider: ProviderId, model: string): string[] {
  if (provider === 'local') return LOCAL_EFFORTS;
  return PROVIDERS[provider].models.find((m) => m.id === model)?.efforts ?? [];
}

/** Keep a chosen level valid when the model changes: same value, else the nearest weaker one. */
export function clampEffort(want: string, efforts: string[]): string {
  if (!efforts.length || efforts.includes(want)) return efforts.length ? want : '';
  const ladder = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
  const rank = ladder.indexOf(want);
  const weaker = efforts.filter((e) => ladder.indexOf(e) <= rank);
  return weaker.length ? weaker[weaker.length - 1] : efforts[0];
}

/**
 * Extra output budget for thinking: official APIs count reasoning tokens
 * against the output cap, so a small cap would end in an empty answer.
 */
export function thinkingBudget(effort: string): number {
  return { none: 0, minimal: 1000, low: 3000, medium: 8000, high: 16000, xhigh: 32000, max: 32000 }[effort] ?? 8000;
}

export interface ChatBodyInput {
  dialect: Dialect;
  model: string;
  messages: unknown[];
  maxTokens: number;
  temperature: number;
  /** '' when the model has no reasoning control. */
  effort: string;
}

/** The request body in each provider's official format. */
export function buildChatBody(i: ChatBodyInput): Record<string, unknown> {
  const body: Record<string, unknown> = { model: i.model, messages: i.messages, stream: false };
  const thinking = i.effort !== '' && i.effort !== 'none';
  switch (i.dialect) {
    case 'openai':
      body.max_completion_tokens = i.maxTokens + (thinking ? thinkingBudget(i.effort) : 0);
      if (i.effort) body.reasoning_effort = i.effort;
      // reasoning models reject sampling parameters; only non-reasoning models get a temperature
      else body.temperature = i.temperature;
      break;
    case 'deepseek':
      body.max_tokens = i.maxTokens + (thinking ? thinkingBudget(i.effort) : 0);
      body.thinking = { type: thinking ? 'enabled' : 'disabled' };
      if (thinking) body.reasoning_effort = i.effort;
      else body.temperature = i.temperature;
      break;
    case 'local':
      // local servers also count thinking against max_tokens (Qwen3 at `low` easily
      // thinks past a 1000-token vote cap, leaving an empty answer)
      body.max_tokens = i.maxTokens + (thinking ? thinkingBudget(i.effort) : 0);
      body.temperature = i.temperature;
      if (thinking) body.reasoning_effort = i.effort;
      // Qwen-style servers: ask the chat template to skip thinking entirely (ignored elsewhere)
      else body.chat_template_kwargs = { enable_thinking: false };
      break;
  }
  return body;
}
