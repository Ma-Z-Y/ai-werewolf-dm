import { describe, expect, it } from 'vitest';
import { parseSave } from './save';

const validSave = {
  v: 3,
  savedAt: 1,
  setupSeed: 2,
  gameSeed: 3,
  journal: [{ t: 1 }, { s: '过' }],
  agents: Array.from({ length: 12 }, () => null),
  elapsedMs: 1200.5,
  marks: Array.from({ length: 12 }, () => null),
  prefs: {
    playerName: '旅人',
    role: 'random',
    wolfChatRounds: 3,
    godView: false,
    mode: 'offline',
  },
  meta: { seat: 0, role: 'seer', day: 2, phase: 'discussion', alive: 10 },
};

describe('save validation', () => {
  it('accepts a complete v3 save and preserves the autosave marker', () => {
    const parsed = parseSave({ ...validSave, auto: true });
    expect(parsed?.auto).toBe(true);
    expect(parsed?.journal).toEqual(validSave.journal);
    expect(parsed?.agents).toHaveLength(12);
  });

  it('rejects damaged saves before the title screen can index into them', () => {
    expect(parseSave({ ...validSave, journal: [{ nope: true }] })).toBeNull();
    expect(parseSave({ ...validSave, meta: { ...validSave.meta, role: 'unknown' } })).toBeNull();
    expect(parseSave({ ...validSave, prefs: { ...validSave.prefs, mode: 'remote' } })).toBeNull();
  });
});
