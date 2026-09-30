import { PROVIDERS, PROVIDER_IDS, effortsFor, providerAvailable } from '../ai/catalog';
import { OpenAICompatibleProvider, PROXY_AVAILABLE } from '../ai/provider';
import { envProvider } from '../config';
import { vault } from '../keyVault';
import { getConfig, localDefaults, onConfigChange, resolveProvider, setActiveProvider, updateConfig, updateProfile } from '../settings';
import { h } from './dom';
import { promptChangePassphrase, promptSetKey, promptUnlock } from './keyDialogs';

const PACES: [number, string][] = [
  [300, '快'],
  [900, '标准'],
  [1800, '慢'],
];

const EFFORT_LABEL: Record<string, string> = { none: 'none（不思考）' };

const hintText = (text: string) => h('span', { class: 'sub', style: 'font-size:12px' }, text);

/**
 * 配置 panel, from the title screen or the pause menu. Every change applies at
 * once. In a game the driver mode is locked (the table is already seated), but
 * the provider can still be switched — the next AI call uses it.
 */
export function showConfig(root: HTMLElement, opts: { inGame: 'llm' | 'offline' | null }): Promise<void> {
  return new Promise((resolve) => {
    const c = getConfig();

    // ── sound ──
    const muted = h('input', { type: 'checkbox', checked: c.audio.muted }) as HTMLInputElement;
    muted.onchange = () => updateConfig({ audio: { muted: muted.checked } });
    const slider = (key: 'music' | 'sfx' | 'ambience') => {
      const out = h('span', { class: 'val' }, `${Math.round(c.audio[key] * 100)}`);
      const r = h('input', { type: 'range', min: '0', max: '100', step: '5', value: String(Math.round(c.audio[key] * 100)), 'aria-label': key }) as HTMLInputElement;
      r.oninput = () => {
        out.textContent = r.value;
        updateConfig({ audio: { [key]: Number(r.value) / 100 } });
      };
      return h('div', { class: 'range' }, r, out);
    };

    // ── pace ──
    const pace = h('div', { class: 'seg', role: 'radiogroup' });
    const renderPace = () =>
      pace.replaceChildren(
        ...PACES.map(([ms, label]) =>
          h('button', { type: 'button', role: 'radio', 'aria-checked': String(getConfig().paceMs === ms), class: `btn ${getConfig().paceMs === ms ? 'on' : ''}`, onclick: () => { updateConfig({ paceMs: ms }); renderPace(); } }, label),
        ),
      );
    renderPace();
    const stepSpeech = h('input', { type: 'checkbox', checked: getConfig().stepSpeech }) as HTMLInputElement;
    stepSpeech.onchange = () => updateConfig({ stepSpeech: stepSpeech.checked });

    // ── AI engine ──
    // in a game, show the mode the table was seated with
    const shownMode = () => opts.inGame ?? getConfig().mode;
    const mode = h('select', { disabled: !!opts.inGame, 'aria-label': '驱动方式' }, h('option', { value: 'llm', selected: shownMode() === 'llm' }, 'LLM 驱动（OpenAI 兼容接口）'), h('option', { value: 'offline', selected: shownMode() === 'offline' }, '离线规则 AI（无需模型，调试用）')) as HTMLSelectElement;
    mode.onchange = () => updateConfig({ mode: mode.value as 'llm' | 'offline' });
    const engine = h('div', { class: 'engine-cfg' });
    const result = h('div', { class: 'test-result', 'aria-live': 'polite' });

    const renderEngine = () => {
      if (shownMode() !== 'llm') return engine.replaceChildren();
      const cfg = getConfig();
      const id = cfg.llm.active;
      const preset = PROVIDERS[id];
      const p = cfg.llm.profiles[id];
      const hints = vault.hints();

      // provider tabs: the chosen one drives the AI; every provider keeps its own settings
      const tabs = h(
        'div',
        { class: 'seg providers', role: 'radiogroup', 'aria-label': '服务商' },
        ...PROVIDER_IDS.map((pid) =>
          h(
            'button',
            {
              type: 'button',
              role: 'radio',
              'aria-checked': String(pid === id),
              class: `btn ${pid === id ? 'on' : ''}`,
              disabled: !providerAvailable(pid),
              title: providerAvailable(pid) ? null : '网页版无法访问本机模型服务，请在本地运行游戏后使用',
              onclick: () => { result.textContent = ''; setActiveProvider(pid); },
            },
            PROVIDERS[pid].label,
            PROVIDERS[pid].needsKey ? h('span', { class: `dot ${hints[pid] ? 'ok' : ''}`, title: hints[pid] ? '已保存 Key' : '未设置 Key' }) : null,
          ),
        ),
      );

      // address: the official one, or the local server's from .env (not editable here)
      const envCfg = envProvider();
      const address = preset.fromEnv
        ? h('div', { class: 'stack' }, h('div', { class: 'fixed-url' }, p.baseUrl || '（未配置）'), hintText('来自 .env 的 LLM_BASE_URL（含端口），游戏里不能改；改 .env 后刷新页面'))
        : h('div', { class: 'fixed-url' }, p.baseUrl, hintText(' 官方地址'));

      // model: pick from a fixed list — the official one, or .env LLM_MODELS for the local server
      let model: HTMLElement;
      if (preset.fromEnv) {
        if (!envCfg.models.length) model = h('p', { class: 'test-result err', style: 'margin:0' }, '.env 里没有配置模型：在 LLM_MODELS 填模型 id（多个用逗号分隔），然后刷新页面');
        else {
          const sel = h('select', { 'aria-label': '模型' }, ...envCfg.models.map((m, i) => h('option', { value: m, selected: m === p.model }, i === 0 ? `${m}（默认）` : m))) as HTMLSelectElement;
          sel.onchange = () => { result.textContent = ''; updateProfile(id, { model: sel.value }); };
          model = h('div', { class: 'stack' }, sel, hintText('列表来自 .env 的 LLM_MODELS（逗号分隔，第一个为默认）；要增删模型请改 .env 后刷新页面'));
        }
      } else {
        const sel = h('select', { 'aria-label': '模型' }, ...preset.models.map((m) => h('option', { value: m.id, selected: m.id === p.model }, `${m.id} — ${m.note}`))) as HTMLSelectElement;
        sel.onchange = () => updateProfile(id, { model: sel.value });
        model = h('div', { class: 'stack' }, sel, hintText('价格：每百万 token 输入 / 输出（美元）'));
      }

      // reasoning: only the values this model officially accepts
      const efforts = effortsFor(id, p.model);
      const effortSel = (field: 'reasoning' | 'decisionReasoning', label: string) => {
        if (!efforts.length) return hintText('非推理模型，不发送推理参数');
        const sel = h('select', { 'aria-label': label }, ...efforts.map((e) => h('option', { value: e, selected: e === p[field] }, EFFORT_LABEL[e] ?? e))) as HTMLSelectElement;
        sel.onchange = () => updateProfile(id, { [field]: sel.value });
        return sel;
      };
      const spec = preset.models.find((m) => m.id === p.model);

      // key: stored encrypted; only its last 4 characters are ever shown
      const hint = hints[id];
      const keyRow = h(
        'div',
        { class: 'inline key-row' },
        hint
          ? h('span', { class: 'key-saved' }, `已加密保存 ${hint}`)
          : h('span', { class: 'sub' }, preset.needsKey ? '未设置' : envCfg.keyOnServer ? '使用 .env 的 LLM_API_KEY（开发服务器注入）' : '可选，本地服务一般不需要'),
        h('button', { class: 'btn', type: 'button', onclick: () => void promptSetKey(root, id) }, hint ? '替换' : '设置'),
        hint ? h('button', { class: 'btn danger', type: 'button', onclick: () => confirm(`删除已保存的 ${preset.label} API Key？`) && vault.removeKey(id) }, '删除') : null,
      );

      const proxy = h('input', { type: 'checkbox', checked: p.useProxy }) as HTMLInputElement;
      proxy.onchange = () => updateProfile(id, { useProxy: proxy.checked });

      const testBtn = h('button', { class: 'btn', type: 'button' }, '测试连接') as HTMLButtonElement;
      testBtn.onclick = () => void test(testBtn);

      engine.replaceChildren(
        h(
          'div',
          { class: 'form', style: 'margin-top:8px' },
          h('label', {}, '服务商'),
          providerAvailable('local')
            ? tabs
            : h('div', { class: 'stack' }, tabs, hintText('「本地 LLM」只能在本机运行游戏时使用：网页版来自公网域名，浏览器会以跨域（CORS / 私有网络访问）为由拦截对 127.0.0.1 的请求。clone 仓库后 npm run dev 即可用本地模型。')),
          h('label', {}, '地址'), address,
          h('label', {}, '模型'), model,
          h('label', {}, '发言推理'), h('div', { class: 'inline' }, effortSel('reasoning', '发言推理'), spec?.defaultEffort ? hintText(`官方默认 ${spec.defaultEffort}`) : null),
          h('label', {}, '决策推理'), h('div', { class: 'inline' }, effortSel('decisionReasoning', '决策推理'), hintText('投票 / 夜间技能 / 狼队沟通，调低可明显提速')),
          h('label', {}, preset.needsKey ? 'API Key' : 'API Key（可选）'), keyRow,
          h('label', {}, '跨域代理'),
          PROXY_AVAILABLE
            ? h('label', { class: 'check' }, proxy, '经开发服务器转发（避免浏览器跨域限制）')
            : hintText('静态网页版直连服务商'),
        ),
        h(
          'div',
          { class: 'inline', style: 'margin-top:10px;gap:8px;flex-wrap:wrap' },
          testBtn,
          preset.fromEnv ? h('button', { class: 'btn', type: 'button', onclick: () => updateProfile(id, localDefaults()) }, '恢复 .env 默认') : null,
          opts.inGame ? hintText('对局中切换后，下一次 AI 调用就会使用新设置') : null,
        ),
        result,
        vaultRow(),
      );
    };

    const vaultRow = () => {
      const st = vault.state;
      if (st === 'empty') return h('p', { class: 'sub vault-row' }, '🔒 API Key 用主口令加密后才保存在浏览器里；第一次设置 Key 时会让你创建主口令。');
      return h(
        'div',
        { class: 'inline vault-row' },
        h('span', {}, st === 'unlocked' ? '🔓 密钥库已解锁（关闭页面即上锁）' : '🔒 密钥库已上锁'),
        st === 'unlocked'
          ? h('button', { class: 'btn', type: 'button', onclick: () => vault.lock() }, '上锁')
          : h('button', { class: 'btn', type: 'button', onclick: () => void promptUnlock(root) }, '解锁'),
        h('button', { class: 'btn', type: 'button', onclick: () => void promptChangePassphrase(root) }, '修改口令'),
        h('button', { class: 'link', type: 'button', onclick: () => confirm('忘记主口令时只能删除全部已保存的 API Key，之后需要重新录入。确定删除？') && vault.reset() }, '忘记口令'),
      );
    };

    const test = async (btn: HTMLButtonElement) => {
      const p = resolveProvider();
      if (vault.hints()[p.provider] && vault.state === 'locked' && !(await promptUnlock(root))) return;
      btn.disabled = true;
      result.className = 'test-result';
      result.textContent = '连接中…（本地推理首个请求可能较慢）';
      const provider = new OpenAICompatibleProvider(p, (id) => vault.getKey(id));
      let served: string[] = [];
      if (PROVIDERS[p.provider].fromEnv) {
        try {
          served = await provider.listModels();
        } catch {
          /* test() below reports connection problems */
        }
      }
      const r = await provider.test();
      renderEngine();
      // the model list is .env's: only point out a mismatch, never switch on our own
      const unlisted = served.length && !served.includes(p.model) ? ` 服务端没有列出「${p.model}」（它提供：${served.join('、')}），请检查 .env 的 LLM_MODELS。` : '';
      result.className = `test-result ${r.ok && !unlisted ? 'ok' : 'err'}`;
      result.textContent = (!r.ok ? '✗ ' : unlisted ? '⚠ ' : '✓ ') + r.message + unlisted;
      btn.disabled = false;
    };

    renderEngine();
    const unsubs = [onConfigChange(renderEngine), vault.onChange(renderEngine)];

    const close = () => {
      for (const u of unsubs) u();
      back.remove();
      document.removeEventListener('keydown', onKey, true);
      resolve();
    };
    const onKey = (e: KeyboardEvent) => {
      // a key dialog on top handles its own Esc
      if (e.key !== 'Escape' || document.querySelector('.key-back')) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      close();
    };

    const env = envProvider();
    const form = h(
      'div',
      { class: 'modal panel config', role: 'dialog', 'aria-modal': 'true', 'aria-label': '配置' },
      h('h1', {}, '配置'),
      h('div', { class: 'sub' }, '改动立即生效，并保存在本机浏览器。'),
      h('h2', {}, '声音'),
      h(
        'div',
        { class: 'form' },
        h('label', {}, '静音'), h('label', { class: 'check' }, muted, '关闭所有声音'),
        h('label', {}, '音乐'), slider('music'),
        h('label', {}, '音效'), slider('sfx'),
        h('label', {}, '环境（风雨雷）'), slider('ambience'),
      ),
      h('h2', {}, '游戏'),
      h(
        'div',
        { class: 'form' },
        h('label', {}, '节奏'), h('div', { class: 'inline' }, pace, hintText('GM 每一步之间的停顿')),
        h('label', {}, 'AI 发言'), h('label', { class: 'check' }, stepSpeech, '白天逐条查看：点「下一位发言」才出现下一段'),
      ),
      h('h2', {}, 'AI 引擎'),
      env.missing.length ? h('p', { class: 'test-result err' }, `.env 缺少 ${env.missing.join('、')}（参考 .env.example）：「本地 LLM」的地址 / 模型列表为空。`) : null,
      h(
        'div',
        { class: 'form' },
        h('label', {}, '驱动方式'),
        opts.inGame ? h('div', { class: 'inline' }, mode, hintText('对局中不能切换')) : mode,
      ),
      engine,
      h('div', { class: 'actions' }, h('button', { class: 'btn primary', type: 'button', onclick: close }, '完成')),
    );
    const back = h('div', { class: 'modal-back config-back' }, form);
    document.addEventListener('keydown', onKey, true);
    root.appendChild(back);
    (form.querySelector('.btn.primary') as HTMLButtonElement).focus({ preventScroll: true }); // a phone would scroll the dialog to the bottom
  });
}
