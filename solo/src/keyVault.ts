import type { ProviderId } from './ai/catalog';

/**
 * API keys at rest: browser storage only ever holds AES-GCM ciphertext. The
 * AES key is derived from a master passphrase (PBKDF2-SHA-256, 600k rounds),
 * is non-extractable, and lives in memory only while the vault is unlocked —
 * the passphrase itself is never stored. So a copied / synced / inspected
 * localStorage reveals nothing but the last 4 characters of each key.
 *
 * Each ciphertext is bound to its provider (AES-GCM additional data), so a
 * blob cannot be moved to another provider's slot. Keys are decrypted per
 * request and never cached as plain text.
 */

const PBKDF2_ROUNDS = 600_000;
const CHECK_TEXT = 'ai-werewolf key vault';
export const MIN_PASSPHRASE = 8;

interface Sealed {
  iv: string;
  ct: string;
}

interface VaultData {
  v: 1;
  salt: string;
  rounds: number;
  /** Encrypts CHECK_TEXT: tells a wrong passphrase from a right one. */
  check: Sealed;
  keys: Partial<Record<ProviderId, Sealed & { hint: string }>>;
}

/** Where the vault blob is kept (localStorage in the browser, a map in tests). */
export interface VaultStore {
  get(): string | null;
  set(v: string): void;
  remove(): void;
}

const KEY = 'ai-werewolf:vault:v1';

export const browserStore: VaultStore = {
  get: () => {
    try {
      return localStorage.getItem(KEY);
    } catch {
      return null;
    }
  },
  set: (v) => localStorage.setItem(KEY, v),
  remove: () => {
    try {
      localStorage.removeItem(KEY);
    } catch {
      /* ignore */
    }
  },
};

const enc = new TextEncoder();
const dec = new TextDecoder();
const b64 = (b: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(b)));
const unb64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0)) as Uint8Array<ArrayBuffer>;

async function derive(passphrase: string, salt: Uint8Array<ArrayBuffer>, rounds: number): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey('raw', enc.encode(passphrase), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', hash: 'SHA-256', salt, iterations: rounds },
    base,
    { name: 'AES-GCM', length: 256 },
    false, // non-extractable: script can use it, never read it out
    ['encrypt', 'decrypt'],
  );
}

async function seal(key: CryptoKey, text: string, aad: string): Promise<Sealed> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: enc.encode(aad) }, key, enc.encode(text));
  return { iv: b64(iv), ct: b64(ct) };
}

async function open(key: CryptoKey, s: Sealed, aad: string): Promise<string> {
  const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unb64(s.iv), additionalData: enc.encode(aad) }, key, unb64(s.ct));
  return dec.decode(pt);
}

export type VaultState = 'empty' | 'locked' | 'unlocked';

export class KeyVault {
  private key: CryptoKey | null = null;
  private listeners = new Set<() => void>();

  constructor(private store: VaultStore = browserStore) {}

  private read(): VaultData | null {
    try {
      const raw = this.store.get();
      const d = raw ? (JSON.parse(raw) as VaultData) : null;
      return d?.v === 1 && d.salt && d.check ? d : null;
    } catch {
      return null;
    }
  }

  private write(d: VaultData) {
    this.store.set(JSON.stringify(d));
    this.changed();
  }

  private changed() {
    for (const fn of this.listeners) fn();
  }

  onChange(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  get state(): VaultState {
    if (!this.read()) return 'empty';
    return this.key ? 'unlocked' : 'locked';
  }

  /** Stored keys, as "…abcd" hints (readable while locked). */
  hints(): Partial<Record<ProviderId, string>> {
    const d = this.read();
    return Object.fromEntries(Object.entries(d?.keys ?? {}).map(([p, k]) => [p, k!.hint]));
  }

  /** First key: set up the vault with a new master passphrase (and unlock it). */
  async create(passphrase: string) {
    if (passphrase.length < MIN_PASSPHRASE) throw new Error(`主口令至少 ${MIN_PASSPHRASE} 位`);
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const key = await derive(passphrase, salt, PBKDF2_ROUNDS);
    this.key = key;
    this.write({ v: 1, salt: b64(salt), rounds: PBKDF2_ROUNDS, check: await seal(key, CHECK_TEXT, 'check'), keys: {} });
  }

  /** False when the passphrase is wrong. */
  async unlock(passphrase: string): Promise<boolean> {
    const d = this.read();
    if (!d) return false;
    const key = await derive(passphrase, unb64(d.salt), d.rounds);
    try {
      if ((await open(key, d.check, 'check')) !== CHECK_TEXT) return false;
    } catch {
      return false;
    }
    this.key = key;
    this.changed();
    return true;
  }

  lock() {
    this.key = null;
    this.changed();
  }

  async setKey(provider: ProviderId, apiKey: string) {
    const d = this.read();
    if (!d || !this.key) throw new Error('密钥库未解锁');
    const clean = apiKey.trim();
    d.keys[provider] = { ...(await seal(this.key, clean, `key:${provider}`)), hint: `…${clean.slice(-4)}` };
    this.write(d);
  }

  removeKey(provider: ProviderId) {
    const d = this.read();
    if (!d?.keys[provider]) return;
    delete d.keys[provider];
    this.write(d);
  }

  /** The plain key for one request; null if none is stored or the vault is locked. */
  async getKey(provider: ProviderId): Promise<string | null> {
    const d = this.read();
    const sealed = d?.keys[provider];
    if (!sealed || !this.key) return null;
    try {
      return await open(this.key, sealed, `key:${provider}`);
    } catch {
      return null;
    }
  }

  /** Re-encrypt every key under a new passphrase (needs the vault unlocked). */
  async changePassphrase(passphrase: string) {
    const d = this.read();
    if (!d || !this.key) throw new Error('密钥库未解锁');
    if (passphrase.length < MIN_PASSPHRASE) throw new Error(`主口令至少 ${MIN_PASSPHRASE} 位`);
    // build the whole new vault first, then swap it in with one write
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const key = await derive(passphrase, salt, PBKDF2_ROUNDS);
    const next: VaultData = { v: 1, salt: b64(salt), rounds: PBKDF2_ROUNDS, check: await seal(key, CHECK_TEXT, 'check'), keys: {} };
    for (const [p, sealed] of Object.entries(d.keys) as [ProviderId, Sealed & { hint: string }][]) {
      const plain = await open(this.key, sealed, `key:${p}`);
      next.keys[p] = { ...(await seal(key, plain, `key:${p}`)), hint: sealed.hint };
    }
    this.key = key;
    this.write(next);
  }

  /** Forgotten passphrase: the keys cannot be recovered, only deleted. */
  reset() {
    this.key = null;
    this.store.remove();
    this.changed();
  }
}

export const vault = new KeyVault();
