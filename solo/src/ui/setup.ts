import { ROLE_NAME, type Role } from '../game/types';
import { PROVIDERS } from '../ai/catalog';
import { getConfig, loadGamePrefs, resolveProvider, saveGamePrefs, type Settings } from '../settings';
import { showConfig } from './config';
import { h } from './dom';

/**
 * New-game screen: the choices for this game only (sound, pace and the AI
 * engine live in 配置). Resolves with the settings, or null for 返回标题.
 */
export function showSetup(root: HTMLElement, onRules: () => void): Promise<Settings | null> {
  const s = loadGamePrefs();
  return new Promise((resolve) => {
    const input = (value: string, type = 'text') => h('input', { type, value }) as HTMLInputElement;
    const name = input(s.playerName);
    const role = h('select', {}, h('option', { value: 'random' }, '随机（标准玩法）'), ...(Object.keys(ROLE_NAME) as Role[]).map((r) => h('option', { value: r, selected: s.role === r }, `${ROLE_NAME[r]}（调试）`))) as HTMLSelectElement;
    const wolfRounds = input(String(s.wolfChatRounds), 'number');
    wolfRounds.min = '1';
    wolfRounds.max = '5';
    const god = h('input', { type: 'checkbox', checked: s.godView }) as HTMLInputElement;

    const engine = h('span', {});
    const renderEngine = () => {
      const c = getConfig();
      const p = resolveProvider(c);
      engine.textContent = c.mode === 'offline' ? '离线规则 AI（非 LLM）' : `${PROVIDERS[p.provider].label} · ${p.model || '未设置模型'}${p.reasoning ? ` · 推理 ${p.reasoning}/${p.decisionReasoning}` : ''}`;
    };
    renderEngine();
    const configBtn = h('button', { class: 'link', type: 'button', onclick: async () => { await showConfig(root, { inGame: null }); renderEngine(); } }, '修改配置');

    const start = h('button', { class: 'btn primary', type: 'submit' }, '开始游戏');
    const rulesBtn = h('button', { class: 'btn', type: 'button', onclick: onRules }, '规则说明');
    const backBtn = h('button', { class: 'btn', type: 'button', style: 'margin-right:auto', onclick: () => { back.remove(); resolve(null); } }, '返回标题');

    const form = h(
      'form',
      { class: 'modal panel' },
      h('h1', {}, '新游戏'),
      h('div', { class: 'sub' }, '12 人局 · 你与 11 位 AI 镇民 · 天黑请闭眼'),
      h('h2', {}, '对局'),
      h(
        'div',
        { class: 'form' },
        h('label', {}, '你的名字'), name,
        h('label', {}, '身份'), role,
        h('label', {}, '狼队沟通轮数'), wolfRounds,
        h('label', {}, '上帝视角'), h('label', { class: 'check' }, god, '显示所有身份与夜间信息（调试用，会剧透）'),
        h('label', {}, 'AI 引擎'), h('div', { class: 'inline' }, engine, configBtn),
      ),
      h('div', { class: 'actions' }, backBtn, rulesBtn, start),
    ) as HTMLFormElement;
    form.onsubmit = (e) => {
      e.preventDefault();
      const prefs = {
        playerName: name.value.trim() || '旅人',
        role: role.value as Settings['role'],
        wolfChatRounds: Math.max(1, Math.min(5, Number(wolfRounds.value) || 3)),
        godView: god.checked,
      };
      saveGamePrefs(prefs);
      const { mode, paceMs } = getConfig();
      back.remove();
      resolve({ ...prefs, mode, paceMs, provider: resolveProvider() });
    };
    const back = h('div', { class: 'modal-back' }, form);
    root.appendChild(back);
  });
}
