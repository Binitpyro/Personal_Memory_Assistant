import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStream } from '../hooks/useChatStream';
import { subscribeQuery } from '../api';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  subscribeQuery: vi.fn(),
}));

type Turn = { role: string; content: string };

describe('useChatStream history payload', () => {
  it('drops empty assistant turns, caps at 50 after the push, clips to 10,000 code points', async () => {
    const payloads: { history: Turn[] }[] = [];
    // Every turn fails with no content chunk, which leaves an empty assistant
    // message behind - exactly the turn that must not be replayed.
    vi.mocked(subscribeQuery).mockImplementation(((payload: { history: Turn[] }, cb: (c: unknown) => void) => {
      payloads.push(payload);
      queueMicrotask(() => cb({ type: 'error', text: 'boom' }));
      return () => {};
    }) as never);

    const { result } = renderHook(() => useChatStream(() => {}));
    const ask = async (q: string) => {
      await act(async () => {
        await result.current.executeSearch(q, {}).catch(() => {});
      });
    };

    await ask('first');
    await ask('second');
    // (1) the failed turn's empty assistant message is not sent.
    expect(payloads[1].history).toEqual([
      { role: 'user', content: 'first' },
      { role: 'user', content: 'second' },
    ]);

    for (let i = 0; i < 58; i++) await ask(`q${i}`);
    const emoji = '\u{1F600}'.repeat(10_005); // 10,005 code points, 20,010 UTF-16 units
    await ask(emoji);
    const last = payloads.at(-1)!.history;
    // (2) 61 user turns were available; slice(-50) runs after the current
    // question is pushed, so the question is the last entry and 50 remain.
    expect(last).toHaveLength(50);
    expect(last.at(-1)!.role).toBe('user');
    // (3) clipped by code points, not UTF-16 units (no split surrogate).
    expect(Array.from(last.at(-1)!.content)).toHaveLength(10_000);
  });

  it('stores a stream error and its code on the assistant message it belongs to', async () => {
    vi.mocked(subscribeQuery).mockImplementation(((_p: unknown, cb: (c: unknown) => void) => {
      queueMicrotask(() => {
        cb({ type: 'content', text: 'partial' });
        cb({ type: 'error', text: 'boom', code: 'empty_answer' });
      });
      return () => {};
    }) as never);

    const { result } = renderHook(() => useChatStream(() => {}));
    await act(async () => {
      await result.current.executeSearch('q', {}).catch(() => {});
    });

    const assistant = result.current.messages.at(-1)!;
    expect(assistant.role).toBe('assistant');
    expect(assistant.isStreaming).toBe(false);
    expect(assistant.error).toEqual({ text: 'boom', code: 'empty_answer' });
    // Tokens still held by the 50 ms throttle are landed before the error.
    expect(assistant.content).toBe('partial');
    expect(result.current.messages.filter(m => m.role === 'user').every(m => !m.error)).toBe(true);
  });
});
