import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { useOptimisticMutation } from '../useOptimisticMutation'

interface Settings {
  enabled: boolean
  label: string
}

const KEY = 'test-settings'

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData<Settings>([KEY], { enabled: false, label: 'unchanged' })

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return { client, wrapper }
}

describe('useOptimisticMutation', () => {
  it('shows the new value before the server answers', async () => {
    const { client, wrapper } = setup()
    let resolve: (v: unknown) => void = () => {}
    const mutationFn = vi.fn(() => new Promise((r) => { resolve = r }))

    const { result } = renderHook(
      () =>
        useOptimisticMutation<boolean, unknown, Settings>({
          mutationFn,
          cacheKey: KEY,
          optimistic: (current, enabled) => (current ? { ...current, enabled } : current),
        }),
      { wrapper },
    )

    act(() => result.current.mutate(true))

    // The point of the exercise: the cache reflects the change while the request
    // is still in flight, so a control bound to it does not snap back.
    await waitFor(() =>
      expect(client.getQueryData<Settings>([KEY])?.enabled).toBe(true),
    )
    expect(mutationFn).toHaveBeenCalledTimes(1)

    await act(async () => { resolve({ ok: true }) })
  })

  it('restores the previous value when the write fails', async () => {
    const { client, wrapper } = setup()
    const onError = vi.fn()

    const { result } = renderHook(
      () =>
        useOptimisticMutation<boolean, unknown, Settings>({
          mutationFn: () => Promise.reject(new Error('server said no')),
          cacheKey: KEY,
          optimistic: (current, enabled) => (current ? { ...current, enabled } : current),
          onError,
        }),
      { wrapper },
    )

    act(() => result.current.mutate(true))

    await waitFor(() => expect(onError).toHaveBeenCalled())
    // Rolled back in full, not merely to a default.
    expect(client.getQueryData<Settings>([KEY])).toEqual({
      enabled: false,
      label: 'unchanged',
    })
  })

  it('leaves an absent cache entry absent rather than inventing one', async () => {
    // The rollback restores `undefined` when there was nothing cached. Writing a
    // partial object instead would hand components a half-populated payload.
    const { client, wrapper } = setup()
    client.removeQueries({ queryKey: [KEY] })
    const onError = vi.fn()

    const { result } = renderHook(
      () =>
        useOptimisticMutation<boolean, unknown, Settings>({
          mutationFn: () => Promise.reject(new Error('nope')),
          cacheKey: KEY,
          optimistic: (current, enabled) => (current ? { ...current, enabled } : current),
          onError,
        }),
      { wrapper },
    )

    act(() => result.current.mutate(true))

    await waitFor(() => expect(onError).toHaveBeenCalled())
    expect(client.getQueryData([KEY])).toBeUndefined()
  })

  it('invalidates the entry and its companions once settled', async () => {
    const { client, wrapper } = setup()
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    const { result } = renderHook(
      () =>
        useOptimisticMutation<boolean, unknown, Settings>({
          mutationFn: () => Promise.resolve({ ok: true }),
          cacheKey: KEY,
          invalidates: ['companion'],
          optimistic: (current, enabled) => (current ? { ...current, enabled } : current),
        }),
      { wrapper },
    )

    await act(async () => { await result.current.mutateAsync(true) })

    const keys = invalidate.mock.calls.map((c) => (c[0] as { queryKey: string[] }).queryKey[0])
    expect(keys).toContain(KEY)
    expect(keys).toContain('companion')
  })
})
