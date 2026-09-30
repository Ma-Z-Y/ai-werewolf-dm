import { validVote, type ReviewContext } from '../game/ceremony';
import { ROLE_NAME, isWolf, seat, type DeathCause, type GameEvent, type Player, type Winner } from '../game/types';
import type { Persona } from '../personas';
import { RULEBOOK } from './prompts';

const CAUSE: Record<DeathCause, string> = {
  wolf: '夜里被狼刀',
  poison: '被女巫毒死',
  hunter: '被猎人开枪带走',
  wolfKing: '被狼王开枪带走',
  vote: '白天被放逐',
  explode: '自爆',
  gm: '被 GM 判出局',
};

const PHASE_TAG: Partial<Record<GameEvent['phase'], string>> = {
  night: '夜',
  election: '警长竞选',
  lastWords: '遗言',
  vote: '投票',
};

const SPEECH_TAG: Record<string, string> = {
  discussion: '发言', summary: '警长归票', lastWords: '遗言', defense: '放逐PK发言', campaign: '警上发言', campaignPk: '警长PK发言',
};

const who = (players: Player[], id: number) => `${seat(id)}（${ROLE_NAME[players[id].role]}）`;

/**
 * 上帝视角的整局记录：每一条公开发言、狼队频道、每个人的私密信息（查验、用药、守护）
 * 和投票明细，按时间顺序；私密信息标明是谁的。`notes` 是各 AI 自己记下的决策理由。
 */
export function godTranscript(
  data: { players: Player[]; events: GameEvent[]; winner: Winner; causes: Record<number, DeathCause> },
  notes: (string[] | null)[] = [],
  maxChars = 60000,
): string {
  const { players, events, winner, causes } = data;
  const lines: string[] = [];
  lines.push(`【身份公开】${players.map((p) => `${who(players, p.id)}${p.isHuman ? '【真人玩家】' : ''}`).join('，')}`);
  lines.push(`【结果】${winner === 'good' ? '好人阵营获胜' : winner === 'wolf' ? '狼人阵营获胜' : '未分胜负'}`);
  const out = players.filter((p) => !p.alive).map((p) => `${who(players, p.id)}：${causes[p.id] ? CAUSE[causes[p.id]] : '出局'}`);
  lines.push(`【出局情况】${out.join('；') || '无人出局'}；存活到最后：${players.filter((p) => p.alive).map((p) => seat(p.id)).join('、') || '无'}`);
  lines.push('【完整记录（按时间顺序）】');
  let lastDay = -1;
  for (const e of events) {
    if (e.type === 'system') continue;
    if (e.day !== lastDay) {
      lastDay = e.day;
      lines.push(e.day === 0 ? '—— 开局 ——' : `—— 第 ${e.day} 天 / 夜 ——`);
    }
    const tag = `[第${e.day}${PHASE_TAG[e.phase] ?? '天'}]`;
    switch (e.type) {
      case 'speech':
        lines.push(`${tag} ${who(players, e.speaker!)} ${e.data?.explode ? '自爆前' : SPEECH_TAG[e.speechKind ?? 'discussion']}：「${e.text}」`);
        break;
      case 'wolfChat':
        lines.push(e.speaker !== undefined ? `${tag} 狼队频道 ${who(players, e.speaker)}：${e.text}` : `${tag} 狼队频道：${e.text}`);
        break;
      case 'private': {
        const to = e.visibility.kind === 'private' ? e.visibility.to : [];
        lines.push(`${tag} 私密→${to.map((id) => who(players, id)).join('、')}：${e.text}`);
        break;
      }
      default:
        lines.push(`${tag} GM：${e.text}`);
    }
  }
  const reasons = notes
    .map((n, id) => (n?.length ? `${who(players, id)}：${n.join('；')}` : ''))
    .filter(Boolean);
  if (reasons.length) lines.push('【各玩家当时记下的决策理由】', ...reasons);
  let text = lines.join('\n');
  if (text.length > maxChars) {
    // keep the roster and the ending; drop from the start of the record
    const head = lines.slice(0, 4).join('\n');
    text = `${head}\n…（中间较早的记录已省略）\n${text.slice(-(maxChars - head.length))}`;
  }
  return text;
}

/** System prompt at the ceremony: the rules, then who you were (the game is over, nothing is secret any more). */
export function reviewSystemPrompt(ctx: ReviewContext, persona: Persona): string {
  const me = ctx.players[ctx.self];
  return `${RULEBOOK}

# 赛后测评（颁奖典礼）
- 游戏已经结束，所有身份都已公开，现在没有阵营、没有秘密，也不需要再伪装。
- 你是 ${seat(me.id)}「${persona.name}」，性格：${persona.trait}。这局你的身份是：${ROLE_NAME[me.role]}（${isWolf(me.role) ? '狼人阵营' : '好人阵营'}），${me.alive ? '活到了最后' : '中途出局'}。
- GM 把整局的完整记录以上帝视角交给了每个人：所有公开发言、狼队频道、每个人的私密信息（查验、用药、守护、刀人）和投票明细。
- 现在是测评环节：每个人轮流发表一轮赛后感言，点评谁打得好、谁打得差，之后大家投票选出全场最佳和全场最差。
- 用简体中文口语，性格影响语气；提到玩家时用「N号」。只输出你说出口的话，不要写动作、旁白或思考过程。`;
}

function reviewsSoFar(ctx: ReviewContext): string {
  if (!ctx.reviews.length) return '（你是第一个发言的）';
  return ctx.reviews.map((r) => `${who(ctx.players, r.speaker)}：「${r.text}」`).join('\n');
}

export function reviewUserPrompt(ctx: ReviewContext, task: string): string {
  return [
    `【整局完整记录 · 上帝视角】\n${ctx.transcript}`,
    `【赛后感言 · 已发言】\n${reviewsSoFar(ctx)}`,
    `【当前任务】\n${task}`,
  ].join('\n\n');
}

/** The extra instruction for the post-game speech. */
export const REVIEW_TASK = `现在是赛后测评环节，游戏已经结束。请以上帝视角回顾整局，发表一轮赛后感言：
- 点名说出你认为这局谁打得好、谁打得差（用「N号」），并给出具体理由：关键发言、查验、用药、守护、刀人、投票、站边、悍跳或倒钩是否成功等。
- 可以回应前面的人的点评（同意或反驳），也可以复盘你自己哪里做得好、哪里失误。
- 性格鲜明、有趣一点，可以调侃，但要基于记录里真实发生的事。150 字以内，直接说内容。`;

export function awardVoteTask(ctx: ReviewContext): string {
  const others = ctx.players.filter((p) => p.id !== ctx.self).map((p) => p.id + 1).join('、');
  return `所有人都发表完赛后感言了，现在投票：选出整场表现最好的玩家（全场最佳）和表现最差的玩家（全场最差）。
- 只能从以下号码中选：${others}（不能投自己）；最佳和最差必须是两个不同的人。
- 结合整局记录和大家的点评，按真实表现投票，不看阵营输赢。
只输出一行 JSON：{"best": 号码, "worst": 号码, "reason": "30字以内的理由"}`;
}

/** Parse `{"best": n, "worst": n}` (1-based seats) into 0-based seats; null if unusable. */
export function parseAwardVote(text: string, self: number, seats: number): { best: number; worst: number; reason: string } | null {
  let best: number | undefined;
  let worst: number | undefined;
  let reason = '';
  const m = text.match(/\{[\s\S]*?\}/);
  if (m) {
    try {
      const o = JSON.parse(m[0]);
      best = Number(String(o.best ?? o['最佳'] ?? '').match(/\d+/)?.[0]);
      worst = Number(String(o.worst ?? o['最差'] ?? '').match(/\d+/)?.[0]);
      reason = String(o.reason ?? o['理由'] ?? '');
    } catch {
      /* fall through */
    }
  }
  if (!best) best = Number(text.match(/"?(?:best|最佳)"?\s*[:：]\s*"?(\d{1,2})/)?.[1]);
  if (!worst) worst = Number(text.match(/"?(?:worst|最差)"?\s*[:：]\s*"?(\d{1,2})/)?.[1]);
  const vote = { best: best - 1, worst: worst - 1, reason };
  return validVote(vote, self, seats) ? vote : null;
}
