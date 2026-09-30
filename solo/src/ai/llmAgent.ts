import type { Agent, PlayerView, SpeechRequest, SpeechResult, TargetRequest, WolfChatRequest } from '../game/types';
import { EXPLODE_CHOICE, YES_NO_ACTIONS, seat, type TargetAction } from '../game/types';

const NOTE_TAG: Record<TargetAction, string> = {
  vote: '投票', revote: 'PK再投', seer: '查验', guard: '守护', wolfKill: '刀', hunterShot: '开枪', wolfKingShot: '狼王开枪', witchSave: '救', witchPoison: '毒',
  runForSheriff: '上警', withdraw: '退水', sheriffVote: '警长投票', sheriffRevote: '警长PK再投', badge: '移交警徽', speakOrder: '发言顺序从',
};
import type { AwardVote, ReviewContext, ReviewResult, Reviewer } from '../game/ceremony';
import { MockAgent, type MockSnapshot } from './mockAgent';
import { REVIEW_TASK, awardVoteTask, parseAwardVote, reviewSystemPrompt, reviewUserPrompt } from './review';
import { cleanSpeech, parseExplode, parseTarget, privateNotebook, sharedNotebook, speechTask, systemPrompt, targetTask, type Persona } from './prompts';
import { ProviderError, type ChatMessage, type OpenAICompatibleProvider, type SerialQueue } from './provider';

export interface AgentTelemetry {
  onCall?(info: { player: number; kind: string; ms: number; ok: boolean; error?: string }): void;
  /**
   * Called when the model could not produce a usable answer. Resolve 'retry'
   * to try again, 'fallback' to let the rule AI act this once. Without this
   * hook the agent falls back immediately (headless / tests).
   */
  onFailure?(info: { player: number; kind: string; error: string }): Promise<'retry' | 'fallback'>;
}

/** Speeches quote other seats: below the provider default (0.9) to keep the numbers straight. */
const SPEECH_TEMPERATURE = 0.7;

export interface LLMSnapshot {
  notes: string[];
  fallback: MockSnapshot;
}

/**
 * An AI NPC driven by an OpenAI-compatible model. Memory =
 * role/system prompt + 共享发言记录本 (public events) + 角色私本 (private
 * events + its own notes). All calls go through one SerialQueue.
 */
export class LLMAgent implements Agent, Reviewer {
  /** Private notes: the reasons behind its own night actions / votes. */
  readonly notes: string[] = [];
  private fallback: MockAgent;

  constructor(
    private id: number,
    readonly persona: Persona,
    private provider: OpenAICompatibleProvider,
    private queue: SerialQueue,
    private telemetry: AgentTelemetry = {},
  ) {
    this.fallback = new MockAgent(id * 7919);
  }

  snapshot(): LLMSnapshot {
    return { notes: this.notes.slice(), fallback: this.fallback.snapshot() };
  }

  restore(s: LLMSnapshot) {
    this.notes.splice(0, this.notes.length, ...s.notes);
    this.fallback.restore(s.fallback);
  }

  private messages(view: PlayerView, task: string): ChatMessage[] {
    const election = sharedNotebook(view, { election: true });
    return [
      { role: 'system', content: systemPrompt(view, this.persona) },
      {
        role: 'user',
        content: [
          `【你的私人记录本（只有你知道，不是公开信息）】\n${privateNotebook(view, this.notes)}`,
          `【共享发言记录本 · 之前几天】\n${sharedNotebook(view, { before: view.day })}`,
          `【警长竞选记录（上警、警上发言、退水、警长投票）】\n${election}`,
          `【今天的发言（第 ${view.day} 天，按顺序）】\n${sharedNotebook(view, { onlyDay: view.day })}`,
          `【当前任务】\n${task}`,
        ].join('\n\n'),
      },
    ];
  }

  private async call(kind: string, msgs: ChatMessage[], maxTokens: number, reasoning?: string, temperature?: number): Promise<string> {
    let lastErr = '';
    // back off on overload (e.g. local server 507 OOM under memory pressure)
    const delays = [0, 5000, 15000];
    for (let attempt = 0; attempt < delays.length; attempt++) {
      if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
      try {
        const r = await this.queue.run(() => this.provider.chat(msgs, { maxTokens, reasoning, temperature }));
        this.telemetry.onCall?.({ player: this.id, kind, ms: r.ms, ok: true });
        if (r.content) return r.content;
        lastErr = '空回复';
      } catch (e) {
        lastErr = (e as Error).message;
        this.telemetry.onCall?.({ player: this.id, kind, ms: 0, ok: false, error: lastErr });
        if (e instanceof ProviderError && !e.retryable) break;
      }
    }
    throw new Error(lastErr);
  }

  /** Ask the host whether to retry after a failure; false = use the rule AI. */
  private async shouldRetry(kind: string, error: string): Promise<boolean> {
    if (!this.telemetry.onFailure) return false;
    return (await this.telemetry.onFailure({ player: this.id, kind, error })) === 'retry';
  }

  async speak(req: SpeechRequest | WolfChatRequest, view: PlayerView): Promise<SpeechResult> {
    const kind = req.kind === 'wolfChat' ? 'wolfChat' : req.purpose;
    while (true) {
      try {
        // wolf chat is frequent and short: use the (cheaper) decision reasoning level
        const reasoning = req.kind === 'wolfChat' ? this.provider.config.decisionReasoning : undefined;
        const raw = await this.call(kind, this.messages(view, speechTask(req, view)), req.kind === 'wolfChat' ? 800 : 2000, reasoning, SPEECH_TEMPERATURE);
        if (req.kind === 'speech' && req.canExplode) {
          const { text, explode } = parseExplode(cleanSpeech(raw, view, this.persona.name));
          if (explode) return { text: text || '过', explode };
          return text;
        }
        return cleanSpeech(raw, view, this.persona.name);
      } catch (e) {
        if (await this.shouldRetry(kind, (e as Error).message)) continue;
        const r = await this.fallback.speak(req, view);
        return typeof r === 'string' ? { text: r, fallback: true } : { ...r, fallback: true };
      }
    }
  }

  // ── 颁奖典礼 (post-game) ──

  private reviewMessages(ctx: ReviewContext, task: string): ChatMessage[] {
    return [
      { role: 'system', content: reviewSystemPrompt(ctx, this.persona) },
      { role: 'user', content: reviewUserPrompt(ctx, task) },
    ];
  }

  async review(ctx: ReviewContext): Promise<ReviewResult> {
    while (true) {
      try {
        const raw = await this.call('review', this.reviewMessages(ctx, REVIEW_TASK), 2000, undefined, SPEECH_TEMPERATURE);
        return cleanSpeech(raw, { self: { id: this.id } }, this.persona.name);
      } catch (e) {
        if (await this.shouldRetry('review', (e as Error).message)) continue;
        const r = await this.fallback.review(ctx);
        return { text: typeof r === 'string' ? r : r.text, fallback: true };
      }
    }
  }

  async awardVote(ctx: ReviewContext): Promise<AwardVote> {
    while (true) {
      try {
        const msgs = this.reviewMessages(ctx, awardVoteTask(ctx));
        let raw = await this.call('awardVote', msgs, 1000, this.provider.config.decisionReasoning);
        let vote = parseAwardVote(raw, ctx.self, ctx.players.length);
        if (!vote) {
          msgs.push(
            { role: 'assistant', content: raw },
            { role: 'user', content: '这个投票无效：best 和 worst 必须是两个不同的号码，且不能是你自己。请重新只输出一行 JSON：{"best": 号码, "worst": 号码, "reason": "30字以内的理由"}' },
          );
          raw = await this.call('awardVote', msgs, 1000, this.provider.config.decisionReasoning);
          vote = parseAwardVote(raw, ctx.self, ctx.players.length);
        }
        if (vote) return vote;
        if (await this.shouldRetry('awardVote', `模型给出的投票无法解析：${raw.slice(0, 80)}`)) continue;
      } catch (e) {
        if (await this.shouldRetry('awardVote', (e as Error).message)) continue;
      }
      return { ...(await this.fallback.awardVote(ctx)), fallback: true };
    }
  }

  async choose(req: TargetRequest, view: PlayerView): Promise<number | null> {
    while (true) {
      let raw = '';
      let parsed: ReturnType<typeof parseTarget>;
      try {
        const msgs = this.messages(view, targetTask(req, view));
        raw = await this.call(req.action, msgs, 1000, this.provider.config.decisionReasoning);
        parsed = parseTarget(raw, req);
        if (parsed.target === undefined) {
          // usually a seat outside the candidates (e.g. poisoning someone already dying
          // tonight): show the model its answer and the valid list once before giving up
          const cands = req.candidates.map((c) => c + 1).join('、');
          msgs.push(
            { role: 'assistant', content: raw },
            { role: 'user', content: `这个选择无效：只能从以下号码中选择：${cands}${req.allowSkip ? '；不选择请填 0' : ''}。请重新只输出一行 JSON：{"target": 号码, "reason": "20字以内的理由"}` },
          );
          raw = await this.call(req.action, msgs, 1000, this.provider.config.decisionReasoning);
          parsed = parseTarget(raw, req);
        }
      } catch (e) {
        if (await this.shouldRetry(req.action, (e as Error).message)) continue;
        return this.fallback.choose(req, view);
      }
      const { target, reason } = parsed;
      if (target === undefined) {
        if (await this.shouldRetry(req.action, `模型给出的目标不在可选号码内或无法解析：${raw.slice(0, 80)}`)) continue;
        return this.fallback.choose(req, view);
      }
      const tag = NOTE_TAG[req.action];
      const night = ['seer', 'guard', 'wolfKill', 'witchSave', 'witchPoison'].includes(req.action);
      const what = YES_NO_ACTIONS.includes(req.action) ? (target === EXPLODE_CHOICE ? '：自爆' : target === null ? '：否' : '：是') : target === null ? '：放弃' : ` ${seat(target)}`;
      this.notes.push(`第${req.day}${night ? '夜' : '天'}${tag}${what}${reason ? `（${reason}）` : ''}`);
      if (this.notes.length > 40) this.notes.splice(0, this.notes.length - 40);
      return target;
    }
  }
}
