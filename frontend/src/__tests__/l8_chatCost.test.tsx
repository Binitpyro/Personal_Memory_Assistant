import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStream } from '../hooks/useChatStream';
import { subscribeQuery } from '../api';
import { queryClient } from '../queryClient';
import { CACHE_KEYS } from '../cacheKeys';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  subscribeQuery: vi.fn(),
}));

function usageThenDone() {
  vi.mocked(subscribeQuery).mockImplementation(((_p: unknown, cb: (c: unknown) => void) => {
    queueMicrotask(() => {
      cb({ type: 'usage', prompt_tokens: 1_000_000, completion_tokens: 0 });
      cb({ type: 'done' });
    });
    return () => {};
  }) as never);
}

async function ask() {
  const { result } = renderHook(() => useChatStream(() => {}));
  await act(async () => {
    await result.current.executeSearch('q', {});
  });
  return result.current.messages.at(-1)!;
}

describe('cost attribution', () => {
  beforeEach(() => queryClient.clear());

  it('A4-14: the new default Anthropic model has a price entry', async () => {
    queryClient.setQueryData([CACHE_KEYS.currentProvider], { provider: 'anthropic', model: 'claude-sonnet-5-5' });
    usageThenDone();
    expect((await ask()).cost).toBeGreaterThan(0);
  });

  it('A7-13: a local answer is not priced as the first keyed cloud provider', async () => {
    queryClient.setQueryData(['providers-list'], [
      { spec: { id: 'gemini' }, is_set: true, default_model: 'gemini-2.5-flash' },
    ]);
    // What the backend actually resolved to.
    queryClient.setQueryData([CACHE_KEYS.currentProvider], { provider: 'ollama', model: 'gemma4-local' });
    usageThenDone();
    expect((await ask()).cost).toBe(0);
  });

  it('A7-13b: a provider with no reported model does not borrow the keyed provider model', async () => {
    queryClient.setQueryData(['providers-list'], [
      { spec: { id: 'gemini' }, is_set: true, default_model: 'gemini-2.5-flash' },
    ]);
    queryClient.setQueryData([CACHE_KEYS.currentProvider], { provider: 'groq', model: null });
    usageThenDone();
    // groq with an unknown model: not priced as gemini-2.5-flash (0.075/M).
    expect((await ask()).cost).toBe(0);
  });

  it('keeps pricing the retired claude-3-5-sonnet id for users still on it', async () => {
    queryClient.setQueryData([CACHE_KEYS.currentProvider], { provider: 'anthropic', model: 'claude-3-5-sonnet-20241022' });
    usageThenDone();
    expect((await ask()).cost).toBeGreaterThan(0);
  });
});
