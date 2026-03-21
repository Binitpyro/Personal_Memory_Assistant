import { useQuery } from '@tanstack/react-query'
import { queryClient } from './queryClient'

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

  // If there's no cacheKey, we generate a unique one so it never caches
  const qKey = cacheKey ? [cacheKey] : ['nocache', Math.random().toString()]

  const query = useQuery({
    queryKey: qKey,
    queryFn: fetcher,
    enabled: enabled,
    refetchInterval: refetchInterval > 0 ? refetchInterval : false,
    staleTime: cacheKey ? 8000 : 0 // Match legacy 8-second TTL
  })

  return {
    data: query.data as T | null | undefined,
    loading: query.isLoading || query.isFetching,
    error: query.error ? (query.error instanceof Error ? query.error.message : 'Unknown error') : null,
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
