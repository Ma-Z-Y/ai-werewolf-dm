import { ROLE_NAME, seat, type Phase } from '../game/types';
import type { SaveGame } from '../save';
import { h } from './dom';
import { formatClock } from './hud';

export const PHASE_NAME: Record<Phase, (day: number) => string> = {
  setup: () => '开局',
  night: (d) => `第 ${d} 夜`,
  dawn: (d) => `第 ${d} 天 · 天亮`,
  election: (d) => `第 ${d} 天 · 警长竞选`,
  discussion: (d) => `第 ${d} 天 · 发言`,
  vote: (d) => `第 ${d} 天 · 投票`,
  lastWords: (d) => `第 ${d} 天 · 遗言`,
  ended: () => '已结束',
};

function savedAt(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, '0');
  const today = new Date().toDateString() === d.toDateString();
  return `${today ? '今天' : `${d.getMonth() + 1}月${d.getDate()}日`} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Title screen over the idle town. Resolves with the player's choice. */
export function showTitle(
  root: HTMLElement,
  save: SaveGame | null,
  actions: { onRules: () => void; onConfig: () => void; onDelete: () => void },
): Promise<'new' | 'load' | 'quick'> {
  return new Promise((resolve) => {
    const choose = (c: 'new' | 'load' | 'quick') => {
      if (c !== 'load' && save && !confirm('开始新游戏会覆盖当前的自动存档，继续？')) return;
      back.remove();
      document.removeEventListener('keydown', onKey);
      resolve(c);
    };
    const loadBtn = h('button', { class: 'title-opt', disabled: !save, onclick: () => choose('load') }, save?.auto ? '继续上次对局' : '载入存档');
    const info = save
      ? h(
          'div',
          { class: 'save-info' },
          h('div', {}, `${save.prefs.playerName} · ${seat(save.meta.seat)} ${ROLE_NAME[save.meta.role]} · ${PHASE_NAME[save.meta.phase](save.meta.day)}${save.auto ? ' · 自动保存' : ''}`),
          h('div', { class: 'dim' }, `存活 ${save.meta.alive}/12 · 用时 ${formatClock(save.elapsedMs)} · 保存于 ${savedAt(save.savedAt)}`),
          h(
            'button',
            {
              class: 'link',
              onclick: () => {
                if (!confirm('删除这个存档？删除后无法恢复。')) return;
                actions.onDelete();
                info?.remove();
                loadBtn.disabled = true;
              },
            },
            '删除存档',
          ),
        )
      : h('div', { class: 'save-info dim' }, '没有保存的游戏');
    const newBtn = h('button', { class: 'title-opt', onclick: () => choose('new') }, '新游戏');
    const quickBtn = h('button', { class: 'title-opt quick', onclick: () => choose('quick') }, '一键离线开局');
    const back = h(
      'div',
      { id: 'title' },
      h(
        'div',
        { class: 'title-card' },
        h('div', { class: 'kicker' }, 'A  WEREWOLF  TALE'),
        h('h1', {}, '雾镇狼人夜'),
        h('div', { class: 'tagline' }, '12 人局 · 你与 11 位 AI 镇民 · 天黑请闭眼'),
        h('div', { class: 'title-note' }, '离线规则 AI 无需 API Key，打开即可玩'),
        h(
          'nav',
          { class: 'title-menu' },
          newBtn,
          quickBtn,
          loadBtn,
          info,
          h('button', { class: 'title-opt', onclick: actions.onConfig }, '配置'),
          h('button', { class: 'title-opt minor', onclick: actions.onRules }, '规则说明'),
        ),
      ),
    );
    // arrow keys move between the options
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      const opts = [...back.querySelectorAll<HTMLButtonElement>('.title-opt:not(:disabled)')];
      const i = opts.indexOf(document.activeElement as HTMLButtonElement);
      opts[(i + (e.key === 'ArrowDown' ? 1 : opts.length - 1)) % opts.length]?.focus();
      e.preventDefault();
    };
    document.addEventListener('keydown', onKey);
    root.appendChild(back);
    (save ? loadBtn : quickBtn).focus();
  });
}
