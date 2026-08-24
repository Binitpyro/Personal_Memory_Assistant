/**
 * One cache key per backend endpoint.
 *
 * These were bare string literals spread across pages, and three endpoints ended
 * up cached under two keys each: getFileTree as both 'file-tree' and
 * 'files-tree', getLLMPreferences as 'llm-preferences' and 'llm-prefs', and
 * getOcrStatus as 'ocr-status' and 'ocr-status-settings'. Each split meant two
 * fetches of the same payload and, worse, invalidation that never crossed:
 * removing a folder index in Explorer invalidated only its own spelling, so the
 * Search page kept offering the deleted folder until a reload.
 *
 * Import from here rather than writing the string, so a typo is a build error
 * instead of a second cache.
 */
export const CACHE_KEYS = {
  appConfig: 'app-config',
  driveInfo: 'drive-info',
  fileTree: 'file-tree',
  health: 'health',
  indexStatus: 'index-status',
  insights: 'insights',
  llmPreferences: 'llm-preferences',
  localModels: 'local-models',
  ocrStatus: 'ocr-status',
  ocrTiers: 'ocr-tiers',
  ocrVlmModels: 'ocr-vlm-models',
  ocrVlmSelection: 'ocr-vlm-selection',
  providerSettings: 'provider-settings',
  providersList: 'providers-list',
  queryHistory: 'query-history',
  systemInfo: 'system-info',
} as const

export type CacheKey = (typeof CACHE_KEYS)[keyof typeof CACHE_KEYS]

/**
 * The views derived from the indexed corpus, so they all go stale together when
 * indexing runs, the index is cleared, or the user asks for a refresh.
 *
 * Deliberately excludes provider, model and OCR-tier state: those describe the
 * machine's configuration, not its contents, and an index run cannot change them.
 */
export const CORPUS_DERIVED_KEYS: readonly CacheKey[] = [
  CACHE_KEYS.fileTree,
  CACHE_KEYS.insights,
  CACHE_KEYS.indexStatus,
  CACHE_KEYS.health,
  CACHE_KEYS.systemInfo,
  CACHE_KEYS.ocrStatus,
]

/** Per-provider launch status. Not a fixed key, so it gets a builder. */
export const launchStatusKey = (providerId: string) => `launch-status-${providerId}`
