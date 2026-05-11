/**
 * Shared API and UI state helper functions.
 * Extracted from api.test.ts to provide a single source of truth for logic
 * validated in tests and used in production components.
 */

export interface SearchResult {
  answer: string
  sources: Array<{ file_path: string; text: string; score: number }>
  cached?: boolean
  latency_ms?: number
}

/** Type guard for validating search results from the backend */
export function isValidSearchResult(data: unknown): data is SearchResult {
  if (typeof data !== 'object' || data === null) return false
  const d = data as Record<string, unknown>
  return (
    typeof d['answer'] === 'string' &&
    Array.isArray(d['sources'])
  )
}

/** Extract a human-readable error message from various error formats (fetch, FastAPI, generic) */
export function extractErrorMessage(error: unknown): string {
  if (typeof error === 'string') return error
  if (error instanceof Error) return error.message
  if (typeof error === 'object' && error !== null) {
    const e = error as Record<string, unknown>
    if (typeof e['detail'] === 'string') return e['detail']
    if (typeof e['message'] === 'string') return e['message']
  }
  return 'An unexpected error occurred'
}

export type QueryStatus = 'idle' | 'loading' | 'streaming' | 'done' | 'error'

/** Determine if a query can be submitted based on current UI state and query content */
export function canSubmitQuery(status: QueryStatus, query: string): boolean {
  return status === 'idle' || status === 'done' || status === 'error'
    ? query.trim().length > 0
    : false
}

export type BadgeVariant = 'primary' | 'secondary' | 'warning' | 'error' | 'neutral'

/** Determine the UI badge color variant based on a retrieval confidence score */
export function getBadgeVariant(score: number): BadgeVariant {
  if (score >= 0.8) return 'primary'
  if (score >= 0.6) return 'secondary'
  if (score >= 0.4) return 'warning'
  if (score >= 0.2) return 'error'
  return 'neutral'
}
