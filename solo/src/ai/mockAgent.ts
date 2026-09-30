import { playScores, scoredVote, type AwardVote, type ReviewContext, type ReviewResult, type Reviewer } from '../game/ceremony';
import { Rng } from '../game/rng';
import { EXPLODE_CHOICE, ROLE_NAME, isWolf, seat, type Agent, type PlayerView, type SpeechRequest, type SpeechResult, type TargetRequest, type WolfChatRequest } from '../game/types';

interface PublicSeerClaims {
  speakers: Set<number>;
  checks: Map<number, { wolf: Set<number>; good: Set<number> }>;
}

/** Public claims a rule AI may use: never touches hidden state, only visible speeches. */
function publicSeerClaims(v: PlayerView): PublicSeerClaims {
  const speakers = new Set<number>();
  const checks = new Map<number, { wolf: Set<number>; good: Set<number> }>();
  for (const e of v.events) {
    if (e.type !== 'speech' || e.speaker === undefined || !/我是预言家/.test(e.text)) continue;
    speakers.add(e.speaker);
    const own = checks.get(e.speaker) ?? { wolf: new Set<number>(), good: new Set<number>() };
    for (const m of e.text.matchAll(/(查杀|金水)\s*(\d{1,2})\s*号?/g)) {
      const id = Number(m[2]) - 1;
      if (id < 0 || id >= v.players.length || id === e.speaker) continue;
      own[m[1] === '查杀' ? 'wolf' : 'good'].add(id);
    }
    checks.set(e.speaker, own);
  }
  return { speakers, checks };
}

export interface MockSnapshot {
  rng: number;
  suspicion: [number, number][];
}

/**
 * Offline rule-based agent: no LLM needed. Used for tests and the
 * "离线规则 AI" mode. Plays plausibly but simply.
 */
export class MockAgent implements Agent, Reviewer {
  private rng: Rng;
  private suspicion = new Map<number, number>();

  constructor(seed?: number, private delayMs = 0) {
    this.rng = new Rng(seed);
  }

  /** Save-game state (the replay never calls agents, so their memory is saved as is). */
  snapshot(): MockSnapshot {
    return { rng: this.rng.state, suspicion: [...this.suspicion] };
  }

  restore(s: MockSnapshot) {
    this.rng = new Rng(s.rng);
    this.suspicion = new Map(s.suspicion);
  }

  private async wait() {
    if (this.delayMs) await new Promise((r) => setTimeout(r, this.delayMs * (0.5 + this.rng.next())));
  }

  private isWolf(v: PlayerView) {
    return isWolf(v.self.role);
  }

  /** Pick the most suspicious candidate, with some noise. */
  private suspect(v: PlayerView, cands: number[]): number {
    const known = v.known;
    const knownWolf = cands.filter((c) => known[c] === 'wolf' || (isWolf(known[c]) && !this.isWolf(v)));
    if (knownWolf.length) return this.rng.pick(knownWolf);
    const claims = publicSeerClaims(v);
    // If exactly one player has claimed seer, a good player should follow the
    // publicly reported 查杀. A wolf instead wants the claimant out.
    if (!this.isWolf(v) && claims.speakers.size === 1) {
      const claimant = [...claims.speakers][0];
      const checked = claims.checks.get(claimant)?.wolf;
      const claimedWolves = cands.filter((c) => checked?.has(c));
      if (claimedWolves.length) return this.rng.pick(claimedWolves);
    }
    const claimedSeers = cands.filter((c) => claims.speakers.has(c));
    if (this.isWolf(v) && claimedSeers.length) return this.rng.pick(claimedSeers);
    // for a wolf, teammates are safe; for good players, any known non-wolf role is safe
    const safe = cands.filter((c) => !(known[c] && known[c] !== 'wolf' && (this.isWolf(v) ? isWolf(known[c]) : !isWolf(known[c]))));
    const pool = safe.length ? safe : cands;
    let best = pool[0];
    let bestScore = -Infinity;
    for (const c of pool) {
      const s = (this.suspicion.get(c) ?? 0) + this.rng.next() * 2;
      if (s > bestScore) [best, bestScore] = [c, s];
    }
    return best;
  }

  /** A wolf called out as 查杀 (or by 3+ speakers today) sometimes self-destructs to cut the day short. */
  private cornered(v: PlayerView): boolean {
    const me = seat(v.self.id);
    const hits = v.events.filter(
      (e) => e.type === 'speech' && e.day === v.day && e.speaker !== v.self.id && e.text.includes(me) && /查杀|是狼/.test(e.text),
    );
    const checked = hits.some((e) => new RegExp(`查杀\\s*${me}|${me}\\s*是狼`).test(e.text));
    return (checked || hits.length >= 3) && this.rng.next() < 0.25;
  }

  async speak(req: SpeechRequest | WolfChatRequest, v: PlayerView): Promise<SpeechResult> {
    await this.wait();
    if (req.kind === 'speech' && req.canExplode && this.cornered(v)) {
      return { text: '算了，不装了，我就是狼。你们好人慢慢猜吧。', explode: true };
    }
    const others = v.players.filter((p) => p.alive && p.id !== v.self.id).map((p) => p.id);
    if (req.kind === 'wolfChat') {
      if (req.round > 1) return 'pass';
      const nonWolf = others.filter((o) => !isWolf(v.known[o]));
      const claims = publicSeerClaims(v);
      const claimedSeer = nonWolf.find((o) => claims.speakers.has(o));
      const sheriff = v.sheriff !== null && nonWolf.includes(v.sheriff) ? v.sheriff : null;
      const target = claimedSeer ?? sheriff ?? this.rng.pick(nonWolf.length ? nonWolf : others);
      return `我建议今晚刀 ${seat(target)}，${claimedSeer === target ? '他跳了预言家，威胁最大。' : sheriff === target ? '警长带队，先处理。' : '看起来像神。'}`;
    }
    const claims = publicSeerClaims(v);
    const target = this.suspect(v, others);
    this.suspicion.set(target, (this.suspicion.get(target) ?? 0) + 1);
    if (req.purpose === 'lastWords') return `我是好人，大家多注意 ${seat(target)}。`;
    if (req.purpose === 'defense') return '我真的是好人，请大家相信我，别投错了。';
    if (req.purpose === 'campaignPk') return '警徽给我，我能带好人把狼找出来。';
    if (req.purpose === 'summary') return `我是警长，听完一轮，我觉得 ${seat(target)} 最可疑，归票 ${seat(target)}。`;
    if (v.self.role === 'seer') {
      const checks = Object.entries(v.known).filter(([id]) => Number(id) !== v.self.id);
      if (checks.length) {
        const said = `我是预言家，${checks.map(([id, t]) => `${t === 'wolf' ? '查杀' : '金水'} ${seat(Number(id))}`).join('，')}。`;
        if (req.purpose !== 'campaign') return said;
        const flow = others.filter((o) => !(o in v.known)).slice(0, 2);
        return `${said}警徽流：${flow.map(seat).join('、')}。`;
      }
    }
    if (req.purpose === 'campaign') return `我上警是想拿警徽带队，目前我比较怀疑 ${seat(target)}。`;
    if (!this.isWolf(v) && claims.speakers.size === 1) {
      const claimant = [...claims.speakers][0];
      const checked = claims.checks.get(claimant)?.wolf;
      const checkedTarget = others.find((o) => checked?.has(o));
      if (checkedTarget !== undefined) return `我先站 ${seat(claimant)} 是真预言家，跟着他的查杀走，今天重点看 ${seat(checkedTarget)}。`;
    }
    const lines = [
      `我是好人，${seat(target)} 发言有点奇怪。`,
      `目前信息不多，先听听大家，我暂时怀疑 ${seat(target)}。`,
      `过，我觉得 ${seat(target)} 需要解释一下。`,
    ];
    return this.rng.pick(lines);
  }

  async review(ctx: ReviewContext): Promise<ReviewResult> {
    await this.wait();
    return mockReview(ctx, this.rng);
  }

  async awardVote(ctx: ReviewContext): Promise<AwardVote> {
    await this.wait();
    return scoredVote(ctx, () => this.rng.next());
  }

  async choose(req: TargetRequest, v: PlayerView): Promise<number | null> {
    await this.wait();
    const c = req.candidates;
    switch (req.action) {
      case 'runForSheriff':
        return v.self.role === 'seer' || this.rng.next() < 0.3 ? v.self.id : null;
      case 'withdraw':
        if (req.canExplode && this.cornered(v)) return EXPLODE_CHOICE;
        return v.self.role !== 'seer' && this.rng.next() < 0.25 ? v.self.id : null;
      case 'sheriffVote':
      case 'sheriffRevote': {
        // back a claimed seer / a checked-good candidate, else whoever looks least suspicious
        const claims = publicSeerClaims(v);
        const trusted = c.filter((x) => v.known[x] === 'good' || (!this.isWolf(v) && claims.speakers.has(x)));
        if (trusted.length) return this.rng.pick(trusted);
        if (this.isWolf(v)) {
          const mates = c.filter((x) => isWolf(v.known[x]));
          if (mates.length) return this.rng.pick(mates);
        }
        return c.reduce((best, x) => ((this.suspicion.get(x) ?? 0) < (this.suspicion.get(best) ?? 0) ? x : best), c[0]);
      }
      case 'badge': {
        // 警徽流: the seer hands it to the latest player checked good; a wolf passes it to a teammate
        const checks = v.events.filter((e) => e.data?.result === 'good' && c.includes(e.data.check as number));
        if (checks.length) return checks[checks.length - 1].data!.check as number;
        if (this.isWolf(v)) {
          const mates = c.filter((x) => isWolf(v.known[x]));
          return mates.length ? this.rng.pick(mates) : null;
        }
        const calm = c.filter((x) => (this.suspicion.get(x) ?? 0) === 0);
        return calm.length ? this.rng.pick(calm) : null;
      }
      case 'speakOrder':
        return this.rng.pick(c);
      case 'wolfKill': {
        const nonWolf = c.filter((x) => !isWolf(v.known[x]));
        const claims = publicSeerClaims(v);
        const claimedSeer = nonWolf.find((x) => claims.speakers.has(x));
        const sheriff = v.sheriff !== null && nonWolf.includes(v.sheriff) ? v.sheriff : null;
        return claimedSeer ?? sheriff ?? this.rng.pick(nonWolf.length ? nonWolf : c);
      }
      case 'seer': {
        const unchecked = c.filter((x) => v.known[x] === undefined);
        return this.rng.pick(unchecked.length ? unchecked : c);
      }
      case 'guard': {
        const claims = publicSeerClaims(v);
        const claimedSeer = c.find((x) => claims.speakers.has(x));
        const sheriff = v.sheriff !== null && c.includes(v.sheriff) ? v.sheriff : null;
        return claimedSeer ?? sheriff ?? this.rng.pick(c);
      }
      case 'witchSave':
        if (v.sheriff !== null && c.includes(v.sheriff)) return v.sheriff;
        if (v.day === 1) return c[0];
        return this.rng.next() < 0.3 ? this.rng.pick(c) : null;
      case 'witchPoison': {
        const knownWolf = c.filter((x) => v.known[x] === 'wolf');
        if (knownWolf.length) return this.rng.pick(knownWolf);
        const wolf = c.filter((x) => (this.suspicion.get(x) ?? 0) >= 2);
        return v.day > 1 && wolf.length && this.rng.next() < 0.5 ? this.rng.pick(wolf) : null;
      }
      case 'hunterShot':
        return this.suspect(v, c);
      case 'wolfKingShot': {
        // take a good player along: a claimed seer first, else whoever is not a teammate
        const nonWolf = c.filter((x) => !isWolf(v.known[x]));
        const claims = publicSeerClaims(v);
        const seer = nonWolf.filter((x) => claims.speakers.has(x));
        const sheriff = v.sheriff !== null && nonWolf.includes(v.sheriff) ? v.sheriff : null;
        return seer.length ? this.rng.pick(seer) : sheriff ?? (nonWolf.length ? this.rng.pick(nonWolf) : null);
      }
      case 'vote':
      case 'revote': {
        if (this.isWolf(v)) {
          const nonWolf = c.filter((x) => !isWolf(v.known[x]));
          if (nonWolf.length) return this.suspect(v, nonWolf);
        }
        return this.suspect(v, c);
      }
    }
    return null;
  }
}

/** The post-game remarks of the rule AI: praise the best-scoring seat, rib the worst. */
function mockReview(ctx: ReviewContext, rng: Rng): string {
  const vote = scoredVote(ctx, () => rng.next());
  const me = ctx.players[ctx.self];
  const role = (id: number) => ROLE_NAME[ctx.players[id].role];
  const won = ctx.winner !== null && isWolf(me.role) === (ctx.winner === 'wolf');
  const self = playScores(ctx)[ctx.self] > 0 ? '我自己这局还算对得起这个身份' : '我自己这局打得不太行，回去再练练';
  return rng.pick([
    `${won ? '赢了真开心！' : '输了有点可惜。'}这局我最佩服${seat(vote.best)}，${role(vote.best)}打得很到位；${seat(vote.worst)}这个${role(vote.worst)}就有点拉胯了。${self}。`,
    `复盘一下：${seat(vote.best)}（${role(vote.best)}）是全场 MVP，关键操作都做对了。${seat(vote.worst)}（${role(vote.worst)}）失误太多，下局加油。${self}。`,
    `我是${role(ctx.self)}。要我说，${seat(vote.best)}打得最好，${seat(vote.worst)}打得最差，没什么好争的。`,
  ]);
}
