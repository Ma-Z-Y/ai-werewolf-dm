import { LOCAL_EFFORTS } from './ai/catalog';
import type { ProviderConfig } from './ai/provider';

/**
 * LLM connection settings come from `.env` (see `.env.example`), for both the
 * game and the tools/tests:
 *
 * - browser: Vite injects the non-secret values as `__LLM_ENV__` at build time;
 *   `LLM_API_KEY` never reaches the browser — the dev-server proxy adds it.
 * - node (tools/*): `process.loadEnvFile('.env')`, then `process.env`.
 */
export interface LlmEnv {
  LLM_BASE_URL?: string;
  LLM_API_KEY?: string;
  /** Comma-separated model ids; the first one is the default. */
  LLM_MODELS?: string;
  /** Older `.env` files: a single model id (used when LLM_MODELS is empty). */
  LLM_MODEL?: string;
  LLM_REASONING?: string;
  LLM_DECISION_REASONING?: string;
  LLM_USE_PROXY?: string;
  LLM_TIMEOUT_MS?: string;
  /** Browser only: '1' when the dev server holds an API key for the proxy. */
  LLM_HAS_KEY?: string;
}

export interface EnvProvider {
  /** `model` is the default: the first entry of `models`. */
  config: ProviderConfig;
  /** The only models the game offers for the local server. */
  models: string[];
  /** The proxy injects `LLM_API_KEY`; the browser does not know the key itself. */
  keyOnServer: boolean;
  /** Required keys missing from `.env`. */
  missing: string[];
}

function level(v: string | undefined, fallback: string): string {
  const s = (v ?? '').trim().toLowerCase();
  return LOCAL_EFFORTS.includes(s) ? s : fallback;
}

/** `a, b,,a` → `['a', 'b']` */
export function parseModels(v: string | undefined): string[] {
  return [...new Set((v ?? '').split(',').map((m) => m.trim()).filter(Boolean))];
}

export function providerFromEnv(env: LlmEnv): EnvProvider {
  const models = parseModels(env.LLM_MODELS?.trim() ? env.LLM_MODELS : env.LLM_MODEL);
  const missing = [...(env.LLM_BASE_URL?.trim() ? [] : ['LLM_BASE_URL']), ...(models.length ? [] : ['LLM_MODELS'])];
  const timeout = Number(env.LLM_TIMEOUT_MS);
  return {
    // `.env` describes the local server (the official APIs are presets, see catalog.ts)
    config: {
      provider: 'local',
      baseUrl: (env.LLM_BASE_URL ?? '').trim().replace(/\/+$/, ''),
      apiKey: (env.LLM_API_KEY ?? '').trim(),
      model: models[0] ?? '',
      reasoning: level(env.LLM_REASONING, 'medium'),
      decisionReasoning: level(env.LLM_DECISION_REASONING, 'low'),
      useProxy: !/^(0|false|no|off)$/i.test((env.LLM_USE_PROXY ?? '').trim()),
      timeoutMs: Number.isFinite(timeout) && timeout > 0 ? timeout : 180_000,
    },
    models,
    keyOnServer: env.LLM_HAS_KEY === '1',
    missing,
  };
}

declare const __LLM_ENV__: LlmEnv | undefined;

/** Settings from `.env` for the current runtime (browser build or node). */
export function envProvider(): EnvProvider {
  if (typeof __LLM_ENV__ !== 'undefined') return providerFromEnv(__LLM_ENV__);
  const proc = (globalThis as { process?: { env: Record<string, string | undefined> } }).process;
  return providerFromEnv(proc?.env ?? {});
}
