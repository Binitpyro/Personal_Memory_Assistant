import { describe, it, expect, vi, afterEach } from 'vitest';
import * as api from '../api';

const enc = new TextEncoder();

function mockStream(parts: string[]) {
  let i = 0;
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    body: {
      getReader: () => ({
        read: () =>
          i < parts.length
            ? Promise.resolve({ done: false, value: enc.encode(parts[i++]) })
            : Promise.resolve({ done: true, value: undefined }),
      }),
    },
  }) as never;
}

function collect(): Promise<api.QueryStreamChunk[]> {
  return new Promise((resolve) => {
    const got: api.QueryStreamChunk[] = [];
    api.subscribeQuery({ question: 'q' }, (c) => {
      got.push(c);
      if (c.type === 'done') resolve(got);
    });
  });
}

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
  vi.useRealTimers();
});

describe('A7-03: NDJSON framing', () => {
  it('keeps events whose strings contain "}{"', async () => {
    const a = JSON.stringify({ type: 'content', text: 'x = \frac{1}{2}' });
    const b = JSON.stringify({ type: 'sources', sources: [{ text: 'print(f"{a}{b}")' }] });
    mockStream([`${a}\n${b}\n`]);
    const got = await collect();
    expect(got.map((c) => c.type)).toEqual(['content', 'sources', 'done']);
    expect(got[0].text).toBe('x = \frac{1}{2}');
  });
});

describe('A7-09 / A5-10: FastAPI {detail} errors', () => {
  it('json() surfaces a string detail', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      json: () => Promise.resolve({ detail: 'Explicit consent required' }),
    }) as never;
    await expect(api.json('/x')).rejects.toThrow('Explicit consent required');
  });

  it('json() joins a validation-error detail list', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable',
      json: () =>
        Promise.resolve({ error: 'Validation error', detail: [{ msg: 'too long' }, { msg: 'bad role' }] }),
    }) as never;
    await expect(api.json('/x')).rejects.toThrow('Validation error: too long; bad role');
  });
});

describe('A7-07: progress stream does not hot-loop while idle', () => {
  it('reconnects slowly after a settled event, fast while running', async () => {
    vi.useFakeTimers();
    const instances: FakeES[] = [];
    class FakeES {
      listeners: Record<string, (e: { data: string }) => void> = {};
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(_url: string) {
        instances.push(this);
      }
      addEventListener(n: string, f: (e: { data: string }) => void) {
        this.listeners[n] = f;
      }
      close() {}
    }
    vi.stubGlobal('EventSource', FakeES);
    const unsub = api.subscribeProgress(() => {});
    await vi.advanceTimersByTimeAsync(300);
    expect(instances.length).toBe(1);

    // idle event, then the server closes the stream
    instances[0].listeners.progress({ data: JSON.stringify({ status: 'idle' }) });
    instances[0].onerror?.();
    await vi.advanceTimersByTimeAsync(5000);
    expect(instances.length).toBe(1); // no 1s reconnect while settled
    await vi.advanceTimersByTimeAsync(10_000);
    expect(instances.length).toBe(2);

    // a running event reconnects after 1s
    instances[1].listeners.progress({ data: JSON.stringify({ status: 'running' }) });
    instances[1].onerror?.();
    await vi.advanceTimersByTimeAsync(1000);
    expect(instances.length).toBe(3);
    unsub();
    vi.unstubAllGlobals();
  });
});
