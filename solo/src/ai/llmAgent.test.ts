import { describe, expect, it } from 'vitest';
import { Game } from '../game/game';
import { ROLE_NAME, type Role } from '../game/types';
import { PERSONAS } from '../personas';
import { LLMAgent } from './llmAgent';
import type { ChatMessage, OpenAICompatibleProvider } from './provider';
import { SerialQueue } from './provider';
import { RULEBOOK } from './prompts';

/** A provider that records every request and answers something parseable. */
function fakeProvider(log: ChatMessage[][], stop: () => void) {
  return {
    config: { decisionReasoning: 'low' },
    chat: async (msgs: ChatMessage[]) => {
      stop();
      log.push(msgs);
      const task = msgs[msgs.length - 1].content;
      const json = task.includes('JSON');
      return { content: json ? '{"target": 0, "reason": "测试"}' : '我是好人，先听听大家。', ms: 1 };
    },
  } as unknown as OpenAICompatibleProvider;
}

describe('rulebook in every model call', () => {
  it('covers every role, the whole day / night flow and the win condition', () => {
    for (const r of Object.values(ROLE_NAME)) expect(RULEBOOK).toContain(`### ${r}`);
    for (const k of ['夜晚流程', '警长竞选', '退水', '警徽流', '放逐投票', 'PK', '自爆', '屠边', '同守同救', '遗言', '谁知道什么', '用规则推理']) {
      expect(RULEBOOK).toContain(k);
    }
  });

  it('puts the same rulebook first in the system prompt of speeches, wolf chat and decisions alike', async () => {
    const log: ChatMessage[][] = [];
    const g = new Game({ names: PERSONAS.map((p) => p.name), humanSeat: -1, seed: 2, wolfChatRounds: 1 });
    // one night and the first day's election, speeches and vote
    const provider = fakeProvider(log, () => g.state.day > 1 && g.abort());
    const queue = new SerialQueue();
    g.setAgents(PERSONAS.map((p, i) => new LLMAgent(i, p, provider, queue)));
    await g.run().catch(() => {});
    const kinds = new Set<string>();
    for (const msgs of log) {
      const system = msgs[0];
      expect(system.role).toBe('system');
      expect(system.content.startsWith(RULEBOOK)).toBe(true);
      const task = msgs[msgs.length - 1].content;
      kinds.add(task.includes('狼队秘密频道') ? 'wolfChat' : task.includes('JSON') ? 'decision' : 'speech');
    }
    expect([...kinds].sort()).toEqual(['decision', 'speech', 'wolfChat']);
    // after the rulebook: who this player is (differs per player)
    const roles = new Set(log.map((m) => m[0].content.slice(RULEBOOK.length).match(/你的真实身份：(\S+?)（/)?.[1]));
    expect([...roles].every((r) => Object.values(ROLE_NAME).includes(r as (typeof ROLE_NAME)[Role]))).toBe(true);
    expect(roles.size).toBeGreaterThan(3);
  });
});
