import { useQuery } from '@tanstack/react-query'
import { queryClient } from './queryClient'
import { CORPUS_DERIVED_KEYS } from './cacheKeys'
import { useId } from 'react'

interface UseApiOptions {
  /** Cache key — if same key is used, cached data is reused within TTL */
  cacheKey?: string
  /** Auto-fetch on mount? (default true) */
  enabled?: boolean
  /** Refetch interval in ms (0 = disabled) */
  refetchInterval?: number
}

/**
 * Drop-in wrapper around @tanstack/react-query that perfectly mimics the 
 * legacy useApi signature so no components need to be changed:
 * returns { data, loading, error, refetch }
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  opts: UseApiOptions = {},
) {
  const { cacheKey, enabled = true, refetchInterval = 0 } = opts

  // If there's no cacheKey, we generate a unique one so it never caches.
  // We use useId to ensure it stays STABLE and PURE across renders.
  const stableId = useId()
  const qKey = cacheKey ? [cacheKey] : ['nocache', stableId]

  const query = useQuery({
    queryKey: qKey,
    queryFn: () => fetcher(),
    enabled: enabled,
    refetchInterval: refetchInterval > 0 ? refetchInterval : false,
    staleTime: cacheKey ? 8000 : 0 // Match legacy 8-second TTL
  })

  let errorMsg = null
  if (query.error) {
    errorMsg = query.error instanceof Error ? query.error.message : 'Unknown error'
  }

  return {
    data: query.data as T | undefined,
    loading: query.isLoading || query.isFetching,
    error: errorMsg,
    refetch: query.refetch
  }
}

/**
 * Invalidate cache entries matching a prefix query key.
 *
 * The prefix is required. It used to be optional, and calling with no argument
 * ran `queryClient.clear()` — which *removes* every query rather than marking it
 * stale (`getAll().forEach(q => this.remove(q))` in query-core 5.94.5). Every
 * mounted observer therefore dropped to `data: undefined` and every page fell
 * back to its cold-start spinner. Four call sites did that, including the one
 * that fires when an index run finishes, so completing a scan blanked the whole
 * app. Invalidation refetches while keeping the previous data on screen, which
 * is what all of them wanted.
 */
export function invalidateCache(prefix: string) {
  // React Query fuzzy-matches keys, so passing ['prefix'] invalidates any key starting with ['prefix']
  queryClient.invalidateQueries({ queryKey: [prefix] })
}

/**
 * Refresh everything derived from the indexed corpus.
 *
 * The replacement for the bare `invalidateCache()` calls: indexing, clearing and
 * a manual refresh all change the same set of views, and naming that set beats
 * evicting unrelated caches (providers, API keys, OCR tiers) to reach it.
 */
export function invalidateCorpusCaches() {
  for (const key of CORPUS_DERIVED_KEYS) {
    invalidateCache(key)
  }
}
