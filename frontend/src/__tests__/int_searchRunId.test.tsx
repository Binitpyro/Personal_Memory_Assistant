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

// Identical counters on purpose: only run_id tells these runs apart.
const frame = (status: string, run_id: number) => ({
  status, run_id, total_files: 10, processed_files: 10, new_files: 10, changed_files: 0,
  failed_files: 0, skipped_files: 0, scan_duration_ms: 100, total_chunks: 50,
});

describe('int: SearchPage refreshes the tree once per finished run_id', () => {
  it('refetches when a settled frame carries a new run_id, even with identical counters', () => {
    renderWithProviders(<SearchPage />);
    const cb = vi.mocked(api.subscribeProgress).mock.calls.at(-1)![0] as (d: unknown) => void;
    refetchTree.mockClear();

    act(() => cb(frame('idle', 1)));
    expect(refetchTree).toHaveBeenCalledTimes(1);
    // The same finished run polled again at the 15 s reconnect.
    act(() => cb(frame('idle', 1)));
    expect(refetchTree).toHaveBeenCalledTimes(1);
    // A run starts and finishes between two polls: same counters, new run_id.
    act(() => cb(frame('idle', 2)));
    expect(refetchTree).toHaveBeenCalledTimes(2);
  });

  it('a running frame of run N does not hide the finish of run N', () => {
    renderWithProviders(<SearchPage />);
    const cb = vi.mocked(api.subscribeProgress).mock.calls.at(-1)![0] as (d: unknown) => void;
    act(() => cb(frame('idle', 1)));
    refetchTree.mockClear();

    act(() => cb(frame('running', 2)));
    expect(refetchTree).toHaveBeenCalledTimes(0);
    act(() => cb(frame('idle', 2)));
    expect(refetchTree).toHaveBeenCalledTimes(1);
  });
});
