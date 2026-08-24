import { useMutation, useQueryClient } from '@tanstack/react-query'

interface Options<TVars, TData, TCache> {
  /** The write itself. */
  mutationFn: (vars: TVars) => Promise<TData>
  /**
   * The cache entry this write changes. Updated immediately, restored if the
   * server rejects. Matches `useApi`'s query key, which is `[cacheKey]`.
   */
  cacheKey: string
  /** Next cached value, given the current one. Return it unchanged to opt out. */
  optimistic: (current: TCache | undefined, vars: TVars) => TCache | undefined
  /** Other keys the write invalidates once the server has answered. */
  invalidates?: readonly string[]
  onError?: (error: unknown, vars: TVars) => void
  onSuccess?: (data: TData, vars: TVars) => void
}

/**
 * A mutation that shows its result before the server confirms it.
 *
 * Every write in this app used to be a bare `await` followed by a refetch, so a
 * control bound to server state (a checkbox reading `checked={!!ocr?.enabled}`,
 * a list rendered from `prefs.fallback_chain`) visibly snapped back to the old
 * value until the round-trip finished — and destructive actions with no pending
 * state could be fired twice.
 *
 * The ceremony that makes this safe is easy to get wrong once, let alone four
 * times, hence one helper: cancel in-flight refetches so a stale response cannot
 * land on top of the optimistic value, snapshot for rollback, restore on error,
 * and re-sync from the server either way.
 */
export function useOptimisticMutation<TVars, TData, TCache>(
  opts: Options<TVars, TData, TCache>,
) {
  const client = useQueryClient()

  return useMutation<TData, unknown, TVars, { previous: TCache | undefined }>({
    mutationFn: opts.mutationFn,
    onMutate: async (vars) => {
      // Without this, a refetch already in flight can resolve after the
      // optimistic write and overwrite it with the pre-mutation server state.
      await client.cancelQueries({ queryKey: [opts.cacheKey] })
      const previous = client.getQueryData<TCache>([opts.cacheKey])
      client.setQueryData<TCache | undefined>([opts.cacheKey], (current) =>
        opts.optimistic(current, vars),
      )
      return { previous }
    },
    onError: (error, vars, context) => {
      // Restore exactly what was there, including "nothing".
      client.setQueryData([opts.cacheKey], context?.previous)
      opts.onError?.(error, vars)
    },
    onSuccess: opts.onSuccess,
    onSettled: () => {
      client.invalidateQueries({ queryKey: [opts.cacheKey] })
      for (const key of opts.invalidates ?? []) {
        client.invalidateQueries({ queryKey: [key] })
      }
    },
  })
}
