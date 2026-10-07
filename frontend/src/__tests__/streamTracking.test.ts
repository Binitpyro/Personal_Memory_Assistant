import { describe, it, expect, vi, afterEach } from 'vitest'
import * as api from '../api'

describe('SSE Stream Parsing', () => {
  it('correctly handles fragmented JSON chunks', async () => {
    // Mock fetch to yield a fragmented stream
    const originalFetch = globalThis.fetch
    
    // Create a mock stream that yields data in weird chunks
    const chunks = [
      '{"type":"con',
      'tent","text":"Hello"}',
      '\n{"type":"content","text":" Wor',
      'ld"}\n',
      '{"type":"content","text":"!"}\n',
      '{"type":"done"}\n'
    ];
    
    let chunkIndex = 0;
    
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: () => {
            if (chunkIndex < chunks.length) {
              const encoder = new TextEncoder();
              const value = encoder.encode(chunks[chunkIndex++]);
              return Promise.resolve({ done: false, value });
            }
            return Promise.resolve({ done: true, value: undefined });
          }
        })
      }
    });

    const receivedChunks: api.QueryStreamChunk[] = [];
    
    await new Promise<void>((resolve) => {
      api.subscribeQuery(
        { question: 'test' },
        (chunk) => {
          receivedChunks.push(chunk);
          if (chunk.type === 'done' || chunk.type === 'error') {
            resolve();
          }
        }
      );
    });

    // We should have received 3 content chunks and 1 done chunk
    expect(receivedChunks.length).toBe(4);
    expect(receivedChunks[0]).toEqual({ type: 'content', text: 'Hello' });
    expect(receivedChunks[1]).toEqual({ type: 'content', text: ' World' });
    expect(receivedChunks[2]).toEqual({ type: 'content', text: '!' });
    expect(receivedChunks[3]).toEqual({ type: 'done' });

    globalThis.fetch = originalFetch;
  });

  it('correctly handles concatenated JSON objects without newlines (Case 3)', async () => {
    const originalFetch = globalThis.fetch
    
    // Create a mock stream with missing newlines between objects
    const chunks = [
      '{"type":"content","text":"A"}{"type":"content","text":"B"}{"type":"done"}'
    ];
    
    let chunkIndex = 0;
    
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: () => {
            if (chunkIndex < chunks.length) {
              const encoder = new TextEncoder();
              const value = encoder.encode(chunks[chunkIndex++]);
              return Promise.resolve({ done: false, value });
            }
            return Promise.resolve({ done: true, value: undefined });
          }
        })
      }
    });

    const receivedChunks: api.QueryStreamChunk[] = [];
    
    await new Promise<void>((resolve) => {
      api.subscribeQuery(
        { question: 'test' },
        (chunk) => {
          receivedChunks.push(chunk);
          if (chunk.type === 'done' || chunk.type === 'error') {
            resolve();
          }
        }
      );
    });

    // We should receive A, B, and done, plus the final done triggered at the end of the stream
    const contentChunks = receivedChunks.filter(c => c.type === 'content');
    expect(contentChunks.length).toBe(2);
    expect(contentChunks[0]).toEqual({ type: 'content', text: 'A' });
    expect(contentChunks[1]).toEqual({ type: 'content', text: 'B' });

    globalThis.fetch = originalFetch;
  });
});

describe('HTTP error messages', () => {
  const originalFetch = globalThis.fetch

  const failWith = (status: number, body: unknown) => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status,
      statusText: 'err',
      json: () => (body === undefined ? Promise.reject(new SyntaxError('no body')) : Promise.resolve(body)),
    }) as never
  }
  const streamError = () =>
    new Promise<string | undefined>((resolve) => {
      api.subscribeQuery({ question: 'q' }, (chunk) => {
        if (chunk.type === 'error') resolve(chunk.text)
      })
    })

  afterEach(() => { globalThis.fetch = originalFetch })

  it('json() reads FastAPI {"detail": str} bodies', async () => {
    failWith(400, { detail: 'Unknown provider: foo' })
    await expect(api.json('/x')).rejects.toThrow('Unknown provider: foo')
  })

  it('json() joins the msgs of the real 422 shape', async () => {
    failWith(422, {
      error: 'Validation error',
      detail: [{ loc: ['body', 'question'], msg: 'Field required', type: 'missing' }, { msg: 'Input should be a string' }],
    })
    await expect(api.json('/x')).rejects.toThrow('Validation error: Field required; Input should be a string')
  })

  it('json() still reads {"error": str} and falls back to the status', async () => {
    failWith(500, { error: 'boom' })
    await expect(api.json('/x')).rejects.toThrow('boom')
    failWith(502, {})
    await expect(api.json('/x')).rejects.toThrow('HTTP 502')
  })

  it('subscribeQuery surfaces both body shapes and survives a non-JSON body', async () => {
    failWith(400, { detail: 'No such model' })
    expect(await streamError()).toBe('No such model')
    failWith(422, { error: 'Validation error', detail: [{ msg: 'bad' }] })
    expect(await streamError()).toBe('Validation error: bad')
    failWith(503, undefined)
    expect(await streamError()).toBe('HTTP 503')
  })
})
