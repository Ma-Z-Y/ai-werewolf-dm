import { describe, expect, it } from 'vitest';
import { PROVIDERS, buildChatBody, clampEffort, effortsFor, thinkingBudget } from './catalog';
import { keyMayGoTo } from './provider';

const base = { messages: [], maxTokens: 1000, temperature: 0.9 };

describe('provider matrix', () => {
  it('OpenAI: reasoning_effort + max_completion_tokens, no temperature on reasoning models', () => {
    const b = buildChatBody({ ...base, dialect: 'openai', model: 'gpt-6-sol', effort: 'low' });
    expect(b).toMatchObject({ model: 'gpt-6-sol', reasoning_effort: 'low', max_completion_tokens: 4000 });
    expect(b).not.toHaveProperty('temperature');
    expect(b).not.toHaveProperty('max_tokens');
    // effort none still is a reasoning model: sent explicitly, no sampling params
    const none = buildChatBody({ ...base, dialect: 'openai', model: 'gpt-6-luna', effort: 'none' });
    expect(none).toMatchObject({ reasoning_effort: 'none', max_completion_tokens: 1000 });
    expect(none).not.toHaveProperty('temperature');
  });

  it('OpenAI: non-reasoning models get temperature and no reasoning parameter', () => {
    const b = buildChatBody({ ...base, dialect: 'openai', model: 'gpt-4.1', effort: '' });
    expect(b).toMatchObject({ temperature: 0.9, max_completion_tokens: 1000 });
    expect(b).not.toHaveProperty('reasoning_effort');
  });

  it('DeepSeek: thinking toggle + top-level reasoning_effort', () => {
    const on = buildChatBody({ ...base, dialect: 'deepseek', model: 'deepseek-flash', effort: 'high' });
    expect(on).toMatchObject({ thinking: { type: 'enabled' }, reasoning_effort: 'high' });
    expect(on).not.toHaveProperty('temperature');
    const off = buildChatBody({ ...base, dialect: 'deepseek', model: 'deepseek-flash', effort: 'none' });
    expect(off).toMatchObject({ thinking: { type: 'disabled' }, temperature: 0.9, max_tokens: 1000 });
    expect(off).not.toHaveProperty('reasoning_effort');
  });

  it('local: Qwen-style format, thinking budget on top of the answer cap', () => {
    expect(buildChatBody({ ...base, dialect: 'local', model: 'm', effort: 'medium' })).toMatchObject({ reasoning_effort: 'medium', max_tokens: 1000 + thinkingBudget('medium') });
    expect(buildChatBody({ ...base, dialect: 'local', model: 'm', effort: 'none' })).toMatchObject({ max_tokens: 1000 });
    expect(buildChatBody({ ...base, dialect: 'local', model: 'm', effort: 'none' })).toMatchObject({ chat_template_kwargs: { enable_thinking: false } });
  });

  it('only offers the values each model officially accepts', () => {
    expect(effortsFor('openai', 'gpt-6-astra')).not.toContain('none');
    expect(effortsFor('openai', 'gpt-5.5')).not.toContain('max');
    expect(effortsFor('openai', 'gpt-4.1')).toEqual([]);
    expect(effortsFor('deepseek', 'deepseek-v4-pro')).toEqual(['none', 'low', 'high', 'max']);
    for (const p of Object.values(PROVIDERS)) {
      for (const m of p.models) if (m.defaultEffort) expect(m.efforts).toContain(m.defaultEffort);
      if (p.models.length) expect(p.models.map((m) => m.id)).toContain(p.defaultModel);
    }
  });

  it('clamps a level to the nearest weaker one the model supports', () => {
    expect(clampEffort('none', ['low', 'medium', 'high', 'xhigh', 'max'])).toBe('low');
    expect(clampEffort('medium', ['none', 'low', 'high', 'max'])).toBe('low');
    expect(clampEffort('xhigh', ['none', 'low', 'high', 'max'])).toBe('high');
    expect(clampEffort('max', ['none', 'low', 'medium', 'high', 'xhigh'])).toBe('xhigh');
    expect(clampEffort('medium', [])).toBe('');
  });

  it('a key only goes to its own provider (the local server: anywhere)', () => {
    expect(keyMayGoTo('openai', 'https://api.openai.com/v1')).toBe(true);
    expect(keyMayGoTo('openai', 'https://api.openai.com.evil.example/v1')).toBe(false);
    expect(keyMayGoTo('deepseek', 'https://api.openai.com/v1')).toBe(false);
    expect(keyMayGoTo('local', 'http://10.0.0.5:1234/v1')).toBe(true);
  });
});
