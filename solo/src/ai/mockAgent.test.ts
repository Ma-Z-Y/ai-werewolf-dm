import { describe, expect, it } from 'vitest';
import type { PlayerView, Role } from '../game/types';
import { MockAgent } from './mockAgent';

function playerView(role: Role, known: PlayerView['known'], events: PlayerView['events'] = []): PlayerView {
  return {
    self: { id: 0, name: 'P1', role, alive: true, isHuman: false },
    day: 1,
    phase: 'discussion',
    players: Array.from({ length: 12 }, (_, id) => ({ id, name: `P${id + 1}`, alive: true })),
    known,
    events,
    sheriff: null,
  };
}

describe('offline rule AI', () => {
  it('follows a lone seer claim’s public 查杀 when voting', async () => {
    const view = playerView('villager', { 0: 'villager' }, [
      {
        seq: 0,
        day: 1,
        phase: 'discussion',
        type: 'speech',
        speaker: 5,
        text: '我是预言家，昨晚查杀 4号。',
        visibility: { kind: 'public' },
      },
    ]);
    const target = await new MockAgent(7).choose(
      { kind: 'target', action: 'vote', day: 1, candidates: [2, 3, 4, 5], allowSkip: true, prompt: '投票' },
      view,
    );
    expect(target).toBe(3);
  });

  it('sends the wolf kill at a public seer claimant', async () => {
    const view = playerView('werewolf', { 0: 'werewolf', 1: 'werewolf' }, [
      {
        seq: 0,
        day: 1,
        phase: 'discussion',
        type: 'speech',
        speaker: 5,
        text: '我是预言家，昨晚金水 2号。',
        visibility: { kind: 'public' },
      },
    ]);
    const target = await new MockAgent(8).choose(
      { kind: 'target', action: 'wolfKill', day: 1, candidates: [2, 3, 4, 5], allowSkip: true, prompt: '刀人' },
      view,
    );
    expect(target).toBe(5);
  });

  it('prefers an unchecked player when the seer has a choice', async () => {
    const view = playerView('seer', { 0: 'seer', 1: 'good' });
    const target = await new MockAgent(9).choose(
      { kind: 'target', action: 'seer', day: 1, candidates: [1, 2, 3], allowSkip: false, prompt: '查验' },
      view,
    );
    expect([2, 3]).toContain(target);
  });
});
