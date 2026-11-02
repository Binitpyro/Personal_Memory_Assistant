import { describe, it, expect, vi } from 'vitest';
import { act } from '@testing-library/react';
import { SearchPage } from '../pages/SearchPage';
import { renderWithProviders } from './test-utils';
import * as api from '../api';

const { refetchTree } = vi.hoisted(() => ({ refetchTree: vi.fn() }));

vi.mock('../useApi', () => ({
  useApi: vi.fn((_, opts) => ({
    data: opts?.cacheKey === 'query-history' ? { history: [] } : undefined,
    loading: false,
    error: null,
    refetch: opts?.cacheKey === 'file-tree' ? refetchTree : vi.fn(),
  })),
  invalidateCache: vi.fn(),
}));

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  getQueryHistory: vi.fn(),
  clearQueryHistory: vi.fn(),
  getFileTree: vi.fn(),
  getCurrentProvider: vi.fn().mockResolvedValue({ provider: 'ollama', model: 'm', source: 'default' }),
  subscribeProgress: vi.fn(() => () => {}),
}));

Element.prototype.scrollIntoView = vi.fn();

describe('A7-07 follow-up: SearchPage refreshes the tree once per finished run', () => {
  it('refetches for a second idle frame that belongs to a new run', () => {
    renderWithProviders(<SearchPage />);
    const cb = vi.mocked(api.subscribeProgress).mock.calls.at(-1)![0] as (d: unknown) => void;
    refetchTree.mockClear();

    const run = (n: number) => ({ status: 'idle', run_id: n, total_files: 10, processed_files: n, new_files: n, changed_files: 0, failed_files: 0, skipped_files: 0, scan_duration_ms: 100 + n, total_chunks: 50 + n });
    act(() => cb(run(1)));
    expect(refetchTree).toHaveBeenCalledTimes(1);
    // Same finished run polled again at the 15 s reconnect: no refetch.
    act(() => cb(run(1)));
    expect(refetchTree).toHaveBeenCalledTimes(1);
    // A short watcher run happened in between; status is "idle" both times.
    act(() => cb(run(2)));
    expect(refetchTree).toHaveBeenCalledTimes(2);
  });
});
