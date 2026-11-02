import { describe, it, expect, vi } from 'vitest';
import { screen, act, waitFor } from '@testing-library/react';
import { LibraryPage } from '../pages/LibraryPage';
import { renderWithProviders } from './test-utils';
import * as api from '../api';

vi.mock('../useApi', () => ({
  useApi: vi.fn((_, opts) => ({
    data: opts?.cacheKey === 'index-status' ? { status: 'running' } : undefined,
    loading: false,
    error: null,
    refetch: vi.fn(),
  })),
  invalidateCache: vi.fn(),
  invalidateCorpusCaches: vi.fn(),
}));

vi.mock('../api', () => ({
  getHealth: vi.fn(),
  getFileTree: vi.fn(),
  getIndexStatus: vi.fn().mockResolvedValue({ last_error: 'PermissionError: boom' }),
  getSystemInfo: vi.fn(),
  getCurrentProvider: vi.fn(),
  getOcrStatus: vi.fn(),
  pickFolder: vi.fn(),
  startIndexing: vi.fn(),
  clearIndex: vi.fn(),
  cancelIndexing: vi.fn(),
  seedDemo: vi.fn(),
  clearBackendCaches: vi.fn(),
  subscribeProgress: vi.fn(() => () => {}),
}));

function emit(data: object) {
  const cb = vi.mocked(api.subscribeProgress).mock.calls.at(-1)![0] as (d: unknown) => void;
  act(() => cb(data));
}

describe('A7-10: failed index run is not reported as complete', () => {
  it('shows the failure and its reason for run_failed', async () => {
    renderWithProviders(<LibraryPage />);
    emit({ status: 'idle', run_failed: true, failed_files: 5, processed_files: 0 });
    await waitFor(() => expect(screen.getByText(/Indexing failed/)).toBeDefined());
    expect(screen.queryByText(/Indexing complete/)).toBeNull();
    await waitFor(() => expect(screen.getByText(/PermissionError: boom/)).toBeDefined());
  });

  it('reports partial failures on a completed run', async () => {
    renderWithProviders(<LibraryPage />);
    emit({ status: 'idle', run_failed: false, failed_files: 2, processed_files: 9 });
    await waitFor(() => expect(screen.getByText(/9 files processed, 2 failed/)).toBeDefined());
  });
});
