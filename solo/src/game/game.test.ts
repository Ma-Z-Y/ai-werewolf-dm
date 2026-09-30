import { describe, expect, it } from 'vitest';
import { MockAgent } from '../ai/mockAgent';
import { Game, circularOrder, nextSeat, type SceneCue } from './game';
import { EXPLODE_CHOICE, GOD_ROLES, isWolf, type Agent, type GameEvent, type PlayerView, type Role, type SpeechRequest, type TargetRequest, type WolfChatRequest } from './types';

const names = Array.from({ length: 12 }, (_, i) => `P${i + 1}`);

function simulate(seed: number, wrap?: (a: Agent, id: number) => Agent) {
  const g = new Game({ names, humanSeat: -1, seed, wolfChatRounds: 2 });
  g.setAgents(names.map((_, i) => (wrap ? wrap(new MockAgent(seed * 31 + i), i) : new MockAgent(seed * 31 + i))));
  return g;
}

describe('Game engine', () => {
  it('deals the standard 12-player board', () => {
    const g = simulate(1);
    const counts = g.players.reduce<Record<string, number>>((m, p) => ((m[p.role] = (m[p.role] ?? 0) + 1), m), {});
    expect(counts).toEqual({ wolfKing: 1, werewolf: 3, villager: 4, seer: 1, witch: 1, hunter: 1, guard: 1 });
  });

  it('forces the human role when requested', () => {
    for (let s = 0; s < 20; s++) {
      const g = new Game({ names, humanSeat: 3, humanRole: 'witch', seed: s });
      expect(g.players[3].role).toBe('witch');
      expect(g.players[3].isHuman).toBe(true);
    }
  });

  it('wolves win by 屠边 (every god or every villager out); good only once every wolf is out', () => {
    const kill = (g: Game, n: number, pred: (r: string) => boolean) => {
      for (const p of g.players.filter((p) => p.alive && pred(p.role)).slice(0, n)) p.alive = false;
    };
    const g = simulate(1);
    expect(g.checkWinner()).toBeNull();
    kill(g, 3, (r) => GOD_ROLES.includes(r as never));
    kill(g, 3, (r) => r === 'villager');
    expect(g.checkWinner()).toBeNull(); // one god, one villager left: plays on
    kill(g, 1, (r) => GOD_ROLES.includes(r as never));
    expect(g.checkWinner()).toBe('wolf'); // every god out

    const h = simulate(2);
    kill(h, 4, (r) => r === 'villager');
    expect(h.checkWinner()).toBe('wolf'); // every villager out, gods and all wolves still up

    const k = simulate(3);
    kill(k, 3, (r) => r === 'werewolf');
    kill(k, 3, (r) => r === 'villager');
    expect(k.checkWinner()).toBeNull(); // the 狼王 is still up: good has to finish the job
    kill(k, 1, (r) => r === 'wolfKing');
    expect(k.checkWinner()).toBe('good');
    // both at once (same night) goes to the wolves
    kill(k, 1, (r) => r === 'villager');
    expect(k.checkWinner()).toBe('wolf');
  });

  it('runs 300 seeded games to a winner while respecting rule invariants', async () => {
    const wins = { good: 0, wolf: 0 };
    for (let seed = 1; seed <= 300; seed++) {
      const requests: { id: number; req: TargetRequest; view: PlayerView }[] = [];
      const g = simulate(seed, (a, id) => ({
        speak: (r, v) => a.speak(r, v),
        choose: (r, v) => {
          requests.push({ id, req: r, view: v });
          return a.choose(r, v);
        },
      }));
      const w = await g.run();
      expect(w === 'good' || w === 'wolf').toBe(true);
      wins[w!]++;
      expect(g.checkWinner()).toBe(w);

      const ran = new Set<number>();
      const pkSpeakers = (day: number) => g.events.filter((e) => e.day === day && e.speechKind === 'defense').map((e) => e.speaker);
      for (const { id, req, view } of requests) {
        // nobody targets themselves with a skill (the guard may guard themselves)
        if (['seer', 'witchPoison', 'hunterShot', 'wolfKingShot', 'badge'].includes(req.action)) {
          expect(req.candidates).not.toContain(id);
        }
        if (req.action === 'guard' && view.guard?.lastGuarded != null) {
          expect(req.candidates).not.toContain(view.guard.lastGuarded);
        }
        if (req.action === 'witchSave') {
          expect(view.witch?.hasAntidote).toBe(true);
          expect(req.candidates).toHaveLength(1); // the witch only learns the knife
          if (req.candidates[0] === id) expect(req.day).toBe(1); // self-save on the first night only
        }
        if (req.action === 'witchPoison') expect(view.witch?.hasPoison).toBe(true);
        if (req.action === 'hunterShot') expect(view.hunter?.hasShot).toBe(false);
        if (req.action === 'wolfKingShot') expect(view.wolfKing?.hasShot).toBe(false);
        // the dead only act to shoot (hunter / 狼王) or hand on the badge (sheriff)
        if (['hunterShot', 'wolfKingShot', 'badge'].includes(req.action)) expect(view.self.alive).toBe(false);
        else expect(view.self.alive).toBe(true);
        if (req.action === 'badge') expect(view.sheriff).toBe(id);
        if (req.action === 'runForSheriff') {
          expect(req.day).toBe(1);
          expect(req.candidates).toEqual([id]);
        }
        if (req.action === 'withdraw') ran.add(id);
        // 警上 / 退水 players never vote for the sheriff; PK players never vote in the PK
        if (req.action === 'sheriffVote' || req.action === 'sheriffRevote') expect(ran.has(id)).toBe(false);
        if (req.action === 'revote') expect(pkSpeakers(req.day)).not.toContain(id);
        if (req.action === 'speakOrder') expect(view.sheriff).toBe(id);
      }
      // the witch never uses both potions on one night
      const witch = g.players.find((p) => p.role === 'witch')!.id;
      for (let d = 1; d <= g.state.day; d++) {
        const mine = g.events.filter((e) => e.day === d && e.phase === 'night' && e.visibility.kind === 'private' && e.visibility.to.includes(witch) && e.data);
        expect(mine.some((e) => 'saved' in e.data!) && mine.some((e) => 'poison' in e.data!)).toBe(false);
      }
      // day 1: the election comes before the night's news
      const news = g.events.findIndex((e) => e.day === 1 && Array.isArray(e.data?.nightDeaths));
      const lastElection = g.events.map((e) => e.phase).lastIndexOf('election');
      if (news >= 0 && g.events.some((e) => e.day === 1 && e.phase === 'election')) {
        expect(g.events[lastElection].day === 1 ? lastElection : -1).toBeLessThan(news);
      }
      // wolf chat never leaks to non-wolves
      for (const e of g.events.filter((e) => e.type === 'wolfChat')) {
        expect(e.visibility.kind).toBe('private');
        if (e.visibility.kind === 'private') {
          for (const id of e.visibility.to) expect(isWolf(g.players[id].role)).toBe(true);
        }
      }
    }
    expect(wins.good + wins.wolf).toBe(300);
  });

  it('every day speech waits for the host before it is heard', async () => {
    const released: number[] = [];
    const heard: number[] = [];
    const g = new Game(
      { names, humanSeat: -1, seed: 5, wolfChatRounds: 2 },
      {
        beforeSpeech: async (id) => void released.push(id),
        onEvent: (e) => {
          if (e.type !== 'speech') return;
          expect(released.at(-1)).toBe(e.speaker); // released right before it lands
          heard.push(e.speaker!);
        },
      },
    );
    g.setAgents(names.map((_, i) => new MockAgent(i)));
    await g.run();
    expect(heard.length).toBeGreaterThan(0);
    expect(released).toHaveLength(heard.length);
  });

  it('an exile is only sent off after their last words, before the night starts', async () => {
    let confirmed = 0;
    for (let seed = 1; seed <= 20; seed++) {
      const log: string[] = [];
      const g = new Game(
        { names, humanSeat: -1, seed, wolfChatRounds: 1 },
        {
          confirmExile: async (id) => void log.push(`confirm:${id}`),
          onEvent: (e) => {
            if (e.type === 'speech' && e.speechKind === 'lastWords') log.push(`lastWords:${e.speaker}`);
            if (e.type === 'death' && e.data?.cause === 'vote') log.push(`exile:${e.data.id}`);
          },
          onState: (s) => {
            if (s.phase === 'night' && log.at(-1) !== 'night') log.push('night');
          },
        },
      );
      g.setAgents(names.map((_, i) => new MockAgent(i + seed * 100)));
      await g.run();
      log.forEach((x, i) => {
        if (!x.startsWith('confirm:')) return;
        confirmed++;
        const id = x.slice(8);
        expect(log.slice(0, i)).toContain(`lastWords:${id}`);
        expect(log[i + 1] ?? 'night').toBe('night');
      });
      // every exile that didn't end the game got confirmed
      const exiles = log.filter((x) => x.startsWith('exile:')).length;
      expect(log.filter((x) => x.startsWith('confirm:')).length).toBeGreaterThanOrEqual(exiles - 1);
    }
    expect(confirmed).toBeGreaterThan(0);
  });

  it('a wolf self-destructing ends the day: rest of the speeches and the vote are skipped', async () => {
    for (let seed = 1; seed <= 20; seed++) {
      const g = new Game({ names, humanSeat: -1, seed, wolfChatRounds: 1 });
      const speakers: number[] = [];
      let exploder = -1;
      let canExplodeSeen = false;
      g.setAgents(
        names.map((_, id): Agent => ({
          speak: async (r) => {
            if (r.kind !== 'speech') return 'pass';
            if (r.canExplode) {
              canExplodeSeen = true;
              expect(isWolf(g.players[id].role)).toBe(true);
            }
            if (r.purpose !== 'discussion') return '过。';
            if (r.day === 1) speakers.push(id);
            // the first wolf to speak on day 1 blows up; good players' explode flags are ignored
            if (r.day === 1 && exploder < 0) {
              if (r.canExplode) {
                exploder = id;
                return { text: '我自爆。', explode: true };
              }
              if (!isWolf(g.players[id].role)) return { text: '我是好人。', explode: true };
            }
            return '过。';
          },
          // nobody runs for sheriff, so day 1 goes straight to the speeches
          choose: async (r) => (r.action === 'runForSheriff' ? null : r.candidates[0] ?? null),
        })),
      );
      await g.run();
      expect(canExplodeSeen).toBe(true);
      expect(exploder).toBeGreaterThanOrEqual(0);
      expect(g.players[exploder].alive).toBe(false);
      // nobody spoke after the wolf, no sheriff speech, no vote on day 1
      expect(speakers[speakers.length - 1]).toBe(exploder);
      const day1 = g.events.filter((e) => e.day === 1);
      const blast = day1.findIndex((e) => e.type === 'death' && e.data?.cause === 'explode');
      expect(day1.some((e) => e.type === 'vote')).toBe(false);
      expect(day1.slice(blast).some((e) => e.type === 'speech')).toBe(false);
      expect(day1.filter((e) => e.type === 'death' && e.data?.cause === 'explode').map((e) => e.data!.id)).toEqual([exploder]);
      // non-wolves' explode flag was ignored: exactly one explosion
      expect(g.journal.filter((d) => 'x' in d).length).toBe(1);
      // straight into the next night
      if (g.state.day > 1) expect(g.events.find((e) => e.day === 2)?.phase).toBe('night');
    }
  });

  it('seer wakes after the witch and is told 好人 / 狼人 at once', async () => {
    for (let seed = 1; seed <= 20; seed++) {
      const g = new Game({ names, humanSeat: -1, seed, wolfChatRounds: 1 });
      const seer = g.players.find((p) => p.role === 'seer')!.id;
      const wolf = g.players.find((p) => p.role === 'werewolf')!.id;
      const agent: Agent = {
        speak: async () => 'pass',
        choose: async (r) => {
          if (r.day > 1) throw new Error('stop');
          if (r.action === 'wolfKill') return seer;
          if (r.action === 'seer') return r.candidates.includes(wolf) ? wolf : r.candidates[0];
          return null;
        },
      };
      g.setAgents(names.map(() => agent));
      await g.run().catch(() => {});
      const witchStep = g.events.findIndex((e) => e.text.startsWith('女巫请睁眼'));
      const result = g.events.findIndex((e) => e.data?.check !== undefined);
      const hunterStep = g.events.findIndex((e) => e.text.startsWith('猎人请睁眼'));
      expect(witchStep).toBeLessThan(result);
      expect(result).toBeLessThan(hunterStep);
      expect(g.state.seerChecks).toEqual({ [wolf]: 'wolf' });
      expect(g.viewFor(seer).known[wolf]).toBe('wolf');
      expect(g.events[result].text).toContain('【狼人】');
    }
  });

  it('wolf chat always lets the human speak last each round and ends once everyone passes', async () => {
    const g = new Game({ names, humanSeat: 0, humanRole: 'werewolf', seed: 5, wolfChatRounds: 3 });
    const humanRounds: number[] = [];
    const cues: string[] = [];
    const agent: Agent = {
      speak: async (r) => (r.kind === 'wolfChat' && r.round === 1 ? '刀 12 号' : 'pass'),
      choose: async (r) => {
        if (r.day > 1) throw new Error('stop');
        return r.allowSkip ? null : r.candidates[r.candidates.length - 1];
      },
    };
    const human: Agent = {
      speak: async (r) => {
        if (r.kind === 'wolfChat') {
          humanRounds.push(r.round);
          if (r.round > 1) expect(r.othersPassed).toBe(true);
        }
        return '好';
      },
      choose: agent.choose,
    };
    const g2 = new Game({ names, humanSeat: 0, humanRole: 'werewolf', seed: 5, wolfChatRounds: 3 }, { cue: async (c) => void (typeof c === 'string' && cues.push(c)) });
    for (const game of [g, g2]) game.setAgents(names.map((_, i) => (i === 0 ? human : agent)));
    await g2.run().catch(() => {});
    // AI wolves pass from round 2, but the human still gets the last word every round;
    // the human keeps adding something ("好"), so all 3 rounds are played
    expect(humanRounds).toEqual([1, 2, 3]);
    expect(cues.slice(0, 4)).toEqual(['nightfall', 'wolvesOut', 'wolvesIn', 'dawn']);
  });

  it('wolf chat ends early when the human also passes', async () => {
    const humanRounds: number[] = [];
    const agent: Agent = {
      speak: async (r) => (r.kind === 'wolfChat' && r.round === 1 ? '刀 12 号' : 'pass'),
      choose: async (r) => {
        if (r.day > 1) throw new Error('stop');
        return r.allowSkip ? null : r.candidates[r.candidates.length - 1];
      },
    };
    const human: Agent = {
      speak: async (r) => {
        if (r.kind === 'wolfChat') humanRounds.push(r.round);
        return r.kind === 'wolfChat' && r.round > 1 ? 'pass' : '同意';
      },
      choose: agent.choose,
    };
    const g = new Game({ names, humanSeat: 0, humanRole: 'werewolf', seed: 5, wolfChatRounds: 3 });
    g.setAgents(names.map((_, i) => (i === 0 ? human : agent)));
    await g.run().catch(() => {});
    expect(humanRounds).toEqual([1, 2]);
  });

  it('an objection from the human in the last wolf-chat round gets one more AI round to answer it', async () => {
    const aiRounds: { round: number; rounds: number; sawObjection: boolean }[] = [];
    const agent: Agent = {
      speak: async (r, v) => {
        if (r.kind === 'wolfChat') aiRounds.push({ round: r.round, rounds: r.rounds, sawObjection: v.events.some((e) => e.text === '我不同意！') });
        return r.kind === 'wolfChat' && r.round === 1 ? '刀 12 号' : 'pass';
      },
      choose: async (r) => {
        if (r.day > 1) throw new Error('stop');
        return r.allowSkip ? null : r.candidates[r.candidates.length - 1];
      },
    };
    const human: Agent = {
      speak: async (r) => (r.kind === 'wolfChat' && r.round === 2 ? '我不同意！' : '同意'),
      choose: agent.choose,
    };
    const g = new Game({ names, humanSeat: 0, humanRole: 'werewolf', seed: 5, wolfChatRounds: 2 });
    g.setAgents(names.map((_, i) => (i === 0 ? human : agent)));
    await g.run().catch(() => {});
    const extra = aiRounds.filter((r) => r.round === 3);
    expect(extra).toHaveLength(3);
    expect(extra.every((r) => r.rounds === 3 && r.sawObjection)).toBe(true);
  });

  it('the human wolf votes last and sees the AI wolves\' picks first', async () => {
    const order: number[] = [];
    let humanPrompt = '';
    const choose = (id: number) => async (r: TargetRequest) => {
      if (r.day > 1) throw new Error('stop');
      if (r.action === 'wolfKill') order.push(id);
      if (id === 0 && r.action === 'wolfKill') humanPrompt = r.prompt;
      return r.allowSkip ? null : r.candidates[r.candidates.length - 1];
    };
    const g = new Game({ names, humanSeat: 0, humanRole: 'werewolf', seed: 5, wolfChatRounds: 1 });
    g.setAgents(names.map((_, i) => ({ speak: async () => 'pass', choose: choose(i) })));
    await g.run().catch(() => {});
    const wolves = g.players.filter((p) => isWolf(p.role)).map((p) => p.id);
    expect(order.at(-1)).toBe(0);
    expect(order.slice().sort((a, b) => a - b)).toEqual(wolves);
    expect(humanPrompt).toMatch(/^队友已投：/);
    expect(g.events.some((e) => e.type === 'wolfChat' && e.text.startsWith('队友已投：'))).toBe(true);
  });

  it('tells each day speaker the round order; the sheriff picks the start and speaks last', async () => {
    const g = new Game({ names, humanSeat: -1, seed: 11, wolfChatRounds: 1 });
    const seen: { id: number; order: number[]; spoken: number[]; purpose: string }[] = [];
    let orderAsked: number[] = [];
    const agent = (id: number): Agent => ({
      speak: async (r) => {
        if (r.kind === 'speech' && r.day === 1 && ['discussion', 'summary'].includes(r.purpose)) seen.push({ id, order: r.order ?? [], spoken: r.spoken ?? [], purpose: r.purpose });
        return '我是好人';
      },
      choose: async (r) => {
        if (r.day > 1) throw new Error('stop');
        if (r.action === 'runForSheriff') return id === 5 ? 5 : null; // 6号 runs alone: sheriff
        if (r.action === 'wolfKill') return null; // 平安夜: 警左 / 警右
        if (r.action === 'speakOrder') {
          orderAsked = r.candidates;
          return r.candidates[1];
        }
        return r.allowSkip ? null : r.candidates[0];
      },
    });
    g.setAgents(names.map((_, i) => agent(i)));
    await g.run().catch(() => {});
    expect(orderAsked).toEqual([6, 4]); // the sheriff's two neighbours: 7号 (顺时针) or 5号 (逆时针)
    const disc = seen.filter((x) => x.purpose === 'discussion');
    expect(disc.map((d) => d.id)).toEqual([4, 3, 2, 1, 0, 11, 10, 9, 8, 7, 6]);
    disc.forEach((x, k) => {
      expect(x.order).toEqual([...disc.map((d) => d.id), 5]); // one shared order, the sheriff last
      expect(x.spoken).toEqual(disc.slice(0, k).map((d) => d.id)); // exactly the earlier speakers
    });
    const summary = seen.find((x) => x.purpose === 'summary')!;
    expect(summary.id).toBe(5);
    expect(summary.spoken).toHaveLength(disc.length);
  });

  it('guard blocks the wolf kill; 同守同救 still dies', async () => {
    for (const save of [false, true]) {
      const g = new Game({ names, humanSeat: -1, seed: 7, wolfChatRounds: 1 });
      const villager = g.players.find((p) => p.role === 'villager')!.id;
      let told = '';
      const agent: Agent = {
        speak: async () => 'pass',
        choose: async (r) => {
          if (r.day > 1) throw new Error('stop');
          if (r.action === 'guard' || r.action === 'wolfKill') return villager;
          if (r.action === 'witchSave') {
            told = r.prompt; // the witch still hears of the kill: she can't tell the guard was there
            return save ? villager : null;
          }
          return null;
        },
      };
      g.setAgents(names.map(() => agent));
      await g.run().catch(() => {});
      expect(told).toContain(`${villager + 1}号`);
      expect(g.players[villager].alive).toBe(save ? false : true);
      expect(g.events.some((e) => e.text.includes('平安夜'))).toBe(!save);
    }
  });

  it('orders speakers circularly', () => {
    expect(circularOrder([0, 2, 5, 9], 5, true)).toEqual([5, 9, 0, 2]);
    expect(circularOrder([0, 2, 5, 9], 5, false)).toEqual([5, 2, 0, 9]);
    expect(nextSeat([0, 2, 5, 9], 5, true)).toBe(9);
    expect(nextSeat([0, 2, 5, 9], 9, true)).toBe(0);
    expect(nextSeat([0, 2, 5, 9], 3, false)).toBe(2);
    expect(nextSeat([0, 2, 5, 9], 0, false)).toBe(9);
  });
});

/** A game where every answer comes from one script (`undefined` = skip if allowed, else the first candidate). */
function scripted(
  seed: number,
  choose: (id: number, r: TargetRequest, v: PlayerView) => number | null | undefined,
  speak: (id: number, r: SpeechRequest | WolfChatRequest, v: PlayerView) => string | { text: string; explode: true } = () => '过。',
  hooks: { cue?: (c: SceneCue) => Promise<void> } = {},
) {
  const g = new Game({ names, humanSeat: -1, seed, wolfChatRounds: 1 }, hooks);
  g.setAgents(
    names.map((_, id): Agent => ({
      speak: async (r, v) => speak(id, r, v),
      choose: async (r, v) => {
        const t = choose(id, r, v);
        return t === undefined ? (r.allowSkip ? null : r.candidates[0]) : t;
      },
    })),
  );
  return g;
}

const idOf = (g: Game, role: Role, nth = 0) => g.players.filter((p) => p.role === role)[nth].id;
const at = (g: Game, pred: (e: GameEvent) => boolean) => g.events.findIndex(pred);
const stopAt = (day: number) => (r: TargetRequest) => {
  if (r.day > day) throw new Error('stop');
};

describe('Standard flow: 警长竞选', () => {
  it('last night\'s dead stand for sheriff, then hear the news: badge, then first-night last words', async () => {
    const probe = new Game({ names, humanSeat: -1, seed: 21 });
    const victim = idOf(probe, 'villager');
    let aliveWhenAsked: boolean | null = null;
    const cues: SceneCue[] = [];
    const g = scripted(
      21,
      (id, r, v) => {
        stopAt(1)(r);
        if (r.action === 'wolfKill') return victim;
        if (r.action === 'runForSheriff') {
          if (id === victim) aliveWhenAsked = v.self.alive;
          return id === victim ? id : null;
        }
        if (r.action === 'badge') return r.candidates[0];
        return undefined;
      },
      undefined,
      { cue: async (c) => void cues.push(c) },
    );
    await g.run().catch(() => {});
    expect(aliveWhenAsked).toBe(true);
    const elected = at(g, (e) => e.text.includes('自动当选警长'));
    const news = at(g, (e) => Array.isArray(e.data?.nightDeaths));
    const handOn = at(g, (e) => e.text.includes('将警徽移交给'));
    const words = at(g, (e) => e.speechKind === 'lastWords' && e.speaker === victim);
    expect(elected).toBeGreaterThan(0);
    expect(elected).toBeLessThan(news);
    expect(news).toBeLessThan(handOn);
    expect(handOn).toBeLessThan(words); // first night: the dead has last words
    expect(g.players[victim].alive).toBe(false);
    expect(g.state.sheriff).not.toBe(victim);
    // the badge flew in on the victim, then on to the heir
    const badges = cues.filter((c): c is Extract<SceneCue, { kind: 'badge' }> => typeof c === 'object' && c.kind === 'badge');
    expect(badges[0]).toEqual({ kind: 'badge', from: null, to: victim });
    expect(badges[1]).toEqual({ kind: 'badge', from: victim, to: g.state.sheriff });
  });

  it('only 警下 players vote; a tie goes to PK and a second tie loses the badge', async () => {
    const [a, b] = [0, 1];
    const g = scripted(4, (id, r) => {
      stopAt(1)(r);
      if (r.action === 'wolfKill') return null;
      if (r.action === 'runForSheriff') return id === a || id === b ? id : null;
      if (r.action === 'sheriffVote' || r.action === 'sheriffRevote') return id === 2 ? a : id === 3 ? b : null;
      return undefined;
    });
    const voters: number[] = [];
    const inner = g as unknown as { askTarget: (id: number, action: string, ...rest: unknown[]) => Promise<unknown> };
    const orig = inner.askTarget.bind(g);
    inner.askTarget = (id, action, ...rest) => {
      if (action === 'sheriffVote') voters.push(id);
      return orig(id, action, ...rest);
    };
    await g.run().catch(() => {});
    expect(voters.sort((x, y) => x - y)).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    expect(g.events.filter((e) => e.speechKind === 'campaignPk').map((e) => e.speaker)).toEqual([a, b]);
    expect(g.events.some((e) => e.text.includes('再次平票，警徽流失'))).toBe(true);
    expect(g.state.sheriff).toBeNull();
  });

  it('withdrawn candidates cannot win; a lone candidate left is elected', async () => {
    const g = scripted(4, (id, r) => {
      stopAt(1)(r);
      if (r.action === 'wolfKill') return null;
      if (r.action === 'runForSheriff') return id < 3 ? id : null;
      if (r.action === 'withdraw') return id === 2 ? null : id; // 1号 and 2号 退水
      return undefined;
    });
    await g.run().catch(() => {});
    expect(g.events.some((e) => e.text === '1号、2号 退水。')).toBe(true);
    expect(g.state.sheriff).toBe(2);
    expect(g.events.some((e) => e.data?.action === 'sheriffVote')).toBe(false);
  });

  it('a 自爆 on the stage postpones the election to the next day\'s 退水; a second one loses the badge', async () => {
    const probe = new Game({ names, humanSeat: -1, seed: 9 });
    const [w1, w2] = [idOf(probe, 'werewolf', 0), idOf(probe, 'werewolf', 1)];
    const c = idOf(probe, 'villager');
    for (const second of [false, true]) {
      const asked: string[] = [];
      const g = scripted(
        9,
        (id, r) => {
          stopAt(2)(r);
          if (r.action === 'runForSheriff' || r.action === 'withdraw') asked.push(`${r.day}:${r.action}:${id}`);
          if (r.action === 'wolfKill') return null;
          if (r.action === 'runForSheriff') return [w1, w2, c].includes(id) ? id : null;
          if (r.action === 'withdraw') {
            expect(r.canExplode ?? false).toBe(id === w2);
            return second && id === w2 ? EXPLODE_CHOICE : null;
          }
          return undefined;
        },
        (id, r) => (r.kind === 'speech' && r.purpose === 'campaign' && id === w1 ? { text: '我自爆。', explode: true } : '过。'),
      );
      await g.run().catch(() => {});
      const day1 = g.events.filter((e) => e.day === 1);
      // day 1: interrupted; the news still comes, then straight to night (no day speeches, no vote)
      expect(day1.some((e) => e.text.includes('警长竞选中断，推迟到明天'))).toBe(true);
      expect(day1.some((e) => Array.isArray(e.data?.nightDeaths))).toBe(true);
      expect(day1.some((e) => e.type === 'vote' || e.speechKind === 'discussion')).toBe(false);
      // day 2: no new 上警, the stage (minus the dead wolf) picks up at 退水
      expect(asked.filter((x) => x.startsWith('2:runForSheriff'))).toEqual([]);
      const day2 = asked.filter((x) => x.startsWith('2:withdraw')).map((x) => Number(x.split(':')[2]));
      if (second) expect(day2).toContain(w2); // the wolf blows up at 退水: the rest aren't asked
      else expect(day2.sort((x, y) => x - y)).toEqual([w2, c].sort((x, y) => x - y));
      if (second) {
        expect(g.players[w2].alive).toBe(false);
        expect(g.events.some((e) => e.day === 2 && e.text.includes('警徽流失'))).toBe(true);
        expect(g.state.sheriff).toBeNull();
      } else {
        // 警下 voted between the two who stayed, before day 2's news
        const vote = at(g, (e) => e.day === 2 && e.data?.action === 'sheriffVote');
        expect(vote).toBeGreaterThan(0);
        expect(vote).toBeLessThan(at(g, (e) => e.day === 2 && Array.isArray(e.data?.nightDeaths)));
      }
    }
  });

  it('the sheriff\'s exile vote counts 1.5; an exiled sheriff hands the badge on', async () => {
    const probe = new Game({ names, humanSeat: -1, seed: 13 });
    const sheriff = idOf(probe, 'villager', 0);
    const [wa, wb] = [idOf(probe, 'werewolf', 0), idOf(probe, 'werewolf', 1)];
    const other = idOf(probe, 'villager', 1);
    const g = scripted(13, (id, r) => {
      stopAt(1)(r);
      if (r.action === 'wolfKill') return null;
      if (r.action === 'runForSheriff') return id === sheriff ? id : null;
      if (r.action === 'vote') return id === sheriff ? wa : id === other ? wb : null;
      return undefined;
    });
    await g.run().catch(() => {});
    expect(g.events.some((e) => e.type === 'vote' && e.text.includes(`${wa + 1}号（1.5票）← ${sheriff + 1}(警长)`))).toBe(true);
    expect(g.events.find((e) => e.type === 'death' && e.data?.cause === 'vote')?.data?.id).toBe(wa);
  });
});

describe('Standard flow: day and night rules', () => {
  it('PK: tied players speak, only the rest vote, a second tie exiles nobody', async () => {
    const [a, b] = [0, 1];
    let pkVoters: number[] = [];
    const g = scripted(5, (id, r) => {
      stopAt(1)(r);
      if (r.action === 'wolfKill' || r.action === 'runForSheriff') return null;
      if (r.action === 'vote') return id === 2 ? a : id === 3 ? b : null;
      if (r.action === 'revote') {
        pkVoters.push(id);
        return id === 4 ? a : id === 5 ? b : null;
      }
      return undefined;
    });
    await g.run().catch(() => {});
    pkVoters = pkVoters.sort((x, y) => x - y);
    expect(pkVoters).toEqual([2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    expect(g.events.filter((e) => e.speechKind === 'defense').map((e) => e.speaker)).toEqual([a, b]);
    expect(g.events.some((e) => e.text === '再次平票，今天无人出局。')).toBe(true);
    expect(g.events.some((e) => e.type === 'death' && e.data?.cause === 'vote')).toBe(false);
  });

  it('night deaths after the first night have no last words', async () => {
    const probe = new Game({ names, humanSeat: -1, seed: 6 });
    const [v1, v2] = [idOf(probe, 'villager', 0), idOf(probe, 'villager', 1)];
    const g = scripted(6, (_id, r) => {
      stopAt(2)(r);
      if (r.action === 'wolfKill') return r.day === 1 ? v1 : v2;
      if (r.action === 'runForSheriff' || r.action === 'vote') return null;
      return undefined;
    });
    await g.run().catch(() => {});
    const words = g.events.filter((e) => e.speechKind === 'lastWords').map((e) => e.speaker);
    expect(words).toContain(v1);
    expect(words).not.toContain(v2);
    expect(g.players[v2].alive).toBe(false);
  });

  it('the witch saves herself only on the first night, and uses one potion a night', async () => {
    const probe = new Game({ names, humanSeat: -1, seed: 8 });
    const witch = idOf(probe, 'witch');
    const villager = idOf(probe, 'villager');
    for (const firstNight of [true, false]) {
      const saves: [number, number[]][] = [];
      const poisonAsked: number[] = [];
      const g = scripted(8, (_id, r) => {
        stopAt(2)(r);
        // night 1: knife the witch (she saves herself) / a villager (she lets it go); night 2: the witch
        if (r.action === 'wolfKill') return r.day === 1 && !firstNight ? villager : witch;
        if (r.action === 'witchSave') {
          saves.push([r.day, r.candidates]);
          return firstNight ? r.candidates[0] : null;
        }
        if (r.action === 'witchPoison') poisonAsked.push(r.day);
        if (r.action === 'runForSheriff' || r.action === 'vote') return null;
        return undefined;
      });
      await g.run().catch(() => {});
      if (firstNight) {
        expect(saves).toEqual([[1, [witch]]]);
        expect(poisonAsked).not.toContain(1); // saved tonight: no poison offered
      } else {
        // night 2: told she was knifed, but no save offered (the antidote is still there)
        expect(saves).toEqual([[1, [villager]]]);
        expect(g.events.some((e) => e.day === 2 && e.text.includes('除第一夜外女巫不能自救'))).toBe(true);
        expect(poisonAsked).toEqual([1, 2]);
        expect(g.players[witch].alive).toBe(false);
      }
    }
  });

  it('a knifed hunter shoots; a poisoned hunter cannot', async () => {
    for (const poisoned of [false, true]) {
      const probe = new Game({ names, humanSeat: -1, seed: 10 });
      const hunter = idOf(probe, 'hunter');
      const villager = idOf(probe, 'villager');
      let asked = false;

      const g = scripted(10, (_id, r) => {
        stopAt(1)(r);
        if (r.action === 'wolfKill') return poisoned ? null : hunter;
        if (r.action === 'witchPoison') return poisoned ? hunter : null;
        if (r.action === 'hunterShot') {
          asked = true;
          return villager;
        }
        if (r.action === 'runForSheriff' || r.action === 'vote') return null;
        return undefined;
      });
      await g.run().catch(() => {});
      const status = g.events.find((e) => e.text.startsWith('你的开枪状态') && e.visibility.kind === 'private' && e.visibility.to.includes(hunter))?.text ?? '';
      expect(status).toContain(poisoned ? '不能开枪' : '可以开枪');
      expect(asked).toBe(!poisoned);
      expect(g.players[villager].alive).toBe(poisoned);
      // shot by day: the victim has last words
      if (!poisoned) expect(g.events.some((e) => e.speechKind === 'lastWords' && e.speaker === villager)).toBe(true);
    }
  });

  it('the pack knows its 狼王; the seer checks the 狼王 as 狼人', () => {
    const g = new Game({ names, humanSeat: -1, seed: 10 });
    const king = idOf(g, 'wolfKing');
    const wolf = idOf(g, 'werewolf');
    expect(g.viewFor(wolf).known[king]).toBe('wolfKing');
    expect(g.viewFor(king).known[wolf]).toBe('werewolf');
    expect(g.viewFor(king).wolfKing).toEqual({ hasShot: false });
    expect(g.viewFor(idOf(g, 'villager')).known[king]).toBeUndefined();
  });

  it('an exiled 狼王 shoots in the open; a poisoned or self-destructed 狼王 cannot', async () => {
    for (const how of ['vote', 'poison', 'explode'] as const) {
      const probe = new Game({ names, humanSeat: -1, seed: 10 });
      const king = idOf(probe, 'wolfKing');
      const seer = idOf(probe, 'seer');
      let asked = false;
      const g = scripted(
        10,
        (id, r) => {
          stopAt(1)(r);
          if (r.action === 'seer') expect(r.candidates).toContain(king);
          if (r.action === 'seer') return king;
          if (r.action === 'wolfKill') return null;
          if (r.action === 'witchPoison') return how === 'poison' ? king : null;
          if (r.action === 'runForSheriff') return null;
          if (r.action === 'vote') return id === king ? null : king;
          if (r.action === 'wolfKingShot') {
            asked = true;
            expect(r.candidates).not.toContain(king);
            return seer;
          }
          return undefined;
        },
        (id, r) => (how === 'explode' && id === king && r.kind === 'speech' && r.canExplode ? { text: '我自爆。', explode: true } : '过。'),
      );
      await g.run().catch(() => {});
      expect(g.state.seerChecks[king]).toBe('wolf');
      // no night turn of its own: never woken alone, never told a shot status
      expect(g.events.some((e) => e.text.startsWith('狼王请睁眼'))).toBe(false);
      expect(g.events.some((e) => e.text.startsWith('你的开枪状态') && e.visibility.kind === 'private' && e.visibility.to.includes(king))).toBe(false);
      expect(g.players[king].alive).toBe(false);
      expect(asked).toBe(how === 'vote');
      expect(g.state.wolfKingShot).toBe(true);
      if (how === 'vote') {
        expect(g.events.some((e) => e.text.includes('是狼王，开枪带走了'))).toBe(true);
        expect(g.players[seer].alive).toBe(false);
        expect(g.deathCauses()[seer]).toBe('wolfKing');
        // shot by day: the victim has last words
        expect(g.events.some((e) => e.speechKind === 'lastWords' && e.speaker === seer)).toBe(true);
      } else expect(g.players[seer].alive).toBe(true);
    }
  });

  it('the guard may guard themselves, and wolves may 空刀', async () => {
    const probe = new Game({ names, humanSeat: -1, seed: 12 });
    const guard = idOf(probe, 'guard');
    let guardCands: number[] = [];
    const g = scripted(12, (_id, r) => {
      stopAt(1)(r);
      if (r.action === 'guard') {
        guardCands = r.candidates;
        return guard;
      }
      if (r.action === 'wolfKill') {
        expect(r.allowSkip).toBe(true);
        return null;
      }
      return null;
    });
    await g.run().catch(() => {});
    expect(guardCands).toContain(guard);
    expect(g.events.some((e) => e.type === 'wolfChat' && e.text.includes('今晚空刀'))).toBe(true);
    expect(g.events.some((e) => e.text === '昨晚是平安夜。')).toBe(true);
  });

  it('ends with 屠边 as soon as every villager is out', async () => {
    const probe = new Game({ names, humanSeat: -1, seed: 14 });
    const villagers = probe.players.filter((p) => p.role === 'villager').map((p) => p.id);
    const g = scripted(14, (_id, r) => {
      if (r.action === 'wolfKill') return villagers.find((v) => r.candidates.includes(v)) ?? null;
      if (r.action === 'vote' || r.action === 'revote') return villagers.find((v) => r.candidates.includes(v)) ?? null;
      if (['guard', 'witchSave', 'witchPoison', 'runForSheriff', 'hunterShot', 'wolfKingShot', 'badge'].includes(r.action)) return null;
      return undefined;
    });
    expect(await g.run()).toBe('wolf');
    expect(villagers.every((v) => !g.players[v].alive)).toBe(true);
    expect(g.players.filter((p) => GOD_ROLES.includes(p.role)).some((p) => p.alive)).toBe(true);
    expect(g.events.some((e) => e.text.includes('所有村民均已出局，狼人屠边成功'))).toBe(true);
  });
});
