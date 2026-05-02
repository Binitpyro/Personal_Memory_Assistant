import { useQuery } from '@tanstack/react-query'
import { queryClient } from './queryClient'
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

/** Invalidate cache entries matching a prefix query key */
export function invalidateCache(prefix?: string) {
  if (!prefix) {
    queryClient.clear()
    return
  }
  // React Query fuzzy-matches keys, so passing ['prefix'] invalidates any key starting with ['prefix']
  queryClient.invalidateQueries({ queryKey: [prefix] })
}
