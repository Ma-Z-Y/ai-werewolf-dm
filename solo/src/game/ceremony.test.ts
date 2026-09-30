import { describe, expect, it } from 'vitest';
import { MockAgent } from '../ai/mockAgent';
import { LLMAgent } from '../ai/llmAgent';
import { godTranscript, parseAwardVote } from '../ai/review';
import type { ChatMessage, OpenAICompatibleProvider } from '../ai/provider';
import { SerialQueue } from '../ai/provider';
import { PERSONAS } from '../personas';
import { tallyAwards, validVote, type ReviewContext } from './ceremony';
import { Game } from './game';

const names = Array.from({ length: 12 }, (_, i) => `P${i + 1}`);

async function finishedGame(seed: number) {
  const g = new Game({ names, humanSeat: -1, seed, wolfChatRounds: 2 });
  g.setAgents(names.map((_, i) => new MockAgent(seed * 31 + i)));
  await g.run();
  return g;
}

function context(g: Game, self: number): ReviewContext {
  const players = g.players.map((p) => ({ ...p }));
  const causes = g.deathCauses();
  const data = { players, events: g.events, winner: g.state.winner, causes };
  return { self, players, winner: g.state.winner, transcript: godTranscript(data), reviews: [], events: g.events, causes };
}

describe('颁奖典礼 votes', () => {
  it('everyone tied for the most votes shares the award', () => {
    const t = tallyAwards([
      { best: 1, worst: 2 },
      { best: 3, worst: 2 },
      null,
      { best: 1, worst: 5 },
      { best: 3, worst: 5 },
    ]);
    expect(t.best).toEqual([1, 3]);
    expect(t.worst).toEqual([2, 5]);
    expect(t.bestVotes.get(1)).toEqual([0, 3]);
  });

  it('a lone top vote wins alone; nobody voting means no award', () => {
    expect(tallyAwards([{ best: 4, worst: 2 }, { best: 4, worst: 3 }, { best: 0, worst: 2 }]).best).toEqual([4]);
    expect(tallyAwards([null, null])).toMatchObject({ best: [], worst: [] });
  });

  it('never yourself, never the same seat for both', () => {
    expect(validVote({ best: 1, worst: 2 }, 0, 12)).toBe(true);
    expect(validVote({ best: 0, worst: 2 }, 0, 12)).toBe(false);
    expect(validVote({ best: 2, worst: 2 }, 0, 12)).toBe(false);
    expect(validVote({ best: 12, worst: 2 }, 0, 12)).toBe(false);
  });

  it('parses the model ballot (1-based seats in, 0-based out)', () => {
    expect(parseAwardVote('{"best": 3, "worst": 7, "reason": "3号查杀准"}', 0, 12)).toEqual({ best: 2, worst: 6, reason: '3号查杀准' });
    expect(parseAwardVote('```json\n{"best": "5号", "worst": "12号"}\n```', 0, 12)).toMatchObject({ best: 4, worst: 11 });
    expect(parseAwardVote('best: 2, worst: 9', 0, 12)).toMatchObject({ best: 1, worst: 8 });
    expect(parseAwardVote('{"best": 1, "worst": 4}', 0, 12)).toBeNull(); // voted for yourself
    expect(parseAwardVote('{"best": 4, "worst": 4}', 0, 12)).toBeNull();
    expect(parseAwardVote('不知道', 0, 12)).toBeNull();
  });
});

describe('颁奖典礼 god view', () => {
  it('hands everyone the whole game: roles, causes, private lines, wolf chat and the AIs’ reasons', async () => {
    const g = await finishedGame(5);
    const text = godTranscript({ players: g.players, events: g.events, winner: g.state.winner, causes: g.deathCauses() }, [['第1夜查验 3号（发言可疑）']]);
    expect(text).toContain('【身份公开】');
    expect(text).toContain(g.state.winner === 'good' ? '好人阵营获胜' : '狼人阵营获胜');
    expect(text).toContain('狼队频道');
    expect(text).toContain('私密→');
    expect(text).toContain('查验结果');
    expect(text).toContain('第1夜查验 3号（发言可疑）');
    // every private line is in there
    for (const e of g.events.filter((e) => e.type === 'private')) expect(text).toContain(e.text);
  });

  it('the rule AI reviews and casts a valid ballot for every seat', async () => {
    const g = await finishedGame(8);
    for (let self = 0; self < 12; self++) {
      const a = new MockAgent(self);
      const ctx = context(g, self);
      const r = await a.review(ctx);
      expect(typeof r === 'string' ? r : r.text).toMatch(/\d+号/);
      expect(validVote(await a.awardVote(ctx), self, 12)).toBe(true);
    }
  });

  it('the model is told it is the post-game review, sees the god-view record, and its ballot is used', async () => {
    const g = await finishedGame(9);
    const log: ChatMessage[][] = [];
    const provider = {
      config: { decisionReasoning: 'low' },
      chat: async (msgs: ChatMessage[]) => {
        log.push(msgs);
        const json = msgs[msgs.length - 1].content.includes('JSON');
        return { content: json ? '{"best": 2, "worst": 3, "reason": "测试"}' : '2号打得最好，3号最差。', ms: 1 };
      },
    } as unknown as OpenAICompatibleProvider;
    const a = new LLMAgent(0, PERSONAS[0], provider, new SerialQueue());
    const ctx = { ...context(g, 0), reviews: [{ speaker: 11, text: '我先说两句' }] };
    expect(await a.review(ctx)).toBe('2号打得最好，3号最差。');
    expect(await a.awardVote(ctx)).toMatchObject({ best: 1, worst: 2 });
    const [system, user] = log[0];
    expect(system.content).toContain('赛后测评');
    expect(user.content).toContain('现在是赛后测评环节');
    expect(user.content).toContain('【整局完整记录 · 上帝视角】');
    expect(user.content).toContain('我先说两句');
  });
});
