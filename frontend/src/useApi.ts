import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Lightweight data-fetching hook with caching and deduplication.
 * Avoids adding react-query as a dependency for this project's scale.
 */

// In-memory cache shared across all hook instances
const cache = new Map<string, { data: unknown; ts: number }>();
const CACHE_TTL = 8_000; // 8 seconds

interface UseApiOptions {
  /** Cache key — if same key is used, cached data is reused within TTL */
  cacheKey?: string;
  /** Auto-fetch on mount? (default true) */
  enabled?: boolean;
  /** Refetch interval in ms (0 = disabled) */
  refetchInterval?: number;
}

export function useApi<T>(
  fetcher: () => Promise<T>,
  opts: UseApiOptions = {},
) {
  const { cacheKey, enabled = true, refetchInterval = 0 } = opts;
  const [data, setData] = useState<T | null>(() => {
    if (cacheKey) {
      const hit = cache.get(cacheKey);
      if (hit && Date.now() - hit.ts < CACHE_TTL) return hit.data as T;
    }
    return null;
  });
  const [loading, setLoading] = useState(!data);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  // Keep latest fetcher in ref to avoid stale closures
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetcherRef.current();
      if (!mountedRef.current) return;
      setData(result);
      if (cacheKey) cache.set(cacheKey, { data: result, ts: Date.now() });
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [cacheKey]);

  useEffect(() => {
    mountedRef.current = true;
    if (enabled) run();
    return () => { mountedRef.current = false; };
  }, [enabled, run]);

  // Polling
  useEffect(() => {
    if (!refetchInterval || !enabled) return;
    const id = setInterval(run, refetchInterval);
    return () => clearInterval(id);
  }, [refetchInterval, enabled, run]);

  return { data, loading, error, refetch: run };
}

/** Invalidate cache entries matching a prefix */
export function invalidateCache(prefix?: string) {
  if (!prefix) {
    cache.clear();
    return;
  }
  for (const key of cache.keys()) {
    if (key.startsWith(prefix)) cache.delete(key);
  }
}
