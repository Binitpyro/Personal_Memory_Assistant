import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { ExplorerPage } from '../../pages/ExplorerPage';
import { renderWithProviders } from '../test-utils';

// Mock useApi directly using cacheKey
vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    if (opts?.cacheKey === 'file-tree') {
      return {
        data: {
          folders: {
            'C:/projects/test': [
              { path: 'C:/projects/test/file1.txt', size: 1024, type: 'txt', usage_count: 0 },
              { path: 'C:/projects/test/file2.pdf', size: 2048, type: 'pdf', usage_count: 1 },
            ],
          },
          total_files: 2,
          total_size: 3072,
        },
        loading: false,
        error: null,
        refetch: vi.fn(),
      };
    }
    return { data: undefined, loading: false, error: null, refetch: vi.fn() };
  }),
  invalidateCache: vi.fn(),
}));

// Mock api endpoints
// Must list every export ExplorerPage.tsx imports from '../api'. vi.mock with a
// factory replaces the whole module, so an export missing here throws at render
// ("No <name> export is defined on the ... mock") rather than falling through to
// the real one.
vi.mock('../../api', () => ({
  getFileTree: vi.fn(),
  removeFolderIndex: vi.fn(),
  getOcrStatus: vi.fn(),
  forceOcr: vi.fn(),
}));

describe('ExplorerPage Component', () => {
  it('renders ExplorerPage and shows folders list', () => {
    renderWithProviders(<ExplorerPage />);

    expect(screen.getByText('Explorer')).toBeDefined();
    // Verify that the mocked folder is displayed in the list (displays normalized name 'test')
    expect(screen.getByText('test')).toBeDefined();
  });
});
