import { h } from './dom';

export interface PauseMenu {
  /** e.g. "第 2 天 · 发言 · 用时 12:34" */
  status: string;
  /** Why saving is unavailable right now, or null. */
  cannotSave: string | null;
  /** Returns an error message, or null when saved. */
  save(): string | null;
  /** Progress made since the last save. */
  dirty(): boolean;
  /** Open 配置 over the menu; resolves when it closes. */
  config(): Promise<void>;
}

/** Pause screen: 继续游戏 / 保存游戏 / 配置 / 回到标题画面. Esc resumes. */
export function showPause(root: HTMLElement, m: PauseMenu): Promise<'resume' | 'title'> {
  return new Promise((resolve) => {
    const finish = (v: 'resume' | 'title') => {
      back.remove();
      document.removeEventListener('keydown', onKey, true);
      resolve(v);
    };
    const note = h('div', { class: 'pause-note', 'aria-live': 'polite' }, m.cannotSave ?? '');
    const save = (): boolean => {
      const err = m.save();
      note.className = `pause-note ${err ? 'err' : 'ok'}`;
      const d = new Date();
      note.textContent = err ?? `已保存（${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}）· 关闭浏览器后可在标题画面载入`;
      return !err;
    };
    const resumeBtn = h('button', { class: 'title-opt', onclick: () => finish('resume') }, '继续游戏');
    const saveBtn = h('button', { class: 'title-opt', disabled: !!m.cannotSave, onclick: save }, '保存游戏');
    let inConfig = false;
    const configBtn = h('button', {
      class: 'title-opt',
      onclick: async () => {
        inConfig = true;
        await m.config();
        inConfig = false;
        configBtn.focus();
      },
    }, '配置');
    const titleBtn = h('button', { class: 'title-opt', onclick: () => (m.dirty() ? askLeave() : finish('title')) }, '回到标题画面');
    const menu = h('nav', { class: 'title-menu' }, resumeBtn, saveBtn, configBtn, titleBtn, note);

    // leaving with unsaved progress: offer to save first
    const askLeave = () => {
      const confirmBox = h(
        'div',
        { class: 'leave-confirm' },
        h('div', {}, m.cannotSave ? '离开后，自上次保存以来的进度会丢失。' : '有未保存的进度。'),
        h(
          'div',
          { class: 'row' },
          m.cannotSave ? null : h('button', { class: 'btn primary', onclick: () => save() && finish('title') }, '保存并返回'),
          h('button', { class: 'btn danger', onclick: () => finish('title') }, '不保存，返回'),
          h('button', { class: 'btn', onclick: () => { confirmBox.remove(); menu.style.display = ''; titleBtn.focus(); } }, '取消'),
        ),
      );
      menu.style.display = 'none';
      card.appendChild(confirmBox);
      confirmBox.querySelector<HTMLButtonElement>('button')?.focus();
    };

    const card = h('div', { class: 'title-card pause-card' }, h('h1', {}, '暂停'), h('div', { class: 'tagline' }, m.status), menu);
    const back = h('div', { id: 'pause', role: 'dialog', 'aria-modal': 'true', 'aria-label': '暂停' }, card);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !inConfig) {
        e.preventDefault();
        e.stopPropagation();
        finish('resume');
      }
    };
    document.addEventListener('keydown', onKey, true);
    root.appendChild(back);
    resumeBtn.focus();
  });
}
