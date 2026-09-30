import type { Persona } from '../personas';
import {
  ROLE_NAME,
  EXPLODE_CHOICE,
  isWolf,
  YES_NO_ACTIONS,
  seat,
  type GameEvent,
  type SpeechKind,
  type PlayerView,
  type Role,
  type SpeechRequest,
  type TargetRequest,
  type WolfChatRequest,
} from '../game/types';

/**
 * 规则手册：全部角色能力 + 完整流程 + 信息可见性 + 术语 + 用规则推理的要点。
 * 每一次模型调用（白天发言、狼队沟通、投票与夜间技能）都把它放在 system prompt
 * 的最前面；所有玩家完全相同，只有后面的「你的身份」一节因人而异。
 */
export const RULEBOOK = `# 狼人杀规则手册（12 人狼王守卫 · 标准流程）
这是本局唯一有效的规则。每次发言、投票、用技能之前，都要先对照这份手册推理：别人的说法是否符合规则、每个事件在规则下有几种可能、下一步怎样做对你的阵营最有利。违反规则的说法必定是假话，违反规则的打算必定无法执行。

## 一、板子与阵营
- 12 名玩家，座位 1–12 号。身份：狼王 + 3 名普通狼人、4 村民、预言家、女巫、猎人、守卫（后四个统称「神职」）。
- 狼人阵营：狼王和 3 名狼人共 4 狼，互相认识（队友知道谁是狼王）。好人阵营：村民 + 神职，彼此不知道身份。
- 身份全程保密：出局的人不公开身份（只有自爆的狼人会暴露）；GM 公布死亡名单时不说死因。

## 二、胜负条件
- 狼人阵营「屠边」获胜：神职全部出局，或村民全部出局，游戏立即结束。
- 好人阵营：把 4 名狼人（含狼王）全部淘汰（放逐、毒杀、枪杀、自爆都算）才获胜。
- 同一夜双方同时达成条件，判狼人获胜。白天出局是一个一个结算的，谁先达成谁赢。

## 三、角色能力
### 狼人
- 每晚在狼队频道商量，然后每只存活的狼投票选择刀谁：可以刀任何存活玩家（包括队友和自己），也可以空刀；得票过半即为刀口，未过半由系统在被投的选项里随机选一个。
- 白天轮到自己发言（含警上发言、PK 发言）或在警上决定是否退水时，可以自爆：公开狼人身份并立即出局，没有遗言，当天剩下的环节全部取消，直接天黑。
### 狼王
- 狼人阵营的一员，夜里和其他狼人一起商量、投票刀人，白天同样可以自爆；预言家查验狼王结果也是「狼人」。
- 比普通狼人多一把枪：出局时（被放逐、被刀、被猎人带走）可以翻牌开枪带走一名存活玩家，也可以不开；整局一次。
- 被女巫毒死不能开枪；自爆不能开枪。夜里不能开枪，也没有单独的夜间回合（只和狼队一起睁眼刀人）。
### 村民
- 没有技能，靠发言和投票找狼。
### 预言家
- 每晚查验一名存活玩家（优先未查验过的），结果只有「好人」或「狼人」，查不出具体神职。查验为好人叫「金水」，为狼人叫「查杀」。
### 女巫
- 一瓶金水（解药）、一瓶银水（毒药），整局各一次，同一晚只能用一瓶。
- 金水还在时，GM 会告诉女巫今晚狼人刀了谁（刀口）；金水用掉后不再告知。
- 只有第一夜可以用金水救自己；之后被刀不能自救。银水可以毒除自己以外的任何存活玩家。
### 猎人
- 出局时（被刀、被放逐、被带走）可以开枪带走一名存活玩家，也可以不开；整局一次。
- 被女巫毒死不能开枪。夜里不能主动开枪。每晚 GM 只告诉猎人「能不能开枪」，能不能开枪就说明他有没有被毒。
### 守卫
- 每晚守护一名存活玩家（可以守自己，可以空守），被守的人不会被狼刀死；不能连续两晚守同一个人。
- 守护不挡毒药。守卫守中的人如果又被女巫用金水救了（同守同救），这个人仍然死亡。

## 四、夜晚流程（每晚相同，死去的角色也会照常被叫到，不泄露信息）
1. 守卫守人。
2. 狼人商量并投票刀人。
3. 女巫得知刀口（金水还在时），决定救人或毒人（同一晚最多一瓶）。
4. 预言家查验，立刻得知结果。
5. 猎人确认开枪状态。
- 结算：被刀的人，除非「只被守」或「只被救」，否则死亡（没守没救死；同守同救也死）。被毒的人一定死亡。所以一晚最多死两人（一刀一毒）。

## 五、白天流程
1. 天亮。
2. 警长竞选（只在第一天；若被自爆打断，第二天在公布死讯前从退水环节继续）：
   - 此时昨晚的死讯还没公布，昨晚死的人自己也不知道，照常参加竞选。
   - 所有人同时决定是否上警。上警的叫警上玩家，其余是警下玩家。
   - 警上玩家从随机一人开始，按顺时针（号码从小到大，12号接1号）或逆时针依次发言。
   - 发言后警上玩家可以退水（放弃竞选）；退水的人不能当选，也不能投警长票。
   - 只剩一人自动当选；无人上警、全部退水或没有警下玩家时，本局没有警长。
   - 警下玩家投票（可弃票），得票最多者当选；平票者 PK 发言后警下再投一次，再平票则警徽流失（本局没有警长）。
   - 狼人在警上自爆：竞选中断，公布死讯后直接天黑，第二天从退水继续；第二次自爆则警徽流失。
3. 公布昨晚死讯（只有名单）。第一夜的死者有遗言；之后夜里死的人没有遗言。死者若是警长先移交警徽；若是没被毒的猎人或狼王可以开枪，被带走的人有遗言。
4. 发言：有警长时由警长决定从谁开始——昨晚只死一人时从死者左右两侧选一边，平安夜或死两人时从警长左右两侧选一边；警长最后一个发言并「归票」（点出建议放逐的号码）。没有警长时，从死者一侧或随机一人开始。
5. 放逐投票：全体存活玩家投票，可以弃票；警长的一票算 1.5 票；得票最多者出局。全员弃票则无人出局。
6. PK：平票的人依次 PK 发言，然后只由台下玩家（不含 PK 的人）在他们之中再投一次；再平票则今天无人出局。
7. 被放逐者：先移交警徽（如果是警长），再留遗言，是猎人或狼王可开枪。之后天黑。
- 自爆：狼人在自己的白天发言回合（或警上退水时）可以自爆，当天剩余环节全部取消，直接天黑。

## 六、警长
- 放逐投票 1.5 票；决定每天的发言顺序；最后发言并归票。警长的归票常常决定当天放逐谁。
- 警长出局时把警徽移交给一名存活玩家，或撕掉警徽（之后本局没有警长）。自爆的警长警徽直接流失。
- 警徽流：预言家在警上报出「今晚验 X、明晚验 Y」。当上警长后若夜里死亡，就把警徽交给当晚查验的人：查验是好人就交给他（等于公布了金水），是狼人就撕警徽或交给已知好人。所以警徽的去向本身就是信息。

## 七、谁知道什么
- 所有人：公开发言、投票明细、死亡名单（不含死因）、警长是谁、自爆者是狼、开枪者的身份（猎人或狼王开枪时 GM 会公布）。
- 狼人：狼队友是谁（含谁是狼王）、狼队频道、今晚刀口。
- 预言家：自己每次的查验结果（好人 / 狼人）。
- 女巫：金水在时的刀口、自己救了谁毒了谁。
- 守卫：自己每晚守了谁。猎人：自己能不能开枪。
- 这些私人信息只有本人知道；别人说出来的「查验」「用药」「守护」只是声明，可能是假的。

## 八、常用术语
- 起跳 / 跳：公开声称自己的身份。悍跳：狼人冒充神职（最常见是冒充预言家）。对跳：两人都声称是同一个身份，至少一个是假的。
- 金水：被预言家验为好人的人。查杀：被验为狼人的人。银水：被女巫救过的人。
- 站边：相信对跳中的某一方。归票：警长或发言者建议大家集中投某人。
- 刀口：狼人今晚要刀的人。空刀：狼人不刀人。平安夜：昨晚没人死。
- 警上 / 警下、退水、警徽流、撕警徽：见第五、六节。PK：平票者再发言再投。
- 屠边：神职全灭或村民全灭。冲锋：狼人公开帮狼队友说话拉票。倒钩：狼人故意站边真预言家、踩队友来骗取信任。深水：发言很少、难以判断的人。

## 九、用规则推理（每次都要想一遍）
- 平安夜的可能：守卫守中了刀口；女巫用金水救了（第一夜最常见）；狼人空刀。不可能是守卫和女巫同时救了同一人（同守同救会死）。
- 昨晚死两人：一定是一刀一毒（刀口没被救 + 女巫用了银水）；死一人：刀口没被救，或平安刀 + 毒一人。
- 声称「查验出某人是女巫 / 猎人」等具体神职的人在说谎：预言家只能查好坏。
- 同一身份被两人认领，至少一人是假的；狼人最常悍跳预言家，真预言家通常会报查验和警徽流。
- 被毒死的猎人不能开枪；猎人夜里不能开枪。自称猎人却说夜里开过枪，是假的。
- 狼王和猎人一样出局能开枪：放逐一个可能是狼王的人时，要想到他会带走一个好人（常常是预言家或警长）；被毒死或自爆的狼王开不了枪，所以女巫毒狼王最稳。开枪的人是狼王，说明被他带走的人很可能是好人里的关键角色。
- 女巫第一夜之后不能自救；同一晚不会既救又毒。
- 自爆的一定是狼；自爆常用来打断对狼队不利的归票、或阻止预言家拿警徽。
- 警长死后警徽的去向、谁拿了警徽后被刀，都能帮助判断真假预言家。
- 算屠边：数清还可能活着的神职和村民；狼人只需要其中一边清空，好人要守住两边。
- 投票明细是公开的：谁和查杀站在一起、谁在关键时刻弃票，都是线索。
- 出局的人不再发言和投票；昨晚的死者在第一天警长竞选时还不知道自己死了。`;

const ROLE_GUIDE: Record<Role, string> = {
  werewolf:
    '你是狼人。目标是屠边：刀光神职或刀光村民。白天必须伪装成好人，绝不能承认自己是狼，也不要暴露狼队友。可以上警悍跳预言家（编造查验和警徽流）、给真预言家泼脏水、抢警徽、把票引向好人。夜里与队友商量刀谁，优先刀已经起跳或暴露的神职和警长；想清楚守卫可能守谁、女巫还有没有金水。局势对狼队明显不利（被可信的预言家查杀、必然被放逐）时，可以考虑自爆打断。',
  wolfKing:
    '你是狼王，狼人阵营，目标同样是屠边。夜里和队友一起商量刀人；白天必须伪装成好人，绝不能承认自己是狼。你比普通狼人多一把枪：被放逐、被刀或被猎人带走时可以开枪带走一名存活玩家（被毒死或自爆则不能开枪）。所以你可以更激进：悍跳、冲锋、硬顶查杀都行，被票出去也能带走一个关键好人（优先起跳可信的真预言家、女巫、警长，不要打自己的狼队友）。能开枪时一般不要自爆，自爆就浪费了枪。',
  villager:
    '你是村民，没有技能。认真分析每个人的发言逻辑、投票行为和前后矛盾之处，对照规则找出说谎的人。可以上警争夺警徽帮好人归票，也可以在警下投票给你认为是真预言家的人。不要冒充神职挡刀以外的用途，也不要轻易暴露谁是神。',
  seer:
    '你是预言家。第一天一般要上警起跳：报出昨晚的查验（金水或查杀），并留警徽流（今晚验谁、明晚验谁）；当上警长后如果夜里死亡，按警徽流把警徽交给查验出的好人（查到狼则撕警徽或交给金水），让信息传下去。优先查验发言可疑或对跳你的人。提防狼人悍跳冒充你，用查验结果和规则拆穿他。',
  witch:
    '你是女巫。金水在时你知道刀口：第一夜可以自救，之后不能；同一晚只能用一瓶药。救人要考虑被刀的人是否像神职、狼人是否可能自刀骗药；毒人要有把握（优先毒被查杀的人或行为明显的狼）。白天一般不急于暴露身份，必要时可以公开用药信息（如报出银水、毒了谁）来证明自己或指认狼人。',
  hunter:
    '你是猎人。出局时可以开枪带走一人（被毒死不能开枪），夜里不能主动开枪；每晚 GM 告诉你能不能开枪，由此可知你有没有被毒。白天可以视情况表明身份来威慑狼人或挡刀。开枪要尽量打中狼人，不要误伤神职。',
  guard:
    '你是守卫。每晚守护一人免受狼刀（可以守自己，不能连续两晚守同一人）。注意同守同救：你和女巫同时守 / 救同一人，他仍会死亡——第一夜女巫常会救人，所以第一夜守人要谨慎。尽量守护关键好人（起跳的真预言家、警长），白天隐藏身份以免被刀。',
};

export type { Persona } from '../personas';

/**
 * System prompt for every call: the rulebook first (the same for all players),
 * then who you are, what you privately know, your role's strategy and how to speak.
 */
export function systemPrompt(view: PlayerView, persona: Persona): string {
  const self = view.self;
  let teammates = '';
  if (isWolf(self.role)) {
    const mates = Object.entries(view.known)
      .filter(([id, r]) => isWolf(r) && Number(id) !== self.id)
      .map(([id, r]) => `${seat(Number(id))}${r === 'wolfKing' ? '（狼王）' : ''}`);
    teammates = `\n- 你的狼队友：${mates.join('、')}。`;
  }
  return `${RULEBOOK}

# 你的身份
- 你在一个中世纪破败小镇里玩这局狼人杀。你是 ${seat(self.id)}「${persona.name}」，性格：${persona.trait}。
- 你的真实身份：${ROLE_NAME[self.role]}（${isWolf(self.role) ? '狼人阵营' : '好人阵营'}）。${teammates}
- 你能用的能力和能知道的信息，严格以上面规则手册里「${ROLE_NAME[self.role]}」一节和「谁知道什么」为准。

# 身份策略
${ROLE_GUIDE[self.role]}

# 思考与表达要求
- 每次先按规则手册推理：当前处在流程的哪一步、你能做什么；别人的声明是否符合规则；昨晚结果、投票明细在规则下说明了什么；下一步怎么做对你的阵营最有利。推理放在心里，不要写出来。
- 用简体中文口语，性格只影响语气，内容必须是基于场上信息和规则的推理。
- 提到玩家时用「N号」。玩家名字里的行当（铁匠、药师、猎户、骑士、裁缝……）只是小镇里的称呼，和狼人杀身份毫无关系：药师不是女巫，猎户不是猎人。谁是什么身份，只能看 GM 公布的信息和他们自己的声明。
- 发言时只输出你说出口的台词：不要写动作、神态、旁白（禁止 *…*、（…）这类描写），不要输出思考过程或标签。做选择（投票、技能）时按任务要求只输出一行 JSON。`;
}

const SPEECH_TAG: Record<SpeechKind, string> = {
  discussion: '发言', summary: '警长归票', lastWords: '遗言', defense: '放逐PK发言', campaign: '警上发言', campaignPk: '警长PK发言',
};

/** 警长竞选 has its own shared record (it may run over two days). */
export const isElection = (e: GameEvent) => e.phase === 'election';

function fmtEvent(e: GameEvent, view: PlayerView): string | null {
  const d = `第${e.day}天`;
  switch (e.type) {
    case 'speech': {
      // speaker on its own header line and the words fenced in 「」: with a one-line
      // "7号卡尔：…6号你承认刀了4号" small models credit the seats inside the text as the speaker
      const tag = SPEECH_TAG[e.speechKind ?? 'discussion'];
      const name = view.players[e.speaker!]?.name;
      return `[${d} ${tag}] 发言人：${seat(e.speaker!)}${name ? `（${name}）` : ''}\n「${e.text}」`;
    }
    case 'gm':
    case 'vote':
      return `[${d} GM] ${e.text}`;
    default:
      return null;
  }
}

/**
 * 共享发言记录本：所有人都能看到的公开内容。`before` / `onlyDay` 按天筛选；
 * 警长竞选的内容默认不在里面（单独一本，`election: true` 只取竞选）。
 */
export function sharedNotebook(view: PlayerView, opts: { before?: number; onlyDay?: number; maxChars?: number; election?: boolean } = {}): string {
  const maxChars = opts.maxChars ?? 20000;
  const lines = view.events
    .filter((e) => e.visibility.kind === 'public')
    .filter((e) => isElection(e) === !!opts.election)
    .filter((e) => (opts.before === undefined || e.day < opts.before) && (opts.onlyDay === undefined || e.day === opts.onlyDay))
    .map((e) => fmtEvent(e, view))
    .filter((x): x is string => !!x);
  let text = lines.join('\n');
  if (text.length > maxChars) text = '…（更早的记录已省略）\n' + text.slice(-maxChars);
  return text || '（暂无）';
}

/** Speeches already made today (the election's on the election stage, the rest otherwise), for the "respond to others" instruction. */
export function todaysSpeakers(view: PlayerView, election = false): number[] {
  const ids = view.events
    .filter((e) => e.type === 'speech' && e.day === view.day && e.speaker !== undefined && e.speaker !== view.self.id && isElection(e) === election)
    .map((e) => e.speaker!);
  return [...new Set(ids)];
}

/** Tonight's wolf-chat lines from teammates after this wolf last spoke, passes left out. */
export function teammatesSinceMe(view: PlayerView, day: number) {
  const tonight = view.events.filter((e) => e.type === 'wolfChat' && e.day === day && e.speaker !== undefined);
  let mine = -1;
  tonight.forEach((e, i) => e.speaker === view.self.id && (mine = i));
  return tonight.slice(mine + 1).filter((e) => e.text !== '（没有补充）');
}

/** 角色私本：只属于自己的信息 + 自己记下的心得。 */
export function privateNotebook(view: PlayerView, notes: string[]): string {
  const lines: string[] = [];
  for (const e of view.events) {
    if (e.visibility.kind !== 'private') continue;
    if (e.type === 'wolfChat') {
      lines.push(e.speaker !== undefined ? `[第${e.day}夜 狼队] ${seat(e.speaker)}：${e.text}` : `[第${e.day}夜 狼队] ${e.text}`);
    } else if (e.type === 'private') {
      lines.push(`[第${e.day}夜] ${e.text}`);
    }
  }
  if (view.witch) lines.push(`（药品状态：金水${view.witch.hasAntidote ? '未用' : '已用'}，银水${view.witch.hasPoison ? '未用' : '已用'}）`);
  if (view.hunter) lines.push(`（猎枪：${view.hunter.hasShot ? '已用掉' : '还在'}）`);
  if (view.wolfKing) lines.push(`（狼王的枪：${view.wolfKing.hasShot ? '已用掉' : '还在'}）`);
  if (view.guard) lines.push(`（上一晚守护：${view.guard.lastGuarded === null ? '无' : seat(view.guard.lastGuarded)}，今晚不能再守同一人）`);
  if (view.self.role === 'seer') {
    const checks = Object.entries(view.known).filter(([id]) => Number(id) !== view.self.id);
    lines.push(`（已知查验：${checks.map(([id, t]) => `${seat(Number(id))}=${t === 'wolf' ? '狼人（查杀）' : '好人（金水）'}`).join('，') || '无'}）`);
  }
  for (const n of notes) lines.push(`[心得] ${n}`);
  return lines.join('\n') || '（暂无）';
}

function aliveList(view: PlayerView) {
  const alive = view.players.filter((p) => p.alive).map((p) => seat(p.id));
  const dead = view.players.filter((p) => !p.alive).map((p) => seat(p.id));
  const sheriff = view.sheriff != null ? `；警长：${seat(view.sheriff)}${view.sheriff === view.self.id ? '（你）' : ''}` : '';
  return `存活：${alive.join('、')}${dead.length ? `；已出局：${dead.join('、')}（已出局的人不再参与投票，不能再当作怀疑或放逐对象）` : ''}${sheriff}`;
}

export function speechTask(req: SpeechRequest | WolfChatRequest, view: PlayerView): string {
  if (req.kind === 'wolfChat') {
    const fresh = teammatesSinceMe(view, req.day);
    const heard = fresh.length
      ? `\n${req.round === 1 ? '在你之前' : '自你上次发言后'}，队友说了：\n${fresh.map((e) => `- ${seat(e.speaker!)}：${e.text}`).join('\n')}`
      : '';
    const answer = '如果有人反对当前刀口或打法、或提出了新方案，你不能 pass：点名回应，正面回答对方的理由（要么被说服并说出改成什么，要么讲清为什么坚持）。';
    const task =
      req.round === 1
        ? fresh.length
          ? `先回应在你之前发言的队友，点名说同意还是反对、为什么。${answer}然后说出你的刀口和明天白天的打法。60 字以内。`
          : '和队友商量今晚刀谁、明天白天怎么打配合（谁悍跳、怎么站边）。60 字以内，直接说内容。'
        : fresh.length
          ? `${answer}如果他们只是附和、没有新分歧，只回复：pass。40 字以内。`
          : '如果刀口和打法已经一致，只回复：pass。';
    return `现在是第 ${req.day} 夜，狼队秘密频道第 ${req.round}/${req.rounds} 轮沟通（只有狼人能看到）。
${aliveList(view)}${heard}
${task}
这是和队友说话，不是投票：用口语直接说出你的话，不要输出 JSON 或 {"vote"/"target"…} 之类的格式。刀人投票在沟通结束后单独进行。`;
  }
  const what = {
    discussion: '现在轮到你白天发言。分析局势，给出你的怀疑对象和理由，也可以根据策略表明（或伪装）身份。',
    summary: '你是警长，所有人都已发言，你最后一个发言：梳理大家的站边与矛盾，明确归票——点出你建议大家放逐的号码（你的票算 1.5 票）。',
    lastWords: '你出局了，这是你的遗言。可以公开身份、留下信息（如查验、用药、警徽流）或指认你认为的狼人。',
    defense: '你在放逐投票中平票了，现在是 PK 发言：为自己辩护，说服台下玩家不要投你。',
    campaign: `你上警竞选警长，现在是警上发言：说明你为什么要当警长、你的身份信息和对局势的判断，争取警下玩家的票。昨晚的死讯还没有公布，不要编造谁死了。${view.self.role === 'seer' ? '你是预言家：一般在这里起跳，报出昨晚的查验结果，并留下警徽流（今晚验几号、明晚验几号）。' : ''}`,
    campaignPk: '你在警长投票中平票，现在是警长 PK 发言：说服警下玩家把警徽投给你。',
  }[req.purpose];
  const onStage = req.purpose === 'campaign' || req.purpose === 'campaignPk';
  const book = onStage ? '【警长竞选记录】' : '【今天的发言】';
  const prior = todaysSpeakers(view, onStage);
  const respond = prior.length
    ? `今天在你之前已有 ${prior.map(seat).join('、')} 发言（见上方${book}${onStage ? '' : '，警上的发言见【警长竞选记录】'}）。你必须具体回应其中至少两人：点名并引用或概括他们说过的内容，说明你同意/反对的理由；同时结合${onStage ? '' : '昨夜的死亡情况、'}身份声明（例如谁跳了预言家、报了什么查验和警徽流）和之前的投票。不要说泛泛的「XX发言奇怪」而不给出依据。
【引用自检】说「N号说了/承认了/跳了……」之前，先确认这句话确实在「发言人：N号」那一段的「」里。发言内容里提到的号码是被谈论或被质问的人，不是说话人：例如 7号说「6号你承认刀了4号？」，承认的是 6号，不是 7号。记不清是谁说的就不要点名引用。`
    : onStage
      ? '你是第一个警上发言的人，不能拿「没发言」怀疑任何人，也不要编造别人说过的话。'
      : '你是今天第一个发言的人，其他人都还没轮到，不能拿「没发言」怀疑任何人。结合昨夜结果、警长竞选和之前几天的记录（如果有）开个头，给出你的初步判断，不要编造别人说过的话。';
  const secret =
    isWolf(view.self.role)
      ? '\n【保密】狼队频道的内容（刀了谁、狼队讨论过什么、谁是你的队友）只有狼人知道，白天绝不能说出口，也不能说漏嘴。昨夜的公开结果只有 GM 宣布的死亡名单。'
      : '\n【保密】私人记录本里的信息别人不知道；除非你有意公开身份（如报查验、报用药），不要把它当成大家都知道的事来说。';
  const progress = speechProgress(req, view);
  const explode = req.canExplode
    ? `\n【自爆选项】你是狼人，可以选择自爆：在发言最开头写「${EXPLODE_TAG}」，后面接你的最后一句话。自爆后你立刻出局，今天剩下的环节全部取消，直接天黑（在警上自爆会让警长竞选推迟到明天）。代价很大（白送一狼），只在局势对狼队明显不利时用：例如你被可信的预言家查杀、今天必然被放逐，自爆能打断好人归票、保住队友或让真预言家来不及报查验。局势正常就不要自爆。`
    : '';
  return `现在是第 ${req.day} 天（第 ${req.day} 轮白天）。${aliveList(view)}
${progress ? `${progress}\n` : ''}${what}
${respond}${req.purpose === 'lastWords' ? '' : secret}${explode}
200 字以内，直接输出发言内容。`;
}

/**
 * 本轮发言进度: the full speaking order with who has spoken, the speaker's own
 * position, and how many are still to come — so the AI can pace its speech
 * (open with a view early, wrap up and push a vote late).
 */
export function speechProgress(req: SpeechRequest, view: PlayerView): string {
  const order = req.order;
  if (!order?.length) return '';
  const me = view.self.id;
  const spoken = new Set(req.spoken ?? []);
  const alive = new Set(view.players.filter((p) => p.alive).map((p) => p.id));
  const tag = (id: number) => {
    if (id === me) return `${seat(id)}（你）`;
    if (spoken.has(id)) return `${seat(id)}（已发言）`;
    if (!alive.has(id)) return `${seat(id)}（已出局）`;
    return `${seat(id)}（未发言）`;
  };
  const line = order.map(tag).join(' → ');
  const total = order.filter((id) => alive.has(id) || spoken.has(id)).length;
  const done = order.filter((id) => spoken.has(id)).length;

  if (req.purpose === 'discussion') {
    // Split instead of one arrow chain tagged 「未发言」: models read that as
    // "stayed silent" and blamed seats whose turn had not come yet.
    const before = order.filter((id) => spoken.has(id));
    const after = order.slice(order.indexOf(me) + 1).filter((id) => alive.has(id) && !spoken.has(id));
    const pos = done + 1;
    const left = after.length;
    const n = view.players.length;
    const dir =
      req.clockwise === undefined ? '' : req.clockwise ? `顺时针（号码从小到大，${n}号之后接1号）` : `逆时针（号码从大到小，1号之后接${n}号）`;
    const opener = req.first !== undefined ? `由 ${seat(req.first)} 开始${dir}发言。` : '';
    const tip =
      left === 0
        ? '你是本轮最后一个发言的人：回应前面所有人的关键观点，给出明确的放逐建议。'
        : pos <= 2
          ? '你发言较早：先亮出你的判断和怀疑对象，同时说明你想听后面哪些人怎么接。'
          : '';
    const sheriff = view.sheriff != null && alive.has(view.sheriff) ? view.sheriff : null;
    const summary = sheriff !== null ? `全部发言结束后，由警长 ${seat(sheriff)} 最后发言并归票，然后放逐投票。` : '全部发言结束后直接放逐投票。';
    const waiting = left
      ? `还没轮到（按发言顺序）：${after.map(seat).join(' → ')}，共 ${left} 人。他们只是还没轮到，不是沉默：不要说他们「没声音」「一直不说话」「在躲」，也不要评价他们的发言。`
      : '在你之后没有人了。';
    return `【本轮发言进度】${opener}
已发言（按顺序）：${before.length ? before.map(seat).join(' → ') : '无，你是第一个'}
轮到你：${seat(me)}，第 ${pos} 位（共 ${total} 人）
${waiting}
${summary}${tip ? `\n${tip}` : ''}`;
  }
  if (req.purpose === 'summary') {
    return `【本轮发言进度】${line}\n其他人都已发言完毕，你作为警长最后发言并归票，之后立即投票。`;
  }
  if (req.purpose === 'campaign') {
    const left = order.slice(order.indexOf(me) + 1).length;
    return `【警上发言进度】上警玩家：${order.map(seat).join('、')}。${line}\n${left ? `你之后还有 ${left} 位警上玩家发言。` : '你是最后一位警上发言的人。'}全部说完后警上玩家可以退水，然后由警下玩家投票选警长。`;
  }
  if (req.purpose === 'defense' || req.purpose === 'campaignPk') {
    const left = total - done - 1;
    const next = req.purpose === 'defense' ? '台下玩家在 PK 玩家中再投一次，再平票则今天无人出局' : '警下玩家在 PK 玩家中再投一次，再平票则警徽流失';
    return `【PK 发言进度】PK 玩家：${order.map(seat).join('、')}。${line}\n${left > 0 ? `你说完后还有 ${left} 位 PK 玩家发言，然后${next}。` : `你是最后一位 PK 发言的，说完后${next}。`}`;
  }
  return '';
}

const ACTION_TEXT: Record<TargetRequest['action'], string> = {
  vote: '放逐投票：选择要放逐的玩家。',
  revote: 'PK 再投：只能在 PK 玩家中选择。',
  seer: '夜晚查验：选择一名玩家查验（结果为好人或狼人）。',
  guard: '夜晚守护：选择一名玩家守护，使其免于狼刀（可以守自己）。',
  wolfKill: '狼队投票：选择今晚要杀的玩家。',
  hunterShot: '猎人开枪：选择要带走的玩家，或不开枪。',
  wolfKingShot: '狼王开枪：你是狼王，已出局。选择要带走的玩家（优先带走对狼队威胁最大的好人，不要打狼队友），或不开枪。',
  witchSave: '女巫救人：选择是否用金水救下今晚被狼人杀害的玩家。',
  witchPoison: '女巫用毒：选择是否用银水毒杀一名玩家。',
  runForSheriff: '警长竞选：决定是否上警。',
  withdraw: '警长竞选：决定是否退水。',
  sheriffVote: '警长投票：在警上玩家中选出警长。',
  sheriffRevote: '警长 PK 再投：只能在 PK 玩家中选择。',
  badge: '移交警徽：你是警长，现在出局了。选择继承警徽的玩家（按你的警徽流 / 最信任的好人），或撕掉警徽。',
  speakOrder: '警长决定发言顺序：选择从哪一位开始发言。',
};

const NIGHT_ACTIONS: TargetRequest['action'][] = ['seer', 'guard', 'wolfKill', 'witchSave', 'witchPoison'];

export function targetTask(req: TargetRequest, view: PlayerView): string {
  const head = `现在是第 ${req.day} ${NIGHT_ACTIONS.includes(req.action) ? '夜' : '天'}。${aliveList(view)}
${ACTION_TEXT[req.action]}
GM：${req.prompt}`;
  if (YES_NO_ACTIONS.includes(req.action)) {
    const yes = req.action === 'runForSheriff' ? '上警' : '退水';
    const no = req.action === 'runForSheriff' ? '不上警' : '继续竞选';
    const explode = req.canExplode ? `；你是狼人，也可以选择现在自爆（立刻出局，竞选推迟到明天，今天直接天黑），自爆填 ${EXPLODE_CHOICE}` : '';
    return `${head}
${yes}填你的号码 ${view.self.id + 1}，${no}填 0${explode}。
只输出一行 JSON：{"target": 号码, "reason": "20字以内的理由"}`;
  }
  const cands = req.candidates.map((c) => `${c + 1}`).join('、');
  const skip = req.allowSkip ? `；${req.action === 'badge' ? '撕掉警徽' : req.action === 'wolfKill' ? '空刀' : '不选择'}请填 0` : '';
  return `${head}
可选号码：${cands}${skip}。
只输出一行 JSON：{"target": 号码, "reason": "20字以内的理由"}`;
}

/** Extract a seat choice from the model output; returns id (0-based), null for skip, or undefined if unparseable. */
export function parseTarget(text: string, req: TargetRequest): { target: number | null | undefined; reason: string } {
  const valid = new Set(req.candidates.map((c) => c + 1));
  let reason = '';
  const m = text.match(/\{[\s\S]*?\}/);
  if (m) {
    try {
      const obj = JSON.parse(m[0]);
      reason = String(obj.reason ?? '');
      const n = Number(obj.target);
      if (n === EXPLODE_CHOICE && req.canExplode) return { target: EXPLODE_CHOICE, reason };
      if (n === 0 && req.allowSkip) return { target: null, reason };
      if (valid.has(n)) return { target: n - 1, reason };
    } catch {
      /* fall through */
    }
  }
  const t = text.match(/"?target"?\s*[:：]\s*"?(\d{1,2})/);
  if (t) {
    const n = Number(t[1]);
    if (n === 0 && req.allowSkip) return { target: null, reason };
    if (valid.has(n)) return { target: n - 1, reason };
  }
  if (req.allowSkip && /不(开枪|救|毒|选|上警|退水)|放弃|弃票|空守|空刀|撕(掉|毁)?警徽|继续竞选/.test(text)) return { target: null, reason };
  for (const n of text.match(/\d{1,2}/g) ?? []) {
    if (valid.has(Number(n))) return { target: Number(n) - 1, reason };
  }
  return { target: undefined, reason };
}

export const EXPLODE_TAG = '【自爆】';

/** Split a leading 【自爆】 marker off a day speech. */
export function parseExplode(text: string): { text: string; explode: boolean } {
  const m = text.match(/^\s*[【\[]\s*自爆\s*[】\]]\s*/);
  return m ? { text: text.slice(m[0].length), explode: true } : { text, explode: false };
}

/** Keys a model uses for the spoken line when it wraps a speech in JSON anyway. */
const SPOKEN_KEYS = ['speech', 'text', 'content', 'say', 'message', 'msg', '发言', '台词'];
const PICK_KEYS = ['target', 'vote', 'kill', '刀口', '目标'];

/**
 * A model that answers a speech / wolf-chat turn with a decision-style JSON
 * ({"vote": "12号", "reason": "…"}), sometimes in a ```json fence: turn it back
 * into a plain line instead of showing raw JSON in the chat.
 */
export function unwrapJsonSpeech(text: string): string {
  const s = text.trim().replace(/^```(?:json)?\s*|\s*```$/g, '').trim();
  // also catch output cut off by the token cap before the closing brace
  if (!/^\{\s*"/.test(s)) return text;
  let obj: Record<string, unknown>;
  try {
    obj = JSON.parse(s);
  } catch {
    // not valid JSON: salvage the quoted string values
    const vals = [...s.matchAll(/"[^"]*"\s*[:：]\s*"([^"]*)(?:"|$)/g)].map((m) => m[1].trim()).filter(Boolean);
    return vals.length ? vals.join('。') : text;
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return text;
  const str = (k: string) => (obj[k] == null ? '' : String(obj[k]).trim());
  const spoken = SPOKEN_KEYS.map(str).find(Boolean);
  if (spoken) return spoken;
  const reason = str('reason') || str('理由');
  const pick = PICK_KEYS.map(str).find(Boolean);
  if (pick) {
    const n = pick.match(/\d{1,2}/)?.[0];
    const said = n ? `${n}号` : pick;
    if (!reason) return /^0$/.test(pick) ? '过' : `我选${said}。`;
    return n && reason.includes(n) ? reason : `我选${said}。${reason}`;
  }
  return reason || Object.values(obj).filter((v) => typeof v === 'string').join('。') || text;
}

/** Trim quotes / "3号艾德：" prefixes a model sometimes adds to speeches. */
export function cleanSpeech(text: string, view: { self: { id: number } }, personaName: string): string {
  let s = unwrapJsonSpeech(text).trim();
  const head = s.slice(0, 20);
  const colon = head.search(/[：:]/);
  if (colon > 0 && (head.slice(0, colon).includes(seat(view.self.id)) || head.slice(0, colon).includes(personaName))) {
    s = s.slice(colon + 1).trim();
  }
  // stage directions the model sometimes adds: *打了个嗝*, （眯着眼）
  s = s.replace(/\*[^*\n]{0,80}\*/g, '').replace(/^[（(][^）)\n]{0,40}[）)]\s*/, '').trim();
  // a self-reported length echoing the prompt's word cap: （约190字）
  s = s.replace(/\s*[（(【\[]\s*(?:约|共|全文|字数[:：]?)?\s*\d+\s*字\s*(?:左右)?\s*[）)】\]]$/, '').trim();
  s = s.replace(/^["“「]+|["”」]+$/g, '');
  return s.slice(0, 400);
}
