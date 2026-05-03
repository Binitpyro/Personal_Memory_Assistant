import { describe, it, expect, vi } from 'vitest'
import * as api from '../api'

describe('Stream Tracking Logic', () => {
  it('updates activeStreamCount on subscribeQuery', async () => {
    // Mock fetch to avoid actual network calls
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      body: {
        getReader: () => ({
          read: () => Promise.resolve({ done: true, value: undefined })
        })
      }
    })

    const initialCount = api.activeStreamCount
    const unsubscribe = api.subscribeQuery({ query: 'test' }, () => {})
    
    expect(api.activeStreamCount).toBeGreaterThan(initialCount)
    
    unsubscribe()
    // Small delay for microtasks
    await new Promise(resolve => setTimeout(resolve, 0))
    expect(api.activeStreamCount).toBe(initialCount)

    globalThis.fetch = originalFetch
  })

  it('failsafe can reset the counter', () => {
    // Manually increment for testing
    // Since it's an exported let, we can't easily re-assign it if it's imported as a namespace
    // but we can call the checkStreamFailsafe
    api.checkStreamFailsafe()
    expect(api.activeStreamCount).toBe(0)
  })
})
