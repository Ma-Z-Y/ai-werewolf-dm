import { seat, teamOf, type DeathCause, type GameEvent, type Player, type Winner } from './types';

/**
 * 颁奖典礼 (after the game): everyone is shown the whole game from the god's
 * view, gives one round of post-game remarks (seat 1 first), then votes for the
 * best and the worst player of the game. Ties share the award.
 */

/** One post-game speech. */
export interface ReviewLine {
  speaker: number;
  text: string;
  /** Written by the rule AI because the model failed. */
  fallback?: boolean;
}

/** Everything a player is told at the awards. */
export interface ReviewContext {
  self: number;
  /** Every seat with its role, alive or not at the end. */
  players: Player[];
  winner: Winner;
  /** The whole game (god view): every public and private line, every decision. */
  transcript: string;
  /** The post-game speeches so far, in order. */
  reviews: ReviewLine[];
  /** The game's raw record, for the rule AI's scoring. */
  events: GameEvent[];
  causes: Record<number, DeathCause>;
}

export interface AwardVote {
  best: number;
  worst: number;
  reason?: string;
  fallback?: boolean;
}

export type ReviewResult = string | { text: string; fallback?: boolean };

/** A player at the ceremony (AI or the human). */
export interface Reviewer {
  review(ctx: ReviewContext): Promise<ReviewResult>;
  awardVote(ctx: ReviewContext): Promise<AwardVote>;
}

/** A vote is valid when both picks are real seats, neither is yourself, and they differ. */
export function validVote(v: { best: number; worst: number }, self: number, seats: number): boolean {
  const ok = (n: number) => Number.isInteger(n) && n >= 0 && n < seats && n !== self;
  return ok(v.best) && ok(v.worst) && v.best !== v.worst;
}

export interface AwardTally {
  /** Seat → votes received, for every seat that got any. */
  bestVotes: Map<number, number[]>;
  worstVotes: Map<number, number[]>;
  /** Everyone tied for the most votes (empty if nobody voted). */
  best: number[];
  worst: number[];
}

/** Count the ballots (`votes[voter]`, null = no vote); every seat tied at the top wins. */
export function tallyAwards(votes: (AwardVote | null)[]): AwardTally {
  const bestVotes = new Map<number, number[]>();
  const worstVotes = new Map<number, number[]>();
  votes.forEach((v, voter) => {
    if (!v) return;
    bestVotes.set(v.best, [...(bestVotes.get(v.best) ?? []), voter]);
    worstVotes.set(v.worst, [...(worstVotes.get(v.worst) ?? []), voter]);
  });
  const top = (m: Map<number, number[]>) => {
    if (!m.size) return [];
    const max = Math.max(...[...m.values()].map((x) => x.length));
    return [...m.entries()].filter(([, x]) => x.length === max).map(([id]) => id).sort((a, b) => a - b);
  };
  return { bestVotes, worstVotes, best: top(bestVotes), worst: top(worstVotes) };
}

/**
 * A rough score of how well each seat played, from the record alone (the rule
 * AI's opinion): good votes on wolves, checks that found wolves, potions and the
 * hunter's (or 狼王's) shot on the right side, and being on the winning team.
 */
export function playScores(ctx: Pick<ReviewContext, 'players' | 'winner' | 'events' | 'causes'>): number[] {
  const { players, winner, events, causes } = ctx;
  const score = players.map(() => 0);
  const wolf = (id: number) => !!players[id] && teamOf(players[id].role) === 'wolf';
  for (const p of players) if (winner && teamOf(p.role) === winner) score[p.id] += 2;
  for (const e of events) {
    const d = e.data;
    if (!d) continue;
    if (e.type === 'vote' && (d.action === 'vote' || d.action === 'revote')) {
      for (const [voter, target] of Object.entries(d.votes as Record<string, number | null>)) {
        if (target === null) continue;
        const v = Number(voter);
        if (!wolf(v)) score[v] += wolf(target) ? 1 : -1;
        else if (!wolf(target)) score[v] += 0.5;
      }
    }
    const actor = e.visibility.kind === 'private' ? e.visibility.to[0] : undefined;
    if (actor === undefined || e.type !== 'private') continue;
    if (typeof d.check === 'number' && d.result === 'wolf') score[actor] += 1;
    if (typeof d.poison === 'number') score[actor] += wolf(d.poison) ? 2 : -2;
    if (typeof d.saved === 'number') score[actor] += wolf(d.saved) ? -1 : 1;
  }
  const hunter = players.find((p) => p.role === 'hunter');
  if (hunter) {
    for (const [id, c] of Object.entries(causes)) if (c === 'hunter') score[hunter.id] += wolf(Number(id)) ? 2 : -2;
  }
  const king = players.find((p) => p.role === 'wolfKing');
  if (king) {
    for (const [id, c] of Object.entries(causes)) if (c === 'wolfKing') score[king.id] += wolf(Number(id)) ? -2 : 2;
  }
  // exiled good players hurt their side; an exiled wolf was caught out
  for (const [id, c] of Object.entries(causes)) if (c === 'vote') score[Number(id)] -= 1;
  return score;
}

/** Best and worst by `playScores`, never yourself; ties broken by `rand` (0..1). */
export function scoredVote(ctx: ReviewContext, rand: () => number): AwardVote {
  const s = playScores(ctx);
  const others = ctx.players.map((p) => p.id).filter((id) => id !== ctx.self);
  const noisy = others.map((id) => [id, s[id] + rand() * 0.9] as const);
  const best = noisy.reduce((a, b) => (b[1] > a[1] ? b : a))[0];
  const rest = noisy.filter(([id]) => id !== best);
  const worst = rest.reduce((a, b) => (b[1] < a[1] ? b : a))[0];
  return { best, worst, reason: `${seat(best)}操作最到位，${seat(worst)}失误最多` };
}
