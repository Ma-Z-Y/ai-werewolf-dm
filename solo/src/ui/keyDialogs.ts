import { PROVIDERS, type ProviderId } from '../ai/catalog';
import { MIN_PASSPHRASE, vault } from '../keyVault';
import { h } from './dom';

/** A small modal form; resolves with the submit handler's result, or null on cancel. */
function dialog<T>(
  root: HTMLElement,
  title: string,
  body: (Node | string | null)[],
  submitLabel: string,
  onSubmit: (fail: (msg: string) => void) => Promise<T | undefined>,
): Promise<T | null> {
  return new Promise((resolve) => {
    const err = h('div', { class: 'test-result err', 'aria-live': 'assertive' });
    const submit = h('button', { class: 'btn primary', type: 'submit' }, submitLabel) as HTMLButtonElement;
    const form = h(
      'form',
      { class: 'modal panel key-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
      h('h2', {}, title),
      ...body,
      err,
      h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', onclick: () => done(null) }, '取消'), submit),
    ) as HTMLFormElement;
    const back = h('div', { class: 'modal-back key-back' }, form);
    const done = (v: T | null) => {
      back.remove();
      document.removeEventListener('keydown', onKey, true);
      resolve(v);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopImmediatePropagation();
      done(null);
    };
    form.onsubmit = async (e) => {
      e.preventDefault();
      err.textContent = '';
      submit.disabled = true;
      submit.textContent = '处理中…';
      let failed = false;
      const out = await onSubmit((msg) => {
        failed = true;
        err.textContent = msg;
      }).catch((x: Error) => {
        failed = true;
        err.textContent = x.message;
        return undefined;
      });
      submit.disabled = false;
      submit.textContent = submitLabel;
      if (!failed) done(out as T);
    };
    document.addEventListener('keydown', onKey, true);
    root.appendChild(back);
    form.querySelector('input')?.focus();
  });
}

const secret = (placeholder: string, autocomplete: string) =>
  h('input', { type: 'password', placeholder, autocomplete, spellcheck: 'false' }) as HTMLInputElement;

const VAULT_NOTE = 'API Key 用主口令加密后才存进浏览器，口令本身不保存；每次打开页面需要输入一次口令解锁。忘记口令只能删除所有已存的 Key 重新录入。';

/** Unlock the vault. True when unlocked (or already was). */
export async function promptUnlock(root: HTMLElement, reason = '使用已保存的 API Key 需要先解锁。'): Promise<boolean> {
  if (vault.state === 'unlocked') return true;
  if (vault.state === 'empty') return false;
  const pass = secret('主口令', 'current-password');
  const ok = await dialog(root, '解锁密钥库', [h('p', { class: 'sub' }, reason), pass], '解锁', async (fail) => {
    if (await vault.unlock(pass.value)) return true;
    fail('口令不正确');
    pass.select();
    return undefined;
  });
  return !!ok;
}

/** Enter (or replace) a provider's API key; creates / unlocks the vault on the way. */
export async function promptSetKey(root: HTMLElement, provider: ProviderId): Promise<boolean> {
  const preset = PROVIDERS[provider];
  const state = vault.state;
  const key = secret(`${preset.label} API Key`, 'off');
  const pass = secret(state === 'empty' ? `新的主口令（至少 ${MIN_PASSPHRASE} 位）` : '主口令', state === 'empty' ? 'new-password' : 'current-password');
  const pass2 = secret('再输入一次主口令', 'new-password');
  const body: (Node | string | null)[] = [
    h('p', { class: 'sub' }, `Key 只会发往 ${preset.baseUrl}。`, preset.keyUrl ? h('a', { href: preset.keyUrl, target: '_blank', rel: 'noopener noreferrer' }, '去获取 Key') : null),
    key,
  ];
  if (state === 'empty') body.push(h('h3', {}, '设置主口令'), h('p', { class: 'sub' }, VAULT_NOTE), pass, pass2);
  else if (state === 'locked') body.push(h('p', { class: 'sub' }, '输入主口令以保存这个 Key。'), pass);
  const ok = await dialog(root, `${preset.label} · API Key`, body, '加密保存', async (fail) => {
    const k = key.value.trim();
    if (k.length < 8 || /\s/.test(k)) return fail('这看起来不是有效的 API Key');
    if (state === 'empty') {
      if (pass.value.length < MIN_PASSPHRASE) return fail(`主口令至少 ${MIN_PASSPHRASE} 位`);
      if (pass.value !== pass2.value) return fail('两次输入的主口令不一致');
      await vault.create(pass.value);
    } else if (state === 'locked' && !(await vault.unlock(pass.value))) return fail('主口令不正确');
    await vault.setKey(provider, k);
    key.value = '';
    return true;
  });
  return !!ok;
}

export async function promptChangePassphrase(root: HTMLElement): Promise<boolean> {
  if (!(await promptUnlock(root, '先用当前口令解锁。'))) return false;
  const pass = secret(`新的主口令（至少 ${MIN_PASSPHRASE} 位）`, 'new-password');
  const pass2 = secret('再输入一次', 'new-password');
  const ok = await dialog(root, '修改主口令', [h('p', { class: 'sub' }, '所有已存的 Key 会用新口令重新加密。'), pass, pass2], '修改', async (fail) => {
    if (pass.value.length < MIN_PASSPHRASE) return fail(`主口令至少 ${MIN_PASSPHRASE} 位`);
    if (pass.value !== pass2.value) return fail('两次输入不一致');
    await vault.changePassphrase(pass.value);
    return true;
  });
  return !!ok;
}
