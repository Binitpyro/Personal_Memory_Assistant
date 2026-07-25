import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { LibraryPage } from '../../pages/LibraryPage';
import { renderWithProviders } from '../test-utils';

// Mock useApi directly using cacheKey
vi.mock('../../useApi', () => ({
  useApi: vi.fn((_, opts) => {
    if (opts?.cacheKey === 'health') {
      return {
        data: {
          version: '0.0.70',
          status: 'ok',
          db: 'connected',
          split_brain_sync_status: 'idle',
          model_ready: true,
        },
        loading: false,
        error: null,
        refetch: vi.fn(),
      };
    }
    if (opts?.cacheKey === 'index-status') {
      return {
        data: {
          status: 'idle',
          files_indexed: 10,
          chunks_indexed: 50,
          progress_percent: 100,
          scan_method: 'rust_core',
          scan_duration_ms: 1200,
          skipped_files: 0,
          new_files: 10,
          changed_files: 0,
          total_files: 10,
          processed_files: 10,
        },
        loading: false,
        error: null,
        refetch: vi.fn(),
      };
    }
    if (opts?.cacheKey === 'system-info') {
      return {
        data: {
          total_files: 10,
          total_chunks: 50,
          database_size_bytes: 1024 * 1024,
        },
        loading: false,
        error: null,
        refetch: vi.fn(),
      };
    }
    if (opts?.cacheKey === 'app-config') {
      return {
        data: {
          watch_dirs: [],
          google_drive_sync: false,
          gemini_model: 'default-model',
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
vi.mock('../../api', () => {
  return {
    getHealth: vi.fn(),
    getIndexStatus: vi.fn(),
    getSystemInfo: vi.fn(),
    getAppConfig: vi.fn(),
    pickFolder: vi.fn(),
    startIndexing: vi.fn(),
    clearIndex: vi.fn(),
    cancelIndexing: vi.fn(),
    seedDemo: vi.fn(),
    clearBackendCaches: vi.fn(),
  };
});

describe('LibraryPage Component', () => {
  it('renders LibraryPage and shows indexing status', () => {
    renderWithProviders(<LibraryPage />);

    expect(screen.getByText('Library')).toBeDefined();
    expect(screen.getByText('Scan Status')).toBeDefined();
    expect(screen.getByText('10')).toBeDefined();
  });

  it('handles custom directory index path input', () => {
    renderWithProviders(<LibraryPage />);
    
    const input = screen.getByPlaceholderText('Select or drag a folder here...');
    fireEvent.change(input, { target: { value: 'C:/test-folder' } });
    
    expect((input as HTMLInputElement).value).toBe('C:/test-folder');
  });
});
