import { describe, it, expect } from 'vitest'

import { selectMostUsedModel } from '../context/SessionProviderContext'

/**
 * The usage log is bucketed by date, so the winner is a SUM across buckets
 * rather than a single stored number. That is the part worth pinning: a naive
 * "max within one day" reading picks a different model, and nothing else in the
 * suite would notice.
 */
describe('selectMostUsedModel', () => {
  it('returns null when nothing has been run', () => {
    expect(selectMostUsedModel({})).toBeNull()
  })

  it('sums a model across date buckets rather than reading one day', () => {
    const log = {
      '2026-09-01T00:00:00.000Z': { 'ollama::gemma4-local:latest': 3, 'gemini::gemini-2.5-flash-lite': 5 },
      '2026-09-02T00:00:00.000Z': { 'ollama::gemma4-local:latest': 4 },
    }
    // Gemini wins any single day (5 > 3, 5 > 0); Ollama wins the window (7 > 5).
    expect(selectMostUsedModel(log)).toEqual({
      provider: 'ollama',
      model: 'gemma4-local:latest',
      count: 7,
    })
  })

  it('keeps a colon inside the model id intact', () => {
    const log = { '2026-09-01T00:00:00.000Z': { 'ollama::gemma4-local:latest': 1 } }
    expect(selectMostUsedModel(log)?.model).toBe('gemma4-local:latest')
  })

  it('breaks ties on most recent use, not on key order', () => {
    const log = {
      '2026-09-01T00:00:00.000Z': { 'gemini::a': 2 },
      '2026-09-05T00:00:00.000Z': { 'ollama::b': 2 },
    }
    expect(selectMostUsedModel(log)?.provider).toBe('ollama')

    // Same data, opposite insertion order — the answer must not move.
    const reversed = {
      '2026-09-05T00:00:00.000Z': { 'ollama::b': 2 },
      '2026-09-01T00:00:00.000Z': { 'gemini::a': 2 },
    }
    expect(selectMostUsedModel(reversed)?.provider).toBe('ollama')
  })

  it('ignores malformed buckets and non-positive counts', () => {
    const log = {
      '2026-09-01T00:00:00.000Z': { 'ollama::good': 2, 'gemini::zero': 0, 'groq::negative': -5 },
      '2026-09-02T00:00:00.000Z': null as unknown as Record<string, number>,
      '2026-09-03T00:00:00.000Z': { 'openai::nan': Number.NaN },
    }
    expect(selectMostUsedModel(log)).toEqual({ provider: 'ollama', model: 'good', count: 2 })
  })

  it('drops a key with no separator instead of inventing a provider', () => {
    expect(selectMostUsedModel({ '2026-09-01T00:00:00.000Z': { malformed: 9 } })).toBeNull()
  })
})
