import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { LibraryPage } from '../pages/LibraryPage';
import { renderWithProviders } from './test-utils';

vi.mock('../useApi', () => ({
  useApi: vi.fn((_, opts) => ({
    data: undefined,
    loading: false,
    // The 401 / backend-down case: every gated call fails.
    error: opts?.cacheKey === 'index-status' ? 'Missing or invalid access token' : null,
    refetch: vi.fn(),
  })),
  invalidateCache: vi.fn(),
  invalidateCorpusCaches: vi.fn(),
}));

vi.mock('../api', () => ({
  getHealth: vi.fn(),
  getFileTree: vi.fn(),
  getIndexStatus: vi.fn(),
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

describe('A7-15: a failing index-status call is not shown as an empty idle library', () => {
  it('surfaces the error instead of Files 0 / Idle with no message', () => {
    renderWithProviders(<LibraryPage />);
    expect(screen.getByRole('alert').textContent).toMatch(/Missing or invalid access token/);
  });
});
