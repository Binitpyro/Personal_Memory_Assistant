import { describe, it, expect, vi, beforeEach } from 'vitest'

import { CACHE_KEYS, CORPUS_DERIVED_KEYS, launchStatusKey } from '../cacheKeys'

vi.mock('../queryClient', () => ({
  queryClient: {
    invalidateQueries: vi.fn(),
    clear: vi.fn(),
  },
}))

import { queryClient } from '../queryClient'
import { invalidateCache, invalidateCorpusCaches } from '../useApi'

describe('cache keys', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('maps every endpoint to a distinct key', () => {
    // The defect this file exists to prevent: three endpoints were each cached
    // under two spellings ('file-tree'/'files-tree',
    // 'llm-preferences'/'llm-prefs', 'ocr-status'/'ocr-status-settings'), so the
    // same payload was fetched twice and invalidating one never reached the
    // other. Two names for one endpoint is the bug; two *values* colliding here
    // would be a different one, so assert the values are unique.
    const values = Object.values(CACHE_KEYS)
    expect(new Set(values).size).toBe(values.length)
  })

  it('builds a distinct launch-status key per provider', () => {
    expect(launchStatusKey('ollama')).not.toBe(launchStatusKey('lm_studio'))
  })

  it('invalidates rather than evicting', () => {
    // queryClient.clear() *removes* every query (query-core 5.94.5:
    // getAll().forEach(q => this.remove(q))), so each mounted observer drops to
    // data: undefined and every page falls back to its cold-start spinner.
    // invalidateQueries refetches while keeping the previous data on screen.
    invalidateCache(CACHE_KEYS.fileTree)

    expect(queryClient.clear).not.toHaveBeenCalled()
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: [CACHE_KEYS.fileTree],
    })
  })

  it('refreshes corpus-derived views without touching configuration state', () => {
    invalidateCorpusCaches()

    const invalidated = vi
      .mocked(queryClient.invalidateQueries)
      .mock.calls.map((call) => (call[0] as { queryKey: string[] }).queryKey[0])

    expect(new Set(invalidated)).toEqual(new Set(CORPUS_DERIVED_KEYS))
    // An index run cannot change which providers are configured, so blowing
    // those away to refresh the file tree is what the old bare
    // invalidateCache() did wrong.
    expect(invalidated).not.toContain(CACHE_KEYS.providersList)
    expect(invalidated).not.toContain(CACHE_KEYS.providerSettings)
    expect(invalidated).not.toContain(CACHE_KEYS.llmPreferences)
    expect(queryClient.clear).not.toHaveBeenCalled()
  })
})
