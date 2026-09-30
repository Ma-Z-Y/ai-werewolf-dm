import { describe, expect, it } from 'vitest';
import { MockAgent, type MockSnapshot } from '../ai/mockAgent';
import { Game, GameAborted, ReplayMismatch } from './game';
import type { Agent, Decision } from './types';

const names = Array.from({ length: 12 }, (_, i) => `P${i + 1}`);
const transcript = (g: Game) => g.events.map((e) => `${e.day}|${e.phase}|${e.type}|${e.speaker ?? ''}|${e.text}`);

/** Play `seed` with mock agents, stopping (like a save) once `stopAt` answers were given. */
async function playUntil(seed: number, stopAt: number) {
  const mocks = names.map((_, i) => new MockAgent(seed * 31 + i));
  let saved: { journal: Decision[]; agents: MockSnapshot[] } | null = null;
  const g = new Game({ names, humanSeat: -1, seed, wolfChatRounds: 2 });
  const stop = () => {
    if (g.journal.length < stopAt || saved) return;
    saved = { journal: g.journal.slice(), agents: mocks.map((m) => m.snapshot()) };
    g.abort();
  };
  g.setAgents(
    mocks.map((m): Agent => ({
      speak: (r, v) => (stop(), m.speak(r, v)),
      choose: (r, v) => (stop(), m.choose(r, v)),
    })),
  );
  const winner = await g.run().catch((e) => {
    if (!(e instanceof GameAborted)) throw e;
    return undefined;
  });
  return { g, winner, saved: saved as { journal: Decision[]; agents: MockSnapshot[] } | null };
}

describe('save games (seed + journal replay)', () => {
  it('a resumed game continues exactly like the uninterrupted one', async () => {
    for (let seed = 1; seed <= 40; seed++) {
      const full = await playUntil(seed, Infinity);
      const total = full.g.journal.length;
      for (const stopAt of [0, 1, Math.floor(total / 3), Math.floor(total / 2), total - 1]) {
        const { saved } = await playUntil(seed, stopAt);
        expect(saved).not.toBeNull();
        const mocks = names.map((_, i) => new MockAgent(seed * 31 + i));
        saved!.agents.forEach((s, i) => mocks[i].restore(s));
        let restoredAt = -1;
        const g = new Game(
          { names, humanSeat: -1, seed, wolfChatRounds: 2, replay: saved!.journal },
          { restored: () => (restoredAt = g.journal.length) },
        );
        g.setAgents(mocks);
        expect(await g.run()).toBe(full.winner);
        // the host hears about it at the first live step, before any new answer
        expect(restoredAt).toBe(stopAt);
        expect(transcript(g)).toEqual(transcript(full.g));
        expect(g.journal).toEqual(full.g.journal);
      }
    }
  });

  it('replays without asking agents or waiting for the scene', async () => {
    const full = await playUntil(7, Infinity);
    let asked = 0;
    let cues = 0;
    let live = false;
    // live pacing only resumes after the last recorded answer (the few steps that end the game)
    const g = new Game(
      { names, humanSeat: -1, seed: 7, wolfChatRounds: 2, paceMs: 40, replay: full.g.journal },
      { cue: async () => void (live || cues++), beforeSpeech: async () => void (live || cues++), restored: () => (live = true) },
    );
    g.setAgents(names.map(() => ({ speak: async () => (asked++, 'x'), choose: async () => (asked++, null) })));
    const t0 = performance.now();
    await g.run();
    expect(performance.now() - t0).toBeLessThan(1000);
    expect(asked).toBe(0);
    expect(cues).toBe(0); // no scene cue or held speech while still replaying
    expect(transcript(g)).toEqual(transcript(full.g));
  });

  it('rejects a journal that no longer fits the rules', async () => {
    const g = new Game({ names, humanSeat: -1, seed: 3, replay: [{ s: 'not a target' }] });
    g.setAgents(names.map(() => new MockAgent(1)));
    await expect(g.run()).rejects.toBeInstanceOf(ReplayMismatch);
  });

  it('pausing holds the GM, including an answer that arrives meanwhile', async () => {
    let answer!: (v: number | null) => void;
    const g = new Game({ names, humanSeat: -1, seed: 5 });
    const mocks = names.map((_, i) => new MockAgent(i));
    let calls = 0;
    g.setAgents(
      mocks.map((m): Agent => ({
        speak: (r, v) => m.speak(r, v),
        choose: (r, v) => (++calls === 1 ? new Promise((res) => (answer = res)) : m.choose(r, v)),
      })),
    );
    const run = g.run();
    await new Promise((r) => setTimeout(r, 0));
    expect(calls).toBe(1);
    g.pause();
    answer(g.viewFor(g.state.actor!).players.find((p) => p.id !== g.state.actor)!.id);
    await new Promise((r) => setTimeout(r, 20));
    expect(calls).toBe(1); // held
    expect(g.journal.length).toBe(1); // but the answer is already journaled for a save
    g.resume();
    expect(await run).toMatch(/good|wolf/);
    expect(calls).toBeGreaterThan(1);
  });
});
