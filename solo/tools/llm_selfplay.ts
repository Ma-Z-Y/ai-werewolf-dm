/**
 * Headless all-AI game against a real OpenAI-compatible endpoint, to check
 * prompt quality and per-phase latency without the browser.
 * Usage: npx tsx tools/llm_selfplay.ts [seed] [wolfChatRounds] [days]
 * `days` stops the game once that day's speeches are done (before the vote).
 */
import { LLMAgent } from '../src/ai/llmAgent';
import { PERSONAS } from '../src/personas';
import { existsSync } from 'node:fs';
import { OpenAICompatibleProvider, SerialQueue } from '../src/ai/provider';
import { envProvider } from '../src/config';
import { Game, GameAborted } from '../src/game/game';
import { ROLE_NAME, seat } from '../src/game/types';

const seed = Number(process.argv[2] ?? 1);
const rounds = Number(process.argv[3] ?? 5);
const days = Number(process.argv[4] ?? 0);
// same connection settings as the game: .env (falls back to the committed .env.example)
process.loadEnvFile(existsSync('.env') ? '.env' : '.env.example');
const env = envProvider();
if (env.missing.length) throw new Error(`.env 缺少 ${env.missing.join(', ')}`);
// node talks to the server directly (no browser CORS), with the key from .env
const provider = new OpenAICompatibleProvider({ ...env.config, useProxy: false });
console.log(`# ${env.config.model} @ ${env.config.baseUrl} (reasoning ${env.config.reasoning} / decisions ${env.config.decisionReasoning})`);
const queue = new SerialQueue();
const stats: Record<string, number[]> = {};
let failures = 0;

const game = new Game(
  { names: PERSONAS.map((p) => p.name), humanSeat: -1, seed, wolfChatRounds: rounds },
  {
    onEvent: (e) => {
      if (e.type === 'system') return;
      const who = e.speaker !== undefined ? `${seat(e.speaker)}(${ROLE_NAME[game.players[e.speaker].role]}) ` : '';
      const vis = e.visibility.kind === 'private' ? `[→${e.visibility.to.map((i) => i + 1).join(',')}] ` : '';
      console.log(`D${e.day} ${e.type.padEnd(8)} ${vis}${who}${e.text}`);
      if (days && e.day >= days && e.type === 'gm' && e.text.startsWith('发言结束')) game.abort();
    },
  },
);
game.setAgents(
  PERSONAS.map((p, i) =>
    new LLMAgent(i, p, provider, queue, {
      onCall: (c) => {
        if (!c.ok) {
          failures++;
          console.log(`   !! call failed ${seat(c.player)} ${c.kind}: ${c.error}`);
        } else (stats[c.kind] ??= []).push(c.ms);
      },
    }),
  ),
);
const t0 = Date.now();
const winner = await game.run().catch((e) => {
  if (e instanceof GameAborted) return `stopped after day ${days}`;
  throw e;
});
console.log(`\nWINNER: ${winner}  total ${(Date.now() - t0) / 1000}s  failures=${failures}`);
for (const [k, v] of Object.entries(stats)) {
  const avg = v.reduce((a, b) => a + b, 0) / v.length;
  console.log(`${k.padEnd(12)} n=${String(v.length).padStart(3)} avg=${(avg / 1000).toFixed(1)}s max=${(Math.max(...v) / 1000).toFixed(1)}s`);
}
