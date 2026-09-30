import { describe, expect, it } from 'vitest';
import type { GameEvent, PlayerView, TargetRequest } from '../game/types';
import { cleanSpeech, parseTarget, sharedNotebook, speechProgress, speechTask, systemPrompt } from './prompts';

const req = (candidates: number[], allowSkip = true): TargetRequest => ({
  kind: 'target', action: 'vote', day: 1, candidates, allowSkip, prompt: '',
});

describe('parseTarget', () => {
  it('reads JSON with 1-based seats', () => {
    expect(parseTarget('{"target": 5, "reason": "可疑"}', req([2, 4, 6]))).toEqual({ target: 4, reason: '可疑' });
  });
  it('treats 0 as skip when allowed', () => {
    expect(parseTarget('{"target": 0}', req([1, 2])).target).toBeNull();
    expect(parseTarget('{"target": 0}', req([1, 2], false)).target).toBeUndefined();
  });
  it('rejects seats outside the candidate list', () => {
    expect(parseTarget('{"target": 9}', req([1, 2], false)).target).toBeUndefined();
  });
  it('falls back to loose formats', () => {
    expect(parseTarget('我决定 target: 3', req([2, 5])).target).toBe(2);
    expect(parseTarget('我投 6 号', req([5, 7])).target).toBe(5);
    expect(parseTarget('我选择不开枪', req([1, 2])).target).toBeNull();
    expect(parseTarget('撕掉警徽', req([1, 2])).target).toBeNull();
  });
  it('reads yes/no answers and a wolf\'s -1 (自爆) only when allowed', () => {
    const withdraw = (canExplode: boolean): TargetRequest => ({ kind: 'target', action: 'withdraw', day: 1, candidates: [4], allowSkip: true, prompt: '', canExplode });
    expect(parseTarget('{"target": 5}', withdraw(false)).target).toBe(4);
    expect(parseTarget('{"target": 0}', withdraw(false)).target).toBeNull();
    expect(parseTarget('{"target": -1}', withdraw(true)).target).toBe(-1);
    expect(parseTarget('{"target": -1}', withdraw(false)).target).toBeUndefined();
  });
});

describe('cleanSpeech', () => {
  const view = { self: { id: 2 } } as PlayerView;
  it('strips a self-attribution prefix', () => {
    expect(cleanSpeech('3号艾德：我是好人。', view, '艾德')).toBe('我是好人。');
    expect(cleanSpeech('「我觉得5号是狼」', view, '艾德')).toBe('我觉得5号是狼');
  });
  it('removes stage directions', () => {
    expect(cleanSpeech('*打了个嗝，眯着眼*\n我觉得5号是狼。', view, '艾德')).toBe('我觉得5号是狼。');
    expect(cleanSpeech('（清了清嗓子）我是好人。', view, '艾德')).toBe('我是好人。');
  });
  it('removes a trailing self-reported word count', () => {
    expect(cleanSpeech('我票先记6号，请5号接唱。\n\n（约190字）', view, '艾德')).toBe('我票先记6号，请5号接唱。');
    expect(cleanSpeech('我是好人。(共120字)', view, '艾德')).toBe('我是好人。');
    expect(cleanSpeech('我查验了3号，查杀。', view, '艾德')).toBe('我查验了3号，查杀。');
  });
  it('unwraps a decision-style JSON answer into a plain line', () => {
    expect(cleanSpeech('{"vote": "12号", "reason": "同意刀12。他拿警徽还查杀3。"}', view, '艾德')).toBe('同意刀12。他拿警徽还查杀3。');
    expect(cleanSpeech('```json\n{"target": 5, "reason": "他像预言家"}\n```', view, '艾德')).toBe('我选5号。他像预言家');
    expect(cleanSpeech('{"speech": "刀12，明天我悍跳。"}', view, '艾德')).toBe('刀12，明天我悍跳。');
    expect(cleanSpeech('{"vote": "12号", "reason": "同意刀12', view, '艾德')).not.toContain('{');
  });
  it('keeps colons that are part of the speech', () => {
    expect(cleanSpeech('我的结论：5号是狼', view, '艾德')).toBe('我的结论：5号是狼');
  });
});

describe('speechProgress', () => {
  const players = Array.from({ length: 12 }, (_, id) => ({ id, name: `P${id + 1}`, alive: id !== 6 }));
  const view = (self: number) => ({ self: { id: self }, players }) as unknown as PlayerView;
  const order = [4, 5, 7, 8, 9, 10, 11, 0, 1, 2, 3]; // seat 7 (id 6) is dead

  it('shows the order, your position and how many are left', () => {
    const text = speechProgress({ kind: 'speech', purpose: 'discussion', day: 2, order, spoken: [4, 5, 7], first: 4, clockwise: true }, view(8));
    expect(text).toContain('由 5号 开始顺时针');
    expect(text).toContain('号码从小到大，12号之后接1号');
    expect(text).toContain('已发言（按顺序）：5号 → 6号 → 8号');
    expect(text).toContain('轮到你：9号，第 4 位（共 11 人）');
    expect(text).toContain('还没轮到（按发言顺序）：10号 → 11号 → 12号 → 1号 → 2号 → 3号 → 4号，共 7 人');
    expect(text).toContain('不是沉默');
    expect(text).not.toContain('未发言');
    expect(text).toContain('全部发言结束后直接放逐投票');
    const withSheriff = speechProgress(
      { kind: 'speech', purpose: 'discussion', day: 2, order, spoken: [4, 5, 7], first: 4, clockwise: true },
      { ...view(8), sheriff: 3 } as PlayerView,
    );
    expect(withSheriff).toContain('由警长 4号 最后发言并归票');
  });

  it('tells the last speaker to wrap up', () => {
    const text = speechProgress({ kind: 'speech', purpose: 'discussion', day: 1, order, spoken: order.slice(0, 10), first: 4, clockwise: true }, view(3));
    expect(text).toContain('在你之后没有人了');
    expect(text).toContain('最后一个发言');
  });

  it('explains counter-clockwise wrap-around (2 → 1 → 12 → … → 3)', () => {
    const ccw = [1, 0, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2];
    const all = Array.from({ length: 12 }, (_, id) => ({ id, name: `P${id + 1}`, alive: true }));
    const v = { self: { id: 11 }, players: all } as unknown as PlayerView;
    const text = speechProgress({ kind: 'speech', purpose: 'discussion', day: 1, order: ccw, spoken: [1, 0], first: 1, clockwise: false }, v);
    expect(text).toContain('由 2号 开始逆时针（号码从大到小，1号之后接12号）发言');
    expect(text).toContain('已发言（按顺序）：2号 → 1号');
    expect(text).toMatch(/还没轮到（按发言顺序）：11号 → .* → 3号，共 9 人/);
  });

  it('covers tie defences', () => {
    const text = speechProgress({ kind: 'speech', purpose: 'defense', day: 1, order: [2, 9], spoken: [] }, view(2));
    expect(text).toContain('PK 玩家：3号、10号');
    expect(text).toContain('还有 1 位 PK 玩家发言');
    expect(text).toContain('台下玩家');
    const pk = speechProgress({ kind: 'speech', purpose: 'campaignPk', day: 1, order: [2, 9], spoken: [2] }, view(9));
    expect(pk).toContain('再平票则警徽流失');
  });

  it('shows the campaign order on the election stage', () => {
    const text = speechProgress({ kind: 'speech', purpose: 'campaign', day: 1, order: [1, 4, 8], spoken: [1], first: 1, clockwise: true }, view(4));
    expect(text).toContain('上警玩家：2号、5号、9号');
    expect(text).toContain('你之后还有 1 位警上玩家发言');
  });
});

describe('wolf chat task', () => {
  const chat = (speaker: number, text: string) =>
    ({ type: 'wolfChat', day: 1, speaker, text, visibility: { kind: 'private', to: [2, 4, 9] } }) as GameEvent;
  const view = (events: GameEvent[]) =>
    ({ self: { id: 2 }, day: 1, players: [2, 4, 9].map((id) => ({ id, name: '', alive: true })), events }) as unknown as PlayerView;
  const req = (round: number) => ({ kind: 'wolfChat', round, rounds: 3, day: 1 }) as const;

  it('quotes teammates who spoke since my last line and forbids passing over an objection', () => {
    const t = speechTask(req(2), view([chat(4, '刀 6 号'), chat(2, '同意'), chat(9, '（没有补充）'), chat(4, '我不同意，改刀 7')]));
    expect(t).toContain('5号：我不同意，改刀 7');
    expect(t).not.toContain('刀 6 号');
    expect(t).not.toContain('没有补充');
    expect(t).toContain('不能 pass');
  });
  it('asks round-1 speakers to answer the teammates before them', () => {
    expect(speechTask(req(1), view([chat(4, '刀 6 号')]))).toContain('先回应在你之前发言的队友');
    expect(speechTask(req(1), view([]))).not.toContain('队友说了');
  });
  it('tells the model this is talk, not a JSON vote', () => {
    expect(speechTask(req(1), view([]))).toContain('不要输出 JSON');
  });
  it('just passes in later rounds when nothing new was said', () => {
    expect(speechTask(req(2), view([chat(4, '刀 6 号'), chat(2, '同意')]))).toContain('只回复：pass');
  });
});

describe('day speech prompt', () => {
  const players = Array.from({ length: 12 }, (_, id) => ({ id, name: `P${id + 1}`, alive: id !== 1 }));
  const speech = (speaker: number, text: string) =>
    ({ type: 'speech', day: 1, speaker, speechKind: 'discussion', text, visibility: { kind: 'public' } }) as GameEvent;
  const view = (self: number, role: string, events: GameEvent[] = []) =>
    ({ self: { id: self, role }, day: 1, players, events }) as unknown as PlayerView;
  const req = { kind: 'speech', purpose: 'discussion', day: 1 } as const;

  it('puts the speaker on a header line and fences the words', () => {
    const t = sharedNotebook(view(0, 'villager', [speech(6, '6号你承认刀了4号？')]), { onlyDay: 1 });
    expect(t).toBe('[第1天 发言] 发言人：7号（P7）\n「6号你承认刀了4号？」');
  });
  it('keeps the election in its own shared record', () => {
    const campaign = { ...speech(3, '我是预言家，警徽流 5、8'), speechKind: 'campaign', phase: 'election' } as GameEvent;
    const day = { ...speech(6, '我站边4号'), phase: 'discussion' } as GameEvent;
    const v = view(0, 'villager', [campaign, day]);
    const election = sharedNotebook(v, { election: true });
    expect(election).toContain('[第1天 警上发言] 发言人：4号');
    expect(election).not.toContain('我站边4号');
    const today = sharedNotebook(v, { onlyDay: 1 });
    expect(today).toContain('我站边4号');
    expect(today).not.toContain('警徽流');
    // a campaign speaker answers the others on the stage, a day speaker is pointed at both books
    expect(speechTask({ kind: 'speech', purpose: 'campaign', day: 1 }, view(8, 'villager', [campaign, day]))).toContain('【警长竞选记录】');
    expect(speechTask(req, view(8, 'villager', [campaign, day]))).toMatch(/在你之前已有 7号 发言.*警上的发言见【警长竞选记录】/);
  });
  it('asks to check who said a quoted line', () => {
    expect(speechTask(req, view(7, 'villager', [speech(6, '…')]))).toContain('【引用自检】');
    expect(speechTask(req, view(7, 'villager'))).not.toContain('【引用自检】');
  });
  it('keeps the wolf channel secret and rules out dead seats', () => {
    const t = speechTask(req, view(5, 'werewolf'));
    expect(t).toContain('狼队频道的内容');
    expect(t).toContain('不能再当作怀疑或放逐对象');
    expect(speechTask(req, view(4, 'villager'))).not.toContain('狼队频道');
  });
});

describe('system prompt', () => {
  it('says town trades in names are not game roles', () => {
    const view = { self: { id: 0, role: 'villager' }, known: {} } as unknown as PlayerView;
    expect(systemPrompt(view, { name: '药师伊索', trait: '', look: {} as never })).toContain('药师不是女巫');
  });
});
