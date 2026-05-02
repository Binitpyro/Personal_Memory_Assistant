/**
 * frontend/src/__tests__/api.test.ts
 * Tests for API response shape validation and error handling patterns
 * (pure logic, no actual fetch calls).
 */
import { describe, it, expect } from 'vitest'

// ── API response type guards ───────────────────────────────────────────────

interface SearchResult {
  answer: string
  sources: Array<{ file_path: string; text: string; score: number }>
  cached?: boolean
  latency_ms?: number
}

function isValidSearchResult(data: unknown): data is SearchResult {
  if (typeof data !== 'object' || data === null) return false
  const d = data as Record<string, unknown>
  return (
    typeof d['answer'] === 'string' &&
    Array.isArray(d['sources'])
  )
}

describe('isValidSearchResult', () => {
  it('accepts valid result', () => {
    expect(isValidSearchResult({
      answer: 'test answer',
      sources: [],
    })).toBe(true)
  })
  it('rejects null', () => {
    expect(isValidSearchResult(null)).toBe(false)
  })
  it('rejects missing answer', () => {
    expect(isValidSearchResult({ sources: [] })).toBe(false)
  })
  it('rejects missing sources', () => {
    expect(isValidSearchResult({ answer: 'hi' })).toBe(false)
  })
  it('accepts with optional cached flag', () => {
    expect(isValidSearchResult({
      answer: 'result',
      sources: [{ file_path: 'a.py', text: 'code', score: 0.9 }],
      cached: true,
    })).toBe(true)
  })
})

// ── Error message extractor ───────────────────────────────────────────────

function extractErrorMessage(error: unknown): string {
  if (typeof error === 'string') return error
  if (error instanceof Error) return error.message
  if (typeof error === 'object' && error !== null) {
    const e = error as Record<string, unknown>
    if (typeof e['detail'] === 'string') return e['detail']
    if (typeof e['message'] === 'string') return e['message']
  }
  return 'An unexpected error occurred'
}

describe('extractErrorMessage', () => {
  it('handles string errors', () => {
    expect(extractErrorMessage('Network error')).toBe('Network error')
  })
  it('handles Error objects', () => {
    expect(extractErrorMessage(new Error('fetch failed'))).toBe('fetch failed')
  })
  it('handles FastAPI detail errors', () => {
    expect(extractErrorMessage({ detail: 'Not found' })).toBe('Not found')
  })
  it('handles generic message key', () => {
    expect(extractErrorMessage({ message: 'Unauthorized' })).toBe('Unauthorized')
  })
  it('handles unknown error types', () => {
    expect(extractErrorMessage(42)).toBe('An unexpected error occurred')
  })
  it('handles null', () => {
    expect(extractErrorMessage(null)).toBe('An unexpected error occurred')
  })
})

// ── Query state machine ───────────────────────────────────────────────────

type QueryStatus = 'idle' | 'loading' | 'streaming' | 'done' | 'error'

function canSubmitQuery(status: QueryStatus, query: string): boolean {
  return status === 'idle' || status === 'done' || status === 'error'
    ? query.trim().length > 0
    : false
}

describe('canSubmitQuery', () => {
  it('allows submission when idle with non-empty query', () => {
    expect(canSubmitQuery('idle', 'what files do I have?')).toBe(true)
  })
  it('blocks submission while loading', () => {
    expect(canSubmitQuery('loading', 'query')).toBe(false)
  })
  it('blocks submission while streaming', () => {
    expect(canSubmitQuery('streaming', 'query')).toBe(false)
  })
  it('allows re-submission after done', () => {
    expect(canSubmitQuery('done', 'another question')).toBe(true)
  })
  it('allows re-submission after error', () => {
    expect(canSubmitQuery('error', 'retry query')).toBe(true)
  })
  it('blocks empty query even when idle', () => {
    expect(canSubmitQuery('idle', '   ')).toBe(false)
  })
})

// ── Source badge color logic ──────────────────────────────────────────────

type BadgeVariant = 'primary' | 'secondary' | 'warning' | 'error' | 'neutral'

function getBadgeVariant(score: number): BadgeVariant {
  if (score >= 0.8) return 'primary'
  if (score >= 0.6) return 'secondary'
  if (score >= 0.4) return 'warning'
  if (score >= 0.2) return 'error'
  return 'neutral'
}

describe('getBadgeVariant', () => {
  it('returns primary for high scores', () => {
    expect(getBadgeVariant(0.9)).toBe('primary')
  })
  it('returns secondary for good scores', () => {
    expect(getBadgeVariant(0.7)).toBe('secondary')
  })
  it('returns warning for medium scores', () => {
    expect(getBadgeVariant(0.5)).toBe('warning')
  })
  it('returns error for low scores', () => {
    expect(getBadgeVariant(0.3)).toBe('error')
  })
  it('returns neutral for very low scores', () => {
    expect(getBadgeVariant(0.1)).toBe('neutral')
  })
  it('handles boundary at 0.8', () => {
    expect(getBadgeVariant(0.8)).toBe('primary')
  })
})
