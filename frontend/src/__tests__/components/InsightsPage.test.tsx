import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { InsightsPage } from '../../pages/InsightsPage';
import { renderWithProviders } from '../test-utils';

// Mock the lazy loaded components synchronously to prevent dynamic imports from hanging in tests
vi.mock('../../components/WebGPUFallback', () => ({
  WebGPUFallback: () => null,
  default: () => null,
}));

vi.mock('../../components/FileTypeTreemap', () => ({
  FileTypeTreemap: () => null,
  default: () => null,
}));

// Mock useApi directly using cacheKey and stable references to prevent infinite render loops in useEffect
vi.mock('../../useApi', () => {
  const mockInsights = {
    total_size_bytes: 1024 * 1024 * 10,
    database_size_bytes: 1024 * 1024,
    file_count: 5,
    top_files: [
      { path: 'file1.txt', size: 1024 * 1024 * 5 },
      { path: 'file2.pdf', size: 1024 * 1024 * 3 },
    ],
    cold_files: [
      { path: 'file3.docx', usage_count: 0 },
    ],
    type_breakdown: {
      'txt': { count: 2, size: 1024 * 1024 * 5 },
      'pdf': { count: 1, size: 1024 * 1024 * 3 },
    },
    error: null,
  };
  const mockFileTree = {
    folders: {},
    total_files: 5,
    total_size: 1024 * 1024 * 10,
  };
  return {
    useApi: vi.fn((_, opts) => {
      if (opts?.cacheKey === 'insights') {
        return {
          data: mockInsights,
          loading: false,
          error: null,
          refetch: vi.fn(),
        };
      }
      if (opts?.cacheKey === 'file-tree') {
        return {
          data: mockFileTree,
          loading: false,
          error: null,
          refetch: vi.fn(),
        };
      }
      return { data: undefined, loading: false, error: null, refetch: vi.fn() };
    }),
    invalidateCache: vi.fn(),
  };
});

// Mock api endpoints
// getPortrait belongs to KnowledgePortrait, which InsightsPage renders as a
// child - the factory has to cover the whole rendered tree's imports, not just
// the page's own.
vi.mock('../../api', () => ({
  getInsights: vi.fn(),
  getInsightsByType: vi.fn(),
  getFileTree: vi.fn(),
  // Resolved promise, not a bare vi.fn(): KnowledgePortrait calls this directly
  // in a useEffect and chains .then/.catch on the result, so undefined throws.
  getPortrait: vi.fn(() => Promise.resolve({ themes: [] })),
}));

describe('InsightsPage Component', () => {
  it('renders Insights page title and cards', () => {
    renderWithProviders(<InsightsPage />);

    expect(screen.getByText('Insights')).toBeDefined();
    expect(screen.getByText('Total Files')).toBeDefined();
    expect(screen.getByText('Indexed Files Size')).toBeDefined();
    expect(screen.getByText('Database Size')).toBeDefined();
  });
});
