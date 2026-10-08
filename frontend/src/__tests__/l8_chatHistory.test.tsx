import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useChatStream } from '../hooks/useChatStream';
import { subscribeQuery } from '../api';

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  subscribeQuery: vi.fn(),
}));

describe('A7-05: current question is not sent inside history', () => {
  it('first turn sends an empty history (so fast path / semantic cache can fire)', async () => {
    const payloads: { question: string; history: { role: string; content: string }[] }[] = [];
    vi.mocked(subscribeQuery).mockImplementation(((p: never, cb: (c: unknown) => void) => {
      payloads.push(p);
      queueMicrotask(() => cb({ type: 'done' }));
      return () => {};
    }) as never);

    const { result } = renderHook(() => useChatStream(() => {}));
    await act(async () => {
      await result.current.executeSearch('what is in my notes', {});
    });
    expect(payloads[0].question).toBe('what is in my notes');
    expect(payloads[0].history).toEqual([]);
  });
});

describe('A7-11: leaving the page mid-answer aborts the stream', () => {
  it('calls the subscription teardown on unmount', async () => {
    const teardown = vi.fn();
    vi.mocked(subscribeQuery).mockImplementation((() => teardown) as never);

    const { result, unmount } = renderHook(() => useChatStream(() => {}));
    let pending: Promise<void> | undefined;
    act(() => {
      pending = result.current.executeSearch('long question', {});
    });
    expect(teardown).not.toHaveBeenCalled();
    unmount();
    expect(teardown).toHaveBeenCalledTimes(1);
    await pending; // finalize resolves it; it must not hang forever
  });
});
