import { tallyAwards, type AwardTally, type AwardVote, type ReviewContext, type ReviewLine, type Reviewer } from '../game/ceremony';
import type { Game } from '../game/game';
import { seat } from '../game/types';
import { godTranscript } from '../ai/review';
import type { Stage } from '../render/stage';
import { audio } from '../audio/audio';
import { getConfig } from '../settings';
import { h } from './dom';

/** A line in the ceremony's own log tab. */
export type CeremonyEntry = { kind: 'say'; id: number; text: string; tag: string; fallback?: boolean } | { kind: 'note'; text: string };

/** What the ceremony needs from the game screen. */
export interface CeremonyHost {
  game: Game;
  stage: Stage;
  me: number;
  /** One per seat; null for the human (asked through the panel instead). */
  reviewers: (Reviewer | null)[];
  /** Each AI's own notes on why it did what it did (null for the human / rule AI). */
  notes: () => (string[] | null)[];
  log(e: CeremonyEntry): void;
  bubble(id: number, text: string | null): void;
  clearBubbles(): void;
  thinking(id: number | null): void;
  status(phase: string, actor?: string): void;
  panel(...children: (Node | string | null)[]): void;
  closePanel(): void;
  /** Everyone is standing again (labels drop the ✝). */
  revive(): void;
  /** The ceremony is over: offer the way back to the title. */
  finish(): void;
  /** Back to the title screen. */
  leave(): void;
  /** The game screen is gone (back at the title). */
  gone(): boolean;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** How long the best players stand on the podium before the worst go up. */
const BEST_HOLD_MS = 20000;

const JEERS = ['下次好好玩！', '接好了！💩', '就这？', '💩💩💩', '臭棋篓子！', '别躲！', '吃我一坨！', '哈哈哈哈'];
const WHINES = ['别扔了！😭', '我错了我错了', '呜呜呜…', '好臭！', '下局一定好好打！'];

export class AwardCeremony {
  private lines: ReviewLine[] = [];

  constructor(private host: CeremonyHost) {}

  private get dead() {
    return this.host.gone();
  }

  async run() {
    const { host } = this;
    const { game, stage } = host;
    host.status('颁奖典礼');
    audio.setEnding('ceremony');
    await this.swapScene(() => stage.ceremonyScene());
    if (this.dead) return;
    host.revive();

    const players = game.players.map((p) => ({ ...p }));
    const causes = game.deathCauses();
    const events = game.events.slice();
    const transcript = godTranscript({ players, events, winner: game.state.winner, causes }, host.notes());
    const ctx = (self: number): ReviewContext => ({ self, players, winner: game.state.winner, transcript, reviews: this.lines.slice(), events, causes });

    host.log({
      kind: 'note',
      text: 'GM：颁奖典礼开始！迷雾散尽，小镇恢复了原样，所有人都回到了广场。GM 已经把整局的完整记录（所有发言、狼队频道、每个人的查验、用药、守护和投票）以上帝视角交给了每一个人。现在是赛后测评环节：从 1 号开始，每人发表一轮赛后感言，说说谁干得好、谁干得差；最后投票选出全场最佳和全场最差。',
    });
    await sleep(2500);

    // ── 赛后感言, seat 1 first ──
    for (let id = 0; id < players.length; id++) {
      if (this.dead) return;
      const p = players[id];
      host.status('颁奖典礼 · 赛后感言', `${seat(id)} ${p.name}`);
      stage.focus(id);
      let line: ReviewLine;
      if (id === host.me) line = { speaker: id, text: await this.askReview() };
      else {
        host.thinking(id);
        const r = await host.reviewers[id]!.review(ctx(id));
        host.thinking(null);
        line = typeof r === 'string' ? { speaker: id, text: r } : { speaker: id, text: r.text, fallback: r.fallback };
      }
      if (this.dead) return;
      this.lines.push(line);
      host.bubble(id, line.text);
      host.log({ kind: 'say', id, text: line.text, tag: '赛后感言', fallback: line.fallback });
      if (id !== host.me && id < players.length - 1) await this.readPause(line.text, players[id + 1]);
    }
    if (this.dead) return;
    stage.focus(null);

    // ── 投票 ──
    host.status('颁奖典礼 · 投票', '选出全场最佳与全场最差');
    host.log({ kind: 'note', text: 'GM：所有人都发表完赛后感言了。现在投票：每人选出一名全场最佳、一名全场最差（不能投自己）。票数最多的上台领奖，平票就一起上台。' });
    const votes: (AwardVote | null)[] = players.map(() => null);
    const human = players[host.me] ? this.askVote(ctx(host.me)).then((v) => (votes[host.me] = v)) : Promise.resolve();
    const ai = (async () => {
      for (let id = 0; id < players.length; id++) {
        if (id === host.me || this.dead) continue;
        host.thinking(id);
        votes[id] = await host.reviewers[id]!.awardVote(ctx(id));
        host.thinking(null);
      }
    })();
    await Promise.all([human, ai]);
    if (this.dead) return;
    host.closePanel();
    host.clearBubbles();
    const tally = tallyAwards(votes);
    this.announce(tally, votes);
    if (!tally.best.length || !tally.worst.length) {
      host.finish();
      return;
    }
    await sleep(2500);
    if (this.dead) return;

    // ── 全场最佳 ──
    const names = (ids: number[]) => ids.map((i) => `${seat(i)} ${players[i].name}`).join('、');
    host.status('颁奖典礼 · 全场最佳', names(tally.best));
    await stage.raisePodium(tally.best.length);
    if (this.dead) return;
    audio.setEnding('award');
    const bestSince = Date.now();
    await stage.awardBest(tally.best);
    if (this.dead) return;
    for (const id of tally.best) host.bubble(id, id === host.me ? '🏆' : '谢谢大家！🏆💐');
    await sleep(Math.max(0, BEST_HOLD_MS - (Date.now() - bestSince)));
    if (this.dead) return;

    // ── 全场最差 ──
    host.clearBubbles();
    audio.setEnding('ceremony');
    await stage.stepDown(tally.best);
    if (this.dead) return;
    host.status('颁奖典礼 · 全场最差', names(tally.worst));
    audio.setEnding('shame');
    await stage.awardWorst(tally.worst);
    if (this.dead) return;
    await sleep(1200);
    if (this.dead) return;
    // the human joins in by hand, unless they are the one up there
    const thrower = tally.worst.includes(host.me) ? null : host.me;
    const storm = stage.poopStorm(tally.worst, thrower);
    void this.heckle(tally.worst);
    await storm;
    if (this.dead) return;
    if (thrower !== null) {
      this.throwPanel(thrower, names(tally.worst));
      return;
    }
    await sleep(6000);
    if (!this.dead) host.finish();
  }

  /** Fade to white, rebuild the scene behind it, fade back in. */
  private async swapScene(swap: () => void) {
    const veil = h('div', { class: 'veil' });
    document.body.appendChild(veil);
    await sleep(30);
    veil.classList.add('show');
    await sleep(900);
    swap();
    await sleep(500);
    veil.classList.remove('show');
    await sleep(1000);
    veil.remove();
  }

  /** Give the human time to read an AI's remarks: until 下一位 (逐条查看), or a reading-speed pause. */
  private async readPause(text: string, next: { id: number; name: string }) {
    if (!getConfig().stepSpeech) {
      await sleep(Math.min(9000, 2500 + text.length * 45));
      return;
    }
    await new Promise<void>((resolve) => {
      const go = () => {
        document.removeEventListener('keydown', onKey);
        this.host.closePanel();
        resolve();
      };
      const onKey = (e: KeyboardEvent) => {
        if (e.repeat || !['Enter', ' ', 'ArrowRight'].includes(e.key)) return;
        if ((e.target as HTMLElement).closest?.('input, textarea, select, .modal-back')) return;
        e.preventDefault();
        go();
      };
      document.addEventListener('keydown', onKey);
      const label = next.id === this.host.me ? '轮到你发表感言 ▶' : `下一位：${seat(next.id)} ${next.name} ▶`;
      this.host.panel(h('div', { class: 'next-speech' }, h('div', { class: 'hint' }, '看完了就请下一位（空格 / 回车）。'), h('button', { class: 'btn primary', onclick: go }, label)));
    });
  }

  private askReview(): Promise<string> {
    return new Promise((resolve) => {
      const ta = h('textarea', { placeholder: '例：3 号预言家查杀报得漂亮，MVP！7 号女巫毒错人，全场最差…', maxlength: '400' }) as HTMLTextAreaElement;
      const count = h('span', { class: 'count' }, '0 / 400');
      ta.oninput = () => (count.textContent = `${ta.value.length} / 400`);
      const done = (text: string) => {
        this.host.closePanel();
        resolve(text);
      };
      ta.onkeydown = (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && ta.value.trim()) done(ta.value.trim());
      };
      audio.chime();
      this.host.panel(
        h('div', { class: 'title' }, '轮到你 · 赛后感言'),
        h('div', { class: 'hint' }, '游戏结束，身份全部公开（「全部」页可以回看整局，包括每个人的私密信息）。说说这局谁打得好、谁打得差，也可以复盘你自己。'),
        ta,
        h('div', { class: 'row' }, count, h('button', { class: 'btn', onclick: () => done('没什么好说的，大家都辛苦了！') }, '过'), h('button', { class: 'btn primary', onclick: () => ta.value.trim() && done(ta.value.trim()) }, '发言 (⌘↵)')),
      );
      // a phone would throw up the keyboard before the prompt is read
      if (!matchMedia('(max-width: 760px), (max-height: 520px)').matches) setTimeout(() => ta.focus(), 50);
    });
  }

  /** The human's ballot: one best, one worst (not yourself, not the same person). */
  private askVote(ctx: ReviewContext): Promise<AwardVote> {
    return new Promise((resolve) => {
      const pick: { best: number | null; worst: number | null } = { best: null, worst: null };
      const others = ctx.players.filter((p) => p.id !== ctx.self);
      const confirm = h('button', { class: 'btn primary', disabled: true }, '投票') as HTMLButtonElement;
      const rows: Record<'best' | 'worst', Map<number, HTMLButtonElement>> = { best: new Map(), worst: new Map() };
      const refresh = () => {
        for (const k of ['best', 'worst'] as const) for (const [id, b] of rows[k]) b.classList.toggle('sel', pick[k] === id);
        const ok = pick.best !== null && pick.worst !== null && pick.best !== pick.worst;
        confirm.disabled = !ok;
        confirm.textContent = ok ? `投票：最佳 ${seat(pick.best!)} · 最差 ${seat(pick.worst!)}` : pick.best !== null && pick.best === pick.worst ? '最佳和最差不能是同一人' : '投票';
      };
      const row = (k: 'best' | 'worst', label: string) =>
        h(
          'div',
          { class: 'award-row' },
          h('span', { class: 'award-label' }, label),
          h(
            'div',
            { class: 'targets' },
            ...others.map((p) => {
              const b = h('button', { class: 'btn', title: p.name, onclick: () => ((pick[k] = p.id), refresh()) }, String(p.id + 1)) as HTMLButtonElement;
              rows[k].set(p.id, b);
              return b;
            }),
          ),
        );
      confirm.onclick = () => {
        if (pick.best === null || pick.worst === null || pick.best === pick.worst) return;
        this.host.panel(h('div', { class: 'hint' }, '已投票，等其他人投完…'));
        resolve({ best: pick.best, worst: pick.worst });
      };
      audio.chime();
      this.host.panel(
        h('div', { class: 'title' }, '颁奖典礼 · 投票'),
        h('div', { class: 'hint' }, '选出整场表现最好和最差的玩家（不能投自己，两项不能是同一人）。'),
        row('best', '🏆 全场最佳'),
        row('worst', '💩 全场最差'),
        h('div', { class: 'row' }, confirm),
      );
    });
  }

  /** Put the ballots and the result in the log. */
  private announce(t: AwardTally, votes: (AwardVote | null)[]) {
    const P = this.host.game.players;
    const ballots = votes
      .map((v, id) => (v ? `${id + 1}号→最佳${v.best + 1}·最差${v.worst + 1}${v.reason ? `（${v.reason}）` : ''}` : null))
      .filter(Boolean)
      .join('；');
    this.host.log({ kind: 'note', text: `投票明细：${ballots}` });
    const line = (m: Map<number, number[]>) =>
      [...m.entries()].sort((a, b) => b[1].length - a[1].length).map(([id, vs]) => `${seat(id)}（${vs.length}票）`).join('、');
    const who = (ids: number[]) => ids.map((i) => `${seat(i)} ${P[i].name}`).join('、');
    this.host.log({ kind: 'note', text: `🏆 全场最佳：${who(t.best) || '无'}　得票：${line(t.bestVotes) || '无'}` });
    this.host.log({ kind: 'note', text: `💩 全场最差：${who(t.worst) || '无'}　得票：${line(t.worstVotes) || '无'}` });
    this.host.panel(
      h(
        'div',
        { class: 'award-result' },
        h('div', { class: 'best' }, '🏆 全场最佳：', who(t.best) || '无'),
        h('div', { class: 'worst' }, '💩 全场最差：', who(t.worst) || '无'),
      ),
    );
  }

  /** The human's own 💩 button (and Space / Enter), with a running count. */
  private throwPanel(me: number, losers: string) {
    let thrown = 0;
    let last = 0;
    const count = h('span', { class: 'count' }, '还没扔');
    const fire = () => {
      if (this.dead) return;
      // a mad clicker still gets a throw about every tenth of a second
      const now = performance.now();
      if (now - last < 110 || !this.host.stage.throwPoop(me)) return;
      last = now;
      thrown++;
      count.textContent = `你已经扔了 ${thrown} 坨`;
      if (thrown % 5 === 1) this.host.bubble(me, ['看招！💩', '接着！', '吃我一坨！', '让你划水！'][Math.floor(thrown / 5) % 4]);
      btn.classList.remove('bump');
      void btn.offsetWidth; // restart the bump animation
      btn.classList.add('bump');
    };
    const onKey = (e: KeyboardEvent) => {
      if (this.dead) return document.removeEventListener('keydown', onKey);
      if (![' ', 'Enter'].includes(e.key)) return;
      if ((e.target as HTMLElement).closest?.('input, textarea, select, .modal-back')) return;
      e.preventDefault();
      fire();
    };
    document.addEventListener('keydown', onKey);
    const btn = h('button', { class: 'btn poop', onclick: fire }, '💩 扔！') as HTMLButtonElement;
    audio.chime();
    this.host.panel(
      h('div', { class: 'title' }, '💩 轮到你了！'),
      h('div', { class: 'hint' }, `狠狠地砸向台上的 ${losers}！点按钮或按空格 / 回车扔，想扔多少扔多少。`),
      btn,
      h('div', { class: 'row' }, count, h('button', { class: 'btn primary', onclick: () => this.host.leave() }, '回到标题画面')),
    );
  }

  /** The crowd jeers and the losers whine while the poop flies. */
  private async heckle(worst: number[]) {
    const pick = <T,>(xs: T[]) => xs[Math.floor(Math.random() * xs.length)];
    const crowd = this.host.game.players.map((p) => p.id).filter((id) => !worst.includes(id) && id !== this.host.me);
    while (!this.dead) {
      this.host.clearBubbles();
      for (const id of crowd) if (Math.random() < 0.35) this.host.bubble(id, pick(JEERS));
      for (const id of worst) if (id !== this.host.me && Math.random() < 0.6) this.host.bubble(id, pick(WHINES));
      await sleep(2600);
    }
  }
}
