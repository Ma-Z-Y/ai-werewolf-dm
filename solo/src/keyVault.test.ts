import { describe, expect, it } from 'vitest';
import { KeyVault, type VaultStore } from './keyVault';

function memory(): VaultStore & { raw: () => string | null } {
  let v: string | null = null;
  return { get: () => v, set: (x) => (v = x), remove: () => (v = null), raw: () => v };
}

describe('key vault', () => {
  it('stores only ciphertext and needs the passphrase to read keys back', async () => {
    const store = memory();
    const a = new KeyVault(store);
    expect(a.state).toBe('empty');
    await a.create('correct horse');
    await a.setKey('openai', 'sk-proj-SECRET-1234abcd');
    expect(store.raw()).not.toContain('SECRET');
    expect(store.raw()).not.toContain('correct horse');
    expect(a.hints()).toEqual({ openai: '…abcd' });
    expect(await a.getKey('openai')).toBe('sk-proj-SECRET-1234abcd');

    // a fresh page load: locked, wrong passphrase rejected, right one works
    const b = new KeyVault(store);
    expect(b.state).toBe('locked');
    expect(await b.getKey('openai')).toBeNull();
    expect(await b.unlock('wrong horse')).toBe(false);
    expect(await b.unlock('correct horse')).toBe(true);
    expect(await b.getKey('openai')).toBe('sk-proj-SECRET-1234abcd');
    b.lock();
    expect(await b.getKey('openai')).toBeNull();
  });

  it('binds each ciphertext to its provider', async () => {
    const store = memory();
    const v = new KeyVault(store);
    await v.create('correct horse');
    await v.setKey('openai', 'sk-openai-key-1111');
    // move the OpenAI blob into the DeepSeek slot: it must not decrypt there
    const d = JSON.parse(store.raw()!);
    d.keys.deepseek = d.keys.openai;
    store.set(JSON.stringify(d));
    expect(await v.getKey('deepseek')).toBeNull();
  });

  it('changes the passphrase, keeps keys, and can be reset', async () => {
    const store = memory();
    const v = new KeyVault(store);
    await v.create('correct horse');
    await v.setKey('deepseek', 'sk-deepseek-2222');
    await v.changePassphrase('battery staple');
    const w = new KeyVault(store);
    expect(await w.unlock('correct horse')).toBe(false);
    expect(await w.unlock('battery staple')).toBe(true);
    expect(await w.getKey('deepseek')).toBe('sk-deepseek-2222');
    await expect(w.create('short')).rejects.toThrow();
    w.reset();
    expect(w.state).toBe('empty');
    expect(store.raw()).toBeNull();
  });
});
